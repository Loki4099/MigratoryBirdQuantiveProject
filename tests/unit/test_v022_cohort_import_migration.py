from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

PATH = Path("migrations/versions/20260821_139_cohort_import.py")


def _module():
    spec = importlib.util.spec_from_file_location("v022_cohort_import", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cohort_import_migration_binds_exact_imported_quality_source() -> None:
    module = _module()
    assert module.revision == "20260821_139_cohort_import"
    assert module.down_revision == "20260821_138_import_quality"
    assert len(module.revision) <= 32
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.upgrade()
    sql = "\n".join(statements)
    assert "v022_imported_quality_report_binds_dataset" in sql
    assert "report.source_dataset_publication_id=risk_dataset.dataset_publication_id" in sql
    assert "report.source_dataset_artifact_id=risk_dataset.artifact_id" in sql
    assert "report.report_document->>'price_semantics'" in sql
    assert "Current Evaluation Cohort validator shape is unknown" in sql


def test_cohort_import_downgrade_is_fail_closed() -> None:
    module = _module()
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.downgrade()
    sql = "\n".join(statements)
    assert "Cannot downgrade imported Evaluation Cohorts" in sql
    assert "DROP FUNCTION data.v022_imported_quality_report_binds_dataset" in sql
