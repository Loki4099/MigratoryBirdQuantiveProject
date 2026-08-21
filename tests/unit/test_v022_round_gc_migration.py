from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

PATH = Path("migrations/versions/20260819_133_v022_round_gc.py")


def _module():
    spec = importlib.util.spec_from_file_location("v022_round_gc_migration", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_round_gc_migration_derives_product_and_active_round_strong_roots() -> None:
    module = _module()
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.upgrade()
    sql = "\n".join(statements)
    assert module.revision == "20260819_133_v022_round_gc"
    assert module.down_revision == "20260819_132_v022_round_ranking"
    assert "CREATE VIEW product.v022_product_strong_artifact" in sql
    assert "WITH RECURSIVE root_artifact" in sql
    assert "round.status='active'" in sql
    assert "data.v022_dataset_payload_binding" in sql
    assert "CREATE TABLE ops.v022_research_round_gc_plan" in sql
    assert "CREATE TABLE ops.v022_research_round_gc_tombstone" in sql


def test_round_gc_downgrade_is_fail_closed_after_planning() -> None:
    module = _module()
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.downgrade()
    sql = "\n".join(statements)
    assert "Cannot downgrade after Research Round GC planning" in sql
    assert "DROP VIEW ops.v022_research_round_artifact" in sql
