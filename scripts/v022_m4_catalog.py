from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from style_rotation.v022.catalog import load_catalog_release
from style_rotation.v022.migration import load_migration_registry

PROJECT_ROOT = Path(__file__).parents[1]
FACTOR_CATALOG = PROJECT_ROOT / "v0.2/catalogs/factors.v0.2.0.json"
SIGNAL_CATALOG = PROJECT_ROOT / "v0.2/catalogs/signals.v0.2.0.json"
REGISTRY = PROJECT_ROOT / "v0.22/m4/migration-registry.v0.22.3.json"
M3_PROCESSING = PROJECT_ROOT / "v0.22/catalogs/processing/representative.v0.22.1.json"
OUTPUT = PROJECT_ROOT / "v0.22/catalogs/processing/legacy_parity.v0.22.2.json"
RELEASE = PROJECT_ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.2.json"
RAW_BINDINGS = {
    "close_adj": ("adjusted_close", "market_price_scalar", "adjusted_close"),
    "close_raw": ("close_raw", "market_price_scalar", "unadjusted_close"),
    "volume_raw": ("volume_raw", "market_volume_scalar", "share_volume"),
}
FACTOR_UNITS = {
    "total_return": "decimal_return",
    "lagged_return": "decimal_return",
    "moving_average_ratio": "ratio",
    "rsi": "oscillator_value",
    "return_skewness": "statistic",
    "return_excess_kurtosis": "statistic",
    "realized_volatility": "annualized_volatility",
    "downside_deviation": "annualized_downside_deviation",
    "maximum_drawdown": "decimal_drawdown",
    "relative_dollar_volume": "ratio",
    "amihud_illiquidity": "return_per_currency",
    "ppo_histogram": "percentage_points",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or verify the M4 parity Catalog")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    processing, release = build_documents()
    if args.verify:
        if _read(OUTPUT) != processing or _read(RELEASE) != release:
            raise ValueError("Committed M4 parity Catalog differs from frozen mapping rules")
    else:
        OUTPUT.write_text(json.dumps(processing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        RELEASE.write_text(json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    loaded = load_catalog_release(RELEASE)
    print(
        json.dumps(
            {
                "catalog_version": loaded.bundle.release.catalog_version,
                "processing_nodes": len(loaded.bundle.processing.nodes),
                "processing_features": sum(
                    len(node.output_features) for node in loaded.bundle.processing.nodes
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )


def build_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    factors = _read(FACTOR_CATALOG)
    signals = _read(SIGNAL_CATALOG)
    registry = load_migration_registry(REGISTRY)
    records = {(item.component_kind, item.legacy_key): item for item in registry.records}
    nodes: list[dict[str, Any]] = deepcopy(_read(M3_PROCESSING)["nodes"])
    produced = {
        output["variant_key"] for node in nodes for output in node["output_features"]
    }
    factor_by_variant: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for definition in factors["definitions"]:
        for variant in definition["variants"]:
            factor_by_variant[variant["key"]] = (definition, variant)
            if variant["key"] not in produced:
                nodes.append(_factor_node(definition, variant, nodes))
                produced.add(variant["key"])
    for template in signals["templates"]:
        for factor_variant_key in template["factor_variants"]:
            legacy_key = f'{template["key"]}__{factor_variant_key}'
            mapped_key = records[("signal_version", legacy_key)].mapping.variant_key
            if mapped_key not in produced:
                definition, factor_variant = factor_by_variant[factor_variant_key]
                nodes.append(
                    _signal_node(
                        template,
                        definition,
                        factor_variant,
                        mapped_key,
                        nodes,
                    )
                )
                produced.add(mapped_key)
    nodes.sort(key=lambda item: (item["stage_no"], item["node_key"], item["variant_key"]))
    processing = {
        "catalog_type": "v022_processing",
        "catalog_version": "0.22.2",
        "nodes": nodes,
    }
    release = {
        "catalog_type": "v022_release",
        "catalog_version": "0.22.2",
        "release_key": "bird_v022_catalog",
        "contract_version": "v0.22.0",
        "processing_stage_count": 3,
        "publisher_actor": "local_researcher",
        "reviewer_actor": "local_researcher",
        "files": [
            {"component_group": "payload", "path": "payload_contracts/representative.v0.22.1.json"},
            {"component_group": "raw_inputs", "path": "raw_inputs/market_inputs.v0.22.0.json"},
            {"component_group": "processing", "path": "processing/legacy_parity.v0.22.2.json"},
            {"component_group": "aggregation", "path": "aggregation/deterministic.v0.22.0.json"},
            {"component_group": "strategy", "path": "strategies/cross_section.v0.22.0.json"},
            {"component_group": "defense", "path": "defense/parity.v0.22.0.json"},
        ],
        "expected_counts": {
            "payload_contracts": 6,
            "physical_encodings": 1,
            "raw_inputs": 9,
            "processing_nodes": len(nodes),
            "processing_features": sum(len(node["output_features"]) for node in nodes),
            "aggregation_families": 4,
            "strategies": 1,
            "defenses": 2,
        },
    }
    return processing, release


def _factor_node(
    definition: dict[str, Any], variant: dict[str, Any], existing: list[dict[str, Any]]
) -> dict[str, Any]:
    family_nodes = [item for item in existing if item["node_key"] == definition["key"]]
    if family_nodes:
        template = family_nodes[0]
        output_template = template["output_features"][0]
        node = deepcopy(template)
        suffix = variant["key"].removeprefix(f'{definition["key"]}__')
        node["variant_key"] = f'{definition["key"]}_node__{suffix}'
        node["parameters"] = variant["parameters"]
        node["execution_contract"]["lookback"] = max(
            0, variant["required_price_observations"] - 1
        )
        output = deepcopy(output_template)
        output["variant_key"] = variant["key"]
        output["parameters"] = variant["parameters"]
        node["output_features"] = [output]
        return node
    ports = []
    bindings = []
    for ordinal, input_key in enumerate(definition["inputs"]):
        source, contract, role = RAW_BINDINGS[input_key]
        ports.append(_port(input_key, "input", ordinal, contract, {"role": role}))
        bindings.append(
            {
                "input_port_key": input_key,
                "source_feature_variant_key": source,
                "binding_role": role,
                "ordinal": ordinal,
            }
        )
    ports.append(
        _port(
            "factor_value",
            "output",
            0,
            "intermediate_numeric_feature",
            {"unit": FACTOR_UNITS[definition["key"]]},
        )
    )
    return {
        "node_key": definition["key"],
        "variant_key": f'{variant["key"]}_node',
        "name": _title(definition["key"]),
        "algorithm_identity": definition["formula"],
        "parameters": variant["parameters"],
        "version_number": 1,
        "stage_no": 1,
        "implementation_key": (
            "style_rotation.v022.processing.compat."
            f'{definition["implementation_key"]}'
        ),
        "implementation_version": "1",
        "determinism_policy": "deterministic",
        "cache_policy": "content_addressed",
        "execution_contract": {
            "execution_mode": "full_recompute",
            "partition_key": ["asset_id"],
            "lookback": max(0, variant["required_price_observations"] - 1),
            "lookforward": 0,
            "revision_impact_policy": "windowed_forward",
            "watermark_policy": "completed_session",
            "checkpoint_contract": "none",
        },
        "ports": ports,
        "input_bindings": bindings,
        "output_features": [
            {
                "output_port_key": "factor_value",
                "family_key": definition["key"],
                "variant_key": variant["key"],
                "name": _title(definition["key"]),
                "formula_identity": definition["formula"],
                "parameters": variant["parameters"],
                "semantic_role": "legacy_factor",
                "unit": FACTOR_UNITS[definition["key"]],
                "direction": "not_applicable",
                "payload_contract_key": "intermediate_numeric_feature",
                "aggregation_readiness": "not_aggregation_ready",
                "research_hypothesis": (
                    "Compatibility implementation of the frozen v0.21 "
                    f'{definition["key"]} recipe.'
                ),
                "research_tier": "compatibility",
                "output_semantics": {"continuous": True, "legacy_parity": True},
            }
        ],
    }


def _signal_node(
    template: dict[str, Any],
    factor_definition: dict[str, Any],
    factor_variant: dict[str, Any],
    mapped_key: str,
    existing: list[dict[str, Any]],
) -> dict[str, Any]:
    family_nodes = [item for item in existing if item["node_key"] == template["key"]]
    if family_nodes:
        node_template = family_nodes[0]
        output_template = node_template["output_features"][0]
        name = node_template["name"]
        algorithm = node_template["algorithm_identity"]
        output_name = output_template["name"]
        formula = output_template["formula_identity"]
        hypothesis = output_template["research_hypothesis"]
        output_semantics = output_template["output_semantics"]
        output_direction = output_template["direction"]
        output_port = output_template["output_port_key"]
    else:
        name = f'{_title(template["key"])} signal'
        algorithm = _signal_algorithm(template)
        output_name = _title(template["key"])
        formula = algorithm
        hypothesis = template["rationale"]
        output_semantics = _signal_output_semantics(template)
        output_direction = "higher_is_better"
        output_port = _signal_output_port(template["form"])
    suffix = mapped_key.removeprefix(f'{template["key"]}__')
    direction = 1 if template["direction"] == "higher_is_better" else -1
    parameters = {
        **factor_variant["parameters"],
        "direction": direction,
        **({"rule": template["rule"]} if template.get("rule") is not None else {}),
    }
    execution = {
        "execution_mode": "full_recompute",
        "partition_key": ["session_date"] if template["form"] == "continuous" else ["asset_id"],
        "lookback": 1 if template["form"] == "crossover_event" else 0,
        "lookforward": 0,
        "revision_impact_policy": (
            "same_cross_section"
            if template["form"] == "continuous"
            else "from_revised_session_forward"
        ),
        "watermark_policy": "completed_session",
        "checkpoint_contract": "none",
    }
    stage_no = (
        family_nodes[0]["stage_no"]
        if family_nodes
        else 3
        if factor_definition["key"] == "amihud_illiquidity"
        else 2
    )
    return {
        "node_key": template["key"],
        "variant_key": f'{template["key"]}_node__{suffix}',
        "name": name,
        "algorithm_identity": algorithm,
        "parameters": parameters,
        "version_number": 1,
        "stage_no": stage_no,
        "implementation_key": f'style_rotation.v022.processing.compat.{template["form"]}_v1',
        "implementation_version": "1",
        "determinism_policy": "deterministic",
        "cache_policy": "content_addressed",
        "execution_contract": execution,
        "ports": [
            _port(
                "feature",
                "input",
                0,
                "intermediate_numeric_feature",
                {"role": factor_definition["key"]},
            ),
            _port(output_port, "output", 0, "final_signal_numeric", {"form": template["form"]}),
        ],
        "input_bindings": [
            {
                "input_port_key": "feature",
                "source_feature_variant_key": factor_variant["key"],
                "binding_role": factor_definition["key"],
                "ordinal": 0,
            }
        ],
        "output_features": [
            {
                "output_port_key": output_port,
                "family_key": template["key"],
                "variant_key": mapped_key,
                "name": output_name,
                "formula_identity": formula,
                "parameters": parameters,
                "semantic_role": "legacy_signal",
                "unit": _signal_unit(template["form"]),
                "direction": output_direction,
                "payload_contract_key": "final_signal_numeric",
                "aggregation_readiness": "aggregation_ready",
                "research_hypothesis": hypothesis,
                "research_tier": "compatibility",
                "output_semantics": output_semantics,
            }
        ],
    }


def _signal_algorithm(template: dict[str, Any]) -> str:
    if template["form"] == "continuous":
        return "directional_cross_sectional_centered_rank_q18"
    rule = json.dumps(template["rule"], sort_keys=True, separators=(",", ":"))
    return f'{template["form"]}:{rule}'


def _signal_output_semantics(template: dict[str, Any]) -> dict[str, Any]:
    if template["form"] == "continuous":
        return {"continuous": True, "rank_meaning": True, "range": [-1, 1], "quantum": "1e-18"}
    return {
        "event": template["form"] == "crossover_event",
        "state": template["form"] == "threshold_state",
        "rank_meaning": True,
        "rule": template["rule"],
    }


def _signal_output_port(form: str) -> str:
    return {
        "continuous": "signal_score",
        "threshold_state": "state_score",
        "crossover_event": "event_score",
    }[form]


def _signal_unit(form: str) -> str:
    return {
        "continuous": "centered_rank",
        "threshold_state": "state_score",
        "crossover_event": "event_score",
    }[form]


def _port(
    key: str,
    direction: str,
    ordinal: int,
    contract: str,
    semantics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "port_key": key,
        "direction": direction,
        "ordinal": ordinal,
        "payload_contract_key": contract,
        "binding_cardinality": "required",
        "semantics": semantics,
    }


def _title(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
