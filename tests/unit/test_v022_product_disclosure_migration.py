from pathlib import Path

MIGRATION = Path("migrations/versions/20260817_107_v022_product_disclosure.py")


def test_product_disclosure_migration_freezes_gate_and_blocks_destructive_downgrade() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision = "20260817_106_v022_cohort_runtime"' in sql
    assert "CREATE TABLE product.v022_product_data_disclosure" in sql
    assert "eligible_with_warnings" in sql
    assert "v0.22.product_data_disclosure.v1" in sql
    assert "evaluation_cohort_runtime_contract_id" in sql
    assert "dataset_gate_fingerprint" in sql
    assert "Product Data Disclosure exact input closure invalid" in sql
    assert "reject_record_mutation" in sql
    assert "Cannot downgrade nonempty Product Data Disclosure state" in sql
