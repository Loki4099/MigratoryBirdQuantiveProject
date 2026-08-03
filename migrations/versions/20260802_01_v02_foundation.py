"""Create the v0.2 schema, lineage identity, and operational foundation.

Revision ID: 20260802_01_v02_foundation
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_01_v02_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_NAMES = (
    "catalog",
    "data",
    "factor",
    "signal",
    "model",
    "strategy",
    "experiment",
    "lineage",
    "ops",
)
HASH_PATTERN = "^[0-9a-f]{64}$"


def upgrade() -> None:
    for schema_name in SCHEMA_NAMES:
        op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))

    op.create_table(
        "artifact",
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_type", sa.String(length=80), nullable=False),
        sa.Column("artifact_key", sa.String(length=200), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
        sa.Column("semantic_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_artifact_version_number_positive"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'retired', 'superseded', 'invalidated')",
            name="ck_artifact_status_allowed",
        ),
        sa.CheckConstraint(
            "status = 'draft' OR "
            "(semantic_fingerprint IS NOT NULL AND content_hash IS NOT NULL "
            "AND published_at IS NOT NULL)",
            name="ck_artifact_published_identity_complete",
        ),
        sa.CheckConstraint(
            f"semantic_fingerprint IS NULL OR semantic_fingerprint ~ '{HASH_PATTERN}'",
            name="ck_artifact_semantic_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            f"content_hash IS NULL OR content_hash ~ '{HASH_PATTERN}'",
            name="ck_artifact_content_hash_sha256",
        ),
        sa.PrimaryKeyConstraint("artifact_id", name="pk_artifact"),
        sa.UniqueConstraint(
            "artifact_type",
            "artifact_key",
            "version_number",
            name="uq_artifact_artifact_identity",
        ),
        sa.UniqueConstraint("semantic_fingerprint", name="uq_artifact_semantic_fingerprint"),
        schema="lineage",
    )
    op.create_index(
        "ix_lineage_artifact_type_status",
        "artifact",
        ["artifact_type", "status"],
        schema="lineage",
    )

    op.create_table(
        "artifact_dependency",
        sa.Column("artifact_dependency_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("depends_on_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "artifact_id <> depends_on_artifact_id",
            name="ck_artifact_dependency_not_self_referential",
        ),
        sa.CheckConstraint(
            "ordinal IS NULL OR ordinal >= 0",
            name="ck_artifact_dependency_ordinal_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_artifact_dependency_artifact_id_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_artifact_dependency_depends_on_artifact_id_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("artifact_dependency_id", name="pk_artifact_dependency"),
        sa.UniqueConstraint(
            "artifact_id",
            "depends_on_artifact_id",
            "role",
            name="uq_artifact_dependency_dependency_role",
        ),
        schema="lineage",
    )
    op.create_index(
        "ix_artifact_dependency_depends_on",
        "artifact_dependency",
        ["depends_on_artifact_id"],
        schema="lineage",
    )

    op.create_table(
        "artifact_status_event",
        sa.Column("artifact_status_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('draft', 'published', 'retired', 'superseded', 'invalidated')",
            name="ck_artifact_status_event_from_status_allowed",
        ),
        sa.CheckConstraint(
            "to_status IN ('draft', 'published', 'retired', 'superseded', 'invalidated')",
            name="ck_artifact_status_event_to_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_artifact_status_event_artifact_id_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("artifact_status_event_id", name="pk_artifact_status_event"),
        schema="lineage",
    )
    op.create_index(
        "ix_artifact_status_event_artifact_time",
        "artifact_status_event",
        ["artifact_id", "occurred_at"],
        schema="lineage",
    )

    op.create_table(
        "engine_definition",
        sa.Column("engine_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("engine_key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("engine_type", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("engine_definition_id", name="pk_engine_definition"),
        sa.UniqueConstraint("engine_key", name="uq_engine_definition_engine_key"),
        schema="ops",
    )

    op.create_table(
        "engine_version",
        sa.Column("engine_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("engine_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("semantic_version", sa.String(length=50), nullable=False),
        sa.Column("git_commit", sa.String(length=64), nullable=False),
        sa.Column("dependency_lock_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_revision", sa.String(length=64), nullable=False),
        sa.Column("configuration_hash", sa.String(length=64), nullable=False),
        sa.Column("numerical_environment", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_engine_version_version_number_positive"),
        sa.CheckConstraint(
            f"dependency_lock_hash ~ '{HASH_PATTERN}'",
            name="ck_engine_version_dependency_lock_hash_sha256",
        ),
        sa.CheckConstraint(
            f"configuration_hash ~ '{HASH_PATTERN}'",
            name="ck_engine_version_configuration_hash_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_engine_version_artifact_id_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["engine_definition_id"],
            ["ops.engine_definition.engine_definition_id"],
            name="fk_engine_version_engine_definition_id_engine_definition",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("engine_version_id", name="pk_engine_version"),
        sa.UniqueConstraint("artifact_id", name="uq_engine_version_artifact_id"),
        sa.UniqueConstraint(
            "engine_definition_id",
            "version_number",
            name="uq_engine_version_definition_version",
        ),
        schema="ops",
    )

    op.create_table(
        "run_attempt",
        sa.Column("run_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("engine_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_type", sa.String(length=80), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_run_attempt_attempt_number_positive"),
        sa.CheckConstraint(
            "completed_at IS NULL OR started_at IS NOT NULL",
            name="ck_run_attempt_completion_requires_start",
        ),
        sa.CheckConstraint(
            f"request_fingerprint ~ '{HASH_PATTERN}'",
            name="ck_run_attempt_request_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_run_attempt_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["engine_version_id"],
            ["ops.engine_version.engine_version_id"],
            name="fk_run_attempt_engine_version_id_engine_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["root_artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_run_attempt_root_artifact_id_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_attempt_id", name="pk_run_attempt"),
        sa.UniqueConstraint(
            "request_fingerprint", "attempt_number", name="uq_run_attempt_request_attempt"
        ),
        schema="ops",
    )
    op.create_index(
        "ix_run_attempt_status_created",
        "run_attempt",
        ["status", "created_at"],
        schema="ops",
    )

    _create_run_child_tables()


def _create_run_child_tables() -> None:
    op.create_table(
        "run_event",
        sa.Column("run_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("message", sa.String(length=2000), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("sequence_number >= 1", name="ck_run_event_sequence_number_positive"),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')", name="ck_run_event_severity_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["run_attempt_id"],
            ["ops.run_attempt.run_attempt_id"],
            name="fk_run_event_run_attempt_id_run_attempt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_event_id", name="pk_run_event"),
        sa.UniqueConstraint("run_attempt_id", "sequence_number", name="uq_run_event_run_sequence"),
        schema="ops",
    )

    op.create_table(
        "run_error",
        sa.Column("run_error_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=False),
        sa.Column("error_type", sa.String(length=200), nullable=False),
        sa.Column("message", sa.String(length=4000), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["run_attempt_id"],
            ["ops.run_attempt.run_attempt_id"],
            name="fk_run_error_run_attempt_id_run_attempt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_error_id", name="pk_run_error"),
        schema="ops",
    )
    op.create_index("ix_run_error_attempt", "run_error", ["run_attempt_id"], schema="ops")

    op.create_table(
        "run_artifact",
        sa.Column("run_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('input', 'output', 'log', 'diagnostic')",
            name="ck_run_artifact_role_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_run_artifact_artifact_id_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_attempt_id"],
            ["ops.run_attempt.run_attempt_id"],
            name="fk_run_artifact_run_attempt_id_run_attempt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_artifact_id", name="pk_run_artifact"),
        sa.UniqueConstraint(
            "run_attempt_id", "artifact_id", "role", name="uq_run_artifact_run_artifact_role"
        ),
        schema="ops",
    )
    op.create_index("ix_run_artifact_artifact", "run_artifact", ["artifact_id"], schema="ops")

    op.create_table(
        "quality_check_result",
        sa.Column("quality_check_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("check_key", sa.String(length=160), nullable=False),
        sa.Column("scope_key", sa.String(length=240), server_default="global", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("message", sa.String(length=2000), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')",
            name="ck_quality_check_result_severity_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'warning', 'failed')",
            name="ck_quality_check_result_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["run_attempt_id"],
            ["ops.run_attempt.run_attempt_id"],
            name="fk_quality_check_result_run_attempt_id_run_attempt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("quality_check_result_id", name="pk_quality_check_result"),
        sa.UniqueConstraint(
            "run_attempt_id", "check_key", "scope_key", name="uq_quality_check_run_check_scope"
        ),
        schema="ops",
    )
    op.create_index(
        "ix_quality_check_run_status",
        "quality_check_result",
        ["run_attempt_id", "status"],
        schema="ops",
    )


def downgrade() -> None:
    op.drop_table("quality_check_result", schema="ops")
    op.drop_table("run_artifact", schema="ops")
    op.drop_table("run_error", schema="ops")
    op.drop_table("run_event", schema="ops")
    op.drop_table("run_attempt", schema="ops")
    op.drop_table("engine_version", schema="ops")
    op.drop_table("engine_definition", schema="ops")
    op.drop_table("artifact_status_event", schema="lineage")
    op.drop_table("artifact_dependency", schema="lineage")
    op.drop_table("artifact", schema="lineage")
    for schema_name in reversed(SCHEMA_NAMES):
        op.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}"'))
