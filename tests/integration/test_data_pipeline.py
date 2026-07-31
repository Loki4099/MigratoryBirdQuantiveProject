from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.cleaning import REQUIRED_SYMBOLS
from style_rotation.data.pipeline import DataIngestionService
from style_rotation.data.repository import DataRepository
from style_rotation.data.types import MarketPriceRecord, ProviderBatch, RateObservation
from style_rotation.persistence.session import create_session_factory

DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


class FakeMarketProvider:
    def download(self, symbols: tuple[str, ...], start: date, end_exclusive: date) -> ProviderBatch:
        days = (date(2026, 7, 29), date(2026, 7, 30))
        records = []
        for symbol in symbols:
            for day in days:
                payload = {
                    "symbol": symbol,
                    "trade_date": day,
                    "open_raw": Decimal("100"),
                    "high_raw": Decimal("101"),
                    "low_raw": Decimal("99"),
                    "close_raw": Decimal("100"),
                    "adj_close": Decimal("50"),
                    "volume_raw": 1000,
                    "dividends": Decimal(0),
                    "stock_splits": Decimal(0),
                }
                records.append(
                    MarketPriceRecord(
                        symbol=symbol,
                        trade_date=day,
                        open_raw=Decimal("100"),
                        high_raw=Decimal("101"),
                        low_raw=Decimal("99"),
                        close_raw=Decimal("100"),
                        adj_close=Decimal("50"),
                        volume_raw=1000,
                        dividends=Decimal(0),
                        stock_splits=Decimal(0),
                        source_row_hash=sha256_hexdigest(payload),
                    )
                )
        return ProviderBatch(
            provider="fake_market",
            requested_at=datetime(2026, 7, 31, tzinfo=UTC),
            request_parameters={"symbols": symbols, "start": start, "end": end_exclusive},
            content_hash=sha256_hexdigest(records),
            records=tuple(records),
        )


class FakeRateProvider:
    def download(self, series_id: str, start: date, end_inclusive: date) -> ProviderBatch:
        observation_date = date(2026, 7, 28)
        payload = {
            "series_id": series_id,
            "observation_date": observation_date,
            "available_date": observation_date + timedelta(days=1),
            "annual_rate_percent": Decimal("5"),
        }
        record = RateObservation(
            series_id=series_id,
            observation_date=observation_date,
            available_date=observation_date + timedelta(days=1),
            annual_rate_percent=Decimal("5"),
            source_row_hash=sha256_hexdigest(payload),
        )
        return ProviderBatch(
            provider="fake_rate",
            requested_at=datetime(2026, 7, 31, tzinfo=UTC),
            request_parameters={"series_id": series_id, "start": start, "end": end_inclusive},
            content_hash=sha256_hexdigest((record,)),
            records=(record,),
        )


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_pipeline_publishes_contracts_raw_clean_and_reuses_identical_version() -> None:
    assert DATABASE_URL is not None
    sqlalchemy_url = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(sqlalchemy_url)
    repository = DataRepository(create_session_factory(engine))
    service = DataIngestionService(repository, FakeMarketProvider(), FakeRateProvider())
    first = service.run(
        start=date(2026, 7, 29),
        end_inclusive=date(2026, 7, 30),
        cleaning_version_key="cleaning-integration-v0.1.0",
        cleaning_rules_hash="a" * 64,
        cleaning_code_hash="b" * 64,
    )
    second = service.run(
        start=date(2026, 7, 29),
        end_inclusive=date(2026, 7, 30),
        cleaning_version_key="cleaning-integration-v0.1.0",
        cleaning_rules_hash="a" * 64,
        cleaning_code_hash="b" * 64,
    )
    assert not first.reused
    assert second.reused
    assert first.data_version_id == second.data_version_id
    assert first.market_rows == len(REQUIRED_SYMBOLS) * 2

    with engine.begin() as connection:
        parameters = {"data_version_id": first.data_version_id}
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM raw_market_prices WHERE data_version_id=:data_version_id"
                ),
                parameters,
            ).scalar_one()
            == len(REQUIRED_SYMBOLS) * 2
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM clean_market_prices "
                    "WHERE data_version_id=:data_version_id"
                ),
                parameters,
            ).scalar_one()
            == len(REQUIRED_SYMBOLS) * 2
        )
        assert (
            connection.execute(
                text("SELECT status FROM data_versions WHERE data_version_id=:data_version_id"),
                parameters,
            ).scalar_one()
            == "published"
        )
        for table in (
            "clean_datasets",
            "reserve_daily_returns",
            "clean_market_prices",
            "data_quality_events",
            "raw_rate_observations",
            "raw_market_prices",
        ):
            connection.execute(
                text(f"DELETE FROM {table} WHERE data_version_id=:data_version_id"),
                parameters,
            )
        connection.execute(
            text("DELETE FROM data_versions WHERE data_version_id=:data_version_id"),
            parameters,
        )
    engine.dispose()
