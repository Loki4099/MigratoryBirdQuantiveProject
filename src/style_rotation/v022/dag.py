from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest

WorkKind = Literal[
    "node",
    "aggregation",
    "strategy_target",
    "defense_decision",
    "sleeve_merge",
    "portfolio_cell",
]

# Historical S&P materialization and full-panel Portfolio Cells can legitimately
# run for several minutes on the local research workstation.  The original
# two-minute lease was shorter than one normal deterministic work item, so a
# healthy worker lost its fence before it could atomically publish completion.
DEFAULT_GRAPH_WORK_LEASE_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class WorkPlan:
    occurrence_kind: WorkKind
    occurrence_key: str
    execution_fingerprint: str
    required_upstream_keys: tuple[str, ...] = ()
    priority: int = 100


@dataclass(frozen=True, slots=True)
class PlannedGraphRun:
    graph_run_id: uuid.UUID
    run_fingerprint: str
    work_item_ids: tuple[uuid.UUID, ...]
    reused: bool = False


@dataclass(frozen=True, slots=True)
class ClaimedGraphWork:
    graph_work_item_id: uuid.UUID
    fencing_token: int
    work_kind: WorkKind


class GraphDagService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def plan_run(
        self,
        *,
        compiled_research_graph_id: uuid.UUID,
        requested_by: str,
        requested_range: dict[str, object],
        environment_fingerprint: str,
        work: tuple[WorkPlan, ...],
    ) -> PlannedGraphRun:
        if not work:
            raise ValueError("Graph Run requires at least one Work Item")
        keys = [item.occurrence_key for item in work]
        if len(keys) != len(set(keys)):
            raise ValueError("Graph Work occurrence keys must be unique")
        unknown = {
            key for item in work for key in item.required_upstream_keys if key not in set(keys)
        }
        if unknown:
            raise ValueError(f"Unknown required upstream Work keys: {sorted(unknown)}")
        run_fingerprint = sha256_hexdigest(
            {
                "compiled_research_graph_id": compiled_research_graph_id,
                "requested_range": requested_range,
                "environment_fingerprint": environment_fingerprint,
                "work": work,
            }
        )
        run_id = uuid.uuid4()
        item_ids: dict[str, uuid.UUID] = {}
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:fingerprint,0))"),
                {"fingerprint": run_fingerprint},
            )
            existing_run = (
                connection.execute(
                    text(
                        """
                        SELECT graph_run_id,compiled_research_graph_id,requested_range,
                               environment_fingerprint
                          FROM workspace.v022_graph_run
                         WHERE run_fingerprint=:fingerprint
                        """
                    ),
                    {"fingerprint": run_fingerprint},
                )
                .mappings()
                .one_or_none()
            )
            if existing_run is not None:
                existing_items = connection.execute(
                    text(
                        """
                        SELECT consumer.occurrence_key,consumer.occurrence_kind,
                               work.graph_work_item_id,work.execution_fingerprint
                          FROM workspace.v022_graph_work_consumer consumer
                          JOIN workspace.v022_graph_work_item work
                            ON work.graph_work_item_id=consumer.graph_work_item_id
                         WHERE consumer.graph_run_id=:run
                        """
                    ),
                    {"run": existing_run["graph_run_id"]},
                ).mappings().all()
                stored = {item["occurrence_key"]: item for item in existing_items}
                if (
                    existing_run["compiled_research_graph_id"]
                    != compiled_research_graph_id
                    or existing_run["requested_range"] != requested_range
                    or existing_run["environment_fingerprint"]
                    != environment_fingerprint
                    or set(stored) != set(keys)
                    or any(
                        stored[item.occurrence_key]["occurrence_kind"]
                        != item.occurrence_kind
                        or stored[item.occurrence_key]["execution_fingerprint"]
                        != item.execution_fingerprint
                        for item in work
                    )
                ):
                    raise RuntimeError("Graph Run fingerprint collided with different semantics")
                return PlannedGraphRun(
                    existing_run["graph_run_id"],
                    run_fingerprint,
                    tuple(stored[key]["graph_work_item_id"] for key in keys),
                    True,
                )
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_graph_run (
                      graph_run_id,compiled_research_graph_id,run_fingerprint,status,
                      requested_by,requested_range,environment_fingerprint
                    ) VALUES (
                      :id,:graph,:fingerprint,'planning',:actor,
                      CAST(:range AS jsonb),:environment
                    )
                    """
                ),
                {
                    "id": run_id,
                    "graph": compiled_research_graph_id,
                    "fingerprint": run_fingerprint,
                    "actor": requested_by,
                    "range": json.dumps(requested_range, sort_keys=True),
                    "environment": environment_fingerprint,
                },
            )
            for item in work:
                existing = (
                    connection.execute(
                        text(
                            "SELECT graph_work_item_id,status FROM workspace.v022_graph_work_item "
                            "WHERE execution_fingerprint=:fingerprint FOR UPDATE"
                        ),
                        {"fingerprint": item.execution_fingerprint},
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is None:
                    item_id = uuid.uuid4()
                    connection.execute(
                        text(
                            """
                            INSERT INTO workspace.v022_graph_work_item (
                              graph_work_item_id,execution_fingerprint,work_kind,status,priority
                            ) VALUES (:id,:fingerprint,:kind,'queued',:priority)
                            """
                        ),
                        {
                            "id": item_id,
                            "fingerprint": item.execution_fingerprint,
                            "kind": item.occurrence_kind,
                            "priority": item.priority,
                        },
                    )
                    disposition = "execute"
                else:
                    item_id = existing["graph_work_item_id"]
                    stored_kind = connection.scalar(
                        text(
                            "SELECT work_kind FROM workspace.v022_graph_work_item "
                            "WHERE graph_work_item_id=:item"
                        ),
                        {"item": item_id},
                    )
                    if stored_kind != item.occurrence_kind:
                        raise RuntimeError(
                            "Graph Work execution fingerprint collided across work kinds"
                        )
                    if existing["status"] in {
                        "failed",
                        "cancelled",
                        "blocked_upstream_failed",
                        "blocked_upstream_cancelled",
                    }:
                        active_consumer = connection.execute(
                            text(
                                """
                                SELECT EXISTS (
                                  SELECT 1
                                    FROM workspace.v022_graph_work_consumer consumer
                                    JOIN workspace.v022_graph_run run
                                      ON run.graph_run_id=consumer.graph_run_id
                                   WHERE consumer.graph_work_item_id=:item
                                     AND consumer.released_at IS NULL
                                     AND run.status IN ('planning','ready','running')
                                )
                                """
                            ),
                            {"item": item_id},
                        ).scalar_one()
                        if active_consumer:
                            raise RuntimeError(
                                "Failed Graph Work still has an active consumer and "
                                "cannot be retried"
                            )
                        connection.execute(
                            text(
                                """
                                UPDATE workspace.v022_graph_work_item
                                   SET status='queued',lease_owner=NULL,
                                       lease_expires_at=NULL,cancel_requested_at=NULL,
                                       failure_details=NULL,updated_at=now()
                                 WHERE graph_work_item_id=:item
                                """
                            ),
                            {"item": item_id},
                        )
                    disposition = (
                        "reuse"
                        if existing["status"] in {"completed", "reused"}
                        else "execute"
                    )
                item_ids[item.occurrence_key] = item_id
                connection.execute(
                    text(
                        """
                        INSERT INTO workspace.v022_graph_work_consumer (
                          graph_run_id,graph_work_item_id,occurrence_kind,occurrence_key,binding_disposition
                        ) VALUES (:run,:item,:kind,:key,:disposition)
                        """
                    ),
                    {
                        "run": run_id,
                        "item": item_id,
                        "kind": item.occurrence_kind,
                        "key": item.occurrence_key,
                        "disposition": disposition,
                    },
                )
                if disposition == "reuse":
                    connection.execute(
                        text(
                            """
                            UPDATE workspace.v022_graph_work_item
                               SET status='reused',updated_at=now()
                             WHERE graph_work_item_id=:item AND status='completed'
                            """
                        ),
                        {"item": item_id},
                    )
            for item in work:
                for upstream_key in item.required_upstream_keys:
                    connection.execute(
                        text(
                            """
                            INSERT INTO workspace.v022_graph_work_dependency (
                              upstream_work_item_id,downstream_work_item_id,dependency_kind
                            ) VALUES (:upstream,:downstream,'required') ON CONFLICT DO NOTHING
                            """
                        ),
                        {
                            "upstream": item_ids[upstream_key],
                            "downstream": item_ids[item.occurrence_key],
                        },
                    )
            connection.execute(
                text("SELECT workspace.v022_mark_graph_ready(:run,:count)"),
                {"run": run_id, "count": len(work)},
            )
        return PlannedGraphRun(
            run_id, run_fingerprint, tuple(item_ids[key] for key in keys), False
        )

    def claim(
        self,
        graph_run_id: uuid.UUID,
        *,
        worker_key: str,
        lease_seconds: int = DEFAULT_GRAPH_WORK_LEASE_SECONDS,
    ) -> ClaimedGraphWork | None:
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT * FROM workspace.v022_claim_graph_work(:run,:worker,:lease)"
                    ),
                    {"run": graph_run_id, "worker": worker_key, "lease": lease_seconds},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return ClaimedGraphWork(
            row["graph_work_item_id"], row["fencing_token"], row["work_kind"]
        )

    def renew(
        self,
        claim: ClaimedGraphWork,
        *,
        worker_key: str,
        lease_seconds: int = DEFAULT_GRAPH_WORK_LEASE_SECONDS,
    ) -> None:
        """Extend one still-active fenced claim without changing its identity."""

        if lease_seconds <= 0:
            raise ValueError("Graph Work lease duration must be positive")
        with self._engine.begin() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE workspace.v022_graph_work_item
                       SET lease_expires_at=now() + (:lease * interval '1 second'),
                           updated_at=now()
                     WHERE graph_work_item_id=:item
                       AND status='running'
                       AND lease_owner=:worker
                       AND fencing_token=:fence
                       AND cancel_requested_at IS NULL
                       AND lease_expires_at>now()
                    """
                ),
                {
                    "item": claim.graph_work_item_id,
                    "worker": worker_key,
                    "fence": claim.fencing_token,
                    "lease": lease_seconds,
                },
            )
            if result.rowcount != 1:
                raise RuntimeError("Graph Work claim cannot be renewed")

    def finish(
        self,
        claim: ClaimedGraphWork,
        *,
        worker_key: str,
        status: Literal["completed", "failed", "cancelled"],
        details: dict[str, object] | None = None,
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT workspace.v022_finish_graph_work("
                    ":item,:worker,:fence,:status,CAST(:details AS jsonb))"
                ),
                {
                    "item": claim.graph_work_item_id,
                    "worker": worker_key,
                    "fence": claim.fencing_token,
                    "status": status,
                    "details": json.dumps(details or {}, sort_keys=True),
                },
            )

    def cancel_run(self, graph_run_id: uuid.UUID) -> None:
        """Release this Graph Run without cancelling Work shared by another live consumer."""

        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT workspace.v022_release_graph_run(:run)"),
                {"run": graph_run_id},
            )
            finalize_released_graph_run(connection, graph_run_id)


def finalize_released_graph_run(
    connection: Connection,
    graph_run_id: uuid.UUID,
) -> None:
    """Close a released running Run once it has no remaining live consumers."""

    connection.execute(
        text(
            """
            UPDATE workspace.v022_graph_run run
               SET status='cancelled',
                   completed_at=coalesce(run.completed_at,now())
             WHERE run.graph_run_id=:run
               AND run.status IN ('planning','ready','running')
               AND run.cancel_requested_at IS NOT NULL
               AND NOT EXISTS (
                 SELECT 1
                   FROM workspace.v022_graph_work_consumer consumer
                  WHERE consumer.graph_run_id=run.graph_run_id
                    AND consumer.released_at IS NULL
               )
            """
        ),
        {"run": graph_run_id},
    )


def execution_fingerprint(
    *,
    component_version_id: uuid.UUID,
    resolved_parameters: dict[str, object],
    ordered_input_manifests: tuple[tuple[str, int, uuid.UUID, str], ...],
    resource_bindings: tuple[tuple[str, uuid.UUID], ...],
    requested_range: dict[str, object],
    executor_version: str,
    environment_fingerprint: str,
    determinism_policy: str,
    cache_policy: str,
    payload_reader_contract: str,
    target_or_fold_identity: str | None = None,
) -> str:
    return sha256_hexdigest(
        {
            "component_version_id": component_version_id,
            "resolved_parameters": resolved_parameters,
            "ordered_input_manifests": ordered_input_manifests,
            "resource_bindings": resource_bindings,
            "requested_range": requested_range,
            "executor_version": executor_version,
            "environment_fingerprint": environment_fingerprint,
            "determinism_policy": determinism_policy,
            "cache_policy": cache_policy,
            "payload_reader_contract": payload_reader_contract,
            "target_or_fold_identity": target_or_fold_identity,
        }
    )


def utc_now() -> datetime:
    return datetime.now(UTC)
