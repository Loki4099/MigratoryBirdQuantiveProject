"""Freeze qualification evidence and enforce post-activation monitoring metadata.

Revision ID: 20260806_38_v021_evidence
Revises: 20260806_37_v021_lifecycle
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260806_38_v021_evidence"
down_revision: str | None = "20260806_37_v021_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "qualification_bundle",
        sa.Column("result_artifact_ids", postgresql.ARRAY(UUID)),
        schema="experiment",
    )
    op.add_column(
        "qualification_bundle",
        sa.Column("cell_artifact_ids", postgresql.ARRAY(UUID)),
        schema="experiment",
    )
    op.add_column(
        "qualification_bundle",
        sa.Column("selection_context", postgresql.JSONB()),
        schema="experiment",
    )
    op.alter_column("product_enrollment", "monitoring_start_at", nullable=True, schema="product")
    op.add_column(
        "monitoring_work_item",
        sa.Column(
            "held_during_suspension", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        schema="product",
    )


def downgrade() -> None:
    op.drop_column("monitoring_work_item", "held_during_suspension", schema="product")
    op.alter_column("product_enrollment", "monitoring_start_at", nullable=False, schema="product")
    op.drop_column("qualification_bundle", "selection_context", schema="experiment")
    op.drop_column("qualification_bundle", "cell_artifact_ids", schema="experiment")
    op.drop_column("qualification_bundle", "result_artifact_ids", schema="experiment")
