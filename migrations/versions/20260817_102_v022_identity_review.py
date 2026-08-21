# ruff: noqa: E501
"""Add immutable v0.22 Security identity review evidence.

Revision ID: 20260817_102_v022_identity
Revises: 20260816_101_v022_ranking
"""

from __future__ import annotations

from alembic import op

revision = "20260817_102_v022_identity"
down_revision = "20260816_101_v022_ranking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE catalog.v022_security_identity_review_case (
          security_identity_review_case_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          external_import_manifest_id uuid NOT NULL
            REFERENCES data.v022_external_import_manifest,
          external_import_manifest_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          case_key varchar(200) NOT NULL CHECK (btrim(case_key)<>''),
          version_number integer NOT NULL CHECK (version_number>=1),
          provider_scope varchar(100) NOT NULL CHECK (btrim(provider_scope)<>''),
          source_symbol varchar(160) NOT NULL CHECK (btrim(source_symbol)<>''),
          first_observed_session date NOT NULL,
          last_observed_session date NOT NULL,
          observed_snapshot_count integer NOT NULL CHECK (observed_snapshot_count>=1),
          membership_episode_count integer NOT NULL CHECK (membership_episode_count>=1),
          reason_code varchar(120) NOT NULL CHECK (btrim(reason_code)<>''),
          review_document jsonb NOT NULL CHECK (jsonb_typeof(review_document)='object'),
          case_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (case_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (case_key,version_number),
          CHECK (first_observed_session<=last_observed_session),
          CHECK ((
            review_document->>'contract_version'='v0.22.security_identity_review.v1' AND
            review_document->>'external_import_manifest_id'=
              external_import_manifest_id::text AND
            review_document->>'case_key'=case_key AND
            (review_document->>'version_number')::integer=version_number AND
            review_document->>'provider_scope'=provider_scope AND
            review_document->>'source_symbol'=source_symbol AND
            (review_document->>'first_observed_session')::date=first_observed_session AND
            (review_document->>'last_observed_session')::date=last_observed_session AND
            (review_document->>'observed_snapshot_count')::integer=observed_snapshot_count AND
            (review_document->>'membership_episode_count')::integer=membership_episode_count AND
            review_document->>'reason_code'=reason_code
          ) IS TRUE)
        );

        CREATE TABLE catalog.v022_security_identity_evidence (
          security_identity_evidence_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          security_identity_review_case_id uuid NOT NULL
            REFERENCES catalog.v022_security_identity_review_case,
          review_case_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          evidence_key varchar(200) NOT NULL CHECK (btrim(evidence_key)<>''),
          version_number integer NOT NULL CHECK (version_number>=1),
          evidence_kind varchar(40) NOT NULL CHECK (evidence_kind IN (
            'source_dataset_row','sec_filing','exchange_notice','company_announcement',
            'provider_metadata','manual_analysis','other_public_record'
          )),
          source_uri text NOT NULL CHECK (
            source_uri ~ '^(https|content|project|git\\+https)://[^[:space:]]+$'
          ),
          content_sha256 varchar(64) NOT NULL
            CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
          known_at timestamptz NOT NULL,
          effective_session date NULL,
          evidence_document jsonb NOT NULL CHECK (jsonb_typeof(evidence_document)='object'),
          evidence_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (evidence_fingerprint ~ '^[0-9a-f]{64}$'),
          recorded_by varchar(160) NOT NULL CHECK (btrim(recorded_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (security_identity_review_case_id,evidence_key,version_number),
          CHECK ((
            evidence_document->>'contract_version'='v0.22.security_identity_evidence.v1' AND
            evidence_document->>'review_case_id'=
              security_identity_review_case_id::text AND
            evidence_document->>'evidence_key'=evidence_key AND
            (evidence_document->>'version_number')::integer=version_number AND
            evidence_document->>'evidence_kind'=evidence_kind AND
            evidence_document->>'source_uri'=source_uri AND
            evidence_document->>'content_sha256'=content_sha256 AND
            (evidence_document->>'known_at')::timestamptz=known_at AND
            CASE WHEN effective_session IS NULL
              THEN evidence_document->'effective_session'='null'::jsonb
              ELSE (evidence_document->>'effective_session')::date=effective_session
            END
          ) IS TRUE)
        );

        CREATE TABLE catalog.v022_security_identity_resolution (
          security_identity_resolution_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          security_identity_review_case_id uuid NOT NULL
            REFERENCES catalog.v022_security_identity_review_case,
          review_case_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          version_number integer NOT NULL CHECK (version_number>=1),
          resolution_status varchar(20) NOT NULL
            CHECK (resolution_status IN ('confirmed','provisional','unresolved')),
          resolution_kind varchar(40) NOT NULL CHECK (resolution_kind IN (
            'map_existing_security','create_security','ticker_rename','ticker_reuse',
            'share_class_conversion','reorganization','not_a_security','unavailable'
          )),
          target_security_id uuid NULL REFERENCES catalog.security,
          target_security_identifier_id uuid NULL REFERENCES catalog.security_identifier,
          supersedes_resolution_id uuid NULL
            REFERENCES catalog.v022_security_identity_resolution,
          evidence_count integer NOT NULL CHECK (evidence_count>=1),
          resolution_document jsonb NOT NULL
            CHECK (jsonb_typeof(resolution_document)='object'),
          resolution_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (resolution_fingerprint ~ '^[0-9a-f]{64}$'),
          resolved_by varchar(160) NOT NULL CHECK (btrim(resolved_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (security_identity_review_case_id,version_number),
          CHECK (((resolution_kind IN (
                    'map_existing_security','create_security','ticker_rename','ticker_reuse',
                    'share_class_conversion','reorganization'
                  ) AND target_security_id IS NOT NULL) OR
                  (resolution_kind IN ('not_a_security','unavailable') AND
                   target_security_id IS NULL AND
                   target_security_identifier_id IS NULL)) IS TRUE),
          CHECK ((
            resolution_document->>'contract_version'='v0.22.security_identity_resolution.v1' AND
            resolution_document->>'review_case_id'=
              security_identity_review_case_id::text AND
            (resolution_document->>'version_number')::integer=version_number AND
            resolution_document->>'resolution_status'=resolution_status AND
            resolution_document->>'resolution_kind'=resolution_kind AND
            (resolution_document->>'evidence_count')::integer=evidence_count AND
            CASE WHEN target_security_id IS NULL
              THEN resolution_document->'target_security_id'='null'::jsonb
              ELSE resolution_document->>'target_security_id'=target_security_id::text
            END AND
            CASE WHEN target_security_identifier_id IS NULL
              THEN resolution_document->'target_security_identifier_id'='null'::jsonb
              ELSE resolution_document->>'target_security_identifier_id'=
                target_security_identifier_id::text
            END AND
            CASE WHEN supersedes_resolution_id IS NULL
              THEN resolution_document->'supersedes_resolution_id'='null'::jsonb
              ELSE resolution_document->>'supersedes_resolution_id'=
                supersedes_resolution_id::text
            END
          ) IS TRUE)
        );

        CREATE TABLE catalog.v022_security_identity_resolution_evidence (
          security_identity_resolution_id uuid NOT NULL
            REFERENCES catalog.v022_security_identity_resolution,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          security_identity_evidence_id uuid NOT NULL
            REFERENCES catalog.v022_security_identity_evidence,
          evidence_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (security_identity_resolution_id,ordinal),
          UNIQUE (security_identity_resolution_id,security_identity_evidence_id)
        );

        CREATE FUNCTION catalog.validate_v022_security_identity_review_case()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; import_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT manifest.artifact_id,artifact.status INTO import_row
            FROM data.v022_external_import_manifest manifest
            JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
           WHERE manifest.external_import_manifest_id=NEW.external_import_manifest_id;
          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_security_identity_review_case' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_security_identity_review__' || NEW.case_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
             import_row.status IS DISTINCT FROM 'published' OR
             NEW.external_import_manifest_artifact_id IS DISTINCT FROM import_row.artifact_id OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>1 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=import_row.artifact_id
                 AND dependency.role='external_import_manifest' AND dependency.ordinal=0) THEN
            RAISE EXCEPTION 'Identity Review Case requires its exact import evidence';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION catalog.validate_v022_security_identity_review_case_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (SELECT status FROM lineage.artifact WHERE artifact_id=NEW.artifact_id)
               IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Identity Review Case Artifact must be published';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION catalog.validate_v022_security_identity_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; case_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT review.artifact_id,artifact.status INTO case_row
            FROM catalog.v022_security_identity_review_case review
            JOIN lineage.artifact artifact ON artifact.artifact_id=review.artifact_id
           WHERE review.security_identity_review_case_id=
             NEW.security_identity_review_case_id;
          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_security_identity_evidence' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_security_identity_evidence__' || NEW.evidence_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
             case_row.status IS DISTINCT FROM 'published' OR
             NEW.review_case_artifact_id IS DISTINCT FROM case_row.artifact_id OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>1 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=case_row.artifact_id
                 AND dependency.role='identity_review_case' AND dependency.ordinal=0) THEN
            RAISE EXCEPTION 'Identity Evidence requires its exact published Review Case';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION catalog.validate_v022_security_identity_evidence_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (SELECT status FROM lineage.artifact WHERE artifact_id=NEW.artifact_id)
               IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Identity Evidence Artifact must be published';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION catalog.validate_v022_security_identity_resolution()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; case_row record; identifier_security uuid;
                previous_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT review.artifact_id,artifact.status INTO case_row
            FROM catalog.v022_security_identity_review_case review
            JOIN lineage.artifact artifact ON artifact.artifact_id=review.artifact_id
           WHERE review.security_identity_review_case_id=
             NEW.security_identity_review_case_id;
          IF NEW.target_security_identifier_id IS NOT NULL THEN
            SELECT security_id INTO identifier_security
              FROM catalog.security_identifier
             WHERE security_identifier_id=NEW.target_security_identifier_id;
          END IF;
          SELECT security_identity_review_case_id,version_number INTO previous_row
            FROM catalog.v022_security_identity_resolution
           WHERE security_identity_resolution_id=NEW.supersedes_resolution_id;
          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_security_identity_resolution' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_security_identity_resolution__' ||
                 NEW.security_identity_review_case_id::text OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
             case_row.status IS DISTINCT FROM 'published' OR
             NEW.review_case_artifact_id IS DISTINCT FROM case_row.artifact_id OR
             (NEW.target_security_identifier_id IS NOT NULL AND
              identifier_security IS DISTINCT FROM NEW.target_security_id) OR
             (NEW.version_number=1 AND NEW.supersedes_resolution_id IS NOT NULL) OR
             (NEW.version_number>1 AND (
               NEW.supersedes_resolution_id IS NULL OR
               previous_row.security_identity_review_case_id IS DISTINCT FROM
                 NEW.security_identity_review_case_id OR
               previous_row.version_number IS DISTINCT FROM NEW.version_number-1
             )) OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>NEW.evidence_count+1 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=case_row.artifact_id
                 AND dependency.role='identity_review_case' AND dependency.ordinal=0) THEN
            RAISE EXCEPTION 'Identity Resolution requires exact Case, target and version';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION catalog.validate_v022_security_identity_resolution_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE resolution_case uuid; evidence_row record; resolution_artifact uuid;
        BEGIN
          SELECT security_identity_review_case_id,artifact_id
            INTO resolution_case,resolution_artifact
            FROM catalog.v022_security_identity_resolution
           WHERE security_identity_resolution_id=NEW.security_identity_resolution_id;
          SELECT evidence.security_identity_review_case_id,evidence.artifact_id,
                 artifact.status INTO evidence_row
            FROM catalog.v022_security_identity_evidence evidence
            JOIN lineage.artifact artifact ON artifact.artifact_id=evidence.artifact_id
           WHERE evidence.security_identity_evidence_id=
             NEW.security_identity_evidence_id;
          IF evidence_row.status IS DISTINCT FROM 'published' OR
             evidence_row.security_identity_review_case_id IS DISTINCT FROM
               resolution_case OR
             NEW.evidence_artifact_id IS DISTINCT FROM evidence_row.artifact_id OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=resolution_artifact
                 AND dependency.depends_on_artifact_id=evidence_row.artifact_id
                 AND dependency.role='identity_evidence'
                 AND dependency.ordinal=NEW.ordinal+1) THEN
            RAISE EXCEPTION 'Identity Resolution Evidence must belong to the exact Case';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION catalog.validate_v022_security_identity_resolution_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_count integer; min_ordinal integer; max_ordinal integer;
                actual_evidence_ids jsonb;
        BEGIN
          SELECT count(*),min(ordinal),max(ordinal),
                 coalesce(jsonb_agg(to_jsonb(security_identity_evidence_id::text)
                   ORDER BY ordinal),'[]'::jsonb)
            INTO actual_count,min_ordinal,max_ordinal,actual_evidence_ids
            FROM catalog.v022_security_identity_resolution_evidence
           WHERE security_identity_resolution_id=NEW.security_identity_resolution_id;
          IF (SELECT status FROM lineage.artifact WHERE artifact_id=NEW.artifact_id)
               IS DISTINCT FROM 'published' OR
             actual_count<>NEW.evidence_count OR min_ordinal<>0 OR
             max_ordinal<>NEW.evidence_count-1 OR
             NEW.resolution_document->'evidence_ids' IS DISTINCT FROM
               actual_evidence_ids THEN
            RAISE EXCEPTION 'Identity Resolution Evidence projection is incomplete';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION catalog.protect_identity_resolution_identifier()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM catalog.v022_security_identity_resolution resolution
             WHERE resolution.target_security_identifier_id=OLD.security_identifier_id
          ) THEN
            RAISE EXCEPTION 'Identity Resolution Security Identifiers are immutable';
          END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END $$;

        CREATE TRIGGER trg_v022_security_identity_review_case_validate
          BEFORE INSERT ON catalog.v022_security_identity_review_case
          FOR EACH ROW EXECUTE FUNCTION catalog.validate_v022_security_identity_review_case();
        CREATE CONSTRAINT TRIGGER trg_v022_security_identity_review_case_complete
          AFTER INSERT ON catalog.v022_security_identity_review_case
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION catalog.validate_v022_security_identity_review_case_complete();
        CREATE TRIGGER trg_v022_security_identity_review_case_append_only
          BEFORE UPDATE OR DELETE ON catalog.v022_security_identity_review_case
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();

        CREATE TRIGGER trg_v022_security_identity_evidence_validate
          BEFORE INSERT ON catalog.v022_security_identity_evidence
          FOR EACH ROW EXECUTE FUNCTION catalog.validate_v022_security_identity_evidence();
        CREATE CONSTRAINT TRIGGER trg_v022_security_identity_evidence_complete
          AFTER INSERT ON catalog.v022_security_identity_evidence
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION catalog.validate_v022_security_identity_evidence_complete();
        CREATE TRIGGER trg_v022_security_identity_evidence_append_only
          BEFORE UPDATE OR DELETE ON catalog.v022_security_identity_evidence
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();

        CREATE TRIGGER trg_v022_security_identity_resolution_validate
          BEFORE INSERT ON catalog.v022_security_identity_resolution
          FOR EACH ROW EXECUTE FUNCTION catalog.validate_v022_security_identity_resolution();
        CREATE TRIGGER trg_v022_security_identity_resolution_evidence_validate
          BEFORE INSERT ON catalog.v022_security_identity_resolution_evidence
          FOR EACH ROW EXECUTE FUNCTION catalog.validate_v022_security_identity_resolution_evidence();
        CREATE CONSTRAINT TRIGGER trg_v022_security_identity_resolution_complete
          AFTER INSERT ON catalog.v022_security_identity_resolution
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION catalog.validate_v022_security_identity_resolution_complete();
        CREATE TRIGGER trg_v022_security_identity_resolution_append_only
          BEFORE UPDATE OR DELETE ON catalog.v022_security_identity_resolution
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_security_identity_resolution_evidence_append_only
          BEFORE UPDATE OR DELETE ON catalog.v022_security_identity_resolution_evidence
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_security_identifier_resolution_immutable
          BEFORE UPDATE OR DELETE ON catalog.security_identifier
          FOR EACH ROW EXECUTE FUNCTION catalog.protect_identity_resolution_identifier();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM catalog.v022_security_identity_review_case) OR
             EXISTS (SELECT 1 FROM catalog.v022_security_identity_evidence) OR
             EXISTS (SELECT 1 FROM catalog.v022_security_identity_resolution) THEN
            RAISE EXCEPTION 'Cannot downgrade with v0.22 Security Identity Review evidence';
          END IF;
        END $$;

        DROP TRIGGER IF EXISTS trg_security_identifier_resolution_immutable
          ON catalog.security_identifier;
        DROP FUNCTION IF EXISTS catalog.protect_identity_resolution_identifier();
        DROP FUNCTION IF EXISTS catalog.validate_v022_security_identity_resolution_complete() CASCADE;
        DROP FUNCTION IF EXISTS catalog.validate_v022_security_identity_resolution_evidence() CASCADE;
        DROP FUNCTION IF EXISTS catalog.validate_v022_security_identity_resolution() CASCADE;
        DROP FUNCTION IF EXISTS catalog.validate_v022_security_identity_evidence_complete() CASCADE;
        DROP FUNCTION IF EXISTS catalog.validate_v022_security_identity_evidence() CASCADE;
        DROP FUNCTION IF EXISTS catalog.validate_v022_security_identity_review_case_complete() CASCADE;
        DROP FUNCTION IF EXISTS catalog.validate_v022_security_identity_review_case() CASCADE;
        DROP TABLE catalog.v022_security_identity_resolution_evidence;
        DROP TABLE catalog.v022_security_identity_resolution;
        DROP TABLE catalog.v022_security_identity_evidence;
        DROP TABLE catalog.v022_security_identity_review_case;
        """
    )
