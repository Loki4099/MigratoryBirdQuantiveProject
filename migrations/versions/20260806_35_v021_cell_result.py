"""Add atomic v0.21 Cell result materialization.

Revision ID: 20260806_35_v021_result
Revises: 20260806_34_v021_model
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260806_35_v021_result"
down_revision: str | None = "20260806_34_v021_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "cell_result",
        sa.Column("cell_result_id", UUID, nullable=False),
        sa.Column("artifact_id", UUID, nullable=False),
        sa.Column("cell_artifact_id", UUID, nullable=False),
        sa.Column("work_item_id", UUID, nullable=False),
        sa.Column("result_type", sa.String(20), nullable=False),
        sa.Column("result_fingerprint", sa.String(64), nullable=False),
        sa.Column("availability_status", sa.String(24), nullable=False),
        sa.Column("quality_status", sa.String(24), nullable=False),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column("series", JSONB, nullable=False),
        sa.Column("diagnostics", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_cell_result_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cell_artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_cell_result_cell",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["ops.work_item.work_item_id"],
            name="fk_cell_result_work_item",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("cell_result_id", name="pk_cell_result"),
        sa.UniqueConstraint("artifact_id", name="uq_cell_result_artifact"),
        sa.UniqueConstraint("work_item_id", name="uq_cell_result_work_item"),
        sa.UniqueConstraint("result_fingerprint", name="uq_cell_result_fingerprint"),
        sa.CheckConstraint(
            "result_type IN ('predictive','portfolio')",
            name="ck_cell_result_type",
        ),
        sa.CheckConstraint(
            "result_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_cell_result_fingerprint",
        ),
        sa.CheckConstraint(
            "availability_status IN ('accepted','capacity_rejected','data_quality_failed')",
            name="ck_cell_result_availability",
        ),
        sa.CheckConstraint(
            "quality_status IN ('passed','warning')",
            name="ck_cell_result_quality",
        ),
        schema="experiment",
    )


def downgrade() -> None:
    op.drop_table("cell_result", schema="experiment")
