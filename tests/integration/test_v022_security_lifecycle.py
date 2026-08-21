from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from style_rotation.lineage.service import ArtifactService
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.security_lifecycle import (
    LifecycleEvidenceRef,
    SecurityLifecycleEventService,
    SecurityLifecycleEventSpec,
    SecuritySettlementLegSpec,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_confirmed_cash_merger_publishes_exact_append_only_settlement() -> None:
    assert DATABASE_URL is not None
    database_name = make_url(DATABASE_URL).database
    assert database_name is not None
    reset_database(DATABASE_URL, database_name, "test")
    engine = create_postgres_engine(DATABASE_URL)
    source_security_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO catalog.security (
                  security_id,security_key,name,instrument_type,currency,status
                ) VALUES (:id,'cash_merger_source','Cash Merger Source',
                          'Common Stock','USD','active')
                """
            ),
            {"id": source_security_id},
        )
    evidence = ArtifactService(engine).publish(
        artifact_type="v022_lifecycle_source_evidence",
        artifact_key="cash_merger_source_notice",
        version_number=1,
        semantic_payload={"source_uri": "https://example.test/cash-merger"},
        content_payload={"content_sha256": "a" * 64},
        reason="publish lifecycle integration evidence",
    )
    spec = SecurityLifecycleEventSpec(
        security_id=source_security_id,
        event_key="cash_merger_2020_01_03",
        version_number=1,
        event_type="cash_merger",
        event_status="confirmed",
        announced_at=datetime(2019, 12, 1, tzinfo=UTC),
        effective_session=date(2020, 1, 3),
        last_trading_session=date(2020, 1, 2),
        settlement_session=date(2020, 1, 3),
        selectable_after=False,
        tradable_after=False,
        valuation_state_after="terminal",
        evidence=(LifecycleEvidenceRef(evidence.artifact_id, "primary_notice"),),
        settlement_legs=(
            SecuritySettlementLegSpec(
                leg_kind="cash",
                cash_amount_per_source_share=Decimal("42.50"),
                currency="USD",
                valuation_policy="fixed_cash",
            ),
        ),
        created_by="integration-reviewer",
    )
    service = SecurityLifecycleEventService(engine)
    published = service.publish(spec)
    replayed = service.publish(spec)

    assert replayed.reused is True
    assert replayed.artifact_id == published.artifact_id
    with engine.connect() as connection:
        event = connection.execute(
            text(
                """
                SELECT event.event_status,event.valuation_state_after,
                       artifact.status
                  FROM catalog.v022_security_lifecycle_event event
                  JOIN lineage.artifact artifact ON artifact.artifact_id=event.artifact_id
                 WHERE event.security_lifecycle_event_id=:event
                """
            ),
            {"event": published.security_lifecycle_event_id},
        ).one()
        leg = connection.execute(
            text(
                """
                SELECT leg_kind,cash_amount_per_source_share,currency
                  FROM catalog.v022_security_settlement_leg
                 WHERE security_lifecycle_event_id=:event
                """
            ),
            {"event": published.security_lifecycle_event_id},
        ).one()
    assert event == ("confirmed", "terminal", "published")
    assert leg == ("cash", Decimal("42.500000000000000000"), "USD")
    with (
        pytest.raises(Exception, match="append-only"),
        engine.begin() as connection,
    ):
        connection.execute(
            text(
                """
                UPDATE catalog.v022_security_lifecycle_event
                   SET event_status='estimated'
                 WHERE security_lifecycle_event_id=:event
                """
            ),
            {"event": published.security_lifecycle_event_id},
        )
    engine.dispose()
