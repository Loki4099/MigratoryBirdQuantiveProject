# ruff: noqa: E501
"""Add immutable warm-up policies and comparison cohorts.

Revision ID: 20260804_25_v02_cohort
Revises: 20260804_24_v02_exp_result
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_25_v02_cohort"
down_revision: str | None = "20260804_24_v02_exp_result"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _id(name: str) -> sa.Column[object]:
    return sa.Column(name, UUID, nullable=False)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table[:16]}_{column[:24]}", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "warmup_policy_version",
        _id("warmup_policy_version_id"),
        _id("artifact_id"),
        sa.Column("policy_key", sa.String(120), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("resolution_method", sa.String(100), nullable=False),
        sa.Column("required_observations", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("warmup_policy", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("warmup_policy_version_id", name="pk_warmup_policy_version"),
        sa.UniqueConstraint("artifact_id", name="uq_warmup_policy_artifact"),
        sa.UniqueConstraint("policy_key", "version_number", name="uq_warmup_policy_identity"),
        sa.CheckConstraint(
            "version_number >= 1 AND required_observations >= 0", name="ck_warmup_policy_values"
        ),
        sa.CheckConstraint(
            "resolution_method = 'dependency_max_required_history'", name="ck_warmup_policy_method"
        ),
        schema="experiment",
    )
    op.create_table(
        "comparison_cohort_version",
        _id("comparison_cohort_version_id"),
        _id("artifact_id"),
        _id("warmup_policy_version_id"),
        _id("universe_version_id"),
        _id("data_bundle_version_id"),
        _id("eligibility_snapshot_id"),
        _id("execution_policy_version_id"),
        _id("reserve_return_model_version_id"),
        _id("benchmark_version_id"),
        _id("cost_scenario_id"),
        _id("performance_metric_catalog_id"),
        _id("accounting_engine_version_id"),
        _id("benchmark_engine_version_id"),
        _id("performance_engine_version_id"),
        sa.Column("cohort_key", sa.String(140), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("context_fingerprint", sa.String(64), nullable=False),
        sa.Column("template_key", sa.String(40), nullable=False),
        sa.Column("initialization_policy", sa.String(20), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("common_data_ready_date", sa.Date(), nullable=False),
        sa.Column("common_simulation_start", sa.Date(), nullable=False),
        sa.Column("common_metric_start", sa.Date(), nullable=False),
        sa.Column("common_metric_end", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("comparison_cohort", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "comparison_cohort",
            "warmup_policy_version_id",
            "experiment.warmup_policy_version.warmup_policy_version_id",
        ),
        _fk(
            "comparison_cohort",
            "universe_version_id",
            "catalog.universe_version.universe_version_id",
        ),
        _fk(
            "comparison_cohort",
            "data_bundle_version_id",
            "data.data_bundle_version.data_bundle_version_id",
        ),
        _fk(
            "comparison_cohort",
            "eligibility_snapshot_id",
            "catalog.eligibility_snapshot.eligibility_snapshot_id",
        ),
        _fk(
            "comparison_cohort",
            "execution_policy_version_id",
            "ops.execution_policy_version.execution_policy_version_id",
        ),
        _fk(
            "comparison_cohort",
            "reserve_return_model_version_id",
            "experiment.reserve_return_model_version.reserve_return_model_version_id",
        ),
        _fk(
            "comparison_cohort",
            "benchmark_version_id",
            "experiment.benchmark_version.benchmark_version_id",
        ),
        _fk("comparison_cohort", "cost_scenario_id", "experiment.cost_scenario.cost_scenario_id"),
        _fk(
            "comparison_cohort",
            "performance_metric_catalog_id",
            "experiment.performance_metric_catalog.performance_metric_catalog_id",
        ),
        _fk(
            "comparison_cohort",
            "accounting_engine_version_id",
            "ops.engine_version.engine_version_id",
        ),
        _fk(
            "comparison_cohort",
            "benchmark_engine_version_id",
            "ops.engine_version.engine_version_id",
        ),
        _fk(
            "comparison_cohort",
            "performance_engine_version_id",
            "ops.engine_version.engine_version_id",
        ),
        sa.PrimaryKeyConstraint(
            "comparison_cohort_version_id", name="pk_comparison_cohort_version"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_comparison_cohort_artifact"),
        sa.UniqueConstraint("cohort_key", "version_number", name="uq_comparison_cohort_identity"),
        sa.UniqueConstraint(
            "context_fingerprint", "version_number", name="uq_comparison_context_version"
        ),
        sa.CheckConstraint(
            "context_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_comparison_context_hash"
        ),
        sa.CheckConstraint(
            "version_number >= 1 AND member_count >= 1", name="ck_comparison_cohort_counts"
        ),
        sa.CheckConstraint(
            "initialization_policy = 'carry_in'", name="ck_comparison_cohort_initialization"
        ),
        sa.CheckConstraint("currency = 'USD'", name="ck_comparison_cohort_currency"),
        sa.CheckConstraint(
            "common_data_ready_date <= common_simulation_start AND common_simulation_start <= common_metric_start AND common_metric_start <= common_metric_end AND common_metric_end <= as_of_date",
            name="ck_comparison_cohort_dates",
        ),
        schema="experiment",
    )
    op.create_table(
        "comparison_cohort_member",
        _id("comparison_cohort_version_id"),
        _id("result_publication_id"),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _fk(
            "comparison_member",
            "comparison_cohort_version_id",
            "experiment.comparison_cohort_version.comparison_cohort_version_id",
        ),
        _fk(
            "comparison_member",
            "result_publication_id",
            "experiment.result_publication.result_publication_id",
        ),
        sa.PrimaryKeyConstraint(
            "comparison_cohort_version_id",
            "result_publication_id",
            name="pk_comparison_cohort_member",
        ),
        sa.UniqueConstraint(
            "comparison_cohort_version_id", "ordinal", name="uq_comparison_cohort_member_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_comparison_cohort_member_ordinal"),
        schema="experiment",
    )
    op.create_index(
        "ix_comparison_cohort_context",
        "comparison_cohort_version",
        [
            "template_key",
            "as_of_date",
            "common_metric_start",
            "common_metric_end",
            "cost_scenario_id",
        ],
        schema="experiment",
    )
    op.execute("""
    CREATE FUNCTION experiment.enforce_cohort_owner_draft() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE owner_artifact uuid; cohort_row record; member_row record;
    BEGIN
      IF TG_TABLE_NAME = 'comparison_cohort_member' THEN
        SELECT artifact_id INTO owner_artifact FROM experiment.comparison_cohort_version
        WHERE comparison_cohort_version_id = COALESCE(NEW.comparison_cohort_version_id, OLD.comparison_cohort_version_id);
        IF TG_OP <> 'DELETE' THEN
          SELECT * INTO cohort_row FROM experiment.comparison_cohort_version
          WHERE comparison_cohort_version_id = NEW.comparison_cohort_version_id;
          SELECT publication.availability_status, result_artifact.status AS result_status,
                 target.universe_version_id, target.data_bundle_version_id,
                 target.eligibility_snapshot_id, strategy_gross.execution_policy_version_id,
                 strategy_gross.reserve_return_model_version_id,
                 specification.benchmark_version_id, specification.cost_scenario_id,
                 specification.performance_metric_catalog_id,
                 specification.accounting_engine_version_id,
                 specification.benchmark_engine_version_id,
                 specification.performance_engine_version_id,
                 specification.template_key, specification.initialization_policy,
                 specification.as_of_date,
                 ready.common_data_ready_date,
                 strategy_gross.effective_nav_start AS common_simulation_start,
                 interval.resolved_start AS common_metric_start,
                 interval.resolved_end AS common_metric_end,
                 benchmark_definition.category AS benchmark_category
          INTO member_row
          FROM experiment.result_publication publication
          JOIN lineage.artifact result_artifact ON result_artifact.artifact_id = publication.artifact_id
          JOIN experiment.experiment_specification specification ON specification.experiment_specification_id = publication.experiment_specification_id
          JOIN experiment.interval_performance_result interval ON interval.interval_performance_result_id = publication.interval_performance_result_id
          JOIN experiment.net_cost_path strategy_net ON strategy_net.net_cost_path_id = interval.strategy_net_cost_path_id
          JOIN experiment.gross_portfolio_path strategy_gross ON strategy_gross.gross_portfolio_path_id = strategy_net.gross_portfolio_path_id
          JOIN strategy.portfolio_target_path target ON target.portfolio_target_path_id = strategy_gross.portfolio_target_path_id
          JOIN experiment.benchmark_version benchmark ON benchmark.benchmark_version_id = specification.benchmark_version_id
          JOIN experiment.benchmark_definition benchmark_definition ON benchmark_definition.benchmark_definition_id = benchmark.benchmark_definition_id
          JOIN LATERAL (
            SELECT max(item.data_ready_date) AS common_data_ready_date
            FROM catalog.eligibility_item item
            WHERE item.eligibility_snapshot_id = target.eligibility_snapshot_id
              AND item.role = 'candidate' AND item.is_eligible
          ) ready ON true
          WHERE publication.result_publication_id = NEW.result_publication_id;
          IF member_row.result_status <> 'published' OR member_row.availability_status <> 'eligible'
             OR member_row.benchmark_category <> 'product_primary'
             OR member_row.universe_version_id IS DISTINCT FROM cohort_row.universe_version_id
             OR member_row.data_bundle_version_id IS DISTINCT FROM cohort_row.data_bundle_version_id
             OR member_row.eligibility_snapshot_id IS DISTINCT FROM cohort_row.eligibility_snapshot_id
             OR member_row.execution_policy_version_id IS DISTINCT FROM cohort_row.execution_policy_version_id
             OR member_row.reserve_return_model_version_id IS DISTINCT FROM cohort_row.reserve_return_model_version_id
             OR member_row.benchmark_version_id IS DISTINCT FROM cohort_row.benchmark_version_id
             OR member_row.cost_scenario_id IS DISTINCT FROM cohort_row.cost_scenario_id
             OR member_row.performance_metric_catalog_id IS DISTINCT FROM cohort_row.performance_metric_catalog_id
             OR member_row.accounting_engine_version_id IS DISTINCT FROM cohort_row.accounting_engine_version_id
             OR member_row.benchmark_engine_version_id IS DISTINCT FROM cohort_row.benchmark_engine_version_id
             OR member_row.performance_engine_version_id IS DISTINCT FROM cohort_row.performance_engine_version_id
             OR member_row.template_key IS DISTINCT FROM cohort_row.template_key
             OR member_row.initialization_policy IS DISTINCT FROM cohort_row.initialization_policy
             OR member_row.as_of_date IS DISTINCT FROM cohort_row.as_of_date
             OR member_row.common_data_ready_date IS DISTINCT FROM cohort_row.common_data_ready_date
             OR member_row.common_simulation_start IS DISTINCT FROM cohort_row.common_simulation_start
             OR member_row.common_metric_start IS DISTINCT FROM cohort_row.common_metric_start
             OR member_row.common_metric_end IS DISTINCT FROM cohort_row.common_metric_end
          THEN RAISE EXCEPTION 'Comparison Cohort member does not match strict context'; END IF;
        END IF;
      ELSE owner_artifact := CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END;
      END IF;
      PERFORM data.assert_artifact_draft(owner_artifact);
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END; $$;
    CREATE TRIGGER trg_warmup_policy_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.warmup_policy_version FOR EACH ROW EXECUTE FUNCTION experiment.enforce_cohort_owner_draft();
    CREATE TRIGGER trg_comparison_cohort_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.comparison_cohort_version FOR EACH ROW EXECUTE FUNCTION experiment.enforce_cohort_owner_draft();
    CREATE TRIGGER trg_comparison_member_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.comparison_cohort_member FOR EACH ROW EXECUTE FUNCTION experiment.enforce_cohort_owner_draft();
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS experiment.enforce_cohort_owner_draft() CASCADE")
    op.drop_index(
        "ix_comparison_cohort_context", table_name="comparison_cohort_version", schema="experiment"
    )
    op.drop_table("comparison_cohort_member", schema="experiment")
    op.drop_table("comparison_cohort_version", schema="experiment")
    op.drop_table("warmup_policy_version", schema="experiment")
