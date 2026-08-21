from __future__ import annotations

from pathlib import Path


def _source() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "20260816_101_v022_ranking.py"
    ).read_text(encoding="utf-8")


def test_ranking_migration_freezes_cohort_members_and_core_metrics() -> None:
    source = _source()

    assert 'revision = "20260816_101_v022_ranking"' in source
    assert 'down_revision = "20260816_100_v022_evidence"' in source
    assert "CREATE TABLE experiment.v022_ranking_cohort_release" in source
    assert "CREATE TABLE experiment.v022_ranking_cohort_member" in source
    assert "cohort_row.research_tier IS DISTINCT FROM 'rankable_research'" in source
    assert "evidence_row.quality_document->>'state' IS DISTINCT FROM 'passed'" in source
    assert "NEW.benchmark_cagr IS DISTINCT FROM expected_cagr-expected_spread" in source
    assert "Ranking Cohort member projection is incomplete" in source


def test_ranking_migration_is_append_only_and_downgrade_fail_closed() -> None:
    source = _source()

    assert "trg_v022_ranking_cohort_release_append_only" in source
    assert "trg_v022_ranking_cohort_member_append_only" in source
    assert "Cannot downgrade with v0.22 Ranking Cohort Releases" in source
