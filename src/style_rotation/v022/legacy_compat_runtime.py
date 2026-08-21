from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.factor.calculator import (
    FactorBar,
    FactorPoint,
    FactorVariantInput,
    VariantCalculation,
    calculate_variant,
)
from style_rotation.signal.calculator import (
    FactorValueInput,
    SignalCalculation,
    SignalVersionInput,
    calculate_signal,
)
from style_rotation.v022.migration import (
    MigrationRegistry,
    MigrationRegistryRecord,
    load_migration_registry,
    migration_registry_summary,
)

RUNTIME_ADAPTER_KEY = "legacy_factor_signal_compat_v1"


@dataclass(frozen=True, slots=True)
class MappedFactorCalculation:
    legacy_key: str
    mapped_variant_key: str
    calculation: VariantCalculation


@dataclass(frozen=True, slots=True)
class MappedSignalCalculation:
    legacy_key: str
    mapped_variant_key: str
    source_factor_legacy_key: str
    calculation: SignalCalculation


@dataclass(frozen=True, slots=True)
class LegacyCompatibilityRun:
    registry_version: str
    runtime_contract_fingerprint: str
    input_fingerprint: str
    execution_fingerprint: str
    factors: tuple[MappedFactorCalculation, ...]
    signals: tuple[MappedSignalCalculation, ...]


class LegacyCompatibilityRuntime:
    """Execute the frozen v0.21 recipes behind their v0.22 Catalog identities."""

    def __init__(self, registry: MigrationRegistry) -> None:
        self._registry = registry
        self._records = {
            (record.component_kind, record.legacy_key): record
            for record in registry.records
        }
        summary = migration_registry_summary(registry)
        self.runtime_contract_fingerprint = sha256_hexdigest(
            {
                "adapter_key": RUNTIME_ADAPTER_KEY,
                "registry_fingerprint": summary["registry_fingerprint"],
            }
        )

    @classmethod
    def from_registry_path(cls, path: Path) -> LegacyCompatibilityRuntime:
        return cls(load_migration_registry(path))

    def execute_factor(
        self,
        legacy_key: str,
        bars_by_asset: dict[uuid.UUID, tuple[FactorBar, ...]],
        *,
        coverage_start: date,
        coverage_end: date,
    ) -> MappedFactorCalculation:
        record = self._record("factor_variant", legacy_key)
        recipe = record.legacy_recipe
        variant = FactorVariantInput(
            factor_variant_id=_identity("factor-variant", record.mapping.variant_key),
            artifact_id=_identity("factor-artifact", record.mapping.variant_key),
            variant_key=record.mapping.variant_key,
            implementation_key=_required_string(recipe, "implementation_key"),
            parameters=_required_dict(recipe, "parameters"),
            required_price_observations=_required_int(
                recipe, "required_price_observations"
            ),
        )
        calculation = calculate_variant(
            bars_by_asset,
            variant,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
        return MappedFactorCalculation(
            legacy_key=record.legacy_key,
            mapped_variant_key=record.mapping.variant_key,
            calculation=calculation,
        )

    def execute_signal(
        self,
        legacy_key: str,
        factor: MappedFactorCalculation,
        *,
        candidate_asset_ids: frozenset[uuid.UUID],
    ) -> MappedSignalCalculation:
        record = self._record("signal_version", legacy_key)
        recipe = record.legacy_recipe
        expected_factor = _required_string(recipe, "factor_variant_key")
        if _required_string(recipe, "input_asset_role") != "candidate":
            raise ValueError(f"Unsupported Signal input asset role: {legacy_key}")
        if factor.legacy_key != expected_factor:
            raise ValueError(
                f"Signal {legacy_key} requires {expected_factor}, received {factor.legacy_key}"
            )
        rule = recipe.get("rule")
        if rule is not None and not isinstance(rule, dict):
            raise ValueError(f"Migration recipe rule must be an object: {legacy_key}")
        version = SignalVersionInput(
            signal_version_id=_identity("signal-version", record.mapping.variant_key),
            artifact_id=_identity("signal-artifact", record.mapping.variant_key),
            signal_key=record.mapping.variant_key,
            factor_variant_id=factor.calculation.variant.factor_variant_id,
            direction=_required_string(recipe, "direction"),
            normalization=_required_string(recipe, "normalization"),
            extreme_policy=_required_string(recipe, "extreme_policy"),
            missing_policy=_required_string(recipe, "missing_policy"),
            tie_policy=_required_string(recipe, "tie_policy"),
            output_type=_required_string(recipe, "form"),
            rule=rule,
        )
        factor_points = tuple(
            _as_signal_input(point)
            for point in factor.calculation.points
            if point.asset_id in candidate_asset_ids
        )
        calculation = calculate_signal(version, factor_points)
        return MappedSignalCalculation(
            legacy_key=record.legacy_key,
            mapped_variant_key=record.mapping.variant_key,
            source_factor_legacy_key=factor.legacy_key,
            calculation=calculation,
        )

    def execute_all(
        self,
        bars_by_asset: dict[uuid.UUID, tuple[FactorBar, ...]],
        *,
        candidate_asset_ids: frozenset[uuid.UUID],
        coverage_start: date,
        coverage_end: date,
    ) -> LegacyCompatibilityRun:
        factors = tuple(
            self.execute_factor(
                record.legacy_key,
                bars_by_asset,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
            )
            for record in self._registry.records
            if record.component_kind == "factor_variant"
        )
        factors_by_legacy_key = {item.legacy_key: item for item in factors}
        signals = tuple(
            self.execute_signal(
                record.legacy_key,
                factors_by_legacy_key[
                    _required_string(record.legacy_recipe, "factor_variant_key")
                ],
                candidate_asset_ids=candidate_asset_ids,
            )
            for record in self._registry.records
            if record.component_kind == "signal_version"
        )
        input_fingerprint = _bars_fingerprint(bars_by_asset)
        execution_fingerprint = sha256_hexdigest(
            {
                "runtime_contract_fingerprint": self.runtime_contract_fingerprint,
                "input_fingerprint": input_fingerprint,
                "coverage_start": coverage_start,
                "coverage_end": coverage_end,
                "factors": factors,
                "signals": signals,
            }
        )
        return LegacyCompatibilityRun(
            registry_version=str(self._registry.registry_version),
            runtime_contract_fingerprint=self.runtime_contract_fingerprint,
            input_fingerprint=input_fingerprint,
            execution_fingerprint=execution_fingerprint,
            factors=factors,
            signals=signals,
        )

    def _record(
        self,
        component_kind: Literal["factor_variant", "signal_version"],
        legacy_key: str,
    ) -> MigrationRegistryRecord:
        try:
            return self._records[(component_kind, legacy_key)]
        except KeyError as error:
            raise KeyError(f"Unknown {component_kind} migration: {legacy_key}") from error


def _as_signal_input(point: FactorPoint) -> FactorValueInput:
    return FactorValueInput(
        asset_id=point.asset_id,
        asset_key=point.asset_key,
        observation_date=point.observation_date,
        value=point.value,
    )


def _identity(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"bird-v022:{RUNTIME_ADAPTER_KEY}:{kind}:{key}")


def _bars_fingerprint(
    bars_by_asset: dict[uuid.UUID, tuple[FactorBar, ...]],
) -> str:
    ordered = [
        bar
        for asset_id in sorted(bars_by_asset, key=str)
        for bar in sorted(bars_by_asset[asset_id], key=lambda item: item.session_date)
    ]
    return sha256_hexdigest(ordered)


def _required_string(recipe: dict[str, Any], key: str) -> str:
    value = recipe.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Migration recipe {key} must be a non-empty string")
    return value


def _required_int(recipe: dict[str, Any], key: str) -> int:
    value = recipe.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Migration recipe {key} must be a positive integer")
    return value


def _required_dict(recipe: dict[str, Any], key: str) -> dict[str, Any]:
    value = recipe.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Migration recipe {key} must be an object")
    return value
