from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, cast
from urllib.parse import urlparse

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

IdentityEvidenceKind = Literal[
    "source_dataset_row",
    "sec_filing",
    "exchange_notice",
    "company_announcement",
    "provider_metadata",
    "manual_analysis",
    "other_public_record",
]
IdentityResolutionStatus = Literal["confirmed", "provisional", "unresolved"]
IdentityResolutionKind = Literal[
    "map_existing_security",
    "create_security",
    "ticker_rename",
    "ticker_reuse",
    "share_class_conversion",
    "reorganization",
    "not_a_security",
    "unavailable",
]

_CASE_CONTRACT = "v0.22.security_identity_review.v1"
_EVIDENCE_CONTRACT = "v0.22.security_identity_evidence.v1"
_RESOLUTION_CONTRACT = "v0.22.security_identity_resolution.v1"
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,199}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_ALLOWED_EVIDENCE_URI_SCHEMES = {"content", "git+https", "https", "project"}
_EVIDENCE_KINDS = {
    "source_dataset_row",
    "sec_filing",
    "exchange_notice",
    "company_announcement",
    "provider_metadata",
    "manual_analysis",
    "other_public_record",
}
_RESOLUTION_STATUSES = {"confirmed", "provisional", "unresolved"}
_RESOLUTION_KINDS = {
    "map_existing_security",
    "create_security",
    "ticker_rename",
    "ticker_reuse",
    "share_class_conversion",
    "reorganization",
    "not_a_security",
    "unavailable",
}
_TARGET_REQUIRED = {
    "map_existing_security",
    "create_security",
    "ticker_rename",
    "ticker_reuse",
    "share_class_conversion",
    "reorganization",
}


@dataclass(frozen=True, slots=True)
class SecurityIdentityReviewCaseSpec:
    external_import_manifest_id: uuid.UUID
    case_key: str
    version_number: int
    provider_scope: str
    source_symbol: str
    first_observed_session: date
    last_observed_session: date
    observed_snapshot_count: int
    membership_episode_count: int
    reason_code: str
    created_by: str
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_key("case_key", self.case_key)
        for label, value in (
            ("provider_scope", self.provider_scope),
            ("source_symbol", self.source_symbol),
            ("reason_code", self.reason_code),
            ("created_by", self.created_by),
        ):
            _require_text(label, value)
        if self.version_number < 1:
            raise ValueError("Identity Review version_number must be positive")
        if self.first_observed_session > self.last_observed_session:
            raise ValueError("Identity Review observed sessions must be ordered")
        if self.observed_snapshot_count < 1 or self.membership_episode_count < 1:
            raise ValueError("Identity Review observation counts must be positive")
        object.__setattr__(self, "context", _json_object("context", self.context))

    def document(self) -> dict[str, Any]:
        return {
            "contract_version": _CASE_CONTRACT,
            "external_import_manifest_id": str(self.external_import_manifest_id),
            "case_key": self.case_key,
            "version_number": self.version_number,
            "provider_scope": self.provider_scope,
            "source_symbol": self.source_symbol,
            "first_observed_session": self.first_observed_session.isoformat(),
            "last_observed_session": self.last_observed_session.isoformat(),
            "observed_snapshot_count": self.observed_snapshot_count,
            "membership_episode_count": self.membership_episode_count,
            "reason_code": self.reason_code,
            "context": self.context,
        }


@dataclass(frozen=True, slots=True)
class SecurityIdentityEvidenceSpec:
    review_case_id: uuid.UUID
    evidence_key: str
    version_number: int
    evidence_kind: IdentityEvidenceKind
    source_uri: str
    content_sha256: str
    known_at: datetime
    effective_session: date | None
    recorded_by: str
    facts: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_key("evidence_key", self.evidence_key)
        _require_text("recorded_by", self.recorded_by)
        if self.version_number < 1:
            raise ValueError("Identity Evidence version_number must be positive")
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("Identity Evidence content_sha256 must be lowercase SHA-256")
        if self.evidence_kind not in _EVIDENCE_KINDS:
            raise ValueError("Unsupported Identity Evidence kind")
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise ValueError("Identity Evidence known_at must be timezone-aware")
        if urlparse(self.source_uri).scheme not in _ALLOWED_EVIDENCE_URI_SCHEMES:
            raise ValueError("Identity Evidence source_uri must use an approved scheme")
        object.__setattr__(self, "facts", _json_object("facts", self.facts))

    def document(self) -> dict[str, Any]:
        return {
            "contract_version": _EVIDENCE_CONTRACT,
            "review_case_id": str(self.review_case_id),
            "evidence_key": self.evidence_key,
            "version_number": self.version_number,
            "evidence_kind": self.evidence_kind,
            "source_uri": self.source_uri,
            "content_sha256": self.content_sha256,
            "known_at": self.known_at.isoformat(),
            "effective_session": (
                self.effective_session.isoformat()
                if self.effective_session is not None
                else None
            ),
            "facts": self.facts,
        }


@dataclass(frozen=True, slots=True)
class SecurityIdentityResolutionSpec:
    review_case_id: uuid.UUID
    version_number: int
    resolution_status: IdentityResolutionStatus
    resolution_kind: IdentityResolutionKind
    evidence_ids: tuple[uuid.UUID, ...]
    resolved_by: str
    target_security_id: uuid.UUID | None = None
    target_security_identifier_id: uuid.UUID | None = None
    supersedes_resolution_id: uuid.UUID | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("resolved_by", self.resolved_by)
        if self.version_number < 1:
            raise ValueError("Identity Resolution version_number must be positive")
        if self.resolution_status not in _RESOLUTION_STATUSES:
            raise ValueError("Unsupported Identity Resolution status")
        if self.resolution_kind not in _RESOLUTION_KINDS:
            raise ValueError("Unsupported Identity Resolution kind")
        if not self.evidence_ids:
            raise ValueError("Identity Resolution requires at least one Evidence item")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Identity Resolution Evidence items must be unique")
        target_required = self.resolution_kind in _TARGET_REQUIRED
        if target_required != (self.target_security_id is not None):
            raise ValueError("Identity Resolution target Security does not match its kind")
        if self.target_security_identifier_id is not None and self.target_security_id is None:
            raise ValueError("Identity Resolution identifier requires a target Security")
        if self.version_number == 1 and self.supersedes_resolution_id is not None:
            raise ValueError("First Identity Resolution cannot supersede another version")
        if self.version_number > 1 and self.supersedes_resolution_id is None:
            raise ValueError("Later Identity Resolution must supersede the previous version")
        object.__setattr__(self, "details", _json_object("details", self.details))

    def canonical_evidence_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(sorted(self.evidence_ids, key=str))

    def document(self) -> dict[str, Any]:
        evidence_ids = self.canonical_evidence_ids()
        return {
            "contract_version": _RESOLUTION_CONTRACT,
            "review_case_id": str(self.review_case_id),
            "version_number": self.version_number,
            "resolution_status": self.resolution_status,
            "resolution_kind": self.resolution_kind,
            "target_security_id": (
                str(self.target_security_id) if self.target_security_id is not None else None
            ),
            "target_security_identifier_id": (
                str(self.target_security_identifier_id)
                if self.target_security_identifier_id is not None
                else None
            ),
            "supersedes_resolution_id": (
                str(self.supersedes_resolution_id)
                if self.supersedes_resolution_id is not None
                else None
            ),
            "evidence_count": len(evidence_ids),
            "evidence_ids": [str(item) for item in evidence_ids],
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class SecurityIdentityReviewPublication:
    projection_id: uuid.UUID
    artifact_id: uuid.UUID
    fingerprint: str
    reused: bool


class SecurityIdentityReviewService:
    """Publish identity review facts without mutating source or canonical data."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish_case(
        self, spec: SecurityIdentityReviewCaseSpec
    ) -> SecurityIdentityReviewPublication:
        import_row = self._published_import_manifest(spec.external_import_manifest_id)
        document = spec.document()
        fingerprint = sha256_hexdigest(document)
        case_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:security-identity-review:{fingerprint}"
        )

        def write_projection(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.v022_security_identity_review_case (
                      security_identity_review_case_id,artifact_id,
                      external_import_manifest_id,external_import_manifest_artifact_id,
                      case_key,version_number,provider_scope,source_symbol,
                      first_observed_session,last_observed_session,
                      observed_snapshot_count,membership_episode_count,reason_code,
                      review_document,case_fingerprint,created_by
                    ) VALUES (
                      :id,:artifact,:manifest,:manifest_artifact,:case_key,:version_number,
                      :provider_scope,:source_symbol,:first_session,:last_session,
                      :snapshot_count,:episode_count,:reason_code,CAST(:document AS jsonb),
                      :fingerprint,:created_by
                    )
                    """
                ),
                {
                    "id": case_id,
                    "artifact": artifact_id,
                    "manifest": spec.external_import_manifest_id,
                    "manifest_artifact": import_row["artifact_id"],
                    "case_key": spec.case_key,
                    "version_number": spec.version_number,
                    "provider_scope": spec.provider_scope,
                    "source_symbol": spec.source_symbol,
                    "first_session": spec.first_observed_session,
                    "last_session": spec.last_observed_session,
                    "snapshot_count": spec.observed_snapshot_count,
                    "episode_count": spec.membership_episode_count,
                    "reason_code": spec.reason_code,
                    "document": _json_dump(document),
                    "fingerprint": fingerprint,
                    "created_by": spec.created_by,
                },
            )

        publication = self._artifacts.publish(
            artifact_type="v022_security_identity_review_case",
            artifact_key=f"v022_security_identity_review__{spec.case_key}",
            version_number=spec.version_number,
            semantic_payload=document,
            content_payload=document,
            dependencies=(
                DependencyInput(
                    cast(uuid.UUID, import_row["artifact_id"]),
                    "external_import_manifest",
                    0,
                ),
            ),
            reason=f"publish Security identity Review Case {spec.case_key}",
            draft_writer=write_projection,
        )
        return SecurityIdentityReviewPublication(
            case_id, publication.artifact_id, fingerprint, publication.reused
        )

    def publish_evidence(
        self, spec: SecurityIdentityEvidenceSpec
    ) -> SecurityIdentityReviewPublication:
        case_row = self._published_case(spec.review_case_id)
        document = spec.document()
        fingerprint = sha256_hexdigest(document)
        evidence_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:security-identity-evidence:{fingerprint}"
        )

        def write_projection(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.v022_security_identity_evidence (
                      security_identity_evidence_id,artifact_id,
                      security_identity_review_case_id,review_case_artifact_id,
                      evidence_key,version_number,evidence_kind,source_uri,content_sha256,
                      known_at,effective_session,evidence_document,evidence_fingerprint,
                      recorded_by
                    ) VALUES (
                      :id,:artifact,:case_id,:case_artifact,:evidence_key,:version_number,
                      :evidence_kind,:source_uri,:content_sha256,:known_at,
                      :effective_session,CAST(:document AS jsonb),:fingerprint,:recorded_by
                    )
                    """
                ),
                {
                    "id": evidence_id,
                    "artifact": artifact_id,
                    "case_id": spec.review_case_id,
                    "case_artifact": case_row["artifact_id"],
                    "evidence_key": spec.evidence_key,
                    "version_number": spec.version_number,
                    "evidence_kind": spec.evidence_kind,
                    "source_uri": spec.source_uri,
                    "content_sha256": spec.content_sha256,
                    "known_at": spec.known_at,
                    "effective_session": spec.effective_session,
                    "document": _json_dump(document),
                    "fingerprint": fingerprint,
                    "recorded_by": spec.recorded_by,
                },
            )

        publication = self._artifacts.publish(
            artifact_type="v022_security_identity_evidence",
            artifact_key=f"v022_security_identity_evidence__{spec.evidence_key}",
            version_number=spec.version_number,
            semantic_payload=document,
            content_payload=document,
            dependencies=(
                DependencyInput(
                    cast(uuid.UUID, case_row["artifact_id"]),
                    "identity_review_case",
                    0,
                ),
            ),
            reason=f"publish Security identity Evidence {spec.evidence_key}",
            draft_writer=write_projection,
        )
        return SecurityIdentityReviewPublication(
            evidence_id, publication.artifact_id, fingerprint, publication.reused
        )

    def publish_resolution(
        self, spec: SecurityIdentityResolutionSpec
    ) -> SecurityIdentityReviewPublication:
        case_row = self._published_case(spec.review_case_id)
        evidence_rows = tuple(
            self._published_evidence(evidence_id, spec.review_case_id)
            for evidence_id in spec.canonical_evidence_ids()
        )
        document = spec.document()
        fingerprint = sha256_hexdigest(document)
        resolution_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:security-identity-resolution:{fingerprint}"
        )

        def write_projection(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.v022_security_identity_resolution (
                      security_identity_resolution_id,artifact_id,
                      security_identity_review_case_id,review_case_artifact_id,
                      version_number,resolution_status,resolution_kind,target_security_id,
                      target_security_identifier_id,supersedes_resolution_id,evidence_count,
                      resolution_document,resolution_fingerprint,resolved_by
                    ) VALUES (
                      :id,:artifact,:case_id,:case_artifact,:version_number,
                      :resolution_status,:resolution_kind,:target_security,
                      :target_identifier,:supersedes,:evidence_count,
                      CAST(:document AS jsonb),:fingerprint,:resolved_by
                    )
                    """
                ),
                {
                    "id": resolution_id,
                    "artifact": artifact_id,
                    "case_id": spec.review_case_id,
                    "case_artifact": case_row["artifact_id"],
                    "version_number": spec.version_number,
                    "resolution_status": spec.resolution_status,
                    "resolution_kind": spec.resolution_kind,
                    "target_security": spec.target_security_id,
                    "target_identifier": spec.target_security_identifier_id,
                    "supersedes": spec.supersedes_resolution_id,
                    "evidence_count": len(evidence_rows),
                    "document": _json_dump(document),
                    "fingerprint": fingerprint,
                    "resolved_by": spec.resolved_by,
                },
            )
            for ordinal, evidence_row in enumerate(evidence_rows):
                connection.execute(
                    text(
                        """
                        INSERT INTO catalog.v022_security_identity_resolution_evidence (
                          security_identity_resolution_id,ordinal,
                          security_identity_evidence_id,evidence_artifact_id
                        ) VALUES (:resolution,:ordinal,:evidence,:evidence_artifact)
                        """
                    ),
                    {
                        "resolution": resolution_id,
                        "ordinal": ordinal,
                        "evidence": evidence_row["security_identity_evidence_id"],
                        "evidence_artifact": evidence_row["artifact_id"],
                    },
                )

        dependencies = (
            DependencyInput(
                cast(uuid.UUID, case_row["artifact_id"]), "identity_review_case", 0
            ),
            *tuple(
                DependencyInput(
                    cast(uuid.UUID, row["artifact_id"]), "identity_evidence", ordinal + 1
                )
                for ordinal, row in enumerate(evidence_rows)
            ),
        )
        publication = self._artifacts.publish(
            artifact_type="v022_security_identity_resolution",
            artifact_key=f"v022_security_identity_resolution__{spec.review_case_id}",
            version_number=spec.version_number,
            semantic_payload=document,
            content_payload=document,
            dependencies=dependencies,
            reason=f"publish Security identity Resolution for {spec.review_case_id}",
            draft_writer=write_projection,
        )
        return SecurityIdentityReviewPublication(
            resolution_id, publication.artifact_id, fingerprint, publication.reused
        )

    def _published_import_manifest(self, manifest_id: uuid.UUID) -> RowMapping:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT manifest.external_import_manifest_id,manifest.artifact_id,
                               artifact.status
                          FROM data.v022_external_import_manifest manifest
                          JOIN lineage.artifact artifact
                            ON artifact.artifact_id=manifest.artifact_id
                         WHERE manifest.external_import_manifest_id=:id
                        """
                    ),
                    {"id": manifest_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None or row["status"] != "published":
            raise LookupError(f"Published External Import Manifest not found: {manifest_id}")
        return row

    def _published_case(self, case_id: uuid.UUID) -> RowMapping:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT review.security_identity_review_case_id,review.artifact_id,
                               review.case_fingerprint,artifact.status
                          FROM catalog.v022_security_identity_review_case review
                          JOIN lineage.artifact artifact
                            ON artifact.artifact_id=review.artifact_id
                         WHERE review.security_identity_review_case_id=:id
                        """
                    ),
                    {"id": case_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None or row["status"] != "published":
            raise LookupError(f"Published Identity Review Case not found: {case_id}")
        return row

    def _published_evidence(
        self, evidence_id: uuid.UUID, case_id: uuid.UUID
    ) -> RowMapping:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT evidence.security_identity_evidence_id,evidence.artifact_id,
                               evidence.security_identity_review_case_id,
                               evidence.evidence_fingerprint,artifact.status
                          FROM catalog.v022_security_identity_evidence evidence
                          JOIN lineage.artifact artifact
                            ON artifact.artifact_id=evidence.artifact_id
                         WHERE evidence.security_identity_evidence_id=:id
                        """
                    ),
                    {"id": evidence_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None or row["status"] != "published":
            raise LookupError(f"Published Identity Evidence not found: {evidence_id}")
        if row["security_identity_review_case_id"] != case_id:
            raise ValueError("Identity Resolution Evidence belongs to a different Review Case")
        return row


def _require_key(label: str, value: str) -> None:
    if not _KEY_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a stable lowercase key")


def _require_text(label: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{label} cannot be blank")


def _json_object(label: str, value: dict[str, Any]) -> dict[str, Any]:
    try:
        normalized = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError) as error:
        raise ValueError(f"Identity Review {label} must be JSON-compatible") from error
    if not isinstance(normalized, dict):
        raise ValueError(f"Identity Review {label} must be an object")
    _reject_local_paths(normalized, label)
    return cast(dict[str, Any], normalized)


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reject_local_paths(value: Any, path: str) -> None:
    if isinstance(value, str):
        if _WINDOWS_ABSOLUTE_PATH.match(value) or value.casefold().startswith("file://"):
            raise ValueError(f"Identity Review cannot embed a workstation path at {path}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_local_paths(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for ordinal, item in enumerate(value):
            _reject_local_paths(item, f"{path}[{ordinal}]")
