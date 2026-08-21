from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import Engine, text

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.product_runtime import (
    DecisionScheduleService,
    DecisionSessionInput,
    ProductEnrollmentService,
)

_COMMAND_KIND = "enroll_v022_product_candidate"


@dataclass(frozen=True, slots=True)
class ProductEnrollmentCommand:
    execution_version_id: uuid.UUID
    qualification_version_id: uuid.UUID
    monitoring_policy_version_id: uuid.UUID
    schedule_key: str
    schedule_version_number: int
    frequency: Literal["weekly", "monthly"]
    sessions: tuple[DecisionSessionInput, ...]
    oos_anchor_cutoff_at: datetime
    activation_effective_at: datetime


class ProductEnrollmentCommandService:
    """Explicitly enroll one immutable v0.22 Product candidate."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._schedules = DecisionScheduleService(engine)
        self._enrollments = ProductEnrollmentService(engine)

    def enroll(
        self,
        *,
        command: ProductEnrollmentCommand,
        actor_key: str,
        idempotency_key: uuid.UUID,
    ) -> dict[str, Any]:
        request = {
            "contract_version": "v0.22.0",
            "execution_version_id": str(command.execution_version_id),
            "qualification_version_id": str(command.qualification_version_id),
            "monitoring_policy_version_id": str(command.monitoring_policy_version_id),
            "schedule_key": command.schedule_key,
            "schedule_version_number": command.schedule_version_number,
            "frequency": command.frequency,
            "sessions": [
                {
                    "session_date": item.session_date.isoformat(),
                    "decision_cutoff_at": item.decision_cutoff_at.isoformat(),
                }
                for item in command.sessions
            ],
            "oos_anchor_cutoff_at": command.oos_anchor_cutoff_at.isoformat(),
            "activation_effective_at": command.activation_effective_at.isoformat(),
        }
        request_fingerprint = sha256_hexdigest(request)
        lock_key = f"{actor_key}:{_COMMAND_KIND}:{idempotency_key}"
        with self._engine.connect() as lock_connection:
            lock_connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key,0))"),
                {"key": lock_key},
            )
            lock_connection.commit()
            try:
                replay = _replay(
                    lock_connection,
                    actor_key=actor_key,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                )
                if replay is not None:
                    return {**replay, "reused": True}
                lock_connection.commit()
                _require_product_data_disclosure(
                    lock_connection,
                    execution_version_id=command.execution_version_id,
                    qualification_version_id=command.qualification_version_id,
                )
                lock_connection.commit()
                schedule = self._schedules.publish(
                    schedule_key=command.schedule_key,
                    version_number=command.schedule_version_number,
                    frequency=command.frequency,
                    sessions=command.sessions,
                )
                enrollment = self._enrollments.publish(
                    execution_version_id=command.execution_version_id,
                    qualification_version_id=command.qualification_version_id,
                    monitoring_policy_version_id=command.monitoring_policy_version_id,
                    decision_schedule_version_id=schedule.decision_schedule_version_id,
                    oos_anchor_cutoff_at=command.oos_anchor_cutoff_at,
                    activation_effective_at=command.activation_effective_at,
                )
                response = {
                    "product_enrollment_id": str(enrollment.product_enrollment_id),
                    "enrollment_artifact_id": str(enrollment.artifact_id),
                    "decision_schedule_version_id": str(
                        schedule.decision_schedule_version_id
                    ),
                    "decision_schedule_artifact_id": str(schedule.artifact_id),
                    "first_eligible_decision_session_id": str(
                        enrollment.first_eligible_decision_session_id
                    ),
                    "lifecycle": "active",
                    "reused": schedule.reused and enrollment.reused,
                }
                with lock_connection.begin():
                    lock_connection.execute(
                        text(
                            """
                            INSERT INTO workspace.v022_command_result (
                              command_result_id,actor_key,command_kind,idempotency_key,
                              request_fingerprint,response_document
                            ) VALUES (
                              :id,:actor,:kind,:key,:fingerprint,CAST(:response AS jsonb)
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid4(),
                            "actor": actor_key,
                            "kind": _COMMAND_KIND,
                            "key": idempotency_key,
                            "fingerprint": request_fingerprint,
                            "response": json.dumps(response, sort_keys=True),
                        },
                    )
                return response
            finally:
                if lock_connection.in_transaction():
                    lock_connection.rollback()
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                    {"key": lock_key},
                )


def _replay(
    connection: Any,
    *,
    actor_key: str,
    idempotency_key: uuid.UUID,
    request_fingerprint: str,
) -> dict[str, Any] | None:
    row = (
        connection.execute(
            text(
                """
                SELECT request_fingerprint,response_document
                  FROM workspace.v022_command_result
                 WHERE actor_key=:actor AND command_kind=:kind AND idempotency_key=:key
                """
            ),
            {"actor": actor_key, "kind": _COMMAND_KIND, "key": idempotency_key},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    if row["request_fingerprint"] != request_fingerprint:
        raise ValueError("Product Enrollment idempotency key has different semantics")
    return cast(dict[str, Any], row["response_document"])


def _require_product_data_disclosure(
    connection: Any,
    *,
    execution_version_id: uuid.UUID,
    qualification_version_id: uuid.UUID,
) -> None:
    eligibility = connection.execute(
        text(
            """
            SELECT disclosure.product_eligibility
              FROM product.v022_product_data_disclosure disclosure
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=disclosure.artifact_id
               AND artifact.status='published'
             WHERE disclosure.execution_version_id=:execution
               AND disclosure.qualification_version_id=:qualification
            """
        ),
        {"execution": execution_version_id, "qualification": qualification_version_id},
    ).scalar_one_or_none()
    if eligibility not in {"eligible", "eligible_with_warnings"}:
        raise ValueError(
            "Product Enrollment requires a published eligible Product Data Disclosure"
        )
