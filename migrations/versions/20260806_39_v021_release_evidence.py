"""Add versioned evidence bindings for v0.21 release gates.

Revision ID: 20260806_39_v021_gates
Revises: 20260806_38_v021_evidence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260806_39_v021_gates"
down_revision: str | None = "20260806_38_v021_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "release_gate_evidence",
        sa.Column("release_gate_evidence_id", UUID, nullable=False),
        sa.Column("artifact_id", UUID, nullable=False),
        sa.Column("gate_key", sa.String(80), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("source_evidence_artifact_id", UUID, nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"], ["lineage.artifact.artifact_id"], name="fk_release_gate_artifact"
        ),
        sa.ForeignKeyConstraint(
            ["source_evidence_artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_release_gate_source",
        ),
        sa.PrimaryKeyConstraint("release_gate_evidence_id", name="pk_release_gate_evidence"),
        sa.UniqueConstraint("artifact_id", name="uq_release_gate_artifact"),
        sa.UniqueConstraint("gate_key", "version_number", name="uq_release_gate_version"),
        sa.CheckConstraint(
            "gate_key IN ('pit_universe','terminal_event','impact_policy')",
            name="ck_release_gate_key",
        ),
        schema="workspace",
    )
    op.create_index(
        "uq_release_gate_active",
        "release_gate_evidence",
        ["gate_key"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("active"),
    )


def downgrade() -> None:
    op.drop_index("uq_release_gate_active", table_name="release_gate_evidence", schema="workspace")
    op.drop_table("release_gate_evidence", schema="workspace")
