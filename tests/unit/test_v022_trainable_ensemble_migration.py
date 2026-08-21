from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

PATH = Path("migrations/versions/20260818_125_v022_train_ensemble.py")


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m125_trainable_ensemble", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m125_freezes_exact_ensemble_spec_and_member_closure() -> None:
    module = _module()
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.upgrade()
    sql = "\n".join(statements)

    assert module.revision == "20260818_125_v022_train_ensemble"
    assert module.down_revision == "20260818_124_v022_supervised_run"
    assert "CREATE TABLE aggregation.v022_trainable_ensemble_spec" in sql
    assert "CREATE TABLE aggregation.v022_trainable_ensemble_member" in sql
    assert "CREATE TABLE workspace.v022_compiled_trainable_ensemble_binding" in sql
    assert "member_count BETWEEN 2 AND 12" in sql
    assert "equal_within_target_equal_across_targets_v1" in sql
    assert "Trainable Ensemble member closure is incomplete" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "Multi-member supervised Aggregation requires one exact Ensemble Spec" in sql
    assert "Compiled Trainable Ensemble binding is not exact" in sql
    assert "lineage.reject_record_mutation()" in sql


def test_m125_downgrade_is_fail_closed_and_restores_direct_guard() -> None:
    module = _module()
    statements: list[str] = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.downgrade()
    sql = "\n".join(statements)

    assert "Cannot downgrade M125 with Trainable Ensemble identities" in sql
    assert "DROP TABLE aggregation.v022_trainable_ensemble_member" in sql
    assert "supervised aggregation requires Target and Training Preset" in sql
