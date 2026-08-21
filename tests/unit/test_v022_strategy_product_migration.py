from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from style_rotation.v022.strategy_product_migration import (
    load_strategy_product_registry,
    strategy_product_summary,
)

REGISTRY = Path("v0.22/m5/strategy-product-migration-registry.v0.22.0.json")


def test_registry_covers_all_strategies_defenses_and_the_product_chain() -> None:
    registry = load_strategy_product_registry(REGISTRY)

    assert strategy_product_summary(registry) == {
        "strategy_version_count": 14,
        "legacy_strategy_family_distribution": {
            "multi_etf_top_k": 4,
            "us_large_cap_top_k": 10,
        },
        "defense_distribution": {"fixed_20": 6, "none": 8},
        "product_version_count": 1,
        "active_product_count": 1,
        "registry_fingerprint": registry["registry_fingerprint"],
    }
    product = registry["product_records"][0]
    assert product["aggregation_mapping"]["family_key"] == "flat_equal_weight_mean"
    assert product["aggregation_mapping"]["parameter_preset_key"] == "signal_equal_v1"
    assert product["aggregation_mapping"]["input_signal_variant_keys"] == [
        "low_skew_premium__w60",
        "return_continuation__w20",
        "return_continuation__w60",
    ]
    assert product["strategy_mapping"]["variant_key"] == (
        "cross_section_rank_top_k_large_cap_parity"
    )
    assert product["lineage_closure"]["artifact_count"] == 218
    assert product["lineage_closure"]["dependency_edge_count"] == 418


def test_registry_rejects_product_lineage_and_strategy_oracle_drift(
    tmp_path: Path,
) -> None:
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    drifted = deepcopy(document)
    drifted["product_records"][0]["lineage_closure"]["artifact_count"] = 217
    path = tmp_path / "lineage-drift.json"
    path.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(ValueError, match="lineage closure drift"):
        load_strategy_product_registry(path)

    drifted = deepcopy(document)
    drifted["strategy_records"][0]["legacy_identity"]["content_hash"] = "0" * 64
    path = tmp_path / "strategy-drift.json"
    path.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(ValueError, match="Strategy Oracle drift"):
        load_strategy_product_registry(path)
