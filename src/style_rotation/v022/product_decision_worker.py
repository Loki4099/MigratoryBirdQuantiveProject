from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore
from style_rotation.v022.product_monitoring import OOSMonitoringService
from style_rotation.v022.product_runtime import ProductDecisionService
from style_rotation.v022.product_runtime_worker import ProductRuntimeWorker
from style_rotation.v022.runtime_contract import V022RuntimeDataError
from style_rotation.v022.suite_runtime_worker import MODEL_MIGRATION_REGISTRY

_RUNTIME_VERSION = "v022-product-decision-runtime-3"


@dataclass(frozen=True, slots=True)
class ProductDecisionWorkerOutcome:
    status: Literal[
        "idle", "waiting_for_input", "completed", "missing", "failed"
    ]
    product_enrollment_id: uuid.UUID | None = None
    decision_session_id: uuid.UUID | None = None
    product_decision_id: uuid.UUID | None = None
    research_suite_id: uuid.UUID | None = None
    graph_run_id: uuid.UUID | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _DueDecision:
    product_enrollment_id: uuid.UUID
    execution_version_id: uuid.UUID
    monitoring_policy_version_id: uuid.UUID
    configuration_snapshot_id: uuid.UUID
    compiled_research_graph_id: uuid.UUID
    decision_session_id: uuid.UUID
    session_date: date
    decision_cutoff_at: datetime
    product_input_snapshot_id: uuid.UUID | None


class ProductDecisionWorker:
    """Execute one due v0.22 Product session from its frozen Execution Version."""

    def __init__(
        self,
        engine: Engine,
        *,
        payload_directory: str | Path,
        worker_key: str,
    ) -> None:
        if not worker_key.strip():
            raise ValueError("v0.22 Product Decision worker key is required")
        self._engine = engine
        self._worker_key = worker_key
        object_root = Path(payload_directory).resolve()
        object_store = LocalPayloadObjectStore(object_root)
        self._runtime = ProductRuntimeWorker(
            engine,
            object_store=object_store,
            object_root=object_root,
            model_registry_path=MODEL_MIGRATION_REGISTRY,
        )
        self._decisions = ProductDecisionService(engine)
        self._monitoring = OOSMonitoringService(engine)

    def run_once(
        self, *, observed_at: datetime | None = None
    ) -> ProductDecisionWorkerOutcome:
        known_at = observed_at or datetime.now(UTC)
        if known_at.tzinfo is None:
            raise ValueError("Product Decision worker observed_at must be timezone-aware")
        with self._engine.connect() as connection:
            due = _next_due_decision(connection, known_at)
        if due is None:
            return ProductDecisionWorkerOutcome("idle")
        if due.product_input_snapshot_id is None:
            return ProductDecisionWorkerOutcome(
                "waiting_for_input",
                due.product_enrollment_id,
                due.decision_session_id,
                reason="product_input_snapshot_not_prepared",
            )
        actor_key = f"v022-product:{due.product_enrollment_id}"
        environment = sha256_hexdigest(
            {"contract_version": "v0.22.0", "runtime": _RUNTIME_VERSION}
        )
        try:
            runtime = self._runtime.execute(
                product_input_snapshot_id=due.product_input_snapshot_id,
                product_enrollment_id=due.product_enrollment_id,
                configuration_snapshot_id=due.configuration_snapshot_id,
                compiled_research_graph_id=due.compiled_research_graph_id,
                decision_session_id=due.decision_session_id,
                decision_date=due.session_date,
                decision_cutoff_at=due.decision_cutoff_at,
                actor_key=actor_key,
                runtime_version=_RUNTIME_VERSION,
                environment_fingerprint=environment,
            )
            publication = self._decisions.publish(
                product_enrollment_id=due.product_enrollment_id,
                decision_session_id=due.decision_session_id,
                evidence_class="prospective_oos",
                decision_status="completed",
                decision_document=runtime.decision_document,
                quality_document={
                    "state": "accepted",
                    "input_frozen": True,
                    "price_basis": "back_adjusted",
                    "market_price_mapping_required": True,
                    "warning_codes": [
                        "back_adjusted_research_requires_market_price_mapping"
                    ],
                },
                runtime_artifacts=runtime.runtime_artifacts,
                product_runtime_binding=runtime.runtime_binding,
            )
            self._publish_monitoring(due, signal_coverage="1")
            return ProductDecisionWorkerOutcome(
                "completed",
                due.product_enrollment_id,
                due.decision_session_id,
                publication.product_decision_id,
                None,
                runtime.processing.graph_run_id,
            )
        except V022RuntimeDataError as error:
            publication = self._decisions.publish(
                product_enrollment_id=due.product_enrollment_id,
                decision_session_id=due.decision_session_id,
                evidence_class="prospective_oos",
                decision_status="missing",
                decision_document={
                    "decision_session": due.session_date.isoformat(),
                    "decision_cutoff_at": due.decision_cutoff_at.isoformat(),
                },
                quality_document={
                    "state": "missing",
                    "input_frozen": True,
                    "details": error.details,
                },
                reason_codes=(error.reason_code,),
            )
            self._publish_monitoring(due, signal_coverage="0")
            return ProductDecisionWorkerOutcome(
                "missing",
                due.product_enrollment_id,
                due.decision_session_id,
                publication.product_decision_id,
                reason=error.reason_code,
            )
        except Exception as error:
            return ProductDecisionWorkerOutcome(
                "failed",
                due.product_enrollment_id,
                due.decision_session_id,
                reason=f"{type(error).__name__}: {error}",
            )

    def _publish_monitoring(
        self, due: _DueDecision, *, signal_coverage: str
    ) -> None:
        engine = ArtifactService(self._engine).publish(
            artifact_type="v022_monitoring_engine_version",
            artifact_key="v022_product_oos_monitoring_v1",
            version_number=1,
            semantic_payload={
                "contract_version": "v0.22.0",
                "engine_key": "product_oos_monitoring",
                "version_number": 1,
                "metric": "signal_coverage",
            },
            content_payload={
                "contract_version": "v0.22.0",
                "engine_key": "product_oos_monitoring",
                "version_number": 1,
                "metric": "signal_coverage",
            },
            reason="publish deterministic v0.22 Product monitoring engine",
        )
        self._monitoring.publish(
            product_enrollment_id=due.product_enrollment_id,
            monitoring_policy_version_id=due.monitoring_policy_version_id,
            monitoring_engine_artifact_id=engine.artifact_id,
            as_of_decision_session_id=due.decision_session_id,
            known_at=due.decision_cutoff_at,
            metrics_document={"signal_coverage": signal_coverage},
        )

def _next_due_decision(
    connection: Connection, observed_at: datetime
) -> _DueDecision | None:
    row = connection.execute(
        text(
            """
            SELECT enrollment.product_enrollment_id,enrollment.execution_version_id,
                   enrollment.monitoring_policy_version_id,
                   execution.configuration_snapshot_id,
                   configuration.compiled_research_graph_id,
                   session.decision_session_id,session.session_date,
                   session.decision_cutoff_at,
                   CASE WHEN input_snapshot_artifact.artifact_id IS NOT NULL
                        THEN input_snapshot.product_input_snapshot_id
                   END AS product_input_snapshot_id
              FROM product.v022_product_enrollment enrollment
              JOIN lineage.artifact enrollment_artifact
                ON enrollment_artifact.artifact_id=enrollment.artifact_id
               AND enrollment_artifact.status='published'
              JOIN product.v022_execution_version execution
                ON execution.execution_version_id=enrollment.execution_version_id
              JOIN experiment.v022_research_configuration_snapshot configuration
                ON configuration.configuration_snapshot_id=
                   execution.configuration_snapshot_id
              JOIN product.v022_decision_schedule_session session
                ON session.decision_schedule_version_id=
                   enrollment.decision_schedule_version_id
              JOIN product.v022_decision_schedule_session first_session
                ON first_session.decision_session_id=
                   enrollment.first_eligible_decision_session_id
              LEFT JOIN LATERAL (
                SELECT event.to_lifecycle
                  FROM product.v022_enrollment_lifecycle_event event
                 WHERE event.product_enrollment_id=enrollment.product_enrollment_id
                   AND event.effective_at<=:observed_at
                 ORDER BY event.effective_at DESC,event.sequence_number DESC
                 LIMIT 1
              ) lifecycle ON true
              LEFT JOIN product.v022_product_input_snapshot input_snapshot
                ON input_snapshot.product_enrollment_id=enrollment.product_enrollment_id
               AND input_snapshot.decision_session_id=session.decision_session_id
              LEFT JOIN lineage.artifact input_snapshot_artifact
                ON input_snapshot_artifact.artifact_id=input_snapshot.artifact_id
               AND input_snapshot_artifact.status='published'
             WHERE session.ordinal>=first_session.ordinal
               AND session.decision_cutoff_at<=:observed_at
               AND coalesce(lifecycle.to_lifecycle,'active')='active'
               AND NOT EXISTS (
                 SELECT 1 FROM product.v022_product_decision decision
                  WHERE decision.execution_version_id=enrollment.execution_version_id
                    AND decision.decision_session_id=session.decision_session_id
               )
               AND NOT EXISTS (
                 SELECT 1
                   FROM product.v022_decision_schedule_session prior_session
                  WHERE prior_session.decision_schedule_version_id=
                        enrollment.decision_schedule_version_id
                    AND prior_session.ordinal>=first_session.ordinal
                    AND prior_session.ordinal<session.ordinal
                    AND prior_session.decision_cutoff_at<=:observed_at
                    AND NOT EXISTS (
                      SELECT 1 FROM product.v022_product_decision prior_decision
                       WHERE prior_decision.execution_version_id=
                             enrollment.execution_version_id
                         AND prior_decision.decision_session_id=
                             prior_session.decision_session_id
                    )
               )
             ORDER BY (input_snapshot_artifact.artifact_id IS NULL),
                      session.decision_cutoff_at,enrollment.product_enrollment_id
             LIMIT 1
            """
        ),
        {"observed_at": observed_at},
    ).mappings().one_or_none()
    if row is None:
        return None
    return _DueDecision(
        row["product_enrollment_id"],
        row["execution_version_id"],
        row["monitoring_policy_version_id"],
        row["configuration_snapshot_id"],
        row["compiled_research_graph_id"],
        row["decision_session_id"],
        row["session_date"],
        row["decision_cutoff_at"],
        row["product_input_snapshot_id"],
    )
