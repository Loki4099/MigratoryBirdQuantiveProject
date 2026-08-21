from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text

from style_rotation.catalog.asset_registry import publish_asset_registry
from style_rotation.data.bundle import publish_reserve_model
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.compiler_service import load_graph_catalog
from style_rotation.v022.graph import DefenseSpec
from style_rotation.v022.publication import (
    CatalogPublicationContext,
    publish_catalog_release,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]
MANIFEST = PROJECT_ROOT / "v0.22" / "catalogs" / "releases" / "catalog_release.v0.22.5.json"
ASSET_CATALOG = PROJECT_ROOT / "v0.21/catalogs/assets.v0.21.1.json"
RESERVE_MODEL = PROJECT_ROOT / "v0.2/catalogs/reserve_model.v0.2.0.json"
CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_defense_package_publication_freezes_exact_policy_registry_and_model_lineage() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        publish_asset_registry(engine, ASSET_CATALOG)
        publish_reserve_model(engine, RESERVE_MODEL)

        first = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
        replay = publish_catalog_release(engine, MANIFEST, context=CONTEXT)

        assert first.component_count == 496
        assert replay.release_artifact_id == first.release_artifact_id
        assert replay.reused_component_count == 496
        with engine.connect() as connection:
            compiler_catalog = load_graph_catalog(
                connection,
                first.catalog_release_id,
            )
            assert set(compiler_catalog.defense_versions) == {
                "fixed20_defense",
                "ma200_tiered_defense",
            }
            for specification in compiler_catalog.defense_versions.values():
                assert isinstance(specification, DefenseSpec)
                assert specification.supported_frequencies == ("weekly", "monthly")
                assert tuple(
                    item.asset_context_key
                    for item in specification.supported_asset_contexts
                ) == (
                    "us_style_rotation_4_etf_sample_v1",
                    "us_liquid_large_cap_300_pit_v1",
                )
            assert (
                connection.scalar(text("SELECT count(*) FROM defense.v022_timing_policy_version"))
                == 2
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM defense.v022_allocation_policy_version")
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM defense.v022_allocation_policy_member")
                )
                == 5
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM defense.v022_defense_package_policy_binding")
                )
                == 2
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM defense.v022_defense_package_supported_asset_set")
                )
                == 4
            )
            assert (
                connection.scalar(
                    text(
                        """
                    SELECT count(*)
                      FROM defense.v022_allocation_policy_version allocation
                      JOIN experiment.reserve_return_model_version model
                        ON model.reserve_return_model_version_id=
                           allocation.reserve_return_model_version_id
                       AND model.artifact_id=allocation.reserve_return_model_artifact_id
                      JOIN catalog.asset_registry_release registry
                        ON registry.asset_registry_release_id=
                           allocation.asset_registry_release_id
                       AND registry.artifact_id=allocation.asset_registry_artifact_id
                      JOIN catalog.asset_set_definition definition
                        ON definition.asset_set_definition_id=
                           allocation.asset_set_definition_id
                       AND definition.asset_registry_release_id=
                           registry.asset_registry_release_id
                     WHERE model.version_number=1
                       AND registry.catalog_version='0.21.1'
                       AND definition.set_key=
                           'standard_defensive_basket_long_history_v1'
                    """
                    )
                )
                == 1
            )
    finally:
        engine.dispose()


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_defense_package_catalog_fails_closed_without_exact_reserve_model() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        publish_asset_registry(engine, ASSET_CATALOG)

        with pytest.raises(ValueError, match="reserve_model_unpublished"):
            publish_catalog_release(engine, MANIFEST, context=CONTEXT)

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM lineage.artifact "
                        "WHERE artifact_type LIKE 'v022_defense_%'"
                    )
                )
                == 0
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM workspace.v022_catalog_release")) == 0
            )
    finally:
        engine.dispose()
