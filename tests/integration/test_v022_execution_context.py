from __future__ import annotations

import copy
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

import style_rotation.v022.draft_service as draft_service_module
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
from style_rotation.v022 import compiler_service as compiler_service_module
from style_rotation.v022.dag import GraphDagService, WorkPlan
from style_rotation.v022.draft_service import (
    GraphDraftCompileResult,
    GraphDraftService,
    GraphDraftSnapshot,
)
from style_rotation.v022.execution_context import ExecutionDataContextService
from style_rotation.v022.publication import CatalogPublicationContext, publish_catalog_release
from style_rotation.v022.suite_identity import GraphSuiteIdentityService
from style_rotation.v022.workspace_context import ActiveV022WorkspaceIdentity
from style_rotation.v022.workspace_view import GraphWorkspacePreviewService

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


class _DroppingCalendarDependencyArtifactService:
    """Exercise the deferred Context lineage guard through the production service."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def publish(self, **kwargs: Any) -> Any:
        kwargs["dependencies"] = tuple(
            dependency
            for dependency in kwargs["dependencies"]
            if dependency.role != "calendar"
        )
        return self._delegate.publish(**kwargs)


class _MissingCalendarLineageContextService(ExecutionDataContextService):
    def __init__(self, engine: Engine) -> None:
        super().__init__(engine)
        self._artifacts = _DroppingCalendarDependencyArtifactService(  # type: ignore[assignment]
            self._artifacts
        )


@pytest.fixture(autouse=True)
def _use_fixture_workspace_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind this focused legacy ETF fixture to the active-context seam."""

    def resolve(connection) -> ActiveV022WorkspaceIdentity:
        registry = connection.execute(
            text(
                "SELECT asset_registry_release_id,artifact_id,version_number,"
                "catalog_version,as_of_date FROM catalog.asset_registry_release "
                "ORDER BY version_number DESC LIMIT 1"
            )
        ).mappings().one()
        dataset = connection.execute(
            text(
                "SELECT dataset_publication_id,artifact_id,dataset_key,version_number "
                "FROM data.dataset_publication WHERE value_kind='daily_bar' "
                "ORDER BY version_number DESC LIMIT 1"
            )
        ).mappings().one()
        return ActiveV022WorkspaceIdentity(
            asset_registry_release_id=registry["asset_registry_release_id"],
            asset_registry_artifact_id=registry["artifact_id"],
            asset_registry_version_number=registry["version_number"],
            asset_registry_catalog_version=registry["catalog_version"],
            asset_registry_as_of_date=registry["as_of_date"],
            universe_history_id=uuid.UUID(int=1),
            risk_dataset_publication_id=dataset["dataset_publication_id"],
            risk_dataset_artifact_id=dataset["artifact_id"],
            risk_dataset_key=dataset["dataset_key"],
            risk_dataset_version_number=dataset["version_number"],
            benchmark_dataset_publication_id=dataset["dataset_publication_id"],
            benchmark_dataset_artifact_id=dataset["artifact_id"],
            benchmark_dataset_key=dataset["dataset_key"],
            benchmark_dataset_version_number=dataset["version_number"],
            dataset_gate_assessment_id=uuid.UUID(int=2),
            dataset_gate_artifact_id=uuid.UUID(int=3),
        )

    monkeypatch.setattr(
        workspace_context,
        "require_active_v022_workspace_identity",
        resolve,
    )
    monkeypatch.setattr(
        draft_service_module,
        "require_active_v022_workspace_identity",
        resolve,
    )


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_execution_context_is_exact_replayable_append_only_and_admits_suite_run() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        _publish_workspace_context(engine)
        release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
        assert release.component_count == 487
        drafts = GraphDraftService(
            engine,
            GraphWorkspacePreviewService.from_manifest(MANIFEST),
        )
        compiled, draft = _compile_graph(
            drafts,
            draft_key="execution_context_weekly",
            frequency="weekly",
        )
        graph_id = compiled.compiled_research_graph_id
        assert compiled.compiled_execution_data_context_id is not None
        assert compiled.execution_data_context_artifact_id is not None
        assert compiled.execution_data_context_fingerprint is not None
        assert compiled.execution_data_context_reused is False

        recompiled = drafts.compile(
            draft.graph_draft_id,
            expected_revision=draft.revision,
            actor_key="execution_context_researcher",
            idempotency_key=uuid.uuid4(),
        )
        assert recompiled.compiled_research_graph_id == graph_id
        assert (
            recompiled.compiled_execution_data_context_id
            == compiled.compiled_execution_data_context_id
        )
        assert (
            recompiled.execution_data_context_artifact_id
            == compiled.execution_data_context_artifact_id
        )
        assert (
            recompiled.execution_data_context_fingerprint
            == compiled.execution_data_context_fingerprint
        )
        assert recompiled.execution_data_context_reused is True

        suite = GraphSuiteIdentityService(engine).publish(
            compiled_research_graph_id=graph_id,
            submission_key=uuid.uuid4(),
            actor_key="execution_context_researcher",
        )
        work_fingerprint = sha256_hexdigest(
            {"graph": graph_id, "kind": "execution_context_admission_fixture"}
        )
        graph_run = GraphDagService(engine).plan_run(
            compiled_research_graph_id=graph_id,
            requested_by="execution_context_researcher",
            requested_range={"start": "2026-07-30", "end": "2026-07-30"},
            environment_fingerprint="e" * 64,
            work=(
                WorkPlan(
                    occurrence_kind="aggregation",
                    occurrence_key="execution_context_admission_fixture",
                    execution_fingerprint=work_fingerprint,
                ),
            ),
        )
        binding_id = uuid.uuid4()
        binding_fingerprint = sha256_hexdigest(
            {
                "research_suite_id": suite.research_suite_id,
                "graph_run_id": graph_run.graph_run_id,
                "binding_ordinal": 0,
            }
        )
        service = ExecutionDataContextService(engine)
        replayed = service.publish(
            graph_id,
            copy.deepcopy(draft.asset_context),
            copy.deepcopy(draft.resolved_data_binding),
        )
        assert replayed.reused is True
        assert replayed.context_id == compiled.compiled_execution_data_context_id
        assert replayed.artifact_id == compiled.execution_data_context_artifact_id
        assert replayed.context_fingerprint == compiled.execution_data_context_fingerprint

        with engine.connect() as connection:
            parent = (
                connection.execute(
                    text(
                        """
                        SELECT context.*,artifact.artifact_type,artifact.artifact_key,
                               artifact.version_number,artifact.status,
                               artifact.semantic_fingerprint
                          FROM workspace.v022_compiled_execution_data_context context
                          JOIN lineage.artifact artifact
                            ON artifact.artifact_id=context.artifact_id
                         WHERE context.compiled_execution_data_context_id=:context
                        """
                    ),
                    {"context": replayed.context_id},
                )
                .mappings()
                .one()
            )
            children = tuple(
                connection.execute(
                    text(
                        """
                        SELECT * FROM workspace.v022_compiled_execution_data_input
                         WHERE compiled_execution_data_context_id=:context
                         ORDER BY ordinal
                        """
                    ),
                    {"context": replayed.context_id},
                ).mappings()
            )
            dependencies = tuple(
                connection.execute(
                    text(
                        """
                        SELECT dependency.role,dependency.ordinal,
                               dependency.depends_on_artifact_id,artifact.status
                          FROM lineage.artifact_dependency dependency
                          JOIN lineage.artifact artifact
                            ON artifact.artifact_id=dependency.depends_on_artifact_id
                         WHERE dependency.artifact_id=:artifact
                         ORDER BY dependency.role,dependency.ordinal
                        """
                    ),
                    {"artifact": replayed.artifact_id},
                ).mappings()
            )

        graph_fingerprint = _graph_fingerprint(engine, graph_id)
        assert parent["compiled_research_graph_id"] == graph_id
        assert parent["artifact_type"] == "v022_compiled_execution_data_context"
        assert parent["artifact_key"] == (
            f"compiled_execution_data_context__{graph_fingerprint}"
        )
        assert parent["version_number"] == 1
        assert parent["status"] == "published"
        assert parent["semantic_fingerprint"] == replayed.context_fingerprint
        assert parent["context_fingerprint"] == replayed.context_fingerprint
        assert parent["asset_context_document"] == draft.asset_context
        assert parent["resolved_data_binding_document"] == draft.resolved_data_binding
        assert parent["asset_context_fingerprint"] == sha256_hexdigest(draft.asset_context)
        assert parent["resolved_data_binding_fingerprint"] == sha256_hexdigest(
            draft.resolved_data_binding
        )
        assert parent["input_count"] == replayed.input_count == len(children) == 1

        expected_binding = draft.resolved_data_binding["bindings"][0]
        assert children[0]["ordinal"] == 0
        assert children[0]["input_key"] == expected_binding["input_key"]
        assert children[0]["binding_document"] == expected_binding
        assert children[0]["binding_fingerprint"] == sha256_hexdigest(expected_binding)
        assert children[0]["security_ids"] == expected_binding["security_ids"]
        assert [(row["role"], row["ordinal"]) for row in dependencies] == [
            ("asset_context", 0),
            ("calendar", 0),
            ("compiled_graph", 0),
            ("data_binding", 0),
        ]
        assert all(row["status"] == "published" for row in dependencies)

        with engine.begin() as connection:
            _insert_suite_run_binding(
                connection,
                binding_id=binding_id,
                research_suite_id=suite.research_suite_id,
                graph_id=graph_id,
                graph_run_id=graph_run.graph_run_id,
                binding_fingerprint=binding_fingerprint,
            )

        immutable_rows = (
            (
                "v022_compiled_execution_data_context",
                "compiled_execution_data_context_id",
                replayed.context_id,
            ),
            (
                "v022_compiled_execution_data_input",
                "compiled_execution_data_context_id",
                replayed.context_id,
            ),
        )
        for table_name, identity_column, identity in immutable_rows:
            with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
                connection.execute(
                    text(
                        f"UPDATE workspace.{table_name} SET created_at=created_at "  # noqa: S608
                        f"WHERE {identity_column}=:identity"  # noqa: S608
                    ),
                    {"identity": identity},
                )
    finally:
        engine.dispose()


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_execution_context_rejects_document_hash_drift_and_missing_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        _publish_workspace_context(engine)
        publish_catalog_release(engine, MANIFEST, context=CONTEXT)
        drafts = GraphDraftService(
            engine,
            GraphWorkspacePreviewService.from_manifest(MANIFEST),
        )
        draft = _prepare_graph_draft(
            drafts,
            draft_key="execution_context_negative",
            frequency="monthly",
        )
        with monkeypatch.context() as scoped:
            scoped.setattr(
                compiler_service_module,
                "ExecutionDataContextService",
                _MissingCalendarLineageContextService,
            )
            with pytest.raises(
                DBAPIError,
                match=(
                    "Compiled Execution Data Context Artifact "
                    "(is not exactly published|lineage is not exact)"
                ),
            ):
                drafts.compile(
                    draft.graph_draft_id,
                    expected_revision=draft.revision,
                    actor_key="execution_context_researcher",
                    idempotency_key=uuid.uuid4(),
                )

        with engine.connect() as connection:
            graph_id = connection.scalar(
                text(
                    "SELECT compiled_research_graph_id "
                    "FROM workspace.compiled_research_graph"
                )
            )
            assert graph_id is not None
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM workspace.v022_compiled_execution_data_context "
                    "WHERE compiled_research_graph_id=:graph"
                ),
                {"graph": graph_id},
            ) == 0

        suite = GraphSuiteIdentityService(engine).publish(
            compiled_research_graph_id=graph_id,
            submission_key=uuid.uuid4(),
            actor_key="execution_context_researcher",
        )
        graph_run = GraphDagService(engine).plan_run(
            compiled_research_graph_id=graph_id,
            requested_by="execution_context_researcher",
            requested_range={"start": "2026-07-30", "end": "2026-07-30"},
            environment_fingerprint="e" * 64,
            work=(
                WorkPlan(
                    occurrence_kind="aggregation",
                    occurrence_key="missing_context_admission_fixture",
                    execution_fingerprint=sha256_hexdigest(
                        {"graph": graph_id, "kind": "missing_context_admission_fixture"}
                    ),
                ),
            ),
        )
        binding_fingerprint = sha256_hexdigest(
            {
                "research_suite_id": suite.research_suite_id,
                "graph_run_id": graph_run.graph_run_id,
                "binding_ordinal": 0,
            }
        )
        with pytest.raises(
            DBAPIError,
            match="requires an exact Compiled Execution Data Context",
        ), engine.begin() as connection:
            _insert_suite_run_binding(
                connection,
                binding_id=uuid.uuid4(),
                research_suite_id=suite.research_suite_id,
                graph_id=graph_id,
                graph_run_id=graph_run.graph_run_id,
                binding_fingerprint=binding_fingerprint,
            )

        service = ExecutionDataContextService(engine)

        wrong_asset_context = copy.deepcopy(draft.asset_context)
        wrong_asset_context["members"][0]["security_key"] = "wrong_security"
        with pytest.raises(
            ValueError,
            match="execution_data_context_asset_fingerprint_mismatch",
        ):
            service.publish(
                graph_id,
                wrong_asset_context,
                draft.resolved_data_binding,
            )

        wrong_data_binding = copy.deepcopy(draft.resolved_data_binding)
        wrong_data_binding["bindings"][0]["dataset_version_number"] += 1
        with pytest.raises(
            ValueError,
            match="execution_data_context_binding_fingerprint_mismatch",
        ):
            service.publish(
                graph_id,
                draft.asset_context,
                wrong_data_binding,
            )

        published = service.publish(
            graph_id,
            draft.asset_context,
            draft.resolved_data_binding,
        )
        assert published.reused is False
        with engine.begin() as connection:
            _insert_suite_run_binding(
                connection,
                binding_id=uuid.uuid4(),
                research_suite_id=suite.research_suite_id,
                graph_id=graph_id,
                graph_run_id=graph_run.graph_run_id,
                binding_fingerprint=binding_fingerprint,
            )
    finally:
        engine.dispose()


def _compile_graph(
    service: GraphDraftService,
    *,
    draft_key: str,
    frequency: str,
) -> tuple[GraphDraftCompileResult, GraphDraftSnapshot]:
    selected = _prepare_graph_draft(
        service,
        draft_key=draft_key,
        frequency=frequency,
    )
    compiled = service.compile(
        selected.graph_draft_id,
        expected_revision=selected.revision,
        actor_key="execution_context_researcher",
        idempotency_key=uuid.uuid4(),
    )
    return compiled, selected


def _prepare_graph_draft(
    service: GraphDraftService,
    *,
    draft_key: str,
    frequency: str,
) -> GraphDraftSnapshot:
    draft = service.create(
        researcher_key="execution_context_researcher",
        draft_key=draft_key,
        name="Execution Context integration fixture",
        idempotency_key=uuid.uuid4(),
        frequency=frequency,  # type: ignore[arg-type]
        asset_context_key="us_style_rotation_4_etf_sample_v1",
        data_input_keys=("canonical_market_bars",),
    )
    selected = service.apply_event(
        draft.graph_draft_id,
        expected_revision=draft.revision,
        actor_key="execution_context_researcher",
        idempotency_key=uuid.uuid4(),
        event_type="select_feature_occurrence",
        event={"feature_key": "return_continuation__w120", "stage_no": 3},
    ).snapshot
    events: tuple[tuple[str, dict[str, object]], ...] = (
        ("select_aggregation_family", {"family_key": "flat_equal_weight_mean"}),
        (
            "set_aggregation_parameter_presets",
            {
                "family_key": "flat_equal_weight_mean",
                "preset_keys": ["signal_equal_v1"],
            },
        ),
        ("select_strategy", {"strategy_key": "cross_section_rank_top_k_parity"}),
        (
            "set_strategy_parameter_presets",
            {
                "strategy_key": "cross_section_rank_top_k_parity",
                "preset_keys": ["k1"],
            },
        ),
        ("select_defense", {"defense_key": "none"}),
    )
    for event_type, event in events:
        selected = service.apply_event(
            draft.graph_draft_id,
            expected_revision=selected.revision,
            actor_key="execution_context_researcher",
            idempotency_key=uuid.uuid4(),
            event_type=event_type,
            event=event,
        ).snapshot
    return selected


def _publish_workspace_context(engine: Engine) -> None:
    publish_research_scope(engine, PROJECT_ROOT / "v0.2/catalogs/research_scope.v0.2.0.json")
    asset_catalog = PROJECT_ROOT / "v0.21/catalogs/assets.v0.21.1.json"
    publish_asset_registry(engine, asset_catalog)
    publish_asset_identities(engine, asset_catalog)
    publish_data_contracts(engine, PROJECT_ROOT / "v0.2/catalogs/data_contracts.v0.2.0.json")
    generated = XNYSCalendarGenerator().generate(date(2026, 7, 30), date(2026, 7, 30))
    calendar = CalendarPublicationService(engine).publish(generated)
    snapshots = SourceSnapshotService(engine)
    fetched = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
    header = "session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
    snapshot_ids = []
    for ordinal, symbol in enumerate(("IWF", "IWD", "IWO", "IWN", "SPY")):
        snapshot = snapshots.publish(
            SnapshotInput(
                series_key="us_etf_daily_market",
                series_version=1,
                snapshot_key=f"v022-execution-context-{symbol.lower()}",
                requested_at=fetched - timedelta(seconds=1),
                fetched_at=fetched + timedelta(microseconds=ordinal),
                as_of_at=fetched + timedelta(microseconds=ordinal),
                media_type="text/csv",
                request_parameters={"tickers": symbol},
                response_metadata={"fixture": True},
                raw_payload=(
                    header
                    + "2026-07-30,100,102,99,101,101,1000,0,0\n"
                ).encode(),
            )
        )
        snapshot_ids.append(snapshot.artifact_id)
    CanonicalDataPublicationService(engine).publish_market(
        tuple(snapshot_ids),
        calendar.artifact_id,
        version_number=1,
    )


def _insert_suite_run_binding(
    connection: Any,
    *,
    binding_id: uuid.UUID,
    research_suite_id: uuid.UUID,
    graph_id: uuid.UUID,
    graph_run_id: uuid.UUID,
    binding_fingerprint: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO experiment.v022_research_suite_graph_run_binding (
              research_suite_graph_run_binding_id,research_suite_id,
              compiled_research_graph_id,graph_run_id,binding_ordinal,
              binding_fingerprint,bound_by
            ) VALUES (:id,:suite,:graph,:run,0,:fingerprint,:actor)
            """
        ),
        {
            "id": binding_id,
            "suite": research_suite_id,
            "graph": graph_id,
            "run": graph_run_id,
            "fingerprint": binding_fingerprint,
            "actor": "execution_context_researcher",
        },
    )


def _graph_fingerprint(engine: Engine, graph_id: uuid.UUID) -> str:
    with engine.connect() as connection:
        return connection.scalar(
            text(
                "SELECT graph_fingerprint FROM workspace.compiled_research_graph "
                "WHERE compiled_research_graph_id=:graph"
            ),
            {"graph": graph_id},
        )
