"""Add immutable factor-layer diagnostics.

Revision ID: 20260803_09_v02_factor_diag
Revises: 20260803_08_v02_factor_engine
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_09_v02_factor_diag"
down_revision: str | None = "20260803_08_v02_factor_engine"
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
        [column],
        [target],
        name=f"fk_{table[:16]}_{column[:24]}",
        ondelete="RESTRICT",
    )


def _finite(column: str) -> str:
    return (
        f"{column} <> 'NaN'::double precision AND "
        f"{column} <> 'Infinity'::double precision AND "
        f"{column} <> '-Infinity'::double precision"
    )


def upgrade() -> None:
    op.create_table(
        "factor_diagnostic_set",
        _id("factor_diagnostic_set_id"),
        _id("artifact_id"),
        _id("factor_catalog_artifact_id"),
        _id("universe_version_id"),
        _id("data_bundle_version_id"),
        _id("eligibility_snapshot_id"),
        _id("factor_engine_version_id"),
        _id("diagnostic_engine_version_id"),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("dataset_count", sa.Integer(), nullable=False),
        sa.Column("asset_count", sa.Integer(), nullable=False),
        sa.Column("observation_count", sa.BigInteger(), nullable=False),
        sa.Column("pair_count", sa.Integer(), nullable=False),
        sa.Column("high_correlation_threshold", sa.Double(), nullable=False),
        _created(),
        _fk("factor_diagnostic_set", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "factor_diagnostic_set",
            "factor_catalog_artifact_id",
            "lineage.artifact.artifact_id",
        ),
        _fk(
            "factor_diagnostic_set",
            "universe_version_id",
            "catalog.universe_version.universe_version_id",
        ),
        _fk(
            "factor_diagnostic_set",
            "data_bundle_version_id",
            "data.data_bundle_version.data_bundle_version_id",
        ),
        _fk(
            "factor_diagnostic_set",
            "eligibility_snapshot_id",
            "catalog.eligibility_snapshot.eligibility_snapshot_id",
        ),
        _fk(
            "factor_diagnostic_set",
            "factor_engine_version_id",
            "ops.engine_version.engine_version_id",
        ),
        _fk(
            "factor_diagnostic_set",
            "diagnostic_engine_version_id",
            "ops.engine_version.engine_version_id",
        ),
        sa.PrimaryKeyConstraint("factor_diagnostic_set_id", name="pk_factor_diagnostic_set"),
        sa.UniqueConstraint("artifact_id", name="uq_factor_diagnostic_set_artifact_id"),
        sa.UniqueConstraint(
            "factor_catalog_artifact_id",
            "universe_version_id",
            "data_bundle_version_id",
            "eligibility_snapshot_id",
            "factor_engine_version_id",
            "diagnostic_engine_version_id",
            name="uq_factor_diagnostic_set_exact_context",
        ),
        sa.CheckConstraint(
            "coverage_start <= coverage_end", name="ck_factor_diagnostic_set_coverage_ordered"
        ),
        sa.CheckConstraint(
            "dataset_count >= 1 AND asset_count >= 1 AND observation_count >= 1",
            name="ck_factor_diagnostic_set_counts_positive",
        ),
        sa.CheckConstraint("pair_count >= 0", name="ck_factor_diagnostic_set_pair_count"),
        sa.CheckConstraint(
            "high_correlation_threshold > 0 AND high_correlation_threshold <= 1 AND "
            + _finite("high_correlation_threshold"),
            name="ck_factor_diagnostic_set_threshold",
        ),
        schema="factor",
    )
    op.create_table(
        "factor_dataset_summary",
        _id("factor_dataset_summary_id"),
        _id("factor_diagnostic_set_id"),
        _id("factor_dataset_id"),
        sa.Column("observation_count", sa.BigInteger(), nullable=False),
        sa.Column("asset_count", sa.Integer(), nullable=False),
        sa.Column("missing_count", sa.BigInteger(), nullable=False),
        sa.Column("mean", sa.Double(), nullable=False),
        sa.Column("standard_deviation", sa.Double(), nullable=False),
        sa.Column("minimum", sa.Double(), nullable=False),
        sa.Column("p05", sa.Double(), nullable=False),
        sa.Column("p25", sa.Double(), nullable=False),
        sa.Column("median", sa.Double(), nullable=False),
        sa.Column("p75", sa.Double(), nullable=False),
        sa.Column("p95", sa.Double(), nullable=False),
        sa.Column("maximum", sa.Double(), nullable=False),
        sa.Column("zero_variance", sa.Boolean(), nullable=False),
        _created(),
        _fk(
            "factor_dataset_summary",
            "factor_diagnostic_set_id",
            "factor.factor_diagnostic_set.factor_diagnostic_set_id",
        ),
        _fk(
            "factor_dataset_summary",
            "factor_dataset_id",
            "factor.factor_dataset.factor_dataset_id",
        ),
        sa.PrimaryKeyConstraint("factor_dataset_summary_id", name="pk_factor_dataset_summary"),
        sa.UniqueConstraint(
            "factor_diagnostic_set_id",
            "factor_dataset_id",
            name="uq_factor_dataset_summary_set_dataset",
        ),
        sa.CheckConstraint(
            "observation_count >= 1 AND asset_count >= 1 AND missing_count >= 0",
            name="ck_factor_dataset_summary_counts",
        ),
        sa.CheckConstraint(
            "standard_deviation >= 0 AND minimum <= p05 AND p05 <= p25 AND "
            "p25 <= median AND median <= p75 AND p75 <= p95 AND p95 <= maximum",
            name="ck_factor_dataset_summary_distribution_ordered",
        ),
        *[
            sa.CheckConstraint(_finite(column), name=f"ck_factor_dataset_summary_{column}_finite")
            for column in (
                "mean",
                "standard_deviation",
                "minimum",
                "p05",
                "p25",
                "median",
                "p75",
                "p95",
                "maximum",
            )
        ],
        schema="factor",
    )
    op.create_table(
        "factor_pair_correlation",
        _id("factor_pair_correlation_id"),
        _id("factor_diagnostic_set_id"),
        _id("left_factor_dataset_id"),
        _id("right_factor_dataset_id"),
        sa.Column("observation_count", sa.BigInteger(), nullable=False),
        sa.Column("spearman_correlation", sa.Double()),
        sa.Column("same_definition", sa.Boolean(), nullable=False),
        sa.Column("high_correlation", sa.Boolean(), nullable=False),
        _created(),
        _fk(
            "factor_pair_correlation",
            "factor_diagnostic_set_id",
            "factor.factor_diagnostic_set.factor_diagnostic_set_id",
        ),
        _fk(
            "factor_pair_correlation",
            "left_factor_dataset_id",
            "factor.factor_dataset.factor_dataset_id",
        ),
        _fk(
            "factor_pair_correlation",
            "right_factor_dataset_id",
            "factor.factor_dataset.factor_dataset_id",
        ),
        sa.PrimaryKeyConstraint("factor_pair_correlation_id", name="pk_factor_pair_correlation"),
        sa.UniqueConstraint(
            "factor_diagnostic_set_id",
            "left_factor_dataset_id",
            "right_factor_dataset_id",
            name="uq_factor_pair_correlation_set_pair",
        ),
        sa.CheckConstraint(
            "left_factor_dataset_id <> right_factor_dataset_id",
            name="ck_factor_pair_correlation_distinct",
        ),
        sa.CheckConstraint(
            "observation_count >= 2", name="ck_factor_pair_correlation_observations"
        ),
        sa.CheckConstraint(
            "spearman_correlation IS NULL OR (spearman_correlation BETWEEN -1 AND 1 AND "
            + _finite("spearman_correlation")
            + ")",
            name="ck_factor_pair_correlation_range",
        ),
        schema="factor",
    )
    op.create_index(
        "ix_factor_pair_correlation_high",
        "factor_pair_correlation",
        ["factor_diagnostic_set_id", "high_correlation"],
        schema="factor",
    )
    op.create_table(
        "factor_diagnostic_issue",
        _id("factor_diagnostic_issue_id"),
        _id("factor_diagnostic_set_id"),
        _id("factor_dataset_id"),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("issue_code", sa.String(120), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        _created(),
        _fk(
            "factor_diagnostic_issue",
            "factor_diagnostic_set_id",
            "factor.factor_diagnostic_set.factor_diagnostic_set_id",
        ),
        _fk(
            "factor_diagnostic_issue",
            "factor_dataset_id",
            "factor.factor_dataset.factor_dataset_id",
        ),
        sa.PrimaryKeyConstraint("factor_diagnostic_issue_id", name="pk_factor_diagnostic_issue"),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')",
            name="ck_factor_diagnostic_issue_severity",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'", name="ck_factor_diagnostic_issue_details_object"
        ),
        schema="factor",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_factor_diagnostic_set_draft
        BEFORE INSERT OR UPDATE OR DELETE ON factor.factor_diagnostic_set
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();

        CREATE FUNCTION factor.enforce_diagnostic_child_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE set_id uuid; owner_artifact_id uuid;
        BEGIN
            set_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.factor_diagnostic_set_id ELSE NEW.factor_diagnostic_set_id END;
            SELECT artifact_id INTO owner_artifact_id FROM factor.factor_diagnostic_set
            WHERE factor_diagnostic_set_id = set_id;
            PERFORM data.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_factor_dataset_summary_draft
        BEFORE INSERT OR UPDATE OR DELETE ON factor.factor_dataset_summary
        FOR EACH ROW EXECUTE FUNCTION factor.enforce_diagnostic_child_draft();
        CREATE TRIGGER trg_factor_pair_correlation_draft
        BEFORE INSERT OR UPDATE OR DELETE ON factor.factor_pair_correlation
        FOR EACH ROW EXECUTE FUNCTION factor.enforce_diagnostic_child_draft();
        CREATE TRIGGER trg_factor_diagnostic_issue_draft
        BEFORE INSERT OR UPDATE OR DELETE ON factor.factor_diagnostic_issue
        FOR EACH ROW EXECUTE FUNCTION factor.enforce_diagnostic_child_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS factor.enforce_diagnostic_child_draft() CASCADE")
    op.drop_table("factor_diagnostic_issue", schema="factor")
    op.drop_index(
        "ix_factor_pair_correlation_high",
        table_name="factor_pair_correlation",
        schema="factor",
    )
    op.drop_table("factor_pair_correlation", schema="factor")
    op.drop_table("factor_dataset_summary", schema="factor")
    op.drop_table("factor_diagnostic_set", schema="factor")
