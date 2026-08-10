# ruff: noqa: E501
"""Add the immutable Workspace component catalog.

Revision ID: 20260805_32_v021_workspace
Revises: 20260805_31_v021_assets
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260805_32_v021_workspace"
down_revision: str | None = "20260805_31_v021_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "component_catalog",
        sa.Column("component_catalog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_key", sa.String(180), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("catalog_version", sa.String(32), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_component_catalog_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("component_catalog_id", name="pk_component_catalog"),
        sa.UniqueConstraint("artifact_id", name="uq_component_catalog_artifact"),
        sa.UniqueConstraint("catalog_key", "version_number", name="uq_component_catalog_version"),
        sa.CheckConstraint("version_number >= 1", name="ck_component_catalog_version"),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("component_catalog", schema="workspace")
