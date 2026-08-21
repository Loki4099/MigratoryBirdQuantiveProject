"""Add all v0.22 Catalog identities, ports, and fixed bindings.

Revision ID: 20260810_49_v022_catalog
Revises: 20260810_48_v022_payload
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_49_v022_catalog"
down_revision: str | None = "20260810_48_v022_payload"
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


def _artifact_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["artifact_id"],
        ["lineage.artifact.artifact_id"],
        name=name,
        ondelete="RESTRICT",
    )


def _hash(column: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{column} ~ '{HASH_PATTERN}'", name=name)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS processing")
    op.execute("CREATE SCHEMA IF NOT EXISTS aggregation")
    op.execute("CREATE SCHEMA IF NOT EXISTS defense")
    _create_feature_identity()
    _create_processing_identity()
    _create_aggregation_identity()
    _create_strategy_identity()
    _create_defense_identity()
    _create_processing_guards()
    _create_append_only_guards()


def _create_feature_identity() -> None:
    op.create_table(
        "feature_family",
        _id("feature_family_id"),
        _id("artifact_id"),
        sa.Column("family_key", sa.String(180), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("formula_identity", sa.Text(), nullable=False),
        sa.Column("input_roles", JSONB, nullable=False),
        sa.Column("output_semantics", JSONB, nullable=False),
        sa.Column("direction", sa.String(40), nullable=False),
        sa.Column("research_hypothesis", sa.Text(), nullable=False),
        _created(),
        _artifact_fk("fk_feature_family_artifact"),
        sa.PrimaryKeyConstraint("feature_family_id", name="pk_feature_family"),
        sa.UniqueConstraint("artifact_id", name="uq_feature_family_artifact"),
        sa.UniqueConstraint("family_key", name="uq_feature_family_key"),
        sa.CheckConstraint(
            "direction IN ('not_applicable','higher_is_better','lower_is_better',"
            "'higher_is_bullish','higher_is_bearish')",
            name="ck_feature_family_direction",
        ),
        schema="processing",
    )
    op.create_table(
        "feature_variant",
        _id("feature_variant_id"),
        _id("feature_family_id"),
        _id("artifact_id"),
        sa.Column("variant_key", sa.String(240), nullable=False),
        sa.Column("parameters", JSONB, nullable=False),
        sa.Column("research_tier", sa.String(30), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["feature_family_id"],
            ["processing.feature_family.feature_family_id"],
            name="fk_feature_variant_family",
            ondelete="RESTRICT",
        ),
        _artifact_fk("fk_feature_variant_artifact"),
        sa.PrimaryKeyConstraint("feature_variant_id", name="pk_feature_variant"),
        sa.UniqueConstraint("artifact_id", name="uq_feature_variant_artifact"),
        sa.UniqueConstraint("variant_key", name="uq_feature_variant_key"),
        sa.CheckConstraint(
            "research_tier IN ('raw','canonical','sensitivity','research_only','compatibility')",
            name="ck_feature_variant_research_tier",
        ),
        schema="processing",
    )
    op.create_table(
        "feature_version",
        _id("feature_version_id"),
        _id("feature_variant_id"),
        _id("artifact_id"),
        _id("payload_contract_version_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("origin_stage", sa.SmallInteger(), nullable=False),
        sa.Column("output_port_key", sa.String(160), nullable=False),
        sa.Column("aggregation_readiness", sa.String(40), nullable=False),
        sa.Column("execution_semantics", JSONB, nullable=False),
        sa.Column("version_fingerprint", sa.String(64), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["feature_variant_id"],
            ["processing.feature_variant.feature_variant_id"],
            name="fk_feature_version_variant",
            ondelete="RESTRICT",
        ),
        _artifact_fk("fk_feature_version_artifact"),
        sa.ForeignKeyConstraint(
            ["payload_contract_version_id"],
            ["data.payload_contract_version.payload_contract_version_id"],
            name="fk_feature_version_payload_contract",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("feature_version_id", name="pk_feature_version"),
        sa.UniqueConstraint("artifact_id", name="uq_feature_version_artifact"),
        sa.UniqueConstraint(
            "feature_variant_id", "version_number", name="uq_feature_variant_version"
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_feature_version_positive"),
        sa.CheckConstraint("origin_stage BETWEEN 0 AND 3", name="ck_feature_origin_stage"),
        sa.CheckConstraint(
            "aggregation_readiness IN ('aggregation_ready','not_aggregation_ready',"
            "'requires_explicit_adapter')",
            name="ck_feature_aggregation_readiness",
        ),
        _hash("version_fingerprint", "ck_feature_version_fingerprint"),
        schema="processing",
    )


def _create_processing_identity() -> None:
    op.create_table(
        "node_definition",
        _id("node_definition_id"),
        _id("artifact_id"),
        sa.Column("node_key", sa.String(180), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("algorithm_identity", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        _created(),
        _artifact_fk("fk_node_definition_artifact"),
        sa.PrimaryKeyConstraint("node_definition_id", name="pk_node_definition"),
        sa.UniqueConstraint("artifact_id", name="uq_node_definition_artifact"),
        sa.UniqueConstraint("node_key", name="uq_node_definition_key"),
        schema="processing",
    )
    op.create_table(
        "node_variant",
        _id("node_variant_id"),
        _id("node_definition_id"),
        _id("artifact_id"),
        sa.Column("variant_key", sa.String(240), nullable=False),
        sa.Column("parameters", JSONB, nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["node_definition_id"],
            ["processing.node_definition.node_definition_id"],
            name="fk_node_variant_definition",
            ondelete="RESTRICT",
        ),
        _artifact_fk("fk_node_variant_artifact"),
        sa.PrimaryKeyConstraint("node_variant_id", name="pk_node_variant"),
        sa.UniqueConstraint("artifact_id", name="uq_node_variant_artifact"),
        sa.UniqueConstraint("variant_key", name="uq_node_variant_key"),
        schema="processing",
    )
    op.create_table(
        "node_version",
        _id("node_version_id"),
        _id("node_variant_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("stage_no", sa.SmallInteger(), nullable=False),
        sa.Column("implementation_key", sa.String(240), nullable=False),
        sa.Column("implementation_version", sa.String(80), nullable=False),
        sa.Column("determinism_policy", sa.String(40), nullable=False),
        sa.Column("cache_policy", sa.String(40), nullable=False),
        sa.Column("execution_contract", JSONB, nullable=False),
        sa.Column("version_fingerprint", sa.String(64), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["node_variant_id"],
            ["processing.node_variant.node_variant_id"],
            name="fk_node_version_variant",
            ondelete="RESTRICT",
        ),
        _artifact_fk("fk_node_version_artifact"),
        sa.PrimaryKeyConstraint("node_version_id", name="pk_node_version"),
        sa.UniqueConstraint("artifact_id", name="uq_node_version_artifact"),
        sa.UniqueConstraint(
            "node_variant_id", "version_number", name="uq_node_variant_version"
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_node_version_positive"),
        sa.CheckConstraint("stage_no BETWEEN 1 AND 3", name="ck_node_version_stage"),
        sa.CheckConstraint(
            "determinism_policy IN ('deterministic','seeded','externally_frozen')",
            name="ck_node_determinism_policy",
        ),
        sa.CheckConstraint(
            "cache_policy IN ('content_addressed','disabled')", name="ck_node_cache_policy"
        ),
        _hash("version_fingerprint", "ck_node_version_fingerprint"),
        schema="processing",
    )
    op.create_table(
        "node_port",
        _id("node_port_id"),
        _id("node_version_id"),
        _id("payload_contract_version_id"),
        sa.Column("port_key", sa.String(160), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("binding_cardinality", sa.String(20), nullable=False),
        sa.Column("port_semantics", JSONB, nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["node_version_id"],
            ["processing.node_version.node_version_id"],
            name="fk_node_port_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payload_contract_version_id"],
            ["data.payload_contract_version.payload_contract_version_id"],
            name="fk_node_port_payload_contract",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("node_port_id", name="pk_node_port"),
        sa.UniqueConstraint("node_version_id", "port_key", name="uq_node_port_key"),
        sa.UniqueConstraint(
            "node_version_id", "direction", "ordinal", name="uq_node_port_ordinal"
        ),
        sa.CheckConstraint("direction IN ('input','output')", name="ck_node_port_direction"),
        sa.CheckConstraint("ordinal >= 0", name="ck_node_port_ordinal"),
        sa.CheckConstraint(
            "binding_cardinality = 'required'", name="ck_node_port_required_only"
        ),
        schema="processing",
    )
    op.create_table(
        "node_input_binding",
        _id("node_input_binding_id"),
        _id("node_version_id"),
        _id("input_port_id"),
        _id("source_feature_version_id"),
        sa.Column("binding_role", sa.String(160), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["node_version_id"],
            ["processing.node_version.node_version_id"],
            name="fk_node_input_binding_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["input_port_id"],
            ["processing.node_port.node_port_id"],
            name="fk_node_input_binding_port",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_feature_version_id"],
            ["processing.feature_version.feature_version_id"],
            name="fk_node_input_binding_feature",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("node_input_binding_id", name="pk_node_input_binding"),
        sa.UniqueConstraint("node_version_id", "input_port_id", name="uq_node_input_binding_port"),
        sa.UniqueConstraint(
            "node_version_id", "binding_role", name="uq_node_input_binding_role"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_node_input_binding_ordinal"),
        schema="processing",
    )
    op.create_table(
        "node_resource_binding",
        _id("node_resource_binding_id"),
        _id("node_version_id"),
        _id("resource_artifact_id"),
        sa.Column("resource_role", sa.String(160), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["node_version_id"],
            ["processing.node_version.node_version_id"],
            name="fk_node_resource_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resource_artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_node_resource_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("node_resource_binding_id", name="pk_node_resource_binding"),
        sa.UniqueConstraint(
            "node_version_id", "resource_role", name="uq_node_resource_binding_role"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_node_resource_binding_ordinal"),
        schema="processing",
    )
    op.create_table(
        "feature_producer",
        _id("feature_producer_id"),
        _id("feature_version_id"),
        _id("node_version_id"),
        _id("output_port_id"),
        _created(),
        sa.ForeignKeyConstraint(
            ["feature_version_id"],
            ["processing.feature_version.feature_version_id"],
            name="fk_feature_producer_feature",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["node_version_id"],
            ["processing.node_version.node_version_id"],
            name="fk_feature_producer_node",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["output_port_id"],
            ["processing.node_port.node_port_id"],
            name="fk_feature_producer_port",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("feature_producer_id", name="pk_feature_producer"),
        sa.UniqueConstraint("feature_version_id", name="uq_feature_producer_feature"),
        schema="processing",
    )


def _create_aggregation_identity() -> None:
    op.create_table(
        "aggregation_family",
        _id("aggregation_family_id"),
        _id("artifact_id"),
        sa.Column("family_key", sa.String(180), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("algorithm_identity", sa.Text(), nullable=False),
        sa.Column("objective_semantics", JSONB, nullable=False),
        sa.Column("output_semantics", JSONB, nullable=False),
        _created(),
        _artifact_fk("fk_aggregation_family_artifact"),
        sa.PrimaryKeyConstraint("aggregation_family_id", name="pk_aggregation_family"),
        sa.UniqueConstraint("artifact_id", name="uq_aggregation_family_artifact"),
        sa.UniqueConstraint("family_key", name="uq_aggregation_family_key"),
        schema="aggregation",
    )
    op.create_table(
        "aggregation_version",
        _id("aggregation_version_id"),
        _id("aggregation_family_id"),
        _id("artifact_id"),
        _id("output_payload_contract_version_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("execution_mode", sa.String(20), nullable=False),
        sa.Column("implementation_key", sa.String(240), nullable=False),
        sa.Column("input_policy", JSONB, nullable=False),
        sa.Column("missing_policy", JSONB, nullable=False),
        sa.Column("tie_policy", JSONB, nullable=False),
        sa.Column("version_fingerprint", sa.String(64), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["aggregation_family_id"],
            ["aggregation.aggregation_family.aggregation_family_id"],
            name="fk_aggregation_version_family",
            ondelete="RESTRICT",
        ),
        _artifact_fk("fk_aggregation_version_artifact"),
        sa.ForeignKeyConstraint(
            ["output_payload_contract_version_id"],
            ["data.payload_contract_version.payload_contract_version_id"],
            name="fk_aggregation_version_output_contract",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("aggregation_version_id", name="pk_aggregation_version"),
        sa.UniqueConstraint("artifact_id", name="uq_aggregation_version_artifact"),
        sa.UniqueConstraint(
            "aggregation_family_id", "version_number", name="uq_aggregation_family_version"
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_aggregation_version_positive"),
        sa.CheckConstraint(
            "execution_mode IN ('deterministic','supervised')",
            name="ck_aggregation_execution_mode",
        ),
        _hash("version_fingerprint", "ck_aggregation_version_fingerprint"),
        schema="aggregation",
    )
    op.create_table(
        "aggregation_input_port",
        _id("aggregation_input_port_id"),
        _id("aggregation_version_id"),
        _id("payload_contract_version_id"),
        sa.Column("port_key", sa.String(160), nullable=False),
        sa.Column("minimum_count", sa.Integer(), nullable=False),
        sa.Column("maximum_count", sa.Integer(), nullable=False),
        sa.Column("ordering_policy", sa.String(40), nullable=False),
        sa.Column("compatibility_policy", JSONB, nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["aggregation_version_id"],
            ["aggregation.aggregation_version.aggregation_version_id"],
            name="fk_aggregation_input_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payload_contract_version_id"],
            ["data.payload_contract_version.payload_contract_version_id"],
            name="fk_aggregation_input_contract",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("aggregation_input_port_id", name="pk_aggregation_input_port"),
        sa.UniqueConstraint("aggregation_version_id", "port_key", name="uq_aggregation_input_key"),
        sa.CheckConstraint(
            "minimum_count >= 1 AND maximum_count >= minimum_count",
            name="ck_aggregation_input_cardinality",
        ),
        sa.CheckConstraint(
            "ordering_policy IN ('explicit_input_order','family_then_variant')",
            name="ck_aggregation_input_ordering",
        ),
        schema="aggregation",
    )
    _create_definition_version_pair(
        schema="aggregation",
        prefix="parameter_preset",
        owner_table="aggregation.aggregation_family",
        owner_column="aggregation_family_id",
    )
    _create_definition_version_pair(
        schema="aggregation",
        prefix="target",
        owner_table="aggregation.aggregation_family",
        owner_column="aggregation_family_id",
    )
    _create_definition_version_pair(
        schema="aggregation",
        prefix="training_preset",
        owner_table="aggregation.aggregation_family",
        owner_column="aggregation_family_id",
    )


def _create_definition_version_pair(
    *, schema: str, prefix: str, owner_table: str, owner_column: str
) -> None:
    definition = f"{prefix}_definition"
    version = f"{prefix}_version"
    definition_id = f"{definition}_id"
    version_id = f"{version}_id"
    op.create_table(
        definition,
        _id(definition_id),
        _id(owner_column),
        _id("artifact_id"),
        sa.Column(f"{prefix}_key", sa.String(200), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            [owner_column],
            [f"{owner_table}.{owner_column}"],
            name=f"fk_{definition}_owner",
            ondelete="RESTRICT",
        ),
        _artifact_fk(f"fk_{definition}_artifact"),
        sa.PrimaryKeyConstraint(definition_id, name=f"pk_{definition}"),
        sa.UniqueConstraint("artifact_id", name=f"uq_{definition}_artifact"),
        sa.UniqueConstraint(f"{prefix}_key", name=f"uq_{definition}_key"),
        schema=schema,
    )
    op.create_table(
        version,
        _id(version_id),
        _id(definition_id),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("semantics", JSONB, nullable=False),
        sa.Column("version_fingerprint", sa.String(64), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            [definition_id],
            [f"{schema}.{definition}.{definition_id}"],
            name=f"fk_{version}_definition",
            ondelete="RESTRICT",
        ),
        _artifact_fk(f"fk_{version}_artifact"),
        sa.PrimaryKeyConstraint(version_id, name=f"pk_{version}"),
        sa.UniqueConstraint("artifact_id", name=f"uq_{version}_artifact"),
        sa.UniqueConstraint(definition_id, "version_number", name=f"uq_{version}_identity"),
        sa.CheckConstraint("version_number >= 1", name=f"ck_{version}_positive"),
        _hash("version_fingerprint", f"ck_{version}_fingerprint"),
        schema=schema,
    )


def _create_strategy_identity() -> None:
    op.create_table(
        "v022_strategy_family",
        _id("strategy_family_id"),
        _id("artifact_id"),
        sa.Column("family_key", sa.String(180), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("selection_semantics", JSONB, nullable=False),
        sa.Column("research_hypothesis", sa.Text(), nullable=False),
        _created(),
        _artifact_fk("fk_v022_strategy_family_artifact"),
        sa.PrimaryKeyConstraint("strategy_family_id", name="pk_v022_strategy_family"),
        sa.UniqueConstraint("artifact_id", name="uq_v022_strategy_family_artifact"),
        sa.UniqueConstraint("family_key", name="uq_v022_strategy_family_key"),
        schema="strategy",
    )
    op.create_table(
        "v022_strategy_variant",
        _id("strategy_variant_id"),
        _id("strategy_family_id"),
        _id("artifact_id"),
        sa.Column("variant_key", sa.String(220), nullable=False),
        sa.Column("parameters", JSONB, nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["strategy_family_id"],
            ["strategy.v022_strategy_family.strategy_family_id"],
            name="fk_v022_strategy_variant_family",
            ondelete="RESTRICT",
        ),
        _artifact_fk("fk_v022_strategy_variant_artifact"),
        sa.PrimaryKeyConstraint("strategy_variant_id", name="pk_v022_strategy_variant"),
        sa.UniqueConstraint("artifact_id", name="uq_v022_strategy_variant_artifact"),
        sa.UniqueConstraint("variant_key", name="uq_v022_strategy_variant_key"),
        schema="strategy",
    )
    op.create_table(
        "v022_strategy_version",
        _id("strategy_version_id"),
        _id("strategy_variant_id"),
        _id("artifact_id"),
        _id("input_payload_contract_version_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("implementation_key", sa.String(240), nullable=False),
        sa.Column("schedule_policy", JSONB, nullable=False),
        sa.Column("execution_policy", JSONB, nullable=False),
        sa.Column("version_fingerprint", sa.String(64), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["strategy_variant_id"],
            ["strategy.v022_strategy_variant.strategy_variant_id"],
            name="fk_v022_strategy_version_variant",
            ondelete="RESTRICT",
        ),
        _artifact_fk("fk_v022_strategy_version_artifact"),
        sa.ForeignKeyConstraint(
            ["input_payload_contract_version_id"],
            ["data.payload_contract_version.payload_contract_version_id"],
            name="fk_v022_strategy_input_contract",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("strategy_version_id", name="pk_v022_strategy_version"),
        sa.UniqueConstraint("artifact_id", name="uq_v022_strategy_version_artifact"),
        sa.UniqueConstraint(
            "strategy_variant_id", "version_number", name="uq_v022_strategy_variant_version"
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_v022_strategy_version_positive"),
        _hash("version_fingerprint", "ck_v022_strategy_version_fingerprint"),
        schema="strategy",
    )


def _create_defense_identity() -> None:
    op.create_table(
        "defense_family",
        _id("defense_family_id"),
        _id("artifact_id"),
        sa.Column("family_key", sa.String(180), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("allocation_semantics", JSONB, nullable=False),
        sa.Column("research_hypothesis", sa.Text(), nullable=False),
        _created(),
        _artifact_fk("fk_defense_family_artifact"),
        sa.PrimaryKeyConstraint("defense_family_id", name="pk_defense_family"),
        sa.UniqueConstraint("artifact_id", name="uq_defense_family_artifact"),
        sa.UniqueConstraint("family_key", name="uq_defense_family_key"),
        schema="defense",
    )
    op.create_table(
        "defense_variant",
        _id("defense_variant_id"),
        _id("defense_family_id"),
        _id("artifact_id"),
        sa.Column("variant_key", sa.String(220), nullable=False),
        sa.Column("parameters", JSONB, nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["defense_family_id"],
            ["defense.defense_family.defense_family_id"],
            name="fk_defense_variant_family",
            ondelete="RESTRICT",
        ),
        _artifact_fk("fk_defense_variant_artifact"),
        sa.PrimaryKeyConstraint("defense_variant_id", name="pk_defense_variant"),
        sa.UniqueConstraint("artifact_id", name="uq_defense_variant_artifact"),
        sa.UniqueConstraint("variant_key", name="uq_defense_variant_key"),
        schema="defense",
    )
    op.create_table(
        "defense_version",
        _id("defense_version_id"),
        _id("defense_variant_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("implementation_key", sa.String(240), nullable=False),
        sa.Column("input_policy", JSONB, nullable=False),
        sa.Column("allocation_policy", JSONB, nullable=False),
        sa.Column("supported_asset_context_keys", JSONB, nullable=False),
        sa.Column("version_fingerprint", sa.String(64), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["defense_variant_id"],
            ["defense.defense_variant.defense_variant_id"],
            name="fk_defense_version_variant",
            ondelete="RESTRICT",
        ),
        _artifact_fk("fk_defense_version_artifact"),
        sa.PrimaryKeyConstraint("defense_version_id", name="pk_defense_version"),
        sa.UniqueConstraint("artifact_id", name="uq_defense_version_artifact"),
        sa.UniqueConstraint(
            "defense_variant_id", "version_number", name="uq_defense_variant_version"
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_defense_version_positive"),
        _hash("version_fingerprint", "ck_defense_version_fingerprint"),
        schema="defense",
    )
    op.create_table(
        "defense_resource_binding",
        _id("defense_resource_binding_id"),
        _id("defense_version_id"),
        _id("resource_artifact_id"),
        sa.Column("resource_role", sa.String(160), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["defense_version_id"],
            ["defense.defense_version.defense_version_id"],
            name="fk_defense_resource_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resource_artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_defense_resource_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "defense_resource_binding_id", name="pk_defense_resource_binding"
        ),
        sa.UniqueConstraint(
            "defense_version_id", "resource_role", name="uq_defense_resource_role"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_defense_resource_ordinal"),
        schema="defense",
    )


def _create_processing_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION processing.validate_node_input_binding() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            node_stage smallint;
            source_stage smallint;
            port_owner uuid;
            port_direction text;
            reader_contract uuid;
            writer_contract uuid;
        BEGIN
            SELECT stage_no INTO node_stage FROM processing.node_version
             WHERE node_version_id = NEW.node_version_id;
            SELECT origin_stage, payload_contract_version_id
              INTO source_stage, writer_contract FROM processing.feature_version
             WHERE feature_version_id = NEW.source_feature_version_id;
            SELECT node_version_id, direction, payload_contract_version_id
              INTO port_owner, port_direction, reader_contract FROM processing.node_port
             WHERE node_port_id = NEW.input_port_id;
            IF port_owner <> NEW.node_version_id OR port_direction <> 'input' THEN
                RAISE EXCEPTION 'input binding port must belong to the target node version';
            END IF;
            IF source_stage <> node_stage - 1 THEN
                RAISE EXCEPTION 'processing edges must connect adjacent stages';
            END IF;
            IF reader_contract <> writer_contract AND NOT EXISTS (
                SELECT 1 FROM data.payload_contract_compatibility c
                 WHERE c.reader_contract_version_id = reader_contract
                   AND c.writer_contract_version_id = writer_contract
                   AND c.compatibility_result = 'compatible'
            ) THEN
                RAISE EXCEPTION 'payload contract incompatible for processing edge';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER trg_validate_node_input_binding
        BEFORE INSERT ON processing.node_input_binding
        FOR EACH ROW EXECUTE FUNCTION processing.validate_node_input_binding();

        CREATE FUNCTION processing.validate_feature_producer() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            feature_stage smallint;
            feature_contract uuid;
            node_stage smallint;
            port_owner uuid;
            port_direction text;
            port_contract uuid;
        BEGIN
            SELECT origin_stage, payload_contract_version_id
              INTO feature_stage, feature_contract FROM processing.feature_version
             WHERE feature_version_id = NEW.feature_version_id;
            SELECT stage_no INTO node_stage FROM processing.node_version
             WHERE node_version_id = NEW.node_version_id;
            SELECT node_version_id, direction, payload_contract_version_id
              INTO port_owner, port_direction, port_contract FROM processing.node_port
             WHERE node_port_id = NEW.output_port_id;
            IF feature_stage = 0 THEN
                RAISE EXCEPTION 'raw feature versions cannot have processing producers';
            END IF;
            IF feature_stage <> node_stage THEN
                RAISE EXCEPTION 'feature and producer must have the same stage';
            END IF;
            IF port_owner <> NEW.node_version_id OR port_direction <> 'output' THEN
                RAISE EXCEPTION 'producer output port must belong to producer node version';
            END IF;
            IF feature_contract <> port_contract THEN
                RAISE EXCEPTION 'feature and producer output contracts must match exactly';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER trg_validate_feature_producer
        BEFORE INSERT ON processing.feature_producer
        FOR EACH ROW EXECUTE FUNCTION processing.validate_feature_producer();
        """
    )


def _create_append_only_guards() -> None:
    for schema, table in _catalog_tables():
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {schema}.{table} "
            "FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def _catalog_tables() -> tuple[tuple[str, str], ...]:
    return (
        ("processing", "feature_family"),
        ("processing", "feature_variant"),
        ("processing", "feature_version"),
        ("processing", "node_definition"),
        ("processing", "node_variant"),
        ("processing", "node_version"),
        ("processing", "node_port"),
        ("processing", "node_input_binding"),
        ("processing", "node_resource_binding"),
        ("processing", "feature_producer"),
        ("aggregation", "aggregation_family"),
        ("aggregation", "aggregation_version"),
        ("aggregation", "aggregation_input_port"),
        ("aggregation", "parameter_preset_definition"),
        ("aggregation", "parameter_preset_version"),
        ("aggregation", "target_definition"),
        ("aggregation", "target_version"),
        ("aggregation", "training_preset_definition"),
        ("aggregation", "training_preset_version"),
        ("strategy", "v022_strategy_family"),
        ("strategy", "v022_strategy_variant"),
        ("strategy", "v022_strategy_version"),
        ("defense", "defense_family"),
        ("defense", "defense_variant"),
        ("defense", "defense_version"),
        ("defense", "defense_resource_binding"),
    )


def downgrade() -> None:
    for schema, table in reversed(_catalog_tables()):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {schema}.{table}")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validate_feature_producer ON processing.feature_producer"
    )
    op.execute("DROP FUNCTION IF EXISTS processing.validate_feature_producer()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validate_node_input_binding ON processing.node_input_binding"
    )
    op.execute("DROP FUNCTION IF EXISTS processing.validate_node_input_binding()")
    op.drop_table("defense_resource_binding", schema="defense")
    op.drop_table("defense_version", schema="defense")
    op.drop_table("defense_variant", schema="defense")
    op.drop_table("defense_family", schema="defense")
    op.drop_table("v022_strategy_version", schema="strategy")
    op.drop_table("v022_strategy_variant", schema="strategy")
    op.drop_table("v022_strategy_family", schema="strategy")
    for table in (
        "training_preset_version",
        "training_preset_definition",
        "target_version",
        "target_definition",
        "parameter_preset_version",
        "parameter_preset_definition",
        "aggregation_input_port",
        "aggregation_version",
        "aggregation_family",
    ):
        op.drop_table(table, schema="aggregation")
    op.drop_table("feature_producer", schema="processing")
    op.drop_table("node_resource_binding", schema="processing")
    op.drop_table("node_input_binding", schema="processing")
    op.drop_table("node_port", schema="processing")
    op.drop_table("node_version", schema="processing")
    op.drop_table("node_variant", schema="processing")
    op.drop_table("node_definition", schema="processing")
    op.drop_table("feature_version", schema="processing")
    op.drop_table("feature_variant", schema="processing")
    op.drop_table("feature_family", schema="processing")
    op.execute("DROP SCHEMA IF EXISTS defense")
    op.execute("DROP SCHEMA IF EXISTS aggregation")
    op.execute("DROP SCHEMA IF EXISTS processing")
