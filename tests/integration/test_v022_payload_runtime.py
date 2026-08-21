from __future__ import annotations

import io
import json
import os
import uuid
from datetime import date, timedelta
from functools import partial
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022 import payload_runtime
from style_rotation.v022.incremental_runtime import (
    IncrementalExecutionContract,
    OutputPartition,
    PriorPartition,
    plan_incremental_run,
    record_partition_plan,
)
from style_rotation.v022.payload_runtime import (
    ExecutedPartitionPayload,
    LocalPayloadObjectStore,
    NodeOutputPayload,
    publish_node_output,
    publish_node_output_bundle,
)
from style_rotation.v022.publication import (
    CatalogPublicationContext,
    publish_catalog_release,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
ROOT = Path(__file__).parents[2]
CATALOG = ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.2.json"
CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_append_publishes_new_manifest_reusing_immutable_old_partitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    publish_catalog_release(engine, CATALOG, context=CONTEXT)
    node_version_id, node_version_artifact_id, output_port = _publish_windowed_node(engine)
    contract = IncrementalExecutionContract("windowed", ("asset_id",), lookback=20)
    sessions = tuple(date(2026, 1, 5) + timedelta(days=index) for index in range(9))
    revisions = {session: sha256_hexdigest(("raw", session)) for session in sessions}
    store = LocalPayloadObjectStore(tmp_path / "payloads")

    first_plan = plan_incremental_run(
        contract=contract,
        partitions=_partitions(sessions[:6]),
        source_revisions={session: revisions[session] for session in sessions[:6]},
    )
    first_run = _publish_node_run(engine, node_version_id, node_version_artifact_id)
    record_partition_plan(engine, node_run_id=first_run, plan=first_plan)
    first = publish_node_output(
        engine,
        object_store=store,
        node_run_id=first_run,
        output_port_key=output_port,
        plan=first_plan,
        executed_payloads=tuple(
            _payload(item, index) for index, item in enumerate(first_plan.partitions)
        ),
    )
    prior = tuple(
        PriorPartition(
            work.partition_key_hash,
            work.source_revision_fingerprint,
            str(partition_id),
        )
        for work, partition_id in zip(
            first_plan.partitions, first.payload_partition_ids, strict=True
        )
    )
    second_plan = plan_incremental_run(
        contract=contract,
        partitions=_partitions(sessions),
        source_revisions=revisions,
        prior_partitions=prior,
    )
    second_run = _publish_node_run(engine, node_version_id, node_version_artifact_id)
    record_partition_plan(engine, node_run_id=second_run, plan=second_plan)
    second = publish_node_output(
        engine,
        object_store=store,
        node_run_id=second_run,
        output_port_key=output_port,
        plan=second_plan,
        executed_payloads=(_payload(second_plan.partitions[-1], 2),),
    )
    retry = publish_node_output(
        engine,
        object_store=store,
        node_run_id=second_run,
        output_port_key=output_port,
        plan=second_plan,
        executed_payloads=(_payload(second_plan.partitions[-1], 2),),
    )

    assert first.payload_manifest_id != second.payload_manifest_id
    assert second.payload_partition_ids[:2] == first.payload_partition_ids
    assert second.executed_partition_count == 1
    assert second.reused_partition_count == 2
    assert retry.reused_publication is True
    assert retry.payload_manifest_id == second.payload_manifest_id

    with engine.connect() as connection:
        counts = connection.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM data.payload_object),
                  (SELECT count(*) FROM data.payload_partition),
                  (SELECT count(*) FROM data.payload_manifest),
                  (SELECT count(*) FROM data.payload_manifest_partition
                    WHERE payload_manifest_id=:first_manifest),
                  (SELECT count(*) FROM data.payload_manifest_partition
                    WHERE payload_manifest_id=:second_manifest),
                  (SELECT count(*) FROM lineage.artifact_dependency
                    WHERE artifact_id=:second_artifact
                      AND role='reused_payload_manifest')
                """
            ),
            {
                "first_manifest": first.payload_manifest_id,
                "second_manifest": second.payload_manifest_id,
                "second_artifact": second.manifest_artifact_id,
            },
        ).one()
        second_statuses = (
            connection.execute(
                text(
                    """
                SELECT status FROM processing.node_run_partition
                WHERE node_run_id=:node_run_id ORDER BY partition_document->'output_range'->>'start'
                """
                ),
                {"node_run_id": second_run},
            )
            .scalars()
            .all()
        )
    assert counts == (3, 3, 2, 2, 3, 1)
    assert second_statuses == ["reused", "reused", "completed"]
    assert len(list((tmp_path / "payloads" / "sha256").glob("*.parquet"))) == 3

    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE data.payload_partition SET row_or_item_count=0 "
                "WHERE payload_partition_id=:partition_id"
            ),
            {"partition_id": first.payload_partition_ids[0]},
        )

    with engine.connect() as connection:
        multi_rows = (
            connection.execute(
                text(
                    """
                SELECT version.node_version_id,version.artifact_id,
                       version.execution_contract,port.port_key
                FROM processing.node_version version
                JOIN processing.node_variant variant
                  ON variant.node_variant_id=version.node_variant_id
                JOIN processing.node_port port ON port.node_version_id=version.node_version_id
                WHERE variant.variant_key='amihud_daily_primitives__canonical'
                  AND port.direction='output'
                ORDER BY port.ordinal
                """
                )
            )
            .mappings()
            .all()
        )
    multi = multi_rows[0]
    multi_document = multi["execution_contract"]
    multi_contract = IncrementalExecutionContract(
        execution_mode=multi_document["execution_mode"],
        partition_key=tuple(multi_document["partition_key"]),
        lookback=multi_document["lookback"],
        lookforward=multi_document["lookforward"],
        revision_impact_policy=multi_document["revision_impact_policy"],
    )
    multi_run = _publish_node_run(engine, multi["node_version_id"], multi["artifact_id"])
    multi_plan = plan_incremental_run(
        contract=multi_contract,
        partitions=(OutputPartition({"asset_id": "A"}, sessions[:3]),),
        source_revisions={session: revisions[session] for session in sessions[:3]},
    )
    record_partition_plan(engine, node_run_id=multi_run, plan=multi_plan)
    with pytest.raises(ValueError, match="atomic output bundle"):
        publish_node_output(
            engine,
            object_store=store,
            node_run_id=multi_run,
            output_port_key=multi["port_key"],
            plan=multi_plan,
            executed_payloads=(_payload(multi_plan.partitions[0], 0),),
        )
    multi_outputs = tuple(
        NodeOutputPayload(
            row["port_key"],
            (_payload(multi_plan.partitions[0], ordinal + 10),),
        )
        for ordinal, row in enumerate(multi_rows)
    )
    multi_bundle = publish_node_output_bundle(
        engine,
        object_store=store,
        node_run_id=multi_run,
        plan=multi_plan,
        outputs=multi_outputs,
    )
    multi_retry = publish_node_output_bundle(
        engine,
        object_store=store,
        node_run_id=multi_run,
        plan=multi_plan,
        outputs=multi_outputs,
    )
    assert len(multi_bundle.outputs) == 3
    assert multi_retry.reused_publication is True
    assert multi_retry.node_output_bundle_id == multi_bundle.node_output_bundle_id
    with engine.connect() as connection:
        atomic_counts = connection.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM processing.node_output_bundle_member
                    WHERE node_output_bundle_id=:bundle_id),
                  (SELECT count(*) FROM processing.node_run_output
                    WHERE node_run_id=:node_run_id),
                  (SELECT status FROM processing.node_run WHERE node_run_id=:node_run_id)
                """
            ),
            {"bundle_id": multi_bundle.node_output_bundle_id, "node_run_id": multi_run},
        ).one()
    assert atomic_counts == (3, 3, "completed")
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing.node_output_bundle SET output_count=2 "
                "WHERE node_output_bundle_id=:bundle_id"
            ),
            {"bundle_id": multi_bundle.node_output_bundle_id},
        )

    failed_run = _publish_node_run(engine, multi["node_version_id"], multi["artifact_id"])
    record_partition_plan(engine, node_run_id=failed_run, plan=multi_plan)
    invalid_outputs = tuple(
        NodeOutputPayload(
            row["port_key"],
            (
                ExecutedPartitionPayload(
                    multi_plan.partitions[0].partition_key_hash,
                    b"not-parquet"
                    if ordinal == 1
                    else _payload(multi_plan.partitions[0], ordinal + 20).content,
                    {},
                ),
            ),
        )
        for ordinal, row in enumerate(multi_rows)
    )
    with pytest.raises(ValueError, match="not valid Parquet"):
        publish_node_output_bundle(
            engine,
            object_store=store,
            node_run_id=failed_run,
            plan=multi_plan,
            outputs=invalid_outputs,
        )
    with engine.connect() as connection:
        failed_counts = connection.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM processing.node_run_output
                    WHERE node_run_id=:node_run_id),
                  (SELECT count(*) FROM processing.node_output_bundle
                    WHERE node_run_id=:node_run_id),
                  (SELECT status FROM processing.node_run WHERE node_run_id=:node_run_id)
                """
            ),
            {"node_run_id": failed_run},
        ).one()
    assert failed_counts == (0, 0, "running")

    rollback_run = _publish_node_run(engine, multi["node_version_id"], multi["artifact_id"])
    record_partition_plan(engine, node_run_id=rollback_run, plan=multi_plan)
    valid_outputs = tuple(
        NodeOutputPayload(
            row["port_key"],
            (_payload(multi_plan.partitions[0], ordinal + 30),),
        )
        for ordinal, row in enumerate(multi_rows)
    )
    original_writer = payload_runtime._write_prepared_manifest
    write_count = 0

    def fail_second_manifest(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        output: object,
    ) -> uuid.UUID:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise RuntimeError("injected second-output failure")
        return original_writer(connection, artifact_id, output=output)

    monkeypatch.setattr(payload_runtime, "_write_prepared_manifest", fail_second_manifest)
    with pytest.raises(RuntimeError, match="second-output"):
        publish_node_output_bundle(
            engine,
            object_store=store,
            node_run_id=rollback_run,
            plan=multi_plan,
            outputs=valid_outputs,
        )
    with engine.connect() as connection:
        rollback_counts = connection.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM processing.node_run_output
                    WHERE node_run_id=:node_run_id),
                  (SELECT count(*) FROM processing.node_output_bundle
                    WHERE node_run_id=:node_run_id),
                  (SELECT count(*) FROM data.payload_manifest manifest
                    JOIN processing.node_run run
                      ON run.artifact_id=manifest.producer_artifact_id
                    WHERE run.node_run_id=:node_run_id),
                  (SELECT status FROM processing.node_run WHERE node_run_id=:node_run_id)
                """
            ),
            {"node_run_id": rollback_run},
        ).one()
    assert rollback_counts == (0, 0, 0, "running")
    engine.dispose()


def _partitions(sessions: tuple[date, ...]) -> tuple[OutputPartition, ...]:
    return tuple(
        OutputPartition({"asset_id": "A"}, sessions[index : index + 3])
        for index in range(0, len(sessions), 3)
    )


def _payload(work: object, ordinal: int) -> ExecutedPartitionPayload:
    assert hasattr(work, "partition_key_hash") and hasattr(work, "output_sessions")
    buffer = io.BytesIO()
    pq.write_table(
        pa.table(
            {
                "asset_id": ["A"] * len(work.output_sessions),
                "session_date": list(work.output_sessions),
                "value": [
                    float(ordinal * 10 + index) for index in range(len(work.output_sessions))
                ],
            }
        ),
        buffer,
        compression="zstd",
        use_dictionary=False,
    )
    return ExecutedPartitionPayload(
        work.partition_key_hash,
        buffer.getvalue(),
        {"ordinal": ordinal},
    )


def _publish_windowed_node(engine: Engine) -> tuple[uuid.UUID, uuid.UUID, str]:
    with engine.connect() as connection:
        base = (
            connection.execute(
                text(
                    """
                SELECT version.node_variant_id,variant.artifact_id AS variant_artifact_id,
                       version.stage_no,version.implementation_key,
                       version.implementation_version,port.payload_contract_version_id,
                       contract.artifact_id AS contract_artifact_id,port.port_key,
                       port.port_semantics
                FROM processing.node_version version
                JOIN processing.node_variant variant
                  ON variant.node_variant_id=version.node_variant_id
                JOIN processing.node_port port ON port.node_version_id=version.node_version_id
                JOIN data.payload_contract_version contract
                  ON contract.payload_contract_version_id=port.payload_contract_version_id
                WHERE variant.variant_key='total_return_node__w20'
                  AND version.version_number=1 AND port.direction='output'
                """
                )
            )
            .mappings()
            .one()
        )
    node_version_id = uuid.uuid4()
    execution = {
        "execution_mode": "windowed",
        "partition_key": ["asset_id"],
        "lookback": 20,
        "lookforward": 0,
        "revision_impact_policy": "windowed_forward",
        "watermark_policy": "completed_session",
        "checkpoint_contract": "none",
    }
    payload = {"test_incremental_version": 2, "execution_contract": execution}
    result = ArtifactService(engine).publish(
        artifact_type="v022_processing_node_version",
        artifact_key="total_return_node__w20",
        version_number=2,
        semantic_payload=payload,
        content_payload=payload,
        dependencies=(
            DependencyInput(base["variant_artifact_id"], "node_variant", 0),
            DependencyInput(base["contract_artifact_id"], "output_contract", 1),
        ),
        draft_writer=partial(
            _write_windowed_node,
            node_version_id=node_version_id,
            base=base,
            execution=execution,
            payload=payload,
        ),
    )
    return node_version_id, result.artifact_id, base["port_key"]


def _write_windowed_node(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    node_version_id: uuid.UUID,
    base: object,
    execution: dict[str, object],
    payload: dict[str, object],
) -> None:
    assert isinstance(base, dict) or hasattr(base, "__getitem__")
    connection.execute(
        text(
            """
            INSERT INTO processing.node_version (
              node_version_id,node_variant_id,artifact_id,version_number,stage_no,
              implementation_key,implementation_version,determinism_policy,cache_policy,
              execution_contract,version_fingerprint
            ) VALUES (
              :id,:variant,:artifact,2,:stage,:implementation,:implementation_version,
              'deterministic','content_addressed',CAST(:execution AS jsonb),:fingerprint
            )
            """
        ),
        {
            "id": node_version_id,
            "variant": base["node_variant_id"],
            "artifact": artifact_id,
            "stage": base["stage_no"],
            "implementation": base["implementation_key"],
            "implementation_version": base["implementation_version"],
            "execution": json.dumps(execution, sort_keys=True),
            "fingerprint": sha256_hexdigest(payload),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO processing.node_port (
              node_port_id,node_version_id,payload_contract_version_id,port_key,
              direction,ordinal,binding_cardinality,port_semantics
            ) VALUES (
              :id,:node,:contract,:port,'output',0,'required',CAST(:semantics AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "node": node_version_id,
            "contract": base["payload_contract_version_id"],
            "port": base["port_key"],
            "semantics": json.dumps(base["port_semantics"], sort_keys=True),
        },
    )


def _publish_node_run(
    engine: Engine, node_version_id: uuid.UUID, node_version_artifact_id: uuid.UUID
) -> uuid.UUID:
    node_run_id = uuid.uuid4()
    fingerprint = sha256_hexdigest(("node-run", node_run_id))
    payload = {"node_run_id": node_run_id, "execution_fingerprint": fingerprint}
    ArtifactService(engine).publish(
        artifact_type="v022_node_run",
        artifact_key=str(node_run_id),
        version_number=1,
        semantic_payload=payload,
        content_payload=payload,
        dependencies=(DependencyInput(node_version_artifact_id, "node_version", 0),),
        draft_writer=partial(
            _write_node_run,
            node_run_id=node_run_id,
            node_version_id=node_version_id,
            fingerprint=fingerprint,
        ),
    )
    return node_run_id


def _write_node_run(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    node_run_id: uuid.UUID,
    node_version_id: uuid.UUID,
    fingerprint: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO processing.node_run (
              node_run_id,artifact_id,node_version_id,execution_fingerprint,
              resolved_parameters,requested_range,executor_version,
              environment_fingerprint,status,cache_eligible,started_at
            ) VALUES (
              :run,:artifact,:node,:fingerprint,'{}'::jsonb,'{}'::jsonb,
              'integration-test',:environment,'running',true,now()
            )
            """
        ),
        {
            "run": node_run_id,
            "artifact": artifact_id,
            "node": node_version_id,
            "fingerprint": fingerprint,
            "environment": sha256_hexdigest("integration-test"),
        },
    )
