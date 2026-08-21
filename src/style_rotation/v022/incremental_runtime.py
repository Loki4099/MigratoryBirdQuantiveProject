from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import Engine, text

from style_rotation.core.canonical import sha256_hexdigest

ExecutionMode = Literal["full_recompute", "windowed"]
PartitionDisposition = Literal["execute", "reuse"]
RevisionImpactPolicy = Literal[
    "windowed_forward", "same_cross_section", "from_revised_session_forward"
]


@dataclass(frozen=True, slots=True)
class IncrementalExecutionContract:
    """Explicit partition semantics frozen with a Node Version.

    ``lookback`` and ``lookforward`` count observed sessions, not calendar days.
    The runtime never infers an incremental mode from implementation code.
    """

    execution_mode: ExecutionMode
    partition_key: tuple[str, ...]
    lookback: int
    lookforward: int = 0
    revision_impact_policy: RevisionImpactPolicy = "windowed_forward"

    def __post_init__(self) -> None:
        if not self.partition_key or any(not item for item in self.partition_key):
            raise ValueError("partition_key must contain non-empty field names")
        if self.lookback < 0 or self.lookforward < 0:
            raise ValueError("lookback and lookforward must be non-negative")


@dataclass(frozen=True, slots=True)
class OutputPartition:
    """A requested immutable output partition over an ordered session axis."""

    partition_key: Mapping[str, str]
    sessions: tuple[date, ...]

    def __post_init__(self) -> None:
        if not self.sessions:
            raise ValueError("an output partition must contain at least one session")
        if tuple(sorted(set(self.sessions))) != self.sessions:
            raise ValueError("partition sessions must be strictly increasing")


def partition_sessions_by_calendar_year(
    *, partition_key: tuple[str, ...], sessions: Sequence[date]
) -> tuple[OutputPartition, ...]:
    """Create canonical non-overlapping annual output partitions."""

    ordered = tuple(sessions)
    if not partition_key or any(not item for item in partition_key):
        raise ValueError("partition_key must contain non-empty field names")
    if not ordered:
        raise ValueError("sessions must not be empty")
    if tuple(sorted(set(ordered))) != ordered:
        raise ValueError("sessions must be strictly increasing")
    grouped: dict[int, list[date]] = {}
    for session in ordered:
        grouped.setdefault(session.year, []).append(session)
    return tuple(
        OutputPartition(
            {key: f"calendar_year:{year}" for key in partition_key},
            tuple(year_sessions),
        )
        for year, year_sessions in sorted(grouped.items())
    )


@dataclass(frozen=True, slots=True)
class PriorPartition:
    partition_key_hash: str
    source_revision_fingerprint: str
    payload_partition_id: str


@dataclass(frozen=True, slots=True)
class PartitionWork:
    partition_key_hash: str
    partition_key: Mapping[str, str]
    output_sessions: tuple[date, ...]
    calculation_sessions: tuple[date, ...]
    source_revision_fingerprint: str
    disposition: PartitionDisposition
    reused_payload_partition_id: str | None


@dataclass(frozen=True, slots=True)
class IncrementalRunPlan:
    contract: IncrementalExecutionContract
    session_axis: tuple[date, ...]
    partitions: tuple[PartitionWork, ...]

    @property
    def execute_count(self) -> int:
        return sum(item.disposition == "execute" for item in self.partitions)

    @property
    def reuse_count(self) -> int:
        return sum(item.disposition == "reuse" for item in self.partitions)


@dataclass(frozen=True, slots=True)
class RecordedPartitionPlan:
    node_run_id: uuid.UUID
    partition_count: int
    reused_existing_plan: bool


def plan_incremental_run(
    *,
    contract: IncrementalExecutionContract,
    partitions: Sequence[OutputPartition],
    source_revisions: Mapping[date, str],
    prior_partitions: Sequence[PriorPartition] = (),
) -> IncrementalRunPlan:
    """Plan partition work without mutating an earlier Manifest.

    A partition may be reused only when the Node Version explicitly declares
    ``windowed`` execution and every source revision in its expanded read range
    is identical. A ``full_recompute`` declaration always executes every
    partition, even if a matching prior payload exists.
    """

    ordered = tuple(partitions)
    session_axis = _validate_and_build_axis(ordered, source_revisions)
    positions = {session: index for index, session in enumerate(session_axis)}
    previous = _index_prior_partitions(prior_partitions)
    work: list[PartitionWork] = []
    for partition in ordered:
        _validate_partition_key(contract, partition)
        first = positions[partition.sessions[0]]
        last = positions[partition.sessions[-1]]
        calculation_start = max(0, first - contract.lookback)
        if contract.revision_impact_policy == "from_revised_session_forward":
            calculation_start = 0
        calculation = session_axis[
            calculation_start : min(len(session_axis), last + contract.lookforward + 1)
        ]
        key_hash = sha256_hexdigest(
            {
                "partition_key": dict(sorted(partition.partition_key.items())),
                "output_sessions": partition.sessions,
            }
        )
        revision_fingerprint = sha256_hexdigest(
            tuple((session, source_revisions[session]) for session in calculation)
        )
        prior = previous.get(key_hash)
        can_reuse = (
            contract.execution_mode == "windowed"
            and prior is not None
            and prior.source_revision_fingerprint == revision_fingerprint
        )
        reused_payload_partition_id = (
            prior.payload_partition_id if can_reuse and prior is not None else None
        )
        work.append(
            PartitionWork(
                partition_key_hash=key_hash,
                partition_key=partition.partition_key,
                output_sessions=partition.sessions,
                calculation_sessions=calculation,
                source_revision_fingerprint=revision_fingerprint,
                disposition="reuse" if can_reuse else "execute",
                reused_payload_partition_id=reused_payload_partition_id,
            )
        )
    return IncrementalRunPlan(contract, session_axis, tuple(work))


def record_partition_plan(
    engine: Engine, *, node_run_id: uuid.UUID, plan: IncrementalRunPlan
) -> RecordedPartitionPlan:
    """Atomically append a plan to an existing running Node Run.

    The persisted Node Version contract is authoritative. This prevents a
    caller from enabling partition reuse for a version published as
    ``full_recompute``. Exact retries are idempotent; partial or conflicting
    plans fail closed.
    """

    documents = tuple(_partition_document(item) for item in plan.partitions)
    with engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    """
                    SELECT run.status, version.execution_contract
                    FROM processing.node_run run
                    JOIN processing.node_version version
                      ON version.node_version_id=run.node_version_id
                    WHERE run.node_run_id=:node_run_id
                    FOR UPDATE OF run
                    """
                ),
                {"node_run_id": node_run_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError("unknown node_run_id")
        if row["status"] != "running":
            raise ValueError("partition plans can only be recorded for running Node Runs")
        persisted_contract = _contract_from_document(row["execution_contract"])
        if persisted_contract != plan.contract:
            raise ValueError("plan contract does not match the published Node Version")
        existing = (
            connection.execute(
                text(
                    """
                SELECT partition_key_hash,partition_document,status,
                       source_revision_fingerprint
                FROM processing.node_run_partition
                WHERE node_run_id=:node_run_id
                ORDER BY partition_key_hash
                """
                ),
                {"node_run_id": node_run_id},
            )
            .mappings()
            .all()
        )
        if existing:
            expected = sorted(
                (
                    item.partition_key_hash,
                    document,
                    "reused" if item.disposition == "reuse" else "planned",
                    item.source_revision_fingerprint,
                )
                for item, document in zip(plan.partitions, documents, strict=True)
            )
            actual = [
                (
                    row["partition_key_hash"],
                    row["partition_document"],
                    row["status"],
                    row["source_revision_fingerprint"],
                )
                for row in existing
            ]
            if actual != expected:
                raise ValueError("Node Run already has a conflicting or partial partition plan")
            return RecordedPartitionPlan(node_run_id, len(existing), True)
        for item, document in zip(plan.partitions, documents, strict=True):
            connection.execute(
                text(
                    """
                    INSERT INTO processing.node_run_partition (
                      node_run_id,partition_key_hash,partition_document,status,
                      source_revision_fingerprint
                    ) VALUES (
                      :node_run_id,:partition_key_hash,CAST(:partition_document AS jsonb),
                      :status,:source_revision_fingerprint
                    )
                    """
                ),
                {
                    "node_run_id": node_run_id,
                    "partition_key_hash": item.partition_key_hash,
                    "partition_document": json.dumps(document, sort_keys=True),
                    "status": "reused" if item.disposition == "reuse" else "planned",
                    "source_revision_fingerprint": item.source_revision_fingerprint,
                },
            )
    return RecordedPartitionPlan(node_run_id, len(plan.partitions), False)


def _validate_and_build_axis(
    partitions: tuple[OutputPartition, ...], source_revisions: Mapping[date, str]
) -> tuple[date, ...]:
    if not partitions:
        raise ValueError("at least one output partition is required")
    output_sessions = tuple(session for item in partitions for session in item.sessions)
    if len(set(output_sessions)) != len(output_sessions):
        raise ValueError("output partitions must not overlap")
    if tuple(sorted(output_sessions)) != output_sessions:
        raise ValueError("output partitions must follow session order")
    source_axis = tuple(sorted(source_revisions))
    if not source_axis:
        raise ValueError("source_revisions must not be empty")
    if any(session not in source_revisions for session in output_sessions):
        raise ValueError("every output session must have a source revision")
    if any(not _is_sha256(value) for value in source_revisions.values()):
        raise ValueError("source revisions must be lowercase SHA-256 hashes")
    return source_axis


def _validate_partition_key(
    contract: IncrementalExecutionContract, partition: OutputPartition
) -> None:
    if set(partition.partition_key) != set(contract.partition_key):
        raise ValueError("partition key fields must exactly match the execution contract")
    if any(not value for value in partition.partition_key.values()):
        raise ValueError("partition key values must be non-empty")


def _index_prior_partitions(
    partitions: Sequence[PriorPartition],
) -> dict[str, PriorPartition]:
    indexed: dict[str, PriorPartition] = {}
    for item in partitions:
        if not _is_sha256(item.partition_key_hash):
            raise ValueError("prior partition key hashes must be lowercase SHA-256 hashes")
        if not _is_sha256(item.source_revision_fingerprint):
            raise ValueError("prior source revisions must be lowercase SHA-256 hashes")
        if not item.payload_partition_id:
            raise ValueError("prior payload partition ids must be non-empty")
        if item.partition_key_hash in indexed:
            raise ValueError("prior partition key hashes must be unique")
        indexed[item.partition_key_hash] = item
    return indexed


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _partition_document(item: PartitionWork) -> dict[str, object]:
    return {
        "partition_key": dict(sorted(item.partition_key.items())),
        "output_range": {
            "start": item.output_sessions[0].isoformat(),
            "end": item.output_sessions[-1].isoformat(),
            "session_count": len(item.output_sessions),
        },
        "calculation_range": {
            "start": item.calculation_sessions[0].isoformat(),
            "end": item.calculation_sessions[-1].isoformat(),
            "session_count": len(item.calculation_sessions),
        },
        "disposition": item.disposition,
        "reused_payload_partition_id": item.reused_payload_partition_id,
    }


def _contract_from_document(document: object) -> IncrementalExecutionContract:
    if not isinstance(document, dict):
        raise ValueError("published Node Version has an invalid execution contract")
    mode = document.get("execution_mode")
    partition_key = document.get("partition_key")
    lookback = document.get("lookback")
    lookforward = document.get("lookforward")
    impact = document.get("revision_impact_policy")
    if mode not in {"full_recompute", "windowed"}:
        raise ValueError("published Node Version uses an unsupported execution mode")
    if not isinstance(partition_key, list) or not all(
        isinstance(item, str) for item in partition_key
    ):
        raise ValueError("published Node Version has an invalid partition key")
    if isinstance(lookback, bool) or not isinstance(lookback, int):
        raise ValueError("published Node Version has an invalid lookback")
    if isinstance(lookforward, bool) or not isinstance(lookforward, int):
        raise ValueError("published Node Version has an invalid lookforward")
    if impact not in {
        "windowed_forward",
        "same_cross_section",
        "from_revised_session_forward",
    }:
        raise ValueError("published Node Version has an unsupported revision policy")
    return IncrementalExecutionContract(
        execution_mode=mode,
        partition_key=tuple(partition_key),
        lookback=lookback,
        lookforward=lookforward,
        revision_impact_policy=impact,
    )
