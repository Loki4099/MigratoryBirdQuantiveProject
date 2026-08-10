from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Engine, text

from style_rotation.domain.enums import AlertStatus
from style_rotation.domain.lifecycle import ensure_alert_transition


@dataclass(frozen=True, slots=True)
class AlertChange:
    alert_id: uuid.UUID
    from_status: str
    to_status: str
    sequence_number: int
    occurred_at: datetime


class ProductAlertService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def change(
        self,
        alert_id: uuid.UUID,
        *,
        target: Literal["acknowledged", "resolved", "superseded"],
        researcher_id: str,
        note: str | None,
        occurred_at: datetime,
    ) -> AlertChange:
        if not researcher_id.strip():
            raise ValueError("Alert change requires a researcher")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("Alert event time must be timezone-aware")
        with self._engine.begin() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM product.product_alert WHERE product_alert_id = :id FOR UPDATE"),
                {"id": alert_id},
            ).scalar_one_or_none()
            if exists is None:
                raise LookupError(f"Product alert not found: {alert_id}")
            latest = (
                connection.execute(
                    text(
                        """
                    SELECT sequence_number, to_status
                    FROM product.product_alert_event
                    WHERE product_alert_id = :id
                    ORDER BY sequence_number DESC LIMIT 1
                    """
                    ),
                    {"id": alert_id},
                )
                .mappings()
                .one_or_none()
            )
            current = AlertStatus(latest["to_status"] if latest else "open")
            next_status = AlertStatus(target)
            ensure_alert_transition(current, next_status)
            sequence = int(latest["sequence_number"] if latest else 0) + 1
            connection.execute(
                text(
                    """
                    INSERT INTO product.product_alert_event (
                        product_alert_event_id, product_alert_id, sequence_number,
                        from_status, to_status, researcher_id, note, occurred_at
                    ) VALUES (:event_id, :alert_id, :sequence, :from_status,
                              :to_status, :researcher, :note, :occurred_at)
                    """
                ),
                {
                    "event_id": uuid.uuid4(),
                    "alert_id": alert_id,
                    "sequence": sequence,
                    "from_status": current.value,
                    "to_status": target,
                    "researcher": researcher_id.strip(),
                    "note": note,
                    "occurred_at": occurred_at,
                },
            )
        return AlertChange(alert_id, current.value, target, sequence, occurred_at)
