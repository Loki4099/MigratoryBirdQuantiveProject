from __future__ import annotations

from pathlib import Path


def _migration_source() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "20260816_100_v022_cohort_evidence.py"
    ).read_text(encoding="utf-8")


def test_cohort_evidence_migration_freezes_exact_panel_and_result_identity() -> None:
    source = _migration_source()

    assert 'revision = "20260816_100_v022_evidence"' in source
    assert 'down_revision = "20260816_99_v022_cohort_runtime"' in source
    assert "uq_v022_common_panel_evaluation_cohort" in source
    assert "exact_evaluation_cohort_eligibility_v1" in source
    assert "session.is_decision_session AND eligibility.is_selectable" in source
    assert "Common Evaluation Panel differs from its exact Cohort mask" in source
    assert "result_row.effective_start IS DISTINCT FROM cohort_row.evaluation_start" in source
    assert "Result Evidence must project its exact Evaluation Cohort" in source
    assert "dependency.role='evaluation_cohort' AND dependency.ordinal=0" in source


def test_cohort_evidence_downgrade_is_fail_closed_and_restores_legacy_guards() -> None:
    source = _migration_source()

    assert "Cannot downgrade with Cohort-backed Result Evidence" in source
    assert "DROP COLUMN evaluation_cohort_version_id" in source
    assert "CREATE OR REPLACE FUNCTION experiment.validate_v022_common_panel()" in source
    assert "CREATE OR REPLACE FUNCTION experiment.validate_v022_result_evidence()" in source
