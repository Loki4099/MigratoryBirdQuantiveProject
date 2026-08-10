"""Persist command idempotency results.

Revision ID: 20260806_40_v021_idempotency
Revises: 20260806_39_v021_gates
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260806_40_v021_idempotency"
down_revision: str | None = "20260806_39_v021_gates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "command_result",
        sa.Column("command_name", sa.String(120), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("command_name", "idempotency_key", name="pk_command_result"),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_command_result_fingerprint"
        ),
        schema="ops",
    )


def downgrade() -> None:
    op.drop_table("command_result", schema="ops")
