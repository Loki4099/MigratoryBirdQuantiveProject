"""Track requested versus applied Product lifecycle transitions.

Revision ID: 20260806_37_v021_lifecycle
Revises: 20260806_36_v021_monitor
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_37_v021_lifecycle"
down_revision: str | None = "20260806_36_v021_monitor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_lifecycle_event",
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        schema="product",
    )
    op.execute(
        "UPDATE product.product_lifecycle_event SET applied_at = effective_at "
        "WHERE applied_at IS NULL"
    )
    op.create_index(
        "ix_product_lifecycle_event_pending",
        "product_lifecycle_event",
        ["effective_at"],
        unique=False,
        schema="product",
        postgresql_where=sa.text("applied_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_lifecycle_event_pending",
        table_name="product_lifecycle_event",
        schema="product",
    )
    op.drop_column("product_lifecycle_event", "applied_at", schema="product")
