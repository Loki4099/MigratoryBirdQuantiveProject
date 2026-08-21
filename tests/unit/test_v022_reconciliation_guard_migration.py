from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _migration() -> ModuleType:
    path = Path("migrations/versions/20260818_116_v022_reconciliation_guard.py")
    spec = importlib.util.spec_from_file_location("v022_reconciliation_guard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m116_only_reads_alternate_record_inside_replacement_branch(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.upgrade()

    sql = "\n".join(statements)
    assert module.revision == "20260818_116_v022_recon_guard"
    assert module.down_revision == "20260817_115_v022_restore"
    branch = sql.index("IF NEW.resolution_kind='replace_with_alternate' THEN")
    lookup = sql.index("INTO alternate_row")
    assert branch < lookup
    assert "Market Gap Resolution alternate observation is incomplete" in sql
    common_identity = sql.index("Market Gap Resolution identity is incomplete")
    assert common_identity < branch


def test_m116_downgrade_restores_m104_guard(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.downgrade()

    sql = "\n".join(statements)
    assert "IF NEW.alternate_observation_set_id IS NOT NULL THEN" in sql
    assert "NEW.resolution_kind='replace_with_alternate' AND" in sql
