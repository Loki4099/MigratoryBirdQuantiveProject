# ruff: noqa: E501
"""Add v0.21 queue, qualification, Product, and monitoring state.

Revision ID: 20260805_30_v021_product
Revises: 20260805_29_v021_core
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260805_30_v021_product"
down_revision: str | None = "20260805_29_v021_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
HASH_PATTERN = "^[0-9a-f]{64}$"


def _id(name: str) -> sa.Column[object]:
    return sa.Column(name, UUID, nullable=False)


def _created() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _fk(columns: list[str], target: list[str], name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(columns, target, name=name, ondelete="RESTRICT")


def upgrade() -> None:
    _create_work_queue()
    _create_experiment_identity()
    _create_product_monitoring()


def _create_work_queue() -> None:
    op.create_table(
        "work_item",
        _id("work_item_id"),
        sa.Column("specification_fingerprint", sa.String(64), nullable=False),
        sa.Column("work_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), server_default="queued", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("stage", sa.String(80), server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("lease_owner", sa.String(160)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("failure_class", sa.String(32)),
        sa.Column("failure_details", JSONB),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _created(),
        sa.PrimaryKeyConstraint("work_item_id", name="pk_work_item"),
        sa.CheckConstraint(
            f"specification_fingerprint ~ '{HASH_PATTERN}'", name="ck_work_item_fingerprint"
        ),
        sa.CheckConstraint(
            "work_type IN ('predictive','portfolio','monitoring','export')",
            name="ck_work_item_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled','reused')",
            name="ck_work_item_status",
        ),
        sa.CheckConstraint(
            "failure_class IS NULL OR failure_class IN ('infrastructure','interrupted','data_quality','capacity','contract')",
            name="ck_work_item_failure_class",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_work_item_attempts",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR status <> 'running'",
            name="ck_work_item_running_lease",
        ),
        schema="ops",
    )
    op.create_index(
        "ix_work_item_claim",
        "work_item",
        ["status", "available_at", "priority", "created_at"],
        schema="ops",
    )
    op.create_index(
        "uq_work_item_active_specification",
        "work_item",
        ["specification_fingerprint", "work_type"],
        unique=True,
        schema="ops",
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_table(
        "work_item_event",
        _id("work_item_event_id"),
        _id("work_item_id"),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("from_status", sa.String(24)),
        sa.Column("to_status", sa.String(24)),
        sa.Column("details", JSONB, nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk(["work_item_id"], ["ops.work_item.work_item_id"], "fk_work_item_event_item"),
        sa.PrimaryKeyConstraint("work_item_event_id", name="pk_work_item_event"),
        sa.UniqueConstraint("work_item_id", "sequence_number", name="uq_work_item_event_sequence"),
        sa.CheckConstraint("sequence_number >= 1", name="ck_work_item_event_sequence"),
        schema="ops",
    )


def _create_experiment_identity() -> None:
    op.create_table(
        "benchmark_set",
        _id("benchmark_set_id"),
        _id("artifact_id"),
        sa.Column("benchmark_set_key", sa.String(180), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("primary_benchmark_key", sa.String(180), nullable=False),
        sa.Column("research_benchmark_key", sa.String(220), nullable=False),
        sa.Column("execution_policy", JSONB, nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_benchmark_set_artifact"),
        sa.PrimaryKeyConstraint("benchmark_set_id", name="pk_benchmark_set"),
        sa.UniqueConstraint("artifact_id", name="uq_benchmark_set_artifact"),
        sa.UniqueConstraint("benchmark_set_key", "version_number", name="uq_benchmark_set_version"),
        sa.CheckConstraint("version_number >= 1", name="ck_benchmark_set_version"),
        schema="experiment",
    )
    op.create_table(
        "comparison_context",
        _id("comparison_context_id"),
        _id("artifact_id"),
        _id("benchmark_set_id"),
        _id("data_bundle_artifact_id"),
        _id("universe_history_artifact_id"),
        sa.Column("context_fingerprint", sa.String(64), nullable=False),
        sa.Column("resolved_start", sa.Date(), nullable=False),
        sa.Column("resolved_end", sa.Date(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("state_reset_at", sa.Date(), nullable=False),
        sa.Column("accounting_policy_key", sa.String(160), nullable=False),
        sa.Column("metric_catalog_key", sa.String(160), nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_comparison_context_artifact"),
        _fk(
            ["benchmark_set_id"],
            ["experiment.benchmark_set.benchmark_set_id"],
            "fk_comparison_context_benchmark",
        ),
        _fk(
            ["data_bundle_artifact_id"],
            ["lineage.artifact.artifact_id"],
            "fk_comparison_context_data",
        ),
        _fk(
            ["universe_history_artifact_id"],
            ["lineage.artifact.artifact_id"],
            "fk_comparison_context_universe",
        ),
        sa.PrimaryKeyConstraint("comparison_context_id", name="pk_comparison_context"),
        sa.UniqueConstraint("artifact_id", name="uq_comparison_context_artifact"),
        sa.UniqueConstraint("context_fingerprint", name="uq_comparison_context_fingerprint"),
        sa.CheckConstraint(
            f"context_fingerprint ~ '{HASH_PATTERN}'", name="ck_comparison_context_fingerprint"
        ),
        sa.CheckConstraint(
            "resolved_start <= state_reset_at AND state_reset_at <= resolved_end AND resolved_end <= as_of_date",
            name="ck_comparison_context_dates",
        ),
        schema="experiment",
    )
    op.create_table(
        "qualification_bundle",
        _id("qualification_bundle_id"),
        _id("artifact_id"),
        _id("compiled_strategy_version_id"),
        _id("source_suite_artifact_id"),
        _id("comparison_context_id"),
        sa.Column("portfolio_cell_count", sa.Integer(), nullable=False),
        sa.Column("formal_eligible", sa.Boolean(), nullable=False),
        sa.Column("product_eligible", sa.Boolean(), nullable=False),
        sa.Column("gate_results", JSONB, nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_qualification_bundle_artifact"),
        _fk(
            ["compiled_strategy_version_id"],
            ["strategy.compiled_strategy_version.compiled_strategy_version_id"],
            "fk_qualification_bundle_strategy",
        ),
        _fk(
            ["source_suite_artifact_id"],
            ["lineage.artifact.artifact_id"],
            "fk_qualification_bundle_suite",
        ),
        _fk(
            ["comparison_context_id"],
            ["experiment.comparison_context.comparison_context_id"],
            "fk_qualification_bundle_context",
        ),
        sa.PrimaryKeyConstraint("qualification_bundle_id", name="pk_qualification_bundle"),
        sa.UniqueConstraint("artifact_id", name="uq_qualification_bundle_artifact"),
        sa.UniqueConstraint(
            "compiled_strategy_version_id",
            "source_suite_artifact_id",
            "comparison_context_id",
            name="uq_qualification_bundle_identity",
        ),
        sa.CheckConstraint("portfolio_cell_count = 6", name="ck_qualification_bundle_six_cells"),
        sa.CheckConstraint(
            "product_eligible = FALSE OR formal_eligible = TRUE",
            name="ck_qualification_bundle_product_requires_formal",
        ),
        schema="experiment",
    )


def _create_product_monitoring() -> None:
    op.create_table(
        "monitoring_policy",
        _id("monitoring_policy_id"),
        _id("artifact_id"),
        sa.Column("policy_key", sa.String(180), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("parameters", JSONB, nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_monitoring_policy_artifact"),
        sa.PrimaryKeyConstraint("monitoring_policy_id", name="pk_monitoring_policy"),
        sa.UniqueConstraint("artifact_id", name="uq_monitoring_policy_artifact"),
        sa.UniqueConstraint("policy_key", "version_number", name="uq_monitoring_policy_version"),
        sa.CheckConstraint("version_number >= 1", name="ck_monitoring_policy_version"),
        schema="product",
    )
    op.create_table(
        "product_version",
        _id("product_version_id"),
        _id("artifact_id"),
        _id("compiled_strategy_version_id"),
        _id("qualification_bundle_id"),
        _id("monitoring_policy_id"),
        _id("benchmark_set_id"),
        _id("capital_policy_artifact_id"),
        _id("cost_model_artifact_id"),
        sa.Column("product_key", sa.String(200), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("product_fingerprint", sa.String(64), nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_product_version_artifact"),
        _fk(
            ["compiled_strategy_version_id"],
            ["strategy.compiled_strategy_version.compiled_strategy_version_id"],
            "fk_product_version_strategy",
        ),
        _fk(
            ["qualification_bundle_id"],
            ["experiment.qualification_bundle.qualification_bundle_id"],
            "fk_product_version_qualification",
        ),
        _fk(
            ["monitoring_policy_id"],
            ["product.monitoring_policy.monitoring_policy_id"],
            "fk_product_version_monitoring",
        ),
        _fk(
            ["benchmark_set_id"],
            ["experiment.benchmark_set.benchmark_set_id"],
            "fk_product_version_benchmark",
        ),
        _fk(
            ["capital_policy_artifact_id"],
            ["lineage.artifact.artifact_id"],
            "fk_product_version_capital",
        ),
        _fk(
            ["cost_model_artifact_id"],
            ["lineage.artifact.artifact_id"],
            "fk_product_version_cost",
        ),
        sa.PrimaryKeyConstraint("product_version_id", name="pk_product_version"),
        sa.UniqueConstraint("artifact_id", name="uq_product_version_artifact"),
        sa.UniqueConstraint("product_fingerprint", name="uq_product_version_fingerprint"),
        sa.UniqueConstraint("product_key", "version_number", name="uq_product_version_key_version"),
        sa.CheckConstraint("version_number >= 1", name="ck_product_version_number"),
        sa.CheckConstraint(
            f"product_fingerprint ~ '{HASH_PATTERN}'", name="ck_product_version_fingerprint"
        ),
        schema="product",
    )
    op.create_table(
        "product_enrollment",
        _id("product_enrollment_id"),
        _id("product_version_id"),
        sa.Column("strategy_fingerprint", sa.String(64), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("researcher_id", sa.String(120), nullable=False),
        sa.Column("selection_reason", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("lifecycle", sa.String(24), server_default="active", nullable=False),
        sa.Column("health", sa.String(24), server_default="observing", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("monitoring_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_decision_at", sa.DateTime(timezone=True)),
        sa.Column("first_execution_at", sa.DateTime(timezone=True)),
        sa.Column("first_warning_at", sa.DateTime(timezone=True)),
        sa.Column("review_required_at", sa.DateTime(timezone=True)),
        sa.Column("retirement_requested_at", sa.DateTime(timezone=True)),
        sa.Column("retirement_effective_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _created(),
        _fk(
            ["product_version_id"],
            ["product.product_version.product_version_id"],
            "fk_product_enrollment_version",
        ),
        sa.PrimaryKeyConstraint("product_enrollment_id", name="pk_product_enrollment"),
        sa.CheckConstraint(
            f"strategy_fingerprint ~ '{HASH_PATTERN}'", name="ck_product_enrollment_fingerprint"
        ),
        sa.CheckConstraint(
            "lifecycle IN ('active','suspended','retired','invalidated')",
            name="ck_product_enrollment_lifecycle",
        ),
        sa.CheckConstraint(
            "health IN ('observing','healthy','watch','warning','data_interrupted')",
            name="ck_product_enrollment_health",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_product_enrollment_revision"),
        sa.CheckConstraint(
            "activated_at <= monitoring_start_at", name="ck_product_enrollment_monitoring_start"
        ),
        schema="product",
    )
    op.create_index(
        "uq_product_enrollment_active_strategy",
        "product_enrollment",
        ["strategy_fingerprint"],
        unique=True,
        schema="product",
        postgresql_where=sa.text("lifecycle IN ('active','suspended')"),
    )
    op.create_index(
        "ix_product_enrollment_catalog",
        "product_enrollment",
        ["lifecycle", "health", "updated_at"],
        schema="product",
    )
    op.create_table(
        "product_lifecycle_event",
        _id("product_lifecycle_event_id"),
        _id("product_enrollment_id"),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("from_lifecycle", sa.String(24)),
        sa.Column("to_lifecycle", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("researcher_id", sa.String(120), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        _created(),
        _fk(
            ["product_enrollment_id"],
            ["product.product_enrollment.product_enrollment_id"],
            "fk_product_lifecycle_event_enrollment",
        ),
        sa.PrimaryKeyConstraint("product_lifecycle_event_id", name="pk_product_lifecycle_event"),
        sa.UniqueConstraint(
            "product_enrollment_id", "sequence_number", name="uq_product_lifecycle_event_sequence"
        ),
        sa.CheckConstraint("sequence_number >= 1", name="ck_product_lifecycle_event_sequence"),
        sa.CheckConstraint("requested_at <= effective_at", name="ck_product_lifecycle_event_dates"),
        schema="product",
    )
    op.create_table(
        "monitoring_snapshot",
        _id("monitoring_snapshot_id"),
        _id("artifact_id"),
        _id("product_enrollment_id"),
        _id("data_bundle_artifact_id"),
        sa.Column("as_of_session", sa.Date(), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("health", sa.String(24), nullable=False),
        sa.Column("session_count", sa.Integer(), nullable=False),
        sa.Column("decision_count", sa.Integer(), nullable=False),
        sa.Column("primary_nav", sa.Numeric(24, 8), nullable=False),
        sa.Column("stress_nav", sa.Numeric(24, 8), nullable=False),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column("health_components", JSONB, nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_monitoring_snapshot_artifact"),
        _fk(
            ["product_enrollment_id"],
            ["product.product_enrollment.product_enrollment_id"],
            "fk_monitoring_snapshot_enrollment",
        ),
        _fk(
            ["data_bundle_artifact_id"],
            ["lineage.artifact.artifact_id"],
            "fk_monitoring_snapshot_data",
        ),
        sa.PrimaryKeyConstraint("monitoring_snapshot_id", name="pk_monitoring_snapshot"),
        sa.UniqueConstraint("artifact_id", name="uq_monitoring_snapshot_artifact"),
        sa.UniqueConstraint(
            "product_enrollment_id",
            "as_of_session",
            "known_at",
            name="uq_monitoring_snapshot_vintage",
        ),
        sa.CheckConstraint(
            "health IN ('observing','healthy','watch','warning','data_interrupted')",
            name="ck_monitoring_snapshot_health",
        ),
        sa.CheckConstraint(
            "session_count >= 0 AND decision_count >= 0", name="ck_monitoring_snapshot_counts"
        ),
        sa.CheckConstraint("primary_nav > 0 AND stress_nav > 0", name="ck_monitoring_snapshot_nav"),
        schema="product",
    )
    op.create_index(
        "ix_monitoring_snapshot_enrollment_session",
        "monitoring_snapshot",
        ["product_enrollment_id", "as_of_session", "known_at"],
        schema="product",
    )
    op.create_table(
        "product_alert",
        _id("product_alert_id"),
        _id("product_enrollment_id"),
        sa.Column("alert_key", sa.String(180), nullable=False),
        sa.Column("alert_type", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        _created(),
        _fk(
            ["product_enrollment_id"],
            ["product.product_enrollment.product_enrollment_id"],
            "fk_product_alert_enrollment",
        ),
        sa.PrimaryKeyConstraint("product_alert_id", name="pk_product_alert"),
        sa.UniqueConstraint("product_enrollment_id", "alert_key", name="uq_product_alert_key"),
        sa.CheckConstraint(
            "severity IN ('info','watch','warning','critical')", name="ck_product_alert_severity"
        ),
        schema="product",
    )
    op.create_table(
        "product_alert_event",
        _id("product_alert_event_id"),
        _id("product_alert_id"),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(24)),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("researcher_id", sa.String(120)),
        sa.Column("note", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _created(),
        _fk(
            ["product_alert_id"],
            ["product.product_alert.product_alert_id"],
            "fk_product_alert_event_alert",
        ),
        sa.PrimaryKeyConstraint("product_alert_event_id", name="pk_product_alert_event"),
        sa.UniqueConstraint(
            "product_alert_id", "sequence_number", name="uq_product_alert_event_sequence"
        ),
        sa.CheckConstraint("sequence_number >= 1", name="ck_product_alert_event_sequence"),
        sa.CheckConstraint(
            "to_status IN ('open','acknowledged','resolved','superseded')",
            name="ck_product_alert_event_status",
        ),
        schema="product",
    )
    op.create_table(
        "product_review",
        _id("product_review_id"),
        _id("product_enrollment_id"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("researcher_id", sa.String(120), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        _created(),
        _fk(
            ["product_enrollment_id"],
            ["product.product_enrollment.product_enrollment_id"],
            "fk_product_review_enrollment",
        ),
        sa.PrimaryKeyConstraint("product_review_id", name="pk_product_review"),
        sa.CheckConstraint(
            "decision IN ('continue','suspend','retire','replace')",
            name="ck_product_review_decision",
        ),
        schema="product",
    )


def downgrade() -> None:
    op.drop_table("product_review", schema="product")
    op.drop_table("product_alert_event", schema="product")
    op.drop_table("product_alert", schema="product")
    op.drop_index(
        "ix_monitoring_snapshot_enrollment_session",
        table_name="monitoring_snapshot",
        schema="product",
    )
    op.drop_table("monitoring_snapshot", schema="product")
    op.drop_table("product_lifecycle_event", schema="product")
    op.drop_index(
        "ix_product_enrollment_catalog", table_name="product_enrollment", schema="product"
    )
    op.drop_index(
        "uq_product_enrollment_active_strategy",
        table_name="product_enrollment",
        schema="product",
    )
    op.drop_table("product_enrollment", schema="product")
    op.drop_table("product_version", schema="product")
    op.drop_table("monitoring_policy", schema="product")
    op.drop_table("qualification_bundle", schema="experiment")
    op.drop_table("comparison_context", schema="experiment")
    op.drop_table("benchmark_set", schema="experiment")
    op.drop_table("work_item_event", schema="ops")
    op.drop_index("uq_work_item_active_specification", table_name="work_item", schema="ops")
    op.drop_index("ix_work_item_claim", table_name="work_item", schema="ops")
    op.drop_table("work_item", schema="ops")
