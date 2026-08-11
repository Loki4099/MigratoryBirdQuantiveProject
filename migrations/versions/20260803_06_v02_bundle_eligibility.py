"""Add reserve accrual, data bundles, and universe eligibility.

Revision ID: 20260803_06_v02_bundle
Revises: 20260803_05_v02_canonical_data
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_06_v02_bundle"
down_revision: str | None = "20260803_05_v02_canonical_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _id(name: str) -> sa.Column:
    return sa.Column(name, UUID, nullable=False)


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _fk(
    table: str, column: str, target: str, *, name: str | None = None
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=name or f"fk_{table}_{column}", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.alter_column("dataset_publication", "cleaning_version_id", nullable=True, schema="data")
    op.alter_column("dataset_input", "source_snapshot_id", nullable=True, schema="data")
    op.add_column(
        "dataset_input",
        sa.Column("upstream_dataset_publication_id", UUID),
        schema="data",
    )
    op.create_foreign_key(
        "fk_dataset_input_upstream_dataset_publication_id",
        "dataset_input",
        "dataset_publication",
        ["upstream_dataset_publication_id"],
        ["dataset_publication_id"],
        source_schema="data",
        referent_schema="data",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_dataset_input_exactly_one_input",
        "dataset_input",
        "num_nonnulls(source_snapshot_id, upstream_dataset_publication_id) = 1",
        schema="data",
    )
    op.create_table(
        "reserve_return_model_definition",
        _id("reserve_return_model_definition_id"),
        _id("artifact_id"),
        sa.Column("model_key", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        _created(),
        _fk("reserve_return_model_definition", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint(
            "reserve_return_model_definition_id", name="pk_reserve_return_model_definition"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_reserve_return_model_definition_artifact_id"),
        sa.UniqueConstraint("model_key", name="uq_reserve_return_model_definition_model_key"),
        schema="experiment",
    )
    op.create_table(
        "reserve_return_model_version",
        _id("reserve_return_model_version_id"),
        _id("reserve_return_model_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("accrual_method", sa.String(80), nullable=False),
        sa.Column("day_count_basis", sa.String(40), nullable=False),
        sa.Column("warning_after_days", sa.Integer(), nullable=False),
        sa.Column("error_after_days", sa.Integer(), nullable=False),
        _created(),
        _fk(
            "reserve_return_model_version",
            "reserve_return_model_definition_id",
            "experiment.reserve_return_model_definition.reserve_return_model_definition_id",
            name="fk_reserve_model_version_definition",
        ),
        _fk("reserve_return_model_version", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint(
            "reserve_return_model_version_id", name="pk_reserve_return_model_version"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_reserve_return_model_version_artifact_id"),
        sa.UniqueConstraint(
            "reserve_return_model_definition_id",
            "version_number",
            name="uq_reserve_return_model_version",
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_reserve_return_model_version_version_number_positive",
        ),
        sa.CheckConstraint(
            "warning_after_days >= 0",
            name="ck_reserve_return_model_version_warning_days_nonnegative",
        ),
        sa.CheckConstraint(
            "error_after_days >= warning_after_days",
            name="ck_reserve_return_model_version_staleness_ordered",
        ),
        schema="experiment",
    )
    op.create_table(
        "reserve_return",
        _id("dataset_publication_id"),
        sa.Column("interval_start", sa.Date(), nullable=False),
        sa.Column("interval_end", sa.Date(), nullable=False),
        sa.Column("source_observation_date", sa.Date(), nullable=False),
        sa.Column("source_available_date", sa.Date(), nullable=False),
        sa.Column("annual_rate_percent", sa.Numeric(18, 8), nullable=False),
        sa.Column("calendar_days", sa.Integer(), nullable=False),
        sa.Column("accrual_factor", sa.Numeric(24, 14), nullable=False),
        sa.Column("staleness_days", sa.Integer(), nullable=False),
        sa.Column("quality_status", sa.String(20), nullable=False),
        _fk(
            "reserve_return",
            "dataset_publication_id",
            "data.dataset_publication.dataset_publication_id",
        ),
        sa.PrimaryKeyConstraint(
            "dataset_publication_id", "interval_start", name="pk_reserve_return"
        ),
        sa.CheckConstraint(
            "interval_start < interval_end", name="ck_reserve_return_interval_ordered"
        ),
        sa.CheckConstraint("calendar_days >= 1", name="ck_reserve_return_calendar_days_positive"),
        sa.CheckConstraint("accrual_factor > 0", name="ck_reserve_return_accrual_factor_positive"),
        sa.CheckConstraint("staleness_days >= 0", name="ck_reserve_return_staleness_nonnegative"),
        sa.CheckConstraint(
            "quality_status IN ('normal', 'warning')",
            name="ck_reserve_return_quality_status_allowed",
        ),
        schema="data",
    )
    op.create_table(
        "data_bundle_definition",
        _id("data_bundle_definition_id"),
        _id("artifact_id"),
        sa.Column("bundle_key", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        _created(),
        _fk("data_bundle_definition", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("data_bundle_definition_id", name="pk_data_bundle_definition"),
        sa.UniqueConstraint("artifact_id", name="uq_data_bundle_definition_artifact_id"),
        sa.UniqueConstraint("bundle_key", name="uq_data_bundle_definition_bundle_key"),
        schema="data",
    )
    op.create_table(
        "data_bundle_version",
        _id("data_bundle_version_id"),
        _id("data_bundle_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        _created(),
        _fk(
            "data_bundle_version",
            "data_bundle_definition_id",
            "data.data_bundle_definition.data_bundle_definition_id",
        ),
        _fk("data_bundle_version", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("data_bundle_version_id", name="pk_data_bundle_version"),
        sa.UniqueConstraint("artifact_id", name="uq_data_bundle_version_artifact_id"),
        sa.UniqueConstraint(
            "data_bundle_definition_id", "version_number", name="uq_data_bundle_version"
        ),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_data_bundle_version_version_number_positive"
        ),
        sa.CheckConstraint(
            "member_count >= 1", name="ck_data_bundle_version_member_count_positive"
        ),
        sa.CheckConstraint(
            "coverage_start <= coverage_end", name="ck_data_bundle_version_coverage_ordered"
        ),
        schema="data",
    )
    op.create_table(
        "data_bundle_member",
        _id("data_bundle_member_id"),
        _id("data_bundle_version_id"),
        sa.Column("dataset_publication_id", UUID),
        sa.Column("calendar_version_id", UUID),
        sa.Column("role", sa.String(80), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _fk(
            "data_bundle_member",
            "data_bundle_version_id",
            "data.data_bundle_version.data_bundle_version_id",
        ),
        _fk(
            "data_bundle_member",
            "dataset_publication_id",
            "data.dataset_publication.dataset_publication_id",
        ),
        _fk(
            "data_bundle_member",
            "calendar_version_id",
            "catalog.calendar_version.calendar_version_id",
        ),
        sa.PrimaryKeyConstraint("data_bundle_member_id", name="pk_data_bundle_member"),
        sa.UniqueConstraint("data_bundle_version_id", "role", name="uq_data_bundle_member_role"),
        sa.CheckConstraint(
            "num_nonnulls(dataset_publication_id, calendar_version_id) = 1",
            name="ck_data_bundle_member_exactly_one_member",
        ),
        schema="data",
    )
    _create_eligibility_tables()
    _create_guards()


def _create_eligibility_tables() -> None:
    op.create_table(
        "eligibility_snapshot",
        _id("eligibility_snapshot_id"),
        _id("artifact_id"),
        _id("universe_version_id"),
        _id("data_requirement_version_id"),
        _id("data_bundle_version_id"),
        sa.Column("snapshot_key", sa.String(180), nullable=False),
        sa.Column("requested_start", sa.Date(), nullable=False),
        sa.Column("requested_end", sa.Date(), nullable=False),
        sa.Column("warmup_observations", sa.Integer(), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        _created(),
        _fk("eligibility_snapshot", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "eligibility_snapshot",
            "universe_version_id",
            "catalog.universe_version.universe_version_id",
        ),
        _fk(
            "eligibility_snapshot",
            "data_requirement_version_id",
            "catalog.data_requirement_version.data_requirement_version_id",
        ),
        _fk(
            "eligibility_snapshot",
            "data_bundle_version_id",
            "data.data_bundle_version.data_bundle_version_id",
        ),
        sa.PrimaryKeyConstraint("eligibility_snapshot_id", name="pk_eligibility_snapshot"),
        sa.UniqueConstraint("artifact_id", name="uq_eligibility_snapshot_artifact_id"),
        sa.UniqueConstraint("snapshot_key", name="uq_eligibility_snapshot_snapshot_key"),
        sa.CheckConstraint(
            "requested_start <= requested_end",
            name="ck_eligibility_snapshot_requested_range_ordered",
        ),
        sa.CheckConstraint(
            "warmup_observations >= 1", name="ck_eligibility_snapshot_warmup_positive"
        ),
        sa.CheckConstraint(
            "eligible_count >= 0", name="ck_eligibility_snapshot_eligible_count_nonnegative"
        ),
        sa.CheckConstraint(
            "member_count >= eligible_count", name="ck_eligibility_snapshot_eligible_count_bounded"
        ),
        schema="catalog",
    )
    op.create_table(
        "eligibility_item",
        _id("eligibility_item_id"),
        _id("eligibility_snapshot_id"),
        _id("asset_id"),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("is_eligible", sa.Boolean(), nullable=False),
        sa.Column("available_start", sa.Date()),
        sa.Column("available_end", sa.Date()),
        sa.Column("data_ready_date", sa.Date()),
        sa.Column("observation_count", sa.BigInteger(), nullable=False),
        _fk(
            "eligibility_item",
            "eligibility_snapshot_id",
            "catalog.eligibility_snapshot.eligibility_snapshot_id",
        ),
        _fk("eligibility_item", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint("eligibility_item_id", name="pk_eligibility_item"),
        sa.UniqueConstraint(
            "eligibility_snapshot_id", "asset_id", name="uq_eligibility_item_asset"
        ),
        schema="catalog",
    )
    op.create_table(
        "eligibility_issue",
        _id("eligibility_issue_id"),
        _id("eligibility_item_id"),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("issue_code", sa.String(100), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        _fk(
            "eligibility_issue",
            "eligibility_item_id",
            "catalog.eligibility_item.eligibility_item_id",
        ),
        sa.PrimaryKeyConstraint("eligibility_issue_id", name="pk_eligibility_issue"),
        sa.CheckConstraint(
            "severity IN ('warning', 'error')", name="ck_eligibility_issue_severity_allowed"
        ),
        schema="catalog",
    )


def _create_guards() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_reserve_return_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.reserve_return
        FOR EACH ROW EXECUTE FUNCTION data.enforce_dataset_child_draft();
        CREATE TRIGGER trg_reserve_model_definition_draft
        BEFORE INSERT OR UPDATE OR DELETE ON experiment.reserve_return_model_definition
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_reserve_model_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON experiment.reserve_return_model_version
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_bundle_definition_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.data_bundle_definition
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_bundle_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.data_bundle_version
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();

        CREATE FUNCTION data.enforce_bundle_member_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE version_id uuid; owner_artifact_id uuid;
        BEGIN
            version_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.data_bundle_version_id ELSE NEW.data_bundle_version_id END;
            SELECT artifact_id INTO owner_artifact_id FROM data.data_bundle_version
            WHERE data_bundle_version_id = version_id;
            PERFORM data.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_bundle_member_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.data_bundle_member
        FOR EACH ROW EXECUTE FUNCTION data.enforce_bundle_member_draft();

        CREATE TRIGGER trg_eligibility_snapshot_draft
        BEFORE INSERT OR UPDATE OR DELETE ON catalog.eligibility_snapshot
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE FUNCTION catalog.enforce_eligibility_item_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE snapshot_id uuid; owner_artifact_id uuid;
        BEGIN
            snapshot_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.eligibility_snapshot_id ELSE NEW.eligibility_snapshot_id END;
            SELECT artifact_id INTO owner_artifact_id FROM catalog.eligibility_snapshot
            WHERE eligibility_snapshot_id = snapshot_id;
            PERFORM data.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_eligibility_item_draft
        BEFORE INSERT OR UPDATE OR DELETE ON catalog.eligibility_item
        FOR EACH ROW EXECUTE FUNCTION catalog.enforce_eligibility_item_draft();
        CREATE FUNCTION catalog.enforce_eligibility_issue_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE item_id uuid; snapshot_id uuid; owner_artifact_id uuid;
        BEGIN
            item_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.eligibility_item_id ELSE NEW.eligibility_item_id END;
            SELECT eligibility_snapshot_id INTO snapshot_id FROM catalog.eligibility_item
            WHERE eligibility_item_id = item_id;
            SELECT artifact_id INTO owner_artifact_id FROM catalog.eligibility_snapshot
            WHERE eligibility_snapshot_id = snapshot_id;
            PERFORM data.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_eligibility_issue_draft
        BEFORE INSERT OR UPDATE OR DELETE ON catalog.eligibility_issue
        FOR EACH ROW EXECUTE FUNCTION catalog.enforce_eligibility_issue_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS catalog.enforce_eligibility_issue_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS catalog.enforce_eligibility_item_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS data.enforce_bundle_member_draft() CASCADE")
    op.drop_table("eligibility_issue", schema="catalog")
    op.drop_table("eligibility_item", schema="catalog")
    op.drop_table("eligibility_snapshot", schema="catalog")
    op.drop_table("data_bundle_member", schema="data")
    op.drop_table("data_bundle_version", schema="data")
    op.drop_table("data_bundle_definition", schema="data")
    op.drop_table("reserve_return", schema="data")
    op.drop_table("reserve_return_model_version", schema="experiment")
    op.drop_table("reserve_return_model_definition", schema="experiment")
    op.drop_constraint(
        "ck_dataset_input_exactly_one_input", "dataset_input", schema="data", type_="check"
    )
    op.drop_constraint(
        "fk_dataset_input_upstream_dataset_publication_id",
        "dataset_input",
        schema="data",
        type_="foreignkey",
    )
    op.drop_column("dataset_input", "upstream_dataset_publication_id", schema="data")
    op.alter_column("dataset_input", "source_snapshot_id", nullable=False, schema="data")
    op.alter_column("dataset_publication", "cleaning_version_id", nullable=False, schema="data")
