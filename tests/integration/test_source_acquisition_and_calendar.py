from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from style_rotation.catalog.scope import publish_research_scope
from style_rotation.data.acquisition import SourceAcquisitionService
from style_rotation.data.calendar import CalendarPublicationService, XNYSCalendarGenerator
from style_rotation.data.providers.snapshots import RawFetch
from style_rotation.data.service import publish_data_contracts
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


class FakeMarketAdapter:
    def __init__(self, fetched_at: datetime) -> None:
        self.fetched_at = fetched_at

    def fetch(self, symbol: str, start: date, end_exclusive: date) -> RawFetch:
        return RawFetch(
            requested_at=self.fetched_at - timedelta(seconds=1),
            fetched_at=self.fetched_at,
            as_of_at=self.fetched_at,
            media_type="text/csv",
            request_parameters={
                "ticker": symbol,
                "start": start.isoformat(),
                "end": end_exclusive.isoformat(),
            },
            response_metadata={"status_code": 200},
            payload=f"session_date,Close\n2026-07-30,{symbol}\n".encode(),
        )


class FakeRateAdapter:
    def __init__(self, fetched_at: datetime) -> None:
        self.fetched_at = fetched_at

    def fetch(self, series_id: str, start: date, end_inclusive: date) -> RawFetch:
        return RawFetch(
            requested_at=self.fetched_at - timedelta(seconds=1),
            fetched_at=self.fetched_at,
            as_of_at=self.fetched_at,
            media_type="text/csv",
            request_parameters={"id": series_id},
            response_metadata={"status_code": 200},
            payload=b"DATE,DGS3MO\n2026-07-30,4.25\n",
        )


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_acquisition_and_calendar_publish_immutable_evidence() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    publish_research_scope(engine, Path("v0.2/catalogs/research_scope.v0.2.0.json"))
    publish_data_contracts(engine, Path("v0.2/catalogs/data_contracts.v0.2.0.json"))
    fetched_at = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
    acquisition = SourceAcquisitionService(
        engine, FakeMarketAdapter(fetched_at), FakeRateAdapter(fetched_at)
    )

    first = acquisition.acquire(
        symbols=("IWF", "SPY"),
        start=date(2026, 7, 1),
        end_inclusive=date(2026, 7, 31),
    )
    second = acquisition.acquire(
        symbols=("IWF", "SPY"),
        start=date(2026, 7, 1),
        end_inclusive=date(2026, 7, 31),
    )
    assert len(first) == 3
    assert all(item.reused is False for item in first)
    assert all(item.reused is True for item in second)

    generated = XNYSCalendarGenerator().generate(date(2026, 11, 25), date(2026, 11, 27))
    calendars = CalendarPublicationService(engine)
    published = calendars.publish(generated)
    reused = calendars.publish(generated)
    assert reused.reused is True
    assert reused.artifact_id == published.artifact_id
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT session_date, is_early_close FROM catalog.calendar_session "
                "ORDER BY session_date"
            )
        ).all()
    assert rows == [(date(2026, 11, 25), False), (date(2026, 11, 27), True)]
    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(
            text(
                "UPDATE catalog.calendar_session SET is_early_close = false "
                "WHERE session_date = '2026-11-27'"
            )
        )
