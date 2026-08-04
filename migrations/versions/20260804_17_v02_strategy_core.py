"""Add immutable Strategy contracts, variants, schedules, and product identities.

Revision ID: 20260804_17_v02_strategy_core
Revises: 20260804_16_v02_model_eval
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_17_v02_strategy_core"
down_revision: str | None = "20260804_16_v02_model_eval"
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
        [column], [target], name=f"fk_{table[:16]}_{column[:24]}", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "rebalance_schedule_definition",
        _id("rebalance_schedule_definition_id"),
        _id("artifact_id"),
        sa.Column("schedule_key", sa.String(160), nullable=False),
        _created(),
        _fk("rebalance_schedule", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint(
            "rebalance_schedule_definition_id", name="pk_rebalance_schedule_definition"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_rebalance_schedule_definition_artifact"),
        sa.UniqueConstraint("schedule_key", name="uq_rebalance_schedule_definition_key"),
        schema="ops",
    )
    op.create_table(
        "rebalance_schedule_version",
        _id("rebalance_schedule_version_id"),
        _id("rebalance_schedule_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("decision_timing", sa.String(80), nullable=False),
        sa.Column("decision_data_policy", sa.String(80), nullable=False),
        _created(),
        _fk(
            "rebalance_sched_v",
            "rebalance_schedule_definition_id",
            "ops.rebalance_schedule_definition.rebalance_schedule_definition_id",
        ),
        _fk("rebalance_sched_v", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint(
            "rebalance_schedule_version_id", name="pk_rebalance_schedule_version"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_rebalance_schedule_version_artifact"),
        sa.UniqueConstraint(
            "rebalance_schedule_definition_id",
            "version_number",
            name="uq_rebalance_schedule_definition_version",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_rebalance_schedule_version_positive"),
        sa.CheckConstraint("frequency IN ('weekly', 'monthly')", name="ck_schedule_frequency"),
        schema="ops",
    )
    op.create_table(
        "execution_policy_definition",
        _id("execution_policy_definition_id"),
        _id("artifact_id"),
        sa.Column("policy_key", sa.String(160), nullable=False),
        _created(),
        _fk("execution_policy", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint(
            "execution_policy_definition_id", name="pk_execution_policy_definition"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_execution_policy_definition_artifact"),
        sa.UniqueConstraint("policy_key", name="uq_execution_policy_definition_key"),
        schema="ops",
    )
    op.create_table(
        "execution_policy_version",
        _id("execution_policy_version_id"),
        _id("execution_policy_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("delay_common_sessions", sa.Integer(), nullable=False),
        sa.Column("execution_price", sa.String(60), nullable=False),
        sa.Column("missing_execution_policy", sa.String(80), nullable=False),
        _created(),
        _fk(
            "execution_policy_v",
            "execution_policy_definition_id",
            "ops.execution_policy_definition.execution_policy_definition_id",
        ),
        _fk("execution_policy_v", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("execution_policy_version_id", name="pk_execution_policy_version"),
        sa.UniqueConstraint("artifact_id", name="uq_execution_policy_version_artifact"),
        sa.UniqueConstraint(
            "execution_policy_definition_id",
            "version_number",
            name="uq_execution_policy_definition_version",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_execution_policy_version_positive"),
        sa.CheckConstraint("delay_common_sessions >= 1", name="ck_execution_policy_delay_positive"),
        schema="ops",
    )
    op.create_table(
        "strategy_definition",
        _id("strategy_definition_id"),
        _id("artifact_id"),
        sa.Column("strategy_key", sa.String(160), nullable=False),
        sa.Column("strategy_family", sa.String(100), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        _created(),
        _fk("strategy_definition", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("strategy_definition_id", name="pk_strategy_definition"),
        sa.UniqueConstraint("artifact_id", name="uq_strategy_definition_artifact"),
        sa.UniqueConstraint("strategy_key", name="uq_strategy_definition_key"),
        schema="strategy",
    )
    op.create_table(
        "strategy_definition_version",
        _id("strategy_definition_version_id"),
        _id("strategy_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("selection_contract", sa.String(100), nullable=False),
        sa.Column("allocation_contract", sa.String(100), nullable=False),
        sa.Column("reserve_contract", sa.String(100), nullable=False),
        _created(),
        _fk(
            "strategy_def_v",
            "strategy_definition_id",
            "strategy.strategy_definition.strategy_definition_id",
        ),
        _fk("strategy_def_v", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint(
            "strategy_definition_version_id", name="pk_strategy_definition_version"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_strategy_definition_version_artifact"),
        sa.UniqueConstraint(
            "strategy_definition_id",
            "version_number",
            name="uq_strategy_definition_version",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_strategy_def_version_positive"),
        schema="strategy",
    )
    op.create_table(
        "strategy_input_contract",
        _id("strategy_input_contract_id"),
        _id("strategy_definition_version_id"),
        _id("artifact_id"),
        sa.Column("contract_key", sa.String(160), nullable=False),
        sa.Column("requires_model_score", sa.Boolean(), nullable=False),
        sa.Column("compatible_model_output_types", postgresql.JSONB(), nullable=False),
        sa.Column("candidate_input_policy", sa.String(100), nullable=False),
        sa.Column("missing_input_policy", sa.String(100), nullable=False),
        _created(),
        _fk(
            "strategy_input",
            "strategy_definition_version_id",
            "strategy.strategy_definition_version.strategy_definition_version_id",
        ),
        _fk("strategy_input", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("strategy_input_contract_id", name="pk_strategy_input_contract"),
        sa.UniqueConstraint("artifact_id", name="uq_strategy_input_contract_artifact"),
        sa.UniqueConstraint(
            "strategy_definition_version_id",
            "contract_key",
            name="uq_strategy_input_contract_key",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(compatible_model_output_types) = 'array'",
            name="ck_strategy_input_output_types_array",
        ),
        schema="strategy",
    )
    op.create_table(
        "strategy_variant",
        _id("strategy_variant_id"),
        _id("strategy_definition_version_id"),
        _id("strategy_input_contract_id"),
        _id("artifact_id"),
        sa.Column("auxiliary_signal_version_id", UUID),
        sa.Column("variant_key", sa.String(200), nullable=False),
        sa.Column("template_key", sa.String(160), nullable=False),
        sa.Column("target_k", sa.Integer(), nullable=False),
        sa.Column("research_tier", sa.String(30), nullable=False),
        sa.Column("selection_order", sa.String(80), nullable=False),
        sa.Column("trend_filter", sa.String(80), nullable=False),
        sa.Column("auxiliary_eligible_state", sa.String(40)),
        sa.Column("empty_slot_policy", sa.String(100), nullable=False),
        sa.Column("tie_policy", sa.String(100), nullable=False),
        sa.Column("slot_weight_rule", sa.String(40), nullable=False),
        sa.Column("reserve_rule", sa.String(100), nullable=False),
        _created(),
        _fk(
            "strategy_variant",
            "strategy_definition_version_id",
            "strategy.strategy_definition_version.strategy_definition_version_id",
        ),
        _fk(
            "strategy_variant",
            "strategy_input_contract_id",
            "strategy.strategy_input_contract.strategy_input_contract_id",
        ),
        _fk("strategy_variant", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "strategy_variant",
            "auxiliary_signal_version_id",
            "signal.signal_version.signal_version_id",
        ),
        sa.PrimaryKeyConstraint("strategy_variant_id", name="pk_strategy_variant"),
        sa.UniqueConstraint("artifact_id", name="uq_strategy_variant_artifact"),
        sa.UniqueConstraint("variant_key", name="uq_strategy_variant_key"),
        sa.CheckConstraint("target_k IN (1, 2, 3)", name="ck_strategy_variant_target_k"),
        sa.CheckConstraint(
            "research_tier IN ('canonical', 'sensitivity')",
            name="ck_strategy_variant_research_tier",
        ),
        sa.CheckConstraint(
            "(trend_filter = 'none' AND auxiliary_signal_version_id IS NULL "
            "AND auxiliary_eligible_state IS NULL) OR "
            "(trend_filter = 'published_threshold_state' "
            "AND auxiliary_signal_version_id IS NOT NULL "
            "AND auxiliary_eligible_state IS NOT NULL)",
            name="ck_strategy_variant_auxiliary_signal",
        ),
        schema="strategy",
    )
    _create_product_tables()
    _create_guards()


def _create_product_tables() -> None:
    op.create_table(
        "strategy_product_definition",
        _id("strategy_product_definition_id"),
        _id("artifact_id"),
        sa.Column("product_key", sa.String(700), nullable=False),
        _created(),
        _fk("strategy_product", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint(
            "strategy_product_definition_id", name="pk_strategy_product_definition"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_strategy_product_definition_artifact"),
        sa.UniqueConstraint("product_key", name="uq_strategy_product_definition_key"),
        schema="strategy",
    )
    op.create_table(
        "strategy_product_version",
        _id("strategy_product_version_id"),
        _id("strategy_product_definition_id"),
        _id("artifact_id"),
        _id("model_specification_id"),
        _id("strategy_variant_id"),
        _id("universe_version_id"),
        _id("rebalance_schedule_version_id"),
        _id("execution_policy_version_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        _created(),
        _fk(
            "strategy_product_v",
            "strategy_product_definition_id",
            "strategy.strategy_product_definition.strategy_product_definition_id",
        ),
        _fk("strategy_product_v", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "strategy_product_v",
            "model_specification_id",
            "model.model_specification.model_specification_id",
        ),
        _fk(
            "strategy_product_v",
            "strategy_variant_id",
            "strategy.strategy_variant.strategy_variant_id",
        ),
        _fk(
            "strategy_product_v",
            "universe_version_id",
            "catalog.universe_version.universe_version_id",
        ),
        _fk(
            "strategy_product_v",
            "rebalance_schedule_version_id",
            "ops.rebalance_schedule_version.rebalance_schedule_version_id",
        ),
        _fk(
            "strategy_product_v",
            "execution_policy_version_id",
            "ops.execution_policy_version.execution_policy_version_id",
        ),
        sa.PrimaryKeyConstraint("strategy_product_version_id", name="pk_strategy_product_version"),
        sa.UniqueConstraint("artifact_id", name="uq_strategy_product_version_artifact"),
        sa.UniqueConstraint(
            "strategy_product_definition_id",
            "version_number",
            name="uq_strategy_product_definition_version",
        ),
        sa.UniqueConstraint(
            "model_specification_id",
            "strategy_variant_id",
            "universe_version_id",
            "rebalance_schedule_version_id",
            "execution_policy_version_id",
            name="uq_strategy_product_complete_identity",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_strategy_product_version_positive"),
        schema="strategy",
    )


def _create_guards() -> None:
    for schema_name, table_name in (
        ("ops", "rebalance_schedule_definition"),
        ("ops", "rebalance_schedule_version"),
        ("ops", "execution_policy_definition"),
        ("ops", "execution_policy_version"),
        ("strategy", "strategy_definition"),
        ("strategy", "strategy_definition_version"),
        ("strategy", "strategy_input_contract"),
        ("strategy", "strategy_variant"),
        ("strategy", "strategy_product_definition"),
        ("strategy", "strategy_product_version"),
    ):
        trigger_name = f"trg_{table_name}_draft"
        op.execute(
            sa.text(
                f"CREATE TRIGGER {trigger_name} BEFORE INSERT OR UPDATE OR DELETE "
                f"ON {schema_name}.{table_name} FOR EACH ROW "
                "EXECUTE FUNCTION data.enforce_artifact_owned_draft()"
            )
        )


def downgrade() -> None:
    op.drop_table("strategy_product_version", schema="strategy")
    op.drop_table("strategy_product_definition", schema="strategy")
    op.drop_table("strategy_variant", schema="strategy")
    op.drop_table("strategy_input_contract", schema="strategy")
    op.drop_table("strategy_definition_version", schema="strategy")
    op.drop_table("strategy_definition", schema="strategy")
    op.drop_table("execution_policy_version", schema="ops")
    op.drop_table("execution_policy_definition", schema="ops")
    op.drop_table("rebalance_schedule_version", schema="ops")
    op.drop_table("rebalance_schedule_definition", schema="ops")
