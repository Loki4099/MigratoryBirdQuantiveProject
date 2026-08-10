from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Engine, text

from style_rotation.domain.enums import ProductLifecycle
from style_rotation.domain.lifecycle import ensure_product_transition


class ProductRevisionConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LifecycleChange:
    enrollment_id: uuid.UUID
    from_lifecycle: str
    to_lifecycle: str
    revision: int
    event_sequence: int
    effective_at: datetime
    applied: bool


class ProductLifecycleService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def change(
        self,
        enrollment_id: uuid.UUID,
        *,
        target: Literal["active", "suspended", "retired", "invalidated"],
        expected_revision: int,
        reason_code: str,
        reason: str,
        researcher_id: str,
        requested_at: datetime,
        effective_at: datetime,
    ) -> LifecycleChange:
        if not reason_code.strip() or not reason.strip() or not researcher_id.strip():
            raise ValueError("Lifecycle changes require researcher and explicit reasons")
        if requested_at > effective_at:
            raise ValueError("Lifecycle effective time cannot precede request")
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT lifecycle, revision FROM product.product_enrollment "
                        "WHERE product_enrollment_id = :id FOR UPDATE"
                    ),
                    {"id": enrollment_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError(f"Product enrollment not found: {enrollment_id}")
            if row["revision"] != expected_revision:
                raise ProductRevisionConflict("Product enrollment revision conflict")
            pending = connection.execute(
                text(
                    "SELECT 1 FROM product.product_lifecycle_event "
                    "WHERE product_enrollment_id = :id AND applied_at IS NULL FOR UPDATE"
                ),
                {"id": enrollment_id},
            ).scalar_one_or_none()
            if pending is not None:
                raise ProductRevisionConflict(
                    "Product enrollment already has a pending lifecycle transition"
                )
            current = ProductLifecycle(row["lifecycle"])
            next_state = ProductLifecycle(target)
            ensure_product_transition(current, next_state)
            sequence = connection.execute(
                text(
                    "SELECT COALESCE(max(sequence_number), 0) + 1 FROM "
                    "product.product_lifecycle_event WHERE product_enrollment_id = :id"
                ),
                {"id": enrollment_id},
            ).scalar_one()
            next_revision = expected_revision + 1
            apply_now = effective_at <= requested_at
            connection.execute(
                text("""
                UPDATE product.product_enrollment
                SET lifecycle = CASE WHEN :apply_now THEN CAST(:target AS varchar)
                                     ELSE lifecycle END,
                    revision = :revision,
                    updated_at = now(),
                    retirement_requested_at = CASE
                        WHEN CAST(:target AS varchar) = 'retired'
                        THEN :requested ELSE retirement_requested_at END,
                    retirement_effective_at = CASE
                        WHEN CAST(:target AS varchar) = 'retired'
                        THEN :effective ELSE retirement_effective_at END
                WHERE product_enrollment_id = :id
            """),
                {
                    "id": enrollment_id,
                    "target": target,
                    "revision": next_revision,
                    "requested": requested_at,
                    "effective": effective_at,
                    "apply_now": apply_now,
                },
            )
            connection.execute(
                text("""
                INSERT INTO product.product_lifecycle_event (
                    product_lifecycle_event_id, product_enrollment_id, sequence_number,
                    from_lifecycle, to_lifecycle, reason_code, reason, researcher_id,
                    requested_at, effective_at, applied_at
                ) VALUES (
                    :event_id, :id, :sequence, :current, :target, :reason_code,
                    :reason, :researcher, :requested, :effective, :applied
                )
            """),
                {
                    "event_id": uuid.uuid4(),
                    "id": enrollment_id,
                    "sequence": sequence,
                    "current": current.value,
                    "target": target,
                    "reason_code": reason_code,
                    "reason": reason,
                    "researcher": researcher_id,
                    "requested": requested_at,
                    "effective": effective_at,
                    "applied": effective_at if apply_now else None,
                },
            )
        return LifecycleChange(
            enrollment_id, current.value, target, next_revision, sequence, effective_at, apply_now
        )

    def apply_due(self, *, as_of: datetime) -> tuple[LifecycleChange, ...]:
        """Apply requested transitions only when their explicit effective time arrives."""
        applied: list[LifecycleChange] = []
        with self._engine.begin() as connection:
            rows = (
                connection.execute(
                    text("""
                    SELECT event.*, enrollment.lifecycle, enrollment.revision
                    FROM product.product_lifecycle_event event
                    JOIN product.product_enrollment enrollment
                      ON enrollment.product_enrollment_id = event.product_enrollment_id
                    WHERE event.applied_at IS NULL AND event.effective_at <= :as_of
                    ORDER BY event.effective_at, event.product_enrollment_id,
                             event.sequence_number
                    FOR UPDATE OF event, enrollment SKIP LOCKED
                """),
                    {"as_of": as_of},
                )
                .mappings()
                .all()
            )
            for row in rows:
                current = ProductLifecycle(row["lifecycle"])
                target = ProductLifecycle(row["to_lifecycle"])
                if current.value != row["from_lifecycle"]:
                    raise ProductRevisionConflict(
                        "Pending lifecycle transition no longer matches current state"
                    )
                ensure_product_transition(current, target)
                next_revision = row["revision"] + 1
                connection.execute(
                    text("""
                    UPDATE product.product_enrollment
                    SET lifecycle = CAST(:target AS varchar), revision = :revision,
                        updated_at = now(),
                        retirement_effective_at = CASE WHEN :target = 'retired'
                            THEN :effective ELSE retirement_effective_at END
                    WHERE product_enrollment_id = :id
                """),
                    {
                        "id": row["product_enrollment_id"],
                        "target": target.value,
                        "revision": next_revision,
                        "effective": row["effective_at"],
                    },
                )
                connection.execute(
                    text(
                        "UPDATE product.product_lifecycle_event SET applied_at = :as_of "
                        "WHERE product_lifecycle_event_id = :event_id"
                    ),
                    {"as_of": as_of, "event_id": row["product_lifecycle_event_id"]},
                )
                applied.append(
                    LifecycleChange(
                        row["product_enrollment_id"],
                        current.value,
                        target.value,
                        next_revision,
                        row["sequence_number"],
                        row["effective_at"],
                        True,
                    )
                )
        return tuple(applied)
