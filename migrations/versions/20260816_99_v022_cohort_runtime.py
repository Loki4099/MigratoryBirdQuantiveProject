# ruff: noqa: E501
"""Require exact Evaluation Cohort ranges in v0.22 runtime plans.

Revision ID: 20260816_99_v022_cohort_runtime
Revises: 20260816_98_v022_eval_cohort
"""

from __future__ import annotations

from alembic import op

revision = "20260816_99_v022_cohort_runtime"
down_revision = "20260816_98_v022_eval_cohort"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.validate_v022_cohort_runtime_plan()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE cohort_row record; frozen_range jsonb;
        BEGIN
          SELECT cohort.evaluation_cohort_version_id,cohort.cohort_fingerprint,
                 cohort.evaluation_start,cohort.evaluation_end,artifact.status
            INTO cohort_row
            FROM experiment.v022_research_suite_evaluation_cohort_binding binding
            JOIN experiment.v022_evaluation_cohort_version cohort
              ON cohort.evaluation_cohort_version_id=binding.evaluation_cohort_version_id
            JOIN lineage.artifact artifact ON artifact.artifact_id=cohort.artifact_id
           WHERE binding.research_suite_id=NEW.research_suite_id;
          IF cohort_row.evaluation_cohort_version_id IS NULL THEN
            IF NEW.requested_range IS DISTINCT FROM NEW.effective_range THEN
              RAISE EXCEPTION 'Runtime Plan cannot shorten an unbound requested range';
            END IF;
            RETURN NEW;
          END IF;
          frozen_range := jsonb_build_object(
            'start',cohort_row.evaluation_start::text,
            'end',cohort_row.evaluation_end::text
          );
          IF cohort_row.status IS DISTINCT FROM 'published' OR
             NEW.requested_range IS DISTINCT FROM frozen_range OR
             NEW.effective_range IS DISTINCT FROM frozen_range THEN
            RAISE EXCEPTION 'Runtime Plan must use its exact published Evaluation Cohort range';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_cohort_runtime_plan_validate
          BEFORE INSERT ON experiment.v022_suite_runtime_plan
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_cohort_runtime_plan();

        CREATE FUNCTION experiment.validate_v022_cohort_runtime_work_spec()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE plan_row record; cohort_row record; frozen_range jsonb;
        BEGIN
          SELECT research_suite_id,effective_range INTO plan_row
            FROM experiment.v022_suite_runtime_plan
           WHERE suite_runtime_plan_id=NEW.suite_runtime_plan_id;
          SELECT cohort.evaluation_cohort_version_id,cohort.cohort_fingerprint,
                 cohort.evaluation_start,cohort.evaluation_end
            INTO cohort_row
            FROM experiment.v022_research_suite_evaluation_cohort_binding binding
            JOIN experiment.v022_evaluation_cohort_version cohort
              ON cohort.evaluation_cohort_version_id=binding.evaluation_cohort_version_id
           WHERE binding.research_suite_id=plan_row.research_suite_id;
          IF cohort_row.evaluation_cohort_version_id IS NULL THEN
            IF NEW.specification_document->>'evaluation_cohort_version_id' IS NOT NULL OR
               NEW.specification_document->>'evaluation_cohort_fingerprint' IS NOT NULL THEN
              RAISE EXCEPTION 'Unbound runtime Work cannot claim an Evaluation Cohort';
            END IF;
            RETURN NEW;
          END IF;
          frozen_range := jsonb_build_object(
            'start',cohort_row.evaluation_start::text,
            'end',cohort_row.evaluation_end::text
          );
          IF NEW.specification_document->>'evaluation_cohort_version_id'
               IS DISTINCT FROM cohort_row.evaluation_cohort_version_id::text OR
             NEW.specification_document->>'evaluation_cohort_fingerprint'
               IS DISTINCT FROM cohort_row.cohort_fingerprint OR
             NEW.specification_document->'effective_range'
               IS DISTINCT FROM frozen_range OR
             plan_row.effective_range IS DISTINCT FROM frozen_range THEN
            RAISE EXCEPTION 'Runtime Work must project its exact Evaluation Cohort identity';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_strategy_target_cohort_validate
          BEFORE INSERT ON strategy.v022_strategy_target_work_spec
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_cohort_runtime_work_spec();
        CREATE TRIGGER trg_v022_defense_decision_cohort_validate
          BEFORE INSERT ON defense.v022_defense_decision_work_spec
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_cohort_runtime_work_spec();
        CREATE TRIGGER trg_v022_sleeve_merge_cohort_validate
          BEFORE INSERT ON strategy.v022_sleeve_merge_work_spec
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_cohort_runtime_work_spec();
        CREATE TRIGGER trg_v022_portfolio_cell_cohort_validate
          BEFORE INSERT ON experiment.v022_portfolio_cell_work_spec
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_cohort_runtime_work_spec();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_v022_portfolio_cell_cohort_validate
          ON experiment.v022_portfolio_cell_work_spec;
        DROP TRIGGER IF EXISTS trg_v022_sleeve_merge_cohort_validate
          ON strategy.v022_sleeve_merge_work_spec;
        DROP TRIGGER IF EXISTS trg_v022_defense_decision_cohort_validate
          ON defense.v022_defense_decision_work_spec;
        DROP TRIGGER IF EXISTS trg_v022_strategy_target_cohort_validate
          ON strategy.v022_strategy_target_work_spec;
        DROP TRIGGER IF EXISTS trg_v022_cohort_runtime_plan_validate
          ON experiment.v022_suite_runtime_plan;
        DROP FUNCTION IF EXISTS experiment.validate_v022_cohort_runtime_work_spec();
        DROP FUNCTION IF EXISTS experiment.validate_v022_cohort_runtime_plan();
        """
    )
