from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import bindparam, text

from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.compiler_service import GraphCompilerService
from style_rotation.v022.graph import AggregationSelection, DraftIntent
from style_rotation.v022.migration import load_migration_registry
from style_rotation.v022.publication import CatalogPublicationContext, publish_catalog_release

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]
MANIFEST = PROJECT_ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.2.json"
REGISTRY = PROJECT_ROOT / "v0.22/m4/migration-registry.v0.22.3.json"
CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_m4_catalog_publishes_and_compiles_every_legacy_signal_to_stage_three() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
    registry = load_migration_registry(REGISTRY)
    signal_keys = tuple(
        item.mapping.variant_key
        for item in registry.records
        if item.component_kind == "signal_version"
    )
    assert release.component_count == 475

    intent = DraftIntent(
        catalog_release_fingerprint=release.release_fingerprint,
        asset_context_fingerprint="a" * 64,
        resolved_data_binding_fingerprint="b" * 64,
        frequency="weekly",
        aggregation_inputs=signal_keys,
        aggregations=(
            AggregationSelection(
                family_key="flat_equal_weight_mean",
                parameter_preset_keys=("signal_equal_v1",),
            ),
        ),
        strategy_keys=("cross_section_rank_top_k_parity",),
        defense_keys=("none",),
    )
    compiler = GraphCompilerService(engine, compiler_version="v022-compiler-m4-v1")
    draft = compiler.create_draft(
        catalog_release_id=release.catalog_release_id,
        draft_key="m4_all_legacy_signals",
        intent=intent,
        actor_key="m4_test",
    )
    outcome = compiler.compile(draft.draft_intent_id)

    with engine.connect() as connection:
        stage3_signals = set(
            connection.execute(
                text(
                    """
                    SELECT variant.variant_key
                    FROM workspace.compiled_feature_occurrence occurrence
                    JOIN processing.feature_version version
                      ON version.feature_version_id=occurrence.feature_version_id
                    JOIN processing.feature_variant variant
                      ON variant.feature_variant_id=version.feature_variant_id
                    WHERE occurrence.compiled_research_graph_id=:graph
                      AND occurrence.stage_no=3
                      AND variant.variant_key IN :signal_keys
                    """
                ).bindparams(bindparam("signal_keys", expanding=True)),
                {
                    "graph": outcome.compiled_research_graph_id,
                    "signal_keys": signal_keys,
                },
            ).scalars()
        )
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
    engine.dispose()

    assert stage3_signals == set(signal_keys)
    assert graph.aggregation_instance_count == 1
    assert graph.strategy_branch_count == 1
    assert graph.node_count == 80
    assert graph.projection_count > 0
    assert graph.occurrence_count > 82
