# ruff: noqa: E501
from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.experiment.benchmark_publication import BenchmarkTargetPublicationService
from style_rotation.experiment.cost_publication import NetCostPathPublicationService
from style_rotation.experiment.performance_publication import IntervalPerformancePublicationService
from style_rotation.experiment.publication import GrossPathPublicationService
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult


@dataclass(frozen=True, slots=True)
class ExperimentExecutionPublication:
    result_artifact_id: uuid.UUID
    interval_result_artifact_id: uuid.UUID
    run_attempt_id: uuid.UUID
    attempt_number: int
    availability_status: str
    quality_status: str
    reused: bool


@dataclass(frozen=True, slots=True)
class _ExecutionContext:
    specification: RowMapping
    orchestration_engine: RowMapping
    specification_artifact_id: uuid.UUID
    strategy_target_artifact_id: uuid.UUID
    benchmark_artifact_id: uuid.UUID
    cost_artifact_id: uuid.UUID
    metric_artifact_id: uuid.UUID
    accounting_engine_artifact_id: uuid.UUID
    benchmark_engine_artifact_id: uuid.UUID
    performance_engine_artifact_id: uuid.UUID


class ExperimentExecutionService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def execute(
        self, specification_artifact_id: uuid.UUID, orchestration_engine_artifact_id: uuid.UUID
    ) -> ExperimentExecutionPublication:
        existing = self._existing_result(specification_artifact_id)
        if existing is not None:
            return _publication_from_row(existing, reused=True)
        context = self._load_context(specification_artifact_id, orchestration_engine_artifact_id)
        run_attempt_id, attempt_number = self._start_attempt(context)
        try:
            benchmark_target = BenchmarkTargetPublicationService(self._engine).publish(
                context.strategy_target_artifact_id,
                context.benchmark_artifact_id,
                context.benchmark_engine_artifact_id,
            )
            self._record_output(run_attempt_id, benchmark_target.artifact_id, "benchmark_target")
            gross_service = GrossPathPublicationService(self._engine)
            strategy_gross = gross_service.publish(
                context.strategy_target_artifact_id, context.accounting_engine_artifact_id
            )
            benchmark_gross = gross_service.publish(
                benchmark_target.artifact_id, context.accounting_engine_artifact_id
            )
            self._record_output(run_attempt_id, strategy_gross.artifact_id, "strategy_gross")
            self._record_output(run_attempt_id, benchmark_gross.artifact_id, "benchmark_gross")
            net_service = NetCostPathPublicationService(self._engine)
            strategy_net = net_service.publish(strategy_gross.artifact_id, context.cost_artifact_id)
            benchmark_net = net_service.publish(
                benchmark_gross.artifact_id, context.cost_artifact_id
            )
            self._record_output(run_attempt_id, strategy_net.artifact_id, "strategy_net")
            self._record_output(run_attempt_id, benchmark_net.artifact_id, "benchmark_net")
            performance = IntervalPerformancePublicationService(self._engine).publish(
                strategy_net.artifact_id,
                benchmark_net.artifact_id,
                context.metric_artifact_id,
                context.performance_engine_artifact_id,
                template_key=context.specification["template_key"],
                as_of_date=context.specification["as_of_date"],
                custom_start=context.specification["custom_start"],
                custom_end=context.specification["custom_end"],
            )
            self._record_output(run_attempt_id, performance.artifact_id, "interval_performance")
            self._run_quality_checks(
                run_attempt_id,
                performance.artifact_id,
                performance.availability_status,
                performance.quality_status,
            )
            self._complete_attempt(run_attempt_id)
        except Exception as error:
            self._fail_attempt(run_attempt_id, error)
            raise
        result = self._accept_result(
            context,
            run_attempt_id,
            performance.artifact_id,
            performance.availability_status,
            performance.quality_status,
        )
        self._attach_artifact(run_attempt_id, result.artifact_id, "output")
        return ExperimentExecutionPublication(
            result.artifact_id,
            performance.artifact_id,
            run_attempt_id,
            attempt_number,
            performance.availability_status,
            performance.quality_status,
            result.reused,
        )

    def _existing_result(self, specification_artifact_id: uuid.UUID) -> RowMapping | None:
        with self._engine.connect() as connection:
            return (
                connection.execute(
                    text(
                        "SELECT publication.artifact_id AS result_artifact_id, interval.artifact_id AS interval_result_artifact_id, publication.accepted_run_attempt_id AS run_attempt_id, attempt.attempt_number, publication.availability_status, publication.quality_status FROM experiment.result_publication publication JOIN experiment.experiment_specification specification ON specification.experiment_specification_id = publication.experiment_specification_id JOIN lineage.artifact result_artifact ON result_artifact.artifact_id = publication.artifact_id AND result_artifact.status = 'published' JOIN experiment.interval_performance_result interval ON interval.interval_performance_result_id = publication.interval_performance_result_id JOIN ops.run_attempt attempt ON attempt.run_attempt_id = publication.accepted_run_attempt_id WHERE specification.artifact_id = :artifact"
                    ),
                    {"artifact": specification_artifact_id},
                )
                .mappings()
                .one_or_none()
            )

    def _load_context(
        self, specification_artifact_id: uuid.UUID, orchestration_engine_artifact_id: uuid.UUID
    ) -> _ExecutionContext:
        with self._engine.connect() as connection:
            specification = (
                connection.execute(
                    text(
                        "SELECT specification.* FROM experiment.experiment_specification specification JOIN lineage.artifact artifact ON artifact.artifact_id = specification.artifact_id AND artifact.status = 'published' WHERE specification.artifact_id = :artifact"
                    ),
                    {"artifact": specification_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if specification is None:
                raise ValueError("Published Experiment Specification not found")
            orchestration = (
                connection.execute(
                    text(
                        "SELECT version.*, artifact.semantic_fingerprint, artifact.content_hash FROM ops.engine_version version JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id AND artifact.status = 'published' WHERE version.artifact_id = :artifact AND definition.engine_key = 'experiment_orchestration_engine'"
                    ),
                    {"artifact": orchestration_engine_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if orchestration is None:
                raise ValueError("Published Experiment Orchestration engine not found")
            return _ExecutionContext(
                specification,
                orchestration,
                specification_artifact_id,
                _artifact(
                    connection,
                    "strategy.portfolio_target_path",
                    "portfolio_target_path_id",
                    specification["strategy_target_path_id"],
                ),
                _artifact(
                    connection,
                    "experiment.benchmark_version",
                    "benchmark_version_id",
                    specification["benchmark_version_id"],
                ),
                _artifact(
                    connection,
                    "experiment.cost_scenario",
                    "cost_scenario_id",
                    specification["cost_scenario_id"],
                ),
                _artifact(
                    connection,
                    "experiment.performance_metric_catalog",
                    "performance_metric_catalog_id",
                    specification["performance_metric_catalog_id"],
                ),
                _artifact(
                    connection,
                    "ops.engine_version",
                    "engine_version_id",
                    specification["accounting_engine_version_id"],
                ),
                _artifact(
                    connection,
                    "ops.engine_version",
                    "engine_version_id",
                    specification["benchmark_engine_version_id"],
                ),
                _artifact(
                    connection,
                    "ops.engine_version",
                    "engine_version_id",
                    specification["performance_engine_version_id"],
                ),
            )

    def _start_attempt(self, context: _ExecutionContext) -> tuple[uuid.UUID, int]:
        request_fingerprint = sha256_hexdigest(
            {
                "specification_artifact_id": context.specification_artifact_id,
                "orchestration_engine_artifact_id": context.orchestration_engine["artifact_id"],
                "orchestration_engine_fingerprint": context.orchestration_engine[
                    "semantic_fingerprint"
                ],
            }
        )
        run_id = uuid.uuid4()
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": request_fingerprint},
            )
            attempt_number = connection.execute(
                text(
                    "SELECT COALESCE(max(attempt_number), 0) + 1 FROM ops.run_attempt WHERE request_fingerprint = :fingerprint"
                ),
                {"fingerprint": request_fingerprint},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO ops.run_attempt (run_attempt_id, engine_version_id, root_artifact_id, run_type, request_fingerprint, attempt_number, status, started_at) VALUES (:id, :engine, :root, 'experiment_specification', :fingerprint, :attempt, 'running', :started)"
                ),
                {
                    "id": run_id,
                    "engine": context.orchestration_engine["engine_version_id"],
                    "root": context.specification_artifact_id,
                    "fingerprint": request_fingerprint,
                    "attempt": attempt_number,
                    "started": now,
                },
            )
            input_ids = (context.specification_artifact_id,) + tuple(
                connection.execute(
                    text(
                        "SELECT depends_on_artifact_id FROM lineage.artifact_dependency WHERE artifact_id = :artifact ORDER BY ordinal"
                    ),
                    {"artifact": context.specification_artifact_id},
                ).scalars()
            )
            connection.execute(
                text(
                    "INSERT INTO ops.run_artifact (run_artifact_id, run_attempt_id, artifact_id, role) VALUES (:id, :run, :artifact, 'input')"
                ),
                [
                    {"id": uuid.uuid4(), "run": run_id, "artifact": artifact_id}
                    for artifact_id in input_ids
                ],
            )
            _insert_event(
                connection,
                run_id,
                "run_started",
                "info",
                "Experiment specification execution started",
                {"attempt_number": attempt_number},
            )
        return run_id, int(attempt_number)

    def _record_output(self, run_id: uuid.UUID, artifact_id: uuid.UUID, step: str) -> None:
        with self._engine.begin() as connection:
            _insert_artifact(connection, run_id, artifact_id, "output")
            _insert_event(
                connection,
                run_id,
                "artifact_published",
                "info",
                f"Completed {step}",
                {"artifact_id": str(artifact_id), "step": step},
            )

    def _run_quality_checks(
        self, run_id: uuid.UUID, interval_artifact_id: uuid.UUID, availability: str, quality: str
    ) -> None:
        with self._engine.begin() as connection:
            statuses = tuple(
                connection.execute(
                    text(
                        "SELECT artifact.status FROM ops.run_artifact link JOIN lineage.artifact artifact ON artifact.artifact_id = link.artifact_id WHERE link.run_attempt_id = :run AND link.role = 'output' ORDER BY link.created_at"
                    ),
                    {"run": run_id},
                ).scalars()
            )
            all_published = bool(statuses) and all(status == "published" for status in statuses)
            checks = [
                (
                    "all_outputs_published",
                    "passed" if all_published else "failed",
                    "error",
                    "All output artifacts are published"
                    if all_published
                    else "At least one output artifact is not published",
                    {"output_count": len(statuses)},
                ),
                (
                    "interval_availability",
                    "passed" if availability == "eligible" else "warning",
                    "info" if availability == "eligible" else "warning",
                    "Interval is eligible"
                    if availability == "eligible"
                    else "Interval is excluded without shortening",
                    {
                        "availability_status": availability,
                        "quality_status": quality,
                        "interval_artifact_id": str(interval_artifact_id),
                    },
                ),
                (
                    "accepted_result_inputs",
                    "passed",
                    "info",
                    "Result inputs are fixed by the atomic specification",
                    {},
                ),
            ]
            connection.execute(
                text(
                    "INSERT INTO ops.quality_check_result (quality_check_result_id, run_attempt_id, check_key, scope_key, status, severity, message, details) VALUES (:id, :run, :key, 'global', :status, :severity, :message, CAST(:details AS jsonb))"
                ),
                [
                    {
                        "id": uuid.uuid4(),
                        "run": run_id,
                        "key": key,
                        "status": status,
                        "severity": severity,
                        "message": message,
                        "details": json.dumps(details, sort_keys=True),
                    }
                    for key, status, severity, message, details in checks
                ],
            )
            _insert_event(
                connection,
                run_id,
                "quality_checks_completed",
                "info" if all_published else "error",
                "Experiment quality checks completed",
                {"failed_count": 0 if all_published else 1},
            )
        if not all_published:
            raise RuntimeError("Experiment output quality checks failed")

    def _complete_attempt(self, run_id: uuid.UUID) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ops.run_attempt SET status = 'completed', completed_at = :completed WHERE run_attempt_id = :run AND status = 'running'"
                ),
                {"completed": datetime.now(UTC), "run": run_id},
            )
            _insert_event(
                connection,
                run_id,
                "run_completed",
                "info",
                "Experiment specification execution completed",
                {},
            )

    def _fail_attempt(self, run_id: uuid.UUID, error: Exception) -> None:
        with self._engine.begin() as connection:
            status = connection.execute(
                text("SELECT status FROM ops.run_attempt WHERE run_attempt_id = :run FOR UPDATE"),
                {"run": run_id},
            ).scalar_one_or_none()
            if status != "running":
                return
            message = str(error)[:2000] or type(error).__name__
            connection.execute(
                text(
                    "UPDATE ops.run_attempt SET status = 'failed', completed_at = :completed, error_summary = :message WHERE run_attempt_id = :run"
                ),
                {"completed": datetime.now(UTC), "message": message, "run": run_id},
            )
            connection.execute(
                text(
                    "INSERT INTO ops.run_error (run_error_id, run_attempt_id, error_code, error_type, message, details) VALUES (:id, :run, 'experiment_execution_failed', :type, :message, CAST(:details AS jsonb))"
                ),
                {
                    "id": uuid.uuid4(),
                    "run": run_id,
                    "type": type(error).__name__,
                    "message": message,
                    "details": json.dumps({}),
                },
            )
            _insert_event(
                connection,
                run_id,
                "run_failed",
                "error",
                message,
                {"error_type": type(error).__name__},
            )

    def _accept_result(
        self,
        context: _ExecutionContext,
        run_id: uuid.UUID,
        interval_artifact_id: uuid.UUID,
        availability: str,
        quality: str,
    ) -> PublicationResult:
        semantic = {
            "specification_artifact_id": str(context.specification_artifact_id),
            "interval_result_artifact_id": str(interval_artifact_id),
        }
        key = sha256_hexdigest(semantic)
        with self._engine.begin() as connection:
            return ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
                artifact_type="experiment_result",
                artifact_key=f"accepted:{key}",
                version_number=1,
                semantic_payload=semantic,
                content_payload={
                    **semantic,
                    "availability_status": availability,
                    "quality_status": quality,
                },
                dependencies=(
                    DependencyInput(
                        context.specification_artifact_id, "experiment_specification", 0
                    ),
                    DependencyInput(interval_artifact_id, "interval_performance_result", 1),
                ),
                reason=f"accept experiment result {key[:12]}",
                draft_writer=partial(
                    _write_result,
                    specification_id=context.specification["experiment_specification_id"],
                    run_id=run_id,
                    interval_artifact_id=interval_artifact_id,
                    availability=availability,
                    quality=quality,
                ),
            )

    def _attach_artifact(self, run_id: uuid.UUID, artifact_id: uuid.UUID, role: str) -> None:
        with self._engine.begin() as connection:
            _insert_artifact(connection, run_id, artifact_id, role)


def _publication_from_row(row: RowMapping, *, reused: bool) -> ExperimentExecutionPublication:
    return ExperimentExecutionPublication(
        row["result_artifact_id"],
        row["interval_result_artifact_id"],
        row["run_attempt_id"],
        int(row["attempt_number"]),
        str(row["availability_status"]),
        str(row["quality_status"]),
        reused,
    )


def _artifact(
    connection: Connection, table: str, id_column: str, business_id: uuid.UUID
) -> uuid.UUID:
    result = connection.execute(
        text(f"SELECT artifact_id FROM {table} WHERE {id_column} = :id"), {"id": business_id}
    ).scalar_one()
    return cast(uuid.UUID, result)


def _insert_artifact(
    connection: Connection, run_id: uuid.UUID, artifact_id: uuid.UUID, role: str
) -> None:
    connection.execute(
        text(
            "INSERT INTO ops.run_artifact (run_artifact_id, run_attempt_id, artifact_id, role) VALUES (:id, :run, :artifact, :role) ON CONFLICT (run_attempt_id, artifact_id, role) DO NOTHING"
        ),
        {"id": uuid.uuid4(), "run": run_id, "artifact": artifact_id, "role": role},
    )


def _insert_event(
    connection: Connection,
    run_id: uuid.UUID,
    event_type: str,
    severity: str,
    message: str,
    details: dict[str, Any],
) -> None:
    sequence = connection.execute(
        text(
            "SELECT COALESCE(max(sequence_number), 0) + 1 FROM ops.run_event WHERE run_attempt_id = :run"
        ),
        {"run": run_id},
    ).scalar_one()
    connection.execute(
        text(
            "INSERT INTO ops.run_event (run_event_id, run_attempt_id, sequence_number, event_type, severity, message, details) VALUES (:id, :run, :sequence, :type, :severity, :message, CAST(:details AS jsonb))"
        ),
        {
            "id": uuid.uuid4(),
            "run": run_id,
            "sequence": sequence,
            "type": event_type,
            "severity": severity,
            "message": message,
            "details": json.dumps(details, sort_keys=True),
        },
    )


def _write_result(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    specification_id: uuid.UUID,
    run_id: uuid.UUID,
    interval_artifact_id: uuid.UUID,
    availability: str,
    quality: str,
) -> None:
    interval_id = connection.execute(
        text(
            "SELECT interval_performance_result_id FROM experiment.interval_performance_result WHERE artifact_id = :artifact"
        ),
        {"artifact": interval_artifact_id},
    ).scalar_one()
    connection.execute(
        text(
            "INSERT INTO experiment.result_publication (result_publication_id, artifact_id, experiment_specification_id, accepted_run_attempt_id, interval_performance_result_id, availability_status, quality_status) VALUES (:id, :artifact, :specification, :run, :interval, :availability, :quality)"
        ),
        {
            "id": uuid.uuid4(),
            "artifact": artifact_id,
            "specification": specification_id,
            "run": run_id,
            "interval": interval_id,
            "availability": availability,
            "quality": quality,
        },
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
