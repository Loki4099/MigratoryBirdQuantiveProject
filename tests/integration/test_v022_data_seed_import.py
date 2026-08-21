from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from style_rotation.catalog.scope import publish_research_scope
from style_rotation.data.service import SnapshotInput, SourceSnapshotService, publish_data_contracts
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.data_seed_import import (
    ExternalImportManifestService,
    ExternalImportManifestSpec,
    ExternalImportObjectSpec,
    ProviderSecurityIdentityService,
    SourceSnapshotSecuritySubjectService,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_seed_manifest_and_provider_subject_replay_are_exact() -> None:
    assert DATABASE_URL is not None
    database_name = make_url(DATABASE_URL).database
    assert database_name is not None
    reset_database(DATABASE_URL, database_name, "test")
    engine = create_postgres_engine(DATABASE_URL)
    publish_research_scope(engine, Path("v0.2/catalogs/research_scope.v0.2.0.json"))
    publish_data_contracts(engine, Path("v0.2/catalogs/data_contracts.v0.2.0.json"))

    security_id = uuid.uuid4()
    other_security_id = uuid.uuid4()
    with engine.begin() as connection:
        for item_id, key in ((security_id, "brk_b_seed"), (other_security_id, "brk_b_other")):
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.security (
                      security_id,security_key,name,instrument_type,currency,status
                    ) VALUES (:id,:key,:key,'Common Stock','USD','active')
                    """
                ),
                {"id": item_id, "key": key},
            )

    identifiers = ProviderSecurityIdentityService(engine)
    identifier = identifiers.register(
        security_id=security_id,
        provider_scope="yahoo_yfinance",
        provider_symbol="BRK-B",
        valid_from=date(2004, 12, 31),
        valid_to=date(2026, 7, 1),
    )
    replayed_identifier = identifiers.register(
        security_id=security_id,
        provider_scope="yahoo_yfinance",
        provider_symbol="BRK-B",
        valid_from=date(2004, 12, 31),
        valid_to=date(2026, 7, 1),
    )
    assert replayed_identifier.reused is True
    assert replayed_identifier.security_identifier_id == identifier.security_identifier_id
    with pytest.raises(Exception, match="effective periods overlap"):
        identifiers.register(
            security_id=other_security_id,
            provider_scope="yahoo_yfinance",
            provider_symbol="brk-b",
            valid_from=date(2020, 1, 1),
            valid_to=date(2025, 1, 1),
        )

    requested = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)
    snapshot_publication = SourceSnapshotService(engine).publish(
        SnapshotInput(
            series_key="us_etf_daily_market",
            series_version=1,
            snapshot_key="brk-b-phase1-seed",
            requested_at=requested,
            fetched_at=requested + timedelta(seconds=1),
            as_of_at=requested,
            media_type="application/json",
            request_parameters={"ticker": "BRK-B", "interval": "1d"},
            response_metadata={"status_code": 200},
            raw_payload=b'{"chart":{"result":[{"symbol":"BRK-B"}]}}',
        )
    )
    with engine.connect() as connection:
        snapshot_id = connection.execute(
            text("SELECT source_snapshot_id FROM data.source_snapshot WHERE artifact_id=:id"),
            {"id": snapshot_publication.artifact_id},
        ).scalar_one()

    subjects = SourceSnapshotSecuritySubjectService(engine)
    subject = subjects.bind(
        source_snapshot_id=snapshot_id,
        security_id=security_id,
        security_identifier_id=identifier.security_identifier_id,
        fetch_status="fetched",
    )
    replayed_subject = subjects.bind(
        source_snapshot_id=snapshot_id,
        security_id=security_id,
        security_identifier_id=identifier.security_identifier_id,
        fetch_status="fetched",
    )
    assert replayed_subject.reused is True
    assert replayed_subject.source_snapshot_security_subject_id == (
        subject.source_snapshot_security_subject_id
    )
    with (
        pytest.raises(Exception, match="Bound Security Identifiers are immutable"),
        engine.begin() as connection,
    ):
        connection.execute(
            text(
                "UPDATE catalog.security_identifier SET valid_to='2026-06-30' "
                "WHERE security_identifier_id=:id"
            ),
            {"id": identifier.security_identifier_id},
        )

    membership_hash = "a" * 64
    manifest_spec = ExternalImportManifestSpec(
        manifest_key="sp500_historical_seed_v1",
        version_number=1,
        source_project_key="momentum_reversion_method",
        source_release_key="2013warmup_2018eval_2026",
        objects=(
            ExternalImportObjectSpec(
                object_role="membership_source",
                logical_key="fja05680_sp500_history",
                media_type="text/csv",
                content_sha256=membership_hash,
                size_bytes=2718,
                source_uri=f"content://sha256/{membership_hash}",
                license_key="MIT",
                provenance_status="verified",
                usage_scope="redistributable",
                metadata={"source_repository": "fja05680/sp500"},
            ),
        ),
        created_by="local",
    )
    manifests = ExternalImportManifestService(engine)
    manifest = manifests.publish(manifest_spec)
    replayed_manifest = manifests.publish(manifest_spec)
    assert replayed_manifest.reused is True
    assert replayed_manifest.artifact_id == manifest.artifact_id
    assert replayed_manifest.manifest_fingerprint == manifest.manifest_fingerprint
    with (
        pytest.raises(Exception, match="append-only"),
        engine.begin() as connection,
    ):
        connection.execute(
            text(
                "UPDATE data.v022_external_import_object SET size_bytes=999 "
                "WHERE external_import_manifest_id=:id"
            ),
            {"id": manifest.external_import_manifest_id},
        )
