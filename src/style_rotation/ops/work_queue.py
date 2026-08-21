from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Engine, text
from sqlalchemy.engine import RowMapping

from style_rotation.domain.enums import WorkFailureClass, WorkItemStatus
from style_rotation.domain.lifecycle import ensure_work_item_transition

WorkType = Literal["predictive", "portfolio", "monitoring", "export", "asset_export"]


@dataclass(frozen=True, slots=True)
class WorkItem:
    work_item_id: uuid.UUID
    specification_fingerprint: str
    work_type: str
    status: str
    priority: int
    stage: str
    attempt_count: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    cancel_requested_at: datetime | None
    failure_class: str | None

    @classmethod
    def from_row(cls, row: RowMapping) -> WorkItem:
        fields = cls.__dataclass_fields__
        return cls(**{key: row[key] for key in fields})

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["work_item_id"] = str(self.work_item_id)
        return payload


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    item: WorkItem
    reused_terminal_result: bool


class WorkQueueService:
    """Persistent lease queue; workers never infer retryability from exception text."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def enqueue(
        self,
        *,
        specification_fingerprint: str,
        work_type: WorkType,
        priority: int = 100,
        max_attempts: int = 3,
    ) -> EnqueueResult:
        _validate_fingerprint(specification_fingerprint)
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        with self._engine.begin() as connection:
            terminal = (
                connection.execute(
                    text(
                        """
                    SELECT * FROM ops.work_item
                    WHERE specification_fingerprint = :fingerprint AND work_type = :work_type
                      AND status IN ('completed', 'reused')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                    ),
                    {"fingerprint": specification_fingerprint, "work_type": work_type},
                )
                .mappings()
                .one_or_none()
            )
            if terminal is not None:
                return EnqueueResult(WorkItem.from_row(terminal), True)
            item_id = uuid.uuid4()
            inserted = (
                connection.execute(
                    text(
                        """
                    INSERT INTO ops.work_item (
                        work_item_id, specification_fingerprint, work_type, priority,
                        max_attempts
                    ) VALUES (:id, :fingerprint, :work_type, :priority, :max_attempts)
                    ON CONFLICT (specification_fingerprint, work_type)
                        WHERE status IN ('queued', 'running')
                    DO NOTHING
                    RETURNING *
                    """
                    ),
                    {
                        "id": item_id,
                        "fingerprint": specification_fingerprint,
                        "work_type": work_type,
                        "priority": priority,
                        "max_attempts": max_attempts,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if inserted is not None:
                self._append_event(connection, item_id, "enqueued", None, "queued", {})
                return EnqueueResult(WorkItem.from_row(inserted), False)

            # A concurrent enqueue won the partial unique-index race.  The
            # INSERT waits for that transaction, so this read observes its
            # committed active or terminal row instead of surfacing an
            # IntegrityError to the caller.
            existing = (
                connection.execute(
                    text(
                        """
                        SELECT * FROM ops.work_item
                        WHERE specification_fingerprint = :fingerprint
                          AND work_type = :work_type
                          AND status IN ('queued', 'running', 'completed', 'reused')
                        ORDER BY
                          CASE WHEN status IN ('completed', 'reused') THEN 0 ELSE 1 END,
                          created_at DESC
                        LIMIT 1
                        """
                    ),
                    {"fingerprint": specification_fingerprint, "work_type": work_type},
                )
                .mappings()
                .one_or_none()
            )
            if existing is None:
                raise RuntimeError("Concurrent Work Item enqueue could not be resolved")
            reused = existing["status"] in {"completed", "reused"}
            return EnqueueResult(WorkItem.from_row(existing), reused)

    def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 120,
        work_types: tuple[WorkType, ...] = (
            "predictive",
            "portfolio",
            "monitoring",
            "export",
            "asset_export",
        ),
    ) -> WorkItem | None:
        if not worker_id.strip() or lease_seconds < 10:
            raise ValueError("Worker id is required and lease must be at least 10 seconds")
        if not work_types:
            raise ValueError("At least one Work Item type must be claimable")
        with self._engine.begin() as connection:
            self._terminalize_expired(connection, work_types)
            row = (
                connection.execute(
                    text(
                        """
                    WITH candidate AS (
                        SELECT work.work_item_id, work.status AS previous_status
                        FROM ops.work_item work
                        WHERE (
                            (work.status = 'queued' AND work.available_at <= now())
                            OR (work.status = 'running' AND work.lease_expires_at <= now())
                        )
                          AND work.work_type = ANY(CAST(:work_types AS text[]))
                          AND work.cancel_requested_at IS NULL
                          AND work.attempt_count < work.max_attempts
                          AND (
                            work.work_type <> 'portfolio'
                            OR EXISTS (
                                SELECT 1
                                FROM experiment.research_suite_work_item portfolio_link
                                JOIN experiment.portfolio_cell_specification portfolio_cell
                                  ON portfolio_cell.artifact_id =
                                     portfolio_link.cell_artifact_id
                                JOIN strategy.compiled_strategy_version strategy
                                  ON strategy.compiled_strategy_version_id =
                                     portfolio_cell.compiled_strategy_version_id
                                JOIN experiment.predictive_cell_specification predictive_cell
                                  ON predictive_cell.research_suite_id =
                                     portfolio_cell.research_suite_id
                                 AND predictive_cell.compiled_model_instance_id =
                                     strategy.compiled_model_instance_id
                                JOIN experiment.cell_result predictive_result
                                  ON predictive_result.cell_artifact_id =
                                     predictive_cell.artifact_id
                                 AND predictive_result.result_type = 'predictive'
                                 AND predictive_result.availability_status = 'accepted'
                                WHERE portfolio_link.work_item_id = work.work_item_id
                                  AND portfolio_link.cell_type = 'portfolio'
                            )
                          )
                          AND (
                            work.work_type <> 'monitoring'
                            OR NOT EXISTS (
                                SELECT 1
                                FROM product.monitoring_work_item current_monitor
                                JOIN product.monitoring_work_item earlier_monitor
                                  ON earlier_monitor.product_enrollment_id =
                                     current_monitor.product_enrollment_id
                                 AND (
                                   earlier_monitor.as_of_session <
                                       current_monitor.as_of_session
                                   OR (
                                     earlier_monitor.as_of_session =
                                         current_monitor.as_of_session
                                     AND earlier_monitor.known_at < current_monitor.known_at
                                   )
                                 )
                                JOIN ops.work_item earlier_work
                                  ON earlier_work.work_item_id = earlier_monitor.work_item_id
                                WHERE current_monitor.work_item_id = work.work_item_id
                                  AND earlier_work.status IN ('queued', 'running')
                            )
                          )
                        ORDER BY work.priority, work.created_at
                        FOR UPDATE SKIP LOCKED LIMIT 1
                    )
                    UPDATE ops.work_item item
                    SET status = 'running', stage = 'claimed', attempt_count = attempt_count + 1,
                        lease_owner = :worker, lease_expires_at = now() + :lease,
                        heartbeat_at = now(), updated_at = now()
                    FROM candidate WHERE item.work_item_id = candidate.work_item_id
                    RETURNING item.*, candidate.previous_status
                    """
                    ),
                    {
                        "worker": worker_id,
                        "lease": timedelta(seconds=lease_seconds),
                        "work_types": list(work_types),
                    },
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            item = WorkItem.from_row(row)
            previous_status = row["previous_status"]
            self._append_event(
                connection,
                item.work_item_id,
                "reclaimed" if previous_status == "running" else "claimed",
                previous_status,
                "running",
                {"worker_id": worker_id, "attempt": item.attempt_count},
            )
            return item

    def cancellation_requested(self, work_item_id: uuid.UUID, *, worker_id: str) -> bool:
        with self._engine.connect() as connection:
            value = connection.execute(
                text(
                    "SELECT cancel_requested_at IS NOT NULL FROM ops.work_item "
                    "WHERE work_item_id = :id AND status = 'running' AND lease_owner = :worker"
                ),
                {"id": work_item_id, "worker": worker_id},
            ).scalar_one_or_none()
        return bool(value)

    def _terminalize_expired(self, connection: Any, work_types: tuple[WorkType, ...]) -> None:
        """Close expired work that cannot legally be reclaimed.

        Cancellation and an exhausted attempt budget are terminal even if the
        previous worker disappeared before it could acknowledge that state.
        """
        rows = (
            connection.execute(
                text(
                    """
                    SELECT * FROM ops.work_item
                    WHERE status = 'running' AND lease_expires_at <= now()
                      AND work_type = ANY(CAST(:work_types AS text[]))
                      AND (cancel_requested_at IS NOT NULL OR attempt_count >= max_attempts)
                    ORDER BY priority, created_at
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {"work_types": list(work_types)},
            )
            .mappings()
            .all()
        )
        for row in rows:
            cancelled = row["cancel_requested_at"] is not None
            status = "cancelled" if cancelled else "failed"
            failure_class = None if cancelled else WorkFailureClass.INFRASTRUCTURE.value
            details = {} if cancelled else {"message": "lease expired after final attempt"}
            connection.execute(
                text(
                    """
                    UPDATE ops.work_item
                    SET status = :status, stage = :status, lease_owner = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL,
                        failure_class = :failure_class,
                        failure_details = CAST(:details AS jsonb), updated_at = now()
                    WHERE work_item_id = :id
                    """
                ),
                {
                    "id": row["work_item_id"],
                    "status": status,
                    "failure_class": failure_class,
                    "details": _json(details),
                },
            )
            self._append_event(
                connection,
                row["work_item_id"],
                "lease_cancelled" if cancelled else "lease_exhausted",
                "running",
                status,
                details,
            )

    def heartbeat(
        self, work_item_id: uuid.UUID, *, worker_id: str, lease_seconds: int = 120
    ) -> WorkItem:
        if lease_seconds < 10:
            raise ValueError("Lease must be at least 10 seconds")
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    UPDATE ops.work_item SET heartbeat_at = now(),
                        lease_expires_at = now() + :lease, updated_at = now()
                    WHERE work_item_id = :id AND status = 'running'
                      AND lease_owner = :worker AND lease_expires_at > now()
                    RETURNING *
                    """
                    ),
                    {
                        "id": work_item_id,
                        "worker": worker_id,
                        "lease": timedelta(seconds=lease_seconds),
                    },
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise RuntimeError("Work item lease is not owned or has expired")
            return WorkItem.from_row(row)

    def request_cancel(self, work_item_id: uuid.UUID) -> WorkItem:
        with self._engine.begin() as connection:
            current = self._get_locked(connection, work_item_id)
            if current.status == WorkItemStatus.QUEUED:
                ensure_work_item_transition(WorkItemStatus.QUEUED, WorkItemStatus.CANCELLED)
                row = (
                    connection.execute(
                        text(
                            """
                        UPDATE ops.work_item SET status = 'cancelled', stage = 'cancelled',
                            cancel_requested_at = now(), updated_at = now()
                        WHERE work_item_id = :id RETURNING *
                        """
                        ),
                        {"id": work_item_id},
                    )
                    .mappings()
                    .one()
                )
                self._append_event(connection, work_item_id, "cancelled", "queued", "cancelled", {})
                return WorkItem.from_row(row)
            if current.status == WorkItemStatus.RUNNING:
                row = (
                    connection.execute(
                        text(
                            """
                            UPDATE ops.work_item
                            SET cancel_requested_at = now(), updated_at = now()
                            WHERE work_item_id = :id RETURNING *
                            """
                        ),
                        {"id": work_item_id},
                    )
                    .mappings()
                    .one()
                )
                self._append_event(
                    connection, work_item_id, "cancel_requested", "running", "running", {}
                )
                return WorkItem.from_row(row)
            return current

    def finish(
        self,
        work_item_id: uuid.UUID,
        *,
        worker_id: str,
        status: Literal["completed", "failed", "cancelled"],
        failure_class: WorkFailureClass | None = None,
        failure_details: dict[str, Any] | None = None,
    ) -> WorkItem:
        with self._engine.begin() as connection:
            return self.finish_in_transaction(
                connection,
                work_item_id,
                worker_id=worker_id,
                status=status,
                failure_class=failure_class,
                failure_details=failure_details,
            )

    def finish_in_transaction(
        self,
        connection: Any,
        work_item_id: uuid.UUID,
        *,
        worker_id: str,
        status: Literal["completed", "failed", "cancelled"],
        failure_class: WorkFailureClass | None = None,
        failure_details: dict[str, Any] | None = None,
    ) -> WorkItem:
        """Finish leased work inside the caller's transaction.

        Publishers use this to commit their immutable result and terminal queue
        transition atomically, so a process crash cannot leave a published
        result attached to a still-running work item.
        """
        target = WorkItemStatus(status)
        current = self._get_locked(connection, work_item_id)
        if current.status != WorkItemStatus.RUNNING or current.lease_owner != worker_id:
            raise RuntimeError("Only the active lease owner can finish a work item")
        ensure_work_item_transition(WorkItemStatus.RUNNING, target)
        if target == WorkItemStatus.FAILED and failure_class is None:
            raise ValueError("Failed work item requires an explicit failure class")
        if target != WorkItemStatus.FAILED and failure_class is not None:
            raise ValueError("Failure class is only valid for failed work")
        row = (
            connection.execute(
                text(
                    """
                    UPDATE ops.work_item SET status = :status, stage = :status,
                        lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                        failure_class = :failure_class,
                        failure_details = CAST(:failure_details AS jsonb), updated_at = now()
                    WHERE work_item_id = :id RETURNING *
                    """
                ),
                {
                    "id": work_item_id,
                    "status": status,
                    "failure_class": failure_class.value if failure_class else None,
                    "failure_details": _json(failure_details or {}),
                },
            )
            .mappings()
            .one()
        )
        self._append_event(
            connection,
            work_item_id,
            status,
            "running",
            status,
            {"failure_class": failure_class.value if failure_class else None},
        )
        return WorkItem.from_row(row)

    def retry(self, work_item_id: uuid.UUID) -> WorkItem:
        with self._engine.begin() as connection:
            current = self._get_locked(connection, work_item_id)
            ensure_work_item_transition(WorkItemStatus(current.status), WorkItemStatus.QUEUED)
            if current.failure_class not in {
                WorkFailureClass.INFRASTRUCTURE,
                WorkFailureClass.INTERRUPTED,
            }:
                raise ValueError("Only infrastructure/interrupted failures may retry unchanged")
            if current.attempt_count >= current.max_attempts:
                raise ValueError("Work item exhausted its attempt budget")
            row = (
                connection.execute(
                    text(
                        """
                    UPDATE ops.work_item SET status = 'queued', stage = 'queued',
                        failure_class = NULL, failure_details = NULL, available_at = now(),
                        updated_at = now() WHERE work_item_id = :id RETURNING *
                    """
                    ),
                    {"id": work_item_id},
                )
                .mappings()
                .one()
            )
            self._append_event(connection, work_item_id, "retried", "failed", "queued", {})
            return WorkItem.from_row(row)

    @staticmethod
    def _get_locked(connection: Any, work_item_id: uuid.UUID) -> WorkItem:
        row = (
            connection.execute(
                text("SELECT * FROM ops.work_item WHERE work_item_id = :id FOR UPDATE"),
                {"id": work_item_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError(f"Work item not found: {work_item_id}")
        return WorkItem.from_row(row)

    @staticmethod
    def _append_event(
        connection: Any,
        work_item_id: uuid.UUID,
        event_type: str,
        from_status: str | None,
        to_status: str | None,
        details: dict[str, Any],
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO ops.work_item_event (
                    work_item_event_id, work_item_id, sequence_number, event_type,
                    from_status, to_status, details
                ) SELECT :event_id, :item_id, COALESCE(max(sequence_number), 0) + 1,
                         :event_type, :from_status, :to_status, CAST(:details AS jsonb)
                  FROM ops.work_item_event WHERE work_item_id = :item_id
                """
            ),
            {
                "event_id": uuid.uuid4(),
                "item_id": work_item_id,
                "event_type": event_type,
                "from_status": from_status,
                "to_status": to_status,
                "details": _json(details),
            },
        )


def _validate_fingerprint(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Specification fingerprint must be lowercase SHA-256")


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))
