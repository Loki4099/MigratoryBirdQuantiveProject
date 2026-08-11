"""Add immutable Signal evaluation diagnostics.

Revision ID: 20260804_13_v02_signal_eval
Revises: 20260804_12_v02_forward_ret
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_13_v02_signal_eval"
down_revision: str | None = "20260804_12_v02_forward_ret"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _id(name: str) -> sa.Column[object]:
    return sa.Column(name, UUID, nullable=False)


def _created() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table[:16]}_{column[:24]}", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "signal_evaluation",
        _id("signal_evaluation_id"),
        _id("artifact_id"),
        _id("signal_catalog_artifact_id"),
        _id("universe_version_id"),
        _id("data_bundle_version_id"),
        _id("eligibility_snapshot_id"),
        _id("signal_engine_version_id"),
        _id("evaluation_engine_version_id"),
        _id("forward_return_dataset_id"),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("signal_count", sa.Integer(), nullable=False),
        sa.Column("period_count", sa.BigInteger(), nullable=False),
        sa.Column("pair_count", sa.BigInteger(), nullable=False),
        sa.Column("high_correlation_threshold", sa.Float(), nullable=False),
        _created(),
        _fk("signal_evaluation", "artifact_id", "lineage.artifact.artifact_id"),
        _fk(
            "signal_evaluation",
            "signal_catalog_artifact_id",
            "lineage.artifact.artifact_id",
        ),
        _fk(
            "signal_evaluation",
            "universe_version_id",
            "catalog.universe_version.universe_version_id",
        ),
        _fk(
            "signal_evaluation",
            "data_bundle_version_id",
            "data.data_bundle_version.data_bundle_version_id",
        ),
        _fk(
            "signal_evaluation",
            "eligibility_snapshot_id",
            "catalog.eligibility_snapshot.eligibility_snapshot_id",
        ),
        _fk(
            "signal_evaluation",
            "signal_engine_version_id",
            "ops.engine_version.engine_version_id",
        ),
        _fk(
            "signal_evaluation",
            "evaluation_engine_version_id",
            "ops.engine_version.engine_version_id",
        ),
        _fk(
            "signal_evaluation",
            "forward_return_dataset_id",
            "data.forward_return_dataset.forward_return_dataset_id",
        ),
        sa.PrimaryKeyConstraint("signal_evaluation_id", name="pk_signal_evaluation"),
        sa.UniqueConstraint("artifact_id", name="uq_signal_evaluation_artifact"),
        sa.UniqueConstraint(
            "signal_catalog_artifact_id",
            "forward_return_dataset_id",
            "signal_engine_version_id",
            "evaluation_engine_version_id",
            name="uq_signal_evaluation_context",
        ),
        sa.CheckConstraint("frequency IN ('weekly', 'monthly')", name="ck_signal_eval_frequency"),
        sa.CheckConstraint("coverage_start <= coverage_end", name="ck_signal_eval_coverage"),
        sa.CheckConstraint("signal_count >= 1 AND period_count >= 1", name="ck_signal_eval_counts"),
        sa.CheckConstraint("pair_count >= 0", name="ck_signal_eval_pairs"),
        sa.CheckConstraint(
            "high_correlation_threshold > 0 AND high_correlation_threshold <= 1",
            name="ck_signal_eval_threshold",
        ),
        schema="signal",
    )
    op.create_table(
        "signal_evaluation_period",
        _id("signal_evaluation_id"),
        _id("signal_dataset_id"),
        sa.Column("decision_date", sa.Date(), nullable=False),
        sa.Column("rank_ic", sa.Float()),
        sa.Column("rank_ic_reason", sa.String(100)),
        sa.Column("top_bottom_spread", sa.Float(), nullable=False),
        sa.Column("active_count", sa.Integer(), nullable=False),
        sa.Column("event_count", sa.Integer()),
        _fk(
            "signal_eval_period",
            "signal_evaluation_id",
            "signal.signal_evaluation.signal_evaluation_id",
        ),
        _fk(
            "signal_eval_period",
            "signal_dataset_id",
            "signal.signal_dataset.signal_dataset_id",
        ),
        sa.PrimaryKeyConstraint(
            "signal_evaluation_id",
            "signal_dataset_id",
            "decision_date",
            name="pk_signal_evaluation_period",
        ),
        sa.CheckConstraint(
            "(rank_ic IS NOT NULL AND rank_ic_reason IS NULL) OR "
            "(rank_ic IS NULL AND rank_ic_reason IS NOT NULL)",
            name="ck_signal_eval_period_ic_state",
        ),
        sa.CheckConstraint("rank_ic IS NULL OR rank_ic BETWEEN -1 AND 1", name="ck_signal_eval_ic"),
        sa.CheckConstraint("active_count BETWEEN 0 AND 4", name="ck_signal_eval_active"),
        sa.CheckConstraint(
            "event_count IS NULL OR event_count BETWEEN 0 AND 4", name="ck_signal_eval_events"
        ),
        sa.CheckConstraint(
            "top_bottom_spread NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision) AND "
            "(rank_ic IS NULL OR rank_ic NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision))",
            name="ck_signal_eval_period_finite",
        ),
        schema="signal",
    )
    op.create_table(
        "signal_evaluation_metric",
        _id("signal_evaluation_id"),
        _id("signal_dataset_id"),
        sa.Column("window_key", sa.String(30), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("period_count", sa.Integer(), nullable=False),
        sa.Column("valid_ic_count", sa.Integer(), nullable=False),
        sa.Column("undefined_ic_count", sa.Integer(), nullable=False),
        sa.Column("mean_rank_ic", sa.Float()),
        sa.Column("median_rank_ic", sa.Float()),
        sa.Column("positive_ic_ratio", sa.Float()),
        sa.Column("information_ratio", sa.Float()),
        sa.Column("mean_top_bottom_spread", sa.Float(), nullable=False),
        sa.Column("event_rate", sa.Float()),
        sa.Column("event_asset_concentration", sa.Float()),
        sa.Column("non_neutral_rate", sa.Float(), nullable=False),
        sa.Column("mean_top2_turnover", sa.Float()),
        _fk(
            "signal_eval_metric",
            "signal_evaluation_id",
            "signal.signal_evaluation.signal_evaluation_id",
        ),
        _fk("signal_eval_metric", "signal_dataset_id", "signal.signal_dataset.signal_dataset_id"),
        sa.PrimaryKeyConstraint(
            "signal_evaluation_id",
            "signal_dataset_id",
            "window_key",
            name="pk_signal_evaluation_metric",
        ),
        sa.CheckConstraint("window_start <= window_end", name="ck_signal_eval_metric_window"),
        sa.CheckConstraint(
            "period_count >= 1 AND valid_ic_count + undefined_ic_count = period_count",
            name="ck_signal_eval_metric_counts",
        ),
        sa.CheckConstraint(
            "positive_ic_ratio IS NULL OR positive_ic_ratio BETWEEN 0 AND 1",
            name="ck_signal_eval_positive_ratio",
        ),
        sa.CheckConstraint(
            "(mean_rank_ic IS NULL OR mean_rank_ic BETWEEN -1 AND 1) AND "
            "(median_rank_ic IS NULL OR median_rank_ic BETWEEN -1 AND 1)",
            name="ck_signal_eval_metric_ic_range",
        ),
        sa.CheckConstraint(
            "event_rate IS NULL OR event_rate BETWEEN 0 AND 1", name="ck_signal_eval_event_rate"
        ),
        sa.CheckConstraint(
            "event_asset_concentration IS NULL OR event_asset_concentration BETWEEN 0 AND 1",
            name="ck_signal_eval_event_concentration",
        ),
        sa.CheckConstraint("non_neutral_rate BETWEEN 0 AND 1", name="ck_signal_eval_non_neutral"),
        sa.CheckConstraint(
            "mean_top2_turnover IS NULL OR mean_top2_turnover BETWEEN 0 AND 1",
            name="ck_signal_eval_turnover",
        ),
        sa.CheckConstraint(
            "mean_top_bottom_spread NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision) AND "
            "non_neutral_rate NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision) AND "
            "(mean_rank_ic IS NULL OR mean_rank_ic NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision)) AND "
            "(median_rank_ic IS NULL OR median_rank_ic NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision)) AND "
            "(information_ratio IS NULL OR information_ratio NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision))",
            name="ck_signal_eval_metric_finite",
        ),
        schema="signal",
    )
    op.create_table(
        "signal_pair_diagnostic",
        _id("signal_evaluation_id"),
        _id("left_signal_dataset_id"),
        _id("right_signal_dataset_id"),
        sa.Column("score_observation_count", sa.BigInteger(), nullable=False),
        sa.Column("score_spearman", sa.Float()),
        sa.Column("spread_period_count", sa.Integer(), nullable=False),
        sa.Column("spread_correlation", sa.Float()),
        sa.Column("mean_top2_overlap", sa.Float(), nullable=False),
        sa.Column("high_correlation", sa.Boolean(), nullable=False),
        _fk(
            "signal_pair_diag",
            "signal_evaluation_id",
            "signal.signal_evaluation.signal_evaluation_id",
        ),
        _fk(
            "signal_pair_diag",
            "left_signal_dataset_id",
            "signal.signal_dataset.signal_dataset_id",
        ),
        _fk(
            "signal_pair_diag",
            "right_signal_dataset_id",
            "signal.signal_dataset.signal_dataset_id",
        ),
        sa.PrimaryKeyConstraint(
            "signal_evaluation_id",
            "left_signal_dataset_id",
            "right_signal_dataset_id",
            name="pk_signal_pair_diagnostic",
        ),
        sa.CheckConstraint(
            "left_signal_dataset_id <> right_signal_dataset_id", name="ck_signal_pair_not_self"
        ),
        sa.CheckConstraint(
            "score_observation_count >= 4 AND spread_period_count >= 1",
            name="ck_signal_pair_counts",
        ),
        sa.CheckConstraint(
            "score_spearman IS NULL OR score_spearman BETWEEN -1 AND 1",
            name="ck_signal_pair_score_corr",
        ),
        sa.CheckConstraint(
            "spread_correlation IS NULL OR spread_correlation BETWEEN -1 AND 1",
            name="ck_signal_pair_spread_corr",
        ),
        sa.CheckConstraint("mean_top2_overlap BETWEEN 0 AND 1", name="ck_signal_pair_overlap"),
        sa.CheckConstraint(
            "mean_top2_overlap NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision) AND "
            "(score_spearman IS NULL OR score_spearman NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision)) AND "
            "(spread_correlation IS NULL OR spread_correlation NOT IN "
            "('NaN'::double precision, 'Infinity'::double precision, "
            "'-Infinity'::double precision))",
            name="ck_signal_pair_finite",
        ),
        schema="signal",
    )
    op.create_table(
        "signal_diagnostic_issue",
        _id("signal_diagnostic_issue_id"),
        _id("signal_evaluation_id"),
        _id("signal_dataset_id"),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("issue_code", sa.String(120), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        _created(),
        _fk(
            "signal_diag_issue",
            "signal_evaluation_id",
            "signal.signal_evaluation.signal_evaluation_id",
        ),
        _fk("signal_diag_issue", "signal_dataset_id", "signal.signal_dataset.signal_dataset_id"),
        sa.PrimaryKeyConstraint("signal_diagnostic_issue_id", name="pk_signal_diagnostic_issue"),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')", name="ck_signal_diag_issue_severity"
        ),
        schema="signal",
    )
    _guards()


def _guards() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_signal_evaluation_draft
        BEFORE INSERT OR UPDATE OR DELETE ON signal.signal_evaluation
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();

        CREATE FUNCTION signal.enforce_evaluation_child_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE evaluation_id uuid; owner_artifact_id uuid;
        BEGIN
            evaluation_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.signal_evaluation_id ELSE NEW.signal_evaluation_id END;
            SELECT artifact_id INTO owner_artifact_id FROM signal.signal_evaluation
            WHERE signal_evaluation_id = evaluation_id;
            PERFORM data.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_signal_evaluation_period_draft
        BEFORE INSERT OR UPDATE OR DELETE ON signal.signal_evaluation_period
        FOR EACH ROW EXECUTE FUNCTION signal.enforce_evaluation_child_draft();
        CREATE TRIGGER trg_signal_evaluation_metric_draft
        BEFORE INSERT OR UPDATE OR DELETE ON signal.signal_evaluation_metric
        FOR EACH ROW EXECUTE FUNCTION signal.enforce_evaluation_child_draft();
        CREATE TRIGGER trg_signal_pair_diagnostic_draft
        BEFORE INSERT OR UPDATE OR DELETE ON signal.signal_pair_diagnostic
        FOR EACH ROW EXECUTE FUNCTION signal.enforce_evaluation_child_draft();
        CREATE TRIGGER trg_signal_diagnostic_issue_draft
        BEFORE INSERT OR UPDATE OR DELETE ON signal.signal_diagnostic_issue
        FOR EACH ROW EXECUTE FUNCTION signal.enforce_evaluation_child_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS signal.enforce_evaluation_child_draft() CASCADE")
    op.drop_table("signal_diagnostic_issue", schema="signal")
    op.drop_table("signal_pair_diagnostic", schema="signal")
    op.drop_table("signal_evaluation_metric", schema="signal")
    op.drop_table("signal_evaluation_period", schema="signal")
    op.drop_table("signal_evaluation", schema="signal")
