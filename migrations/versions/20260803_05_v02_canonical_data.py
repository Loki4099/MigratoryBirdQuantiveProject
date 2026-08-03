"""Add immutable canonical datasets, typed values, coverage, and quality diagnostics.

Revision ID: 20260803_05_v02_canonical_data
Revises: 20260803_04_v02_data_contracts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_05_v02_canonical_data"
down_revision: str | None = "20260803_04_v02_data_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _id(name: str) -> sa.Column:
    return sa.Column(name, UUID, nullable=False)


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table}_{column}", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "dataset_publication",
        _id("dataset_publication_id"),
        _id("artifact_id"),
        _id("cleaning_version_id"),
        sa.Column("calendar_version_id", UUID),
        sa.Column("dataset_key", sa.String(140), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("dataset_kind", sa.String(30), nullable=False),
        sa.Column("value_kind", sa.String(40), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        _created(),
        _fk("dataset_publication", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "dataset_publication",
            "cleaning_version_id",
            "data.cleaning_version.cleaning_version_id",
        ),
        _fk(
            "dataset_publication",
            "calendar_version_id",
            "catalog.calendar_version.calendar_version_id",
        ),
        sa.PrimaryKeyConstraint("dataset_publication_id", name="pk_dataset_publication"),
        sa.UniqueConstraint("artifact_id", name="uq_dataset_publication_artifact_id"),
        sa.UniqueConstraint("dataset_key", "version_number", name="uq_dataset_key_version"),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_dataset_publication_version_number_positive"
        ),
        sa.CheckConstraint(
            "dataset_kind IN ('canonical', 'derived')",
            name="ck_dataset_publication_dataset_kind_allowed",
        ),
        sa.CheckConstraint(
            "value_kind IN ('daily_bar', 'rate_observation', 'reserve_return')",
            name="ck_dataset_publication_value_kind_allowed",
        ),
        sa.CheckConstraint(
            "coverage_start <= coverage_end", name="ck_dataset_publication_coverage_ordered"
        ),
        sa.CheckConstraint("row_count >= 1", name="ck_dataset_publication_row_count_positive"),
        schema="data",
    )
    op.create_table(
        "dataset_input",
        _id("dataset_input_id"),
        _id("dataset_publication_id"),
        _id("source_snapshot_id"),
        sa.Column("role", sa.String(80), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _fk(
            "dataset_input",
            "dataset_publication_id",
            "data.dataset_publication.dataset_publication_id",
        ),
        _fk("dataset_input", "source_snapshot_id", "data.source_snapshot.source_snapshot_id"),
        sa.PrimaryKeyConstraint("dataset_input_id", name="pk_dataset_input"),
        sa.UniqueConstraint(
            "dataset_publication_id", "role", "ordinal", name="uq_dataset_input_role"
        ),
        schema="data",
    )
    money = sa.Numeric(24, 10)
    op.create_table(
        "daily_bar",
        _id("dataset_publication_id"),
        _id("asset_id"),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("open_raw", money, nullable=False),
        sa.Column("high_raw", money, nullable=False),
        sa.Column("low_raw", money, nullable=False),
        sa.Column("close_raw", money, nullable=False),
        sa.Column("adj_close", money, nullable=False),
        sa.Column("open_adj", money, nullable=False),
        sa.Column("high_adj", money, nullable=False),
        sa.Column("low_adj", money, nullable=False),
        sa.Column("close_adj", money, nullable=False),
        sa.Column("adjustment_factor", sa.Numeric(24, 14), nullable=False),
        sa.Column("volume_raw", sa.BigInteger(), nullable=False),
        _fk(
            "daily_bar", "dataset_publication_id", "data.dataset_publication.dataset_publication_id"
        ),
        _fk("daily_bar", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint(
            "dataset_publication_id", "asset_id", "session_date", name="pk_daily_bar"
        ),
        sa.CheckConstraint(
            "LEAST(open_raw, high_raw, low_raw, close_raw, adj_close, "
            "open_adj, high_adj, low_adj, close_adj, adjustment_factor) > 0",
            name="ck_daily_bar_prices_positive",
        ),
        sa.CheckConstraint("volume_raw >= 0", name="ck_daily_bar_volume_nonnegative"),
        schema="data",
    )
    op.create_table(
        "corporate_action",
        _id("dataset_publication_id"),
        _id("asset_id"),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("cash_dividend", money, nullable=False),
        sa.Column("split_ratio", money, nullable=False),
        _fk(
            "corporate_action",
            "dataset_publication_id",
            "data.dataset_publication.dataset_publication_id",
        ),
        _fk("corporate_action", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint(
            "dataset_publication_id", "asset_id", "effective_date", name="pk_corporate_action"
        ),
        sa.CheckConstraint(
            "cash_dividend >= 0", name="ck_corporate_action_cash_dividend_nonnegative"
        ),
        sa.CheckConstraint("split_ratio >= 0", name="ck_corporate_action_split_ratio_nonnegative"),
        sa.CheckConstraint(
            "cash_dividend > 0 OR split_ratio > 0", name="ck_corporate_action_action_nonempty"
        ),
        schema="data",
    )
    op.create_table(
        "rate_observation",
        _id("dataset_publication_id"),
        sa.Column("series_key", sa.String(100), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("available_date", sa.Date(), nullable=False),
        sa.Column("annual_rate_percent", sa.Numeric(18, 8), nullable=False),
        _fk(
            "rate_observation",
            "dataset_publication_id",
            "data.dataset_publication.dataset_publication_id",
        ),
        sa.PrimaryKeyConstraint(
            "dataset_publication_id", "series_key", "observation_date", name="pk_rate_observation"
        ),
        schema="data",
    )
    op.create_table(
        "dataset_coverage",
        _id("dataset_coverage_id"),
        _id("dataset_publication_id"),
        sa.Column("asset_id", UUID),
        sa.Column("subject_key", sa.String(100), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("observation_count", sa.BigInteger(), nullable=False),
        sa.Column("missing_count", sa.BigInteger(), nullable=False),
        _fk(
            "dataset_coverage",
            "dataset_publication_id",
            "data.dataset_publication.dataset_publication_id",
        ),
        _fk("dataset_coverage", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint("dataset_coverage_id", name="pk_dataset_coverage"),
        sa.UniqueConstraint(
            "dataset_publication_id", "subject_key", name="uq_dataset_coverage_subject"
        ),
        sa.CheckConstraint(
            "coverage_start <= coverage_end", name="ck_dataset_coverage_coverage_ordered"
        ),
        sa.CheckConstraint(
            "observation_count >= 1", name="ck_dataset_coverage_observation_count_positive"
        ),
        sa.CheckConstraint(
            "missing_count >= 0", name="ck_dataset_coverage_missing_count_nonnegative"
        ),
        schema="data",
    )
    op.create_table(
        "quality_issue",
        _id("quality_issue_id"),
        _id("dataset_publication_id"),
        sa.Column("asset_id", UUID),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("rule_code", sa.String(100), nullable=False),
        sa.Column("event_date", sa.Date()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        _created(),
        _fk(
            "quality_issue",
            "dataset_publication_id",
            "data.dataset_publication.dataset_publication_id",
        ),
        _fk("quality_issue", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint("quality_issue_id", name="pk_quality_issue"),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')", name="ck_quality_issue_severity_allowed"
        ),
        schema="data",
    )
    op.create_index(
        "ix_quality_issue_dataset_severity",
        "quality_issue",
        ["dataset_publication_id", "severity"],
        schema="data",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION data.enforce_dataset_child_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE publication_id uuid; owner_artifact_id uuid;
        BEGIN
            publication_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.dataset_publication_id ELSE NEW.dataset_publication_id END;
            SELECT artifact_id INTO owner_artifact_id FROM data.dataset_publication
            WHERE dataset_publication_id = publication_id;
            PERFORM data.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_dataset_publication_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.dataset_publication
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_dataset_input_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.dataset_input
        FOR EACH ROW EXECUTE FUNCTION data.enforce_dataset_child_draft();
        CREATE TRIGGER trg_daily_bar_draft BEFORE INSERT OR UPDATE OR DELETE ON data.daily_bar
        FOR EACH ROW EXECUTE FUNCTION data.enforce_dataset_child_draft();
        CREATE TRIGGER trg_corporate_action_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.corporate_action
        FOR EACH ROW EXECUTE FUNCTION data.enforce_dataset_child_draft();
        CREATE TRIGGER trg_rate_observation_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.rate_observation
        FOR EACH ROW EXECUTE FUNCTION data.enforce_dataset_child_draft();
        CREATE TRIGGER trg_dataset_coverage_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.dataset_coverage
        FOR EACH ROW EXECUTE FUNCTION data.enforce_dataset_child_draft();
        CREATE TRIGGER trg_quality_issue_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.quality_issue
        FOR EACH ROW EXECUTE FUNCTION data.enforce_dataset_child_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS data.enforce_dataset_child_draft() CASCADE")
    op.drop_index("ix_quality_issue_dataset_severity", table_name="quality_issue", schema="data")
    op.drop_table("quality_issue", schema="data")
    op.drop_table("dataset_coverage", schema="data")
    op.drop_table("rate_observation", schema="data")
    op.drop_table("corporate_action", schema="data")
    op.drop_table("daily_bar", schema="data")
    op.drop_table("dataset_input", schema="data")
    op.drop_table("dataset_publication", schema="data")
