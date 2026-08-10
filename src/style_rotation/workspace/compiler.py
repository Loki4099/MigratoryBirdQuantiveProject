from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.workspace.contracts import (
    CompilationIssue,
    CompilationLayer,
    CompilationReasonCode,
    CompiledModelInstance,
    CompiledResearchSpec,
    CompiledStrategyBranch,
    ModelPresetDescriptor,
    ResearchDraftSelection,
    SignalDescriptor,
    SlotAssignment,
    StrategyPresetDescriptor,
)


def compile_research_spec(
    draft: ResearchDraftSelection,
    *,
    signals: Iterable[SignalDescriptor],
    models: Iterable[ModelPresetDescriptor],
    strategies: Iterable[StrategyPresetDescriptor],
) -> CompiledResearchSpec:
    """Compile selected versions without silently dropping an incompatible upstream input."""

    signal_catalog = _index("Signal", signals, lambda item: item.version_key)
    model_catalog = _index("Model", models, lambda item: item.preset_key)
    strategy_catalog = _index("Strategy", strategies, lambda item: item.preset_key)
    issues: list[CompilationIssue] = []

    selected_signals: list[SignalDescriptor] = []
    for key in sorted(draft.signal_version_keys):
        signal = signal_catalog.get(key)
        if signal is None:
            issues.append(_issue("selection_unknown", "signal", key))
            continue
        if signal.factor_variant_key not in draft.factor_variant_keys:
            issues.append(
                _issue(
                    "signal_factor_not_selected",
                    "signal",
                    key,
                    (signal.factor_variant_key,),
                )
            )
            continue
        if signal.frequency != draft.frequency:
            issues.append(
                _issue("signal_frequency_incompatible", "signal", key, (draft.frequency,))
            )
            continue
        selected_signals.append(signal)

    compiled_models: list[CompiledModelInstance] = []
    for key in sorted(draft.model_preset_keys):
        model = model_catalog.get(key)
        if model is None:
            issues.append(_issue("selection_unknown", "model", key))
            continue
        if draft.frequency not in model.supported_frequencies:
            issues.append(_issue("model_frequency_incompatible", "model", key, (draft.frequency,)))
            continue
        assignments: dict[str, list[str]] = defaultdict(list)
        model_invalid = False
        for signal in selected_signals:
            matching = [
                slot
                for slot in model.input_slots
                if signal.dimension_key in slot.allowed_dimension_keys
                and signal.output_type in slot.allowed_output_types
            ]
            if not matching:
                issues.append(
                    _issue("model_signal_unaccepted", "model", key, (signal.version_key,))
                )
                model_invalid = True
            elif len(matching) > 1:
                issues.append(
                    _issue("model_signal_ambiguous_slot", "model", key, (signal.version_key,))
                )
                model_invalid = True
            else:
                assignments[matching[0].slot_key].append(signal.version_key)
        for slot in model.input_slots:
            count = len(assignments[slot.slot_key])
            if count < slot.minimum_count:
                issues.append(_issue("model_slot_underflow", "model", key, (slot.slot_key,)))
                model_invalid = True
            if count > slot.maximum_count:
                issues.append(_issue("model_slot_overflow", "model", key, (slot.slot_key,)))
                model_invalid = True
        if model_invalid:
            continue
        for target_key in sorted(draft.model_target_keys):
            if target_key not in {
                f"{kind}__h{horizon}"
                for kind in ("future_return", "cross_sectional_relative_return")
                for horizon in (5, 21, 63)
            }:
                issues.append(_issue("selection_unknown", "model", target_key))
                continue
            compiled_models.append(
              CompiledModelInstance(
                instance_key=f"{model.preset_key}__{target_key}__{draft.frequency}",
                preset_key=model.preset_key,
                family_key=model.family_key,
                output_type=model.output_type,
                frequency=draft.frequency,
                slot_assignments=tuple(
                    SlotAssignment(
                        slot_key=slot.slot_key,
                        signal_version_keys=tuple(sorted(assignments[slot.slot_key])),
                    )
                    for slot in model.input_slots
                ),
                parameters=model.parameters,
                target_key=target_key,
              )
            )

    branches: list[CompiledStrategyBranch] = []
    for strategy_key in sorted(draft.strategy_preset_keys):
        strategy = strategy_catalog.get(strategy_key)
        if strategy is None:
            issues.append(_issue("selection_unknown", "strategy", strategy_key))
            continue
        if draft.frequency not in strategy.supported_frequencies:
            issues.append(
                _issue(
                    "strategy_frequency_incompatible",
                    "strategy",
                    strategy_key,
                    (draft.frequency,),
                )
            )
            continue
        for compiled_model in compiled_models:
            if compiled_model.output_type not in strategy.compatible_model_output_types:
                issues.append(
                    _issue(
                        "strategy_model_output_incompatible",
                        "strategy",
                        strategy_key,
                        (compiled_model.instance_key,),
                    )
                )
                continue
            descriptor = model_catalog[compiled_model.preset_key]
            if descriptor.output_comparability != "cross_sectional":
                issues.append(
                    _issue(
                        "strategy_requires_continuous_comparable_score",
                        "strategy",
                        strategy_key,
                        (compiled_model.instance_key,),
                    )
                )
                continue
            branches.append(
                CompiledStrategyBranch(
                    branch_key=f"{compiled_model.instance_key}__{strategy.preset_key}",
                    model_instance_key=compiled_model.instance_key,
                    strategy_preset_key=strategy.preset_key,
                    strategy_family_key=strategy.family_key,
                    frequency=draft.frequency,
                    target_k=strategy.target_k,
                    parameters=strategy.parameters,
                )
            )

    normalized = {
        "asset_context_key": draft.asset_context_key,
        "factor_variant_keys": sorted(draft.factor_variant_keys),
        "signal_version_keys": sorted(item.version_key for item in selected_signals),
        "model_target_keys": sorted(draft.model_target_keys),
        "model_instances": [item.model_dump(mode="json") for item in compiled_models],
        "strategy_branches": [item.model_dump(mode="json") for item in branches],
        "frequency": draft.frequency,
    }
    fingerprint = sha256_hexdigest(normalized)
    unique_predictive = len(compiled_models)
    return CompiledResearchSpec(
        specification_fingerprint=fingerprint,
        asset_context_key=draft.asset_context_key,
        factor_variant_keys=tuple(sorted(draft.factor_variant_keys)),
        signal_version_keys=tuple(sorted(item.version_key for item in selected_signals)),
        model_instances=tuple(compiled_models),
        strategy_branches=tuple(branches),
        issues=tuple(issues),
        predictive_cell_count=unique_predictive,
        portfolio_cell_count=len(branches) * 6,
        runnable=bool(branches)
        and not any(item.reason_code == "selection_unknown" for item in issues),
    )


def _index[Descriptor](
    label: str,
    items: Iterable[Descriptor],
    key: Callable[[Descriptor], str],
) -> dict[str, Descriptor]:
    indexed: dict[str, Descriptor] = {}
    for item in items:
        value = key(item)
        if value in indexed:
            raise ValueError(f"Duplicate {label} descriptor key: {value}")
        indexed[value] = item
    return indexed


def _issue(
    reason_code: CompilationReasonCode,
    layer: CompilationLayer,
    object_key: str,
    related_keys: tuple[str, ...] = (),
) -> CompilationIssue:
    return CompilationIssue(
        reason_code=reason_code,
        layer=layer,
        object_key=object_key,
        related_keys=related_keys,
    )
