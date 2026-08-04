from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from style_rotation.persistence.base import SCHEMA_NAMES
from style_rotation.persistence.database import (
    database_status,
    downgrade_database,
    reset_database,
    upgrade_database,
)

DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


def _reset() -> str:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    return DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def _insert_published(
    connection: psycopg.Connection[tuple[object, ...]],
    artifact_id: uuid.UUID,
    artifact_key: str,
    semantic_fingerprint: str,
    content_hash: str,
) -> None:
    connection.execute(
        """
        INSERT INTO lineage.artifact (
            artifact_id, artifact_type, artifact_key, version_number, status
        ) VALUES (%s, 'test', %s, 1, 'draft')
        """,
        (artifact_id, artifact_key),
    )
    connection.execute(
        """
        SELECT set_config('style_rotation.status_event_id', %s, true),
               set_config('style_rotation.status_reason', 'test publication', true)
        """,
        (str(uuid.uuid4()),),
    )
    connection.execute(
        """
        UPDATE lineage.artifact
        SET status = 'published', semantic_fingerprint = %s, content_hash = %s,
            published_at = now()
        WHERE artifact_id = %s
        """,
        (semantic_fingerprint, content_hash, artifact_id),
    )


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_empty_database_migrates_to_one_clean_v02_head() -> None:
    psycopg_url = _reset()
    assert DATABASE_URL is not None
    status = database_status(DATABASE_URL)
    assert status.current_revision == "20260804_15_v02_model_data"
    assert status.head_revisions == ("20260804_15_v02_model_data",)
    assert status.present_schemas == SCHEMA_NAMES
    assert status.missing_schemas == ()

    with psycopg.connect(psycopg_url) as connection:
        schemas = {
            row[0]
            for row in connection.execute(
                "SELECT schema_name FROM information_schema.schemata"
            ).fetchall()
        }
        assert set(SCHEMA_NAMES).issubset(schemas)
        public_tables = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
        assert public_tables == [("alembic_version",)]


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_lineage_constraints_and_restrict_foreign_keys_are_enforced() -> None:
    psycopg_url = _reset()
    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    dependency_id = uuid.uuid4()

    with psycopg.connect(psycopg_url) as connection:
        _insert_published(connection, parent_id, "parent", "a" * 64, "b" * 64)
        connection.execute(
            """
            INSERT INTO lineage.artifact (
                artifact_id, artifact_type, artifact_key, version_number, status
            ) VALUES (%s, 'test', 'child', 1, 'draft')
            """,
            (child_id,),
        )
        connection.execute(
            """
            INSERT INTO lineage.artifact_dependency (
                artifact_dependency_id, artifact_id, depends_on_artifact_id, role
            ) VALUES (%s, %s, %s, 'input')
            """,
            (dependency_id, child_id, parent_id),
        )
        connection.execute(
            """
            SELECT set_config('style_rotation.status_event_id', %s, true),
                   set_config('style_rotation.status_reason', 'test publication', true)
            """,
            (str(uuid.uuid4()),),
        )
        connection.execute(
            """
            UPDATE lineage.artifact SET status = 'published', semantic_fingerprint = %s,
                content_hash = %s, published_at = now() WHERE artifact_id = %s
            """,
            ("c" * 64, "d" * 64, child_id),
        )

        connection.execute("SAVEPOINT restrict_check")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute("DELETE FROM lineage.artifact WHERE artifact_id = %s", (parent_id,))
        connection.execute("ROLLBACK TO SAVEPOINT restrict_check")

        connection.execute("SAVEPOINT self_check")
        with pytest.raises(psycopg.DatabaseError):
            connection.execute(
                """
                INSERT INTO lineage.artifact_dependency (
                    artifact_dependency_id, artifact_id, depends_on_artifact_id, role
                ) VALUES (%s, %s, %s, 'invalid')
                """,
                (uuid.uuid4(), child_id, child_id),
            )
        connection.execute("ROLLBACK TO SAVEPOINT self_check")

        connection.execute("SAVEPOINT hash_check")
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO lineage.artifact (
                    artifact_id, artifact_type, artifact_key, version_number, status,
                    semantic_fingerprint
                ) VALUES (%s, 'test', 'bad-hash', 1, 'draft', 'bad')
                """,
                (uuid.uuid4(),),
            )
        connection.execute("ROLLBACK TO SAVEPOINT hash_check")

        connection.rollback()


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_ops_engine_and_run_constraints_are_enforced() -> None:
    psycopg_url = _reset()
    artifact_id = uuid.uuid4()
    engine_definition_id = uuid.uuid4()
    engine_version_id = uuid.uuid4()
    run_attempt_id = uuid.uuid4()
    fingerprint = "f" * 64

    with psycopg.connect(psycopg_url) as connection:
        connection.execute(
            """
            INSERT INTO lineage.artifact (
                artifact_id, artifact_type, artifact_key, version_number, status
            ) VALUES (%s, 'test', 'test-engine-v1', 1, 'draft')
            """,
            (artifact_id,),
        )
        connection.execute(
            """
            INSERT INTO ops.engine_definition (
                engine_definition_id, engine_key, name, engine_type
            ) VALUES (%s, 'test-engine', 'Test Engine', 'test')
            """,
            (engine_definition_id,),
        )
        connection.execute(
            """
            INSERT INTO ops.engine_version (
                engine_version_id, engine_definition_id, artifact_id, version_number,
                semantic_version, git_commit, dependency_lock_hash, schema_revision,
                configuration_hash, numerical_environment
            ) VALUES (%s, %s, %s, 1, '0.2.0', 'test', %s,
                      '20260802_01_v02_foundation', %s, '{}'::jsonb)
            """,
            (engine_version_id, engine_definition_id, artifact_id, "3" * 64, "4" * 64),
        )
        connection.execute(
            """
            SELECT set_config('style_rotation.status_event_id', %s, true),
                   set_config('style_rotation.status_reason', 'test publication', true)
            """,
            (str(uuid.uuid4()),),
        )
        connection.execute(
            """
            UPDATE lineage.artifact
            SET status = 'published', semantic_fingerprint = %s, content_hash = %s,
                published_at = now()
            WHERE artifact_id = %s
            """,
            ("1" * 64, "2" * 64, artifact_id),
        )
        connection.execute(
            """
            INSERT INTO ops.run_attempt (
                run_attempt_id, engine_version_id, run_type, request_fingerprint,
                attempt_number, status
            ) VALUES (%s, %s, 'test', %s, 1, 'queued')
            """,
            (run_attempt_id, engine_version_id, fingerprint),
        )

        connection.execute("SAVEPOINT status_check")
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "UPDATE ops.run_attempt SET status = 'unknown' WHERE run_attempt_id = %s",
                (run_attempt_id,),
            )
        connection.execute("ROLLBACK TO SAVEPOINT status_check")

        connection.execute("SAVEPOINT duplicate_check")
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                """
                INSERT INTO ops.run_attempt (
                    run_attempt_id, engine_version_id, run_type, request_fingerprint,
                    attempt_number, status
                ) VALUES (%s, %s, 'test', %s, 1, 'queued')
                """,
                (uuid.uuid4(), engine_version_id, fingerprint),
            )
        connection.execute("ROLLBACK TO SAVEPOINT duplicate_check")

        connection.execute("SAVEPOINT definition_check")
        with pytest.raises(psycopg.DatabaseError):
            connection.execute(
                "UPDATE ops.engine_definition SET name = 'changed' WHERE engine_definition_id = %s",
                (engine_definition_id,),
            )
        connection.execute("ROLLBACK TO SAVEPOINT definition_check")

        connection.rollback()


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_migration_can_downgrade_to_base_and_upgrade_again() -> None:
    _reset()
    assert DATABASE_URL is not None
    downgrade_database(DATABASE_URL, "base")
    downgraded = database_status(DATABASE_URL)
    assert downgraded.current_revision is None
    assert downgraded.missing_schemas == SCHEMA_NAMES

    upgrade_database(DATABASE_URL)
    upgraded = database_status(DATABASE_URL)
    assert upgraded.current_revision == "20260804_15_v02_model_data"
    assert upgraded.missing_schemas == ()
