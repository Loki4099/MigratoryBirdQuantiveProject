from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from style_rotation.v022.model_migration import (
    model_registry_summary,
    validate_model_migration_registry,
)

REGISTRY_PATH = Path("v0.22/m5/model-migration-registry.v0.22.0.json")
ORACLE_PATH = Path("v0.22/m0/v021-baseline-manifest.v0.22.0.json")
AGGREGATION_PATH = Path("v0.22/catalogs/aggregation/deterministic.v0.22.0.json")
SIGNAL_PATH = Path("v0.22/m4/migration-registry.v0.22.3.json")


def test_model_registry_covers_all_legacy_specifications_and_oracles() -> None:
    registry, oracle, aggregation, signals = _documents()

    validate_model_migration_registry(
        registry,
        oracle_manifest=oracle,
        aggregation_catalog=aggregation,
        signal_registry=signals,
    )

    assert model_registry_summary(registry) == {
        "record_count": 86,
        "distribution": {
            "dimension_subset_equal_weight": 31,
            "directional_vote": 2,
            "fixed_weight": 2,
            "single_signal": 51,
        },
        "family_mapping": {
            "directional_weighted_vote": 2,
            "hierarchical_weighted_mean": 33,
            "single_signal_identity": 51,
        },
        "oracle_binding_count": 172,
        "registry_fingerprint": registry["registry_fingerprint"],
    }


def test_model_registry_preserves_exact_special_recipes() -> None:
    registry, _, _, _ = _documents()
    records = {record["legacy_key"]: record for record in registry["records"]}

    trend = records["trend_tilt_v1"]
    assert trend["mapping"]["parameter_preset_key"] == "legacy_trend_tilt_v1"
    assert trend["legacy_recipe"]["method"] == "weighted_mean"
    assert {
        item["method"] for item in trend["legacy_recipe"]["dimensions"]
    } == {"weighted_mean"}
    assert [item["weight"] for item in trend["legacy_recipe"]["dimensions"]] == [
        "0.400000000000000000",
        "0.100000000000000000",
        "0.100000000000000000",
        "0.200000000000000000",
        "0.200000000000000000",
    ]

    vote = records["five_dimension_weighted_vote_v1"]
    assert vote["mapping"]["family_key"] == "directional_weighted_vote"
    assert vote["mapping"]["parameter_preset_key"] == "legacy_weighted_vote_v1"
    assert vote["legacy_recipe"]["method"] == "weighted_vote"
    assert {item["input_transform"] for item in vote["legacy_recipe"]["dimensions"]} == {
        "sign"
    }


def test_model_registry_rejects_weight_and_oracle_drift() -> None:
    registry, oracle, aggregation, signals = _documents()
    invalid_weight = deepcopy(registry)
    invalid_weight["records"][0]["legacy_recipe"]["dimensions"][0]["weight"] = "0.8"
    with pytest.raises(ValueError, match="dimension weights"):
        validate_model_migration_registry(
            invalid_weight,
            oracle_manifest=oracle,
            aggregation_catalog=aggregation,
            signal_registry=signals,
        )

    invalid_oracle = deepcopy(registry)
    invalid_oracle["records"][0]["oracle_outputs"][0]["content_hash"] = "0" * 64
    with pytest.raises(ValueError, match="Oracle binding drift"):
        validate_model_migration_registry(
            invalid_oracle,
            oracle_manifest=oracle,
            aggregation_catalog=aggregation,
            signal_registry=signals,
        )


def _documents() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]
]:
    return tuple(
        json.loads(path.read_text(encoding="utf-8"))
        for path in (REGISTRY_PATH, ORACLE_PATH, AGGREGATION_PATH, SIGNAL_PATH)
    )
