from pathlib import Path


def test_m105_dataset_gate_schema_freezes_both_admission_dimensions() -> None:
    sql = Path(
        "migrations/versions/20260817_105_v022_dataset_gate.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision = "20260817_104_v022_reconciliation"' in sql
    assert "CREATE TABLE data.v022_dataset_gate_assessment" in sql
    assert "CREATE TABLE data.v022_dataset_gate_assessment_evidence" in sql
    assert "CREATE TABLE data.v022_dataset_gate_finding" in sql
    assert "CREATE TABLE data.v022_dataset_gate_uniform_exclusion" in sql
    assert "ranking_eligibility IN ('rankable_research','exploratory_only')" in sql
    assert "product_eligibility IN ('eligible','eligible_with_warnings','ineligible')" in sql
    assert "historical_membership_retrospective" in sql
    assert "retrospective_price_snapshot" in sql
    assert "Dataset Gate Assessment projection is incomplete or inconsistent" in sql


def test_m105_dataset_gate_is_append_only_and_downgrade_is_fail_closed() -> None:
    sql = Path(
        "migrations/versions/20260817_105_v022_dataset_gate.py"
    ).read_text(encoding="utf-8")

    assert sql.count("EXECUTE FUNCTION lineage.reject_record_mutation()") == 4
    assert "Cannot downgrade with v0.22 Dataset Gate Assessments" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
