"""Add versioned factor definitions, variants, datasets, and typed values.

Revision ID: 20260803_07_v02_factor
Revises: 20260803_06_v02_bundle
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_07_v02_factor"
down_revision: str | None = "20260803_06_v02_bundle"
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
        "factor_definition",
        _id("factor_definition_id"),
        _id("artifact_id"),
        sa.Column("factor_key", sa.String(120), nullable=False),
        sa.Column("measurement_family", sa.String(80), nullable=False),
        _created(),
        _fk("factor_definition", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("factor_definition_id", name="pk_factor_definition"),
        sa.UniqueConstraint("artifact_id", name="uq_factor_definition_artifact_id"),
        sa.UniqueConstraint("factor_key", name="uq_factor_definition_factor_key"),
        schema="factor",
    )
    op.create_table(
        "factor_definition_version",
        _id("factor_definition_version_id"),
        _id("factor_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("output_unit", sa.String(80), nullable=False),
        sa.Column("time_semantics", sa.String(160), nullable=False),
        sa.Column("implementation_key", sa.String(160), nullable=False),
        _created(),
        _fk(
            "factor_definition_version",
            "factor_definition_id",
            "factor.factor_definition.factor_definition_id",
        ),
        _fk("factor_definition_version", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint(
            "factor_definition_version_id", name="pk_factor_definition_version"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_factor_definition_version_artifact_id"),
        sa.UniqueConstraint(
            "factor_definition_id",
            "version_number",
            name="uq_factor_definition_version_definition_version",
        ),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_factor_definition_version_version_positive"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(inputs) = 'array' AND jsonb_array_length(inputs) >= 1",
            name="ck_factor_definition_version_inputs_nonempty_array",
        ),
        schema="factor",
    )
    op.create_table(
        "factor_variant",
        _id("factor_variant_id"),
        _id("factor_definition_version_id"),
        _id("artifact_id"),
        sa.Column("variant_key", sa.String(180), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("parameter_hash", sa.String(64), nullable=False),
        sa.Column("required_price_observations", sa.Integer(), nullable=False),
        sa.Column("preset_type", sa.String(40), nullable=False),
        _created(),
        _fk(
            "factor_variant",
            "factor_definition_version_id",
            "factor.factor_definition_version.factor_definition_version_id",
        ),
        _fk("factor_variant", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("factor_variant_id", name="pk_factor_variant"),
        sa.UniqueConstraint("artifact_id", name="uq_factor_variant_artifact_id"),
        sa.UniqueConstraint(
            "factor_definition_version_id",
            "variant_key",
            name="uq_factor_variant_definition_variant_key",
        ),
        sa.UniqueConstraint(
            "factor_definition_version_id",
            "parameter_hash",
            name="uq_factor_variant_definition_parameters",
        ),
        sa.CheckConstraint(
            "parameter_hash ~ '^[0-9a-f]{64}$'", name="ck_factor_variant_parameter_hash_sha256"
        ),
        sa.CheckConstraint(
            "required_price_observations >= 1",
            name="ck_factor_variant_required_observations_positive",
        ),
        sa.CheckConstraint(
            "preset_type IN ('canonical', 'horizon_anchor', 'sensitivity', 'exploratory')",
            name="ck_factor_variant_preset_type_allowed",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parameters) = 'object'",
            name="ck_factor_variant_parameters_object",
        ),
        schema="factor",
    )
    op.create_table(
        "factor_dataset",
        _id("factor_dataset_id"),
        _id("artifact_id"),
        _id("factor_variant_id"),
        _id("universe_version_id"),
        _id("data_bundle_version_id"),
        _id("eligibility_snapshot_id"),
        _id("engine_version_id"),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        _created(),
        _fk("factor_dataset", "artifact_id", "lineage.artifact.artifact_id"),
        _fk("factor_dataset", "factor_variant_id", "factor.factor_variant.factor_variant_id"),
        _fk(
            "factor_dataset", "universe_version_id", "catalog.universe_version.universe_version_id"
        ),
        _fk(
            "factor_dataset",
            "data_bundle_version_id",
            "data.data_bundle_version.data_bundle_version_id",
        ),
        _fk(
            "factor_dataset",
            "eligibility_snapshot_id",
            "catalog.eligibility_snapshot.eligibility_snapshot_id",
        ),
        _fk("factor_dataset", "engine_version_id", "ops.engine_version.engine_version_id"),
        sa.PrimaryKeyConstraint("factor_dataset_id", name="pk_factor_dataset"),
        sa.UniqueConstraint("artifact_id", name="uq_factor_dataset_artifact_id"),
        sa.UniqueConstraint(
            "factor_variant_id",
            "universe_version_id",
            "data_bundle_version_id",
            "eligibility_snapshot_id",
            "engine_version_id",
            name="uq_factor_dataset_exact_inputs",
        ),
        sa.CheckConstraint(
            "coverage_start <= coverage_end", name="ck_factor_dataset_coverage_ordered"
        ),
        sa.CheckConstraint("row_count >= 1", name="ck_factor_dataset_row_count_positive"),
        schema="factor",
    )
    op.create_table(
        "factor_value",
        _id("factor_dataset_id"),
        _id("asset_id"),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        _fk("factor_value", "factor_dataset_id", "factor.factor_dataset.factor_dataset_id"),
        _fk("factor_value", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint(
            "factor_dataset_id", "asset_id", "observation_date", name="pk_factor_value"
        ),
        sa.CheckConstraint(
            "value <> 'NaN'::double precision AND "
            "value <> 'Infinity'::double precision AND "
            "value <> '-Infinity'::double precision",
            name="ck_factor_value_finite",
        ),
        schema="factor",
    )
    op.create_table(
        "factor_quality_issue",
        _id("factor_quality_issue_id"),
        _id("factor_dataset_id"),
        sa.Column("asset_id", UUID),
        sa.Column("observation_date", sa.Date()),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("issue_code", sa.String(120), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        _created(),
        _fk("factor_quality_issue", "factor_dataset_id", "factor.factor_dataset.factor_dataset_id"),
        _fk("factor_quality_issue", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint("factor_quality_issue_id", name="pk_factor_quality_issue"),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')",
            name="ck_factor_quality_issue_severity_allowed",
        ),
        schema="factor",
    )
    op.create_index(
        "ix_factor_quality_issue_dataset_severity",
        "factor_quality_issue",
        ["factor_dataset_id", "severity"],
        schema="factor",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_factor_definition_draft
        BEFORE INSERT OR UPDATE OR DELETE ON factor.factor_definition
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_factor_definition_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON factor.factor_definition_version
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_factor_variant_draft
        BEFORE INSERT OR UPDATE OR DELETE ON factor.factor_variant
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();

        CREATE FUNCTION factor.validate_dataset_owner() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE eligibility record;
        BEGIN
            PERFORM data.assert_artifact_draft(
                CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END
            );
            IF TG_OP <> 'DELETE' THEN
                SELECT universe_version_id, data_bundle_version_id, member_count, eligible_count
                INTO eligibility FROM catalog.eligibility_snapshot
                WHERE eligibility_snapshot_id = NEW.eligibility_snapshot_id;
                IF eligibility.universe_version_id <> NEW.universe_version_id
                   OR eligibility.data_bundle_version_id <> NEW.data_bundle_version_id THEN
                    RAISE EXCEPTION 'factor dataset inputs do not match eligibility snapshot';
                END IF;
                IF eligibility.eligible_count <> eligibility.member_count THEN
                    RAISE EXCEPTION 'factor dataset requires a fully eligible snapshot';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_factor_dataset_draft
        BEFORE INSERT OR UPDATE OR DELETE ON factor.factor_dataset
        FOR EACH ROW EXECUTE FUNCTION factor.validate_dataset_owner();

        CREATE FUNCTION factor.enforce_dataset_child_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE dataset_id uuid; owner_artifact_id uuid;
        BEGIN
            dataset_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.factor_dataset_id ELSE NEW.factor_dataset_id END;
            SELECT artifact_id INTO owner_artifact_id FROM factor.factor_dataset
            WHERE factor_dataset_id = dataset_id;
            PERFORM data.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_factor_value_draft
        BEFORE INSERT OR UPDATE OR DELETE ON factor.factor_value
        FOR EACH ROW EXECUTE FUNCTION factor.enforce_dataset_child_draft();
        CREATE TRIGGER trg_factor_quality_issue_draft
        BEFORE INSERT OR UPDATE OR DELETE ON factor.factor_quality_issue
        FOR EACH ROW EXECUTE FUNCTION factor.enforce_dataset_child_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS factor.enforce_dataset_child_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS factor.validate_dataset_owner() CASCADE")
    op.drop_index(
        "ix_factor_quality_issue_dataset_severity",
        table_name="factor_quality_issue",
        schema="factor",
    )
    op.drop_table("factor_quality_issue", schema="factor")
    op.drop_table("factor_value", schema="factor")
    op.drop_table("factor_dataset", schema="factor")
    op.drop_table("factor_variant", schema="factor")
    op.drop_table("factor_definition_version", schema="factor")
    op.drop_table("factor_definition", schema="factor")
