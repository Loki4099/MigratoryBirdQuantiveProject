"""Add source contracts, immutable snapshots, cleaning versions, and calendars.

Revision ID: 20260803_04_v02_data_contracts
Revises: 20260802_03_v02_catalog
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_04_v02_data_contracts"
down_revision: str | None = "20260802_03_v02_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _id(name: str) -> sa.Column:
    return sa.Column(name, UUID, nullable=False)


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table}_{column}", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "data_contract_release",
        _id("data_contract_release_id"),
        _id("artifact_id"),
        sa.Column("release_key", sa.String(120), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        _created(),
        _fk("data_contract_release", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("data_contract_release_id", name="pk_data_contract_release"),
        sa.UniqueConstraint("artifact_id", name="uq_data_contract_release_artifact_id"),
        sa.UniqueConstraint(
            "release_key", "version_number", name="uq_data_contract_release_key_version"
        ),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_data_contract_release_version_number_positive"
        ),
        schema="data",
    )
    op.create_table(
        "source_provider",
        _id("source_provider_id"),
        _id("data_contract_release_id"),
        sa.Column("provider_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("provider_type", sa.String(40), nullable=False),
        sa.Column("homepage", sa.Text(), nullable=False),
        sa.Column("terms_note", sa.Text(), nullable=False),
        _created(),
        _fk(
            "source_provider",
            "data_contract_release_id",
            "data.data_contract_release.data_contract_release_id",
        ),
        sa.PrimaryKeyConstraint("source_provider_id", name="pk_source_provider"),
        sa.UniqueConstraint("provider_key", name="uq_source_provider_provider_key"),
        sa.CheckConstraint(
            "provider_type IN ('market_data_wrapper', 'official_api', 'software_calendar')",
            name="ck_source_provider_provider_type_allowed",
        ),
        schema="data",
    )
    op.create_table(
        "data_series_definition",
        _id("data_series_definition_id"),
        _id("data_contract_release_id"),
        sa.Column("series_key", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.String(40), nullable=False),
        sa.Column("value_kind", sa.String(40), nullable=False),
        _created(),
        _fk(
            "data_series_definition",
            "data_contract_release_id",
            "data.data_contract_release.data_contract_release_id",
        ),
        sa.PrimaryKeyConstraint("data_series_definition_id", name="pk_data_series_definition"),
        sa.UniqueConstraint("series_key", name="uq_data_series_definition_series_key"),
        sa.CheckConstraint(
            "subject_type IN ('asset_listing', 'reference_series', 'calendar')",
            name="ck_data_series_definition_subject_type_allowed",
        ),
        sa.CheckConstraint(
            "value_kind IN ('market_bar', 'rate_observation', 'calendar_session')",
            name="ck_data_series_definition_value_kind_allowed",
        ),
        schema="data",
    )
    op.create_table(
        "cleaning_definition",
        _id("cleaning_definition_id"),
        _id("data_contract_release_id"),
        sa.Column("cleaning_key", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        _created(),
        _fk(
            "cleaning_definition",
            "data_contract_release_id",
            "data.data_contract_release.data_contract_release_id",
        ),
        sa.PrimaryKeyConstraint("cleaning_definition_id", name="pk_cleaning_definition"),
        sa.UniqueConstraint("cleaning_key", name="uq_cleaning_definition_cleaning_key"),
        schema="data",
    )
    op.create_table(
        "data_series_version",
        _id("data_series_version_id"),
        _id("data_series_definition_id"),
        _id("source_provider_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("provider_series_key", sa.String(160), nullable=False),
        sa.Column("interval_unit", sa.String(30), nullable=False),
        sa.Column("interval_count", sa.Integer(), nullable=False),
        sa.Column("calendar_key", sa.String(40), nullable=False),
        sa.Column("timestamp_semantics", sa.String(100), nullable=False),
        sa.Column("availability_semantics", sa.String(160), nullable=False),
        sa.Column("parser_version", sa.String(80), nullable=False),
        sa.Column("request_template", postgresql.JSONB(), nullable=False),
        sa.Column("field_mapping", postgresql.JSONB(), nullable=False),
        _created(),
        _fk(
            "data_series_version",
            "data_series_definition_id",
            "data.data_series_definition.data_series_definition_id",
        ),
        _fk(
            "data_series_version",
            "source_provider_id",
            "data.source_provider.source_provider_id",
        ),
        _fk("data_series_version", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("data_series_version_id", name="pk_data_series_version"),
        sa.UniqueConstraint("artifact_id", name="uq_data_series_version_artifact_id"),
        sa.UniqueConstraint(
            "data_series_definition_id",
            "version_number",
            name="uq_data_series_version_definition_version",
        ),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_data_series_version_version_number_positive"
        ),
        sa.CheckConstraint(
            "interval_count >= 1", name="ck_data_series_version_interval_count_positive"
        ),
        schema="data",
    )
    op.create_table(
        "cleaning_version",
        _id("cleaning_version_id"),
        _id("cleaning_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("implementation_key", sa.String(140), nullable=False),
        sa.Column("implementation_version", sa.String(80), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        _created(),
        _fk(
            "cleaning_version",
            "cleaning_definition_id",
            "data.cleaning_definition.cleaning_definition_id",
        ),
        _fk("cleaning_version", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("cleaning_version_id", name="pk_cleaning_version"),
        sa.UniqueConstraint("artifact_id", name="uq_cleaning_version_artifact_id"),
        sa.UniqueConstraint(
            "cleaning_definition_id",
            "version_number",
            name="uq_cleaning_version_definition_version",
        ),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_cleaning_version_version_number_positive"
        ),
        schema="data",
    )
    op.create_table(
        "source_snapshot",
        _id("source_snapshot_id"),
        _id("data_series_version_id"),
        _id("artifact_id"),
        sa.Column("snapshot_key", sa.String(180), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("parser_version", sa.String(80), nullable=False),
        sa.Column("request_parameters", postgresql.JSONB(), nullable=False),
        sa.Column("response_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload_compression", sa.String(20), nullable=False),
        sa.Column("raw_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("compressed_payload", sa.LargeBinary(), nullable=False),
        _created(),
        _fk(
            "source_snapshot",
            "data_series_version_id",
            "data.data_series_version.data_series_version_id",
        ),
        _fk("source_snapshot", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("source_snapshot_id", name="pk_source_snapshot"),
        sa.UniqueConstraint("artifact_id", name="uq_source_snapshot_artifact_id"),
        sa.UniqueConstraint("snapshot_key", name="uq_source_snapshot_snapshot_key"),
        sa.CheckConstraint(
            "fetched_at >= requested_at", name="ck_source_snapshot_fetch_time_ordered"
        ),
        sa.CheckConstraint("raw_size_bytes >= 0", name="ck_source_snapshot_raw_size_nonnegative"),
        sa.CheckConstraint(
            "payload_compression = 'zlib'", name="ck_source_snapshot_compression_allowed"
        ),
        sa.CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$'",
            name="ck_source_snapshot_payload_hash_sha256",
        ),
        schema="data",
    )
    op.create_index(
        "ix_source_snapshot_series_fetched",
        "source_snapshot",
        ["data_series_version_id", "fetched_at"],
        schema="data",
    )
    op.create_table(
        "calendar_version",
        _id("calendar_version_id"),
        _id("calendar_definition_id"),
        _id("data_series_version_id"),
        sa.Column("source_snapshot_id", UUID),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("library_name", sa.String(120), nullable=False),
        sa.Column("library_version", sa.String(80), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("session_count", sa.Integer(), nullable=False),
        _created(),
        _fk(
            "calendar_version",
            "calendar_definition_id",
            "catalog.calendar_definition.calendar_definition_id",
        ),
        _fk(
            "calendar_version",
            "data_series_version_id",
            "data.data_series_version.data_series_version_id",
        ),
        _fk(
            "calendar_version",
            "source_snapshot_id",
            "data.source_snapshot.source_snapshot_id",
        ),
        _fk("calendar_version", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("calendar_version_id", name="pk_calendar_version"),
        sa.UniqueConstraint("artifact_id", name="uq_calendar_version_artifact_id"),
        sa.UniqueConstraint(
            "calendar_definition_id",
            "version_number",
            name="uq_calendar_version_definition_version",
        ),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_calendar_version_version_number_positive"
        ),
        sa.CheckConstraint(
            "coverage_start <= coverage_end", name="ck_calendar_version_coverage_ordered"
        ),
        sa.CheckConstraint("session_count >= 1", name="ck_calendar_version_session_count_positive"),
        schema="catalog",
    )
    op.create_table(
        "calendar_session",
        _id("calendar_version_id"),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("open_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_early_close", sa.Boolean(), nullable=False),
        _fk(
            "calendar_session",
            "calendar_version_id",
            "catalog.calendar_version.calendar_version_id",
        ),
        sa.PrimaryKeyConstraint("calendar_version_id", "session_date", name="pk_calendar_session"),
        sa.CheckConstraint(
            "open_at_utc < close_at_utc", name="ck_calendar_session_open_before_close"
        ),
        schema="catalog",
    )
    _create_publication_guards()


def _create_publication_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION data.assert_artifact_draft(owner_artifact_id uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE owner_status text;
        BEGIN
            SELECT status INTO owner_status
            FROM lineage.artifact WHERE artifact_id = owner_artifact_id;
            IF owner_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'data rows can only change while their artifact is draft';
            END IF;
        END;
        $$;

        CREATE FUNCTION data.enforce_release_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM data.assert_artifact_draft(
                CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END
            );
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_data_contract_release_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.data_contract_release
        FOR EACH ROW EXECUTE FUNCTION data.enforce_release_draft();

        CREATE FUNCTION data.enforce_contract_owned_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE release_id uuid; owner_artifact_id uuid;
        BEGIN
            release_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.data_contract_release_id ELSE NEW.data_contract_release_id END;
            SELECT artifact_id INTO owner_artifact_id FROM data.data_contract_release
            WHERE data_contract_release_id = release_id;
            PERFORM data.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_source_provider_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.source_provider
        FOR EACH ROW EXECUTE FUNCTION data.enforce_contract_owned_draft();
        CREATE TRIGGER trg_data_series_definition_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.data_series_definition
        FOR EACH ROW EXECUTE FUNCTION data.enforce_contract_owned_draft();
        CREATE TRIGGER trg_cleaning_definition_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.cleaning_definition
        FOR EACH ROW EXECUTE FUNCTION data.enforce_contract_owned_draft();

        CREATE FUNCTION data.enforce_artifact_owned_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM data.assert_artifact_draft(
                CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END
            );
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_data_series_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.data_series_version
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_cleaning_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.cleaning_version
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        CREATE TRIGGER trg_source_snapshot_draft
        BEFORE INSERT OR UPDATE OR DELETE ON data.source_snapshot
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();

        CREATE FUNCTION catalog.enforce_calendar_version_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM data.assert_artifact_draft(
                CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END
            );
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_calendar_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON catalog.calendar_version
        FOR EACH ROW EXECUTE FUNCTION catalog.enforce_calendar_version_draft();

        CREATE FUNCTION catalog.enforce_calendar_session_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE version_id uuid; owner_artifact_id uuid;
        BEGIN
            version_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.calendar_version_id ELSE NEW.calendar_version_id END;
            SELECT artifact_id INTO owner_artifact_id FROM catalog.calendar_version
            WHERE calendar_version_id = version_id;
            PERFORM data.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_calendar_session_draft
        BEFORE INSERT OR UPDATE OR DELETE ON catalog.calendar_session
        FOR EACH ROW EXECUTE FUNCTION catalog.enforce_calendar_session_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS catalog.enforce_calendar_session_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS catalog.enforce_calendar_version_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS data.enforce_artifact_owned_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS data.enforce_contract_owned_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS data.enforce_release_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS data.assert_artifact_draft(uuid) CASCADE")
    op.drop_table("calendar_session", schema="catalog")
    op.drop_table("calendar_version", schema="catalog")
    op.drop_index("ix_source_snapshot_series_fetched", table_name="source_snapshot", schema="data")
    op.drop_table("source_snapshot", schema="data")
    op.drop_table("cleaning_version", schema="data")
    op.drop_table("data_series_version", schema="data")
    op.drop_table("cleaning_definition", schema="data")
    op.drop_table("data_series_definition", schema="data")
    op.drop_table("source_provider", schema="data")
    op.drop_table("data_contract_release", schema="data")
