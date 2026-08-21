"""Add immutable v0.22 Catalog Release membership and evidence.

Revision ID: 20260810_50_v022_release
Revises: 20260810_49_v022_catalog
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_50_v022_release"
down_revision: str | None = "20260810_49_v022_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
HASH_PATTERN = "^[0-9a-f]{64}$"


def upgrade() -> None:
    op.create_table(
        "catalog_publisher_authorization",
        sa.Column("catalog_publisher_authorization_id", UUID, nullable=False),
        sa.Column("actor_key", sa.String(160), nullable=False),
        sa.Column("authorization_scope", JSONB, nullable=False),
        sa.Column("authorization_source", sa.String(240), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint(
            "catalog_publisher_authorization_id", name="pk_catalog_publisher_authorization"
        ),
        sa.UniqueConstraint(
            "actor_key", "valid_from", name="uq_catalog_publisher_authorization_window"
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_catalog_publisher_authorization_window",
        ),
        schema="workspace",
    )
    op.create_table(
        "v022_catalog_release",
        sa.Column("catalog_release_id", UUID, nullable=False),
        sa.Column("artifact_id", UUID, nullable=False),
        sa.Column("publisher_authorization_id", UUID, nullable=False),
        sa.Column("release_key", sa.String(180), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("contract_version", sa.String(40), nullable=False),
        sa.Column("processing_stage_count", sa.SmallInteger(), nullable=False),
        sa.Column("release_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_manifest_hash", sa.String(64), nullable=False),
        sa.Column("publisher_actor", sa.String(160), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_v022_catalog_release_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publisher_authorization_id"],
            [
                "workspace.catalog_publisher_authorization."
                "catalog_publisher_authorization_id"
            ],
            name="fk_v022_catalog_release_authorization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("catalog_release_id", name="pk_v022_catalog_release"),
        sa.UniqueConstraint("artifact_id", name="uq_v022_catalog_release_artifact"),
        sa.UniqueConstraint(
            "release_key", "version_number", name="uq_v022_catalog_release_version"
        ),
        sa.UniqueConstraint("release_fingerprint", name="uq_v022_catalog_release_fingerprint"),
        sa.CheckConstraint("version_number >= 1", name="ck_v022_catalog_release_version"),
        sa.CheckConstraint(
            "contract_version = 'v0.22.0'", name="ck_v022_catalog_release_contract"
        ),
        sa.CheckConstraint(
            "processing_stage_count = 3", name="ck_v022_catalog_release_stage_count"
        ),
        sa.CheckConstraint(
            f"release_fingerprint ~ '{HASH_PATTERN}'",
            name="ck_v022_catalog_release_fingerprint",
        ),
        sa.CheckConstraint(
            f"source_manifest_hash ~ '{HASH_PATTERN}'",
            name="ck_v022_catalog_release_manifest_hash",
        ),
        schema="workspace",
    )
    op.create_table(
        "v022_catalog_release_component",
        sa.Column("catalog_release_id", UUID, nullable=False),
        sa.Column("component_artifact_id", UUID, nullable=False),
        sa.Column("component_kind", sa.String(80), nullable=False),
        sa.Column("component_key", sa.String(240), nullable=False),
        sa.Column("component_version", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("component_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["catalog_release_id"],
            ["workspace.v022_catalog_release.catalog_release_id"],
            name="fk_v022_release_component_release",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["component_artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_v022_release_component_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "catalog_release_id",
            "component_artifact_id",
            name="pk_v022_catalog_release_component",
        ),
        sa.UniqueConstraint(
            "catalog_release_id", "ordinal", name="uq_v022_release_component_ordinal"
        ),
        sa.UniqueConstraint(
            "catalog_release_id",
            "component_kind",
            "component_key",
            "component_version",
            name="uq_v022_release_component_identity",
        ),
        sa.CheckConstraint("component_version >= 1", name="ck_v022_component_version"),
        sa.CheckConstraint("ordinal >= 0", name="ck_v022_component_ordinal"),
        sa.CheckConstraint(
            f"component_fingerprint ~ '{HASH_PATTERN}'",
            name="ck_v022_component_fingerprint",
        ),
        sa.CheckConstraint(
            "component_kind IN ("
            "'payload_contract_family','payload_contract_version','payload_compatibility',"
            "'physical_encoding_version','feature_family','feature_variant','feature_version',"
            "'processing_node_definition','processing_node_variant','processing_node_version',"
            "'aggregation_family','aggregation_version',"
            "'aggregation_parameter_preset_definition',"
            "'aggregation_parameter_preset_version','aggregation_target_definition',"
            "'aggregation_target_version','aggregation_training_preset_definition',"
            "'aggregation_training_preset_version','strategy_family',"
            "'strategy_variant','strategy_version','defense_family','defense_variant',"
            "'defense_version')",
            name="ck_v022_component_kind",
        ),
        schema="workspace",
    )
    op.create_table(
        "v022_catalog_validation_evidence",
        sa.Column("catalog_validation_evidence_id", UUID, nullable=False),
        sa.Column("artifact_id", UUID, nullable=False),
        sa.Column("catalog_release_id", UUID, nullable=False),
        sa.Column("evidence_kind", sa.String(40), nullable=False),
        sa.Column("validator_version", sa.String(80), nullable=False),
        sa.Column("checks", JSONB, nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("publisher_actor", sa.String(160), nullable=False),
        sa.Column("reviewer_actor", sa.String(160), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_v022_catalog_evidence_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["catalog_release_id"],
            ["workspace.v022_catalog_release.catalog_release_id"],
            name="fk_v022_catalog_evidence_release",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "catalog_validation_evidence_id", name="pk_v022_catalog_validation_evidence"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_v022_catalog_evidence_artifact"),
        sa.UniqueConstraint(
            "catalog_release_id", "evidence_kind", name="uq_v022_catalog_evidence_kind"
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('lint','publish','rebuild_verify')",
            name="ck_v022_catalog_evidence_kind",
        ),
        schema="workspace",
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION workspace.validate_v022_catalog_authorization() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM workspace.catalog_publisher_authorization a
                 WHERE a.catalog_publisher_authorization_id = NEW.publisher_authorization_id
                   AND a.actor_key = NEW.publisher_actor
                   AND a.valid_from <= NEW.published_at
                   AND (a.valid_until IS NULL OR a.valid_until > NEW.published_at)
            ) THEN
                RAISE EXCEPTION 'catalog publisher is not authorized at publication time';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER trg_validate_v022_catalog_authorization
        BEFORE INSERT ON workspace.v022_catalog_release
        FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_catalog_authorization();

        CREATE FUNCTION workspace.validate_v022_release_component() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE artifact_status text;
        DECLARE artifact_fingerprint text;
        BEGIN
            SELECT status, semantic_fingerprint INTO artifact_status, artifact_fingerprint
              FROM lineage.artifact WHERE artifact_id = NEW.component_artifact_id;
            IF artifact_status <> 'published' THEN
                RAISE EXCEPTION 'Catalog Release components must already be published';
            END IF;
            IF artifact_fingerprint <> NEW.component_fingerprint THEN
                RAISE EXCEPTION 'Catalog Release component fingerprint mismatch';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER trg_validate_v022_release_component
        BEFORE INSERT ON workspace.v022_catalog_release_component
        FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_release_component();
        """
    )
    for table in (
        "catalog_publisher_authorization",
        "v022_catalog_release",
        "v022_catalog_release_component",
        "v022_catalog_validation_evidence",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON workspace.{table} FOR EACH ROW "
            "EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def downgrade() -> None:
    for table in (
        "v022_catalog_validation_evidence",
        "v022_catalog_release_component",
        "v022_catalog_release",
        "catalog_publisher_authorization",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON workspace.{table}")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validate_v022_release_component "
        "ON workspace.v022_catalog_release_component"
    )
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_release_component()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validate_v022_catalog_authorization "
        "ON workspace.v022_catalog_release"
    )
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_catalog_authorization()")
    op.drop_table("v022_catalog_validation_evidence", schema="workspace")
    op.drop_table("v022_catalog_release_component", schema="workspace")
    op.drop_table("v022_catalog_release", schema="workspace")
    op.drop_table("catalog_publisher_authorization", schema="workspace")
