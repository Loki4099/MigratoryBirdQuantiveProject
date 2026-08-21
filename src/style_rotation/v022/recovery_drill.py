from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import partial

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.ops.idempotency import CommandIdempotencyService
from style_rotation.v022.mutation_admission import MutationAdmissionService
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore


@dataclass(frozen=True, slots=True)
class RestoredObjectObservation:
    payload_object_id: uuid.UUID
    observed_content_hash: str
    observed_byte_size: int


@dataclass(frozen=True, slots=True)
class RestoredObjectResult:
    ordinal: int
    payload_manifest_id: uuid.UUID
    manifest_artifact_id: uuid.UUID
    payload_object_id: uuid.UUID
    expected_content_hash: str
    observed_content_hash: str | None
    expected_byte_size: int
    observed_byte_size: int | None
    passed: bool
    blocker_code: str | None


@dataclass(frozen=True, slots=True)
class RestoreDrillPublication:
    restore_drill_snapshot_id: uuid.UUID
    artifact_id: uuid.UUID
    drill_fingerprint: str
    ready_for_gate: bool
    blocker_codes: tuple[str, ...]
    results: tuple[RestoredObjectResult, ...]
    reused: bool


@dataclass(frozen=True, slots=True)
class RollbackProbe:
    v021_read_probe_passed: bool
    v022_submission_rejected: bool
    exact_pinned_replay_passed: bool
    probe_document: dict[str, object]


@dataclass(frozen=True, slots=True)
class RollbackDrillPublication:
    rollback_drill_snapshot_id: uuid.UUID
    artifact_id: uuid.UUID
    drill_fingerprint: str
    ready_for_gate: bool
    blocker_codes: tuple[str, ...]
    reused: bool


class RollbackProbeService:
    """Run rollback assertions from persisted facts without trusting boolean input."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def run(
        self,
        *,
        rollback_transition_artifact_id: uuid.UUID,
        v021_artifact_id: uuid.UUID,
        replay_command_name: str,
        replay_idempotency_key: uuid.UUID,
        replay_request: dict[str, object],
    ) -> RollbackProbe:
        request_fingerprint = sha256_hexdigest(replay_request)
        with self._engine.connect() as connection:
            transition = connection.execute(text("""
                SELECT transition.artifact_id,transition.to_state,transition.requested_at
                FROM workspace.v022_release_transition transition
                JOIN lineage.artifact artifact ON artifact.artifact_id=transition.artifact_id
                WHERE transition.artifact_id=:artifact AND artifact.status='published'
            """), {"artifact": rollback_transition_artifact_id}).mappings().one_or_none()
            artifact = connection.execute(text("""
                SELECT artifact_id,artifact_type,artifact_key,version_number,status,
                       semantic_fingerprint,content_hash,published_at
                FROM lineage.artifact WHERE artifact_id=:artifact
            """), {"artifact": v021_artifact_id}).mappings().one_or_none()
            replay_row = connection.execute(text("""
                SELECT request_fingerprint,response,created_at
                FROM ops.command_result
                WHERE command_name=:name AND idempotency_key=:key
            """), {
                "name": replay_command_name,
                "key": replay_idempotency_key,
            }).mappings().one_or_none()

        rollback_at = transition["requested_at"] if transition is not None else None
        transition_valid = bool(
            transition is not None and transition["to_state"] == "maintenance_read_only"
        )
        v021_read_passed = bool(
            transition_valid
            and rollback_at is not None
            and artifact is not None
            and artifact["status"] == "published"
            and not str(artifact["artifact_type"]).startswith("v022_")
            and artifact["semantic_fingerprint"]
            and artifact["content_hash"]
            and artifact["published_at"] <= rollback_at
        )
        admission = MutationAdmissionService(self._engine).decide("v022_research")
        v022_rejected = (
            not admission.allowed
            and admission.release_state == "maintenance_read_only"
            and admission.reason_code == "release_maintenance_read_only"
        )

        exact_record = bool(
            transition_valid
            and rollback_at is not None
            and replay_row is not None
            and replay_row["request_fingerprint"] == request_fingerprint
            and replay_row["response"] is not None
            and replay_row["created_at"] <= rollback_at
        )
        replay_passed = False
        replay_response_hash: str | None = None
        if exact_record:
            assert replay_row is not None
            operation_executed = False

            def forbidden_operation() -> dict[str, object]:
                nonlocal operation_executed
                operation_executed = True
                raise RuntimeError("Pinned replay attempted to execute its business operation")

            replayed = CommandIdempotencyService(self._engine).execute(
                command_name=replay_command_name,
                idempotency_key=replay_idempotency_key,
                request=replay_request,
                operation=forbidden_operation,
            )
            replay_response_hash = sha256_hexdigest(replayed)
            replay_passed = not operation_executed and replayed == dict(replay_row["response"])

        return RollbackProbe(
            v021_read_probe_passed=v021_read_passed,
            v022_submission_rejected=v022_rejected,
            exact_pinned_replay_passed=replay_passed,
            probe_document={
                "rollback_transition": (
                    {
                        "artifact_id": str(transition["artifact_id"]),
                        "to_state": transition["to_state"],
                        "requested_at": transition["requested_at"],
                    }
                    if transition is not None
                    else {
                        "artifact_id": str(rollback_transition_artifact_id),
                        "missing": True,
                    }
                ),
                "v021_artifact": (
                    {
                        "artifact_id": str(artifact["artifact_id"]),
                        "artifact_type": artifact["artifact_type"],
                        "artifact_key": artifact["artifact_key"],
                        "version_number": artifact["version_number"],
                        "semantic_fingerprint": artifact["semantic_fingerprint"],
                        "content_hash": artifact["content_hash"],
                    }
                    if artifact is not None
                    else {"artifact_id": str(v021_artifact_id), "missing": True}
                ),
                "v022_mutation_admission": {
                    "scope": admission.scope,
                    "release_state": admission.release_state,
                    "allowed": admission.allowed,
                    "reason_code": admission.reason_code,
                },
                "exact_pinned_replay": {
                    "command_name": replay_command_name,
                    "idempotency_key": str(replay_idempotency_key),
                    "request_fingerprint": request_fingerprint,
                    "record_found": replay_row is not None,
                    "record_exact": exact_record,
                    "response_hash": replay_response_hash,
                },
            },
        )


def evaluate_restored_objects(
    inventory: tuple[RowMapping, ...],
    observations: tuple[RestoredObjectObservation, ...],
) -> tuple[tuple[RestoredObjectResult, ...], tuple[str, ...]]:
    observed = {item.payload_object_id: item for item in observations}
    if len(observed) != len(observations):
        raise ValueError("Restore drill observations contain duplicate Payload Objects")
    expected_ids = {row["payload_object_id"] for row in inventory}
    unexpected = sorted(str(item) for item in set(observed) - expected_ids)
    if unexpected:
        raise ValueError(f"Restore drill contains unexpected Payload Objects: {unexpected}")
    blockers: list[str] = []
    results: list[RestoredObjectResult] = []
    if not inventory:
        blockers.append("no_materialized_strong_root_objects")
    for ordinal, row in enumerate(inventory, start=1):
        observation = observed.get(row["payload_object_id"])
        blocker: str | None = None
        if observation is None:
            blocker = f"missing_object:{row['payload_object_id']}"
        elif observation.observed_content_hash != row["object_content_hash"]:
            blocker = f"content_hash_mismatch:{row['payload_object_id']}"
        elif observation.observed_byte_size != row["byte_size"]:
            blocker = f"byte_size_mismatch:{row['payload_object_id']}"
        elif row["object_state"] != "published" or row["verification_status"] != "verified":
            blocker = f"source_object_not_verified:{row['payload_object_id']}"
        if blocker is not None:
            blockers.append(blocker)
        results.append(
            RestoredObjectResult(
                ordinal,
                row["payload_manifest_id"],
                row["manifest_artifact_id"],
                row["payload_object_id"],
                row["object_content_hash"],
                observation.observed_content_hash if observation else None,
                row["byte_size"],
                observation.observed_byte_size if observation else None,
                blocker is None,
                blocker,
            )
        )
    return tuple(results), tuple(blockers)


class RestoreDrillService:
    """Publish DB restore plus exhaustive strong-root Object Store verification."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        backup_record_id: uuid.UUID,
        observations: tuple[RestoredObjectObservation, ...],
        started_at: datetime,
        completed_at: datetime | None = None,
    ) -> RestoreDrillPublication:
        ended_at = completed_at or datetime.now(UTC)
        if started_at.tzinfo is None or ended_at.tzinfo is None or started_at >= ended_at:
            raise ValueError("Restore drill requires a valid timezone-aware interval")
        backup = self._backup(backup_record_id)
        if backup["status"] != "restore_tested" or backup["restore_tested_at"] is None:
            raise ValueError("Restore drill requires a successfully restore-tested DB backup")
        inventory = self._strong_object_inventory()
        results, blockers = evaluate_restored_objects(inventory, observations)
        if not (started_at <= backup["restore_tested_at"] <= ended_at):
            blockers = (*blockers, "database_restore_outside_drill_window")
        document = {
            "contract_version": "v0.22.0",
            "backup_record_id": str(backup_record_id),
            "schema_revision": backup["schema_revision"],
            "git_commit": backup["git_commit"],
            "dump_sha256": backup["dump_sha256"],
            "started_at": started_at,
            "completed_at": ended_at,
            "ready_for_gate": not blockers,
            "blocker_codes": list(blockers),
            "results": [asdict(item) for item in results],
        }
        fingerprint = sha256_hexdigest(document)
        snapshot_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:restore-drill:{fingerprint}")
        manifest_artifact_ids = tuple(
            sorted({row["manifest_artifact_id"] for row in inventory}, key=str)
        )
        dependencies = tuple(
            DependencyInput(artifact_id, "strong_root_manifest", ordinal)
            for ordinal, artifact_id in enumerate(manifest_artifact_ids)
        )
        publication = self._artifacts.publish(
            artifact_type="v022_restore_drill_evidence",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=document,
            content_payload=document,
            dependencies=dependencies,
            reason="publish v0.22 DB and Object Store restore drill evidence",
            draft_writer=partial(
                self._write,
                snapshot_id=snapshot_id,
                backup_id=backup_record_id,
                started_at=started_at,
                completed_at=ended_at,
                results=results,
                blockers=blockers,
                document=document,
                fingerprint=fingerprint,
            ),
        )
        return RestoreDrillPublication(
            snapshot_id,
            publication.artifact_id,
            fingerprint,
            not blockers,
            blockers,
            results,
            publication.reused,
        )

    def publish_restored_store(
        self,
        *,
        backup_record_id: uuid.UUID,
        restored_object_store: LocalPayloadObjectStore,
        started_at: datetime,
        completed_at: datetime | None = None,
    ) -> RestoreDrillPublication:
        """Read every DB-declared strong root from the restored Object Store."""

        observations: list[RestoredObjectObservation] = []
        observed_ids: set[uuid.UUID] = set()
        for row in self._strong_object_inventory():
            object_id = row["payload_object_id"]
            if object_id in observed_ids:
                continue
            try:
                content_hash, byte_size = restored_object_store.observe(row["storage_uri"])
            except FileNotFoundError:
                continue
            observations.append(RestoredObjectObservation(object_id, content_hash, byte_size))
            observed_ids.add(object_id)
        return self.publish(
            backup_record_id=backup_record_id,
            observations=tuple(observations),
            started_at=started_at,
            completed_at=completed_at,
        )

    def _backup(self, backup_id: uuid.UUID) -> RowMapping:
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM ops.backup_record WHERE backup_record_id=:id"),
                {"id": backup_id},
            ).mappings().one_or_none()
        if row is None:
            raise ValueError(f"Restore drill backup does not exist: {backup_id}")
        return row

    def _strong_object_inventory(self) -> tuple[RowMapping, ...]:
        return strong_object_inventory(self._engine)

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        snapshot_id: uuid.UUID,
        backup_id: uuid.UUID,
        started_at: datetime,
        completed_at: datetime,
        results: tuple[RestoredObjectResult, ...],
        blockers: tuple[str, ...],
        document: dict[str, object],
        fingerprint: str,
    ) -> None:
        connection.execute(text("""
            INSERT INTO ops.v022_restore_drill_snapshot (
              restore_drill_snapshot_id,artifact_id,backup_record_id,started_at,completed_at,
              expected_object_count,verified_object_count,ready_for_gate,blocker_codes,
              drill_document,drill_fingerprint
            ) VALUES (:id,:artifact,:backup,:started,:completed,:expected,:verified,:ready,
                      CAST(:blockers AS jsonb),CAST(:document AS jsonb),:fingerprint)
        """), {
            "id": snapshot_id, "artifact": artifact_id, "backup": backup_id,
            "started": started_at, "completed": completed_at, "expected": len(results),
            "verified": sum(item.passed for item in results), "ready": not blockers,
            "blockers": json.dumps(blockers),
            "document": json.dumps(document, sort_keys=True, default=str),
            "fingerprint": fingerprint,
        })
        for item in results:
            connection.execute(text("""
                INSERT INTO ops.v022_restore_drill_object (
                  restore_drill_snapshot_id,ordinal,payload_manifest_id,manifest_artifact_id,
                  payload_object_id,expected_content_hash,observed_content_hash,
                  expected_byte_size,observed_byte_size,passed,blocker_code
                ) VALUES (:snapshot,:ordinal,:manifest,:manifest_artifact,:object,:expected_hash,
                          :observed_hash,:expected_bytes,:observed_bytes,:passed,:blocker)
            """), {
                "snapshot": snapshot_id, "ordinal": item.ordinal,
                "manifest": item.payload_manifest_id,
                "manifest_artifact": item.manifest_artifact_id,
                "object": item.payload_object_id,
                "expected_hash": item.expected_content_hash,
                "observed_hash": item.observed_content_hash,
                "expected_bytes": item.expected_byte_size,
                "observed_bytes": item.observed_byte_size,
                "passed": item.passed, "blocker": item.blocker_code,
            })


def strong_object_inventory(engine: Engine) -> tuple[RowMapping, ...]:
    """Return the exact published, materialized Object Store strong-root closure."""

    with engine.connect() as connection:
        return tuple(connection.execute(text("""
            SELECT DISTINCT manifest.payload_manifest_id,
                   manifest.artifact_id AS manifest_artifact_id,
                   object.payload_object_id,object.object_content_hash,
                   object.storage_uri,object.byte_size,object.object_state,
                   object.verification_status
              FROM data.payload_manifest manifest
              JOIN data.v022_strong_payload_manifest strong_root
                ON strong_root.payload_manifest_id=manifest.payload_manifest_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
              JOIN data.payload_manifest_partition link
                ON link.payload_manifest_id=manifest.payload_manifest_id
              JOIN data.payload_partition partition
                ON partition.payload_partition_id=link.payload_partition_id
              JOIN data.payload_object object
                ON object.payload_object_id=partition.payload_object_id
             WHERE manifest.materialization_state='materialized'
               AND artifact.status='published'
             ORDER BY manifest.payload_manifest_id,object.payload_object_id
        """)).mappings())


class RollbackDrillService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        rollback_transition_artifact_id: uuid.UUID,
        probe: RollbackProbe,
        completed_at: datetime | None = None,
    ) -> RollbackDrillPublication:
        if not probe.probe_document:
            raise ValueError("Rollback drill requires a nonempty probe document")
        occurred_at = completed_at or datetime.now(UTC)
        if occurred_at.tzinfo is None:
            raise ValueError("Rollback drill completion time must be timezone-aware")
        transition, duplicate_count, post_count = self._facts(rollback_transition_artifact_id)
        blockers: list[str] = []
        if transition["to_state"] != "maintenance_read_only":
            blockers.append("not_maintenance_read_only_transition")
        if duplicate_count:
            blockers.append("duplicate_product_decision_identity")
        if post_count:
            blockers.append("product_decision_published_after_rollback")
        if not probe.v021_read_probe_passed:
            blockers.append("v021_read_probe_failed")
        if not probe.v022_submission_rejected:
            blockers.append("v022_submission_not_rejected")
        if not probe.exact_pinned_replay_passed:
            blockers.append("exact_pinned_replay_failed")
        document = {
            "contract_version": "v0.22.0",
            "rollback_transition_artifact_id": str(rollback_transition_artifact_id),
            "transition_sequence": transition["sequence_number"],
            "completed_at": occurred_at,
            "duplicate_product_decision_count": duplicate_count,
            "post_rollback_product_decision_count": post_count,
            "probe": asdict(probe),
            "ready_for_gate": not blockers,
            "blocker_codes": blockers,
        }
        fingerprint = sha256_hexdigest(document)
        snapshot_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:rollback-drill:{fingerprint}")
        publication = self._artifacts.publish(
            artifact_type="v022_rollback_drill_evidence",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=document,
            content_payload=document,
            dependencies=(
                DependencyInput(rollback_transition_artifact_id, "rollback_transition", 0),
            ),
            reason="publish v0.22 rollback behavior drill evidence",
            draft_writer=partial(
                self._write,
                snapshot_id=snapshot_id,
                transition_artifact_id=rollback_transition_artifact_id,
                occurred_at=occurred_at,
                duplicate_count=duplicate_count,
                post_count=post_count,
                probe=probe,
                blockers=tuple(blockers),
                document=document,
                fingerprint=fingerprint,
            ),
        )
        return RollbackDrillPublication(
            snapshot_id, publication.artifact_id, fingerprint, not blockers,
            tuple(blockers), publication.reused,
        )

    def publish_verified(
        self,
        *,
        rollback_transition_artifact_id: uuid.UUID,
        v021_artifact_id: uuid.UUID,
        replay_command_name: str,
        replay_idempotency_key: uuid.UUID,
        replay_request: dict[str, object],
        completed_at: datetime | None = None,
    ) -> RollbackDrillPublication:
        probe = RollbackProbeService(self._engine).run(
            rollback_transition_artifact_id=rollback_transition_artifact_id,
            v021_artifact_id=v021_artifact_id,
            replay_command_name=replay_command_name,
            replay_idempotency_key=replay_idempotency_key,
            replay_request=replay_request,
        )
        return self.publish(
            rollback_transition_artifact_id=rollback_transition_artifact_id,
            probe=probe,
            completed_at=completed_at,
        )

    def _facts(self, artifact_id: uuid.UUID) -> tuple[RowMapping, int, int]:
        with self._engine.connect() as connection:
            transition = connection.execute(text("""
                SELECT transition.* FROM workspace.v022_release_transition transition
                JOIN lineage.artifact artifact ON artifact.artifact_id=transition.artifact_id
                WHERE transition.artifact_id=:artifact AND artifact.status='published'
            """), {"artifact": artifact_id}).mappings().one_or_none()
            if transition is None:
                raise ValueError("Rollback drill requires a published Release Transition")
            duplicate_count = connection.scalar(text("""
                SELECT count(*) FROM (
                  SELECT execution_version_id,decision_session_id
                  FROM product.v022_product_decision
                  GROUP BY execution_version_id,decision_session_id HAVING count(*)>1
                ) duplicate
            """))
            post_count = connection.scalar(text("""
                SELECT count(*) FROM product.v022_product_decision
                WHERE created_at>:rollback_at
            """), {"rollback_at": transition["requested_at"]})
        return transition, int(duplicate_count or 0), int(post_count or 0)

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        snapshot_id: uuid.UUID,
        transition_artifact_id: uuid.UUID,
        occurred_at: datetime,
        duplicate_count: int,
        post_count: int,
        probe: RollbackProbe,
        blockers: tuple[str, ...],
        document: dict[str, object],
        fingerprint: str,
    ) -> None:
        connection.execute(text("""
            INSERT INTO ops.v022_rollback_drill_snapshot (
              rollback_drill_snapshot_id,artifact_id,rollback_transition_artifact_id,
              completed_at,duplicate_product_decision_count,
              post_rollback_product_decision_count,v021_read_probe_passed,
              v022_submission_rejected,exact_pinned_replay_passed,ready_for_gate,
              blocker_codes,probe_document,drill_fingerprint
            ) VALUES (:id,:artifact,:transition,:completed,:duplicates,:post_count,:v021_read,
                      :v022_rejected,:replay,:ready,CAST(:blockers AS jsonb),
                      CAST(:document AS jsonb),:fingerprint)
        """), {
            "id": snapshot_id, "artifact": artifact_id,
            "transition": transition_artifact_id, "completed": occurred_at,
            "duplicates": duplicate_count, "post_count": post_count,
            "v021_read": probe.v021_read_probe_passed,
            "v022_rejected": probe.v022_submission_rejected,
            "replay": probe.exact_pinned_replay_passed, "ready": not blockers,
            "blockers": json.dumps(blockers),
            "document": json.dumps(document, sort_keys=True, default=str),
            "fingerprint": fingerprint,
        })
