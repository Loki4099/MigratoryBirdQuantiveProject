from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

MIGRATION = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260819_131_v022_research_round.py"
)


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("v022_research_round_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_research_round_upgrade_freezes_revision_and_batch_scope() -> None:
    module = _module()
    execute = Mock()
    with patch.object(module.op, "execute", execute):
        module.upgrade()
    sql = "\n".join(str(call.args[0]) for call in execute.call_args_list)
    assert module.down_revision == "20260819_130_v022_launch_events"
    assert "CREATE TABLE workspace.v022_research_round" in sql
    assert "CREATE TABLE workspace.v022_graph_draft_revision_round" in sql
    assert "CREATE TABLE experiment.v022_suite_launch_batch_round" in sql
    assert "legacy_reset_backfill" in sql
    assert "WHERE status='active'" in sql
    assert "v0.22 Research Round identity is immutable" in sql
    assert "reject_v022_revision_round_mutation" in sql


def test_research_round_downgrade_is_fail_closed() -> None:
    module = _module()
    execute = Mock()
    with patch.object(module.op, "execute", execute):
        module.downgrade()
    sql = "\n".join(str(call.args[0]) for call in execute.call_args_list)
    assert "Cannot downgrade nonempty v0.22 Research Round identities" in sql
