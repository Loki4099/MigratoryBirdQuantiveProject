from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import (
    CANONICAL_SERIALIZATION_VERSION,
    sha256_hexdigest,
)


@dataclass(frozen=True, slots=True)
class DependencyInput:
    artifact_id: uuid.UUID
    role: str
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class PublicationResult:
    artifact_id: uuid.UUID
    semantic_fingerprint: str
    content_hash: str
    manifest_hash: str
    reused: bool


class ArtifactService:
    """The sole application path for immutable artifact publication."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        *,
        artifact_type: str,
        artifact_key: str,
        version_number: int,
        semantic_payload: Any,
        content_payload: Any,
        dependencies: tuple[DependencyInput, ...] = (),
        reason: str = "publish immutable artifact",
        draft_writer: Callable[[Connection, uuid.UUID], None] | None = None,
    ) -> PublicationResult:
        if version_number < 1:
            raise ValueError("Artifact version_number must be positive")
        self._validate_dependencies(dependencies)
        with self._engine.begin() as connection:
            lock_key = f"{artifact_type}:{artifact_key}:{version_number}"
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            dependency_rows = self._dependency_rows(connection, dependencies)
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
                            "role": item.role,
                            "ordinal": item.ordinal,
                            "semantic_fingerprint": row["semantic_fingerprint"],
                        }
                        for item, row in zip(dependencies, dependency_rows, strict=True)
                    ],
                }
            )
            content_hash = sha256_hexdigest(
                {
                    "semantic_fingerprint": semantic_fingerprint,
                    "content_payload": content_payload,
                    "dependencies": [
                        {
                            "role": item.role,
                            "ordinal": item.ordinal,
                            "content_hash": row["content_hash"],
                        }
                        for item, row in zip(dependencies, dependency_rows, strict=True)
                    ],
                }
            )
            artifact = (
                connection.execute(
                    text(
                        """
                    SELECT * FROM lineage.artifact
                    WHERE artifact_type = :artifact_type AND artifact_key = :artifact_key
                      AND version_number = :version_number
                    FOR UPDATE
                    """
                    ),
                    {
                        "artifact_type": artifact_type,
                        "artifact_key": artifact_key,
                        "version_number": version_number,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if artifact is not None and artifact["status"] != "draft":
                return self._existing_result(
                    connection, artifact, semantic_fingerprint, content_hash
                )
            artifact_id = artifact["artifact_id"] if artifact is not None else uuid.uuid4()
            if artifact is None:
                connection.execute(
                    text(
                        """
                        INSERT INTO lineage.artifact (
                            artifact_id, artifact_type, artifact_key, version_number, status
                        ) VALUES (
                            :artifact_id, :artifact_type, :artifact_key, :version_number, 'draft'
                        )
                        """
                    ),
                    {
                        "artifact_id": artifact_id,
                        "artifact_type": artifact_type,
                        "artifact_key": artifact_key,
                        "version_number": version_number,
                    },
                )
            self._replace_draft_dependencies(connection, artifact_id, dependencies)
            if draft_writer is not None:
                draft_writer(connection, artifact_id)
            self._set_status_context(connection, reason)
            connection.execute(
                text(
                    """
                    UPDATE lineage.artifact
                    SET semantic_fingerprint = :semantic_fingerprint,
                        content_hash = :content_hash,
                        published_at = :published_at,
                        status = 'published'
                    WHERE artifact_id = :artifact_id
                    """
                ),
                {
                    "artifact_id": artifact_id,
                    "semantic_fingerprint": semantic_fingerprint,
                    "content_hash": content_hash,
                    "published_at": datetime.now(UTC),
                },
            )
            manifest, manifest_hash = self._build_manifest(connection, artifact_id)
            connection.execute(
                text(
                    """
                    INSERT INTO lineage.lineage_manifest (
                        lineage_manifest_id, root_artifact_id, root_content_hash,
                        manifest_hash, canonical_version, manifest
                    ) VALUES (
                        :manifest_id, :artifact_id, :content_hash, :manifest_hash,
                        :canonical_version, CAST(:manifest AS jsonb)
                    )
                    """
                ),
                {
                    "manifest_id": uuid.uuid4(),
                    "artifact_id": artifact_id,
                    "content_hash": content_hash,
                    "manifest_hash": manifest_hash,
                    "canonical_version": CANONICAL_SERIALIZATION_VERSION,
                    "manifest": __import__("json").dumps(manifest, ensure_ascii=False),
                },
            )
            return PublicationResult(
                artifact_id, semantic_fingerprint, content_hash, manifest_hash, False
            )

    def invalidate(
        self,
        artifact_id: uuid.UUID,
        reason: str,
        replacement_artifact_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, ...]:
        if not reason.strip():
            raise ValueError("Invalidation reason is required")
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": str(artifact_id)},
            )
            root = self._artifact_by_id(connection, artifact_id, for_update=True)
            if root["status"] == "invalidated":
                return ()
            if root["status"] == "draft":
                raise ValueError("Draft artifacts cannot be invalidated; publish or abandon them")
            if replacement_artifact_id is not None:
                replacement = self._artifact_by_id(connection, replacement_artifact_id)
                if replacement["status"] != "published":
                    raise ValueError("Replacement artifact must be published")
            self._transition(connection, artifact_id, "invalidated", reason)
            connection.execute(
                text(
                    """
                    INSERT INTO lineage.artifact_invalidation (
                        artifact_invalidation_id, artifact_id, replacement_artifact_id,
                        reason, invalidated_at
                    ) VALUES (:id, :artifact_id, :replacement_id, :reason, :invalidated_at)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "artifact_id": artifact_id,
                    "replacement_id": replacement_artifact_id,
                    "reason": reason,
                    "invalidated_at": datetime.now(UTC),
                },
            )
            dependents = (
                connection.execute(
                    text(
                        """
                    WITH RECURSIVE affected(artifact_id) AS (
                        SELECT artifact_id FROM lineage.artifact_dependency
                        WHERE depends_on_artifact_id = :artifact_id
                        UNION
                        SELECT dependency.artifact_id
                        FROM lineage.artifact_dependency dependency
                        JOIN affected ON dependency.depends_on_artifact_id = affected.artifact_id
                    )
                    SELECT artifact_id FROM affected ORDER BY artifact_id
                    """
                    ),
                    {"artifact_id": artifact_id},
                )
                .scalars()
                .all()
            )
            tainted: list[uuid.UUID] = []
            for dependent_id in dependents:
                dependent = self._artifact_by_id(connection, dependent_id, for_update=True)
                if dependent["status"] == "published":
                    self._transition(
                        connection,
                        dependent_id,
                        "tainted",
                        f"upstream invalidated: {artifact_id}; {reason}",
                    )
                    tainted.append(dependent_id)
            return tuple(tainted)

    def list_artifacts(self) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT artifact_id, artifact_type, artifact_key, version_number, status,
                           semantic_fingerprint, content_hash, published_at
                    FROM lineage.artifact
                    ORDER BY artifact_type, artifact_key, version_number
                    """
                )
            ).mappings()
            return [self._json_row(row) for row in rows]

    def describe(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            artifact = self._artifact_by_id(connection, artifact_id)
            manifest = (
                connection.execute(
                    text(
                        """
                    SELECT manifest_hash, canonical_version, manifest, created_at
                    FROM lineage.lineage_manifest WHERE root_artifact_id = :artifact_id
                    """
                    ),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            return {
                "artifact": self._json_row(artifact),
                "lineage_manifest": self._json_row(manifest) if manifest else None,
            }

    @staticmethod
    def _validate_dependencies(dependencies: tuple[DependencyInput, ...]) -> None:
        identities = [(item.artifact_id, item.role) for item in dependencies]
        if len(identities) != len(set(identities)):
            raise ValueError("Duplicate artifact dependency role")
        if any(item.ordinal is not None and item.ordinal < 0 for item in dependencies):
            raise ValueError("Dependency ordinal cannot be negative")

    def _dependency_rows(
        self, connection: Connection, dependencies: tuple[DependencyInput, ...]
    ) -> list[RowMapping]:
        rows: list[RowMapping] = []
        for dependency in dependencies:
            row = self._artifact_by_id(connection, dependency.artifact_id)
            if row["status"] != "published":
                raise ValueError(f"Dependency is not published: {dependency.artifact_id}")
            rows.append(row)
        return rows

    @staticmethod
    def _replace_draft_dependencies(
        connection: Connection,
        artifact_id: uuid.UUID,
        dependencies: tuple[DependencyInput, ...],
    ) -> None:
        connection.execute(
            text("DELETE FROM lineage.artifact_dependency WHERE artifact_id = :artifact_id"),
            {"artifact_id": artifact_id},
        )
        for dependency in dependencies:
            connection.execute(
                text(
                    """
                    INSERT INTO lineage.artifact_dependency (
                        artifact_dependency_id, artifact_id, depends_on_artifact_id, role, ordinal
                    ) VALUES (:id, :artifact_id, :depends_on, :role, :ordinal)
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

    def _existing_result(
        self,
        connection: Connection,
        artifact: RowMapping,
        semantic_fingerprint: str,
        content_hash: str,
    ) -> PublicationResult:
        if artifact["status"] != "published":
            status = artifact["status"]
            raise ValueError(
                f"Artifact identity already exists with non-publishable status: {status}"
            )
        if artifact["semantic_fingerprint"] != semantic_fingerprint:
            raise ValueError("Artifact identity already exists with different semantics")
        if artifact["content_hash"] != content_hash:
            raise ValueError("Artifact identity already exists with different content")
        manifest_hash = connection.execute(
            text(
                "SELECT manifest_hash FROM lineage.lineage_manifest "
                "WHERE root_artifact_id = :artifact_id"
            ),
            {"artifact_id": artifact["artifact_id"]},
        ).scalar_one()
        return PublicationResult(
            artifact["artifact_id"], semantic_fingerprint, content_hash, manifest_hash, True
        )

    def _build_manifest(
        self, connection: Connection, root_artifact_id: uuid.UUID
    ) -> tuple[dict[str, Any], str]:
        artifact_rows = (
            connection.execute(
                text(
                    """
                WITH RECURSIVE tree(artifact_id) AS (
                    SELECT :root_artifact_id
                    UNION
                    SELECT dependency.depends_on_artifact_id
                    FROM lineage.artifact_dependency dependency
                    JOIN tree ON dependency.artifact_id = tree.artifact_id
                )
                SELECT artifact_id, artifact_type, artifact_key, version_number,
                       semantic_fingerprint, content_hash
                FROM lineage.artifact WHERE artifact_id IN (SELECT artifact_id FROM tree)
                ORDER BY artifact_type, artifact_key, version_number, artifact_id
                """
                ),
                {"root_artifact_id": root_artifact_id},
            )
            .mappings()
            .all()
        )
        artifact_ids = [row["artifact_id"] for row in artifact_rows]
        edge_rows: list[RowMapping] = []
        if artifact_ids:
            edge_rows = list(
                connection.execute(
                    text(
                        """
                        SELECT artifact_id, depends_on_artifact_id, role, ordinal
                        FROM lineage.artifact_dependency
                        WHERE artifact_id = ANY(:artifact_ids)
                        ORDER BY artifact_id, role, ordinal, depends_on_artifact_id
                        """
                    ),
                    {"artifact_ids": artifact_ids},
                ).mappings()
            )
        manifest = {
            "root_artifact_id": str(root_artifact_id),
            "artifacts": [self._json_row(row) for row in artifact_rows],
            "dependencies": [self._json_row(row) for row in edge_rows],
        }
        return manifest, sha256_hexdigest(manifest)

    @staticmethod
    def _artifact_by_id(
        connection: Connection, artifact_id: uuid.UUID, *, for_update: bool = False
    ) -> RowMapping:
        suffix = " FOR UPDATE" if for_update else ""
        row = (
            connection.execute(
                text(f"SELECT * FROM lineage.artifact WHERE artifact_id = :artifact_id{suffix}"),
                {"artifact_id": artifact_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError(f"Unknown artifact: {artifact_id}")
        return row

    @staticmethod
    def _set_status_context(connection: Connection, reason: str) -> None:
        connection.execute(
            text(
                "SELECT set_config('style_rotation.status_event_id', :event_id, true), "
                "set_config('style_rotation.status_reason', :reason, true)"
            ),
            {"event_id": str(uuid.uuid4()), "reason": reason},
        )

    def _transition(
        self, connection: Connection, artifact_id: uuid.UUID, status: str, reason: str
    ) -> None:
        self._set_status_context(connection, reason)
        connection.execute(
            text("UPDATE lineage.artifact SET status = :status WHERE artifact_id = :artifact_id"),
            {"artifact_id": artifact_id, "status": status},
        )

    @staticmethod
    def _json_row(row: RowMapping) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, uuid.UUID):
                result[key] = str(value)
            elif isinstance(value, datetime):
                result[key] = value.isoformat()
            else:
                result[key] = value
        return result
