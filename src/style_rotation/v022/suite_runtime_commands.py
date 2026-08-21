from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import (
    CANONICAL_SERIALIZATION_VERSION,
    sha256_hexdigest,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.frozen_sp500_environment import FROZEN_SP500_COHORT_VERSION
from style_rotation.v022.suite_identity import GraphSuiteIdentityService
from style_rotation.v022.suite_runtime_planner import (
    CONTRACT_VERSION,
    PROCESSING_RUNTIME_ENVIRONMENT_FINGERPRINT,
    PROCESSING_RUNTIME_EXECUTOR_VERSION,
    RuntimeWorkBlueprint,
    SuiteRuntimePlanRequest,
    VerifiedSuiteRuntimeFacts,
    build_suite_runtime_preflight,
    load_verified_facts,
)

BACK_ADJUSTED_RESEARCH_SEMANTICS: dict[str, object] = {
    "price_basis": "back_adjusted",
    "known_at_rule": "xnys_session_close_at_utc",
    "product_warning_required": True,
}
RESEARCH_DATA_BUNDLE_KEY = "us_style_daily_research_bundle"
SUITE_RUNTIME_EXECUTOR_VERSION = "v022-first-slice-runtime-37"


@dataclass(frozen=True, slots=True)
class SuiteRuntimeSubmitCommand:
    compiled_research_graph_id: uuid.UUID
    submission_key: uuid.UUID
    requested_by: str
    requested_range: dict[str, object]
    executor_version: str
    environment_fingerprint: str
    suite_mode: Literal["exploratory"] = "exploratory"
    evaluation_cohort_version_id: uuid.UUID | None = None
    materialization_range: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class SuiteRuntimeSubmission:
    research_suite_id: uuid.UUID
    suite_runtime_plan_id: uuid.UUID
    graph_run_id: uuid.UUID
    plan_artifact_id: uuid.UUID
    status: str
    reused: bool


@dataclass(frozen=True, slots=True)
class _ArtifactPublication:
    artifact_id: uuid.UUID
    semantic_fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class _EvaluationInput:
    dataset_publication_id: uuid.UUID
    dataset_artifact_id: uuid.UUID
    dataset_fingerprint: str
    calendar_version_id: uuid.UUID | None
    calendar_artifact_id: uuid.UUID | None
    coverage_start: date
    coverage_end: date


@dataclass(frozen=True, slots=True)
class _EvaluationBundle:
    data_bundle_version_id: uuid.UUID
    data_bundle_artifact_id: uuid.UUID
    reserve: _EvaluationInput
    reserve_return_model_version_id: uuid.UUID
    reserve_return_model_artifact_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class _EvaluationContextPublication:
    context_id: uuid.UUID
    artifact_id: uuid.UUID


class SuiteRuntimeCommandService:
    """Submit the first executable v0.22 Suite as one runtime DAG.

    Suite and Configuration identities are published by the existing idempotent
    identity service.  All runtime-specific identities and queue rows are then
    committed in one database transaction, so a failed preflight cannot expose a
    successful runnable submission.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        materialize_sources: Callable[[uuid.UUID, dict[str, object], str, str, str], None]
        | None = None,
        execute_runtime: Callable[[uuid.UUID], None] | None = None,
    ) -> None:
        self._engine = engine
        self._suites = GraphSuiteIdentityService(engine)
        self._artifacts = ArtifactService(engine)
        self._materialize_sources = materialize_sources
        self._execute_runtime = execute_runtime

    def submit_command(self, command: SuiteRuntimeSubmitCommand) -> SuiteRuntimeSubmission:
        if not command.requested_by.strip():
            raise ValueError("Suite runtime actor is required")
        suite = self._suites.publish(
            compiled_research_graph_id=command.compiled_research_graph_id,
            submission_key=command.submission_key,
            actor_key=command.requested_by,
            suite_mode=command.suite_mode,
        )
        return self._process_published_suite(suite.research_suite_id, command)

    def _process_published_suite(
        self,
        research_suite_id: uuid.UUID,
        command: SuiteRuntimeSubmitCommand,
    ) -> SuiteRuntimeSubmission:
        if command.evaluation_cohort_version_id is not None:
            with self._engine.begin() as connection:
                _bind_evaluation_cohort(
                    connection,
                    research_suite_id=research_suite_id,
                    evaluation_cohort_version_id=(command.evaluation_cohort_version_id),
                    bound_by=command.requested_by,
                )
        if self._materialize_sources is not None:
            self._materialize_sources(
                command.compiled_research_graph_id,
                command.materialization_range or command.requested_range,
                command.executor_version,
                command.environment_fingerprint,
                command.requested_by,
            )
        request = SuiteRuntimePlanRequest(
            research_suite_id=research_suite_id,
            requested_by=command.requested_by,
            requested_range=command.requested_range,
            executor_version=command.executor_version,
            environment_fingerprint=command.environment_fingerprint,
            evaluation_cohort_version_id=command.evaluation_cohort_version_id,
            materialization_range=command.materialization_range,
            source_executor_version=PROCESSING_RUNTIME_EXECUTOR_VERSION,
            source_environment_fingerprint=(PROCESSING_RUNTIME_ENVIRONMENT_FINGERPRINT),
        )
        with self._engine.begin() as connection:
            # Serialize plan publication for one immutable Suite. A retry keeps
            # the prior Run binding as audit evidence and appends the next
            # binding ordinal instead of competing for ordinal zero.
            connection.execute(
                text(
                    """
                    SELECT research_suite_id
                      FROM experiment.v022_research_suite
                     WHERE research_suite_id=:suite
                     FOR UPDATE
                    """
                ),
                {"suite": research_suite_id},
            ).one()
            _publish_evaluation_contexts(
                connection,
                artifacts=self._artifacts,
                research_suite_id=research_suite_id,
            )
            facts = load_verified_facts(connection, request)
            preflight = build_suite_runtime_preflight(request, facts)
            existing = _existing_plan(connection, preflight.plan_fingerprint)
            if existing is not None:
                submission = SuiteRuntimeSubmission(
                    research_suite_id,
                    existing["suite_runtime_plan_id"],
                    existing["graph_run_id"],
                    existing["artifact_id"],
                    existing["status"],
                    True,
                )
            else:
                submission = _write_runtime_plan(
                    connection,
                    artifacts=self._artifacts,
                    request=request,
                    facts=facts,
                    preflight=preflight,
                )
        if self._execute_runtime is not None:
            self._execute_runtime(submission.graph_run_id)
            with self._engine.connect() as connection:
                status = cast(
                    str,
                    connection.scalar(
                        text("SELECT status FROM workspace.v022_graph_run WHERE graph_run_id=:run"),
                        {"run": submission.graph_run_id},
                    ),
                )
            submission = SuiteRuntimeSubmission(
                submission.research_suite_id,
                submission.suite_runtime_plan_id,
                submission.graph_run_id,
                submission.plan_artifact_id,
                status,
                submission.reused,
            )
        return submission

    def submit_graph(
        self,
        *,
        compiled_research_graph_id: uuid.UUID,
        submission_key: uuid.UUID,
        requested_by: str,
        suite_mode: Literal["exploratory"] = "exploratory",
    ) -> SuiteRuntimeSubmission:
        """Public first-slice adapter; date and executor identity stay server-owned."""

        return self.submit_command(
            self._server_owned_command(
                compiled_research_graph_id=compiled_research_graph_id,
                submission_key=submission_key,
                requested_by=requested_by,
                suite_mode=suite_mode,
            )
        )

    def process_suite(self, research_suite_id: uuid.UUID) -> SuiteRuntimeSubmission:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT compiled_research_graph_id,owner_key,suite_mode
                      FROM experiment.v022_research_suite
                     WHERE research_suite_id=:suite
                    """
                    ),
                    {"suite": research_suite_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError(f"Research Suite not found: {research_suite_id}")
        command = self._server_owned_command(
            compiled_research_graph_id=row["compiled_research_graph_id"],
            submission_key=uuid.UUID(int=0),
            requested_by=row["owner_key"],
            suite_mode=cast(Literal["exploratory"], row["suite_mode"]),
            research_suite_id=research_suite_id,
        )
        return self._process_published_suite(research_suite_id, command)

    def _server_owned_command(
        self,
        *,
        compiled_research_graph_id: uuid.UUID,
        submission_key: uuid.UUID,
        requested_by: str,
        suite_mode: Literal["exploratory"],
        research_suite_id: uuid.UUID | None = None,
    ) -> SuiteRuntimeSubmitCommand:
        with self._engine.connect() as connection:
            cohort = _bound_or_default_evaluation_cohort(
                connection,
                compiled_research_graph_id=compiled_research_graph_id,
                research_suite_id=research_suite_id,
            )
        runtime_version = SUITE_RUNTIME_EXECUTOR_VERSION
        return SuiteRuntimeSubmitCommand(
            compiled_research_graph_id=compiled_research_graph_id,
            submission_key=submission_key,
            requested_by=requested_by,
            requested_range={
                "start": cohort["evaluation_start"].isoformat(),
                "end": cohort["evaluation_end"].isoformat(),
            },
            executor_version=runtime_version,
            environment_fingerprint=sha256_hexdigest(
                {"contract_version": CONTRACT_VERSION, "runtime": runtime_version}
            ),
            suite_mode=suite_mode,
            evaluation_cohort_version_id=cohort["evaluation_cohort_version_id"],
            materialization_range={
                "start": cohort["warmup_start"].isoformat(),
                "end": cohort["evaluation_end"].isoformat(),
            },
        )

    def replay(
        self,
        *,
        actor_key: str,
        idempotency_key: uuid.UUID,
        compiled_research_graph_id: uuid.UUID,
        suite_mode: Literal["exploratory"],
    ) -> dict[str, Any] | None:
        suite_key = _suite_key(actor_key, idempotency_key)
        with self._engine.connect() as connection:
            row = _public_suite_row(connection, suite_key=suite_key)
            if row is None:
                return None
            if (
                row["compiled_research_graph_id"] != compiled_research_graph_id
                or row["suite_mode"] != suite_mode
            ):
                raise ValueError("Suite idempotency key already has different semantics")
            return _public_submit_document(row, reused=True)

    def submit(
        self,
        *,
        actor_key: str,
        idempotency_key: uuid.UUID,
        compiled_research_graph_id: uuid.UUID,
        suite_mode: Literal["exploratory"],
    ) -> dict[str, Any]:
        self._suites.publish(
            compiled_research_graph_id=compiled_research_graph_id,
            submission_key=idempotency_key,
            actor_key=actor_key,
            suite_mode=suite_mode,
        )
        with self._engine.connect() as connection:
            row = _public_suite_row(connection, suite_key=_suite_key(actor_key, idempotency_key))
        if row is None:
            raise RuntimeError("Submitted Suite identity is unavailable")
        return _public_submit_document(row, reused=False)

    def status(self, research_suite_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT suite.research_suite_id,suite.compiled_research_graph_id,
                           suite.suite_mode,run.status AS run_status,
                           (SELECT count(*)
                              FROM workspace.v022_graph_work_consumer consumer
                             WHERE consumer.graph_run_id=run.graph_run_id) AS total,
                           (SELECT count(*)
                              FROM workspace.v022_graph_work_consumer consumer
                              JOIN workspace.v022_graph_work_item work
                                ON work.graph_work_item_id=
                                   consumer.graph_work_item_id
                             WHERE consumer.graph_run_id=run.graph_run_id
                               AND work.status IN (
                                 'completed','reused','failed','cancelled',
                                 'blocked_upstream_failed',
                                 'blocked_upstream_cancelled'
                               )) AS terminal,
                           (SELECT coalesce(
                              jsonb_object_agg(summary.status,summary.item_count),
                              '{}'::jsonb)
                              FROM (
                                SELECT work.status,count(*) AS item_count
                                  FROM workspace.v022_graph_work_consumer consumer
                                  JOIN workspace.v022_graph_work_item work
                                    ON work.graph_work_item_id=
                                       consumer.graph_work_item_id
                                 WHERE consumer.graph_run_id=run.graph_run_id
                                 GROUP BY work.status
                              ) summary) AS status_counts,
                           (SELECT coalesce(
                              jsonb_object_agg(summary.work_kind,summary.item_count),
                              '{}'::jsonb)
                              FROM (
                                SELECT work.work_kind,count(*) AS item_count
                                  FROM workspace.v022_graph_work_consumer consumer
                                  JOIN workspace.v022_graph_work_item work
                                    ON work.graph_work_item_id=
                                       consumer.graph_work_item_id
                                 WHERE consumer.graph_run_id=run.graph_run_id
                                   AND (
                                     work.status='running' OR
                                     work.status='queued' AND NOT EXISTS (
                                       SELECT 1
                                         FROM workspace.v022_graph_work_dependency dependency
                                         JOIN workspace.v022_graph_work_item upstream
                                           ON upstream.graph_work_item_id=
                                              dependency.upstream_work_item_id
                                        WHERE dependency.downstream_work_item_id=
                                              work.graph_work_item_id
                                          AND dependency.dependency_kind='required'
                                          AND upstream.status NOT IN (
                                            'completed','reused'
                                          )
                                     )
                                   )
                                 GROUP BY work.work_kind
                              ) summary) AS active_kinds
                      FROM experiment.v022_research_suite suite
                      LEFT JOIN experiment.v022_research_suite_graph_run_binding binding
                        ON binding.research_suite_id=suite.research_suite_id
                      LEFT JOIN workspace.v022_graph_run run
                        ON run.graph_run_id=binding.graph_run_id
                     WHERE suite.research_suite_id=:suite
                     ORDER BY run.created_at DESC
                     LIMIT 1
                    """
                    ),
                    {"suite": research_suite_id},
                )
                .mappings()
                .one_or_none()
            )
            materialization = None
            if row is not None and row["run_status"] is None:
                materialization = (
                    connection.execute(
                        text(
                            """
                        WITH latest_node AS (
                          SELECT DISTINCT ON (binding.compiled_graph_node_id)
                                 binding.compiled_graph_node_id,node_run.status
                            FROM processing.graph_run_node_binding binding
                            JOIN workspace.v022_graph_run graph_run
                              ON graph_run.graph_run_id=binding.graph_run_id
                            JOIN processing.node_run node_run
                              ON node_run.node_run_id=binding.node_run_id
                           WHERE graph_run.compiled_research_graph_id=:graph
                           ORDER BY binding.compiled_graph_node_id,
                                    graph_run.created_at DESC,node_run.created_at DESC
                        ), status_summary AS (
                          SELECT status,count(*) AS item_count
                            FROM latest_node GROUP BY status
                        )
                        SELECT (
                                 SELECT count(*) FROM workspace.compiled_graph_node node
                                  WHERE node.compiled_research_graph_id=:graph
                               ) AS total,
                               (SELECT count(*) FROM latest_node) AS bound,
                               (
                                 SELECT count(*) FROM latest_node
                                  WHERE status IN ('completed','failed','cancelled')
                               ) AS terminal,
                               coalesce(
                                 (SELECT jsonb_object_agg(status,item_count)
                                    FROM status_summary),
                                 '{}'::jsonb
                               ) AS status_counts
                        """
                        ),
                        {"graph": row["compiled_research_graph_id"]},
                    )
                    .mappings()
                    .one()
                )
        if row is None:
            raise LookupError(f"Research Suite not found: {research_suite_id}")
        has_run = row["run_status"] is not None
        if not has_run and materialization is not None and int(materialization["bound"]) > 0:
            total = int(materialization["total"])
            terminal = int(materialization["terminal"])
            status_counts = dict(materialization["status_counts"])
            queued = max(total - sum(int(value) for value in status_counts.values()), 0)
            if queued:
                status_counts["queued"] = queued
            status = "materializing"
        else:
            total = int(row["total"])
            terminal = int(row["terminal"])
            status_counts = dict(row["status_counts"])
            status = _public_status(row["run_status"], row["active_kinds"])
        return {
            "research_suite_id": row["research_suite_id"],
            "compiled_research_graph_id": row["compiled_research_graph_id"],
            "status": status,
            "total": total,
            "terminal": terminal,
            "complete": has_run and total > 0 and terminal == total,
            "status_counts": status_counts,
            "suite_mode": row["suite_mode"],
        }

    def results(self, research_suite_id: uuid.UUID) -> dict[str, Any]:
        status = self.status(research_suite_id)
        if not status["complete"]:
            raise GraphSuiteResultsNotReady(status)
        with self._engine.connect() as connection:
            expected = connection.scalar(
                text(
                    "SELECT cell_count FROM experiment.v022_research_suite "
                    "WHERE research_suite_id=:suite"
                ),
                {"suite": research_suite_id},
            )
            rows = (
                connection.execute(
                    text(
                        """
                    WITH selected_run AS (
                      SELECT binding.graph_run_id
                        FROM experiment.v022_research_suite_graph_run_binding binding
                        JOIN workspace.v022_graph_run run
                          ON run.graph_run_id=binding.graph_run_id
                       WHERE binding.research_suite_id=:suite
                       ORDER BY binding.binding_ordinal DESC
                       LIMIT 1
                    )
                    SELECT spec.research_cell_id,spec.research_suite_branch_id,
                           spec.compiled_strategy_branch_id,
                           spec.configuration_snapshot_id,
                           spec.portfolio_evaluation_data_context_id,
                           result.artifact_id AS result_artifact_id,
                           result.payload_manifest_id,
                           result.payload_manifest_artifact_id,
                           result.result_fingerprint,
                           result.logical_payload_fingerprint,
                           result.manifest_hash,result.outcome,result.quality_status,
                           result.effective_start,result.effective_end,
                           result.metric_document,result.result_document,
                           evidence.result_evidence_snapshot_id,
                           evidence_artifact.artifact_id AS result_evidence_artifact_id,
                           evidence.evidence_fingerprint,evidence.evidence_class,
                           evidence.common_evaluation_panel_id,
                           panel.panel_fingerprint AS common_evaluation_panel_fingerprint,
                           COALESCE((
                             SELECT jsonb_agg(
                                      jsonb_build_object(
                                        'result_element_diagnostic_id',
                                          element.result_element_diagnostic_id,
                                        'artifact_id',element.artifact_id,
                                        'diagnostic_fingerprint',
                                          element.diagnostic_fingerprint,
                                        'diagnostic_document',
                                          element.diagnostic_document
                                      ) ORDER BY
                                        element.diagnostic_document->>'stage_no',
                                        element.diagnostic_document->>'feature_variant_key'
                                    )
                               FROM experiment.v022_result_element_diagnostic element
                               JOIN lineage.artifact element_artifact
                                 ON element_artifact.artifact_id=element.artifact_id
                                AND element_artifact.status='published'
                              WHERE element.result_artifact_id=result.artifact_id
                           ),'[]'::jsonb) AS element_diagnostics
                      FROM selected_run selected
                      JOIN experiment.v022_suite_runtime_plan plan
                        ON plan.graph_run_id=selected.graph_run_id
                      JOIN experiment.v022_portfolio_cell_work_spec spec
                        ON spec.suite_runtime_plan_id=plan.suite_runtime_plan_id
                      JOIN experiment.v022_portfolio_cell_runtime_result result
                        ON result.graph_work_item_id=spec.graph_work_item_id
                      JOIN lineage.artifact result_artifact
                        ON result_artifact.artifact_id=result.artifact_id
                       AND result_artifact.status='published'
                      JOIN lineage.artifact manifest_artifact
                        ON manifest_artifact.artifact_id=
                           result.payload_manifest_artifact_id
                       AND manifest_artifact.status='published'
                      JOIN data.payload_manifest manifest
                        ON manifest.payload_manifest_id=result.payload_manifest_id
                       AND manifest.materialization_state='materialized'
                      LEFT JOIN experiment.v022_result_evidence_snapshot evidence
                        ON evidence.result_artifact_id=result.artifact_id
                      LEFT JOIN lineage.artifact evidence_artifact
                        ON evidence_artifact.artifact_id=evidence.artifact_id
                       AND evidence_artifact.status='published'
                      LEFT JOIN experiment.v022_common_evaluation_panel panel
                        ON panel.common_evaluation_panel_id=
                           evidence.common_evaluation_panel_id
                     ORDER BY spec.research_cell_id
                    """
                    ),
                    {"suite": research_suite_id},
                )
                .mappings()
                .all()
            )
        if not isinstance(expected, int) or expected < 1:
            raise RuntimeError(f"v0.22 Suite has invalid Cell count: {research_suite_id}")
        results = [_result_item(row) for row in rows]
        return {
            "research_suite_id": status["research_suite_id"],
            "compiled_research_graph_id": status["compiled_research_graph_id"],
            "status": status["status"],
            "complete": status["complete"],
            "expected_result_count": expected,
            "result_count": len(results),
            "results": results,
        }

    def list_suites(self, *, limit: int, offset: int) -> dict[str, Any]:
        if limit < 1 or limit > 100 or offset < 0:
            raise ValueError("Suite history pagination is outside the supported range")
        with self._engine.connect() as connection:
            total_count = connection.scalar(
                text(
                    """
                    SELECT count(*)
                      FROM experiment.v022_research_suite suite
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=suite.artifact_id
                       AND artifact.status='published'
                      JOIN experiment.v022_suite_launch_batch_child launch_child
                        ON launch_child.research_suite_id=suite.research_suite_id
                      JOIN experiment.v022_suite_launch_batch_round batch_round
                        ON batch_round.suite_launch_batch_id=
                           launch_child.suite_launch_batch_id
                      JOIN workspace.v022_research_round research_round
                        ON research_round.research_round_id=batch_round.research_round_id
                       AND research_round.status='active'
                    """
                )
            )
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT suite.research_suite_id,suite.compiled_research_graph_id,
                           graph.graph_fingerprint,suite.suite_fingerprint,
                           suite.branch_count,suite.cell_count,suite.suite_mode,
                           suite.created_at,run.status AS run_status,
                           (SELECT count(*)
                              FROM workspace.v022_graph_work_consumer consumer
                             WHERE consumer.graph_run_id=run.graph_run_id) AS total,
                           (SELECT count(*)
                              FROM workspace.v022_graph_work_consumer consumer
                              JOIN workspace.v022_graph_work_item work
                                ON work.graph_work_item_id=consumer.graph_work_item_id
                             WHERE consumer.graph_run_id=run.graph_run_id
                               AND work.status IN (
                                 'completed','reused','failed','cancelled',
                                 'blocked_upstream_failed','blocked_upstream_cancelled'
                               )) AS terminal,
                           (SELECT coalesce(
                              jsonb_object_agg(summary.status,summary.item_count),
                              '{}'::jsonb)
                              FROM (
                                SELECT work.status,count(*) AS item_count
                                  FROM workspace.v022_graph_work_consumer consumer
                                  JOIN workspace.v022_graph_work_item work
                                    ON work.graph_work_item_id=consumer.graph_work_item_id
                                 WHERE consumer.graph_run_id=run.graph_run_id
                                 GROUP BY work.status
                              ) summary) AS status_counts,
                           (SELECT coalesce(
                              jsonb_object_agg(summary.work_kind,summary.item_count),
                              '{}'::jsonb)
                              FROM (
                                SELECT work.work_kind,count(*) AS item_count
                                  FROM workspace.v022_graph_work_consumer consumer
                                  JOIN workspace.v022_graph_work_item work
                                    ON work.graph_work_item_id=consumer.graph_work_item_id
                                 WHERE consumer.graph_run_id=run.graph_run_id
                                   AND (
                                     work.status='running' OR
                                     work.status='queued' AND NOT EXISTS (
                                       SELECT 1
                                         FROM workspace.v022_graph_work_dependency dependency
                                         JOIN workspace.v022_graph_work_item upstream
                                           ON upstream.graph_work_item_id=
                                              dependency.upstream_work_item_id
                                        WHERE dependency.downstream_work_item_id=
                                              work.graph_work_item_id
                                          AND dependency.dependency_kind='required'
                                          AND upstream.status NOT IN ('completed','reused')
                                     )
                                   )
                                 GROUP BY work.work_kind
                              ) summary) AS active_kinds
                      FROM experiment.v022_research_suite suite
                      JOIN workspace.compiled_research_graph graph
                        ON graph.compiled_research_graph_id=
                           suite.compiled_research_graph_id
                      JOIN lineage.artifact suite_artifact
                        ON suite_artifact.artifact_id=suite.artifact_id
                       AND suite_artifact.status='published'
                      JOIN experiment.v022_suite_launch_batch_child launch_child
                        ON launch_child.research_suite_id=suite.research_suite_id
                      JOIN experiment.v022_suite_launch_batch_round batch_round
                        ON batch_round.suite_launch_batch_id=
                           launch_child.suite_launch_batch_id
                      JOIN workspace.v022_research_round research_round
                        ON research_round.research_round_id=batch_round.research_round_id
                       AND research_round.status='active'
                      LEFT JOIN LATERAL (
                        SELECT graph_run.status,graph_run.graph_run_id
                          FROM experiment.v022_research_suite_graph_run_binding binding
                          JOIN workspace.v022_graph_run graph_run
                            ON graph_run.graph_run_id=binding.graph_run_id
                         WHERE binding.research_suite_id=suite.research_suite_id
                         ORDER BY binding.binding_ordinal DESC
                         LIMIT 1
                      ) run ON true
                     ORDER BY suite.created_at DESC,suite.research_suite_id DESC
                     LIMIT :limit OFFSET :offset
                    """
                    ),
                    {"limit": limit, "offset": offset},
                )
                .mappings()
                .all()
            )
        items = []
        for row in rows:
            total = int(row["total"])
            terminal = int(row["terminal"])
            has_run = row["run_status"] is not None
            items.append(
                {
                    "research_suite_id": row["research_suite_id"],
                    "compiled_research_graph_id": row["compiled_research_graph_id"],
                    "graph_fingerprint": row["graph_fingerprint"],
                    "suite_fingerprint": row["suite_fingerprint"],
                    "status": _public_status(row["run_status"], row["active_kinds"] or {}),
                    "total": total,
                    "terminal": terminal,
                    "complete": has_run and total > 0 and terminal == total,
                    "status_counts": dict(row["status_counts"] or {}),
                    "strategy_branch_count": row["branch_count"],
                    "backtest_cell_count": row["cell_count"],
                    "suite_mode": row["suite_mode"],
                    "created_at": row["created_at"],
                }
            )
        return {
            "items": items,
            "total_count": int(total_count or 0),
            "limit": limit,
            "offset": offset,
        }


class GraphSuiteCommandsAdapter:
    """Structural adapter for ``api.app.GraphSuiteCommands``."""

    def __init__(self, service: SuiteRuntimeCommandService) -> None:
        self._service = service

    def replay(self, **kwargs: Any) -> dict[str, Any] | None:
        return self._service.replay(**kwargs)

    def submit(self, **kwargs: Any) -> dict[str, Any]:
        return self._service.submit(**kwargs)

    def status(self, research_suite_id: uuid.UUID) -> dict[str, Any]:
        return self._service.status(research_suite_id)

    def results(self, research_suite_id: uuid.UUID) -> dict[str, Any]:
        return self._service.results(research_suite_id)

    def list_suites(self, *, limit: int, offset: int) -> dict[str, Any]:
        return self._service.list_suites(limit=limit, offset=offset)


class GraphSuiteResultsNotReady(RuntimeError):
    def __init__(self, status: dict[str, Any]) -> None:
        self.status = str(status["status"])
        self.total = int(status["total"])
        self.terminal = int(status["terminal"])
        super().__init__(
            "v0.22 Graph Suite results are not ready: "
            f"status={self.status}, terminal={self.terminal}/{self.total}"
        )


def _result_item(row: RowMapping) -> dict[str, Any]:
    item = dict(row)
    document = _mapping(item["result_document"])
    quality = _mapping(document.get("quality"))
    context = _mapping(document.get("evaluation_context"))
    execution_identity = _mapping(document.get("execution_identity"))
    benchmark = _mapping(context.get("benchmark_identity"))
    cost = _mapping(context.get("cost_policy_identity"))
    net_path = document.get("net_path")
    evidence_published = item["result_evidence_artifact_id"] is not None
    item["diagnostic"] = {
        "metrics": _diagnostic_metrics(item["metric_document"]),
        "quality": {
            "outcome": item["outcome"],
            "status": item["quality_status"],
            "reason_code": _optional_text(quality.get("reason_code")),
            "details": dict(_mapping(quality.get("details"))),
            "path_session_count": len(net_path) if isinstance(net_path, list) else 0,
        },
        "execution": {
            "benchmark_asset_id": benchmark.get("asset_id"),
            "benchmark_asset_key": _optional_text(benchmark.get("asset_key")),
            "cost_policy_key": _optional_text(cost.get("policy_key")),
            "basis_points_per_side": _optional_text(cost.get("basis_points_per_side")),
            "execution_delay_sessions": context.get("execution_delay_sessions"),
            "evaluation_input_cutoff_at": context.get("evaluation_input_cutoff_at"),
            "work_execution_fingerprint": _optional_text(
                execution_identity.get("work_execution_fingerprint")
            ),
            "evaluation_data_context_fingerprint": _optional_text(
                execution_identity.get("evaluation_data_context_fingerprint")
            ),
        },
        "evidence": {
            "publication_status": "published" if evidence_published else "not_published",
            "result_evidence_snapshot_id": (
                item["result_evidence_snapshot_id"] if evidence_published else None
            ),
            "result_evidence_artifact_id": (
                item["result_evidence_artifact_id"] if evidence_published else None
            ),
            "evidence_fingerprint": (item["evidence_fingerprint"] if evidence_published else None),
            "evidence_class": item["evidence_class"] if evidence_published else None,
            "common_evaluation_panel_id": (
                item["common_evaluation_panel_id"] if evidence_published else None
            ),
            "common_evaluation_panel_fingerprint": (
                item["common_evaluation_panel_fingerprint"] if evidence_published else None
            ),
        },
        "elements": list(item.pop("element_diagnostics", [])),
    }
    for key in (
        "result_evidence_snapshot_id",
        "result_evidence_artifact_id",
        "evidence_fingerprint",
        "evidence_class",
        "common_evaluation_panel_id",
        "common_evaluation_panel_fingerprint",
    ):
        item.pop(key)
    return item


def _diagnostic_metrics(value: Any) -> list[dict[str, Any]]:
    document = _mapping(value)
    metrics: list[dict[str, Any]] = []
    for group, key in (
        ("absolute", "absolute_metrics"),
        ("relative", "relative_metrics"),
    ):
        rows = document.get(key)
        if not isinstance(rows, list):
            continue
        for row_value in rows:
            row = _mapping(row_value)
            metric_value = row.get("value")
            metrics.append(
                {
                    "metric_group": group,
                    "metric_key": str(row.get("metric_key", "")),
                    "value": str(metric_value) if metric_value is not None else None,
                    "value_status": ("defined" if metric_value is not None else "unavailable"),
                    "reason_code": _optional_text(row.get("reason_code")),
                    "observation_count": int(row.get("observation_count", 0)),
                }
            )
    return metrics


def _mapping(value: Any) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _suite_key(actor_key: str, idempotency_key: uuid.UUID) -> str:
    scope = sha256_hexdigest({"actor_key": actor_key, "submission_key": str(idempotency_key)})
    return f"v022_graph_suite__{scope}"


def _public_suite_row(connection: Connection, *, suite_key: str) -> RowMapping | None:
    return (
        connection.execute(
            text(
                """
            SELECT suite.research_suite_id,suite.artifact_id AS suite_artifact_id,
                   suite.compiled_research_graph_id,suite.suite_fingerprint,
                   suite.branch_count,suite.cell_count,suite.suite_mode,
                   graph.graph_fingerprint,run.status AS run_status,
                   plan.suite_runtime_plan_id
              FROM experiment.v022_research_suite suite
              JOIN workspace.compiled_research_graph graph
                ON graph.compiled_research_graph_id=suite.compiled_research_graph_id
              JOIN lineage.artifact suite_artifact
                ON suite_artifact.artifact_id=suite.artifact_id
               AND suite_artifact.status='published'
              LEFT JOIN experiment.v022_research_suite_graph_run_binding binding
                ON binding.research_suite_id=suite.research_suite_id
              LEFT JOIN workspace.v022_graph_run run
                ON run.graph_run_id=binding.graph_run_id
              LEFT JOIN experiment.v022_suite_runtime_plan plan
                ON plan.graph_run_id=run.graph_run_id
             WHERE suite.suite_key=:key
             ORDER BY run.created_at DESC NULLS LAST
             LIMIT 1
            """
            ),
            {"key": suite_key},
        )
        .mappings()
        .one_or_none()
    )


def _public_submit_document(row: RowMapping, *, reused: bool) -> dict[str, Any]:
    return {
        "research_suite_id": row["research_suite_id"],
        "suite_artifact_id": row["suite_artifact_id"],
        "compiled_research_graph_id": row["compiled_research_graph_id"],
        "graph_fingerprint": row["graph_fingerprint"],
        "suite_fingerprint": row["suite_fingerprint"],
        "strategy_branch_count": row["branch_count"],
        "backtest_cell_count": row["cell_count"],
        "status": _public_status(row["run_status"], {}),
        "reused": reused,
        "suite_mode": row["suite_mode"],
    }


def _public_status(run_status: object | None, counts: Mapping[str, int]) -> str:
    if run_status is None:
        return "not_started"
    run_status = str(run_status)
    if run_status in {"completed", "failed", "cancelled"}:
        return run_status
    if counts.get("portfolio_cell", 0):
        return "evaluating"
    if counts.get("sleeve_merge", 0):
        return "merging"
    if counts.get("strategy_target", 0) or counts.get("defense_decision", 0):
        return "targeting"
    return "materializing"


def _default_evaluation_cohort(
    connection: Connection, compiled_research_graph_id: uuid.UUID
) -> RowMapping:
    rows = (
        connection.execute(
            text(
                """
            SELECT cohort.evaluation_cohort_version_id,cohort.warmup_start,
                   cohort.evaluation_start,cohort.evaluation_end,
                   cohort.cohort_fingerprint
              FROM workspace.compiled_research_graph graph
              JOIN experiment.v022_evaluation_cohort_version cohort
                ON cohort.frequency=graph.frequency
              JOIN lineage.artifact artifact ON artifact.artifact_id=cohort.artifact_id
              JOIN experiment.v022_evaluation_cohort_runtime_contract runtime
                ON runtime.evaluation_cohort_version_id=
                   cohort.evaluation_cohort_version_id
              JOIN lineage.artifact runtime_artifact
                ON runtime_artifact.artifact_id=runtime.artifact_id
              JOIN data.v022_dataset_gate_assessment gate
                ON gate.dataset_gate_assessment_id=
                   runtime.dataset_gate_assessment_id
              JOIN lineage.artifact gate_artifact
                ON gate_artifact.artifact_id=gate.artifact_id
             WHERE graph.compiled_research_graph_id=:graph
               AND cohort.research_tier='rankable_research'
               AND cohort.cohort_key=
                   'sp500_free_research_2007_2026_' || graph.frequency ||
                   '_v' || :cohort_version
               AND cohort.version_number=:cohort_version
               AND artifact.status='published'
               AND runtime_artifact.status='published'
               AND gate_artifact.status='published'
               AND runtime.ranking_eligibility='rankable_research'
               AND gate.ranking_eligibility=cohort.research_tier
               AND gate.dataset_publication_id=cohort.dataset_publication_id
               AND gate.universe_history_id=cohort.universe_history_id
               AND gate.security_market_quality_report_id=
                   cohort.security_market_quality_report_id
               AND gate.calendar_version_id=cohort.calendar_version_id
            """
            ),
            {
                "graph": compiled_research_graph_id,
                "cohort_version": FROZEN_SP500_COHORT_VERSION,
            },
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise ValueError(
            "Compiled Graph requires exactly one published rankable Evaluation Cohort "
            f"for its frequency; found {len(rows)}"
        )
    return rows[0]


def _bound_or_default_evaluation_cohort(
    connection: Connection,
    *,
    compiled_research_graph_id: uuid.UUID,
    research_suite_id: uuid.UUID | None,
) -> RowMapping:
    """Preserve a published Suite's immutable Cohort across worker upgrades."""

    if research_suite_id is None:
        return _default_evaluation_cohort(connection, compiled_research_graph_id)
    row = (
        connection.execute(
            text(
                """
            SELECT cohort.evaluation_cohort_version_id,cohort.warmup_start,
                   cohort.evaluation_start,cohort.evaluation_end,
                   cohort.cohort_fingerprint
              FROM experiment.v022_research_suite_evaluation_cohort_binding binding
              JOIN experiment.v022_evaluation_cohort_version cohort
                ON cohort.evaluation_cohort_version_id=
                   binding.evaluation_cohort_version_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=cohort.artifact_id
             WHERE binding.research_suite_id=:suite
               AND artifact.status='published'
            """
            ),
            {"suite": research_suite_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is not None:
        return row
    return _default_evaluation_cohort(connection, compiled_research_graph_id)


def _bind_evaluation_cohort(
    connection: Connection,
    *,
    research_suite_id: uuid.UUID,
    evaluation_cohort_version_id: uuid.UUID,
    bound_by: str,
) -> None:
    existing = (
        connection.execute(
            text(
                """
            SELECT binding.evaluation_cohort_version_id,
                   binding.binding_fingerprint,cohort.frequency
              FROM experiment.v022_research_suite_evaluation_cohort_binding binding
              JOIN experiment.v022_evaluation_cohort_version cohort
                ON cohort.evaluation_cohort_version_id=
                   binding.evaluation_cohort_version_id
             WHERE binding.research_suite_id=:suite
            """
            ),
            {"suite": research_suite_id},
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        document = {
            "contract_version": "v0.22.suite_evaluation_cohort_binding.v1",
            "research_suite_id": str(research_suite_id),
            "evaluation_cohort_version_id": str(evaluation_cohort_version_id),
            "frequency": existing["frequency"],
        }
        fingerprint = sha256_hexdigest(document)
        if (
            existing["evaluation_cohort_version_id"] != evaluation_cohort_version_id
            or existing["binding_fingerprint"] != fingerprint
        ):
            raise ValueError("Research Suite is already bound to another Evaluation Cohort")
        # A published Suite keeps its immutable Cohort binding across upgrades.
        return

    row = (
        connection.execute(
            text(
                """
            SELECT cohort.frequency
              FROM experiment.v022_evaluation_cohort_version cohort
              JOIN lineage.artifact artifact ON artifact.artifact_id=cohort.artifact_id
              JOIN experiment.v022_evaluation_cohort_runtime_contract runtime
                ON runtime.evaluation_cohort_version_id=
                   cohort.evaluation_cohort_version_id
              JOIN lineage.artifact runtime_artifact
                ON runtime_artifact.artifact_id=runtime.artifact_id
              JOIN data.v022_dataset_gate_assessment gate
                ON gate.dataset_gate_assessment_id=
                   runtime.dataset_gate_assessment_id
              JOIN lineage.artifact gate_artifact
                ON gate_artifact.artifact_id=gate.artifact_id
             WHERE cohort.evaluation_cohort_version_id=:cohort
               AND artifact.status='published'
               AND runtime_artifact.status='published'
               AND gate_artifact.status='published'
               AND runtime.ranking_eligibility='rankable_research'
               AND gate.ranking_eligibility=cohort.research_tier
               AND gate.dataset_publication_id=cohort.dataset_publication_id
               AND gate.universe_history_id=cohort.universe_history_id
               AND gate.security_market_quality_report_id=
                   cohort.security_market_quality_report_id
               AND gate.calendar_version_id=cohort.calendar_version_id
            """
            ),
            {"cohort": evaluation_cohort_version_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Evaluation Cohort is not admitted by an exact published runtime and Gate")
    document = {
        "contract_version": "v0.22.suite_evaluation_cohort_binding.v1",
        "research_suite_id": str(research_suite_id),
        "evaluation_cohort_version_id": str(evaluation_cohort_version_id),
        "frequency": row["frequency"],
    }
    fingerprint = sha256_hexdigest(document)
    connection.execute(
        text(
            """
            INSERT INTO experiment.v022_research_suite_evaluation_cohort_binding (
              research_suite_id,evaluation_cohort_version_id,frequency,
              binding_fingerprint,bound_by
            ) VALUES (:suite,:cohort,:frequency,:fingerprint,:actor)
            """
        ),
        {
            "suite": research_suite_id,
            "cohort": evaluation_cohort_version_id,
            "frequency": row["frequency"],
            "fingerprint": fingerprint,
            "actor": bound_by,
        },
    )


def _publish_evaluation_contexts(
    connection: Connection,
    *,
    artifacts: ArtifactService,
    research_suite_id: uuid.UUID,
) -> None:
    policy = (
        connection.execute(
            text(
                """
            SELECT suite.evaluation_matrix_policy_id,policy.artifact_id,
                   context.ordinal,context.context_fingerprint
              FROM experiment.v022_research_suite suite
              JOIN experiment.v022_evaluation_matrix_policy policy
                ON policy.evaluation_matrix_policy_id=
                   suite.evaluation_matrix_policy_id
              JOIN experiment.v022_evaluation_matrix_policy_context context
                ON context.evaluation_matrix_policy_id=
                   policy.evaluation_matrix_policy_id
             WHERE suite.research_suite_id=:suite
             ORDER BY context.ordinal
            """
            ),
            {"suite": research_suite_id},
        )
        .mappings()
        .all()
    )
    if not policy:
        raise ValueError("Suite evaluation policy is empty")
    benchmark = _benchmark_input(connection, research_suite_id)
    bundle = _reserve_input(connection, research_suite_id, benchmark=benchmark)
    benchmark_asset_id = cast(
        uuid.UUID,
        connection.scalar(text("SELECT asset_id FROM catalog.asset WHERE asset_key='spy'")),
    )
    for row in policy:
        publication = _publish_evaluation_context(
            connection,
            artifacts=artifacts,
            policy_id=row["evaluation_matrix_policy_id"],
            policy_artifact_id=row["artifact_id"],
            context_ordinal=row["ordinal"],
            policy_context_fingerprint=row["context_fingerprint"],
            benchmark_asset_id=benchmark_asset_id,
            benchmark=benchmark,
            bundle=bundle,
        )
        cells = connection.scalars(
            text(
                """
                SELECT research_cell_id FROM experiment.v022_research_cell
                 WHERE research_suite_id=:suite
                   AND evaluation_context_ordinal=:ordinal
                 ORDER BY ordinal
                """
            ),
            {"suite": research_suite_id, "ordinal": row["ordinal"]},
        ).all()
        for cell_id in cells:
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.v022_research_cell_evaluation_data_context_binding (
                      research_cell_id,portfolio_evaluation_data_context_id
                    ) VALUES (:cell,:context)
                    ON CONFLICT (research_cell_id) DO NOTHING
                    """
                ),
                {"cell": cell_id, "context": publication.context_id},
            )


def _benchmark_input(connection: Connection, research_suite_id: uuid.UUID) -> _EvaluationInput:
    rows = (
        connection.execute(
            text(
                """
            SELECT DISTINCT publication.dataset_publication_id,
                   publication.artifact_id AS dataset_artifact_id,
                   artifact.semantic_fingerprint AS dataset_fingerprint,
                   publication.calendar_version_id,calendar.artifact_id AS calendar_artifact_id,
                   publication.coverage_start,publication.coverage_end
              FROM experiment.v022_research_suite suite
              JOIN experiment.v022_research_suite_evaluation_cohort_binding cohort_binding
                ON cohort_binding.research_suite_id=suite.research_suite_id
              JOIN experiment.v022_evaluation_cohort_version cohort
                ON cohort.evaluation_cohort_version_id=
                   cohort_binding.evaluation_cohort_version_id
              JOIN data.dataset_publication publication
                ON publication.dataset_publication_id=
                   cohort.benchmark_dataset_publication_id
               AND publication.artifact_id=cohort.benchmark_dataset_artifact_id
               AND publication.value_kind='daily_bar'
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=publication.artifact_id
               AND artifact.status='published'
              JOIN catalog.calendar_version calendar
                ON calendar.calendar_version_id=publication.calendar_version_id
              JOIN lineage.artifact calendar_artifact
                ON calendar_artifact.artifact_id=calendar.artifact_id
               AND calendar_artifact.status='published'
              JOIN catalog.asset spy
                ON spy.asset_key='spy'
             WHERE suite.research_suite_id=:suite
               AND EXISTS (
                 SELECT 1 FROM data.daily_bar bar
                  WHERE bar.dataset_publication_id=publication.dataset_publication_id
                    AND bar.asset_id=spy.asset_id
               )
            """
            ),
            {"suite": research_suite_id},
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise ValueError("Suite requires one unambiguous exact SPY daily-bar evaluation input")
    return _evaluation_input(rows[0])


def _reserve_input(
    connection: Connection,
    research_suite_id: uuid.UUID,
    *,
    benchmark: _EvaluationInput,
) -> _EvaluationBundle:
    rows = (
        connection.execute(
            text(
                """
            SELECT bundle.data_bundle_version_id,
                   bundle.artifact_id AS bundle_artifact_id,
                   publication.dataset_publication_id,
                   publication.artifact_id AS dataset_artifact_id,
                   dataset.semantic_fingerprint AS dataset_fingerprint,
                   publication.calendar_version_id,
                   calendar.artifact_id AS calendar_artifact_id,
                   publication.coverage_start,publication.coverage_end,
                   model.reserve_return_model_version_id,
                   model.artifact_id AS reserve_return_model_artifact_id
              FROM experiment.v022_research_suite suite
              JOIN data.data_bundle_member market_member
                ON market_member.dataset_publication_id=:benchmark_publication
               AND market_member.role='canonical_market'
              JOIN data.data_bundle_version bundle
                ON bundle.data_bundle_version_id=
                   market_member.data_bundle_version_id
              JOIN data.data_bundle_definition definition
                ON definition.data_bundle_definition_id=
                   bundle.data_bundle_definition_id
               AND definition.bundle_key=:bundle_key
              JOIN lineage.artifact bundle_artifact
                ON bundle_artifact.artifact_id=bundle.artifact_id
               AND bundle_artifact.status='published'
              JOIN data.data_bundle_member reserve_member
                ON reserve_member.data_bundle_version_id=
                   bundle.data_bundle_version_id
               AND reserve_member.role='reserve_return'
              JOIN data.dataset_publication publication
                ON publication.dataset_publication_id=
                   reserve_member.dataset_publication_id
               AND publication.dataset_key='dgs3mo_reserve_return'
               AND publication.value_kind='reserve_return'
              JOIN lineage.artifact dataset
                ON dataset.artifact_id=publication.artifact_id
               AND dataset.status='published'
              JOIN catalog.calendar_version calendar
                ON calendar.calendar_version_id=
                   publication.calendar_version_id
              JOIN lineage.artifact calendar_artifact
                ON calendar_artifact.artifact_id=calendar.artifact_id
               AND calendar_artifact.status='published'
              JOIN lineage.artifact_dependency model_dependency
                ON model_dependency.artifact_id=publication.artifact_id
               AND model_dependency.role='reserve_model'
               AND model_dependency.ordinal=2
              JOIN experiment.reserve_return_model_version model
                ON model.artifact_id=
                   model_dependency.depends_on_artifact_id
              JOIN lineage.artifact model_artifact
                ON model_artifact.artifact_id=model.artifact_id
               AND model_artifact.status='published'
             WHERE suite.research_suite_id=:suite
               AND EXISTS (
                 SELECT 1 FROM data.reserve_return reserve
                  WHERE reserve.dataset_publication_id=
                        publication.dataset_publication_id
               )
            """
            ),
            {
                "suite": research_suite_id,
                "bundle_key": RESEARCH_DATA_BUNDLE_KEY,
                "benchmark_publication": benchmark.dataset_publication_id,
            },
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise ValueError(
            "Suite requires one unambiguous published Bundle with the exact "
            "market, Calendar, Reserve, and Reserve Model identities"
        )
    row = rows[0]
    return _EvaluationBundle(
        row["data_bundle_version_id"],
        row["bundle_artifact_id"],
        _evaluation_input(row),
        row["reserve_return_model_version_id"],
        row["reserve_return_model_artifact_id"],
    )


def _evaluation_input(row: RowMapping) -> _EvaluationInput:
    return _EvaluationInput(
        row["dataset_publication_id"],
        row["dataset_artifact_id"],
        row["dataset_fingerprint"],
        row["calendar_version_id"],
        row["calendar_artifact_id"],
        row["coverage_start"],
        row["coverage_end"],
    )


def _publish_evaluation_context(
    connection: Connection,
    *,
    artifacts: ArtifactService,
    policy_id: uuid.UUID,
    policy_artifact_id: uuid.UUID,
    context_ordinal: int,
    policy_context_fingerprint: str,
    benchmark_asset_id: uuid.UUID,
    benchmark: _EvaluationInput,
    bundle: _EvaluationBundle,
) -> _EvaluationContextPublication:
    reserve = bundle.reserve
    reserve_model_id = bundle.reserve_return_model_version_id
    reserve_model_artifact_id = bundle.reserve_return_model_artifact_id
    coverage_start = max(benchmark.coverage_start, reserve.coverage_start)
    coverage_end = min(benchmark.coverage_end, reserve.coverage_end)
    if coverage_start > coverage_end:
        raise ValueError("SPY and Reserve evaluation inputs have no common interval")
    pit_document = {
        "policy_key": "point_in_time_known_at_v1",
        **BACK_ADJUSTED_RESEARCH_SEMANTICS,
        "benchmark_dataset_publication_id": str(benchmark.dataset_publication_id),
        "data_bundle_version_id": str(bundle.data_bundle_version_id),
        "data_bundle_artifact_id": str(bundle.data_bundle_artifact_id),
        "reserve_dataset_publication_id": str(reserve.dataset_publication_id),
        "reserve_return_model_version_id": str(reserve_model_id),
    }
    common_document = {
        "policy_key": "full_common_history_spy_v1",
        **BACK_ADJUSTED_RESEARCH_SEMANTICS,
        "evaluation_matrix_policy_id": str(policy_id),
        "evaluation_context_ordinal": context_ordinal,
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
        "benchmark_calendar_version_id": str(benchmark.calendar_version_id),
        "reserve_calendar_version_id": (
            None if reserve.calendar_version_id is None else str(reserve.calendar_version_id)
        ),
    }
    semantic = {
        "contract_version": CONTRACT_VERSION,
        **BACK_ADJUSTED_RESEARCH_SEMANTICS,
        "evaluation_matrix_policy_id": str(policy_id),
        "evaluation_context_ordinal": context_ordinal,
        "evaluation_policy_context_fingerprint": policy_context_fingerprint,
        "benchmark_asset_id": str(benchmark_asset_id),
        "benchmark_dataset_publication_id": str(benchmark.dataset_publication_id),
        "data_bundle_version_id": str(bundle.data_bundle_version_id),
        "data_bundle_artifact_id": str(bundle.data_bundle_artifact_id),
        "reserve_dataset_publication_id": str(reserve.dataset_publication_id),
        "reserve_return_model_version_id": str(reserve_model_id),
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "pit_document": pit_document,
        "common_interval_document": common_document,
    }
    context_fingerprint = sha256_hexdigest(semantic)
    context_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"bird:v0.22:portfolio-evaluation-data-context:{context_fingerprint}",
    )
    dependencies = [
        DependencyInput(benchmark.dataset_artifact_id, "benchmark_dataset", 0),
        DependencyInput(cast(uuid.UUID, benchmark.calendar_artifact_id), "benchmark_calendar", 1),
        DependencyInput(reserve_model_artifact_id, "reserve_return_model", 2),
        DependencyInput(reserve.dataset_artifact_id, "reserve_dataset", 3),
        DependencyInput(policy_artifact_id, "evaluation_policy", 4),
        DependencyInput(bundle.data_bundle_artifact_id, "evaluation_data_bundle", 5),
    ]
    if reserve.calendar_artifact_id is not None:
        dependencies.append(DependencyInput(reserve.calendar_artifact_id, "reserve_calendar", 6))

    def write(conn: Connection, artifact_id: uuid.UUID, artifact_fingerprint: str) -> None:
        conn.execute(
            text(
                """
                INSERT INTO experiment.v022_portfolio_evaluation_data_context (
                  portfolio_evaluation_data_context_id,artifact_id,
                  evaluation_matrix_policy_id,evaluation_context_ordinal,
                  benchmark_asset_id,benchmark_asset_key,
                  benchmark_dataset_publication_id,benchmark_dataset_artifact_id,
                  benchmark_calendar_version_id,benchmark_calendar_artifact_id,
                  data_bundle_version_id,data_bundle_artifact_id,
                  reserve_return_model_version_id,reserve_return_model_artifact_id,
                  reserve_dataset_publication_id,reserve_dataset_artifact_id,
                  reserve_calendar_version_id,reserve_calendar_artifact_id,
                  coverage_start,coverage_end,pit_document,common_interval_document,
                  context_fingerprint,artifact_semantic_fingerprint
                ) VALUES (
                  :id,:artifact,:policy,:ordinal,:benchmark_asset,'spy',
                  :benchmark_publication,:benchmark_artifact,:benchmark_calendar,
                  :benchmark_calendar_artifact,:bundle_version,:bundle_artifact,
                  :reserve_model,:reserve_model_artifact,
                  :reserve_publication,:reserve_artifact,:reserve_calendar,
                  :reserve_calendar_artifact,:coverage_start,:coverage_end,
                  CAST(:pit AS jsonb),CAST(:common AS jsonb),:fingerprint,
                  :artifact_fingerprint
                )
                """
            ),
            {
                "id": context_id,
                "artifact": artifact_id,
                "policy": policy_id,
                "ordinal": context_ordinal,
                "benchmark_asset": benchmark_asset_id,
                "benchmark_publication": benchmark.dataset_publication_id,
                "benchmark_artifact": benchmark.dataset_artifact_id,
                "benchmark_calendar": benchmark.calendar_version_id,
                "benchmark_calendar_artifact": benchmark.calendar_artifact_id,
                "bundle_version": bundle.data_bundle_version_id,
                "bundle_artifact": bundle.data_bundle_artifact_id,
                "reserve_model": reserve_model_id,
                "reserve_model_artifact": reserve_model_artifact_id,
                "reserve_publication": reserve.dataset_publication_id,
                "reserve_artifact": reserve.dataset_artifact_id,
                "reserve_calendar": reserve.calendar_version_id,
                "reserve_calendar_artifact": reserve.calendar_artifact_id,
                "coverage_start": coverage_start,
                "coverage_end": coverage_end,
                "pit": _json(pit_document),
                "common": _json(common_document),
                "fingerprint": context_fingerprint,
                "artifact_fingerprint": artifact_fingerprint,
            },
        )
        for ordinal, (role, item) in enumerate(
            (("benchmark_daily_bar", benchmark), ("reserve_return", reserve))
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO experiment.v022_portfolio_evaluation_data_input (
                      portfolio_evaluation_data_context_id,ordinal,input_role,
                      dataset_publication_id,dataset_artifact_id,calendar_version_id,
                      calendar_artifact_id,coverage_start,coverage_end,dataset_fingerprint
                    ) VALUES (
                      :context,:ordinal,:role,:publication,:artifact,:calendar,
                      :calendar_artifact,:start,:end,:fingerprint
                    )
                    """
                ),
                {
                    "context": context_id,
                    "ordinal": ordinal,
                    "role": role,
                    "publication": item.dataset_publication_id,
                    "artifact": item.dataset_artifact_id,
                    "calendar": item.calendar_version_id,
                    "calendar_artifact": item.calendar_artifact_id,
                    "start": coverage_start,
                    "end": coverage_end,
                    "fingerprint": item.dataset_fingerprint,
                },
            )

    publication = _publish_artifact(
        connection,
        artifacts=artifacts,
        artifact_type="v022_portfolio_evaluation_data_context",
        artifact_key=f"v022_portfolio_evaluation_data_context__{context_fingerprint}",
        semantic_payload=semantic,
        content_payload=semantic,
        dependencies=tuple(dependencies),
        reason="publish v0.22 Portfolio Evaluation Data Context",
        draft_writer=write,
    )
    return _EvaluationContextPublication(context_id, publication.artifact_id)


def _existing_plan(connection: Connection, plan_fingerprint: str) -> RowMapping | None:
    return (
        connection.execute(
            text(
                """
            SELECT plan.suite_runtime_plan_id,plan.graph_run_id,plan.artifact_id,
                   run.status
              FROM experiment.v022_suite_runtime_plan plan
              JOIN lineage.artifact artifact ON artifact.artifact_id=plan.artifact_id
              JOIN workspace.v022_graph_run run ON run.graph_run_id=plan.graph_run_id
             WHERE plan.plan_fingerprint=:fingerprint
               AND artifact.status='published'
            """
            ),
            {"fingerprint": plan_fingerprint},
        )
        .mappings()
        .one_or_none()
    )


def _next_suite_run_binding_ordinal(connection: Connection, research_suite_id: uuid.UUID) -> int:
    return int(
        connection.scalar(
            text(
                """
                SELECT COALESCE(MAX(binding_ordinal), -1) + 1
                  FROM experiment.v022_research_suite_graph_run_binding
                 WHERE research_suite_id=:suite
                """
            ),
            {"suite": research_suite_id},
        )
    )


def _write_runtime_plan(
    connection: Connection,
    *,
    artifacts: ArtifactService,
    request: SuiteRuntimePlanRequest,
    facts: VerifiedSuiteRuntimeFacts,
    preflight: Any,
) -> SuiteRuntimeSubmission:
    graph_run_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO workspace.v022_graph_run (
              graph_run_id,compiled_research_graph_id,run_fingerprint,status,
              requested_by,requested_range,environment_fingerprint
            ) VALUES (
              :id,:graph,:fingerprint,'planning',:actor,CAST(:range AS jsonb),:environment
            )
            """
        ),
        {
            "id": graph_run_id,
            "graph": facts.compiled_research_graph_id,
            "fingerprint": preflight.run_fingerprint,
            "actor": request.requested_by,
            "range": _json(request.requested_range),
            "environment": request.environment_fingerprint,
        },
    )
    work_ids = _write_graph_work(connection, graph_run_id, preflight.work)
    _write_reused_node_bindings(
        connection,
        graph_run_id=graph_run_id,
        facts=facts,
        work_ids=work_ids,
    )
    _write_aggregation_runs(
        connection,
        artifacts=artifacts,
        graph_run_id=graph_run_id,
        request=request,
        facts=facts,
        work_ids=work_ids,
        work=preflight.work,
    )
    binding_ordinal = _next_suite_run_binding_ordinal(connection, facts.research_suite_id)
    binding_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        (
            "bird:v0.22:suite-run-binding:"
            f"{facts.research_suite_id}:{binding_ordinal}:{graph_run_id}"
        ),
    )
    connection.execute(
        text(
            """
            INSERT INTO experiment.v022_research_suite_graph_run_binding (
              research_suite_graph_run_binding_id,research_suite_id,
              compiled_research_graph_id,graph_run_id,binding_ordinal,
              binding_fingerprint,bound_by
            ) VALUES (:id,:suite,:graph,:run,:ordinal,:fingerprint,:actor)
            """
        ),
        {
            "id": binding_id,
            "suite": facts.research_suite_id,
            "graph": facts.compiled_research_graph_id,
            "run": graph_run_id,
            "ordinal": binding_ordinal,
            "fingerprint": sha256_hexdigest(
                {
                    "research_suite_id": facts.research_suite_id,
                    "graph_run_id": graph_run_id,
                    "binding_ordinal": binding_ordinal,
                }
            ),
            "actor": request.requested_by,
        },
    )
    plan_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"bird:v0.22:suite-runtime-plan:{preflight.plan_fingerprint}"
    )
    context_artifacts = sorted(
        {
            (cell.portfolio_evaluation_data_context_artifact_id, cell.evaluation_context_ordinal)
            for cell in facts.cells
        },
        key=lambda item: (item[1], str(item[0])),
    )
    dependencies = (
        DependencyInput(facts.research_suite_artifact_id, "research_suite", 0),
        DependencyInput(facts.execution_data_context_artifact_id, "execution_data_context", 1),
        DependencyInput(facts.catalog_release_artifact_id, "catalog_release", 2),
        *tuple(
            DependencyInput(artifact_id, "portfolio_evaluation_data_context", ordinal)
            for ordinal, (artifact_id, _context_ordinal) in enumerate(context_artifacts)
        ),
    )
    semantic = {
        "contract_version": CONTRACT_VERSION,
        **BACK_ADJUSTED_RESEARCH_SEMANTICS,
        "plan_fingerprint": preflight.plan_fingerprint,
        "run_fingerprint": preflight.run_fingerprint,
        "research_suite_id": str(facts.research_suite_id),
        "compiled_research_graph_id": str(facts.compiled_research_graph_id),
        "catalog_release_id": str(facts.catalog_release_id),
        "compiled_execution_data_context_id": str(facts.compiled_execution_data_context_id),
        "requested_range": request.requested_range,
        "effective_range": facts.effective_range,
        "evaluation_cohort_version_id": (
            str(facts.evaluation_cohort_version_id)
            if facts.evaluation_cohort_version_id is not None
            else None
        ),
        "evaluation_cohort_fingerprint": facts.evaluation_cohort_fingerprint,
        "evaluation_cohort_research_tier": facts.evaluation_cohort_research_tier,
        "work": [
            {
                "occurrence_kind": item.occurrence_kind,
                "occurrence_key": item.occurrence_key,
                "execution_fingerprint": item.execution_fingerprint,
            }
            for item in preflight.work
        ],
    }

    def write_plan(conn: Connection, artifact_id: uuid.UUID, artifact_fingerprint: str) -> None:
        conn.execute(
            text(
                """
                INSERT INTO experiment.v022_suite_runtime_plan (
                  suite_runtime_plan_id,artifact_id,
                  research_suite_graph_run_binding_id,research_suite_id,
                  compiled_research_graph_id,catalog_release_id,graph_run_id,
                  compiled_execution_data_context_id,
                  strategy_target_payload_contract_version_id,
                  defense_decision_payload_contract_version_id,
                  sleeve_merge_payload_contract_version_id,
                  portfolio_cell_payload_contract_version_id,
                  physical_encoding_version_id,contract_version,requested_range,
                  effective_range,executor_version,environment_fingerprint,
                  strategy_target_work_count,defense_decision_work_count,
                  sleeve_merge_work_count,portfolio_cell_work_count,total_work_count,
                  plan_fingerprint,artifact_semantic_fingerprint
                ) VALUES (
                  :id,:artifact,:binding,:suite,:graph,:catalog,:run,:context,
                  :strategy_contract,:defense_contract,:merge_contract,:cell_contract,
                  :encoding,:contract,CAST(:requested AS jsonb),CAST(:effective AS jsonb),
                  :executor,:environment,:strategy_count,:defense_count,:merge_count,
                  :cell_count,:total,:fingerprint,:artifact_fingerprint
                )
                """
            ),
            {
                "id": plan_id,
                "artifact": artifact_id,
                "binding": binding_id,
                "suite": facts.research_suite_id,
                "graph": facts.compiled_research_graph_id,
                "catalog": facts.catalog_release_id,
                "run": graph_run_id,
                "context": facts.compiled_execution_data_context_id,
                "strategy_contract": facts.payload_pins.strategy_target_payload_contract_version_id,
                "defense_contract": facts.payload_pins.defense_decision_payload_contract_version_id,
                "merge_contract": facts.payload_pins.sleeve_merge_payload_contract_version_id,
                "cell_contract": facts.payload_pins.portfolio_cell_payload_contract_version_id,
                "encoding": facts.payload_pins.physical_encoding_version_id,
                "contract": CONTRACT_VERSION,
                "requested": _json(request.requested_range),
                "effective": _json(facts.effective_range),
                "executor": request.executor_version,
                "environment": request.environment_fingerprint,
                "strategy_count": preflight.strategy_target_work_count,
                "defense_count": preflight.defense_decision_work_count,
                "merge_count": preflight.sleeve_merge_work_count,
                "cell_count": preflight.portfolio_cell_work_count,
                "total": preflight.typed_work_count,
                "fingerprint": preflight.plan_fingerprint,
                "artifact_fingerprint": artifact_fingerprint,
            },
        )
        _write_typed_specs(
            conn,
            plan_id=plan_id,
            plan_artifact_fingerprint=artifact_fingerprint,
            facts=facts,
            work=preflight.work,
            work_ids=work_ids,
        )

    plan_artifact = _publish_artifact(
        connection,
        artifacts=artifacts,
        artifact_type="v022_suite_runtime_plan",
        artifact_key=f"v022_suite_runtime_plan__{preflight.plan_fingerprint}",
        semantic_payload=semantic,
        content_payload=semantic,
        dependencies=dependencies,
        reason="publish v0.22 Suite Runtime Plan",
        draft_writer=write_plan,
    )
    connection.execute(
        text("SELECT workspace.v022_mark_graph_ready(:run,:count)"),
        {"run": graph_run_id, "count": preflight.graph_consumer_count},
    )
    status = cast(
        str,
        connection.scalar(
            text("SELECT status FROM workspace.v022_graph_run WHERE graph_run_id=:run"),
            {"run": graph_run_id},
        ),
    )
    return SuiteRuntimeSubmission(
        facts.research_suite_id,
        plan_id,
        graph_run_id,
        plan_artifact.artifact_id,
        status,
        False,
    )


def _write_graph_work(
    connection: Connection,
    graph_run_id: uuid.UUID,
    work: tuple[RuntimeWorkBlueprint, ...],
) -> dict[str, uuid.UUID]:
    work_ids: dict[str, uuid.UUID] = {}
    for item in work:
        row = (
            connection.execute(
                text(
                    """
                SELECT graph_work_item_id,work_kind,status
                  FROM workspace.v022_graph_work_item
                 WHERE execution_fingerprint=:fingerprint
                 FOR UPDATE
                """
                ),
                {"fingerprint": item.execution_fingerprint},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            if item.required_existing_work_item_id is not None:
                raise ValueError("Required materialized Processing Work is unavailable")
            work_id = uuid.uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_graph_work_item (
                      graph_work_item_id,execution_fingerprint,work_kind,status,priority
                    ) VALUES (:id,:fingerprint,:kind,'queued',:priority)
                    """
                ),
                {
                    "id": work_id,
                    "fingerprint": item.execution_fingerprint,
                    "kind": item.occurrence_kind,
                    "priority": item.priority,
                },
            )
            disposition = "execute"
        else:
            work_id = row["graph_work_item_id"]
            if (
                row["work_kind"] != item.occurrence_kind
                or (
                    item.required_existing_work_item_id is not None
                    and item.required_existing_work_item_id != work_id
                )
                or row["status"] not in {"queued", "running", "completed", "reused"}
            ):
                raise ValueError("Reusable Graph Work identity is not runnable")
            disposition = "reuse" if row["status"] in {"completed", "reused"} else "execute"
        work_ids[item.occurrence_key] = work_id
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_graph_work_consumer (
                  graph_run_id,graph_work_item_id,occurrence_kind,occurrence_key,
                  binding_disposition
                ) VALUES (:run,:item,:kind,:key,:disposition)
                """
            ),
            {
                "run": graph_run_id,
                "item": work_id,
                "kind": item.occurrence_kind,
                "key": item.occurrence_key,
                "disposition": disposition,
            },
        )
    for item in work:
        for upstream_key in item.required_upstream_keys:
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_graph_work_dependency (
                      upstream_work_item_id,downstream_work_item_id,dependency_kind
                    ) VALUES (:upstream,:downstream,'required')
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "upstream": work_ids[upstream_key],
                    "downstream": work_ids[item.occurrence_key],
                },
            )
    return work_ids


def _write_reused_node_bindings(
    connection: Connection,
    *,
    graph_run_id: uuid.UUID,
    facts: VerifiedSuiteRuntimeFacts,
    work_ids: Mapping[str, uuid.UUID],
) -> None:
    bindings = {
        item.compiled_graph_node_id: item
        for item in facts.source_materializations
        if item.source_kind == "node_output"
    }
    for compiled_node_id, item in sorted(bindings.items(), key=lambda pair: str(pair[0])):
        if compiled_node_id is None or item.node_run_id is None:
            raise ValueError("Reusable Processing source lacks its exact Node Run identity")
        work_key = f"node:{compiled_node_id}"
        work_id = work_ids.get(work_key)
        if work_id is None or work_id != item.graph_work_item_id:
            raise ValueError("Reusable Processing source does not match planned Graph Work")
        connection.execute(
            text(
                """
                INSERT INTO processing.graph_run_node_binding (
                  graph_run_id,compiled_graph_node_id,graph_work_item_id,
                  node_run_id,binding_disposition
                ) VALUES (:run,:compiled_node,:work,:node_run,'reused')
                """
            ),
            {
                "run": graph_run_id,
                "compiled_node": compiled_node_id,
                "work": work_id,
                "node_run": item.node_run_id,
            },
        )


def _write_aggregation_runs(
    connection: Connection,
    *,
    artifacts: ArtifactService,
    graph_run_id: uuid.UUID,
    request: SuiteRuntimePlanRequest,
    facts: VerifiedSuiteRuntimeFacts,
    work_ids: Mapping[str, uuid.UUID],
    work: tuple[RuntimeWorkBlueprint, ...],
) -> None:
    blueprints = {
        item.occurrence_key: item for item in work if item.occurrence_kind == "aggregation"
    }
    materializations = {item.terminal_occurrence_id: item for item in facts.source_materializations}
    occurrences = {item.compiled_feature_occurrence_id: item for item in facts.occurrences}
    for aggregation in facts.aggregations:
        key = f"aggregation:{aggregation.compiled_aggregation_instance_id}"
        blueprint = blueprints[key]
        existing = (
            connection.execute(
                text(
                    """
                SELECT run.aggregation_run_id,run.status,
                       run.target_version_id,run.training_preset_version_id,
                       run.ensemble_spec_id,
                       artifact.status AS artifact_status
                  FROM aggregation.aggregation_run run
                  JOIN lineage.artifact artifact ON artifact.artifact_id=run.artifact_id
                 WHERE run.execution_fingerprint=:fingerprint
                """
                ),
                {"fingerprint": blueprint.execution_fingerprint},
            )
            .mappings()
            .one_or_none()
        )
        if existing is None:
            run_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"bird:v0.22:aggregation-run:{blueprint.execution_fingerprint}",
            )
            ordered_inputs: list[tuple[str, int, Any]] = []
            for compiled_input in sorted(aggregation.inputs, key=lambda item: item.ordinal):
                occurrence = occurrences[compiled_input.compiled_feature_occurrence_id]
                while occurrence.production_kind == "layer_projection":
                    assert occurrence.source_occurrence_id is not None
                    occurrence = occurrences[occurrence.source_occurrence_id]
                materialization = materializations[occurrence.compiled_feature_occurrence_id]
                ordered_inputs.append(
                    (compiled_input.slot_key, compiled_input.ordinal, materialization)
                )
            dependency_items = [
                DependencyInput(
                    aggregation.aggregation_version_artifact_id,
                    "aggregation_version",
                    0,
                )
            ]
            dependency_items.extend(
                DependencyInput(
                    item.payload_manifest_artifact_id,
                    "aggregation_input",
                    ordinal + 1,
                )
                for _slot, ordinal, item in ordered_inputs
            )
            if aggregation.execution_mode == "supervised":
                if aggregation.ensemble_spec_id is not None:
                    if aggregation.ensemble_spec_artifact_id is None:
                        raise ValueError("Supervised Ensemble lacks its exact Spec Artifact")
                    dependency_items.append(
                        DependencyInput(
                            aggregation.ensemble_spec_artifact_id,
                            "trainable_ensemble_spec",
                            len(dependency_items),
                        )
                    )
                else:
                    if (
                        aggregation.target_version_artifact_id is None
                        or aggregation.training_preset_version_artifact_id is None
                    ):
                        raise ValueError(
                            "Supervised Aggregation lacks exact Target/Training Artifacts"
                        )
                    dependency_items.extend(
                        (
                            DependencyInput(
                                aggregation.target_version_artifact_id,
                                "target_version",
                                len(dependency_items),
                            ),
                            DependencyInput(
                                aggregation.training_preset_version_artifact_id,
                                "training_preset_version",
                                len(dependency_items) + 1,
                            ),
                        )
                    )
            dependencies = tuple(dependency_items)
            semantic = {
                "contract_version": CONTRACT_VERSION,
                "aggregation_run_id": str(run_id),
                "compiled_aggregation_instance_id": str(
                    aggregation.compiled_aggregation_instance_id
                ),
                "execution_fingerprint": blueprint.execution_fingerprint,
                "execution_mode": aggregation.execution_mode,
                "target_version_id": (
                    str(aggregation.target_version_id)
                    if aggregation.target_version_id is not None
                    else None
                ),
                "training_preset_version_id": (
                    str(aggregation.training_preset_version_id)
                    if aggregation.training_preset_version_id is not None
                    else None
                ),
                "ensemble_spec_id": (
                    str(aggregation.ensemble_spec_id)
                    if aggregation.ensemble_spec_id is not None
                    else None
                ),
                "ensemble_fingerprint": aggregation.ensemble_fingerprint,
                "resolved_parameters": aggregation.resolved_parameters,
                "inputs": [
                    {
                        "slot_key": slot,
                        "ordinal": ordinal,
                        "payload_manifest_id": str(item.payload_manifest_id),
                        "manifest_hash": item.manifest_hash,
                    }
                    for slot, ordinal, item in ordered_inputs
                ],
            }

            def write_run(
                conn: Connection,
                artifact_id: uuid.UUID,
                _artifact_fingerprint: str,
                *,
                aggregation_run_id: uuid.UUID = run_id,
                aggregation_version_id: uuid.UUID = aggregation.aggregation_version_id,
                parameter_preset_version_id: uuid.UUID | None = (
                    aggregation.parameter_preset_version_id
                ),
                target_version_id: uuid.UUID | None = aggregation.target_version_id,
                training_preset_version_id: uuid.UUID | None = (
                    aggregation.training_preset_version_id
                ),
                ensemble_spec_id: uuid.UUID | None = aggregation.ensemble_spec_id,
                execution_fingerprint: str = blueprint.execution_fingerprint,
                resolved_parameters: Mapping[str, object] = (aggregation.resolved_parameters),
                frozen_inputs: tuple[tuple[str, int, Any], ...] = tuple(ordered_inputs),
            ) -> None:
                conn.execute(
                    text(
                        """
                        INSERT INTO aggregation.aggregation_run (
                          aggregation_run_id,artifact_id,aggregation_version_id,
                          parameter_preset_version_id,target_version_id,
                          training_preset_version_id,ensemble_spec_id,
                          execution_fingerprint,
                          resolved_parameters,executor_version,environment_fingerprint,
                          status,started_at
                        ) VALUES (
                          :id,:artifact,:version,:preset,:target,:training,:ensemble,
                          :fingerprint,
                          CAST(:parameters AS jsonb),:executor,:environment,'running',now()
                        )
                        """
                    ),
                    {
                        "id": aggregation_run_id,
                        "artifact": artifact_id,
                        "version": aggregation_version_id,
                        "preset": parameter_preset_version_id,
                        "target": target_version_id,
                        "training": training_preset_version_id,
                        "ensemble": ensemble_spec_id,
                        "fingerprint": execution_fingerprint,
                        "parameters": _json(resolved_parameters),
                        "executor": request.executor_version,
                        "environment": request.environment_fingerprint,
                    },
                )
                for slot, ordinal, item in frozen_inputs:
                    conn.execute(
                        text(
                            """
                            INSERT INTO aggregation.aggregation_run_input (
                              aggregation_run_id,slot_key,payload_manifest_id,ordinal,
                              manifest_hash
                            ) VALUES (:run,:slot,:manifest,:ordinal,:hash)
                            """
                        ),
                        {
                            "run": aggregation_run_id,
                            "slot": slot,
                            "manifest": item.payload_manifest_id,
                            "ordinal": ordinal,
                            "hash": item.manifest_hash,
                        },
                    )

            _publish_artifact(
                connection,
                artifacts=artifacts,
                artifact_type="v022_aggregation_run",
                artifact_key=str(run_id),
                semantic_payload=semantic,
                content_payload=semantic,
                dependencies=dependencies,
                reason="publish v0.22 Aggregation Run",
                draft_writer=write_run,
            )
            disposition = "executed"
        else:
            if existing["artifact_status"] != "published" or existing["status"] not in {
                "running",
                "completed",
            }:
                raise ValueError("Aggregation Run is not reusable")
            if (
                existing["target_version_id"] != aggregation.target_version_id
                or existing["training_preset_version_id"] != aggregation.training_preset_version_id
                or existing["ensemble_spec_id"] != aggregation.ensemble_spec_id
            ):
                raise ValueError("Aggregation Run supervised axes are not reusable")
            run_id = existing["aggregation_run_id"]
            disposition = "reused" if existing["status"] == "completed" else "executed"
        connection.execute(
            text(
                """
                INSERT INTO aggregation.graph_run_aggregation_binding (
                  graph_run_id,compiled_aggregation_instance_id,graph_work_item_id,
                  aggregation_run_id,binding_disposition
                ) VALUES (:graph_run,:instance,:work,:aggregation_run,:disposition)
                """
            ),
            {
                "graph_run": graph_run_id,
                "instance": aggregation.compiled_aggregation_instance_id,
                "work": work_ids[key],
                "aggregation_run": run_id,
                "disposition": disposition,
            },
        )


def _write_typed_specs(
    connection: Connection,
    *,
    plan_id: uuid.UUID,
    plan_artifact_fingerprint: str,
    facts: VerifiedSuiteRuntimeFacts,
    work: tuple[RuntimeWorkBlueprint, ...],
    work_ids: Mapping[str, uuid.UUID],
) -> None:
    by_kind: dict[str, list[RuntimeWorkBlueprint]] = {}
    for item in work:
        by_kind.setdefault(item.occurrence_kind, []).append(item)
    branches = {str(item.compiled_strategy_branch_id): item for item in facts.branches}
    for kind in ("strategy_target", "defense_decision", "sleeve_merge"):
        for item in by_kind.get(kind, []):
            branch = branches[str(item.semantic_identity["compiled_strategy_branch_id"])]
            sources = [work_ids[key] for key in item.required_upstream_keys]
            document = {
                **item.semantic_identity,
                **BACK_ADJUSTED_RESEARCH_SEMANTICS,
                "work_execution_fingerprint": item.execution_fingerprint,
                "occurrence_key": item.occurrence_key,
            }
            if kind == "strategy_target":
                document["source_aggregation_work_item_id"] = str(sources[0])
                _insert_strategy_spec(
                    connection, plan_id, plan_artifact_fingerprint, branch, item, sources, document
                )
            elif kind == "defense_decision":
                document["source_strategy_work_item_id"] = str(sources[0])
                _insert_defense_spec(
                    connection, plan_id, plan_artifact_fingerprint, branch, item, sources, document
                )
            else:
                document["source_strategy_work_item_id"] = str(sources[0])
                document["source_defense_work_item_id"] = (
                    None if len(sources) == 1 else str(sources[1])
                )
                _insert_merge_spec(
                    connection, plan_id, plan_artifact_fingerprint, branch, item, sources, document
                )
    for item in by_kind.get("portfolio_cell", []):
        snapshot_id = str(item.semantic_identity["configuration_snapshot_id"])
        ordinal_value = item.semantic_identity["evaluation_context_ordinal"]
        if not isinstance(ordinal_value, int):
            raise ValueError("Portfolio Cell evaluation ordinal is invalid")
        ordinal = ordinal_value
        cell = next(
            candidate
            for candidate in facts.cells
            if str(
                next(
                    branch.configuration_snapshot_id
                    for branch in facts.branches
                    if branch.research_suite_branch_id == candidate.research_suite_branch_id
                )
            )
            == snapshot_id
            and candidate.evaluation_context_ordinal == ordinal
        )
        branch = next(
            branch
            for branch in facts.branches
            if branch.research_suite_branch_id == cell.research_suite_branch_id
        )
        source = work_ids[item.required_upstream_keys[0]]
        document = {
            **item.semantic_identity,
            **BACK_ADJUSTED_RESEARCH_SEMANTICS,
            "work_execution_fingerprint": item.execution_fingerprint,
            "occurrence_key": item.occurrence_key,
            "source_merge_work_item_id": str(source),
        }
        connection.execute(
            text(
                """
                INSERT INTO experiment.v022_portfolio_cell_work_spec (
                  portfolio_cell_work_spec_id,graph_work_item_id,suite_runtime_plan_id,
                  research_suite_branch_id,research_cell_id,compiled_strategy_branch_id,
                  configuration_snapshot_id,portfolio_evaluation_data_context_id,
                  output_payload_contract_version_id,physical_encoding_version_id,
                  source_merge_work_item_id,occurrence_key,specification_document,
                  specification_fingerprint,plan_artifact_semantic_fingerprint
                ) VALUES (
                  :id,:work,:plan,:suite_branch,:cell,:branch,:snapshot,:context,
                  :contract,:encoding,:source,:key,CAST(:document AS jsonb),
                  :fingerprint,:plan_fingerprint
                )
                """
            ),
            {
                "id": uuid.uuid4(),
                "work": work_ids[item.occurrence_key],
                "plan": plan_id,
                "suite_branch": branch.research_suite_branch_id,
                "cell": cell.research_cell_id,
                "branch": branch.compiled_strategy_branch_id,
                "snapshot": branch.configuration_snapshot_id,
                "context": cell.portfolio_evaluation_data_context_id,
                "contract": facts.payload_pins.portfolio_cell_payload_contract_version_id,
                "encoding": facts.payload_pins.physical_encoding_version_id,
                "source": source,
                "key": item.occurrence_key,
                "document": _json(document),
                "fingerprint": item.execution_fingerprint,
                "plan_fingerprint": plan_artifact_fingerprint,
            },
        )


def _insert_strategy_spec(
    connection: Connection,
    plan_id: uuid.UUID,
    plan_fingerprint: str,
    branch: Any,
    item: RuntimeWorkBlueprint,
    sources: list[uuid.UUID],
    document: Mapping[str, object],
) -> None:
    work_id = _work_id(connection, item.execution_fingerprint)
    connection.execute(
        text(
            """
            INSERT INTO strategy.v022_strategy_target_work_spec (
              strategy_target_work_spec_id,graph_work_item_id,suite_runtime_plan_id,
              research_suite_branch_id,compiled_strategy_branch_id,
              configuration_snapshot_id,compiled_execution_data_context_id,
              output_payload_contract_version_id,physical_encoding_version_id,
              source_aggregation_work_item_id,occurrence_key,specification_document,
              specification_fingerprint,plan_artifact_semantic_fingerprint
            ) VALUES (
              :id,:work,:plan,:suite_branch,:branch,:snapshot,:context,:contract,
              :encoding,:source,:key,CAST(:document AS jsonb),:fingerprint,
              :plan_fingerprint
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "work": work_id,
            "plan": plan_id,
            "suite_branch": branch.research_suite_branch_id,
            "branch": branch.compiled_strategy_branch_id,
            "snapshot": branch.configuration_snapshot_id,
            "context": uuid.UUID(str(item.semantic_identity["compiled_execution_data_context_id"])),
            "contract": uuid.UUID(
                str(item.semantic_identity["output_payload_contract_version_id"])
            ),
            "encoding": uuid.UUID(str(item.semantic_identity["physical_encoding_version_id"])),
            "source": sources[0],
            "key": item.occurrence_key,
            "document": _json(document),
            "fingerprint": item.execution_fingerprint,
            "plan_fingerprint": plan_fingerprint,
        },
    )


def _insert_defense_spec(
    connection: Connection,
    plan_id: uuid.UUID,
    plan_fingerprint: str,
    branch: Any,
    item: RuntimeWorkBlueprint,
    sources: list[uuid.UUID],
    document: Mapping[str, object],
) -> None:
    values = item.semantic_identity
    connection.execute(
        text(
            """
            INSERT INTO defense.v022_defense_decision_work_spec (
              defense_decision_work_spec_id,graph_work_item_id,suite_runtime_plan_id,
              research_suite_branch_id,compiled_strategy_branch_id,
              configuration_snapshot_id,defense_version_id,timing_policy_version_id,
              allocation_policy_version_id,compiled_defense_execution_context_id,
              output_payload_contract_version_id,physical_encoding_version_id,
              source_strategy_work_item_id,occurrence_key,specification_document,
              specification_fingerprint,plan_artifact_semantic_fingerprint
            ) VALUES (
              :id,:work,:plan,:suite_branch,:branch,:snapshot,:defense,:timing,
              :allocation,:context,:contract,:encoding,:source,:key,
              CAST(:document AS jsonb),:fingerprint,:plan_fingerprint
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "work": _work_id(connection, item.execution_fingerprint),
            "plan": plan_id,
            "suite_branch": branch.research_suite_branch_id,
            "branch": branch.compiled_strategy_branch_id,
            "snapshot": branch.configuration_snapshot_id,
            "defense": uuid.UUID(str(values["defense_version_id"])),
            "timing": uuid.UUID(str(values["timing_policy_version_id"])),
            "allocation": uuid.UUID(str(values["allocation_policy_version_id"])),
            "context": uuid.UUID(str(values["compiled_defense_execution_context_id"])),
            "contract": uuid.UUID(str(values["output_payload_contract_version_id"])),
            "encoding": uuid.UUID(str(values["physical_encoding_version_id"])),
            "source": sources[0],
            "key": item.occurrence_key,
            "document": _json(document),
            "fingerprint": item.execution_fingerprint,
            "plan_fingerprint": plan_fingerprint,
        },
    )


def _insert_merge_spec(
    connection: Connection,
    plan_id: uuid.UUID,
    plan_fingerprint: str,
    branch: Any,
    item: RuntimeWorkBlueprint,
    sources: list[uuid.UUID],
    document: Mapping[str, object],
) -> None:
    values = item.semantic_identity
    connection.execute(
        text(
            """
            INSERT INTO strategy.v022_sleeve_merge_work_spec (
              sleeve_merge_work_spec_id,graph_work_item_id,suite_runtime_plan_id,
              research_suite_branch_id,compiled_strategy_branch_id,
              configuration_snapshot_id,output_payload_contract_version_id,
              physical_encoding_version_id,source_strategy_work_item_id,
              source_defense_work_item_id,occurrence_key,specification_document,
              specification_fingerprint,plan_artifact_semantic_fingerprint
            ) VALUES (
              :id,:work,:plan,:suite_branch,:branch,:snapshot,:contract,:encoding,
              :strategy_source,:defense_source,:key,CAST(:document AS jsonb),
              :fingerprint,:plan_fingerprint
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "work": _work_id(connection, item.execution_fingerprint),
            "plan": plan_id,
            "suite_branch": branch.research_suite_branch_id,
            "branch": branch.compiled_strategy_branch_id,
            "snapshot": branch.configuration_snapshot_id,
            "contract": uuid.UUID(str(values["output_payload_contract_version_id"])),
            "encoding": uuid.UUID(str(values["physical_encoding_version_id"])),
            "strategy_source": sources[0],
            "defense_source": None if len(sources) == 1 else sources[1],
            "key": item.occurrence_key,
            "document": _json(document),
            "fingerprint": item.execution_fingerprint,
            "plan_fingerprint": plan_fingerprint,
        },
    )


def _work_id(connection: Connection, execution_fingerprint: str) -> uuid.UUID:
    return cast(
        uuid.UUID,
        connection.scalar(
            text(
                "SELECT graph_work_item_id FROM workspace.v022_graph_work_item "
                "WHERE execution_fingerprint=:fingerprint"
            ),
            {"fingerprint": execution_fingerprint},
        ),
    )


def _publish_artifact(
    connection: Connection,
    *,
    artifacts: ArtifactService,
    artifact_type: str,
    artifact_key: str,
    semantic_payload: object,
    content_payload: object,
    dependencies: tuple[DependencyInput, ...],
    reason: str,
    draft_writer: Callable[[Connection, uuid.UUID, str], None],
) -> _ArtifactPublication:
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"{artifact_type}:{artifact_key}:1"},
    )
    dependency_rows = artifacts._dependency_rows(connection, dependencies)
    semantic_fingerprint = sha256_hexdigest(
        {
            "artifact_identity": {
                "artifact_type": artifact_type,
                "artifact_key": artifact_key,
                "version_number": 1,
            },
            "semantic_payload": semantic_payload,
            "dependencies": [
                {
                    "role": item.role,
                    "ordinal": item.ordinal,
                    "semantic_fingerprint": row["semantic_fingerprint"],
                }
                for item, row in zip(dependencies, dependency_rows, strict=True)
            ],
        }
    )
    content_hash = sha256_hexdigest(
        {
            "semantic_fingerprint": semantic_fingerprint,
            "content_payload": content_payload,
            "dependencies": [
                {
                    "role": item.role,
                    "ordinal": item.ordinal,
                    "content_hash": row["content_hash"],
                }
                for item, row in zip(dependencies, dependency_rows, strict=True)
            ],
        }
    )
    existing = (
        connection.execute(
            text(
                """
            SELECT * FROM lineage.artifact
             WHERE artifact_type=:type AND artifact_key=:key AND version_number=1
             FOR UPDATE
            """
            ),
            {"type": artifact_type, "key": artifact_key},
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        if (
            existing["status"] != "published"
            or existing["semantic_fingerprint"] != semantic_fingerprint
            or existing["content_hash"] != content_hash
        ):
            raise ValueError("Artifact identity already exists with different semantics")
        return _ArtifactPublication(existing["artifact_id"], semantic_fingerprint, True)
    artifact_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO lineage.artifact (
              artifact_id,artifact_type,artifact_key,version_number,status
            ) VALUES (:id,:type,:key,1,'draft')
            """
        ),
        {"id": artifact_id, "type": artifact_type, "key": artifact_key},
    )
    artifacts._replace_draft_dependencies(connection, artifact_id, dependencies)
    draft_writer(connection, artifact_id, semantic_fingerprint)
    artifacts._set_status_context(connection, reason)
    connection.execute(
        text(
            """
            UPDATE lineage.artifact
               SET semantic_fingerprint=:semantic,content_hash=:content,
                   published_at=:published,status='published'
             WHERE artifact_id=:id
            """
        ),
        {
            "id": artifact_id,
            "semantic": semantic_fingerprint,
            "content": content_hash,
            "published": datetime.now(UTC),
        },
    )
    manifest, manifest_hash = artifacts._build_manifest(connection, artifact_id)
    connection.execute(
        text(
            """
            INSERT INTO lineage.lineage_manifest (
              lineage_manifest_id,root_artifact_id,root_content_hash,manifest_hash,
              canonical_version,manifest
            ) VALUES (:id,:artifact,:content,:hash,:version,CAST(:manifest AS jsonb))
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact": artifact_id,
            "content": content_hash,
            "hash": manifest_hash,
            "version": CANONICAL_SERIALIZATION_VERSION,
            "manifest": _json(manifest),
        },
    )
    return _ArtifactPublication(artifact_id, semantic_fingerprint, False)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
