from __future__ import annotations

import importlib.util
from pathlib import Path

PATH = Path("migrations/versions/20260818_127_v022_train_diagnostic.py")


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def _module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("v022_m127", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m127_freezes_exact_trainable_run_diagnostic(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = _module()
    recorder = _Recorder()
    monkeypatch.setattr(module.op, "execute", recorder.execute)

    module.upgrade()

    sql = "\n".join(recorder.statements)
    assert module.revision == "20260818_127_v022_train_diag"
    assert module.down_revision == "20260818_126_v022_ensemble_run"
    assert "v022_trainable_aggregation_diagnostic" in sql
    assert "strict_oof_trainable_ensemble_v1" in sql
    assert "run_spec IS DISTINCT FROM NEW.ensemble_spec_id" in sql
    assert "spec.ensemble_fingerprint=document_spec" in sql
    assert "lineage.reject_record_mutation()" in sql


def test_m127_downgrade_is_fail_closed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = _module()
    recorder = _Recorder()
    monkeypatch.setattr(module.op, "execute", recorder.execute)

    module.downgrade()

    sql = "\n".join(recorder.statements)
    assert "Cannot downgrade M127 with Trainable Aggregation Diagnostics" in sql
    assert "DROP TABLE aggregation.v022_trainable_aggregation_diagnostic" in sql
