from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

PATH = Path("migrations/versions/20260818_124_v022_supervised_run.py")


def _module():
    spec = importlib.util.spec_from_file_location("v022_m124", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m124_adds_exact_supervised_run_axes(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    monkeypatch.setattr(module, "op", Mock(execute=statements.append))

    module.upgrade()

    sql = "\n".join(statements)
    assert module.revision == "20260818_124_v022_supervised_run"
    assert module.down_revision == "20260818_123_v022_train_artifact"
    assert "ADD COLUMN target_version_id" in sql
    assert "ADD COLUMN training_preset_version_id" in sql
    assert "Supervised Aggregation Run requires Target and Training Preset" in sql
    assert "target_family IS DISTINCT FROM aggregation_family" in sql
    assert "training_family IS DISTINCT FROM aggregation_family" in sql
    assert "Aggregation Run immutable execution identity changed" in sql


def test_m124_downgrade_refuses_supervised_runs(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    monkeypatch.setattr(module, "op", Mock(execute=statements.append))

    module.downgrade()

    sql = "\n".join(statements)
    assert "Cannot downgrade M124 with supervised Aggregation Runs" in sql
    assert "DROP COLUMN training_preset_version_id" in sql
    assert "CREATE FUNCTION aggregation.validate_v022_deterministic_run" in sql
