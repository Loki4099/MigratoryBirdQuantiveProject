# ruff: noqa: E501
"""Add accepted experiment result publications.

Revision ID: 20260804_24_v02_exp_result
Revises: 20260804_23_v02_experiment
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_24_v02_exp_result"
down_revision: str | None = "20260804_23_v02_experiment"
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
    # The M7C constraint omitted the engine carried by the parent target path and
    # incorrectly prevented a new Benchmark Engine version from publishing a new path.
    op.drop_constraint(
        "uq_benchmark_target_exact_inputs",
        "benchmark_target_path",
        schema="strategy",
        type_="unique",
    )
    op.create_table(
        "result_publication",
        _id("result_publication_id"),
        _id("artifact_id"),
        _id("experiment_specification_id"),
        _id("accepted_run_attempt_id"),
        _id("interval_performance_result_id"),
        sa.Column("availability_status", sa.String(20), nullable=False),
        sa.Column("quality_status", sa.String(40), nullable=False),
        sa.Column(
            "accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("result_publication", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "result_publication",
            "experiment_specification_id",
            "experiment.experiment_specification.experiment_specification_id",
        ),
        _fk("result_publication", "accepted_run_attempt_id", "ops.run_attempt.run_attempt_id"),
        _fk(
            "result_publication",
            "interval_performance_result_id",
            "experiment.interval_performance_result.interval_performance_result_id",
        ),
        sa.PrimaryKeyConstraint("result_publication_id", name="pk_result_publication"),
        sa.UniqueConstraint("artifact_id", name="uq_result_publication_artifact"),
        sa.UniqueConstraint(
            "experiment_specification_id", name="uq_result_publication_specification"
        ),
        sa.UniqueConstraint(
            "interval_performance_result_id", name="uq_result_publication_interval_result"
        ),
        sa.CheckConstraint(
            "availability_status IN ('eligible','excluded')",
            name="ck_result_publication_availability",
        ),
        sa.CheckConstraint(
            "quality_status IN ('normal','short_sample_warning','very_short_sample_warning','not_applicable')",
            name="ck_result_publication_quality",
        ),
        schema="experiment",
    )
    op.create_index(
        "ix_result_publication_context",
        "result_publication",
        ["availability_status", "quality_status", "accepted_at"],
        schema="experiment",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute("""
    CREATE FUNCTION experiment.validate_result_publication() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE spec_row record; run_row record; result_row record; failed_checks bigint; output_link bigint;
    BEGIN
      PERFORM data.assert_artifact_draft(CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END);
      IF TG_OP <> 'DELETE' THEN
        SELECT specification.*, artifact.artifact_id AS specification_artifact_id, artifact.status AS specification_status INTO spec_row FROM experiment.experiment_specification specification JOIN lineage.artifact artifact ON artifact.artifact_id = specification.artifact_id WHERE specification.experiment_specification_id = NEW.experiment_specification_id;
        SELECT attempt.*, definition.engine_key INTO run_row FROM ops.run_attempt attempt JOIN ops.engine_version version ON version.engine_version_id = attempt.engine_version_id JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id WHERE attempt.run_attempt_id = NEW.accepted_run_attempt_id;
        SELECT result.*, strategy_net.cost_scenario_id AS strategy_cost_scenario_id, benchmark_net.cost_scenario_id AS benchmark_cost_scenario_id, strategy_gross.portfolio_target_path_id AS strategy_target_path_id, strategy_gross.engine_version_id AS strategy_accounting_engine_version_id, benchmark_gross.engine_version_id AS benchmark_accounting_engine_version_id, benchmark_target.benchmark_version_id, benchmark_path.engine_version_id AS benchmark_engine_version_id, result_artifact.status AS result_status INTO result_row FROM experiment.interval_performance_result result JOIN lineage.artifact result_artifact ON result_artifact.artifact_id = result.artifact_id JOIN experiment.net_cost_path strategy_net ON strategy_net.net_cost_path_id = result.strategy_net_cost_path_id JOIN experiment.gross_portfolio_path strategy_gross ON strategy_gross.gross_portfolio_path_id = strategy_net.gross_portfolio_path_id JOIN experiment.net_cost_path benchmark_net ON benchmark_net.net_cost_path_id = result.benchmark_net_cost_path_id JOIN experiment.gross_portfolio_path benchmark_gross ON benchmark_gross.gross_portfolio_path_id = benchmark_net.gross_portfolio_path_id JOIN strategy.benchmark_target_path benchmark_target ON benchmark_target.portfolio_target_path_id = benchmark_gross.portfolio_target_path_id JOIN strategy.portfolio_target_path benchmark_path ON benchmark_path.portfolio_target_path_id = benchmark_target.portfolio_target_path_id WHERE result.interval_performance_result_id = NEW.interval_performance_result_id;
        SELECT count(*) INTO failed_checks FROM ops.quality_check_result WHERE run_attempt_id = NEW.accepted_run_attempt_id AND status = 'failed';
        SELECT count(*) INTO output_link FROM ops.run_artifact WHERE run_attempt_id = NEW.accepted_run_attempt_id AND artifact_id = result_row.artifact_id AND role = 'output';
        IF spec_row.specification_status <> 'published' OR result_row.result_status <> 'published' THEN RAISE EXCEPTION 'Accepted Result requires published Specification and Interval Result'; END IF;
        IF run_row.status <> 'completed' OR run_row.root_artifact_id <> spec_row.specification_artifact_id OR run_row.engine_key <> 'experiment_orchestration_engine' OR failed_checks <> 0 OR output_link <> 1 THEN RAISE EXCEPTION 'Accepted Result requires a completed matching run with no failed quality checks and linked output'; END IF;
        IF result_row.strategy_target_path_id <> spec_row.strategy_target_path_id OR result_row.benchmark_version_id <> spec_row.benchmark_version_id OR result_row.strategy_cost_scenario_id <> spec_row.cost_scenario_id OR result_row.benchmark_cost_scenario_id <> spec_row.cost_scenario_id OR result_row.strategy_accounting_engine_version_id <> spec_row.accounting_engine_version_id OR result_row.benchmark_accounting_engine_version_id <> spec_row.accounting_engine_version_id OR result_row.benchmark_engine_version_id <> spec_row.benchmark_engine_version_id OR result_row.performance_metric_catalog_id <> spec_row.performance_metric_catalog_id OR result_row.engine_version_id <> spec_row.performance_engine_version_id OR result_row.template_key <> spec_row.template_key OR result_row.initialization_policy <> spec_row.initialization_policy OR result_row.as_of_date <> spec_row.as_of_date OR result_row.requested_start <> COALESCE(spec_row.custom_start, result_row.requested_start) OR result_row.requested_end <> COALESCE(spec_row.custom_end, result_row.requested_end) THEN RAISE EXCEPTION 'Accepted Interval Result does not implement the exact Experiment Specification'; END IF;
        IF NEW.availability_status <> result_row.availability_status OR NEW.quality_status <> result_row.quality_status THEN RAISE EXCEPTION 'Accepted Result summary must match Interval Result'; END IF;
      END IF;
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END; $$;
    CREATE TRIGGER trg_result_publication_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.result_publication FOR EACH ROW EXECUTE FUNCTION experiment.validate_result_publication();
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS experiment.validate_result_publication() CASCADE")
    op.drop_index(
        "ix_result_publication_context", table_name="result_publication", schema="experiment"
    )
    op.drop_table("result_publication", schema="experiment")
    op.create_unique_constraint(
        "uq_benchmark_target_exact_inputs",
        "benchmark_target_path",
        ["reference_portfolio_target_path_id", "benchmark_version_id"],
        schema="strategy",
    )
