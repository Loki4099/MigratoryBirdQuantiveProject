from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

PATH = Path("migrations/versions/20260819_129_v022_schema_binding.py")


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m129_schema_binding", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m129_globalizes_schema_and_freezes_exact_instance_binding(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    operation = MagicMock()
    operation.execute.side_effect = statements.append
    monkeypatch.setattr(module, "op", operation)

    module.upgrade()

    sql = "\n".join(statements)
    assert module.revision == "20260819_129_v022_schema_binding"
    assert module.down_revision == "20260819_128_v022_product_state"
    assert "CREATE TABLE workspace.v022_compiled_feature_schema_binding" in sql
    assert "Compiled Feature Schema binding is not exact and published" in sql
    assert "DROP COLUMN compiled_research_graph_id" in sql
    assert "DROP COLUMN aggregation_version_id" in sql
    assert "Supervised Aggregation requires one exact Feature Schema" in sql
    assert "lineage.reject_record_mutation" in sql


def test_m129_downgrade_is_fail_closed(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    operation = MagicMock()
    operation.execute.side_effect = statements.append
    monkeypatch.setattr(module, "op", operation)

    module.downgrade()

    sql = "\n".join(statements)
    assert "Cannot downgrade M129 with global Feature Schema identities" in sql
    assert "DROP TABLE workspace.v022_compiled_feature_schema_binding" in sql
