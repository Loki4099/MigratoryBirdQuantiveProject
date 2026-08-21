from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from style_rotation.v022.draft_service import (
    GraphCatalogRebaseRequired,
    GraphDraftService,
    GraphDraftSnapshot,
    GraphWorkspaceViewIncompatible,
)
from style_rotation.v022.workspace_view import (
    ExplicitFeature,
    GraphWorkspacePreviewService,
    WorkspacePreviewIntent,
    representative_workspace_service,
)

ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.1.json"
CURRENT_MANIFEST = ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.4.json"
DEFENSE_MANIFEST = ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.5.json"
RUNTIME_MANIFEST = ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.13.json"


def _service() -> GraphWorkspacePreviewService:
    return GraphWorkspacePreviewService.from_manifest(MANIFEST)


def _runtime_draft_snapshot() -> tuple[
    GraphWorkspacePreviewService, GraphDraftSnapshot
]:
    workspace = GraphWorkspacePreviewService.from_manifest(RUNTIME_MANIFEST)
    intent = {
        "explicit_features": [],
        "aggregation_family_keys": [],
        "aggregation_parameter_preset_keys": {},
        "strategy_keys": ["cross_section_rank_top_k_parity"],
        "strategy_parameter_preset_keys": {},
        "defense_keys": ["none"],
        "frequency": "weekly",
    }
    derived = workspace.preview(
        WorkspacePreviewIntent((), (), "weekly"),
        asset_context={},
    )
    release_id = uuid.uuid4()
    return workspace, GraphDraftSnapshot(
        uuid.uuid4(),
        release_id,
        "legacy_presentation",
        "Legacy presentation",
        1,
        "active",
        {},
        {},
        intent,
        derived,
    )


def _without_presentation_fields(derived: dict[str, Any]) -> dict[str, Any]:
    legacy = deepcopy(derived)
    for stage in legacy["stages"]:
        for family in stage["families"]:
            for variant in family["variants"]:
                for key in (
                    "formula_identity",
                    "semantic_role",
                    "unit",
                    "input_feature_keys",
                    "output_semantics",
                ):
                    variant.pop(key)
    return legacy


def test_legacy_stage_presentation_is_enriched_without_identity_mutation() -> None:
    workspace, current = _runtime_draft_snapshot()
    legacy_view = _without_presentation_fields(current.derived_view)
    snapshot = GraphDraftSnapshot(
        current.graph_draft_id,
        current.catalog_release_id,
        current.draft_key,
        current.name,
        current.revision,
        current.status,
        current.asset_context,
        current.resolved_data_binding,
        current.intent,
        legacy_view,
    )
    service = GraphDraftService(MagicMock(), workspace, compiler=MagicMock())

    enriched = service._stage_derived_view(
        snapshot,
        current_catalog_release_id=current.catalog_release_id,
    )

    first_variant = enriched["stages"][0]["families"][0]["variants"][0]
    assert first_variant["formula_identity"]
    assert enriched["selection_fingerprint"] == legacy_view["selection_fingerprint"]
    assert enriched["derived_state_fingerprint"] == legacy_view["derived_state_fingerprint"]
    assert "formula_identity" not in legacy_view["stages"][0]["families"][0]["variants"][0]


def test_current_draft_refreshes_derivable_presentation_without_writing_revision() -> None:
    workspace, current = _runtime_draft_snapshot()
    stale_view = deepcopy(current.derived_view)
    stale_view["defenses"][0]["compatible"] = False
    stale_view["defenses"][0]["reason_codes"] = ["asset_context_unsupported"]
    stale = GraphDraftSnapshot(
        current.graph_draft_id,
        current.catalog_release_id,
        current.draft_key,
        current.name,
        current.revision,
        current.status,
        current.asset_context,
        current.resolved_data_binding,
        current.intent,
        stale_view,
    )
    service = GraphDraftService(MagicMock(), workspace, compiler=MagicMock())
    service._catalog_release_id = MagicMock(return_value=current.catalog_release_id)  # type: ignore[method-assign]
    service._derive = MagicMock(return_value=current.derived_view)  # type: ignore[method-assign]

    refreshed = service._presentation_snapshot(MagicMock(), stale)

    assert refreshed.revision == stale.revision
    assert refreshed.derived_view["defenses"][0]["compatible"] is True
    assert stale.derived_view["defenses"][0]["compatible"] is False


def test_stale_catalog_draft_adds_only_legacy_aggregation_presentation() -> None:
    workspace, current = _runtime_draft_snapshot()
    legacy_view = deepcopy(current.derived_view)
    for option in legacy_view["aggregations"]:
        option.pop("internal_member_count")
        for preset in option["parameter_preset_definitions"]:
            preset.pop("selectable")
            preset.pop("reason_codes")
    stale = replace(current, derived_view=legacy_view)
    service = GraphDraftService(MagicMock(), workspace, compiler=MagicMock())
    service._catalog_release_id = MagicMock(return_value=uuid.uuid4())  # type: ignore[method-assign]

    restored = service._presentation_snapshot(MagicMock(), stale)

    assert restored.revision == stale.revision
    assert restored.catalog_release_id == stale.catalog_release_id
    assert restored.derived_view["selection_fingerprint"] == legacy_view["selection_fingerprint"]
    assert (
        restored.derived_view["derived_state_fingerprint"]
        == legacy_view["derived_state_fingerprint"]
    )
    assert all(
        option["internal_member_count"] == 0
        and all(
            preset["selectable"] is True and preset["reason_codes"] == []
            for preset in option["parameter_preset_definitions"]
        )
        for option in restored.derived_view["aggregations"]
    )
    assert "internal_member_count" not in legacy_view["aggregations"][0]


def test_legacy_stage_presentation_fails_closed_on_identity_or_catalog_drift() -> None:
    workspace, current = _runtime_draft_snapshot()
    legacy_view = _without_presentation_fields(current.derived_view)
    legacy_view["derived_state_fingerprint"] = "0" * 64
    snapshot = GraphDraftSnapshot(
        current.graph_draft_id,
        current.catalog_release_id,
        current.draft_key,
        current.name,
        current.revision,
        current.status,
        current.asset_context,
        current.resolved_data_binding,
        current.intent,
        legacy_view,
    )
    service = GraphDraftService(MagicMock(), workspace, compiler=MagicMock())

    with pytest.raises(GraphWorkspaceViewIncompatible) as mismatch:
        service._stage_derived_view(
            snapshot,
            current_catalog_release_id=current.catalog_release_id,
        )
    assert mismatch.value.reason_code == "workspace_view_identity_mismatch"

    with pytest.raises(GraphCatalogRebaseRequired):
        service._stage_derived_view(
            snapshot,
            current_catalog_release_id=uuid.uuid4(),
        )


def test_production_workspace_uses_runtime_catalog_without_mutating_old_intent() -> None:
    production = representative_workspace_service()
    expected = GraphWorkspacePreviewService.from_manifest(RUNTIME_MANIFEST)
    old_intent = {
        "explicit_features": [
            {"feature_key": "return_continuation__w120", "stage_no": 3}
        ],
        "aggregation_family_keys": ["flat_equal_weight_mean"],
        "aggregation_parameter_preset_keys": {
            "flat_equal_weight_mean": ["signal_equal_v1"]
        },
        "strategy_keys": ["cross_section_rank_top_k_parity"],
        "strategy_parameter_preset_keys": {
            "cross_section_rank_top_k_parity": ["k2"]
        },
        "defense_keys": ["none"],
    }
    frozen_old_intent = {
        key: value.copy() if isinstance(value, list) else value.copy()
        for key, value in old_intent.items()
    }

    rebased, removals = production.rebase_intent(old_intent)

    assert production.catalog_identity() == expected.catalog_identity()
    assert production.catalog_identity()["catalog_version"] == "0.22.13"
    assert old_intent == frozen_old_intent
    assert rebased == old_intent
    assert all(not removed for removed in removals.values())


def _variant(view: dict[str, object], stage: int, key: str) -> dict[str, object]:
    stages = view["stages"]
    assert isinstance(stages, list)
    stage_view = stages[stage]
    for family in stage_view["families"]:
        for variant in family["variants"]:
            if variant["feature_key"] == key:
                return variant
    raise AssertionError(f"variant not found: {key}@{stage}")


def test_reverse_stage3_selection_lights_ancestors_and_multi_outputs() -> None:
    view = _service().preview(
        WorkspacePreviewIntent(
            explicit_features=(ExplicitFeature("low_illiquidity_quality__w20", 3),),
            aggregation_family_keys=("flat_equal_weight_mean",),
            frequency="weekly",
            aggregation_parameter_preset_keys=(("flat_equal_weight_mean", ("signal_equal_v1",)),),
        )
    )

    assert view["aggregation_inputs"] == ["low_illiquidity_quality__w20"]
    assert view["blockers"] == []
    assert view["summary"] == {
        "explicit_count": 1,
        "required_count": 7,
        "stage3_input_count": 1,
        "aggregation_instance_count": 1,
        "strategy_branch_count": 1,
        "backtest_cell_count": 7,
    }
    assert view["resources"]["state"] == "accepted"
    adjusted = _variant(view, 0, "adjusted_close")
    assert adjusted["is_required"] is True
    assert adjusted["lock_state"] == "locked"
    sibling = _variant(view, 1, "dollar_volume__close_times_volume")
    assert sibling["is_present"] is True
    assert sibling["reason_codes"] == ["co_produced_output"]
    assert sibling["formula_identity"] == "close_raw[t]*volume_raw[t]"
    assert sibling["input_feature_keys"] == ["adjusted_close", "close_raw", "volume_raw"]
    assert sibling["semantic_role"] == "trading_value"
    assert sibling["unit"] == "currency"
    assert sibling["output_semantics"] == {"continuous": True, "non_negative": True}


def test_raw_inputs_expose_exact_source_definition_for_processing_one() -> None:
    view = _service().preview(
        WorkspacePreviewIntent(
            (),
            ("flat_equal_weight_mean",),
            "weekly",
            (("flat_equal_weight_mean", ("signal_equal_v1",)),),
        )
    )

    adjusted = _variant(view, 1, "adjusted_close")
    assert adjusted["origin_stage"] == 0
    assert adjusted["formula_identity"] == "vendor total-return adjusted close"
    assert adjusted["input_feature_keys"] == []
    assert adjusted["semantic_role"] == "adjusted_market_close"
    assert adjusted["unit"] == "price"
    assert adjusted["output_semantics"] == {
        "source_series_key": "us_etf_daily_market",
        "source_field": "adj_close",
    }


def test_upstream_selection_makes_reachable_downstream_ready_without_selecting_it() -> None:
    empty = _service().preview(
        WorkspacePreviewIntent(
            (),
            ("flat_equal_weight_mean",),
            "weekly",
            (("flat_equal_weight_mean", ("signal_equal_v1",)),),
        )
    )
    selected = _service().preview(
        WorkspacePreviewIntent(
            (ExplicitFeature("adjusted_close", 0),),
            ("flat_equal_weight_mean",),
            "weekly",
            (("flat_equal_weight_mean", ("signal_equal_v1",)),),
        )
    )

    before = _variant(empty, 1, "total_return__w120")
    after = _variant(selected, 1, "total_return__w120")
    assert before["availability"] == "requires_ancestors"
    assert after["availability"] == "ready"
    assert after["is_present"] is False


def test_unpublished_raw_projection_is_absent_from_final_signal_stage() -> None:
    view = _service().preview(
        WorkspacePreviewIntent(
            (),
            ("flat_equal_weight_mean",),
            "weekly",
            (("flat_equal_weight_mean", ("signal_equal_v1",)),),
        )
    )
    final_signal = _variant(view, 3, "return_continuation__w120")

    with pytest.raises(AssertionError, match="variant not found: adjusted_close@3"):
        _variant(view, 3, "adjusted_close")
    assert final_signal["availability"] == "requires_ancestors"


def test_aggregation_options_expose_published_rules_and_preset_definitions() -> None:
    view = _service().preview(
        WorkspacePreviewIntent(
            (),
            ("flat_equal_weight_mean",),
            "weekly",
            (("flat_equal_weight_mean", ("signal_equal_v1",)),),
        )
    )

    aggregation = next(
        item for item in view["aggregations"] if item["family_key"] == "flat_equal_weight_mean"
    )
    assert aggregation["algorithm_identity"] == "Q18(sum(x_i/n)) in explicit input order"
    assert aggregation["input_payload_contract_key"] == "final_signal_numeric"
    assert aggregation["output_payload_contract_key"] == "final_signal_numeric"
    assert aggregation["missing_policy"] == {"mode": "published_complete_case_policy"}
    assert aggregation["parameter_preset_definitions"] == [
        {
            "preset_key": "signal_equal_v1",
            "name": "Signal equal weight",
            "description": "v0.21 Workspace signal-equal compatibility preset.",
            "version_number": 1,
            "semantics": {
                "weight_policy": "equal_direct_inputs",
                "quantum": "1e-18",
                "rounding": "half_even",
            },
            "selected": True,
            "selectable": True,
            "reason_codes": [],
        }
    ]


def test_selected_families_and_variants_are_sorted_first() -> None:
    view = _service().preview(
        WorkspacePreviewIntent(
            (ExplicitFeature("volume_raw", 0),),
            ("flat_equal_weight_mean",),
            "weekly",
            (("flat_equal_weight_mean", ("signal_equal_v1",)),),
        )
    )
    stage = view["stages"][0]
    assert stage["families"][0]["family_key"] == "volume_raw"
    assert stage["families"][0]["pinned"] is True


def test_shared_ancestor_lists_every_explicit_downstream_consumer() -> None:
    view = _service().preview(
        WorkspacePreviewIntent(
            (
                ExplicitFeature("return_continuation__w120", 3),
                ExplicitFeature("price_cross_above_ma__s1_l200", 3),
                ExplicitFeature("low_illiquidity_quality__w20", 3),
            ),
            ("flat_equal_weight_mean",),
            "weekly",
            (("flat_equal_weight_mean", ("signal_equal_v1",)),),
        )
    )
    adjusted = _variant(view, 0, "adjusted_close")

    assert adjusted["locked_by"] == [
        "low_illiquidity_quality__w20@3",
        "price_cross_above_ma__s1_l200@3",
        "return_continuation__w120@3",
    ]


def test_branch_axes_change_derived_identity_and_exact_resource_estimate() -> None:
    base = WorkspacePreviewIntent(
        (ExplicitFeature("return_continuation__w120", 3),),
        ("flat_equal_weight_mean",),
        "weekly",
        (("flat_equal_weight_mean", ("signal_equal_v1",)),),
    )
    expanded = WorkspacePreviewIntent(
        base.explicit_features,
        ("hierarchical_weighted_mean",),
        base.frequency,
        ((
            "hierarchical_weighted_mean",
            ("legacy_dimension_equal_v1", "legacy_trend_tilt_v1"),
        ),),
    )

    base_view = _service().preview(base)
    expanded_view = _service().preview(expanded)

    assert base_view["derived_state_fingerprint"] != expanded_view["derived_state_fingerprint"]
    assert expanded_view["summary"]["strategy_branch_count"] == 2
    assert expanded_view["summary"]["backtest_cell_count"] == 14
    assert expanded_view["resources"]["estimates"]["work_items"] == 14


def test_single_available_aggregation_preset_still_requires_explicit_selection() -> None:
    view = _service().preview(
        WorkspacePreviewIntent(
            (ExplicitFeature("return_continuation__w120", 3),),
            ("flat_equal_weight_mean",),
            "weekly",
        )
    )

    assert view["blockers"] == [
        {
            "layer": "aggregation",
            "object_key": "flat_equal_weight_mean",
            "reason_codes": ["aggregation_parameter_preset_required"],
        }
    ]


def test_legacy_hierarchical_preset_requires_an_exact_frozen_recipe() -> None:
    service = GraphWorkspacePreviewService.from_manifest(RUNTIME_MANIFEST)
    inputs = (
        "high_kurtosis_tail_regime__w120",
        "high_skew_regime__w60",
        "return_continuation__w252",
    )
    view = service.preview(
        WorkspacePreviewIntent(
            tuple(ExplicitFeature(key, 3) for key in inputs),
            ("hierarchical_weighted_mean",),
            "weekly",
            (("hierarchical_weighted_mean", ("legacy_dimension_equal_v1",)),),
        )
    )

    blocker = next(
        item
        for item in view["blockers"]
        if item["object_key"]
        == "hierarchical_weighted_mean:legacy_dimension_equal_v1"
    )
    assert blocker == {
        "layer": "aggregation",
        "object_key": "hierarchical_weighted_mean:legacy_dimension_equal_v1",
        "reason_codes": ["aggregation_recipe_unavailable"],
        "feature_keys": sorted(inputs),
    }
    aggregation = next(
        item
        for item in view["aggregations"]
        if item["family_key"] == "hierarchical_weighted_mean"
    )
    preset = next(
        item
        for item in aggregation["parameter_preset_definitions"]
        if item["preset_key"] == "legacy_dimension_equal_v1"
    )
    assert preset["selected"] is True
    assert preset["selectable"] is False
    assert preset["reason_codes"] == ["aggregation_recipe_unavailable"]


def test_exact_legacy_hierarchical_recipe_is_selectable_before_compile() -> None:
    service = GraphWorkspacePreviewService.from_manifest(RUNTIME_MANIFEST)
    inputs = (
        "return_continuation__w120",
        "lagged_return_continuation__l252_s20",
        "ma_trend_strength__s50_l200",
        "ppo_trend_acceleration__f12_s26_g9",
    )
    view = service.preview(
        WorkspacePreviewIntent(
            tuple(ExplicitFeature(key, 3) for key in inputs),
            ("hierarchical_weighted_mean",),
            "weekly",
            (("hierarchical_weighted_mean", ("legacy_dimension_equal_v1",)),),
        )
    )

    assert not any(
        "aggregation_recipe_unavailable" in item["reason_codes"]
        for item in view["blockers"]
    )
    aggregation = next(
        item
        for item in view["aggregations"]
        if item["family_key"] == "hierarchical_weighted_mean"
    )
    preset = next(
        item
        for item in aggregation["parameter_preset_definitions"]
        if item["preset_key"] == "legacy_dimension_equal_v1"
    )
    assert preset["selectable"] is True
    assert preset["reason_codes"] == []


def test_native_hierarchical_preset_accepts_centered_rank_inputs_by_taxonomy() -> None:
    service = GraphWorkspacePreviewService.from_manifest(RUNTIME_MANIFEST)
    inputs = (
        "return_continuation__w120",
        "lagged_return_continuation__l252_s20",
        "low_volatility__w20",
    )
    view = service.preview(
        WorkspacePreviewIntent(
            tuple(ExplicitFeature(key, 3) for key in inputs),
            ("hierarchical_weighted_mean",),
            "weekly",
            ((
                "hierarchical_weighted_mean",
                ("active_dimension_equal_component_equal_v1",),
            ),),
        )
    )

    preset = next(
        item
        for aggregation in view["aggregations"]
        if aggregation["family_key"] == "hierarchical_weighted_mean"
        for item in aggregation["parameter_preset_definitions"]
        if item["preset_key"] == "active_dimension_equal_component_equal_v1"
    )
    assert preset["selected"] is True
    assert preset["selectable"] is True
    assert preset["reason_codes"] == []
    assert not any(
        item["object_key"].endswith("active_dimension_equal_component_equal_v1")
        for item in view["blockers"]
    )


def test_native_hierarchical_preset_rejects_uncalibrated_event_score() -> None:
    service = GraphWorkspacePreviewService.from_manifest(RUNTIME_MANIFEST)
    inputs = ("return_continuation__w120", "golden_cross_event__s50_l200")
    view = service.preview(
        WorkspacePreviewIntent(
            tuple(ExplicitFeature(key, 3) for key in inputs),
            ("hierarchical_weighted_mean",),
            "weekly",
            ((
                "hierarchical_weighted_mean",
                ("active_dimension_equal_component_equal_v1",),
            ),),
        )
    )

    blocker = next(
        item
        for item in view["blockers"]
        if item["object_key"].endswith(
            "active_dimension_equal_component_equal_v1"
        )
    )
    assert blocker["reason_codes"] == ["aggregation_native_calibration_required"]
    assert blocker["feature_keys"] == sorted(inputs)


def test_selected_strategy_reports_frequency_incompatibility() -> None:
    view = _service().preview(
        WorkspacePreviewIntent(
            (ExplicitFeature("return_continuation__w120", 3),),
            ("flat_equal_weight_mean",),
            "daily",
            (("flat_equal_weight_mean", ("signal_equal_v1",)),),
        )
    )

    assert view["blockers"] == [
        {
            "layer": "strategy",
            "object_key": "cross_section_rank_top_k_parity",
            "reason_codes": ["frequency_unsupported"],
        }
    ]
    assert view["strategies"][0]["compatible"] is False


def test_strategy_presets_expand_branches_and_use_frozen_asset_context_legality() -> None:
    service = GraphWorkspacePreviewService.from_manifest(CURRENT_MANIFEST)
    asset_context = {
        "members": [
            {"security_key": key, "instrument_type": "etf"} for key in ("iwf", "iwd", "iwo", "iwn")
        ]
    }
    view = service.preview(
        WorkspacePreviewIntent(
            (ExplicitFeature("return_continuation__w120", 3),),
            ("flat_equal_weight_mean",),
            "weekly",
            (("flat_equal_weight_mean", ("signal_equal_v1",)),),
            ("cross_section_rank_top_k_parity",),
            ("none",),
            (("cross_section_rank_top_k_parity", ("k1", "k2")),),
        ),
        asset_context=asset_context,
    )

    assert view["blockers"] == []
    assert view["summary"]["strategy_branch_count"] == 2
    assert view["strategies"][0]["variant_key"] == "cross_section_rank_top_k_parity"
    assert view["strategies"][0]["selection_semantics"]["ranking"] == (
        "descending_higher_is_better"
    )
    assert view["strategies"][0]["execution_policy"]["weighting"] == (
        "equal_selected_assets"
    )
    assert view["strategies"][0]["research_hypothesis"].startswith(
        "A directionally stable cross-sectional score"
    )
    assert [item["preset_key"] for item in view["strategies"][0]["parameter_presets"][:2]] == [
        "k1",
        "k2",
    ]
    large_cap = next(
        item
        for item in view["strategies"]
        if item["variant_key"] == "cross_section_rank_top_k_large_cap_parity"
    )
    assert all(not preset["selectable"] for preset in large_cap["parameter_presets"])
    assert large_cap["parameter_presets"][0]["reason_codes"] == [
        "asset_context_instrument_type_unsupported",
        "insufficient_eligible_assets",
    ]


def test_current_strategy_variant_requires_an_explicit_parameter_preset() -> None:
    service = GraphWorkspacePreviewService.from_manifest(CURRENT_MANIFEST)
    view = service.preview(
        WorkspacePreviewIntent(
            (ExplicitFeature("return_continuation__w120", 3),),
            ("flat_equal_weight_mean",),
            "weekly",
            (("flat_equal_weight_mean", ("signal_equal_v1",)),),
        )
    )
    assert {
        "layer": "strategy",
        "object_key": "cross_section_rank_top_k_parity",
        "reason_codes": ["strategy_parameter_preset_required"],
    } in view["blockers"]


def test_selected_strategy_preset_requires_the_frozen_asset_context() -> None:
    service = GraphWorkspacePreviewService.from_manifest(CURRENT_MANIFEST)
    view = service.preview(
        WorkspacePreviewIntent(
            (ExplicitFeature("return_continuation__w120", 3),),
            ("flat_equal_weight_mean",),
            "weekly",
            (("flat_equal_weight_mean", ("signal_equal_v1",)),),
            ("cross_section_rank_top_k_parity",),
            ("none",),
            (("cross_section_rank_top_k_parity", ("k2",)),),
        )
    )
    assert {
        "layer": "strategy",
        "object_key": "cross_section_rank_top_k_parity:k2",
        "reason_codes": ["asset_context_required"],
    } in view["blockers"]


def test_retired_defense_catalog_projects_only_no_defense() -> None:
    service = GraphWorkspacePreviewService.from_manifest(DEFENSE_MANIFEST)
    asset_context = {
        "asset_context_key": "us_style_rotation_4_etf_sample_v1",
        "asset_registry_catalog_version": "0.21.1",
        "members": [
            {"security_key": key, "instrument_type": "etf"} for key in ("iwf", "iwd", "iwo", "iwn")
        ],
    }
    view = service.preview(
        WorkspacePreviewIntent(
            (ExplicitFeature("return_continuation__w120", 3),),
            ("flat_equal_weight_mean",),
            "weekly",
            (("flat_equal_weight_mean", ("signal_equal_v1",)),),
            ("cross_section_rank_top_k_parity",),
            ("none",),
            (("cross_section_rank_top_k_parity", ("k2",)),),
        ),
        asset_context=asset_context,
    )

    assert view["blockers"] == []
    assert [item["variant_key"] for item in view["defenses"]] == ["none"]
    assert view["defenses"][0]["selected"] is True


@pytest.mark.parametrize("defense_key", ["fixed20_defense", "ma200_tiered_defense"])
def test_retired_defense_is_rejected_before_preview(defense_key: str) -> None:
    service = GraphWorkspacePreviewService.from_manifest(DEFENSE_MANIFEST)
    with pytest.raises(ValueError, match=f"Unknown Defense: {defense_key}"):
        service.preview(
            WorkspacePreviewIntent(
                (ExplicitFeature("return_continuation__w120", 3),),
                ("flat_equal_weight_mean",),
                "weekly",
                (("flat_equal_weight_mean", ("signal_equal_v1",)),),
                ("cross_section_rank_top_k_parity",),
                (defense_key,),
                (("cross_section_rank_top_k_parity", ("k2",)),),
            )
        )


def test_bulk_selection_events_update_each_axis_in_one_intent_change() -> None:
    workspace, snapshot = _runtime_draft_snapshot()
    service = GraphDraftService(
        MagicMock(),
        workspace,
        compiler=MagicMock(),
        context_resolver=MagicMock(),
    )
    derived = deepcopy(snapshot.derived_view)
    derived["strategies"] = [
        {
            "variant_key": "strategy_a",
            "parameter_presets": [
                {"preset_key": "a1", "selectable": True},
                {"preset_key": "a2", "selectable": False},
            ],
        },
        {
            "variant_key": "strategy_b",
            "parameter_presets": [{"preset_key": "b1", "selectable": True}],
        },
    ]
    derived["defenses"] = [
        {"variant_key": "none", "compatible": True},
        {"variant_key": "fixed20", "compatible": True},
        {"variant_key": "unsupported", "compatible": False},
    ]
    current = GraphDraftSnapshot(
        snapshot.graph_draft_id,
        snapshot.catalog_release_id,
        snapshot.draft_key,
        snapshot.name,
        snapshot.revision,
        "draft",
        snapshot.asset_context,
        snapshot.resolved_data_binding,
        deepcopy(snapshot.intent),
        derived,
    )

    strategies = service._mutate(
        current, "select_all_compatible_strategy_presets", {}
    )
    assert strategies["strategy_keys"] == ["strategy_a", "strategy_b"]
    assert strategies["strategy_parameter_preset_keys"] == {
        "strategy_a": ["a1"],
        "strategy_b": ["b1"],
    }
    defenses = service._mutate(current, "select_all_compatible_defenses", {})
    assert defenses["defense_keys"] == ["none"]
    with pytest.raises(ValueError, match="defense_retired"):
        service._mutate(
            current,
            "select_defense",
            {"defense_key": "fixed20_defense"},
        )

    selected = service._mutate(
        current, "select_all_legal_feature_occurrences", {"stage_no": 1}
    )
    expected = {
        (variant["feature_key"], 1)
        for family in derived["stages"][1]["families"]
        for variant in family["variants"]
        if variant["availability"] != "hard_incompatible"
    }
    assert {
        (item["feature_key"], item["stage_no"])
        for item in selected["explicit_features"]
    } == expected
