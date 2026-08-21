"""Add exact provider identities and immutable v0.22 seed import evidence.

Revision ID: 20260816_94_v022_seed_import
Revises: 20260814_93_v022_diag_lineage
"""

from __future__ import annotations

from alembic import op

revision = "20260816_94_v022_seed_import"
down_revision = "20260814_93_v022_diag_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE catalog.security_identifier
          ADD COLUMN provider_scope varchar(100);
        UPDATE catalog.security_identifier SET provider_scope='catalog';
        ALTER TABLE catalog.security_identifier
          ALTER COLUMN provider_scope SET NOT NULL,
          ADD CONSTRAINT ck_security_identifier_provider_scope
            CHECK (btrim(provider_scope)<>''),
          DROP CONSTRAINT uq_security_identifier_period,
          ADD CONSTRAINT uq_security_identifier_provider_period
            UNIQUE (provider_scope,identifier_type,identifier_value,valid_from);

        DO $$ BEGIN
          IF EXISTS (
            SELECT 1
              FROM catalog.security_identifier left_item
              JOIN catalog.security_identifier right_item
                ON left_item.security_identifier_id < right_item.security_identifier_id
               AND left_item.provider_scope=right_item.provider_scope
               AND left_item.identifier_type=right_item.identifier_type
               AND lower(left_item.identifier_value)=lower(right_item.identifier_value)
               AND daterange(coalesce(left_item.valid_from,'-infinity'::date),
                             coalesce(left_item.valid_to,'infinity'::date),'[)')
                   && daterange(coalesce(right_item.valid_from,'-infinity'::date),
                                coalesce(right_item.valid_to,'infinity'::date),'[)')
          ) THEN
            RAISE EXCEPTION 'Existing Security Identifier effective periods overlap';
          END IF;
        END $$;

        CREATE FUNCTION catalog.reject_security_identifier_overlap()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(
            NEW.provider_scope || ':' || NEW.identifier_type || ':' ||
            lower(NEW.identifier_value),0
          ));
          IF EXISTS (
            SELECT 1 FROM catalog.security_identifier item
             WHERE item.security_identifier_id<>NEW.security_identifier_id
               AND item.provider_scope=NEW.provider_scope
               AND item.identifier_type=NEW.identifier_type
               AND lower(item.identifier_value)=lower(NEW.identifier_value)
               AND daterange(coalesce(item.valid_from,'-infinity'::date),
                             coalesce(item.valid_to,'infinity'::date),'[)')
                   && daterange(coalesce(NEW.valid_from,'-infinity'::date),
                                coalesce(NEW.valid_to,'infinity'::date),'[)')
          ) THEN
            RAISE EXCEPTION 'Security Identifier effective periods overlap';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_security_identifier_no_overlap
          BEFORE INSERT OR UPDATE ON catalog.security_identifier
          FOR EACH ROW EXECUTE FUNCTION catalog.reject_security_identifier_overlap();

        CREATE TABLE data.source_snapshot_security_subject (
          source_snapshot_security_subject_id uuid PRIMARY KEY,
          source_snapshot_id uuid NOT NULL REFERENCES data.source_snapshot,
          security_id uuid NOT NULL REFERENCES catalog.security,
          security_identifier_id uuid NOT NULL REFERENCES catalog.security_identifier,
          provider_scope varchar(100) NOT NULL CHECK (btrim(provider_scope)<>''),
          provider_symbol varchar(160) NOT NULL CHECK (btrim(provider_symbol)<>''),
          identifier_valid_from date NULL,
          identifier_valid_to date NULL,
          fetch_status varchar(24) NOT NULL
            CHECK (fetch_status IN ('fetched','unavailable','failed')),
          failure_reason varchar(240) NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (source_snapshot_id,security_id),
          UNIQUE (source_snapshot_id,provider_scope,provider_symbol),
          CHECK (identifier_valid_to IS NULL OR identifier_valid_from IS NULL OR
                 identifier_valid_from<identifier_valid_to),
          CHECK (((fetch_status='fetched' AND failure_reason IS NULL) OR
                  (fetch_status<>'fetched' AND failure_reason IS NOT NULL AND
                   btrim(failure_reason)<>'')) IS TRUE)
        );

        CREATE FUNCTION data.validate_source_snapshot_security_subject()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE identifier_row record; snapshot_row record;
        BEGIN
          SELECT identifier.security_id,identifier.provider_scope,
                 identifier.identifier_type,identifier.identifier_value,
                 identifier.valid_from,identifier.valid_to
            INTO identifier_row
            FROM catalog.security_identifier identifier
           WHERE identifier.security_identifier_id=NEW.security_identifier_id;
          SELECT artifact.status,provider.provider_key
            INTO snapshot_row
            FROM data.source_snapshot snapshot
            JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id
            JOIN data.data_series_version version
              ON version.data_series_version_id=snapshot.data_series_version_id
            JOIN data.source_provider provider
              ON provider.source_provider_id=version.source_provider_id
           WHERE snapshot.source_snapshot_id=NEW.source_snapshot_id;
          IF snapshot_row.status IS DISTINCT FROM 'published' OR
             identifier_row.security_id IS DISTINCT FROM NEW.security_id OR
             identifier_row.identifier_type IS DISTINCT FROM 'provider_symbol' OR
             identifier_row.provider_scope IS DISTINCT FROM NEW.provider_scope OR
             identifier_row.identifier_value IS DISTINCT FROM NEW.provider_symbol OR
             identifier_row.valid_from IS DISTINCT FROM NEW.identifier_valid_from OR
             identifier_row.valid_to IS DISTINCT FROM NEW.identifier_valid_to OR
             snapshot_row.provider_key IS DISTINCT FROM NEW.provider_scope THEN
            RAISE EXCEPTION 'Source Snapshot subject requires exact provider Security identity';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_source_snapshot_security_subject_validate
          BEFORE INSERT ON data.source_snapshot_security_subject
          FOR EACH ROW EXECUTE FUNCTION data.validate_source_snapshot_security_subject();
        CREATE TRIGGER trg_source_snapshot_security_subject_append_only
          BEFORE UPDATE OR DELETE ON data.source_snapshot_security_subject
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();

        CREATE FUNCTION catalog.protect_bound_security_identifier()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM data.source_snapshot_security_subject subject
             WHERE subject.security_identifier_id=OLD.security_identifier_id
          ) THEN
            RAISE EXCEPTION 'Bound Security Identifiers are immutable';
          END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END $$;
        CREATE TRIGGER trg_security_identifier_bound_immutable
          BEFORE UPDATE OR DELETE ON catalog.security_identifier
          FOR EACH ROW EXECUTE FUNCTION catalog.protect_bound_security_identifier();

        CREATE TABLE data.v022_external_import_manifest (
          external_import_manifest_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          manifest_key varchar(160) NOT NULL CHECK (btrim(manifest_key)<>''),
          version_number integer NOT NULL CHECK (version_number>=1),
          source_project_key varchar(160) NOT NULL CHECK (btrim(source_project_key)<>''),
          source_release_key varchar(240) NOT NULL CHECK (btrim(source_release_key)<>''),
          object_count integer NOT NULL CHECK (object_count>=1),
          manifest_document jsonb NOT NULL CHECK (jsonb_typeof(manifest_document)='object'),
          manifest_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (manifest_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (manifest_key,version_number)
        );
        CREATE TABLE data.v022_external_import_object (
          external_import_object_id uuid PRIMARY KEY,
          external_import_manifest_id uuid NOT NULL
            REFERENCES data.v022_external_import_manifest,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          object_role varchar(80) NOT NULL CHECK (btrim(object_role)<>''),
          logical_key varchar(240) NOT NULL CHECK (btrim(logical_key)<>''),
          media_type varchar(120) NOT NULL CHECK (btrim(media_type)<>''),
          content_sha256 varchar(64) NOT NULL
            CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
          size_bytes bigint NOT NULL CHECK (size_bytes>=0),
          source_uri text NOT NULL CHECK (btrim(source_uri)<>''),
          license_key varchar(120) NOT NULL CHECK (btrim(license_key)<>''),
          provider_key varchar(100) NULL,
          provenance_status varchar(24) NOT NULL
            CHECK (provenance_status IN ('verified','needs_review','unavailable')),
          usage_scope varchar(24) NOT NULL
            CHECK (usage_scope IN ('local_research','redistributable','unresolved')),
          metadata_document jsonb NOT NULL
            CHECK (jsonb_typeof(metadata_document)='object'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (external_import_manifest_id,ordinal),
          UNIQUE (external_import_manifest_id,logical_key)
        );

        CREATE FUNCTION data.validate_v022_external_import_manifest()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number
            INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF artifact_row.artifact_type IS DISTINCT FROM 'v022_external_import_manifest' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_external_import_manifest__' || NEW.manifest_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number THEN
            RAISE EXCEPTION 'External Import Manifest requires its exact draft Artifact';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_external_import_manifest_validate
          BEFORE INSERT ON data.v022_external_import_manifest
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_external_import_manifest();

        CREATE FUNCTION data.validate_v022_external_import_manifest_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_status_value varchar; actual_count integer;
                actual_document jsonb;
        BEGIN
          SELECT status INTO artifact_status_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT count(*),jsonb_build_object(
                   'contract_version','v0.22.sp500_seed_import.v1',
                   'manifest_key',NEW.manifest_key,
                   'version_number',NEW.version_number,
                   'source_project_key',NEW.source_project_key,
                   'source_release_key',NEW.source_release_key,
                   'objects',coalesce(jsonb_agg(jsonb_build_object(
                     'ordinal',object.ordinal,
                     'object_role',object.object_role,
                     'logical_key',object.logical_key,
                     'media_type',object.media_type,
                     'content_sha256',object.content_sha256,
                     'size_bytes',object.size_bytes,
                     'source_uri',object.source_uri,
                     'license_key',object.license_key,
                     'provider_key',object.provider_key,
                     'provenance_status',object.provenance_status,
                     'usage_scope',object.usage_scope,
                     'metadata',object.metadata_document
                   ) ORDER BY object.ordinal),'[]'::jsonb)
                 )
            INTO actual_count,actual_document
            FROM data.v022_external_import_object object
           WHERE object.external_import_manifest_id=NEW.external_import_manifest_id;
          IF artifact_status_value IS DISTINCT FROM 'published' OR
             actual_count<>NEW.object_count OR
             actual_document IS DISTINCT FROM NEW.manifest_document THEN
            RAISE EXCEPTION 'External Import Manifest projection is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_external_import_manifest_complete
          AFTER INSERT ON data.v022_external_import_manifest
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION data.validate_v022_external_import_manifest_complete();
        CREATE TRIGGER trg_v022_external_import_manifest_append_only
          BEFORE UPDATE OR DELETE ON data.v022_external_import_manifest
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_external_import_object_append_only
          BEFORE UPDATE OR DELETE ON data.v022_external_import_object
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM data.source_snapshot_security_subject) OR
             EXISTS (SELECT 1 FROM data.v022_external_import_manifest) OR
             EXISTS (
               SELECT 1 FROM catalog.security_identifier
                WHERE provider_scope<>'catalog'
             ) THEN
            RAISE EXCEPTION 'Cannot downgrade with v0.22 seed import identities';
          END IF;
        END $$;

        DROP FUNCTION IF EXISTS data.validate_v022_external_import_manifest_complete() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_v022_external_import_manifest() CASCADE;
        DROP TABLE data.v022_external_import_object;
        DROP TABLE data.v022_external_import_manifest;
        DROP FUNCTION IF EXISTS catalog.protect_bound_security_identifier() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_source_snapshot_security_subject() CASCADE;
        DROP TABLE data.source_snapshot_security_subject;
        DROP FUNCTION IF EXISTS catalog.reject_security_identifier_overlap() CASCADE;
        ALTER TABLE catalog.security_identifier
          DROP CONSTRAINT uq_security_identifier_provider_period,
          DROP CONSTRAINT ck_security_identifier_provider_scope,
          ADD CONSTRAINT uq_security_identifier_period
            UNIQUE (identifier_type,identifier_value,valid_from),
          DROP COLUMN provider_scope;
        """
    )
