from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from style_rotation.backtest.repository import BacktestRepository
from style_rotation.backtest.types import (
    BacktestResult,
    DailyNavRecord,
    DailyPositionRecord,
    ExecutionRecord,
    TradeRecord,
)
from style_rotation.persistence.models import (
    CleaningVersion,
    DataVersion,
    EngineVersion,
    Experiment,
    FactorVersion,
    StrategyVersion,
)
from style_rotation.persistence.session import create_session_factory

DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_backtest_repository_atomically_publishes_all_result_layers() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1))
    session_factory = create_session_factory(engine)
    token = uuid.uuid4().hex
    data_version_id = uuid.uuid4()
    cleaning_version_id = uuid.uuid4()
    factor_version_id = uuid.uuid4()
    strategy_version_id = uuid.uuid4()
    engine_version_id = uuid.uuid4()
    experiment_id = uuid.uuid4()
    archive_id = uuid.uuid4()
    first = date(2025, 1, 6)
    second = date(2025, 1, 7)

    with session_factory.begin() as session:
        session.add(
            DataVersion(
                data_version_id=data_version_id,
                version_key=f"backtest-test-data-{token}",
                provider="backtest_test",
                content_hash=token.ljust(64, "0"),
                requested_at=datetime.now(UTC),
                coverage_start=first,
                coverage_end=second,
                request_parameters={},
                source_metadata={},
                status="published",
                published_at=datetime.now(UTC),
            )
        )
        session.add(
            CleaningVersion(
                cleaning_version_id=cleaning_version_id,
                version_key=f"backtest-test-cleaning-{token}",
                rules_hash="a" * 64,
                code_hash="b" * 64,
                configuration={},
            )
        )
        session.add(
            FactorVersion(
                factor_version_id=factor_version_id,
                version_key=f"backtest-test-factor-{token}",
                registry_hash="c" * 64,
                code_hash="d" * 64,
            )
        )
        session.add(
            StrategyVersion(
                strategy_version_id=strategy_version_id,
                version_key=f"backtest-test-strategy-{token}",
                configuration_hash="e" * 64,
                configuration={},
            )
        )
        session.add(
            EngineVersion(
                engine_version_id=engine_version_id,
                version_key=f"backtest-test-engine-{token}",
                git_commit="f" * 40,
                dependency_lock_hash="1" * 64,
                code_hash="2" * 64,
                python_version="3.13",
            )
        )
        session.add(
            Experiment(
                experiment_id=experiment_id,
                name=f"backtest-test-{token}",
                system_version="0.1.0",
                status="running",
            )
        )

    result = BacktestResult(
        daily_nav=(
            DailyNavRecord(
                first,
                Decimal("0.0095"),
                Decimal("0.0090"),
                Decimal("1.0095"),
                Decimal("1.0090"),
                Decimal("1"),
                Decimal("0.0005"),
                Decimal("0.0005"),
            ),
            DailyNavRecord(
                second,
                Decimal("0.01"),
                Decimal("0.01"),
                Decimal("1.019595"),
                Decimal("1.01909"),
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
            ),
        ),
        daily_positions=tuple(
            DailyPositionRecord(nav_date, sleeve, weight)
            for nav_date in (first, second)
            for sleeve, weight in (
                ("IWF", Decimal("1")),
                ("IWD", Decimal("0")),
                ("IWO", Decimal("0")),
                ("IWN", Decimal("0")),
                ("RESERVE", Decimal("0")),
            )
        ),
        executions=(
            ExecutionRecord(
                date(2025, 1, 3),
                first,
                Decimal("1"),
                Decimal("0.0005"),
                Decimal("0.0005"),
                Decimal("1"),
                Decimal("1"),
            ),
        ),
        trades=(
            TradeRecord(
                first,
                "IWF",
                "buy",
                Decimal("100"),
                Decimal("0"),
                Decimal("1"),
                Decimal("1"),
            ),
        ),
    )

    repository = BacktestRepository(session_factory)
    run_id = repository.publish_run(
        run_fields={
            "experiment_id": experiment_id,
            "data_version_id": data_version_id,
            "cleaning_version_id": cleaning_version_id,
            "factor_version_id": factor_version_id,
            "strategy_version_id": strategy_version_id,
            "engine_version_id": engine_version_id,
            "run_fingerprint": token.ljust(64, "9"),
            "factor_variant_key": "integration_variant",
            "warmup_start_date": date(2024, 1, 1),
            "official_signal_start_date": date(2025, 1, 3),
            "first_execution_date": first,
            "official_end_date": second,
            "rebalance_frequency": "weekly",
            "strategy_template": "cross_sectional",
            "transaction_cost_bps": Decimal("5"),
            "configuration": {"cost_model": "single_sided_turnover"},
        },
        result=result,
        equal_weight_benchmark=result,
        spy_benchmark=result,
    )

    with engine.begin() as connection:
        parameters = {"run_id": run_id}
        assert connection.execute(
            text("SELECT status FROM backtest_runs WHERE run_id=:run_id"), parameters
        ).scalar_one() == "completed"
        assert connection.execute(
            text("SELECT count(*) FROM daily_nav WHERE run_id=:run_id"), parameters
        ).scalar_one() == 2
        assert connection.execute(
            text("SELECT count(*) FROM daily_positions WHERE run_id=:run_id"), parameters
        ).scalar_one() == 10
        assert connection.execute(
            text("SELECT count(*) FROM rebalance_executions WHERE run_id=:run_id"), parameters
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM trades WHERE run_id=:run_id"), parameters
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM benchmark_daily_nav WHERE run_id=:run_id"), parameters
        ).scalar_one() == 4
        assert connection.execute(
            text("SELECT count(*) FROM run_events WHERE run_id=:run_id AND status='completed'"),
            parameters,
        ).scalar_one() == 1

        connection.execute(
            text(
                "INSERT INTO version_archives "
                "(archive_id,system_version,status,archive_uri,manifest_hash,manifest,"
                "verified_at,restore_tested_at) VALUES "
                "(:archive_id,'0.1.0','restore_tested','test://backtest-publication',"
                ":manifest_hash,'{}',now(),now()) ON CONFLICT (system_version) DO NOTHING"
            ),
            {"archive_id": archive_id, "manifest_hash": token.ljust(64, "a")},
        )
        connection.execute(text("DELETE FROM backtest_runs WHERE run_id=:run_id"), parameters)
        connection.execute(
            text("DELETE FROM experiments WHERE experiment_id=:experiment_id"),
            {"experiment_id": experiment_id},
        )
        connection.execute(
            text("DELETE FROM engine_versions WHERE engine_version_id=:engine_version_id"),
            {"engine_version_id": engine_version_id},
        )
        connection.execute(
            text("DELETE FROM strategy_versions WHERE strategy_version_id=:strategy_version_id"),
            {"strategy_version_id": strategy_version_id},
        )
        connection.execute(
            text("DELETE FROM factor_versions WHERE factor_version_id=:factor_version_id"),
            {"factor_version_id": factor_version_id},
        )
        connection.execute(
            text("DELETE FROM cleaning_versions WHERE cleaning_version_id=:cleaning_version_id"),
            {"cleaning_version_id": cleaning_version_id},
        )
        connection.execute(
            text("DELETE FROM data_versions WHERE data_version_id=:data_version_id"),
            {"data_version_id": data_version_id},
        )
        connection.execute(
            text("DELETE FROM version_archives WHERE archive_id=:archive_id"),
            {"archive_id": archive_id},
        )
    engine.dispose()
