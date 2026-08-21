from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text

from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.compiler_service import GraphCompilerService
from style_rotation.v022.graph import AggregationSelection, DraftIntent
from style_rotation.v022.publication import (
    CatalogPublicationContext,
    publish_catalog_release,
    verify_published_catalog,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]
MANIFEST = (
    PROJECT_ROOT
    / "v0.22"
    / "catalogs"
    / "releases"
    / "catalog_release.v0.22.1.json"
)
CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_representative_catalog_publishes_multi_output_identity_and_compiles() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)

    assert release.component_count == 117
    evidence = verify_published_catalog(engine, release.release_artifact_id)
    assert evidence["status"] == "passed"
    assert all(evidence["checks"].values())

    with engine.connect() as connection:
        catalog_counts = connection.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM processing.node_definition),
                  (SELECT count(*) FROM processing.node_variant),
                  (SELECT count(*) FROM processing.node_version),
                  (SELECT count(*) FROM processing.feature_version),
                  (SELECT count(*) FROM processing.feature_producer)
                """
            )
        ).one()
        primitive_outputs = tuple(
            connection.execute(
                text(
                    """
                    SELECT ff.family_key,fvar.variant_key,p.port_key
                    FROM processing.feature_producer fp
                    JOIN processing.node_version nv ON nv.node_version_id=fp.node_version_id
                    JOIN processing.node_variant nvvar ON nvvar.node_variant_id=nv.node_variant_id
                    JOIN processing.feature_version fv
                      ON fv.feature_version_id=fp.feature_version_id
                    JOIN processing.feature_variant fvar
                      ON fvar.feature_variant_id=fv.feature_variant_id
                    JOIN processing.feature_family ff ON ff.feature_family_id=fvar.feature_family_id
                    JOIN processing.node_port p ON p.node_port_id=fp.output_port_id
                    WHERE nvvar.variant_key='amihud_daily_primitives__canonical'
                    ORDER BY p.ordinal
                    """
                )
            ).tuples()
        )
    assert catalog_counts == (7, 7, 7, 18, 9)
    assert primitive_outputs == (
        ("simple_return", "simple_return__amihud_daily", "simple_return"),
        ("dollar_volume", "dollar_volume__close_times_volume", "dollar_volume"),
        ("daily_price_impact", "daily_price_impact__amihud", "daily_price_impact"),
    )

    intent = DraftIntent(
        catalog_release_fingerprint=release.release_fingerprint,
        asset_context_fingerprint="a" * 64,
        resolved_data_binding_fingerprint="b" * 64,
        frequency="weekly",
        aggregation_inputs=(
            "return_continuation__w120",
            "price_cross_above_ma__s1_l200",
            "low_illiquidity_quality__w20",
        ),
        aggregations=(
            AggregationSelection(
                family_key="flat_equal_weight_mean",
                parameter_preset_keys=("signal_equal_v1",),
            ),
        ),
        strategy_keys=("cross_section_rank_top_k_parity",),
        defense_keys=("none",),
    )
    service = GraphCompilerService(engine, compiler_version="v022-compiler-m3-v1")
    draft = service.create_draft(
        catalog_release_id=release.catalog_release_id,
        draft_key="m3_representative_vertical_slice",
        intent=intent,
        actor_key="m3_test",
    )
    outcome = service.compile(draft.draft_intent_id)

    with engine.connect() as connection:
        graph = connection.execute(
            text(
                """
                SELECT node_count,occurrence_count,projection_count,
                       aggregation_instance_count,strategy_branch_count
                FROM workspace.compiled_research_graph
                WHERE compiled_research_graph_id=:graph
                """
            ),
            {"graph": outcome.compiled_research_graph_id},
        ).one()
        multi_output_occurrences = connection.scalar(
            text(
                """
                SELECT count(*)
                FROM workspace.compiled_feature_occurrence o
                JOIN workspace.compiled_graph_node n
                  ON n.compiled_graph_node_id=o.compiled_graph_node_id
                JOIN processing.node_version nv ON nv.node_version_id=n.node_version_id
                JOIN processing.node_variant v ON v.node_variant_id=nv.node_variant_id
                WHERE o.compiled_research_graph_id=:graph
                  AND v.variant_key='amihud_daily_primitives__canonical'
                """
            ),
            {"graph": outcome.compiled_research_graph_id},
        )
    engine.dispose()

    assert graph == (7, 14, 2, 1, 1)
    assert multi_output_occurrences == 3
