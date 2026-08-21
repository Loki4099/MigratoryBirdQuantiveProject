from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

PATH = Path("migrations/versions/20260818_122_v022_train_core.py")


def _module():
    spec = importlib.util.spec_from_file_location("v022_m122", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m122_creates_complete_append_only_training_identity_chain(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    monkeypatch.setattr(module, "op", Mock(execute=statements.append))

    module.upgrade()

    sql = "\n".join(statements)
    assert module.revision == "20260818_122_v022_train_core"
    assert module.down_revision == "20260818_121_v022_agg_recipe"
    for table in (
        "v022_feature_schema_version",
        "v022_training_matrix",
        "v022_fold_policy_version",
        "v022_training_fold",
        "v022_base_learner_spec",
        "v022_fitted_model_state",
        "v022_oof_prediction",
        "v022_oof_prediction_fold",
    ):
        assert table in sql
    assert "xnys_completed_session_daily" in sql
    assert "expanding_walk_forward" in sql
    assert "random_split" in sql
    assert "Payload Manifest is not published" in sql
    assert "trainable Aggregation identities are append-only" in sql


def test_m122_downgrade_refuses_to_drop_published_training_identity(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    monkeypatch.setattr(module, "op", Mock(execute=statements.append))

    module.downgrade()

    sql = "\n".join(statements)
    assert "Cannot downgrade nonempty v0.22 trainable Aggregation identities" in sql
    assert "EXISTS (SELECT 1 FROM aggregation.v022_oof_prediction)" in sql
    assert "EXISTS (SELECT 1 FROM aggregation.v022_fitted_model_state)" in sql
