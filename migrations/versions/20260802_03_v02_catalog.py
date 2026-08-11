"""Add the versioned asset, universe, and data-requirement catalog.

Revision ID: 20260802_03_v02_catalog
Revises: 20260802_02_v02_lineage
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_03_v02_catalog"
down_revision: str | None = "20260802_02_v02_lineage"
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
        [column],
        [target],
        name=f"fk_{table}_{column}",
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        "master_data_release",
        _id("master_data_release_id"),
        _id("artifact_id"),
        sa.Column("release_key", sa.String(120), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        _created(),
        _fk("master_data_release", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("master_data_release_id", name="pk_master_data_release"),
        sa.UniqueConstraint("artifact_id", name="uq_master_data_release_artifact_id"),
        sa.UniqueConstraint(
            "release_key", "version_number", name="uq_master_data_release_key_version"
        ),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_master_data_release_version_number_positive"
        ),
        schema="catalog",
    )
    op.create_table(
        "asset",
        _id("asset_id"),
        _id("master_data_release_id"),
        sa.Column("asset_key", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("asset_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        _created(),
        _fk(
            "asset", "master_data_release_id", "catalog.master_data_release.master_data_release_id"
        ),
        sa.PrimaryKeyConstraint("asset_id", name="pk_asset"),
        sa.UniqueConstraint("asset_key", name="uq_asset_asset_key"),
        sa.CheckConstraint(
            "asset_type IN ('etf', 'equity', 'fund', 'index', 'commodity')",
            name="ck_asset_asset_type_allowed",
        ),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_asset_status_allowed"),
        schema="catalog",
    )
    op.create_table(
        "calendar_definition",
        _id("calendar_definition_id"),
        _id("master_data_release_id"),
        sa.Column("calendar_key", sa.String(40), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("venue_mic", sa.String(4), nullable=False),
        _created(),
        _fk(
            "calendar_definition",
            "master_data_release_id",
            "catalog.master_data_release.master_data_release_id",
        ),
        sa.PrimaryKeyConstraint("calendar_definition_id", name="pk_calendar_definition"),
        sa.UniqueConstraint("calendar_key", name="uq_calendar_definition_calendar_key"),
        schema="catalog",
    )
    op.create_table(
        "classification_scheme",
        _id("classification_scheme_id"),
        _id("master_data_release_id"),
        sa.Column("scheme_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        _created(),
        _fk(
            "classification_scheme",
            "master_data_release_id",
            "catalog.master_data_release.master_data_release_id",
        ),
        sa.PrimaryKeyConstraint("classification_scheme_id", name="pk_classification_scheme"),
        sa.UniqueConstraint("scheme_key", name="uq_classification_scheme_scheme_key"),
        schema="catalog",
    )
    op.create_table(
        "universe_definition",
        _id("universe_definition_id"),
        _id("master_data_release_id"),
        sa.Column("universe_key", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        _created(),
        _fk(
            "universe_definition",
            "master_data_release_id",
            "catalog.master_data_release.master_data_release_id",
        ),
        sa.PrimaryKeyConstraint("universe_definition_id", name="pk_universe_definition"),
        sa.UniqueConstraint("universe_key", name="uq_universe_definition_universe_key"),
        schema="catalog",
    )
    op.create_table(
        "data_requirement_definition",
        _id("data_requirement_definition_id"),
        _id("master_data_release_id"),
        sa.Column("requirement_set_key", sa.String(140), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        _created(),
        _fk(
            "data_requirement_definition",
            "master_data_release_id",
            "catalog.master_data_release.master_data_release_id",
        ),
        sa.PrimaryKeyConstraint(
            "data_requirement_definition_id", name="pk_data_requirement_definition"
        ),
        sa.UniqueConstraint(
            "requirement_set_key", name="uq_data_requirement_definition_requirement_set_key"
        ),
        schema="catalog",
    )
    op.create_table(
        "asset_identifier",
        _id("asset_identifier_id"),
        _id("master_data_release_id"),
        _id("asset_id"),
        sa.Column("identifier_type", sa.String(30), nullable=False),
        sa.Column("identifier_value", sa.String(80), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        _created(),
        _fk(
            "asset_identifier",
            "master_data_release_id",
            "catalog.master_data_release.master_data_release_id",
        ),
        _fk("asset_identifier", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint("asset_identifier_id", name="pk_asset_identifier"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to",
            name="ck_asset_identifier_valid_period",
        ),
        schema="catalog",
    )
    op.create_index(
        "ix_asset_identifier_lookup",
        "asset_identifier",
        ["identifier_type", "identifier_value"],
        schema="catalog",
    )
    op.create_table(
        "asset_listing",
        _id("asset_listing_id"),
        _id("master_data_release_id"),
        _id("asset_id"),
        _id("calendar_definition_id"),
        sa.Column("listing_key", sa.String(140), nullable=False),
        sa.Column("venue_mic", sa.String(4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        _created(),
        _fk(
            "asset_listing",
            "master_data_release_id",
            "catalog.master_data_release.master_data_release_id",
        ),
        _fk("asset_listing", "asset_id", "catalog.asset.asset_id"),
        _fk(
            "asset_listing",
            "calendar_definition_id",
            "catalog.calendar_definition.calendar_definition_id",
        ),
        sa.PrimaryKeyConstraint("asset_listing_id", name="pk_asset_listing"),
        sa.UniqueConstraint("listing_key", name="uq_asset_listing_listing_key"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_asset_listing_currency_iso_code"),
        sa.CheckConstraint("venue_mic ~ '^[A-Z0-9]{4}$'", name="ck_asset_listing_venue_mic_format"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to",
            name="ck_asset_listing_valid_period",
        ),
        schema="catalog",
    )
    op.create_table(
        "classification_value",
        _id("classification_value_id"),
        _id("master_data_release_id"),
        _id("classification_scheme_id"),
        sa.Column("value_key", sa.String(100), nullable=False),
        sa.Column("label_key", sa.String(200), nullable=False),
        _created(),
        _fk(
            "classification_value",
            "master_data_release_id",
            "catalog.master_data_release.master_data_release_id",
        ),
        _fk(
            "classification_value",
            "classification_scheme_id",
            "catalog.classification_scheme.classification_scheme_id",
        ),
        sa.PrimaryKeyConstraint("classification_value_id", name="pk_classification_value"),
        sa.UniqueConstraint(
            "classification_scheme_id",
            "value_key",
            name="uq_classification_value_scheme_value_key",
        ),
        schema="catalog",
    )
    op.create_table(
        "listing_symbol",
        _id("listing_symbol_id"),
        _id("master_data_release_id"),
        _id("asset_listing_id"),
        sa.Column("symbol_type", sa.String(30), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        _created(),
        _fk(
            "listing_symbol",
            "master_data_release_id",
            "catalog.master_data_release.master_data_release_id",
        ),
        _fk("listing_symbol", "asset_listing_id", "catalog.asset_listing.asset_listing_id"),
        sa.PrimaryKeyConstraint("listing_symbol_id", name="pk_listing_symbol"),
        sa.CheckConstraint(
            "symbol_type IN ('ticker', 'vendor_symbol')",
            name="ck_listing_symbol_symbol_type_allowed",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to",
            name="ck_listing_symbol_valid_period",
        ),
        schema="catalog",
    )
    op.create_index(
        "ix_listing_symbol_lookup",
        "listing_symbol",
        ["symbol_type", "symbol"],
        schema="catalog",
    )
    op.create_table(
        "asset_classification",
        _id("asset_classification_id"),
        _id("master_data_release_id"),
        _id("asset_id"),
        _id("classification_value_id"),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        _created(),
        _fk(
            "asset_classification",
            "master_data_release_id",
            "catalog.master_data_release.master_data_release_id",
        ),
        _fk("asset_classification", "asset_id", "catalog.asset.asset_id"),
        _fk(
            "asset_classification",
            "classification_value_id",
            "catalog.classification_value.classification_value_id",
        ),
        sa.PrimaryKeyConstraint("asset_classification_id", name="pk_asset_classification"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to",
            name="ck_asset_classification_valid_period",
        ),
        schema="catalog",
    )
    op.create_table(
        "universe_version",
        _id("universe_version_id"),
        _id("universe_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        _created(),
        _fk(
            "universe_version",
            "universe_definition_id",
            "catalog.universe_definition.universe_definition_id",
        ),
        _fk("universe_version", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("universe_version_id", name="pk_universe_version"),
        sa.UniqueConstraint("artifact_id", name="uq_universe_version_artifact_id"),
        sa.UniqueConstraint(
            "universe_definition_id",
            "version_number",
            name="uq_universe_version_definition_version",
        ),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_universe_version_version_number_positive"
        ),
        sa.CheckConstraint("member_count >= 1", name="ck_universe_version_member_count_positive"),
        schema="catalog",
    )
    op.create_table(
        "universe_member",
        _id("universe_member_id"),
        _id("universe_version_id"),
        _id("asset_id"),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _created(),
        _fk(
            "universe_member", "universe_version_id", "catalog.universe_version.universe_version_id"
        ),
        _fk("universe_member", "asset_id", "catalog.asset.asset_id"),
        sa.PrimaryKeyConstraint("universe_member_id", name="pk_universe_member"),
        sa.UniqueConstraint(
            "universe_version_id", "asset_id", name="uq_universe_member_version_asset"
        ),
        sa.UniqueConstraint(
            "universe_version_id", "ordinal", name="uq_universe_member_version_ordinal"
        ),
        sa.CheckConstraint(
            "role IN ('candidate', 'benchmark', 'auxiliary_tradable')",
            name="ck_universe_member_role_allowed",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_universe_member_ordinal_nonnegative"),
        schema="catalog",
    )
    op.create_table(
        "data_requirement_version",
        _id("data_requirement_version_id"),
        _id("data_requirement_definition_id"),
        _id("artifact_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("requirement_count", sa.Integer(), nullable=False),
        _created(),
        _fk(
            "data_requirement_version",
            "data_requirement_definition_id",
            "catalog.data_requirement_definition.data_requirement_definition_id",
        ),
        _fk("data_requirement_version", "artifact_id", "lineage.artifact.artifact_id"),
        sa.PrimaryKeyConstraint("data_requirement_version_id", name="pk_data_requirement_version"),
        sa.UniqueConstraint("artifact_id", name="uq_data_requirement_version_artifact_id"),
        sa.UniqueConstraint(
            "data_requirement_definition_id",
            "version_number",
            name="uq_data_requirement_version_definition_version",
        ),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_data_requirement_version_version_number_positive"
        ),
        sa.CheckConstraint(
            "requirement_count >= 1",
            name="ck_data_requirement_version_requirement_count_positive",
        ),
        schema="catalog",
    )
    op.create_table(
        "data_requirement_member",
        _id("data_requirement_member_id"),
        _id("data_requirement_version_id"),
        sa.Column("requirement_key", sa.String(140), nullable=False),
        sa.Column("subject", sa.String(40), nullable=False),
        sa.Column("series_key", sa.String(120), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("interval_unit", sa.String(30), nullable=False),
        sa.Column("interval_count", sa.Integer(), nullable=False),
        sa.Column("calendar_type", sa.String(30), nullable=False),
        sa.Column("session_type", sa.String(30), nullable=False),
        sa.Column("timestamp_semantics", sa.String(80), nullable=False),
        _created(),
        _fk(
            "data_requirement_member",
            "data_requirement_version_id",
            "catalog.data_requirement_version.data_requirement_version_id",
        ),
        sa.PrimaryKeyConstraint("data_requirement_member_id", name="pk_data_requirement_member"),
        sa.UniqueConstraint(
            "data_requirement_version_id",
            "requirement_key",
            name="uq_data_requirement_member_version_requirement_key",
        ),
        sa.CheckConstraint(
            "subject IN ('universe_candidate', 'universe_benchmark', "
            "'reference_series', 'calendar')",
            name="ck_data_requirement_member_subject_allowed",
        ),
        sa.CheckConstraint(
            "interval_count >= 1", name="ck_data_requirement_member_interval_count_positive"
        ),
        schema="catalog",
    )
    _create_catalog_guards()


def _create_catalog_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION catalog.assert_artifact_draft(owner_artifact_id uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE owner_status text;
        BEGIN
            SELECT status INTO owner_status
            FROM lineage.artifact WHERE artifact_id = owner_artifact_id;
            IF owner_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'catalog rows can only change while their artifact is draft';
            END IF;
        END;
        $$;

        CREATE FUNCTION catalog.enforce_release_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM catalog.assert_artifact_draft(
                CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END
            );
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_master_data_release_draft
        BEFORE INSERT OR UPDATE OR DELETE ON catalog.master_data_release
        FOR EACH ROW EXECUTE FUNCTION catalog.enforce_release_draft();

        CREATE FUNCTION catalog.enforce_master_owned_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE release_id uuid; owner_artifact_id uuid;
        BEGIN
            release_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.master_data_release_id ELSE NEW.master_data_release_id END;
            SELECT artifact_id INTO owner_artifact_id FROM catalog.master_data_release
            WHERE master_data_release_id = release_id;
            PERFORM catalog.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        """
    )
    master_tables = (
        "asset",
        "asset_identifier",
        "calendar_definition",
        "asset_listing",
        "listing_symbol",
        "classification_scheme",
        "classification_value",
        "asset_classification",
        "universe_definition",
        "data_requirement_definition",
    )
    for table in master_tables:
        op.execute(
            f"CREATE TRIGGER trg_{table}_draft BEFORE INSERT OR UPDATE OR DELETE "
            f"ON catalog.{table} FOR EACH ROW "
            "EXECUTE FUNCTION catalog.enforce_master_owned_draft()"
        )
    op.execute(
        """
        CREATE FUNCTION catalog.enforce_version_owned_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE owner_artifact_id uuid;
        BEGIN
            owner_artifact_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.artifact_id ELSE NEW.artifact_id END;
            PERFORM catalog.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_universe_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON catalog.universe_version
        FOR EACH ROW EXECUTE FUNCTION catalog.enforce_version_owned_draft();
        CREATE TRIGGER trg_data_requirement_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON catalog.data_requirement_version
        FOR EACH ROW EXECUTE FUNCTION catalog.enforce_version_owned_draft();

        CREATE FUNCTION catalog.enforce_universe_member_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE version_id uuid; owner_artifact_id uuid;
        BEGIN
            version_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.universe_version_id ELSE NEW.universe_version_id END;
            SELECT artifact_id INTO owner_artifact_id FROM catalog.universe_version
            WHERE universe_version_id = version_id;
            PERFORM catalog.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_universe_member_draft
        BEFORE INSERT OR UPDATE OR DELETE ON catalog.universe_member
        FOR EACH ROW EXECUTE FUNCTION catalog.enforce_universe_member_draft();

        CREATE FUNCTION catalog.enforce_requirement_member_draft() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE version_id uuid; owner_artifact_id uuid;
        BEGIN
            version_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.data_requirement_version_id ELSE NEW.data_requirement_version_id END;
            SELECT artifact_id INTO owner_artifact_id FROM catalog.data_requirement_version
            WHERE data_requirement_version_id = version_id;
            PERFORM catalog.assert_artifact_draft(owner_artifact_id);
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_data_requirement_member_draft
        BEFORE INSERT OR UPDATE OR DELETE ON catalog.data_requirement_member
        FOR EACH ROW EXECUTE FUNCTION catalog.enforce_requirement_member_draft();

        CREATE FUNCTION catalog.reject_identifier_overlap() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM catalog.asset_identifier item
                WHERE item.asset_identifier_id <> NEW.asset_identifier_id
                  AND item.identifier_type = NEW.identifier_type
                  AND item.identifier_value = NEW.identifier_value
                  AND daterange(COALESCE(item.valid_from, '-infinity'::date),
                                COALESCE(item.valid_to, 'infinity'::date), '[)')
                      && daterange(COALESCE(NEW.valid_from, '-infinity'::date),
                                   COALESCE(NEW.valid_to, 'infinity'::date), '[)')
            ) THEN
                RAISE EXCEPTION 'asset identifier effective periods overlap';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_asset_identifier_no_overlap
        BEFORE INSERT OR UPDATE ON catalog.asset_identifier
        FOR EACH ROW EXECUTE FUNCTION catalog.reject_identifier_overlap();

        CREATE FUNCTION catalog.reject_listing_symbol_overlap() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM catalog.listing_symbol item
                WHERE item.listing_symbol_id <> NEW.listing_symbol_id
                  AND item.asset_listing_id = NEW.asset_listing_id
                  AND item.symbol_type = NEW.symbol_type
                  AND daterange(COALESCE(item.valid_from, '-infinity'::date),
                                COALESCE(item.valid_to, 'infinity'::date), '[)')
                      && daterange(COALESCE(NEW.valid_from, '-infinity'::date),
                                   COALESCE(NEW.valid_to, 'infinity'::date), '[)')
            ) THEN
                RAISE EXCEPTION 'listing symbol effective periods overlap';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_listing_symbol_no_overlap
        BEFORE INSERT OR UPDATE ON catalog.listing_symbol
        FOR EACH ROW EXECUTE FUNCTION catalog.reject_listing_symbol_overlap();

        CREATE FUNCTION catalog.reject_classification_overlap() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM catalog.asset_classification item
                JOIN catalog.classification_value old_value
                  ON old_value.classification_value_id = item.classification_value_id
                JOIN catalog.classification_value new_value
                  ON new_value.classification_value_id = NEW.classification_value_id
                WHERE item.asset_classification_id <> NEW.asset_classification_id
                  AND item.asset_id = NEW.asset_id
                  AND old_value.classification_scheme_id = new_value.classification_scheme_id
                  AND daterange(COALESCE(item.valid_from, '-infinity'::date),
                                COALESCE(item.valid_to, 'infinity'::date), '[)')
                      && daterange(COALESCE(NEW.valid_from, '-infinity'::date),
                                   COALESCE(NEW.valid_to, 'infinity'::date), '[)')
            ) THEN
                RAISE EXCEPTION 'asset classification effective periods overlap';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_asset_classification_no_overlap
        BEFORE INSERT OR UPDATE ON catalog.asset_classification
        FOR EACH ROW EXECUTE FUNCTION catalog.reject_classification_overlap();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS catalog.reject_classification_overlap() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS catalog.reject_listing_symbol_overlap() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS catalog.reject_identifier_overlap() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS catalog.enforce_requirement_member_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS catalog.enforce_universe_member_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS catalog.enforce_version_owned_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS catalog.enforce_master_owned_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS catalog.enforce_release_draft() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS catalog.assert_artifact_draft(uuid) CASCADE")
    for table in (
        "data_requirement_member",
        "data_requirement_version",
        "universe_member",
        "universe_version",
        "asset_classification",
        "listing_symbol",
        "classification_value",
        "asset_listing",
        "asset_identifier",
        "data_requirement_definition",
        "universe_definition",
        "classification_scheme",
        "calendar_definition",
        "asset",
        "master_data_release",
    ):
        op.drop_table(table, schema="catalog")
