from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from style_rotation.persistence.base import Base, CreatedAtMixin

ARTIFACT_STATUSES = (
    "draft",
    "published",
    "retired",
    "superseded",
    "invalidated",
    "tainted",
)
STATUS_SQL = ", ".join(f"'{item}'" for item in ARTIFACT_STATUSES)
HASH_CHECK = "^[0-9a-f]{64}$"


class Artifact(CreatedAtMixin, Base):
    __tablename__ = "artifact"
    __table_args__ = (
        UniqueConstraint(
            "artifact_type",
            "artifact_key",
            "version_number",
            name="uq_artifact_artifact_identity",
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint(f"status IN ({STATUS_SQL})", name="status_allowed"),
        CheckConstraint(
            "status = 'draft' OR "
            "(semantic_fingerprint IS NOT NULL AND content_hash IS NOT NULL "
            "AND published_at IS NOT NULL)",
            name="published_identity_complete",
        ),
        CheckConstraint(
            f"semantic_fingerprint IS NULL OR semantic_fingerprint ~ '{HASH_CHECK}'",
            name="semantic_fingerprint_sha256",
        ),
        CheckConstraint(
            f"content_hash IS NULL OR content_hash ~ '{HASH_CHECK}'",
            name="content_hash_sha256",
        ),
        Index("ix_lineage_artifact_type_status", "artifact_type", "status"),
        {"schema": "lineage"},
    )

    artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(80), nullable=False)
    artifact_key: Mapped[str] = mapped_column(String(200), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="draft")
    semantic_fingerprint: Mapped[str | None] = mapped_column(String(64), unique=True)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArtifactDependency(CreatedAtMixin, Base):
    __tablename__ = "artifact_dependency"
    __table_args__ = (
        UniqueConstraint(
            "artifact_id",
            "depends_on_artifact_id",
            "role",
            name="uq_artifact_dependency_dependency_role",
        ),
        CheckConstraint("artifact_id <> depends_on_artifact_id", name="not_self_referential"),
        CheckConstraint("ordinal IS NULL OR ordinal >= 0", name="ordinal_nonnegative"),
        Index("ix_artifact_dependency_depends_on", "depends_on_artifact_id"),
        {"schema": "lineage"},
    )

    artifact_dependency_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    depends_on_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    ordinal: Mapped[int | None] = mapped_column(Integer)


class ArtifactStatusEvent(Base):
    __tablename__ = "artifact_status_event"
    __table_args__ = (
        CheckConstraint(
            f"from_status IS NULL OR from_status IN ({STATUS_SQL})", name="from_status_allowed"
        ),
        CheckConstraint(f"to_status IN ({STATUS_SQL})", name="to_status_allowed"),
        Index("ix_artifact_status_event_artifact_time", "artifact_id", "occurred_at"),
        {"schema": "lineage"},
    )

    artifact_status_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LineageManifest(CreatedAtMixin, Base):
    __tablename__ = "lineage_manifest"
    __table_args__ = (
        CheckConstraint(f"root_content_hash ~ '{HASH_CHECK}'", name="root_content_hash_sha256"),
        CheckConstraint(f"manifest_hash ~ '{HASH_CHECK}'", name="manifest_hash_sha256"),
        {"schema": "lineage"},
    )

    lineage_manifest_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    root_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    root_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    canonical_version: Mapped[str] = mapped_column(String(40), nullable=False)
    manifest: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class ArtifactInvalidation(Base):
    __tablename__ = "artifact_invalidation"
    __table_args__ = (
        CheckConstraint(
            "replacement_artifact_id IS NULL OR replacement_artifact_id <> artifact_id",
            name="replacement_is_different",
        ),
        {"schema": "lineage"},
    )

    artifact_invalidation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    replacement_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    invalidated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
