"""Create phase 2 raw, clean, reserve, and quality data tables.

Revision ID: 20260731_0002
Revises: 20260731_0001
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0002"
down_revision: str | None = "20260731_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ASSET_ROWS = [
    {
        "asset_id": "00000000-0000-0000-0000-000000000101",
        "symbol": "IWF",
        "name": "iShares Russell 1000 Growth ETF",
        "role": "candidate",
    },
    {
        "asset_id": "00000000-0000-0000-0000-000000000102",
        "symbol": "IWD",
        "name": "iShares Russell 1000 Value ETF",
        "role": "candidate",
    },
    {
        "asset_id": "00000000-0000-0000-0000-000000000103",
        "symbol": "IWO",
        "name": "iShares Russell 2000 Growth ETF",
        "role": "candidate",
    },
    {
        "asset_id": "00000000-0000-0000-0000-000000000104",
        "symbol": "IWN",
        "name": "iShares Russell 2000 Value ETF",
        "role": "candidate",
    },
    {
        "asset_id": "00000000-0000-0000-0000-000000000105",
        "symbol": "SPY",
        "name": "SPDR S&P 500 ETF Trust",
        "role": "benchmark",
    },
]


def upgrade() -> None:
    op.add_column(
        "data_versions",
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
    )
    op.add_column("data_versions", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.add_column("data_versions", sa.Column("failure_message", sa.Text()))
    op.create_check_constraint(
        "valid_status",
        "data_versions",
        "status IN ('pending','published','failed')",
    )

    assets = op.create_table(
        "assets",
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), server_default="USD", nullable=False),
        sa.Column("exchange_calendar", sa.String(20), server_default="XNYS", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("asset_id", name="pk_assets"),
        sa.UniqueConstraint("symbol", name="uq_assets_symbol"),
        sa.CheckConstraint("role IN ('candidate','benchmark')", name="valid_role"),
    )
    op.bulk_insert(assets, ASSET_ROWS)

    op.create_table(
        "raw_market_prices",
        sa.Column("data_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open_raw", sa.Numeric(20, 8)),
        sa.Column("high_raw", sa.Numeric(20, 8)),
        sa.Column("low_raw", sa.Numeric(20, 8)),
        sa.Column("close_raw", sa.Numeric(20, 8)),
        sa.Column("adj_close", sa.Numeric(20, 8)),
        sa.Column("volume_raw", sa.BigInteger()),
        sa.Column("dividends", sa.Numeric(20, 8), nullable=False),
        sa.Column("stock_splits", sa.Numeric(20, 8), nullable=False),
        sa.Column("source_row_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_version_id"], ["data_versions.data_version_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.asset_id"]),
        sa.PrimaryKeyConstraint("data_version_id", "asset_id", "trade_date"),
        sa.CheckConstraint("volume_raw IS NULL OR volume_raw >= 0", name="nonnegative_volume"),
    )
    op.create_index(
        "ix_raw_market_prices_asset_date",
        "raw_market_prices",
        ["asset_id", "trade_date"],
    )

    op.create_table(
        "raw_rate_observations",
        sa.Column("data_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("series_id", sa.String(30), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("available_date", sa.Date(), nullable=False),
        sa.Column("annual_rate_percent", sa.Numeric(12, 8), nullable=False),
        sa.Column("source_row_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_version_id"], ["data_versions.data_version_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("data_version_id", "series_id", "observation_date"),
        sa.CheckConstraint(
            "available_date >= observation_date",
            name="available_not_before_observation",
        ),
    )

    op.create_table(
        "clean_market_prices",
        sa.Column("data_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleaning_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open_adj", sa.Numeric(20, 8), nullable=False),
        sa.Column("high_adj", sa.Numeric(20, 8), nullable=False),
        sa.Column("low_adj", sa.Numeric(20, 8), nullable=False),
        sa.Column("close_adj", sa.Numeric(20, 8), nullable=False),
        sa.Column("adj_factor", sa.Numeric(24, 12), nullable=False),
        sa.Column("volume_raw", sa.BigInteger(), nullable=False),
        sa.Column("dividends", sa.Numeric(20, 8), nullable=False),
        sa.Column("stock_splits", sa.Numeric(20, 8), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_version_id"], ["data_versions.data_version_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["cleaning_version_id"], ["cleaning_versions.cleaning_version_id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.asset_id"]),
        sa.PrimaryKeyConstraint("data_version_id", "cleaning_version_id", "asset_id", "trade_date"),
        sa.CheckConstraint("adj_factor > 0", name="positive_adjustment_factor"),
        sa.CheckConstraint("volume_raw >= 0", name="nonnegative_volume"),
    )
    op.create_index(
        "ix_clean_market_prices_asset_date",
        "clean_market_prices",
        ["asset_id", "trade_date"],
    )

    op.create_table(
        "reserve_daily_returns",
        sa.Column("data_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleaning_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("series_id", sa.String(30), nullable=False),
        sa.Column("source_observation_date", sa.Date(), nullable=False),
        sa.Column("source_available_date", sa.Date(), nullable=False),
        sa.Column("annual_rate_percent", sa.Numeric(12, 8), nullable=False),
        sa.Column("calendar_daily_factor", sa.Numeric(24, 16), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_version_id"], ["data_versions.data_version_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["cleaning_version_id"], ["cleaning_versions.cleaning_version_id"]),
        sa.PrimaryKeyConstraint("data_version_id", "cleaning_version_id", "nav_date"),
        sa.CheckConstraint("calendar_daily_factor > 0", name="positive_daily_factor"),
    )

    op.create_table(
        "clean_datasets",
        sa.Column("data_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleaning_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("common_market_start", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), server_default="published", nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["data_version_id"], ["data_versions.data_version_id"]),
        sa.ForeignKeyConstraint(["cleaning_version_id"], ["cleaning_versions.cleaning_version_id"]),
        sa.PrimaryKeyConstraint("data_version_id", "cleaning_version_id"),
        sa.UniqueConstraint("content_hash", name="uq_clean_datasets_content_hash"),
        sa.CheckConstraint("coverage_start <= coverage_end", name="coverage_dates_ordered"),
        sa.CheckConstraint(
            "common_market_start >= coverage_start",
            name="common_start_in_coverage",
        ),
        sa.CheckConstraint("status = 'published'", name="published_only"),
    )

    op.create_table(
        "data_quality_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleaning_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("rule_code", sa.String(100), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("series_id", sa.String(30)),
        sa.Column("event_date", sa.Date()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["data_version_id"], ["data_versions.data_version_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["cleaning_version_id"], ["cleaning_versions.cleaning_version_id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.asset_id"]),
        sa.PrimaryKeyConstraint("event_id"),
        sa.CheckConstraint("severity IN ('info','warning','error')", name="valid_severity"),
    )
    op.create_index(
        "ix_data_quality_version_severity",
        "data_quality_events",
        ["data_version_id", "severity"],
    )


def downgrade() -> None:
    op.drop_index("ix_data_quality_version_severity", table_name="data_quality_events")
    op.drop_table("data_quality_events")
    op.drop_table("clean_datasets")
    op.drop_table("reserve_daily_returns")
    op.drop_index("ix_clean_market_prices_asset_date", table_name="clean_market_prices")
    op.drop_table("clean_market_prices")
    op.drop_table("raw_rate_observations")
    op.drop_index("ix_raw_market_prices_asset_date", table_name="raw_market_prices")
    op.drop_table("raw_market_prices")
    op.drop_table("assets")
    op.drop_constraint(op.f("ck_data_versions_valid_status"), "data_versions", type_="check")
    op.drop_column("data_versions", "failure_message")
    op.drop_column("data_versions", "published_at")
    op.drop_column("data_versions", "status")
