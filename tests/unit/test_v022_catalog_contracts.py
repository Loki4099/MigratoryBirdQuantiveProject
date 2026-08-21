from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from style_rotation.v022.catalog import (
    catalog_component_plan,
    diff_catalog_releases,
    lint_catalog_release,
    load_catalog_release,
)
from style_rotation.v022.contracts import (
    AggregationFamilySeed,
    CatalogBundle,
    CatalogReleaseManifest,
    RawInputSeed,
    StrategyCatalog,
)
from style_rotation.v022.publication import (
    CatalogPublicationContext,
    publish_catalog_release,
)

MANIFEST = Path("v0.22/catalogs/releases/catalog_release.v0.22.0.json")
M3_MANIFEST = Path("v0.22/catalogs/releases/catalog_release.v0.22.1.json")
M5_MANIFEST = Path("v0.22/catalogs/releases/catalog_release.v0.22.3.json")
PARAMETER_MANIFEST = Path("v0.22/catalogs/releases/catalog_release.v0.22.4.json")


def test_m1_release_is_strict_complete_and_canonical() -> None:
    loaded = load_catalog_release(MANIFEST)
    lint = lint_catalog_release(MANIFEST)
    plan = catalog_component_plan(loaded.bundle)

    assert lint["status"] == "passed"
    assert all(lint["checks"].values())
    assert lint["component_count"] == 67
    assert len(loaded.bundle.raw_inputs.raw_inputs) == 9
    assert {item.family_key for item in loaded.bundle.aggregation.families} == {
        "single_signal_identity",
        "flat_equal_weight_mean",
        "hierarchical_weighted_mean",
        "directional_weighted_vote",
    }
    assert len(plan) == len(
        {
            (item.component_kind, item.component_key, item.component_version)
            for item in plan
        }
    )


def test_contracts_forbid_extra_fields_and_unsafe_release_paths() -> None:
    raw = load_catalog_release(MANIFEST).bundle.raw_inputs.raw_inputs[0].model_dump(mode="json")
    raw["unexpected"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RawInputSeed.model_validate(raw)

    release = load_catalog_release(MANIFEST).bundle.release.model_dump(mode="json")
    release["files"][0]["path"] = "../outside.json"
    with pytest.raises(ValidationError, match="safe relative JSON path"):
        CatalogReleaseManifest.model_validate(release)


def test_deterministic_aggregation_rejects_target_and_training_axes() -> None:
    family = load_catalog_release(MANIFEST).bundle.aggregation.families[0].model_dump(mode="json")
    family["targets"] = [
        {
            "target_key": "illegal_target",
            "name": "Illegal target",
            "description": "A deterministic Aggregator cannot declare this axis.",
            "version_number": 1,
            "semantics": {"future_window": 20},
        }
    ]
    with pytest.raises(ValidationError, match="Deterministic aggregation"):
        AggregationFamilySeed.model_validate(family)


def test_processing_graph_rejects_same_stage_fixed_source() -> None:
    document = deepcopy(load_catalog_release(M3_MANIFEST).bundle.model_dump(mode="json"))
    low_illiquidity = next(
        item
        for item in document["processing"]["nodes"]
        if item["node_key"] == "low_illiquidity_quality"
    )
    low_illiquidity["input_bindings"][0]["source_feature_variant_key"] = (
        "return_continuation__w120"
    )
    with pytest.raises(ValidationError, match="earlier stage"):
        CatalogBundle.model_validate(document)


def test_catalog_diff_is_identity_stable_for_the_same_release() -> None:
    diff = diff_catalog_releases(MANIFEST, MANIFEST)

    assert not diff.added
    assert not diff.removed
    assert not diff.changed
    assert len(diff.unchanged) == 67


def test_m5_release_adds_two_strategy_variants_under_one_family() -> None:
    loaded = load_catalog_release(M5_MANIFEST)
    lint = lint_catalog_release(M5_MANIFEST)
    strategies = loaded.bundle.strategy.strategies

    assert lint["status"] == "passed"
    assert lint["component_count"] == 477
    assert {item.family_key for item in strategies} == {"cross_section_rank_top_k"}
    assert {item.variant_key for item in strategies} == {
        "cross_section_rank_top_k_parity",
        "cross_section_rank_top_k_large_cap_parity",
    }
    defenses = {item.variant_key: item for item in loaded.bundle.defense.defenses}
    assert defenses["fixed20_defense"].version_number == 2
    assert defenses["ma200_tiered_defense"].allocation_semantics == {
        "indicator": "spy_close_div_sma200_minus_one",
        "above_upper_budget": 0.0,
        "middle_budget": 0.2,
        "below_lower_budget": 0.4,
        "rebalance_with_strategy": True,
    }


def test_strategy_variants_cannot_redefine_their_shared_family() -> None:
    document = load_catalog_release(M5_MANIFEST).bundle.strategy.model_dump(mode="json")
    document["strategies"][1]["research_hypothesis"] = "different family semantics"

    with pytest.raises(ValidationError, match="Family semantics drift"):
        StrategyCatalog.model_validate(document)


def test_strategy_parameter_presets_are_explicit_variant_owned_components() -> None:
    previous = load_catalog_release(M5_MANIFEST)
    loaded = load_catalog_release(PARAMETER_MANIFEST)
    lint = lint_catalog_release(PARAMETER_MANIFEST)
    diff = diff_catalog_releases(M5_MANIFEST, PARAMETER_MANIFEST)
    presets = loaded.bundle.strategy.parameter_presets

    assert previous.bundle.strategy.parameter_presets == []
    assert previous.bundle.strategy.strategies == loaded.bundle.strategy.strategies
    assert lint["status"] == "passed"
    assert lint["component_count"] == 487
    assert {
        (item.strategy_variant_key, item.preset_key)
        for item in presets
    } == {
        ("cross_section_rank_top_k_parity", "k1"),
        ("cross_section_rank_top_k_parity", "k2"),
        ("cross_section_rank_top_k_parity", "k3"),
        ("cross_section_rank_top_k_large_cap_parity", "k10"),
        ("cross_section_rank_top_k_large_cap_parity", "k20"),
    }
    assert {
        (item.component_kind, item.component_key)
        for item in diff.added
    } == {
        (kind, f"{variant}__{preset}")
        for kind in (
            "strategy_parameter_preset_definition",
            "strategy_parameter_preset_version",
        )
        for variant, preset in (
            ("cross_section_rank_top_k_parity", "k1"),
            ("cross_section_rank_top_k_parity", "k2"),
            ("cross_section_rank_top_k_parity", "k3"),
            ("cross_section_rank_top_k_large_cap_parity", "k10"),
            ("cross_section_rank_top_k_large_cap_parity", "k20"),
        )
    }
    assert not diff.removed
    assert not diff.changed


def test_strategy_parameter_presets_reject_unknown_parent_and_owner_local_duplicate() -> None:
    document = load_catalog_release(PARAMETER_MANIFEST).bundle.strategy.model_dump(mode="json")
    document["parameter_presets"][0]["strategy_variant_key"] = "unknown_strategy"
    with pytest.raises(ValidationError, match="unknown Variants"):
        StrategyCatalog.model_validate(document)

    duplicate = load_catalog_release(PARAMETER_MANIFEST).bundle.strategy.model_dump(mode="json")
    duplicate["parameter_presets"].append(deepcopy(duplicate["parameter_presets"][0]))
    with pytest.raises(ValidationError, match="strategy parameter preset"):
        StrategyCatalog.model_validate(duplicate)


def test_catalog_publish_rejects_actor_claim_not_bound_to_auth_context() -> None:
    engine = Mock(spec=Engine)
    context = CatalogPublicationContext(
        actor_key="request_claimed_actor",
        reviewer_actor="local_researcher",
        trusted_local_authorization_bootstrap=True,
    )

    with pytest.raises(ValueError, match="Authenticated publisher"):
        publish_catalog_release(engine, MANIFEST, context=context)

    engine.begin.assert_not_called()
