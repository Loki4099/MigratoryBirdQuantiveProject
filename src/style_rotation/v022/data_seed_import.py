from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, cast
from urllib.parse import urlparse

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService

ImportProvenanceStatus = Literal["verified", "needs_review", "unavailable"]
ImportUsageScope = Literal["local_research", "redistributable", "unresolved"]
SnapshotFetchStatus = Literal["fetched", "unavailable", "failed"]

_IMPORT_CONTRACT_VERSION = "v0.22.sp500_seed_import.v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_ALLOWED_SOURCE_URI_SCHEMES = {"content", "git+https", "https", "project"}


@dataclass(frozen=True, slots=True)
class ExternalImportObjectSpec:
    object_role: str
    logical_key: str
    media_type: str
    content_sha256: str
    size_bytes: int
    source_uri: str
    license_key: str
    provenance_status: ImportProvenanceStatus
    usage_scope: ImportUsageScope
    provider_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for label, value in (
            ("object_role", self.object_role),
            ("logical_key", self.logical_key),
            ("media_type", self.media_type),
            ("source_uri", self.source_uri),
            ("license_key", self.license_key),
        ):
            _require_text(label, value)
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("Import object content_sha256 must be lowercase SHA-256")
        if self.size_bytes < 0:
            raise ValueError("Import object size_bytes cannot be negative")
        parsed = urlparse(self.source_uri)
        if parsed.scheme not in _ALLOWED_SOURCE_URI_SCHEMES:
            raise ValueError("Import object source_uri must be an approved non-filesystem URI")
        if self.provider_key is not None:
            _require_text("provider_key", self.provider_key)
        try:
            canonical_metadata = json.loads(
                json.dumps(self.metadata, ensure_ascii=False, sort_keys=True)
            )
        except (TypeError, ValueError) as error:
            raise ValueError("Import object metadata must be JSON-compatible") from error
        if not isinstance(canonical_metadata, dict):
            raise ValueError("Import object metadata must be an object")
        _reject_local_paths(canonical_metadata, "metadata")
        object.__setattr__(self, "metadata", canonical_metadata)


@dataclass(frozen=True, slots=True)
class ExternalImportManifestSpec:
    manifest_key: str
    version_number: int
    source_project_key: str
    source_release_key: str
    objects: tuple[ExternalImportObjectSpec, ...]
    created_by: str

    def __post_init__(self) -> None:
        for label, value in (
            ("manifest_key", self.manifest_key),
            ("source_project_key", self.source_project_key),
            ("source_release_key", self.source_release_key),
            ("created_by", self.created_by),
        ):
            _require_text(label, value)
        if self.version_number < 1:
            raise ValueError("Import manifest version_number must be positive")
        if not self.objects:
            raise ValueError("Import manifest requires at least one object")
        logical_keys = [item.logical_key for item in self.objects]
        if len(logical_keys) != len(set(logical_keys)):
            raise ValueError("Import manifest object logical_key values must be unique")

    def canonical_objects(self) -> tuple[ExternalImportObjectSpec, ...]:
        return tuple(sorted(self.objects, key=lambda item: (item.object_role, item.logical_key)))

    def document(self) -> dict[str, Any]:
        return {
            "contract_version": _IMPORT_CONTRACT_VERSION,
            "manifest_key": self.manifest_key,
            "version_number": self.version_number,
            "source_project_key": self.source_project_key,
            "source_release_key": self.source_release_key,
            "objects": [
                {
                    "ordinal": ordinal,
                    "object_role": item.object_role,
                    "logical_key": item.logical_key,
                    "media_type": item.media_type,
                    "content_sha256": item.content_sha256,
                    "size_bytes": item.size_bytes,
                    "source_uri": item.source_uri,
                    "license_key": item.license_key,
                    "provider_key": item.provider_key,
                    "provenance_status": item.provenance_status,
                    "usage_scope": item.usage_scope,
                    "metadata": item.metadata,
                }
                for ordinal, item in enumerate(self.canonical_objects())
            ],
        }


@dataclass(frozen=True, slots=True)
class ExternalImportManifestPublication:
    external_import_manifest_id: uuid.UUID
    artifact_id: uuid.UUID
    manifest_fingerprint: str
    reused: bool


class ExternalImportManifestService:
    """Publish content-addressed evidence without embedding workstation paths."""

    def __init__(self, engine: Engine) -> None:
        self._artifacts = ArtifactService(engine)

    def publish(self, spec: ExternalImportManifestSpec) -> ExternalImportManifestPublication:
        document = spec.document()
        fingerprint = sha256_hexdigest(document)
        manifest_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bird:v0.22:external-import-manifest:{fingerprint}",
        )

        def write_projection(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO data.v022_external_import_manifest (
                      external_import_manifest_id,artifact_id,manifest_key,version_number,
                      source_project_key,source_release_key,object_count,
                      manifest_document,manifest_fingerprint,created_by
                    ) VALUES (
                      :id,:artifact,:manifest_key,:version_number,:source_project_key,
                      :source_release_key,:object_count,CAST(:document AS jsonb),
                      :fingerprint,:created_by
                    )
                    """
                ),
                {
                    "id": manifest_id,
                    "artifact": artifact_id,
                    "manifest_key": spec.manifest_key,
                    "version_number": spec.version_number,
                    "source_project_key": spec.source_project_key,
                    "source_release_key": spec.source_release_key,
                    "object_count": len(spec.objects),
                    "document": json.dumps(document, ensure_ascii=False, sort_keys=True),
                    "fingerprint": fingerprint,
                    "created_by": spec.created_by,
                },
            )
            for ordinal, item in enumerate(spec.canonical_objects()):
                connection.execute(
                    text(
                        """
                        INSERT INTO data.v022_external_import_object (
                          external_import_object_id,external_import_manifest_id,ordinal,
                          object_role,logical_key,media_type,content_sha256,size_bytes,
                          source_uri,license_key,provider_key,provenance_status,
                          usage_scope,metadata_document
                        ) VALUES (
                          :id,:manifest,:ordinal,:object_role,:logical_key,:media_type,
                          :content_sha256,:size_bytes,:source_uri,:license_key,:provider_key,
                          :provenance_status,:usage_scope,CAST(:metadata AS jsonb)
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"bird:v0.22:external-import-object:{fingerprint}:{ordinal}",
                        ),
                        "manifest": manifest_id,
                        "ordinal": ordinal,
                        "object_role": item.object_role,
                        "logical_key": item.logical_key,
                        "media_type": item.media_type,
                        "content_sha256": item.content_sha256,
                        "size_bytes": item.size_bytes,
                        "source_uri": item.source_uri,
                        "license_key": item.license_key,
                        "provider_key": item.provider_key,
                        "provenance_status": item.provenance_status,
                        "usage_scope": item.usage_scope,
                        "metadata": json.dumps(item.metadata, ensure_ascii=False, sort_keys=True),
                    },
                )

        publication = self._artifacts.publish(
            artifact_type="v022_external_import_manifest",
            artifact_key=f"v022_external_import_manifest__{spec.manifest_key}",
            version_number=spec.version_number,
            semantic_payload=document,
            content_payload=document,
            reason=f"publish v0.22 external import manifest {spec.manifest_key}",
            draft_writer=write_projection,
        )
        return ExternalImportManifestPublication(
            manifest_id,
            publication.artifact_id,
            fingerprint,
            publication.reused,
        )


@dataclass(frozen=True, slots=True)
class ProviderSecurityIdentifierPublication:
    security_identifier_id: uuid.UUID
    reused: bool


class ProviderSecurityIdentityService:
    """Register one provider-scoped symbol interval for a stable Security."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register(
        self,
        *,
        security_id: uuid.UUID,
        provider_scope: str,
        provider_symbol: str,
        valid_from: date | None,
        valid_to: date | None,
    ) -> ProviderSecurityIdentifierPublication:
        _require_text("provider_scope", provider_scope)
        _require_text("provider_symbol", provider_symbol)
        if provider_scope == "catalog":
            raise ValueError("Provider Security identifiers cannot use the catalog scope")
        if valid_to is not None and valid_from is not None and valid_from >= valid_to:
            raise ValueError("Provider Security identifier interval must be half-open")
        lock_key = f"v022-provider-symbol:{provider_scope}:{provider_symbol.casefold()}"
        identifier_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            ":".join(
                (
                    "bird:v0.22:provider-security-identifier",
                    str(security_id),
                    provider_scope,
                    provider_symbol,
                    valid_from.isoformat() if valid_from is not None else "-infinity",
                    valid_to.isoformat() if valid_to is not None else "infinity",
                )
            ),
        )
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": lock_key},
            )
            if not connection.execute(
                text("SELECT EXISTS(SELECT 1 FROM catalog.security WHERE security_id=:id)"),
                {"id": security_id},
            ).scalar_one():
                raise LookupError("Security not found")
            if not connection.execute(
                text(
                    """
                    SELECT EXISTS(
                      SELECT 1 FROM data.source_provider provider
                      JOIN data.data_contract_release release
                        ON release.data_contract_release_id=provider.data_contract_release_id
                      JOIN lineage.artifact artifact ON artifact.artifact_id=release.artifact_id
                     WHERE provider.provider_key=:provider AND artifact.status='published'
                    )
                    """
                ),
                {"provider": provider_scope},
            ).scalar_one():
                raise LookupError("Published Source Provider not found")
            existing = connection.execute(
                text(
                    """
                    SELECT security_identifier_id
                      FROM catalog.security_identifier
                     WHERE security_id=:security
                       AND provider_scope=:provider
                       AND identifier_type='provider_symbol'
                       AND identifier_value=:symbol
                       AND valid_from IS NOT DISTINCT FROM :valid_from
                       AND valid_to IS NOT DISTINCT FROM :valid_to
                    """
                ),
                {
                    "security": security_id,
                    "provider": provider_scope,
                    "symbol": provider_symbol,
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                },
            ).scalar_one_or_none()
            if existing is not None:
                return ProviderSecurityIdentifierPublication(cast(uuid.UUID, existing), True)
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.security_identifier (
                      security_identifier_id,security_id,provider_scope,
                      identifier_type,identifier_value,valid_from,valid_to
                    ) VALUES (
                      :id,:security,:provider,'provider_symbol',:symbol,:valid_from,:valid_to
                    )
                    """
                ),
                {
                    "id": identifier_id,
                    "security": security_id,
                    "provider": provider_scope,
                    "symbol": provider_symbol,
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                },
            )
        return ProviderSecurityIdentifierPublication(identifier_id, False)


@dataclass(frozen=True, slots=True)
class SourceSnapshotSecuritySubjectPublication:
    source_snapshot_security_subject_id: uuid.UUID
    reused: bool


class SourceSnapshotSecuritySubjectService:
    """Bind a frozen provider response to one exact Security identifier interval."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def bind(
        self,
        *,
        source_snapshot_id: uuid.UUID,
        security_id: uuid.UUID,
        security_identifier_id: uuid.UUID,
        fetch_status: SnapshotFetchStatus,
        failure_reason: str | None = None,
    ) -> SourceSnapshotSecuritySubjectPublication:
        if fetch_status == "fetched" and failure_reason is not None:
            raise ValueError("Fetched Source Snapshot subjects cannot have a failure reason")
        if fetch_status != "fetched" and (failure_reason is None or not failure_reason.strip()):
            raise ValueError("Non-fetched Source Snapshot subjects require a failure reason")
        subject_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bird:v0.22:source-snapshot-subject:{source_snapshot_id}:{security_id}",
        )
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"v022-source-snapshot-subject:{source_snapshot_id}:{security_id}"},
            )
            existing = connection.execute(
                text(
                    """
                    SELECT source_snapshot_security_subject_id,security_identifier_id,
                           fetch_status,failure_reason
                      FROM data.source_snapshot_security_subject
                     WHERE source_snapshot_id=:snapshot AND security_id=:security
                    """
                ),
                {"snapshot": source_snapshot_id, "security": security_id},
            ).mappings().one_or_none()
            if existing is not None:
                if (
                    existing["security_identifier_id"] != security_identifier_id
                    or existing["fetch_status"] != fetch_status
                    or existing["failure_reason"] != failure_reason
                ):
                    raise ValueError("Source Snapshot subject already exists with different facts")
                return SourceSnapshotSecuritySubjectPublication(
                    cast(uuid.UUID, existing["source_snapshot_security_subject_id"]), True
                )
            identifier = _provider_identifier(connection, security_identifier_id)
            if identifier["security_id"] != security_id:
                raise ValueError("Provider Security identifier belongs to a different Security")
            connection.execute(
                text(
                    """
                    INSERT INTO data.source_snapshot_security_subject (
                      source_snapshot_security_subject_id,source_snapshot_id,security_id,
                      security_identifier_id,provider_scope,provider_symbol,
                      identifier_valid_from,identifier_valid_to,fetch_status,failure_reason
                    ) VALUES (
                      :id,:snapshot,:security,:identifier,:provider,:symbol,
                      :valid_from,:valid_to,:fetch_status,:failure_reason
                    )
                    """
                ),
                {
                    "id": subject_id,
                    "snapshot": source_snapshot_id,
                    "security": security_id,
                    "identifier": security_identifier_id,
                    "provider": identifier["provider_scope"],
                    "symbol": identifier["identifier_value"],
                    "valid_from": identifier["valid_from"],
                    "valid_to": identifier["valid_to"],
                    "fetch_status": fetch_status,
                    "failure_reason": failure_reason,
                },
            )
        return SourceSnapshotSecuritySubjectPublication(subject_id, False)


def _provider_identifier(connection: Connection, identifier_id: uuid.UUID) -> RowMapping:
    row = connection.execute(
        text(
            """
            SELECT security_id,provider_scope,identifier_type,identifier_value,
                   valid_from,valid_to
              FROM catalog.security_identifier
             WHERE security_identifier_id=:identifier
            """
        ),
        {"identifier": identifier_id},
    ).mappings().one_or_none()
    if row is None or row["identifier_type"] != "provider_symbol":
        raise LookupError("Provider Security identifier not found")
    return row


def _require_text(label: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{label} is required")


def _reject_local_paths(value: Any, path: str) -> None:
    if isinstance(value, str):
        if _WINDOWS_ABSOLUTE_PATH.match(value) or value.casefold().startswith("file://"):
            raise ValueError(f"Import manifest cannot embed a workstation path at {path}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_local_paths(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for ordinal, item in enumerate(value):
            _reject_local_paths(item, f"{path}[{ordinal}]")
