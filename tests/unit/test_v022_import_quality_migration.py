from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

PATH = Path("migrations/versions/20260821_138_import_quality.py")


def _module():
    spec = importlib.util.spec_from_file_location("v022_import_quality_migration", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_quality_migration_has_exact_source_branches() -> None:
    module = _module()
    assert module.revision == "20260821_138_import_quality"
    assert module.down_revision == "20260821_137_v022_import_proof"
    assert len(module.revision) <= 32

    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.upgrade()
    sql = "\n".join(statements)
    assert "ck_v022_quality_report_source_identity" in sql
    assert "source_dataset_publication_id" in sql
    assert "external_import_manifest_id" in sql
    assert "dependency_count<>3" in sql
    assert "dependency.role='market_dataset' AND dependency.ordinal=0" in sql
    assert "dependency.role='external_import_manifest'" in sql
    assert "dependency.role='calendar_version' AND dependency.ordinal=2" in sql
    assert "dataset_row.value_kind IS DISTINCT FROM 'daily_bar'" in sql
    assert "dependency_count<>2" in sql


def test_import_quality_downgrade_is_fail_closed() -> None:
    module = _module()
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.downgrade()
    sql = "\n".join(statements)
    assert "Cannot downgrade imported Security market quality reports" in sql
    assert "ALTER COLUMN yahoo_ingestion_plan_id SET NOT NULL" in sql
    assert "DROP COLUMN source_dataset_publication_id" in sql
