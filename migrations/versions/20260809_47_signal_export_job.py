"""Persist asynchronous Signal research exports.

Revision ID: 20260809_47_signal_export_job
Revises: 20260809_46_v021_cell_payload
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260809_47_signal_export_job"
down_revision: str | None = "20260809_46_v021_cell_payload"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
HASH_PATTERN = "^[0-9a-f]{64}$"


def upgrade() -> None:
    op.create_table(
        "research_export_job",
        sa.Column("export_job_id", UUID, nullable=False),
        sa.Column("work_item_id", UUID, nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_document", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["ops.work_item.work_item_id"],
            name="fk_research_export_job_work_item",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("export_job_id", name="pk_research_export_job"),
        sa.UniqueConstraint("work_item_id", name="uq_research_export_job_work_item"),
        sa.CheckConstraint(
            f"request_fingerprint ~ '{HASH_PATTERN}'",
            name="ck_research_export_job_fingerprint",
        ),
        schema="signal",
    )
    op.create_index(
        "ix_research_export_job_request",
        "research_export_job",
        ["request_fingerprint", "created_at"],
        schema="signal",
    )
    op.create_table(
        "research_export_result",
        sa.Column("export_result_id", UUID, nullable=False),
        sa.Column("export_job_id", UUID, nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.String(240), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now() + interval '14 days'"),
            nullable=False,
        ),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["export_job_id"],
            ["signal.research_export_job.export_job_id"],
            name="fk_research_export_result_job",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("export_result_id", name="pk_research_export_result"),
        sa.UniqueConstraint("export_job_id", name="uq_research_export_result_job"),
        sa.CheckConstraint(
            f"content_hash ~ '{HASH_PATTERN}'", name="ck_research_export_result_hash"
        ),
        sa.CheckConstraint("byte_size > 0", name="ck_research_export_result_size"),
        sa.CheckConstraint(
            "storage_uri = 'signal-export://sha256/' || content_hash || '.zip'",
            name="ck_research_export_result_uri",
        ),
        sa.CheckConstraint(
            "schema_version = 'signal_research_export_zip_v1'",
            name="ck_research_export_result_schema",
        ),
        schema="signal",
    )
    op.create_index(
        "ix_research_export_result_content_hash",
        "research_export_result",
        ["content_hash"],
        schema="signal",
    )
    op.create_index(
        "ix_research_export_result_expiry",
        "research_export_result",
        ["expires_at"],
        schema="signal",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_export_result_expiry",
        table_name="research_export_result",
        schema="signal",
    )
    op.drop_index(
        "ix_research_export_result_content_hash",
        table_name="research_export_result",
        schema="signal",
    )
    op.drop_table("research_export_result", schema="signal")
    op.drop_index(
        "ix_research_export_job_request",
        table_name="research_export_job",
        schema="signal",
    )
    op.drop_table("research_export_job", schema="signal")
