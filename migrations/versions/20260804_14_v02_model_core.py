"""Add versioned Model methods, specifications, dimensions, and components.

Revision ID: 20260804_14_v02_model_core
Revises: 20260804_13_v02_signal_eval
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_14_v02_model_core"
down_revision: str | None = "20260804_13_v02_signal_eval"
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
        [column], [target], name=f"fk_{table[:16]}_{column[:24]}", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "model_method_definition",
        _id("model_method_definition_id"),
        _id("artifact_id"),
        sa.Column("method_key", sa.String(80), nullable=False),
        _created(),
        _fk("model_method_definition", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("model_method_definition_id", name="pk_model_method_definition"),
        sa.UniqueConstraint("artifact_id", name="uq_model_method_definition_artifact"),
        sa.UniqueConstraint("method_key", name="uq_model_method_definition_key"),
        schema="model",
    )
    op.create_table(
        "model_method_version",
        _id("model_method_version_id"),
        _id("model_method_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("supported_input_transforms", postgresql.JSONB(), nullable=False),
        sa.Column("missing_policy", sa.String(80), nullable=False),
        sa.Column("neutral_policy", sa.String(80), nullable=False),
        sa.Column("tie_policy", sa.String(80), nullable=False),
        sa.Column("output_scaling", sa.String(80), nullable=False),
        _created(),
        _fk(
            "model_method_version",
            "model_method_definition_id",
            "model.model_method_definition.model_method_definition_id",
        ),
        _fk("model_method_version", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("model_method_version_id", name="pk_model_method_version"),
        sa.UniqueConstraint("artifact_id", name="uq_model_method_version_artifact"),
        sa.UniqueConstraint(
            "model_method_definition_id",
            "version_number",
            name="uq_model_method_definition_version",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_model_method_version_positive"),
        sa.CheckConstraint(
            "jsonb_typeof(supported_input_transforms) = 'array'",
            name="ck_model_method_transforms_array",
        ),
        schema="model",
    )
    op.create_table(
        "model_definition",
        _id("model_definition_id"),
        _id("artifact_id"),
        sa.Column("model_key", sa.String(160), nullable=False),
        sa.Column("model_family", sa.String(100), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        _created(),
        _fk("model_definition", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("model_definition_id", name="pk_model_definition"),
        sa.UniqueConstraint("artifact_id", name="uq_model_definition_artifact"),
        sa.UniqueConstraint("model_key", name="uq_model_definition_key"),
        schema="model",
    )
    op.create_table(
        "model_definition_version",
        _id("model_definition_version_id"),
        _id("model_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("architecture", sa.String(100), nullable=False),
        sa.Column("missing_policy", sa.String(100), nullable=False),
        sa.Column("neutral_policy", sa.String(100), nullable=False),
        _created(),
        _fk(
            "model_definition_version",
            "model_definition_id",
            "model.model_definition.model_definition_id",
        ),
        _fk("model_definition_version", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("model_definition_version_id", name="pk_model_definition_version"),
        sa.UniqueConstraint("artifact_id", name="uq_model_definition_version_artifact"),
        sa.UniqueConstraint(
            "model_definition_id",
            "version_number",
            name="uq_model_definition_version",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_model_definition_version_positive"),
        schema="model",
    )
    op.create_table(
        "model_specification",
        _id("model_specification_id"),
        _id("model_definition_version_id"),
        _id("overall_method_version_id"),
        _id("artifact_id"),
        sa.Column("specification_key", sa.String(500), nullable=False),
        sa.Column("specification_type", sa.String(60), nullable=False),
        sa.Column("tie_output", sa.String(40), nullable=False),
        sa.Column("output_type", sa.String(40), nullable=False),
        sa.Column("active_dimension_count", sa.Integer(), nullable=False),
        sa.Column("component_count", sa.Integer(), nullable=False),
        sa.Column("research_tier", sa.String(30), nullable=False),
        _created(),
        _fk(
            "model_specification",
            "model_definition_version_id",
            "model.model_definition_version.model_definition_version_id",
        ),
        _fk(
            "model_specification",
            "overall_method_version_id",
            "model.model_method_version.model_method_version_id",
        ),
        _fk("model_specification", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("model_specification_id", name="pk_model_specification"),
        sa.UniqueConstraint("artifact_id", name="uq_model_specification_artifact"),
        sa.UniqueConstraint("specification_key", name="uq_model_specification_key"),
        sa.CheckConstraint(
            "specification_type IN ('single_signal', 'dimension_subset_equal_weight', "
            "'fixed_weight', 'directional_vote')",
            name="ck_model_specification_type",
        ),
        sa.CheckConstraint(
            "tie_output IN ('neutral', 'not_applicable')",
            name="ck_model_specification_tie_output",
        ),
        sa.CheckConstraint(
            "output_type IN ('continuous_score', 'directional_score')",
            name="ck_model_specification_output_type",
        ),
        sa.CheckConstraint(
            "active_dimension_count >= 1", name="ck_model_specification_dimensions_positive"
        ),
        sa.CheckConstraint(
            "component_count >= 1", name="ck_model_specification_components_positive"
        ),
        schema="model",
    )
    op.create_table(
        "model_dimension",
        _id("model_dimension_id"),
        _id("model_specification_id"),
        _id("method_version_id"),
        sa.Column("dimension_key", sa.String(80), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("input_transform", sa.String(40), nullable=False),
        sa.Column("weight", sa.Numeric(24, 18), nullable=False),
        _created(),
        _fk(
            "model_dimension",
            "model_specification_id",
            "model.model_specification.model_specification_id",
        ),
        _fk(
            "model_dimension",
            "method_version_id",
            "model.model_method_version.model_method_version_id",
        ),
        sa.PrimaryKeyConstraint("model_dimension_id", name="pk_model_dimension"),
        sa.UniqueConstraint(
            "model_specification_id", "model_dimension_id", name="uq_model_dimension_identity"
        ),
        sa.UniqueConstraint(
            "model_specification_id", "dimension_key", name="uq_model_dimension_key"
        ),
        sa.UniqueConstraint("model_specification_id", "ordinal", name="uq_model_dimension_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_model_dimension_ordinal_nonnegative"),
        sa.CheckConstraint("weight > 0 AND weight <= 1", name="ck_model_dimension_weight"),
        sa.CheckConstraint(
            "input_transform IN ('identity', 'sign', 'threshold_state')",
            name="ck_model_dimension_transform",
        ),
        schema="model",
    )
    op.create_table(
        "model_component",
        _id("model_component_id"),
        _id("model_specification_id"),
        _id("model_dimension_id"),
        _id("signal_version_id"),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("input_transform", sa.String(40), nullable=False),
        sa.Column("weight", sa.Numeric(24, 18), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["model_specification_id", "model_dimension_id"],
            [
                "model.model_dimension.model_specification_id",
                "model.model_dimension.model_dimension_id",
            ],
            name="fk_model_component_dimension_identity",
            ondelete="RESTRICT",
        ),
        _fk(
            "model_component",
            "signal_version_id",
            "signal.signal_version.signal_version_id",
        ),
        sa.PrimaryKeyConstraint("model_component_id", name="pk_model_component"),
        sa.UniqueConstraint(
            "model_dimension_id", "ordinal", name="uq_model_component_dimension_ordinal"
        ),
        sa.UniqueConstraint(
            "model_specification_id", "signal_version_id", name="uq_model_component_signal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_model_component_ordinal_nonnegative"),
        sa.CheckConstraint("weight > 0 AND weight <= 1", name="ck_model_component_weight"),
        sa.CheckConstraint(
            "input_transform IN ('identity', 'sign', 'threshold_state')",
            name="ck_model_component_transform",
        ),
        schema="model",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_model_method_definition_draft
        BEFORE INSERT OR UPDATE OR DELETE ON model.model_method_definition
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_model_method_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON model.model_method_version
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_model_definition_draft
        BEFORE INSERT OR UPDATE OR DELETE ON model.model_definition
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_model_definition_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON model.model_definition_version
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_model_specification_draft
        BEFORE INSERT OR UPDATE OR DELETE ON model.model_specification
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();

        CREATE FUNCTION model.enforce_specification_child_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE current_status text;
        BEGIN
            SELECT artifact.status INTO current_status
            FROM model.model_specification specification
            JOIN lineage.artifact artifact ON artifact.artifact_id = specification.artifact_id
            WHERE specification.model_specification_id =
                COALESCE(NEW.model_specification_id, OLD.model_specification_id);
            IF current_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION
                    'model specification children can only change while artifact is draft';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$;
        CREATE TRIGGER trg_model_dimension_draft
        BEFORE INSERT OR UPDATE OR DELETE ON model.model_dimension
        FOR EACH ROW EXECUTE FUNCTION model.enforce_specification_child_draft();
        CREATE TRIGGER trg_model_component_draft
        BEFORE INSERT OR UPDATE OR DELETE ON model.model_component
        FOR EACH ROW EXECUTE FUNCTION model.enforce_specification_child_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS model.enforce_specification_child_draft() CASCADE")
    op.drop_table("model_component", schema="model")
    op.drop_table("model_dimension", schema="model")
    op.drop_table("model_specification", schema="model")
    op.drop_table("model_definition_version", schema="model")
    op.drop_table("model_definition", schema="model")
    op.drop_table("model_method_version", schema="model")
    op.drop_table("model_method_definition", schema="model")
