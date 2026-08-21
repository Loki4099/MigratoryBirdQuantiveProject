from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from style_rotation.lineage.service import ArtifactService
from style_rotation.ops.idempotency import CommandIdempotencyService
from style_rotation.persistence.database import create_postgres_engine, reset_database
from style_rotation.v022.incremental_runtime import (
    IncrementalExecutionContract,
    plan_incremental_run,
    record_partition_plan,
)
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore, publish_node_output
from style_rotation.v022.publication import publish_catalog_release
from style_rotation.v022.recovery_drill import (
    RestoreDrillService,
    RollbackDrillService,
    RollbackProbeService,
)
from style_rotation.v022.release_control import ReleaseControlService
from tests.integration.test_v022_payload_runtime import (
    CATALOG,
    CONTEXT,
    _partitions,
    _payload,
    _publish_node_run,
    _publish_windowed_node,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_recovery_drills_fail_closed_and_publish_typed_rollback_evidence(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    now = datetime.now(UTC)
    backup_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO ops.backup_record (
              backup_record_id,system_version,schema_revision,git_commit,dump_sha256,
              storage_reference,byte_count,status,verified_at,restore_tested_at
            ) VALUES (:id,'0.22.0','20260821_142_asset_export','abcdef1',:hash,
                      'controlled-test.dump',100,'restore_tested',:now,:now)
        """), {"id": backup_id, "hash": "a" * 64, "now": now})

    restore = RestoreDrillService(engine).publish(
        backup_record_id=backup_id,
        observations=(),
        started_at=now - timedelta(minutes=1),
        completed_at=now + timedelta(minutes=1),
    )
    assert restore.ready_for_gate is False
    assert restore.blocker_codes == ("no_materialized_strong_root_objects",)
    with pytest.raises(ValueError, match="not ready for release"):
        ReleaseControlService(engine)._published_evidence(  # noqa: SLF001
            {"restore_drill_artifact_id": restore.artifact_id}
        )

    publish_catalog_release(engine, CATALOG, context=CONTEXT)
    node_version_id, node_artifact_id, output_port = _publish_windowed_node(engine)
    sessions = (date(2026, 8, 10),)
    plan = plan_incremental_run(
        contract=IncrementalExecutionContract("windowed", ("asset_id",), lookback=20),
        partitions=_partitions(sessions),
        source_revisions={sessions[0]: "b" * 64},
    )
    node_run_id = _publish_node_run(engine, node_version_id, node_artifact_id)
    record_partition_plan(engine, node_run_id=node_run_id, plan=plan)
    source_store = LocalPayloadObjectStore(tmp_path / "source_payloads")
    publish_node_output(
        engine,
        object_store=source_store,
        node_run_id=node_run_id,
        output_port_key=output_port,
        plan=plan,
        executed_payloads=(_payload(plan.partitions[0], 0),),
        retention_class="product",
    )
    missing_restore = RestoreDrillService(engine).publish_restored_store(
        backup_record_id=backup_id,
        restored_object_store=LocalPayloadObjectStore(tmp_path / "missing_restored_payloads"),
        started_at=now - timedelta(minutes=1),
        completed_at=now + timedelta(minutes=1),
    )
    assert missing_restore.ready_for_gate is False
    assert len(missing_restore.blocker_codes) == 1
    assert missing_restore.blocker_codes[0].startswith("missing_object:")

    source_object = next((tmp_path / "source_payloads" / "sha256").iterdir())
    LocalPayloadObjectStore(tmp_path / "restored_payloads").publish(
        source_object.read_bytes(), file_extension="parquet"
    )
    verified_restore = RestoreDrillService(engine).publish_restored_store(
        backup_record_id=backup_id,
        restored_object_store=LocalPayloadObjectStore(tmp_path / "restored_payloads"),
        started_at=now - timedelta(minutes=1),
        completed_at=now + timedelta(minutes=1),
    )
    assert verified_restore.ready_for_gate is True
    validated_restore = ReleaseControlService(engine)._published_evidence(  # noqa: SLF001
        {"restore_drill_artifact_id": verified_restore.artifact_id}
    )
    assert validated_restore["restore_drill_artifact_id"]["artifact_id"] == (
        verified_restore.artifact_id
    )

    shadow_plan = ArtifactService(engine).publish(
        artifact_type="v022_shadow_plan",
        artifact_key="recovery_drill_shadow_plan",
        version_number=1,
        semantic_payload={"plan": "controlled recovery drill"},
        content_payload={"plan": "controlled recovery drill"},
    )
    legacy_probe = ArtifactService(engine).publish(
        artifact_type="v021_workspace_probe",
        artifact_key="rollback_read_probe",
        version_number=1,
        semantic_payload={"version": "0.21", "identity": "frozen"},
        content_payload={"version": "0.21", "identity": "frozen"},
    )
    replay_command_name = "rollback_exact_replay_probe"
    replay_idempotency_key = uuid.uuid4()
    replay_request = {"artifact_id": str(legacy_probe.artifact_id), "mode": "exact"}
    replay_response = {"artifact_id": str(legacy_probe.artifact_id), "reused": True}
    CommandIdempotencyService(engine).execute(
        command_name=replay_command_name,
        idempotency_key=replay_idempotency_key,
        request=replay_request,
        operation=lambda: replay_response,
    )
    release = ReleaseControlService(engine)
    release.transition(
        target="shadow",
        reason_code="controlled_drill_setup",
        reason="enter shadow before rollback drill",
        requested_by="integration_test",
        gate_evidence={"shadow_plan_artifact_id": shadow_plan.artifact_id},
    )
    transition = release.transition(
        target="maintenance_read_only",
        reason_code="controlled_rollback_drill",
        reason="verify submissions stop without duplicating decisions",
        requested_by="integration_test",
        incident={"incident_id": "DRILL-022", "impact_summary": "controlled drill"},
    )
    failed_probe = RollbackProbeService(engine).run(
        rollback_transition_artifact_id=transition.artifact_id,
        v021_artifact_id=legacy_probe.artifact_id,
        replay_command_name=replay_command_name,
        replay_idempotency_key=replay_idempotency_key,
        replay_request={**replay_request, "mode": "changed"},
    )
    assert failed_probe.v021_read_probe_passed is True
    assert failed_probe.v022_submission_rejected is True
    assert failed_probe.exact_pinned_replay_passed is False

    rollback = RollbackDrillService(engine).publish_verified(
        rollback_transition_artifact_id=transition.artifact_id,
        v021_artifact_id=legacy_probe.artifact_id,
        replay_command_name=replay_command_name,
        replay_idempotency_key=replay_idempotency_key,
        replay_request=replay_request,
    )
    assert rollback.ready_for_gate is True
    validated = release._published_evidence(  # noqa: SLF001
        {"rollback_drill_artifact_id": rollback.artifact_id}
    )
    assert validated["rollback_drill_artifact_id"]["artifact_id"] == rollback.artifact_id

    with engine.begin() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("""
            UPDATE ops.v022_rollback_drill_snapshot SET ready_for_gate=false
            WHERE rollback_drill_snapshot_id=:id
        """), {"id": rollback.rollback_drill_snapshot_id})
    engine.dispose()
