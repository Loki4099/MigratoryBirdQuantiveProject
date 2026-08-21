from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from style_rotation.catalog.scope import publish_research_scope
from style_rotation.data.calendar import CalendarPublicationService, XNYSCalendarGenerator
from style_rotation.data.providers.snapshots import RawFetch
from style_rotation.data.service import publish_data_contracts
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.data_seed_import import (
    ExternalImportManifestService,
    ExternalImportManifestSpec,
    ExternalImportObjectSpec,
    ProviderSecurityIdentityService,
)
from style_rotation.v022.historical_universe import (
    HistoricalSp500UniversePublicationService,
    HistoricalSp500UniverseSpec,
    MembershipSecurityMapping,
    parse_fja_snapshot_csv,
)
from style_rotation.v022.security_market_data import (
    SecurityMarketDataPublicationService,
    SecurityMarketPublicationSpec,
)
from style_rotation.v022.yahoo_ingestion import (
    YahooEquityContractService,
    YahooIngestionExecutionService,
    YahooIngestionPlanService,
    YahooIngestionPlanSpec,
    load_yahoo_equity_contract,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")

MEMBERSHIP_CSV = (
    'Date,tickers\n'
    '2017-12-29,"AAA,BRK.B,OLD,REN.OLD"\n'
    '2018-01-03,"AAA,BRK.B,NEW,REN.NEW"\n'
    '2018-06-01,"AAA,BRK.B,NEW,REN.NEW"\n'
    '2019-01-02,"AAA,NEW,REN.NEW"\n'
    '2020-01-02,"AAA,BRK.B,NEW,REN.NEW"\n'
)


class _FlakyYahooAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, symbol: str, start: date, end_exclusive: date) -> RawFetch:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary provider failure")
        requested = datetime(2026, 8, 16, 4, tzinfo=UTC) + timedelta(
            seconds=self.calls * 2
        )
        fetched = requested + timedelta(seconds=1)
        payload = (
            "session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
            f"{start.isoformat()},10,11,9,10,10,100,0,0\n"
        ).encode()
        return RawFetch(
            requested_at=requested,
            fetched_at=fetched,
            as_of_at=fetched,
            media_type="text/csv; charset=utf-8",
            request_parameters={
                "tickers": symbol,
                "provider_ticker": symbol,
                "start": start.isoformat(),
                "end": end_exclusive.isoformat(),
                "interval": "1d",
                "auto_adjust": False,
                "actions": True,
            },
            response_metadata={"adapter": "test", "row_count": 1},
            payload=payload,
        )


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_source_backed_history_is_exact_immutable_and_replayable() -> None:
    assert DATABASE_URL is not None
    database_name = make_url(DATABASE_URL).database
    assert database_name is not None
    reset_database(DATABASE_URL, database_name, "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        publish_research_scope(engine, Path("v0.2/catalogs/research_scope.v0.2.0.json"))
        publish_data_contracts(engine, Path("v0.2/catalogs/data_contracts.v0.2.0.json"))
        securities = {
            symbol: uuid.uuid4() for symbol in ("AAA", "BRK.B", "OLD", "NEW", "REN")
        }
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.security (
                      security_id,security_key,name,instrument_type,currency,status
                    ) VALUES (:id,:key,:key,'Common Stock','USD','active')
                    """
                ),
                [
                    {"id": security_id, "key": symbol.casefold().replace(".", "_")}
                    for symbol, security_id in securities.items()
                ],
            )

        content_hash = hashlib.sha256(MEMBERSHIP_CSV.encode()).hexdigest()
        imported = ExternalImportManifestService(engine).publish(
            ExternalImportManifestSpec(
                manifest_key="sp500_membership_seed_v1",
                version_number=1,
                source_project_key="momentum_reversion_method",
                source_release_key="fja05680_snapshot_seed",
                objects=(
                    ExternalImportObjectSpec(
                        object_role="membership_source",
                        logical_key="fja05680_sp500_history",
                        media_type="text/csv",
                        content_sha256=content_hash,
                        size_bytes=len(MEMBERSHIP_CSV.encode()),
                        source_uri=f"content://sha256/{content_hash}",
                        license_key="MIT",
                        provenance_status="verified",
                        usage_scope="redistributable",
                        metadata={"columns": ["Date", "tickers"]},
                    ),
                ),
                created_by="local",
            )
        )
        spec = HistoricalSp500UniverseSpec(
            external_import_manifest_artifact_id=imported.artifact_id,
            source_object_logical_key="fja05680_sp500_history",
            universe_key="sp500_historical_seed_v1",
            version_number=1,
            methodology_key="sp500_historical_membership",
            methodology_version=1,
            research_tier="rankable_research",
            snapshots=parse_fja_snapshot_csv(MEMBERSHIP_CSV),
            mappings=(
                MembershipSecurityMapping("AAA", securities["AAA"]),
                MembershipSecurityMapping("BRK.B", securities["BRK.B"]),
                MembershipSecurityMapping("OLD", securities["OLD"]),
                MembershipSecurityMapping("NEW", securities["NEW"]),
                MembershipSecurityMapping("REN.OLD", securities["REN"]),
                MembershipSecurityMapping("REN.NEW", securities["REN"]),
            ),
            data_cutoff_at=datetime(2026, 8, 16, 2, tzinfo=UTC),
            published_at=datetime(2026, 8, 16, 3, tzinfo=UTC),
            created_by="local",
        )
        service = HistoricalSp500UniversePublicationService(engine)
        publication = service.publish(spec)
        replay = service.publish(spec)

        assert replay.reused is True
        assert replay.universe_history_artifact_id == publication.universe_history_artifact_id
        assert publication.snapshot_count == 4
        assert publication.event_count == 8
        with engine.connect() as connection:
            batches = connection.execute(
                text(
                    """
                    SELECT effective_session,added_count,removed_count,source_member_count
                      FROM catalog.v022_universe_change_batch ORDER BY ordinal
                    """
                )
            ).all()
            snapshots = connection.execute(
                text(
                    """
                    SELECT snapshot.effective_session,count(member.security_id)
                      FROM catalog.universe_snapshot snapshot
                      JOIN catalog.universe_snapshot_member member
                        ON member.universe_snapshot_id=snapshot.universe_snapshot_id
                     WHERE snapshot.universe_history_id=:history
                     GROUP BY snapshot.effective_session ORDER BY snapshot.effective_session
                    """
                ),
                {"history": publication.universe_history_id},
            ).all()
            dependencies = connection.execute(
                text(
                    """
                    SELECT role,ordinal FROM lineage.artifact_dependency
                     WHERE artifact_id=:artifact ORDER BY ordinal
                    """
                ),
                {"artifact": publication.universe_history_artifact_id},
            ).all()
        assert [(str(row[0]), row[1], row[2], row[3]) for row in batches] == [
            ("2017-12-29", 4, 0, 4),
            ("2018-01-03", 1, 1, 4),
            ("2019-01-02", 0, 1, 3),
            ("2020-01-02", 1, 0, 4),
        ]
        assert [(str(row[0]), row[1]) for row in snapshots] == [
            ("2017-12-29", 4),
            ("2018-01-03", 4),
            ("2019-01-02", 3),
            ("2020-01-02", 4),
        ]
        assert dependencies == [("universe_methodology", 0), ("membership_ledger", 1)]

        identifiers = ProviderSecurityIdentityService(engine)
        identifier_specs = (
            ("AAA", securities["AAA"], date(2017, 1, 1), None),
            ("BRK-B", securities["BRK.B"], date(2017, 1, 1), None),
            ("OLD", securities["OLD"], date(2017, 1, 1), date(2018, 1, 3)),
            ("NEW", securities["NEW"], date(2018, 1, 3), None),
            ("REN-OLD", securities["REN"], date(2017, 1, 1), date(2018, 1, 3)),
            ("REN-NEW", securities["REN"], date(2018, 1, 3), None),
        )
        for symbol, security_id, valid_from, valid_to in identifier_specs:
            identifiers.register(
                security_id=security_id,
                provider_scope="yahoo_yfinance",
                provider_symbol=symbol,
                valid_from=valid_from,
                valid_to=valid_to,
            )
        equity_contract = YahooEquityContractService(engine).publish(
            load_yahoo_equity_contract(
                Path("v0.22/catalogs/data_contracts/equity_market.v0.22.0.json")
            )
        )
        ingestion_plans = YahooIngestionPlanService(engine)
        plan_spec = YahooIngestionPlanSpec(
            plan_key="sp500_yahoo_history_v1",
            version_number=1,
            universe_history_id=publication.universe_history_id,
            data_series_version_id=equity_contract.data_series_version_id,
            coverage_start=date(2017, 1, 1),
            coverage_end=date(2020, 12, 31),
            created_by="local",
        )
        plan = ingestion_plans.publish(plan_spec)
        plan_replay = ingestion_plans.publish(plan_spec)
        assert plan.segment_count == 6
        assert plan_replay.reused is True
        assert plan_replay.artifact_id == plan.artifact_id

        adapter = _FlakyYahooAdapter()
        executor = YahooIngestionExecutionService(
            engine, adapter, clock=lambda: datetime(2026, 8, 16, 4, tzinfo=UTC)
        )
        first_attempt = executor.execute_pending(plan.yahoo_ingestion_plan_id, limit=1)
        assert [item.status for item in first_attempt] == ["failed"]
        assert len(executor.pending_segment_ids(plan.yahoo_ingestion_plan_id)) == 6
        resumed = executor.execute_pending(plan.yahoo_ingestion_plan_id)
        assert len(resumed) == 6
        assert {item.status for item in resumed} == {"fetched"}
        assert executor.pending_segment_ids(plan.yahoo_ingestion_plan_id) == ()

        with engine.connect() as connection:
            attempts = connection.execute(
                text(
                    """
                    SELECT segment.ordinal,attempt.attempt_ordinal,attempt.attempt_status
                      FROM data.v022_yahoo_ingestion_attempt attempt
                      JOIN data.v022_yahoo_ingestion_segment segment ON
                        segment.yahoo_ingestion_segment_id=
                        attempt.yahoo_ingestion_segment_id
                     ORDER BY segment.ordinal,attempt.attempt_ordinal
                    """
                )
            ).all()
            rename_segments = connection.execute(
                text(
                    """
                    SELECT provider_symbol,coverage_start,coverage_end,security_id
                      FROM data.v022_yahoo_ingestion_segment
                     WHERE provider_symbol LIKE 'REN-%' ORDER BY coverage_start
                    """
                )
            ).all()
        assert attempts[:2] == [(0, 0, "failed"), (0, 1, "fetched")]
        assert len(attempts) == 7
        assert [(row[0], str(row[1]), str(row[2])) for row in rename_segments] == [
            ("REN-OLD", "2017-01-01", "2018-01-02"),
            ("REN-NEW", "2018-01-03", "2020-12-31"),
        ]
        assert rename_segments[0][3] == rename_segments[1][3] == securities["REN"]
        with pytest.raises(
            ValueError, match="Completed Yahoo ingestion segment cannot be retried"
        ):
            executor.execute_segment(resumed[0].yahoo_ingestion_segment_id)

        calendar = CalendarPublicationService(engine).publish(
            XNYSCalendarGenerator().generate(date(2017, 1, 1), date(2020, 12, 31))
        )
        with engine.connect() as connection:
            cleaning_version_id = connection.execute(
                text(
                    """
                    SELECT version.cleaning_version_id
                      FROM data.cleaning_version version
                      JOIN data.cleaning_definition definition ON
                        definition.cleaning_definition_id=version.cleaning_definition_id
                     WHERE definition.cleaning_key='adjusted_ohlc'
                       AND version.version_number=1
                    """
                )
            ).scalar_one()
        quality = SecurityMarketDataPublicationService(engine).publish(
            SecurityMarketPublicationSpec(
                yahoo_ingestion_plan_id=plan.yahoo_ingestion_plan_id,
                calendar_artifact_id=calendar.artifact_id,
                cleaning_version_id=cleaning_version_id,
                dataset_key="sp500_security_daily_market_canonical_test",
                version_number=1,
                research_tier="rankable_research",
                created_by="local",
            )
        )
        assert quality.error_count > 0
        assert quality.dataset_artifact_id is None
        with engine.connect() as connection:
            persisted_quality = connection.execute(
                text(
                    """
                    SELECT error_count,report_document->>'historical_pit_claimed'
                      FROM data.v022_security_market_quality_report
                     WHERE security_market_quality_report_id=:report
                    """
                ),
                {"report": quality.quality_report_id},
            ).one()
        assert persisted_quality[0] == quality.error_count
        assert persisted_quality[1] == "false"

        with pytest.raises(Exception, match="append-only"), engine.begin() as connection:
            connection.execute(
                text("UPDATE catalog.v022_universe_membership_event SET reason_code='drift'")
            )
        with pytest.raises(
            Exception, match="Source-backed v0.22 Universe projections are immutable"
        ), engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE catalog.universe_snapshot_member SET ordinal=ordinal+100
                     WHERE universe_snapshot_id IN (
                       SELECT universe_snapshot_id FROM catalog.universe_snapshot
                        WHERE universe_history_id=:history
                     )
                    """
                ),
                {"history": publication.universe_history_id},
            )
    finally:
        engine.dispose()
