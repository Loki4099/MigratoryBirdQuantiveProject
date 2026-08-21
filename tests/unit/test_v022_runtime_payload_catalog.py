from __future__ import annotations

from pathlib import Path

from style_rotation.v022.catalog import (
    catalog_component_plan,
    diff_catalog_releases,
    lint_catalog_release,
    load_catalog_release,
)

PREVIOUS_MANIFEST = Path("v0.22/catalogs/releases/catalog_release.v0.22.5.json")
MANIFEST = Path("v0.22/catalogs/releases/catalog_release.v0.22.6.json")
RUNTIME_CONTRACT_KEYS = {
    "strategy_unit_risk_target",
    "defense_budget_decision",
    "merged_portfolio_target",
    "portfolio_cell_result",
}


def test_runtime_release_adds_four_exact_typed_output_contracts() -> None:
    loaded = load_catalog_release(MANIFEST)
    contracts = {item.contract_key: item for item in loaded.bundle.payload.contracts}

    assert lint_catalog_release(MANIFEST)["component_count"] == 504
    assert contracts.keys() >= RUNTIME_CONTRACT_KEYS
    assert all(contracts[key].version_number == 1 for key in RUNTIME_CONTRACT_KEYS)
    assert all(
        contracts[key].aggregation_role == "not_aggregation_ready"
        for key in RUNTIME_CONTRACT_KEYS
    )
    assert contracts["strategy_unit_risk_target"].schema_document[
        "defense_budget_forbidden"
    ] is True
    assert contracts["defense_budget_decision"].schema_document[
        "none_package_forbidden"
    ] is True
    assert contracts["defense_budget_decision"].schema_document[
        "fixed_budget_nullable_fields"
    ] == ["input_known_at", "indicator_value"]
    assert contracts["defense_budget_decision"].time_axis["known_at_field"] == (
        "decision_cutoff_at"
    )
    assert contracts["defense_budget_decision"].pit_contract[
        "optional_input_known_at_not_after_decision_cutoff"
    ] is True
    assert contracts["merged_portfolio_target"].schema_document[
        "overlap_policy"
    ] == "preserve_attribution_net_by_asset_id"
    assert contracts["merged_portfolio_target"].schema_document[
        "decision_identity_fields"
    ][1:4] == [
        "decision_cutoff_at",
        "input_known_at",
        "compiled_strategy_branch_id",
    ]
    assert contracts["portfolio_cell_result"].schema_document[
        "currency_result_forbidden"
    ] is True
    cell_result = contracts["portfolio_cell_result"]
    assert cell_result.schema_document["execution_identity_fields"] == [
        "work_execution_fingerprint",
        "compiled_strategy_branch_id",
        "configuration_snapshot_id",
        "evaluation_data_context_fingerprint",
    ]
    assert "evaluation_input_cutoff_at" in cell_result.schema_document[
        "evaluation_context_fields"
    ]
    assert cell_result.schema_document["result_envelope_fields"][-2:] == [
        "reason_code",
        "details",
    ]
    assert cell_result.schema_document["plan_specific_cell_identity_forbidden"] is True
    assert cell_result.entity_axis["identity_field"] == "work_execution_fingerprint"
    assert "research_cell_id" not in cell_result.schema_document["path_fields"]


def test_runtime_release_is_additive_and_preserves_v0225_identity() -> None:
    previous = catalog_component_plan(load_catalog_release(PREVIOUS_MANIFEST).bundle)
    current = catalog_component_plan(load_catalog_release(MANIFEST).bundle)
    diff = diff_catalog_releases(PREVIOUS_MANIFEST, MANIFEST)

    assert len(previous) == 496
    assert len(current) == 504
    assert not diff.removed
    assert not diff.changed
    assert {
        (item.component_kind, item.component_key, item.component_version)
        for item in diff.added
    } == {
        (kind, key, 1)
        for key in RUNTIME_CONTRACT_KEYS
        for kind in ("payload_contract_family", "payload_contract_version")
    }
