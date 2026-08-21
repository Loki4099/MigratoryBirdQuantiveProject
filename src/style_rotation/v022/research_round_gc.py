from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import partial

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore
from style_rotation.v022.retention_lock import v022_retention_guard

TERMINAL_GRAPH_WORK_STATUSES = (
    "completed",
    "reused",
    "failed",
    "cancelled",
    "blocked_upstream_failed",
    "blocked_upstream_cancelled",
)

_NONTERMINAL_GRAPH_WORK_COUNT = text(
    """
    SELECT count(DISTINCT work.graph_work_item_id)
      FROM experiment.v022_suite_launch_batch_round batch_round
      JOIN experiment.v022_suite_launch_batch_child child
        ON child.suite_launch_batch_id=batch_round.suite_launch_batch_id
      JOIN experiment.v022_research_suite_graph_run_binding suite_run
        ON suite_run.research_suite_id=child.research_suite_id
      JOIN workspace.v022_graph_work_consumer consumer
        ON consumer.graph_run_id=suite_run.graph_run_id
      JOIN workspace.v022_graph_work_item work
        ON work.graph_work_item_id=consumer.graph_work_item_id
     WHERE batch_round.research_round_id=:round
       AND work.status NOT IN :terminal_statuses
    """
).bindparams(bindparam("terminal_statuses", expanding=True))


def _count_nonterminal_graph_work(connection: Connection, round_id: uuid.UUID) -> int:
    return int(
        connection.scalar(
            _NONTERMINAL_GRAPH_WORK_COUNT,
            {
                "round": round_id,
                "terminal_statuses": TERMINAL_GRAPH_WORK_STATUSES,
            },
        )
        or 0
    )


@dataclass(frozen=True, slots=True)
class ResearchRoundGCObject:
    payload_object_id: uuid.UUID
    storage_uri: str
    content_hash: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class ResearchRoundGCPlan:
    plan_id: uuid.UUID
    round_id: uuid.UUID
    status: str
    objects: tuple[ResearchRoundGCObject, ...]
    manifest_count: int
    estimated_bytes: int
    fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class ResearchRoundGCResult:
    plan_id: uuid.UUID
    round_id: uuid.UUID
    object_count: int
    deleted_bytes: int
    tombstone_fingerprint: str


class ResearchRoundGCService:
    """Plan and evict closed-round payloads without crossing a strong root."""

    def __init__(self, engine: Engine, object_store: LocalPayloadObjectStore) -> None:
        self._engine = engine
        self._object_store = object_store

    def plan(self, round_id: uuid.UUID) -> ResearchRoundGCPlan:
        with self._engine.connect() as connection:
            round_row = (
                connection.execute(
                    text(
                        "SELECT research_round_id,status,reset_idempotency_key "
                        "FROM workspace.v022_research_round "
                        "WHERE research_round_id=:round"
                    ),
                    {"round": round_id},
                )
                .mappings()
                .one_or_none()
            )
            if round_row is None:
                raise LookupError("research_round_not_found")
            if round_row["status"] not in {"gc_pending", "gc_complete"}:
                raise ValueError("research_round_not_closed_for_gc")
            if round_row["reset_idempotency_key"] is None:
                raise ValueError("research_round_reset_identity_missing")
            nonterminal_count = _count_nonterminal_graph_work(connection, round_id)
            if nonterminal_count:
                raise ValueError("research_round_gc_waiting_for_terminal_work")
            rows = tuple(
                connection.execute(
                    text(
                        """
                        WITH RECURSIVE artifact_closure(artifact_id) AS (
                          SELECT artifact_id FROM ops.v022_research_round_artifact
                           WHERE research_round_id=:round
                          UNION
                          SELECT dependency.depends_on_artifact_id
                            FROM artifact_closure closure
                            JOIN lineage.artifact_dependency dependency
                              ON dependency.artifact_id=closure.artifact_id
                        ), round_manifest(payload_manifest_id) AS (
                          SELECT manifest.payload_manifest_id
                            FROM data.payload_manifest manifest
                            JOIN artifact_closure closure
                              ON closure.artifact_id=manifest.artifact_id
                        ), candidate_object AS (
                          SELECT DISTINCT object.payload_object_id,object.storage_uri,
                                 object.object_content_hash,object.byte_size
                            FROM round_manifest round_manifest
                            JOIN data.payload_manifest_partition manifest_partition
                              ON manifest_partition.payload_manifest_id=
                                 round_manifest.payload_manifest_id
                            JOIN data.payload_partition partition
                              ON partition.payload_partition_id=
                                 manifest_partition.payload_partition_id
                            JOIN data.payload_object object
                              ON object.payload_object_id=partition.payload_object_id
                           WHERE NOT EXISTS (
                             SELECT 1
                               FROM data.payload_partition other_partition
                               JOIN data.payload_manifest_partition other_link
                                ON other_link.payload_partition_id=
                                   other_partition.payload_partition_id
                               JOIN data.v022_strong_payload_manifest strong
                                 ON strong.payload_manifest_id=other_link.payload_manifest_id
                              WHERE other_partition.payload_object_id=object.payload_object_id
                           )
                        )
                        SELECT * FROM candidate_object
                        ORDER BY object_content_hash,payload_object_id
                        """
                    ),
                    {"round": round_id},
                ).mappings()
            )
            manifest_count = int(
                connection.scalar(
                    text(
                        """
                        WITH RECURSIVE closure(artifact_id) AS (
                          SELECT artifact_id FROM ops.v022_research_round_artifact
                           WHERE research_round_id=:round
                          UNION
                          SELECT dependency.depends_on_artifact_id
                            FROM closure
                            JOIN lineage.artifact_dependency dependency
                              ON dependency.artifact_id=closure.artifact_id
                        )
                        SELECT count(DISTINCT manifest.payload_manifest_id)
                          FROM data.payload_manifest manifest JOIN closure USING (artifact_id)
                         WHERE NOT EXISTS (
                           SELECT 1 FROM data.v022_strong_payload_manifest strong
                            WHERE strong.payload_manifest_id=manifest.payload_manifest_id
                         )
                        """
                    ),
                    {"round": round_id},
                )
                or 0
            )
        objects = tuple(
            ResearchRoundGCObject(
                payload_object_id=row["payload_object_id"],
                storage_uri=str(row["storage_uri"]),
                content_hash=str(row["object_content_hash"]),
                byte_size=int(row["byte_size"]),
            )
            for row in rows
        )
        semantic = {
            "contract_version": "v0.22.0",
            "research_round_id": str(round_id),
            "manifest_count": manifest_count,
            "objects": [
                {
                    "payload_object_id": str(item.payload_object_id),
                    "content_hash": item.content_hash,
                    "byte_size": item.byte_size,
                }
                for item in objects
            ],
        }
        fingerprint = sha256_hexdigest(semantic)
        plan_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:round-gc:{fingerprint}")
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_research_round_gc_plan",
            artifact_key=f"round:{round_id}:gc:{fingerprint}",
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            reason="publish Product-safe Research Round GC plan",
            draft_writer=partial(
                self._write_plan,
                plan_id=plan_id,
                round_id=round_id,
                fingerprint=fingerprint,
                objects=objects,
                manifest_count=manifest_count,
            ),
        )
        with self._engine.connect() as connection:
            persisted_status = str(
                connection.scalar(
                    text(
                        "SELECT status FROM ops.v022_research_round_gc_plan "
                        "WHERE research_round_gc_plan_id=:plan"
                    ),
                    {"plan": plan_id},
                )
            )
        return ResearchRoundGCPlan(
            plan_id=plan_id,
            round_id=round_id,
            status=persisted_status,
            objects=objects,
            manifest_count=manifest_count,
            estimated_bytes=sum(item.byte_size for item in objects),
            fingerprint=fingerprint,
            reused=publication.reused,
        )

    def execute(self, plan_id: uuid.UUID) -> ResearchRoundGCResult:
        with v022_retention_guard(self._engine):
            return self._execute_locked(plan_id)

    def _execute_locked(self, plan_id: uuid.UUID) -> ResearchRoundGCResult:
        with self._engine.begin() as connection:
            plan = (
                connection.execute(
                    text(
                        "SELECT * FROM ops.v022_research_round_gc_plan "
                        "WHERE research_round_gc_plan_id=:plan FOR UPDATE"
                    ),
                    {"plan": plan_id},
                )
                .mappings()
                .one_or_none()
            )
            if plan is None:
                raise LookupError("research_round_gc_plan_not_found")
            if plan["status"] == "completed":
                tombstone = connection.execute(
                    text(
                        "SELECT * FROM ops.v022_research_round_gc_tombstone "
                        "WHERE research_round_gc_plan_id=:plan"
                    ),
                    {"plan": plan_id},
                ).mappings().one()
                return ResearchRoundGCResult(
                    plan_id, plan["research_round_id"], int(tombstone["deleted_object_count"]),
                    int(tombstone["deleted_bytes"]), str(tombstone["summary_fingerprint"]),
                )
            if plan["status"] not in {"planned", "failed", "running"}:
                raise ValueError("research_round_gc_plan_not_executable")
            connection.execute(
                text(
                    "UPDATE ops.v022_research_round_gc_plan SET status='running',"
                    "started_at=COALESCE(started_at,now()),failure_summary=NULL "
                    "WHERE research_round_gc_plan_id=:plan"
                ),
                {"plan": plan_id},
            )
            objects = tuple(
                connection.execute(
                    text(
                        "SELECT candidate.*,object.storage_uri AS current_storage_uri "
                        "FROM ops.v022_research_round_gc_object candidate "
                        "JOIN data.payload_object object USING (payload_object_id) "
                        "WHERE research_round_gc_plan_id=:plan ORDER BY ordinal"
                    ),
                    {"plan": plan_id},
                ).mappings()
            )
            for item in objects:
                if bool(
                    connection.scalar(
                        text(
                            """
                            SELECT EXISTS (
                              SELECT 1 FROM data.payload_partition partition
                              JOIN data.payload_manifest_partition link
                                ON link.payload_partition_id=partition.payload_partition_id
                              JOIN data.v022_strong_payload_manifest strong
                                ON strong.payload_manifest_id=link.payload_manifest_id
                             WHERE partition.payload_object_id=:object
                            )
                            """
                        ),
                        {"object": item["payload_object_id"]},
                    )
                ):
                    raise ValueError("research_round_gc_object_became_strong_root")
        deleted_bytes = 0
        try:
            for item in objects:
                self._object_store.evict(
                    str(item["storage_uri"]),
                    expected_content_hash=str(item["expected_content_hash"]),
                    expected_byte_size=int(item["expected_byte_size"]),
                )
                deleted_bytes += int(item["expected_byte_size"])
        except Exception as error:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE ops.v022_research_round_gc_plan "
                        "SET status='failed',failure_summary=:summary "
                        "WHERE research_round_gc_plan_id=:plan"
                    ),
                    {"plan": plan_id, "summary": f"{type(error).__name__}: {error}"[:1000]},
                )
            raise
        summary = {
            "plan_id": str(plan_id),
            "research_round_id": str(plan["research_round_id"]),
            "deleted_object_count": len(objects),
            "deleted_bytes": deleted_bytes,
        }
        summary_fingerprint = sha256_hexdigest(summary)
        with self._engine.begin() as connection:
            locked = connection.execute(
                text(
                    "SELECT status,reset_idempotency_key FROM workspace.v022_research_round "
                    "WHERE research_round_id=:round FOR UPDATE"
                ),
                {"round": plan["research_round_id"]},
            ).mappings().one()
            if locked["status"] not in {"gc_pending", "gc_complete"}:
                raise ValueError("research_round_status_changed_during_gc")
            connection.execute(
                text(
                    "UPDATE ops.v022_research_round_gc_plan SET status='completed',"
                    "deleted_object_count=:count,deleted_bytes=:bytes,completed_at=now() "
                    "WHERE research_round_gc_plan_id=:plan"
                ),
                {"plan": plan_id, "count": len(objects), "bytes": deleted_bytes},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO ops.v022_research_round_gc_tombstone
                      (research_round_id,research_round_gc_plan_id,reset_idempotency_key,
                       deleted_object_count,deleted_bytes,completed_at,summary_fingerprint)
                    VALUES (:round,:plan,:reset,:count,:bytes,now(),:fingerprint)
                    ON CONFLICT (research_round_id) DO NOTHING
                    """
                ),
                {
                    "round": plan["research_round_id"], "plan": plan_id,
                    "reset": locked["reset_idempotency_key"], "count": len(objects),
                    "bytes": deleted_bytes, "fingerprint": summary_fingerprint,
                },
            )
            if locked["status"] == "gc_pending":
                connection.execute(
                    text(
                        "UPDATE workspace.v022_research_round SET status='gc_complete' "
                        "WHERE research_round_id=:round"
                    ),
                    {"round": plan["research_round_id"]},
                )
        return ResearchRoundGCResult(
            plan_id, plan["research_round_id"], len(objects), deleted_bytes, summary_fingerprint
        )

    @staticmethod
    def _write_plan(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        plan_id: uuid.UUID,
        round_id: uuid.UUID,
        fingerprint: str,
        objects: tuple[ResearchRoundGCObject, ...],
        manifest_count: int,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO ops.v022_research_round_gc_plan
                  (research_round_gc_plan_id,artifact_id,research_round_id,plan_fingerprint,
                   status,object_count,manifest_count,estimated_bytes)
                VALUES (:plan,:artifact,:round,:fingerprint,'planned',:count,:manifests,:bytes)
                """
            ),
            {
                "plan": plan_id, "artifact": artifact_id, "round": round_id,
                "fingerprint": fingerprint, "count": len(objects),
                "manifests": manifest_count,
                "bytes": sum(item.byte_size for item in objects),
            },
        )
        if objects:
            connection.execute(
                text(
                    """
                    INSERT INTO ops.v022_research_round_gc_object
                      (research_round_gc_plan_id,ordinal,payload_object_id,storage_uri,
                       expected_content_hash,expected_byte_size)
                    VALUES (:plan,:ordinal,:object,:uri,:hash,:bytes)
                    """
                ),
                [
                    {
                        "plan": plan_id, "ordinal": ordinal,
                        "object": item.payload_object_id, "uri": item.storage_uri,
                        "hash": item.content_hash, "bytes": item.byte_size,
                    }
                    for ordinal, item in enumerate(objects)
                ],
            )
