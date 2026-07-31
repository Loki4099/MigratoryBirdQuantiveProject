"""Create phase 3 factor registry, values, and dataset tables.

Revision ID: 20260731_0003
Revises: 20260731_0002
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0003"
down_revision: str | None = "20260731_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "factor_definitions",
        sa.Column("factor_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factor_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_key", sa.String(100), nullable=False),
        sa.Column("family", sa.String(50), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("required_fields", postgresql.JSONB(), nullable=False),
        sa.Column("direction", sa.String(30), nullable=False),
        sa.Column("implementation_key", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["factor_version_id"], ["factor_versions.factor_version_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("factor_definition_id", name="pk_factor_definitions"),
        sa.UniqueConstraint(
            "factor_version_id",
            "definition_key",
            name="uq_factor_definition_version_key",
        ),
        sa.CheckConstraint(
            "direction IN ('higher_is_better','lower_is_better')",
            name="valid_direction",
        ),
    )
    op.create_table(
        "factor_variants",
        sa.Column("factor_variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factor_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factor_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_key", sa.String(150), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("minimum_observations", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["factor_version_id"], ["factor_versions.factor_version_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["factor_definition_id"],
            ["factor_definitions.factor_definition_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("factor_variant_id", name="pk_factor_variants"),
        sa.UniqueConstraint(
            "factor_version_id", "variant_key", name="uq_factor_variant_version_key"
        ),
        sa.UniqueConstraint(
            "factor_version_id",
            "factor_variant_id",
            name="uq_factor_variant_version_identity",
        ),
        sa.CheckConstraint("minimum_observations > 0", name="positive_minimum_observations"),
    )
    op.create_table(
        "factor_values",
        sa.Column("data_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleaning_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factor_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factor_variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("raw_value", sa.Numeric(30, 14), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_version_id", "cleaning_version_id", "asset_id", "trade_date"],
            [
                "clean_market_prices.data_version_id",
                "clean_market_prices.cleaning_version_id",
                "clean_market_prices.asset_id",
                "clean_market_prices.trade_date",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["factor_version_id", "factor_variant_id"],
            ["factor_variants.factor_version_id", "factor_variants.factor_variant_id"],
        ),
        sa.PrimaryKeyConstraint(
            "data_version_id",
            "cleaning_version_id",
            "factor_version_id",
            "factor_variant_id",
            "asset_id",
            "trade_date",
            name="pk_factor_values",
        ),
    )
    op.create_index(
        "ix_factor_values_variant_asset_date",
        "factor_values",
        ["factor_variant_id", "asset_id", "trade_date"],
    )
    op.create_table(
        "factor_datasets",
        sa.Column("data_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleaning_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factor_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("common_valid_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
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
        sa.ForeignKeyConstraint(["factor_version_id"], ["factor_versions.factor_version_id"]),
        sa.PrimaryKeyConstraint(
            "data_version_id",
            "cleaning_version_id",
            "factor_version_id",
            name="pk_factor_datasets",
        ),
        sa.CheckConstraint("common_valid_start <= coverage_end", name="valid_coverage"),
        sa.CheckConstraint("row_count > 0", name="positive_row_count"),
        sa.CheckConstraint("status = 'published'", name="published_only"),
    )


def downgrade() -> None:
    op.drop_table("factor_datasets")
    op.drop_index("ix_factor_values_variant_asset_date", table_name="factor_values")
    op.drop_table("factor_values")
    op.drop_table("factor_variants")
    op.drop_table("factor_definitions")
