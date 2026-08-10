from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.domain.enums import WorkFailureClass
from style_rotation.experiment.result_payload import (
    CellResultPayloadStore,
    ExternalCellPayload,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.ops.work_queue import WorkItem, WorkQueueService

ResultType = Literal["predictive", "portfolio"]


@dataclass(frozen=True, slots=True)
class CellExecutionRequest:
    work_item_id: uuid.UUID
    cell_artifact_id: uuid.UUID
    result_type: ResultType
    cell_specification: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CellExecutionOutput:
    availability_status: Literal["accepted", "capacity_rejected", "data_quality_failed"]
    quality_status: Literal["passed", "warning"]
    metrics: dict[str, Any]
    series: dict[str, Any]
    diagnostics: dict[str, Any]


class ClassifiedWorkFailure(RuntimeError):
    def __init__(
        self,
        failure_class: WorkFailureClass,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.failure_class = failure_class
        self.details = details or {}
        super().__init__(message)


class CancellationRequested(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    work_item_id: uuid.UUID
    status: Literal["idle", "completed", "failed", "cancelled", "retrying"]
    result_artifact_id: uuid.UUID | None = None


def run_persistent_worker(
    run_once: Callable[[], WorkerOutcome],
    *,
    poll_seconds: float = 1.0,
    stop_event: threading.Event | None = None,
    on_outcome: Callable[[WorkerOutcome], None] | None = None,
    on_idle_maintenance: Callable[[], None] | None = None,
    maintenance_interval_seconds: float = 60.0,
) -> int:
    """Continuously recover and consume a persistent queue until asked to stop.

    An idle queue is not a terminal condition for a service Worker.  The loop
    therefore waits without busy-spinning and checks the database again.  A
    newly started process naturally recovers queued items and expired leases
    through ``WorkQueueService.claim``.
    """

    if poll_seconds <= 0:
        raise ValueError("poll-seconds must be positive")
    if maintenance_interval_seconds <= 0:
        raise ValueError("maintenance-interval-seconds must be positive")
    stopped = stop_event or threading.Event()
    processed = 0
    last_maintenance_at: float | None = None
    while not stopped.is_set():
        outcome = run_once()
        if outcome.status == "idle":
            now = time.monotonic()
            if on_idle_maintenance is not None and (
                last_maintenance_at is None
                or now - last_maintenance_at >= maintenance_interval_seconds
            ):
                on_idle_maintenance()
                last_maintenance_at = time.monotonic()
            stopped.wait(poll_seconds)
            continue
        processed += 1
        if on_outcome is not None:
            on_outcome(outcome)
    return processed


class CellResultMaterializer:
    """Publishes the Result and completes its Work Item in one database transaction."""

    def __init__(
        self, engine: Engine, *, payload_store: CellResultPayloadStore | None = None
    ) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)
        self._payload_store = payload_store or CellResultPayloadStore()

    def complete(
        self,
        *,
        request: CellExecutionRequest,
        output: CellExecutionOutput,
        worker_id: str,
    ) -> uuid.UUID:
        lease = self._payload_store.stage_publication(
            series=output.series,
            diagnostics=output.diagnostics,
            owner_work_item_id=request.work_item_id,
        )
        try:
            return self._complete_staged(
                request=request,
                output=output,
                worker_id=worker_id,
                external=lease.payload,
            )
        finally:
            self._payload_store.finalize_publication(lease)

    def _complete_staged(
        self,
        *,
        request: CellExecutionRequest,
        output: CellExecutionOutput,
        worker_id: str,
        external: ExternalCellPayload,
    ) -> uuid.UUID:
        logical_payload = {
            "cell_artifact_id": str(request.cell_artifact_id),
            "result_type": request.result_type,
            "availability_status": output.availability_status,
            "quality_status": output.quality_status,
            "metrics": output.metrics,
            "series": output.series,
            "diagnostics": output.diagnostics,
        }
        fingerprint = sha256_hexdigest(logical_payload)
        published_payload = {
            "cell_artifact_id": str(request.cell_artifact_id),
            "result_type": request.result_type,
            "availability_status": output.availability_status,
            "quality_status": output.quality_status,
            "metrics": output.metrics,
            "series": external.series_summary,
            "diagnostics": external.diagnostics_summary,
            "external_payload": {
                "storage_uri": external.storage_uri,
                "content_hash": external.content_hash,
                "storage_format": external.storage_format,
                "schema_version": external.schema_version,
            },
        }
        dependencies = [DependencyInput(request.cell_artifact_id, "cell_specification")]
        if request.result_type == "predictive":
            dependencies.extend(
                DependencyInput(uuid.UUID(value), "signal_materialization", index)
                for index, value in enumerate(
                    output.diagnostics.get("signal_dataset_artifact_ids", [])
                )
            )
            target_id = output.diagnostics.get("forward_return_dataset_artifact_id")
            if target_id:
                dependencies.append(
                    DependencyInput(
                        uuid.UUID(str(target_id)),
                        str(
                            output.diagnostics.get(
                                "forward_return_target_role", "forward_return_target_dataset"
                            )
                        ),
                    )
                )
        else:
            for key, role in (
                ("predictive_result_artifact_id", "predictive_result"),
                ("data_bundle_artifact_id", "data_bundle"),
                ("pit_gate_artifact_id", "pit_universe_gate"),
                ("terminal_gate_artifact_id", "terminal_event_gate"),
                ("impact_gate_artifact_id", "impact_policy_gate"),
            ):
                value = output.diagnostics.get(key)
                if value:
                    dependencies.append(DependencyInput(uuid.UUID(str(value)), role))

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            self._payload_store.ensure_reference_available(external)
            item = (
                connection.execute(
                    text("SELECT * FROM ops.work_item WHERE work_item_id = :id FOR UPDATE"),
                    {"id": request.work_item_id},
                )
                .mappings()
                .one_or_none()
            )
            if item is None:
                raise LookupError(f"Work item not found: {request.work_item_id}")
            if item["status"] != "running" or item["lease_owner"] != worker_id:
                raise RuntimeError("Only the active lease owner may materialize a result")
            if item["cancel_requested_at"] is not None:
                raise CancellationRequested("Cancellation was requested before publication")
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.cell_result (
                        cell_result_id, artifact_id, cell_artifact_id, work_item_id,
                        result_type, result_fingerprint, availability_status,
                        quality_status, metrics, series, diagnostics,
                        payload_storage_uri, payload_content_hash,
                        payload_storage_format, payload_schema_version, payload_byte_size
                    ) VALUES (
                        :id, :artifact_id, :cell_artifact_id, :work_item_id,
                        :result_type, :fingerprint, :availability, :quality,
                        CAST(:metrics AS jsonb), CAST(:series AS jsonb),
                        CAST(:diagnostics AS jsonb), :payload_storage_uri,
                        :payload_content_hash, :payload_storage_format,
                        :payload_schema_version, :payload_byte_size
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "artifact_id": artifact_id,
                    "cell_artifact_id": request.cell_artifact_id,
                    "work_item_id": request.work_item_id,
                    "result_type": request.result_type,
                    "fingerprint": fingerprint,
                    "availability": output.availability_status,
                    "quality": output.quality_status,
                    "metrics": _json(output.metrics),
                    "series": _json(external.series_summary),
                    "diagnostics": _json(external.diagnostics_summary),
                    "payload_storage_uri": external.storage_uri,
                    "payload_content_hash": external.content_hash,
                    "payload_storage_format": external.storage_format,
                    "payload_schema_version": external.schema_version,
                    "payload_byte_size": external.byte_size,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE ops.work_item
                    SET status = 'completed', stage = 'completed', lease_owner = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL, updated_at = now()
                    WHERE work_item_id = :id
                    """
                ),
                {"id": request.work_item_id},
            )
            sequence = connection.execute(
                text(
                    "SELECT COALESCE(max(sequence_number), 0) + 1 "
                    "FROM ops.work_item_event WHERE work_item_id = :id"
                ),
                {"id": request.work_item_id},
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO ops.work_item_event (
                        work_item_event_id, work_item_id, sequence_number, event_type,
                        from_status, to_status, details
                    ) VALUES (:event_id, :item_id, :sequence, 'completed',
                              'running', 'completed', CAST(:details AS jsonb))
                    """
                ),
                {
                    "event_id": uuid.uuid4(),
                    "item_id": request.work_item_id,
                    "sequence": sequence,
                    "details": _json({"result_artifact_id": str(artifact_id)}),
                },
            )

        publication = self._artifacts.publish(
            artifact_type="v021_cell_result",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=published_payload,
            content_payload=published_payload,
            dependencies=tuple(dependencies),
            draft_writer=write,
        )
        return publication.artifact_id


class WorkItemWorker:
    def __init__(
        self,
        engine: Engine,
        *,
        worker_id: str,
        handlers: Mapping[ResultType, Callable[[CellExecutionRequest], CellExecutionOutput]],
    ) -> None:
        if not worker_id.strip():
            raise ValueError("Worker id is required")
        self._engine = engine
        self._worker_id = worker_id
        self._handlers = handlers
        self._queue = WorkQueueService(engine)
        self._materializer = CellResultMaterializer(engine)

    def run_once(self) -> WorkerOutcome:
        item = self._queue.claim(worker_id=self._worker_id, work_types=tuple(self._handlers))
        if item is None:
            return WorkerOutcome(uuid.UUID(int=0), "idle")
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()

        def keep_lease() -> None:
            while not stop_heartbeat.wait(30):
                try:
                    self._queue.heartbeat(item.work_item_id, worker_id=self._worker_id)
                except RuntimeError:
                    lease_lost.set()
                    return

        heartbeat = threading.Thread(
            target=keep_lease,
            name=f"work-heartbeat-{item.work_item_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            request = self._resolve_request(item)
            handler = self._handlers.get(request.result_type)
            if handler is None:
                raise ClassifiedWorkFailure(
                    WorkFailureClass.CONTRACT,
                    f"No executor registered for {request.result_type}",
                )
            output = handler(request)
            if lease_lost.is_set():
                raise ClassifiedWorkFailure(
                    WorkFailureClass.INTERRUPTED, "Work Item lease was lost during execution"
                )
            if self._queue.cancellation_requested(item.work_item_id, worker_id=self._worker_id):
                raise CancellationRequested
            output = _validate_execution_output(request, output)
            result_id = self._materializer.complete(
                request=request, output=output, worker_id=self._worker_id
            )
            return WorkerOutcome(item.work_item_id, "completed", result_id)
        except CancellationRequested:
            self._queue.finish(item.work_item_id, worker_id=self._worker_id, status="cancelled")
            return WorkerOutcome(item.work_item_id, "cancelled")
        except ClassifiedWorkFailure as error:
            failed = self._queue.finish(
                item.work_item_id,
                worker_id=self._worker_id,
                status="failed",
                failure_class=error.failure_class,
                failure_details={"message": str(error), **error.details},
            )
            if (
                error.failure_class
                in {
                    WorkFailureClass.INFRASTRUCTURE,
                    WorkFailureClass.INTERRUPTED,
                }
                and failed.attempt_count < failed.max_attempts
            ):
                self._queue.retry(item.work_item_id)
                return WorkerOutcome(item.work_item_id, "retrying")
            return WorkerOutcome(item.work_item_id, "failed")
        except Exception as error:
            failed = self._queue.finish(
                item.work_item_id,
                worker_id=self._worker_id,
                status="failed",
                failure_class=WorkFailureClass.INFRASTRUCTURE,
                failure_details={"type": type(error).__name__, "message": str(error)},
            )
            if failed.attempt_count < failed.max_attempts:
                self._queue.retry(item.work_item_id)
                return WorkerOutcome(item.work_item_id, "retrying")
            return WorkerOutcome(item.work_item_id, "failed")
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2)

    def heartbeat(self, work_item_id: uuid.UUID) -> WorkItem:
        return self._queue.heartbeat(work_item_id, worker_id=self._worker_id)

    def _resolve_request(self, item: WorkItem) -> CellExecutionRequest:
        with self._engine.connect() as connection:
            link = (
                connection.execute(
                    text(
                        """
                    SELECT link.cell_artifact_id, link.cell_type
                    FROM experiment.research_suite_work_item link
                    WHERE link.work_item_id = :work_item_id
                    """
                    ),
                    {"work_item_id": item.work_item_id},
                )
                .mappings()
                .one_or_none()
            )
            if link is None:
                raise ClassifiedWorkFailure(
                    WorkFailureClass.CONTRACT, "Work Item has no Suite Cell binding"
                )
            table = (
                "experiment.predictive_cell_specification"
                if link["cell_type"] == "predictive"
                else "experiment.portfolio_cell_specification"
            )
            spec = (
                connection.execute(
                    text(f"SELECT * FROM {table} WHERE artifact_id = :artifact_id"),
                    {"artifact_id": link["cell_artifact_id"]},
                )
                .mappings()
                .one()
            )
        return CellExecutionRequest(
            work_item_id=item.work_item_id,
            cell_artifact_id=link["cell_artifact_id"],
            result_type=cast(ResultType, link["cell_type"]),
            cell_specification={key: _plain(value) for key, value in spec.items()},
        )


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _validate_execution_output(
    request: CellExecutionRequest, output: CellExecutionOutput
) -> CellExecutionOutput:
    """System-owned Quality Gate; executors cannot self-certify arbitrary payloads."""
    checks = output.diagnostics.get("quality_checks")
    if not isinstance(checks, list) or not checks:
        raise ClassifiedWorkFailure(
            WorkFailureClass.DATA_QUALITY, "Cell output has no system-readable Quality checks"
        )
    failed = [
        item
        for item in checks
        if not isinstance(item, dict) or item.get("status") not in {"passed", "accepted", "warning"}
    ]
    if failed:
        raise ClassifiedWorkFailure(
            WorkFailureClass.DATA_QUALITY,
            "Cell output failed the system Quality Gate",
            details={"failed_checks": failed},
        )
    if output.availability_status == "accepted":
        if request.result_type == "predictive":
            scores = output.series.get("model_scores")
            audit = output.series.get("model_input_audit")
            if not isinstance(scores, list) or not scores or not isinstance(audit, list):
                raise ClassifiedWorkFailure(
                    WorkFailureClass.DATA_QUALITY,
                    "Accepted Predictive Result requires scores and input audit",
                )
            _validate_predictive_evidence(scores, audit)
            coverage = output.metrics.get("target_period_coverage")
            nondegenerate = output.metrics.get("nondegenerate_target_ratio")
            rank_ic = output.metrics.get("mean_rank_ic")
            if (
                not isinstance(coverage, (int, float))
                or coverage < 0.9
                or not isinstance(nondegenerate, (int, float))
                or nondegenerate < 0.8
                or not isinstance(rank_ic, (int, float))
                or not -1 <= rank_ic <= 1
            ):
                raise ClassifiedWorkFailure(
                    WorkFailureClass.DATA_QUALITY,
                    "Accepted Predictive Result lacks valid target/Rank IC evidence",
                )
        else:
            nav = output.series.get("nav_series")
            strategy = output.metrics.get("strategy")
            required = {"cagr", "sharpe_ratio", "maximum_drawdown"}
            if (
                not isinstance(nav, list)
                or len(nav) < 2
                or not isinstance(strategy, dict)
                or not required.issubset(strategy)
            ):
                raise ClassifiedWorkFailure(
                    WorkFailureClass.DATA_QUALITY,
                    "Accepted Portfolio Result lacks NAV or required performance metrics",
                )
            _validate_portfolio_evidence(
                output,
                allow_capacity_warning=output.diagnostics.get("suite_mode") == "exploratory",
            )
    elif output.availability_status == "capacity_rejected" and (
        not output.series.get("gross_nav_series") or not output.series.get("trade_capacity")
    ):
        raise ClassifiedWorkFailure(
            WorkFailureClass.DATA_QUALITY,
            "Capacity Rejected Result must retain Gross NAV and capacity evidence",
        )
    derived_quality: Literal["passed", "warning"] = (
        "warning" if any(item.get("status") == "warning" for item in checks) else "passed"
    )
    return replace(output, quality_status=derived_quality)


def _validate_predictive_evidence(scores: list[Any], audit: list[Any]) -> None:
    if len(scores) != len(audit):
        raise ClassifiedWorkFailure(
            WorkFailureClass.DATA_QUALITY, "Predictive input audit is not point-complete"
        )
    by_date: dict[str, list[float]] = {}
    for score, evidence in zip(scores, audit, strict=True):
        if not isinstance(score, dict) or not isinstance(evidence, dict):
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY, "Predictive points must be structured records"
            )
        common = evidence.get("common_asset_ids")
        common_count = evidence.get("common_asset_count")
        inputs = evidence.get("inputs")
        if (
            not (
                (isinstance(common, list) and len(common) >= 2)
                or (isinstance(common_count, int) and common_count >= 2)
            )
            or not isinstance(inputs, list)
            or not inputs
        ):
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY,
                "Predictive point lacks a valid common cross-section",
            )
        contribution = sum(float(item["contribution"]) for item in inputs)
        if abs(contribution - float(score["score"])) > 1e-10:
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY, "Model score does not reconcile to contributions"
            )
        if any(abs(float(item["normalized_input_value"])) > 1.0000000001 for item in inputs):
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY, "Normalized Model input is outside [-1,1]"
            )
        by_date.setdefault(str(score["observation_date"]), []).append(float(score["score"]))
    if any(len(values) < 2 or max(values) == min(values) for values in by_date.values()):
        raise ClassifiedWorkFailure(
            WorkFailureClass.DATA_QUALITY,
            "Predictive cross-section is missing or degenerate on at least one date",
        )


def _validate_portfolio_evidence(
    output: CellExecutionOutput, *, allow_capacity_warning: bool = False
) -> None:
    nav = output.series["nav_series"]
    decisions = output.series.get("decisions")
    trades = output.series.get("trade_capacity")
    if not isinstance(decisions, list) or not decisions or not isinstance(trades, list):
        raise ClassifiedWorkFailure(
            WorkFailureClass.DATA_QUALITY, "Portfolio Result lacks Decision/capacity evidence"
        )
    if any(float(item.get("coverage_ratio", 0)) < 0.9 for item in decisions):
        raise ClassifiedWorkFailure(
            WorkFailureClass.DATA_QUALITY, "Portfolio Decision violates 90% rankable coverage"
        )
    for item in nav:
        wealth = float(item["strategy_wealth"])
        benchmark = float(item["benchmark_wealth"])
        if abs(float(item["strategy_currency_nav"]) - wealth * 100_000_000) > 0.01:
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY, "Currency NAV does not reconcile to Wealth"
            )
        if abs(float(item["excess_wealth"]) - wealth / benchmark) > 1e-10:
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY, "Excess Wealth does not reconcile"
            )
    for item in trades:
        advisory = allow_capacity_warning and item.get("advisory_only") is True
        if item.get("status") != "accepted" and not advisory:
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY, "Accepted Result contains rejected capacity"
            )
        if item["decision_date"] >= item["execution_date"] or float(item["raw_open"]) <= 0:
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY, "Execution violates next-session raw-open policy"
            )
        expected = float(item["pretrade_currency_nav"]) * float(item["absolute_weight_change"])
        if abs(float(item["order_notional"]) - expected) > max(0.01, abs(expected) * 1e-12):
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY, "Order Notional does not use pre-trade Currency NAV"
            )
        participation = item.get("participation_rate")
        if not advisory and participation is not None and float(participation) > 0.05:
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY, "Accepted Result exceeds the 5% ADV hard limit"
            )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
