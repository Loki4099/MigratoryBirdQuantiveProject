from __future__ import annotations

import os
import uuid
from datetime import date

import psycopg
import pytest
from psycopg.types.json import Jsonb

DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_completed_run_requires_restore_tested_archive_before_deletion() -> None:
    assert DATABASE_URL is not None
    ids = {
        name: uuid.uuid4()
        for name in (
            "data",
            "clean",
            "factor",
            "strategy",
            "engine",
            "experiment",
            "run",
            "archive",
        )
    }

    with psycopg.connect(DATABASE_URL) as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO data_versions (
                data_version_id, version_key, provider, content_hash, requested_at,
                coverage_start, coverage_end, request_parameters, source_metadata
            ) VALUES (%s, %s, 'test', %s, now(), %s, %s, %s, %s)
            """,
            (
                ids["data"],
                f"data-{ids['data']}",
                uuid.uuid4().hex * 2,
                date(2000, 1, 1),
                date(2026, 7, 31),
                Jsonb({}),
                Jsonb({}),
            ),
        )
        cursor.execute(
            """
            INSERT INTO cleaning_versions (
                cleaning_version_id, version_key, rules_hash, code_hash, configuration
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (ids["clean"], f"clean-{ids['clean']}", "a" * 64, "b" * 64, Jsonb({})),
        )
        cursor.execute(
            """
            INSERT INTO factor_versions (
                factor_version_id, version_key, registry_hash, code_hash
            ) VALUES (%s, %s, %s, %s)
            """,
            (ids["factor"], f"factor-{ids['factor']}", "c" * 64, "d" * 64),
        )
        cursor.execute(
            """
            INSERT INTO strategy_versions (
                strategy_version_id, version_key, configuration_hash, configuration
            ) VALUES (%s, %s, %s, %s)
            """,
            (ids["strategy"], f"strategy-{ids['strategy']}", "e" * 64, Jsonb({})),
        )
        cursor.execute(
            """
            INSERT INTO engine_versions (
                engine_version_id, version_key, git_commit, dependency_lock_hash,
                code_hash, python_version
            ) VALUES (%s, %s, %s, %s, %s, '3.12')
            """,
            (ids["engine"], f"engine-{ids['engine']}", "f" * 64, "1" * 64, "2" * 64),
        )
        cursor.execute(
            """
            INSERT INTO experiments (experiment_id, name, system_version, status)
            VALUES (%s, 'integration test', '0.1.0', 'running')
            """,
            (ids["experiment"],),
        )
        cursor.execute(
            """
            INSERT INTO backtest_runs (
                run_id, experiment_id, data_version_id, cleaning_version_id,
                factor_version_id, strategy_version_id, engine_version_id,
                run_fingerprint, factor_variant_key, warmup_start_date,
                official_signal_start_date, first_execution_date, official_end_date,
                rebalance_frequency, strategy_template, transaction_cost_bps,
                configuration, status
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, 'momentum_simple_20',
                %s, %s, %s, %s, 'weekly', 'cross_sectional', 5, %s, 'pending'
            )
            """,
            (
                ids["run"],
                ids["experiment"],
                ids["data"],
                ids["clean"],
                ids["factor"],
                ids["strategy"],
                ids["engine"],
                uuid.uuid4().hex * 2,
                date(2000, 1, 1),
                date(2001, 1, 1),
                date(2001, 1, 2),
                date(2026, 7, 31),
                Jsonb({}),
            ),
        )
        cursor.execute(
            "UPDATE backtest_runs SET status = 'running' WHERE run_id = %s", (ids["run"],)
        )
        cursor.execute(
            "UPDATE backtest_runs SET status = 'completed' WHERE run_id = %s", (ids["run"],)
        )

        cursor.execute("SAVEPOINT immutable_check")
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            cursor.execute(
                "UPDATE backtest_runs SET factor_variant_key = 'changed' WHERE run_id = %s",
                (ids["run"],),
            )
        cursor.execute("ROLLBACK TO SAVEPOINT immutable_check")

        cursor.execute("SAVEPOINT delete_check")
        with pytest.raises(psycopg.errors.RaiseException, match="restore-tested"):
            cursor.execute("DELETE FROM backtest_runs WHERE run_id = %s", (ids["run"],))
        cursor.execute("ROLLBACK TO SAVEPOINT delete_check")

        cursor.execute(
            """
            INSERT INTO version_archives (
                archive_id, system_version, status, archive_uri, manifest_hash,
                manifest, verified_at, restore_tested_at
            ) VALUES (%s, '0.1.0', 'restore_tested', 'test://archive', %s, %s, now(), now())
            """,
            (ids["archive"], uuid.uuid4().hex * 2, Jsonb({"system_version": "0.1.0"})),
        )
        cursor.execute("DELETE FROM backtest_runs WHERE run_id = %s", (ids["run"],))
        cursor.execute("SELECT count(*) FROM backtest_runs WHERE run_id = %s", (ids["run"],))
        assert cursor.fetchone() == (0,)

        connection.rollback()
