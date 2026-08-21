from __future__ import annotations

import importlib.util
from pathlib import Path

PATH = Path("migrations/versions/20260821_142_asset_export.py")


def _module():
    spec = importlib.util.spec_from_file_location("m142", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m142_adds_asset_export_queue_and_immutable_result(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.upgrade()

    sql = "\n".join(statements)
    assert module.revision == "20260821_142_asset_export"
    assert module.down_revision == "20260821_141_simple_runtime"
    assert len(module.revision) <= 32
    assert "'asset_export'" in sql
    assert "v022_asset_data_export_job" in sql
    assert "v022_asset_data_export_result" in sql
    assert "v0.22 Asset Data Export Results are immutable" in sql
    assert "interval '7 days'" in sql


def test_m142_downgrade_fails_closed_when_jobs_exist(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.downgrade()

    assert "Cannot downgrade while v0.22 Asset Data Export evidence exists" in statements[0]
