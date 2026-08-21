from __future__ import annotations

from pathlib import Path

from style_rotation.v022.catalog import (
    diff_catalog_releases,
    lint_catalog_release,
    load_catalog_release,
)

PREVIOUS_MANIFEST = Path("v0.22/catalogs/releases/catalog_release.v0.22.7.json")
MANIFEST = Path("v0.22/catalogs/releases/catalog_release.v0.22.8.json")


def test_native_hierarchical_catalog_adds_taxonomy_without_mutating_legacy_presets() -> None:
    previous = load_catalog_release(PREVIOUS_MANIFEST)
    current = load_catalog_release(MANIFEST)
    lint = lint_catalog_release(MANIFEST)
    taxonomy = current.bundle.aggregation.feature_taxonomy
    assert taxonomy is not None

    assert lint["status"] == "passed"
    assert lint["component_count"] == 507
    assert len(taxonomy.entries) == 27
    assert {
        item.research_dimension_key
        for item in taxonomy.entries
        if item.native_hierarchical_eligible
    } == {"liquidity", "momentum_trend", "relative_strength", "reversal", "risk", "tail_shape"}
    assert all(
        item.accepted_units == ["centered_rank"]
        and item.accepted_directions == ["higher_is_better"]
        for item in taxonomy.entries
        if item.native_hierarchical_eligible
    )

    old_hierarchical = next(
        item for item in previous.bundle.aggregation.families
        if item.family_key == "hierarchical_weighted_mean"
    )
    new_hierarchical = next(
        item for item in current.bundle.aggregation.families
        if item.family_key == "hierarchical_weighted_mean"
    )
    assert new_hierarchical.version_number == old_hierarchical.version_number + 1
    assert new_hierarchical.parameter_presets[:-1] == old_hierarchical.parameter_presets
    assert new_hierarchical.parameter_presets[-1].preset_key == (
        "active_dimension_equal_component_equal_v1"
    )
    old_directional = next(
        item for item in previous.bundle.aggregation.families
        if item.family_key == "directional_weighted_vote"
    )
    new_directional = next(
        item for item in current.bundle.aggregation.families
        if item.family_key == "directional_weighted_vote"
    )
    assert new_directional.version_number == old_directional.version_number + 1
    assert new_directional.implementation_key == old_directional.implementation_key

    diff = diff_catalog_releases(PREVIOUS_MANIFEST, MANIFEST)
    assert {
        (item.component_kind, item.component_key, item.component_version)
        for item in diff.removed
    } == {
        ("aggregation_version", "directional_weighted_vote", 1),
        ("aggregation_version", "hierarchical_weighted_mean", 1),
    }
    assert {
        (item.component_kind, item.component_key)
        for item in diff.added
    } >= {
        (
            "aggregation_feature_taxonomy_version",
            "native_hierarchical_research_dimensions_v1",
        ),
        (
            "aggregation_parameter_preset_definition",
            "hierarchical_weighted_mean__active_dimension_equal_component_equal_v1",
        ),
        (
            "aggregation_parameter_preset_version",
            "hierarchical_weighted_mean__active_dimension_equal_component_equal_v1",
        ),
    }


def test_event_and_state_scores_require_published_calibration() -> None:
    taxonomy = load_catalog_release(MANIFEST).bundle.aggregation.feature_taxonomy
    assert taxonomy is not None
    entries = {item.feature_family_key: item for item in taxonomy.entries}

    for family_key in (
        "golden_cross_event",
        "death_cross_event",
        "price_above_ma_state",
        "rsi_oversold_state",
    ):
        assert entries[family_key].native_hierarchical_eligible is False
        assert entries[family_key].accepted_units in (["event_score"], ["state_score"])
