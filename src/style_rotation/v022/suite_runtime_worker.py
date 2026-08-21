from __future__ import annotations

import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection

from style_rotation.data.forward_return_calculator import ForwardReturnCalculationError
from style_rotation.v022.aggregation_work_runtime import AggregationWorkExecutor
from style_rotation.v022.dag import GraphDagService
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore
from style_rotation.v022.ranking_cohort import RankingCohortService
from style_rotation.v022.representative_pipeline_runtime import (
    materialize_representative_processing,
)
from style_rotation.v022.runtime_contract import (
    V022RuntimeContractError,
    V022RuntimeDataError,
)
from style_rotation.v022.runtime_telemetry import (
    LocalRuntimeTelemetry,
    PeriodicLeaseHeartbeat,
    RuntimeTelemetryIdentity,
)
from style_rotation.v022.suite_element_diagnostics import SuiteElementDiagnosticService
from style_rotation.v022.suite_result_evidence import SuiteResultEvidenceService
from style_rotation.v022.suite_runtime_commands import (
    SUITE_RUNTIME_EXECUTOR_VERSION,
    SuiteRuntimeCommandService,
)
from style_rotation.v022.suite_runtime_planner import (
    PROCESSING_RUNTIME_ENVIRONMENT_FINGERPRINT,
    PROCESSING_RUNTIME_EXECUTOR_VERSION,
    RUNTIME_CATALOG_VERSION,
)
from style_rotation.v022.suite_typed_work_runtime import (
    RepresentativePortfolioMarketInputLoader,
    TypedSuiteWorkExecutor,
)
from style_rotation.v022.trainable_aggregation_work_runtime import (
    TrainableAggregationWorkExecutor,
)

MODEL_MIGRATION_REGISTRY = (
    Path(__file__).resolve().parents[3] / "v0.22" / "m5" / "model-migration-registry.v0.22.0.json"
)


@dataclass(frozen=True, slots=True)
class SuiteRuntimeWorkerOutcome:
    status: Literal["idle", "processed", "completed", "failed"]
    research_suite_id: uuid.UUID | None = None
    graph_run_id: uuid.UUID | None = None
    graph_work_item_id: uuid.UUID | None = None


class SuiteRuntimeWorker:
    """Materialize, plan, and execute one recoverable v0.22 Suite work item."""

    def __init__(
        self,
        engine: Engine,
        *,
        payload_directory: str | Path,
        worker_key: str,
    ) -> None:
        if not worker_key.strip():
            raise ValueError("v0.22 Suite worker key is required")
        self._engine = engine
        self._worker_key = worker_key
        object_root = Path(payload_directory).resolve()
        object_store = LocalPayloadObjectStore(object_root)
        self._dag = GraphDagService(engine)
        self._telemetry = LocalRuntimeTelemetry()
        self._aggregation = AggregationWorkExecutor(
            engine,
            object_store=object_store,
            object_root=object_root,
            model_registry_path=MODEL_MIGRATION_REGISTRY,
        )
        self._trainable_aggregation = TrainableAggregationWorkExecutor(
            engine,
            object_store=object_store,
            object_root=object_root,
        )
        self._typed = TypedSuiteWorkExecutor(
            engine,
            object_store=object_store,
            object_root=object_root,
            portfolio_input_loader=RepresentativePortfolioMarketInputLoader(engine),
        )
        self._evidence = SuiteResultEvidenceService(engine)
        self._ranking = RankingCohortService(engine)
        self._element_diagnostics = SuiteElementDiagnosticService(engine, object_root=object_root)

        def materialize_sources(
            compiled_research_graph_id: uuid.UUID,
            requested_range: dict[str, object],
            _executor_version: str,
            _environment_fingerprint: str,
            requested_by: str,
        ) -> None:
            with self._telemetry.span(
                RuntimeTelemetryIdentity(
                    worker_key=self._worker_key,
                    stage="processing_materialization",
                ),
                details={
                    "compiled_research_graph_id": compiled_research_graph_id,
                    "requested_start": requested_range["start"],
                    "requested_end": requested_range["end"],
                },
            ) as span:
                materialized = materialize_representative_processing(
                    engine,
                    object_store=object_store,
                    compiled_research_graph_id=compiled_research_graph_id,
                    requested_start=date.fromisoformat(cast(str, requested_range["start"])),
                    requested_end=date.fromisoformat(cast(str, requested_range["end"])),
                    requested_by=requested_by,
                    # Processing semantics did not change with the trainable-target
                    # fix.  Keep their independent identity stable so a Suite
                    # runtime retry reuses the exact completed Node outputs.
                    executor_version=PROCESSING_RUNTIME_EXECUTOR_VERSION,
                    environment_fingerprint=PROCESSING_RUNTIME_ENVIRONMENT_FINGERPRINT,
                )
                manifests = tuple(
                    item.payload_manifest_id for item in materialized.raw_payloads.outputs
                ) + tuple(
                    item.payload_manifest_id for item in materialized.stage3_outputs.values()
                )
                span.record(
                    raw_output_count=len(materialized.raw_payloads.outputs),
                    stage3_output_count=len(materialized.stage3_outputs),
                    cache_hit_count=sum(
                        item.reused_publication
                        for item in materialized.raw_payloads.outputs
                    )
                    + sum(
                        item.reused_publication
                        for item in materialized.stage3_outputs.values()
                    ),
                    **_payload_manifest_metrics(engine, manifests),
                )

        self._commands = SuiteRuntimeCommandService(engine, materialize_sources=materialize_sources)

    def run_once(self) -> SuiteRuntimeWorkerOutcome:
        with self._engine.connect() as lock_connection:
            suite_id = _lock_next_suite(lock_connection)
            if suite_id is None:
                return SuiteRuntimeWorkerOutcome("idle")
            try:
                run_id = _latest_active_run(lock_connection, suite_id)
                if run_id is None:
                    for obsolete_run_id in _obsolete_active_runs(lock_connection, suite_id):
                        self._dag.cancel_run(obsolete_run_id)
                    run_id = self._commands.process_suite(suite_id).graph_run_id
                claim = self._dag.claim(run_id, worker_key=self._worker_key)
                if claim is None:
                    status = _run_status(lock_connection, run_id)
                    if status == "completed":
                        self._finalize_completed_suite(suite_id)
                    return SuiteRuntimeWorkerOutcome(
                        "completed" if status == "completed" else "processed",
                        suite_id,
                        run_id,
                    )
                try:
                    with self._telemetry.span(
                        RuntimeTelemetryIdentity(
                            worker_key=self._worker_key,
                            stage=claim.work_kind,
                            research_suite_id=suite_id,
                            graph_run_id=run_id,
                            graph_work_item_id=claim.graph_work_item_id,
                            work_kind=claim.work_kind,
                        )
                    ) as span:
                        with PeriodicLeaseHeartbeat(
                            lambda: self._dag.renew(
                                claim,
                                worker_key=self._worker_key,
                            )
                        ) as lease:
                            if claim.work_kind == "aggregation":
                                execution_mode = _aggregation_execution_mode(
                                    lock_connection,
                                    graph_run_id=run_id,
                                    graph_work_item_id=claim.graph_work_item_id,
                                )
                                executor = (
                                    self._trainable_aggregation
                                    if execution_mode == "supervised"
                                    else self._aggregation
                                )
                                aggregation_publication = executor.execute(
                                    graph_run_id=run_id,
                                    claim=claim,
                                    worker_key=self._worker_key,
                                )
                                publication_manifest_id = (
                                    aggregation_publication.payload_manifest_id
                                )
                                reused_publication = (
                                    aggregation_publication.reused_publication
                                )
                                span.record(execution_mode=execution_mode)
                            elif claim.work_kind in {
                                "strategy_target",
                                "defense_decision",
                                "sleeve_merge",
                                "portfolio_cell",
                            }:
                                typed_publication = self._typed.execute_claim(
                                    graph_run_id=run_id,
                                    claim=claim,
                                    worker_key=self._worker_key,
                                )
                                publication_manifest_id = typed_publication.payload_manifest_id
                                reused_publication = typed_publication.reused_publication
                            else:
                                raise RuntimeError(
                                    f"Unexpected runnable v0.22 Work kind: {claim.work_kind}"
                                )
                        span.record(
                            reused_publication=reused_publication,
                            **_payload_manifest_metrics(
                                self._engine,
                                (publication_manifest_id,),
                            ),
                        )
                        if lease.error is not None and _work_item_status(
                            self._engine, claim.graph_work_item_id
                        ) not in {"completed", "reused"}:
                            raise RuntimeError("Graph Work lease heartbeat failed") from lease.error
                except Exception as error:
                    runtime_details = (
                        error.details
                        if isinstance(error, (V022RuntimeContractError, V022RuntimeDataError))
                        else {}
                    )
                    self._dag.finish(
                        claim,
                        worker_key=self._worker_key,
                        status="failed",
                        details={
                            "error_type": type(error).__name__,
                            "message": str(error),
                            "details": runtime_details,
                        },
                    )
                    return SuiteRuntimeWorkerOutcome(
                        "failed", suite_id, run_id, claim.graph_work_item_id
                    )
                status = _run_status(lock_connection, run_id)
                if status == "completed":
                    self._finalize_completed_suite(suite_id)
                return SuiteRuntimeWorkerOutcome(
                    "completed" if status == "completed" else "processed",
                    suite_id,
                    run_id,
                    claim.graph_work_item_id,
                )
            finally:
                if lock_connection.in_transaction():
                    lock_connection.rollback()
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                    {"key": f"v022-suite-runtime:{suite_id}"},
                )
                lock_connection.commit()

    def _finalize_completed_suite(self, suite_id: uuid.UUID) -> None:
        with self._telemetry.span(
            RuntimeTelemetryIdentity(
                worker_key=self._worker_key,
                stage="result_evidence",
                research_suite_id=suite_id,
            )
        ) as span:
            evidence = self._evidence.publish(suite_id)
            span.record(publication_count=len(evidence.publications))
        with self._telemetry.span(
            RuntimeTelemetryIdentity(
                worker_key=self._worker_key,
                stage="ranking_publication",
                research_suite_id=suite_id,
            )
        ):
            self._ranking.publish_for_suite(suite_id, released_by=self._worker_key)
        # Element diagnostics are auxiliary research evidence. A provider gap in
        # their forward-return panel must not invalidate an accepted Portfolio
        # result or its immutable Ranking release.
        with suppress(ForwardReturnCalculationError), self._telemetry.span(
            RuntimeTelemetryIdentity(
                worker_key=self._worker_key,
                stage="element_diagnostics",
                research_suite_id=suite_id,
            )
        ):
            self._element_diagnostics.publish(suite_id)


def _payload_manifest_metrics(
    engine: Engine,
    payload_manifest_ids: tuple[uuid.UUID, ...],
) -> dict[str, object]:
    if not payload_manifest_ids:
        return {"output_rows": 0, "output_bytes": 0, "output_partitions": 0}
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT coalesce(sum(row_or_item_count),0) AS output_rows,
                       coalesce(sum(byte_size),0) AS output_bytes,
                       coalesce(sum(partition_count),0) AS output_partitions
                  FROM data.payload_manifest
                WHERE payload_manifest_id IN :manifests
                """
            ).bindparams(bindparam("manifests", expanding=True)),
            {"manifests": payload_manifest_ids},
        ).mappings().one()
    return {
        "output_rows": int(row["output_rows"]),
        "output_bytes": int(row["output_bytes"]),
        "output_partitions": int(row["output_partitions"]),
    }


def _work_item_status(engine: Engine, graph_work_item_id: uuid.UUID) -> str | None:
    with engine.connect() as connection:
        return cast(
            str | None,
            connection.scalar(
                text(
                    "SELECT status FROM workspace.v022_graph_work_item "
                    "WHERE graph_work_item_id=:work"
                ),
                {"work": graph_work_item_id},
            ),
        )


def _aggregation_execution_mode(
    connection: Connection,
    *,
    graph_run_id: uuid.UUID,
    graph_work_item_id: uuid.UUID,
) -> Literal["deterministic", "supervised"]:
    mode = connection.scalar(
        text(
            """
            SELECT version.execution_mode
              FROM aggregation.graph_run_aggregation_binding binding
              JOIN aggregation.aggregation_run run
                ON run.aggregation_run_id=binding.aggregation_run_id
              JOIN aggregation.aggregation_version version
                ON version.aggregation_version_id=run.aggregation_version_id
             WHERE binding.graph_run_id=:run
               AND binding.graph_work_item_id=:work
            """
        ),
        {"run": graph_run_id, "work": graph_work_item_id},
    )
    if mode not in {"deterministic", "supervised"}:
        raise V022RuntimeContractError(
            "aggregation_execution_mode_missing",
            "Aggregation Work lacks one exact supported execution mode",
        )
    return cast(Literal["deterministic", "supervised"], mode)


def _lock_next_suite(connection: Connection) -> uuid.UUID | None:
    candidates = connection.scalars(
        text(
            """
            SELECT suite.research_suite_id
              FROM experiment.v022_research_suite suite
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=suite.artifact_id
               AND artifact.status='published'
              JOIN workspace.compiled_research_graph graph
                ON graph.compiled_research_graph_id=suite.compiled_research_graph_id
              JOIN workspace.v022_catalog_release catalog_release
                ON catalog_release.catalog_release_id=graph.catalog_release_id
              LEFT JOIN experiment.v022_research_suite_graph_run_binding binding
                ON binding.research_suite_id=suite.research_suite_id
              LEFT JOIN workspace.v022_graph_run run
                ON run.graph_run_id=binding.graph_run_id
             WHERE (binding.graph_run_id IS NOT NULL
                    OR catalog_release.version_number=:runtime_catalog_version)
               AND EXISTS (
                 SELECT 1
                   FROM experiment.v022_suite_launch_batch_child active_child
                   JOIN experiment.v022_suite_launch_batch_round active_batch
                     ON active_batch.suite_launch_batch_id=
                        active_child.suite_launch_batch_id
                   JOIN workspace.v022_research_round active_round
                     ON active_round.research_round_id=active_batch.research_round_id
                  WHERE active_child.research_suite_id=suite.research_suite_id
                    AND active_round.status='active'
               )
               AND (binding.graph_run_id IS NULL
                OR run.status IN ('ready','running')
                OR run.status='completed' AND EXISTS (
                     SELECT 1
                       FROM experiment.v022_suite_runtime_plan plan
                       JOIN experiment.v022_portfolio_cell_work_spec spec
                         ON spec.suite_runtime_plan_id=plan.suite_runtime_plan_id
                       JOIN experiment.v022_portfolio_cell_runtime_result result
                         ON result.graph_work_item_id=spec.graph_work_item_id
                      WHERE plan.graph_run_id=run.graph_run_id
                        AND plan.executor_version=:runtime_executor_version
                        AND (NOT EXISTS (
                          SELECT 1
                            FROM experiment.v022_result_evidence_snapshot evidence
                           WHERE evidence.result_artifact_id=result.artifact_id
                        ) OR NOT EXISTS (
                          SELECT 1
                            FROM experiment.v022_result_evidence_snapshot evidence
                            JOIN experiment.v022_ranking_cohort_member member
                              ON member.result_evidence_snapshot_id=
                                 evidence.result_evidence_snapshot_id
                           WHERE evidence.result_artifact_id=result.artifact_id
                        ) OR NOT EXISTS (
                          SELECT 1
                            FROM experiment.v022_result_element_diagnostic diagnostic
                           WHERE diagnostic.result_artifact_id=result.artifact_id
                        )
                   )
                ))
             ORDER BY suite.created_at,suite.research_suite_id
             LIMIT 32
            """
        ),
        {
            "runtime_catalog_version": RUNTIME_CATALOG_VERSION,
            "runtime_executor_version": SUITE_RUNTIME_EXECUTOR_VERSION,
        },
    ).all()
    for suite_id in candidates:
        locked = connection.scalar(
            text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"),
            {"key": f"v022-suite-runtime:{suite_id}"},
        )
        if locked is True:
            return cast(uuid.UUID, suite_id)
    return None


def _latest_active_run(connection: Connection, research_suite_id: uuid.UUID) -> uuid.UUID | None:
    return cast(
        uuid.UUID | None,
        connection.scalar(
            text(
                """
                SELECT run.graph_run_id
                  FROM experiment.v022_research_suite_graph_run_binding binding
                  JOIN workspace.v022_graph_run run
                    ON run.graph_run_id=binding.graph_run_id
                  JOIN experiment.v022_suite_runtime_plan plan
                    ON plan.graph_run_id=run.graph_run_id
                 WHERE binding.research_suite_id=:suite
                   AND plan.executor_version=:runtime_executor_version
                   AND (
                     run.status IN ('ready','running') OR
                     run.status='completed' AND EXISTS (
                       SELECT 1
                         FROM experiment.v022_portfolio_cell_work_spec spec
                         JOIN experiment.v022_portfolio_cell_runtime_result result
                           ON result.graph_work_item_id=spec.graph_work_item_id
                        WHERE spec.suite_runtime_plan_id=plan.suite_runtime_plan_id
                          AND (NOT EXISTS (
                            SELECT 1
                              FROM experiment.v022_result_evidence_snapshot evidence
                             WHERE evidence.result_artifact_id=result.artifact_id
                          ) OR NOT EXISTS (
                            SELECT 1
                              FROM experiment.v022_result_evidence_snapshot evidence
                              JOIN experiment.v022_ranking_cohort_member member
                                ON member.result_evidence_snapshot_id=
                                   evidence.result_evidence_snapshot_id
                             WHERE evidence.result_artifact_id=result.artifact_id
                          ) OR NOT EXISTS (
                            SELECT 1
                              FROM experiment.v022_result_element_diagnostic diagnostic
                             WHERE diagnostic.result_artifact_id=result.artifact_id
                          )
                          )
                     )
                   )
                 ORDER BY run.created_at DESC LIMIT 1
                """
            ),
            {
                "suite": research_suite_id,
                "runtime_executor_version": SUITE_RUNTIME_EXECUTOR_VERSION,
            },
        ),
    )


def _obsolete_active_runs(
    connection: Connection, research_suite_id: uuid.UUID
) -> tuple[uuid.UUID, ...]:
    """Return active Suite Runs whose runtime identity is no longer executable."""

    return tuple(
        cast(uuid.UUID, item)
        for item in connection.scalars(
            text(
                """
                SELECT run.graph_run_id
                  FROM experiment.v022_research_suite_graph_run_binding binding
                  JOIN workspace.v022_graph_run run
                    ON run.graph_run_id=binding.graph_run_id
                  JOIN experiment.v022_suite_runtime_plan plan
                    ON plan.graph_run_id=run.graph_run_id
                 WHERE binding.research_suite_id=:suite
                   AND run.status IN ('ready','running')
                   AND plan.executor_version<>:runtime_executor_version
                 ORDER BY binding.binding_ordinal
                """
            ),
            {
                "suite": research_suite_id,
                "runtime_executor_version": SUITE_RUNTIME_EXECUTOR_VERSION,
            },
        )
    )


def _run_status(connection: Connection, graph_run_id: uuid.UUID) -> str:
    status = connection.scalar(
        text("SELECT status FROM workspace.v022_graph_run WHERE graph_run_id=:run"),
        {"run": graph_run_id},
    )
    if not isinstance(status, str):
        raise RuntimeError(f"v0.22 Graph Run not found: {graph_run_id}")
    return status
