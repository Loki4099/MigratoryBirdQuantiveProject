from __future__ import annotations

from typing import Any

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.workspace.compiler import compile_research_spec
from style_rotation.workspace.contracts import (
    ModelPresetDescriptor,
    ResearchDraftSelection,
    SignalDescriptor,
    StrategyPresetDescriptor,
)
from style_rotation.workspace.options import Frequency, build_workspace_options


def build_compile_preview(
    document: dict[str, Any],
    *,
    frequency: Frequency,
    asset_security_ids: tuple[str, ...],
    selected_assets: tuple[dict[str, Any], ...],
    factor_variant_keys: tuple[str, ...],
    signal_version_keys: tuple[str, ...],
    model_preset_keys: tuple[str, ...],
    strategy_preset_keys: tuple[str, ...],
    asset_data_inputs: dict[str, tuple[str, ...]] | None = None,
    model_target_keys: tuple[str, ...] = ("cross_sectional_relative_return__h5",),
) -> dict[str, Any]:
    options = build_workspace_options(
        document,
        frequency=frequency,
        selected_factor_variants=factor_variant_keys,
        selected_signals=signal_version_keys,
        selected_models=model_preset_keys,
        selected_strategies=strategy_preset_keys,
        selected_assets=selected_assets,
        selected_asset_data_inputs=asset_data_inputs,
    )
    signals = tuple(
        SignalDescriptor(
            version_key=version["version_key"],
            factor_variant_key=version["factor_variant_key"],
            dimension_key=family["dimension_hint"],
            output_type=family["output_type"],
            frequency=frequency,
        )
        for family in options["signal_families"]
        for version in family["versions"]
    )
    models = tuple(
        ModelPresetDescriptor.model_validate(
            {
                **{
                    key: value
                    for key, value in preset.items()
                    if key not in {"selectable", "reason_codes", "accepted_signal_keys"}
                },
                "family_key": family["key"],
            }
        )
        for family in options["model_families"]
        for preset in family["presets"]
    )
    strategies = tuple(
        StrategyPresetDescriptor(
            preset_key=preset["preset_key"],
            family_key=family["key"],
            compatible_model_output_types=frozenset(family["compatible_model_output_types"]),
            supported_frequencies=frozenset(family["supported_frequencies"]),
            target_k=int(preset["parameters"]["target_k"]),
            minimum_eligible_assets=family["minimum_eligible_assets"],
            formal_minimum_eligible_assets=family["formal_minimum_eligible_assets"],
            coverage_ratio=family["coverage_ratio"],
            parameters=preset["parameters"],
        )
        for family in options["strategy_families"]
        for preset in family["presets"]
    )
    asset_context_key = sha256_hexdigest(
        {
            "asset_security_ids": sorted(asset_security_ids),
            "asset_data_inputs": {
                security_id: sorted(input_keys)
                for security_id, input_keys in sorted(
                    (asset_data_inputs or {
                        security_id: ("canonical_market_bars",)
                        for security_id in asset_security_ids
                    }).items()
                )
            },
            "catalog": document["catalog_version"],
        }
    )
    compiled = compile_research_spec(
        ResearchDraftSelection(
            asset_context_key=asset_context_key,
            factor_variant_keys=factor_variant_keys,
            signal_version_keys=signal_version_keys,
            model_preset_keys=model_preset_keys,
            model_target_keys=model_target_keys,
            strategy_preset_keys=strategy_preset_keys,
            frequency=frequency,
        ),
        signals=signals,
        models=models,
        strategies=strategies,
    )
    blockers = _selected_blockers(
        options, factor_variant_keys, model_preset_keys, strategy_preset_keys
    )
    if blockers:
        compiled = compiled.model_copy(update={"runnable": False})
    return {
        "catalog_version": document["catalog_version"],
        "compiled": compiled.model_dump(mode="json"),
        "blockers": blockers,
        "selected_asset_count": options["selected_asset_count"],
        "usable_asset_count": options["usable_asset_count"],
    }


def _selected_blockers(
    options: dict[str, Any],
    selected_factors: tuple[str, ...],
    selected_models: tuple[str, ...],
    selected_strategies: tuple[str, ...],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    blockers.extend(
        {
            "layer": "asset",
            "object_key": f"{item['security_id']}:{item['input_key']}",
            "reason_codes": item["reason_codes"],
        }
        for item in options["asset_data_input_blockers"]
    )
    factor_options = {
        variant["key"]: variant
        for family in options["factor_families"]
        for variant in family["variants"]
    }
    for key in selected_factors:
        option = factor_options.get(key)
        reasons = ["selection_unknown"] if option is None else option["reason_codes"]
        if reasons:
            blockers.append({"layer": "factor", "object_key": key, "reason_codes": reasons})
    model_options = {
        preset["preset_key"]: preset
        for family in options["model_families"]
        for preset in family["presets"]
    }
    strategy_options = {
        preset["preset_key"]: preset
        for family in options["strategy_families"]
        for preset in family["presets"]
    }
    for layer, selected, catalog in (
        ("model", selected_models, model_options),
        ("strategy", selected_strategies, strategy_options),
    ):
        for key in selected:
            option = catalog.get(key)
            reasons = ["selection_unknown"] if option is None else option["reason_codes"]
            if reasons:
                blockers.append({"layer": layer, "object_key": key, "reason_codes": reasons})
    return blockers
