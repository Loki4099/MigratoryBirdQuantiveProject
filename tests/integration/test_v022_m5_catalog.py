from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text

from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.publication import CatalogPublicationContext, publish_catalog_release

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]
MANIFEST = PROJECT_ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.3.json"
CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_m5_release_publishes_shared_strategy_family_and_corrected_defenses() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
        assert release.component_count == 477
        with engine.connect() as connection:
            strategy = connection.execute(
                text(
                    "SELECT count(DISTINCT family.family_key) family_count, "
                    "count(DISTINCT variant.variant_key) variant_count "
                    "FROM strategy.v022_strategy_family family JOIN "
                    "strategy.v022_strategy_variant variant ON "
                    "variant.strategy_family_id=family.strategy_family_id"
                )
            ).mappings().one()
            defenses = connection.execute(
                text(
                    "SELECT variant.variant_key,version.version_number "
                    "FROM defense.defense_variant variant JOIN defense.defense_version version "
                    "ON version.defense_variant_id=variant.defense_variant_id "
                    "WHERE variant.variant_key IN ('fixed20_defense','ma200_tiered_defense') "
                    "ORDER BY variant.variant_key"
                )
            ).all()
        assert dict(strategy) == {"family_count": 1, "variant_count": 2}
        assert [tuple(row) for row in defenses] == [
            ("fixed20_defense", 2),
            ("ma200_tiered_defense", 1),
        ]
    finally:
        engine.dispose()
