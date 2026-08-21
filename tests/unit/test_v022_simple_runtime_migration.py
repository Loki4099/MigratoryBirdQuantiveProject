from __future__ import annotations

import importlib.util
from pathlib import Path

PATH = Path("migrations/versions/20260821_141_simple_runtime.py")


def _module():
    spec = importlib.util.spec_from_file_location("m141", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m141_versions_and_guards_are_scoped_to_simple_runtime() -> None:
    module = _module()

    assert module.revision == "20260821_141_simple_runtime"
    assert module.down_revision == "20260821_140_gate_import"
    assert len(module.revision) <= 32
    assert "exact published Graph Catalog Release" in module._PLAN_CATALOG_NEW
    assert "v022_graph_uses_composed_defense" in module._PLAN_BINDING_NEW
    assert "Simple Runtime Work Spec cannot bind" in module._WORK_BINDING_NEW
    assert "Risk Context drifted" in module._WORK_BINDING_NEW


def test_m141_fails_closed_when_expected_function_shape_drifted(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.upgrade()

    assert len(statements) == 3
    assert all("position($old$" in statement for statement in statements)
    assert all("RAISE EXCEPTION 'M141" in statement for statement in statements)
    assert "validate_v022_suite_runtime_plan" in statements[0]
    assert "validate_v022_suite_runtime_work_spec" in statements[2]


def test_m141_downgrade_rejects_existing_simple_plans(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.downgrade()

    assert "Cannot downgrade while simple Suite Runtime Plans exist" in statements[0]
    assert "NOT experiment.v022_graph_uses_composed_defense" in statements[0]
    assert len(statements) == 4
