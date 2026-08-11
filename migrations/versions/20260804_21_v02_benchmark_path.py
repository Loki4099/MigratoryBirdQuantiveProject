# ruff: noqa: E501
"""Add versioned benchmark definitions and benchmark target paths.

Revision ID: 20260804_21_v02_benchmark_path
Revises: 20260804_20_v02_net_cost_path
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_21_v02_benchmark_path"
down_revision: str | None = "20260804_20_v02_net_cost_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _id(name: str) -> sa.Column[object]:
    return sa.Column(name, UUID, nullable=False)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table[:16]}_{column[:24]}", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "benchmark_definition",
        _id("benchmark_definition_id"),
        _id("artifact_id"),
        sa.Column("benchmark_key", sa.String(140), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("benchmark_def", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("benchmark_definition_id", name="pk_benchmark_definition"),
        sa.UniqueConstraint("artifact_id", name="uq_benchmark_definition_artifact"),
        sa.UniqueConstraint("benchmark_key", name="uq_benchmark_definition_key"),
        sa.CheckConstraint(
            "category IN ('product_primary', 'research')", name="ck_benchmark_definition_category"
        ),
        schema="experiment",
    )
    op.create_table(
        "benchmark_version",
        _id("benchmark_version_id"),
        _id("benchmark_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("target_rule", sa.String(80), nullable=False),
        sa.Column("member_role", sa.String(30), nullable=False),
        sa.Column("rebalance_policy", sa.String(80), nullable=False),
        sa.Column("initial_reserve_weight", sa.Numeric(24, 18), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk(
            "benchmark_ver",
            "benchmark_definition_id",
            "experiment.benchmark_definition.benchmark_definition_id",
        ),
        _fk("benchmark_ver", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("benchmark_version_id", name="pk_benchmark_version"),
        sa.UniqueConstraint("artifact_id", name="uq_benchmark_version_artifact"),
        sa.UniqueConstraint(
            "benchmark_definition_id", "version_number", name="uq_benchmark_version_identity"
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_benchmark_version_positive"),
        sa.CheckConstraint(
            "member_role IN ('candidate', 'benchmark')", name="ck_benchmark_version_role"
        ),
        sa.CheckConstraint("initial_reserve_weight = 1", name="ck_benchmark_initial_reserve"),
        schema="experiment",
    )

    op.alter_column("benchmark_target_path", "benchmark_asset_id", nullable=True, schema="strategy")
    op.add_column("benchmark_target_path", _id("benchmark_version_id"), schema="strategy")
    op.add_column(
        "benchmark_target_path", _id("reference_portfolio_target_path_id"), schema="strategy"
    )
    op.add_column("benchmark_target_path", _id("execution_policy_version_id"), schema="strategy")
    op.add_column("benchmark_target_path", _id("rebalance_schedule_version_id"), schema="strategy")
    op.add_column(
        "benchmark_target_path",
        sa.Column("simulation_end", sa.Date(), nullable=False),
        schema="strategy",
    )
    op.create_foreign_key(
        "fk_benchmark_target_version",
        "benchmark_target_path",
        "benchmark_version",
        ["benchmark_version_id"],
        ["benchmark_version_id"],
        source_schema="strategy",
        referent_schema="experiment",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_benchmark_target_reference",
        "benchmark_target_path",
        "portfolio_target_path",
        ["reference_portfolio_target_path_id"],
        ["portfolio_target_path_id"],
        source_schema="strategy",
        referent_schema="strategy",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_benchmark_target_execution",
        "benchmark_target_path",
        "execution_policy_version",
        ["execution_policy_version_id"],
        ["execution_policy_version_id"],
        source_schema="strategy",
        referent_schema="ops",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_benchmark_target_schedule",
        "benchmark_target_path",
        "rebalance_schedule_version",
        ["rebalance_schedule_version_id"],
        ["rebalance_schedule_version_id"],
        source_schema="strategy",
        referent_schema="ops",
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_benchmark_target_exact_inputs",
        "benchmark_target_path",
        ["reference_portfolio_target_path_id", "benchmark_version_id"],
        schema="strategy",
    )
    op.create_check_constraint(
        "ck_benchmark_target_simulation_end",
        "benchmark_target_path",
        "simulation_end IS NOT NULL",
        schema="strategy",
    )

    op.create_table(
        "benchmark_decision",
        _id("benchmark_decision_id"),
        _id("portfolio_target_path_id"),
        sa.Column("decision_date", sa.Date(), nullable=False),
        sa.Column("actual_holding_count", sa.Integer(), nullable=False),
        sa.Column("reserve_target_weight", sa.Numeric(24, 18), nullable=False),
        _fk(
            "benchmark_decision",
            "portfolio_target_path_id",
            "strategy.portfolio_target_path.portfolio_target_path_id",
        ),
        sa.PrimaryKeyConstraint("benchmark_decision_id", name="pk_benchmark_decision"),
        sa.UniqueConstraint(
            "portfolio_target_path_id", "decision_date", name="uq_benchmark_decision_date"
        ),
        sa.CheckConstraint("actual_holding_count >= 1", name="ck_benchmark_decision_holdings"),
        sa.CheckConstraint(
            "reserve_target_weight BETWEEN 0 AND 1", name="ck_benchmark_decision_reserve"
        ),
        schema="strategy",
    )
    op.create_table(
        "benchmark_asset_position",
        _id("benchmark_decision_id"),
        _id("asset_id"),
        sa.Column("target_weight", sa.Numeric(24, 18), nullable=False),
        _fk(
            "benchmark_position",
            "benchmark_decision_id",
            "strategy.benchmark_decision.benchmark_decision_id",
        ),
        _fk("benchmark_position", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint(
            "benchmark_decision_id", "asset_id", name="pk_benchmark_asset_position"
        ),
        sa.CheckConstraint(
            "target_weight > 0 AND target_weight <= 1", name="ck_benchmark_position_weight"
        ),
        schema="strategy",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.enforce_benchmark_artifact_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM data.assert_artifact_draft(CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END; $$;
        CREATE TRIGGER trg_benchmark_definition_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.benchmark_definition FOR EACH ROW EXECUTE FUNCTION experiment.enforce_benchmark_artifact_draft();
        CREATE TRIGGER trg_benchmark_version_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.benchmark_version FOR EACH ROW EXECUTE FUNCTION experiment.enforce_benchmark_artifact_draft();

        CREATE OR REPLACE FUNCTION strategy.validate_target_owner() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE engine_key_value text; eligibility_row record;
        BEGIN
            PERFORM data.assert_artifact_draft(CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END);
            IF TG_OP <> 'DELETE' THEN
                SELECT definition.engine_key INTO engine_key_value FROM ops.engine_version version JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id WHERE version.engine_version_id = NEW.engine_version_id;
                IF (NEW.target_type = 'model_strategy' AND engine_key_value <> 'strategy_target_engine') OR (NEW.target_type = 'benchmark' AND engine_key_value <> 'benchmark_target_engine') THEN
                    RAISE EXCEPTION 'Target path type and Target engine mismatch';
                END IF;
                SELECT universe_version_id, data_bundle_version_id INTO eligibility_row FROM catalog.eligibility_snapshot WHERE eligibility_snapshot_id = NEW.eligibility_snapshot_id;
                IF eligibility_row.universe_version_id <> NEW.universe_version_id OR eligibility_row.data_bundle_version_id <> NEW.data_bundle_version_id THEN
                    RAISE EXCEPTION 'target path context does not match eligibility';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END; $$;

        CREATE FUNCTION strategy.validate_benchmark_target() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE owner record; reference_row record; product_row record; benchmark_row record;
        BEGIN
            SELECT * INTO owner FROM strategy.portfolio_target_path WHERE portfolio_target_path_id = COALESCE(NEW.portfolio_target_path_id, OLD.portfolio_target_path_id);
            PERFORM data.assert_artifact_draft(owner.artifact_id);
            IF TG_OP <> 'DELETE' THEN
                SELECT path.*, model_owner.strategy_product_version_id INTO reference_row FROM strategy.portfolio_target_path path JOIN strategy.model_strategy_target_path model_owner ON model_owner.portfolio_target_path_id = path.portfolio_target_path_id WHERE path.portfolio_target_path_id = NEW.reference_portfolio_target_path_id AND path.target_type = 'model_strategy';
                SELECT execution_policy_version_id, rebalance_schedule_version_id INTO product_row FROM strategy.strategy_product_version WHERE strategy_product_version_id = reference_row.strategy_product_version_id;
                SELECT definition.benchmark_key, version.member_role INTO benchmark_row FROM experiment.benchmark_version version JOIN experiment.benchmark_definition definition ON definition.benchmark_definition_id = version.benchmark_definition_id WHERE version.benchmark_version_id = NEW.benchmark_version_id;
                IF reference_row.portfolio_target_path_id IS NULL OR owner.target_type <> 'benchmark' OR owner.universe_version_id <> reference_row.universe_version_id OR owner.data_bundle_version_id <> reference_row.data_bundle_version_id OR owner.eligibility_snapshot_id <> reference_row.eligibility_snapshot_id OR NEW.execution_policy_version_id <> product_row.execution_policy_version_id OR NEW.rebalance_schedule_version_id <> product_row.rebalance_schedule_version_id THEN
                    RAISE EXCEPTION 'Benchmark Target Path and reference Strategy Target context mismatch';
                END IF;
                IF (benchmark_row.member_role = 'benchmark') <> (NEW.benchmark_asset_id IS NOT NULL) THEN
                    RAISE EXCEPTION 'Benchmark asset identity does not match benchmark member role';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END; $$;
        CREATE TRIGGER trg_validate_benchmark_target BEFORE INSERT OR UPDATE OR DELETE ON strategy.benchmark_target_path FOR EACH ROW EXECUTE FUNCTION strategy.validate_benchmark_target();

        CREATE FUNCTION strategy.enforce_benchmark_child_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE path_id uuid; owner_artifact uuid;
        BEGIN
            IF TG_TABLE_NAME = 'benchmark_asset_position' THEN
                SELECT portfolio_target_path_id INTO path_id FROM strategy.benchmark_decision WHERE benchmark_decision_id = COALESCE(NEW.benchmark_decision_id, OLD.benchmark_decision_id);
            ELSE
                path_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.portfolio_target_path_id ELSE NEW.portfolio_target_path_id END;
            END IF;
            SELECT artifact_id INTO owner_artifact FROM strategy.portfolio_target_path WHERE portfolio_target_path_id = path_id;
            PERFORM data.assert_artifact_draft(owner_artifact);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END; $$;
        CREATE TRIGGER trg_benchmark_decision_draft BEFORE INSERT OR UPDATE OR DELETE ON strategy.benchmark_decision FOR EACH ROW EXECUTE FUNCTION strategy.enforce_benchmark_child_draft();
        CREATE TRIGGER trg_benchmark_position_draft BEFORE INSERT OR UPDATE OR DELETE ON strategy.benchmark_asset_position FOR EACH ROW EXECUTE FUNCTION strategy.enforce_benchmark_child_draft();

        CREATE OR REPLACE FUNCTION experiment.validate_gross_path() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE target_row record; required_execution uuid; engine_key_value text;
        BEGIN
            PERFORM data.assert_artifact_draft(CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END);
            IF TG_OP <> 'DELETE' THEN
                SELECT data_bundle_version_id, target_type INTO target_row FROM strategy.portfolio_target_path WHERE portfolio_target_path_id = NEW.portfolio_target_path_id;
                IF target_row.data_bundle_version_id <> NEW.data_bundle_version_id THEN RAISE EXCEPTION 'Gross path and target path data bundles mismatch'; END IF;
                IF target_row.target_type = 'model_strategy' THEN
                    SELECT product.execution_policy_version_id INTO required_execution FROM strategy.model_strategy_target_path owner JOIN strategy.strategy_product_version product ON product.strategy_product_version_id = owner.strategy_product_version_id WHERE owner.portfolio_target_path_id = NEW.portfolio_target_path_id;
                ELSE
                    SELECT execution_policy_version_id INTO required_execution FROM strategy.benchmark_target_path WHERE portfolio_target_path_id = NEW.portfolio_target_path_id;
                END IF;
                IF required_execution IS NULL OR required_execution <> NEW.execution_policy_version_id THEN RAISE EXCEPTION 'Gross path requires target execution policy'; END IF;
                SELECT definition.engine_key INTO engine_key_value FROM ops.engine_version version JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id WHERE version.engine_version_id = NEW.engine_version_id;
                IF engine_key_value <> 'portfolio_accounting_engine' THEN RAISE EXCEPTION 'Gross path requires a Portfolio Accounting engine'; END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END; $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS strategy.enforce_benchmark_child_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS strategy.validate_benchmark_target() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS experiment.enforce_benchmark_artifact_draft() CASCADE")
    op.drop_table("benchmark_asset_position", schema="strategy")
    op.drop_table("benchmark_decision", schema="strategy")
    op.drop_constraint(
        "ck_benchmark_target_simulation_end",
        "benchmark_target_path",
        schema="strategy",
        type_="check",
    )
    op.drop_constraint(
        "uq_benchmark_target_exact_inputs",
        "benchmark_target_path",
        schema="strategy",
        type_="unique",
    )
    for name in (
        "fk_benchmark_target_schedule",
        "fk_benchmark_target_execution",
        "fk_benchmark_target_reference",
        "fk_benchmark_target_version",
    ):
        op.drop_constraint(name, "benchmark_target_path", schema="strategy", type_="foreignkey")
    for column in (
        "simulation_end",
        "rebalance_schedule_version_id",
        "execution_policy_version_id",
        "reference_portfolio_target_path_id",
        "benchmark_version_id",
    ):
        op.drop_column("benchmark_target_path", column, schema="strategy")
    op.alter_column(
        "benchmark_target_path", "benchmark_asset_id", nullable=False, schema="strategy"
    )
    op.drop_table("benchmark_version", schema="experiment")
    op.drop_table("benchmark_definition", schema="experiment")
