from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

PATH = Path("migrations/versions/20260821_140_gate_import.py")


def _module():
    spec = importlib.util.spec_from_file_location("v022_gate_import", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gate_import_migration_adds_exact_source_projection() -> None:
    module = _module()
    assert module.revision == "20260821_140_gate_import"
    assert module.down_revision == "20260821_139_cohort_import"
    assert len(module.revision) <= 32
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.upgrade()
    sql = "\n".join(statements)
    assert "v022_dataset_gate_source_binding" in sql
    assert "v022_imported_quality_report_binds_dataset" in sql
    assert "report.report_document->>'price_semantics'" in sql
    assert "report.source_dataset_artifact_id" in sql
    assert "source.security_market_quality_report_id" in sql
    assert "market.price_semantics)::text" in sql


def test_gate_import_downgrade_is_fail_closed() -> None:
    module = _module()
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.downgrade()
    sql = "\n".join(statements)
    assert "Cannot downgrade import-native Dataset Gate Assessments" in sql
    assert "v022_security_market_dataset_binding" in sql
    assert "DROP VIEW data.v022_dataset_gate_source_binding" in sql
