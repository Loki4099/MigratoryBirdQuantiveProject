from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.parity_publication import publish_parity_evidence
from style_rotation.v022.publication import (
    CatalogPublicationContext,
    publish_catalog_release,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
ROOT = Path(__file__).parents[2]
CATALOG = ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.2.json"
REGISTRY = ROOT / "v0.22/m4/migration-registry.v0.22.3.json"
EVIDENCE = ROOT / "v0.22/m4/parity-evidence.v0.22.0.json"
CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_m4_parity_evidence_publication_is_complete_idempotent_and_append_only() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    catalog = publish_catalog_release(engine, CATALOG, context=CONTEXT)

    first = publish_parity_evidence(
        engine,
        REGISTRY,
        EVIDENCE,
        catalog_release_artifact_id=catalog.release_artifact_id,
    )
    second = publish_parity_evidence(
        engine,
        REGISTRY,
        EVIDENCE,
        catalog_release_artifact_id=catalog.release_artifact_id,
    )

    assert len(first.evidence_artifact_ids) == 79
    assert first.reused_evidence_count == 0
    assert first.registry_reused is False
    assert second.reused_evidence_count == 79
    assert second.registry_reused is True
    assert second.registry_artifact_id == first.registry_artifact_id
    assert second.evidence_artifact_ids == first.evidence_artifact_ids

    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM compatibility.v022_parity_evidence),"
                "(SELECT count(*) FROM compatibility.v022_migration_registry),"
                "(SELECT count(*) FROM compatibility.v022_migration_registry_member),"
                "(SELECT count(*) FROM lineage.artifact_dependency "
                " WHERE artifact_id=:registry AND role='parity_evidence')"
            ),
            {"registry": first.registry_artifact_id},
        ).one()
    assert counts == (79, 1, 79, 79)

    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE compatibility.v022_parity_evidence SET passed=false"
            )
        )
    engine.dispose()
