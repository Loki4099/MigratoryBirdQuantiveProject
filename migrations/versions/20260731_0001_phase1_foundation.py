"""Create phase 1 version, contract, experiment, run, and archive tables.

Revision ID: 20260731_0001
Revises:
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_column(name: str, *, primary_key: bool = False, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name, postgresql.UUID(as_uuid=True), primary_key=primary_key, nullable=nullable
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "data_versions",
        _uuid_column("data_version_id", primary_key=True),
        sa.Column("version_key", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("request_parameters", postgresql.JSONB(), nullable=False),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.CheckConstraint("coverage_start <= coverage_end", name="coverage_dates_ordered"),
        sa.UniqueConstraint("version_key", name="uq_data_versions_version_key"),
        sa.UniqueConstraint("content_hash", name="uq_data_versions_content_hash"),
    )
    op.create_table(
        "cleaning_versions",
        _uuid_column("cleaning_version_id", primary_key=True),
        sa.Column("version_key", sa.String(100), nullable=False),
        sa.Column("rules_hash", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.UniqueConstraint("version_key", name="uq_cleaning_versions_version_key"),
    )
    op.create_table(
        "factor_versions",
        _uuid_column("factor_version_id", primary_key=True),
        sa.Column("version_key", sa.String(100), nullable=False),
        sa.Column("registry_hash", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        _created_at(),
        sa.UniqueConstraint("version_key", name="uq_factor_versions_version_key"),
    )
    op.create_table(
        "strategy_versions",
        _uuid_column("strategy_version_id", primary_key=True),
        sa.Column("version_key", sa.String(100), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.UniqueConstraint("version_key", name="uq_strategy_versions_version_key"),
    )
    op.create_table(
        "engine_versions",
        _uuid_column("engine_version_id", primary_key=True),
        sa.Column("version_key", sa.String(100), nullable=False),
        sa.Column("git_commit", sa.String(64), nullable=False),
        sa.Column("dependency_lock_hash", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("python_version", sa.String(30), nullable=False),
        _created_at(),
        sa.UniqueConstraint("version_key", name="uq_engine_versions_version_key"),
    )
    op.create_table(
        "data_contracts",
        _uuid_column("data_contract_id", primary_key=True),
        sa.Column("layer", sa.String(30), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("schema_version", sa.String(50), nullable=False),
        sa.Column("contract_hash", sa.String(64), nullable=False),
        sa.Column("contract_body", postgresql.JSONB(), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _created_at(),
        sa.UniqueConstraint("layer", "name", "schema_version", name="uq_data_contract_identity"),
        sa.UniqueConstraint("contract_hash", name="uq_data_contract_hash"),
    )
    op.create_table(
        "experiments",
        _uuid_column("experiment_id", primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("system_version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('draft','running','completed','archived')",
            name="valid_status",
        ),
    )
    op.create_index("ix_experiments_system_version", "experiments", ["system_version"])
    op.create_table(
        "version_archives",
        _uuid_column("archive_id", primary_key=True),
        sa.Column("system_version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("archive_uri", sa.Text(), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("restore_tested_at", sa.DateTime(timezone=True)),
        sa.Column("failure_message", sa.Text()),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('pending','verified','restore_tested','failed')",
            name="valid_status",
        ),
        sa.UniqueConstraint("system_version", name="uq_version_archives_system_version"),
        sa.UniqueConstraint("manifest_hash", name="uq_version_archives_manifest_hash"),
    )
    op.create_table(
        "backtest_runs",
        _uuid_column("run_id", primary_key=True),
        _uuid_column("experiment_id"),
        _uuid_column("data_version_id"),
        _uuid_column("cleaning_version_id"),
        _uuid_column("factor_version_id"),
        _uuid_column("strategy_version_id"),
        _uuid_column("engine_version_id"),
        sa.Column("run_fingerprint", sa.String(64), nullable=False),
        sa.Column("factor_variant_key", sa.String(150), nullable=False),
        sa.Column("warmup_start_date", sa.Date(), nullable=False),
        sa.Column("official_signal_start_date", sa.Date(), nullable=False),
        sa.Column("first_execution_date", sa.Date(), nullable=False),
        sa.Column("official_end_date", sa.Date(), nullable=False),
        sa.Column("rebalance_frequency", sa.String(20), nullable=False),
        sa.Column("strategy_template", sa.String(30), nullable=False),
        sa.Column("transaction_cost_bps", sa.Numeric(8, 4), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_type", sa.String(200)),
        sa.Column("error_message", sa.Text()),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["experiment_id"], ["experiments.experiment_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["data_version_id"], ["data_versions.data_version_id"]),
        sa.ForeignKeyConstraint(["cleaning_version_id"], ["cleaning_versions.cleaning_version_id"]),
        sa.ForeignKeyConstraint(["factor_version_id"], ["factor_versions.factor_version_id"]),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["strategy_versions.strategy_version_id"]),
        sa.ForeignKeyConstraint(["engine_version_id"], ["engine_versions.engine_version_id"]),
        sa.UniqueConstraint("run_fingerprint", name="uq_backtest_runs_run_fingerprint"),
        sa.CheckConstraint(
            "warmup_start_date <= official_signal_start_date",
            name="warmup_before_signal",
        ),
        sa.CheckConstraint(
            "official_signal_start_date <= first_execution_date",
            name="signal_before_execution",
        ),
        sa.CheckConstraint(
            "first_execution_date <= official_end_date",
            name="execution_before_end",
        ),
        sa.CheckConstraint("transaction_cost_bps >= 0", name="nonnegative_cost"),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="valid_status",
        ),
        sa.CheckConstraint("rebalance_frequency IN ('weekly','monthly')", name="valid_frequency"),
        sa.CheckConstraint(
            "strategy_template IN ('cross_sectional','trend_filtered')",
            name="valid_template",
        ),
    )
    op.create_index(
        "ix_backtest_runs_experiment_status",
        "backtest_runs",
        ["experiment_id", "status"],
    )
    op.create_table(
        "run_events",
        _uuid_column("run_event_id", primary_key=True),
        _uuid_column("run_id"),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["run_id"], ["backtest_runs.run_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "sequence_no", name="uq_run_event_sequence"),
        sa.CheckConstraint("sequence_no >= 0", name="nonnegative_sequence"),
    )

    op.execute(
        """
        CREATE FUNCTION prevent_completed_run_update() RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'completed' THEN
                RAISE EXCEPTION 'completed backtest runs are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_prevent_completed_run_update
        BEFORE UPDATE ON backtest_runs
        FOR EACH ROW EXECUTE FUNCTION prevent_completed_run_update();
        """
    )
    op.execute(
        """
        CREATE FUNCTION require_restore_tested_archive_for_completed_run_delete()
        RETURNS trigger AS $$
        DECLARE run_system_version text;
        BEGIN
            IF OLD.status <> 'completed' THEN
                RETURN OLD;
            END IF;
            SELECT system_version INTO run_system_version
            FROM experiments WHERE experiment_id = OLD.experiment_id;
            IF NOT EXISTS (
                SELECT 1 FROM version_archives
                WHERE system_version = run_system_version
                  AND status = 'restore_tested'
                  AND verified_at IS NOT NULL
                  AND restore_tested_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION '%',
                    'completed run deletion requires a verified, '
                    || 'restore-tested version archive';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_require_archive_before_completed_run_delete
        BEFORE DELETE ON backtest_runs
        FOR EACH ROW EXECUTE FUNCTION require_restore_tested_archive_for_completed_run_delete();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_require_archive_before_completed_run_delete ON backtest_runs"
    )
    op.execute("DROP FUNCTION IF EXISTS require_restore_tested_archive_for_completed_run_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_completed_run_update ON backtest_runs")
    op.execute("DROP FUNCTION IF EXISTS prevent_completed_run_update")
    op.drop_table("run_events")
    op.drop_index("ix_backtest_runs_experiment_status", table_name="backtest_runs")
    op.drop_table("backtest_runs")
    op.drop_table("version_archives")
    op.drop_index("ix_experiments_system_version", table_name="experiments")
    op.drop_table("experiments")
    op.drop_table("data_contracts")
    op.drop_table("engine_versions")
    op.drop_table("strategy_versions")
    op.drop_table("factor_versions")
    op.drop_table("cleaning_versions")
    op.drop_table("data_versions")
