from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "20260817_110_v022_product_input.py"
)


def test_m110_creates_exact_append_only_product_input_identity() -> None:
    module = _migration()
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.upgrade()
    sql = "\n".join(statements)

    assert "CREATE TABLE product.v022_product_input_snapshot" in sql
    assert "UNIQUE (product_enrollment_id,decision_session_id)" in sql
    assert "UNIQUE (execution_version_id,decision_session_id)" in sql
    assert "runtime_network_access'='false'" in sql
    assert "dataset_row.dataset_key IS DISTINCT FROM baseline_row.dataset_key" in sql
    assert "history_row.universe_methodology_id IS DISTINCT FROM" in sql
    assert "calendar_row.calendar_definition_id IS DISTINCT FROM" in sql
    assert "gate_row.product_eligibility='ineligible' OR gate_row.blocker_count<>0" in sql
    assert "dependency_count<>6" in sql
    assert "trg_v022_product_input_snapshot_append_only" in sql


def test_m110_downgrade_is_fail_closed_for_published_snapshots() -> None:
    module = _migration()
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.downgrade()
    sql = "\n".join(statements)

    assert "IF EXISTS (SELECT 1 FROM product.v022_product_input_snapshot)" in sql
    assert "Cannot downgrade nonempty Product Input Snapshot state" in sql


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m110_product_input", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
