from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from style_rotation.model.calculator import (
    ModelComponentInput,
    ModelDimensionInput,
    ModelSpecificationInput,
    SignalScoreInput,
    calculate_model,
)
from style_rotation.v022.aggregation_runtime import (
    AggregationDimension,
    WeightedAggregationInput,
    directional_weighted_vote,
    hierarchical_weighted_mean,
)
from style_rotation.v022.model_compat_runtime import (
    AggregationSignalPoint,
    LegacyModelCompatibilityRuntime,
)

REGISTRY_PATH = Path("v0.22/m5/model-migration-registry.v0.22.0.json")


def test_hierarchical_and_vote_aggregation_are_complete_case_and_quantized() -> None:
    dimensions = (
        AggregationDimension(
            "first",
            (
                WeightedAggregationInput("a", Decimal("0.4"), Decimal("0.5")),
                WeightedAggregationInput("b", Decimal("0.8"), Decimal("0.5")),
            ),
            Decimal("0.25"),
        ),
        AggregationDimension(
            "second",
            (WeightedAggregationInput("c", Decimal("-0.2"), Decimal("1")),),
            Decimal("0.75"),
        ),
    )

    assert hierarchical_weighted_mean(dimensions) == Decimal("0.000000000000000000")
    assert directional_weighted_vote(dimensions) == Decimal("-0.500000000000000000")

    missing = (
        AggregationDimension(
            "missing",
            (WeightedAggregationInput("x", None, Decimal("1")),),
            Decimal("1"),
        ),
    )
    assert hierarchical_weighted_mean(missing) is None


def test_all_86_mapped_aggregations_match_the_frozen_v021_calculator() -> None:
    document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    runtime = LegacyModelCompatibilityRuntime.from_registry_path(REGISTRY_PATH)
    inputs = _signal_points(document)
    actual = {item.legacy_key: item for item in runtime.execute_all(inputs)}

    assert len(actual) == 86
    assert len({item.execution_fingerprint for item in actual.values()}) == 86
    for record in document["records"]:
        expected = calculate_model(
            _legacy_specification(record),
            _legacy_signal_points(record, inputs),
        )
        result = actual[record["legacy_key"]]
        assert result.coverage_start == expected.coverage_start
        assert result.coverage_end == expected.coverage_end
        assert [
            (point.asset_id, point.observation_date, point.score, point.direction, point.confidence)
            for point in result.points
        ] == [
            (point.asset_id, point.observation_date, point.score, point.direction, point.confidence)
            for point in expected.points
        ]


def test_runtime_rejects_incomplete_or_unknown_signal_inputs() -> None:
    document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    runtime = LegacyModelCompatibilityRuntime.from_registry_path(REGISTRY_PATH)
    record = document["records"][0]
    inputs = _signal_points(document)
    required = {
        component["legacy_signal_key"]: inputs[component["legacy_signal_key"]]
        for dimension in record["legacy_recipe"]["dimensions"]
        for component in dimension["components"]
    }
    required.pop(next(iter(required)))

    with pytest.raises(ValueError, match="exact Signal inputs"):
        runtime.execute(record["legacy_key"], required)


def _signal_points(
    document: dict[str, Any],
) -> dict[str, tuple[AggregationSignalPoint, ...]]:
    keys = {
        component["legacy_signal_key"]
        for record in document["records"]
        for dimension in record["legacy_recipe"]["dimensions"]
        for component in dimension["components"]
    }
    output: dict[str, tuple[AggregationSignalPoint, ...]] = {}
    for signal_number, signal_key in enumerate(sorted(keys), 1):
        points = []
        for asset_number, asset_key in enumerate(("asset_a", "asset_b"), 1):
            asset_id = _id("asset", asset_key)
            for day in range(3):
                numerator = ((signal_number + asset_number + day) % 9) - 4
                points.append(
                    AggregationSignalPoint(
                        asset_id,
                        asset_key,
                        date(2026, 1, 2) + timedelta(days=day),
                        Decimal(numerator) / Decimal(4),
                    )
                )
        output[signal_key] = tuple(points)
    return output


def _legacy_specification(record: dict[str, Any]) -> ModelSpecificationInput:
    recipe = record["legacy_recipe"]
    return ModelSpecificationInput(
        _id("model", record["legacy_key"]),
        _id("artifact", record["legacy_key"]),
        record["legacy_key"],
        recipe["method"],
        recipe["tie_output"],
        recipe["output_type"],
        tuple(
            ModelDimensionInput(
                _id("dimension", f"{record['legacy_key']}:{dimension['dimension_key']}"),
                dimension["dimension_key"],
                dimension["method"],
                dimension["input_transform"],
                Decimal(dimension["weight"]),
                tuple(
                    ModelComponentInput(
                        _id(
                            "component",
                            f"{record['legacy_key']}:{component['legacy_signal_key']}",
                        ),
                        _id("signal", component["legacy_signal_key"]),
                        component["legacy_signal_key"],
                        component["input_transform"],
                        Decimal(component["weight"]),
                    )
                    for component in dimension["components"]
                ),
            )
            for dimension in recipe["dimensions"]
        ),
    )


def _legacy_signal_points(
    record: dict[str, Any],
    inputs: dict[str, tuple[AggregationSignalPoint, ...]],
) -> dict[uuid.UUID, tuple[SignalScoreInput, ...]]:
    keys = {
        component["legacy_signal_key"]
        for dimension in record["legacy_recipe"]["dimensions"]
        for component in dimension["components"]
    }
    return {
        _id("signal", key): tuple(
            SignalScoreInput(
                _id("signal", key),
                point.asset_id,
                point.asset_key,
                point.observation_date,
                point.score,
            )
            for point in inputs[key]
        )
        for key in keys
    }


def _id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"test:{kind}:{key}")
