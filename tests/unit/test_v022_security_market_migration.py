from __future__ import annotations

import importlib.util
from pathlib import Path


def _migration():
    path = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "20260816_97_v022_security_market.py"
    )
    spec = importlib.util.spec_from_file_location("m97", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m97_contains_exact_quality_dataset_and_lifecycle_guards(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.upgrade()
    sql = "\n".join(statements)

    assert module.down_revision == "20260816_96_v022_yahoo_ingestion"
    assert "data.v022_security_market_quality_report" in sql
    assert "data.v022_security_market_dataset_binding" in sql
    assert "catalog.v022_security_terminal_event_evidence_binding" in sql
    assert "historical_constituent_pit__frozen_retrospective_yahoo_prices" in sql
    assert "quality_report.error_count IS DISTINCT FROM 0" not in sql
    assert "report_row.error_count IS DISTINCT FROM 0" in sql
    assert "source_evidence" in sql
    assert sql.count("lineage.reject_record_mutation") == 3


def test_m97_downgrade_is_fail_closed(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.downgrade()
    sql = "\n".join(statements)

    assert "Cannot downgrade with v0.22 Security market evidence" in sql
    assert "DROP TABLE data.v022_security_market_quality_report" in sql
