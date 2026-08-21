from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "20260816_94_v022_seed_import.py"
)


def _migration() -> ModuleType:
    spec = spec_from_file_location("v022_seed_import_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("M94 migration cannot be loaded")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m94_adds_exact_provider_subjects_and_import_evidence() -> None:
    migration = _migration()

    assert migration.revision == "20260816_94_v022_seed_import"
    assert migration.down_revision == "20260814_93_v022_diag_lineage"
    with patch.object(migration.op, "execute") as execute:
        migration.upgrade()

    execute.assert_called_once()
    sql = execute.call_args.args[0]
    for required in (
        "provider_scope varchar(100)",
        "catalog.reject_security_identifier_overlap",
        "lower(item.identifier_value)=lower(NEW.identifier_value)",
        "data.source_snapshot_security_subject",
        "identifier_row.identifier_type IS DISTINCT FROM 'provider_symbol'",
        "snapshot_row.provider_key IS DISTINCT FROM NEW.provider_scope",
        "catalog.protect_bound_security_identifier",
        "data.v022_external_import_manifest",
        "data.v022_external_import_object",
        "v0.22.sp500_seed_import.v1",
        "External Import Manifest projection is incomplete",
        "lineage.reject_record_mutation",
    ):
        assert required in sql


def test_m94_downgrade_is_empty_state_only() -> None:
    migration = _migration()

    with patch.object(migration.op, "execute") as execute:
        migration.downgrade()

    execute.assert_called_once()
    sql = execute.call_args.args[0]
    assert "Cannot downgrade with v0.22 seed import identities" in sql
    assert "provider_scope<>'catalog'" in sql
    assert "ADD CONSTRAINT uq_security_identifier_period" in sql
