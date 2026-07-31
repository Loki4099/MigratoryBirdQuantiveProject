from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, text

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.factors.repository import FactorRepository
from style_rotation.factors.service import FactorComputationService
from style_rotation.persistence.models import (
    Asset,
    CleanDataset,
    CleaningVersion,
    CleanMarketPrice,
    DataVersion,
)
from style_rotation.persistence.session import create_session_factory

DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_factor_service_publishes_registry_values_and_reuses_dataset() -> None:
    assert DATABASE_URL is not None
    sqlalchemy_url = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(sqlalchemy_url)
    session_factory = create_session_factory(engine)
    data_version_id = uuid.uuid4()
    cleaning_version_id = uuid.uuid4()
    token = uuid.uuid4().hex
    start = date(2025, 1, 1)
    end = start + timedelta(days=259)

    with session_factory.begin() as session:
        assets = {
            asset.symbol: asset.asset_id
            for asset in session.scalars(
                select(Asset).where(Asset.symbol.in_(("IWF", "IWD", "IWO", "IWN")))
            )
        }
        assert len(assets) == 4
        session.add(
            DataVersion(
                data_version_id=data_version_id,
                version_key=f"factor-test-data-{token}",
                provider="factor_test",
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
                version_key=f"factor-test-cleaning-{token}",
                rules_hash="a" * 64,
                code_hash="b" * 64,
                configuration={},
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
        prices = []
        for symbol_index, (_symbol, asset_id) in enumerate(sorted(assets.items())):
            for index in range(260):
                close = (
                    Decimal(100 + symbol_index * 10)
                    + Decimal(index)
                    + Decimal(index * index) / 1000
                )
                prices.append(
                    CleanMarketPrice(
                        data_version_id=data_version_id,
                        cleaning_version_id=cleaning_version_id,
                        asset_id=asset_id,
                        trade_date=start + timedelta(days=index),
                        open_adj=close - Decimal("0.5"),
                        high_adj=close + Decimal("2"),
                        low_adj=close - Decimal("1"),
                        close_adj=close,
                        adj_factor=Decimal(1),
                        volume_raw=1_000 + index * index,
                        dividends=Decimal(0),
                        stock_splits=Decimal(0),
                    )
                )
        session.add_all(prices)

    repository = FactorRepository(session_factory)
    service = FactorComputationService(repository)
    factor_key = f"factor-test-{token}"
    first = service.run(
        data_version_id=data_version_id,
        cleaning_version_id=cleaning_version_id,
        factor_version_key=factor_key,
        factor_code_hash="c" * 64,
    )
    second = service.run(
        data_version_id=data_version_id,
        cleaning_version_id=cleaning_version_id,
        factor_version_key=factor_key,
        factor_code_hash="c" * 64,
    )
    assert not first.reused
    assert second.reused
    assert first.factor_value_rows == second.factor_value_rows
    assert first.common_valid_start == start + timedelta(days=252)

    with engine.begin() as connection:
        parameters = {"factor_version_id": first.factor_version_id}
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM factor_definitions "
                    "WHERE factor_version_id=:factor_version_id"
                ),
                parameters,
            ).scalar_one()
            == 11
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM factor_variants "
                    "WHERE factor_version_id=:factor_version_id"
                ),
                parameters,
            ).scalar_one()
            == 24
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM factor_values WHERE factor_version_id=:factor_version_id"
                ),
                parameters,
            ).scalar_one()
            == first.factor_value_rows
        )
        connection.execute(
            text("DELETE FROM factor_datasets WHERE factor_version_id=:factor_version_id"),
            parameters,
        )
        connection.execute(
            text("DELETE FROM factor_values WHERE factor_version_id=:factor_version_id"),
            parameters,
        )
        connection.execute(
            text("DELETE FROM factor_versions WHERE factor_version_id=:factor_version_id"),
            parameters,
        )
        connection.execute(
            text(
                "DELETE FROM clean_market_prices "
                "WHERE data_version_id=:data_version_id "
                "AND cleaning_version_id=:cleaning_version_id"
            ),
            {
                "data_version_id": data_version_id,
                "cleaning_version_id": cleaning_version_id,
            },
        )
        connection.execute(
            text(
                "DELETE FROM clean_datasets "
                "WHERE data_version_id=:data_version_id "
                "AND cleaning_version_id=:cleaning_version_id"
            ),
            {
                "data_version_id": data_version_id,
                "cleaning_version_id": cleaning_version_id,
            },
        )
        connection.execute(
            text("DELETE FROM data_versions WHERE data_version_id=:data_version_id"),
            {"data_version_id": data_version_id},
        )
        connection.execute(
            text("DELETE FROM cleaning_versions WHERE cleaning_version_id=:cleaning_version_id"),
            {"cleaning_version_id": cleaning_version_id},
        )
    engine.dispose()
