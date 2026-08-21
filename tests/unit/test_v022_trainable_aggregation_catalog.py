from __future__ import annotations

from pathlib import Path

from style_rotation.v022.catalog import lint_catalog_release, load_catalog_release
from style_rotation.v022.workspace_view import (
    ExplicitFeature,
    GraphWorkspacePreviewService,
    WorkspacePreviewIntent,
)

MANIFEST = Path("v0.22/catalogs/releases/catalog_release.v0.22.13.json")


def test_trainable_catalog_inherits_deterministic_families_and_freezes_axes() -> None:
    loaded = load_catalog_release(MANIFEST)
    families = {item.family_key: item for item in loaded.bundle.aggregation.families}

    assert set(families) == {
        "single_signal_identity",
        "flat_equal_weight_mean",
        "hierarchical_weighted_mean",
        "directional_weighted_vote",
        "ols_cross_sectional_regression",
        "ridge_cross_sectional_regression",
        "random_forest_cross_sectional_regression",
        "lightgbm_cross_sectional_regression",
        "xgboost_cross_sectional_regression",
    }
    for key in (
        "ols_cross_sectional_regression",
        "ridge_cross_sectional_regression",
        "random_forest_cross_sectional_regression",
        "lightgbm_cross_sectional_regression",
        "xgboost_cross_sectional_regression",
    ):
        family = families[key]
        assert family.execution_mode == "supervised"
        assert family.minimum_inputs == 1
        assert family.maximum_inputs == 32
        assert [item.target_key for item in family.targets] == [
            "forward_rank_h5",
            "forward_rank_h10",
            "forward_rank_h21",
        ]
        expected_presets = (
            2
            if key
            in {
                "random_forest_cross_sectional_regression",
                "lightgbm_cross_sectional_regression",
                "xgboost_cross_sectional_regression",
            }
            else 1
        )
        assert len(family.training_presets) == expected_presets
        assert family.training_presets[0].semantics["random_split"] is False
    assert "aggregation/deterministic.v0.22.1.json" in loaded.source_documents
    assert "aggregation/trainable_regression.v0.22.2.json" in loaded.source_documents
    assert "aggregation/trainable_regression.v0.22.3.json" in loaded.source_documents
    assert "aggregation/trainable_regression.v0.22.4.json" in loaded.source_documents
    assert "payload_contracts/trainable_runtime.v0.22.3.json" in loaded.source_documents
    payload_keys = {item.contract_key for item in loaded.bundle.payload.contracts}
    assert {"training_matrix_numeric", "fitted_regression_model"} <= payload_keys
    assert lint_catalog_release(MANIFEST)["status"] == "passed"


def test_supervised_workspace_requires_and_projects_exact_axes() -> None:
    service = GraphWorkspacePreviewService.from_manifest(MANIFEST)
    base = dict(
        explicit_features=(ExplicitFeature("return_continuation__w120", 3),),
        aggregation_family_keys=("ols_cross_sectional_regression",),
        frequency="weekly",
        strategy_keys=("cross_section_rank_top_k_parity",),
        defense_keys=("none",),
        strategy_parameter_preset_keys=(("cross_section_rank_top_k_parity", ("k2",)),),
    )

    incomplete = service.preview(WorkspacePreviewIntent(**base))
    reasons = {
        reason
        for blocker in incomplete["blockers"]
        for reason in blocker["reason_codes"]
    }
    assert reasons >= {
        "aggregation_target_required",
        "aggregation_training_preset_required",
    }

    complete = service.preview(
        WorkspacePreviewIntent(
            **base,
            aggregation_target_keys=(("ols_cross_sectional_regression", ("forward_rank_h5",)),),
            aggregation_training_preset_keys=(
                ("ols_cross_sectional_regression", ("expanding_daily_ols_v1",)),
            ),
        )
    )
    option = next(
        item
        for item in complete["aggregations"]
        if item["family_key"] == "ols_cross_sectional_regression"
    )
    assert option["selected_targets"] == ["forward_rank_h5"]
    assert option["selected_training_presets"] == ["expanding_daily_ols_v1"]
    assert option["internal_member_count"] == 1
    assert complete["summary"]["aggregation_instance_count"] == 1
    assert not {
        "aggregation_target_required",
        "aggregation_training_preset_required",
    } & {
        reason
        for blocker in complete["blockers"]
        for reason in blocker["reason_codes"]
    }


def test_random_forest_workspace_projects_target_preset_cartesian_members() -> None:
    service = GraphWorkspacePreviewService.from_manifest(MANIFEST)
    preview = service.preview(
        WorkspacePreviewIntent(
            explicit_features=(ExplicitFeature("return_continuation__w120", 3),),
            aggregation_family_keys=("random_forest_cross_sectional_regression",),
            frequency="weekly",
            strategy_keys=("cross_section_rank_top_k_parity",),
            defense_keys=("none",),
            strategy_parameter_preset_keys=(
                ("cross_section_rank_top_k_parity", ("k2",)),
            ),
            aggregation_target_keys=(
                (
                    "random_forest_cross_sectional_regression",
                    ("forward_rank_h5", "forward_rank_h21"),
                ),
            ),
            aggregation_training_preset_keys=(
                (
                    "random_forest_cross_sectional_regression",
                    (
                        "expanding_daily_rf_balanced_v1",
                        "expanding_daily_rf_feature_subsample_v1",
                    ),
                ),
            ),
        )
    )

    option = next(
        item
        for item in preview["aggregations"]
        if item["family_key"] == "random_forest_cross_sectional_regression"
    )
    assert option["selected_targets"] == ["forward_rank_h5", "forward_rank_h21"]
    assert option["selected_training_presets"] == [
        "expanding_daily_rf_balanced_v1",
        "expanding_daily_rf_feature_subsample_v1",
    ]
    assert option["internal_member_count"] == 4
    assert preview["summary"]["aggregation_instance_count"] == 1
