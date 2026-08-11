# ruff: noqa: E501
"""Add linear cost scenarios and immutable net cost paths.

Revision ID: 20260804_20_v02_net_cost_path
Revises: 20260804_19_v02_gross_path
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_20_v02_net_cost_path"
down_revision: str | None = "20260804_19_v02_gross_path"
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
        "cost_model_definition",
        _id("cost_model_definition_id"),
        _id("artifact_id"),
        sa.Column("model_key", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("cost_model_def", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("cost_model_definition_id", name="pk_cost_model_definition"),
        sa.UniqueConstraint("artifact_id", name="uq_cost_model_definition_artifact"),
        sa.UniqueConstraint("model_key", name="uq_cost_model_definition_key"),
        schema="experiment",
    )
    op.create_table(
        "cost_model_version",
        _id("cost_model_version_id"),
        _id("cost_model_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("calculation_method", sa.String(100), nullable=False),
        sa.Column("charge_basis", sa.String(100), nullable=False),
        sa.Column("deduction_timing", sa.String(100), nullable=False),
        sa.Column("reserve_charged", sa.Boolean(), nullable=False),
        sa.Column("bps_divisor", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk(
            "cost_model_ver",
            "cost_model_definition_id",
            "experiment.cost_model_definition.cost_model_definition_id",
        ),
        _fk("cost_model_ver", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("cost_model_version_id", name="pk_cost_model_version"),
        sa.UniqueConstraint("artifact_id", name="uq_cost_model_version_artifact"),
        sa.UniqueConstraint(
            "cost_model_definition_id", "version_number", name="uq_cost_model_version_identity"
        ),
        sa.CheckConstraint(
            "version_number >= 1 AND bps_divisor = 10000", name="ck_cost_model_version_numeric"
        ),
        sa.CheckConstraint("reserve_charged = false", name="ck_cost_model_reserve_not_charged"),
        schema="experiment",
    )
    op.create_table(
        "cost_scenario",
        _id("cost_scenario_id"),
        _id("artifact_id"),
        _id("cost_model_version_id"),
        sa.Column("scenario_key", sa.String(120), nullable=False),
        sa.Column("cost_bps_per_side", sa.Numeric(8, 4), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("cost_scenario", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "cost_scenario",
            "cost_model_version_id",
            "experiment.cost_model_version.cost_model_version_id",
        ),
        sa.PrimaryKeyConstraint("cost_scenario_id", name="pk_cost_scenario"),
        sa.UniqueConstraint("artifact_id", name="uq_cost_scenario_artifact"),
        sa.UniqueConstraint("scenario_key", name="uq_cost_scenario_key"),
        sa.UniqueConstraint(
            "cost_model_version_id", "cost_bps_per_side", name="uq_cost_scenario_model_bps"
        ),
        sa.CheckConstraint("cost_bps_per_side IN (2, 5, 10)", name="ck_cost_scenario_formal_bps"),
        schema="experiment",
    )
    op.create_table(
        "net_cost_path",
        _id("net_cost_path_id"),
        _id("artifact_id"),
        _id("gross_portfolio_path_id"),
        _id("cost_scenario_id"),
        sa.Column("effective_nav_start", sa.Date(), nullable=False),
        sa.Column("effective_nav_end", sa.Date(), nullable=False),
        sa.Column("nav_count", sa.BigInteger(), nullable=False),
        sa.Column("execution_cost_count", sa.BigInteger(), nullable=False),
        sa.Column("cumulative_cost_amount", NUMERIC, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("net_cost_path", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "net_cost_path",
            "gross_portfolio_path_id",
            "experiment.gross_portfolio_path.gross_portfolio_path_id",
        ),
        _fk("net_cost_path", "cost_scenario_id", "experiment.cost_scenario.cost_scenario_id"),
        sa.PrimaryKeyConstraint("net_cost_path_id", name="pk_net_cost_path"),
        sa.UniqueConstraint("artifact_id", name="uq_net_cost_path_artifact"),
        sa.UniqueConstraint(
            "gross_portfolio_path_id", "cost_scenario_id", name="uq_net_cost_path_exact_inputs"
        ),
        sa.CheckConstraint(
            "effective_nav_start <= effective_nav_end", name="ck_net_cost_path_coverage"
        ),
        sa.CheckConstraint(
            "nav_count >= 1 AND execution_cost_count >= 1 AND cumulative_cost_amount >= 0",
            name="ck_net_cost_path_counts_cost",
        ),
        schema="experiment",
    )
    op.create_table(
        "net_daily_nav",
        _id("net_cost_path_id"),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("net_daily_return", NUMERIC, nullable=False),
        sa.Column("net_nav", NUMERIC, nullable=False),
        sa.Column("gross_nav", NUMERIC, nullable=False),
        sa.Column("daily_cost_amount", NUMERIC, nullable=False),
        _fk("net_daily_nav", "net_cost_path_id", "experiment.net_cost_path.net_cost_path_id"),
        sa.PrimaryKeyConstraint("net_cost_path_id", "nav_date", name="pk_net_daily_nav"),
        sa.CheckConstraint(
            "net_nav > 0 AND gross_nav > 0 AND net_nav <= gross_nav",
            name="ck_net_daily_nav_positive_ordered",
        ),
        sa.CheckConstraint("daily_cost_amount >= 0", name="ck_net_daily_nav_cost_nonnegative"),
        schema="experiment",
    )
    op.create_table(
        "execution_cost",
        _id("net_cost_path_id"),
        _id("portfolio_execution_id"),
        sa.Column("net_pretrade_nav", NUMERIC, nullable=False),
        sa.Column("gross_traded_notional", NUMERIC, nullable=False),
        sa.Column("cost_fraction", NUMERIC, nullable=False),
        sa.Column("cost_amount", NUMERIC, nullable=False),
        _fk("execution_cost", "net_cost_path_id", "experiment.net_cost_path.net_cost_path_id"),
        _fk(
            "execution_cost",
            "portfolio_execution_id",
            "experiment.portfolio_execution.portfolio_execution_id",
        ),
        sa.PrimaryKeyConstraint(
            "net_cost_path_id", "portfolio_execution_id", name="pk_execution_cost"
        ),
        sa.CheckConstraint(
            "net_pretrade_nav > 0 AND gross_traded_notional >= 0 AND cost_fraction >= 0 AND cost_amount >= 0",
            name="ck_execution_cost_nonnegative",
        ),
        sa.CheckConstraint(
            "abs(cost_amount - net_pretrade_nav * cost_fraction) <= 0.000000000000000000000001",
            name="ck_execution_cost_reconciliation",
        ),
        schema="experiment",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.enforce_cost_artifact_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE owner_artifact uuid;
        BEGIN
            IF TG_TABLE_NAME = 'cost_model_version' THEN
                owner_artifact := CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END;
            ELSIF TG_TABLE_NAME = 'cost_scenario' THEN
                owner_artifact := CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END;
            ELSE
                owner_artifact := CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END;
            END IF;
            PERFORM data.assert_artifact_draft(owner_artifact);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END; $$;
        CREATE TRIGGER trg_cost_model_definition_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.cost_model_definition FOR EACH ROW EXECUTE FUNCTION experiment.enforce_cost_artifact_draft();
        CREATE TRIGGER trg_cost_model_version_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.cost_model_version FOR EACH ROW EXECUTE FUNCTION experiment.enforce_cost_artifact_draft();
        CREATE TRIGGER trg_cost_scenario_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.cost_scenario FOR EACH ROW EXECUTE FUNCTION experiment.enforce_cost_artifact_draft();

        CREATE FUNCTION experiment.validate_net_cost_path() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE gross_status text; scenario_status text; gross_row record;
        BEGIN
            PERFORM data.assert_artifact_draft(CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END);
            IF TG_OP <> 'DELETE' THEN
                SELECT artifact.status, path.effective_nav_start, path.effective_nav_end, path.nav_count, path.execution_count
                INTO gross_row FROM experiment.gross_portfolio_path path JOIN lineage.artifact artifact ON artifact.artifact_id = path.artifact_id
                WHERE path.gross_portfolio_path_id = NEW.gross_portfolio_path_id;
                SELECT artifact.status INTO scenario_status FROM experiment.cost_scenario scenario JOIN lineage.artifact artifact ON artifact.artifact_id = scenario.artifact_id WHERE scenario.cost_scenario_id = NEW.cost_scenario_id;
                IF gross_row.status <> 'published' OR scenario_status <> 'published' THEN
                    RAISE EXCEPTION 'Net Cost Path requires published Gross Path and Cost Scenario';
                END IF;
                IF NEW.effective_nav_start <> gross_row.effective_nav_start OR NEW.effective_nav_end <> gross_row.effective_nav_end OR NEW.nav_count <> gross_row.nav_count OR NEW.execution_cost_count <> gross_row.execution_count THEN
                    RAISE EXCEPTION 'Net Cost Path coverage and counts must match Gross Path';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END; $$;
        CREATE TRIGGER trg_net_cost_path_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.net_cost_path FOR EACH ROW EXECUTE FUNCTION experiment.validate_net_cost_path();

        CREATE FUNCTION experiment.enforce_net_child_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE path_id uuid; owner_artifact uuid;
        BEGIN
            path_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.net_cost_path_id ELSE NEW.net_cost_path_id END;
            SELECT artifact_id INTO owner_artifact FROM experiment.net_cost_path WHERE net_cost_path_id = path_id;
            PERFORM data.assert_artifact_draft(owner_artifact);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END; $$;
        CREATE TRIGGER trg_net_daily_nav_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.net_daily_nav FOR EACH ROW EXECUTE FUNCTION experiment.enforce_net_child_draft();
        CREATE TRIGGER trg_execution_cost_draft BEFORE INSERT OR UPDATE OR DELETE ON experiment.execution_cost FOR EACH ROW EXECUTE FUNCTION experiment.enforce_net_child_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS experiment.enforce_net_child_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS experiment.validate_net_cost_path() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS experiment.enforce_cost_artifact_draft() CASCADE")
    op.drop_table("execution_cost", schema="experiment")
    op.drop_table("net_daily_nav", schema="experiment")
    op.drop_table("net_cost_path", schema="experiment")
    op.drop_table("cost_scenario", schema="experiment")
    op.drop_table("cost_model_version", schema="experiment")
    op.drop_table("cost_model_definition", schema="experiment")
