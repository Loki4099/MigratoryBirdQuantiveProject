from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from style_rotation.lineage.service import ArtifactService
from style_rotation.persistence.database import downgrade_database, reset_database, upgrade_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.compiler_service import GraphCompilerService
from style_rotation.v022.dag import ClaimedGraphWork, GraphDagService, WorkPlan
from style_rotation.v022.graph import (
    AggregationSelection,
    DraftIntent,
    FeatureSelection,
)
from style_rotation.v022.publication import (
    CatalogPublicationContext,
    publish_catalog_release,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]
MANIFEST = PROJECT_ROOT / "v0.22" / "catalogs" / "releases" / "catalog_release.v0.22.0.json"
CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_three_work_dag_ready_gate_claim_has_no_race_and_completes() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
    graph_id = _insert_minimal_graph(engine, release.catalog_release_id)
    service = GraphDagService(engine)
    plans = (
        WorkPlan("node", "stage1", "1" * 64),
        WorkPlan("node", "stage2", "2" * 64, ("stage1",)),
        WorkPlan("aggregation", "aggregate", "3" * 64, ("stage2",)),
    )
    planned = service.plan_run(
        compiled_research_graph_id=graph_id,
        requested_by="m2_test",
        requested_range={"start": "2020-01-01", "end": "2020-12-31"},
        environment_fingerprint="4" * 64,
        work=plans,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda worker: service.claim(planned.graph_run_id, worker_key=worker),
                ("worker-a", "worker-b"),
            )
        )
    claimed = [item for item in claims if item is not None]
    assert len(claimed) == 1
    assert claimed[0].work_kind == "node"
    first_worker = "worker-a" if claims[0] is not None else "worker-b"
    service.finish(claimed[0], worker_key=first_worker, status="completed")

    second = service.claim(planned.graph_run_id, worker_key="worker-c")
    assert second is not None and second.work_kind == "node"
    service.finish(second, worker_key="worker-c", status="completed")
    third = service.claim(planned.graph_run_id, worker_key="worker-c")
    assert third is not None and third.work_kind == "aggregation"
    service.finish(third, worker_key="worker-c", status="completed")

    with engine.connect() as connection:
        status = connection.scalar(
            text("SELECT status FROM workspace.v022_graph_run WHERE graph_run_id=:id"),
            {"id": planned.graph_run_id},
        )
        counts = connection.execute(
            text(
                "SELECT count(*),count(DISTINCT graph_work_item_id) "
                "FROM workspace.v022_graph_work_consumer WHERE graph_run_id=:id"
            ),
            {"id": planned.graph_run_id},
        ).one()
    engine.dispose()
    assert status == "completed"
    assert counts == (3, 3)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_failure_propagates_and_stale_fence_is_rejected() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
    graph_id = _insert_minimal_graph(engine, release.catalog_release_id)
    service = GraphDagService(engine)
    planned = service.plan_run(
        compiled_research_graph_id=graph_id,
        requested_by="m2_test",
        requested_range={},
        environment_fingerprint="5" * 64,
        work=(
            WorkPlan("node", "upstream", "6" * 64),
            WorkPlan("aggregation", "downstream", "7" * 64, ("upstream",)),
        ),
    )
    claim = service.claim(planned.graph_run_id, worker_key="worker")
    assert claim is not None
    with pytest.raises(DBAPIError, match="fencing token"):
        service.finish(
            type(claim)(claim.graph_work_item_id, claim.fencing_token + 1, claim.work_kind),
            worker_key="worker",
            status="completed",
        )
    service.finish(claim, worker_key="worker", status="failed", details={"reason": "test"})
    with engine.connect() as connection:
        statuses = tuple(
            connection.scalars(
                text(
                    "SELECT status FROM workspace.v022_graph_work_item "
                    "WHERE graph_work_item_id=ANY(:ids) ORDER BY status"
                ),
                {"ids": list(planned.work_item_ids)},
            )
        )
        run_status = connection.scalar(
            text("SELECT status FROM workspace.v022_graph_run WHERE graph_run_id=:id"),
            {"id": planned.graph_run_id},
        )
    engine.dispose()
    assert statuses == ("blocked_upstream_failed", "failed")
    assert run_status == "failed"


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_m1_database_upgrades_additively_to_m2_runtime() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    downgrade_database(DATABASE_URL, "20260810_50_v022_release")
    engine = create_postgres_engine(DATABASE_URL)
    marker = ArtifactService(engine).publish(
        artifact_type="m2_additive_upgrade_marker",
        artifact_key="m1_history",
        version_number=1,
        semantic_payload={"legacy": True},
        content_payload={"legacy": True},
    )
    engine.dispose()

    upgrade_database(DATABASE_URL)
    engine = create_postgres_engine(DATABASE_URL)
    with engine.connect() as connection:
        preserved = connection.scalar(
            text("SELECT count(*) FROM lineage.artifact WHERE artifact_id=:id"),
            {"id": marker.artifact_id},
        )
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        runtime_tables = connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE "
                "(table_schema,table_name) IN "
                "(('workspace','v022_graph_run'),('processing','node_run'),"
                "('aggregation','aggregation_run'))"
            )
        )
    engine.dispose()
    assert preserved == 1
    assert revision == "20260821_142_asset_export"
    assert runtime_tables == 3


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_draft_compile_rejection_is_audited_and_raw_is_not_silently_aggregated() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
    service = GraphCompilerService(engine)
    intent = DraftIntent(
        catalog_release_fingerprint=release.release_fingerprint,
        asset_context_fingerprint="a" * 64,
        resolved_data_binding_fingerprint="b" * 64,
        frequency="weekly",
        aggregation_inputs=("adjusted_close",),
        explicit_features=(FeatureSelection(feature_key="adjusted_close", visible_stage=3),),
        aggregations=(AggregationSelection(family_key="single_signal_identity"),),
        strategy_keys=("cross_section_rank_top_k_parity",),
        defense_keys=("none",),
    )
    draft = service.create_draft(
        catalog_release_id=release.catalog_release_id,
        draft_key="m2_raw_contract_rejection",
        intent=intent,
        actor_key="m2_test",
    )
    with pytest.raises(
        ValueError,
        match="Feature adjusted_close is not published for projection to stage 3",
    ):
        service.compile(draft.draft_intent_id)
    with engine.connect() as connection:
        attempt = connection.execute(
            text(
                "SELECT status,compiled_research_graph_id,diagnostics "
                "FROM workspace.v022_compile_attempt WHERE draft_intent_id=:draft"
            ),
            {"draft": draft.draft_intent_id},
        ).one()
    engine.dispose()
    assert attempt.status == "rejected"
    assert attempt.compiled_research_graph_id is None
    assert attempt.diagnostics[0]["reason_code"] == "contract_rejected"


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_shared_work_is_cancelled_only_after_the_last_consumer_releases() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
    graph_id = _insert_minimal_graph(engine, release.catalog_release_id)
    service = GraphDagService(engine)
    work = (WorkPlan("node", "shared", "c" * 64),)
    first = service.plan_run(
        compiled_research_graph_id=graph_id,
        requested_by="first",
        requested_range={"branch": 1},
        environment_fingerprint="d" * 64,
        work=work,
    )
    second = service.plan_run(
        compiled_research_graph_id=graph_id,
        requested_by="second",
        requested_range={"branch": 2},
        environment_fingerprint="d" * 64,
        work=work,
    )
    assert first.work_item_ids == second.work_item_ids

    service.cancel_run(first.graph_run_id)
    with engine.connect() as connection:
        still_shared = connection.scalar(
            text(
                "SELECT status FROM workspace.v022_graph_work_item "
                "WHERE graph_work_item_id=:id"
            ),
            {"id": first.work_item_ids[0]},
        )
    assert still_shared == "queued"

    service.cancel_run(second.graph_run_id)
    with engine.connect() as connection:
        finally_cancelled = connection.scalar(
            text(
                "SELECT status FROM workspace.v022_graph_work_item "
                "WHERE graph_work_item_id=:id"
            ),
            {"id": first.work_item_ids[0]},
        )
    engine.dispose()
    assert finally_cancelled == "cancelled"


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_completed_work_reuse_finishes_new_run_without_worker_claim() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
    graph_id = _insert_minimal_graph(engine, release.catalog_release_id)
    service = GraphDagService(engine)
    work = (WorkPlan("node", "cached", "e" * 64),)
    first = service.plan_run(
        compiled_research_graph_id=graph_id,
        requested_by="first",
        requested_range={"run": 1},
        environment_fingerprint="f" * 64,
        work=work,
    )
    claim = service.claim(first.graph_run_id, worker_key="worker")
    assert claim is not None
    service.finish(claim, worker_key="worker", status="completed")

    second = service.plan_run(
        compiled_research_graph_id=graph_id,
        requested_by="second",
        requested_range={"run": 2},
        environment_fingerprint="f" * 64,
        work=work,
    )
    with engine.connect() as connection:
        second_status = connection.scalar(
            text("SELECT status FROM workspace.v022_graph_run WHERE graph_run_id=:id"),
            {"id": second.graph_run_id},
        )
        item_status = connection.scalar(
            text(
                "SELECT status FROM workspace.v022_graph_work_item "
                "WHERE graph_work_item_id=:id"
            ),
            {"id": second.work_item_ids[0]},
        )
    engine.dispose()
    assert first.work_item_ids == second.work_item_ids
    assert second_status == "completed"
    assert item_status == "reused"


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_terminal_failed_work_requeues_and_expired_lease_is_fenced() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    release = publish_catalog_release(engine, MANIFEST, context=CONTEXT)
    graph_id = _insert_minimal_graph(engine, release.catalog_release_id)
    service = GraphDagService(engine)
    work = (WorkPlan("node", "retry", "a" * 64),)
    first = service.plan_run(
        compiled_research_graph_id=graph_id,
        requested_by="first",
        requested_range={"attempt": 1},
        environment_fingerprint="b" * 64,
        work=work,
    )
    failed = service.claim(first.graph_run_id, worker_key="worker-a")
    assert failed is not None
    service.finish(failed, worker_key="worker-a", status="failed")
    second = service.plan_run(
        compiled_research_graph_id=graph_id,
        requested_by="second",
        requested_range={"attempt": 2},
        environment_fingerprint="b" * 64,
        work=work,
    )
    reclaimed = service.claim(second.graph_run_id, worker_key="worker-b", lease_seconds=1)
    assert reclaimed is not None and reclaimed.fencing_token > failed.fencing_token
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workspace.v022_graph_work_item "
                "SET lease_expires_at=now()-interval '1 second' "
                "WHERE graph_work_item_id=:id"
            ),
            {"id": reclaimed.graph_work_item_id},
        )
    recovered = service.claim(second.graph_run_id, worker_key="worker-c")
    assert recovered is not None and recovered.fencing_token > reclaimed.fencing_token
    with pytest.raises(DBAPIError, match="fencing token"):
        service.finish(reclaimed, worker_key="worker-b", status="failed")
    service.finish(recovered, worker_key="worker-c", status="completed")
    engine.dispose()


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_typed_work_cannot_complete_without_published_typed_output() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    item_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_graph_work_item (
                  graph_work_item_id,execution_fingerprint,work_kind,status,
                  priority,lease_owner,lease_expires_at,lease_generation,
                  fencing_token,attempt_count
                ) VALUES (
                  :id,:fingerprint,'strategy_target','running',100,
                  'typed-worker',now()+interval '5 minutes',1,1,1
                )
                """
            ),
            {"id": item_id, "fingerprint": "d" * 64},
        )
    with pytest.raises(DBAPIError, match="published output"):
        GraphDagService(engine).finish(
            ClaimedGraphWork(item_id, 1, "strategy_target"),
            worker_key="typed-worker",
            status="completed",
        )
    with engine.connect() as connection:
        status = connection.scalar(
            text(
                "SELECT status FROM workspace.v022_graph_work_item "
                "WHERE graph_work_item_id=:id"
            ),
            {"id": item_id},
        )
    engine.dispose()
    assert status == "running"


def _insert_minimal_graph(engine: object, release_id: uuid.UUID) -> uuid.UUID:
    artifact_id = uuid.uuid4()
    graph_id = uuid.uuid4()
    with engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "INSERT INTO lineage.artifact "
                "(artifact_id,artifact_type,artifact_key,version_number,status) "
                "VALUES (:id,'m2_test_graph',:key,1,'draft')"
            ),
            {"id": artifact_id, "key": str(graph_id)},
        )
        connection.execute(
            text(
                """
                INSERT INTO workspace.compiled_research_graph (
                  compiled_research_graph_id,artifact_id,graph_fingerprint,contract_version,
                  compiler_version,catalog_release_id,asset_context_fingerprint,
                  resolved_data_binding_fingerprint,frequency,normalized_graph,node_count,
                  occurrence_count,edge_count,projection_count,aggregation_instance_count,
                  strategy_branch_count
                ) VALUES (:id,:artifact,:fingerprint,'v0.22.0','m2-test',:release,:asset,:binding,
                          'daily','{}'::jsonb,0,1,0,0,1,1)
                """
            ),
            {
                "id": graph_id,
                "artifact": artifact_id,
                "fingerprint": uuid.uuid4().hex * 2,
                "release": release_id,
                "asset": "8" * 64,
                "binding": "9" * 64,
            },
        )
    return graph_id
