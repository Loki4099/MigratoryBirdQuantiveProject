# ruff: noqa: E501
"""Add versioned interval performance results.

Revision ID: 20260804_22_v02_performance
Revises: 20260804_21_v02_benchmark_path
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_22_v02_performance"
down_revision: str | None = "20260804_21_v02_benchmark_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
NUMERIC = sa.Numeric(38, 24)


def _id(name: str) -> sa.Column[object]:
    return sa.Column(name, UUID, nullable=False)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table[:16]}_{column[:24]}", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "performance_metric_catalog",
        _id("performance_metric_catalog_id"),
        _id("artifact_id"),
        sa.Column("catalog_key", sa.String(120), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("methodology", sa.String(120), nullable=False),
        sa.Column("metric_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("metric_catalog", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint(
            "performance_metric_catalog_id", name="pk_performance_metric_catalog"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_performance_metric_catalog_artifact"),
        sa.UniqueConstraint(
            "catalog_key", "version_number", name="uq_performance_metric_catalog_version"
        ),
        sa.CheckConstraint(
            "version_number >= 1 AND metric_count >= 1", name="ck_performance_metric_catalog_counts"
        ),
        schema="experiment",
    )
    op.create_table(
        "performance_metric_definition",
        _id("performance_metric_definition_id"),
        _id("performance_metric_catalog_id"),
        sa.Column("metric_scope", sa.String(20), nullable=False),
        sa.Column("metric_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _fk(
            "metric_definition",
            "performance_metric_catalog_id",
            "experiment.performance_metric_catalog.performance_metric_catalog_id",
        ),
        sa.PrimaryKeyConstraint(
            "performance_metric_definition_id", name="pk_performance_metric_definition"
        ),
        sa.UniqueConstraint(
            "performance_metric_catalog_id",
            "metric_scope",
            "metric_key",
            name="uq_performance_metric_definition_key",
        ),
        sa.UniqueConstraint(
            "performance_metric_catalog_id",
            "ordinal",
            name="uq_performance_metric_definition_ordinal",
        ),
        sa.CheckConstraint(
            "metric_scope IN ('absolute', 'relative')",
            name="ck_performance_metric_definition_scope",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_performance_metric_definition_ordinal"),
        schema="experiment",
    )
    op.create_table(
        "interval_performance_result",
        _id("interval_performance_result_id"),
        _id("artifact_id"),
        _id("strategy_net_cost_path_id"),
        _id("benchmark_net_cost_path_id"),
        _id("performance_metric_catalog_id"),
        _id("engine_version_id"),
        sa.Column("template_key", sa.String(40), nullable=False),
        sa.Column("initialization_policy", sa.String(20), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("requested_start", sa.Date(), nullable=False),
        sa.Column("requested_end", sa.Date(), nullable=False),
        sa.Column("resolved_start", sa.Date()),
        sa.Column("resolved_end", sa.Date()),
        sa.Column("normalization_nav_date", sa.Date()),
        sa.Column("availability_status", sa.String(20), nullable=False),
        sa.Column("exclusion_reason", sa.String(100)),
        sa.Column("quality_status", sa.String(40), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("metric_value_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("interval_result", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "interval_result",
            "strategy_net_cost_path_id",
            "experiment.net_cost_path.net_cost_path_id",
        ),
        _fk(
            "interval_result",
            "benchmark_net_cost_path_id",
            "experiment.net_cost_path.net_cost_path_id",
        ),
        _fk(
            "interval_result",
            "performance_metric_catalog_id",
            "experiment.performance_metric_catalog.performance_metric_catalog_id",
        ),
        _fk("interval_result", "engine_version_id", "ops.engine_version.engine_version_id"),
        sa.PrimaryKeyConstraint(
            "interval_performance_result_id", name="pk_interval_performance_result"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_interval_performance_result_artifact"),
        sa.UniqueConstraint(
            "strategy_net_cost_path_id",
            "benchmark_net_cost_path_id",
            "performance_metric_catalog_id",
            "engine_version_id",
            "template_key",
            "initialization_policy",
            "as_of_date",
            "requested_start",
            "requested_end",
            name="uq_interval_performance_exact_inputs",
        ),
        sa.CheckConstraint(
            "template_key IN ('full_history','trailing_10_years','trailing_5_years','trailing_3_years','trailing_1_year','custom')",
            name="ck_interval_performance_template",
        ),
        sa.CheckConstraint(
            "initialization_policy IN ('carry_in','fresh_start')",
            name="ck_interval_performance_initialization",
        ),
        sa.CheckConstraint(
            "requested_start <= requested_end AND requested_end <= as_of_date",
            name="ck_interval_performance_request",
        ),
        sa.CheckConstraint(
            "availability_status IN ('eligible','excluded')",
            name="ck_interval_performance_availability",
        ),
        sa.CheckConstraint(
            "quality_status IN ('normal','short_sample_warning','very_short_sample_warning','not_applicable')",
            name="ck_interval_performance_quality",
        ),
        sa.CheckConstraint(
            "observation_count >= 0 AND metric_value_count >= 0",
            name="ck_interval_performance_counts",
        ),
        sa.CheckConstraint(
            "(availability_status = 'eligible' AND exclusion_reason IS NULL AND resolved_start IS NOT NULL AND resolved_end IS NOT NULL AND resolved_start <= resolved_end AND observation_count >= 1 AND metric_value_count >= 1) OR (availability_status = 'excluded' AND exclusion_reason IS NOT NULL AND resolved_start IS NULL AND resolved_end IS NULL AND normalization_nav_date IS NULL AND quality_status = 'not_applicable' AND observation_count = 0 AND metric_value_count = 0)",
            name="ck_interval_performance_state",
        ),
        schema="experiment",
    )
    op.create_table(
        "performance_metric_value",
        _id("interval_performance_result_id"),
        _id("performance_metric_definition_id"),
        sa.Column("series_role", sa.String(20), nullable=False),
        sa.Column("metric_value", NUMERIC),
        sa.Column("value_status", sa.String(20), nullable=False),
        sa.Column("reason_code", sa.String(100)),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        _fk(
            "metric_value",
            "interval_performance_result_id",
            "experiment.interval_performance_result.interval_performance_result_id",
        ),
        _fk(
            "metric_value",
            "performance_metric_definition_id",
            "experiment.performance_metric_definition.performance_metric_definition_id",
        ),
        sa.PrimaryKeyConstraint(
            "interval_performance_result_id",
            "series_role",
            "performance_metric_definition_id",
            name="pk_performance_metric_value",
        ),
        sa.CheckConstraint(
            "series_role IN ('strategy','benchmark','relative')",
            name="ck_performance_metric_value_role",
        ),
        sa.CheckConstraint(
            "value_status IN ('defined','undefined')", name="ck_performance_metric_value_status"
        ),
        sa.CheckConstraint(
            "(value_status = 'defined' AND metric_value IS NOT NULL AND reason_code IS NULL) OR (value_status = 'undefined' AND metric_value IS NULL AND reason_code IS NOT NULL)",
            name="ck_performance_metric_value_state",
        ),
        sa.CheckConstraint("observation_count >= 0", name="ck_performance_metric_value_count"),
        schema="experiment",
    )
    op.create_index(
        "ix_interval_performance_comparison",
        "interval_performance_result",
        ["template_key", "as_of_date", "availability_status", "quality_status"],
        schema="experiment",
    )
    op.create_index(
        "ix_performance_metric_lookup",
        "performance_metric_value",
        ["series_role", "performance_metric_definition_id", "value_status", "metric_value"],
        schema="experiment",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute("""
    CREATE FUNCTION experiment.enforce_performance_owner_draft() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE owner_artifact uuid;
    BEGIN
      IF TG_TABLE_NAME = 'performance_metric_catalog' OR TG_TABLE_NAME = 'interval_performance_result' THEN
        owner_artifact := CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END;
      ELSIF TG_TABLE_NAME = 'performance_metric_definition' THEN
        SELECT artifact_id INTO owner_artifact FROM experiment.performance_metric_catalog WHERE performance_metric_catalog_id = COALESCE(NEW.performance_metric_catalog_id, OLD.performance_metric_catalog_id);
      ELSE
        SELECT result.artifact_id INTO owner_artifact FROM experiment.interval_performance_result result WHERE result.interval_performance_result_id = COALESCE(NEW.interval_performance_result_id, OLD.interval_performance_result_id);
      END IF;
      PERFORM data.assert_artifact_draft(owner_artifact);
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END; $$;
    CREATE TRIGGER trg_performance_catalog_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.performance_metric_catalog FOR EACH ROW EXECUTE FUNCTION experiment.enforce_performance_owner_draft();
    CREATE TRIGGER trg_performance_definition_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.performance_metric_definition FOR EACH ROW EXECUTE FUNCTION experiment.enforce_performance_owner_draft();
    CREATE TRIGGER trg_interval_performance_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.interval_performance_result FOR EACH ROW EXECUTE FUNCTION experiment.enforce_performance_owner_draft();
    CREATE TRIGGER trg_performance_value_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.performance_metric_value FOR EACH ROW EXECUTE FUNCTION experiment.enforce_performance_owner_draft();

    CREATE FUNCTION experiment.validate_interval_performance() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE strategy_row record; benchmark_row record; engine_key_value text; catalog_status text;
    BEGIN
      IF TG_OP <> 'DELETE' THEN
        SELECT net.cost_scenario_id, gross.data_bundle_version_id, gross.effective_nav_start, gross.effective_nav_end, target.portfolio_target_path_id
          INTO strategy_row FROM experiment.net_cost_path net JOIN experiment.gross_portfolio_path gross ON gross.gross_portfolio_path_id = net.gross_portfolio_path_id JOIN strategy.portfolio_target_path target ON target.portfolio_target_path_id = gross.portfolio_target_path_id JOIN lineage.artifact artifact ON artifact.artifact_id = net.artifact_id AND artifact.status = 'published' WHERE net.net_cost_path_id = NEW.strategy_net_cost_path_id AND target.target_type = 'model_strategy';
        SELECT net.cost_scenario_id, gross.data_bundle_version_id, gross.effective_nav_start, gross.effective_nav_end, target.reference_portfolio_target_path_id
          INTO benchmark_row FROM experiment.net_cost_path net JOIN experiment.gross_portfolio_path gross ON gross.gross_portfolio_path_id = net.gross_portfolio_path_id JOIN strategy.benchmark_target_path target ON target.portfolio_target_path_id = gross.portfolio_target_path_id JOIN lineage.artifact artifact ON artifact.artifact_id = net.artifact_id AND artifact.status = 'published' WHERE net.net_cost_path_id = NEW.benchmark_net_cost_path_id;
        SELECT definition.engine_key INTO engine_key_value FROM ops.engine_version version JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id AND artifact.status = 'published' WHERE version.engine_version_id = NEW.engine_version_id;
        SELECT artifact.status INTO catalog_status FROM experiment.performance_metric_catalog catalog JOIN lineage.artifact artifact ON artifact.artifact_id = catalog.artifact_id WHERE catalog.performance_metric_catalog_id = NEW.performance_metric_catalog_id;
        IF strategy_row.portfolio_target_path_id IS NULL OR benchmark_row.reference_portfolio_target_path_id IS NULL OR strategy_row.cost_scenario_id <> benchmark_row.cost_scenario_id OR strategy_row.data_bundle_version_id <> benchmark_row.data_bundle_version_id OR strategy_row.effective_nav_start <> benchmark_row.effective_nav_start OR strategy_row.effective_nav_end <> benchmark_row.effective_nav_end OR strategy_row.portfolio_target_path_id <> benchmark_row.reference_portfolio_target_path_id THEN RAISE EXCEPTION 'Interval performance requires same-context strategy and benchmark Net Paths'; END IF;
        IF engine_key_value <> 'performance_engine' OR catalog_status <> 'published' THEN RAISE EXCEPTION 'Interval performance requires published metric catalog and Performance engine'; END IF;
      END IF;
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END; $$;
    CREATE TRIGGER trg_validate_interval_performance BEFORE INSERT OR UPDATE ON experiment.interval_performance_result FOR EACH ROW EXECUTE FUNCTION experiment.validate_interval_performance();
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS experiment.validate_interval_performance() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS experiment.enforce_performance_owner_draft() CASCADE")
    op.drop_index(
        "ix_performance_metric_lookup", table_name="performance_metric_value", schema="experiment"
    )
    op.drop_index(
        "ix_interval_performance_comparison",
        table_name="interval_performance_result",
        schema="experiment",
    )
    op.drop_table("performance_metric_value", schema="experiment")
    op.drop_table("interval_performance_result", schema="experiment")
    op.drop_table("performance_metric_definition", schema="experiment")
    op.drop_table("performance_metric_catalog", schema="experiment")
