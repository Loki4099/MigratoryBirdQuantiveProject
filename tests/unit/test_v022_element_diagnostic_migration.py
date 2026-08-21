from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "20260814_93_v022_diag_lineage.py"
)


def _migration() -> ModuleType:
    spec = spec_from_file_location("v022_diag_lineage_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("M93 migration cannot be loaded")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m93_replaces_only_the_element_diagnostic_validation_guard() -> None:
    migration = _migration()

    assert migration.revision == "20260814_93_v022_diag_lineage"
    assert migration.down_revision == "20260814_92_v022_ctx_payload"
    with patch.object(migration.op, "execute") as execute:
        migration.upgrade()

    execute.assert_called_once()
    sql = execute.call_args.args[0]
    for preserved_guard in (
        "data.assert_artifact_draft(NEW.artifact_id)",
        "Element Diagnostic requires its exact draft Artifact",
        "Element Diagnostic requires a published Result Artifact",
        "Element Diagnostic requires its exact materialized input Manifest",
        "Element Diagnostic physical identities do not match their Artifacts",
    ):
        assert preserved_guard in sql
    for lineage_guard in (
        "experiment.v022_portfolio_cell_runtime_result",
        "result.configuration_snapshot_id",
        "WITH RECURSIVE occurrence_edge",
        "experiment.v022_configuration_direct_input",
        "workspace.compiled_node_input",
        "strategy.v022_strategy_target_work_spec",
        "aggregation.graph_run_aggregation_binding",
        "aggregation.aggregation_run_input",
        "manifest_lineage AS",
        "processing.node_run_input",
        "processing.graph_run_node_binding",
        "node_run.status='completed'",
        "node_output.output_port_key=occurrence.output_port_key",
        "lineage.payload_manifest_id=NEW.payload_manifest_id",
        "binding.node_run_id=node_run.node_run_id",
        "manifest_matches_occurrence_output",
    ):
        assert lineage_guard in sql
    assert "ON binding.graph_run_id=plan.graph_run_id" not in sql
    assert "DROP TRIGGER" not in sql
    assert "ALTER TABLE experiment.v022_result_element_diagnostic" not in sql


def test_m93_downgrade_fails_closed_before_restoring_the_m88_direct_guard() -> None:
    migration = _migration()

    with patch.object(migration.op, "execute") as execute:
        migration.downgrade()

    execute.assert_called_once()
    sql = execute.call_args.args[0]
    assert "Cannot downgrade while non-direct v0.22 Element Diagnostics exist" in sql
    assert "WHERE NOT EXISTS" in sql
    assert "Element Diagnostic occurrence is not a frozen direct input" in sql
    assert "WITH RECURSIVE occurrence_edge" not in sql
    assert "DROP TABLE" not in sql
    assert "DROP TRIGGER" not in sql
