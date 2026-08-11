# ruff: noqa: E501
"""Add immutable gross portfolio accounting paths.

Revision ID: 20260804_19_v02_gross_path
Revises: 20260804_18_v02_strategy_target
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_19_v02_gross_path"
down_revision: str | None = "20260804_18_v02_strategy_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
NUMERIC = sa.Numeric(38, 24)


def _id(name: str) -> sa.Column[object]:
    return sa.Column(name, UUID, nullable=False)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table[:16]}_{column[:24]}", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "gross_portfolio_path",
        _id("gross_portfolio_path_id"),
        _id("artifact_id"),
        _id("portfolio_target_path_id"),
        _id("data_bundle_version_id"),
        _id("execution_policy_version_id"),
        _id("reserve_return_model_version_id"),
        _id("engine_version_id"),
        sa.Column("simulation_end", sa.Date(), nullable=False),
        sa.Column("first_decision_date", sa.Date(), nullable=False),
        sa.Column("first_execution_date", sa.Date(), nullable=False),
        sa.Column("effective_nav_start", sa.Date(), nullable=False),
        sa.Column("effective_nav_end", sa.Date(), nullable=False),
        sa.Column("nav_count", sa.BigInteger(), nullable=False),
        sa.Column("execution_count", sa.BigInteger(), nullable=False),
        sa.Column("trade_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("gross_path", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "gross_path",
            "portfolio_target_path_id",
            "strategy.portfolio_target_path.portfolio_target_path_id",
        ),
        _fk(
            "gross_path",
            "data_bundle_version_id",
            "data.data_bundle_version.data_bundle_version_id",
        ),
        _fk(
            "gross_path",
            "execution_policy_version_id",
            "ops.execution_policy_version.execution_policy_version_id",
        ),
        _fk(
            "gross_path",
            "reserve_return_model_version_id",
            "experiment.reserve_return_model_version.reserve_return_model_version_id",
        ),
        _fk("gross_path", "engine_version_id", "ops.engine_version.engine_version_id"),
        sa.PrimaryKeyConstraint("gross_portfolio_path_id", name="pk_gross_portfolio_path"),
        sa.UniqueConstraint("artifact_id", name="uq_gross_portfolio_path_artifact"),
        sa.UniqueConstraint(
            "portfolio_target_path_id",
            "engine_version_id",
            "simulation_end",
            name="uq_gross_path_exact_inputs",
        ),
        sa.CheckConstraint(
            "first_decision_date < first_execution_date", name="ck_gross_path_first_execution"
        ),
        sa.CheckConstraint(
            "effective_nav_start = first_execution_date AND effective_nav_start <= effective_nav_end AND effective_nav_end <= simulation_end",
            name="ck_gross_path_coverage",
        ),
        sa.CheckConstraint(
            "nav_count >= 1 AND execution_count >= 1 AND trade_count >= execution_count",
            name="ck_gross_path_counts",
        ),
        schema="experiment",
    )
    op.create_table(
        "gross_daily_nav",
        _id("gross_portfolio_path_id"),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("daily_return", NUMERIC, nullable=False),
        sa.Column("gross_nav", NUMERIC, nullable=False),
        sa.Column("overnight_factor", NUMERIC, nullable=False),
        sa.Column("intraday_factor", NUMERIC, nullable=False),
        _fk(
            "gross_daily_nav",
            "gross_portfolio_path_id",
            "experiment.gross_portfolio_path.gross_portfolio_path_id",
        ),
        sa.PrimaryKeyConstraint("gross_portfolio_path_id", "nav_date", name="pk_gross_daily_nav"),
        sa.CheckConstraint(
            "gross_nav > 0 AND overnight_factor > 0 AND intraday_factor > 0",
            name="ck_gross_daily_nav_positive",
        ),
        schema="experiment",
    )
    op.create_table(
        "daily_asset_position",
        _id("gross_portfolio_path_id"),
        sa.Column("nav_date", sa.Date(), nullable=False),
        _id("asset_id"),
        sa.Column("close_weight", NUMERIC, nullable=False),
        _fk(
            "daily_asset_position",
            "gross_portfolio_path_id",
            "experiment.gross_portfolio_path.gross_portfolio_path_id",
        ),
        _fk("daily_asset_position", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint(
            "gross_portfolio_path_id", "nav_date", "asset_id", name="pk_daily_asset_position"
        ),
        sa.CheckConstraint("close_weight BETWEEN 0 AND 1", name="ck_daily_asset_position_weight"),
        schema="experiment",
    )
    op.create_table(
        "daily_reserve_position",
        _id("gross_portfolio_path_id"),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("close_weight", NUMERIC, nullable=False),
        sa.Column("source_observation_date", sa.Date()),
        sa.Column("source_available_date", sa.Date()),
        sa.Column("quality_status", sa.String(20), nullable=False),
        _fk(
            "daily_reserve_position",
            "gross_portfolio_path_id",
            "experiment.gross_portfolio_path.gross_portfolio_path_id",
        ),
        sa.PrimaryKeyConstraint(
            "gross_portfolio_path_id", "nav_date", name="pk_daily_reserve_position"
        ),
        sa.CheckConstraint("close_weight BETWEEN 0 AND 1", name="ck_daily_reserve_position_weight"),
        sa.CheckConstraint(
            "quality_status IN ('not_applicable', 'normal', 'warning')",
            name="ck_daily_reserve_position_quality",
        ),
        sa.CheckConstraint(
            "(quality_status = 'not_applicable') = (source_observation_date IS NULL AND source_available_date IS NULL)",
            name="ck_daily_reserve_position_source",
        ),
        schema="experiment",
    )
    op.create_table(
        "portfolio_execution",
        _id("portfolio_execution_id"),
        _id("gross_portfolio_path_id"),
        sa.Column("decision_date", sa.Date(), nullable=False),
        sa.Column("execution_date", sa.Date(), nullable=False),
        sa.Column("gross_pretrade_nav", NUMERIC, nullable=False),
        sa.Column("one_way_turnover", NUMERIC, nullable=False),
        sa.Column("gross_traded_fraction", NUMERIC, nullable=False),
        sa.Column("pretrade_reserve_weight", NUMERIC, nullable=False),
        sa.Column("posttrade_reserve_weight", NUMERIC, nullable=False),
        _fk(
            "portfolio_execution",
            "gross_portfolio_path_id",
            "experiment.gross_portfolio_path.gross_portfolio_path_id",
        ),
        sa.PrimaryKeyConstraint("portfolio_execution_id", name="pk_portfolio_execution"),
        sa.UniqueConstraint(
            "gross_portfolio_path_id", "execution_date", name="uq_portfolio_execution_date"
        ),
        sa.CheckConstraint(
            "decision_date < execution_date AND gross_pretrade_nav > 0",
            name="ck_portfolio_execution_dates_nav",
        ),
        sa.CheckConstraint(
            "one_way_turnover BETWEEN 0 AND 1 AND gross_traded_fraction BETWEEN 0 AND 2",
            name="ck_portfolio_execution_turnover",
        ),
        sa.CheckConstraint(
            "pretrade_reserve_weight BETWEEN 0 AND 1 AND posttrade_reserve_weight BETWEEN 0 AND 1",
            name="ck_portfolio_execution_reserve",
        ),
        schema="experiment",
    )
    op.create_table(
        "portfolio_trade",
        _id("portfolio_execution_id"),
        _id("asset_id"),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("adjusted_execution_price", NUMERIC, nullable=False),
        sa.Column("pretrade_weight", NUMERIC, nullable=False),
        sa.Column("target_weight", NUMERIC, nullable=False),
        sa.Column("signed_weight_change", NUMERIC, nullable=False),
        sa.Column("absolute_weight_change", NUMERIC, nullable=False),
        _fk(
            "portfolio_trade",
            "portfolio_execution_id",
            "experiment.portfolio_execution.portfolio_execution_id",
        ),
        _fk("portfolio_trade", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint("portfolio_execution_id", "asset_id", name="pk_portfolio_trade"),
        sa.CheckConstraint("side IN ('buy', 'sell', 'none')", name="ck_portfolio_trade_side"),
        sa.CheckConstraint(
            "adjusted_execution_price > 0 AND pretrade_weight BETWEEN 0 AND 1 AND target_weight BETWEEN 0 AND 1",
            name="ck_portfolio_trade_numeric",
        ),
        sa.CheckConstraint(
            "absolute_weight_change = abs(signed_weight_change)", name="ck_portfolio_trade_absolute"
        ),
        sa.CheckConstraint(
            "(side = 'buy' AND signed_weight_change > 0) OR (side = 'sell' AND signed_weight_change < 0) OR (side = 'none' AND signed_weight_change = 0)",
            name="ck_portfolio_trade_direction",
        ),
        schema="experiment",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.validate_gross_path() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE target_row record; product_execution uuid; engine_key_value text;
        BEGIN
            PERFORM data.assert_artifact_draft(CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END);
            IF TG_OP <> 'DELETE' THEN
                SELECT data_bundle_version_id INTO target_row FROM strategy.portfolio_target_path
                WHERE portfolio_target_path_id = NEW.portfolio_target_path_id;
                IF target_row.data_bundle_version_id <> NEW.data_bundle_version_id THEN
                    RAISE EXCEPTION 'Gross path and target path data bundles mismatch';
                END IF;
                SELECT product.execution_policy_version_id INTO product_execution
                FROM strategy.model_strategy_target_path owner
                JOIN strategy.strategy_product_version product ON product.strategy_product_version_id = owner.strategy_product_version_id
                WHERE owner.portfolio_target_path_id = NEW.portfolio_target_path_id;
                IF product_execution IS NULL OR product_execution <> NEW.execution_policy_version_id THEN
                    RAISE EXCEPTION 'Gross path requires the target Strategy Product execution policy';
                END IF;
                SELECT definition.engine_key INTO engine_key_value
                FROM ops.engine_version version JOIN ops.engine_definition definition
                ON definition.engine_definition_id = version.engine_definition_id
                WHERE version.engine_version_id = NEW.engine_version_id;
                IF engine_key_value <> 'portfolio_accounting_engine' THEN
                    RAISE EXCEPTION 'Gross path requires a Portfolio Accounting engine';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END; $$;
        CREATE TRIGGER trg_gross_portfolio_path_draft
        BEFORE INSERT OR UPDATE OR DELETE ON experiment.gross_portfolio_path
        FOR EACH ROW EXECUTE FUNCTION experiment.validate_gross_path();

        CREATE FUNCTION experiment.enforce_gross_child_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE path_id uuid; owner_artifact uuid;
        BEGIN
            IF TG_TABLE_NAME = 'portfolio_trade' THEN
                SELECT gross_portfolio_path_id INTO path_id FROM experiment.portfolio_execution
                WHERE portfolio_execution_id = COALESCE(NEW.portfolio_execution_id, OLD.portfolio_execution_id);
            ELSE
                path_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.gross_portfolio_path_id ELSE NEW.gross_portfolio_path_id END;
            END IF;
            SELECT artifact_id INTO owner_artifact FROM experiment.gross_portfolio_path
            WHERE gross_portfolio_path_id = path_id;
            PERFORM data.assert_artifact_draft(owner_artifact);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END; $$;
        CREATE TRIGGER trg_gross_daily_nav_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.gross_daily_nav FOR EACH ROW EXECUTE FUNCTION experiment.enforce_gross_child_draft();
        CREATE TRIGGER trg_daily_asset_position_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.daily_asset_position FOR EACH ROW EXECUTE FUNCTION experiment.enforce_gross_child_draft();
        CREATE TRIGGER trg_daily_reserve_position_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.daily_reserve_position FOR EACH ROW EXECUTE FUNCTION experiment.enforce_gross_child_draft();
        CREATE TRIGGER trg_portfolio_execution_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.portfolio_execution FOR EACH ROW EXECUTE FUNCTION experiment.enforce_gross_child_draft();
        CREATE TRIGGER trg_portfolio_trade_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.portfolio_trade FOR EACH ROW EXECUTE FUNCTION experiment.enforce_gross_child_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS experiment.enforce_gross_child_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS experiment.validate_gross_path() CASCADE")
    op.drop_table("portfolio_trade", schema="experiment")
    op.drop_table("portfolio_execution", schema="experiment")
    op.drop_table("daily_reserve_position", schema="experiment")
    op.drop_table("daily_asset_position", schema="experiment")
    op.drop_table("gross_daily_nav", schema="experiment")
    op.drop_table("gross_portfolio_path", schema="experiment")
