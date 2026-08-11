# ruff: noqa: E501
"""Add v0.21 stable catalog, partition, and compilation identity.

Revision ID: 20260805_29_v021_core
Revises: 20260805_28_v02_target_engine
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260805_29_v021_core"
down_revision: str | None = "20260805_28_v02_target_engine"
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
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "workspace"'))
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "product"'))
    _create_stable_security_identity()
    _create_dynamic_universe_identity()
    _create_partition_manifests()
    _create_workspace_compilation()


def _create_stable_security_identity() -> None:
    op.create_table(
        "issuer",
        _id("issuer_id"),
        sa.Column("issuer_key", sa.String(160), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("country_code", sa.String(2)),
        _created(),
        sa.PrimaryKeyConstraint("issuer_id", name="pk_issuer"),
        sa.UniqueConstraint("issuer_key", name="uq_issuer_key"),
        sa.CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'", name="ck_issuer_country"
        ),
        schema="catalog",
    )
    op.create_table(
        "security",
        _id("security_id"),
        sa.Column("issuer_id", UUID),
        sa.Column("legacy_asset_id", UUID),
        sa.Column("security_key", sa.String(180), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("instrument_type", sa.String(60), nullable=False),
        sa.Column("currency", sa.String(3)),
        sa.Column("status", sa.String(24), server_default="active", nullable=False),
        _created(),
        _fk(["issuer_id"], ["catalog.issuer.issuer_id"], "fk_security_issuer"),
        _fk(["legacy_asset_id"], ["catalog.asset.asset_id"], "fk_security_legacy_asset"),
        sa.PrimaryKeyConstraint("security_id", name="pk_security"),
        sa.UniqueConstraint("security_key", name="uq_security_key"),
        sa.UniqueConstraint("legacy_asset_id", name="uq_security_legacy_asset"),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="ck_security_currency"
        ),
        sa.CheckConstraint(
            "status IN ('active','inactive','terminated','reference')", name="ck_security_status"
        ),
        schema="catalog",
    )
    op.create_index(
        "ix_security_type_status",
        "security",
        ["instrument_type", "status"],
        schema="catalog",
    )
    op.create_table(
        "security_identifier",
        _id("security_identifier_id"),
        _id("security_id"),
        sa.Column("identifier_type", sa.String(40), nullable=False),
        sa.Column("identifier_value", sa.String(120), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        _created(),
        _fk(["security_id"], ["catalog.security.security_id"], "fk_security_identifier_security"),
        sa.PrimaryKeyConstraint("security_identifier_id", name="pk_security_identifier"),
        sa.UniqueConstraint(
            "identifier_type",
            "identifier_value",
            "valid_from",
            name="uq_security_identifier_period",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to",
            name="ck_security_identifier_period",
        ),
        schema="catalog",
    )
    op.create_index(
        "ix_security_identifier_lookup",
        "security_identifier",
        ["identifier_value", "identifier_type"],
        schema="catalog",
    )
    op.create_table(
        "security_capability",
        _id("security_capability_id"),
        _id("security_id"),
        sa.Column("capability_key", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(100)),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        _created(),
        _fk(["security_id"], ["catalog.security.security_id"], "fk_security_capability_security"),
        sa.PrimaryKeyConstraint("security_capability_id", name="pk_security_capability"),
        sa.UniqueConstraint(
            "security_id", "capability_key", "valid_from", name="uq_security_capability_period"
        ),
        sa.CheckConstraint(
            "status IN ('available','exploratory','blocked','display_only')",
            name="ck_security_capability_status",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to",
            name="ck_security_capability_period",
        ),
        schema="catalog",
    )


def _create_dynamic_universe_identity() -> None:
    op.create_table(
        "universe_methodology",
        _id("universe_methodology_id"),
        _id("artifact_id"),
        sa.Column("methodology_key", sa.String(180), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("research_mode", sa.String(24), nullable=False),
        sa.Column("parameters", JSONB, nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_universe_methodology_artifact"),
        sa.PrimaryKeyConstraint("universe_methodology_id", name="pk_universe_methodology"),
        sa.UniqueConstraint("artifact_id", name="uq_universe_methodology_artifact"),
        sa.UniqueConstraint(
            "methodology_key", "version_number", name="uq_universe_methodology_version"
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_universe_methodology_version"),
        sa.CheckConstraint(
            "research_mode IN ('formal','exploratory')", name="ck_universe_methodology_mode"
        ),
        schema="catalog",
    )
    op.create_table(
        "universe_history",
        _id("universe_history_id"),
        _id("artifact_id"),
        _id("universe_methodology_id"),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("snapshot_count", sa.Integer(), nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_universe_history_artifact"),
        _fk(
            ["universe_methodology_id"],
            ["catalog.universe_methodology.universe_methodology_id"],
            "fk_universe_history_methodology",
        ),
        sa.PrimaryKeyConstraint("universe_history_id", name="pk_universe_history"),
        sa.UniqueConstraint("artifact_id", name="uq_universe_history_artifact"),
        sa.CheckConstraint("snapshot_count >= 1", name="ck_universe_history_snapshots"),
        schema="catalog",
    )
    op.create_table(
        "universe_snapshot",
        _id("universe_snapshot_id"),
        _id("universe_history_id"),
        sa.Column("rank_date", sa.Date(), nullable=False),
        sa.Column("data_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_session", sa.Date(), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        _created(),
        _fk(
            ["universe_history_id"],
            ["catalog.universe_history.universe_history_id"],
            "fk_universe_snapshot_history",
        ),
        sa.PrimaryKeyConstraint("universe_snapshot_id", name="pk_universe_snapshot"),
        sa.UniqueConstraint(
            "universe_history_id", "effective_session", name="uq_universe_snapshot_effective"
        ),
        sa.CheckConstraint("member_count >= 1", name="ck_universe_snapshot_members"),
        sa.CheckConstraint("data_cutoff_at <= published_at", name="ck_universe_snapshot_cutoff"),
        schema="catalog",
    )
    op.create_table(
        "universe_candidate_evaluation",
        _id("universe_candidate_evaluation_id"),
        _id("universe_snapshot_id"),
        _id("security_id"),
        sa.Column("issuer_id", UUID),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("reason_codes", JSONB, nullable=False),
        sa.Column("market_cap_rank", sa.Integer()),
        sa.Column("liquidity_rank", sa.Integer()),
        sa.Column("market_cap", sa.Numeric(24, 4)),
        sa.Column("median_dollar_volume_60", sa.Numeric(24, 4)),
        _created(),
        _fk(
            ["universe_snapshot_id"],
            ["catalog.universe_snapshot.universe_snapshot_id"],
            "fk_universe_candidate_snapshot",
        ),
        _fk(["security_id"], ["catalog.security.security_id"], "fk_universe_candidate_security"),
        _fk(["issuer_id"], ["catalog.issuer.issuer_id"], "fk_universe_candidate_issuer"),
        sa.PrimaryKeyConstraint(
            "universe_candidate_evaluation_id", name="pk_universe_candidate_evaluation"
        ),
        sa.UniqueConstraint(
            "universe_snapshot_id", "security_id", name="uq_universe_candidate_security"
        ),
        sa.CheckConstraint(
            "market_cap_rank IS NULL OR market_cap_rank >= 1", name="ck_universe_candidate_cap_rank"
        ),
        sa.CheckConstraint(
            "liquidity_rank IS NULL OR liquidity_rank >= 1", name="ck_universe_candidate_liq_rank"
        ),
        schema="catalog",
    )
    op.create_table(
        "universe_snapshot_member",
        _id("universe_snapshot_id"),
        _id("security_id"),
        sa.Column("issuer_id", UUID),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("primary_selection_security", sa.Boolean(), nullable=False),
        _created(),
        _fk(
            ["universe_snapshot_id"],
            ["catalog.universe_snapshot.universe_snapshot_id"],
            "fk_universe_member_snapshot",
        ),
        _fk(["security_id"], ["catalog.security.security_id"], "fk_universe_member_security"),
        _fk(["issuer_id"], ["catalog.issuer.issuer_id"], "fk_universe_member_issuer"),
        sa.PrimaryKeyConstraint(
            "universe_snapshot_id", "security_id", name="pk_universe_snapshot_member"
        ),
        sa.UniqueConstraint(
            "universe_snapshot_id", "ordinal", name="uq_universe_snapshot_member_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_universe_snapshot_member_ordinal"),
        schema="catalog",
    )
    op.create_index(
        "uq_universe_snapshot_primary_issuer",
        "universe_snapshot_member",
        ["universe_snapshot_id", "issuer_id"],
        unique=True,
        schema="catalog",
        postgresql_where=sa.text("issuer_id IS NOT NULL AND primary_selection_security"),
    )


def _create_partition_manifests() -> None:
    op.create_table(
        "value_partition",
        _id("value_partition_id"),
        sa.Column("stage_key", sa.String(40), nullable=False),
        sa.Column("definition_key", sa.String(200), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("asset_count", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("partition_hash", sa.String(64), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        _created(),
        sa.PrimaryKeyConstraint("value_partition_id", name="pk_value_partition"),
        sa.UniqueConstraint("partition_hash", name="uq_value_partition_hash"),
        sa.CheckConstraint("coverage_start <= coverage_end", name="ck_value_partition_dates"),
        sa.CheckConstraint("asset_count >= 1 AND row_count >= 1", name="ck_value_partition_counts"),
        sa.CheckConstraint("partition_hash ~ '^[0-9a-f]{64}$'", name="ck_value_partition_hash"),
        schema="data",
    )
    op.create_index(
        "ix_value_partition_stage_definition_date",
        "value_partition",
        ["stage_key", "definition_key", "coverage_start", "coverage_end"],
        schema="data",
    )
    op.create_table(
        "dataset_manifest",
        _id("dataset_manifest_id"),
        _id("artifact_id"),
        sa.Column("stage_key", sa.String(40), nullable=False),
        sa.Column("definition_key", sa.String(200), nullable=False),
        sa.Column("partition_count", sa.Integer(), nullable=False),
        sa.Column("missing_count", sa.BigInteger(), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_dataset_manifest_artifact"),
        sa.PrimaryKeyConstraint("dataset_manifest_id", name="pk_dataset_manifest"),
        sa.UniqueConstraint("artifact_id", name="uq_dataset_manifest_artifact"),
        sa.UniqueConstraint("manifest_hash", name="uq_dataset_manifest_hash"),
        sa.CheckConstraint(
            "partition_count >= 1 AND missing_count >= 0", name="ck_dataset_manifest_counts"
        ),
        sa.CheckConstraint("manifest_hash ~ '^[0-9a-f]{64}$'", name="ck_dataset_manifest_hash"),
        schema="data",
    )
    op.create_table(
        "dataset_manifest_partition",
        _id("dataset_manifest_id"),
        _id("value_partition_id"),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _created(),
        _fk(
            ["dataset_manifest_id"],
            ["data.dataset_manifest.dataset_manifest_id"],
            "fk_manifest_partition_manifest",
        ),
        _fk(
            ["value_partition_id"],
            ["data.value_partition.value_partition_id"],
            "fk_manifest_partition_value",
        ),
        sa.PrimaryKeyConstraint(
            "dataset_manifest_id", "value_partition_id", name="pk_dataset_manifest_partition"
        ),
        sa.UniqueConstraint(
            "dataset_manifest_id", "ordinal", name="uq_dataset_manifest_partition_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_dataset_manifest_partition_ordinal"),
        schema="data",
    )
    op.create_table(
        "missing_observation",
        _id("missing_observation_id"),
        _id("dataset_manifest_id"),
        _id("security_id"),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False),
        _created(),
        _fk(
            ["dataset_manifest_id"],
            ["data.dataset_manifest.dataset_manifest_id"],
            "fk_missing_observation_manifest",
        ),
        _fk(["security_id"], ["catalog.security.security_id"], "fk_missing_observation_security"),
        sa.PrimaryKeyConstraint("missing_observation_id", name="pk_missing_observation"),
        sa.UniqueConstraint(
            "dataset_manifest_id",
            "security_id",
            "session_date",
            name="uq_missing_observation_point",
        ),
        schema="data",
    )
    op.create_index(
        "ix_missing_observation_date_security",
        "missing_observation",
        ["session_date", "security_id"],
        schema="data",
    )


def _create_workspace_compilation() -> None:
    op.create_table(
        "research_draft",
        _id("research_draft_id"),
        sa.Column("draft_key", sa.String(180), nullable=False),
        sa.Column("researcher_id", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("selection", JSONB, nullable=False),
        sa.Column("last_compiled_artifact_id", UUID),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _created(),
        _fk(
            ["last_compiled_artifact_id"],
            ["lineage.artifact.artifact_id"],
            "fk_research_draft_last_compiled",
        ),
        sa.PrimaryKeyConstraint("research_draft_id", name="pk_research_draft"),
        sa.UniqueConstraint("researcher_id", "draft_key", name="uq_research_draft_researcher_key"),
        sa.CheckConstraint("revision >= 1", name="ck_research_draft_revision"),
        schema="workspace",
    )
    op.create_table(
        "compiled_research_spec",
        _id("compiled_research_spec_id"),
        _id("artifact_id"),
        sa.Column("specification_fingerprint", sa.String(64), nullable=False),
        sa.Column("asset_context_key", sa.String(200), nullable=False),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("normalized_selection", JSONB, nullable=False),
        sa.Column("model_instance_count", sa.Integer(), nullable=False),
        sa.Column("strategy_branch_count", sa.Integer(), nullable=False),
        sa.Column("predictive_cell_count", sa.Integer(), nullable=False),
        sa.Column("portfolio_cell_count", sa.Integer(), nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_compiled_spec_artifact"),
        sa.PrimaryKeyConstraint("compiled_research_spec_id", name="pk_compiled_research_spec"),
        sa.UniqueConstraint("artifact_id", name="uq_compiled_research_spec_artifact"),
        sa.UniqueConstraint(
            "specification_fingerprint", name="uq_compiled_research_spec_fingerprint"
        ),
        sa.CheckConstraint(
            f"specification_fingerprint ~ '{HASH_PATTERN}'", name="ck_compiled_spec_fingerprint"
        ),
        sa.CheckConstraint("frequency IN ('weekly','monthly')", name="ck_compiled_spec_frequency"),
        sa.CheckConstraint(
            "model_instance_count >= 1 AND strategy_branch_count >= 1 AND predictive_cell_count >= 1 AND portfolio_cell_count >= 6",
            name="ck_compiled_spec_counts",
        ),
        schema="workspace",
    )
    op.create_table(
        "compiled_model_instance",
        _id("compiled_model_instance_id"),
        _id("compiled_research_spec_id"),
        sa.Column("instance_key", sa.String(240), nullable=False),
        sa.Column("preset_key", sa.String(200), nullable=False),
        sa.Column("family_key", sa.String(160), nullable=False),
        sa.Column("output_type", sa.String(40), nullable=False),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("slot_assignments", JSONB, nullable=False),
        sa.Column("instance_fingerprint", sa.String(64), nullable=False),
        _created(),
        _fk(
            ["compiled_research_spec_id"],
            ["workspace.compiled_research_spec.compiled_research_spec_id"],
            "fk_compiled_model_spec",
        ),
        sa.PrimaryKeyConstraint("compiled_model_instance_id", name="pk_compiled_model_instance"),
        sa.UniqueConstraint(
            "compiled_research_spec_id", "instance_key", name="uq_compiled_model_instance_key"
        ),
        sa.UniqueConstraint("instance_fingerprint", name="uq_compiled_model_instance_fingerprint"),
        sa.CheckConstraint(
            "output_type IN ('continuous_score','directional_score')",
            name="ck_compiled_model_output",
        ),
        sa.CheckConstraint("frequency IN ('weekly','monthly')", name="ck_compiled_model_frequency"),
        sa.CheckConstraint(
            f"instance_fingerprint ~ '{HASH_PATTERN}'", name="ck_compiled_model_fingerprint"
        ),
        schema="workspace",
    )
    op.create_table(
        "compiled_strategy_version",
        _id("compiled_strategy_version_id"),
        _id("artifact_id"),
        _id("compiled_research_spec_id"),
        _id("compiled_model_instance_id"),
        sa.Column("branch_key", sa.String(300), nullable=False),
        sa.Column("strategy_family_key", sa.String(160), nullable=False),
        sa.Column("strategy_preset_key", sa.String(200), nullable=False),
        sa.Column("schedule_key", sa.String(160), nullable=False),
        sa.Column("rule_graph", JSONB, nullable=False),
        sa.Column("strategy_fingerprint", sa.String(64), nullable=False),
        _created(),
        _fk(["artifact_id"], ["lineage.artifact.artifact_id"], "fk_compiled_strategy_artifact"),
        _fk(
            ["compiled_research_spec_id"],
            ["workspace.compiled_research_spec.compiled_research_spec_id"],
            "fk_compiled_strategy_spec",
        ),
        _fk(
            ["compiled_model_instance_id"],
            ["workspace.compiled_model_instance.compiled_model_instance_id"],
            "fk_compiled_strategy_model",
        ),
        sa.PrimaryKeyConstraint(
            "compiled_strategy_version_id", name="pk_compiled_strategy_version"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_compiled_strategy_artifact"),
        sa.UniqueConstraint("strategy_fingerprint", name="uq_compiled_strategy_fingerprint"),
        sa.UniqueConstraint(
            "compiled_research_spec_id", "branch_key", name="uq_compiled_strategy_branch"
        ),
        sa.CheckConstraint(
            f"strategy_fingerprint ~ '{HASH_PATTERN}'", name="ck_compiled_strategy_fingerprint"
        ),
        schema="strategy",
    )


def downgrade() -> None:
    op.drop_table("compiled_strategy_version", schema="strategy")
    op.drop_table("compiled_model_instance", schema="workspace")
    op.drop_table("compiled_research_spec", schema="workspace")
    op.drop_table("research_draft", schema="workspace")
    op.drop_index(
        "ix_missing_observation_date_security", table_name="missing_observation", schema="data"
    )
    op.drop_table("missing_observation", schema="data")
    op.drop_table("dataset_manifest_partition", schema="data")
    op.drop_table("dataset_manifest", schema="data")
    op.drop_index(
        "ix_value_partition_stage_definition_date", table_name="value_partition", schema="data"
    )
    op.drop_table("value_partition", schema="data")
    op.drop_index(
        "uq_universe_snapshot_primary_issuer",
        table_name="universe_snapshot_member",
        schema="catalog",
    )
    op.drop_table("universe_snapshot_member", schema="catalog")
    op.drop_table("universe_candidate_evaluation", schema="catalog")
    op.drop_table("universe_snapshot", schema="catalog")
    op.drop_table("universe_history", schema="catalog")
    op.drop_table("universe_methodology", schema="catalog")
    op.drop_table("security_capability", schema="catalog")
    op.drop_index(
        "ix_security_identifier_lookup", table_name="security_identifier", schema="catalog"
    )
    op.drop_table("security_identifier", schema="catalog")
    op.drop_index("ix_security_type_status", table_name="security", schema="catalog")
    op.drop_table("security", schema="catalog")
    op.drop_table("issuer", schema="catalog")
    op.execute(sa.text('DROP SCHEMA IF EXISTS "product"'))
    op.execute(sa.text('DROP SCHEMA IF EXISTS "workspace"'))
