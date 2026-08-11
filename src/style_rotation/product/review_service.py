from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Engine, text

ReviewDecision = Literal["continue", "suspend", "retire", "replace"]


@dataclass(frozen=True, slots=True)
class ProductReviewResult:
    product_review_id: uuid.UUID
    product_enrollment_id: uuid.UUID
    decision: ReviewDecision
    reviewed_at: datetime


class ProductReviewService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(
        self,
        enrollment_id: uuid.UUID,
        *,
        decision: ReviewDecision,
        researcher_id: str,
        reason: str,
        evidence: dict[str, Any],
        reviewed_at: datetime,
    ) -> ProductReviewResult:
        if not researcher_id.strip() or not reason.strip():
            raise ValueError("Product Review requires researcher and reason")
        if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
            raise ValueError("Product Review time must be timezone-aware")
        review_id = uuid.uuid4()
        with self._engine.begin() as connection:
            exists = connection.execute(
                text(
                    "SELECT 1 FROM product.product_enrollment "
                    "WHERE product_enrollment_id = :id FOR UPDATE"
                ),
                {"id": enrollment_id},
            ).scalar_one_or_none()
            if exists is None:
                raise LookupError(f"Product Enrollment not found: {enrollment_id}")
            connection.execute(
                text(
                    """
                    INSERT INTO product.product_review (
                        product_review_id, product_enrollment_id, reviewed_at,
                        researcher_id, decision, reason, evidence
                    ) VALUES (:id, :enrollment_id, :reviewed_at, :researcher,
                              :decision, :reason, CAST(:evidence AS jsonb))
                    """
                ),
                {
                    "id": review_id,
                    "enrollment_id": enrollment_id,
                    "reviewed_at": reviewed_at,
                    "researcher": researcher_id.strip(),
                    "decision": decision,
                    "reason": reason.strip(),
                    "evidence": json.dumps(evidence, sort_keys=True, default=str),
                },
            )
        return ProductReviewResult(review_id, enrollment_id, decision, reviewed_at)
