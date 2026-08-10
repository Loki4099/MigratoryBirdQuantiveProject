"""Persist compiled Model parameters and target identity.

Revision ID: 20260806_34_v021_model
Revises: 20260805_33_v021_matrix
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260806_34_v021_model"
down_revision: str | None = "20260805_33_v021_matrix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "compiled_model_instance",
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        schema="workspace",
    )
    op.add_column(
        "compiled_model_instance",
        sa.Column("target_key", sa.String(200), nullable=True),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_column("compiled_model_instance", "target_key", schema="workspace")
    op.drop_column("compiled_model_instance", "parameters", schema="workspace")
