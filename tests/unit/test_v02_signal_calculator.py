from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from style_rotation.signal.calculator import (
    FactorValueInput,
    SignalCalculation,
    SignalCalculationError,
    SignalVersionInput,
    calculate_signal,
)

ASSETS = tuple((uuid.UUID(int=index + 1), f"asset_{index + 1}") for index in range(4))
DAYS = (date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6))


def _version(
    *,
    direction: str = "higher_is_better",
    output_type: str = "continuous",
    rule: dict[str, object] | None = None,
) -> SignalVersionInput:
    discrete = output_type != "continuous"
    return SignalVersionInput(
        uuid.UUID(int=100),
        uuid.UUID(int=101),
        "test_signal__factor_variant",
        uuid.UUID(int=102),
        direction,
        "none" if discrete else "cross_sectional_centered_rank_-1_1",
        "none",
        "error_after_common_warmup",
        "not_applicable" if discrete else "average_rank",
        output_type,
        rule,
    )


def _points(values_by_day: tuple[tuple[float, ...], ...]) -> tuple[FactorValueInput, ...]:
    return tuple(
        FactorValueInput(asset_id, asset_key, DAYS[day_index], value)
        for day_index, values in enumerate(values_by_day)
        for (asset_id, asset_key), value in zip(ASSETS, values, strict=True)
    )


def _scores(calculation: SignalCalculation, day: date) -> dict[str, Decimal]:
    return {
        point.asset_key: point.score
        for point in calculation.points
        if point.observation_date == day
    }


def test_centered_rank_uses_average_ties_and_direction_after_ranking() -> None:
    inputs = _points(((1.0, 2.0, 3.0, 4.0), (1.0, 2.0, 2.0, 4.0)))
    higher = calculate_signal(_version(), inputs)
    lower = calculate_signal(_version(direction="lower_is_better"), inputs)
    assert _scores(higher, DAYS[0]) == {
        "asset_1": Decimal("-1.000000000000000000"),
        "asset_2": Decimal("-0.333333333333333333"),
        "asset_3": Decimal("0.333333333333333333"),
        "asset_4": Decimal("1.000000000000000000"),
    }
    assert _scores(higher, DAYS[1]) == {
        "asset_1": Decimal("-1.000000000000000000"),
        "asset_2": Decimal("0E-18"),
        "asset_3": Decimal("0E-18"),
        "asset_4": Decimal("1.000000000000000000"),
    }
    assert all(
        high.score == -low.score for high, low in zip(higher.points, lower.points, strict=True)
    )


def test_threshold_state_preserves_declared_boundary_and_score_meaning() -> None:
    inputs = _points(((29.0, 30.0, 31.0, 70.0),))
    calculation = calculate_signal(
        _version(
            direction="lower_is_better",
            output_type="threshold_state",
            rule={"operator": "<=", "threshold": 30, "true_score": 1, "false_score": 0},
        ),
        inputs,
    )
    assert _scores(calculation, DAYS[0]) == {
        "asset_1": Decimal("1.000000000000000000"),
        "asset_2": Decimal("1.000000000000000000"),
        "asset_3": Decimal("0E-18"),
        "asset_4": Decimal("0E-18"),
    }
    assert [point.state for point in calculation.points] == [
        "positive",
        "positive",
        "neutral",
        "neutral",
    ]


def test_crossover_excludes_first_date_and_distinguishes_event_from_neutral() -> None:
    inputs = _points(((-1.0, 1.0, 0.0, -1.0), (1.0, 2.0, 1.0, -2.0), (2.0, -1.0, 2.0, 1.0)))
    calculation = calculate_signal(
        _version(
            output_type="crossover_event",
            rule={"previous": "<=0", "current": ">0", "event_score": 1, "otherwise": "neutral"},
        ),
        inputs,
    )
    assert calculation.coverage_start == DAYS[1]
    assert len(calculation.points) == 8
    assert _scores(calculation, DAYS[1]) == {
        "asset_1": Decimal("1.000000000000000000"),
        "asset_2": Decimal("0E-18"),
        "asset_3": Decimal("1.000000000000000000"),
        "asset_4": Decimal("0E-18"),
    }
    day_two = [item for item in calculation.points if item.observation_date == DAYS[1]]
    assert [item.event for item in day_two] == [True, False, True, False]


def test_formal_signal_rejects_missing_asset_dates_and_unsupported_policy() -> None:
    inputs = _points(((1.0, 2.0, 3.0, 4.0), (2.0, 3.0, 4.0, 5.0)))[:-1]
    with pytest.raises(SignalCalculationError, match="not aligned"):
        calculate_signal(_version(), inputs)
    invalid = replace(_version(), missing_policy="neutral")
    with pytest.raises(SignalCalculationError, match="Unsupported missing policy"):
        calculate_signal(invalid, _points(((1.0, 2.0, 3.0, 4.0),)))
