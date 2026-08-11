from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from style_rotation.model.calculator import (
    ModelCalculationError,
    ModelComponentInput,
    ModelDimensionInput,
    ModelSpecificationInput,
    SignalScoreInput,
    calculate_model,
)


def _component(signal_key: str, weight: str = "1") -> ModelComponentInput:
    return ModelComponentInput(
        uuid.uuid5(uuid.NAMESPACE_URL, f"component:{signal_key}"),
        uuid.uuid5(uuid.NAMESPACE_URL, f"signal:{signal_key}"),
        signal_key,
        "identity",
        Decimal(weight),
    )


def _dimension(
    key: str,
    components: tuple[ModelComponentInput, ...],
    *,
    weight: str = "1",
    transform: str = "identity",
) -> ModelDimensionInput:
    return ModelDimensionInput(
        uuid.uuid5(uuid.NAMESPACE_URL, f"dimension:{key}"),
        key,
        "weighted_mean",
        transform,
        Decimal(weight),
        components,
    )


def _specification(
    dimensions: tuple[ModelDimensionInput, ...],
    *,
    method: str = "weighted_mean",
) -> ModelSpecificationInput:
    vote = method != "weighted_mean"
    return ModelSpecificationInput(
        uuid.uuid4(),
        uuid.uuid4(),
        f"test_{method}",
        method,
        "neutral" if vote else "not_applicable",
        "directional_score" if vote else "continuous_score",
        dimensions,
    )


def _points(
    component: ModelComponentInput, values: list[tuple[date, str]]
) -> tuple[SignalScoreInput, ...]:
    asset = uuid.uuid5(uuid.NAMESPACE_URL, "asset:iwf")
    second_asset = uuid.uuid5(uuid.NAMESPACE_URL, "asset:iwd")
    return tuple(
        SignalScoreInput(
            component.signal_version_id,
            asset if index % 2 == 0 else second_asset,
            "iwf" if index % 2 == 0 else "iwd",
            day,
            Decimal(value),
        )
        for index, (day, value) in enumerate(values)
    )


def test_two_level_weighted_model_uses_common_warmup_and_exact_decimal_score() -> None:
    first, second = _component("first", "0.25"), _component("second", "0.75")
    third = _component("third")
    specification = _specification(
        (
            _dimension("momentum", (first, second), weight="0.4"),
            _dimension("risk", (third,), weight="0.6"),
        )
    )
    day1, day2 = date(2025, 1, 2), date(2025, 1, 3)
    inputs = {
        first.signal_version_id: _points(
            first, [(day1, "1"), (day1, "-1"), (day2, "0.5"), (day2, "-0.5")]
        ),
        second.signal_version_id: _points(
            second, [(day1, "0"), (day1, "0"), (day2, "1"), (day2, "-1")]
        ),
        third.signal_version_id: _points(third, [(day2, "-0.5"), (day2, "0.5")]),
    }
    result = calculate_model(specification, inputs)
    assert result.coverage_start == result.coverage_end == day2
    assert [
        (item.asset_key, item.score, item.direction, item.confidence) for item in result.points
    ] == [
        ("iwd", Decimal("-0.050000000000000000"), "negative", Decimal("0.050000000000000000")),
        ("iwf", Decimal("0.050000000000000000"), "positive", Decimal("0.050000000000000000")),
    ]


def test_majority_vote_counts_dimension_directions_and_preserves_neutral_tie() -> None:
    components = tuple(_component(f"signal-{index}") for index in range(4))
    dimensions = tuple(
        _dimension(
            f"dimension-{index}",
            (component,),
            weight="0.25",
            transform="sign",
        )
        for index, component in enumerate(components)
    )
    specification = _specification(dimensions, method="majority_vote")
    day = date(2025, 1, 2)
    values = ("1", "1", "-1", "-1")
    inputs = {
        component.signal_version_id: _points(component, [(day, value), (day, value)])
        for component, value in zip(components, values, strict=True)
    }
    result = calculate_model(specification, inputs)
    assert {item.score for item in result.points} == {Decimal("0E-18")}
    assert {item.direction for item in result.points} == {"neutral"}
    assert {item.confidence for item in result.points} == {Decimal("0E-18")}


def test_weighted_vote_exposes_directional_margin_as_confidence() -> None:
    positive, negative = _component("positive"), _component("negative")
    specification = _specification(
        (
            _dimension("trend", (positive,), weight="0.7", transform="sign"),
            _dimension("risk", (negative,), weight="0.3", transform="sign"),
        ),
        method="weighted_vote",
    )
    day = date(2025, 1, 2)
    result = calculate_model(
        specification,
        {
            positive.signal_version_id: _points(positive, [(day, "0.01"), (day, "0.01")]),
            negative.signal_version_id: _points(negative, [(day, "-1"), (day, "-1")]),
        },
    )
    assert {item.score for item in result.points} == {Decimal("0.400000000000000000")}
    assert {item.confidence for item in result.points} == {Decimal("0.400000000000000000")}
    assert {item.direction for item in result.points} == {"positive"}


def test_model_rejects_missing_after_common_warmup_or_dynamic_reweighting() -> None:
    first, second = _component("first", "0.5"), _component("second", "0.5")
    specification = _specification((_dimension("dimension", (first, second)),))
    day1, day2 = date(2025, 1, 2), date(2025, 1, 3)
    first_points = _points(first, [(day1, "1"), (day1, "-1"), (day2, "1"), (day2, "-1")])
    second_points = _points(second, [(day1, "1"), (day1, "-1"), (day2, "1")])
    with pytest.raises(ModelCalculationError, match="missing observations"):
        calculate_model(
            specification,
            {first.signal_version_id: first_points, second.signal_version_id: second_points},
        )
    invalid = replace(
        specification,
        dimensions=(
            replace(
                specification.dimensions[0],
                components=(first, replace(second, weight=Decimal("0.4"))),
            ),
        ),
    )
    with pytest.raises(ModelCalculationError, match="weights must sum to one"):
        calculate_model(
            invalid,
            {first.signal_version_id: first_points, second.signal_version_id: first_points},
        )
