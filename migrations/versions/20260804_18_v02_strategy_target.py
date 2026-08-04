"""Add immutable Strategy target paths and per-candidate decisions.

Revision ID: 20260804_18_v02_strategy_target
Revises: 20260804_17_v02_strategy_core
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_18_v02_strategy_target"
down_revision: str | None = "20260804_17_v02_strategy_core"
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
        "portfolio_target_path",
        _id("portfolio_target_path_id"),
        _id("artifact_id"),
        _id("universe_version_id"),
        _id("data_bundle_version_id"),
        _id("eligibility_snapshot_id"),
        _id("engine_version_id"),
        sa.Column("target_type", sa.String(30), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("decision_count", sa.BigInteger(), nullable=False),
        sa.Column("position_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("portfolio_target", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "portfolio_target",
            "universe_version_id",
            "catalog.universe_version.universe_version_id",
        ),
        _fk(
            "portfolio_target",
            "data_bundle_version_id",
            "data.data_bundle_version.data_bundle_version_id",
        ),
        _fk(
            "portfolio_target",
            "eligibility_snapshot_id",
            "catalog.eligibility_snapshot.eligibility_snapshot_id",
        ),
        _fk("portfolio_target", "engine_version_id", "ops.engine_version.engine_version_id"),
        sa.PrimaryKeyConstraint("portfolio_target_path_id", name="pk_portfolio_target_path"),
        sa.UniqueConstraint("artifact_id", name="uq_portfolio_target_path_artifact"),
        sa.CheckConstraint(
            "target_type IN ('model_strategy', 'benchmark')", name="ck_target_path_type"
        ),
        sa.CheckConstraint("coverage_start <= coverage_end", name="ck_target_path_coverage"),
        sa.CheckConstraint(
            "decision_count >= 1 AND position_count >= decision_count",
            name="ck_target_path_counts",
        ),
        schema="strategy",
    )
    op.create_table(
        "model_strategy_target_path",
        _id("portfolio_target_path_id"),
        _id("strategy_product_version_id"),
        _id("model_dataset_id"),
        _fk(
            "model_target_path",
            "portfolio_target_path_id",
            "strategy.portfolio_target_path.portfolio_target_path_id",
        ),
        _fk(
            "model_target_path",
            "strategy_product_version_id",
            "strategy.strategy_product_version.strategy_product_version_id",
        ),
        _fk("model_target_path", "model_dataset_id", "model.model_dataset.model_dataset_id"),
        sa.PrimaryKeyConstraint("portfolio_target_path_id", name="pk_model_strategy_target_path"),
        sa.UniqueConstraint(
            "strategy_product_version_id",
            "model_dataset_id",
            name="uq_model_strategy_target_exact_inputs",
        ),
        schema="strategy",
    )
    op.create_table(
        "benchmark_target_path",
        _id("portfolio_target_path_id"),
        _id("benchmark_asset_id"),
        _fk(
            "benchmark_target",
            "portfolio_target_path_id",
            "strategy.portfolio_target_path.portfolio_target_path_id",
        ),
        _fk("benchmark_target", "benchmark_asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint("portfolio_target_path_id", name="pk_benchmark_target_path"),
        schema="strategy",
    )
    op.create_table(
        "target_path_auxiliary_input",
        _id("portfolio_target_path_id"),
        _id("signal_dataset_id"),
        sa.Column("role", sa.String(80), nullable=False),
        _fk(
            "target_aux_input",
            "portfolio_target_path_id",
            "strategy.portfolio_target_path.portfolio_target_path_id",
        ),
        _fk("target_aux_input", "signal_dataset_id", "signal.signal_dataset.signal_dataset_id"),
        sa.PrimaryKeyConstraint(
            "portfolio_target_path_id", "signal_dataset_id", name="pk_target_path_aux_input"
        ),
        sa.UniqueConstraint("portfolio_target_path_id", "role", name="uq_target_path_aux_role"),
        schema="strategy",
    )
    op.create_table(
        "portfolio_decision",
        _id("portfolio_decision_id"),
        _id("portfolio_target_path_id"),
        sa.Column("decision_date", sa.Date(), nullable=False),
        sa.Column("target_k", sa.Integer(), nullable=False),
        sa.Column("actual_holding_count", sa.Integer(), nullable=False),
        sa.Column("boundary_tie_count", sa.Integer(), nullable=False),
        sa.Column("reserve_target_weight", sa.Numeric(24, 18), nullable=False),
        _fk(
            "portfolio_decision",
            "portfolio_target_path_id",
            "strategy.portfolio_target_path.portfolio_target_path_id",
        ),
        sa.PrimaryKeyConstraint("portfolio_decision_id", name="pk_portfolio_decision"),
        sa.UniqueConstraint(
            "portfolio_target_path_id", "decision_date", name="uq_portfolio_decision_date"
        ),
        sa.CheckConstraint("target_k IN (1, 2, 3)", name="ck_portfolio_decision_k"),
        sa.CheckConstraint(
            "actual_holding_count >= 0 AND boundary_tie_count >= 0",
            name="ck_portfolio_decision_counts",
        ),
        sa.CheckConstraint(
            "reserve_target_weight BETWEEN 0 AND 1", name="ck_decision_reserve_weight"
        ),
        schema="strategy",
    )
    op.create_table(
        "target_asset_position",
        _id("portfolio_decision_id"),
        _id("asset_id"),
        sa.Column("model_score", sa.Numeric(24, 18), nullable=False),
        sa.Column("model_rank", sa.Numeric(12, 6), nullable=False),
        sa.Column("selection_rank", sa.Numeric(12, 6)),
        sa.Column("trend_state", sa.String(20)),
        sa.Column("strategy_eligible", sa.Boolean(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("target_weight", sa.Numeric(24, 18), nullable=False),
        sa.Column("decision_reason", sa.String(100), nullable=False),
        _fk(
            "target_position",
            "portfolio_decision_id",
            "strategy.portfolio_decision.portfolio_decision_id",
        ),
        _fk("target_position", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint(
            "portfolio_decision_id", "asset_id", name="pk_target_asset_position"
        ),
        sa.CheckConstraint(
            "model_score BETWEEN -1 AND 1 AND target_weight BETWEEN 0 AND 1",
            name="ck_target_position_numeric",
        ),
        sa.CheckConstraint(
            "model_rank >= 1 AND (selection_rank IS NULL OR selection_rank >= 1)",
            name="ck_target_position_ranks",
        ),
        sa.CheckConstraint(
            "trend_state IS NULL OR trend_state IN ('positive', 'negative', 'neutral')",
            name="ck_target_position_trend_state",
        ),
        sa.CheckConstraint("selected = (target_weight > 0)", name="ck_target_selected_weight"),
        schema="strategy",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION strategy.validate_target_owner() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE engine_key_value text; eligibility_row record;
        BEGIN
            PERFORM data.assert_artifact_draft(
                CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END
            );
            IF TG_OP <> 'DELETE' THEN
                SELECT definition.engine_key INTO engine_key_value
                FROM ops.engine_version version JOIN ops.engine_definition definition
                ON definition.engine_definition_id = version.engine_definition_id
                WHERE version.engine_version_id = NEW.engine_version_id;
                IF engine_key_value <> 'strategy_target_engine' THEN
                    RAISE EXCEPTION 'target path requires a Strategy Target engine';
                END IF;
                SELECT universe_version_id, data_bundle_version_id INTO eligibility_row
                FROM catalog.eligibility_snapshot
                WHERE eligibility_snapshot_id = NEW.eligibility_snapshot_id;
                IF eligibility_row.universe_version_id <> NEW.universe_version_id
                   OR eligibility_row.data_bundle_version_id <> NEW.data_bundle_version_id THEN
                    RAISE EXCEPTION 'target path context does not match eligibility';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_portfolio_target_path_draft
        BEFORE INSERT OR UPDATE OR DELETE ON strategy.portfolio_target_path
        FOR EACH ROW EXECUTE FUNCTION strategy.validate_target_owner();

        CREATE FUNCTION strategy.enforce_target_child_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE path_id uuid; owner record; product_row record; model_row record;
                signal_row record; required_signal_id uuid;
        BEGIN
            IF TG_TABLE_NAME = 'portfolio_decision' THEN
                path_id := CASE WHEN TG_OP = 'DELETE'
                    THEN OLD.portfolio_target_path_id ELSE NEW.portfolio_target_path_id END;
            ELSIF TG_TABLE_NAME = 'target_asset_position' THEN
                SELECT portfolio_target_path_id INTO path_id FROM strategy.portfolio_decision
                WHERE portfolio_decision_id = COALESCE(NEW.portfolio_decision_id,
                                                       OLD.portfolio_decision_id);
            ELSE
                path_id := CASE WHEN TG_OP = 'DELETE'
                    THEN OLD.portfolio_target_path_id ELSE NEW.portfolio_target_path_id END;
            END IF;
            SELECT * INTO owner FROM strategy.portfolio_target_path
            WHERE portfolio_target_path_id = path_id;
            PERFORM data.assert_artifact_draft(owner.artifact_id);
            IF TG_TABLE_NAME = 'model_strategy_target_path' AND TG_OP <> 'DELETE' THEN
                SELECT product.universe_version_id, product.model_specification_id
                INTO product_row FROM strategy.strategy_product_version product
                WHERE product.strategy_product_version_id = NEW.strategy_product_version_id;
                SELECT dataset.universe_version_id, dataset.data_bundle_version_id,
                       dataset.eligibility_snapshot_id, dataset.model_specification_id
                INTO model_row FROM model.model_dataset dataset
                WHERE dataset.model_dataset_id = NEW.model_dataset_id;
                IF product_row.universe_version_id <> owner.universe_version_id
                   OR model_row.universe_version_id <> owner.universe_version_id
                   OR model_row.data_bundle_version_id <> owner.data_bundle_version_id
                   OR model_row.eligibility_snapshot_id <> owner.eligibility_snapshot_id
                   OR product_row.model_specification_id <> model_row.model_specification_id THEN
                    RAISE EXCEPTION 'Strategy Product, Model Dataset, and target context mismatch';
                END IF;
            ELSIF TG_TABLE_NAME = 'target_path_auxiliary_input' AND TG_OP <> 'DELETE' THEN
                SELECT dataset.signal_version_id, dataset.universe_version_id,
                       dataset.data_bundle_version_id, dataset.eligibility_snapshot_id
                INTO signal_row FROM signal.signal_dataset dataset
                WHERE dataset.signal_dataset_id = NEW.signal_dataset_id;
                SELECT variant.auxiliary_signal_version_id INTO required_signal_id
                FROM strategy.model_strategy_target_path model_path
                JOIN strategy.strategy_product_version product ON
                     product.strategy_product_version_id = model_path.strategy_product_version_id
                JOIN strategy.strategy_variant variant ON
                     variant.strategy_variant_id = product.strategy_variant_id
                WHERE model_path.portfolio_target_path_id = path_id;
                IF signal_row.signal_version_id <> required_signal_id
                   OR signal_row.universe_version_id <> owner.universe_version_id
                   OR signal_row.data_bundle_version_id <> owner.data_bundle_version_id
                   OR signal_row.eligibility_snapshot_id <> owner.eligibility_snapshot_id THEN
                    RAISE EXCEPTION 'auxiliary Signal Dataset and target context mismatch';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_model_strategy_target_draft
        BEFORE INSERT OR UPDATE OR DELETE ON strategy.model_strategy_target_path
        FOR EACH ROW EXECUTE FUNCTION strategy.enforce_target_child_draft();
        CREATE TRIGGER trg_benchmark_target_draft
        BEFORE INSERT OR UPDATE OR DELETE ON strategy.benchmark_target_path
        FOR EACH ROW EXECUTE FUNCTION strategy.enforce_target_child_draft();
        CREATE TRIGGER trg_target_aux_input_draft
        BEFORE INSERT OR UPDATE OR DELETE ON strategy.target_path_auxiliary_input
        FOR EACH ROW EXECUTE FUNCTION strategy.enforce_target_child_draft();
        CREATE TRIGGER trg_portfolio_decision_draft
        BEFORE INSERT OR UPDATE OR DELETE ON strategy.portfolio_decision
        FOR EACH ROW EXECUTE FUNCTION strategy.enforce_target_child_draft();
        CREATE TRIGGER trg_target_asset_position_draft
        BEFORE INSERT OR UPDATE OR DELETE ON strategy.target_asset_position
        FOR EACH ROW EXECUTE FUNCTION strategy.enforce_target_child_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS strategy.enforce_target_child_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS strategy.validate_target_owner() CASCADE")
    op.drop_table("target_asset_position", schema="strategy")
    op.drop_table("portfolio_decision", schema="strategy")
    op.drop_table("target_path_auxiliary_input", schema="strategy")
    op.drop_table("benchmark_target_path", schema="strategy")
    op.drop_table("model_strategy_target_path", schema="strategy")
    op.drop_table("portfolio_target_path", schema="strategy")
