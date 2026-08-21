from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "20260817_111_v022_prod_runtime.py"
)


def test_m111_freezes_exact_members_and_product_payload_bindings() -> None:
    module = _migration()
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.upgrade()
    sql = "\n".join(statements)

    assert "CREATE TABLE product.v022_product_input_member" in sql
    assert "CREATE TABLE data.v022_product_input_payload_binding" in sql
    assert "required_history_sessions=504" in sql
    assert "Product Input Snapshot member set is incomplete" in sql
    assert "dependency.role='product_input_snapshot'" in sql
    assert "trg_v022_product_input_member_append_only" in sql
    assert "trg_v022_product_input_payload_binding_append_only" in sql


def test_m111_downgrade_is_fail_closed() -> None:
    module = _migration()
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.downgrade()
    sql = "\n".join(statements)

    assert "SELECT 1 FROM product.v022_product_input_member" in sql
    assert "SELECT 1 FROM data.v022_product_input_payload_binding" in sql
    assert "Cannot downgrade nonempty Product runtime input state" in sql


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m111_product_runtime_input", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
