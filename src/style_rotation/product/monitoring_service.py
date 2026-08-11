from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.domain.enums import WorkFailureClass
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.ops.work_queue import WorkQueueService
from style_rotation.ops.worker import CancellationRequested, ClassifiedWorkFailure, WorkerOutcome
from style_rotation.product.lifecycle_service import ProductLifecycleService
from style_rotation.product.monitoring import MonitoringEvidence, evaluate_monitoring_health


@dataclass(frozen=True, slots=True)
class MonitoringRequest:
    work_item_id: uuid.UUID
    product_enrollment_id: uuid.UUID
    data_bundle_artifact_id: uuid.UUID
    as_of_session: date
    known_at: datetime
    held_during_suspension: bool = False
    rebalance_due: bool = False


@dataclass(frozen=True, slots=True)
class MonitoringOutput:
    evidence: MonitoringEvidence
    primary_nav: Decimal
    stress_nav: Decimal
    metrics: dict[str, Any]
    health_components: dict[str, Any]


class MonitoringScheduler:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._queue = WorkQueueService(engine)
        self._lifecycle = ProductLifecycleService(engine)

    def enqueue_for_data_bundle(
        self,
        *,
        data_bundle_artifact_id: uuid.UUID,
        as_of_session: date,
        known_at: datetime,
    ) -> list[uuid.UUID]:
        if known_at.tzinfo is None or known_at.utcoffset() is None:
            raise ValueError("Monitoring known_at must be timezone-aware")
        self._lifecycle.apply_due(as_of=known_at)
        with self._engine.connect() as connection:
            bundle = (
                connection.execute(
                    text("""
                SELECT artifact.status, artifact.artifact_type, artifact.created_at,
                       version.coverage_end
                FROM lineage.artifact artifact
                LEFT JOIN data.data_bundle_version version
                  ON version.artifact_id = artifact.artifact_id
                WHERE artifact.artifact_id = :artifact_id
            """),
                    {"artifact_id": data_bundle_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if (
                bundle is None
                or bundle["status"] != "published"
                or bundle["artifact_type"] != "data_bundle_version"
                or bundle["coverage_end"] != as_of_session
                or known_at < bundle["created_at"]
            ):
                raise ValueError("Monitoring requires a published Data Bundle Artifact")
            enrollments = (
                connection.execute(
                    text("""
                    SELECT enrollment.product_enrollment_id, enrollment.lifecycle,
                           enrollment.monitoring_start_at, spec.frequency,
                           max(snapshot.as_of_session) AS latest_as_of_session
                    FROM product.product_enrollment enrollment
                    LEFT JOIN product.monitoring_snapshot snapshot
                      ON snapshot.product_enrollment_id = enrollment.product_enrollment_id
                    JOIN product.product_version version
                      ON version.product_version_id = enrollment.product_version_id
                    JOIN strategy.compiled_strategy_version strategy
                      ON strategy.compiled_strategy_version_id =
                         version.compiled_strategy_version_id
                    JOIN workspace.compiled_research_spec spec
                      ON spec.compiled_research_spec_id = strategy.compiled_research_spec_id
                    WHERE enrollment.lifecycle IN ('active','suspended')
                    GROUP BY enrollment.product_enrollment_id, spec.frequency
                    HAVING :as_of_session > enrollment.activated_at::date
                       AND (max(snapshot.as_of_session) IS NULL
                            OR :as_of_session > max(snapshot.as_of_session))
                    ORDER BY enrollment.product_enrollment_id
                """),
                    {"as_of_session": as_of_session},
                )
                .mappings()
                .all()
            )
        work_ids: list[uuid.UUID] = []
        for enrollment in enrollments:
            enrollment_id = enrollment["product_enrollment_id"]
            held = enrollment["lifecycle"] == "suspended"
            if held and enrollment["monitoring_start_at"] is None:
                # A candidate suspended before its first legal Decision has no OOS
                # holdings or NAV to continue; observation begins only after resume.
                continue
            legal_decision = self._is_legal_decision_session(
                data_bundle_artifact_id, as_of_session, enrollment["frequency"]
            )
            if not held and enrollment["monitoring_start_at"] is None and not legal_decision:
                continue
            rebalance_due = not held and legal_decision
            fingerprint = sha256_hexdigest(
                {
                    "product_enrollment_id": str(enrollment_id),
                    "data_bundle_artifact_id": str(data_bundle_artifact_id),
                    "as_of_session": as_of_session,
                    "known_at": known_at,
                    "rebalance_due": rebalance_due,
                }
            )
            item = self._queue.enqueue(
                specification_fingerprint=fingerprint, work_type="monitoring"
            ).item
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO product.monitoring_work_item (
                            work_item_id, product_enrollment_id,
                            data_bundle_artifact_id, as_of_session, known_at,
                            held_during_suspension, rebalance_due
                        ) VALUES (:work_item_id, :enrollment_id, :data_bundle,
                                  :as_of_session, :known_at, :held, :rebalance_due)
                        ON CONFLICT (work_item_id) DO NOTHING
                        """
                    ),
                    {
                        "work_item_id": item.work_item_id,
                        "enrollment_id": enrollment_id,
                        "data_bundle": data_bundle_artifact_id,
                        "as_of_session": as_of_session,
                        "known_at": known_at,
                        "held": held,
                        "rebalance_due": rebalance_due,
                    },
                )
            work_ids.append(item.work_item_id)
        return work_ids

    def _is_legal_decision_session(
        self, bundle_artifact_id: uuid.UUID, session: date, frequency: str
    ) -> bool:
        with self._engine.connect() as connection:
            next_session = connection.execute(
                text("""
                SELECT min(calendar.session_date)
                FROM data.data_bundle_version bundle
                JOIN data.data_bundle_member member
                  ON member.data_bundle_version_id = bundle.data_bundle_version_id
                JOIN catalog.calendar_session calendar
                  ON calendar.calendar_version_id = member.calendar_version_id
                WHERE bundle.artifact_id = :bundle_artifact_id
                  AND calendar.session_date > :session
            """),
                {"bundle_artifact_id": bundle_artifact_id, "session": session},
            ).scalar_one_or_none()
        if next_session is None:
            raise ValueError("Data Bundle Calendar cannot resolve the next legal Decision")
        if frequency == "weekly":
            return bool(next_session.isocalendar()[:2] != session.isocalendar()[:2])
        return bool((next_session.year, next_session.month) != (session.year, session.month))


class MonitoringSnapshotMaterializer:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def complete(
        self, *, request: MonitoringRequest, output: MonitoringOutput, worker_id: str
    ) -> uuid.UUID:
        if output.primary_nav <= 0 or output.stress_nav <= 0:
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY, "Monitoring NAV must remain positive"
            )
        health = evaluate_monitoring_health(output.evidence)
        payload = {
            "request": asdict(request),
            "health": asdict(health),
            "primary_nav": output.primary_nav,
            "stress_nav": output.stress_nav,
            "metrics": output.metrics,
            "health_components": output.health_components,
        }
        fingerprint = sha256_hexdigest(payload)
        with self._engine.connect() as connection:
            version_artifact_id = connection.execute(
                text(
                    """
                    SELECT version.artifact_id
                    FROM product.product_enrollment enrollment
                    JOIN product.product_version version
                      ON version.product_version_id = enrollment.product_version_id
                    WHERE enrollment.product_enrollment_id = :enrollment_id
                    """
                ),
                {"enrollment_id": request.product_enrollment_id},
            ).scalar_one()

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            item = (
                connection.execute(
                    text("SELECT * FROM ops.work_item WHERE work_item_id = :id FOR UPDATE"),
                    {"id": request.work_item_id},
                )
                .mappings()
                .one()
            )
            if item["status"] != "running" or item["lease_owner"] != worker_id:
                raise RuntimeError("Only the monitoring lease owner may publish a Snapshot")
            connection.execute(
                text("""
                INSERT INTO product.monitoring_snapshot (
                    monitoring_snapshot_id, artifact_id, product_enrollment_id,
                    data_bundle_artifact_id, as_of_session, known_at, health,
                    session_count, decision_count, primary_nav, stress_nav,
                    metrics, health_components
                ) VALUES (:id, :artifact_id, :enrollment_id, :data_bundle,
                          :as_of_session, :known_at, :health, :sessions, :decisions,
                          :primary_nav, :stress_nav, CAST(:metrics AS jsonb),
                          CAST(:components AS jsonb))
            """),
                {
                    "id": uuid.uuid4(),
                    "artifact_id": artifact_id,
                    "enrollment_id": request.product_enrollment_id,
                    "data_bundle": request.data_bundle_artifact_id,
                    "as_of_session": request.as_of_session,
                    "known_at": request.known_at,
                    "health": health.overall,
                    "sessions": output.evidence.session_count,
                    "decisions": output.evidence.decision_count,
                    "primary_nav": output.primary_nav,
                    "stress_nav": output.stress_nav,
                    "metrics": _json(output.metrics),
                    "components": _json(
                        {
                            **output.health_components,
                            "reason_codes": health.reason_codes,
                            "held_during_suspension": request.held_during_suspension,
                        }
                    ),
                },
            )
            connection.execute(
                text("""
                UPDATE product.product_enrollment
                SET health = CAST(:health AS varchar), updated_at = now(),
                    monitoring_start_at = COALESCE(monitoring_start_at, :known_at),
                    first_decision_at = CASE WHEN :rebalance_due
                        THEN COALESCE(first_decision_at, :known_at)
                        ELSE first_decision_at END,
                    first_execution_at = CASE WHEN :executed_target
                        THEN COALESCE(first_execution_at, :known_at)
                        ELSE first_execution_at END,
                    first_warning_at = CASE
                        WHEN CAST(:health AS varchar) IN ('warning','data_interrupted')
                        THEN COALESCE(first_warning_at, :known_at)
                        ELSE first_warning_at END,
                    review_required_at = CASE WHEN :review_required
                        THEN COALESCE(review_required_at, :known_at)
                        ELSE review_required_at END
                WHERE product_enrollment_id = :enrollment_id
            """),
                {
                    "health": health.overall,
                    "known_at": request.known_at,
                    "rebalance_due": request.rebalance_due,
                    "executed_target": bool(output.health_components.get("executed_target")),
                    "review_required": bool(
                        health.overall == "warning"
                        and health.performance_ready
                        and health.predictive_ready
                    ),
                    "enrollment_id": request.product_enrollment_id,
                },
            )
            if health.overall in {"warning", "data_interrupted"}:
                self._open_alert(connection, request, health.overall, health.reason_codes)
            _complete_work_item(connection, request.work_item_id, artifact_id)

        result = self._artifacts.publish(
            artifact_type="monitoring_snapshot",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=payload,
            content_payload=payload,
            dependencies=(
                DependencyInput(version_artifact_id, "product_version"),
                DependencyInput(request.data_bundle_artifact_id, "data_bundle"),
            ),
            draft_writer=write,
        )
        return result.artifact_id

    @staticmethod
    def _open_alert(
        connection: Connection,
        request: MonitoringRequest,
        health: str,
        reason_codes: tuple[str, ...],
    ) -> None:
        alert_key = f"{health}__{request.as_of_session.isoformat()}"
        alert_id = uuid.uuid4()
        inserted = connection.execute(
            text("""
            INSERT INTO product.product_alert (
                product_alert_id, product_enrollment_id, alert_key, alert_type,
                severity, opened_at, evidence
            ) VALUES (:id, :enrollment_id, :key, :type, :severity,
                      :opened_at, CAST(:evidence AS jsonb))
            ON CONFLICT (product_enrollment_id, alert_key) DO NOTHING
            RETURNING product_alert_id
        """),
            {
                "id": alert_id,
                "enrollment_id": request.product_enrollment_id,
                "key": alert_key,
                "type": health,
                "severity": "critical" if health == "data_interrupted" else "warning",
                "opened_at": request.known_at,
                "evidence": _json({"reason_codes": reason_codes}),
            },
        ).scalar_one_or_none()
        if inserted is not None:
            connection.execute(
                text("""
                INSERT INTO product.product_alert_event (
                    product_alert_event_id, product_alert_id, sequence_number,
                    from_status, to_status, occurred_at
                ) VALUES (:event_id, :alert_id, 1, NULL, 'open', :occurred_at)
            """),
                {"event_id": uuid.uuid4(), "alert_id": inserted, "occurred_at": request.known_at},
            )


class MonitoringWorker:
    def __init__(
        self,
        engine: Engine,
        *,
        worker_id: str,
        calculator: Callable[[MonitoringRequest], MonitoringOutput],
    ) -> None:
        self._engine = engine
        self._worker_id = worker_id
        self._calculator = calculator
        self._queue = WorkQueueService(engine)
        self._materializer = MonitoringSnapshotMaterializer(engine)

    def run_once(self) -> WorkerOutcome:
        item = self._queue.claim(worker_id=self._worker_id, work_types=("monitoring",))
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

        heartbeat = threading.Thread(target=keep_lease, daemon=True)
        heartbeat.start()
        request: MonitoringRequest | None = None
        try:
            request = self._request(item.work_item_id)
            output = self._calculator(request)
            if lease_lost.is_set():
                raise ClassifiedWorkFailure(
                    WorkFailureClass.INTERRUPTED, "Monitoring Work Item lease was lost"
                )
            if self._queue.cancellation_requested(item.work_item_id, worker_id=self._worker_id):
                raise CancellationRequested
            artifact_id = self._materializer.complete(
                request=request,
                output=output,
                worker_id=self._worker_id,
            )
            return WorkerOutcome(item.work_item_id, "completed", artifact_id)
        except CancellationRequested:
            self._queue.finish(item.work_item_id, worker_id=self._worker_id, status="cancelled")
            return WorkerOutcome(item.work_item_id, "cancelled")
        except ClassifiedWorkFailure as error:
            if request is not None and error.failure_class in {
                WorkFailureClass.DATA_QUALITY,
                WorkFailureClass.CONTRACT,
            }:
                # A hard monitoring-contract interruption is itself an OOS
                # observation.  Preserve the last real NAV/holdings, append a
                # data_interrupted Snapshot and Alert, and never make this state
                # invisible by recording it only in the operational queue.
                interrupted = self._interrupted_output(request, error)
                artifact_id = self._materializer.complete(
                    request=request,
                    output=interrupted,
                    worker_id=self._worker_id,
                )
                return WorkerOutcome(item.work_item_id, "completed", artifact_id)
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

    def _interrupted_output(
        self, request: MonitoringRequest, error: ClassifiedWorkFailure
    ) -> MonitoringOutput:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT spec.frequency,
                               snapshot.primary_nav, snapshot.stress_nav,
                               COALESCE(snapshot.metrics, '{}'::jsonb) AS metrics,
                               COALESCE(snapshot.session_count, 0) AS session_count,
                               COALESCE(snapshot.decision_count, 0) AS decision_count,
                               COALESCE(snapshot.health_components, '{}'::jsonb)
                                   AS health_components
                        FROM product.product_enrollment enrollment
                        JOIN product.product_version version
                          ON version.product_version_id = enrollment.product_version_id
                        JOIN strategy.compiled_strategy_version strategy
                          ON strategy.compiled_strategy_version_id =
                             version.compiled_strategy_version_id
                        JOIN workspace.compiled_research_spec spec
                          ON spec.compiled_research_spec_id =
                             strategy.compiled_research_spec_id
                        LEFT JOIN LATERAL (
                            SELECT * FROM product.monitoring_snapshot snapshot
                            WHERE snapshot.product_enrollment_id =
                                  enrollment.product_enrollment_id
                            ORDER BY snapshot.as_of_session DESC LIMIT 1
                        ) snapshot ON true
                        WHERE enrollment.product_enrollment_id = :enrollment_id
                        """
                    ),
                    {"enrollment_id": request.product_enrollment_id},
                )
                .mappings()
                .one()
            )
        metrics = dict(row["metrics"])
        pending_date = metrics.get("pending_decision_date")
        stale_pending = bool(
            request.held_during_suspension
            or (pending_date and date.fromisoformat(str(pending_date)) < request.as_of_session)
        )
        if stale_pending:
            metrics["pending_target_holdings"] = []
            metrics["pending_decision_date"] = None
        metrics["interruption"] = {
            "failure_class": error.failure_class.value,
            "message": str(error),
            "details": error.details,
            "as_of_session": request.as_of_session.isoformat(),
            "execution_interrupted": stale_pending,
        }
        return MonitoringOutput(
            evidence=MonitoringEvidence(
                frequency=row["frequency"],
                session_count=int(row["session_count"]),
                decision_count=int(row["decision_count"]),
                data_contract_ok=False,
                capacity_ok=True,
            ),
            primary_nav=Decimal(str(row["primary_nav"] or 1)),
            stress_nav=Decimal(str(row["stress_nav"] or 1)),
            metrics=metrics,
            health_components={
                **dict(row["health_components"]),
                "data_contract_interrupted": True,
                "failure_class": error.failure_class.value,
                "failure_message": str(error),
                "failure_details": error.details,
                "executed_target": False,
            },
        )

    def _request(self, work_item_id: uuid.UUID) -> MonitoringRequest:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT * FROM product.monitoring_work_item "
                        "WHERE work_item_id = :work_item_id"
                    ),
                    {"work_item_id": work_item_id},
                )
                .mappings()
                .one()
            )
        return MonitoringRequest(
            work_item_id=work_item_id,
            product_enrollment_id=row["product_enrollment_id"],
            data_bundle_artifact_id=row["data_bundle_artifact_id"],
            as_of_session=row["as_of_session"],
            known_at=row["known_at"],
            held_during_suspension=row["held_during_suspension"],
            rebalance_due=row["rebalance_due"],
        )


def _complete_work_item(
    connection: Connection, work_item_id: uuid.UUID, result_artifact_id: uuid.UUID
) -> None:
    connection.execute(
        text("""
        UPDATE ops.work_item SET status = 'completed', stage = 'completed',
            lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
            updated_at = now() WHERE work_item_id = :work_item_id
    """),
        {"work_item_id": work_item_id},
    )
    sequence = connection.execute(
        text(
            """
            SELECT COALESCE(max(sequence_number), 0) + 1
            FROM ops.work_item_event WHERE work_item_id = :id
            """
        ),
        {"id": work_item_id},
    ).scalar_one()
    connection.execute(
        text("""
        INSERT INTO ops.work_item_event (
            work_item_event_id, work_item_id, sequence_number, event_type,
            from_status, to_status, details
        ) VALUES (:event_id, :work_item_id, :sequence, 'completed',
                  'running', 'completed', CAST(:details AS jsonb))
    """),
        {
            "event_id": uuid.uuid4(),
            "work_item_id": work_item_id,
            "sequence": sequence,
            "details": _json({"result_artifact_id": str(result_artifact_id)}),
        },
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
