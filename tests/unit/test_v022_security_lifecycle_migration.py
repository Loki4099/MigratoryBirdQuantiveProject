from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "20260817_103_v022_lifecycle.py"
)


def _migration() -> ModuleType:
    spec = spec_from_file_location("v022_security_lifecycle_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("M103 migration cannot be loaded")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m103_adds_exact_lifecycle_evidence_and_settlement_closure() -> None:
    migration = _migration()

    assert migration.revision == "20260817_103_v022_lifecycle"
    assert migration.down_revision == "20260817_102_v022_identity"
    with patch.object(migration.op, "execute") as execute:
        migration.upgrade()

    sql = execute.call_args.args[0]
    for required in (
        "catalog.v022_security_lifecycle_event",
        "catalog.v022_security_lifecycle_event_evidence",
        "catalog.v022_security_settlement_leg",
        "v0.22.security_lifecycle_event.v1",
        "dependency.role='source_evidence'",
        "dependency.role='superseded_lifecycle_event'",
        "Security Lifecycle Event closure is incomplete",
        "event_status='confirmed' AND event_type IN",
        "lineage.reject_record_mutation",
    ):
        assert required in sql


def test_m103_downgrade_is_empty_state_only() -> None:
    migration = _migration()

    with patch.object(migration.op, "execute") as execute:
        migration.downgrade()

    sql = execute.call_args.args[0]
    assert "Cannot downgrade with v0.22 Security Lifecycle evidence" in sql
    assert "DROP TABLE catalog.v022_security_settlement_leg" in sql
    assert "DROP TABLE catalog.v022_security_lifecycle_event" in sql
