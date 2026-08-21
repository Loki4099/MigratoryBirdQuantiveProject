from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

import style_rotation.v022.draft_service as draft_service_module
import style_rotation.v022.workspace_context as workspace_context
from style_rotation.api.app import create_app
from style_rotation.api.query import ArtifactQueryService
from style_rotation.catalog.asset_registry import (
    publish_asset_identities,
    publish_asset_registry,
)
from style_rotation.catalog.scope import publish_research_scope
from style_rotation.data.calendar import CalendarPublicationService, XNYSCalendarGenerator
from style_rotation.data.publication import CanonicalDataPublicationService
from style_rotation.data.service import SnapshotInput, SourceSnapshotService, publish_data_contracts
from style_rotation.lineage.service import ArtifactService
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.comparison_identity import (
    MatchedBaselineService,
    ResultComparisonService,
)
from style_rotation.v022.draft_service import (
    CascadeConfirmationRequired,
    ChangePreviewExpired,
    GraphCatalogRebaseRequired,
    GraphDraftCompileResult,
    GraphDraftEventResult,
    GraphDraftIdempotencyConflict,
    GraphDraftRevisionConflict,
    GraphDraftService,
)
from style_rotation.v022.experiment_identity import (
    CommonEvaluationPanelService,
    ConfigurationSnapshotService,
    PanelObservation,
    ResultEvidenceService,
)
from style_rotation.v022.product_identity import ProductIdentityService
from style_rotation.v022.product_monitoring import (
    EnrollmentLifecycleService,
    OOSMonitoringService,
)
from style_rotation.v022.product_runtime import (
    DecisionScheduleService,
    DecisionSessionInput,
    ProductDecisionService,
    ProductEnrollmentService,
    RuntimeArtifactSet,
)
from style_rotation.v022.publication import (
    CatalogPublicationContext,
    publish_catalog_release,
)
from style_rotation.v022.release_control import ReleaseControlService
from style_rotation.v022.shadow_comparator import (
    ComparatorField,
    ShadowComparatorVersionService,
    ShadowComparisonCoordinator,
    V021ShadowReferenceService,
)
from style_rotation.v022.shadow_coverage import (
    ShadowComparisonService,
    ShadowCoverageService,
)
from style_rotation.v022.shadow_dual_run import (
    RuntimeCapability,
    ShadowDualRunScheduler,
    ShadowRuntimeBindingService,
    ShadowV022DecisionWorker,
    ShadowWorkerService,
)
from style_rotation.v022.shadow_plan import (
    ShadowContext,
    ShadowPlanService,
    ShadowRepresentative,
)
from style_rotation.v022.workspace_context import ActiveV022WorkspaceIdentity
from style_rotation.v022.workspace_view import GraphWorkspacePreviewService

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
MANIFEST = (
    Path(__file__).parents[2] / "v0.22" / "catalogs" / "releases" / "catalog_release.v0.22.1.json"
)
CURRENT_MANIFEST = (
    Path(__file__).parents[2] / "v0.22" / "catalogs" / "releases" / "catalog_release.v0.22.4.json"
)
CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)


class _Reader:
    def database_revision(self) -> str:
        return "20260821_142_asset_export"


@pytest.fixture(autouse=True)
def _use_fixture_workspace_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapt the legacy ETF fixture to the strict active-environment contract.

    Production now requires a fully published Registry/Gate/weekly/monthly
    environment.  These integration tests intentionally exercise Draft state,
    not baseline publication, so bind their small canonical Dataset explicitly
    instead of recreating the multi-million-row Green baseline.
    """

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


def _publish_workspace_context(engine: Engine) -> None:
    publish_research_scope(engine, Path("v0.2/catalogs/research_scope.v0.2.0.json"))
    publish_asset_registry(engine, Path("v0.21/catalogs/assets.v0.21.1.json"))
    publish_asset_identities(engine, Path("v0.21/catalogs/assets.v0.21.1.json"))
    publish_data_contracts(engine, Path("v0.2/catalogs/data_contracts.v0.2.0.json"))
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
                snapshot_key=f"v022-{symbol.lower()}",
                requested_at=fetched - timedelta(seconds=1),
                fetched_at=fetched + timedelta(microseconds=ordinal),
                as_of_at=fetched + timedelta(microseconds=ordinal),
                media_type="text/csv",
                request_parameters={"tickers": symbol},
                response_metadata={"fixture": True},
                raw_payload=(header + "2026-07-30,100,102,99,101,101,1000,0,0\n").encode(),
            )
        )
        snapshot_ids.append(snapshot.artifact_id)
    CanonicalDataPublicationService(engine).publish_market(
        tuple(snapshot_ids), calendar.artifact_id, version_number=1
    )


def _configure_etf_baseline(
    service: GraphDraftService,
    snapshot,
    *,
    actor_key: str,
):
    """Explicitly configure the former ETF baseline in tests that need it.

    Production Drafts intentionally start blank after M131.  Tests that exercise
    compilation must therefore make the same explicit choices as a researcher.
    """
    strategy = next(
        item
        for item in snapshot.derived_view["strategies"]
        if item["variant_key"] == "cross_section_rank_top_k_parity"
    )
    strategy_presets = [
        str(item["preset_key"])
        for item in strategy.get("parameter_presets", [])
        if item.get("selectable", True)
    ]
    commands: list[tuple[str, dict[str, object]]] = [
        (
            "select_aggregation_family",
            {"family_key": "flat_equal_weight_mean"},
        ),
        (
            "set_aggregation_parameter_presets",
            {
                "family_key": "flat_equal_weight_mean",
                "preset_keys": ["signal_equal_v1"],
            },
        ),
        (
            "select_strategy",
            {"strategy_key": "cross_section_rank_top_k_parity"},
        ),
    ]
    if strategy_presets:
        commands.append(
            (
                "set_strategy_parameter_presets",
                {
                    "strategy_key": "cross_section_rank_top_k_parity",
                    "preset_keys": strategy_presets,
                },
            )
        )
    commands.append(("select_defense", {"defense_key": "none"}))
    configured = snapshot
    for event_type, event in commands:
        configured = service.apply_event(
            configured.graph_draft_id,
            expected_revision=configured.revision,
            actor_key=actor_key,
            idempotency_key=uuid.uuid4(),
            event_type=event_type,
            event=event,
        ).snapshot
    return configured


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_reset_opens_an_idempotent_blank_research_round() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    _publish_workspace_context(engine)
    publish_catalog_release(engine, CURRENT_MANIFEST, context=CONTEXT)
    service = GraphDraftService(
        engine, GraphWorkspacePreviewService.from_manifest(CURRENT_MANIFEST)
    )
    draft = service.create(
        researcher_key="reset_researcher",
        draft_key="reset_round",
        name="Reset round",
        idempotency_key=uuid.uuid4(),
        asset_context_key="us_style_rotation_4_etf_sample_v1",
        data_input_keys=("canonical_market_bars",),
    )
    selected = service.apply_event(
        draft.graph_draft_id,
        expected_revision=draft.revision,
        actor_key="reset_researcher",
        idempotency_key=uuid.uuid4(),
        event_type="select_feature_occurrence",
        event={"feature_key": "return_continuation__w120", "stage_no": 3},
    ).snapshot
    reset_key = uuid.uuid4()
    reset = service.reset_current_research(
        draft.graph_draft_id,
        expected_revision=selected.revision,
        actor_key="reset_researcher",
        idempotency_key=reset_key,
    )
    replay = service.reset_current_research(
        draft.graph_draft_id,
        expected_revision=selected.revision,
        actor_key="reset_researcher",
        idempotency_key=reset_key,
    )

    assert reset.snapshot.revision == selected.revision + 1
    assert reset.snapshot.asset_context["selection_kind"] == "unconfigured"
    assert reset.snapshot.asset_context["members"] == []
    assert reset.snapshot.resolved_data_binding["bindings"] == []
    assert reset.snapshot.intent == {
        "explicit_features": [],
        "aggregation_family_keys": [],
        "aggregation_parameter_preset_keys": {},
        "aggregation_target_keys": {},
        "aggregation_training_preset_keys": {},
        "frequency": "weekly",
        "strategy_keys": [],
        "strategy_parameter_preset_keys": {},
        "defense_keys": [],
    }
    assert reset.cancelled_graph_run_count == 0
    assert replay.snapshot.revision == reset.snapshot.revision
    assert replay.closed_research_round_id == reset.closed_research_round_id
    assert replay.opened_research_round_id == reset.opened_research_round_id

    with engine.connect() as connection:
        rounds = tuple(
            connection.execute(
                text(
                    "SELECT research_round_id,ordinal,status,close_reason "
                    "FROM workspace.v022_research_round "
                    "WHERE root_graph_draft_id=:draft ORDER BY ordinal"
                ),
                {"draft": draft.graph_draft_id},
            ).mappings()
        )
        current_round_id = connection.scalar(
            text(
                "SELECT research_round_id "
                "FROM workspace.v022_graph_draft_revision_round "
                "WHERE graph_draft_id=:draft AND revision=:revision"
            ),
            {"draft": draft.graph_draft_id, "revision": reset.snapshot.revision},
        )
    engine.dispose()
    assert [(row["ordinal"], row["status"]) for row in rounds] == [
        (1, "gc_pending"),
        (2, "active"),
    ]
    assert rounds[0]["close_reason"] == "user_reset"
    assert current_round_id == reset.opened_research_round_id


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_graph_draft_revision_idempotency_and_locked_cascade() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    _publish_workspace_context(engine)
    publish_catalog_release(engine, MANIFEST, context=CONTEXT)
    service = GraphDraftService(engine, GraphWorkspacePreviewService.from_manifest(MANIFEST))

    client = TestClient(create_app(_Reader(), graph_drafts=service))  # type: ignore[arg-type]
    api_create = client.post(
        "/api/v2/workspace/graph-drafts",
        json={
            "researcher_key": "api_researcher",
            "draft_key": "api_vertical_slice",
            "name": "API vertical slice",
            "idempotency_key": str(uuid.uuid4()),
            "frequency": "weekly",
        },
    )
    assert api_create.status_code == 200
    assert api_create.json()["revision"] == 1
    assert api_create.json()["quality"]["state"] == "warning"
    assert all(stage["families"] == [] for stage in api_create.json()["derived_view"]["stages"])

    create_key = uuid.uuid4()
    draft = service.create(
        researcher_key="m3_researcher",
        draft_key="persistent_vertical_slice",
        name="Persistent vertical slice",
        idempotency_key=create_key,
        asset_context_key="us_style_rotation_4_etf_sample_v1",
        data_input_keys=("canonical_market_bars",),
    )
    replayed_create = service.create(
        researcher_key="m3_researcher",
        draft_key="persistent_vertical_slice",
        name="Persistent vertical slice",
        idempotency_key=create_key,
        asset_context_key="us_style_rotation_4_etf_sample_v1",
        data_input_keys=("canonical_market_bars",),
    )
    recovered_by_logical_key = service.create(
        researcher_key="m3_researcher",
        draft_key="persistent_vertical_slice",
        name="Persistent vertical slice",
        idempotency_key=uuid.uuid4(),
        asset_context_key="us_style_rotation_4_etf_sample_v1",
        data_input_keys=("canonical_market_bars",),
    )
    assert replayed_create.graph_draft_id == draft.graph_draft_id
    assert recovered_by_logical_key.graph_draft_id == draft.graph_draft_id
    assert draft.revision == 1
    assert draft.asset_context["asset_context_key"] == "us_style_rotation_4_etf_sample_v1"
    assert [item["security_key"] for item in draft.asset_context["members"]] == [
        "iwf",
        "iwd",
        "iwo",
        "iwn",
    ]
    assert draft.resolved_data_binding["bindings"][0]["input_key"] == ("canonical_market_bars")

    first_page = client.get(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/stages/3/families",
        params={"limit": 1},
    )
    assert first_page.status_code == 200, first_page.text
    first_page_payload = first_page.json()
    assert first_page_payload["revision"] == 1
    assert first_page_payload["pinned_families"] == []
    assert len(first_page_payload["catalog_families"]) == 1
    assert first_page_payload["next_cursor"] is not None
    second_page = client.get(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/stages/3/families",
        params={"limit": 1, "cursor": first_page_payload["next_cursor"]},
    )
    assert second_page.status_code == 200
    assert second_page.json()["view_token"] == first_page_payload["view_token"]
    wrong_query = client.get(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/stages/3/families",
        params={
            "limit": 1,
            "search": "return",
            "cursor": first_page_payload["next_cursor"],
        },
    )
    assert wrong_query.status_code == 422
    assert wrong_query.json()["code"] == "invalid_request"

    event_key = uuid.uuid4()

    def select_once() -> GraphDraftEventResult:
        return service.apply_event(
            draft.graph_draft_id,
            expected_revision=1,
            actor_key="m3_researcher",
            idempotency_key=event_key,
            event_type="select_feature_occurrence",
            event={"feature_key": "return_continuation__w120", "stage_no": 3},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        selected, replayed_event = tuple(executor.map(lambda _index: select_once(), range(2)))
    assert selected.applied is True
    assert selected.snapshot.revision == 2
    assert replayed_event.snapshot.revision == 2
    configured = _configure_etf_baseline(
        service,
        selected.snapshot,
        actor_key="m3_researcher",
    )
    assert configured.revision > selected.snapshot.revision

    stale_page = client.get(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/stages/3/families",
        params={"limit": 1, "cursor": first_page_payload["next_cursor"]},
    )
    assert stale_page.status_code == 409
    assert stale_page.json()["code"] == "workspace_view_token_conflict"
    searched = client.get(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/stages/3/families",
        params={"search": "does-not-exist"},
    )
    assert searched.status_code == 200
    assert searched.json()["catalog_families"] == []
    assert [family["family_key"] for family in searched.json()["pinned_families"]] == [
        "return_continuation"
    ]

    compile_key = uuid.uuid4()
    def compile_once() -> GraphDraftCompileResult:
        return service.compile(
            draft.graph_draft_id,
            expected_revision=configured.revision,
            actor_key="m3_researcher",
            idempotency_key=compile_key,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_compile, concurrent_replay = tuple(
            executor.map(lambda _index: compile_once(), range(2))
        )
    assert first_compile.compile_attempt_id == concurrent_replay.compile_attempt_id
    with engine.connect() as connection:
        attempt_count = connection.scalar(
            text(
                "SELECT count(*) FROM workspace.v022_compile_attempt "
                "WHERE draft_intent_id=:draft"
            ),
            {"draft": first_compile.draft_intent_id},
        )
    assert attempt_count == 1

    compiled = client.post(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/compile",
        json={
            "expected_revision": configured.revision,
            "actor_key": "m3_researcher",
            "idempotency_key": str(compile_key),
        },
    )
    assert compiled.status_code == 200
    compiled_payload = compiled.json()
    assert compiled_payload["graph_draft_revision"] == configured.revision
    assert compiled_payload["compile_attempt_id"] == str(first_compile.compile_attempt_id)
    assert service.get(draft.graph_draft_id).revision == configured.revision

    reused_compile = service.compile(
        draft.graph_draft_id,
        expected_revision=configured.revision,
        actor_key="m3_researcher",
        idempotency_key=uuid.uuid4(),
    )
    assert (
        str(reused_compile.compiled_research_graph_id)
        == compiled_payload["compiled_research_graph_id"]
    )
    assert reused_compile.compile_attempt_id != uuid.UUID(compiled_payload["compile_attempt_id"])
    assert reused_compile.reused is True
    current_compile = service.current_compile(
        draft.graph_draft_id,
        actor_key="m3_researcher",
    )
    assert current_compile is not None
    assert current_compile.compile_attempt_id == reused_compile.compile_attempt_id

    clone_key = uuid.uuid4()
    clone_response = client.post(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/clones",
        json={
            "source_revision": configured.revision,
            "researcher_key": "m3_researcher",
            "draft_key": "persistent_vertical_slice_clone",
            "name": "Persistent vertical slice clone",
            "idempotency_key": str(clone_key),
        },
    )
    replayed_clone = client.post(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/clones",
        json={
            "source_revision": configured.revision,
            "researcher_key": "m3_researcher",
            "draft_key": "persistent_vertical_slice_clone",
            "name": "Persistent vertical slice clone",
            "idempotency_key": str(clone_key),
        },
    )
    assert clone_response.status_code == replayed_clone.status_code == 200
    clone_payload = clone_response.json()
    assert clone_payload["graph_draft_id"] == replayed_clone.json()["graph_draft_id"]
    assert clone_payload["revision"] == 1
    assert clone_payload["intent"] == configured.intent
    assert clone_payload["catalog_release_id"] == str(draft.catalog_release_id)
    assert clone_payload["cloned_from_graph_draft_id"] == str(draft.graph_draft_id)
    assert clone_payload["cloned_from_revision"] == configured.revision

    with pytest.raises(GraphDraftRevisionConflict):
        service.apply_event(
            draft.graph_draft_id,
            expected_revision=1,
            actor_key="m3_researcher",
            idempotency_key=uuid.uuid4(),
            event_type="set_frequency",
            event={"frequency": "monthly"},
        )


    with pytest.raises(GraphDraftIdempotencyConflict):
        service.apply_event(
            draft.graph_draft_id,
            expected_revision=1,
            actor_key="m3_researcher",
            idempotency_key=event_key,
            event_type="set_frequency",
            event={"frequency": "monthly"},
        )

    with pytest.raises(CascadeConfirmationRequired):
        service.apply_event(
            draft.graph_draft_id,
            expected_revision=configured.revision,
            actor_key="m3_researcher",
            idempotency_key=uuid.uuid4(),
            event_type="deselect_feature_occurrence",
            event={"feature_key": "adjusted_close", "stage_no": 0},
        )

    preview = service.preview_cascade_deselect(
        draft.graph_draft_id,
        expected_revision=configured.revision,
        actor_key="m3_researcher",
        feature_key="adjusted_close",
        stage_no=0,
    )
    assert "return_continuation__w120@3" in preview.impact["removed_explicit_occurrences"]
    confirmed = service.confirm_change_preview(
        draft.graph_draft_id,
        preview.impact_token,
        expected_revision=configured.revision,
        actor_key="m3_researcher",
        idempotency_key=uuid.uuid4(),
    )
    assert confirmed.revision == configured.revision + 1
    assert confirmed.intent["explicit_features"] == []
    assert service.current_compile(
        draft.graph_draft_id,
        actor_key="m3_researcher",
    ) is None
    assert service.get(uuid.UUID(clone_payload["graph_draft_id"])).intent == (
        configured.intent
    )

    no_op = service.apply_event(
        draft.graph_draft_id,
        expected_revision=confirmed.revision,
        actor_key="m3_researcher",
        idempotency_key=uuid.uuid4(),
        event_type="set_frequency",
        event={"frequency": "weekly"},
    )
    assert no_op.applied is False
    assert no_op.snapshot.revision == confirmed.revision

    blocked_compile = client.post(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/compile",
        json={
            "expected_revision": confirmed.revision,
            "actor_key": "m3_researcher",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert blocked_compile.status_code == 409
    assert blocked_compile.json()["code"] == "selection_conflict"

    with engine.connect() as connection:
        revision_count = connection.scalar(
            text(
                "SELECT count(*) FROM workspace.v022_graph_draft_revision "
                "WHERE graph_draft_id=:draft"
            ),
            {"draft": draft.graph_draft_id},
        )
        event_count = connection.scalar(
            text(
                "SELECT count(*) FROM workspace.v022_graph_draft_event WHERE graph_draft_id=:draft"
            ),
            {"draft": draft.graph_draft_id},
        )
        bridge = (
            connection.execute(
                text(
                    """
                SELECT b.graph_draft_revision,i.revision,i.intent_document,
                       g.compiled_research_graph_id,g.occurrence_count,
                       g.edge_count,g.projection_count,g.aggregation_instance_count,
                       g.strategy_branch_count
                FROM workspace.v022_graph_draft_compile_binding b
                JOIN workspace.v022_draft_intent i
                  ON i.draft_intent_id=b.draft_intent_id
                JOIN workspace.compiled_research_graph g
                  ON g.compiled_research_graph_id=:graph
                WHERE b.graph_draft_id=:draft
                  AND b.graph_draft_revision=:revision
                """
                ),
                {
                    "draft": draft.graph_draft_id,
                    "graph": first_compile.compiled_research_graph_id,
                    "revision": configured.revision,
                },
            )
            .mappings()
            .one()
        )
    engine.dispose()
    assert revision_count == confirmed.revision
    assert event_count == confirmed.revision
    assert bridge["graph_draft_revision"] == bridge["revision"] == configured.revision
    assert bridge["intent_document"]["aggregation_inputs"] == ["return_continuation__w120"]
    assert (
        str(bridge["compiled_research_graph_id"])
        == compiled_payload["compiled_research_graph_id"]
    )
    estimates = configured.derived_view["resources"]["estimates"]
    assert estimates["feature_occurrences"] == bridge["occurrence_count"]
    assert estimates["graph_edges"] == bridge["edge_count"] + bridge["projection_count"]
    assert estimates["aggregation_instances"] == bridge["aggregation_instance_count"]
    assert estimates["strategy_branches"] == bridge["strategy_branch_count"]
    assert estimates["backtest_cells"] == (
        bridge["aggregation_instance_count"] + bridge["strategy_branch_count"] * 6
    )


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_explicit_asset_selection_is_revision_scoped_and_compilable() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    _publish_workspace_context(engine)
    publish_catalog_release(engine, CURRENT_MANIFEST, context=CONTEXT)
    service = GraphDraftService(
        engine, GraphWorkspacePreviewService.from_manifest(CURRENT_MANIFEST)
    )
    draft = service.create(
        researcher_key="asset_selection_researcher",
        draft_key="explicit_asset_selection",
        name="Explicit Asset Selection",
        idempotency_key=uuid.uuid4(),
    )
    with engine.connect() as connection:
        security_ids = tuple(
            str(value)
            for value in connection.scalars(
                text(
                    "SELECT DISTINCT security.security_id "
                    "FROM catalog.security security JOIN data.daily_bar bar "
                    "ON bar.asset_id=security.legacy_asset_id "
                    "ORDER BY security.security_id LIMIT 2"
                )
            ).all()
        )
    selected = service.apply_event(
        draft.graph_draft_id,
        expected_revision=1,
        actor_key="asset_selection_researcher",
        idempotency_key=uuid.uuid4(),
        event_type="set_asset_selection",
        event={"security_ids": list(security_ids)},
    )
    assert selected.snapshot.asset_context["selection_kind"] == (
        "explicit_security_selection"
    )
    feature = service.apply_event(
        draft.graph_draft_id,
        expected_revision=2,
        actor_key="asset_selection_researcher",
        idempotency_key=uuid.uuid4(),
        event_type="select_feature_occurrence",
        event={"feature_key": "return_continuation__w120", "stage_no": 3},
    )
    configured = _configure_etf_baseline(
        service,
        feature.snapshot,
        actor_key="asset_selection_researcher",
    )
    compiled = service.compile(
        draft.graph_draft_id,
        expected_revision=configured.revision,
        actor_key="asset_selection_researcher",
        idempotency_key=uuid.uuid4(),
    )
    with engine.connect() as connection:
        old_context = connection.scalar(
            text(
                "SELECT asset_context_document "
                "FROM workspace.v022_graph_draft_revision "
                "WHERE graph_draft_id=:draft AND revision=1"
            ),
            {"draft": draft.graph_draft_id},
        )
        execution_context = connection.execute(
            text(
                "SELECT asset_set_definition_id,explicit_asset_selection_id "
                "FROM workspace.v022_compiled_execution_data_context "
                "WHERE compiled_execution_data_context_id=:context"
            ),
            {"context": compiled.compiled_execution_data_context_id},
        ).mappings().one()
    assert old_context["selection_kind"] == "unconfigured"
    assert old_context["members"] == []
    assert execution_context["asset_set_definition_id"] is None
    assert execution_context["explicit_asset_selection_id"] is not None


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_catalog_rebase_requires_preview_and_preserves_revision_identity() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    _publish_workspace_context(engine)
    old_release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
    old_service = GraphDraftService(engine, GraphWorkspacePreviewService.from_manifest(MANIFEST))
    draft = old_service.create(
        researcher_key="rebase_researcher",
        draft_key="rebase_source",
        name="Rebase source",
        idempotency_key=uuid.uuid4(),
    )
    selected = old_service.apply_event(
        draft.graph_draft_id,
        expected_revision=1,
        actor_key="rebase_researcher",
        idempotency_key=uuid.uuid4(),
        event_type="select_feature_occurrence",
        event={"feature_key": "return_continuation__w120", "stage_no": 3},
    ).snapshot

    current_release = publish_catalog_release(engine, CURRENT_MANIFEST, context=CONTEXT)
    current_service = GraphDraftService(
        engine, GraphWorkspacePreviewService.from_manifest(CURRENT_MANIFEST)
    )
    client = TestClient(create_app(_Reader(), graph_drafts=current_service))  # type: ignore[arg-type]
    blocked = client.post(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/events",
        json={
            "expected_revision": 2,
            "actor_key": "rebase_researcher",
            "idempotency_key": str(uuid.uuid4()),
            "event_type": "set_frequency",
            "event": {"frequency": "monthly"},
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "catalog_rebase_required"
    with pytest.raises(GraphCatalogRebaseRequired):
        current_service.compile(
            draft.graph_draft_id,
            expected_revision=2,
            actor_key="rebase_researcher",
            idempotency_key=uuid.uuid4(),
        )

    preview = client.post(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/rebase-previews",
        json={"expected_revision": 2, "actor_key": "rebase_researcher"},
    )
    assert preview.status_code == 200, preview.text
    impact = preview.json()["impact"]
    assert impact["change_type"] == "rebase_catalog"
    assert impact["from_catalog_release_id"] == str(old_release.catalog_release_id)
    assert impact["to_catalog_release_id"] == str(current_release.catalog_release_id)
    assert impact["removed_explicit_occurrences"] == []
    confirmed = client.post(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/change-previews/"
        f"{preview.json()['impact_token']}/confirm",
        json={
            "expected_revision": 2,
            "actor_key": "rebase_researcher",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["revision"] == 3
    assert confirmed.json()["catalog_release_id"] == str(current_release.catalog_release_id)
    assert confirmed.json()["intent"] == selected.intent
    with engine.connect() as connection:
        release_ids = tuple(
            connection.execute(
                text(
                    "SELECT catalog_release_id FROM workspace.v022_graph_draft_revision "
                    "WHERE graph_draft_id=:draft ORDER BY revision"
                ),
                {"draft": draft.graph_draft_id},
            ).scalars()
        )
    engine.dispose()
    assert release_ids == (
        old_release.catalog_release_id,
        old_release.catalog_release_id,
        current_release.catalog_release_id,
    )


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_feature_batch_event_is_atomic_and_uses_one_revision() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    _publish_workspace_context(engine)
    publish_catalog_release(engine, CURRENT_MANIFEST, context=CONTEXT)
    service = GraphDraftService(
        engine, GraphWorkspacePreviewService.from_manifest(CURRENT_MANIFEST)
    )
    draft = service.create(
        researcher_key="batch_researcher",
        draft_key="batch_atomicity",
        name="Batch atomicity",
        idempotency_key=uuid.uuid4(),
    )
    client = TestClient(create_app(_Reader(), graph_drafts=service))  # type: ignore[arg-type]

    invalid = client.post(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/events",
        json={
            "expected_revision": 1,
            "actor_key": "batch_researcher",
            "idempotency_key": str(uuid.uuid4()),
            "event_type": "batch_select_feature_occurrences",
            "event": {
                "occurrences": [
                    {"feature_key": "return_continuation__w120", "stage_no": 3},
                    {"feature_key": "unknown_feature", "stage_no": 3},
                ]
            },
        },
    )
    assert invalid.status_code == 422
    assert service.get(draft.graph_draft_id).revision == 1
    assert service.get(draft.graph_draft_id).intent["explicit_features"] == []

    event_key = uuid.uuid4()
    payload = {
        "expected_revision": 1,
        "actor_key": "batch_researcher",
        "idempotency_key": str(event_key),
        "event_type": "batch_select_feature_occurrences",
        "event": {
            "occurrences": [
                {"feature_key": "return_continuation__w120", "stage_no": 3},
                {"feature_key": "price_cross_above_ma__s1_l200", "stage_no": 3},
                {"feature_key": "low_illiquidity_quality__w20", "stage_no": 3},
            ]
        },
    }
    selected = client.post(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/events", json=payload
    )
    replayed = client.post(
        f"/api/v2/workspace/graph-drafts/{draft.graph_draft_id}/events", json=payload
    )
    assert selected.status_code == replayed.status_code == 200
    assert selected.json()["revision"] == replayed.json()["revision"] == 2
    assert len(selected.json()["intent"]["explicit_features"]) == 3
    with engine.connect() as connection:
        event_count = connection.scalar(
            text(
                "SELECT count(*) FROM workspace.v022_graph_draft_event WHERE graph_draft_id=:draft"
            ),
            {"draft": draft.graph_draft_id},
        )
    engine.dispose()
    assert event_count == 1


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_explicit_configuration_axes_compile_exact_instances_and_branches() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    _publish_workspace_context(engine)
    publish_catalog_release(engine, CURRENT_MANIFEST, context=CONTEXT)
    service = GraphDraftService(
        engine, GraphWorkspacePreviewService.from_manifest(CURRENT_MANIFEST)
    )
    draft = service.create(
        researcher_key="configuration_researcher",
        draft_key="configuration_axes",
        name="Configuration axes",
        idempotency_key=uuid.uuid4(),
        asset_context_key="us_style_rotation_4_etf_sample_v1",
        data_input_keys=("canonical_market_bars",),
    )

    commands = (
        (
            "select_feature_occurrence",
            {"feature_key": "return_continuation__w120", "stage_no": 3},
        ),
        (
            "select_aggregation_family",
            {"family_key": "flat_equal_weight_mean"},
        ),
        (
            "set_aggregation_parameter_presets",
            {
                "family_key": "flat_equal_weight_mean",
                "preset_keys": ["signal_equal_v1"],
            },
        ),
        (
            "set_strategy_parameter_presets",
            {
                "strategy_key": "cross_section_rank_top_k_parity",
                "preset_keys": ["k1", "k2", "k3"],
            },
        ),
        ("select_defense", {"defense_key": "none"}),
    )
    snapshot = draft
    for event_type, event in commands:
        snapshot = service.apply_event(
            draft.graph_draft_id,
            expected_revision=snapshot.revision,
            actor_key="configuration_researcher",
            idempotency_key=uuid.uuid4(),
            event_type=event_type,
            event=event,
        ).snapshot

    assert snapshot.revision == 6
    assert snapshot.derived_view["summary"]["aggregation_instance_count"] == 1
    assert snapshot.derived_view["summary"]["strategy_branch_count"] == 3
    assert snapshot.derived_view["summary"]["backtest_cell_count"] == 19
    assert snapshot.derived_view["blockers"] == []
    compiled = service.compile(
        draft.graph_draft_id,
        expected_revision=snapshot.revision,
        actor_key="configuration_researcher",
        idempotency_key=uuid.uuid4(),
    )
    with engine.connect() as connection:
        graph = (
            connection.execute(
                text(
                    "SELECT aggregation_instance_count,strategy_branch_count "
                    "FROM workspace.compiled_research_graph "
                    "WHERE compiled_research_graph_id=:graph"
                ),
                {"graph": compiled.compiled_research_graph_id},
            )
            .mappings()
            .one()
        )
        preset_rows = tuple(
            connection.execute(
                text(
                    "SELECT f.family_key,d.parameter_preset_key "
                    "FROM workspace.compiled_aggregation_instance i "
                    "JOIN aggregation.parameter_preset_version v "
                    "ON v.parameter_preset_version_id=i.parameter_preset_version_id "
                    "JOIN aggregation.parameter_preset_definition d "
                    "ON d.parameter_preset_definition_id=v.parameter_preset_definition_id "
                    "JOIN aggregation.aggregation_family f "
                    "ON f.aggregation_family_id=d.aggregation_family_id "
                    "WHERE i.compiled_research_graph_id=:graph ORDER BY i.instance_key"
                ),
                {"graph": compiled.compiled_research_graph_id},
            ).mappings()
        )
        presets = tuple(
            row["parameter_preset_key"].removeprefix(f"{row['family_key']}__")
            for row in preset_rows
        )
        branch_ids = tuple(
            connection.scalars(
                text(
                    "SELECT compiled_strategy_branch_id "
                    "FROM strategy.v022_compiled_strategy_branch "
                    "WHERE compiled_research_graph_id=:graph "
                    "AND defense_version_id IS NULL "
                    "ORDER BY branch_key LIMIT 2"
                ),
                {"graph": compiled.compiled_research_graph_id},
            )
        )
    assert len(branch_ids) == 2
    baseline_branch_id, branch_id = branch_ids
    identities = ConfigurationSnapshotService(engine)
    configuration = identities.publish(
        compiled_strategy_branch_id=branch_id,
        execution_policy_document={"cost_policy": "linear_10bps_v1"},
        provenance_document={
            "graph_draft_id": str(draft.graph_draft_id),
            "graph_draft_revision": snapshot.revision,
            "source": "first_publication",
        },
    )
    replay = identities.publish(
        compiled_strategy_branch_id=branch_id,
        execution_policy_document={"cost_policy": "linear_10bps_v1"},
        provenance_document={"source": "must_not_replace_frozen_provenance"},
    )
    changed_policy = identities.publish(
        compiled_strategy_branch_id=branch_id,
        execution_policy_document={"cost_policy": "linear_20bps_v1"},
        provenance_document={"source": "new_semantic_configuration"},
    )
    baseline_configuration = identities.publish(
        compiled_strategy_branch_id=baseline_branch_id,
        execution_policy_document={"cost_policy": "linear_10bps_v1"},
        provenance_document={"source": "matched_none_branch"},
    )
    panel_service = CommonEvaluationPanelService(engine)
    observations = (
        PanelObservation(date(2026, 7, 29), "IWD"),
        PanelObservation(date(2026, 7, 29), "IWF"),
    )
    panel = panel_service.publish(
        evidence_class="walk_forward_backtest",
        observations=observations,
        panel_document={"calendar": "XNYS", "mask_policy": "exact_intersection_v1"},
    )
    panel_replay = panel_service.publish(
        evidence_class="walk_forward_backtest",
        observations=observations,
        panel_document={"calendar": "XNYS", "mask_policy": "exact_intersection_v1"},
    )
    result_artifact = ArtifactService(engine).publish(
        artifact_type="v022_test_result",
        artifact_key="configuration_axes_result",
        version_number=1,
        semantic_payload={"result": "configuration_axes"},
        content_payload={"metric": "0.1"},
    )
    evidence_service = ResultEvidenceService(engine)
    evidence = evidence_service.publish(
        result_artifact_id=result_artifact.artifact_id,
        configuration_snapshot_id=configuration.configuration_snapshot_id,
        common_evaluation_panel_id=panel.common_evaluation_panel_id,
        evidence_class="walk_forward_backtest",
        evidence_document={
            "interval": ["2026-07-29", "2026-07-29"],
            "comparison_contexts": {"portfolio": {"benchmark": "SPY", "cost": "linear_10bps_v1"}},
        },
        quality_document={"state": "accepted", "reason_codes": []},
    )
    evidence_replay = evidence_service.publish(
        result_artifact_id=result_artifact.artifact_id,
        configuration_snapshot_id=configuration.configuration_snapshot_id,
        common_evaluation_panel_id=panel.common_evaluation_panel_id,
        evidence_class="walk_forward_backtest",
        evidence_document={
            "interval": ["2026-07-29", "2026-07-29"],
            "comparison_contexts": {"portfolio": {"benchmark": "SPY", "cost": "linear_10bps_v1"}},
        },
        quality_document={"state": "accepted", "reason_codes": []},
    )
    with pytest.raises(ValueError, match="already bound to different Evidence"):
        evidence_service.publish(
            result_artifact_id=result_artifact.artifact_id,
            configuration_snapshot_id=configuration.configuration_snapshot_id,
            common_evaluation_panel_id=panel.common_evaluation_panel_id,
            evidence_class="walk_forward_backtest",
            evidence_document={
                "interval": ["2026-07-29", "2026-07-29"],
                "comparison_contexts": {
                    "portfolio": {"benchmark": "SPY", "cost": "linear_10bps_v1"}
                },
            },
            quality_document={"state": "rejected", "reason_codes": ["late_data"]},
        )
    baseline_result = ArtifactService(engine).publish(
        artifact_type="v022_test_result",
        artifact_key="configuration_axes_none_baseline",
        version_number=1,
        semantic_payload={"result": "configuration_axes_none_baseline"},
        content_payload={"metric": "0.09"},
    )
    baseline_evidence = evidence_service.publish(
        result_artifact_id=baseline_result.artifact_id,
        configuration_snapshot_id=baseline_configuration.configuration_snapshot_id,
        common_evaluation_panel_id=panel.common_evaluation_panel_id,
        evidence_class="walk_forward_backtest",
        evidence_document={
            "interval": ["2026-07-29", "2026-07-29"],
            "comparison_contexts": {"portfolio": {"benchmark": "SPY", "cost": "linear_10bps_v1"}},
        },
        quality_document={"state": "accepted", "reason_codes": []},
    )
    comparison = ResultComparisonService(engine).publish(
        left_result_evidence_snapshot_id=evidence.result_evidence_snapshot_id,
        right_result_evidence_snapshot_id=baseline_evidence.result_evidence_snapshot_id,
        comparison_scope="portfolio",
    )
    baseline_service = MatchedBaselineService(engine)
    baseline_assessment = baseline_service.publish(
        subject_result_evidence_snapshot_id=evidence.result_evidence_snapshot_id,
        baseline_kind="defense_none",
        assessment_version=1,
        reason_codes=("defense_retired",),
    )
    baseline_assessment_replay = baseline_service.publish(
        subject_result_evidence_snapshot_id=evidence.result_evidence_snapshot_id,
        baseline_kind="defense_none",
        assessment_version=1,
        reason_codes=("defense_retired",),
    )
    missing_baseline = baseline_service.publish(
        subject_result_evidence_snapshot_id=evidence.result_evidence_snapshot_id,
        baseline_kind="deterministic_aggregation",
        assessment_version=1,
        reason_codes=("matched_baseline_missing",),
    )
    product_service = ProductIdentityService(engine)
    product = product_service.publish_definition(
        product_key="v022_return_continuation_defended",
        name="Return continuation defended",
        description="M7 deterministic Product identity fixture",
    )
    execution_version = product_service.publish_execution_version(
        product_definition_id=product.product_definition_id,
        version_number=1,
        configuration_snapshot_id=configuration.configuration_snapshot_id,
        promotion_result_evidence_snapshot_id=evidence.result_evidence_snapshot_id,
        runtime_policy_document={
            "model_state_policy": "deterministic_null",
            "decision_failure_policy": "suspend",
        },
    )
    execution_replay = product_service.publish_execution_version(
        product_definition_id=product.product_definition_id,
        version_number=1,
        configuration_snapshot_id=configuration.configuration_snapshot_id,
        promotion_result_evidence_snapshot_id=evidence.result_evidence_snapshot_id,
        runtime_policy_document={
            "model_state_policy": "deterministic_null",
            "decision_failure_policy": "suspend",
        },
    )
    qualification_version = product_service.publish_qualification_version(
        product_definition_id=product.product_definition_id,
        version_number=1,
        execution_version_id=execution_version.version_id,
        result_evidence_snapshot_id=evidence.result_evidence_snapshot_id,
        qualification_document={
            "state": "qualified",
            "comparison_id": str(comparison.result_comparison_id),
        },
        evidence_artifact_ids=(comparison.artifact_id, baseline_assessment.artifact_id),
    )
    monitoring_v1 = product_service.publish_monitoring_policy_version(
        product_definition_id=product.product_definition_id,
        version_number=1,
        monitoring_policy_document={
            "minimum_completed_decisions": 1,
            "maximum_missing_fraction": "0.50",
            "coverage_warning_floor": "0.80",
            "coverage_watch_floor": "0.90",
        },
    )
    monitoring_v2 = product_service.publish_monitoring_policy_version(
        product_definition_id=product.product_definition_id,
        version_number=2,
        monitoring_policy_document={
            "minimum_completed_decisions": 1,
            "maximum_missing_fraction": "0.50",
            "coverage_warning_floor": "0.80",
            "coverage_watch_floor": "0.95",
        },
    )
    with pytest.raises(ValueError, match="already bound to different semantics"):
        product_service.publish_monitoring_policy_version(
            product_definition_id=product.product_definition_id,
            version_number=1,
            monitoring_policy_document={"coverage_floor": "0.50"},
        )
    schedule = DecisionScheduleService(engine).publish(
        schedule_key="weekly_monday_close_v1",
        version_number=1,
        frequency="weekly",
        sessions=(
            DecisionSessionInput(date(2026, 8, 3), datetime(2026, 8, 3, 20, tzinfo=UTC)),
            DecisionSessionInput(date(2026, 8, 10), datetime(2026, 8, 10, 20, tzinfo=UTC)),
            DecisionSessionInput(date(2026, 8, 17), datetime(2026, 8, 17, 20, tzinfo=UTC)),
        ),
    )
    enrollment_service = ProductEnrollmentService(engine)
    enrollment = enrollment_service.publish(
        execution_version_id=execution_version.version_id,
        qualification_version_id=qualification_version.version_id,
        monitoring_policy_version_id=monitoring_v1.version_id,
        decision_schedule_version_id=schedule.decision_schedule_version_id,
        oos_anchor_cutoff_at=datetime(2026, 8, 4, 20, tzinfo=UTC),
        activation_effective_at=datetime(2026, 8, 5, 12, tzinfo=UTC),
    )
    enrollment_replay = enrollment_service.publish(
        execution_version_id=execution_version.version_id,
        qualification_version_id=qualification_version.version_id,
        monitoring_policy_version_id=monitoring_v1.version_id,
        decision_schedule_version_id=schedule.decision_schedule_version_id,
        oos_anchor_cutoff_at=datetime(2026, 8, 4, 20, tzinfo=UTC),
        activation_effective_at=datetime(2026, 8, 5, 12, tzinfo=UTC),
    )
    shadow_service = ShadowPlanService(engine)
    shadow_plan = shadow_service.publish(
        plan_key="representative_shadow_wave_1",
        version_number=1,
        representatives=(
            ShadowRepresentative(
                context=ShadowContext(
                    "us_style_rotation_4_etf_sample_v1", "etf", "weekly"
                ),
                product_enrollment_id=enrollment.product_enrollment_id,
                representative_role="active_product_shadow",
            ),
        ),
    )
    shadow_plan_replay = shadow_service.publish(
        plan_key="representative_shadow_wave_1",
        version_number=1,
        representatives=(
            ShadowRepresentative(
                context=ShadowContext(
                    "us_style_rotation_4_etf_sample_v1", "etf", "weekly"
                ),
                product_enrollment_id=enrollment.product_enrollment_id,
                representative_role="active_product_shadow",
            ),
        ),
    )
    scheduler_shadow_plan = shadow_service.publish(
        plan_key="representative_shadow_scheduler_fixture",
        version_number=1,
        representatives=(
            ShadowRepresentative(
                context=ShadowContext(
                    "us_style_rotation_4_etf_sample_v1", "etf", "weekly"
                ),
                product_enrollment_id=enrollment.product_enrollment_id,
                representative_role="shadow_only",
            ),
        ),
    )
    with pytest.raises(ValueError, match="frequency does not match"):
        shadow_service.publish(
            plan_key="invalid_monthly_shadow",
            version_number=1,
            representatives=(
                ShadowRepresentative(
                    context=ShadowContext(
                        "us_style_rotation_4_etf_sample_v1", "etf", "monthly"
                    ),
                    product_enrollment_id=enrollment.product_enrollment_id,
                    representative_role="shadow_only",
                ),
            ),
        )
    runtime_ids = tuple(
        ArtifactService(engine)
        .publish(
            artifact_type=f"v022_test_{role}",
            artifact_key=f"product_decision_{role}",
            version_number=1,
            semantic_payload={"role": role},
            content_payload={"role": role},
        )
        .artifact_id
        for role in ("input", "aggregation", "strategy", "defense", "merged")
    )
    decision_service = ProductDecisionService(engine)
    bridge_gap = decision_service.publish(
        product_enrollment_id=enrollment.product_enrollment_id,
        decision_session_id=schedule.session_ids[0],
        evidence_class="qualification_bridge",
        decision_status="missing",
        decision_document={"planned_execution_date": "2026-08-04"},
        quality_document={"state": "not_run"},
        reason_codes=("pre_activation_session",),
    )
    completed_decision = decision_service.publish(
        product_enrollment_id=enrollment.product_enrollment_id,
        decision_session_id=schedule.session_ids[1],
        evidence_class="prospective_oos",
        decision_status="completed",
        decision_document={"recommended_execution_date": "2026-08-11"},
        quality_document={"state": "accepted"},
        runtime_artifacts=RuntimeArtifactSet(
            input_manifest_artifact_id=runtime_ids[0],
            aggregation_run_artifact_id=runtime_ids[1],
            strategy_target_artifact_id=runtime_ids[2],
            defense_decision_artifact_id=None,
            merged_target_artifact_id=runtime_ids[4],
        ),
    )
    completed_replay = decision_service.publish(
        product_enrollment_id=enrollment.product_enrollment_id,
        decision_session_id=schedule.session_ids[1],
        evidence_class="prospective_oos",
        decision_status="completed",
        decision_document={"recommended_execution_date": "2026-08-11"},
        quality_document={"state": "accepted"},
        runtime_artifacts=RuntimeArtifactSet(
            input_manifest_artifact_id=runtime_ids[0],
            aggregation_run_artifact_id=runtime_ids[1],
            strategy_target_artifact_id=runtime_ids[2],
            defense_decision_artifact_id=None,
            merged_target_artifact_id=runtime_ids[4],
        ),
    )
    model_state = ArtifactService(engine).publish(
        artifact_type="v022_test_model_state",
        artifact_key="deterministic_decision_illegal_model_state",
        version_number=1,
        semantic_payload={"model_state": "illegal_for_deterministic"},
        content_payload={"model_state": "illegal_for_deterministic"},
    )
    with pytest.raises(ValueError, match="NULL active Model State"):
        decision_service.publish(
            product_enrollment_id=enrollment.product_enrollment_id,
            decision_session_id=schedule.session_ids[2],
            evidence_class="prospective_oos",
            decision_status="completed",
            decision_document={"recommended_execution_date": "2026-08-18"},
            quality_document={"state": "accepted"},
            runtime_artifacts=RuntimeArtifactSet(
                input_manifest_artifact_id=runtime_ids[0],
                aggregation_run_artifact_id=runtime_ids[1],
                strategy_target_artifact_id=runtime_ids[2],
                defense_decision_artifact_id=None,
                merged_target_artifact_id=runtime_ids[4],
                active_model_state_artifact_id=model_state.artifact_id,
            ),
        )
    missing_decision = decision_service.publish(
        product_enrollment_id=enrollment.product_enrollment_id,
        decision_session_id=schedule.session_ids[2],
        evidence_class="prospective_oos",
        decision_status="missing",
        decision_document={"planned_execution_date": "2026-08-18"},
        quality_document={"state": "not_run"},
        reason_codes=("input_snapshot_unavailable",),
    )
    with engine.connect() as connection:
        shadow_representative_id = connection.scalar(
            text(
                "SELECT shadow_representative_id "
                "FROM workspace.v022_shadow_representative WHERE shadow_plan_id=:plan"
            ),
            {"plan": shadow_plan.shadow_plan_id},
        )
    assert shadow_representative_id is not None
    comparator = ShadowComparatorVersionService(engine).publish(
        comparator_key="strict_decision_comparator",
        version_number=1,
        fields=(
            ComparatorField(
                "execution_date",
                ("recommended_execution_date",),
                ("recommended_execution_date",),
            ),
        ),
    )
    manual_legacy_references = tuple(
        ArtifactService(engine)
        .publish(
            artifact_type="v021_manual_shadow_reference_fixture",
            artifact_key=f"manual_legacy_reference_{ordinal}",
            version_number=1,
            semantic_payload={"ordinal": ordinal},
            content_payload={"ordinal": ordinal},
        )
        .artifact_id
        for ordinal in (1, 2)
    )
    with engine.connect() as connection:
        scheduler_representative_id = connection.scalar(
            text(
                "SELECT shadow_representative_id "
                "FROM workspace.v022_shadow_representative WHERE shadow_plan_id=:plan"
            ),
            {"plan": scheduler_shadow_plan.shadow_plan_id},
        )
    assert scheduler_representative_id is not None
    v021_execution_spec = ArtifactService(engine).publish(
        artifact_type="v021_shadow_execution_spec",
        artifact_key="legacy_deterministic_product_runtime_v1",
        version_number=1,
        semantic_payload={"runtime_contract": "v0.21", "mode": "shadow_only"},
        content_payload={"runtime_contract": "v0.21", "mode": "shadow_only"},
    )
    v021_capability = RuntimeCapability(
        "v0.21", "compiler-21.9", "executor-21.9", "1" * 64, "legacy-product"
    )
    v022_capability = RuntimeCapability(
        "v0.22", "compiler-22.0", "executor-22.0", "2" * 64, "v022-product"
    )
    runtime_binding = ShadowRuntimeBindingService(engine).publish(
        shadow_representative_id=scheduler_representative_id,
        v021_product_enrollment_id=None,
        v021_execution_spec_artifact_id=v021_execution_spec.artifact_id,
        comparator_artifact_id=comparator.artifact_id,
        v021_capability=v021_capability,
        v022_capability=v022_capability,
    )
    runtime_binding_replay = ShadowRuntimeBindingService(engine).publish(
        shadow_representative_id=scheduler_representative_id,
        v021_product_enrollment_id=None,
        v021_execution_spec_artifact_id=v021_execution_spec.artifact_id,
        comparator_artifact_id=comparator.artifact_id,
        v021_capability=v021_capability,
        v022_capability=v022_capability,
    )
    ReleaseControlService(engine).transition(
        target="shadow",
        reason_code="begin_scheduler_fixture",
        reason="Enable exact-capability Shadow dual-run scheduling.",
        requested_by="integration-test",
        gate_evidence={"shadow_plan_artifact_id": scheduler_shadow_plan.artifact_id},
        requested_at=datetime(2026, 8, 17, 23, tzinfo=UTC),
    )
    scheduler = ShadowDualRunScheduler(engine)
    scheduled_shadow = scheduler.schedule_due(
        shadow_runtime_binding_id=runtime_binding.shadow_runtime_binding_id,
        scheduled_at=datetime(2026, 8, 18, 0, tzinfo=UTC),
    )
    scheduled_shadow_replay = scheduler.schedule_due(
        shadow_runtime_binding_id=runtime_binding.shadow_runtime_binding_id,
        scheduled_at=datetime(2026, 8, 18, 0, tzinfo=UTC),
    )
    expired_worker = ShadowWorkerService(engine, service_principal="shadow-runtime")
    expired_worker.register(
        worker_id="expired-n-n1-worker",
        capabilities=(v021_capability, v022_capability),
        ttl_seconds=30,
        registered_at=datetime(2026, 8, 17, 23, 58, tzinfo=UTC),
    )
    assert (
        expired_worker.claim(
            worker_id="expired-n-n1-worker",
            claimed_at=datetime(2026, 8, 18, 0, tzinfo=UTC),
        )
        is None
    )
    exact_worker = ShadowWorkerService(engine, service_principal="shadow-runtime")
    exact_worker.register(
        worker_id="exact-n-n1-worker",
        capabilities=(v021_capability, v022_capability),
        ttl_seconds=600,
        registered_at=datetime(2026, 8, 18, 0, tzinfo=UTC),
    )
    v021_claim = exact_worker.claim(
        worker_id="exact-n-n1-worker",
        claimed_at=datetime(2026, 8, 18, 0, 1, tzinfo=UTC),
    )
    assert v021_claim is not None and v021_claim.runtime_contract == "v0.21"
    scheduled_reference = V021ShadowReferenceService(engine).publish(
        shadow_runtime_binding_id=runtime_binding.shadow_runtime_binding_id,
        decision_session_id=v021_claim.decision_session_id,
        decision_document={"recommended_execution_date": "2026-08-11"},
        known_at=datetime(2026, 8, 18, 0, 1, 30, tzinfo=UTC),
    )
    exact_worker.complete(
        v021_claim,
        worker_id="exact-n-n1-worker",
        v021_reference_artifact_id=scheduled_reference.artifact_id,
        completed_at=datetime(2026, 8, 18, 0, 2, tzinfo=UTC),
    )
    v022_shadow_worker = ShadowV022DecisionWorker(
        engine,
        service_principal="shadow-runtime",
        worker_id="v022-product-shadow-worker",
        capability=v022_capability,
    )
    v022_outcome = v022_shadow_worker.run_once(
        observed_at=datetime(2026, 8, 18, 0, 2, tzinfo=UTC)
    )
    assert v022_outcome.status == "completed"
    assert v022_outcome.product_decision_id == completed_decision.product_decision_id
    coordinated_shadow = ShadowComparisonCoordinator(engine).publish_ready(
        known_at=datetime(2026, 8, 18, 0, 4, tzinfo=UTC)
    )
    coordinated_shadow_replay = ShadowComparisonCoordinator(engine).publish_ready(
        known_at=datetime(2026, 8, 18, 0, 4, tzinfo=UTC)
    )
    v021_missing_claim = exact_worker.claim(
        worker_id="exact-n-n1-worker",
        claimed_at=datetime(2026, 8, 18, 0, 5, tzinfo=UTC),
    )
    assert v021_missing_claim is not None
    assert v021_missing_claim.runtime_contract == "v0.21"
    missing_session_reference = V021ShadowReferenceService(engine).publish(
        shadow_runtime_binding_id=runtime_binding.shadow_runtime_binding_id,
        decision_session_id=v021_missing_claim.decision_session_id,
        decision_document={"recommended_execution_date": "2026-08-18"},
        known_at=datetime(2026, 8, 18, 0, 5, 30, tzinfo=UTC),
    )
    exact_worker.complete(
        v021_missing_claim,
        worker_id="exact-n-n1-worker",
        v021_reference_artifact_id=missing_session_reference.artifact_id,
        completed_at=datetime(2026, 8, 18, 0, 6, tzinfo=UTC),
    )
    v022_missing_outcome = v022_shadow_worker.run_once(
        observed_at=datetime(2026, 8, 18, 0, 6, 30, tzinfo=UTC)
    )
    assert v022_missing_outcome.status == "completed"
    assert v022_missing_outcome.product_decision_id == missing_decision.product_decision_id
    coordinated_missing_shadow = ShadowComparisonCoordinator(engine).publish_ready(
        known_at=datetime(2026, 8, 18, 0, 8, tzinfo=UTC)
    )
    with engine.connect() as connection:
        scheduler_outcomes = tuple(
            connection.scalars(
                text(
                    "SELECT outcome FROM workspace.v022_shadow_decision_comparison "
                    "WHERE shadow_representative_id=:representative ORDER BY known_at"
                ),
                {"representative": scheduler_representative_id},
            )
        )
    shadow_comparisons = ShadowComparisonService(engine)
    matched_shadow = shadow_comparisons.publish(
        shadow_representative_id=shadow_representative_id,
        v022_product_decision_id=completed_decision.product_decision_id,
        comparator_artifact_id=comparator.artifact_id,
        v021_reference_artifact_id=manual_legacy_references[0],
        outcome="matched",
        comparison_document={"target_weights_equal": True},
        known_at=datetime(2026, 8, 10, 21, tzinfo=UTC),
    )
    unmatched_shadow = shadow_comparisons.publish(
        shadow_representative_id=shadow_representative_id,
        v022_product_decision_id=missing_decision.product_decision_id,
        comparator_artifact_id=comparator.artifact_id,
        v021_reference_artifact_id=manual_legacy_references[1],
        outcome="different",
        comparison_document={"v022_decision_status": "missing"},
        known_at=datetime(2026, 8, 17, 21, tzinfo=UTC),
    )
    shadow_coverage_service = ShadowCoverageService(engine)
    shadow_coverage = shadow_coverage_service.publish(
        shadow_plan_id=shadow_plan.shadow_plan_id,
        comparator_artifact_id=comparator.artifact_id,
        known_at=datetime(2026, 8, 18, 0, tzinfo=UTC),
    )
    shadow_coverage_replay = shadow_coverage_service.publish(
        shadow_plan_id=shadow_plan.shadow_plan_id,
        comparator_artifact_id=comparator.artifact_id,
        known_at=datetime(2026, 8, 18, 0, tzinfo=UTC),
    )
    lifecycle_service = EnrollmentLifecycleService(engine)
    suspended = lifecycle_service.publish(
        product_enrollment_id=enrollment.product_enrollment_id,
        expected_sequence=1,
        target="suspended",
        reason_code="operator_review",
        reason="Pause decisions for an explicit operator review.",
        requested_by="integration-test",
        requested_at=datetime(2026, 8, 18, 20, tzinfo=UTC),
        effective_at=datetime(2026, 8, 18, 21, tzinfo=UTC),
    )
    suspended_replay = lifecycle_service.publish(
        product_enrollment_id=enrollment.product_enrollment_id,
        expected_sequence=1,
        target="suspended",
        reason_code="operator_review",
        reason="Pause decisions for an explicit operator review.",
        requested_by="integration-test",
        requested_at=datetime(2026, 8, 18, 20, tzinfo=UTC),
        effective_at=datetime(2026, 8, 18, 21, tzinfo=UTC),
    )
    resumed = lifecycle_service.publish(
        product_enrollment_id=enrollment.product_enrollment_id,
        expected_sequence=2,
        target="active",
        reason_code="review_cleared",
        reason="Resume after the operator review cleared.",
        requested_by="integration-test",
        requested_at=datetime(2026, 8, 19, 20, tzinfo=UTC),
        effective_at=datetime(2026, 8, 19, 21, tzinfo=UTC),
    )
    monitoring_service = OOSMonitoringService(engine)
    monitoring_engine = ArtifactService(engine).publish(
        artifact_type="v022_monitoring_engine_version",
        artifact_key="v022_basic_oos_health_v1",
        version_number=1,
        semantic_payload={"implementation": "basic_oos_health", "version": 1},
        content_payload={"implementation": "basic_oos_health", "version": 1},
    )
    monitoring_snapshot_v1 = monitoring_service.publish(
        product_enrollment_id=enrollment.product_enrollment_id,
        monitoring_policy_version_id=monitoring_v1.version_id,
        monitoring_engine_artifact_id=monitoring_engine.artifact_id,
        as_of_decision_session_id=schedule.session_ids[2],
        known_at=datetime(2026, 8, 17, 21, tzinfo=UTC),
        metrics_document={"signal_coverage": "0.94"},
    )
    monitoring_snapshot_v2 = monitoring_service.publish(
        product_enrollment_id=enrollment.product_enrollment_id,
        monitoring_policy_version_id=monitoring_v2.version_id,
        monitoring_engine_artifact_id=monitoring_engine.artifact_id,
        as_of_decision_session_id=schedule.session_ids[2],
        known_at=datetime(2026, 8, 17, 21, tzinfo=UTC),
        metrics_document={"signal_coverage": "0.94"},
    )
    monitoring_replay = monitoring_service.publish(
        product_enrollment_id=enrollment.product_enrollment_id,
        monitoring_policy_version_id=monitoring_v2.version_id,
        monitoring_engine_artifact_id=monitoring_engine.artifact_id,
        as_of_decision_session_id=schedule.session_ids[2],
        known_at=datetime(2026, 8, 17, 21, tzinfo=UTC),
        metrics_document={"signal_coverage": "0.94"},
    )
    identity_client = TestClient(create_app(ArtifactQueryService(engine)))
    experiment_catalog_response = identity_client.get("/api/v2/v022/experiments")
    experiment_detail_response = identity_client.get(
        f"/api/v2/v022/experiments/{evidence.result_evidence_snapshot_id}"
    )
    product_catalog_response = identity_client.get("/api/v2/v022/products")
    product_detail_response = identity_client.get(
        f"/api/v2/v022/products/{enrollment.product_enrollment_id}"
    )
    with engine.connect() as connection:
        direct_inputs = connection.scalar(
            text(
                "SELECT count(*) FROM experiment.v022_configuration_direct_input "
                "WHERE configuration_snapshot_id=:snapshot"
            ),
            {"snapshot": configuration.configuration_snapshot_id},
        )
        panel_members = connection.scalar(
            text(
                "SELECT count(*) FROM experiment.v022_common_evaluation_panel_member "
                "WHERE common_evaluation_panel_id=:panel"
            ),
            {"panel": panel.common_evaluation_panel_id},
        )
    engine.dispose()
    assert graph["aggregation_instance_count"] == 1
    assert graph["strategy_branch_count"] == 3
    assert presets == ("signal_equal_v1",)
    assert direct_inputs == 1
    assert configuration.semantic_identity_document["direct_inputs"][0]["variant_key"] == (
        "return_continuation__w120"
    )
    assert configuration.display_document["direct_inputs"][0]["name"] == "Return continuation"
    assert replay.reused is True
    assert replay.configuration_snapshot_id == configuration.configuration_snapshot_id
    assert replay.provenance_document["source"] == "first_publication"
    assert changed_policy.configuration_fingerprint != configuration.configuration_fingerprint
    assert panel_members == 2
    assert panel_replay.reused is True
    assert evidence_replay.reused is True
    assert evidence_replay.result_evidence_snapshot_id == evidence.result_evidence_snapshot_id
    assert comparison.classification == "controlled"
    assert comparison.changed_dimensions == ("strategy_selection",)
    assert baseline_assessment.status == "missing"
    assert baseline_assessment.baseline_result_evidence_snapshot_id is None
    assert baseline_assessment_replay.reused is True
    assert missing_baseline.status == "missing"
    assert missing_baseline.baseline_result_evidence_snapshot_id is None
    assert execution_version.version_kind == "execution"
    assert execution_replay.reused is True
    assert qualification_version.version_kind == "qualification"
    assert monitoring_v1.version_kind == "monitoring_policy"
    assert monitoring_v2.version_number == 2
    assert monitoring_v1.fingerprint != monitoring_v2.fingerprint
    assert execution_version.version_id == execution_replay.version_id
    assert enrollment.first_eligible_decision_session_id == schedule.session_ids[1]
    assert enrollment_replay.reused is True
    assert shadow_plan.representative_count == 1
    assert shadow_plan.weekly_count == 1
    assert shadow_plan.monthly_count == 0
    assert shadow_plan.covers_etf is True
    assert shadow_plan.covers_large_cap is False
    assert shadow_plan_replay.reused is True
    assert runtime_binding_replay.reused is True
    assert scheduled_shadow.eligible_session_count == 2
    assert scheduled_shadow.created_intent_count == 2
    assert scheduled_shadow.created_work_item_count == 4
    assert scheduled_shadow_replay.created_intent_count == 0
    assert scheduled_shadow_replay.created_work_item_count == 0
    assert coordinated_shadow.ready_pair_count == 1
    assert coordinated_shadow.published_comparison_count == 1
    assert coordinated_shadow_replay.ready_pair_count == 0
    assert coordinated_shadow_replay.published_comparison_count == 0
    assert coordinated_missing_shadow.ready_pair_count == 1
    assert coordinated_missing_shadow.published_comparison_count == 1
    assert scheduler_outcomes == ("matched", "different")
    assert bridge_gap.oos_eligible is False
    assert completed_decision.oos_eligible is True
    assert completed_replay.reused is True
    assert missing_decision.decision_status == "missing"
    assert missing_decision.oos_eligible is True
    assert matched_shadow.outcome == "matched"
    assert unmatched_shadow.outcome == "different"
    assert shadow_coverage.ready_for_default is False
    assert shadow_coverage.member_stats[0].eligible_session_count == 2
    assert shadow_coverage.member_stats[0].comparison_count == 2
    assert shadow_coverage.member_stats[0].missing_v022_count == 1
    assert "shadow_plan_missing_large_cap" in shadow_coverage.blocker_codes
    assert "shadow_plan_missing_monthly" in shadow_coverage.blocker_codes
    assert shadow_coverage_replay.reused is True
    assert suspended.from_lifecycle == "active"
    assert suspended_replay.reused is True
    assert resumed.from_lifecycle == "suspended"
    assert (
        lifecycle_service.current(
            enrollment.product_enrollment_id,
            as_of=datetime(2026, 8, 18, 22, tzinfo=UTC),
        )
        == "suspended"
    )
    assert (
        lifecycle_service.current(
            enrollment.product_enrollment_id,
            as_of=datetime(2026, 8, 20, 0, tzinfo=UTC),
        )
        == "active"
    )
    assert monitoring_snapshot_v1.health == "healthy"
    assert monitoring_snapshot_v2.health == "watch"
    assert monitoring_snapshot_v2.eligible_decision_count == 2
    assert monitoring_snapshot_v2.completed_decision_count == 1
    assert monitoring_snapshot_v2.missing_decision_count == 1
    assert monitoring_replay.reused is True
    assert experiment_catalog_response.status_code == 200
    # Evidence published outside an active Research Round remains directly
    # readable for audit/Product lineage, but is not shown in the active-round
    # experiment catalog.
    assert experiment_catalog_response.json()["items"] == []
    # This legacy identity-only fixture does not publish a typed Portfolio Cell
    # runtime result, so it is intentionally excluded from the v0.22 backtest
    # detail projection.
    assert experiment_detail_response.status_code == 404
    assert product_catalog_response.status_code == 200
    assert product_catalog_response.json()["items"][0]["health"] == "watch"
    assert product_detail_response.status_code == 200
    assert product_detail_response.json()["latest_decision"]["decision_status"] == "missing"
    assert len(product_detail_response.json()["monitoring_snapshots"]) == 2


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_expired_change_preview_cannot_be_confirmed() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    _publish_workspace_context(engine)
    publish_catalog_release(engine, MANIFEST, context=CONTEXT)
    service = GraphDraftService(engine, GraphWorkspacePreviewService.from_manifest(MANIFEST))
    draft = service.create(
        researcher_key="expiry_researcher",
        draft_key="expiry",
        name="Expiry",
        idempotency_key=uuid.uuid4(),
    )
    selected = service.apply_event(
        draft.graph_draft_id,
        expected_revision=1,
        actor_key="expiry_researcher",
        idempotency_key=uuid.uuid4(),
        event_type="select_feature_occurrence",
        event={"feature_key": "return_continuation__w120", "stage_no": 3},
    )
    preview = service.preview_cascade_deselect(
        draft.graph_draft_id,
        expected_revision=selected.snapshot.revision,
        actor_key="expiry_researcher",
        feature_key="adjusted_close",
        stage_no=0,
        ttl=timedelta(seconds=-1),
    )
    with pytest.raises(ChangePreviewExpired):
        service.confirm_change_preview(
            draft.graph_draft_id,
            preview.impact_token,
            expected_revision=selected.snapshot.revision,
            actor_key="expiry_researcher",
            idempotency_key=uuid.uuid4(),
        )
    engine.dispose()
