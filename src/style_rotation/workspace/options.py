from __future__ import annotations

from collections import defaultdict
from itertools import product
from typing import Any, Literal

from style_rotation.workspace.contracts import ModelPresetDescriptor, SignalDescriptor

Frequency = Literal["weekly", "monthly"]


def build_workspace_options(
    document: dict[str, Any],
    *,
    frequency: Frequency,
    selected_factor_variants: tuple[str, ...],
    selected_signals: tuple[str, ...],
    selected_models: tuple[str, ...] = (),
    selected_strategies: tuple[str, ...] = (),
    selected_assets: tuple[dict[str, Any], ...] = (),
    selected_asset_data_inputs: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    """Resolve downstream legality without silently repairing user selections."""
    selected_factor_set = set(selected_factor_variants)
    selected_signal_set = set(selected_signals)
    known_factor_variants = {
        variant["key"] for family in document["factor_families"] for variant in family["variants"]
    }
    unknown_factors = sorted(selected_factor_set.difference(known_factor_variants))

    resolved_asset_inputs = (
        selected_asset_data_inputs
        if selected_asset_data_inputs is not None
        else {
            str(asset["security_id"]): ("canonical_market_bars",)
            for asset in selected_assets
        }
    )
    available_inputs_by_asset: dict[str, set[str]] = {}
    asset_data_input_blockers: list[dict[str, Any]] = []
    for asset in selected_assets:
        security_id = str(asset["security_id"])
        available: set[str] = set()
        if bool(asset.get("canonical_data_available", asset.get("selectable", False))):
            available.add("canonical_market_bars")
        available_inputs_by_asset[security_id] = available
        for input_key in sorted(set(resolved_asset_inputs.get(security_id, ()))):
            if input_key not in available:
                asset_data_input_blockers.append(
                    {
                        "security_id": security_id,
                        "input_key": input_key,
                        "reason_codes": ["asset_data_input_unavailable"],
                    }
                )

    factor_families = []
    factor_variant_selectable: dict[str, bool] = {}
    for family in document["factor_families"]:
        # Catalogs published before per-asset input selection contained only
        # market-derived Factors; their explicit compatibility default is bars.
        required_inputs = set(
            family.get("required_asset_input_keys", ("canonical_market_bars",))
        )
        input_ready = all(
            required_inputs.issubset(set(resolved_asset_inputs.get(str(asset["security_id"]), ())))
            and required_inputs.issubset(available_inputs_by_asset[str(asset["security_id"])])
            for asset in selected_assets
        )
        reasons = [] if input_ready else ["asset_data_input_missing"]
        variants = [
            {
                **variant,
                "selected": variant["key"] in selected_factor_set,
                "selectable": input_ready,
                "reason_codes": reasons,
            }
            for variant in family["variants"]
        ]
        factor_variant_selectable.update(
            {variant["key"]: input_ready for variant in family["variants"]}
        )
        factor_families.append(
            {
                **family,
                "required_asset_input_keys": sorted(required_inputs),
                "variants": variants,
            }
        )

    signal_descriptors: dict[str, SignalDescriptor] = {}
    signal_families = []
    for template in document["signal_templates"]:
        versions = []
        output_type = _signal_output_type(template["form"])
        for factor_variant in template["factor_variants"]:
            key = f"{template['key']}__{factor_variant}"
            reasons = [] if factor_variant in selected_factor_set else ["factor_not_selected"]
            if not factor_variant_selectable.get(factor_variant, False):
                reasons.append("factor_data_input_unavailable")
            signal_descriptor = SignalDescriptor(
                version_key=key,
                factor_variant_key=factor_variant,
                dimension_key=template["dimension_hint"],
                output_type=output_type,
                frequency=frequency,
            )
            signal_descriptors[key] = signal_descriptor
            versions.append(
                {
                    "version_key": key,
                    "factor_variant_key": factor_variant,
                    "selected": key in selected_signal_set,
                    "selectable": not reasons,
                    "reason_codes": reasons,
                }
            )
        signal_families.append({**template, "output_type": output_type, "versions": versions})

    unknown_signals = sorted(selected_signal_set.difference(signal_descriptors))
    selected_descriptors = [
        signal_descriptors[key]
        for key in sorted(selected_signal_set.intersection(signal_descriptors))
    ]
    invalid_selected_signals = {
        signal.version_key
        for signal in selected_descriptors
        if signal.factor_variant_key not in selected_factor_set
        or not factor_variant_selectable.get(signal.factor_variant_key, False)
    }

    model_families = []
    model_options: dict[str, dict[str, Any]] = {}
    for family in document["model_families"]:
        presets = []
        for preset in family["presets"]:
            model_descriptor = ModelPresetDescriptor.model_validate(
                {
                    **{
                        key: value
                        for key, value in preset.items()
                        if key not in {"parameters", "target_key"}
                    },
                    "family_key": family["key"],
                }
            )
            reasons = _model_reasons(
                model_descriptor,
                selected_descriptors,
                frequency=frequency,
                implementation_status=family["implementation_status"],
            )
            if invalid_selected_signals:
                reasons.append("selected_signal_invalidated")
            if unknown_signals:
                reasons.append("selected_signal_unknown")
            option = {
                **preset,
                "selectable": not reasons,
                "reason_codes": list(dict.fromkeys(reasons)),
                "accepted_signal_keys": (sorted(selected_signal_set) if not reasons else []),
            }
            presets.append(option)
            model_options[preset["preset_key"]] = option
        model_families.append({**family, "presets": presets})

    selected_model_set = set(selected_models)
    unknown_models = sorted(selected_model_set.difference(model_options))
    input_ready_asset_ids = {
        str(asset["security_id"])
        for asset in selected_assets
        if "canonical_market_bars"
        in set(resolved_asset_inputs.get(str(asset["security_id"]), ()))
        and "canonical_market_bars" in available_inputs_by_asset[str(asset["security_id"])]
    }
    usable_assets = tuple(
        asset
        for asset in selected_assets
        if asset.get("selectable", False) and str(asset["security_id"]) in input_ready_asset_ids
    )
    asset_type_counts: dict[str, int] = defaultdict(int)
    for asset in usable_assets:
        asset_type_counts[str(asset["instrument_type"])] += 1
    selected_asset_ids = {str(asset["security_id"]) for asset in selected_assets}
    unusable_asset_selected = len(usable_assets) != len(selected_assets)
    strategy_families = []
    for family in document.get("strategy_families", []):
        presets = []
        axes = family["parameter_options"]
        for target_k, defense, selection_buffer, sector_cap in product(
            axes["target_k"], axes["defense"], axes["selection_buffer"], axes["sector_cap"]
        ):
            parameters = {
                "target_k": target_k,
                "defense": defense,
                "selection_buffer": selection_buffer,
                "sector_cap": sector_cap,
            }
            preset_key = _strategy_preset_key(family["key"], parameters)
            strategy_reasons: list[str] = []
            if family["implementation_status"] != "available":
                strategy_reasons.append("implementation_unavailable")
            if frequency not in family["supported_frequencies"]:
                strategy_reasons.append("frequency_incompatible")
            if not selected_model_set:
                strategy_reasons.append("model_not_selected")
            if unknown_models:
                strategy_reasons.append("selected_model_unknown")
            for model_key in sorted(selected_model_set.intersection(model_options)):
                model = model_options[model_key]
                if not model["selectable"]:
                    strategy_reasons.append("selected_model_invalidated")
                if model["output_type"] not in family["compatible_model_output_types"]:
                    strategy_reasons.append("model_output_incompatible")
                if model["output_comparability"] != "cross_sectional":
                    strategy_reasons.append("model_not_cross_sectionally_comparable")
            required_types = {family["required_instrument_type"]}
            if family["required_instrument_type"] == "Common Stock":
                # ADRs in the large-cap stock catalog are equity claims and share
                # the same execution contract as Common Stock. They must not make
                # an otherwise homogeneous stock universe unusable.
                required_types.add("ADR")
            matching_count = sum(asset_type_counts[item] for item in required_types)
            if not selected_asset_ids:
                strategy_reasons.append("asset_not_selected")
            if unusable_asset_selected:
                strategy_reasons.append("selected_asset_unavailable")
            if input_ready_asset_ids != selected_asset_ids:
                strategy_reasons.append("selected_asset_data_input_missing")
            if sum(asset_type_counts.values()) != matching_count:
                strategy_reasons.append("asset_type_incompatible")
            if matching_count < family["minimum_eligible_assets"]:
                strategy_reasons.append("asset_count_below_launch_minimum")
            if target_k > matching_count:
                strategy_reasons.append("rankable_count_below_k")
            if family["key"] == "multi_etf_top_k" and target_k > matching_count // 2:
                strategy_reasons.append("etf_k_exceeds_half_rankable")
            if sector_cap == "pit_30_percent" and not all(
                bool(asset.get("pit_sector_available")) for asset in usable_assets
            ):
                strategy_reasons.append("pit_sector_data_unavailable")
            research_mode = (
                "formal"
                if matching_count >= family["formal_minimum_eligible_assets"]
                else "exploratory"
            )
            presets.append(
                {
                    "preset_key": preset_key,
                    "parameters": parameters,
                    "selected": preset_key in set(selected_strategies),
                    "selectable": not strategy_reasons,
                    "reason_codes": list(dict.fromkeys(strategy_reasons)),
                    "research_mode": research_mode,
                }
            )
        strategy_families.append({**family, "presets": presets})

    return {
        "catalog_version": document["catalog_version"],
        "frequency": frequency,
        "model_target_options": [
            {
                "target_key": f"{kind}__h{horizon}",
                "target_kind": kind,
                "horizon_sessions": horizon,
                "recommended": (frequency == "weekly" and horizon == 5)
                or (frequency == "monthly" and horizon == 21),
            }
            for kind in ("future_return", "cross_sectional_relative_return")
            for horizon in (5, 21, 63)
        ],
        "unknown_factor_variant_keys": unknown_factors,
        "unknown_signal_version_keys": unknown_signals,
        "unknown_model_preset_keys": unknown_models,
        "asset_data_input_blockers": asset_data_input_blockers,
        "selected_asset_count": len(selected_asset_ids),
        "usable_asset_count": len(usable_assets),
        "selected_asset_type_counts": dict(sorted(asset_type_counts.items())),
        "factor_families": factor_families,
        "signal_families": signal_families,
        "model_families": model_families,
        "strategy_families": strategy_families,
    }


def _strategy_preset_key(family_key: str, parameters: dict[str, Any]) -> str:
    return (
        f"{family_key}__k{parameters['target_k']}__{parameters['defense']}__"
        f"{parameters['selection_buffer']}__{parameters['sector_cap']}"
    )


def _model_reasons(
    model: ModelPresetDescriptor,
    signals: list[SignalDescriptor],
    *,
    frequency: Frequency,
    implementation_status: str,
) -> list[str]:
    reasons: list[str] = []
    if implementation_status != "available":
        reasons.append("implementation_unavailable")
    if frequency not in model.supported_frequencies:
        reasons.append("frequency_incompatible")
    assignments: dict[str, int] = defaultdict(int)
    for signal in signals:
        matching = [
            slot
            for slot in model.input_slots
            if signal.dimension_key in slot.allowed_dimension_keys
            and signal.output_type in slot.allowed_output_types
        ]
        if not matching:
            reasons.append("signal_unaccepted")
        elif len(matching) > 1:
            reasons.append("signal_slot_ambiguous")
        else:
            assignments[matching[0].slot_key] += 1
    for slot in model.input_slots:
        count = assignments[slot.slot_key]
        if count < slot.minimum_count:
            reasons.append("slot_underflow")
        if count > slot.maximum_count:
            reasons.append("slot_overflow")
    return reasons


def _signal_output_type(form: str) -> Literal["continuous", "directional", "event"]:
    if form == "continuous":
        return "continuous"
    if form == "threshold_state":
        return "directional"
    return "event"
