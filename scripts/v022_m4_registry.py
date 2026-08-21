from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.migration import load_migration_registry, migration_registry_summary

PROJECT_ROOT = Path(__file__).parents[1]
FACTOR_CATALOG = PROJECT_ROOT / "v0.2/catalogs/factors.v0.2.0.json"
SIGNAL_CATALOG = PROJECT_ROOT / "v0.2/catalogs/signals.v0.2.0.json"
ORACLE_MANIFEST = PROJECT_ROOT / "v0.22/m0/v021-baseline-manifest.v0.22.0.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "v0.22/m4/migration-registry.v0.22.3.json"
EXECUTABLE_FACTORS = {
    "total_return__w120": 1,
    "moving_average_ratio__s1_l200": 1,
    "amihud_illiquidity__w20": 2,
}
FROZEN_FACTOR_FAMILY_STAGES = {
    "total_return": 1,
    "moving_average_ratio": 1,
    "amihud_illiquidity": 2,
}
EXECUTABLE_SIGNALS = {
    "return_continuation__total_return__w120": ("return_continuation__w120", 3),
    "price_cross_above_ma__moving_average_ratio__s1_l200": (
        "price_cross_above_ma__s1_l200",
        2,
    ),
    "low_illiquidity_quality__amihud_illiquidity__w20": (
        "low_illiquidity_quality__w20",
        3,
    ),
}
FROZEN_SIGNAL_FAMILY_STAGES = {
    "return_continuation": 3,
    "price_cross_above_ma": 2,
    "low_illiquidity_quality": 3,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or verify the frozen M4 registry")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    expected = build_registry()
    if args.verify:
        if _read(args.output) != expected:
            raise ValueError("Committed M4 Migration Registry does not match its frozen inputs")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(expected, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    registry = load_migration_registry(
        args.output,
        factor_catalog_path=FACTOR_CATALOG,
        signal_catalog_path=SIGNAL_CATALOG,
        oracle_manifest_path=ORACLE_MANIFEST,
    )
    print(json.dumps(migration_registry_summary(registry), indent=2, sort_keys=True))


def build_registry() -> dict[str, Any]:
    factors = _read(FACTOR_CATALOG)
    signals = _read(SIGNAL_CATALOG)
    oracle = _read(ORACLE_MANIFEST)
    oracle_index = _oracle_index(oracle)
    records: list[dict[str, Any]] = []
    factor_by_variant: dict[str, str] = {}
    for definition in factors["definitions"]:
        for variant in definition["variants"]:
            legacy_key = variant["key"]
            factor_by_variant[legacy_key] = definition["key"]
            records.append(
                {
                    "component_kind": "factor_variant",
                    "legacy_key": legacy_key,
                    "legacy_family_key": definition["key"],
                    "legacy_recipe": {
                        "formula": definition["formula"],
                        "implementation_key": definition["implementation_key"],
                        "inputs": definition["inputs"],
                        "parameters": variant["parameters"],
                        "required_price_observations": variant[
                            "required_price_observations"
                        ],
                    },
                    "mapping": {
                        "family_key": definition["key"],
                        "variant_key": legacy_key,
                        "origin_stage": FROZEN_FACTOR_FAMILY_STAGES.get(
                            definition["key"], 1
                        ),
                    },
                    "oracle_outputs": oracle_index[("factor_variant", legacy_key)],
                    "status": "executable" if legacy_key in EXECUTABLE_FACTORS else "mapped",
                }
            )
    for template in signals["templates"]:
        for factor_variant in template["factor_variants"]:
            legacy_key = f'{template["key"]}__{factor_variant}'
            suffix = factor_variant.removeprefix(f'{factor_by_variant[factor_variant]}__')
            mapped_variant, origin_stage = EXECUTABLE_SIGNALS.get(
                legacy_key,
                (
                    f'{template["key"]}__{suffix}',
                    FROZEN_SIGNAL_FAMILY_STAGES.get(
                        template["key"],
                        3
                        if factor_by_variant[factor_variant] == "amihud_illiquidity"
                        else 2,
                    ),
                ),
            )
            records.append(
                {
                    "component_kind": "signal_version",
                    "legacy_key": legacy_key,
                    "legacy_family_key": template["key"],
                    "legacy_recipe": {
                        "factor_variant_key": factor_variant,
                        "input_asset_role": "candidate",
                        "form": template["form"],
                        "direction": template["direction"],
                        "rule": template.get("rule"),
                        "normalization": (
                            signals["defaults"]["continuous_normalization"]
                            if template["form"] == "continuous"
                            else "none"
                        ),
                        "extreme_policy": "none",
                        "missing_policy": signals["defaults"]["missing_policy"],
                        "tie_policy": (
                            signals["defaults"]["tie_policy"]
                            if template["form"] == "continuous"
                            else "not_applicable"
                        ),
                    },
                    "mapping": {
                        "family_key": template["key"],
                        "variant_key": mapped_variant,
                        "origin_stage": origin_stage,
                    },
                    "oracle_outputs": oracle_index[("signal_version", legacy_key)],
                    "status": "executable" if legacy_key in EXECUTABLE_SIGNALS else "mapped",
                }
            )
    return {
        "catalog_type": "v022_migration_registry",
        "registry_version": "0.22.3",
        "contract_version": "v0.22.0",
        "oracle_baseline_id": oracle["baseline_id"],
        "factor_catalog_fingerprint": sha256_hexdigest(factors),
        "signal_catalog_fingerprint": sha256_hexdigest(signals),
        "oracle_manifest_fingerprint": sha256_hexdigest(oracle),
        "records": records,
    }


def _oracle_index(document: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    groups = (
        ("factor_variant", "factor_datasets", "variant_key"),
        ("signal_version", "signal_datasets", "signal_key"),
    )
    fields = (
        "artifact_id",
        "semantic_fingerprint",
        "content_hash",
        "bundle_key",
        "bundle_version",
        "universe_key",
        "universe_version",
        "engine_key",
        "engine_version",
        "coverage_start",
        "coverage_end",
        "row_count",
    )
    for kind, group, key_field in groups:
        for item in document["oracle_outputs"][group]:
            index.setdefault((kind, item[key_field]), []).append(
                {field: item[field] for field in fields}
            )
    return index


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
