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
MANIFEST = PROJECT_ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.4.json"
CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_v0224_release_publishes_exact_strategy_parameter_preset_versions() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
        assert release.component_count == 487
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT variant.variant_key,definition.preset_key,"
                    "version.version_number,version.resolved_parameters,"
                    "version.parameter_fingerprint,"
                    "strategy.v022_strategy_parameter_fingerprint("
                    "version.resolved_parameters) AS recomputed_parameter_fingerprint,"
                    "artifact.artifact_key,artifact.status "
                    "FROM strategy.v022_strategy_parameter_preset_version version "
                    "JOIN strategy.v022_strategy_parameter_preset_definition definition "
                    "ON definition.strategy_parameter_preset_definition_id="
                    "version.strategy_parameter_preset_definition_id "
                    "JOIN strategy.v022_strategy_variant variant "
                    "ON variant.strategy_variant_id=definition.strategy_variant_id "
                    "JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id "
                    "ORDER BY variant.variant_key,definition.preset_key"
                )
            ).mappings().all()
            release_components = connection.scalar(
                text(
                    "SELECT count(*) FROM workspace.v022_catalog_release_component "
                    "WHERE catalog_release_id=:release AND component_kind IN "
                    "('strategy_parameter_preset_definition',"
                    "'strategy_parameter_preset_version')"
                ),
                {"release": release.catalog_release_id},
            )

        assert release_components == 10
        assert [
            (row["variant_key"], row["preset_key"], row["resolved_parameters"])
            for row in rows
        ] == [
            (
                "cross_section_rank_top_k_large_cap_parity",
                "k10",
                {"target_k": 10, "selection_buffer": "half_k", "sector_cap": "none"},
            ),
            (
                "cross_section_rank_top_k_large_cap_parity",
                "k20",
                {"target_k": 20, "selection_buffer": "half_k", "sector_cap": "none"},
            ),
            (
                "cross_section_rank_top_k_parity",
                "k1",
                {"target_k": 1, "selection_buffer": "none", "sector_cap": "none"},
            ),
            (
                "cross_section_rank_top_k_parity",
                "k2",
                {"target_k": 2, "selection_buffer": "none", "sector_cap": "none"},
            ),
            (
                "cross_section_rank_top_k_parity",
                "k3",
                {"target_k": 3, "selection_buffer": "none", "sector_cap": "none"},
            ),
        ]
        assert all(row["version_number"] == 1 for row in rows)
        assert all(
            row["parameter_fingerprint"] == row["recomputed_parameter_fingerprint"]
            for row in rows
        )
        assert all(
            row["artifact_key"] == f"{row['variant_key']}__{row['preset_key']}"
            for row in rows
        )
        assert all(row["status"] == "published" for row in rows)
    finally:
        engine.dispose()
