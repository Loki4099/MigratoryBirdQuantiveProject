# ruff: noqa: E501
"""Add v0.21 predictive and fixed six-cell Portfolio Suite identity.

Revision ID: 20260805_33_v021_matrix
Revises: 20260805_32_v021_workspace
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260805_33_v021_matrix"
down_revision: str | None = "20260805_32_v021_workspace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
HASH_PATTERN = "^[0-9a-f]{64}$"


def _id(name: str) -> sa.Column[object]:
    return sa.Column(name, UUID, nullable=False)


def _created() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _fk(columns: list[str], target: list[str], name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(columns, target, name=name, ondelete="RESTRICT")


def upgrade() -> None:
    op.create_table(
        "execution_policy_catalog",
        _id("execution_policy_catalog_id"),
        _id("artifact_id"),
        sa.Column("policy_key", sa.String(180), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("document", JSONB, nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_execution_policy_artifact"),
        sa.PrimaryKeyConstraint("execution_policy_catalog_id", name="pk_execution_policy_catalog"),
        sa.UniqueConstraint("artifact_id", name="uq_execution_policy_artifact"),
        sa.UniqueConstraint("policy_key", "version_number", name="uq_execution_policy_version"),
        sa.CheckConstraint("version_number >= 1", name="ck_execution_policy_version"),
        schema="experiment",
    )
    op.create_table(
        "research_suite",
        _id("research_suite_id"),
        _id("artifact_id"),
        _id("compiled_research_spec_id"),
        _id("execution_policy_catalog_id"),
        sa.Column("suite_key", sa.String(200), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("suite_fingerprint", sa.String(64), nullable=False),
        sa.Column("predictive_cell_count", sa.Integer(), nullable=False),
        sa.Column("portfolio_cell_count", sa.Integer(), nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_research_suite_artifact"),
        _fk(
            ["compiled_research_spec_id"],
            ["workspace.compiled_research_spec.compiled_research_spec_id"],
            "fk_research_suite_compiled",
        ),
        _fk(
            ["execution_policy_catalog_id"],
            ["experiment.execution_policy_catalog.execution_policy_catalog_id"],
            "fk_research_suite_policy",
        ),
        sa.PrimaryKeyConstraint("research_suite_id", name="pk_research_suite"),
        sa.UniqueConstraint("artifact_id", name="uq_research_suite_artifact"),
        sa.UniqueConstraint("suite_fingerprint", name="uq_research_suite_fingerprint"),
        sa.UniqueConstraint("suite_key", "version_number", name="uq_research_suite_version"),
        sa.CheckConstraint(
            f"suite_fingerprint ~ '{HASH_PATTERN}'", name="ck_research_suite_fingerprint"
        ),
        sa.CheckConstraint(
            "predictive_cell_count >= 1 AND portfolio_cell_count >= 6 AND portfolio_cell_count % 6 = 0",
            name="ck_research_suite_counts",
        ),
        schema="experiment",
    )
    op.create_table(
        "predictive_cell_specification",
        _id("predictive_cell_specification_id"),
        _id("artifact_id"),
        _id("research_suite_id"),
        _id("compiled_model_instance_id"),
        sa.Column("cell_key", sa.String(300), nullable=False),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("evaluation_target_key", sa.String(200), nullable=False),
        sa.Column("cell_fingerprint", sa.String(64), nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_predictive_cell_artifact"),
        _fk(
            ["research_suite_id"],
            ["experiment.research_suite.research_suite_id"],
            "fk_predictive_cell_suite",
        ),
        _fk(
            ["compiled_model_instance_id"],
            ["workspace.compiled_model_instance.compiled_model_instance_id"],
            "fk_predictive_cell_model",
        ),
        sa.PrimaryKeyConstraint("predictive_cell_specification_id", name="pk_predictive_cell"),
        sa.UniqueConstraint("artifact_id", name="uq_predictive_cell_artifact"),
        sa.UniqueConstraint("research_suite_id", "cell_key", name="uq_predictive_cell_key"),
        sa.UniqueConstraint("cell_fingerprint", name="uq_predictive_cell_fingerprint"),
        sa.CheckConstraint(
            "frequency IN ('weekly','monthly')", name="ck_predictive_cell_frequency"
        ),
        sa.CheckConstraint(
            f"cell_fingerprint ~ '{HASH_PATTERN}'", name="ck_predictive_cell_fingerprint"
        ),
        schema="experiment",
    )
    op.create_table(
        "portfolio_cell_specification",
        _id("portfolio_cell_specification_id"),
        _id("artifact_id"),
        _id("research_suite_id"),
        _id("compiled_strategy_version_id"),
        sa.Column("cell_key", sa.String(420), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("window_key", sa.String(40), nullable=False),
        sa.Column("cost_key", sa.String(80), nullable=False),
        sa.Column("cost_bps_per_side", sa.Integer(), nullable=False),
        sa.Column("initial_capital_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("initialization_policy", sa.String(24), nullable=False),
        sa.Column("state_reset", sa.Boolean(), nullable=False),
        sa.Column("capacity_adv_limit", sa.Numeric(8, 6), nullable=False),
        sa.Column("cell_fingerprint", sa.String(64), nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_portfolio_cell_artifact"),
        _fk(
            ["research_suite_id"],
            ["experiment.research_suite.research_suite_id"],
            "fk_portfolio_cell_suite",
        ),
        _fk(
            ["compiled_strategy_version_id"],
            ["strategy.compiled_strategy_version.compiled_strategy_version_id"],
            "fk_portfolio_cell_strategy",
        ),
        sa.PrimaryKeyConstraint("portfolio_cell_specification_id", name="pk_portfolio_cell"),
        sa.UniqueConstraint("artifact_id", name="uq_portfolio_cell_artifact"),
        sa.UniqueConstraint("research_suite_id", "cell_key", name="uq_portfolio_cell_key"),
        sa.UniqueConstraint("research_suite_id", "ordinal", name="uq_portfolio_cell_ordinal"),
        sa.UniqueConstraint("cell_fingerprint", name="uq_portfolio_cell_fingerprint"),
        sa.CheckConstraint("ordinal >= 0", name="ck_portfolio_cell_ordinal"),
        sa.CheckConstraint(
            "window_key IN ('full_common_history','trailing_3_years','trailing_1_year')",
            name="ck_portfolio_cell_window",
        ),
        sa.CheckConstraint("cost_bps_per_side IN (5,10)", name="ck_portfolio_cell_cost"),
        sa.CheckConstraint("initial_capital_usd = 100000000.00", name="ck_portfolio_cell_capital"),
        sa.CheckConstraint(
            "initialization_policy = 'fresh_start' AND state_reset",
            name="ck_portfolio_cell_fresh_start",
        ),
        sa.CheckConstraint("capacity_adv_limit = 0.05", name="ck_portfolio_cell_capacity"),
        sa.CheckConstraint(
            f"cell_fingerprint ~ '{HASH_PATTERN}'", name="ck_portfolio_cell_fingerprint"
        ),
        schema="experiment",
    )
    op.create_table(
        "research_suite_work_item",
        _id("research_suite_id"),
        _id("cell_artifact_id"),
        _id("work_item_id"),
        sa.Column("cell_type", sa.String(20), nullable=False),
        _created(),
        _fk(
            ["research_suite_id"],
            ["experiment.research_suite.research_suite_id"],
            "fk_suite_work_item_suite",
        ),
        _fk(["cell_artifact_id"], ["lineage.artifact.artifact_id"], "fk_suite_work_item_cell"),
        _fk(["work_item_id"], ["ops.work_item.work_item_id"], "fk_suite_work_item_work"),
        sa.PrimaryKeyConstraint("research_suite_id", "cell_artifact_id", name="pk_suite_work_item"),
        sa.UniqueConstraint("work_item_id", name="uq_suite_work_item_work"),
        sa.CheckConstraint(
            "cell_type IN ('predictive','portfolio')", name="ck_suite_work_item_type"
        ),
        schema="experiment",
    )


def downgrade() -> None:
    op.drop_table("research_suite_work_item", schema="experiment")
    op.drop_table("portfolio_cell_specification", schema="experiment")
    op.drop_table("predictive_cell_specification", schema="experiment")
    op.drop_table("research_suite", schema="experiment")
    op.drop_table("execution_policy_catalog", schema="experiment")
