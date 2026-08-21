from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from itertools import product
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

import style_rotation.v022.workspace_context as workspace_context
from style_rotation.catalog.asset_registry import (
    publish_asset_identities,
    publish_asset_registry,
)
from style_rotation.catalog.scope import publish_research_scope
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.calendar import CalendarPublicationService, XNYSCalendarGenerator
from style_rotation.data.publication import CanonicalDataPublicationService
from style_rotation.data.service import SnapshotInput, SourceSnapshotService, publish_data_contracts
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.compiler_service import CompileOutcome, GraphCompilerService
from style_rotation.v022.execution_context import ResolvedDataBindingSnapshot
from style_rotation.v022.graph import (
    AggregationSelection,
    AssetContextSnapshot,
    DraftIntent,
)
from style_rotation.v022.publication import CatalogPublicationContext, publish_catalog_release
from style_rotation.v022.suite_identity import GraphSuiteIdentityService
from style_rotation.v022.workspace_context import (
    CANONICAL_MARKET_INPUT,
    ActiveV022WorkspaceIdentity,
    GraphWorkspaceContextResolver,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]
MANIFEST = (
    PROJECT_ROOT
    / "v0.22"
    / "catalogs"
    / "releases"
    / "catalog_release.v0.22.4.json"
)
CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)
DIRECT_INPUTS = (
    "return_continuation__w120",
    "price_cross_above_ma__s1_l200",
    "low_illiquidity_quality__w20",
)
FIXTURE_ASSET_CONTEXT_KEY = "us_style_rotation_4_etf_sample_v1"


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_compiled_graph_suite_identity_is_complete_idempotent_and_append_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        asset_context, data_binding = _publish_execution_context(engine, monkeypatch)
        release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
        assert release.component_count == 487

        first_graph = _compile_graph(
            engine,
            catalog_release_id=release.catalog_release_id,
            catalog_release_fingerprint=release.release_fingerprint,
            draft_key="suite_identity_representative_graph",
            asset_context=asset_context,
            resolved_data_binding=data_binding,
            strategy_parameter_preset_key="k2",
        )
        different_graph = _compile_graph(
            engine,
            catalog_release_id=release.catalog_release_id,
            catalog_release_fingerprint=release.release_fingerprint,
            draft_key="suite_identity_different_semantics",
            asset_context=asset_context,
            resolved_data_binding=data_binding,
            strategy_parameter_preset_key="k1",
        )
        assert first_graph.compiled_research_graph_id != different_graph.compiled_research_graph_id
        assert first_graph.graph_fingerprint != different_graph.graph_fingerprint

        with engine.connect() as connection:
            graph_branch_count = connection.scalar(
                text(
                    "SELECT strategy_branch_count FROM workspace.compiled_research_graph "
                    "WHERE compiled_research_graph_id=:graph"
                ),
                {"graph": first_graph.compiled_research_graph_id},
            )
            counts_before_suite = _identity_counts(connection)
        assert graph_branch_count == 3
        assert counts_before_suite == (0, 0, 0, 0, 0)

        submission_key = uuid.uuid4()
        service = GraphSuiteIdentityService(engine)
        published = service.publish(
            compiled_research_graph_id=first_graph.compiled_research_graph_id,
            submission_key=submission_key,
            actor_key="suite_identity_researcher",
        )
        replayed = service.publish(
            compiled_research_graph_id=first_graph.compiled_research_graph_id,
            submission_key=submission_key,
            actor_key="suite_identity_researcher",
        )

        assert published.reused is False
        assert replayed.reused is True
        assert replayed.research_suite_id == published.research_suite_id
        assert replayed.suite_artifact_id == published.suite_artifact_id
        assert replayed.suite_fingerprint == published.suite_fingerprint
        assert replayed.compiled_research_graph_id == first_graph.compiled_research_graph_id
        assert published.strategy_branch_count == graph_branch_count

        with engine.connect() as connection:
            compiled_branches = tuple(
                connection.execute(
                    text(
                        "SELECT compiled_strategy_branch_id,branch_key "
                        "FROM strategy.v022_compiled_strategy_branch "
                        "WHERE compiled_research_graph_id=:graph ORDER BY branch_key"
                    ),
                    {"graph": first_graph.compiled_research_graph_id},
                ).mappings()
            )
            suite_branches = tuple(
                connection.execute(
                    text(
                        "SELECT research_suite_branch_id,compiled_strategy_branch_id,"
                        "configuration_snapshot_id,ordinal,branch_key "
                        "FROM experiment.v022_research_suite_branch "
                        "WHERE research_suite_id=:suite ORDER BY ordinal"
                    ),
                    {"suite": published.research_suite_id},
                ).mappings()
            )
            policy_contexts = tuple(
                connection.execute(
                    text(
                        "SELECT ordinal,context_fingerprint "
                        "FROM experiment.v022_evaluation_matrix_policy_context "
                        "WHERE evaluation_matrix_policy_id=:policy ORDER BY ordinal"
                    ),
                    {"policy": published.evaluation_matrix_policy_id},
                ).mappings()
            )
            cells = tuple(
                connection.execute(
                    text(
                        "SELECT research_suite_branch_id,compiled_strategy_branch_id,"
                        "configuration_snapshot_id,evaluation_context_ordinal,"
                        "evaluation_context_fingerprint,ordinal "
                        "FROM experiment.v022_research_cell "
                        "WHERE research_suite_id=:suite ORDER BY ordinal"
                    ),
                    {"suite": published.research_suite_id},
                ).mappings()
            )
            snapshots = tuple(
                connection.execute(
                    text(
                        "SELECT suite_branch.ordinal,snapshot.configuration_snapshot_id,"
                        "snapshot.semantic_identity_document,artifact.status "
                        "FROM experiment.v022_research_suite_branch suite_branch "
                        "JOIN experiment.v022_research_configuration_snapshot snapshot "
                        "ON snapshot.configuration_snapshot_id="
                        "suite_branch.configuration_snapshot_id "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id="
                        "snapshot.artifact_id "
                        "WHERE suite_branch.research_suite_id=:suite ORDER BY suite_branch.ordinal"
                    ),
                    {"suite": published.research_suite_id},
                ).mappings()
            )
            snapshot_inputs = tuple(
                connection.execute(
                    text(
                        "SELECT suite_branch.ordinal AS branch_ordinal,"
                        "direct_input.ordinal AS input_ordinal,"
                        "direct_input.compiled_feature_occurrence_id,variant.variant_key "
                        "FROM experiment.v022_research_suite_branch suite_branch "
                        "JOIN experiment.v022_configuration_direct_input direct_input "
                        "ON direct_input.configuration_snapshot_id="
                        "suite_branch.configuration_snapshot_id "
                        "JOIN workspace.compiled_feature_occurrence occurrence "
                        "ON occurrence.compiled_feature_occurrence_id="
                        "direct_input.compiled_feature_occurrence_id "
                        "JOIN processing.feature_version version "
                        "ON version.feature_version_id=occurrence.feature_version_id "
                        "JOIN processing.feature_variant variant "
                        "ON variant.feature_variant_id=version.feature_variant_id "
                        "WHERE suite_branch.research_suite_id=:suite "
                        "ORDER BY suite_branch.ordinal,direct_input.ordinal"
                    ),
                    {"suite": published.research_suite_id},
                ).mappings()
            )
            compiled_inputs = tuple(
                connection.execute(
                    text(
                        "SELECT suite_branch.ordinal AS branch_ordinal,"
                        "aggregation_input.ordinal AS input_ordinal,"
                        "aggregation_input.compiled_feature_occurrence_id,variant.variant_key "
                        "FROM experiment.v022_research_suite_branch suite_branch "
                        "JOIN strategy.v022_compiled_strategy_branch compiled_branch "
                        "ON compiled_branch.compiled_strategy_branch_id="
                        "suite_branch.compiled_strategy_branch_id "
                        "JOIN workspace.compiled_aggregation_input aggregation_input "
                        "ON aggregation_input.compiled_aggregation_instance_id="
                        "compiled_branch.compiled_aggregation_instance_id "
                        "JOIN workspace.compiled_feature_occurrence occurrence "
                        "ON occurrence.compiled_feature_occurrence_id="
                        "aggregation_input.compiled_feature_occurrence_id "
                        "JOIN processing.feature_version version "
                        "ON version.feature_version_id=occurrence.feature_version_id "
                        "JOIN processing.feature_variant variant "
                        "ON variant.feature_variant_id=version.feature_variant_id "
                        "WHERE suite_branch.research_suite_id=:suite "
                        "ORDER BY suite_branch.ordinal,aggregation_input.ordinal"
                    ),
                    {"suite": published.research_suite_id},
                ).mappings()
            )
            counts_after_replay = _identity_counts(connection)

        assert tuple(row["compiled_strategy_branch_id"] for row in suite_branches) == tuple(
            row["compiled_strategy_branch_id"] for row in compiled_branches
        )
        assert tuple(row["branch_key"] for row in suite_branches) == tuple(
            row["branch_key"] for row in compiled_branches
        )
        assert tuple(row["ordinal"] for row in suite_branches) == tuple(
            range(len(compiled_branches))
        )
        assert len(policy_contexts) > 0

        expected_cells = tuple(
            (
                suite_branch["research_suite_branch_id"],
                suite_branch["compiled_strategy_branch_id"],
                suite_branch["configuration_snapshot_id"],
                context["ordinal"],
                context["context_fingerprint"],
                ordinal,
            )
            for ordinal, (suite_branch, context) in enumerate(
                product(suite_branches, policy_contexts)
            )
        )
        actual_cells = tuple(
            (
                cell["research_suite_branch_id"],
                cell["compiled_strategy_branch_id"],
                cell["configuration_snapshot_id"],
                cell["evaluation_context_ordinal"],
                cell["evaluation_context_fingerprint"],
                cell["ordinal"],
            )
            for cell in cells
        )
        assert actual_cells == expected_cells
        assert published.backtest_cell_count == len(compiled_branches) * len(policy_contexts)

        assert len(snapshots) == len(compiled_branches)
        assert all(row["status"] == "published" for row in snapshots)
        for row in snapshots:
            strategy_preset = row["semantic_identity_document"]["strategy"][
                "parameter_preset"
            ]
            assert strategy_preset["preset_key"] == "k2"
            assert strategy_preset["resolved_parameters"]["target_k"] == 2
            semantic_inputs = row["semantic_identity_document"]["direct_inputs"]
            assert tuple(item["ordinal"] for item in semantic_inputs) == tuple(
                range(len(DIRECT_INPUTS))
            )
            assert tuple(item["variant_key"] for item in semantic_inputs) == DIRECT_INPUTS
        assert tuple(tuple(row.values()) for row in snapshot_inputs) == tuple(
            tuple(row.values()) for row in compiled_inputs
        )
        assert counts_after_replay == (
            1,
            len(compiled_branches),
            len(cells),
            len(compiled_branches),
            1,
        )

        with pytest.raises(
            ValueError, match="Artifact identity already exists with different semantics"
        ):
            service.publish(
                compiled_research_graph_id=different_graph.compiled_research_graph_id,
                submission_key=submission_key,
                actor_key="suite_identity_researcher",
            )
        with engine.connect() as connection:
            assert _identity_counts(connection) == counts_after_replay

        immutable_rows = (
            (
                "v022_research_suite",
                "research_suite_id",
                published.research_suite_id,
            ),
            (
                "v022_research_suite_branch",
                "research_suite_branch_id",
                suite_branches[0]["research_suite_branch_id"],
            ),
            (
                "v022_research_cell",
                "research_cell_id",
                _first_cell_id(engine, published.research_suite_id),
            ),
        )
        for table_name, identity_column, identity in immutable_rows:
            with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
                connection.execute(
                    text(
                        f"UPDATE experiment.{table_name} SET created_at=created_at "  # noqa: S608
                        f"WHERE {identity_column}=:identity"  # noqa: S608
                    ),
                    {"identity": identity},
                )
    finally:
        engine.dispose()


def _compile_graph(
    engine: Engine,
    *,
    catalog_release_id: uuid.UUID,
    catalog_release_fingerprint: str,
    draft_key: str,
    asset_context: AssetContextSnapshot,
    resolved_data_binding: ResolvedDataBindingSnapshot,
    strategy_parameter_preset_key: str,
) -> CompileOutcome:
    intent = DraftIntent(
        catalog_release_fingerprint=catalog_release_fingerprint,
        asset_context_fingerprint=sha256_hexdigest(
            asset_context.model_dump(mode="json")
        ),
        resolved_data_binding_fingerprint=sha256_hexdigest(
            resolved_data_binding.model_dump(mode="json")
        ),
        frequency="weekly",
        aggregation_inputs=DIRECT_INPUTS,
        aggregations=(
            AggregationSelection(
                family_key="flat_equal_weight_mean",
                parameter_preset_keys=("signal_equal_v1",),
            ),
            AggregationSelection(
                family_key="directional_weighted_vote",
                parameter_preset_keys=(
                    "legacy_equal_vote_v1",
                    "legacy_weighted_vote_v1",
                ),
            ),
        ),
        strategy_keys=("cross_section_rank_top_k_parity",),
        strategy_parameter_preset_keys=(
            (
                "cross_section_rank_top_k_parity",
                (strategy_parameter_preset_key,),
            ),
        ),
        defense_keys=("none",),
    )
    compiler = GraphCompilerService(engine, compiler_version="v022-suite-identity-test-v1")
    draft = compiler.create_draft(
        catalog_release_id=catalog_release_id,
        draft_key=draft_key,
        intent=intent,
        actor_key="suite_identity_researcher",
    )
    return compiler.compile(
        draft.draft_intent_id,
        asset_context_snapshot=asset_context,
        resolved_data_binding_snapshot=resolved_data_binding,
    )


def _publish_execution_context(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AssetContextSnapshot, ResolvedDataBindingSnapshot]:
    publish_research_scope(engine, PROJECT_ROOT / "v0.2/catalogs/research_scope.v0.2.0.json")
    asset_catalog = PROJECT_ROOT / "v0.21/catalogs/assets.v0.21.1.json"
    publish_asset_registry(engine, asset_catalog)
    publish_asset_identities(engine, asset_catalog)
    publish_data_contracts(engine, PROJECT_ROOT / "v0.2/catalogs/data_contracts.v0.2.0.json")
    calendar = CalendarPublicationService(engine).publish(
        XNYSCalendarGenerator().generate(date(2026, 7, 29), date(2026, 7, 30))
    )
    snapshots = SourceSnapshotService(engine)
    fetched = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
    header = "session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
    snapshot_ids = []
    for ordinal, symbol in enumerate(("IWF", "IWD", "IWO", "IWN", "SPY")):
        snapshot = snapshots.publish(
            SnapshotInput(
                series_key="us_etf_daily_market",
                series_version=1,
                snapshot_key=f"v022-suite-context-{symbol.lower()}",
                requested_at=fetched - timedelta(seconds=1),
                fetched_at=fetched + timedelta(microseconds=ordinal),
                as_of_at=fetched + timedelta(microseconds=ordinal),
                media_type="text/csv",
                request_parameters={"tickers": symbol},
                response_metadata={"fixture": True},
                raw_payload=(
                    header
                    + "2026-07-29,99,101,98,100,100,900,0,0\n"
                    + "2026-07-30,100,102,99,101,101,1000,0,0\n"
                ).encode(),
            )
        )
        snapshot_ids.append(snapshot.artifact_id)
    publications = CanonicalDataPublicationService(engine)
    risk = publications.publish_market(
        tuple(snapshot_ids), calendar.artifact_id, version_number=1
    )
    with engine.connect() as connection:
        risk_dataset = connection.execute(
            text(
                "SELECT dataset_publication_id,dataset_key,version_number "
                "FROM data.dataset_publication WHERE artifact_id=:artifact"
            ),
            {"artifact": risk.artifact_id},
        ).mappings().one()
        registry = connection.execute(
            text(
                "SELECT asset_registry_release_id,artifact_id,version_number,"
                "catalog_version,as_of_date FROM catalog.asset_registry_release"
            )
        ).mappings().one()
        active_identity = ActiveV022WorkspaceIdentity(
            asset_registry_release_id=registry["asset_registry_release_id"],
            asset_registry_artifact_id=registry["artifact_id"],
            asset_registry_version_number=registry["version_number"],
            asset_registry_catalog_version=registry["catalog_version"],
            asset_registry_as_of_date=registry["as_of_date"],
            universe_history_id=uuid.uuid4(),
            risk_dataset_publication_id=risk_dataset["dataset_publication_id"],
            risk_dataset_artifact_id=risk.artifact_id,
            risk_dataset_key=risk_dataset["dataset_key"],
            risk_dataset_version_number=risk_dataset["version_number"],
            benchmark_dataset_publication_id=risk_dataset["dataset_publication_id"],
            benchmark_dataset_artifact_id=risk.artifact_id,
            benchmark_dataset_key=risk_dataset["dataset_key"],
            benchmark_dataset_version_number=risk_dataset["version_number"],
            dataset_gate_assessment_id=uuid.uuid4(),
            dataset_gate_artifact_id=uuid.uuid4(),
        )
        monkeypatch.setattr(
            workspace_context,
            "require_active_v022_workspace_identity",
            lambda _connection: active_identity,
        )
        resolved = GraphWorkspaceContextResolver().resolve(
            connection,
            # New Research Rounds are intentionally blank.  This low-level Suite
            # identity test therefore opts into its fixture context explicitly.
            asset_context_key=FIXTURE_ASSET_CONTEXT_KEY,
            data_input_keys=(CANONICAL_MARKET_INPUT,),
        )
    return (
        AssetContextSnapshot.model_validate(resolved.asset_context_document),
        ResolvedDataBindingSnapshot.model_validate(
            resolved.resolved_data_binding_document
        ),
    )


def _identity_counts(connection: object) -> tuple[int, int, int, int, int]:
    return tuple(
        connection.execute(  # type: ignore[attr-defined,no-any-return]
            text(
                "SELECT "
                "(SELECT count(*) FROM experiment.v022_research_suite),"
                "(SELECT count(*) FROM experiment.v022_research_suite_branch),"
                "(SELECT count(*) FROM experiment.v022_research_cell),"
                "(SELECT count(*) FROM experiment.v022_research_configuration_snapshot),"
                "(SELECT count(*) FROM lineage.artifact "
                " WHERE artifact_type='v022_research_suite')"
            )
        ).one()
    )


def _first_cell_id(engine: Engine, suite_id: uuid.UUID) -> uuid.UUID:
    with engine.connect() as connection:
        return connection.scalar(
            text(
                "SELECT research_cell_id FROM experiment.v022_research_cell "
                "WHERE research_suite_id=:suite ORDER BY ordinal LIMIT 1"
            ),
            {"suite": suite_id},
        )
