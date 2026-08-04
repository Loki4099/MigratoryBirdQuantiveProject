from __future__ import annotations

import json
from itertools import chain
from pathlib import Path
from typing import Any

CATALOG_ROOT = Path(__file__).resolve().parents[1] / "catalogs"


def _load(name: str) -> dict[str, Any]:
    path = CATALOG_ROOT / name
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AssertionError(f"{name} must contain a JSON object")
    return value


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        duplicates = sorted({item for item in values if values.count(item) > 1})
        raise AssertionError(f"Duplicate {label}: {duplicates}")


def _assert_weight_vector(weights: list[float], label: str) -> None:
    if not weights or any(weight <= 0 for weight in weights):
        raise AssertionError(f"{label} weights must all be positive")
    if abs(sum(weights) - 1.0) > 1e-12:
        raise AssertionError(f"{label} weights must sum to one")


def _assert_weight_mapping(weights: dict[str, float], dimensions: set[str], label: str) -> None:
    if set(weights) != dimensions:
        raise AssertionError(f"{label} must contain every representative dimension exactly once")
    _assert_weight_vector(list(weights.values()), label)


def validate() -> dict[str, int]:
    factors = _load("factors.v0.2.0.json")
    signals = _load("signals.v0.2.0.json")
    models = _load("models.v0.2.0.json")
    strategies = _load("strategies.v0.2.0.json")
    forward_returns = _load("forward_returns.v0.2.0.json")

    definitions = factors["definitions"]
    definition_keys = [item["key"] for item in definitions]
    _unique(definition_keys, "factor definition keys")
    if len(definition_keys) != 12:
        raise AssertionError(f"Expected 12 factor definitions, found {len(definition_keys)}")

    variants = list(chain.from_iterable(item["variants"] for item in definitions))
    variant_keys = [item["key"] for item in variants]
    _unique(variant_keys, "factor variant keys")
    if len(variant_keys) != 28:
        raise AssertionError(f"Expected 28 factor variants, found {len(variant_keys)}")
    for definition in definitions:
        if not definition["formula"] or not definition["inputs"]:
            raise AssertionError(f"Factor {definition['key']} lacks formula or inputs")
        for variant in definition["variants"]:
            if variant["required_price_observations"] <= 0:
                raise AssertionError(f"Factor variant {variant['key']} has invalid history")

    templates = signals["templates"]
    template_keys = [item["key"] for item in templates]
    _unique(template_keys, "signal template keys")
    generated_signal_keys: list[str] = []
    product_eligible_count = 0
    allowed_forms = {"continuous", "threshold_state", "crossover_event", "recent_event"}
    allowed_directions = {"higher_is_better", "lower_is_better"}
    for template in templates:
        if template["form"] not in allowed_forms:
            raise AssertionError(f"Signal {template['key']} has unsupported form")
        if template["direction"] not in allowed_directions:
            raise AssertionError(f"Signal {template['key']} has unsupported direction")
        if not template["rationale"] or not template["rationale_type"]:
            raise AssertionError(f"Signal {template['key']} lacks economic rationale")
        for variant_key in template["factor_variants"]:
            if variant_key not in variant_keys:
                raise AssertionError(
                    f"Signal {template['key']} references unknown factor variant {variant_key}"
                )
            generated_signal_keys.append(f"{template['key']}__{variant_key}")
            product_eligible_count += int(template["product_eligible"])
    _unique(generated_signal_keys, "generated signal keys")
    if len(generated_signal_keys) != 51:
        raise AssertionError(f"Expected 51 generated signals, found {len(generated_signal_keys)}")
    generated_signal_key_set = set(generated_signal_keys)

    dimensions = models["representative_dimensions"]
    dimension_keys = [item["key"] for item in dimensions]
    _unique(dimension_keys, "representative dimension keys")
    if len(dimension_keys) != 5:
        raise AssertionError(f"Expected five dimensions, found {len(dimension_keys)}")
    for dimension in dimensions:
        if len(dimension["components"]) != len(dimension["weights"]):
            raise AssertionError(f"Dimension {dimension['key']} component/weight length mismatch")
        _assert_weight_vector(dimension["weights"], f"dimension {dimension['key']}")
        unknown = set(dimension["components"]).difference(generated_signal_key_set)
        if unknown:
            raise AssertionError(f"Dimension {dimension['key']} has unknown signals: {unknown}")

    subset_patterns = (2 ** len(dimensions)) - 1
    expected_subset_patterns = models["expected_counts"]["dimension_subset_patterns"]
    if subset_patterns != expected_subset_patterns:
        message = (
            f"Expected {expected_subset_patterns} dimension subset patterns, "
            f"found {subset_patterns}"
        )
        raise AssertionError(message)

    dimension_key_set = set(dimension_keys)
    for specification in models["fixed_weight_specifications"]:
        _assert_weight_mapping(
            specification["dimension_weights"], dimension_key_set, specification["key"]
        )
    for specification in models["vote_specifications"]:
        weights = specification["dimension_weights"]
        if set(weights) != dimension_key_set or any(weight <= 0 for weight in weights.values()):
            message = f"Vote model {specification['key']} has invalid dimensions/weights"
            raise AssertionError(message)

    concrete_model_specifications = (
        len(generated_signal_keys)
        + subset_patterns
        + len(models["fixed_weight_specifications"])
        + len(models["vote_specifications"])
    )
    expected_models = models["expected_counts"]["concrete_model_specifications"]
    if concrete_model_specifications != expected_models:
        raise AssertionError(
            f"Expected {expected_models} concrete models, found {concrete_model_specifications}"
        )

    strategy_configurations = len(strategies["variant_templates"]) * len(strategies["k_values"])
    expected_strategy_configurations = strategies["expected_counts"][
        "strategy_variant_configurations"
    ]
    if strategy_configurations != expected_strategy_configurations:
        raise AssertionError(
            f"Expected {expected_strategy_configurations} strategy configurations, "
            f"found {strategy_configurations}"
        )
    if strategies["trend_signal"] not in generated_signal_key_set:
        raise AssertionError("Strategy trend filter references an unknown signal")
    if len(strategies["schedule_versions"]) != 2:
        raise AssertionError("Exactly weekly and monthly schedules are required in v0.2.0")

    targets = forward_returns["definitions"]
    target_keys = [item["key"] for item in targets]
    _unique(target_keys, "forward-return target keys")
    if {item["frequency"] for item in targets} != {"weekly", "monthly"}:
        raise AssertionError(
            "Exactly one weekly and one monthly forward-return target are required"
        )
    if any(item["included_member_roles"] != ["candidate", "benchmark"] for item in targets):
        raise AssertionError("v0.2 forward returns must cover candidates and the benchmark")

    return {
        "factor_definitions": len(definition_keys),
        "factor_variants": len(variant_keys),
        "signal_templates": len(template_keys),
        "generated_signals": len(generated_signal_keys),
        "product_eligible_signals": product_eligible_count,
        "representative_dimensions": len(dimension_keys),
        "dimension_subset_patterns": subset_patterns,
        "concrete_model_specifications": concrete_model_specifications,
        "strategy_variant_configurations": strategy_configurations,
        "schedule_versions": len(strategies["schedule_versions"]),
        "forward_return_targets": len(targets),
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
