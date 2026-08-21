from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Engine, text

from style_rotation.v022.operations_slo import (
    SLODomain,
    SLOMeasurementInput,
    SLOMeasurementPublication,
    SLOMeasurementService,
)

_PROBE_VERSION = "v022_operational_ratio_probes_v1"


@dataclass(frozen=True, slots=True)
class OperationsProbeResult:
    window_start_at: datetime
    window_end_at: datetime
    measured_at: datetime
    measurements: tuple[SLOMeasurementInput, ...]


@dataclass(frozen=True, slots=True)
class OperationsProbePublication:
    window_start_at: datetime
    window_end_at: datetime
    measured_at: datetime
    measurement_ids: tuple[uuid.UUID, ...]
    artifact_ids: tuple[uuid.UUID, ...]


class OperationsSLOProbeService:
    """Observe six frozen operational domains without manufacturing empty samples."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._measurements = SLOMeasurementService(engine)

    def observe(
        self,
        *,
        window_start_at: datetime,
        window_end_at: datetime,
        measured_at: datetime | None = None,
    ) -> OperationsProbeResult:
        occurred_at = measured_at or datetime.now(UTC)
        if any(value.tzinfo is None for value in (window_start_at, window_end_at, occurred_at)):
            raise ValueError("Operations probe timestamps must be timezone-aware")
        if window_start_at >= window_end_at or occurred_at < window_end_at:
            raise ValueError("Operations probe window or measurement time is invalid")
        with self._engine.connect() as connection:
            rows = tuple(
                connection.execute(
                    text(_PROBE_SQL),
                    {
                        "window_start": window_start_at,
                        "window_end": window_end_at,
                    },
                ).mappings()
            )
        measurements = tuple(
            measurement
            for row in rows
            if (
                measurement := _ratio_measurement(
                    metric_key=row["metric_key"],
                    domain_key=row["domain_key"],
                    numerator=int(row["numerator"]),
                    denominator=int(row["denominator"]),
                    window_start_at=window_start_at,
                    window_end_at=window_end_at,
                    measured_at=occurred_at,
                )
            )
            is not None
        )
        return OperationsProbeResult(
            window_start_at, window_end_at, occurred_at, measurements
        )

    def publish_window(
        self,
        *,
        window_start_at: datetime,
        window_end_at: datetime,
        measured_at: datetime | None = None,
    ) -> OperationsProbePublication:
        result = self.observe(
            window_start_at=window_start_at,
            window_end_at=window_end_at,
            measured_at=measured_at,
        )
        published: tuple[SLOMeasurementPublication, ...] = tuple(
            self._measurements.publish(item) for item in result.measurements
        )
        return OperationsProbePublication(
            result.window_start_at,
            result.window_end_at,
            result.measured_at,
            tuple(item.slo_measurement_id for item in published),
            tuple(item.artifact_id for item in published),
        )


def _ratio_measurement(
    *,
    metric_key: str,
    domain_key: SLODomain,
    numerator: int,
    denominator: int,
    window_start_at: datetime,
    window_end_at: datetime,
    measured_at: datetime,
) -> SLOMeasurementInput | None:
    if numerator < 0 or denominator < 0 or numerator > denominator:
        raise ValueError("Operations probe counts violate ratio invariants")
    if denominator == 0:
        return None
    return SLOMeasurementInput(
        metric_key,
        domain_key,
        Decimal(numerator) / Decimal(denominator),
        denominator,
        window_start_at,
        window_end_at,
        measured_at,
        {
            "probe_version": _PROBE_VERSION,
            "metric_key": metric_key,
            "numerator": numerator,
            "denominator": denominator,
            "window_semantics": "created_or_due_at_gte_start_and_lt_end",
        },
    )


_PROBE_SQL = """
WITH compile_counts AS (
  SELECT count(*) FILTER (WHERE status='succeeded') AS numerator,
         count(*) FILTER (WHERE status IN ('succeeded','failed')) AS denominator
    FROM workspace.v022_compile_attempt
   WHERE created_at>=:window_start AND created_at<:window_end
), queue_samples AS (
  SELECT status FROM workspace.v022_graph_work_item
   WHERE created_at>=:window_start AND created_at<:window_end
  UNION ALL
  SELECT status FROM ops.v022_shadow_work_item
   WHERE created_at>=:window_start AND created_at<:window_end
), queue_counts AS (
  SELECT count(*) FILTER (WHERE status IN (
           'completed','reused','failed','cancelled','blocked_upstream_failed',
           'blocked_upstream_cancelled')) AS numerator,count(*) AS denominator
    FROM queue_samples
), cache_counts AS (
  SELECT count(*) FILTER (WHERE status='reused') AS numerator,count(*) AS denominator
    FROM workspace.v022_graph_work_item
   WHERE status IN ('completed','reused')
     AND updated_at>=:window_start AND updated_at<:window_end
), storage_counts AS (
  SELECT count(*) FILTER (WHERE object_state='published'
                                  AND verification_status='verified'
                                  AND verified_at IS NOT NULL)
           AS numerator,count(*) AS denominator
    FROM data.payload_object
   WHERE created_at>=:window_start AND created_at<:window_end
), export_counts AS (
  SELECT count(*) FILTER (WHERE work.status='completed') AS numerator,count(*) AS denominator
    FROM signal.research_export_job job
    JOIN ops.work_item work ON work.work_item_id=job.work_item_id
   WHERE job.created_at>=:window_start AND job.created_at<:window_end
), product_due AS (
  SELECT enrollment.execution_version_id,session.decision_session_id,
         decision.product_decision_id
    FROM product.v022_product_enrollment enrollment
    JOIN lineage.artifact enrollment_artifact
      ON enrollment_artifact.artifact_id=enrollment.artifact_id
     AND enrollment_artifact.status='published'
    JOIN product.v022_decision_schedule_session session
      ON session.decision_schedule_version_id=enrollment.decision_schedule_version_id
    JOIN product.v022_decision_schedule_session first_session
      ON first_session.decision_session_id=enrollment.first_eligible_decision_session_id
    LEFT JOIN LATERAL (
      SELECT event.to_lifecycle
        FROM product.v022_enrollment_lifecycle_event event
       WHERE event.product_enrollment_id=enrollment.product_enrollment_id
         AND event.effective_at<=session.decision_cutoff_at
       ORDER BY event.effective_at DESC,event.sequence_number DESC LIMIT 1
    ) lifecycle ON true
    LEFT JOIN product.v022_product_decision decision
      ON decision.execution_version_id=enrollment.execution_version_id
     AND decision.decision_session_id=session.decision_session_id
     AND decision.created_at<:window_end
   WHERE session.ordinal>=first_session.ordinal
     AND session.decision_cutoff_at>=:window_start
     AND session.decision_cutoff_at<:window_end
     AND coalesce(lifecycle.to_lifecycle,'active')='active'
), product_counts AS (
  SELECT count(*) FILTER (WHERE product_decision_id IS NOT NULL) AS numerator,
         count(*) AS denominator FROM product_due
)
SELECT 'compile_success_ratio' AS metric_key,'compile' AS domain_key,
       numerator,denominator FROM compile_counts
UNION ALL SELECT 'queue_terminal_ratio','queue',numerator,denominator FROM queue_counts
UNION ALL SELECT 'cache_reuse_ratio','cache',numerator,denominator FROM cache_counts
UNION ALL SELECT 'storage_verified_ratio','storage',numerator,denominator FROM storage_counts
UNION ALL SELECT 'export_success_ratio','export',numerator,denominator FROM export_counts
UNION ALL SELECT 'product_decision_availability_ratio','product_freshness',
                 numerator,denominator FROM product_counts
ORDER BY domain_key
"""
