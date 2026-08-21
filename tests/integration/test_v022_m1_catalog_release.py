from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from style_rotation.catalog.asset_registry import publish_asset_registry
from style_rotation.data.bundle import publish_reserve_model
from style_rotation.lineage.service import ArtifactService
from style_rotation.persistence.database import (
    downgrade_database,
    reset_database,
    upgrade_database,
)
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.publication import (
    CatalogPublicationContext,
    publish_catalog_release,
    verify_published_catalog,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]
MANIFEST = PROJECT_ROOT / "v0.22" / "catalogs" / "releases" / "catalog_release.v0.22.0.json"
CATALOG_7 = PROJECT_ROOT / "v0.22" / "catalogs" / "releases" / "catalog_release.v0.22.7.json"
CATALOG_8 = PROJECT_ROOT / "v0.22" / "catalogs" / "releases" / "catalog_release.v0.22.8.json"
ASSET_CATALOG = PROJECT_ROOT / "v0.21" / "catalogs" / "assets.v0.21.1.json"
RESERVE_MODEL = PROJECT_ROOT / "v0.2" / "catalogs" / "reserve_model.v0.2.0.json"
PUBLICATION_CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_native_taxonomy_release_publishes_after_previous_catalog() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        publish_asset_registry(engine, ASSET_CATALOG)
        publish_reserve_model(engine, RESERVE_MODEL)
        previous = publish_catalog_release(engine, CATALOG_7, context=PUBLICATION_CONTEXT)
        current = publish_catalog_release(engine, CATALOG_8, context=PUBLICATION_CONTEXT)
        rebuilt = verify_published_catalog(engine, current.release_artifact_id)

        assert previous.release_artifact_id != current.release_artifact_id
        assert current.release_reused is False
        assert rebuilt["status"] == "passed"
        assert rebuilt["component_count"] == 507
    finally:
        engine.dispose()


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_m1_catalog_release_is_rebuildable_idempotent_and_append_only() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    first = publish_catalog_release(engine, MANIFEST, context=PUBLICATION_CONTEXT)
    second = publish_catalog_release(engine, MANIFEST, context=PUBLICATION_CONTEXT)
    rebuilt = verify_published_catalog(engine, first.release_artifact_id)

    assert first.component_count == 67
    assert first.reused_component_count == 0
    assert second.reused_component_count == 67
    assert second.release_reused is True
    assert second.evidence_reused is True
    assert first.release_artifact_id == second.release_artifact_id
    assert rebuilt["status"] == "passed"
    assert rebuilt["component_count"] == 67
    assert all(rebuilt["checks"].values())

    with engine.connect() as connection:
        counts = connection.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM data.payload_contract_family),
                  (SELECT count(*) FROM data.payload_contract_version),
                  (SELECT count(*) FROM data.physical_encoding_version),
                  (SELECT count(*) FROM processing.feature_version WHERE origin_stage = 0),
                  (SELECT count(*) FROM aggregation.aggregation_family),
                  (SELECT count(*) FROM strategy.v022_strategy_version),
                  (SELECT count(*) FROM defense.defense_version),
                  (SELECT count(*) FROM workspace.v022_catalog_release_component)
                """
            )
        ).one()
    assert counts == (5, 5, 1, 9, 4, 1, 2, 67)

    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing.feature_family SET name = 'mutated' "
                "WHERE family_key = 'adjusted_close'"
            )
        )
    engine.dispose()


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_database_allows_earlier_feature_binding_for_compiler_projection() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    publish_catalog_release(engine, MANIFEST, context=PUBLICATION_CONTEXT)

    with engine.begin() as connection:
        source = connection.execute(
            text(
                """
                    SELECT fv.feature_version_id, fv.payload_contract_version_id
                    FROM processing.feature_version fv
                    JOIN processing.feature_variant v
                      ON v.feature_variant_id = fv.feature_variant_id
                    WHERE v.variant_key = 'adjusted_close'
                    """
            )
        ).one()
        artifacts = [uuid.uuid4() for _ in range(3)]
        for ordinal, artifact_id in enumerate(artifacts):
            connection.execute(
                text(
                    """
                        INSERT INTO lineage.artifact (
                            artifact_id, artifact_type, artifact_key, version_number, status
                        ) VALUES (:id, 'm1_trigger_test', :key, 1, 'draft')
                        """
                ),
                {"id": artifact_id, "key": f"m1_trigger_test_{ordinal}"},
            )
        definition_id = uuid.uuid4()
        variant_id = uuid.uuid4()
        node_version_id = uuid.uuid4()
        input_port_id = uuid.uuid4()
        connection.execute(
            text(
                """
                    INSERT INTO processing.node_definition (
                        node_definition_id, artifact_id, node_key, name,
                        algorithm_identity, description
                    ) VALUES (:id, :artifact, 'm1_test_node', 'M1 test', 'test', 'test')
                    """
            ),
            {"id": definition_id, "artifact": artifacts[0]},
        )
        connection.execute(
            text(
                """
                    INSERT INTO processing.node_variant (
                        node_variant_id, node_definition_id, artifact_id,
                        variant_key, parameters
                    ) VALUES (:id, :definition, :artifact, 'm1_test_node_v1', '{}'::jsonb)
                    """
            ),
            {"id": variant_id, "definition": definition_id, "artifact": artifacts[1]},
        )
        connection.execute(
            text(
                """
                    INSERT INTO processing.node_version (
                        node_version_id, node_variant_id, artifact_id, version_number,
                        stage_no, implementation_key, implementation_version,
                        determinism_policy, cache_policy, execution_contract,
                        version_fingerprint
                    ) VALUES (
                        :id, :variant, :artifact, 1, 2, 'tests.m1', '1',
                        'deterministic', 'content_addressed', '{}'::jsonb, :fingerprint
                    )
                    """
            ),
            {
                "id": node_version_id,
                "variant": variant_id,
                "artifact": artifacts[2],
                "fingerprint": "0" * 64,
            },
        )
        connection.execute(
            text(
                """
                    INSERT INTO processing.node_port (
                        node_port_id, node_version_id, payload_contract_version_id,
                        port_key, direction, ordinal, binding_cardinality, port_semantics
                    ) VALUES (
                        :id, :node, :contract, 'price_input', 'input', 0,
                        'required', '{}'::jsonb
                    )
                    """
            ),
            {
                "id": input_port_id,
                "node": node_version_id,
                "contract": source.payload_contract_version_id,
            },
        )
        connection.execute(
            text(
                """
                    INSERT INTO processing.node_input_binding (
                        node_input_binding_id, node_version_id, input_port_id,
                        source_feature_version_id, binding_role, ordinal
                    ) VALUES (:id, :node, :port, :source, 'price', 0)
                    """
            ),
            {
                "id": uuid.uuid4(),
                "node": node_version_id,
                "port": input_port_id,
                "source": source.feature_version_id,
            },
        )
    with engine.connect() as connection:
        binding_count = connection.scalar(
            text(
                "SELECT count(*) FROM processing.node_input_binding b "
                "JOIN processing.node_version n ON n.node_version_id=b.node_version_id "
                "WHERE n.node_version_id=:node"
            ),
            {"node": node_version_id},
        )
    engine.dispose()
    assert binding_count == 1


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_v021_database_upgrades_additively_to_m1() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    downgrade_database(DATABASE_URL, "20260809_47_signal_export_job")
    engine = create_postgres_engine(DATABASE_URL)
    marker = ArtifactService(engine).publish(
        artifact_type="m1_additive_upgrade_marker",
        artifact_key="v021_history",
        version_number=1,
        semantic_payload={"legacy": True},
        content_payload={"legacy": True},
    )
    engine.dispose()

    upgrade_database(DATABASE_URL)
    engine = create_postgres_engine(DATABASE_URL)
    with engine.connect() as connection:
        preserved = connection.scalar(
            text("SELECT count(*) FROM lineage.artifact WHERE artifact_id = :id"),
            {"id": marker.artifact_id},
        )
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    engine.dispose()
    assert preserved == 1
    assert revision == "20260821_142_asset_export"
