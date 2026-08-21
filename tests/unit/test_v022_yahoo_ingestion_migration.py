from __future__ import annotations

import importlib


def test_m96_adds_immutable_resumable_yahoo_ingestion() -> None:
    migration = importlib.import_module(
        "migrations.versions.20260816_96_v022_yahoo_ingestion"
    )
    statements: list[str] = []
    original = migration.op.execute
    migration.op.execute = statements.append
    try:
        migration.upgrade()
    finally:
        migration.op.execute = original

    sql = "\n".join(statements)
    assert "CREATE TABLE data.v022_yahoo_ingestion_plan" in sql
    assert "CREATE TABLE data.v022_yahoo_ingestion_segment" in sql
    assert "CREATE TABLE data.v022_yahoo_ingestion_attempt" in sql
    assert "Completed Yahoo Ingestion Segment cannot be retried" in sql
    assert "Attempt ordinals must be contiguous" in sql
    assert "source_snapshot_security_subject_id" in sql
    assert "request_parameters->>'provider_ticker' IS DISTINCT FROM" in sql
    assert "lineage.reject_record_mutation" in sql


def test_m96_downgrade_is_fail_closed() -> None:
    migration = importlib.import_module(
        "migrations.versions.20260816_96_v022_yahoo_ingestion"
    )
    statements: list[str] = []
    original = migration.op.execute
    migration.op.execute = statements.append
    try:
        migration.downgrade()
    finally:
        migration.op.execute = original

    sql = "\n".join(statements)
    assert "Cannot downgrade with v0.22 Yahoo ingestion records" in sql
    assert "DROP TABLE data.v022_yahoo_ingestion_plan" in sql
