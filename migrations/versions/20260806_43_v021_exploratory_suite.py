"""Separate exploratory and formal v0.21 Research Suites.

Revision ID: 20260806_43_v021_exploratory
Revises: 20260806_42_v021_oos
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_43_v021_exploratory"
down_revision: str | None = "20260806_42_v021_oos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_suite",
        sa.Column("suite_mode", sa.String(20), server_default="formal", nullable=False),
        schema="experiment",
    )
    op.create_check_constraint(
        "ck_research_suite_mode",
        "research_suite",
        "suite_mode IN ('formal','exploratory')",
        schema="experiment",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_research_suite_mode", "research_suite", schema="experiment", type_="check"
    )
    op.drop_column("research_suite", "suite_mode", schema="experiment")
