from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

PATH = Path("migrations/versions/20260819_128_v022_product_ensemble_state.py")


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m128_product_state", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m128_freezes_atomic_product_ensemble_state(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    operation = MagicMock()
    operation.execute.side_effect = statements.append
    monkeypatch.setattr(module, "op", operation)

    module.upgrade()

    sql = "\n".join(statements)
    assert module.revision == "20260819_128_v022_product_state"
    assert module.down_revision == "20260818_127_v022_train_diag"
    assert "CREATE TABLE product.v022_product_ensemble_state" in sql
    assert "CREATE TABLE product.v022_product_ensemble_state_member" in sql
    assert "complete_atomic_member_set_v1" in sql
    assert "retain_previous_complete_state" in sql
    assert "enrollment.execution_version_id" in sql
    assert "Product Ensemble State member closure is incomplete" in sql
    assert "active Ensemble State is not bound to Aggregation" in sql
    assert "lineage.reject_record_mutation" in sql


def test_m128_downgrade_is_fail_closed(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    operation = MagicMock()
    operation.execute.side_effect = statements.append
    monkeypatch.setattr(module, "op", operation)

    module.downgrade()

    sql = "\n".join(statements)
    assert "Cannot downgrade M128 with Product Ensemble States" in sql
    assert "DROP TABLE product.v022_product_ensemble_state" in sql
