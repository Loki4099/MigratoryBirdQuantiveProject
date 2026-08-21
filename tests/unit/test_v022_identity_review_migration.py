from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "20260817_102_v022_identity_review.py"
)


def _migration() -> ModuleType:
    spec = spec_from_file_location("v022_identity_review_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("M102 migration cannot be loaded")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m102_adds_append_only_case_evidence_and_resolution_chain() -> None:
    migration = _migration()

    assert migration.revision == "20260817_102_v022_identity"
    assert migration.down_revision == "20260816_101_v022_ranking"
    with patch.object(migration.op, "execute") as execute:
        migration.upgrade()

    sql = execute.call_args.args[0]
    for required in (
        "catalog.v022_security_identity_review_case",
        "catalog.v022_security_identity_evidence",
        "catalog.v022_security_identity_resolution",
        "catalog.v022_security_identity_resolution_evidence",
        "v0.22.security_identity_review.v1",
        "v0.22.security_identity_evidence.v1",
        "v0.22.security_identity_resolution.v1",
        "dependency.role='external_import_manifest'",
        "dependency.role='identity_review_case'",
        "dependency.role='identity_evidence'",
        "Identity Resolution Evidence projection is incomplete",
        "trg_security_identifier_resolution_immutable",
        "lineage.reject_record_mutation",
    ):
        assert required in sql


def test_m102_downgrade_is_empty_state_only() -> None:
    migration = _migration()

    with patch.object(migration.op, "execute") as execute:
        migration.downgrade()

    sql = execute.call_args.args[0]
    assert "Cannot downgrade with v0.22 Security Identity Review evidence" in sql
    assert "DROP TABLE catalog.v022_security_identity_resolution_evidence" in sql
    assert "DROP TABLE catalog.v022_security_identity_review_case" in sql
