from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from style_rotation.api.query import ArtifactQueryService
from style_rotation.experiment.history import ExperimentHistoryService
from style_rotation.experiment.result_payload import hydrate_cell_result_row
from style_rotation.experiment.suite_submission import (
    FormalExecutionEvidence,
    FormalSubmissionBlocked,
    ResearchSuiteSubmissionService,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.ops.maintenance import prune_latest_non_product_suites
from style_rotation.ops.work_queue import WorkQueueService
from style_rotation.ops.worker import CellExecutionOutput, WorkItemWorker
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.product.alert_service import ProductAlertService
from style_rotation.product.lifecycle_service import ProductLifecycleService
from style_rotation.product.monitoring import MonitoringEvidence
from style_rotation.product.monitoring_service import (
    MonitoringOutput,
    MonitoringScheduler,
    MonitoringWorker,
)
from style_rotation.product.promotion import ProductPromotionService
from style_rotation.workspace.compiler import compile_research_spec
from style_rotation.workspace.contracts import (
    ModelInputSlot,
    ModelPresetDescriptor,
    ResearchDraftSelection,
    SignalDescriptor,
    StrategyPresetDescriptor,
)
from style_rotation.workspace.release_gates import ReleaseGateEvidenceService, ReleaseGateStatus

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


def _compiled(
    *,
    asset_context_key: str = "test_asset_context",
    include_sibling_strategy: bool = False,
    include_sibling_model: bool = False,
):
    strategy_keys = ("etf_k2", "etf_k1") if include_sibling_strategy else ("etf_k2",)
    model_keys = (
        ("linear_equal", "unselected_sibling_model")
        if include_sibling_model
        else ("linear_equal",)
    )
    draft = ResearchDraftSelection(
        asset_context_key=asset_context_key,
        factor_variant_keys=("return_w20",),
        signal_version_keys=("momentum_w20",),
        model_preset_keys=model_keys,
        strategy_preset_keys=strategy_keys,
        frequency="weekly",
    )
    return compile_research_spec(
        draft,
        signals=(
            SignalDescriptor(
                version_key="momentum_w20",
                factor_variant_key="return_w20",
                dimension_key="momentum",
                output_type="continuous",
                frequency="weekly",
            ),
        ),
        models=(
            ModelPresetDescriptor(
                preset_key="linear_equal",
                family_key="linear_weighted",
                output_type="continuous_score",
                output_comparability="cross_sectional",
                supported_frequencies=frozenset({"weekly"}),
                input_slots=(
                    ModelInputSlot(
                        slot_key="inputs",
                        allowed_dimension_keys=frozenset({"momentum"}),
                        allowed_output_types=frozenset({"continuous"}),
                        minimum_count=1,
                        maximum_count=4,
                    ),
                ),
                parameters={"weighting": "equal"},
                target_key=None,
            ),
            *(
                (
                    ModelPresetDescriptor(
                        preset_key="unselected_sibling_model",
                        family_key="linear_weighted",
                        output_type="continuous_score",
                        output_comparability="cross_sectional",
                        supported_frequencies=frozenset({"weekly"}),
                        input_slots=(
                            ModelInputSlot(
                                slot_key="inputs",
                                allowed_dimension_keys=frozenset({"momentum"}),
                                allowed_output_types=frozenset({"continuous"}),
                                minimum_count=1,
                                maximum_count=4,
                            ),
                        ),
                        parameters={"weighting": "sibling_only"},
                        target_key=None,
                    ),
                )
                if include_sibling_model
                else ()
            ),
        ),
        strategies=(
            StrategyPresetDescriptor(
                preset_key="etf_k2",
                family_key="multi_etf_top_k",
                compatible_model_output_types=frozenset({"continuous_score"}),
                supported_frequencies=frozenset({"weekly"}),
                target_k=2,
                minimum_eligible_assets=4,
                formal_minimum_eligible_assets=4,
                coverage_ratio=0.9,
            ),
            *(
                (
                    StrategyPresetDescriptor(
                        preset_key="etf_k1",
                        family_key="multi_etf_top_k",
                        compatible_model_output_types=frozenset({"continuous_score"}),
                        supported_frequencies=frozenset({"weekly"}),
                        target_k=1,
                        minimum_eligible_assets=4,
                        formal_minimum_eligible_assets=4,
                        coverage_ratio=0.9,
                    ),
                )
                if include_sibling_strategy
                else ()
            ),
        ),
    )


def _accepted_execution(request) -> CellExecutionOutput:
    if request.result_type == "predictive":
        asset_ids = [str(uuid.UUID(int=1)), str(uuid.UUID(int=2))]
        scores = [
            {
                "asset_id": asset_ids[0],
                "asset_key": "IWF",
                "observation_date": "2026-08-01",
                "score": "-1",
            },
            {
                "asset_id": asset_ids[1],
                "asset_key": "IWD",
                "observation_date": "2026-08-01",
                "score": "1",
            },
        ]
        audit = [
            {
                "common_asset_ids": asset_ids,
                "inputs": [
                    {
                        "contribution": score["score"],
                        "normalized_input_value": score["score"],
                    }
                ],
            }
            for score in scores
        ]
        return CellExecutionOutput(
            availability_status="accepted",
            quality_status="passed",
            metrics={
                "signal_count": 1,
                "model_point_count": 2,
                "target_period_coverage": 1.0,
                "nondegenerate_target_ratio": 1.0,
                "mean_rank_ic": 1.0,
            },
            series={"model_scores": scores, "model_input_audit": audit},
            diagnostics={"quality_checks": [{"status": "passed"}]},
        )
    nav = [
        {
            "nav_date": day,
            "strategy_wealth": 1.0,
            "strategy_currency_nav": 100_000_000.0,
            "benchmark_wealth": 1.0,
            "excess_wealth": 1.0,
        }
        for day in ("2026-08-01", "2026-08-02")
    ]
    return CellExecutionOutput(
        availability_status="accepted",
        quality_status="passed",
        metrics={"strategy": {"cagr": 0.1, "sharpe_ratio": 1.2, "maximum_drawdown": -0.1}},
        series={
            "nav_series": nav,
            "decisions": [{"coverage_ratio": "0.9"}],
            "trade_capacity": [
                {
                    "status": "accepted",
                    "decision_date": "2026-07-31",
                    "execution_date": "2026-08-01",
                    "raw_open": "100",
                    "pretrade_currency_nav": "100000000",
                    "absolute_weight_change": "0.5",
                    "order_notional": "50000000",
                    "participation_rate": "0.04",
                }
            ],
        },
        diagnostics={"quality_checks": [{"status": "passed"}]},
    )


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_suite_submission_is_gated_idempotent_and_cancellable() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    compiled = _compiled()
    evidence = FormalExecutionEvidence(
        comparison_context_fingerprint="a" * 64,
        impact_policy_key="test_finalized_impact_v1",
        impact_coefficient=Decimal("0.5"),
        impact_maximum_bps=Decimal("50"),
        comparison_context_artifact_id=uuid.UUID(int=0),
        pit_gate_artifact_id=uuid.UUID(int=0),
        terminal_gate_artifact_id=uuid.UUID(int=0),
        impact_gate_artifact_id=uuid.UUID(int=0),
    )
    blocked = ResearchSuiteSubmissionService(engine)
    with pytest.raises(FormalSubmissionBlocked):
        blocked.submit(compiled=compiled, normalized_selection={}, evidence=evidence)

    evidence = _release_evidence(engine, "a" * 64)

    enabled = ResearchSuiteSubmissionService(
        engine,
        gate_provider=lambda: ReleaseGateStatus(True, False, ()),
    )
    first = enabled.submit(
        compiled=compiled,
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
    )
    assert first.predictive_cell_count == 1
    assert first.portfolio_cell_count == 6
    assert first.queued_work_item_count == 7
    second = enabled.submit(
        compiled=compiled,
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
    )
    assert second.research_suite_id == first.research_suite_id
    assert second.reused is True
    assert enabled.status(first.research_suite_id)["status_counts"] == {"queued": 7}
    assert enabled.cancel(first.research_suite_id) == 7
    status = enabled.status(first.research_suite_id)
    assert status["complete"] is True
    assert status["status_counts"] == {"cancelled": 7}
    resumed = enabled.submit(
        compiled=compiled,
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
    )
    assert resumed.research_suite_id == first.research_suite_id
    assert enabled.status(first.research_suite_id)["status_counts"] == {"queued": 7}


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_portfolio_waits_without_attempts_while_a_second_worker_runs_slow_predictive() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    service = ResearchSuiteSubmissionService(
        engine, gate_provider=lambda: ReleaseGateStatus(True, False, ())
    )
    submitted = service.submit(
        compiled=_compiled(asset_context_key="slow_predictive_dependency"),
        normalized_selection={"frequency": "weekly"},
        evidence=_release_evidence(engine, "d" * 64),
    )
    predictive_started = threading.Event()
    release_predictive = threading.Event()

    def slow_predictive(request) -> CellExecutionOutput:
        if request.result_type == "predictive":
            predictive_started.set()
            assert release_predictive.wait(10), "test did not release the slow Predictive Cell"
        return _accepted_execution(request)

    first_worker = WorkItemWorker(
        engine,
        worker_id="slow-predictive-worker",
        handlers={"predictive": slow_predictive, "portfolio": slow_predictive},
    )
    second_worker = WorkItemWorker(
        engine,
        worker_id="concurrent-portfolio-worker",
        handlers={"predictive": slow_predictive, "portfolio": slow_predictive},
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        predictive_future = pool.submit(first_worker.run_once)
        assert predictive_started.wait(10)

        blocked = second_worker.run_once()
        assert blocked.status == "idle"
        with engine.connect() as connection:
            portfolio_queue = connection.execute(
                text("""
                    SELECT work.status, work.attempt_count, count(*) AS item_count
                    FROM experiment.research_suite_work_item link
                    JOIN ops.work_item work ON work.work_item_id = link.work_item_id
                    WHERE link.research_suite_id = :suite_id
                      AND link.cell_type = 'portfolio'
                    GROUP BY work.status, work.attempt_count
                """),
                {"suite_id": submitted.research_suite_id},
            ).one()
        assert portfolio_queue == ("queued", 0, 6)

        release_predictive.set()
        assert predictive_future.result(timeout=10).status == "completed"

    def drain(worker: WorkItemWorker) -> list[str]:
        outcomes: list[str] = []
        while True:
            outcome = worker.run_once()
            if outcome.status == "idle":
                return outcomes
            outcomes.append(outcome.status)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [
            status
            for worker_outcomes in pool.map(drain, (first_worker, second_worker))
            for status in worker_outcomes
        ]

    assert outcomes == ["completed"] * 6
    assert service.status(submitted.research_suite_id)["status_counts"] == {"completed": 7}
    with engine.connect() as connection:
        portfolio_attempts = connection.execute(
            text("""
                SELECT min(work.attempt_count), max(work.attempt_count)
                FROM experiment.research_suite_work_item link
                JOIN ops.work_item work ON work.work_item_id = link.work_item_id
                WHERE link.research_suite_id = :suite_id
                  AND link.cell_type = 'portfolio'
            """),
            {"suite_id": submitted.research_suite_id},
        ).one()
    assert portfolio_attempts == (1, 1)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_new_suite_prunes_older_non_product_experiment_batches() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    service = ResearchSuiteSubmissionService(
        engine, gate_provider=lambda: ReleaseGateStatus(True, False, ())
    )
    first = service.submit(
        compiled=_compiled(asset_context_key="first_asset_context"),
        normalized_selection={"frequency": "weekly"},
        evidence=_release_evidence(engine, "c" * 64),
    )
    completed_worker = WorkItemWorker(
        engine,
        worker_id="completed-history-worker",
        handlers={"predictive": _accepted_execution, "portfolio": _accepted_execution},
    )
    assert all(completed_worker.run_once().status == "completed" for _ in range(7))
    second = service.submit(
        compiled=_compiled(asset_context_key="second_asset_context"),
        normalized_selection={"frequency": "weekly"},
        evidence=_release_evidence(engine, "c" * 64),
    )
    assert first.research_suite_id != second.research_suite_id

    removed = ExperimentHistoryService(engine).prune_non_product_suites(
        retain_suite_id=second.research_suite_id
    )

    assert removed == 1
    with engine.connect() as connection:
        suites = tuple(
            connection.execute(
                text("SELECT research_suite_id FROM experiment.research_suite")
            ).scalars()
        )
        work_items = connection.execute(text("SELECT count(*) FROM ops.work_item")).scalar_one()
        compiled_specs = connection.execute(
            text("SELECT count(*) FROM workspace.compiled_research_spec")
        ).scalar_one()
        compiled_models = connection.execute(
            text("SELECT count(*) FROM workspace.compiled_model_instance")
        ).scalar_one()
        compiled_strategies = connection.execute(
            text("SELECT count(*) FROM strategy.compiled_strategy_version")
        ).scalar_one()
    assert suites == (second.research_suite_id,)
    assert work_items == 7
    # Compiled catalogs are small, immutable relational projections of their
    # lineage Artifacts.  Keep them so future submissions can reuse those
    # published identities without leaving an Artifact/typed-row tombstone.
    assert (compiled_specs, compiled_models, compiled_strategies) == (2, 2, 2)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_submission_rematerializes_legacy_pruned_configuration_rows() -> None:
    """A pre-fix catalog tombstone cannot break a later normal submission."""

    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    service = ResearchSuiteSubmissionService(
        engine, gate_provider=lambda: ReleaseGateStatus(True, False, ())
    )
    compiled = _compiled(asset_context_key="reusable_asset_context")
    evidence = _release_evidence(engine, "9" * 64)
    first = service.submit(
        compiled=compiled,
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
        submission_key="legacy-first-submission",
    )
    worker = WorkItemWorker(
        engine,
        worker_id="legacy-catalog-worker",
        handlers={"predictive": _accepted_execution, "portfolio": _accepted_execution},
    )
    assert all(worker.run_once().status == "completed" for _ in range(7))
    with engine.connect() as connection:
        legacy = (
            connection.execute(
                text(
                    """
                    SELECT suite.compiled_research_spec_id,
                           spec.artifact_id AS compiled_artifact_id,
                           suite.execution_policy_catalog_id,
                           policy.artifact_id AS policy_artifact_id,
                           strategy.artifact_id AS strategy_artifact_id
                    FROM experiment.research_suite suite
                    JOIN workspace.compiled_research_spec spec
                      ON spec.compiled_research_spec_id = suite.compiled_research_spec_id
                    JOIN experiment.execution_policy_catalog policy
                      ON policy.execution_policy_catalog_id =
                         suite.execution_policy_catalog_id
                    JOIN strategy.compiled_strategy_version strategy
                      ON strategy.compiled_research_spec_id =
                         suite.compiled_research_spec_id
                    WHERE suite.research_suite_id = :suite_id
                    """
                ),
                {"suite_id": first.research_suite_id},
            )
            .mappings()
            .one()
        )

    # A different context/policy becomes the retained Suite, so the first
    # Suite is genuine superseded experiment history.
    replacement = service.submit(
        compiled=_compiled(asset_context_key="replacement_asset_context"),
        normalized_selection={"frequency": "weekly"},
        evidence=replace(evidence, impact_policy_key="replacement_impact_policy"),
        submission_key="replacement-submission",
    )
    assert ExperimentHistoryService(engine).prune_non_product_suites(
        retain_suite_id=replacement.research_suite_id
    ) == 1

    # Reproduce the relational tombstones left by the retired retention code.
    # Published lineage Artifacts intentionally remain immutable and present.
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM strategy.compiled_strategy_version "
                "WHERE compiled_research_spec_id = :spec_id"
            ),
            {"spec_id": legacy["compiled_research_spec_id"]},
        )
        connection.execute(
            text(
                "DELETE FROM workspace.compiled_model_instance "
                "WHERE compiled_research_spec_id = :spec_id"
            ),
            {"spec_id": legacy["compiled_research_spec_id"]},
        )
        connection.execute(
            text(
                "DELETE FROM workspace.compiled_research_spec "
                "WHERE compiled_research_spec_id = :spec_id"
            ),
            {"spec_id": legacy["compiled_research_spec_id"]},
        )
        connection.execute(
            text(
                "DELETE FROM experiment.execution_policy_catalog "
                "WHERE execution_policy_catalog_id = :policy_id"
            ),
            {"policy_id": legacy["execution_policy_catalog_id"]},
        )

    repeated = service.submit(
        compiled=compiled,
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
        submission_key="post-retention-submission",
    )
    assert repeated.research_suite_id != first.research_suite_id
    assert repeated.queued_work_item_count == 7
    with engine.connect() as connection:
        rematerialized = (
            connection.execute(
                text(
                    """
                    SELECT spec.artifact_id AS compiled_artifact_id,
                           policy.artifact_id AS policy_artifact_id,
                           strategy.artifact_id AS strategy_artifact_id,
                           count(model.compiled_model_instance_id) AS model_count
                    FROM experiment.research_suite suite
                    JOIN workspace.compiled_research_spec spec
                      ON spec.compiled_research_spec_id = suite.compiled_research_spec_id
                    JOIN workspace.compiled_model_instance model
                      ON model.compiled_research_spec_id = spec.compiled_research_spec_id
                    JOIN experiment.execution_policy_catalog policy
                      ON policy.execution_policy_catalog_id =
                         suite.execution_policy_catalog_id
                    JOIN strategy.compiled_strategy_version strategy
                      ON strategy.compiled_research_spec_id =
                         suite.compiled_research_spec_id
                    WHERE suite.research_suite_id = :suite_id
                    GROUP BY spec.artifact_id, policy.artifact_id, strategy.artifact_id
                    """
                ),
                {"suite_id": repeated.research_suite_id},
            )
            .mappings()
            .one()
        )
    assert rematerialized["compiled_artifact_id"] == legacy["compiled_artifact_id"]
    assert rematerialized["policy_artifact_id"] == legacy["policy_artifact_id"]
    assert rematerialized["strategy_artifact_id"] == legacy["strategy_artifact_id"]
    assert rematerialized["model_count"] == 1


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_prune_requests_cancellation_and_waits_for_a_leased_worker() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    service = ResearchSuiteSubmissionService(
        engine, gate_provider=lambda: ReleaseGateStatus(True, False, ())
    )
    first = service.submit(
        compiled=_compiled(asset_context_key="leased_asset_context"),
        normalized_selection={"frequency": "weekly"},
        evidence=_release_evidence(engine, "c" * 64),
    )
    queue = WorkQueueService(engine)
    leased = queue.claim(
        worker_id="retention-race-worker",
        work_types=("predictive", "portfolio"),
        lease_seconds=120,
    )
    assert leased is not None
    second = service.submit(
        compiled=_compiled(asset_context_key="replacement_asset_context"),
        normalized_selection={"frequency": "weekly"},
        evidence=_release_evidence(engine, "c" * 64),
    )
    history = ExperimentHistoryService(engine)

    assert history.prune_non_product_suites(retain_suite_id=second.research_suite_id) == 0
    with engine.connect() as connection:
        statuses = dict(
            connection.execute(
                text("""
                    SELECT work.status, count(*)
                    FROM experiment.research_suite_work_item link
                    JOIN ops.work_item work ON work.work_item_id = link.work_item_id
                    WHERE link.research_suite_id = :suite_id
                    GROUP BY work.status
                """),
                {"suite_id": first.research_suite_id},
            ).all()
        )
        cancellation_requested = connection.execute(
            text(
                "SELECT cancel_requested_at IS NOT NULL FROM ops.work_item "
                "WHERE work_item_id = :work_item_id"
            ),
            {"work_item_id": leased.work_item_id},
        ).scalar_one()
    assert statuses == {"cancelled": 6, "running": 1}
    assert cancellation_requested is True

    queue.finish(
        leased.work_item_id,
        worker_id="retention-race-worker",
        status="cancelled",
    )
    assert history.prune_non_product_suites(retain_suite_id=second.research_suite_id) == 1
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM experiment.research_suite "
                "WHERE research_suite_id = :suite_id"
            ),
            {"suite_id": first.research_suite_id},
        ).scalar_one() == 0


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_product_retains_exact_six_cells_and_corresponding_predictive_evidence() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)

    def gates() -> ReleaseGateStatus:
        return ReleaseGateStatus(True, True, ())

    service = ResearchSuiteSubmissionService(engine, gate_provider=gates)
    evidence = _release_evidence(engine, "c" * 64)
    source = service.submit(
        compiled=_compiled(include_sibling_strategy=True, include_sibling_model=True),
        normalized_selection={
            "asset_security_ids": [],
            "asset_data_inputs": {},
            "factor_variant_keys": ["return_w20"],
            "signal_version_keys": ["momentum_w20"],
            "model_preset_keys": ["linear_equal", "unselected_sibling_model"],
            "model_target_keys": [
                "cross_sectional_relative_return__h5",
                "future_return__h21",
            ],
            "strategy_preset_keys": ["etf_k2", "etf_k1"],
            "frequency": "weekly",
        },
        evidence=evidence,
    )
    with engine.connect() as connection:
        sibling_model_id = connection.execute(
            text("""
                SELECT model.compiled_model_instance_id
                FROM workspace.compiled_model_instance model
                JOIN experiment.research_suite suite
                  ON suite.compiled_research_spec_id = model.compiled_research_spec_id
                WHERE suite.research_suite_id = :suite_id
                  AND model.preset_key = 'unselected_sibling_model'
            """),
            {"suite_id": source.research_suite_id},
        ).scalar_one()

    def execute_with_failed_sibling_predictive(request) -> CellExecutionOutput:
        output = _accepted_execution(request)
        if (
            request.result_type == "predictive"
            and request.cell_specification["compiled_model_instance_id"]
            == str(sibling_model_id)
        ):
            return replace(output, availability_status="data_quality_failed")
        return output

    worker = WorkItemWorker(
        engine,
        worker_id="precise-retention-worker",
        handlers={
            "predictive": execute_with_failed_sibling_predictive,
            "portfolio": execute_with_failed_sibling_predictive,
        },
    )
    assert all(worker.run_once().status == "completed" for _ in range(14))
    assert worker.run_once().status == "idle"
    with engine.connect() as connection:
        blocked_sibling_portfolios = connection.execute(
            text("""
                SELECT work.status, work.attempt_count, count(*) AS item_count
                FROM experiment.research_suite_work_item link
                JOIN ops.work_item work ON work.work_item_id = link.work_item_id
                JOIN experiment.portfolio_cell_specification cell
                  ON cell.artifact_id = link.cell_artifact_id
                JOIN strategy.compiled_strategy_version strategy
                  ON strategy.compiled_strategy_version_id =
                     cell.compiled_strategy_version_id
                WHERE link.research_suite_id = :suite_id
                  AND strategy.compiled_model_instance_id = :model_id
                GROUP BY work.status, work.attempt_count
            """),
            {"suite_id": source.research_suite_id, "model_id": sibling_model_id},
        ).one()
    assert blocked_sibling_portfolios == ("queued", 0, 12)
    with engine.connect() as connection:
        selected_result = connection.execute(
            text("""
                SELECT result.artifact_id
                FROM experiment.cell_result result
                JOIN experiment.portfolio_cell_specification cell
                  ON cell.artifact_id = result.cell_artifact_id
                JOIN strategy.compiled_strategy_version strategy
                  ON strategy.compiled_strategy_version_id =
                     cell.compiled_strategy_version_id
                JOIN workspace.compiled_model_instance model
                  ON model.compiled_model_instance_id =
                     strategy.compiled_model_instance_id
                WHERE cell.research_suite_id = :suite_id
                  AND strategy.strategy_preset_key = 'etf_k2'
                  AND model.preset_key = 'linear_equal'
                ORDER BY cell.ordinal LIMIT 1
            """),
            {"suite_id": source.research_suite_id},
        ).scalar_one()
    promotion = ProductPromotionService(engine, gate_provider=gates)
    promoted = promotion.promote(
        selected_result,
        name="Precisely retained candidate",
        researcher_id="tester",
        selection_reason="Retention integration fixture",
    )
    replacement = service.submit(
        compiled=_compiled(asset_context_key="replacement_asset_context"),
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
    )

    assert (
        ExperimentHistoryService(engine).prune_non_product_suites(
            retain_suite_id=replacement.research_suite_id
        )
        == 0
    )
    with engine.connect() as connection:
        source_counts = dict(
            connection.execute(
                text("""
                    SELECT
                      (SELECT count(*) FROM experiment.predictive_cell_specification
                       WHERE research_suite_id = :suite_id) AS predictive_cells,
                      (SELECT count(*) FROM experiment.portfolio_cell_specification
                       WHERE research_suite_id = :suite_id) AS portfolio_cells,
                      (SELECT count(*) FROM experiment.cell_result result
                       WHERE result.cell_artifact_id IN (
                         SELECT artifact_id FROM experiment.predictive_cell_specification
                         WHERE research_suite_id = :suite_id
                         UNION
                         SELECT artifact_id FROM experiment.portfolio_cell_specification
                         WHERE research_suite_id = :suite_id
                       )) AS results,
                      (SELECT count(*) FROM experiment.research_suite_work_item
                       WHERE research_suite_id = :suite_id) AS work_items
                """),
                {"suite_id": source.research_suite_id},
            ).mappings().one()
        )
        qualification = connection.execute(
            text("""
                SELECT cardinality(qualification.result_artifact_ids) AS results,
                       cardinality(qualification.cell_artifact_ids) AS cells,
                       qualification.artifact_id,
                       qualification.selection_context
                FROM product.product_enrollment enrollment
                JOIN product.product_version version
                  ON version.product_version_id = enrollment.product_version_id
                JOIN experiment.qualification_bundle qualification
                  ON qualification.qualification_bundle_id =
                     version.qualification_bundle_id
                WHERE enrollment.product_enrollment_id = :enrollment_id
            """),
            {"enrollment_id": promoted.product_enrollment_id},
        ).mappings().one()
        dependency_counts = dict(
            connection.execute(
                text("""
                    SELECT role, count(*)
                    FROM lineage.artifact_dependency
                    WHERE artifact_id = :qualification_artifact_id
                    GROUP BY role
                """),
                {"qualification_artifact_id": qualification["artifact_id"]},
            ).all()
        )
        active_strategy_count = connection.execute(
            text("""
                SELECT count(*)
                FROM strategy.compiled_strategy_version strategy
                JOIN experiment.research_suite suite
                  ON suite.compiled_research_spec_id =
                     strategy.compiled_research_spec_id
                WHERE suite.research_suite_id = :suite_id
            """),
            {"suite_id": source.research_suite_id},
        ).scalar_one()
    assert source_counts == {
        "predictive_cells": 1,
        "portfolio_cells": 6,
        "results": 7,
        "work_items": 7,
    }
    assert qualification["results"] == 6
    assert qualification["cells"] == 6
    assert dependency_counts == {
        "source_suite": 1,
        "qualification_result": 6,
        "qualification_predictive_result": 1,
    }
    selection_context = qualification["selection_context"]
    assert "normalized_selection" not in selection_context
    assert "candidate_branches" not in selection_context
    assert selection_context["exact_selection"]["model"]["preset_key"] == "linear_equal"
    assert selection_context["exact_selection"]["strategy"]["preset_key"] == "etf_k2"
    # The Product evidence remains exact even though the small immutable
    # compilation catalog retains sibling definitions for future reuse.
    assert active_strategy_count == 4
    assert promotion.evaluate(selected_result).eligible is True
    detail = ArtifactQueryService(engine).product_detail(promoted.product_enrollment_id)
    assert detail["research_chain"]["model_preset_keys"] == ["linear_equal"]
    assert detail["research_chain"]["strategy_preset_keys"] == ["etf_k2"]
    assert "unselected_sibling_model" not in str(detail["research_chain"])
    assert "etf_k1" not in str(detail["research_chain"])
    assert detail["oos_window"]["frozen_anchor_session"] == detail[
        "qualification_backtest"
    ]["resolved_end"]
    assert detail["oos_window"]["activation_session"] == detail["candidate"][
        "activated_at"
    ].date()
    assert detail["oos_window"]["post_freeze_session_count"] >= 0
    assert detail["oos_window"]["prospective_oos_session_count"] >= 0


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_promotion_guard_blocks_prune_between_qualification_and_product_commit() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)

    def gates() -> ReleaseGateStatus:
        return ReleaseGateStatus(True, True, ())

    suite_service = ResearchSuiteSubmissionService(engine, gate_provider=gates)
    evidence = _release_evidence(engine, "c" * 64)
    source = suite_service.submit(
        compiled=_compiled(include_sibling_strategy=True),
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
    )
    worker = WorkItemWorker(
        engine,
        worker_id="promotion-prune-race-worker",
        handlers={"predictive": _accepted_execution, "portfolio": _accepted_execution},
    )
    assert all(worker.run_once().status == "completed" for _ in range(13))
    with engine.connect() as connection:
        selected_result = connection.execute(
            text("""
                SELECT result.artifact_id
                FROM experiment.cell_result result
                JOIN experiment.portfolio_cell_specification cell
                  ON cell.artifact_id = result.cell_artifact_id
                JOIN strategy.compiled_strategy_version strategy
                  ON strategy.compiled_strategy_version_id =
                     cell.compiled_strategy_version_id
                WHERE cell.research_suite_id = :suite_id
                  AND strategy.strategy_preset_key = 'etf_k2'
                ORDER BY cell.ordinal LIMIT 1
            """),
            {"suite_id": source.research_suite_id},
        ).scalar_one()
    replacement = suite_service.submit(
        compiled=_compiled(asset_context_key="promotion_race_replacement"),
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
    )
    assert replacement.research_suite_id != source.research_suite_id

    qualification_committed = threading.Event()
    release_promotion = threading.Event()
    prune_started = threading.Event()
    promotion = ProductPromotionService(engine, gate_provider=gates)
    original_publish_monitoring = promotion._publish_monitoring_policy

    def pause_after_qualification(evaluation):
        qualification_committed.set()
        if not release_promotion.wait(timeout=10):
            raise TimeoutError("test did not release Product promotion")
        return original_publish_monitoring(evaluation)

    def prune_after_start_signal() -> int:
        prune_started.set()
        return prune_latest_non_product_suites(engine)

    with (
        patch.object(
            promotion,
            "_publish_monitoring_policy",
            side_effect=pause_after_qualification,
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        promotion_future = pool.submit(
            promotion.promote,
            selected_result,
            name="Promotion/prune race candidate",
            researcher_id="tester",
            selection_reason="Deterministic advisory lock fixture",
        )
        assert qualification_committed.wait(timeout=10)
        prune_future = pool.submit(prune_after_start_signal)
        assert prune_started.wait(timeout=10)
        time.sleep(0.2)
        assert not prune_future.done()
        release_promotion.set()
        promoted = promotion_future.result(timeout=10)
        assert prune_future.result(timeout=10) == 0

    with engine.connect() as connection:
        counts = connection.execute(
            text("""
                SELECT
                  (SELECT count(*)
                   FROM experiment.portfolio_cell_specification
                   WHERE research_suite_id = :suite_id) AS portfolio_cells,
                  (SELECT count(*)
                   FROM experiment.predictive_cell_specification
                   WHERE research_suite_id = :suite_id) AS predictive_cells,
                  (SELECT count(*)
                   FROM experiment.cell_result result
                   WHERE result.cell_artifact_id IN (
                     SELECT artifact_id
                     FROM experiment.portfolio_cell_specification
                     WHERE research_suite_id = :suite_id
                     UNION
                     SELECT artifact_id
                     FROM experiment.predictive_cell_specification
                     WHERE research_suite_id = :suite_id
                   )) AS result_count
            """),
            {"suite_id": source.research_suite_id},
        ).mappings().one()
        enrollment_exists = connection.execute(
            text(
                "SELECT count(*) FROM product.product_enrollment "
                "WHERE product_enrollment_id = :enrollment_id"
            ),
            {"enrollment_id": promoted.product_enrollment_id},
        ).scalar_one()
    assert dict(counts) == {
        "portfolio_cells": 6,
        "predictive_cells": 1,
        "result_count": 7,
    }
    assert enrollment_exists == 1


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_worker_atomically_materializes_one_cell_result() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    service = ResearchSuiteSubmissionService(
        engine, gate_provider=lambda: ReleaseGateStatus(True, False, ())
    )
    suite = service.submit(
        compiled=_compiled(),
        normalized_selection={"frequency": "weekly"},
        evidence=_release_evidence(engine, "b" * 64),
    )

    worker = WorkItemWorker(
        engine,
        worker_id="integration-worker",
        handlers={"predictive": _accepted_execution, "portfolio": _accepted_execution},
    )
    outcome = worker.run_once()
    assert outcome.status == "completed"
    assert outcome.result_artifact_id is not None
    with engine.connect() as connection:
        stored = (
            connection.execute(
                text("""
                    SELECT series, diagnostics, payload_storage_uri,
                           payload_content_hash, payload_storage_format,
                           payload_schema_version, payload_byte_size
                    FROM experiment.cell_result WHERE artifact_id = :artifact_id
                """),
                {"artifact_id": outcome.result_artifact_id},
            )
            .mappings()
            .one()
        )
    assert stored["series"]["externalized"] is True
    assert stored["payload_storage_uri"].startswith("cell-result://sha256/")
    assert stored["payload_byte_size"] > 0
    hydrated = hydrate_cell_result_row(stored)
    assert len(hydrated["series"]["model_scores"]) == 2
    assert hydrated["diagnostics"]["quality_checks"] == [{"status": "passed"}]
    status = service.status(suite.research_suite_id)
    assert status["status_counts"] == {"completed": 1, "queued": 6}


def _publish_comparison_context(engine, fingerprint: str) -> uuid.UUID:
    artifacts = ArtifactService(engine)
    bundle_definition_id = uuid.uuid4()

    def write_bundle_definition(connection: Connection, artifact_id: uuid.UUID) -> None:
        connection.execute(
            text("""
            INSERT INTO data.data_bundle_definition (
                data_bundle_definition_id, artifact_id, bundle_key, name, description
            ) VALUES (:id, :artifact_id, :key, 'Test bundle', 'Integration fixture')
        """),
            {
                "id": bundle_definition_id,
                "artifact_id": artifact_id,
                "key": f"test_bundle_{fingerprint[:8]}",
            },
        )

    artifacts.publish(
        artifact_type="data_bundle_definition",
        artifact_key=f"test_bundle_definition_{fingerprint[:8]}",
        version_number=1,
        semantic_payload={},
        content_payload={},
        draft_writer=write_bundle_definition,
    )

    def write_bundle(connection: Connection, artifact_id: uuid.UUID) -> None:
        connection.execute(
            text("""
            INSERT INTO data.data_bundle_version (
                data_bundle_version_id, data_bundle_definition_id, artifact_id,
                version_number, member_count, coverage_start, coverage_end
            ) VALUES (:id, :definition_id, :artifact_id, 1, 1, :start, :end)
        """),
            {
                "id": uuid.uuid4(),
                "definition_id": bundle_definition_id,
                "artifact_id": artifact_id,
                "start": date(2020, 1, 2),
                "end": date(2026, 8, 3),
            },
        )

    data = artifacts.publish(
        artifact_type="data_bundle_version",
        artifact_key="test_data_bundle",
        version_number=1,
        semantic_payload={},
        content_payload={},
        draft_writer=write_bundle,
    )
    universe = artifacts.publish(
        artifact_type="test_universe_history",
        artifact_key="test_universe_history",
        version_number=1,
        semantic_payload={},
        content_payload={},
    )
    benchmark_id = uuid.uuid4()

    def write_benchmark(connection: Connection, artifact_id: uuid.UUID) -> None:
        connection.execute(
            text("""
            INSERT INTO experiment.benchmark_set (
                benchmark_set_id, artifact_id, benchmark_set_key, version_number,
                primary_benchmark_key, research_benchmark_key, execution_policy
            ) VALUES (:id, :artifact_id, 'test_benchmark', 1, 'SPY',
                      'equal_weight_universe', '{}'::jsonb)
        """),
            {"id": benchmark_id, "artifact_id": artifact_id},
        )

    benchmark = artifacts.publish(
        artifact_type="benchmark_set",
        artifact_key="test_benchmark",
        version_number=1,
        semantic_payload={},
        content_payload={},
        draft_writer=write_benchmark,
    )

    def write_context(connection: Connection, artifact_id: uuid.UUID) -> None:
        connection.execute(
            text("""
            INSERT INTO experiment.comparison_context (
                comparison_context_id, artifact_id, benchmark_set_id,
                data_bundle_artifact_id, universe_history_artifact_id,
                context_fingerprint, resolved_start, resolved_end, as_of_date,
                state_reset_at, accounting_policy_key, metric_catalog_key
            ) VALUES (:id, :artifact_id, :benchmark_id, :data_id, :universe_id,
                      :fingerprint, :start, :end, :as_of, :start,
                      'test_accounting', 'test_metrics')
        """),
            {
                "id": uuid.uuid4(),
                "artifact_id": artifact_id,
                "benchmark_id": benchmark_id,
                "data_id": data.artifact_id,
                "universe_id": universe.artifact_id,
                "fingerprint": fingerprint,
                "start": date(2020, 1, 2),
                "end": date(2026, 8, 3),
                "as_of": date(2026, 8, 5),
            },
        )

    context = artifacts.publish(
        artifact_type="comparison_context",
        artifact_key=fingerprint,
        version_number=1,
        semantic_payload={"fingerprint": fingerprint},
        content_payload={},
        dependencies=(
            DependencyInput(benchmark.artifact_id, "benchmark_set"),
            DependencyInput(data.artifact_id, "data_bundle"),
            DependencyInput(universe.artifact_id, "universe_history"),
        ),
        draft_writer=write_context,
    )
    return context.artifact_id


def _release_evidence(engine, fingerprint: str) -> FormalExecutionEvidence:
    context_id = _publish_comparison_context(engine, fingerprint)
    artifacts = ArtifactService(engine)
    pit = artifacts.publish(
        artifact_type="pit_universe_snapshot",
        artifact_key=f"pit_{fingerprint[:8]}",
        version_number=1,
        semantic_payload={},
        content_payload={},
    )
    terminal = artifacts.publish(
        artifact_type="terminal_event_dataset",
        artifact_key=f"terminal_{fingerprint[:8]}",
        version_number=1,
        semantic_payload={},
        content_payload={},
    )
    impact = artifacts.publish(
        artifact_type="impact_policy",
        artifact_key=f"impact_{fingerprint[:8]}",
        version_number=1,
        semantic_payload={},
        content_payload={},
    )
    gates = ReleaseGateEvidenceService(engine)
    pit_gate = gates.publish(
        gate_key="pit_universe",
        version_number=1,
        source_evidence_artifact_id=pit.artifact_id,
        document={"p0_finalized": True},
    )
    terminal_gate = gates.publish(
        gate_key="terminal_event",
        version_number=1,
        source_evidence_artifact_id=terminal.artifact_id,
        document={"p0_finalized": True},
    )
    impact_gate = gates.publish(
        gate_key="impact_policy",
        version_number=1,
        source_evidence_artifact_id=impact.artifact_id,
        document={
            "policy_key": "test_finalized_impact_v1",
            "coefficient": "0.5",
            "maximum_bps": "50",
            "comparison_context_artifact_id": str(context_id),
        },
    )
    assert gates.current_status().formal_enabled is True
    return FormalExecutionEvidence(
        comparison_context_fingerprint=fingerprint,
        impact_policy_key="test_finalized_impact_v1",
        impact_coefficient=Decimal("0.5"),
        impact_maximum_bps=Decimal("50"),
        comparison_context_artifact_id=context_id,
        pit_gate_artifact_id=pit_gate,
        terminal_gate_artifact_id=terminal_gate,
        impact_gate_artifact_id=impact_gate,
    )


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_complete_six_cell_bundle_promotes_atomically_to_product_candidate() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    context_fingerprint = "c" * 64

    def gates() -> ReleaseGateStatus:
        return ReleaseGateStatus(True, True, ())

    suite_service = ResearchSuiteSubmissionService(engine, gate_provider=gates)
    evidence = _release_evidence(engine, context_fingerprint)
    suite = suite_service.submit(
        compiled=_compiled(),
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
    )

    worker = WorkItemWorker(
        engine,
        worker_id="promotion-worker",
        handlers={"predictive": _accepted_execution, "portfolio": _accepted_execution},
    )
    outcomes = [worker.run_once() for _ in range(7)]
    portfolio_result = next(
        item.result_artifact_id for item in outcomes[1:] if item.result_artifact_id
    )
    assert portfolio_result is not None
    promotion = ProductPromotionService(engine, gate_provider=gates)
    assert promotion.evaluate(portfolio_result).eligible is True
    promoted = promotion.promote(
        portfolio_result,
        name="Test research candidate",
        researcher_id="tester",
        selection_reason="Best fixture result",
        note="integration test",
    )
    assert promoted.lifecycle == "active"
    assert promoted.revision == 1
    repeated = promotion.promote(
        portfolio_result,
        name="Test research candidate",
        researcher_id="tester",
        selection_reason="Best fixture result",
        note="integration test",
    )
    assert repeated.product_enrollment_id == promoted.product_enrollment_id
    replacement_suite = suite_service.submit(
        compiled=_compiled(asset_context_key="replacement_asset_context"),
        normalized_selection={"frequency": "weekly"},
        evidence=evidence,
    )
    assert (
        ExperimentHistoryService(engine).prune_non_product_suites(
            retain_suite_id=replacement_suite.research_suite_id
        )
        == 0
    )
    with engine.connect() as connection:
        enrollment = (
            connection.execute(
                text(
                    """
                    SELECT lifecycle, revision
                    FROM product.product_enrollment
                    WHERE product_enrollment_id = :id
                    """
                ),
                {"id": promoted.product_enrollment_id},
            )
            .mappings()
            .one()
        )
        suite_count = connection.execute(
            text("SELECT count(*) FROM experiment.research_suite")
        ).scalar_one()
    assert dict(enrollment) == {"lifecycle": "active", "revision": 1}
    assert suite_count == 2
    assert suite_service.status(suite.research_suite_id)["complete"] is True

    with engine.begin() as connection:
        connection.execute(
            text("""
            UPDATE product.product_enrollment
            SET activated_at = '2026-08-01T00:00:00+00:00',
                monitoring_start_at = '2026-08-01T00:00:00+00:00',
                first_decision_at = '2026-08-01T00:00:00+00:00'
            WHERE product_enrollment_id = :id
        """),
            {"id": promoted.product_enrollment_id},
        )
        new_data_id = connection.execute(
            text("""
            SELECT context.data_bundle_artifact_id
            FROM experiment.comparison_context context
            WHERE context.context_fingerprint = :fingerprint
        """),
            {"fingerprint": context_fingerprint},
        ).scalar_one()
    with patch.object(MonitoringScheduler, "_is_legal_decision_session", return_value=True):
        scheduled = MonitoringScheduler(engine).enqueue_for_data_bundle(
            data_bundle_artifact_id=new_data_id,
            as_of_session=date(2026, 8, 3),
            known_at=datetime.now(UTC),
        )
    assert len(scheduled) == 1

    def calculate_monitoring(_request):
        return MonitoringOutput(
            evidence=MonitoringEvidence(
                frequency="weekly",
                session_count=130,
                decision_count=30,
                data_contract_ok=True,
                capacity_ok=False,
            ),
            primary_nav=Decimal("1.01"),
            stress_nav=Decimal("0.99"),
            metrics={"cumulative_return": 0.01},
            health_components={},
        )

    monitor = MonitoringWorker(
        engine, worker_id="monitoring-worker", calculator=calculate_monitoring
    )
    assert monitor.run_once().status == "completed"
    with engine.connect() as connection:
        snapshot = connection.execute(
            text("SELECT health FROM product.monitoring_snapshot")
        ).scalar_one()
        alert = (
            connection.execute(
                text(
                    """
                    SELECT alert.product_alert_id, event.to_status
                    FROM product.product_alert_event event
                    JOIN product.product_alert alert
                      ON alert.product_alert_id = event.product_alert_id
                    WHERE alert.product_enrollment_id = :id
                    """
                ),
                {"id": promoted.product_enrollment_id},
            )
            .mappings()
            .one()
        )
    assert snapshot == "warning"
    assert alert["to_status"] == "open"
    alert_change = ProductAlertService(engine).change(
        alert["product_alert_id"],
        target="acknowledged",
        researcher_id="tester",
        note="reviewing",
        occurred_at=datetime.now(UTC),
    )
    assert alert_change.to_status == "acknowledged"
    now = datetime.now(UTC)
    future = now.replace(year=now.year + 1)
    scheduled_suspend = ProductLifecycleService(engine).change(
        promoted.product_enrollment_id,
        target="suspended",
        expected_revision=1,
        reason_code="scheduled_review",
        reason="Pause at the future Decision",
        researcher_id="tester",
        requested_at=now,
        effective_at=future,
    )
    assert scheduled_suspend.applied is False
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT lifecycle FROM product.product_enrollment "
                    "WHERE product_enrollment_id = :id"
                ),
                {"id": promoted.product_enrollment_id},
            ).scalar_one()
            == "active"
        )
    applied = ProductLifecycleService(engine).apply_due(as_of=future)
    assert applied[0].to_lifecycle == "suspended"
    ProductLifecycleService(engine).change(
        promoted.product_enrollment_id,
        target="active",
        expected_revision=3,
        reason_code="resume_after_review",
        reason="Resume observation",
        researcher_id="tester",
        requested_at=future,
        effective_at=future,
    )
    lifecycle = ProductLifecycleService(engine).change(
        promoted.product_enrollment_id,
        target="retired",
        expected_revision=4,
        reason_code="manual_retirement",
        reason="End observation window",
        researcher_id="tester",
        requested_at=future,
        effective_at=future,
    )
    assert lifecycle.to_lifecycle == "retired"
    assert lifecycle.revision == 5
    reenrolled = promotion.promote(
        portfolio_result,
        name="Test research candidate v2",
        researcher_id="tester",
        selection_reason="Restart observation after retirement",
        note=None,
    )
    assert reenrolled.product_enrollment_id != promoted.product_enrollment_id


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_warning_bearing_exploratory_suite_promotes_as_nonformal_candidate() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    open_gates = ReleaseGateStatus(
        False,
        False,
        (
            "pit_universe_gate_open",
            "terminal_event_gate_open",
            "impact_policy_gate_open",
        ),
    )
    evidence = replace(
        _release_evidence(engine, "e" * 64),
        suite_mode="exploratory",
    )
    ResearchSuiteSubmissionService(
        engine, gate_provider=lambda: open_gates
    ).submit(
        compiled=_compiled(asset_context_key="exploratory_candidate_context"),
        normalized_selection={
            "frequency": "weekly",
            "asset_security_ids": [],
            "asset_data_inputs": {},
            "factor_variant_keys": ["return_w20"],
            "signal_version_keys": ["momentum_w20"],
            "model_preset_keys": ["linear_equal"],
            "model_target_keys": ["cross_sectional_relative_return__h5"],
            "strategy_preset_keys": ["etf_k2"],
        },
        evidence=evidence,
    )
    worker = WorkItemWorker(
        engine,
        worker_id="exploratory-promotion-worker",
        handlers={"predictive": _accepted_execution, "portfolio": _accepted_execution},
    )
    outcomes = [worker.run_once() for _ in range(7)]
    portfolio_result = next(
        item.result_artifact_id for item in outcomes[1:] if item.result_artifact_id
    )
    assert portfolio_result is not None

    promotion = ProductPromotionService(engine, gate_provider=lambda: open_gates)
    evaluation = promotion.evaluate(portfolio_result)
    assert evaluation.eligible is True
    assert evaluation.warning_codes == (
        "candidate_non_pit_survivorship_warning",
        "candidate_terminal_event_coverage_warning",
        "candidate_uncalibrated_impact_warning",
        "candidate_exploratory_suite",
    )
    promoted = promotion.promote(
        portfolio_result,
        name="Warning-bearing research candidate",
        researcher_id="tester",
        selection_reason="Explicit exploratory observation",
    )
    with engine.connect() as connection:
        qualification = (
            connection.execute(
                text(
                    """
                    SELECT bundle.formal_eligible, bundle.product_eligible
                    FROM product.product_version version
                    JOIN experiment.qualification_bundle bundle
                      ON bundle.qualification_bundle_id = version.qualification_bundle_id
                    JOIN product.product_enrollment enrollment
                      ON enrollment.product_version_id = version.product_version_id
                    WHERE enrollment.product_enrollment_id = :enrollment_id
                    """
                ),
                {"enrollment_id": promoted.product_enrollment_id},
            )
            .mappings()
            .one()
        )
    assert dict(qualification) == {"formal_eligible": False, "product_eligible": True}
    detail = ArtifactQueryService(engine).product_detail(promoted.product_enrollment_id)
    assert detail["research_chain"]["selected_result_artifact_id"] == portfolio_result
    assert detail["research_chain"]["factor_variant_keys"] == ["return_w20"]
    assert detail["research_chain"]["signal_version_keys"] == ["momentum_w20"]
    assert detail["research_chain"]["model_preset_keys"] == ["linear_equal"]
    assert detail["research_chain"]["model_target_keys"] == [
        "cross_sectional_relative_return__h5"
    ]
    assert detail["research_chain"]["strategy_preset_keys"] == ["etf_k2"]
    assert detail["research_chain"]["frequency"] == "weekly"
    assert detail["qualification_backtest"]["result_artifact_id"] == portfolio_result
    assert detail["qualification_backtest"]["run_status"] == "completed"
    assert detail["qualification_backtest"]["metrics"]
    assert detail["qualification_backtest"]["nav_series"]
