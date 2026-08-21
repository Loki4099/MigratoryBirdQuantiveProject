from __future__ import annotations

import importlib.util
from pathlib import Path


def _migration():
    path = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "20260816_98_v022_eval_cohort.py"
    )
    spec = importlib.util.spec_from_file_location("m98", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m98_freezes_cohort_sessions_eligibility_and_suite_binding(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.upgrade()
    sql = "\n".join(statements)

    assert module.down_revision == "20260816_97_v022_security_market"
    assert "experiment.v022_evaluation_cohort_version" in sql
    assert "required_history_sessions=504" in sql
    assert "experiment.v022_evaluation_cohort_session" in sql
    assert "experiment.v022_cohort_eligibility_interval" in sql
    assert "experiment.v022_research_suite_evaluation_cohort_binding" in sql
    assert "actual_warmup<>NEW.required_history_sessions" in sql
    assert "dataset_row.coverage_start>NEW.warmup_start" in sql
    assert "history_row.unresolved_count<>0" in sql
    assert "daterange(existing.effective_start" in sql
    assert sql.count("lineage.reject_record_mutation") == 4


def test_m98_downgrade_is_fail_closed(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.downgrade()
    sql = "\n".join(statements)

    assert "Cannot downgrade with v0.22 Evaluation Cohorts" in sql
    assert "DROP TABLE experiment.v022_evaluation_cohort_version" in sql
