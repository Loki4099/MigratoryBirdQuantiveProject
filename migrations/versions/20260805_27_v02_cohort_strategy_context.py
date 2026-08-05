# ruff: noqa: E501
"""Make target K and rebalance frequency explicit comparison-cohort context.

Revision ID: 20260805_27_v02_cohort_ctx
Revises: 20260805_26_v02_backup
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_27_v02_cohort_ctx"
down_revision: str | None = "20260805_26_v02_backup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "comparison_cohort_version",
        sa.Column("target_k", sa.SmallInteger(), nullable=True),
        schema="experiment",
    )
    op.add_column(
        "comparison_cohort_version",
        sa.Column("frequency", sa.String(12), nullable=True),
        schema="experiment",
    )
    op.execute(
        "ALTER TABLE experiment.comparison_cohort_version "
        "DISABLE TRIGGER trg_comparison_cohort_draft"
    )
    op.execute("""
        UPDATE experiment.comparison_cohort_version cohort
        SET target_k = context.target_k, frequency = context.frequency
        FROM (
            SELECT member.comparison_cohort_version_id,
                   min(variant.target_k) AS target_k,
                   min(schedule.frequency) AS frequency,
                   count(DISTINCT variant.target_k) AS k_count,
                   count(DISTINCT schedule.frequency) AS frequency_count
            FROM experiment.comparison_cohort_member member
            JOIN experiment.result_publication publication USING (result_publication_id)
            JOIN experiment.experiment_specification specification USING
                 (experiment_specification_id)
            JOIN strategy.model_strategy_target_path model_path ON
                 model_path.portfolio_target_path_id = specification.strategy_target_path_id
            JOIN strategy.strategy_product_version product USING (strategy_product_version_id)
            JOIN strategy.strategy_variant variant USING (strategy_variant_id)
            JOIN ops.rebalance_schedule_version schedule USING (rebalance_schedule_version_id)
            GROUP BY member.comparison_cohort_version_id
        ) context
        WHERE context.comparison_cohort_version_id = cohort.comparison_cohort_version_id
          AND context.k_count = 1 AND context.frequency_count = 1
    """)
    op.execute(
        "ALTER TABLE experiment.comparison_cohort_version "
        "ENABLE TRIGGER trg_comparison_cohort_draft"
    )
    op.alter_column(
        "comparison_cohort_version", "target_k", nullable=False, schema="experiment"
    )
    op.alter_column(
        "comparison_cohort_version", "frequency", nullable=False, schema="experiment"
    )
    op.create_check_constraint(
        "ck_comparison_cohort_target_k",
        "comparison_cohort_version",
        "target_k IN (1, 2, 3)",
        schema="experiment",
    )
    op.create_check_constraint(
        "ck_comparison_cohort_frequency",
        "comparison_cohort_version",
        "frequency IN ('weekly', 'monthly')",
        schema="experiment",
    )
    op.execute("""
    CREATE OR REPLACE FUNCTION experiment.enforce_cohort_owner_draft()
    RETURNS trigger LANGUAGE plpgsql AS $$
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
                 specification.as_of_date, variant.target_k, schedule.frequency,
                 ready.common_data_ready_date,
                 strategy_gross.effective_nav_start AS common_simulation_start,
                 interval.resolved_start AS common_metric_start,
                 interval.resolved_end AS common_metric_end,
                 benchmark_definition.category AS benchmark_category
          INTO member_row
          FROM experiment.result_publication publication
          JOIN lineage.artifact result_artifact ON result_artifact.artifact_id = publication.artifact_id
          JOIN experiment.experiment_specification specification USING (experiment_specification_id)
          JOIN experiment.interval_performance_result interval USING (interval_performance_result_id)
          JOIN experiment.net_cost_path strategy_net ON
               strategy_net.net_cost_path_id = interval.strategy_net_cost_path_id
          JOIN experiment.gross_portfolio_path strategy_gross ON
               strategy_gross.gross_portfolio_path_id = strategy_net.gross_portfolio_path_id
          JOIN strategy.portfolio_target_path target ON
               target.portfolio_target_path_id = strategy_gross.portfolio_target_path_id
          JOIN strategy.model_strategy_target_path model_path ON
               model_path.portfolio_target_path_id = target.portfolio_target_path_id
          JOIN strategy.strategy_product_version product USING (strategy_product_version_id)
          JOIN strategy.strategy_variant variant USING (strategy_variant_id)
          JOIN ops.rebalance_schedule_version schedule USING (rebalance_schedule_version_id)
          JOIN experiment.benchmark_version benchmark ON benchmark.benchmark_version_id = specification.benchmark_version_id
          JOIN experiment.benchmark_definition benchmark_definition USING (benchmark_definition_id)
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
             OR member_row.target_k IS DISTINCT FROM cohort_row.target_k
             OR member_row.frequency IS DISTINCT FROM cohort_row.frequency
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
    """)


def downgrade() -> None:
    op.execute("""
      DELETE FROM lineage.artifact_dependency dependency
      USING experiment.comparison_cohort_version cohort
      WHERE dependency.artifact_id = cohort.artifact_id;
      DELETE FROM experiment.comparison_cohort_member;
      DELETE FROM experiment.comparison_cohort_version;
      DELETE FROM lineage.artifact WHERE artifact_type = 'comparison_cohort_version';
    """)
    op.execute(
        "DROP FUNCTION IF EXISTS experiment.enforce_cohort_owner_draft() CASCADE"
    )
    op.drop_constraint(
        "ck_comparison_cohort_frequency",
        "comparison_cohort_version",
        schema="experiment",
        type_="check",
    )
    op.drop_constraint(
        "ck_comparison_cohort_target_k",
        "comparison_cohort_version",
        schema="experiment",
        type_="check",
    )
    op.drop_column("comparison_cohort_version", "frequency", schema="experiment")
    op.drop_column("comparison_cohort_version", "target_k", schema="experiment")
