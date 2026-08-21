from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock


def _module():
    path = Path("migrations/versions/20260818_119_v022_restore_roots.py")
    spec = importlib.util.spec_from_file_location("v022_restore_roots", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_validates_restore_rows_against_the_derived_root(monkeypatch) -> None:
    module = _module()
    execute = Mock()
    monkeypatch.setattr(module.op, "execute", execute)

    module.upgrade()

    sql = execute.call_args.args[0]
    assert module.revision == "20260818_119_v022_restore_root"
    assert module.down_revision == "20260818_118_v022_strong_root"
    assert "CREATE OR REPLACE FUNCTION ops.validate_v022_restore_drill_object" in sql
    assert "data.v022_strong_payload_manifest" in sql
    assert "root.payload_manifest_id=NEW.payload_manifest_id" in sql


def test_downgrade_restores_the_original_retention_validation(monkeypatch) -> None:
    module = _module()
    execute = Mock()
    monkeypatch.setattr(module.op, "execute", execute)

    module.downgrade()

    sql = execute.call_args.args[0]
    assert "source.retention_class NOT IN" in sql
    assert "data.v022_strong_payload_manifest" not in sql
