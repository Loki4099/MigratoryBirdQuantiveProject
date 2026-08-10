from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import text

from style_rotation.domain.enums import WorkFailureClass
from style_rotation.experiment.suite_submission import ResearchSuiteSubmissionService
from style_rotation.ops.maintenance import prune_latest_non_product_suites
from style_rotation.ops.work_queue import WorkQueueService
from style_rotation.ops.worker import WorkItemWorker
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.product.monitoring import MonitoringEvidence
from style_rotation.product.monitoring_service import (
    MonitoringOutput,
    MonitoringScheduler,
    MonitoringWorker,
)
from style_rotation.product.promotion import ProductPromotionService
from style_rotation.workspace.release_gates import ReleaseGateStatus
from tests.integration.test_v021_suite_submission import (
    _accepted_execution,
    _compiled,
    _release_evidence,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_deferred_retention_retries_after_the_old_lease_reaches_terminal_state() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)

    def gates() -> ReleaseGateStatus:
        return ReleaseGateStatus(True, True, ())

    evidence = _release_evidence(engine, "f" * 64)
    suites = ResearchSuiteSubmissionService(engine, gate_provider=gates)
    old = suites.submit(
        compiled=_compiled(asset_context_key="retention_old"),
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
    )
    queue = WorkQueueService(engine)
    running = queue.claim(worker_id="retention-lease", work_types=("predictive", "portfolio"))
    assert running is not None
    newest = suites.submit(
        compiled=_compiled(asset_context_key="retention_new"),
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
    )

    assert prune_latest_non_product_suites(engine) == 0
    assert queue.cancellation_requested(running.work_item_id, worker_id="retention-lease") is True
    queue.finish(running.work_item_id, worker_id="retention-lease", status="cancelled")
    assert prune_latest_non_product_suites(engine) == 1

    with engine.connect() as connection:
        retained = tuple(
            connection.execute(
                text("SELECT research_suite_id FROM experiment.research_suite")
            ).scalars()
        )
    assert old.research_suite_id not in retained
    assert retained == (newest.research_suite_id,)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_monitoring_cursor_starts_on_snapshot_and_terminal_gaps_do_not_block() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)

    def gates() -> ReleaseGateStatus:
        return ReleaseGateStatus(True, True, ())

    evidence = _release_evidence(engine, "e" * 64)
    ResearchSuiteSubmissionService(engine, gate_provider=gates).submit(
        compiled=_compiled(),
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
    )
    worker = WorkItemWorker(
        engine,
        worker_id="recovery-fixture-worker",
        handlers={"predictive": _accepted_execution, "portfolio": _accepted_execution},
    )
    outcomes = [worker.run_once() for _ in range(7)]
    portfolio_result = next(
        item.result_artifact_id for item in outcomes[1:] if item.result_artifact_id is not None
    )
    promoted = ProductPromotionService(engine, gate_provider=gates).promote(
        portfolio_result,
        name="Recovery candidate",
        researcher_id="tester",
        selection_reason="Persistent Worker recovery fixture",
        note=None,
    )
    with engine.begin() as connection:
        connection.execute(
            text("""
                UPDATE product.product_enrollment
                SET activated_at = '2026-08-01T00:00:00+00:00',
                    monitoring_start_at = NULL, first_decision_at = NULL
                WHERE product_enrollment_id = :enrollment_id
            """),
            {"enrollment_id": promoted.product_enrollment_id},
        )
        data_bundle_artifact_id = connection.execute(
            text("""
                SELECT data_bundle_artifact_id
                FROM experiment.comparison_context
                WHERE artifact_id = :context_artifact_id
            """),
            {"context_artifact_id": evidence.comparison_context_artifact_id},
        ).scalar_one()

    known_at = datetime.now(UTC)
    with patch.object(MonitoringScheduler, "_is_legal_decision_session", return_value=True):
        scheduled = MonitoringScheduler(engine).enqueue_for_data_bundle(
            data_bundle_artifact_id=data_bundle_artifact_id,
            as_of_session=date(2026, 8, 3),
            known_at=known_at,
        )
    assert len(scheduled) == 1
    with engine.connect() as connection:
        before = connection.execute(
            text("""
                SELECT monitoring_start_at, first_decision_at
                FROM product.product_enrollment
                WHERE product_enrollment_id = :enrollment_id
            """),
            {"enrollment_id": promoted.product_enrollment_id},
        ).mappings().one()
    assert before["monitoring_start_at"] is None
    assert before["first_decision_at"] is None

    def calculate(_request):
        return MonitoringOutput(
            evidence=MonitoringEvidence(
                frequency="weekly",
                session_count=1,
                decision_count=1,
                data_contract_ok=True,
                capacity_ok=True,
            ),
            primary_nav=Decimal("1"),
            stress_nav=Decimal("1"),
            metrics={},
            health_components={"executed_target": False},
        )

    assert (
        MonitoringWorker(
            engine, worker_id="recovery-monitor", calculator=calculate
        ).run_once().status
        == "completed"
    )
    with engine.connect() as connection:
        after = connection.execute(
            text("""
                SELECT monitoring_start_at, first_decision_at
                FROM product.product_enrollment
                WHERE product_enrollment_id = :enrollment_id
            """),
            {"enrollment_id": promoted.product_enrollment_id},
        ).mappings().one()
    assert after["monitoring_start_at"] == known_at
    assert after["first_decision_at"] == known_at

    queue = WorkQueueService(engine)
    failed_earlier, queued_later = _bind_monitoring_pair(
        engine,
        queue,
        enrollment_id=promoted.product_enrollment_id,
        data_bundle_artifact_id=data_bundle_artifact_id,
        first_session=date(2026, 8, 4),
        known_at=known_at + timedelta(minutes=1),
    )
    claimed = queue.claim(worker_id="failed-head", work_types=("monitoring",))
    assert claimed is not None and claimed.work_item_id == failed_earlier
    queue.finish(
        failed_earlier,
        worker_id="failed-head",
        status="failed",
        failure_class=WorkFailureClass.CONTRACT,
    )
    successor = queue.claim(worker_id="after-failure", work_types=("monitoring",))
    assert successor is not None and successor.work_item_id == queued_later
    queue.finish(queued_later, worker_id="after-failure", status="cancelled")

    cancelled_earlier, queued_after_cancel = _bind_monitoring_pair(
        engine,
        queue,
        enrollment_id=promoted.product_enrollment_id,
        data_bundle_artifact_id=data_bundle_artifact_id,
        first_session=date(2026, 8, 6),
        known_at=known_at + timedelta(minutes=3),
    )
    queue.request_cancel(cancelled_earlier)
    successor = queue.claim(worker_id="after-cancel", work_types=("monitoring",))
    assert successor is not None and successor.work_item_id == queued_after_cancel
    queue.finish(queued_after_cancel, worker_id="after-cancel", status="cancelled")


def _bind_monitoring_pair(
    engine,
    queue: WorkQueueService,
    *,
    enrollment_id: uuid.UUID,
    data_bundle_artifact_id: uuid.UUID,
    first_session: date,
    known_at: datetime,
) -> tuple[uuid.UUID, uuid.UUID]:
    items = [
        queue.enqueue(
            specification_fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            work_type="monitoring",
        ).item
        for _ in range(2)
    ]
    with engine.begin() as connection:
        for offset, item in enumerate(items):
            connection.execute(
                text("""
                    INSERT INTO product.monitoring_work_item (
                        work_item_id, product_enrollment_id, data_bundle_artifact_id,
                        as_of_session, known_at, held_during_suspension, rebalance_due
                    ) VALUES (:work_item_id, :enrollment_id, :data_bundle_artifact_id,
                              :as_of_session, :known_at, false, true)
                """),
                {
                    "work_item_id": item.work_item_id,
                    "enrollment_id": enrollment_id,
                    "data_bundle_artifact_id": data_bundle_artifact_id,
                    "as_of_session": first_session + timedelta(days=offset),
                    "known_at": known_at + timedelta(minutes=offset),
                },
            )
    return items[0].work_item_id, items[1].work_item_id
