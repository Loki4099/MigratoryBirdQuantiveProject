from __future__ import annotations

from pathlib import Path

from style_rotation.v022.catalog import catalog_component_plan, load_catalog_release
from style_rotation.v022.migration import load_migration_registry
from style_rotation.v022.workspace_view import (
    ExplicitFeature,
    GraphWorkspacePreviewService,
    WorkspacePreviewIntent,
)

MANIFEST = Path("v0.22/catalogs/releases/catalog_release.v0.22.2.json")
REGISTRY = Path("v0.22/m4/migration-registry.v0.22.3.json")


def test_m4_catalog_contains_every_registry_mapping_and_preserves_m3_nodes() -> None:
    loaded = load_catalog_release(MANIFEST)
    registry = load_migration_registry(REGISTRY)
    nodes = loaded.bundle.processing.nodes
    outputs = {
        output.variant_key: (node.stage_no, output.family_key)
        for node in nodes
        for output in node.output_features
    }

    assert len(nodes) == 80
    assert len(outputs) == 82
    assert len(catalog_component_plan(loaded.bundle)) == 475
    assert all(
        outputs[item.mapping.variant_key] == (item.mapping.origin_stage, item.mapping.family_key)
        for item in registry.records
    )
    assert (
        next(
            node for node in nodes if node.variant_key == "return_continuation_node__w120"
        ).stage_no
        == 3
    )
    assert (
        next(
            node for node in nodes if node.variant_key == "price_cross_above_ma_node__s1_l200"
        ).stage_no
        == 2
    )


def test_all_51_legacy_signals_are_aggregation_ready_at_stage_three() -> None:
    registry = load_migration_registry(REGISTRY)
    signal_keys = tuple(
        item.mapping.variant_key
        for item in registry.records
        if item.component_kind == "signal_version"
    )
    service = GraphWorkspacePreviewService.from_manifest(MANIFEST)
    preview = service.preview(
        WorkspacePreviewIntent(
            explicit_features=tuple(ExplicitFeature(key, 3) for key in signal_keys),
            aggregation_family_keys=("flat_equal_weight_mean",),
            frequency="weekly",
            aggregation_parameter_preset_keys=(("flat_equal_weight_mean", ("signal_equal_v1",)),),
        )
    )

    assert preview["aggregation_inputs"] == sorted(signal_keys)
    assert preview["summary"]["stage3_input_count"] == 51
    assert preview["summary"]["explicit_count"] == 51
    assert preview["summary"]["required_count"] > 51
    assert preview["blockers"] == []
