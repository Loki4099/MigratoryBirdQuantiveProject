from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

SCORE_QUANTUM = Decimal("0.000000000000000001")


@dataclass(frozen=True, slots=True)
class ModelComponentInput:
    model_component_id: uuid.UUID
    signal_version_id: uuid.UUID
    signal_key: str
    input_transform: str
    weight: Decimal


@dataclass(frozen=True, slots=True)
class ModelDimensionInput:
    model_dimension_id: uuid.UUID
    dimension_key: str
    method: str
    input_transform: str
    weight: Decimal
    components: tuple[ModelComponentInput, ...]


@dataclass(frozen=True, slots=True)
class ModelSpecificationInput:
    model_specification_id: uuid.UUID
    artifact_id: uuid.UUID
    specification_key: str
    method: str
    tie_output: str
    output_type: str
    dimensions: tuple[ModelDimensionInput, ...]


@dataclass(frozen=True, slots=True)
class SignalScoreInput:
    signal_version_id: uuid.UUID
    asset_id: uuid.UUID
    asset_key: str
    observation_date: date
    score: Decimal


@dataclass(frozen=True, slots=True)
class ModelPoint:
    asset_id: uuid.UUID
    asset_key: str
    observation_date: date
    score: Decimal
    direction: str
    confidence: Decimal


@dataclass(frozen=True, slots=True)
class ModelCalculation:
    specification: ModelSpecificationInput
    coverage_start: date
    coverage_end: date
    points: tuple[ModelPoint, ...]


class ModelCalculationError(RuntimeError):
    """Raised when a formal Model cannot be calculated without ambiguity."""


def calculate_model(
    specification: ModelSpecificationInput,
    signal_points: Mapping[uuid.UUID, tuple[SignalScoreInput, ...]],
) -> ModelCalculation:
    components = _validate_specification(specification)
    expected_versions = {item.signal_version_id for item in components}
    if set(signal_points) != expected_versions:
        raise ModelCalculationError(
            f"Model {specification.specification_key} requires its exact Signal inputs"
        )
    indexed = {
        version_id: _index_signal(version_id, signal_points[version_id])
        for version_id in expected_versions
    }
    assets = {frozenset(point.asset_id for point in points.values()) for points in indexed.values()}
    if len(assets) != 1 or not assets or len(next(iter(assets))) < 2:
        raise ModelCalculationError("Model Signal inputs must share at least two assets")
    starts = [
        min(point.observation_date for point in points.values()) for points in indexed.values()
    ]
    ends = [max(point.observation_date for point in points.values()) for points in indexed.values()]
    coverage_start, coverage_end = max(starts), min(ends)
    if coverage_start > coverage_end:
        raise ModelCalculationError("Model Signal inputs have no common date range")
    identities: set[tuple[uuid.UUID, date]] | None = None
    for points in indexed.values():
        current = {identity for identity in points if coverage_start <= identity[1] <= coverage_end}
        if identities is None:
            identities = current
        elif current != identities:
            raise ModelCalculationError(
                "Model Signal inputs contain missing observations after common warmup"
            )
    if not identities:
        raise ModelCalculationError("Model Signal inputs contain no common observations")

    output: list[ModelPoint] = []
    for asset_id, observation_date in sorted(identities, key=lambda item: (item[1], str(item[0]))):
        asset_keys = {
            indexed[item.signal_version_id][(asset_id, observation_date)].asset_key
            for item in components
        }
        if len(asset_keys) != 1:
            raise ModelCalculationError("Model Signal inputs have unstable asset identities")
        dimension_values: list[tuple[Decimal, Decimal]] = []
        for dimension in specification.dimensions:
            component_values = [
                (
                    _transform(
                        indexed[component.signal_version_id][(asset_id, observation_date)].score,
                        component.input_transform,
                    ),
                    component.weight,
                )
                for component in dimension.components
            ]
            dimension_score = _aggregate(dimension.method, component_values)
            dimension_values.append(
                (_transform(dimension_score, dimension.input_transform), dimension.weight)
            )
        score = _aggregate(specification.method, dimension_values)
        output.append(
            ModelPoint(
                asset_id,
                next(iter(asset_keys)),
                observation_date,
                score,
                _direction(score),
                abs(score).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_EVEN),
            )
        )
    output.sort(key=lambda item: (item.asset_key, item.observation_date, str(item.asset_id)))
    return ModelCalculation(specification, coverage_start, coverage_end, tuple(output))


def _validate_specification(
    specification: ModelSpecificationInput,
) -> tuple[ModelComponentInput, ...]:
    if not specification.dimensions:
        raise ModelCalculationError("Model requires at least one active dimension")
    _normalized([item.weight for item in specification.dimensions], "dimension")
    components = tuple(
        component for dimension in specification.dimensions for component in dimension.components
    )
    if not components:
        raise ModelCalculationError("Model requires at least one Signal component")
    if len({item.signal_version_id for item in components}) != len(components):
        raise ModelCalculationError("A Signal can appear only once in a Model specification")
    for dimension in specification.dimensions:
        if dimension.method != "weighted_mean":
            raise ModelCalculationError("v1 dimensions require weighted_mean aggregation")
        _normalized([item.weight for item in dimension.components], dimension.dimension_key)
    if specification.method not in {"weighted_mean", "majority_vote", "weighted_vote"}:
        raise ModelCalculationError(f"Unsupported Model method: {specification.method}")
    if specification.method == "weighted_mean":
        if (
            specification.output_type != "continuous_score"
            or specification.tie_output != "not_applicable"
        ):
            raise ModelCalculationError("Continuous Model output policy is inconsistent")
    elif specification.output_type != "directional_score" or specification.tie_output != "neutral":
        raise ModelCalculationError("Vote Model output policy is inconsistent")
    return components


def _index_signal(
    version_id: uuid.UUID, points: tuple[SignalScoreInput, ...]
) -> dict[tuple[uuid.UUID, date], SignalScoreInput]:
    if not points:
        raise ModelCalculationError(f"Signal version {version_id} contains no points")
    indexed = {(item.asset_id, item.observation_date): item for item in points}
    if len(indexed) != len(points):
        raise ModelCalculationError(f"Signal version {version_id} contains duplicate asset dates")
    if any(item.signal_version_id != version_id for item in points):
        raise ModelCalculationError("Signal point is mapped to the wrong Signal version")
    if any(not item.score.is_finite() for item in points):
        raise ModelCalculationError("Model received a non-finite Signal score")
    return indexed


def _aggregate(method: str, values: list[tuple[Decimal, Decimal]]) -> Decimal:
    if method == "weighted_mean":
        result = sum((value * weight for value, weight in values), start=Decimal(0))
    elif method in {"majority_vote", "weighted_vote"}:
        result = sum((_sign(value) * weight for value, weight in values), start=Decimal(0))
    else:
        raise ModelCalculationError(f"Unsupported aggregation method: {method}")
    return result.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_EVEN)


def _transform(value: Decimal, transform: str) -> Decimal:
    if transform == "identity":
        return value
    if transform in {"sign", "threshold_state"}:
        return _sign(value)
    raise ModelCalculationError(f"Unsupported Model input transform: {transform}")


def _sign(value: Decimal) -> Decimal:
    if value > 0:
        return Decimal(1)
    if value < 0:
        return Decimal(-1)
    return Decimal(0)


def _direction(score: Decimal) -> str:
    if score > 0:
        return "positive"
    if score < 0:
        return "negative"
    return "neutral"


def _normalized(weights: list[Decimal], label: str) -> None:
    if not weights or any(not item.is_finite() or item <= 0 for item in weights):
        raise ModelCalculationError(f"{label} weights must be positive and finite")
    if abs(sum(weights, start=Decimal(0)) - Decimal(1)) > Decimal("0.000000000001"):
        raise ModelCalculationError(f"{label} weights must sum to one")
