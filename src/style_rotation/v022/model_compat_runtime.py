from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.aggregation_runtime import (
    AggregationDimension,
    WeightedAggregationInput,
    execute_deterministic_aggregation,
)
from style_rotation.v022.model_migration import load_model_migration_registry

RUNTIME_ADAPTER_KEY = "legacy_model_aggregation_compat_v1"


@dataclass(frozen=True, slots=True)
class AggregationSignalPoint:
    asset_id: uuid.UUID
    asset_key: str
    observation_date: date
    score: Decimal


@dataclass(frozen=True, slots=True)
class AggregationPoint:
    asset_id: uuid.UUID
    asset_key: str
    observation_date: date
    score: Decimal
    direction: str
    confidence: Decimal


@dataclass(frozen=True, slots=True)
class MappedAggregationCalculation:
    legacy_key: str
    family_key: str
    parameter_preset_key: str | None
    coverage_start: date
    coverage_end: date
    points: tuple[AggregationPoint, ...]
    execution_fingerprint: str


class LegacyModelCompatibilityRuntime:
    """Execute frozen v0.21 Model recipes as v0.22 deterministic Aggregations."""

    def __init__(self, registry: Mapping[str, Any]) -> None:
        self._registry = registry
        self._records = {
            str(record["legacy_key"]): record for record in registry["records"]
        }
        self.runtime_contract_fingerprint = sha256_hexdigest(
            {
                "adapter_key": RUNTIME_ADAPTER_KEY,
                "registry_fingerprint": registry["registry_fingerprint"],
            }
        )

    @classmethod
    def from_registry_path(cls, path: Path) -> LegacyModelCompatibilityRuntime:
        return cls(load_model_migration_registry(path))

    def execute(
        self,
        legacy_key: str,
        signal_points: Mapping[str, tuple[AggregationSignalPoint, ...]],
    ) -> MappedAggregationCalculation:
        try:
            record = self._records[legacy_key]
        except KeyError as error:
            raise KeyError(f"Unknown legacy Model migration: {legacy_key}") from error
        recipe = record["legacy_recipe"]
        required = tuple(
            component["legacy_signal_key"]
            for dimension in recipe["dimensions"]
            for component in dimension["components"]
        )
        if set(signal_points) != set(required):
            raise ValueError(f"Aggregation {legacy_key} requires its exact Signal inputs")
        indexed = {
            signal_key: _index_signal(signal_key, signal_points[signal_key])
            for signal_key in required
        }
        identities, coverage_start, coverage_end = _common_identities(indexed)
        output: list[AggregationPoint] = []
        for asset_id, observation_date in sorted(
            identities, key=lambda item: (item[1], str(item[0]))
        ):
            source_points = {
                signal_key: indexed[signal_key][(asset_id, observation_date)]
                for signal_key in required
            }
            asset_keys = {point.asset_key for point in source_points.values()}
            if len(asset_keys) != 1:
                raise ValueError("Aggregation Signal inputs have unstable asset identities")
            dimensions = _dimensions(recipe, source_points)
            score = execute_deterministic_aggregation(
                str(record["mapping"]["family_key"]), dimensions
            )
            if score is None:
                raise ValueError("complete legacy Aggregation produced a missing score")
            output.append(
                AggregationPoint(
                    asset_id=asset_id,
                    asset_key=next(iter(asset_keys)),
                    observation_date=observation_date,
                    score=score,
                    direction=_direction(score),
                    confidence=abs(score),
                )
            )
        ordered = tuple(
            sorted(
                output,
                key=lambda item: (
                    item.asset_key,
                    item.observation_date,
                    str(item.asset_id),
                ),
            )
        )
        payload = {
            "runtime_contract_fingerprint": self.runtime_contract_fingerprint,
            "legacy_key": legacy_key,
            "mapping": record["mapping"],
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
            "points": ordered,
        }
        return MappedAggregationCalculation(
            legacy_key=legacy_key,
            family_key=str(record["mapping"]["family_key"]),
            parameter_preset_key=record["mapping"]["parameter_preset_key"],
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            points=ordered,
            execution_fingerprint=sha256_hexdigest(payload),
        )

    def execute_all(
        self,
        signal_points: Mapping[str, tuple[AggregationSignalPoint, ...]],
    ) -> tuple[MappedAggregationCalculation, ...]:
        return tuple(
            self.execute(
                str(record["legacy_key"]),
                {
                    component["legacy_signal_key"]: signal_points[
                        component["legacy_signal_key"]
                    ]
                    for dimension in record["legacy_recipe"]["dimensions"]
                    for component in dimension["components"]
                },
            )
            for record in self._registry["records"]
        )


def _dimensions(
    recipe: Mapping[str, Any],
    points: Mapping[str, AggregationSignalPoint],
) -> tuple[AggregationDimension, ...]:
    return tuple(
        AggregationDimension(
            dimension_key=str(dimension["dimension_key"]),
            method=str(dimension["method"]),
            transform=str(dimension["input_transform"]),
            weight=Decimal(dimension["weight"]),
            inputs=tuple(
                WeightedAggregationInput(
                    input_key=str(component["mapped_signal_variant_key"]),
                    value=points[str(component["legacy_signal_key"])].score,
                    weight=Decimal(component["weight"]),
                    transform=str(component["input_transform"]),
                )
                for component in dimension["components"]
            ),
        )
        for dimension in recipe["dimensions"]
    )


def _index_signal(
    signal_key: str, points: tuple[AggregationSignalPoint, ...]
) -> dict[tuple[uuid.UUID, date], AggregationSignalPoint]:
    if not points:
        raise ValueError(f"Aggregation Signal {signal_key} contains no points")
    indexed = {(point.asset_id, point.observation_date): point for point in points}
    if len(indexed) != len(points):
        raise ValueError(f"Aggregation Signal {signal_key} contains duplicate asset dates")
    if any(not point.score.is_finite() for point in points):
        raise ValueError(f"Aggregation Signal {signal_key} contains a non-finite score")
    return indexed


def _common_identities(
    indexed: Mapping[
        str, dict[tuple[uuid.UUID, date], AggregationSignalPoint]
    ],
) -> tuple[set[tuple[uuid.UUID, date]], date, date]:
    asset_sets = {
        frozenset(point.asset_id for point in points.values())
        for points in indexed.values()
    }
    if len(asset_sets) != 1 or not asset_sets or len(next(iter(asset_sets))) < 2:
        raise ValueError("Aggregation inputs must share at least two assets")
    coverage_start = max(
        min(point.observation_date for point in points.values())
        for points in indexed.values()
    )
    coverage_end = min(
        max(point.observation_date for point in points.values())
        for points in indexed.values()
    )
    if coverage_start > coverage_end:
        raise ValueError("Aggregation inputs have no common date range")
    identities: set[tuple[uuid.UUID, date]] | None = None
    for points in indexed.values():
        current = {
            identity
            for identity in points
            if coverage_start <= identity[1] <= coverage_end
        }
        if identities is None:
            identities = current
        elif current != identities:
            raise ValueError(
                "Aggregation inputs contain missing observations after common warmup"
            )
    if not identities:
        raise ValueError("Aggregation inputs contain no common observations")
    return identities, coverage_start, coverage_end


def _direction(score: Decimal) -> str:
    if score > 0:
        return "positive"
    if score < 0:
        return "negative"
    return "neutral"
