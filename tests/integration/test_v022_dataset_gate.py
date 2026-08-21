from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, date, datetime
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
from style_rotation.v022.dataset_gate import (
    DatasetGateAssessmentService,
    DatasetGateAssessmentSpec,
    DatasetGateEvidenceRef,
    DatasetGateFinding,
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

_MEMBERSHIP_CSV = 'Date,tickers\n2020-01-02,"AAA"\n2020-01-10,"AAA"\n'
_SESSIONS = (
    date(2020, 1, 2),
    date(2020, 1, 3),
    date(2020, 1, 6),
    date(2020, 1, 7),
    date(2020, 1, 8),
    date(2020, 1, 9),
    date(2020, 1, 10),
)


class _CompleteYahooAdapter:
    def fetch(self, symbol: str, start: date, end_exclusive: date) -> RawFetch:
        rows = [
            "session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits"
        ]
        rows.extend(
            f"{session.isoformat()},10,11,9,10,10,100,0,0"
            for session in _SESSIONS
            if start <= session < end_exclusive
        )
        requested = datetime(2020, 1, 11, 1, tzinfo=UTC)
        fetched = datetime(2020, 1, 11, 1, 1, tzinfo=UTC)
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
            response_metadata={"adapter": "test", "row_count": len(rows) - 1},
            payload=("\n".join(rows) + "\n").encode(),
        )


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_dataset_gate_publishes_exact_independent_decisions_and_replays() -> None:
    assert DATABASE_URL is not None
    database_name = make_url(DATABASE_URL).database
    assert database_name is not None
    reset_database(DATABASE_URL, database_name, "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        publish_research_scope(engine, Path("v0.2/catalogs/research_scope.v0.2.0.json"))
        publish_data_contracts(engine, Path("v0.2/catalogs/data_contracts.v0.2.0.json"))
        security_id = uuid.uuid4()
        with engine.begin() as connection:
            asset_id = connection.execute(
                text("SELECT asset_id FROM catalog.asset WHERE asset_key='iwf'")
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.security (
                      security_id,security_key,name,instrument_type,currency,status,
                      legacy_asset_id
                    ) VALUES (:id,'aaa','AAA','Common Stock','USD','active',:asset)
                    """
                ),
                {"id": security_id, "asset": asset_id},
            )

        source_hash = hashlib.sha256(_MEMBERSHIP_CSV.encode()).hexdigest()
        imported = ExternalImportManifestService(engine).publish(
            ExternalImportManifestSpec(
                manifest_key="m105_sp500_seed",
                version_number=1,
                source_project_key="m105_integration",
                source_release_key="m105_fixture_v1",
                objects=(
                    ExternalImportObjectSpec(
                        object_role="membership_source",
                        logical_key="m105_sp500_history",
                        media_type="text/csv",
                        content_sha256=source_hash,
                        size_bytes=len(_MEMBERSHIP_CSV.encode()),
                        source_uri=f"content://sha256/{source_hash}",
                        license_key="test_fixture",
                        provenance_status="verified",
                        usage_scope="redistributable",
                        metadata={"columns": ["Date", "tickers"]},
                    ),
                ),
                created_by="local",
            )
        )
        history = HistoricalSp500UniversePublicationService(engine).publish(
            HistoricalSp500UniverseSpec(
                external_import_manifest_artifact_id=imported.artifact_id,
                source_object_logical_key="m105_sp500_history",
                universe_key="m105_sp500_history",
                version_number=1,
                methodology_key="sp500_historical_membership",
                methodology_version=1,
                research_tier="rankable_research",
                snapshots=parse_fja_snapshot_csv(_MEMBERSHIP_CSV),
                mappings=(MembershipSecurityMapping("AAA", security_id),),
                data_cutoff_at=datetime(2020, 1, 10, 22, tzinfo=UTC),
                published_at=datetime(2020, 1, 11, 2, tzinfo=UTC),
                created_by="local",
            )
        )
        with engine.connect() as connection:
            ledger_id = connection.execute(
                text(
                    """
                    SELECT universe_membership_ledger_id
                      FROM catalog.v022_universe_history_ledger_binding
                     WHERE universe_history_id=:history
                    """
                ),
                {"history": history.universe_history_id},
            ).scalar_one()

        ProviderSecurityIdentityService(engine).register(
            security_id=security_id,
            provider_scope="yahoo_yfinance",
            provider_symbol="AAA",
            valid_from=date(2020, 1, 2),
            valid_to=None,
        )
        contract = YahooEquityContractService(engine).publish(
            load_yahoo_equity_contract(
                Path("v0.22/catalogs/data_contracts/equity_market.v0.22.0.json")
            )
        )
        plan = YahooIngestionPlanService(engine).publish(
            YahooIngestionPlanSpec(
                plan_key="m105_market_plan",
                version_number=1,
                universe_history_id=history.universe_history_id,
                data_series_version_id=contract.data_series_version_id,
                coverage_start=_SESSIONS[0],
                coverage_end=_SESSIONS[-1],
                created_by="local",
            )
        )
        executed = YahooIngestionExecutionService(
            engine,
            _CompleteYahooAdapter(),
            clock=lambda: datetime(2020, 1, 11, 3, tzinfo=UTC),
        ).execute_pending(plan.yahoo_ingestion_plan_id)
        assert [item.status for item in executed] == ["fetched"]

        calendar = CalendarPublicationService(engine).publish(
            XNYSCalendarGenerator().generate(_SESSIONS[0], _SESSIONS[-1])
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
        market = SecurityMarketDataPublicationService(engine).publish(
            SecurityMarketPublicationSpec(
                yahoo_ingestion_plan_id=plan.yahoo_ingestion_plan_id,
                calendar_artifact_id=calendar.artifact_id,
                cleaning_version_id=cleaning_version_id,
                dataset_key="m105_security_daily_market",
                version_number=1,
                research_tier="rankable_research",
                created_by="local",
            )
        )
        assert market.error_count == 0
        assert market.dataset_publication_id is not None

        spec = DatasetGateAssessmentSpec(
            dataset_publication_id=market.dataset_publication_id,
            universe_membership_ledger_id=ledger_id,
            gate_key="m105_gate_v1",
            version_number=1,
            assessed_coverage_start=_SESSIONS[0],
            assessed_coverage_end=_SESSIONS[-1],
            ranking_eligibility="rankable_research",
            product_eligibility="eligible_with_warnings",
            evidence=(DatasetGateEvidenceRef(imported.artifact_id, "supporting_evidence"),),
            findings=(
                DatasetGateFinding(
                    finding_code="historical_membership_retrospective",
                    finding_category="membership",
                    severity="warning",
                    ranking_effect="none",
                    product_effect="warning",
                    evidence_artifact_id=imported.artifact_id,
                ),
                DatasetGateFinding(
                    finding_code="retrospective_price_snapshot",
                    finding_category="data_provenance",
                    severity="warning",
                    ranking_effect="none",
                    product_effect="warning",
                ),
            ),
            uniform_exclusions=(),
            created_by="local",
        )
        service = DatasetGateAssessmentService(engine)
        published = service.publish(spec)
        replay = service.publish(spec)
        assert published.ranking_eligibility == "rankable_research"
        assert published.product_eligibility == "eligible_with_warnings"
        assert published.warning_count == 2
        assert published.blocker_count == 0
        assert replay.reused is True
        assert replay.artifact_id == published.artifact_id

        with engine.connect() as connection:
            decisions = connection.execute(
                text(
                    """
                    SELECT ranking_eligibility,product_eligibility,
                           finding_count,evidence_count,assessment_document
                      FROM data.v022_dataset_gate_assessment
                     WHERE dataset_gate_assessment_id=:assessment
                    """
                ),
                {"assessment": published.dataset_gate_assessment_id},
            ).one()
        assert decisions[0:4] == ("rankable_research", "eligible_with_warnings", 2, 1)
        assert decisions[4]["historical_pit_claimed"] is False
        assert decisions[4]["findings"][0]["finding_code"] == (
            "historical_membership_retrospective"
        )

        with pytest.raises(Exception, match="append-only"), engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE data.v022_dataset_gate_assessment
                       SET product_eligibility='eligible'
                     WHERE dataset_gate_assessment_id=:assessment
                    """
                ),
                {"assessment": published.dataset_gate_assessment_id},
            )
    finally:
        engine.dispose()
