from __future__ import annotations

import math
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from style_rotation.api.app import create_app
from style_rotation.api.query import ArtifactQueryService
from style_rotation.catalog.bootstrap import publish_catalogs
from style_rotation.catalog.eligibility import EligibilityPublicationService
from style_rotation.catalog.scope import publish_research_scope
from style_rotation.data.bundle import (
    ReservePublicationService,
    publish_data_bundle,
    publish_reserve_model,
)
from style_rotation.data.calendar import CalendarPublicationService, XNYSCalendarGenerator
from style_rotation.data.forward_return_engine import (
    build_forward_return_engine_spec,
    publish_forward_return_engine,
)
from style_rotation.data.forward_return_publication import (
    ForwardReturnDatasetPublicationService,
    publish_forward_return_catalog,
)
from style_rotation.data.publication import CanonicalDataPublicationService
from style_rotation.data.service import SnapshotInput, SourceSnapshotService, publish_data_contracts
from style_rotation.experiment.benchmark_engine import (
    build_benchmark_target_engine_spec,
    publish_benchmark_target_engine,
)
from style_rotation.experiment.benchmark_publication import (
    BenchmarkTargetPublicationService,
    publish_benchmark_catalog,
)
from style_rotation.experiment.cost_publication import (
    NetCostPathPublicationService,
    publish_cost_catalog,
)
from style_rotation.experiment.engine import (
    build_accounting_engine_spec,
    publish_accounting_engine,
)
from style_rotation.experiment.performance_engine import (
    build_performance_engine_spec,
    publish_performance_engine,
)
from style_rotation.experiment.performance_publication import (
    IntervalPerformancePublicationService,
    publish_performance_metric_catalog,
)
from style_rotation.experiment.publication import GrossPathPublicationService
from style_rotation.experiment.suite_publication import (
    ExperimentCellRequest,
    publish_experiment_suite,
)
from style_rotation.factor.diagnostic_publication import FactorDiagnosticPublicationService
from style_rotation.factor.engine import (
    build_factor_diagnostic_engine_spec,
    build_factor_engine_spec,
    publish_factor_diagnostic_engine,
    publish_factor_engine,
)
from style_rotation.factor.publication import FactorDatasetPublicationService
from style_rotation.factor.service import publish_factor_catalog
from style_rotation.lineage.service import ArtifactService
from style_rotation.model.diagnostic_publication import ModelDiagnosticPublicationService
from style_rotation.model.engine import (
    build_model_engine_spec,
    build_model_evaluation_engine_spec,
    publish_model_engine,
    publish_model_evaluation_engine,
)
from style_rotation.model.publication import ModelDatasetPublicationService
from style_rotation.model.service import publish_model_catalog
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.signal.diagnostic_engine import (
    build_signal_evaluation_engine_spec,
    publish_signal_evaluation_engine,
)
from style_rotation.signal.diagnostic_publication import SignalDiagnosticPublicationService
from style_rotation.signal.engine import build_signal_engine_spec, publish_signal_engine
from style_rotation.signal.publication import SignalDatasetPublicationService
from style_rotation.signal.service import publish_signal_catalog
from style_rotation.strategy.engine import (
    build_strategy_target_engine_spec,
    publish_strategy_target_engine,
)
from style_rotation.strategy.product_service import publish_strategy_product
from style_rotation.strategy.service import publish_strategy_catalog
from style_rotation.strategy.target_publication import StrategyTargetPublicationService

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]


def _snapshot(
    service: SourceSnapshotService,
    subject: str,
    payload: bytes,
    fetched_at: datetime,
) -> uuid.UUID:
    market = subject != "DGS3MO"
    result = service.publish(
        SnapshotInput(
            series_key="us_etf_daily_market" if market else "dgs3mo_daily",
            series_version=1,
            snapshot_key=f"factor-{subject.lower()}-{fetched_at:%Y%m%dT%H%M%S%fZ}",
            requested_at=fetched_at - timedelta(seconds=1),
            fetched_at=fetched_at,
            as_of_at=fetched_at,
            media_type="text/csv",
            request_parameters={"tickers": subject} if market else {"id": subject},
            response_metadata={"fixture": True},
            raw_payload=payload,
        )
    )
    return result.artifact_id


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_factor_engine_publishes_all_catalog_variants_atomically_and_reuses_them() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    publish_catalogs(ArtifactService(engine), PROJECT_ROOT / "v0.2" / "catalogs")
    scope = publish_research_scope(
        engine, PROJECT_ROOT / "v0.2" / "catalogs" / "research_scope.v0.2.0.json"
    )
    publish_data_contracts(
        engine, PROJECT_ROOT / "v0.2" / "catalogs" / "data_contracts.v0.2.0.json"
    )
    factor_catalog = publish_factor_catalog(
        engine, PROJECT_ROOT / "v0.2" / "catalogs" / "factors.v0.2.0.json"
    )
    signal_catalog = publish_signal_catalog(
        engine, PROJECT_ROOT / "v0.2" / "catalogs" / "signals.v0.2.0.json"
    )
    model_catalog = publish_model_catalog(
        engine, PROJECT_ROOT / "v0.2" / "catalogs" / "models.v0.2.0.json"
    )
    strategy_catalog = publish_strategy_catalog(
        engine, PROJECT_ROOT / "v0.2" / "catalogs" / "strategies.v0.2.0.json"
    )
    target_catalog = publish_forward_return_catalog(
        engine, PROJECT_ROOT / "v0.2" / "catalogs" / "forward_returns.v0.2.0.json"
    )

    generated = XNYSCalendarGenerator().generate(date(2024, 1, 2), date(2025, 3, 31))
    assert len(generated.sessions) > 260
    calendar = CalendarPublicationService(engine).publish(generated)
    fetched = datetime(2026, 8, 3, 2, 0, tzinfo=UTC)
    snapshots = SourceSnapshotService(engine)
    header = "session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
    market_ids: list[uuid.UUID] = []
    symbols = ("IWF", "IWD", "IWO", "IWN", "SPY")
    for ordinal, symbol in enumerate(symbols):
        rows = []
        for index, session in enumerate(generated.sessions):
            close = 100 + ordinal * 7 + index * 0.05 + (index % 13) * 0.03
            rows.append(
                f"{session.session_date},{close - 0.2:.6f},{close + 0.5:.6f},"
                f"{close - 0.5:.6f},{close:.6f},{close:.6f},"
                f"{1000000 + ordinal * 10000 + index * 100},0,0\n"
            )
        market_ids.append(
            _snapshot(
                snapshots,
                symbol,
                (header + "".join(rows)).encode(),
                fetched + timedelta(microseconds=ordinal),
            )
        )
    first_day = generated.sessions[0].session_date - timedelta(days=3)
    final_day = generated.sessions[-1].session_date
    rate_rows = ["observation_date,DGS3MO\n"]
    current = first_day
    while current <= final_day:
        rate_rows.append(f"{current},4.00\n")
        current += timedelta(days=1)
    rate_snapshot = _snapshot(
        snapshots,
        "DGS3MO",
        "".join(rate_rows).encode(),
        fetched + timedelta(microseconds=10),
    )
    canonical = CanonicalDataPublicationService(engine)
    market = canonical.publish_market(tuple(market_ids), calendar.artifact_id, version_number=1)
    rate = canonical.publish_rate(rate_snapshot, version_number=1)
    _model_definition, reserve_model = publish_reserve_model(engine)
    reserve = ReservePublicationService(engine).publish(
        rate.artifact_id, calendar.artifact_id, reserve_model.artifact_id, version_number=1
    )
    _bundle_definition, bundle = publish_data_bundle(
        engine,
        market.artifact_id,
        rate.artifact_id,
        reserve.artifact_id,
        calendar.artifact_id,
        version_number=1,
    )
    forward_engine_spec = build_forward_return_engine_spec(
        "a" * 40,
        PROJECT_ROOT / "requirements.lock",
        "20260804_13_v02_signal_eval",
    )
    forward_engine = publish_forward_return_engine(engine, forward_engine_spec)
    assert publish_forward_return_engine(engine, forward_engine_spec).reused is True
    forward_service = ForwardReturnDatasetPublicationService(engine)
    forward_start = generated.sessions[20].session_date
    forward_end = generated.sessions[-1].session_date
    forward = forward_service.publish(
        target_catalog.release_artifact_id,
        uuid.UUID(scope[1]["artifact_id"]),
        bundle.artifact_id,
        forward_engine.artifact_id,
        requested_start=forward_start,
        requested_end=forward_end,
    )
    forward_reused = forward_service.publish(
        target_catalog.release_artifact_id,
        uuid.UUID(scope[1]["artifact_id"]),
        bundle.artifact_id,
        forward_engine.artifact_id,
        requested_start=forward_start,
        requested_end=forward_end,
    )
    assert {item.target_key for item in forward} == {
        "weekly_next_open_to_next_open",
        "monthly_next_open_to_next_open",
    }
    assert all(item.row_count > 0 and item.row_count % 5 == 0 for item in forward)
    assert not any(item.reused for item in forward)
    assert all(item.reused for item in forward_reused)
    assert [item.artifact_id for item in forward] == [item.artifact_id for item in forward_reused]
    with engine.connect() as connection:
        forward_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM data.forward_return_dataset), "
                "(SELECT count(*) FROM data.forward_return_value), "
                "(SELECT count(DISTINCT value.asset_id) FROM data.forward_return_value value), "
                "(SELECT count(*) FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = "
                "dependency.artifact_id WHERE artifact.artifact_type = "
                "'forward_return_dataset')"
            )
        ).one()
    assert forward_counts == (2, sum(item.row_count for item in forward), 5, 12)
    requested_start = generated.sessions[270].session_date
    requested_end = generated.sessions[277].session_date
    eligibility = EligibilityPublicationService(engine).publish(
        uuid.UUID(scope[1]["artifact_id"]),
        uuid.UUID(scope[2]["artifact_id"]),
        bundle.artifact_id,
        requested_start=requested_start,
        requested_end=requested_end,
        warmup_observations=253,
        version_number=1,
    )
    engine_spec = build_factor_engine_spec(
        "a" * 40,
        PROJECT_ROOT / "requirements.lock",
        "20260804_13_v02_signal_eval",
    )
    factor_engine = publish_factor_engine(engine, engine_spec)
    assert publish_factor_engine(engine, engine_spec).reused is True

    service = FactorDatasetPublicationService(engine)
    first = service.publish(
        factor_catalog.release_artifact_id,
        bundle.artifact_id,
        eligibility.artifact_id,
        factor_engine.artifact_id,
    )
    second = service.publish(
        factor_catalog.release_artifact_id,
        bundle.artifact_id,
        eligibility.artifact_id,
        factor_engine.artifact_id,
    )

    assert len(first) == 28
    assert {item.variant_key for item in first} == {item.variant_key for item in second}
    assert all(item.row_count == 40 for item in first)
    assert not any(item.reused for item in first)
    assert all(item.reused for item in second)
    assert [item.artifact_id for item in first] == [item.artifact_id for item in second]

    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM factor.factor_dataset), "
                "(SELECT count(*) FROM factor.factor_value), "
                "(SELECT count(*) FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = dependency.artifact_id "
                "WHERE artifact.artifact_type = 'factor_dataset')"
            )
        ).one()
        sample = connection.execute(
            text(
                "SELECT value.value FROM factor.factor_value value "
                "JOIN factor.factor_dataset dataset ON dataset.factor_dataset_id = "
                "value.factor_dataset_id JOIN factor.factor_variant variant ON "
                "variant.factor_variant_id = dataset.factor_variant_id "
                "JOIN catalog.asset asset ON asset.asset_id = value.asset_id "
                "WHERE variant.variant_key = 'total_return__w252' "
                "AND asset.asset_key = 'iwf' AND value.observation_date = :day"
            ),
            {"day": requested_start},
        ).scalar_one()
    assert counts == (28, 1120, 140)
    expected = (100 + 270 * 0.05 + (270 % 13) * 0.03) / (100 + 18 * 0.05 + (18 % 13) * 0.03) - 1
    assert math.isclose(sample, expected, rel_tol=0, abs_tol=1e-12)

    signal_engine_spec = build_signal_engine_spec(
        "a" * 40,
        PROJECT_ROOT / "requirements.lock",
        "20260804_13_v02_signal_eval",
    )
    signal_engine = publish_signal_engine(engine, signal_engine_spec)
    assert publish_signal_engine(engine, signal_engine_spec).reused is True
    signal_service = SignalDatasetPublicationService(engine)
    signals = signal_service.publish(
        signal_catalog.release_artifact_id,
        factor_catalog.release_artifact_id,
        bundle.artifact_id,
        eligibility.artifact_id,
        factor_engine.artifact_id,
        signal_engine.artifact_id,
    )
    signals_reused = signal_service.publish(
        signal_catalog.release_artifact_id,
        factor_catalog.release_artifact_id,
        bundle.artifact_id,
        eligibility.artifact_id,
        factor_engine.artifact_id,
        signal_engine.artifact_id,
    )
    assert len(signals) == 51
    assert not any(item.reused for item in signals)
    assert all(item.reused for item in signals_reused)
    assert [item.artifact_id for item in signals] == [item.artifact_id for item in signals_reused]
    assert sum(item.output_type == "continuous" for item in signals) == 39
    assert sum(item.output_type == "threshold_state" for item in signals) == 4
    assert sum(item.output_type == "crossover_event" for item in signals) == 8
    assert {item.row_count for item in signals if item.output_type != "crossover_event"} == {32}
    assert {item.row_count for item in signals if item.output_type == "crossover_event"} == {28}
    with engine.connect() as connection:
        signal_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM signal.signal_dataset), "
                "(SELECT count(*) FROM signal.signal_value), "
                "(SELECT count(*) FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = dependency.artifact_id "
                "WHERE artifact.artifact_type = 'signal_dataset')"
            )
        ).one()
        continuous_scores = (
            connection.execute(
                text(
                    "SELECT value.score FROM signal.signal_value value "
                    "JOIN signal.signal_dataset dataset ON dataset.signal_dataset_id = "
                    "value.signal_dataset_id JOIN signal.signal_version version ON "
                    "version.signal_version_id = dataset.signal_version_id "
                    "JOIN signal.signal_definition definition ON definition.signal_definition_id = "
                    "version.signal_definition_id WHERE definition.signal_key = "
                    "'return_continuation__total_return__w252' "
                    "AND value.observation_date = :day ORDER BY value.score"
                ),
                {"day": requested_start},
            )
            .scalars()
            .all()
        )
        threshold_values = connection.execute(
            text(
                "SELECT value.score, value.state, value.event FROM signal.signal_value value "
                "JOIN signal.signal_dataset dataset ON dataset.signal_dataset_id = "
                "value.signal_dataset_id JOIN signal.signal_version version ON "
                "version.signal_version_id = dataset.signal_version_id "
                "JOIN signal.signal_definition definition ON definition.signal_definition_id = "
                "version.signal_definition_id WHERE definition.signal_key = "
                "'price_above_ma_state__moving_average_ratio__s1_l200' "
                "AND value.observation_date = :day"
            ),
            {"day": requested_start},
        ).all()
        crossover_start = connection.execute(
            text(
                "SELECT dataset.coverage_start FROM signal.signal_dataset dataset "
                "JOIN signal.signal_version version ON version.signal_version_id = "
                "dataset.signal_version_id JOIN signal.signal_definition definition ON "
                "definition.signal_definition_id = version.signal_definition_id "
                "WHERE definition.signal_key = "
                "'price_cross_above_ma__moving_average_ratio__s1_l200'"
            )
        ).scalar_one()
    assert signal_counts == (51, 1600, 306)
    assert continuous_scores == [
        Decimal("-1.000000000000000000"),
        Decimal("-0.333333333333333333"),
        Decimal("0.333333333333333333"),
        Decimal("1.000000000000000000"),
    ]
    assert threshold_values == [(Decimal("1.000000000000000000"), "positive", None)] * 4
    assert crossover_start == generated.sessions[271].session_date

    model_engine_spec = build_model_engine_spec(
        "a" * 40,
        PROJECT_ROOT / "requirements.lock",
        "20260804_18_v02_strategy_target",
    )
    model_engine = publish_model_engine(engine, model_engine_spec)
    assert publish_model_engine(engine, model_engine_spec).reused is True
    model_service = ModelDatasetPublicationService(engine)
    models = model_service.publish(
        model_catalog.release_artifact_id,
        signal_catalog.release_artifact_id,
        bundle.artifact_id,
        eligibility.artifact_id,
        signal_engine.artifact_id,
        model_engine.artifact_id,
    )
    models_reused = model_service.publish(
        model_catalog.release_artifact_id,
        signal_catalog.release_artifact_id,
        bundle.artifact_id,
        eligibility.artifact_id,
        signal_engine.artifact_id,
        model_engine.artifact_id,
    )
    assert len(models) == 86
    assert sum(item.specification_type == "single_signal" for item in models) == 51
    assert sum(item.specification_type == "directional_vote" for item in models) == 2
    assert sum(item.input_count for item in models) == 331
    assert not any(item.reused for item in models)
    assert all(item.reused for item in models_reused)
    assert [item.artifact_id for item in models] == [item.artifact_id for item in models_reused]
    assert sum(item.row_count == 28 for item in models) == 8
    assert sum(item.row_count == 32 for item in models) == 78
    with engine.connect() as connection:
        model_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM model.model_dataset), "
                "(SELECT count(*) FROM model.model_dataset_input), "
                "(SELECT count(*) FROM model.model_value), "
                "(SELECT count(*) FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = "
                "dependency.artifact_id WHERE artifact.artifact_type = 'model_dataset')"
            )
        ).one()
        single_matches_signal = connection.execute(
            text(
                "SELECT count(*) FROM model.model_value model_value "
                "JOIN model.model_dataset model_dataset ON model_dataset.model_dataset_id = "
                "model_value.model_dataset_id JOIN model.model_specification specification ON "
                "specification.model_specification_id = model_dataset.model_specification_id "
                "JOIN signal.signal_value signal_value ON signal_value.asset_id = "
                "model_value.asset_id AND signal_value.observation_date = "
                "model_value.observation_date JOIN signal.signal_dataset signal_dataset ON "
                "signal_dataset.signal_dataset_id = signal_value.signal_dataset_id "
                "JOIN signal.signal_version signal_version ON signal_version.signal_version_id = "
                "signal_dataset.signal_version_id JOIN signal.signal_definition definition ON "
                "definition.signal_definition_id = signal_version.signal_definition_id "
                "WHERE specification.specification_key = "
                "'single_signal__return_continuation__total_return__w252' "
                "AND definition.signal_key = 'return_continuation__total_return__w252' "
                "AND model_value.score = signal_value.score"
            )
        ).scalar_one()
        invalid_model_values = connection.execute(
            text(
                "SELECT count(*) FROM model.model_value WHERE confidence <> abs(score) OR "
                "(score > 0 AND direction <> 'positive') OR "
                "(score < 0 AND direction <> 'negative') OR "
                "(score = 0 AND direction <> 'neutral')"
            )
        ).scalar_one()
    assert model_counts == (86, 331, 2720, 761)
    assert single_matches_signal == 32
    assert invalid_model_values == 0

    universe_artifact_id = uuid.UUID(
        next(item["artifact_id"] for item in scope if item["catalog_type"] == "universe_version")
    )
    product = publish_strategy_product(
        engine,
        strategy_catalog.release_artifact_id,
        model_catalog.release_artifact_id,
        universe_artifact_id,
        "dimension_equal_weight__momentum_trend",
        "pre_selection_trend_eligible_top_k__k2",
        "weekly_last_common_session_close",
    )
    target_engine_spec = build_strategy_target_engine_spec(
        "a" * 40,
        PROJECT_ROOT / "requirements.lock",
        "20260804_18_v02_strategy_target",
    )
    strategy_target_engine = publish_strategy_target_engine(engine, target_engine_spec)
    target_service = StrategyTargetPublicationService(engine)
    target_model = next(
        item
        for item in models
        if item.specification_key == "dimension_equal_weight__momentum_trend"
    )
    trend_signal = next(
        item
        for item in signals
        if item.signal_key == "price_above_ma_state__moving_average_ratio__s1_l200"
    )
    with pytest.raises(ValueError, match="requires its auxiliary Signal Dataset"):
        target_service.publish(
            product.version_artifact_id,
            target_model.artifact_id,
            strategy_target_engine.artifact_id,
        )
    target_path = target_service.publish(
        product.version_artifact_id,
        target_model.artifact_id,
        strategy_target_engine.artifact_id,
        trend_signal.artifact_id,
    )
    target_reused = target_service.publish(
        product.version_artifact_id,
        target_model.artifact_id,
        strategy_target_engine.artifact_id,
        trend_signal.artifact_id,
    )
    assert target_path.reused is False
    assert target_reused.reused is True
    assert target_path.artifact_id == target_reused.artifact_id
    assert target_path.frequency == "weekly"
    assert target_path.decision_count == 2
    assert target_path.position_count == target_path.decision_count * 4
    with engine.connect() as connection:
        target_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM strategy.portfolio_target_path), "
                "(SELECT count(*) FROM strategy.model_strategy_target_path), "
                "(SELECT count(*) FROM strategy.target_path_auxiliary_input), "
                "(SELECT count(*) FROM strategy.portfolio_decision), "
                "(SELECT count(*) FROM strategy.target_asset_position)"
            )
        ).one()
        budgets = connection.execute(
            text(
                "SELECT decision.decision_date, decision.reserve_target_weight + "
                "sum(position.target_weight) FROM strategy.portfolio_decision decision "
                "JOIN strategy.target_asset_position position ON "
                "position.portfolio_decision_id = decision.portfolio_decision_id "
                "GROUP BY decision.portfolio_decision_id ORDER BY decision.decision_date"
            )
        ).all()
    assert target_counts == (
        1,
        1,
        1,
        target_path.decision_count,
        target_path.position_count,
    )
    assert all(
        abs(Decimal(total) - Decimal(1)) <= Decimal("0.000000000000001") for _, total in budgets
    )
    strategy_client = TestClient(create_app(ArtifactQueryService(engine)))
    strategy_overview = strategy_client.get("/api/v2/strategies/overview")
    assert strategy_overview.status_code == 200
    strategy_payload = strategy_overview.json()
    assert len(strategy_payload["rules"]["variants"]) == 9
    assert len(strategy_payload["products"]) == 1
    assert len(strategy_payload["target_paths"]) == 1
    assert "sharpe" not in strategy_overview.text.lower()
    target_detail = strategy_client.get(f"/api/v2/strategies/targets/{target_path.artifact_id}")
    assert target_detail.status_code == 200
    target_payload = target_detail.json()
    assert len(target_payload["decisions"]) == target_path.decision_count
    assert all(len(item["positions"]) == 4 for item in target_payload["decisions"])
    assert target_payload["auxiliary_signal_dataset_artifact_id"] == str(trend_signal.artifact_id)

    accounting_spec = build_accounting_engine_spec(
        "a" * 40,
        PROJECT_ROOT / "requirements.lock",
        "20260804_19_v02_gross_path",
    )
    accounting_engine = publish_accounting_engine(engine, accounting_spec)
    assert publish_accounting_engine(engine, accounting_spec).reused is True
    gross_service = GrossPathPublicationService(engine)
    gross_path = gross_service.publish(target_path.artifact_id, accounting_engine.artifact_id)
    gross_reused = gross_service.publish(target_path.artifact_id, accounting_engine.artifact_id)
    assert gross_path.reused is False
    assert gross_reused.reused is True
    assert gross_path.artifact_id == gross_reused.artifact_id
    assert gross_path.nav_count >= gross_path.execution_count == target_path.decision_count
    assert gross_path.trade_count == gross_path.execution_count * 4
    with engine.connect() as connection:
        gross_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM experiment.gross_portfolio_path), "
                "(SELECT count(*) FROM experiment.gross_daily_nav), "
                "(SELECT count(*) FROM experiment.daily_asset_position), "
                "(SELECT count(*) FROM experiment.daily_reserve_position), "
                "(SELECT count(*) FROM experiment.portfolio_execution), "
                "(SELECT count(*) FROM experiment.portfolio_trade)"
            )
        ).one()
        gross_budgets = connection.execute(
            text(
                "SELECT reserve.nav_date, reserve.close_weight + sum(asset.close_weight) "
                "FROM experiment.daily_reserve_position reserve "
                "JOIN experiment.daily_asset_position asset ON "
                "asset.gross_portfolio_path_id = reserve.gross_portfolio_path_id "
                "AND asset.nav_date = reserve.nav_date GROUP BY reserve.gross_portfolio_path_id, "
                "reserve.nav_date ORDER BY reserve.nav_date"
            )
        ).all()
        execution_lags = connection.execute(
            text(
                "SELECT execution.decision_date, execution.execution_date "
                "FROM experiment.portfolio_execution execution ORDER BY execution.execution_date"
            )
        ).all()
    assert gross_counts == (
        1,
        gross_path.nav_count,
        gross_path.nav_count * 4,
        gross_path.nav_count,
        gross_path.execution_count,
        gross_path.trade_count,
    )
    assert all(
        abs(Decimal(total) - Decimal(1)) <= Decimal("0.00000000000000000000001")
        for _, total in gross_budgets
    )
    session_dates = [item.session_date for item in generated.sessions]
    assert all(
        session_dates.index(execution) == session_dates.index(decision) + 1
        for decision, execution in execution_lags
    )

    cost_catalog = publish_cost_catalog(engine)
    cost_catalog_reused = publish_cost_catalog(engine)
    assert len(cost_catalog.scenarios) == 3
    assert {item.cost_bps_per_side for item in cost_catalog.scenarios} == {
        Decimal("2"),
        Decimal("5"),
        Decimal("10"),
    }
    assert all(item.reused for item in cost_catalog_reused.scenarios)
    net_service = NetCostPathPublicationService(engine)
    net_paths = tuple(
        net_service.publish(gross_path.artifact_id, scenario.artifact_id)
        for scenario in cost_catalog.scenarios
    )
    net_reused = tuple(
        net_service.publish(gross_path.artifact_id, scenario.artifact_id)
        for scenario in cost_catalog.scenarios
    )
    assert all(item.reused for item in net_reused)
    assert [item.artifact_id for item in net_paths] == [item.artifact_id for item in net_reused]
    assert all(item.nav_count == gross_path.nav_count for item in net_paths)
    assert all(item.execution_cost_count == gross_path.execution_count for item in net_paths)
    final_net_by_bps: dict[Decimal, Decimal] = {}
    with engine.connect() as connection:
        net_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM experiment.cost_model_definition), "
                "(SELECT count(*) FROM experiment.cost_model_version), "
                "(SELECT count(*) FROM experiment.cost_scenario), "
                "(SELECT count(*) FROM experiment.net_cost_path), "
                "(SELECT count(*) FROM experiment.net_daily_nav), "
                "(SELECT count(*) FROM experiment.execution_cost)"
            )
        ).one()
        cost_reconciliation = connection.execute(
            text(
                "SELECT max(abs(cost.cost_amount - cost.net_pretrade_nav * "
                "cost.cost_fraction)) FROM experiment.execution_cost cost"
            )
        ).scalar_one()
        final_rows = connection.execute(
            text(
                "SELECT scenario.cost_bps_per_side, nav.net_nav FROM experiment.net_cost_path path "
                "JOIN experiment.cost_scenario scenario ON scenario.cost_scenario_id = "
                "path.cost_scenario_id JOIN experiment.net_daily_nav nav ON "
                "nav.net_cost_path_id = path.net_cost_path_id WHERE nav.nav_date = "
                "path.effective_nav_end ORDER BY scenario.cost_bps_per_side"
            )
        ).all()
        gross_after_net = connection.execute(
            text(
                "SELECT gross_nav FROM experiment.gross_daily_nav WHERE "
                "gross_portfolio_path_id = (SELECT gross_portfolio_path_id FROM "
                "experiment.gross_portfolio_path) ORDER BY nav_date DESC LIMIT 1"
            )
        ).scalar_one()
    assert net_counts == (
        1,
        1,
        3,
        3,
        gross_path.nav_count * 3,
        gross_path.execution_count * 3,
    )
    assert Decimal(cost_reconciliation) <= Decimal("0.000000000000000000000001")
    final_net_by_bps.update((Decimal(bps), Decimal(nav)) for bps, nav in final_rows)
    assert final_net_by_bps[Decimal("2")] > final_net_by_bps[Decimal("5")]
    assert final_net_by_bps[Decimal("5")] > final_net_by_bps[Decimal("10")]
    assert all(nav < Decimal(gross_after_net) for nav in final_net_by_bps.values())
    with engine.connect() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "UPDATE experiment.net_daily_nav SET net_nav = net_nav + 1 "
                    "WHERE net_cost_path_id = (SELECT net_cost_path_id FROM "
                    "experiment.net_cost_path LIMIT 1)"
                )
            )
        connection.rollback()

    benchmark_catalog = publish_benchmark_catalog(engine)
    benchmark_catalog_reused = publish_benchmark_catalog(engine)
    assert len(benchmark_catalog.benchmarks) == 3
    assert all(item.reused for item in benchmark_catalog_reused.benchmarks)
    benchmark_engine_spec = build_benchmark_target_engine_spec(
        "a" * 40,
        PROJECT_ROOT / "requirements.lock",
        "20260804_21_v02_benchmark_path",
    )
    benchmark_engine = publish_benchmark_target_engine(engine, benchmark_engine_spec)
    assert publish_benchmark_target_engine(engine, benchmark_engine_spec).reused is True
    benchmark_target_service = BenchmarkTargetPublicationService(engine)
    benchmark_targets = tuple(
        benchmark_target_service.publish(
            target_path.artifact_id,
            benchmark.version_artifact_id,
            benchmark_engine.artifact_id,
        )
        for benchmark in benchmark_catalog.benchmarks
    )
    benchmark_targets_reused = tuple(
        benchmark_target_service.publish(
            target_path.artifact_id,
            benchmark.version_artifact_id,
            benchmark_engine.artifact_id,
        )
        for benchmark in benchmark_catalog.benchmarks
    )
    assert all(item.reused for item in benchmark_targets_reused)
    targets_by_key = {item.benchmark_key: item for item in benchmark_targets}
    assert targets_by_key["spy_buy_and_hold"].category == "product_primary"
    assert targets_by_key["spy_buy_and_hold"].decision_count == 1
    assert targets_by_key["spy_buy_and_hold"].position_count == 1
    assert targets_by_key["four_etf_equal_weight_buy_and_hold"].decision_count == 1
    assert targets_by_key["four_etf_equal_weight_buy_and_hold"].position_count == 4
    assert (
        targets_by_key["four_etf_equal_weight_same_schedule_rebalanced"].decision_count
        == target_path.decision_count
    )
    assert (
        targets_by_key["four_etf_equal_weight_same_schedule_rebalanced"].position_count
        == target_path.decision_count * 4
    )
    benchmark_gross = tuple(
        gross_service.publish(item.artifact_id, accounting_engine.artifact_id)
        for item in benchmark_targets
    )
    gross_by_key = dict(
        zip((item.benchmark_key for item in benchmark_targets), benchmark_gross, strict=True)
    )
    assert gross_by_key["spy_buy_and_hold"].execution_count == 1
    assert gross_by_key["spy_buy_and_hold"].trade_count == 1
    assert gross_by_key["four_etf_equal_weight_buy_and_hold"].execution_count == 1
    assert gross_by_key["four_etf_equal_weight_buy_and_hold"].trade_count == 4
    assert (
        gross_by_key["four_etf_equal_weight_same_schedule_rebalanced"].execution_count
        == target_path.decision_count
    )
    benchmark_net = tuple(
        net_service.publish(gross.artifact_id, scenario.artifact_id)
        for gross in benchmark_gross
        for scenario in cost_catalog.scenarios
    )
    assert len(benchmark_net) == 9
    assert all(item.nav_count == gross_path.nav_count for item in benchmark_net)
    with engine.connect() as connection:
        benchmark_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM experiment.benchmark_definition), "
                "(SELECT count(*) FROM experiment.benchmark_version), "
                "(SELECT count(*) FROM strategy.benchmark_target_path), "
                "(SELECT count(*) FROM strategy.benchmark_decision), "
                "(SELECT count(*) FROM strategy.benchmark_asset_position), "
                "(SELECT count(*) FROM experiment.gross_portfolio_path), "
                "(SELECT count(*) FROM experiment.net_cost_path)"
            )
        ).one()
        spy_execution = connection.execute(
            text(
                "SELECT execution.gross_traded_fraction FROM experiment.portfolio_execution "
                "execution JOIN experiment.gross_portfolio_path gross ON "
                "gross.gross_portfolio_path_id = execution.gross_portfolio_path_id JOIN "
                "strategy.benchmark_target_path target ON target.portfolio_target_path_id = "
                "gross.portfolio_target_path_id JOIN experiment.benchmark_version version ON "
                "version.benchmark_version_id = target.benchmark_version_id JOIN "
                "experiment.benchmark_definition definition ON "
                "definition.benchmark_definition_id = version.benchmark_definition_id WHERE "
                "definition.benchmark_key = 'spy_buy_and_hold'"
            )
        ).scalar_one()
    assert benchmark_counts == (3, 3, 3, 4, 13, 4, 12)
    assert Decimal(spy_execution) == Decimal(1)

    metric_catalog = publish_performance_metric_catalog(engine)
    assert metric_catalog.metric_count == 22
    assert publish_performance_metric_catalog(engine).reused is True
    performance_spec = build_performance_engine_spec(
        "a" * 40,
        PROJECT_ROOT / "requirements.lock",
        "20260804_22_v02_performance",
    )
    performance_engine = publish_performance_engine(engine, performance_spec)
    assert publish_performance_engine(engine, performance_spec).reused is True
    performance_service = IntervalPerformancePublicationService(engine)
    performance_result = performance_service.publish(
        net_paths[1].artifact_id,
        benchmark_net[1].artifact_id,
        metric_catalog.artifact_id,
        performance_engine.artifact_id,
        template_key="full_history",
        as_of_date=date.fromisoformat(gross_path.effective_nav_end),
    )
    performance_reused = performance_service.publish(
        net_paths[1].artifact_id,
        benchmark_net[1].artifact_id,
        metric_catalog.artifact_id,
        performance_engine.artifact_id,
        template_key="full_history",
        as_of_date=date.fromisoformat(gross_path.effective_nav_end),
    )
    excluded_result = performance_service.publish(
        net_paths[1].artifact_id,
        benchmark_net[1].artifact_id,
        metric_catalog.artifact_id,
        performance_engine.artifact_id,
        template_key="trailing_10_years",
        as_of_date=date.fromisoformat(gross_path.effective_nav_end),
    )
    assert performance_result.availability_status == "eligible"
    assert performance_result.metric_value_count == 36
    assert performance_result.observation_count == gross_path.nav_count
    assert performance_reused.reused is True
    assert performance_reused.artifact_id == performance_result.artifact_id
    assert excluded_result.availability_status == "excluded"
    assert excluded_result.exclusion_reason == "insufficient_history"
    assert excluded_result.metric_value_count == 0
    with pytest.raises(ValueError, match="share context and per-side cost"):
        performance_service.publish(
            net_paths[0].artifact_id,
            benchmark_net[1].artifact_id,
            metric_catalog.artifact_id,
            performance_engine.artifact_id,
            template_key="full_history",
            as_of_date=date.fromisoformat(gross_path.effective_nav_end),
        )
    with engine.connect() as connection:
        performance_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM experiment.performance_metric_catalog), "
                "(SELECT count(*) FROM experiment.performance_metric_definition), "
                "(SELECT count(*) FROM experiment.interval_performance_result), "
                "(SELECT count(*) FROM experiment.performance_metric_value)"
            )
        ).one()
        relative_rows = connection.execute(
            text(
                "SELECT count(*) FROM experiment.performance_metric_value value JOIN "
                "experiment.performance_metric_definition definition ON "
                "definition.performance_metric_definition_id = "
                "value.performance_metric_definition_id WHERE value.series_role = 'relative' "
                "AND definition.metric_scope = 'relative'"
            )
        ).scalar_one()
    assert performance_counts == (1, 22, 2, 36)
    assert relative_rows == 8
    with engine.connect() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "UPDATE experiment.performance_metric_value SET metric_value = 0 "
                    "WHERE interval_performance_result_id = "
                    "(SELECT interval_performance_result_id FROM "
                    "experiment.interval_performance_result WHERE availability_status = "
                    "'eligible' LIMIT 1)"
                )
            )
        connection.rollback()

    suite_cells = (
        ExperimentCellRequest(
            "spy_5bps_full",
            target_path.artifact_id,
            benchmark_catalog.benchmarks[0].version_artifact_id,
            cost_catalog.scenarios[1].artifact_id,
            metric_catalog.artifact_id,
            accounting_engine.artifact_id,
            benchmark_engine.artifact_id,
            performance_engine.artifact_id,
            "full_history",
            date.fromisoformat(gross_path.effective_nav_end),
        ),
        ExperimentCellRequest(
            "spy_5bps_trailing_10y",
            target_path.artifact_id,
            benchmark_catalog.benchmarks[0].version_artifact_id,
            cost_catalog.scenarios[1].artifact_id,
            metric_catalog.artifact_id,
            accounting_engine.artifact_id,
            benchmark_engine.artifact_id,
            performance_engine.artifact_id,
            "trailing_10_years",
            date.fromisoformat(gross_path.effective_nav_end),
        ),
    )
    suite = publish_experiment_suite(
        engine,
        suite_key="initial_spy_comparison",
        name="Initial SPY Comparison",
        description="Explicit full-history and trailing-ten-year cells.",
        cells=suite_cells,
    )
    suite_reused = publish_experiment_suite(
        engine,
        suite_key="initial_spy_comparison",
        name="Initial SPY Comparison",
        description="Explicit full-history and trailing-ten-year cells.",
        cells=suite_cells,
    )
    shared_suite = publish_experiment_suite(
        engine,
        suite_key="shared_full_history_check",
        name="Shared Full-History Check",
        description="Confirms that atomic specifications are reusable across suites.",
        cells=(suite_cells[0],),
    )
    assert suite.specification_count == 2
    assert suite_reused.reused is True
    assert suite_reused.artifact_id == suite.artifact_id
    assert all(item.reused for item in suite_reused.specifications)
    assert shared_suite.specifications[0].reused is True
    assert shared_suite.specifications[0].artifact_id == suite.specifications[0].artifact_id
    with pytest.raises(ValueError, match="performance_engine"):
        publish_experiment_suite(
            engine,
            suite_key="invalid_engine_roles",
            name="Invalid Engine Roles",
            description="Must roll back without publishing a partial suite.",
            cells=(
                ExperimentCellRequest(
                    "invalid",
                    target_path.artifact_id,
                    benchmark_catalog.benchmarks[0].version_artifact_id,
                    cost_catalog.scenarios[1].artifact_id,
                    metric_catalog.artifact_id,
                    accounting_engine.artifact_id,
                    benchmark_engine.artifact_id,
                    accounting_engine.artifact_id,
                    "full_history",
                    date.fromisoformat(gross_path.effective_nav_end),
                ),
            ),
        )
    with engine.connect() as connection:
        suite_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM experiment.experiment_suite), "
                "(SELECT count(*) FROM experiment.experiment_specification), "
                "(SELECT count(*) FROM experiment.experiment_suite_cell), "
                "(SELECT count(*) FROM lineage.artifact WHERE artifact_type = "
                "'experiment_suite')"
            )
        ).one()
    assert suite_counts == (2, 2, 3, 2)
    with engine.connect() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                text("UPDATE experiment.experiment_suite_cell SET ordinal = 9")
            )
        connection.rollback()

    evaluation_spec = build_signal_evaluation_engine_spec(
        "a" * 40,
        PROJECT_ROOT / "requirements.lock",
        "20260804_13_v02_signal_eval",
    )
    evaluation_engine = publish_signal_evaluation_engine(engine, evaluation_spec)
    evaluation_service = SignalDiagnosticPublicationService(engine)
    evaluations = tuple(
        evaluation_service.publish(
            signal_catalog.release_artifact_id,
            item.artifact_id,
            signal_engine.artifact_id,
            evaluation_engine.artifact_id,
        )
        for item in forward
    )
    evaluations_reused = tuple(
        evaluation_service.publish(
            signal_catalog.release_artifact_id,
            item.artifact_id,
            signal_engine.artifact_id,
            evaluation_engine.artifact_id,
        )
        for item in forward
    )
    assert {item.frequency for item in evaluations} == {"weekly", "monthly"}
    assert {item.signal_count for item in evaluations} == {51}
    assert {item.pair_count for item in evaluations} == {1275}
    assert all(item.reused for item in evaluations_reused)
    assert [item.artifact_id for item in evaluations] == [
        item.artifact_id for item in evaluations_reused
    ]
    with engine.connect() as connection:
        evaluation_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM signal.signal_evaluation), "
                "(SELECT count(*) FROM signal.signal_evaluation_period), "
                "(SELECT count(*) FROM signal.signal_evaluation_metric), "
                "(SELECT count(*) FROM signal.signal_pair_diagnostic), "
                "(SELECT count(*) FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = "
                "dependency.artifact_id WHERE artifact.artifact_type = 'signal_evaluation')"
            )
        ).one()
    assert evaluation_counts == (2, 153, 204, 2550, 116)

    model_evaluation_spec = build_model_evaluation_engine_spec(
        "a" * 40,
        PROJECT_ROOT / "requirements.lock",
        "20260804_18_v02_strategy_target",
    )
    model_evaluation_engine = publish_model_evaluation_engine(engine, model_evaluation_spec)
    assert publish_model_evaluation_engine(engine, model_evaluation_spec).reused is True
    model_evaluation_service = ModelDiagnosticPublicationService(engine)
    model_evaluations = tuple(
        model_evaluation_service.publish(
            model_catalog.release_artifact_id,
            item.artifact_id,
            model_engine.artifact_id,
            model_evaluation_engine.artifact_id,
        )
        for item in forward
    )
    model_evaluations_reused = tuple(
        model_evaluation_service.publish(
            model_catalog.release_artifact_id,
            item.artifact_id,
            model_engine.artifact_id,
            model_evaluation_engine.artifact_id,
        )
        for item in forward
    )
    assert {item.frequency for item in model_evaluations} == {"weekly", "monthly"}
    assert {item.model_count for item in model_evaluations} == {86}
    assert {item.pair_count for item in model_evaluations} == {3655}
    assert {item.ablation_count for item in model_evaluations} == {150}
    assert all(item.reused for item in model_evaluations_reused)
    with engine.connect() as connection:
        model_evaluation_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM model.model_evaluation), "
                "(SELECT count(*) FROM model.model_evaluation_period), "
                "(SELECT count(*) FROM model.model_evaluation_metric), "
                "(SELECT count(*) FROM model.model_pair_diagnostic), "
                "(SELECT count(*) FROM model.model_ablation_comparison), "
                "(SELECT count(*) FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = "
                "dependency.artifact_id WHERE artifact.artifact_type = 'model_evaluation')"
            )
        ).one()
    assert model_evaluation_counts == (2, 258, 344, 7310, 300, 186)
    model_overview = TestClient(create_app(ArtifactQueryService(engine))).get(
        "/api/v2/models/overview?frequency=weekly"
    )
    assert model_overview.status_code == 200
    model_payload = model_overview.json()
    assert model_payload["frequency"] == "weekly"
    assert model_payload["model_count"] == 86
    assert model_payload["common_period_count"] == 2
    assert len(model_payload["models"]) == 86
    assert len(model_payload["pairs"]) == 3655
    assert len(model_payload["ablations"]) == 150
    assert sum(len(item["dimensions"]) for item in model_payload["models"]) == 151
    assert "sharpe" not in model_overview.text.lower()
    signal_overview = TestClient(create_app(ArtifactQueryService(engine))).get(
        "/api/v2/signals/overview?frequency=weekly"
    )
    assert signal_overview.status_code == 200
    signal_payload = signal_overview.json()
    assert signal_payload["frequency"] == "weekly"
    assert signal_payload["signal_count"] == 51
    assert signal_payload["common_period_count"] == 2
    assert len(signal_payload["signals"]) == 51
    assert len(signal_payload["pairs"]) == 1275
    assert "sharpe" not in signal_overview.text.lower()

    diagnostic_spec = build_factor_diagnostic_engine_spec(
        "a" * 40,
        PROJECT_ROOT / "requirements.lock",
        "20260804_13_v02_signal_eval",
    )
    diagnostic_engine = publish_factor_diagnostic_engine(engine, diagnostic_spec)
    diagnostic_service = FactorDiagnosticPublicationService(engine)
    diagnostics = diagnostic_service.publish(
        factor_catalog.release_artifact_id,
        bundle.artifact_id,
        eligibility.artifact_id,
        factor_engine.artifact_id,
        diagnostic_engine.artifact_id,
    )
    diagnostic_reuse = diagnostic_service.publish(
        factor_catalog.release_artifact_id,
        bundle.artifact_id,
        eligibility.artifact_id,
        factor_engine.artifact_id,
        diagnostic_engine.artifact_id,
    )
    assert diagnostics.dataset_count == 28
    assert diagnostics.pair_count == 378
    assert diagnostic_reuse.reused is True
    assert diagnostic_reuse.artifact_id == diagnostics.artifact_id
    with engine.connect() as connection:
        diagnostic_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM factor.factor_diagnostic_set), "
                "(SELECT count(*) FROM factor.factor_dataset_summary), "
                "(SELECT count(*) FROM factor.factor_pair_correlation), "
                "(SELECT count(*) FROM lineage.artifact_dependency "
                "WHERE artifact_id = :artifact_id)"
            ),
            {"artifact_id": diagnostics.artifact_id},
        ).one()
    assert diagnostic_counts == (1, 28, 378, 34)
    overview = TestClient(create_app(ArtifactQueryService(engine))).get("/api/v2/factors/overview")
    assert overview.status_code == 200
    overview_payload = overview.json()
    assert overview_payload["dataset_count"] == 28
    assert overview_payload["pair_count"] == 378
    assert len(overview_payload["datasets"]) == 28
    assert len(overview_payload["correlations"]) == 378
    assert "sharpe" not in overview.text.lower()

    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE factor.factor_dataset_summary SET mean = 0"))

    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE factor.factor_value SET value = 0"))

    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE signal.signal_value SET score = 0"))

    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE model.model_value SET score = 0"))

    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE data.forward_return_value SET forward_return = 0"))

    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE signal.signal_evaluation_metric SET period_count = 1"))

    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE model.model_ablation_comparison SET period_count = 1"))

    short = EligibilityPublicationService(engine).publish(
        uuid.UUID(scope[1]["artifact_id"]),
        uuid.UUID(scope[2]["artifact_id"]),
        bundle.artifact_id,
        requested_start=requested_start,
        requested_end=requested_end,
        warmup_observations=20,
        version_number=2,
    )
    with pytest.raises(ValueError, match="warmup is shorter"):
        service.publish(
            factor_catalog.release_artifact_id,
            bundle.artifact_id,
            short.artifact_id,
            factor_engine.artifact_id,
        )
