"""Add the v0.22 shared immutable Payload infrastructure.

Revision ID: 20260810_48_v022_payload
Revises: 20260809_47_signal_export_job
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_48_v022_payload"
down_revision: str | None = "20260809_47_signal_export_job"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
HASH_PATTERN = "^[0-9a-f]{64}$"


def _id(name: str) -> sa.Column[object]:
    return sa.Column(name, UUID, nullable=False)


def _artifact_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["artifact_id"],
        ["lineage.artifact.artifact_id"],
        name=name,
        ondelete="RESTRICT",
    )


def _created() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _hash(column: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{column} ~ '{HASH_PATTERN}'", name=name)


def upgrade() -> None:
    _create_contract_tables()
    _create_encoding_and_object_tables()
    _create_manifest_tables()
    _create_publication_tables()
    _create_immutability_guards()


def _create_contract_tables() -> None:
    op.create_table(
        "payload_contract_family",
        _id("payload_contract_family_id"),
        _id("artifact_id"),
        sa.Column("contract_key", sa.String(160), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("semantic_role", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        _created(),
        _artifact_fk("fk_payload_contract_family_artifact"),
        sa.PrimaryKeyConstraint(
            "payload_contract_family_id", name="pk_payload_contract_family"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_payload_contract_family_artifact"),
        sa.UniqueConstraint("contract_key", name="uq_payload_contract_family_key"),
        schema="data",
    )
    op.create_table(
        "payload_contract_version",
        _id("payload_contract_version_id"),
        _id("payload_contract_family_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("payload_kind", sa.String(40), nullable=False),
        sa.Column("schema_document", JSONB, nullable=False),
        sa.Column("entity_axis", JSONB, nullable=False),
        sa.Column("time_axis", JSONB, nullable=False),
        sa.Column("observation_grain", JSONB, nullable=False),
        sa.Column("primary_key_fields", JSONB, nullable=False),
        sa.Column("ordering_contract", JSONB, nullable=False),
        sa.Column("missingness_contract", JSONB, nullable=False),
        sa.Column("pit_contract", JSONB, nullable=False),
        sa.Column("quality_contract", JSONB, nullable=False),
        sa.Column("aggregation_role", sa.String(60), nullable=False),
        sa.Column("export_policy", JSONB, nullable=False),
        sa.Column("schema_fingerprint", sa.String(64), nullable=False),
        sa.Column("compatibility_class", sa.String(40), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["payload_contract_family_id"],
            ["data.payload_contract_family.payload_contract_family_id"],
            name="fk_payload_contract_version_family",
            ondelete="RESTRICT",
        ),
        _artifact_fk("fk_payload_contract_version_artifact"),
        sa.PrimaryKeyConstraint(
            "payload_contract_version_id", name="pk_payload_contract_version"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_payload_contract_version_artifact"),
        sa.UniqueConstraint(
            "payload_contract_family_id",
            "version_number",
            name="uq_payload_contract_family_version",
        ),
        sa.UniqueConstraint(
            "payload_contract_family_id",
            "payload_contract_version_id",
            name="uq_payload_contract_version_family_pair",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_payload_contract_version_positive"),
        sa.CheckConstraint(
            "payload_kind IN ('numeric_scalar','tabular','vector_series','text_series',"
            "'event_series','structured_pattern','document_reference','tensor_series',"
            "'model_state','mapping_table','opaque_bundle')",
            name="ck_payload_contract_kind",
        ),
        sa.CheckConstraint(
            "compatibility_class IN ('initial','backward_compatible','forward_compatible',"
            "'breaking','semantic_change')",
            name="ck_payload_contract_compatibility_class",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(primary_key_fields) = 'array'",
            name="ck_payload_contract_primary_key_array",
        ),
        _hash("schema_fingerprint", "ck_payload_contract_schema_fingerprint"),
        schema="data",
    )
    op.create_table(
        "payload_contract_compatibility",
        _id("payload_contract_compatibility_id"),
        _id("artifact_id"),
        _id("reader_contract_version_id"),
        _id("writer_contract_version_id"),
        sa.Column("compatibility_direction", sa.String(30), nullable=False),
        sa.Column("compatibility_result", sa.String(20), nullable=False),
        sa.Column("reason_document", JSONB, nullable=False),
        sa.Column("checker_version", sa.String(80), nullable=False),
        _created(),
        _artifact_fk("fk_payload_compatibility_artifact"),
        sa.ForeignKeyConstraint(
            ["reader_contract_version_id"],
            ["data.payload_contract_version.payload_contract_version_id"],
            name="fk_payload_compatibility_reader",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["writer_contract_version_id"],
            ["data.payload_contract_version.payload_contract_version_id"],
            name="fk_payload_compatibility_writer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "payload_contract_compatibility_id", name="pk_payload_contract_compatibility"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_payload_compatibility_artifact"),
        sa.UniqueConstraint(
            "reader_contract_version_id",
            "writer_contract_version_id",
            "compatibility_direction",
            name="uq_payload_contract_compatibility_pair",
        ),
        sa.CheckConstraint(
            "reader_contract_version_id <> writer_contract_version_id",
            name="ck_payload_compatibility_distinct_versions",
        ),
        sa.CheckConstraint(
            "compatibility_direction IN ('reader_accepts_writer','writer_readable_by_reader')",
            name="ck_payload_compatibility_direction",
        ),
        sa.CheckConstraint(
            "compatibility_result IN ('compatible','incompatible')",
            name="ck_payload_compatibility_result",
        ),
        schema="data",
    )


def _create_encoding_and_object_tables() -> None:
    op.create_table(
        "physical_encoding_version",
        _id("physical_encoding_version_id"),
        _id("artifact_id"),
        sa.Column("encoding_key", sa.String(160), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(160), nullable=False),
        sa.Column("file_extension", sa.String(20), nullable=False),
        sa.Column("compression", sa.String(60), nullable=False),
        sa.Column("writer_version", sa.String(120), nullable=False),
        sa.Column("reader_min_version", sa.String(120), nullable=False),
        sa.Column("reader_max_version", sa.String(120), nullable=True),
        sa.Column("canonicalization_policy", JSONB, nullable=False),
        sa.Column("partition_policy", JSONB, nullable=False),
        sa.Column("encryption_policy", JSONB, nullable=False),
        sa.Column("verification_implementation", sa.String(240), nullable=False),
        _created(),
        _artifact_fk("fk_physical_encoding_artifact"),
        sa.PrimaryKeyConstraint(
            "physical_encoding_version_id", name="pk_physical_encoding_version"
        ),
        sa.UniqueConstraint("artifact_id", name="uq_physical_encoding_artifact"),
        sa.UniqueConstraint(
            "encoding_key", "version_number", name="uq_physical_encoding_key_version"
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_physical_encoding_version_positive"),
        sa.CheckConstraint(
            "file_extension ~ '^[a-z0-9][a-z0-9._-]{0,19}$'",
            name="ck_physical_encoding_extension",
        ),
        schema="data",
    )
    op.create_table(
        "payload_object",
        _id("payload_object_id"),
        sa.Column("object_content_hash", sa.String(64), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("object_state", sa.String(20), nullable=False),
        sa.Column("verification_status", sa.String(20), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_marked_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint("payload_object_id", name="pk_payload_object"),
        sa.UniqueConstraint("object_content_hash", name="uq_payload_object_content_hash"),
        sa.UniqueConstraint("storage_uri", name="uq_payload_object_storage_uri"),
        _hash("object_content_hash", "ck_payload_object_content_hash"),
        sa.CheckConstraint("byte_size >= 0", name="ck_payload_object_byte_size"),
        sa.CheckConstraint(
            "object_state IN ('staging','published','quarantined','deleting','deleted')",
            name="ck_payload_object_state",
        ),
        sa.CheckConstraint(
            "verification_status IN ('pending','verified','failed')",
            name="ck_payload_object_verification_status",
        ),
        sa.CheckConstraint(
            "storage_uri LIKE 'payload-object://sha256/' || object_content_hash || '.%'",
            name="ck_payload_object_content_addressed_uri",
        ),
        schema="data",
    )


def _create_manifest_tables() -> None:
    op.create_table(
        "payload_manifest",
        _id("payload_manifest_id"),
        _id("artifact_id"),
        _id("payload_contract_version_id"),
        _id("physical_encoding_version_id"),
        _id("producer_artifact_id"),
        sa.Column("producer_output_port_key", sa.String(160), nullable=False),
        sa.Column("logical_payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("partition_count", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("row_or_item_count", sa.BigInteger(), nullable=False),
        sa.Column("coverage_document", JSONB, nullable=False),
        sa.Column("retention_class", sa.String(30), nullable=False),
        sa.Column("materialization_state", sa.String(20), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        _created(),
        _artifact_fk("fk_payload_manifest_artifact"),
        sa.ForeignKeyConstraint(
            ["payload_contract_version_id"],
            ["data.payload_contract_version.payload_contract_version_id"],
            name="fk_payload_manifest_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["physical_encoding_version_id"],
            ["data.physical_encoding_version.physical_encoding_version_id"],
            name="fk_payload_manifest_encoding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["producer_artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_payload_manifest_producer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("payload_manifest_id", name="pk_payload_manifest"),
        sa.UniqueConstraint("artifact_id", name="uq_payload_manifest_artifact"),
        sa.UniqueConstraint("manifest_hash", name="uq_payload_manifest_hash"),
        _hash("logical_payload_fingerprint", "ck_payload_manifest_logical_hash"),
        _hash("manifest_hash", "ck_payload_manifest_hash"),
        sa.CheckConstraint("partition_count >= 1", name="ck_payload_manifest_partition_count"),
        sa.CheckConstraint("byte_size >= 0", name="ck_payload_manifest_byte_size"),
        sa.CheckConstraint("row_or_item_count >= 0", name="ck_payload_manifest_item_count"),
        sa.CheckConstraint(
            "retention_class IN ('cache','research','product','evidence','export','legal_hold')",
            name="ck_payload_manifest_retention_class",
        ),
        sa.CheckConstraint(
            "materialization_state IN ('materialized','evicted','rehydrating')",
            name="ck_payload_manifest_materialization_state",
        ),
        schema="data",
    )
    op.create_table(
        "payload_partition",
        _id("payload_partition_id"),
        _id("payload_object_id"),
        sa.Column("partition_descriptor_hash", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("row_or_item_count", sa.BigInteger(), nullable=False),
        sa.Column("partition_key", JSONB, nullable=False),
        sa.Column("coverage_document", JSONB, nullable=False),
        sa.Column("statistics", JSONB, nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["payload_object_id"],
            ["data.payload_object.payload_object_id"],
            name="fk_payload_partition_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("payload_partition_id", name="pk_payload_partition"),
        sa.UniqueConstraint(
            "partition_descriptor_hash", name="uq_payload_partition_descriptor_hash"
        ),
        _hash("partition_descriptor_hash", "ck_payload_partition_descriptor_hash"),
        sa.CheckConstraint("byte_size >= 0", name="ck_payload_partition_byte_size"),
        sa.CheckConstraint("row_or_item_count >= 0", name="ck_payload_partition_item_count"),
        schema="data",
    )
    op.create_table(
        "payload_manifest_partition",
        _id("payload_manifest_id"),
        _id("payload_partition_id"),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["payload_manifest_id"],
            ["data.payload_manifest.payload_manifest_id"],
            name="fk_payload_manifest_partition_manifest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payload_partition_id"],
            ["data.payload_partition.payload_partition_id"],
            name="fk_payload_manifest_partition_partition",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "payload_manifest_id",
            "payload_partition_id",
            name="pk_payload_manifest_partition",
        ),
        sa.UniqueConstraint(
            "payload_manifest_id", "ordinal", name="uq_payload_manifest_partition_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_payload_manifest_partition_ordinal"),
        schema="data",
    )
    op.create_table(
        "payload_quality_summary",
        _id("payload_quality_summary_id"),
        _id("payload_manifest_id"),
        sa.Column("quality_status", sa.String(20), nullable=False),
        sa.Column("missing_count", sa.BigInteger(), nullable=False),
        sa.Column("invalid_count", sa.BigInteger(), nullable=False),
        sa.Column("coverage_ratio", sa.Numeric(20, 18), nullable=True),
        sa.Column("quality_document", JSONB, nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["payload_manifest_id"],
            ["data.payload_manifest.payload_manifest_id"],
            name="fk_payload_quality_manifest",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("payload_quality_summary_id", name="pk_payload_quality_summary"),
        sa.UniqueConstraint("payload_manifest_id", name="uq_payload_quality_manifest"),
        sa.CheckConstraint(
            "quality_status IN ('passed','warning','failed','unknown')",
            name="ck_payload_quality_status",
        ),
        sa.CheckConstraint("missing_count >= 0", name="ck_payload_quality_missing_count"),
        sa.CheckConstraint("invalid_count >= 0", name="ck_payload_quality_invalid_count"),
        sa.CheckConstraint(
            "coverage_ratio IS NULL OR (coverage_ratio >= 0 AND coverage_ratio <= 1)",
            name="ck_payload_quality_coverage_ratio",
        ),
        schema="data",
    )


def _create_publication_tables() -> None:
    op.create_table(
        "payload_publication_lease",
        _id("payload_publication_lease_id"),
        sa.Column("lease_key", sa.String(240), nullable=False),
        sa.Column("holder_id", sa.String(160), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("lease_status", sa.String(20), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint(
            "payload_publication_lease_id", name="pk_payload_publication_lease"
        ),
        sa.UniqueConstraint("lease_key", "fencing_token", name="uq_payload_lease_fence"),
        sa.CheckConstraint("fencing_token >= 1", name="ck_payload_lease_fence_positive"),
        sa.CheckConstraint(
            "lease_status IN ('active','released','expired','fenced')",
            name="ck_payload_lease_status",
        ),
        sa.CheckConstraint("expires_at > acquired_at", name="ck_payload_lease_expiry"),
        schema="data",
    )
    op.create_index(
        "uq_payload_publication_lease_active",
        "payload_publication_lease",
        ["lease_key"],
        unique=True,
        schema="data",
        postgresql_where=sa.text("lease_status = 'active'"),
    )


def _create_immutability_guards() -> None:
    immutable_tables = (
        "payload_contract_family",
        "payload_contract_version",
        "payload_contract_compatibility",
        "physical_encoding_version",
        "payload_manifest",
        "payload_partition",
        "payload_manifest_partition",
        "payload_quality_summary",
    )
    for table in immutable_tables:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON data.{table} "
            "FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def downgrade() -> None:
    for table in (
        "payload_quality_summary",
        "payload_manifest_partition",
        "payload_partition",
        "payload_manifest",
        "physical_encoding_version",
        "payload_contract_compatibility",
        "payload_contract_version",
        "payload_contract_family",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON data.{table}")
    op.drop_index(
        "uq_payload_publication_lease_active",
        table_name="payload_publication_lease",
        schema="data",
    )
    op.drop_table("payload_publication_lease", schema="data")
    op.drop_table("payload_quality_summary", schema="data")
    op.drop_table("payload_manifest_partition", schema="data")
    op.drop_table("payload_partition", schema="data")
    op.drop_table("payload_manifest", schema="data")
    op.drop_table("payload_object", schema="data")
    op.drop_table("physical_encoding_version", schema="data")
    op.drop_table("payload_contract_compatibility", schema="data")
    op.drop_table("payload_contract_version", schema="data")
    op.drop_table("payload_contract_family", schema="data")
