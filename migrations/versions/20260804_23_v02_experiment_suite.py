# ruff: noqa: E501
"""Add immutable experiment suites and atomic specifications.

Revision ID: 20260804_23_v02_experiment
Revises: 20260804_22_v02_performance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_23_v02_experiment"
down_revision: str | None = "20260804_22_v02_performance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
HASH_PATTERN = "^[0-9a-f]{64}$"


def _id(name: str) -> sa.Column[object]:
    return sa.Column(name, UUID, nullable=False)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table[:16]}_{column[:24]}", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "experiment_specification",
        _id("experiment_specification_id"),
        _id("artifact_id"),
        _id("strategy_target_path_id"),
        _id("benchmark_version_id"),
        _id("cost_scenario_id"),
        _id("performance_metric_catalog_id"),
        _id("accounting_engine_version_id"),
        _id("benchmark_engine_version_id"),
        _id("performance_engine_version_id"),
        sa.Column("specification_fingerprint", sa.String(64), nullable=False),
        sa.Column("template_key", sa.String(40), nullable=False),
        sa.Column("initialization_policy", sa.String(20), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("custom_start", sa.Date()),
        sa.Column("custom_end", sa.Date()),
        sa.Column("simulation_end", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("experiment_spec", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "experiment_spec",
            "strategy_target_path_id",
            "strategy.portfolio_target_path.portfolio_target_path_id",
        ),
        _fk(
            "experiment_spec",
            "benchmark_version_id",
            "experiment.benchmark_version.benchmark_version_id",
        ),
        _fk("experiment_spec", "cost_scenario_id", "experiment.cost_scenario.cost_scenario_id"),
        _fk(
            "experiment_spec",
            "performance_metric_catalog_id",
            "experiment.performance_metric_catalog.performance_metric_catalog_id",
        ),
        _fk(
            "experiment_spec",
            "accounting_engine_version_id",
            "ops.engine_version.engine_version_id",
        ),
        _fk(
            "experiment_spec", "benchmark_engine_version_id", "ops.engine_version.engine_version_id"
        ),
        _fk(
            "experiment_spec",
            "performance_engine_version_id",
            "ops.engine_version.engine_version_id",
        ),
        sa.PrimaryKeyConstraint("experiment_specification_id", name="pk_experiment_specification"),
        sa.UniqueConstraint("artifact_id", name="uq_experiment_specification_artifact"),
        sa.UniqueConstraint(
            "specification_fingerprint", name="uq_experiment_specification_fingerprint"
        ),
        sa.CheckConstraint(
            f"specification_fingerprint ~ '{HASH_PATTERN}'",
            name="ck_experiment_specification_fingerprint",
        ),
        sa.CheckConstraint(
            "template_key IN ('full_history','trailing_10_years','trailing_5_years','trailing_3_years','trailing_1_year','custom')",
            name="ck_experiment_specification_template",
        ),
        sa.CheckConstraint(
            "initialization_policy = 'carry_in'", name="ck_experiment_specification_initialization"
        ),
        sa.CheckConstraint("as_of_date <= simulation_end", name="ck_experiment_specification_asof"),
        sa.CheckConstraint(
            "(template_key = 'custom' AND custom_start IS NOT NULL AND custom_end IS NOT NULL AND custom_start <= custom_end AND custom_end <= as_of_date) OR (template_key <> 'custom' AND custom_start IS NULL AND custom_end IS NULL)",
            name="ck_experiment_specification_custom",
        ),
        schema="experiment",
    )
    op.create_table(
        "experiment_suite",
        _id("experiment_suite_id"),
        _id("artifact_id"),
        sa.Column("suite_key", sa.String(140), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("specification_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("experiment_suite", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("experiment_suite_id", name="pk_experiment_suite"),
        sa.UniqueConstraint("artifact_id", name="uq_experiment_suite_artifact"),
        sa.UniqueConstraint("suite_key", "version_number", name="uq_experiment_suite_version"),
        sa.CheckConstraint(
            "version_number >= 1 AND specification_count >= 1", name="ck_experiment_suite_counts"
        ),
        schema="experiment",
    )
    op.create_table(
        "experiment_suite_cell",
        _id("experiment_suite_id"),
        _id("experiment_specification_id"),
        sa.Column("cell_key", sa.String(180), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _fk(
            "experiment_cell",
            "experiment_suite_id",
            "experiment.experiment_suite.experiment_suite_id",
        ),
        _fk(
            "experiment_cell",
            "experiment_specification_id",
            "experiment.experiment_specification.experiment_specification_id",
        ),
        sa.PrimaryKeyConstraint(
            "experiment_suite_id", "experiment_specification_id", name="pk_experiment_suite_cell"
        ),
        sa.UniqueConstraint("experiment_suite_id", "cell_key", name="uq_experiment_suite_cell_key"),
        sa.UniqueConstraint(
            "experiment_suite_id", "ordinal", name="uq_experiment_suite_cell_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_experiment_suite_cell_ordinal"),
        schema="experiment",
    )
    op.create_index(
        "ix_experiment_specification_context",
        "experiment_specification",
        ["template_key", "as_of_date", "cost_scenario_id", "benchmark_version_id"],
        schema="experiment",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute("""
    CREATE FUNCTION experiment.enforce_suite_owner_draft() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE owner_artifact uuid;
    BEGIN
      IF TG_TABLE_NAME IN ('experiment_specification', 'experiment_suite') THEN
        owner_artifact := CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END;
      ELSE
        SELECT artifact_id INTO owner_artifact FROM experiment.experiment_suite WHERE experiment_suite_id = COALESCE(NEW.experiment_suite_id, OLD.experiment_suite_id);
      END IF;
      PERFORM data.assert_artifact_draft(owner_artifact);
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END; $$;
    CREATE TRIGGER trg_experiment_specification_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.experiment_specification FOR EACH ROW EXECUTE FUNCTION experiment.enforce_suite_owner_draft();
    CREATE TRIGGER trg_experiment_suite_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.experiment_suite FOR EACH ROW EXECUTE FUNCTION experiment.enforce_suite_owner_draft();
    CREATE TRIGGER trg_experiment_suite_cell_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.experiment_suite_cell FOR EACH ROW EXECUTE FUNCTION experiment.enforce_suite_owner_draft();

    CREATE FUNCTION experiment.validate_experiment_specification() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE target_row record; benchmark_status text; cost_status text; metric_status text; accounting_key text; benchmark_key text; performance_key text;
    BEGIN
      IF TG_OP <> 'DELETE' THEN
        SELECT path.target_type, dataset.coverage_end AS simulation_end INTO target_row FROM strategy.portfolio_target_path path JOIN strategy.model_strategy_target_path owner ON owner.portfolio_target_path_id = path.portfolio_target_path_id JOIN model.model_dataset dataset ON dataset.model_dataset_id = owner.model_dataset_id JOIN lineage.artifact artifact ON artifact.artifact_id = path.artifact_id AND artifact.status = 'published' WHERE path.portfolio_target_path_id = NEW.strategy_target_path_id;
        SELECT artifact.status INTO benchmark_status FROM experiment.benchmark_version version JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id WHERE version.benchmark_version_id = NEW.benchmark_version_id;
        SELECT artifact.status INTO cost_status FROM experiment.cost_scenario scenario JOIN lineage.artifact artifact ON artifact.artifact_id = scenario.artifact_id WHERE scenario.cost_scenario_id = NEW.cost_scenario_id;
        SELECT artifact.status INTO metric_status FROM experiment.performance_metric_catalog catalog JOIN lineage.artifact artifact ON artifact.artifact_id = catalog.artifact_id WHERE catalog.performance_metric_catalog_id = NEW.performance_metric_catalog_id;
        SELECT definition.engine_key INTO accounting_key FROM ops.engine_version version JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id AND artifact.status = 'published' WHERE version.engine_version_id = NEW.accounting_engine_version_id;
        SELECT definition.engine_key INTO benchmark_key FROM ops.engine_version version JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id AND artifact.status = 'published' WHERE version.engine_version_id = NEW.benchmark_engine_version_id;
        SELECT definition.engine_key INTO performance_key FROM ops.engine_version version JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id AND artifact.status = 'published' WHERE version.engine_version_id = NEW.performance_engine_version_id;
        IF target_row.target_type <> 'model_strategy' OR target_row.simulation_end <> NEW.simulation_end THEN RAISE EXCEPTION 'Experiment Specification requires a published Model Strategy Target and exact simulation end'; END IF;
        IF benchmark_status <> 'published' OR cost_status <> 'published' OR metric_status <> 'published' THEN RAISE EXCEPTION 'Experiment Specification requires published benchmark, cost, and metric inputs'; END IF;
        IF accounting_key <> 'portfolio_accounting_engine' OR benchmark_key <> 'benchmark_target_engine' OR performance_key <> 'performance_engine' THEN RAISE EXCEPTION 'Experiment Specification engine roles are incompatible'; END IF;
      END IF;
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END; $$;
    CREATE TRIGGER trg_validate_experiment_specification BEFORE INSERT OR UPDATE ON experiment.experiment_specification FOR EACH ROW EXECUTE FUNCTION experiment.validate_experiment_specification();
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS experiment.validate_experiment_specification() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS experiment.enforce_suite_owner_draft() CASCADE")
    op.drop_index(
        "ix_experiment_specification_context",
        table_name="experiment_specification",
        schema="experiment",
    )
    op.drop_table("experiment_suite_cell", schema="experiment")
    op.drop_table("experiment_suite", schema="experiment")
    op.drop_table("experiment_specification", schema="experiment")
