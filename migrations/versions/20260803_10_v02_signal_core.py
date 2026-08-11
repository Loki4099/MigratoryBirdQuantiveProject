"""Add versioned signal definitions and transformation specifications.

Revision ID: 20260803_10_v02_signal_core
Revises: 20260803_09_v02_factor_diag
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_10_v02_signal_core"
down_revision: str | None = "20260803_09_v02_factor_diag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _id(name: str) -> sa.Column[object]:
    return sa.Column(name, UUID, nullable=False)


def _created() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [target],
        name=f"fk_{table[:16]}_{column[:24]}",
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        "signal_definition",
        _id("signal_definition_id"),
        _id("artifact_id"),
        sa.Column("signal_key", sa.String(300), nullable=False),
        sa.Column("template_key", sa.String(120), nullable=False),
        sa.Column("economic_family", sa.String(80), nullable=False),
        sa.Column("rationale_type", sa.String(40), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("research_tier", sa.String(30), nullable=False),
        sa.Column("product_eligible", sa.Boolean(), nullable=False),
        _created(),
        _fk("signal_definition", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("signal_definition_id", name="pk_signal_definition"),
        sa.UniqueConstraint("artifact_id", name="uq_signal_definition_artifact_id"),
        sa.UniqueConstraint("signal_key", name="uq_signal_definition_signal_key"),
        sa.CheckConstraint(
            "rationale_type IN ('academic', 'institutional_research', "
            "'market_convention', 'practitioner_hypothesis')",
            name="ck_signal_definition_rationale_type",
        ),
        sa.CheckConstraint(
            "research_tier IN ('canonical', 'sensitivity', 'exploratory')",
            name="ck_signal_definition_research_tier",
        ),
        schema="signal",
    )
    op.create_index(
        "ix_signal_definition_template_key",
        "signal_definition",
        ["template_key"],
        schema="signal",
    )
    op.create_table(
        "signal_version",
        _id("signal_version_id"),
        _id("signal_definition_id"),
        _id("factor_variant_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(30), nullable=False),
        sa.Column("normalization", sa.String(80), nullable=False),
        sa.Column("extreme_policy", sa.String(80), nullable=False),
        sa.Column("missing_policy", sa.String(80), nullable=False),
        sa.Column("tie_policy", sa.String(40), nullable=False),
        sa.Column("output_type", sa.String(40), nullable=False),
        sa.Column("rule", postgresql.JSONB()),
        sa.Column("calculation_frequency", sa.String(30), nullable=False),
        sa.Column("time_semantics", sa.String(160), nullable=False),
        sa.Column("evaluation_horizon_policy", sa.String(100), nullable=False),
        _created(),
        _fk(
            "signal_version",
            "signal_definition_id",
            "signal.signal_definition.signal_definition_id",
        ),
        _fk("signal_version", "factor_variant_id", "factor.factor_variant.factor_variant_id"),
        _fk("signal_version", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("signal_version_id", name="pk_signal_version"),
        sa.UniqueConstraint("artifact_id", name="uq_signal_version_artifact_id"),
        sa.UniqueConstraint(
            "signal_definition_id",
            "version_number",
            name="uq_signal_version_definition_version",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_signal_version_version_positive"),
        sa.CheckConstraint(
            "direction IN ('higher_is_better', 'lower_is_better')",
            name="ck_signal_version_direction",
        ),
        sa.CheckConstraint(
            "output_type IN ('continuous', 'threshold_state', 'crossover_event', 'recent_event')",
            name="ck_signal_version_output_type",
        ),
        sa.CheckConstraint(
            "(output_type = 'continuous' AND rule IS NULL) OR "
            "(output_type <> 'continuous' AND jsonb_typeof(rule) = 'object')",
            name="ck_signal_version_rule_matches_output",
        ),
        sa.CheckConstraint(
            "calculation_frequency = 'daily'",
            name="ck_signal_version_daily_calculation",
        ),
        schema="signal",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_signal_definition_draft
        BEFORE INSERT OR UPDATE OR DELETE ON signal.signal_definition
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_signal_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON signal.signal_version
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        """
    )


def downgrade() -> None:
    op.drop_table("signal_version", schema="signal")
    op.drop_index(
        "ix_signal_definition_template_key",
        table_name="signal_definition",
        schema="signal",
    )
    op.drop_table("signal_definition", schema="signal")
