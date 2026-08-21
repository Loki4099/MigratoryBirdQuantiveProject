from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

PATH = Path("migrations/versions/20260819_130_v022_launch_events.py")


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m130_launch_events", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m130_persists_safe_append_only_launch_stage_outcomes(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    operation = MagicMock()
    operation.execute.side_effect = statements.append
    monkeypatch.setattr(module, "op", operation)
    module.upgrade()
    sql = "\n".join(statements)
    assert module.revision == "20260819_130_v022_launch_events"
    assert module.down_revision == "20260819_129_v022_schema_binding"
    assert "CREATE TABLE experiment.v022_suite_launch_batch_event" in sql
    assert "length(error_summary)<=1000" in sql
    assert "lineage.reject_record_mutation" in sql


def test_m130_downgrade_is_fail_closed(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    operation = MagicMock()
    operation.execute.side_effect = statements.append
    monkeypatch.setattr(module, "op", operation)
    module.downgrade()
    assert "Cannot downgrade M130" in "\n".join(statements)
