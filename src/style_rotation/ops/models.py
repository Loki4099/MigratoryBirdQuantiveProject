from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from style_rotation.persistence.base import Base, CreatedAtMixin

HASH_CHECK = "^[0-9a-f]{64}$"
RUN_STATUSES = ("queued", "running", "completed", "failed", "cancelled")
RUN_STATUS_SQL = ", ".join(f"'{item}'" for item in RUN_STATUSES)


class EngineDefinition(CreatedAtMixin, Base):
    __tablename__ = "engine_definition"
    __table_args__ = ({"schema": "ops"},)

    engine_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    engine_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    engine_type: Mapped[str] = mapped_column(String(80), nullable=False)


class EngineVersion(CreatedAtMixin, Base):
    __tablename__ = "engine_version"
    __table_args__ = (
        UniqueConstraint(
            "engine_definition_id",
            "version_number",
            name="uq_engine_version_definition_version",
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint(
            f"dependency_lock_hash ~ '{HASH_CHECK}'", name="dependency_lock_hash_sha256"
        ),
        CheckConstraint(f"configuration_hash ~ '{HASH_CHECK}'", name="configuration_hash_sha256"),
        {"schema": "ops"},
    )

    engine_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    engine_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ops.engine_definition.engine_definition_id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    semantic_version: Mapped[str] = mapped_column(String(50), nullable=False)
    git_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    dependency_lock_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    numerical_environment: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class RunAttempt(CreatedAtMixin, Base):
    __tablename__ = "run_attempt"
    __table_args__ = (
        UniqueConstraint(
            "request_fingerprint", "attempt_number", name="uq_run_attempt_request_attempt"
        ),
        CheckConstraint(f"status IN ({RUN_STATUS_SQL})", name="status_allowed"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint(f"request_fingerprint ~ '{HASH_CHECK}'", name="request_fingerprint_sha256"),
        CheckConstraint(
            "completed_at IS NULL OR started_at IS NOT NULL", name="completion_requires_start"
        ),
        Index("ix_run_attempt_status_created", "status", "created_at"),
        {"schema": "ops"},
    )

    run_attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    engine_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ops.engine_version.engine_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    root_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT")
    )
    run_type: Mapped[str] = mapped_column(String(80), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(String(2000))


class RunEvent(Base):
    __tablename__ = "run_event"
    __table_args__ = (
        UniqueConstraint("run_attempt_id", "sequence_number", name="uq_run_event_run_sequence"),
        CheckConstraint("sequence_number >= 1", name="sequence_number_positive"),
        CheckConstraint("severity IN ('info', 'warning', 'error')", name="severity_allowed"),
        {"schema": "ops"},
    )

    run_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ops.run_attempt.run_attempt_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(String(2000), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunError(CreatedAtMixin, Base):
    __tablename__ = "run_error"
    __table_args__ = (Index("ix_run_error_attempt", "run_attempt_id"), {"schema": "ops"})

    run_error_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ops.run_attempt.run_attempt_id", ondelete="RESTRICT"),
        nullable=False,
    )
    error_code: Mapped[str] = mapped_column(String(120), nullable=False)
    error_type: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(String(4000), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class RunArtifact(CreatedAtMixin, Base):
    __tablename__ = "run_artifact"
    __table_args__ = (
        UniqueConstraint(
            "run_attempt_id",
            "artifact_id",
            "role",
            name="uq_run_artifact_run_artifact_role",
        ),
        CheckConstraint("role IN ('input', 'output', 'log', 'diagnostic')", name="role_allowed"),
        Index("ix_run_artifact_artifact", "artifact_id"),
        {"schema": "ops"},
    )

    run_artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ops.run_attempt.run_attempt_id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(24), nullable=False)


class QualityCheckResult(CreatedAtMixin, Base):
    __tablename__ = "quality_check_result"
    __table_args__ = (
        UniqueConstraint(
            "run_attempt_id",
            "check_key",
            "scope_key",
            name="uq_quality_check_run_check_scope",
        ),
        CheckConstraint("status IN ('passed', 'warning', 'failed')", name="status_allowed"),
        CheckConstraint("severity IN ('info', 'warning', 'error')", name="severity_allowed"),
        Index("ix_quality_check_run_status", "run_attempt_id", "status"),
        {"schema": "ops"},
    )

    quality_check_result_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ops.run_attempt.run_attempt_id", ondelete="RESTRICT"),
        nullable=False,
    )
    check_key: Mapped[str] = mapped_column(String(160), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(240), nullable=False, server_default="global")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(String(2000), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
