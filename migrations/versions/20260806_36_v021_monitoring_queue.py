"""Bind Product monitoring jobs to the persistent Work queue.

Revision ID: 20260806_36_v021_monitor
Revises: 20260806_35_v021_result
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260806_36_v021_monitor"
down_revision: str | None = "20260806_35_v021_result"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "monitoring_work_item",
        sa.Column("work_item_id", UUID, nullable=False),
        sa.Column("product_enrollment_id", UUID, nullable=False),
        sa.Column("data_bundle_artifact_id", UUID, nullable=False),
        sa.Column("as_of_session", sa.Date(), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["ops.work_item.work_item_id"],
            name="fk_monitoring_work_item_work",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_enrollment_id"],
            ["product.product_enrollment.product_enrollment_id"],
            name="fk_monitoring_work_item_enrollment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["data_bundle_artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_monitoring_work_item_data",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("work_item_id", name="pk_monitoring_work_item"),
        sa.UniqueConstraint(
            "product_enrollment_id",
            "data_bundle_artifact_id",
            "known_at",
            name="uq_monitoring_work_item_vintage",
        ),
        schema="product",
    )


def downgrade() -> None:
    op.drop_table("monitoring_work_item", schema="product")
