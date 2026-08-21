from __future__ import annotations

import json
from pathlib import Path

import pytest

from style_rotation.v022.migration import (
    load_migration_registry,
    migration_registry_summary,
)

REGISTRY = Path("v0.22/m4/migration-registry.v0.22.3.json")


def test_m4_registry_covers_every_concrete_factor_and_signal_with_frozen_oracles() -> None:
    registry = load_migration_registry(REGISTRY)
    summary = migration_registry_summary(registry)

    assert summary["factor_variant_count"] == 28
    assert summary["signal_version_count"] == 51
    assert summary["oracle_binding_count"] == 158
    assert summary["status_counts"] == {"executable": 6, "mapped": 73}
    assert len(summary["registry_fingerprint"]) == 64
    assert all(len(item.oracle_outputs) == 2 for item in registry.records)


def test_m3_executable_mappings_keep_their_frozen_feature_identities() -> None:
    registry = load_migration_registry(REGISTRY)
    mappings = {item.legacy_key: item.mapping for item in registry.records}

    assert mappings["return_continuation__total_return__w120"].variant_key == (
        "return_continuation__w120"
    )
    assert mappings[
        "price_cross_above_ma__moving_average_ratio__s1_l200"
    ].origin_stage == 2
    assert mappings[
        "low_illiquidity_quality__amihud_illiquidity__w20"
    ].variant_key == "low_illiquidity_quality__w20"
    assert mappings[
        "illiquidity_premium__amihud_illiquidity__w60"
    ].origin_stage == 3
    assert mappings["return_continuation__total_return__w5"].origin_stage == 3
    assert mappings["amihud_illiquidity__w60"].origin_stage == 2


def test_registry_freezes_complete_legacy_execution_policies() -> None:
    registry = load_migration_registry(REGISTRY)
    recipes = {item.legacy_key: item.legacy_recipe for item in registry.records}

    assert recipes["total_return__w120"]["required_price_observations"] == 121
    assert recipes["return_continuation__total_return__w120"] == {
        "factor_variant_key": "total_return__w120",
        "input_asset_role": "candidate",
        "form": "continuous",
        "direction": "higher_is_better",
        "rule": None,
        "normalization": "cross_sectional_centered_rank_-1_1",
        "extreme_policy": "none",
        "missing_policy": "error_after_common_warmup",
        "tie_policy": "average_rank",
    }
    discrete = recipes["price_cross_above_ma__moving_average_ratio__s1_l200"]
    assert discrete["normalization"] == "none"
    assert discrete["tie_policy"] == "not_applicable"


def test_registry_rejects_missing_concrete_version_and_oracle_drift(tmp_path: Path) -> None:
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    document["records"].pop()
    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="count mismatch"):
        load_migration_registry(missing)

    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    document["records"][0]["oracle_outputs"][0]["content_hash"] = "0" * 64
    drift = tmp_path / "drift.json"
    drift.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="Oracle binding drift"):
        load_migration_registry(drift)
