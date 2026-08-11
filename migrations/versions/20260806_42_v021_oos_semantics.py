"""Freeze OOS rebalance intent and protect Product reviews.

Revision ID: 20260806_42_v021_oos
Revises: 20260806_41_v021_append_only
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_42_v021_oos"
down_revision: str | None = "20260806_41_v021_append_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "monitoring_work_item",
        sa.Column("rebalance_due", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema="product",
    )
    op.execute("""
        CREATE TRIGGER trg_product_review_append_only
        BEFORE UPDATE OR DELETE ON product.product_review
        FOR EACH ROW EXECUTE FUNCTION product.reject_audit_record_mutation()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_product_review_append_only ON product.product_review")
    op.drop_column("monitoring_work_item", "rebalance_due", schema="product")
