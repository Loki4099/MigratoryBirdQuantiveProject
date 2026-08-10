\set ON_ERROR_STOP on

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM product.product_enrollment) THEN
        RAISE EXCEPTION
            'Experiment purge refused: Product candidates exist and their evidence must be retained';
    END IF;
END $$;

CREATE TEMP TABLE purge_artifact_id (
    artifact_id uuid PRIMARY KEY
) ON COMMIT DROP;

INSERT INTO purge_artifact_id (artifact_id)
SELECT artifact_id FROM experiment.experiment_suite
UNION SELECT artifact_id FROM experiment.experiment_specification
UNION SELECT artifact_id FROM experiment.gross_portfolio_path
UNION SELECT artifact_id FROM experiment.net_cost_path
UNION SELECT artifact_id FROM experiment.interval_performance_result
UNION SELECT artifact_id FROM experiment.result_publication
UNION SELECT artifact_id FROM experiment.comparison_cohort_version
UNION SELECT artifact_id FROM experiment.research_suite
UNION SELECT artifact_id FROM experiment.predictive_cell_specification
UNION SELECT artifact_id FROM experiment.portfolio_cell_specification
UNION SELECT artifact_id FROM experiment.cell_result
UNION SELECT artifact_id FROM ops.run_artifact WHERE role = 'output'
ON CONFLICT DO NOTHING;

CREATE TEMP TABLE purge_run_attempt_id ON COMMIT DROP AS
SELECT run_attempt_id
FROM ops.run_attempt
WHERE run_type = 'experiment_specification';

CREATE TEMP TABLE purge_work_item_id ON COMMIT DROP AS
SELECT work_item_id
FROM ops.work_item
WHERE work_type IN ('predictive', 'portfolio');

UPDATE workspace.research_draft
SET last_compiled_artifact_id = NULL
WHERE last_compiled_artifact_id IN (SELECT artifact_id FROM purge_artifact_id);

DELETE FROM ops.quality_check_result
WHERE run_attempt_id IN (SELECT run_attempt_id FROM purge_run_attempt_id);
DELETE FROM ops.run_error
WHERE run_attempt_id IN (SELECT run_attempt_id FROM purge_run_attempt_id);
DELETE FROM ops.run_event
WHERE run_attempt_id IN (SELECT run_attempt_id FROM purge_run_attempt_id);
DELETE FROM ops.run_artifact
WHERE run_attempt_id IN (SELECT run_attempt_id FROM purge_run_attempt_id);

DELETE FROM ops.work_item_event
WHERE work_item_id IN (SELECT work_item_id FROM purge_work_item_id);

TRUNCATE TABLE
    experiment.comparison_cohort_member,
    experiment.comparison_cohort_version,
    experiment.performance_metric_value,
    experiment.result_publication,
    experiment.interval_performance_result,
    experiment.execution_cost,
    experiment.net_daily_nav,
    experiment.net_cost_path,
    experiment.portfolio_trade,
    experiment.portfolio_execution,
    experiment.daily_asset_position,
    experiment.daily_reserve_position,
    experiment.gross_daily_nav,
    experiment.gross_portfolio_path,
    experiment.experiment_suite_cell,
    experiment.experiment_specification,
    experiment.experiment_suite,
    experiment.cell_result,
    experiment.research_suite_work_item,
    experiment.portfolio_cell_specification,
    experiment.predictive_cell_specification,
    experiment.research_suite;

DELETE FROM ops.run_attempt
WHERE run_attempt_id IN (SELECT run_attempt_id FROM purge_run_attempt_id);

DELETE FROM ops.work_item
WHERE work_item_id IN (SELECT work_item_id FROM purge_work_item_id);

-- Published lineage is immutable by contract.  The experiment run/result rows are
-- removed from the active research surface, while their artifact tombstones remain
-- as an audit record instead of mutating published dependency history.

COMMIT;
