"""Create phase 6 factor diagnostics and performance metrics tables.

Revision ID: 20260731_0006
Revises: 20260731_0005
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0006"
down_revision: str | None = "20260731_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "metric_versions",
        sa.Column("metric_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_key", sa.String(100), nullable=False),
        sa.Column("methodology_hash", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("dependency_lock_hash", sa.String(64), nullable=False),
        sa.Column("git_commit", sa.String(64), nullable=False),
        sa.Column("python_version", sa.String(30), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("metric_version_id", name="pk_metric_versions"),
        sa.UniqueConstraint("version_key", name="uq_metric_versions_version_key"),
    )
    op.create_table(
        "factor_diagnostic_sets",
        sa.Column("diagnostic_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleaning_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factor_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factor_variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rebalance_frequency", sa.String(20), nullable=False),
        sa.Column("diagnostic_fingerprint", sa.String(64), nullable=False),
        sa.Column("period_count", sa.Integer(), nullable=False),
        sa.Column("valid_ic_count", sa.Integer(), nullable=False),
        sa.Column("undefined_ic_count", sa.Integer(), nullable=False),
        sa.Column("mean_rank_ic", sa.Numeric(30, 18)),
        sa.Column("positive_ic_ratio", sa.Numeric(30, 18)),
        sa.Column("mean_top_bottom_return_spread", sa.Numeric(30, 18), nullable=False),
        sa.Column("ic_summary_reason_code", sa.String(100)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), server_default="publishing", nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["metric_version_id"], ["metric_versions.metric_version_id"]
        ),
        sa.ForeignKeyConstraint(
            [
                "data_version_id",
                "cleaning_version_id",
                "factor_version_id",
                "strategy_version_id",
            ],
            [
                "signal_datasets.data_version_id",
                "signal_datasets.cleaning_version_id",
                "signal_datasets.factor_version_id",
                "signal_datasets.strategy_version_id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["factor_version_id", "factor_variant_id"],
            ["factor_variants.factor_version_id", "factor_variants.factor_variant_id"],
        ),
        sa.PrimaryKeyConstraint("diagnostic_set_id", name="pk_factor_diagnostic_sets"),
        sa.UniqueConstraint(
            "diagnostic_fingerprint", name="uq_factor_diagnostic_sets_fingerprint"
        ),
        sa.UniqueConstraint(
            "metric_version_id",
            "data_version_id",
            "cleaning_version_id",
            "factor_version_id",
            "strategy_version_id",
            "factor_variant_id",
            "rebalance_frequency",
            name="uq_factor_diagnostic_set_identity",
        ),
        sa.UniqueConstraint(
            "diagnostic_set_id",
            "metric_version_id",
            name="uq_factor_diagnostic_set_version_identity",
        ),
        sa.CheckConstraint(
            "rebalance_frequency IN ('weekly','monthly')", name="valid_frequency"
        ),
        sa.CheckConstraint("period_count > 0", name="positive_period_count"),
        sa.CheckConstraint(
            "period_count = valid_ic_count + undefined_ic_count", name="valid_ic_counts"
        ),
        sa.CheckConstraint(
            "mean_rank_ic IS NULL OR mean_rank_ic BETWEEN -1 AND 1",
            name="valid_mean_rank_ic",
        ),
        sa.CheckConstraint(
            "positive_ic_ratio IS NULL OR positive_ic_ratio BETWEEN 0 AND 1",
            name="valid_positive_ic_ratio",
        ),
        sa.CheckConstraint(
            "(mean_rank_ic IS NULL AND positive_ic_ratio IS NULL "
            "AND ic_summary_reason_code IS NOT NULL) OR "
            "(mean_rank_ic IS NOT NULL AND positive_ic_ratio IS NOT NULL "
            "AND ic_summary_reason_code IS NULL)",
            name="valid_ic_summary_state",
        ),
        sa.CheckConstraint(
            "status IN ('publishing','published')", name="valid_publication_status"
        ),
    )
    op.create_index(
        "ix_factor_diagnostic_sets_lookup",
        "factor_diagnostic_sets",
        ["factor_variant_id", "rebalance_frequency", "metric_version_id"],
    )
    op.create_table(
        "factor_diagnostic_periods",
        sa.Column("diagnostic_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("execution_date", sa.Date(), nullable=False),
        sa.Column("next_execution_date", sa.Date(), nullable=False),
        sa.Column("rank_ic", sa.Numeric(30, 18)),
        sa.Column("rank_ic_reason_code", sa.String(100)),
        sa.Column("top_bottom_return_spread", sa.Numeric(30, 18), nullable=False),
        sa.ForeignKeyConstraint(
            ["diagnostic_set_id"],
            ["factor_diagnostic_sets.diagnostic_set_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "diagnostic_set_id", "signal_date", name="pk_factor_diagnostic_periods"
        ),
        sa.CheckConstraint("signal_date < execution_date", name="signal_before_execution"),
        sa.CheckConstraint(
            "execution_date < next_execution_date", name="execution_before_next_execution"
        ),
        sa.CheckConstraint("rank_ic IS NULL OR rank_ic BETWEEN -1 AND 1", name="valid_rank_ic"),
        sa.CheckConstraint(
            "(rank_ic IS NULL AND rank_ic_reason_code IS NOT NULL) OR "
            "(rank_ic IS NOT NULL AND rank_ic_reason_code IS NULL)",
            name="valid_rank_ic_state",
        ),
    )
    op.create_table(
        "metric_publications",
        sa.Column("metric_publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("diagnostic_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_fingerprint", sa.String(64), nullable=False),
        sa.Column("input_manifest_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("metric_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default="publishing", nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["run_id"], ["backtest_runs.run_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["metric_version_id"], ["metric_versions.metric_version_id"]
        ),
        sa.ForeignKeyConstraint(
            ["diagnostic_set_id", "metric_version_id"],
            [
                "factor_diagnostic_sets.diagnostic_set_id",
                "factor_diagnostic_sets.metric_version_id",
            ],
        ),
        sa.PrimaryKeyConstraint("metric_publication_id", name="pk_metric_publications"),
        sa.UniqueConstraint("metric_fingerprint", name="uq_metric_publications_fingerprint"),
        sa.UniqueConstraint(
            "run_id", "metric_version_id", name="uq_metric_publication_run_version"
        ),
        sa.CheckConstraint("metric_count > 0", name="positive_metric_count"),
        sa.CheckConstraint(
            "status IN ('publishing','published')", name="valid_publication_status"
        ),
    )
    op.create_index(
        "ix_metric_publications_run_status",
        "metric_publications",
        ["run_id", "status"],
    )
    op.create_table(
        "performance_metrics",
        sa.Column("metric_publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("series_type", sa.String(50), nullable=False),
        sa.Column("return_basis", sa.String(30), nullable=False),
        sa.Column("metric_key", sa.String(100), nullable=False),
        sa.Column("metric_value", sa.Numeric(38, 18)),
        sa.Column("value_status", sa.String(20), nullable=False),
        sa.Column("reason_code", sa.String(100)),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(50), nullable=False),
        sa.ForeignKeyConstraint(
            ["metric_publication_id"],
            ["metric_publications.metric_publication_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "metric_publication_id",
            "series_type",
            "return_basis",
            "metric_key",
            name="pk_performance_metrics",
        ),
        sa.CheckConstraint(
            "series_type IN ('strategy','four_etf_equal_weight','spy_buy_hold',"
            "'strategy_vs_four_etf_equal_weight')",
            name="valid_series_type",
        ),
        sa.CheckConstraint(
            "return_basis IN ('gross','net','cost_independent')",
            name="valid_return_basis",
        ),
        sa.CheckConstraint(
            "value_status IN ('defined','undefined','not_applicable')",
            name="valid_value_status",
        ),
        sa.CheckConstraint(
            "(value_status = 'defined' AND metric_value IS NOT NULL AND reason_code IS NULL) OR "
            "(value_status IN ('undefined','not_applicable') AND metric_value IS NULL "
            "AND reason_code IS NOT NULL)",
            name="valid_metric_value_state",
        ),
        sa.CheckConstraint(
            "observation_count >= 0", name="nonnegative_observation_count"
        ),
        sa.CheckConstraint(
            "metric_value IS NULL OR metric_value NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="finite_metric_value",
        ),
    )
    op.create_index(
        "ix_performance_metrics_leaderboard",
        "performance_metrics",
        ["series_type", "return_basis", "metric_key", "value_status", "metric_value"],
    )

    op.execute(
        """
        CREATE FUNCTION prevent_completed_run_child_mutation() RETURNS trigger AS $$
        DECLARE
            affected_run_id uuid;
            parent_status text;
        BEGIN
            IF TG_OP IN ('DELETE', 'UPDATE') THEN
                affected_run_id := OLD.run_id;
            ELSE
                affected_run_id := NEW.run_id;
            END IF;
            SELECT status INTO parent_status
            FROM backtest_runs WHERE run_id = affected_run_id;
            IF parent_status = 'completed' THEN
                RAISE EXCEPTION 'completed backtest run results are immutable';
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.run_id <> OLD.run_id THEN
                SELECT status INTO parent_status
                FROM backtest_runs WHERE run_id = NEW.run_id;
                IF parent_status = 'completed' THEN
                    RAISE EXCEPTION 'completed backtest run results are immutable';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table_name in (
        "rebalance_executions",
        "trades",
        "daily_positions",
        "daily_nav",
        "benchmark_daily_nav",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_protect_completed_{table_name}
            BEFORE INSERT OR UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_completed_run_child_mutation();
            """
        )

    op.execute(
        """
        CREATE FUNCTION prevent_metric_version_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'metric versions are immutable';
            END IF;
            IF EXISTS (
                SELECT 1 FROM factor_diagnostic_sets
                WHERE metric_version_id = OLD.metric_version_id
            ) OR EXISTS (
                SELECT 1 FROM metric_publications
                WHERE metric_version_id = OLD.metric_version_id
            ) THEN
                RAISE EXCEPTION 'published metric versions are immutable';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_prevent_metric_version_mutation
        BEFORE UPDATE OR DELETE ON metric_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_metric_version_mutation();
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_published_metric_record_mutation() RETURNS trigger AS $$
        DECLARE
            parent_exists boolean;
            parent_status text;
            child_count bigint;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF TG_TABLE_NAME = 'factor_diagnostic_periods' THEN
                    SELECT status INTO parent_status
                    FROM factor_diagnostic_sets
                    WHERE diagnostic_set_id = NEW.diagnostic_set_id;
                ELSIF TG_TABLE_NAME = 'performance_metrics' THEN
                    SELECT status INTO parent_status
                    FROM metric_publications
                    WHERE metric_publication_id = NEW.metric_publication_id;
                ELSE
                    RAISE EXCEPTION 'unsupported immutable metric child table %', TG_TABLE_NAME;
                END IF;
                IF parent_status IS DISTINCT FROM 'publishing' THEN
                    RAISE EXCEPTION 'published metric results are immutable';
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'UPDATE' THEN
                IF TG_TABLE_NAME IN ('factor_diagnostic_sets', 'metric_publications') THEN
                    IF OLD.status = 'publishing'
                       AND NEW.status = 'published'
                       AND (to_jsonb(NEW) - 'status') = (to_jsonb(OLD) - 'status') THEN
                        IF TG_TABLE_NAME = 'factor_diagnostic_sets' THEN
                            SELECT count(*) INTO child_count
                            FROM factor_diagnostic_periods
                            WHERE diagnostic_set_id = NEW.diagnostic_set_id;
                            IF child_count <> NEW.period_count THEN
                                RAISE EXCEPTION 'diagnostic publication child count mismatch';
                            END IF;
                        ELSE
                            SELECT count(*) INTO child_count
                            FROM performance_metrics
                            WHERE metric_publication_id = NEW.metric_publication_id;
                            IF child_count <> NEW.metric_count THEN
                                RAISE EXCEPTION 'metric publication child count mismatch';
                            END IF;
                        END IF;
                        RETURN NEW;
                    END IF;
                END IF;
                RAISE EXCEPTION 'published metric results are immutable';
            END IF;

            IF TG_TABLE_NAME = 'factor_diagnostic_sets' THEN
                SELECT EXISTS (
                    SELECT 1 FROM signal_datasets
                    WHERE data_version_id = OLD.data_version_id
                      AND cleaning_version_id = OLD.cleaning_version_id
                      AND factor_version_id = OLD.factor_version_id
                      AND strategy_version_id = OLD.strategy_version_id
                ) INTO parent_exists;
            ELSIF TG_TABLE_NAME = 'factor_diagnostic_periods' THEN
                SELECT EXISTS (
                    SELECT 1 FROM factor_diagnostic_sets
                    WHERE diagnostic_set_id = OLD.diagnostic_set_id
                ) INTO parent_exists;
            ELSIF TG_TABLE_NAME = 'metric_publications' THEN
                SELECT EXISTS (
                    SELECT 1 FROM backtest_runs WHERE run_id = OLD.run_id
                ) INTO parent_exists;
            ELSIF TG_TABLE_NAME = 'performance_metrics' THEN
                SELECT EXISTS (
                    SELECT 1 FROM metric_publications
                    WHERE metric_publication_id = OLD.metric_publication_id
                ) INTO parent_exists;
            ELSE
                RAISE EXCEPTION 'unsupported immutable metric table %', TG_TABLE_NAME;
            END IF;

            IF parent_exists THEN
                RAISE EXCEPTION 'published metric results are immutable';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table_name in (
        "factor_diagnostic_sets",
        "metric_publications",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_protect_published_{table_name}
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_published_metric_record_mutation();
            """
        )
    for table_name in ("factor_diagnostic_periods", "performance_metrics"):
        op.execute(
            f"""
            CREATE TRIGGER trg_protect_published_{table_name}
            BEFORE INSERT OR UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_published_metric_record_mutation();
            """
        )


def downgrade() -> None:
    for table_name in (
        "performance_metrics",
        "metric_publications",
        "factor_diagnostic_periods",
        "factor_diagnostic_sets",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_protect_published_{table_name} ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS prevent_published_metric_record_mutation")
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_metric_version_mutation ON metric_versions")
    op.execute("DROP FUNCTION IF EXISTS prevent_metric_version_mutation")
    for table_name in (
        "benchmark_daily_nav",
        "daily_nav",
        "daily_positions",
        "trades",
        "rebalance_executions",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_protect_completed_{table_name} ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS prevent_completed_run_child_mutation")
    op.drop_index("ix_performance_metrics_leaderboard", table_name="performance_metrics")
    op.drop_table("performance_metrics")
    op.drop_index("ix_metric_publications_run_status", table_name="metric_publications")
    op.drop_table("metric_publications")
    op.drop_table("factor_diagnostic_periods")
    op.drop_index("ix_factor_diagnostic_sets_lookup", table_name="factor_diagnostic_sets")
    op.drop_table("factor_diagnostic_sets")
    op.drop_table("metric_versions")
