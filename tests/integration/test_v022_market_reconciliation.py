from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import DBAPIError

from style_rotation.catalog.scope import publish_research_scope
from style_rotation.data.calendar import CalendarPublicationService, XNYSCalendarGenerator
from style_rotation.data.service import SnapshotInput, SourceSnapshotService, publish_data_contracts
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.data_seed_import import (
    ProviderSecurityIdentityService,
    SourceSnapshotSecuritySubjectService,
)
from style_rotation.v022.market_reconciliation import (
    AlternateObservationService,
    AlternateObservationSetSpec,
    GapResolutionEvidenceRef,
    MarketGapResolutionService,
    MarketGapResolutionSpec,
    MarketReconciliationService,
    MarketReconciliationSpec,
)
from style_rotation.v022.yahoo_ingestion import (
    YahooEquityContractService,
    load_yahoo_equity_contract,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_reviewed_alternate_interval_publishes_new_replayable_dataset() -> None:
    assert DATABASE_URL is not None
    database_name = make_url(DATABASE_URL).database
    assert database_name is not None
    reset_database(DATABASE_URL, database_name, "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        publish_research_scope(engine, Path("v0.2/catalogs/research_scope.v0.2.0.json"))
        publish_data_contracts(engine, Path("v0.2/catalogs/data_contracts.v0.2.0.json"))
        YahooEquityContractService(engine).publish(
            load_yahoo_equity_contract(
                Path("v0.22/catalogs/data_contracts/equity_market.v0.22.0.json")
            )
        )
        calendar = CalendarPublicationService(engine).publish(
            XNYSCalendarGenerator().generate(date(2020, 1, 2), date(2020, 1, 6))
        )
        with engine.connect() as connection:
            asset_id = connection.execute(
                text("SELECT asset_id FROM catalog.asset WHERE asset_key='iwf'")
            ).scalar_one()
            cleaning_id = connection.execute(
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
            calendar_id = connection.execute(
                text(
                    "SELECT calendar_version_id FROM catalog.calendar_version "
                    "WHERE artifact_id=:artifact"
                ),
                {"artifact": calendar.artifact_id},
            ).scalar_one()
        security_id = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.security (
                      security_id,legacy_asset_id,security_key,name,instrument_type,
                      currency,status
                    ) VALUES (:id,:asset,'iwf_reconciliation_test','IWF Test',
                              'Exchange Traded Fund','USD','active')
                    """
                ),
                {"id": security_id, "asset": asset_id},
            )
        identifier = ProviderSecurityIdentityService(engine).register(
            security_id=security_id,
            provider_scope="yahoo_yfinance",
            provider_symbol="IWF",
            valid_from=date(2019, 1, 1),
            valid_to=None,
        )
        primary_snapshot, _ = _snapshot_and_subject(
            engine,
            security_id,
            identifier.security_identifier_id,
            "primary",
            (
                b"session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
                b"2020-01-02,50,50,50,50,50,100,0,0\n"
                b"2020-01-06,49,49,49,49,49,100,1,0\n"
            ),
        )
        _, alternate_subject = _snapshot_and_subject(
            engine,
            security_id,
            identifier.security_identifier_id,
            "alternate",
            (
                b"session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
                b"2020-01-03,50,50,50,50,777,150,0,2\n"
            ),
        )
        primary_dataset_id = uuid.uuid4()
        primary = ArtifactService(engine).publish(
            artifact_type="dataset_publication",
            artifact_key="sp500_primary_test",
            version_number=1,
            semantic_payload={"dataset": "primary"},
            content_payload={"rows": 2},
            dependencies=(DependencyInput(primary_snapshot, "source_snapshot", 0),),
            reason="publish primary reconciliation fixture",
            draft_writer=lambda connection, artifact_id: _write_primary_dataset(
                connection,
                primary_dataset_id,
                artifact_id,
                primary_snapshot,
                asset_id,
                cleaning_id,
                calendar_id,
            ),
        )
        review_evidence = ArtifactService(engine).publish(
            artifact_type="v022_market_review_note",
            artifact_key="iwf_2020_01_03_review",
            version_number=1,
            semantic_payload={"decision": "accept alternate raw observation"},
            content_payload={"reviewer": "test"},
            reason="publish reviewed provider comparison",
        )
        observation = AlternateObservationService(engine).publish(
            AlternateObservationSetSpec(
                alternate_subject,
                "iwf_alternate_2020_01_03",
                1,
                "reviewer",
            )
        )
        resolution = MarketGapResolutionService(engine).publish(
            MarketGapResolutionSpec(
                primary_dataset_publication_id=primary_dataset_id,
                security_id=security_id,
                gap_key="iwf_missing_2020_01_03",
                version_number=1,
                gap_type="missing_bar",
                gap_start=date(2020, 1, 3),
                gap_end=date(2020, 1, 3),
                resolution_kind="replace_with_alternate",
                alternate_observation_set_id=observation.alternate_observation_set_id,
                evidence=(
                    GapResolutionEvidenceRef(
                        review_evidence.artifact_id, "provider_comparison"
                    ),
                ),
                created_by="reviewer",
            )
        )
        spec = MarketReconciliationSpec(
            primary_dataset_publication_id=primary_dataset_id,
            resolution_ids=(resolution.market_gap_resolution_id,),
            cleaning_version_id=cleaning_id,
            calendar_version_id=calendar_id,
            output_dataset_key="sp500_reconciled_test",
            output_version_number=1,
            created_by="reviewer",
        )
        service = MarketReconciliationService(engine)
        published = service.reconcile(spec)
        replay = service.reconcile(spec)

        assert replay.reused is True
        assert replay.dataset_artifact_id == published.dataset_artifact_id
        assert published.replaced_bar_count == 1
        with engine.connect() as connection:
            output = connection.execute(
                text(
                    """
                    SELECT session_date,close_raw,close_adj,adjustment_factor
                      FROM data.daily_bar WHERE dataset_publication_id=:dataset
                     ORDER BY session_date
                    """
                ),
                {"dataset": published.dataset_publication_id},
            ).all()
            primary_count = connection.execute(
                text(
                    "SELECT count(*) FROM data.daily_bar "
                    "WHERE dataset_publication_id=:dataset"
                ),
                {"dataset": primary_dataset_id},
            ).scalar_one()
            binding = connection.execute(
                text(
                    """
                    SELECT reconstruction_policy,replaced_bar_count
                      FROM data.v022_reconciled_market_dataset_binding
                     WHERE dataset_publication_id=:dataset
                    """
                ),
                {"dataset": published.dataset_publication_id},
            ).one()
        assert primary_count == 2
        assert [(str(row[0]), row[1], row[2]) for row in output] == [
            ("2020-01-02", Decimal("50.0000000000"), Decimal("49.0000000000")),
            ("2020-01-03", Decimal("50.0000000000"), Decimal("49.0000000000")),
            ("2020-01-06", Decimal("49.0000000000"), Decimal("49.0000000000")),
        ]
        assert binding == ("split_normalized_ohlcv_dividends_backward_total_return_v2", 1)
        assert primary.artifact_id != published.dataset_artifact_id
        with engine.connect() as connection:
            transaction = connection.begin()
            with pytest.raises(DBAPIError, match="append-only"):
                connection.execute(
                    text(
                        "UPDATE data.v022_market_gap_resolution "
                        "SET created_by='mutated' WHERE market_gap_resolution_id=:id"
                    ),
                    {"id": resolution.market_gap_resolution_id},
                )
            transaction.rollback()
    finally:
        engine.dispose()


def _snapshot_and_subject(
    engine: Engine,
    security_id: uuid.UUID,
    identifier_id: uuid.UUID,
    key: str,
    payload: bytes,
) -> tuple[uuid.UUID, uuid.UUID]:
    snapshot = SourceSnapshotService(engine).publish(
        SnapshotInput(
            series_key="us_equity_daily_market_yahoo",
            series_version=1,
            snapshot_key=f"reconciliation_{key}",
            requested_at=datetime(2026, 8, 17, 1, tzinfo=UTC),
            fetched_at=datetime(2026, 8, 17, 1, 1, tzinfo=UTC),
            as_of_at=datetime(2026, 8, 17, 1, 1, tzinfo=UTC),
            media_type="text/csv; charset=utf-8",
            request_parameters={"provider_symbol": "IWF", "interval": "1d"},
            response_metadata={"adapter": "integration_fixture"},
            raw_payload=payload,
        )
    )
    with engine.connect() as connection:
        snapshot_id = connection.execute(
            text("SELECT source_snapshot_id FROM data.source_snapshot WHERE artifact_id=:id"),
            {"id": snapshot.artifact_id},
        ).scalar_one()
    subject = SourceSnapshotSecuritySubjectService(engine).bind(
        source_snapshot_id=snapshot_id,
        security_id=security_id,
        security_identifier_id=identifier_id,
        fetch_status="fetched",
    )
    return snapshot.artifact_id, subject.source_snapshot_security_subject_id


def _write_primary_dataset(
    connection: Connection,
    dataset_id: uuid.UUID,
    artifact_id: uuid.UUID,
    snapshot_artifact_id: uuid.UUID,
    asset_id: uuid.UUID,
    cleaning_id: uuid.UUID,
    calendar_id: uuid.UUID,
) -> None:
    snapshot_id = connection.execute(
        text("SELECT source_snapshot_id FROM data.source_snapshot WHERE artifact_id=:artifact"),
        {"artifact": snapshot_artifact_id},
    ).scalar_one()
    connection.execute(
        text(
            """
            INSERT INTO data.dataset_publication (
              dataset_publication_id,artifact_id,cleaning_version_id,calendar_version_id,
              dataset_key,version_number,dataset_kind,value_kind,coverage_start,
              coverage_end,row_count
            ) VALUES (:id,:artifact,:cleaning,:calendar,'sp500_primary_test',1,
                      'canonical','daily_bar','2020-01-02','2020-01-06',2)
            """
        ),
        {
            "id": dataset_id,
            "artifact": artifact_id,
            "cleaning": cleaning_id,
            "calendar": calendar_id,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO data.dataset_input (
              dataset_input_id,dataset_publication_id,source_snapshot_id,role,ordinal
            ) VALUES (:id,:dataset,:snapshot,'source_snapshot',0)
            """
        ),
        {"id": uuid.uuid4(), "dataset": dataset_id, "snapshot": snapshot_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO data.daily_bar (
              dataset_publication_id,asset_id,session_date,open_raw,high_raw,low_raw,
              close_raw,adj_close,open_adj,high_adj,low_adj,close_adj,
              adjustment_factor,volume_raw
            ) VALUES
              (:dataset,:asset,'2020-01-02',50,50,50,50,50,50,50,50,50,1,100),
              (:dataset,:asset,'2020-01-06',49,49,49,49,49,49,49,49,49,1,100)
            """
        ),
        {"dataset": dataset_id, "asset": asset_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO data.corporate_action (
              dataset_publication_id,asset_id,effective_date,cash_dividend,split_ratio
            ) VALUES (:dataset,:asset,'2020-01-06',1,0)
            """
        ),
        {"dataset": dataset_id, "asset": asset_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO data.dataset_coverage (
              dataset_coverage_id,dataset_publication_id,asset_id,subject_key,
              coverage_start,coverage_end,observation_count,missing_count
            ) VALUES (:id,:dataset,:asset,'iwf','2020-01-02','2020-01-06',2,1)
            """
        ),
        {"id": uuid.uuid4(), "dataset": dataset_id, "asset": asset_id},
    )
