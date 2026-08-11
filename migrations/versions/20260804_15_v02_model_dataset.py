"""Add immutable Model datasets, explicit inputs, and typed values.

Revision ID: 20260804_15_v02_model_data
Revises: 20260804_14_v02_model_core
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_15_v02_model_data"
down_revision: str | None = "20260804_14_v02_model_core"
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
        "model_dataset",
        _id("model_dataset_id"),
        _id("artifact_id"),
        _id("model_specification_id"),
        _id("universe_version_id"),
        _id("data_bundle_version_id"),
        _id("eligibility_snapshot_id"),
        _id("engine_version_id"),
        sa.Column("input_set_hash", sa.String(64), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        _created(),
        _fk("model_dataset", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "model_dataset",
            "model_specification_id",
            "model.model_specification.model_specification_id",
        ),
        _fk(
            "model_dataset",
            "universe_version_id",
            "catalog.universe_version.universe_version_id",
        ),
        _fk(
            "model_dataset",
            "data_bundle_version_id",
            "data.data_bundle_version.data_bundle_version_id",
        ),
        _fk(
            "model_dataset",
            "eligibility_snapshot_id",
            "catalog.eligibility_snapshot.eligibility_snapshot_id",
        ),
        _fk("model_dataset", "engine_version_id", "ops.engine_version.engine_version_id"),
        sa.PrimaryKeyConstraint("model_dataset_id", name="pk_model_dataset"),
        sa.UniqueConstraint("artifact_id", name="uq_model_dataset_artifact"),
        sa.UniqueConstraint(
            "model_specification_id",
            "universe_version_id",
            "data_bundle_version_id",
            "eligibility_snapshot_id",
            "engine_version_id",
            "input_set_hash",
            name="uq_model_dataset_exact_inputs",
        ),
        sa.CheckConstraint("input_set_hash ~ '^[0-9a-f]{64}$'", name="ck_model_dataset_input_hash"),
        sa.CheckConstraint("coverage_start <= coverage_end", name="ck_model_dataset_coverage"),
        sa.CheckConstraint("row_count >= 1", name="ck_model_dataset_row_count"),
        schema="model",
    )
    op.create_table(
        "model_dataset_input",
        _id("model_dataset_id"),
        _id("model_component_id"),
        _id("signal_dataset_id"),
        _created(),
        _fk(
            "model_dataset_input",
            "model_dataset_id",
            "model.model_dataset.model_dataset_id",
        ),
        _fk(
            "model_dataset_input",
            "model_component_id",
            "model.model_component.model_component_id",
        ),
        _fk(
            "model_dataset_input",
            "signal_dataset_id",
            "signal.signal_dataset.signal_dataset_id",
        ),
        sa.PrimaryKeyConstraint(
            "model_dataset_id", "model_component_id", name="pk_model_dataset_input"
        ),
        sa.UniqueConstraint(
            "model_dataset_id", "signal_dataset_id", name="uq_model_dataset_signal_input"
        ),
        schema="model",
    )
    op.create_table(
        "model_value",
        _id("model_dataset_id"),
        _id("asset_id"),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("score", sa.Numeric(24, 18), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Numeric(24, 18), nullable=False),
        _fk("model_value", "model_dataset_id", "model.model_dataset.model_dataset_id"),
        _fk("model_value", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint(
            "model_dataset_id", "asset_id", "observation_date", name="pk_model_value"
        ),
        sa.CheckConstraint(
            "score NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric) "
            "AND score BETWEEN -1 AND 1",
            name="ck_model_value_score",
        ),
        sa.CheckConstraint(
            "direction IN ('positive', 'negative', 'neutral')",
            name="ck_model_value_direction",
        ),
        sa.CheckConstraint(
            "confidence NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric) "
            "AND confidence BETWEEN 0 AND 1",
            name="ck_model_value_confidence",
        ),
        sa.CheckConstraint(
            "(score > 0 AND direction = 'positive') OR "
            "(score < 0 AND direction = 'negative') OR "
            "(score = 0 AND direction = 'neutral')",
            name="ck_model_value_direction_matches_score",
        ),
        sa.CheckConstraint(
            "confidence = abs(score)", name="ck_model_value_confidence_matches_score"
        ),
        schema="model",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION model.validate_dataset_owner() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE engine_key_value text; eligibility_row record;
        BEGIN
            PERFORM data.assert_artifact_draft(
                CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END
            );
            IF TG_OP <> 'DELETE' THEN
                SELECT definition.engine_key INTO engine_key_value
                FROM ops.engine_version version JOIN ops.engine_definition definition
                ON definition.engine_definition_id = version.engine_definition_id
                WHERE version.engine_version_id = NEW.engine_version_id;
                IF engine_key_value <> 'model_engine' THEN
                    RAISE EXCEPTION 'model dataset requires a model engine';
                END IF;
                SELECT universe_version_id, data_bundle_version_id INTO eligibility_row
                FROM catalog.eligibility_snapshot
                WHERE eligibility_snapshot_id = NEW.eligibility_snapshot_id;
                IF eligibility_row.universe_version_id <> NEW.universe_version_id
                   OR eligibility_row.data_bundle_version_id <> NEW.data_bundle_version_id THEN
                    RAISE EXCEPTION 'model dataset context does not match eligibility';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_model_dataset_draft
        BEFORE INSERT OR UPDATE OR DELETE ON model.model_dataset
        FOR EACH ROW EXECUTE FUNCTION model.validate_dataset_owner();

        CREATE FUNCTION model.enforce_dataset_child_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE dataset_id uuid; owner record; component_row record; signal_row record;
        BEGIN
            dataset_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.model_dataset_id ELSE NEW.model_dataset_id END;
            SELECT * INTO owner FROM model.model_dataset WHERE model_dataset_id = dataset_id;
            PERFORM data.assert_artifact_draft(owner.artifact_id);
            IF TG_TABLE_NAME = 'model_dataset_input' AND TG_OP <> 'DELETE' THEN
                SELECT model_specification_id, signal_version_id INTO component_row
                FROM model.model_component
                WHERE model_component_id = NEW.model_component_id;
                SELECT signal_version_id, universe_version_id, data_bundle_version_id,
                       eligibility_snapshot_id INTO signal_row
                FROM signal.signal_dataset WHERE signal_dataset_id = NEW.signal_dataset_id;
                IF component_row.model_specification_id <> owner.model_specification_id
                   OR component_row.signal_version_id <> signal_row.signal_version_id THEN
                    RAISE EXCEPTION 'model component and Signal dataset do not match';
                END IF;
                IF signal_row.universe_version_id <> owner.universe_version_id
                   OR signal_row.data_bundle_version_id <> owner.data_bundle_version_id
                   OR signal_row.eligibility_snapshot_id <> owner.eligibility_snapshot_id THEN
                    RAISE EXCEPTION 'model and Signal dataset contexts do not match';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_model_dataset_input_draft
        BEFORE INSERT OR UPDATE OR DELETE ON model.model_dataset_input
        FOR EACH ROW EXECUTE FUNCTION model.enforce_dataset_child_draft();
        CREATE TRIGGER trg_model_value_draft
        BEFORE INSERT OR UPDATE OR DELETE ON model.model_value
        FOR EACH ROW EXECUTE FUNCTION model.enforce_dataset_child_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS model.enforce_dataset_child_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS model.validate_dataset_owner() CASCADE")
    op.drop_table("model_value", schema="model")
    op.drop_table("model_dataset_input", schema="model")
    op.drop_table("model_dataset", schema="model")
