from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

PATH = Path("migrations/versions/20260818_123_v022_train_artifact_identity.py")


def _module():
    spec = importlib.util.spec_from_file_location("v022_m123", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m123_separates_intrinsic_and_artifact_semantic_identity(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    monkeypatch.setattr(module, "op", Mock(execute=statements.append))

    module.upgrade()

    sql = "\n".join(statements)
    assert module.revision == "20260818_123_v022_train_artifact"
    assert module.down_revision == "20260818_122_v022_train_core"
    assert sql.count("ADD COLUMN artifact_semantic_fingerprint") == 7
    assert "NEW.artifact_semantic_fingerprint" in sql
    assert "identity_artifact.artifact_key IS DISTINCT FROM fingerprint_value" in sql
    assert "manifest_producer_artifact_id IS DISTINCT FROM NEW.artifact_id" in sql
    assert "Payload Manifest is not exact and published" in sql
    assert "requires empty unpublished trainable Aggregation identities" in sql


def test_m123_downgrade_is_empty_only_and_restores_m122_guard(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    monkeypatch.setattr(module, "op", Mock(execute=statements.append))

    module.downgrade()

    sql = "\n".join(statements)
    assert "Cannot downgrade M123 with published trainable Aggregation identities" in sql
    assert "semantic_fingerprint IS DISTINCT FROM fingerprint_value" in sql
    assert sql.count("DROP COLUMN artifact_semantic_fingerprint") == 7
