from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from style_rotation.catalog.scope import publish_research_scope
from style_rotation.data.service import (
    SnapshotInput,
    SourceSnapshotService,
    publish_data_contracts,
)
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_contracts_and_source_snapshot_are_reusable_and_frozen() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    publish_research_scope(engine, Path("v0.2/catalogs/research_scope.v0.2.0.json"))
    first = publish_data_contracts(engine, Path("v0.2/catalogs/data_contracts.v0.2.0.json"))
    second = publish_data_contracts(engine, Path("v0.2/catalogs/data_contracts.v0.2.0.json"))

    assert len(first) == 6
    assert all(item["reused"] is False for item in first)
    assert all(item["reused"] is True for item in second)

    requested = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
    raw_payload = b'{"chart":{"result":[{"symbol":"IWF"}]}}'
    snapshot_input = SnapshotInput(
        series_key="us_etf_daily_market",
        series_version=1,
        snapshot_key="iwf-20260803T010001Z",
        requested_at=requested,
        fetched_at=requested + timedelta(seconds=1),
        as_of_at=requested,
        media_type="application/json",
        request_parameters={"ticker": "IWF", "interval": "1d"},
        response_metadata={"status_code": 200},
        raw_payload=raw_payload,
    )
    snapshots = SourceSnapshotService(engine)
    published = snapshots.publish(snapshot_input)
    reused = snapshots.publish(snapshot_input)

    assert reused.reused is True
    assert reused.artifact_id == published.artifact_id
    assert snapshots.raw_payload(published.artifact_id) == raw_payload
    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(
            text(
                "UPDATE data.source_snapshot SET media_type = 'text/plain' "
                "WHERE artifact_id = :artifact_id"
            ),
            {"artifact_id": published.artifact_id},
        )
