from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "20260817_114_v022_prod_raw_guard.py"
)


def _module() -> object:
    spec = importlib.util.spec_from_file_location("m114_product_raw_guard", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m114_validates_atomic_product_raw_publication_and_calendar_sessions() -> None:
    module = _module()
    statements: list[str] = []

    with patch.object(module.op, "execute", side_effect=statements.append):
        module.upgrade()

    sql = "\n".join(statements)
    assert "CREATE OR REPLACE FUNCTION" in sql
    assert "min(session.session_date),max(session.session_date)" in sql
    assert "NEW.coverage_start IS DISTINCT FROM expected_start" in sql
    assert "NEW.coverage_end IS DISTINCT FROM expected_end" in sql
    assert "manifest_row.artifact_status IS DISTINCT FROM 'draft'" in sql
    assert "dependency.role='product_input_snapshot'" in sql


def test_m114_downgrade_is_fail_closed_for_published_product_inputs() -> None:
    module = _module()
    statements: list[str] = []

    with patch.object(module.op, "execute", side_effect=statements.append):
        module.downgrade()

    sql = "\n".join(statements)
    assert "v022_product_input_payload_binding" in sql
    assert "Cannot restore the pre-M114" in sql
    assert "NEW.coverage_start>input_row.input_start" in sql
