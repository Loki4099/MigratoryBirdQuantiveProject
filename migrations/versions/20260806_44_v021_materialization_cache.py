"""Index content-addressed Workspace materializations.

Revision ID: 20260806_44_v021_materialization
Revises: 20260806_43_v021_exploratory
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260806_44_v021_materialization"
down_revision: str | None = "20260806_43_v021_exploratory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_materialization_cache",
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column(
            "data_bundle_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("materializer_version", sa.String(80), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("semantic", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("cache_key", name="pk_research_materialization_cache"),
        sa.CheckConstraint(
            "cache_key ~ '^[0-9a-f]{64}$'", name="ck_research_materialization_cache_key"
        ),
        sa.CheckConstraint("row_count >= 1", name="ck_research_materialization_row_count"),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("research_materialization_cache", schema="workspace")
