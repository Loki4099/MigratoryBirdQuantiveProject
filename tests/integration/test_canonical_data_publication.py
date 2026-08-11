from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from style_rotation.catalog.scope import publish_research_scope
from style_rotation.data.calendar import CalendarPublicationService, XNYSCalendarGenerator
from style_rotation.data.publication import CanonicalDataPublicationService
from style_rotation.data.service import SnapshotInput, SourceSnapshotService, publish_data_contracts
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


def _snapshot(
    service: SourceSnapshotService,
    subject: str,
    payload: bytes,
    fetched_at: datetime,
) -> object:
    market = subject != "DGS3MO"
    return service.publish(
        SnapshotInput(
            series_key="us_etf_daily_market" if market else "dgs3mo_daily",
            series_version=1,
            snapshot_key=f"{subject.lower()}-{fetched_at:%Y%m%dT%H%M%S%fZ}",
            requested_at=fetched_at - timedelta(seconds=1),
            fetched_at=fetched_at,
            as_of_at=fetched_at,
            media_type="text/csv",
            request_parameters={"tickers": subject} if market else {"id": subject},
            response_metadata={"fixture": True},
            raw_payload=payload,
        )
    )


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_canonical_market_and_rate_are_typed_reusable_and_frozen() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    publish_research_scope(engine, Path("v0.2/catalogs/research_scope.v0.2.0.json"))
    publish_data_contracts(engine, Path("v0.2/catalogs/data_contracts.v0.2.0.json"))
    calendar = CalendarPublicationService(engine).publish(
        XNYSCalendarGenerator().generate(date(2026, 7, 30), date(2026, 7, 30))
    )
    snapshots = SourceSnapshotService(engine)
    fetched = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
    header = "session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
    market_ids = []
    for ordinal, symbol in enumerate(("IWF", "IWD", "IWO", "IWN", "SPY")):
        row = f"2026-07-30,100,102,99,101,50.5,1000,{1.25 if symbol == 'IWF' else 0},0\n"
        published = _snapshot(
            snapshots, symbol, (header + row).encode(), fetched + timedelta(microseconds=ordinal)
        )
        market_ids.append(published.artifact_id)
    rate = _snapshot(
        snapshots,
        "DGS3MO",
        b"observation_date,DGS3MO\n2026-07-30,4.25\n",
        fetched + timedelta(microseconds=10),
    )
    service = CanonicalDataPublicationService(engine)

    market = service.publish_market(tuple(market_ids), calendar.artifact_id, version_number=1)
    market_reuse = service.publish_market(tuple(market_ids), calendar.artifact_id, version_number=1)
    rates = service.publish_rate(rate.artifact_id, version_number=1)
    rate_reuse = service.publish_rate(rate.artifact_id, version_number=1)
    assert market_reuse.reused is True
    assert market_reuse.artifact_id == market.artifact_id
    assert rate_reuse.reused is True
    assert rate_reuse.artifact_id == rates.artifact_id

    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM data.daily_bar), "
                "(SELECT count(*) FROM data.corporate_action), "
                "(SELECT count(*) FROM data.rate_observation), "
                "(SELECT count(*) FROM data.dataset_coverage)"
            )
        ).one()
        adjusted = connection.execute(
            text(
                "SELECT open_adj FROM data.daily_bar bar JOIN catalog.asset asset "
                "ON asset.asset_id = bar.asset_id WHERE asset.asset_key = 'iwf'"
            )
        ).scalar_one()
        available = connection.execute(
            text("SELECT available_date FROM data.rate_observation")
        ).scalar_one()
    assert counts == (5, 1, 1, 6)
    assert adjusted == Decimal("50.0000000000")
    assert available == date(2026, 7, 31)
    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE data.daily_bar SET close_adj = 1"))
