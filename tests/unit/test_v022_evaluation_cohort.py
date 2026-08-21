from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.engine import Connection

from style_rotation.v022.evaluation_cohort import (
    CohortSession,
    EvaluationCohortSpec,
    _bars,
    _decision_sessions,
    _derive_eligibility,
    _FrozenInputs,
    _require_bound_price_semantics,
    _unavailable,
)

PRICE_SEMANTICS = (
    "historical_constituent_pit__frozen_reconciled_retrospective_"
    "split_normalized_total_return_prices"
)


def _spec(*, frequency: str = "weekly") -> EvaluationCohortSpec:
    return EvaluationCohortSpec(
        cohort_key=f"sp500_{frequency}_v1",
        version_number=1,
        research_tier="rankable_research",
        frequency=frequency,  # type: ignore[arg-type]
        universe_history_id=uuid.uuid4(),
        dataset_publication_id=uuid.uuid4(),
        benchmark_dataset_publication_id=uuid.uuid4(),
        security_market_quality_report_id=uuid.uuid4(),
        calendar_version_id=uuid.uuid4(),
        warmup_start=date(2020, 1, 1),
        evaluation_start=date(2021, 5, 19),
        evaluation_end=date(2021, 5, 20),
        cost_bps_per_side=Decimal("5"),
        created_by="test",
    )


def test_cohort_policy_rejects_dynamic_or_short_warmup_contracts() -> None:
    with pytest.raises(ValueError, match="exactly 504"):
        replace(_spec(), required_history_sessions=253)


def test_weekly_and_monthly_decisions_exclude_unexecutable_terminal_period() -> None:
    sessions = (
        date(2024, 1, 29),
        date(2024, 1, 30),
        date(2024, 1, 31),
        date(2024, 2, 1),
        date(2024, 2, 2),
        date(2024, 2, 5),
    )

    assert _decision_sessions(sessions, "weekly") == frozenset({date(2024, 2, 2)})
    assert _decision_sessions(sessions, "monthly") == frozenset({date(2024, 1, 31)})


def test_eligibility_waits_for_504_observations_and_uniformly_excludes_provider_gap() -> None:
    start = date(2020, 1, 1)
    dates = tuple(start + timedelta(days=offset) for offset in range(505))
    available = uuid.uuid4()
    unavailable = uuid.uuid4()
    evidence = tuple(uuid.uuid4() for _ in range(5))
    sessions = tuple(
        CohortSession(
            item,
            "warmup" if ordinal < 504 else "evaluation",
            ordinal == 504,
        )
        for ordinal, item in enumerate(dates)
    )
    spec = EvaluationCohortSpec(
        cohort_key="sp500_weekly_v1",
        version_number=1,
        research_tier="rankable_research",
        frequency="weekly",
        universe_history_id=uuid.uuid4(),
        dataset_publication_id=uuid.uuid4(),
        benchmark_dataset_publication_id=uuid.uuid4(),
        security_market_quality_report_id=uuid.uuid4(),
        calendar_version_id=uuid.uuid4(),
        warmup_start=dates[0],
        evaluation_start=dates[504],
        evaluation_end=dates[504],
        cost_bps_per_side=Decimal("5"),
        created_by="test",
    )
    members = {item: frozenset({available, unavailable}) for item in dates}
    inputs = _FrozenInputs(
        evidence[0],
        evidence[1],
        evidence[2],
        evidence[3],
        evidence[4],
        sessions,
        (available, unavailable),
        members,
        {available: frozenset(dates)},
        frozenset({unavailable}),
        {},
        PRICE_SEMANTICS,
    )

    intervals = _derive_eligibility(spec, inputs)
    available_intervals = [item for item in intervals if item.security_id == available]
    unavailable_intervals = [item for item in intervals if item.security_id == unavailable]

    assert available_intervals[-1].effective_start == dates[504]
    assert available_intervals[-1].is_selectable is True
    assert available_intervals[-1].is_warmup_ready is True
    assert len(unavailable_intervals) == 1
    assert unavailable_intervals[0].valuation_state == "unavailable"
    assert unavailable_intervals[0].is_selectable is False
    assert "provider_unavailable_uniform_exclusion" in unavailable_intervals[0].reason_codes


def test_index_removal_stops_selection_but_preserves_close_only_trading() -> None:
    start = date(2020, 1, 1)
    dates = tuple(start + timedelta(days=offset) for offset in range(506))
    security_id = uuid.uuid4()
    evidence = tuple(uuid.uuid4() for _ in range(5))
    sessions = tuple(
        CohortSession(
            item,
            "warmup" if ordinal < 504 else "evaluation",
            ordinal >= 504,
        )
        for ordinal, item in enumerate(dates)
    )
    spec = EvaluationCohortSpec(
        cohort_key="sp500_weekly_v1",
        version_number=1,
        research_tier="rankable_research",
        frequency="weekly",
        universe_history_id=uuid.uuid4(),
        dataset_publication_id=uuid.uuid4(),
        benchmark_dataset_publication_id=uuid.uuid4(),
        security_market_quality_report_id=uuid.uuid4(),
        calendar_version_id=uuid.uuid4(),
        warmup_start=dates[0],
        evaluation_start=dates[504],
        evaluation_end=dates[505],
        cost_bps_per_side=Decimal("5"),
        created_by="test",
    )
    members = {
        item: frozenset({security_id}) if item <= dates[504] else frozenset()
        for item in dates
    }
    inputs = _FrozenInputs(
        evidence[0],
        evidence[1],
        evidence[2],
        evidence[3],
        evidence[4],
        sessions,
        (security_id,),
        members,
        {security_id: frozenset(dates)},
        frozenset(),
        {},
        PRICE_SEMANTICS,
    )

    intervals = _derive_eligibility(spec, inputs)
    removed = intervals[-1]

    assert removed.effective_start == dates[505]
    assert removed.is_member is False
    assert removed.is_selectable is False
    assert removed.is_tradable is True
    assert removed.valuation_state == "live"
    assert "not_sp500_member" in removed.reason_codes


def test_missing_usable_bar_resets_consecutive_warmup_readiness() -> None:
    start = date(2020, 1, 1)
    dates = tuple(start + timedelta(days=offset) for offset in range(506))
    security_id = uuid.uuid4()
    evidence = tuple(uuid.uuid4() for _ in range(5))
    sessions = tuple(
        CohortSession(
            item,
            "warmup" if ordinal < 504 else "evaluation",
            ordinal >= 504,
        )
        for ordinal, item in enumerate(dates)
    )
    spec = EvaluationCohortSpec(
        cohort_key="sp500_weekly_v1",
        version_number=1,
        research_tier="rankable_research",
        frequency="weekly",
        universe_history_id=uuid.uuid4(),
        dataset_publication_id=uuid.uuid4(),
        benchmark_dataset_publication_id=uuid.uuid4(),
        security_market_quality_report_id=uuid.uuid4(),
        calendar_version_id=uuid.uuid4(),
        warmup_start=dates[0],
        evaluation_start=dates[504],
        evaluation_end=dates[505],
        cost_bps_per_side=Decimal("5"),
        created_by="test",
    )
    members = {item: frozenset({security_id}) for item in dates}
    usable_dates = frozenset(item for item in dates if item != dates[504])
    inputs = _FrozenInputs(
        evidence[0],
        evidence[1],
        evidence[2],
        evidence[3],
        evidence[4],
        sessions,
        (security_id,),
        members,
        {security_id: usable_dates},
        frozenset(),
        {},
        PRICE_SEMANTICS,
    )

    intervals = _derive_eligibility(spec, inputs)
    by_start = {item.effective_start: item for item in intervals}

    assert by_start[dates[504]].is_warmup_ready is False
    assert by_start[dates[504]].is_tradable is False
    assert by_start[dates[504]].valuation_state == "unavailable"
    assert by_start[dates[505]].is_warmup_ready is False
    assert by_start[dates[505]].is_tradable is True
    assert "warmup_504_incomplete" in by_start[dates[505]].reason_codes


def test_bound_price_semantics_are_exact_and_fail_closed() -> None:
    assert _require_bound_price_semantics(PRICE_SEMANTICS) == PRICE_SEMANTICS
    for value in (None, "", " price_semantics", "price_semantics "):
        with pytest.raises(ValueError, match="price semantics are not exact"):
            _require_bound_price_semantics(value)


def test_bar_admission_filters_zero_volume_at_the_dataset_boundary() -> None:
    security_id = uuid.uuid4()

    class _Rows:
        def all(self) -> list[tuple[uuid.UUID, date]]:
            return [(security_id, date(2024, 1, 2))]

    class _Connection:
        statement = ""

        def execute(self, statement: object, parameters: object) -> _Rows:
            self.statement = str(statement)
            return _Rows()

    fake = _Connection()

    assert _bars(
        cast(Connection, fake), uuid.uuid4(), (security_id,)
    ) == {security_id: frozenset({date(2024, 1, 2)})}
    assert "bar.volume_raw>0" in fake.statement
    assert "bar.adjustment_factor" in fake.statement


def test_uniform_exclusion_is_read_only_from_explicit_quality_code() -> None:
    security_id = uuid.uuid4()
    document = {
        "issues": [
            {
                "severity": "warning",
                "rule_code": "security_uniformly_excluded_provider_unavailable",
                "subject_key": str(security_id),
            },
            {
                "severity": "warning",
                "rule_code": "some_other_warning",
                "subject_key": str(uuid.uuid4()),
            },
        ]
    }

    assert _unavailable(document) == frozenset({security_id})
