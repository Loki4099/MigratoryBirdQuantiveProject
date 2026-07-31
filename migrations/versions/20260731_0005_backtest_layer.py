"""Create phase 5 executions, trades, positions, NAV, and benchmark tables.

Revision ID: 20260731_0005
Revises: 20260731_0004
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0005"
down_revision: str | None = "20260731_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rebalance_executions",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_date", sa.Date(), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("turnover", sa.Numeric(20, 14), nullable=False),
        sa.Column("transaction_cost_fraction", sa.Numeric(20, 14), nullable=False),
        sa.Column("transaction_cost_amount", sa.Numeric(30, 14), nullable=False),
        sa.Column("gross_pretrade_nav", sa.Numeric(30, 14), nullable=False),
        sa.Column("net_pretrade_nav", sa.Numeric(30, 14), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["backtest_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "execution_date", name="pk_rebalance_executions"),
        sa.CheckConstraint("signal_date < execution_date", name="signal_before_execution"),
        sa.CheckConstraint("turnover >= 0", name="nonnegative_turnover"),
        sa.CheckConstraint("transaction_cost_fraction >= 0", name="nonnegative_cost_fraction"),
    )
    op.create_table(
        "trades",
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_date", sa.Date(), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("execution_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("pretrade_weight", sa.Numeric(20, 14), nullable=False),
        sa.Column("target_weight", sa.Numeric(20, 14), nullable=False),
        sa.Column("weight_change", sa.Numeric(20, 14), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["backtest_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.asset_id"]),
        sa.PrimaryKeyConstraint("trade_id", name="pk_trades"),
        sa.UniqueConstraint("run_id", "execution_date", "asset_id", name="uq_trade_run_date_asset"),
        sa.CheckConstraint("side IN ('buy','sell')", name="valid_side"),
        sa.CheckConstraint("execution_price > 0", name="positive_execution_price"),
    )
    op.create_index("ix_trades_run_date", "trades", ["run_id", "execution_date"])
    op.create_table(
        "daily_positions",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("sleeve", sa.String(20), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("close_weight", sa.Numeric(20, 14), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["backtest_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.asset_id"]),
        sa.PrimaryKeyConstraint("run_id", "nav_date", "sleeve", name="pk_daily_positions"),
        sa.CheckConstraint("close_weight >= 0 AND close_weight <= 1", name="valid_weight"),
        sa.CheckConstraint(
            "(sleeve = 'RESERVE' AND asset_id IS NULL) OR "
            "(sleeve <> 'RESERVE' AND asset_id IS NOT NULL)",
            name="valid_sleeve_asset",
        ),
    )
    op.create_index("ix_daily_positions_run_date", "daily_positions", ["run_id", "nav_date"])
    op.create_table(
        "daily_nav",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("gross_daily_return", sa.Numeric(30, 14), nullable=False),
        sa.Column("net_daily_return", sa.Numeric(30, 14), nullable=False),
        sa.Column("gross_nav", sa.Numeric(30, 14), nullable=False),
        sa.Column("net_nav", sa.Numeric(30, 14), nullable=False),
        sa.Column("turnover", sa.Numeric(20, 14), nullable=False),
        sa.Column("transaction_cost_fraction", sa.Numeric(20, 14), nullable=False),
        sa.Column("transaction_cost_amount", sa.Numeric(30, 14), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["backtest_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "nav_date", name="pk_daily_nav"),
        sa.CheckConstraint("gross_nav > 0 AND net_nav > 0", name="positive_nav"),
        sa.CheckConstraint("turnover >= 0", name="nonnegative_turnover"),
    )
    op.create_index("ix_daily_nav_date", "daily_nav", ["nav_date"])
    op.create_table(
        "benchmark_daily_nav",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("benchmark_type", sa.String(30), nullable=False),
        sa.Column("gross_daily_return", sa.Numeric(30, 14), nullable=False),
        sa.Column("net_daily_return", sa.Numeric(30, 14), nullable=False),
        sa.Column("gross_nav", sa.Numeric(30, 14), nullable=False),
        sa.Column("net_nav", sa.Numeric(30, 14), nullable=False),
        sa.Column("turnover", sa.Numeric(20, 14), nullable=False),
        sa.Column("transaction_cost_fraction", sa.Numeric(20, 14), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["backtest_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(
            "run_id", "nav_date", "benchmark_type", name="pk_benchmark_daily_nav"
        ),
        sa.CheckConstraint(
            "benchmark_type IN ('four_etf_equal_weight','spy_buy_hold')",
            name="valid_benchmark_type",
        ),
        sa.CheckConstraint("gross_nav > 0 AND net_nav > 0", name="positive_nav"),
    )
    op.create_index("ix_benchmark_daily_nav_date", "benchmark_daily_nav", ["nav_date"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_daily_nav_date", table_name="benchmark_daily_nav")
    op.drop_table("benchmark_daily_nav")
    op.drop_index("ix_daily_nav_date", table_name="daily_nav")
    op.drop_table("daily_nav")
    op.drop_index("ix_daily_positions_run_date", table_name="daily_positions")
    op.drop_table("daily_positions")
    op.drop_index("ix_trades_run_date", table_name="trades")
    op.drop_table("trades")
    op.drop_table("rebalance_executions")
