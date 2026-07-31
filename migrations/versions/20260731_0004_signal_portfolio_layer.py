"""Create phase 4 signal datasets, rebalance events, and target positions.

Revision ID: 20260731_0004
Revises: 20260731_0003
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0004"
down_revision: str | None = "20260731_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "signal_datasets",
        sa.Column("data_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleaning_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factor_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("first_signal_date", sa.Date(), nullable=False),
        sa.Column("first_execution_date", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("event_count", sa.BigInteger(), nullable=False),
        sa.Column("position_count", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), server_default="published", nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _created_at(),
        sa.ForeignKeyConstraint(["data_version_id"], ["data_versions.data_version_id"]),
        sa.ForeignKeyConstraint(["cleaning_version_id"], ["cleaning_versions.cleaning_version_id"]),
        sa.ForeignKeyConstraint(["factor_version_id"], ["factor_versions.factor_version_id"]),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["strategy_versions.strategy_version_id"]),
        sa.PrimaryKeyConstraint(
            "data_version_id",
            "cleaning_version_id",
            "factor_version_id",
            "strategy_version_id",
            name="pk_signal_datasets",
        ),
        sa.CheckConstraint(
            "first_signal_date < first_execution_date", name="signal_before_execution"
        ),
        sa.CheckConstraint("first_execution_date <= coverage_end", name="execution_in_coverage"),
        sa.CheckConstraint("event_count > 0", name="positive_event_count"),
        sa.CheckConstraint("position_count = event_count * 4", name="four_positions_per_event"),
        sa.CheckConstraint("status = 'published'", name="published_only"),
    )
    op.create_table(
        "rebalance_events",
        sa.Column("rebalance_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleaning_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factor_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factor_variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rebalance_frequency", sa.String(20), nullable=False),
        sa.Column("strategy_template", sa.String(30), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("execution_date", sa.Date(), nullable=False),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        sa.Column("tie_flag", sa.Boolean(), nullable=False),
        sa.Column("reserve_target_weight", sa.Numeric(12, 10), nullable=False),
        sa.ForeignKeyConstraint(
            [
                "data_version_id",
                "cleaning_version_id",
                "factor_version_id",
                "strategy_version_id",
            ],
            [
                "signal_datasets.data_version_id",
                "signal_datasets.cleaning_version_id",
                "signal_datasets.factor_version_id",
                "signal_datasets.strategy_version_id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["factor_version_id", "factor_variant_id"],
            ["factor_variants.factor_version_id", "factor_variants.factor_variant_id"],
        ),
        sa.PrimaryKeyConstraint("rebalance_event_id", name="pk_rebalance_events"),
        sa.UniqueConstraint(
            "data_version_id",
            "cleaning_version_id",
            "factor_version_id",
            "strategy_version_id",
            "factor_variant_id",
            "rebalance_frequency",
            "strategy_template",
            "signal_date",
            name="uq_rebalance_event_identity",
        ),
        sa.CheckConstraint("signal_date < execution_date", name="signal_before_execution"),
        sa.CheckConstraint("eligible_count BETWEEN 0 AND 4", name="valid_eligible_count"),
        sa.CheckConstraint(
            "reserve_target_weight >= 0 AND reserve_target_weight <= 1",
            name="valid_reserve_weight",
        ),
        sa.CheckConstraint("rebalance_frequency IN ('weekly','monthly')", name="valid_frequency"),
        sa.CheckConstraint(
            "strategy_template IN ('cross_sectional','trend_filtered')",
            name="valid_template",
        ),
    )
    op.create_index(
        "ix_rebalance_events_variant_frequency_date",
        "rebalance_events",
        ["factor_variant_id", "rebalance_frequency", "signal_date"],
    )
    op.create_table(
        "target_positions",
        sa.Column("rebalance_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_factor_value", sa.Numeric(30, 14), nullable=False),
        sa.Column("oriented_factor_value", sa.Numeric(30, 14), nullable=False),
        sa.Column("rank", sa.Integer()),
        sa.Column("trend_eligible", sa.Boolean(), nullable=False),
        sa.Column("tie_flag", sa.Boolean(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("target_weight", sa.Numeric(12, 10), nullable=False),
        sa.ForeignKeyConstraint(
            ["rebalance_event_id"],
            ["rebalance_events.rebalance_event_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.asset_id"]),
        sa.PrimaryKeyConstraint("rebalance_event_id", "asset_id", name="pk_target_positions"),
        sa.CheckConstraint("rank IS NULL OR rank BETWEEN 1 AND 4", name="valid_rank"),
        sa.CheckConstraint("target_weight >= 0 AND target_weight <= 0.5", name="valid_weight"),
        sa.CheckConstraint(
            "(selected AND target_weight = 0.5) OR (NOT selected AND target_weight = 0)",
            name="selection_matches_weight",
        ),
    )
    op.create_index(
        "ix_target_positions_asset_event",
        "target_positions",
        ["asset_id", "rebalance_event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_target_positions_asset_event", table_name="target_positions")
    op.drop_table("target_positions")
    op.drop_index("ix_rebalance_events_variant_frequency_date", table_name="rebalance_events")
    op.drop_table("rebalance_events")
    op.drop_table("signal_datasets")
