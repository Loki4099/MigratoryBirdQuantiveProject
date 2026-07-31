from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.metrics.repository import MetricsRepository
from style_rotation.metrics.types import (
    FactorDiagnosticPeriod,
    FactorDiagnosticSummary,
    PerformanceMetricResult,
    SourceRunSet,
)
from style_rotation.persistence.models import (
    BacktestRun,
    CleaningVersion,
    DailyNav,
    DataVersion,
    EngineVersion,
    Experiment,
    FactorDefinition,
    FactorVariant,
    FactorVersion,
    MetricVersion,
    SignalDataset,
    StrategyVersion,
)
from style_rotation.persistence.session import create_session_factory

DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_metric_results_publish_reuse_and_protect_completed_inputs() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1))
    session_factory = create_session_factory(engine)
    token = uuid.uuid4().hex
    ids = {name: uuid.uuid4() for name in (
        "data",
        "cleaning",
        "factor",
        "definition",
        "variant",
        "strategy",
        "engine",
        "experiment",
        "run",
        "metric_version",
        "archive",
    )}
    first = date(2025, 1, 3)
    end = date(2025, 1, 6)
    system_version = f"test-{token}"

    with session_factory.begin() as session:
        session.add(
            DataVersion(
                data_version_id=ids["data"],
                version_key=f"metrics-test-data-{token}",
                provider="metrics_test",
                content_hash=sha256_hexdigest({"data": token}),
                requested_at=datetime.now(UTC),
                coverage_start=date(2024, 1, 1),
                coverage_end=end,
                request_parameters={},
                source_metadata={},
                status="published",
                published_at=datetime.now(UTC),
            )
        )
        session.add(
            CleaningVersion(
                cleaning_version_id=ids["cleaning"],
                version_key=f"metrics-test-cleaning-{token}",
                rules_hash="a" * 64,
                code_hash="b" * 64,
                configuration={},
            )
        )
        session.add(
            FactorVersion(
                factor_version_id=ids["factor"],
                version_key=f"metrics-test-factor-{token}",
                registry_hash="c" * 64,
                code_hash="d" * 64,
            )
        )
        session.add(
            StrategyVersion(
                strategy_version_id=ids["strategy"],
                version_key=f"metrics-test-strategy-{token}",
                configuration_hash="e" * 64,
                configuration={},
            )
        )
        session.add(
            EngineVersion(
                engine_version_id=ids["engine"],
                version_key=f"metrics-test-engine-{token}",
                git_commit="f" * 40,
                dependency_lock_hash="1" * 64,
                code_hash="2" * 64,
                python_version="3.12",
            )
        )
        session.add(
            Experiment(
                experiment_id=ids["experiment"],
                name=f"metrics-test-{token}",
                system_version=system_version,
                status="running",
            )
        )
        session.flush()
        session.add(
            FactorDefinition(
                factor_definition_id=ids["definition"],
                factor_version_id=ids["factor"],
                definition_key=f"metrics-test-definition-{token}",
                family="test",
                name="Metrics test factor",
                description="Integration fixture",
                formula="fixture",
                required_fields=["close_adj"],
                direction="higher_is_better",
                implementation_key="fixture",
            )
        )
        session.flush()
        session.add(
            FactorVariant(
                factor_variant_id=ids["variant"],
                factor_version_id=ids["factor"],
                factor_definition_id=ids["definition"],
                variant_key=f"metrics_test_variant_{token}",
                parameters={"window": 2},
                minimum_observations=2,
            )
        )
        session.add(
            SignalDataset(
                data_version_id=ids["data"],
                cleaning_version_id=ids["cleaning"],
                factor_version_id=ids["factor"],
                strategy_version_id=ids["strategy"],
                content_hash=sha256_hexdigest({"signal": token}),
                first_signal_date=date(2025, 1, 2),
                first_execution_date=first,
                coverage_end=end,
                event_count=1,
                position_count=4,
                status="published",
            )
        )
        session.add(
            BacktestRun(
                run_id=ids["run"],
                experiment_id=ids["experiment"],
                data_version_id=ids["data"],
                cleaning_version_id=ids["cleaning"],
                factor_version_id=ids["factor"],
                strategy_version_id=ids["strategy"],
                engine_version_id=ids["engine"],
                run_fingerprint=sha256_hexdigest({"run": token}),
                factor_variant_key=f"metrics_test_variant_{token}",
                warmup_start_date=date(2024, 1, 1),
                official_signal_start_date=date(2025, 1, 2),
                first_execution_date=first,
                official_end_date=end,
                rebalance_frequency="weekly",
                strategy_template="cross_sectional",
                transaction_cost_bps=Decimal(5),
                configuration={},
                status="running",
                started_at=datetime.now(UTC),
            )
        )
        session.flush()
        session.add(
            DailyNav(
                run_id=ids["run"],
                nav_date=first,
                gross_daily_return=Decimal("0.01"),
                net_daily_return=Decimal("0.0095"),
                gross_nav=Decimal("1.01"),
                net_nav=Decimal("1.0095"),
                turnover=Decimal(1),
                transaction_cost_fraction=Decimal("0.0005"),
                transaction_cost_amount=Decimal("0.0005"),
            )
        )
        session.add(
            MetricVersion(
                metric_version_id=ids["metric_version"],
                version_key=f"metrics-test-version-{token}",
                methodology_hash="3" * 64,
                code_hash="4" * 64,
                dependency_lock_hash="5" * 64,
                git_commit="6" * 40,
                python_version="3.12",
                configuration={},
            )
        )
    with session_factory.begin() as session:
        run = session.get(BacktestRun, ids["run"])
        assert run is not None
        run.status = "completed"
        run.completed_at = datetime.now(UTC)

    source = SourceRunSet(
        experiment_id=ids["experiment"],
        data_version_id=ids["data"],
        cleaning_version_id=ids["cleaning"],
        factor_version_id=ids["factor"],
        strategy_version_id=ids["strategy"],
        source_engine_version_id=ids["engine"],
        runs=(),
    )
    summary = FactorDiagnosticSummary(
        factor_variant_id=ids["variant"],
        variant_key=f"metrics_test_variant_{token}",
        rebalance_frequency="weekly",
        period_count=1,
        valid_ic_count=1,
        undefined_ic_count=0,
        mean_rank_ic=Decimal(1),
        positive_ic_ratio=Decimal(1),
        mean_top_bottom_return_spread=Decimal("0.02"),
        ic_summary_reason_code=None,
    )
    periods = (
        FactorDiagnosticPeriod(
            factor_variant_id=ids["variant"],
            variant_key=summary.variant_key,
            rebalance_frequency="weekly",
            signal_date=date(2025, 1, 2),
            execution_date=first,
            next_execution_date=end,
            rank_ic=Decimal(1),
            rank_ic_reason_code=None,
            top_bottom_return_spread=Decimal("0.02"),
        ),
    )
    repository = MetricsRepository(session_factory)
    diagnostic_fingerprint = sha256_hexdigest({"diagnostic": token})
    diagnostic_set_id = repository.publish_diagnostic_set(
        source=source,
        metric_version_id=ids["metric_version"],
        fingerprint=diagnostic_fingerprint,
        summary=summary,
        periods=periods,
    )
    metrics = (
        PerformanceMetricResult(
            "strategy",
            "net",
            "cumulative_return",
            Decimal("0.0095"),
            "defined",
            None,
            1,
            "decimal_return",
        ),
        PerformanceMetricResult(
            "strategy",
            "net",
            "sharpe_ratio",
            None,
            "undefined",
            "insufficient_observations",
            1,
            "ratio",
        ),
    )
    metric_fingerprint = sha256_hexdigest({"metric": token})
    publication_id = repository.publish_run_metrics(
        run_id=ids["run"],
        metric_version_id=ids["metric_version"],
        diagnostic_set_id=diagnostic_set_id,
        metric_fingerprint=metric_fingerprint,
        input_manifest_hash=sha256_hexdigest({"input": token}),
        metrics=metrics,
    )
    assert repository.publication_exists(ids["run"], ids["metric_version"])

    with engine.connect() as connection:
        transaction = connection.begin()
        immutable_statements = (
            "UPDATE daily_nav SET net_nav=2 WHERE run_id=:run",
            "UPDATE metric_versions SET python_version='changed' "
            "WHERE metric_version_id=:metric_version",
            "DELETE FROM metric_versions WHERE metric_version_id=:metric_version",
            "UPDATE factor_diagnostic_sets SET mean_rank_ic=0 "
            "WHERE diagnostic_set_id=:diagnostic_set",
            "DELETE FROM factor_diagnostic_sets WHERE diagnostic_set_id=:diagnostic_set",
            "UPDATE factor_diagnostic_periods SET rank_ic=0 "
            "WHERE diagnostic_set_id=:diagnostic_set",
            "INSERT INTO factor_diagnostic_periods "
            "(diagnostic_set_id,signal_date,execution_date,next_execution_date,rank_ic,"
            "rank_ic_reason_code,top_bottom_return_spread) VALUES "
            "(:diagnostic_set,'2025-01-03','2025-01-04','2025-01-05',0.5,NULL,0.1)",
            "DELETE FROM factor_diagnostic_periods WHERE diagnostic_set_id=:diagnostic_set",
            "UPDATE metric_publications SET content_hash=repeat('0',64) WHERE run_id=:run",
            "DELETE FROM metric_publications WHERE run_id=:run",
            "UPDATE performance_metrics SET unit='changed' WHERE metric_publication_id IN "
            "(SELECT metric_publication_id FROM metric_publications WHERE run_id=:run)",
            "INSERT INTO performance_metrics "
            "(metric_publication_id,series_type,return_basis,metric_key,metric_value,"
            "value_status,reason_code,observation_count,unit) VALUES "
            "(:publication,'strategy','net','rogue_metric',0.1,'defined',NULL,1,'ratio')",
            "DELETE FROM performance_metrics WHERE metric_publication_id IN "
            "(SELECT metric_publication_id FROM metric_publications WHERE run_id=:run)",
        )
        parameters = {
            "run": ids["run"],
            "metric_version": ids["metric_version"],
            "diagnostic_set": diagnostic_set_id,
            "publication": publication_id,
        }
        for statement in immutable_statements:
            savepoint = connection.begin_nested()
            with pytest.raises(ProgrammingError, match="immutable"):
                connection.execute(text(statement), parameters)
            savepoint.rollback()
        transaction.rollback()

    with engine.begin() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM performance_metrics m JOIN metric_publications p "
                "USING(metric_publication_id) WHERE p.run_id=:run_id"
            ),
            {"run_id": ids["run"]},
        ).scalar_one() == 2
        connection.execute(
            text(
                "INSERT INTO version_archives "
                "(archive_id,system_version,status,archive_uri,manifest_hash,manifest,"
                "verified_at,restore_tested_at) VALUES "
                "(:archive_id,:system_version,'restore_tested',:archive_uri,"
                ":manifest_hash,'{}',now(),now())"
            ),
            {
                "archive_id": ids["archive"],
                "system_version": system_version,
                "archive_uri": f"test://metrics/{token}",
                "manifest_hash": sha256_hexdigest({"archive": token}),
            },
        )
        connection.execute(
            text("DELETE FROM backtest_runs WHERE run_id=:run_id"), {"run_id": ids["run"]}
        )
        assert connection.execute(
            text("SELECT count(*) FROM metric_publications WHERE run_id=:run_id"),
            {"run_id": ids["run"]},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM performance_metrics "
                "WHERE metric_publication_id=:publication_id"
            ),
            {"publication_id": publication_id},
        ).scalar_one() == 0
        connection.execute(
            text(
                "DELETE FROM signal_datasets WHERE data_version_id=:data "
                "AND cleaning_version_id=:cleaning AND factor_version_id=:factor "
                "AND strategy_version_id=:strategy"
            ),
            ids,
        )
        assert connection.execute(
            text("SELECT count(*) FROM factor_diagnostic_sets WHERE diagnostic_set_id=:set_id"),
            {"set_id": diagnostic_set_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM factor_diagnostic_periods "
                "WHERE diagnostic_set_id=:set_id"
            ),
            {"set_id": diagnostic_set_id},
        ).scalar_one() == 0
        connection.execute(
            text("DELETE FROM metric_versions WHERE metric_version_id=:metric_version_id"),
            {"metric_version_id": ids["metric_version"]},
        )
        connection.execute(
            text("DELETE FROM experiments WHERE experiment_id=:experiment"), ids
        )
        connection.execute(text("DELETE FROM engine_versions WHERE engine_version_id=:engine"), ids)
        connection.execute(
            text("DELETE FROM strategy_versions WHERE strategy_version_id=:strategy"), ids
        )
        connection.execute(text("DELETE FROM factor_versions WHERE factor_version_id=:factor"), ids)
        connection.execute(
            text("DELETE FROM cleaning_versions WHERE cleaning_version_id=:cleaning"), ids
        )
        connection.execute(text("DELETE FROM data_versions WHERE data_version_id=:data"), ids)
        connection.execute(
            text("DELETE FROM version_archives WHERE archive_id=:archive"), ids
        )
    engine.dispose()
