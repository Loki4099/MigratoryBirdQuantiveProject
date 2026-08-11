"""Add operational backup and restore verification records.

Revision ID: 20260805_26_v02_backup
Revises: 20260804_25_v02_cohort
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260805_26_v02_backup"
down_revision: str | None = "20260804_25_v02_cohort"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backup_record",
        sa.Column("backup_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("system_version", sa.String(40), nullable=False),
        sa.Column("schema_revision", sa.String(80), nullable=False),
        sa.Column("git_commit", sa.String(64), nullable=False),
        sa.Column("dump_sha256", sa.String(64), nullable=False),
        sa.Column("storage_reference", sa.Text(), nullable=False),
        sa.Column("byte_count", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restore_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("backup_record_id", name="pk_backup_record"),
        sa.UniqueConstraint("dump_sha256", name="uq_backup_record_dump_sha256"),
        sa.CheckConstraint("git_commit ~ '^[0-9a-f]{7,64}$'", name="ck_backup_git_commit"),
        sa.CheckConstraint("dump_sha256 ~ '^[0-9a-f]{64}$'", name="ck_backup_sha256"),
        sa.CheckConstraint("byte_count > 0", name="ck_backup_byte_count"),
        sa.CheckConstraint(
            "status IN ('verified','restore_tested','failed')", name="ck_backup_status"
        ),
        schema="ops",
    )
    op.create_index("ix_backup_record_created_at", "backup_record", ["created_at"], schema="ops")


def downgrade() -> None:
    op.drop_index("ix_backup_record_created_at", table_name="backup_record", schema="ops")
    op.drop_table("backup_record", schema="ops")
