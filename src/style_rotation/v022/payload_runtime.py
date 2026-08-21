from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from pathlib import Path

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import CANONICAL_SERIALIZATION_VERSION, sha256_hexdigest
from style_rotation.lineage.service import DependencyInput
from style_rotation.v022.incremental_runtime import IncrementalRunPlan, PartitionWork


@dataclass(frozen=True, slots=True)
class ExecutedPartitionPayload:
    partition_key_hash: str
    content: bytes
    statistics: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PublishedNodeOutput:
    node_run_id: uuid.UUID
    payload_manifest_id: uuid.UUID
    manifest_artifact_id: uuid.UUID
    payload_partition_ids: tuple[uuid.UUID, ...]
    executed_partition_count: int
    reused_partition_count: int
    reused_publication: bool


@dataclass(frozen=True, slots=True)
class NodeOutputPayload:
    output_port_key: str
    executed_payloads: tuple[ExecutedPartitionPayload, ...]
    retention_class: str = "cache"
    reused_partition_ids: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PublishedNodeOutputBundle:
    node_run_id: uuid.UUID
    node_output_bundle_id: uuid.UUID
    bundle_artifact_id: uuid.UUID
    outputs: tuple[PublishedNodeOutput, ...]
    reused_publication: bool


@dataclass(frozen=True, slots=True)
class _PreparedOutput:
    context: _PublicationContext
    prepared: tuple[_PreparedPartition, ...]
    dependencies: tuple[DependencyInput, ...]
    logical_fingerprint: str
    manifest_hash: str
    semantic_payload: Mapping[str, object]
    content_payload: Mapping[str, object]
    retention_class: str


@dataclass(frozen=True, slots=True)
class _StoredObject:
    content_hash: str
    storage_uri: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class _PreparedPartition:
    work: PartitionWork
    payload_partition_id: uuid.UUID
    descriptor_hash: str
    payload_object_id: uuid.UUID | None
    stored_object: _StoredObject | None
    row_count: int
    statistics: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _PublicationContext:
    node_run_id: uuid.UUID
    producer_artifact_id: uuid.UUID
    payload_contract_version_id: uuid.UUID
    physical_encoding_version_id: uuid.UUID
    output_port_key: str
    file_extension: str


class LocalPayloadObjectStore:
    """Filesystem backing for the content-addressed ``payload-object://`` namespace."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def publish(self, content: bytes, *, file_extension: str) -> _StoredObject:
        if not file_extension or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
            for character in file_extension
        ):
            raise ValueError("file_extension is not storage-safe")
        content_hash = hashlib.sha256(content).hexdigest()
        directory = self._root / "sha256"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{content_hash}.{file_extension}"
        if target.exists():
            existing = target.read_bytes()
            if hashlib.sha256(existing).hexdigest() != content_hash:
                raise ValueError(f"content-addressed object is corrupt: {target}")
        else:
            temporary_name: str | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
                    temporary_name = handle.name
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, target)
                temporary_name = None
            finally:
                if temporary_name is not None:
                    Path(temporary_name).unlink(missing_ok=True)
        return _StoredObject(
            content_hash=content_hash,
            storage_uri=f"payload-object://sha256/{content_hash}.{file_extension}",
            byte_size=len(content),
        )

    def observe(self, storage_uri: str) -> tuple[str, int]:
        """Read an object from this restored root and return its actual hash and size."""

        match = re.fullmatch(
            r"payload-object://sha256/([0-9a-f]{64})\.([a-z0-9][a-z0-9._-]{0,19})",
            storage_uri,
        )
        if match is None:
            raise ValueError("storage_uri is not a canonical Payload Object URI")
        target = (self._root / "sha256" / f"{match.group(1)}.{match.group(2)}").resolve()
        if target.parent != (self._root / "sha256").resolve():
            raise ValueError("storage_uri escapes the Payload Object root")
        digest = hashlib.sha256()
        byte_count = 0
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                byte_count += len(chunk)
        return digest.hexdigest(), byte_count

    def read(self, storage_uri: str) -> bytes:
        """Read exact content-addressed bytes from this configured object root."""

        match = re.fullmatch(
            r"payload-object://sha256/([0-9a-f]{64})\.([a-z0-9][a-z0-9._-]{0,19})",
            storage_uri,
        )
        if match is None:
            raise ValueError("storage_uri is not a canonical Payload Object URI")
        target = (self._root / "sha256" / f"{match.group(1)}.{match.group(2)}").resolve()
        if target.parent != (self._root / "sha256").resolve():
            raise ValueError("storage_uri escapes the Payload Object root")
        content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != match.group(1):
            raise ValueError("content-addressed object bytes do not match their URI")
        return content

    def evict(
        self,
        storage_uri: str,
        *,
        expected_content_hash: str,
        expected_byte_size: int,
    ) -> bool:
        """Remove one exact content-addressed object after verifying its identity.

        A missing target is an idempotent replay.  Callers must independently prove
        that the object is not reachable from a current strong root before invoking
        this method; this class deliberately owns filesystem safety only.
        """

        match = re.fullmatch(
            r"payload-object://sha256/([0-9a-f]{64})\.([a-z0-9][a-z0-9._-]{0,19})",
            storage_uri,
        )
        if match is None:
            raise ValueError("storage_uri is not a canonical Payload Object URI")
        if match.group(1) != expected_content_hash:
            raise ValueError("storage_uri hash does not match the GC plan")
        target = (self._root / "sha256" / f"{match.group(1)}.{match.group(2)}").resolve()
        if target.parent != (self._root / "sha256").resolve():
            raise ValueError("storage_uri escapes the Payload Object root")
        if not target.exists():
            return False
        actual_hash, actual_size = self.observe(storage_uri)
        if actual_hash != expected_content_hash or actual_size != expected_byte_size:
            raise ValueError("Payload Object changed after the GC plan was frozen")
        target.unlink()
        return True


def publish_node_output(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    node_run_id: uuid.UUID,
    output_port_key: str,
    plan: IncrementalRunPlan,
    executed_payloads: tuple[ExecutedPartitionPayload, ...],
    encoding_key: str = "canonical_parquet",
    encoding_version: int = 1,
    retention_class: str = "cache",
) -> PublishedNodeOutput:
    """Convenience wrapper over the atomic bundle publisher for one-output Nodes."""

    bundle = publish_node_output_bundle(
        engine,
        object_store=object_store,
        node_run_id=node_run_id,
        plan=plan,
        outputs=(NodeOutputPayload(output_port_key, executed_payloads, retention_class),),
        encoding_key=encoding_key,
        encoding_version=encoding_version,
    )
    return bundle.outputs[0]


def publish_node_output_bundle(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    node_run_id: uuid.UUID,
    plan: IncrementalRunPlan,
    outputs: tuple[NodeOutputPayload, ...],
    encoding_key: str = "canonical_parquet",
    encoding_version: int = 1,
) -> PublishedNodeOutputBundle:
    """Publish every declared output port in one all-or-nothing transaction."""

    if not outputs:
        raise ValueError("an output bundle must contain at least one output")
    output_keys = tuple(item.output_port_key for item in outputs)
    if len(set(output_keys)) != len(output_keys):
        raise ValueError("output bundle port keys must be unique")
    contexts = _load_bundle_contexts(
        engine,
        node_run_id=node_run_id,
        output_port_keys=output_keys,
        encoding_key=encoding_key,
        encoding_version=encoding_version,
    )
    if len(outputs) > 1 and plan.reuse_count and any(
        set(item.reused_partition_ids)
        != {
            work.partition_key_hash
            for work in plan.partitions
            if work.disposition == "reuse"
        }
        for item in outputs
    ):
        raise ValueError(
            "multi-output reuse requires a prior Payload Partition id per port and partition"
        )
    prepared_outputs = tuple(
        _prepare_output(
            engine,
            object_store=object_store,
            context=contexts[item.output_port_key],
            plan=plan,
            payload=item,
        )
        for item in sorted(outputs, key=lambda candidate: candidate.output_port_key)
    )
    return _publish_prepared_bundle(engine, node_run_id, prepared_outputs)


def _load_publication_context(
    engine: Engine,
    *,
    node_run_id: uuid.UUID,
    output_port_key: str,
    encoding_key: str,
    encoding_version: int,
) -> _PublicationContext:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    """
                    SELECT run.status,run.artifact_id,artifact.status AS artifact_status,
                           port.payload_contract_version_id,
                           encoding.physical_encoding_version_id,encoding.file_extension
                    FROM processing.node_run run
                    JOIN lineage.artifact artifact ON artifact.artifact_id=run.artifact_id
                    JOIN processing.node_port port
                      ON port.node_version_id=run.node_version_id
                     AND port.direction='output' AND port.port_key=:output_port_key
                    JOIN data.physical_encoding_version encoding
                      ON encoding.encoding_key=:encoding_key
                     AND encoding.version_number=:encoding_version
                    WHERE run.node_run_id=:node_run_id
                    """
                ),
                {
                    "node_run_id": node_run_id,
                    "output_port_key": output_port_key,
                    "encoding_key": encoding_key,
                    "encoding_version": encoding_version,
                },
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise ValueError("unknown Node Run, output port, or encoding")
    if row["artifact_status"] != "published":
        raise ValueError("Node Run artifact must be published before its output")
    if row["status"] not in {"running", "completed"}:
        raise ValueError("Node Run output cannot be published from its current status")
    return _PublicationContext(
        node_run_id=node_run_id,
        producer_artifact_id=row["artifact_id"],
        payload_contract_version_id=row["payload_contract_version_id"],
        physical_encoding_version_id=row["physical_encoding_version_id"],
        output_port_key=output_port_key,
        file_extension=row["file_extension"],
    )


def _load_bundle_contexts(
    engine: Engine,
    *,
    node_run_id: uuid.UUID,
    output_port_keys: tuple[str, ...],
    encoding_key: str,
    encoding_version: int,
) -> dict[str, _PublicationContext]:
    with engine.connect() as connection:
        declared = tuple(
            connection.execute(
                text(
                    """
                    SELECT port.port_key
                    FROM processing.node_run run
                    JOIN processing.node_port port
                      ON port.node_version_id=run.node_version_id
                    WHERE run.node_run_id=:node_run_id AND port.direction='output'
                    ORDER BY port.port_key
                    """
                ),
                {"node_run_id": node_run_id},
            ).scalars()
        )
    if not declared:
        raise ValueError("unknown Node Run or Node Version has no output ports")
    if tuple(sorted(output_port_keys)) != declared:
        raise ValueError("atomic output bundle must contain every declared output port")
    return {
        port_key: _load_publication_context(
            engine,
            node_run_id=node_run_id,
            output_port_key=port_key,
            encoding_key=encoding_key,
            encoding_version=encoding_version,
        )
        for port_key in declared
    }


def _prepare_partitions(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    context: _PublicationContext,
    plan: IncrementalRunPlan,
    payload_by_hash: Mapping[str, ExecutedPartitionPayload],
    reused_partition_ids: Mapping[str, str] | None = None,
) -> tuple[_PreparedPartition, ...]:
    result: list[_PreparedPartition] = []
    with engine.connect() as connection:
        for work in plan.partitions:
            if work.disposition == "reuse":
                override = (reused_partition_ids or {}).get(work.partition_key_hash)
                reused_partition_id = override or work.reused_payload_partition_id
                if reused_partition_id is None:
                    raise ValueError("reused work is missing payload_partition_id")
                partition_id = uuid.UUID(reused_partition_id)
                row = (
                    connection.execute(
                        text(
                            """
                            SELECT partition_descriptor_hash,row_or_item_count,
                                   partition_key,coverage_document,statistics
                            FROM data.payload_partition
                            WHERE payload_partition_id=:partition_id
                            """
                        ),
                        {"partition_id": partition_id},
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise ValueError("planned reused Payload Partition does not exist")
                _validate_reused_partition(work, row["partition_key"], row["coverage_document"])
                result.append(
                    _PreparedPartition(
                        work=work,
                        payload_partition_id=partition_id,
                        descriptor_hash=row["partition_descriptor_hash"],
                        payload_object_id=None,
                        stored_object=None,
                        row_count=row["row_or_item_count"],
                        statistics=row["statistics"],
                    )
                )
                continue
            payload = payload_by_hash[work.partition_key_hash]
            row_count = _parquet_row_count(payload.content)
            stored = object_store.publish(payload.content, file_extension=context.file_extension)
            descriptor_hash = sha256_hexdigest(
                {
                    "object_content_hash": stored.content_hash,
                    "payload_contract_version_id": context.payload_contract_version_id,
                    "physical_encoding_version_id": context.physical_encoding_version_id,
                    "partition_key_hash": work.partition_key_hash,
                    "partition_key": dict(sorted(work.partition_key.items())),
                    "output_sessions": work.output_sessions,
                    "row_count": row_count,
                    "coverage_document": _coverage(work),
                    "statistics": dict(payload.statistics),
                }
            )
            result.append(
                _PreparedPartition(
                    work=work,
                    payload_partition_id=uuid.uuid5(
                        uuid.NAMESPACE_URL, f"bird-v022:payload-partition:{descriptor_hash}"
                    ),
                    descriptor_hash=descriptor_hash,
                    payload_object_id=uuid.uuid5(
                        uuid.NAMESPACE_URL, f"bird-v022:payload-object:{stored.content_hash}"
                    ),
                    stored_object=stored,
                    row_count=row_count,
                    statistics=payload.statistics,
                )
            )
    return tuple(result)


def _publication_dependencies(
    engine: Engine,
    context: _PublicationContext,
    prepared: tuple[_PreparedPartition, ...],
) -> tuple[DependencyInput, ...]:
    source_artifacts: set[uuid.UUID] = set()
    reused_ids = [
        item.payload_partition_id for item in prepared if item.work.disposition == "reuse"
    ]
    if reused_ids:
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT DISTINCT ON (link.payload_partition_id)
                           link.payload_partition_id,manifest.artifact_id
                    FROM data.payload_manifest_partition link
                    JOIN data.payload_manifest manifest
                      ON manifest.payload_manifest_id=link.payload_manifest_id
                    WHERE link.payload_partition_id = ANY(:partition_ids)
                    ORDER BY link.payload_partition_id,manifest.created_at,
                             manifest.payload_manifest_id
                    """
                    ),
                    {"partition_ids": reused_ids},
                )
                .mappings()
                .all()
            )
        source_by_partition = {row["payload_partition_id"]: row["artifact_id"] for row in rows}
        if set(source_by_partition) != set(reused_ids):
            raise ValueError("reused Payload Partition has no source Manifest")
        source_artifacts.update(source_by_partition.values())
    dependencies = [DependencyInput(context.producer_artifact_id, "producer_node_run", 0)]
    dependencies.extend(
        DependencyInput(artifact_id, "reused_payload_manifest", ordinal)
        for ordinal, artifact_id in enumerate(sorted(source_artifacts, key=str))
    )
    return tuple(dependencies)


def _prepare_output(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    context: _PublicationContext,
    plan: IncrementalRunPlan,
    payload: NodeOutputPayload,
) -> _PreparedOutput:
    if payload.retention_class not in {
        "cache",
        "research",
        "product",
        "evidence",
        "export",
    }:
        raise ValueError("unsupported retention_class for Node output")
    payload_by_hash = _index_executed_payloads(payload.executed_payloads)
    expected_execution = {
        item.partition_key_hash for item in plan.partitions if item.disposition == "execute"
    }
    if set(payload_by_hash) != expected_execution:
        raise ValueError("executed payloads must exactly match planned execute partitions")
    prepared = _prepare_partitions(
        engine,
        object_store=object_store,
        context=context,
        plan=plan,
        payload_by_hash=payload_by_hash,
        reused_partition_ids=payload.reused_partition_ids,
    )
    dependencies = _publication_dependencies(engine, context, prepared)
    partition_identity = tuple(
        {
            "ordinal": ordinal,
            "partition_key_hash": item.work.partition_key_hash,
            "descriptor_hash": item.descriptor_hash,
            "payload_partition_id": item.payload_partition_id,
        }
        for ordinal, item in enumerate(prepared)
    )
    logical_fingerprint = sha256_hexdigest(
        {
            "payload_contract_version_id": context.payload_contract_version_id,
            "partitions": tuple(item.descriptor_hash for item in prepared),
        }
    )
    manifest_hash = sha256_hexdigest(
        {
            "node_run_id": context.node_run_id,
            "output_port_key": context.output_port_key,
            "physical_encoding_version_id": context.physical_encoding_version_id,
            "logical_payload_fingerprint": logical_fingerprint,
            "partitions": partition_identity,
        }
    )
    semantic_payload: Mapping[str, object] = {
        "node_run_id": context.node_run_id,
        "output_port_key": context.output_port_key,
        "payload_contract_version_id": context.payload_contract_version_id,
        "logical_payload_fingerprint": logical_fingerprint,
        "partition_descriptors": tuple(item.descriptor_hash for item in prepared),
    }
    content_payload: Mapping[str, object] = {
        **semantic_payload,
        "physical_encoding_version_id": context.physical_encoding_version_id,
        "manifest_hash": manifest_hash,
        "partitions": partition_identity,
    }
    return _PreparedOutput(
        context=context,
        prepared=prepared,
        dependencies=dependencies,
        logical_fingerprint=logical_fingerprint,
        manifest_hash=manifest_hash,
        semantic_payload=semantic_payload,
        content_payload=content_payload,
        retention_class=payload.retention_class,
    )


def _publish_prepared_bundle(
    engine: Engine,
    node_run_id: uuid.UUID,
    outputs: tuple[_PreparedOutput, ...],
) -> PublishedNodeOutputBundle:
    bundle_fingerprint = sha256_hexdigest(
        tuple(
            (item.context.output_port_key, item.manifest_hash)
            for item in sorted(outputs, key=lambda candidate: candidate.context.output_port_key)
        )
    )
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"v022-node-output-bundle:{node_run_id}"},
        )
        published_outputs: list[PublishedNodeOutput] = []
        output_dependencies: list[DependencyInput] = []
        output_members: list[tuple[str, uuid.UUID]] = []
        for item in outputs:
            artifact_id, artifact_reused, manifest_id = _publish_artifact_in_connection(
                connection,
                artifact_type="v022_payload_manifest",
                artifact_key=(
                    f"node-output:{node_run_id}:{item.context.output_port_key}"
                ),
                version_number=1,
                semantic_payload=item.semantic_payload,
                content_payload=item.content_payload,
                dependencies=item.dependencies,
                reason=(
                    f"publish Node Run output {node_run_id}:"
                    f"{item.context.output_port_key}"
                ),
                draft_writer=partial(_write_prepared_manifest, output=item),
            )
            if manifest_id is None:
                manifest_id = connection.execute(
                    text(
                        "SELECT payload_manifest_id FROM data.payload_manifest "
                        "WHERE artifact_id=:artifact_id"
                    ),
                    {"artifact_id": artifact_id},
                ).scalar_one()
            output_members.append((item.context.output_port_key, manifest_id))
            output_dependencies.append(
                DependencyInput(
                    artifact_id,
                    "output_manifest",
                    len(output_dependencies) + 1,
                )
            )
            published_outputs.append(
                PublishedNodeOutput(
                    node_run_id=node_run_id,
                    payload_manifest_id=manifest_id,
                    manifest_artifact_id=artifact_id,
                    payload_partition_ids=tuple(
                        partition.payload_partition_id for partition in item.prepared
                    ),
                    executed_partition_count=sum(
                        partition.work.disposition == "execute"
                        for partition in item.prepared
                    ),
                    reused_partition_count=sum(
                        partition.work.disposition == "reuse"
                        for partition in item.prepared
                    ),
                    reused_publication=artifact_reused,
                )
            )
        producer_artifact_id = outputs[0].context.producer_artifact_id
        _finalize_node_run(connection, node_run_id)
        bundle_semantic: Mapping[str, object] = {
            "node_run_id": node_run_id,
            "bundle_fingerprint": bundle_fingerprint,
            "outputs": tuple(
                (item.context.output_port_key, item.logical_fingerprint)
                for item in outputs
            ),
        }
        bundle_content: Mapping[str, object] = {
            **bundle_semantic,
            "manifests": tuple(output_members),
        }
        bundle_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird-v022:node-output-bundle:{bundle_fingerprint}"
        )
        bundle_artifact_id, bundle_reused, _ = _publish_artifact_in_connection(
            connection,
            artifact_type="v022_node_output_bundle",
            artifact_key=str(node_run_id),
            version_number=1,
            semantic_payload=bundle_semantic,
            content_payload=bundle_content,
            dependencies=(
                DependencyInput(producer_artifact_id, "producer_node_run", 0),
                *output_dependencies,
            ),
            reason=f"publish atomic Node Run output bundle {node_run_id}",
            draft_writer=partial(
                _write_output_bundle,
                bundle_id=bundle_id,
                node_run_id=node_run_id,
                bundle_fingerprint=bundle_fingerprint,
                members=tuple(output_members),
            ),
        )
    return PublishedNodeOutputBundle(
        node_run_id=node_run_id,
        node_output_bundle_id=bundle_id,
        bundle_artifact_id=bundle_artifact_id,
        outputs=tuple(published_outputs),
        reused_publication=bundle_reused,
    )


def _finalize_node_run(connection: Connection, node_run_id: uuid.UUID) -> None:
    connection.execute(
        text(
            """
            UPDATE processing.node_run_partition SET status='completed'
            WHERE node_run_id=:node_run_id AND status='planned'
            """
        ),
        {"node_run_id": node_run_id},
    )
    connection.execute(
        text(
            """
            UPDATE processing.node_run SET status='completed',completed_at=:completed_at
            WHERE node_run_id=:node_run_id AND status='running'
            """
        ),
        {"node_run_id": node_run_id, "completed_at": datetime.now(UTC)},
    )


def _write_prepared_manifest(
    connection: Connection, artifact_id: uuid.UUID, *, output: _PreparedOutput
) -> uuid.UUID:
    return _write_manifest(
        connection,
        artifact_id,
        context=output.context,
        prepared=output.prepared,
        logical_fingerprint=output.logical_fingerprint,
        manifest_hash=output.manifest_hash,
        retention_class=output.retention_class,
        finalize_run=False,
    )


def _write_output_bundle(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    bundle_id: uuid.UUID,
    node_run_id: uuid.UUID,
    bundle_fingerprint: str,
    members: tuple[tuple[str, uuid.UUID], ...],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO processing.node_output_bundle (
              node_output_bundle_id,node_run_id,artifact_id,bundle_fingerprint,output_count
            ) VALUES (:bundle_id,:node_run_id,:artifact_id,:fingerprint,:output_count)
            """
        ),
        {
            "bundle_id": bundle_id,
            "node_run_id": node_run_id,
            "artifact_id": artifact_id,
            "fingerprint": bundle_fingerprint,
            "output_count": len(members),
        },
    )
    for ordinal, (port_key, manifest_id) in enumerate(members):
        connection.execute(
            text(
                """
                INSERT INTO processing.node_output_bundle_member (
                  node_output_bundle_id,output_port_key,payload_manifest_id,ordinal
                ) VALUES (:bundle_id,:port_key,:manifest_id,:ordinal)
                """
            ),
            {
                "bundle_id": bundle_id,
                "port_key": port_key,
                "manifest_id": manifest_id,
                "ordinal": ordinal,
            },
        )


def _publish_artifact_in_connection(
    connection: Connection,
    *,
    artifact_type: str,
    artifact_key: str,
    version_number: int,
    semantic_payload: Mapping[str, object],
    content_payload: Mapping[str, object],
    dependencies: tuple[DependencyInput, ...],
    reason: str,
    draft_writer: Callable[[Connection, uuid.UUID], uuid.UUID | None],
) -> tuple[uuid.UUID, bool, uuid.UUID | None]:
    dependency_rows = []
    for dependency in dependencies:
        row = connection.execute(
            text(
                "SELECT artifact_id,status,semantic_fingerprint,content_hash "
                "FROM lineage.artifact WHERE artifact_id=:artifact_id"
            ),
            {"artifact_id": dependency.artifact_id},
        ).mappings().one_or_none()
        if row is None or row["status"] != "published":
            raise ValueError("atomic bundle dependency is not published")
        dependency_rows.append(row)
    semantic_fingerprint = sha256_hexdigest(
        {
            "artifact_identity": {
                "artifact_type": artifact_type,
                "artifact_key": artifact_key,
                "version_number": version_number,
            },
            "semantic_payload": semantic_payload,
            "dependencies": [
                {
                    "role": dependency.role,
                    "ordinal": dependency.ordinal,
                    "semantic_fingerprint": row["semantic_fingerprint"],
                }
                for dependency, row in zip(dependencies, dependency_rows, strict=True)
            ],
        }
    )
    content_hash = sha256_hexdigest(
        {
            "semantic_fingerprint": semantic_fingerprint,
            "content_payload": content_payload,
            "dependencies": [
                {
                    "role": dependency.role,
                    "ordinal": dependency.ordinal,
                    "content_hash": row["content_hash"],
                }
                for dependency, row in zip(dependencies, dependency_rows, strict=True)
            ],
        }
    )
    artifact = connection.execute(
        text(
            """
            SELECT artifact_id,status,semantic_fingerprint,content_hash
            FROM lineage.artifact
            WHERE artifact_type=:artifact_type AND artifact_key=:artifact_key
              AND version_number=:version_number
            FOR UPDATE
            """
        ),
        {
            "artifact_type": artifact_type,
            "artifact_key": artifact_key,
            "version_number": version_number,
        },
    ).mappings().one_or_none()
    if artifact is not None and artifact["status"] != "draft":
        if artifact["status"] != "published":
            raise ValueError("atomic bundle artifact is not reusable")
        if (
            artifact["semantic_fingerprint"] != semantic_fingerprint
            or artifact["content_hash"] != content_hash
        ):
            raise ValueError("atomic bundle artifact identity conflicts with existing content")
        return artifact["artifact_id"], True, None
    artifact_id = artifact["artifact_id"] if artifact is not None else uuid.uuid4()
    if artifact is None:
        connection.execute(
            text(
                """
                INSERT INTO lineage.artifact (
                  artifact_id,artifact_type,artifact_key,version_number,status
                ) VALUES (:artifact_id,:artifact_type,:artifact_key,:version_number,'draft')
                """
            ),
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "artifact_key": artifact_key,
                "version_number": version_number,
            },
        )
    connection.execute(
        text("DELETE FROM lineage.artifact_dependency WHERE artifact_id=:artifact_id"),
        {"artifact_id": artifact_id},
    )
    for dependency in dependencies:
        connection.execute(
            text(
                """
                INSERT INTO lineage.artifact_dependency (
                  artifact_dependency_id,artifact_id,depends_on_artifact_id,role,ordinal
                ) VALUES (:id,:artifact_id,:depends_on,:role,:ordinal)
                """
            ),
            {
                "id": uuid.uuid4(),
                "artifact_id": artifact_id,
                "depends_on": dependency.artifact_id,
                "role": dependency.role,
                "ordinal": dependency.ordinal,
            },
        )
    writer_result = draft_writer(connection, artifact_id)
    _set_status_context(connection, reason)
    connection.execute(
        text(
            """
            UPDATE lineage.artifact
            SET semantic_fingerprint=:semantic_fingerprint,content_hash=:content_hash,
                published_at=:published_at,status='published'
            WHERE artifact_id=:artifact_id
            """
        ),
        {
            "artifact_id": artifact_id,
            "semantic_fingerprint": semantic_fingerprint,
            "content_hash": content_hash,
            "published_at": datetime.now(UTC),
        },
    )
    manifest, lineage_hash = _build_lineage_manifest(connection, artifact_id)
    connection.execute(
        text(
            """
            INSERT INTO lineage.lineage_manifest (
              lineage_manifest_id,root_artifact_id,root_content_hash,manifest_hash,
              canonical_version,manifest
            ) VALUES (
              :id,:artifact_id,:content_hash,:manifest_hash,:canonical_version,
              CAST(:manifest AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact_id": artifact_id,
            "content_hash": content_hash,
            "manifest_hash": lineage_hash,
            "canonical_version": CANONICAL_SERIALIZATION_VERSION,
            "manifest": _json(manifest),
        },
    )
    return artifact_id, False, writer_result


def _set_status_context(connection: Connection, reason: str) -> None:
    connection.execute(
        text(
            "SELECT set_config('style_rotation.status_event_id',:event_id,true),"
            "set_config('style_rotation.status_reason',:reason,true)"
        ),
        {"event_id": str(uuid.uuid4()), "reason": reason},
    )


def _build_lineage_manifest(
    connection: Connection, root_artifact_id: uuid.UUID
) -> tuple[dict[str, object], str]:
    artifacts = connection.execute(
        text(
            """
            WITH RECURSIVE tree(artifact_id) AS (
              SELECT :root_artifact_id
              UNION
              SELECT dependency.depends_on_artifact_id
              FROM lineage.artifact_dependency dependency
              JOIN tree ON dependency.artifact_id=tree.artifact_id
            )
            SELECT artifact_id,artifact_type,artifact_key,version_number,
                   semantic_fingerprint,content_hash
            FROM lineage.artifact WHERE artifact_id IN (SELECT artifact_id FROM tree)
            ORDER BY artifact_type,artifact_key,version_number,artifact_id
            """
        ),
        {"root_artifact_id": root_artifact_id},
    ).mappings().all()
    artifact_ids = [row["artifact_id"] for row in artifacts]
    dependencies = connection.execute(
        text(
            """
            SELECT artifact_id,depends_on_artifact_id,role,ordinal
            FROM lineage.artifact_dependency
            WHERE artifact_id = ANY(:artifact_ids)
            ORDER BY artifact_id,role,ordinal,depends_on_artifact_id
            """
        ),
        {"artifact_ids": artifact_ids},
    ).mappings().all()
    manifest: dict[str, object] = {
        "root_artifact_id": str(root_artifact_id),
        "artifacts": [_json_row(row) for row in artifacts],
        "dependencies": [_json_row(row) for row in dependencies],
    }
    return manifest, sha256_hexdigest(manifest)


def _json_row(row: RowMapping) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in row.items():
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


def _write_manifest(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _PublicationContext,
    prepared: tuple[_PreparedPartition, ...],
    logical_fingerprint: str,
    manifest_hash: str,
    retention_class: str,
    finalize_run: bool = True,
) -> uuid.UUID:
    persisted = (
        connection.execute(
            text(
                """
            SELECT partition_key_hash,status,source_revision_fingerprint
            FROM processing.node_run_partition
            WHERE node_run_id=:node_run_id
            """
            ),
            {"node_run_id": context.node_run_id},
        )
        .mappings()
        .all()
    )
    expected = {
        item.work.partition_key_hash: (
            "reused" if item.work.disposition == "reuse" else "planned",
            item.work.source_revision_fingerprint,
        )
        for item in prepared
    }
    actual = {
        row["partition_key_hash"]: (row["status"], row["source_revision_fingerprint"])
        for row in persisted
    }
    if actual != expected:
        raise ValueError("persisted Node Run partition plan does not match publication")
    for item in prepared:
        if item.stored_object is None or item.payload_object_id is None:
            continue
        connection.execute(
            text(
                """
                INSERT INTO data.payload_object (
                  payload_object_id,object_content_hash,storage_uri,byte_size,
                  object_state,verification_status,verified_at
                ) VALUES (
                  :object_id,:content_hash,:storage_uri,:byte_size,
                  'published','verified',:verified_at
                ) ON CONFLICT (object_content_hash) DO NOTHING
                """
            ),
            {
                "object_id": item.payload_object_id,
                "content_hash": item.stored_object.content_hash,
                "storage_uri": item.stored_object.storage_uri,
                "byte_size": item.stored_object.byte_size,
                "verified_at": datetime.now(UTC),
            },
        )
        object_row = (
            connection.execute(
                text(
                    "SELECT payload_object_id,storage_uri,byte_size FROM data.payload_object "
                    "WHERE object_content_hash=:content_hash"
                ),
                {"content_hash": item.stored_object.content_hash},
            )
            .mappings()
            .one()
        )
        if (
            object_row["payload_object_id"] != item.payload_object_id
            or object_row["storage_uri"] != item.stored_object.storage_uri
            or object_row["byte_size"] != item.stored_object.byte_size
        ):
            raise ValueError("content-addressed Payload Object identity conflict")
        connection.execute(
            text(
                """
                INSERT INTO data.payload_partition (
                  payload_partition_id,payload_object_id,partition_descriptor_hash,
                  byte_size,row_or_item_count,partition_key,coverage_document,statistics
                ) VALUES (
                  :partition_id,:object_id,:descriptor_hash,:byte_size,:row_count,
                  CAST(:partition_key AS jsonb),CAST(:coverage AS jsonb),
                  CAST(:statistics AS jsonb)
                ) ON CONFLICT (partition_descriptor_hash) DO NOTHING
                """
            ),
            {
                "partition_id": item.payload_partition_id,
                "object_id": item.payload_object_id,
                "descriptor_hash": item.descriptor_hash,
                "byte_size": item.stored_object.byte_size,
                "row_count": item.row_count,
                "partition_key": _json(
                    {
                        "fields": dict(sorted(item.work.partition_key.items())),
                        "partition_key_hash": item.work.partition_key_hash,
                    }
                ),
                "coverage": _json(_coverage(item.work)),
                "statistics": _json(dict(item.statistics)),
            },
        )
        partition_row = (
            connection.execute(
                text(
                    """
                SELECT payload_partition_id,payload_object_id,row_or_item_count,
                       partition_key,coverage_document,statistics
                FROM data.payload_partition
                WHERE partition_descriptor_hash=:descriptor_hash
                """
                ),
                {"descriptor_hash": item.descriptor_hash},
            )
            .mappings()
            .one()
        )
        expected_key = {
            "fields": dict(sorted(item.work.partition_key.items())),
            "partition_key_hash": item.work.partition_key_hash,
        }
        if (
            partition_row["payload_partition_id"] != item.payload_partition_id
            or partition_row["payload_object_id"] != item.payload_object_id
            or partition_row["row_or_item_count"] != item.row_count
            or partition_row["partition_key"] != expected_key
            or partition_row["coverage_document"] != _coverage(item.work)
            or partition_row["statistics"] != dict(item.statistics)
        ):
            raise ValueError("content-addressed Payload Partition identity conflict")
    manifest_id = uuid.uuid4()
    total_bytes = connection.execute(
        text(
            "SELECT coalesce(sum(byte_size),0) FROM data.payload_partition "
            "WHERE payload_partition_id = ANY(:partition_ids)"
        ),
        {"partition_ids": [item.payload_partition_id for item in prepared]},
    ).scalar_one()
    output_sessions = sorted(
        {session for item in prepared for session in item.work.output_sessions}
    )
    connection.execute(
        text(
            """
            INSERT INTO data.payload_manifest (
              payload_manifest_id,artifact_id,payload_contract_version_id,
              physical_encoding_version_id,producer_artifact_id,
              producer_output_port_key,logical_payload_fingerprint,manifest_hash,
              partition_count,byte_size,row_or_item_count,coverage_document,
              retention_class,materialization_state
            ) VALUES (
              :manifest_id,:artifact_id,:contract_id,:encoding_id,:producer_id,
              :output_port_key,:logical_fingerprint,:manifest_hash,
              :partition_count,:byte_size,:row_count,CAST(:coverage AS jsonb),
              :retention_class,'materialized'
            )
            """
        ),
        {
            "manifest_id": manifest_id,
            "artifact_id": artifact_id,
            "contract_id": context.payload_contract_version_id,
            "encoding_id": context.physical_encoding_version_id,
            "producer_id": context.producer_artifact_id,
            "output_port_key": context.output_port_key,
            "logical_fingerprint": logical_fingerprint,
            "manifest_hash": manifest_hash,
            "partition_count": len(prepared),
            "byte_size": total_bytes,
            "row_count": sum(item.row_count for item in prepared),
            "coverage": _json(
                {
                    "start": output_sessions[0].isoformat(),
                    "end": output_sessions[-1].isoformat(),
                    "session_count": len(output_sessions),
                }
            ),
            "retention_class": retention_class,
        },
    )
    for ordinal, item in enumerate(prepared):
        connection.execute(
            text(
                """
                INSERT INTO data.payload_manifest_partition (
                  payload_manifest_id,payload_partition_id,ordinal
                ) VALUES (:manifest_id,:partition_id,:ordinal)
                """
            ),
            {
                "manifest_id": manifest_id,
                "partition_id": item.payload_partition_id,
                "ordinal": ordinal,
            },
        )
        if finalize_run and item.work.disposition == "execute":
            connection.execute(
                text(
                    """
                    UPDATE processing.node_run_partition SET status='completed'
                    WHERE node_run_id=:node_run_id
                      AND partition_key_hash=:partition_key_hash AND status='planned'
                    """
                ),
                {
                    "node_run_id": context.node_run_id,
                    "partition_key_hash": item.work.partition_key_hash,
                },
            )
    row_count = sum(item.row_count for item in prepared)
    missing_count = sum(
        _quality_statistic(item.statistics, "missing_count") for item in prepared
    )
    invalid_count = sum(
        _quality_statistic(item.statistics, "invalid_count") for item in prepared
    )
    valid_count = max(row_count - missing_count - invalid_count, 0)
    connection.execute(
        text(
            """
            INSERT INTO data.payload_quality_summary (
              payload_quality_summary_id,payload_manifest_id,quality_status,
              missing_count,invalid_count,coverage_ratio,quality_document
            ) VALUES (
              :id,:manifest,:status,:missing,:invalid,:coverage,CAST(:document AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid5(
                uuid.NAMESPACE_URL, f"bird-v022:payload-quality:{manifest_hash}"
            ),
            "manifest": manifest_id,
            "status": "warning" if missing_count or invalid_count else "passed",
            "missing": missing_count,
            "invalid": invalid_count,
            "coverage": Decimal(valid_count) / Decimal(row_count) if row_count else None,
            "document": _json(
                {
                    "policy": "node_output_partition_statistics_v1",
                    "partition_count": len(prepared),
                    "row_count": row_count,
                }
            ),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO processing.node_run_output (
              node_run_id,output_port_key,payload_manifest_id
            ) VALUES (:node_run_id,:output_port_key,:manifest_id)
            """
        ),
        {
            "node_run_id": context.node_run_id,
            "output_port_key": context.output_port_key,
            "manifest_id": manifest_id,
        },
    )
    if finalize_run:
        connection.execute(
            text(
                """
                UPDATE processing.node_run
                SET status='completed',completed_at=:completed_at
                WHERE node_run_id=:node_run_id AND status='running'
                """
            ),
            {"node_run_id": context.node_run_id, "completed_at": datetime.now(UTC)},
        )
    return manifest_id


def _quality_statistic(statistics: Mapping[str, object], key: str) -> int:
    value = statistics.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Payload Partition {key} must be a non-negative integer")
    return value


def _index_executed_payloads(
    payloads: tuple[ExecutedPartitionPayload, ...],
) -> dict[str, ExecutedPartitionPayload]:
    result: dict[str, ExecutedPartitionPayload] = {}
    for payload in payloads:
        if payload.partition_key_hash in result:
            raise ValueError("executed partition payload hashes must be unique")
        result[payload.partition_key_hash] = payload
    return result


def _parquet_row_count(content: bytes) -> int:
    try:
        metadata = pq.read_metadata(io.BytesIO(content))
    except Exception as error:
        raise ValueError("executed partition is not valid Parquet") from error
    return int(metadata.num_rows)


def _validate_reused_partition(
    work: PartitionWork, partition_key: object, coverage_document: object
) -> None:
    if not isinstance(partition_key, dict) or (
        partition_key.get("partition_key_hash") != work.partition_key_hash
    ):
        raise ValueError("reused Payload Partition key does not match planned work")
    if coverage_document != _coverage(work):
        raise ValueError("reused Payload Partition coverage does not match planned work")


def _coverage(work: PartitionWork) -> dict[str, object]:
    return {
        "start": work.output_sessions[0].isoformat(),
        "end": work.output_sessions[-1].isoformat(),
        "session_count": len(work.output_sessions),
    }


def _json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
