from pathlib import Path

MIGRATION = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260817_113_v022_prod_decision.py"
)


def test_product_decision_runtime_binding_is_exact_and_fail_closed() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260817_113_v022_prod_decision"' in source
    assert 'down_revision = "20260817_112_v022_prod_stages"' in source
    assert "CREATE TABLE product.v022_product_decision_runtime_binding" in source
    assert "product_input_snapshot_id uuid NOT NULL UNIQUE" in source
    assert "product_runtime_execution_id uuid NOT NULL UNIQUE" in source
    assert "aggregation_row.stage_kind IS DISTINCT FROM 'aggregation'" in source
    assert "strategy_row.stage_kind IS DISTINCT FROM 'strategy'" in source
    assert "defense_row.stage_kind IS DISTINCT FROM 'defense'" in source
    assert "merge_row.stage_kind IS DISTINCT FROM 'merge'" in source
    assert "input.role='processing_manifest'" in source
    assert "dependency.role='product_input_snapshot'" in source
    assert "dependency.role='product_runtime_execution'" in source
    assert "aggregation_type='v022_product_aggregation_output'" in source
    assert "DEFERRABLE INITIALLY DEFERRED" in source
    assert "Cannot downgrade nonempty Product Decision Runtime bindings" in source
