from __future__ import annotations

import importlib.util
from pathlib import Path

PATH = Path("migrations/versions/20260818_126_v022_ensemble_run.py")


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def _module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("v022_m126", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m126_binds_exact_direct_or_ensemble_run_identity(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = _module()
    recorder = _Recorder()
    monkeypatch.setattr(module.op, "execute", recorder.execute)

    module.upgrade()

    sql = "\n".join(recorder.statements)
    assert module.revision == "20260818_126_v022_ensemble_run"
    assert module.down_revision == "20260818_125_v022_train_ensemble"
    assert "ADD COLUMN ensemble_spec_id uuid NULL" in sql
    assert "direct_bound = ensemble_bound" in sql
    assert "spec_version IS DISTINCT FROM NEW.aggregation_version_id" in sql
    assert "OLD.ensemble_spec_id IS DISTINCT FROM NEW.ensemble_spec_id" in sql


def test_m126_downgrade_is_fail_closed_for_ensemble_runs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = _module()
    recorder = _Recorder()
    monkeypatch.setattr(module.op, "execute", recorder.execute)

    module.downgrade()

    sql = "\n".join(recorder.statements)
    assert "Cannot downgrade M126 with Trainable Ensemble Aggregation Runs" in sql
    assert "DROP COLUMN ensemble_spec_id" in sql
    assert "Supervised Aggregation Run requires Target and Training Preset" in sql
