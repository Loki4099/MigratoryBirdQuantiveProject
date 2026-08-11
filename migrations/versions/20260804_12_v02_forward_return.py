"""Add versioned forward-return targets and datasets.

Revision ID: 20260804_12_v02_forward_ret
Revises: 20260803_11_v02_signal_data
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_12_v02_forward_ret"
down_revision: str | None = "20260803_11_v02_signal_data"
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
        "forward_return_definition",
        _id("forward_return_definition_id"),
        _id("artifact_id"),
        sa.Column("target_key", sa.String(140), nullable=False),
        _created(),
        _fk("forward_return_definition", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint(
            "forward_return_definition_id", name="pk_forward_return_definition"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_forward_return_definition_artifact"),
        sa.UniqueConstraint("target_key", name="uq_forward_return_definition_key"),
        schema="data",
    )
    op.create_table(
        "forward_return_version",
        _id("forward_return_version_id"),
        _id("forward_return_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("decision_rule", sa.String(80), nullable=False),
        sa.Column("decision_time", sa.String(40), nullable=False),
        sa.Column("execution_policy", sa.String(100), nullable=False),
        sa.Column("start_price", sa.String(40), nullable=False),
        sa.Column("end_price", sa.String(80), nullable=False),
        sa.Column("execution_lag_sessions", sa.Integer(), nullable=False),
        sa.Column("overlap_policy", sa.String(80), nullable=False),
        sa.Column("calendar_key", sa.String(40), nullable=False),
        sa.Column("included_member_roles", postgresql.JSONB(), nullable=False),
        _created(),
        _fk(
            "forward_return_version",
            "forward_return_definition_id",
            "data.forward_return_definition.forward_return_definition_id",
        ),
        _fk("forward_return_version", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("forward_return_version_id", name="pk_forward_return_version"),
        sa.UniqueConstraint("artifact_id", name="uq_forward_return_version_artifact"),
        sa.UniqueConstraint(
            "forward_return_definition_id",
            "version_number",
            name="uq_forward_return_version_definition_version",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_forward_return_version_positive"),
        sa.CheckConstraint(
            "frequency IN ('weekly', 'monthly')", name="ck_forward_return_version_frequency"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(included_member_roles) = 'array' "
            "AND jsonb_array_length(included_member_roles) >= 1",
            name="ck_forward_return_version_roles",
        ),
        schema="data",
    )
    op.create_table(
        "forward_return_dataset",
        _id("forward_return_dataset_id"),
        _id("artifact_id"),
        _id("forward_return_version_id"),
        _id("universe_version_id"),
        _id("data_bundle_version_id"),
        _id("market_dataset_publication_id"),
        _id("calendar_version_id"),
        _id("engine_version_id"),
        sa.Column("requested_start", sa.Date(), nullable=False),
        sa.Column("requested_end", sa.Date(), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        _created(),
        _fk("forward_return_dataset", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "forward_return_dataset",
            "forward_return_version_id",
            "data.forward_return_version.forward_return_version_id",
        ),
        _fk(
            "forward_return_dataset",
            "universe_version_id",
            "catalog.universe_version.universe_version_id",
        ),
        _fk(
            "forward_return_dataset",
            "data_bundle_version_id",
            "data.data_bundle_version.data_bundle_version_id",
        ),
        _fk(
            "forward_return_dataset",
            "market_dataset_publication_id",
            "data.dataset_publication.dataset_publication_id",
        ),
        _fk(
            "forward_return_dataset",
            "calendar_version_id",
            "catalog.calendar_version.calendar_version_id",
        ),
        _fk("forward_return_dataset", "engine_version_id", "ops.engine_version.engine_version_id"),
        sa.PrimaryKeyConstraint("forward_return_dataset_id", name="pk_forward_return_dataset"),
        sa.UniqueConstraint("artifact_id", name="uq_forward_return_dataset_artifact"),
        sa.UniqueConstraint(
            "forward_return_version_id",
            "universe_version_id",
            "data_bundle_version_id",
            "engine_version_id",
            "requested_start",
            "requested_end",
            name="uq_forward_return_dataset_context",
        ),
        sa.CheckConstraint(
            "requested_start <= coverage_start AND coverage_start <= coverage_end "
            "AND coverage_end <= requested_end",
            name="ck_forward_return_dataset_coverage",
        ),
        sa.CheckConstraint("row_count >= 1", name="ck_forward_return_dataset_rows"),
        schema="data",
    )
    op.create_table(
        "forward_return_value",
        _id("forward_return_dataset_id"),
        _id("asset_id"),
        sa.Column("decision_date", sa.Date(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("forward_return", sa.Numeric(28, 18), nullable=False),
        _fk(
            "forward_return_value",
            "forward_return_dataset_id",
            "data.forward_return_dataset.forward_return_dataset_id",
        ),
        _fk("forward_return_value", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint(
            "forward_return_dataset_id", "asset_id", "decision_date", name="pk_forward_return_value"
        ),
        sa.CheckConstraint(
            "decision_date < start_date AND start_date < end_date",
            name="ck_forward_return_value_dates",
        ),
        sa.CheckConstraint(
            "forward_return NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="ck_forward_return_value_finite",
        ),
        schema="data",
    )
    _guards()


def _guards() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_forward_return_definition_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.forward_return_definition
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_forward_return_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.forward_return_version
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_forward_return_dataset_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.forward_return_dataset
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();

        CREATE FUNCTION data.enforce_forward_return_child_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE dataset_id uuid; owner_artifact_id uuid;
        BEGIN
            dataset_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.forward_return_dataset_id ELSE NEW.forward_return_dataset_id END;
            SELECT artifact_id INTO owner_artifact_id FROM data.forward_return_dataset
            WHERE forward_return_dataset_id = dataset_id;
            PERFORM data.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_forward_return_value_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.forward_return_value
        FOR EACH ROW EXECUTE FUNCTION data.enforce_forward_return_child_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS data.enforce_forward_return_child_draft() CASCADE")
    op.drop_table("forward_return_value", schema="data")
    op.drop_table("forward_return_dataset", schema="data")
    op.drop_table("forward_return_version", schema="data")
    op.drop_table("forward_return_definition", schema="data")
