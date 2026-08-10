# ruff: noqa: E501
"""Add the versioned v0.21 asset registry and terminal-event contract.

Revision ID: 20260805_31_v021_assets
Revises: 20260805_30_v021_product
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260805_31_v021_assets"
down_revision: str | None = "20260805_30_v021_product"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


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
        "asset_registry_release",
        _id("asset_registry_release_id"),
        _id("artifact_id"),
        sa.Column("release_key", sa.String(180), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("catalog_version", sa.String(32), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_asset_registry_artifact"),
        sa.PrimaryKeyConstraint("asset_registry_release_id", name="pk_asset_registry_release"),
        sa.UniqueConstraint("artifact_id", name="uq_asset_registry_artifact"),
        sa.UniqueConstraint("release_key", "version_number", name="uq_asset_registry_version"),
        sa.CheckConstraint("version_number >= 1", name="ck_asset_registry_version"),
        schema="catalog",
    )
    op.create_table(
        "asset_category",
        _id("asset_category_id"),
        _id("asset_registry_release_id"),
        sa.Column("category_key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _created(),
        _fk(
            ["asset_registry_release_id"],
            ["catalog.asset_registry_release.asset_registry_release_id"],
            "fk_asset_category_release",
        ),
        sa.PrimaryKeyConstraint("asset_category_id", name="pk_asset_category"),
        sa.UniqueConstraint(
            "asset_registry_release_id", "category_key", name="uq_asset_category_key"
        ),
        sa.UniqueConstraint(
            "asset_registry_release_id", "ordinal", name="uq_asset_category_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_asset_category_ordinal"),
        schema="catalog",
    )
    op.create_table(
        "security_profile",
        _id("security_profile_id"),
        _id("asset_registry_release_id"),
        _id("security_id"),
        _id("asset_category_id"),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("aliases", JSONB, nullable=False),
        sa.Column("asset_class", sa.String(80), nullable=False),
        sa.Column("instrument_type", sa.String(80), nullable=False),
        sa.Column("tradability", sa.String(24), nullable=False),
        sa.Column("venue_mic", sa.String(4)),
        sa.Column("calendar_key", sa.String(80)),
        sa.Column("tags", JSONB, nullable=False),
        sa.Column("maturity", sa.String(32), nullable=False),
        sa.Column("target_maturity", sa.String(32), nullable=False),
        sa.Column("missing_requirements", JSONB, nullable=False),
        sa.Column("search_document", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _created(),
        _fk(
            ["asset_registry_release_id"],
            ["catalog.asset_registry_release.asset_registry_release_id"],
            "fk_security_profile_release",
        ),
        _fk(["security_id"], ["catalog.security.security_id"], "fk_security_profile_security"),
        _fk(
            ["asset_category_id"],
            ["catalog.asset_category.asset_category_id"],
            "fk_security_profile_category",
        ),
        sa.PrimaryKeyConstraint("security_profile_id", name="pk_security_profile"),
        sa.UniqueConstraint(
            "asset_registry_release_id", "security_id", name="uq_security_profile_security"
        ),
        sa.UniqueConstraint(
            "asset_registry_release_id", "symbol", name="uq_security_profile_symbol"
        ),
        sa.UniqueConstraint(
            "asset_registry_release_id", "ordinal", name="uq_security_profile_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_security_profile_ordinal"),
        sa.CheckConstraint(
            "tradability IN ('tradable','reference_only','synthetic')",
            name="ck_security_profile_tradability",
        ),
        sa.CheckConstraint(
            "maturity IN ('cataloged','reference_data','canonical_ready','research_ready','strategy_ready','product_eligible_input')",
            name="ck_security_profile_maturity",
        ),
        sa.CheckConstraint(
            "target_maturity IN ('cataloged','reference_data','canonical_ready','research_ready','strategy_ready','product_eligible_input')",
            name="ck_security_profile_target_maturity",
        ),
        schema="catalog",
    )
    op.create_index(
        "ix_security_profile_filters",
        "security_profile",
        ["asset_registry_release_id", "asset_category_id", "maturity", "tradability"],
        schema="catalog",
    )
    op.create_table(
        "asset_set_definition",
        _id("asset_set_definition_id"),
        _id("asset_registry_release_id"),
        sa.Column("set_key", sa.String(180), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("set_type", sa.String(32), nullable=False),
        sa.Column("maturity", sa.String(32), nullable=False),
        sa.Column("formal_eligible", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        _created(),
        _fk(
            ["asset_registry_release_id"],
            ["catalog.asset_registry_release.asset_registry_release_id"],
            "fk_asset_set_release",
        ),
        sa.PrimaryKeyConstraint("asset_set_definition_id", name="pk_asset_set_definition"),
        sa.UniqueConstraint("asset_registry_release_id", "set_key", name="uq_asset_set_key"),
        sa.CheckConstraint(
            "set_type IN ('fixed','dynamic_methodology','defensive_basket')",
            name="ck_asset_set_type",
        ),
        schema="catalog",
    )
    op.create_table(
        "asset_set_member",
        _id("asset_set_definition_id"),
        _id("security_id"),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _created(),
        _fk(
            ["asset_set_definition_id"],
            ["catalog.asset_set_definition.asset_set_definition_id"],
            "fk_asset_set_member_set",
        ),
        _fk(["security_id"], ["catalog.security.security_id"], "fk_asset_set_member_security"),
        sa.PrimaryKeyConstraint(
            "asset_set_definition_id", "security_id", name="pk_asset_set_member"
        ),
        sa.UniqueConstraint(
            "asset_set_definition_id", "ordinal", name="uq_asset_set_member_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_asset_set_member_ordinal"),
        schema="catalog",
    )
    op.create_table(
        "security_terminal_event",
        _id("security_terminal_event_id"),
        _id("artifact_id"),
        _id("security_id"),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("effective_session", sa.Date(), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_total_return", sa.Numeric(24, 12)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("details", JSONB, nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_terminal_event_artifact"),
        _fk(["security_id"], ["catalog.security.security_id"], "fk_terminal_event_security"),
        sa.PrimaryKeyConstraint("security_terminal_event_id", name="pk_security_terminal_event"),
        sa.UniqueConstraint("artifact_id", name="uq_security_terminal_event_artifact"),
        sa.CheckConstraint("known_at::date <= effective_session", name="ck_terminal_event_known"),
        sa.CheckConstraint(
            "status IN ('confirmed','estimated','unresolved')", name="ck_terminal_event_status"
        ),
        schema="catalog",
    )


def downgrade() -> None:
    op.drop_table("security_terminal_event", schema="catalog")
    op.drop_table("asset_set_member", schema="catalog")
    op.drop_table("asset_set_definition", schema="catalog")
    op.drop_index("ix_security_profile_filters", table_name="security_profile", schema="catalog")
    op.drop_table("security_profile", schema="catalog")
    op.drop_table("asset_category", schema="catalog")
    op.drop_table("asset_registry_release", schema="catalog")
