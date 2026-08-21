from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

PATH = Path("migrations/versions/20260820_134_v022_recon_v2.py")


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("v022_reconstruction_v2_migration", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m134_versions_reconstruction_without_rewriting_v1(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.upgrade()

    sql = "\n".join(statements)
    assert module.revision == "20260820_134_v022_recon_v2"
    assert module.down_revision == "20260819_133_v022_round_gc"
    assert "raw_ohlcv_actions_backward_total_return_v1" in sql
    assert "split_normalized_ohlcv_dividends_backward_total_return_v2" in sql
    assert "split_normalized_total_return_prices" in sql
    assert "UPDATE " not in sql.upper()
    assert "DELETE " not in sql.upper()


def test_m134_downgrade_is_fail_closed_when_v2_evidence_exists(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.downgrade()

    sql = "\n".join(statements)
    assert "Cannot downgrade with v0.22 split-normalized reconstruction v2 evidence" in sql
    assert "RAISE EXCEPTION" in sql
    assert "ck_v022_recon_plan_policy_v2" in sql
