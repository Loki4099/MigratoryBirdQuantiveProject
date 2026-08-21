from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock


def _module():
    path = Path("migrations/versions/20260818_118_v022_strong_roots.py")
    spec = importlib.util.spec_from_file_location("v022_strong_roots", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_derives_strong_roots_from_published_evidence(monkeypatch) -> None:
    module = _module()
    execute = Mock()
    monkeypatch.setattr(module.op, "execute", execute)

    module.upgrade()

    sql = execute.call_args.args[0]
    assert module.revision == "20260818_118_v022_strong_root"
    assert module.down_revision == "20260818_117_v022_cohort_recon"
    assert "CREATE VIEW data.v022_strong_payload_manifest" in sql
    assert "v022_result_evidence_snapshot" in sql
    assert "v022_result_element_diagnostic" in sql
    assert "v022_product_input_payload_binding" in sql
    assert "artifact.status='published'" in sql


def test_downgrade_only_removes_the_derived_view(monkeypatch) -> None:
    module = _module()
    execute = Mock()
    monkeypatch.setattr(module.op, "execute", execute)

    module.downgrade()

    execute.assert_called_once_with("DROP VIEW data.v022_strong_payload_manifest")
