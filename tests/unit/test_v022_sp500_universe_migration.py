from __future__ import annotations

import importlib


def test_m95_adds_source_backed_membership_and_history_closure() -> None:
    migration = importlib.import_module(
        "migrations.versions.20260816_95_v022_sp500_universe"
    )
    statements: list[str] = []
    original = migration.op.execute
    migration.op.execute = statements.append
    try:
        migration.upgrade()
    finally:
        migration.op.execute = original

    sql = "\n".join(statements)
    assert "CREATE TABLE catalog.v022_universe_membership_ledger" in sql
    assert "CREATE TABLE catalog.v022_universe_membership_event" in sql
    assert "CREATE TABLE catalog.v022_universe_history_ledger_binding" in sql
    assert "membership_ledger'" in sql
    assert "expected_members" in sql
    assert "Source-backed v0.22 Universe projections are immutable" in sql
    assert "lineage.reject_record_mutation" in sql


def test_m95_downgrade_is_fail_closed_for_published_ledgers() -> None:
    migration = importlib.import_module(
        "migrations.versions.20260816_95_v022_sp500_universe"
    )
    statements: list[str] = []
    original = migration.op.execute
    migration.op.execute = statements.append
    try:
        migration.downgrade()
    finally:
        migration.op.execute = original

    sql = "\n".join(statements)
    assert "Cannot downgrade with source-backed v0.22 Universe identities" in sql
    assert "DROP TABLE catalog.v022_universe_membership_ledger" in sql
