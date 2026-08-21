from __future__ import annotations

import uuid
from datetime import UTC, datetime

from style_rotation.v022.shadow_comparator import ComparisonCoordinationResult
from style_rotation.v022.shadow_dual_run import (
    ShadowV021ReferenceOutcome,
    ShadowV022DecisionOutcome,
)
from style_rotation.v022.shadow_runtime_worker import ShadowRuntimeCycle


class _V021Worker:
    def __init__(self, status: str) -> None:
        self.status = status
        self.observed_at: datetime | None = None

    def run_once(self, *, observed_at: datetime) -> ShadowV021ReferenceOutcome:
        self.observed_at = observed_at
        return ShadowV021ReferenceOutcome(
            self.status,  # type: ignore[arg-type]
            uuid.uuid4() if self.status == "completed" else None,
            uuid.uuid4() if self.status == "completed" else None,
        )


class _V022Worker:
    def __init__(self, status: str) -> None:
        self.status = status
        self.observed_at: datetime | None = None

    def run_once(self, *, observed_at: datetime) -> ShadowV022DecisionOutcome:
        self.observed_at = observed_at
        return ShadowV022DecisionOutcome(
            self.status,  # type: ignore[arg-type]
            uuid.uuid4() if self.status == "completed" else None,
            uuid.uuid4() if self.status == "completed" else None,
        )


class _Comparisons:
    def __init__(self, published: int) -> None:
        self.published = published
        self.known_at: datetime | None = None

    def publish_ready(self, *, known_at: datetime) -> ComparisonCoordinationResult:
        self.known_at = known_at
        return ComparisonCoordinationResult(self.published, self.published, 0)


def test_shadow_cycle_advances_both_legs_before_comparison() -> None:
    observed = datetime(2026, 8, 18, 1, tzinfo=UTC)
    v021 = _V021Worker("completed")
    v022 = _V022Worker("completed")
    comparisons = _Comparisons(1)
    cycle = ShadowRuntimeCycle(
        v021_worker=v021,  # type: ignore[arg-type]
        v022_worker=v022,  # type: ignore[arg-type]
        comparisons=comparisons,  # type: ignore[arg-type]
    )

    outcome = cycle.run_once(observed_at=observed)

    assert outcome.status == "progressed"
    assert outcome.published_comparison_count == 1
    assert v021.observed_at == observed
    assert v022.observed_at == observed
    assert comparisons.known_at == observed


def test_shadow_cycle_is_idle_when_neither_leg_or_comparator_progresses() -> None:
    cycle = ShadowRuntimeCycle(
        v021_worker=_V021Worker("idle"),  # type: ignore[arg-type]
        v022_worker=_V022Worker("idle"),  # type: ignore[arg-type]
        comparisons=_Comparisons(0),  # type: ignore[arg-type]
    )

    outcome = cycle.run_once(observed_at=datetime(2026, 8, 18, 1, tzinfo=UTC))

    assert outcome.status == "idle"
    assert outcome.ready_comparison_count == 0
