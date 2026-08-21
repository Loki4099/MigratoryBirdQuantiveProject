from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from style_rotation.v022.shadow_comparator import (
    ComparisonCoordinationResult,
    ShadowComparisonCoordinator,
)
from style_rotation.v022.shadow_dual_run import (
    ShadowV021ReferenceOutcome,
    ShadowV021ReferenceWorker,
    ShadowV022DecisionOutcome,
    ShadowV022DecisionWorker,
)


@dataclass(frozen=True, slots=True)
class ShadowRuntimeCycleOutcome:
    status: Literal["idle", "progressed"]
    v021_status: Literal["idle", "completed"]
    v022_status: Literal["idle", "completed"]
    ready_comparison_count: int
    published_comparison_count: int
    skipped_comparison_count: int


class ShadowRuntimeCycle:
    """Advance both frozen Shadow legs and publish newly complete comparisons."""

    def __init__(
        self,
        *,
        v021_worker: ShadowV021ReferenceWorker,
        v022_worker: ShadowV022DecisionWorker,
        comparisons: ShadowComparisonCoordinator,
    ) -> None:
        self._v021_worker = v021_worker
        self._v022_worker = v022_worker
        self._comparisons = comparisons

    def run_once(self, *, observed_at: datetime | None = None) -> ShadowRuntimeCycleOutcome:
        known_at = observed_at or datetime.now(UTC)
        if known_at.tzinfo is None:
            raise ValueError("Shadow runtime cycle timestamp must be timezone-aware")
        v021 = self._v021_worker.run_once(observed_at=known_at)
        v022 = self._v022_worker.run_once(observed_at=known_at)
        comparisons = self._comparisons.publish_ready(known_at=known_at)
        progressed = (
            v021.status == "completed"
            or v022.status == "completed"
            or comparisons.published_comparison_count > 0
        )
        return _cycle_outcome(v021, v022, comparisons, progressed=progressed)


def _cycle_outcome(
    v021: ShadowV021ReferenceOutcome,
    v022: ShadowV022DecisionOutcome,
    comparisons: ComparisonCoordinationResult,
    *,
    progressed: bool,
) -> ShadowRuntimeCycleOutcome:
    return ShadowRuntimeCycleOutcome(
        "progressed" if progressed else "idle",
        v021.status,
        v022.status,
        comparisons.ready_pair_count,
        comparisons.published_comparison_count,
        comparisons.skipped_existing_count,
    )
