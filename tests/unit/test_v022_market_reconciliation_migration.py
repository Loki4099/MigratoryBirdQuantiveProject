from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _migration() -> ModuleType:
    path = Path("migrations/versions/20260817_104_v022_reconciliation.py")
    spec = importlib.util.spec_from_file_location("v022_reconciliation_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m104_schema_freezes_observation_resolution_and_new_dataset(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.upgrade()

    sql = "\n".join(statements)
    assert module.down_revision == "20260817_103_v022_lifecycle"
    for table in (
        "data.v022_alternate_observation_set",
        "data.v022_alternate_market_bar",
        "data.v022_market_gap_resolution",
        "data.v022_market_reconciliation_plan",
        "data.v022_reconciled_market_dataset_binding",
    ):
        assert f"CREATE TABLE {table}" in sql
    assert "raw_ohlcv_actions_backward_total_return_v1" in sql
    assert "replace_with_alternate" in sql
    assert "resolution_kind='unresolved'" in sql
    assert "lineage.reject_record_mutation" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql


def test_m104_downgrade_is_nonempty_fail_closed(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.downgrade()

    sql = "\n".join(statements)
    assert "Cannot downgrade with v0.22 reconciliation evidence" in sql
    assert "DROP TABLE data.v022_alternate_observation_set" in sql
