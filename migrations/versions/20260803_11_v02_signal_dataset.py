"""Add immutable signal datasets, typed values, and quality issues.

Revision ID: 20260803_11_v02_signal_data
Revises: 20260803_10_v02_signal_core
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_11_v02_signal_data"
down_revision: str | None = "20260803_10_v02_signal_core"
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


def upgrade() -> None:
    op.create_table(
        "signal_dataset",
        _id("signal_dataset_id"),
        _id("artifact_id"),
        _id("signal_version_id"),
        _id("factor_dataset_id"),
        _id("universe_version_id"),
        _id("data_bundle_version_id"),
        _id("eligibility_snapshot_id"),
        _id("engine_version_id"),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        _created(),
        _fk("signal_dataset", "artifact_id", "lineage.artifact.artifact_id"),
        _fk("signal_dataset", "signal_version_id", "signal.signal_version.signal_version_id"),
        _fk("signal_dataset", "factor_dataset_id", "factor.factor_dataset.factor_dataset_id"),
        _fk(
            "signal_dataset",
            "universe_version_id",
            "catalog.universe_version.universe_version_id",
        ),
        _fk(
            "signal_dataset",
            "data_bundle_version_id",
            "data.data_bundle_version.data_bundle_version_id",
        ),
        _fk(
            "signal_dataset",
            "eligibility_snapshot_id",
            "catalog.eligibility_snapshot.eligibility_snapshot_id",
        ),
        _fk("signal_dataset", "engine_version_id", "ops.engine_version.engine_version_id"),
        sa.PrimaryKeyConstraint("signal_dataset_id", name="pk_signal_dataset"),
        sa.UniqueConstraint("artifact_id", name="uq_signal_dataset_artifact_id"),
        sa.UniqueConstraint(
            "signal_version_id",
            "factor_dataset_id",
            "engine_version_id",
            name="uq_signal_dataset_exact_inputs",
        ),
        sa.CheckConstraint(
            "coverage_start <= coverage_end", name="ck_signal_dataset_coverage_ordered"
        ),
        sa.CheckConstraint("row_count >= 1", name="ck_signal_dataset_row_count_positive"),
        schema="signal",
    )
    op.create_table(
        "signal_value",
        _id("signal_dataset_id"),
        _id("asset_id"),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("score", sa.Numeric(24, 18), nullable=False),
        sa.Column("state", sa.String(20)),
        sa.Column("event", sa.Boolean()),
        _fk("signal_value", "signal_dataset_id", "signal.signal_dataset.signal_dataset_id"),
        _fk("signal_value", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint(
            "signal_dataset_id", "asset_id", "observation_date", name="pk_signal_value"
        ),
        sa.CheckConstraint(
            "score NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="ck_signal_value_score_finite",
        ),
        sa.CheckConstraint(
            "state IS NULL OR state IN ('positive', 'negative', 'neutral')",
            name="ck_signal_value_state_allowed",
        ),
        schema="signal",
    )
    op.create_table(
        "signal_quality_issue",
        _id("signal_quality_issue_id"),
        _id("signal_dataset_id"),
        sa.Column("asset_id", UUID),
        sa.Column("observation_date", sa.Date()),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("issue_code", sa.String(120), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        _created(),
        _fk(
            "signal_quality_issue",
            "signal_dataset_id",
            "signal.signal_dataset.signal_dataset_id",
        ),
        _fk("signal_quality_issue", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint("signal_quality_issue_id", name="pk_signal_quality_issue"),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')",
            name="ck_signal_quality_issue_severity_allowed",
        ),
        schema="signal",
    )
    op.create_index(
        "ix_signal_quality_issue_dataset_severity",
        "signal_quality_issue",
        ["signal_dataset_id", "severity"],
        schema="signal",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION signal.validate_dataset_owner() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE factor_row record; signal_factor_id uuid; engine_key_value text;
        BEGIN
            PERFORM data.assert_artifact_draft(
                CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END
            );
            IF TG_OP <> 'DELETE' THEN
                SELECT factor_variant_id, universe_version_id, data_bundle_version_id,
                       eligibility_snapshot_id INTO factor_row
                FROM factor.factor_dataset WHERE factor_dataset_id = NEW.factor_dataset_id;
                SELECT factor_variant_id INTO signal_factor_id FROM signal.signal_version
                WHERE signal_version_id = NEW.signal_version_id;
                IF factor_row.factor_variant_id <> signal_factor_id THEN
                    RAISE EXCEPTION 'signal version and factor dataset variants do not match';
                END IF;
                IF factor_row.universe_version_id <> NEW.universe_version_id
                   OR factor_row.data_bundle_version_id <> NEW.data_bundle_version_id
                   OR factor_row.eligibility_snapshot_id <> NEW.eligibility_snapshot_id THEN
                    RAISE EXCEPTION 'signal dataset context does not match factor dataset';
                END IF;
                SELECT definition.engine_key INTO engine_key_value
                FROM ops.engine_version version JOIN ops.engine_definition definition
                ON definition.engine_definition_id = version.engine_definition_id
                WHERE version.engine_version_id = NEW.engine_version_id;
                IF engine_key_value <> 'signal_engine' THEN
                    RAISE EXCEPTION 'signal dataset requires a signal engine';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_signal_dataset_draft
        BEFORE INSERT OR UPDATE OR DELETE ON signal.signal_dataset
        FOR EACH ROW EXECUTE FUNCTION signal.validate_dataset_owner();

        CREATE FUNCTION signal.enforce_dataset_child_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE dataset_id uuid; owner_artifact_id uuid; output_type_value text;
        BEGIN
            dataset_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.signal_dataset_id ELSE NEW.signal_dataset_id END;
            SELECT dataset.artifact_id, version.output_type
            INTO owner_artifact_id, output_type_value
            FROM signal.signal_dataset dataset JOIN signal.signal_version version
            ON version.signal_version_id = dataset.signal_version_id
            WHERE dataset.signal_dataset_id = dataset_id;
            PERFORM data.assert_artifact_draft(owner_artifact_id);
            IF TG_TABLE_NAME = 'signal_value' AND TG_OP <> 'DELETE' THEN
                IF output_type_value = 'continuous'
                   AND (NEW.state IS NOT NULL OR NEW.event IS NOT NULL) THEN
                    RAISE EXCEPTION 'continuous signal values cannot contain state or event';
                ELSIF output_type_value = 'threshold_state'
                   AND (NEW.state IS NULL OR NEW.event IS NOT NULL) THEN
                    RAISE EXCEPTION 'threshold signal values require state and no event';
                ELSIF output_type_value = 'crossover_event'
                   AND (NEW.state IS NULL OR NEW.event IS NULL) THEN
                    RAISE EXCEPTION 'crossover signal values require state and event';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_signal_value_draft
        BEFORE INSERT OR UPDATE OR DELETE ON signal.signal_value
        FOR EACH ROW EXECUTE FUNCTION signal.enforce_dataset_child_draft();
        CREATE TRIGGER trg_signal_quality_issue_draft
        BEFORE INSERT OR UPDATE OR DELETE ON signal.signal_quality_issue
        FOR EACH ROW EXECUTE FUNCTION signal.enforce_dataset_child_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS signal.enforce_dataset_child_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS signal.validate_dataset_owner() CASCADE")
    op.drop_index(
        "ix_signal_quality_issue_dataset_severity",
        table_name="signal_quality_issue",
        schema="signal",
    )
    op.drop_table("signal_quality_issue", schema="signal")
    op.drop_table("signal_value", schema="signal")
    op.drop_table("signal_dataset", schema="signal")
