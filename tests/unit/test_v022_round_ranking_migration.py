from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

PATH = Path("migrations/versions/20260819_132_v022_round_ranking.py")


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("v022_round_ranking", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_round_ranking_binding_is_append_only() -> None:
    module = _module()
    execute = Mock()
    with patch.object(module.op, "execute", execute):
        module.upgrade()
    sql = "\n".join(str(call.args[0]) for call in execute.call_args_list)
    assert module.down_revision == "20260819_131_v022_research_round"
    assert "CREATE TABLE experiment.v022_ranking_cohort_release_round" in sql
    assert "reject_v022_ranking_release_round_mutation" in sql


def test_round_ranking_downgrade_is_fail_closed() -> None:
    module = _module()
    execute = Mock()
    with patch.object(module.op, "execute", execute):
        module.downgrade()
    sql = "\n".join(str(call.args[0]) for call in execute.call_args_list)
    assert "Cannot downgrade nonempty v0.22 Round Ranking bindings" in sql
