from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

QUANTUM = Decimal("1e-18")


@dataclass(frozen=True, slots=True)
class WeightedAggregationInput:
    input_key: str
    value: Decimal | None
    weight: Decimal
    transform: str = "identity"


@dataclass(frozen=True, slots=True)
class AggregationDimension:
    dimension_key: str
    inputs: tuple[WeightedAggregationInput, ...]
    weight: Decimal
    transform: str = "identity"
    method: str = "weighted_mean"


def single_signal_identity(values: Sequence[Decimal | None]) -> Decimal | None:
    if len(values) != 1:
        raise ValueError("single_signal_identity requires exactly one input")
    return values[0]


def flat_equal_weight_mean(values: Sequence[Decimal | None]) -> Decimal | None:
    """Frozen M2 complete-case deterministic mean in explicit input order."""

    if not values:
        raise ValueError("flat_equal_weight_mean requires at least one input")
    if any(value is None for value in values):
        return None
    present = tuple(value for value in values if value is not None)
    return (sum(present, Decimal(0)) / Decimal(len(present))).quantize(
        QUANTUM, rounding=ROUND_HALF_EVEN
    )


def hierarchical_weighted_mean(
    dimensions: Sequence[AggregationDimension],
) -> Decimal | None:
    values = _dimension_values(dimensions)
    if values is None:
        return None
    return _weighted_sum(values)


def directional_weighted_vote(
    dimensions: Sequence[AggregationDimension],
) -> Decimal | None:
    values = _dimension_values(dimensions)
    if values is None:
        return None
    return _weighted_sum(tuple((_sign(value), weight) for value, weight in values))


def execute_deterministic_aggregation(
    family_key: str,
    dimensions: Sequence[AggregationDimension],
) -> Decimal | None:
    if not dimensions:
        raise ValueError("deterministic Aggregation requires at least one dimension")
    if family_key == "single_signal_identity":
        inputs = tuple(item for dimension in dimensions for item in dimension.inputs)
        if len(dimensions) != 1 or len(inputs) != 1:
            raise ValueError("single_signal_identity requires one declared input")
        return single_signal_identity((inputs[0].value,))
    if family_key == "flat_equal_weight_mean":
        return flat_equal_weight_mean(
            tuple(item.value for dimension in dimensions for item in dimension.inputs)
        )
    if family_key == "hierarchical_weighted_mean":
        return hierarchical_weighted_mean(dimensions)
    if family_key == "directional_weighted_vote":
        return directional_weighted_vote(dimensions)
    raise ValueError(f"unsupported deterministic Aggregation Family: {family_key}")


def _dimension_values(
    dimensions: Sequence[AggregationDimension],
) -> tuple[tuple[Decimal, Decimal], ...] | None:
    if not dimensions:
        raise ValueError("hierarchical Aggregation requires at least one dimension")
    _validate_weights(tuple(item.weight for item in dimensions), "dimension")
    output: list[tuple[Decimal, Decimal]] = []
    for dimension in dimensions:
        if dimension.method != "weighted_mean" or not dimension.inputs:
            raise ValueError(
                f"Aggregation dimension {dimension.dimension_key} requires weighted_mean inputs"
            )
        _validate_weights(
            tuple(item.weight for item in dimension.inputs),
            f"dimension {dimension.dimension_key} input",
        )
        if any(item.value is None for item in dimension.inputs):
            return None
        component_values = tuple(
            (_transform(item.value, item.transform), item.weight)
            for item in dimension.inputs
            if item.value is not None
        )
        dimension_value = _transform(
            _weighted_sum(component_values), dimension.transform
        )
        output.append((dimension_value, dimension.weight))
    return tuple(output)


def _weighted_sum(values: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
    return sum((value * weight for value, weight in values), Decimal()).quantize(
        QUANTUM, rounding=ROUND_HALF_EVEN
    )


def _transform(value: Decimal, transform: str) -> Decimal:
    if transform == "identity":
        return value
    if transform in {"sign", "threshold_state"}:
        return _sign(value)
    raise ValueError(f"unsupported Aggregation transform: {transform}")


def _sign(value: Decimal) -> Decimal:
    if value > 0:
        return Decimal(1)
    if value < 0:
        return Decimal(-1)
    return Decimal()


def _validate_weights(weights: Sequence[Decimal], label: str) -> None:
    if not weights or any(not value.is_finite() or value <= 0 for value in weights):
        raise ValueError(f"{label} weights must be positive and finite")
    if abs(sum(weights, Decimal()) - Decimal(1)) > Decimal("1e-16"):
        raise ValueError(f"{label} weights must sum to one")
