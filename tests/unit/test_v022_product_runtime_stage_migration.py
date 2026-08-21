from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260817_112_v022_prod_stages.py"
)


def test_product_runtime_stage_migration_freezes_exact_chain() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260817_112_v022_prod_stages"' in source
    assert 'down_revision = "20260817_111_v022_prod_runtime"' in source
    assert "CREATE TABLE product.v022_product_runtime_execution" in source
    assert "CREATE TABLE product.v022_product_runtime_stage" in source
    assert "CREATE TABLE product.v022_product_runtime_stage_input" in source
    assert "UNIQUE (product_input_snapshot_id,runtime_version)" in source
    assert "configuration_snapshot_id uuid NOT NULL" in source
    assert "product_input_snapshot' AND dependency.ordinal=0" in source
    assert "configuration_snapshot' AND dependency.ordinal=1" in source
    assert "input.role='aggregation_output'" in source
    assert "input.role='strategy_target'" in source
    assert "input.role='defense_decision'" in source
    assert "NEW.input_count IN (2,3)" in source
    assert "NEW.input_count=1" in source
    assert "DEFERRABLE INITIALLY DEFERRED" in source
    assert "lineage.reject_record_mutation" in source
    assert "Cannot downgrade nonempty Product Runtime Stage state" in source
