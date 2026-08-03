from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from style_rotation.catalog.eligibility import EligibilityPublicationService
from style_rotation.catalog.scope import publish_research_scope
from style_rotation.data.bundle import (
    ReservePublicationService,
    publish_data_bundle,
    publish_reserve_model,
)
from style_rotation.data.calendar import CalendarPublicationService, XNYSCalendarGenerator
from style_rotation.data.publication import CanonicalDataPublicationService
from style_rotation.data.service import SnapshotInput, SourceSnapshotService, publish_data_contracts
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


def _source_snapshot(
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
    return result.artifact_id


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_reserve_bundle_and_eligibility_complete_the_formal_data_chain() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    scope = publish_research_scope(engine, Path("v0.2/catalogs/research_scope.v0.2.0.json"))
    publish_data_contracts(engine, Path("v0.2/catalogs/data_contracts.v0.2.0.json"))
    generated = XNYSCalendarGenerator().generate(date(2026, 7, 27), date(2026, 7, 31))
    calendar = CalendarPublicationService(engine).publish(generated)
    snapshots = SourceSnapshotService(engine)
    fetched = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
    header = "session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
    market_ids = []
    for ordinal, symbol in enumerate(("IWF", "IWD", "IWO", "IWN", "SPY")):
        rows = "".join(
            f"{session.session_date},100,102,99,101,101,1000,0,0\n"
            for session in generated.sessions
        )
        market_ids.append(
            _source_snapshot(
                snapshots,
                symbol,
                (header + rows).encode(),
                fetched + timedelta(microseconds=ordinal),
            )
        )
    rate_snapshot = _source_snapshot(
        snapshots,
        "DGS3MO",
        b"observation_date,DGS3MO\n2026-07-24,4.00\n2026-07-29,4.10\n",
        fetched + timedelta(microseconds=10),
    )
    canonical = CanonicalDataPublicationService(engine)
    market = canonical.publish_market(tuple(market_ids), calendar.artifact_id, version_number=1)
    rate = canonical.publish_rate(rate_snapshot, version_number=1)
    _model_definition, model = publish_reserve_model(engine)
    reserve_service = ReservePublicationService(engine)
    reserve = reserve_service.publish(
        rate.artifact_id, calendar.artifact_id, model.artifact_id, version_number=1
    )
    reserve_reuse = reserve_service.publish(
        rate.artifact_id, calendar.artifact_id, model.artifact_id, version_number=1
    )
    assert reserve_reuse.reused is True
    assert reserve_reuse.artifact_id == reserve.artifact_id

    _bundle_definition, bundle = publish_data_bundle(
        engine,
        market.artifact_id,
        rate.artifact_id,
        reserve.artifact_id,
        calendar.artifact_id,
        version_number=1,
    )
    _, bundle_reuse = publish_data_bundle(
        engine,
        market.artifact_id,
        rate.artifact_id,
        reserve.artifact_id,
        calendar.artifact_id,
        version_number=1,
    )
    assert bundle_reuse.reused is True
    eligibility_service = EligibilityPublicationService(engine)
    eligibility = eligibility_service.publish(
        uuid.UUID(scope[1]["artifact_id"]),
        uuid.UUID(scope[2]["artifact_id"]),
        bundle.artifact_id,
        requested_start=date(2026, 7, 29),
        requested_end=date(2026, 7, 31),
        warmup_observations=3,
        version_number=1,
    )
    eligibility_reuse = eligibility_service.publish(
        uuid.UUID(scope[1]["artifact_id"]),
        uuid.UUID(scope[2]["artifact_id"]),
        bundle.artifact_id,
        requested_start=date(2026, 7, 29),
        requested_end=date(2026, 7, 31),
        warmup_observations=3,
        version_number=1,
    )
    assert eligibility_reuse.reused is True
    assert eligibility_reuse.artifact_id == eligibility.artifact_id

    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM data.reserve_return), "
                "(SELECT count(*) FROM data.data_bundle_member), "
                "(SELECT count(*) FROM catalog.eligibility_item WHERE is_eligible), "
                "(SELECT count(*) FROM catalog.eligibility_issue)"
            )
        ).one()
        friday_to_monday = connection.execute(
            text(
                "SELECT calendar_days FROM data.reserve_return WHERE interval_start = '2026-07-31'"
            )
        ).scalar_one_or_none()
    assert counts == (4, 4, 5, 0)
    assert friday_to_monday is None
    rejected = eligibility_service.publish(
        uuid.UUID(scope[1]["artifact_id"]),
        uuid.UUID(scope[2]["artifact_id"]),
        bundle.artifact_id,
        requested_start=date(2026, 7, 28),
        requested_end=date(2026, 7, 31),
        warmup_observations=3,
        version_number=2,
    )
    with engine.connect() as connection:
        rejected_counts = connection.execute(
            text(
                "SELECT count(*) FILTER (WHERE item.is_eligible), count(issue.*) "
                "FROM catalog.eligibility_snapshot snapshot "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = snapshot.artifact_id "
                "JOIN catalog.eligibility_item item ON item.eligibility_snapshot_id = "
                "snapshot.eligibility_snapshot_id LEFT JOIN catalog.eligibility_issue issue "
                "ON issue.eligibility_item_id = item.eligibility_item_id "
                "WHERE artifact.artifact_id = :artifact_id"
            ),
            {"artifact_id": rejected.artifact_id},
        ).one()
    assert rejected_counts == (0, 5)
    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE catalog.eligibility_item SET is_eligible = false"))
