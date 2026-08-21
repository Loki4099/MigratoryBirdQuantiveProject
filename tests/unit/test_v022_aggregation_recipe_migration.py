from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

PATH = Path("migrations/versions/20260818_121_v022_aggregation_recipe.py")


def _module():
    spec = importlib.util.spec_from_file_location("v022_m121", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m121_creates_append_only_taxonomy_and_compiled_recipe(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    monkeypatch.setattr(module, "op", Mock(execute=statements.append))

    module.upgrade()

    sql = "\n".join(statements)
    assert module.revision == "20260818_121_v022_agg_recipe"
    assert module.down_revision == "20260818_120_v022_launch_batch"
    assert "aggregation.v022_feature_taxonomy_version" in sql
    assert "workspace.v022_compiled_aggregation_recipe" in sql
    assert "feature_taxonomy_version_id" in sql
    assert "recipe_fingerprint" in sql
    assert "reject_v022_compiled_aggregation_recipe_mutation" in sql
    assert "aggregation_feature_taxonomy_version" in sql


def test_m121_downgrade_is_fail_closed_for_published_identity(monkeypatch) -> None:
    module = _module()
    statements: list[str] = []
    monkeypatch.setattr(module, "op", Mock(execute=statements.append))

    module.downgrade()

    sql = "\n".join(statements)
    assert "Cannot downgrade nonempty v0.22 Aggregation taxonomy/Recipe identities" in sql
    assert "EXISTS (SELECT 1 FROM workspace.v022_compiled_aggregation_recipe)" in sql
    assert "EXISTS (SELECT 1 FROM aggregation.v022_feature_taxonomy_version)" in sql
