from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, text

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.persistence.models import (
    Asset,
    CleanDataset,
    CleaningVersion,
    CleanMarketPrice,
    DataVersion,
    FactorDataset,
    FactorDefinition,
    FactorValue,
    FactorVariant,
    FactorVersion,
)
from style_rotation.persistence.session import create_session_factory
from style_rotation.signals.repository import SignalRepository
from style_rotation.signals.service import SignalComputationService

DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_signal_service_publishes_events_positions_and_reuses_dataset() -> None:
    assert DATABASE_URL is not None
    sqlalchemy_url = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(sqlalchemy_url)
    session_factory = create_session_factory(engine)
    token = uuid.uuid4().hex
    data_version_id = uuid.uuid4()
    cleaning_version_id = uuid.uuid4()
    factor_version_id = uuid.uuid4()
    definition_id = uuid.uuid4()
    variant_id = uuid.uuid4()
    start = date(2025, 1, 1)
    end = start + timedelta(days=229)

    with session_factory.begin() as session:
        assets = {
            asset.symbol: asset.asset_id
            for asset in session.scalars(
                select(Asset).where(Asset.symbol.in_(("IWF", "IWD", "IWO", "IWN")))
            )
        }
        session.add(
            DataVersion(
                data_version_id=data_version_id,
                version_key=f"signal-test-data-{token}",
                provider="signal_test",
                content_hash=sha256_hexdigest({"data": token}),
                requested_at=datetime.now(UTC),
                coverage_start=start,
                coverage_end=end,
                request_parameters={},
                source_metadata={},
                status="published",
                published_at=datetime.now(UTC),
            )
        )
        session.add(
            CleaningVersion(
                cleaning_version_id=cleaning_version_id,
                version_key=f"signal-test-cleaning-{token}",
                rules_hash="a" * 64,
                code_hash="b" * 64,
                configuration={},
            )
        )
        session.add(
            FactorVersion(
                factor_version_id=factor_version_id,
                version_key=f"signal-test-factor-{token}",
                registry_hash="c" * 64,
                code_hash="d" * 64,
            )
        )
        session.flush()
        session.add(
            CleanDataset(
                data_version_id=data_version_id,
                cleaning_version_id=cleaning_version_id,
                content_hash=sha256_hexdigest({"clean": token}),
                coverage_start=start,
                coverage_end=end,
                common_market_start=start,
                status="published",
            )
        )
        session.add(
            FactorDefinition(
                factor_definition_id=definition_id,
                factor_version_id=factor_version_id,
                definition_key="signal_test_momentum",
                family="trend_return",
                name="Signal test momentum",
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
                factor_variant_id=variant_id,
                factor_version_id=factor_version_id,
                factor_definition_id=definition_id,
                variant_key="signal_test_momentum_20",
                parameters={"window": 20},
                minimum_observations=21,
            )
        )
        prices: list[CleanMarketPrice] = []
        values: list[FactorValue] = []
        for symbol_index, (_symbol, asset_id) in enumerate(sorted(assets.items())):
            for index in range(230):
                close = (
                    Decimal(100 + symbol_index * 10)
                    + Decimal(index)
                    + Decimal(index * index) / 1000
                )
                trade_date = start + timedelta(days=index)
                prices.append(
                    CleanMarketPrice(
                        data_version_id=data_version_id,
                        cleaning_version_id=cleaning_version_id,
                        asset_id=asset_id,
                        trade_date=trade_date,
                        open_adj=close,
                        high_adj=close + 1,
                        low_adj=close - 1,
                        close_adj=close,
                        adj_factor=Decimal(1),
                        volume_raw=1_000,
                        dividends=Decimal(0),
                        stock_splits=Decimal(0),
                    )
                )
                if index >= 200:
                    values.append(
                        FactorValue(
                            data_version_id=data_version_id,
                            cleaning_version_id=cleaning_version_id,
                            factor_version_id=factor_version_id,
                            factor_variant_id=variant_id,
                            asset_id=asset_id,
                            trade_date=trade_date,
                            raw_value=Decimal(4 - symbol_index),
                        )
                    )
        session.add_all(prices)
        session.flush()
        session.add_all(values)
        session.add(
            FactorDataset(
                data_version_id=data_version_id,
                cleaning_version_id=cleaning_version_id,
                factor_version_id=factor_version_id,
                content_hash=sha256_hexdigest({"factor": token}),
                common_valid_start=start + timedelta(days=200),
                coverage_end=end,
                row_count=len(values),
                status="published",
            )
        )

    repository = SignalRepository(session_factory)
    service = SignalComputationService(repository)
    strategy_key = f"signal-test-strategy-{token}"
    first = service.run(
        data_version_id=data_version_id,
        cleaning_version_id=cleaning_version_id,
        factor_version_id=factor_version_id,
        strategy_version_key=strategy_key,
    )
    second = service.run(
        data_version_id=data_version_id,
        cleaning_version_id=cleaning_version_id,
        factor_version_id=factor_version_id,
        strategy_version_key=strategy_key,
    )
    assert not first.reused
    assert second.reused
    assert first.event_count == second.event_count
    assert first.position_count == first.event_count * 4

    parameters = {"strategy_version_id": first.strategy_version_id}
    with engine.begin() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM rebalance_events "
                    "WHERE strategy_version_id=:strategy_version_id "
                    "AND signal_date >= execution_date"
                ),
                parameters,
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM ("
                    "SELECT p.rebalance_event_id FROM target_positions p "
                    "JOIN rebalance_events e USING (rebalance_event_id) "
                    "WHERE e.strategy_version_id=:strategy_version_id "
                    "GROUP BY p.rebalance_event_id HAVING count(*) <> 4) x"
                ),
                parameters,
            ).scalar_one()
            == 0
        )
        connection.execute(
            text("DELETE FROM signal_datasets WHERE strategy_version_id=:strategy_version_id"),
            parameters,
        )
        connection.execute(
            text("DELETE FROM strategy_versions WHERE strategy_version_id=:strategy_version_id"),
            parameters,
        )
        upstream = {
            "data_version_id": data_version_id,
            "cleaning_version_id": cleaning_version_id,
            "factor_version_id": factor_version_id,
        }
        connection.execute(
            text("DELETE FROM factor_datasets WHERE factor_version_id=:factor_version_id"),
            upstream,
        )
        connection.execute(
            text("DELETE FROM factor_values WHERE factor_version_id=:factor_version_id"),
            upstream,
        )
        connection.execute(
            text("DELETE FROM factor_versions WHERE factor_version_id=:factor_version_id"),
            upstream,
        )
        connection.execute(
            text(
                "DELETE FROM clean_datasets WHERE data_version_id=:data_version_id "
                "AND cleaning_version_id=:cleaning_version_id"
            ),
            upstream,
        )
        connection.execute(
            text(
                "DELETE FROM clean_market_prices WHERE data_version_id=:data_version_id "
                "AND cleaning_version_id=:cleaning_version_id"
            ),
            upstream,
        )
        connection.execute(
            text("DELETE FROM data_versions WHERE data_version_id=:data_version_id"),
            upstream,
        )
        connection.execute(
            text("DELETE FROM cleaning_versions WHERE cleaning_version_id=:cleaning_version_id"),
            upstream,
        )
    engine.dispose()
