"""Add immutable v0.22 Yahoo acquisition plans and append-only attempts.

Revision ID: 20260816_96_v022_yahoo_ingestion
Revises: 20260816_95_v022_sp500_universe
"""

from __future__ import annotations

from alembic import op

revision = "20260816_96_v022_yahoo_ingestion"
down_revision = "20260816_95_v022_sp500_universe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE data.v022_yahoo_ingestion_plan (
          yahoo_ingestion_plan_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          plan_key varchar(160) NOT NULL CHECK (btrim(plan_key)<>''),
          version_number integer NOT NULL CHECK (version_number>=1),
          universe_history_id uuid NOT NULL REFERENCES catalog.universe_history,
          universe_history_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          data_series_version_id uuid NOT NULL REFERENCES data.data_series_version,
          data_series_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          provider_key varchar(100) NOT NULL CHECK (provider_key='yahoo_yfinance'),
          coverage_start date NOT NULL,
          coverage_end date NOT NULL,
          segment_count integer NOT NULL CHECK (segment_count>=1),
          plan_document jsonb NOT NULL CHECK (jsonb_typeof(plan_document)='object'),
          plan_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (plan_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (plan_key,version_number),
          CHECK (coverage_start<=coverage_end)
        );

        CREATE TABLE data.v022_yahoo_ingestion_segment (
          yahoo_ingestion_segment_id uuid PRIMARY KEY,
          yahoo_ingestion_plan_id uuid NOT NULL
            REFERENCES data.v022_yahoo_ingestion_plan,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          security_id uuid NOT NULL REFERENCES catalog.security,
          security_identifier_id uuid NOT NULL REFERENCES catalog.security_identifier,
          provider_symbol varchar(160) NOT NULL CHECK (btrim(provider_symbol)<>''),
          coverage_start date NOT NULL,
          coverage_end date NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (yahoo_ingestion_plan_id,ordinal),
          UNIQUE (
            yahoo_ingestion_plan_id,security_id,security_identifier_id,
            coverage_start,coverage_end
          ),
          CHECK (coverage_start<=coverage_end)
        );

        CREATE TABLE data.v022_yahoo_ingestion_attempt (
          yahoo_ingestion_attempt_id uuid PRIMARY KEY,
          yahoo_ingestion_segment_id uuid NOT NULL
            REFERENCES data.v022_yahoo_ingestion_segment,
          attempt_ordinal integer NOT NULL CHECK (attempt_ordinal>=0),
          attempt_status varchar(24) NOT NULL
            CHECK (attempt_status IN ('fetched','failed','unavailable')),
          requested_at timestamptz NOT NULL,
          completed_at timestamptz NOT NULL,
          source_snapshot_id uuid NULL REFERENCES data.source_snapshot,
          source_snapshot_security_subject_id uuid NULL
            REFERENCES data.source_snapshot_security_subject,
          failure_reason varchar(1000) NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (yahoo_ingestion_segment_id,attempt_ordinal),
          CHECK (requested_at<=completed_at),
          CHECK (((attempt_status='fetched' AND source_snapshot_id IS NOT NULL AND
                   source_snapshot_security_subject_id IS NOT NULL AND
                   failure_reason IS NULL) OR
                  (attempt_status<>'fetched' AND source_snapshot_id IS NULL AND
                   source_snapshot_security_subject_id IS NULL AND
                   failure_reason IS NOT NULL AND btrim(failure_reason)<>'')) IS TRUE)
        );

        CREATE FUNCTION data.validate_v022_yahoo_ingestion_plan()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; history_row record; series_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number
            INTO artifact_row FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT history.artifact_id,artifact.status,binding.universe_history_id
            INTO history_row
            FROM catalog.universe_history history
            JOIN lineage.artifact artifact ON artifact.artifact_id=history.artifact_id
            JOIN catalog.v022_universe_history_ledger_binding binding
              ON binding.universe_history_id=history.universe_history_id
           WHERE history.universe_history_id=NEW.universe_history_id;
          SELECT version.artifact_id,artifact.status,definition.series_key,
                 provider.provider_key
            INTO series_row
            FROM data.data_series_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
            JOIN data.data_series_definition definition
              ON definition.data_series_definition_id=version.data_series_definition_id
            JOIN data.source_provider provider
              ON provider.source_provider_id=version.source_provider_id
           WHERE version.data_series_version_id=NEW.data_series_version_id;
          IF artifact_row.artifact_type IS DISTINCT FROM 'v022_yahoo_ingestion_plan' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_yahoo_ingestion_plan__' || NEW.plan_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
             history_row.status IS DISTINCT FROM 'published' OR
             history_row.artifact_id IS DISTINCT FROM NEW.universe_history_artifact_id OR
             series_row.status IS DISTINCT FROM 'published' OR
             series_row.artifact_id IS DISTINCT FROM NEW.data_series_artifact_id OR
             series_row.series_key IS DISTINCT FROM 'us_equity_daily_market_yahoo' OR
             series_row.provider_key IS DISTINCT FROM NEW.provider_key THEN
            RAISE EXCEPTION 'Yahoo Ingestion Plan requires exact published inputs';
          END IF;
          IF (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>2 OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=NEW.universe_history_artifact_id
                  AND dependency.role='universe_history' AND dependency.ordinal=0
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=NEW.data_series_artifact_id
                  AND dependency.role='data_series_version' AND dependency.ordinal=1
             ) THEN
            RAISE EXCEPTION 'Yahoo Ingestion Plan lineage is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_yahoo_ingestion_plan_validate
          BEFORE INSERT ON data.v022_yahoo_ingestion_plan
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_yahoo_ingestion_plan();

        CREATE FUNCTION data.validate_v022_yahoo_ingestion_segment()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE plan_row record; identifier_row record;
        BEGIN
          SELECT universe_history_id,coverage_start,coverage_end
            INTO plan_row FROM data.v022_yahoo_ingestion_plan
           WHERE yahoo_ingestion_plan_id=NEW.yahoo_ingestion_plan_id;
          SELECT security_id,provider_scope,identifier_type,identifier_value,
                 valid_from,valid_to
            INTO identifier_row FROM catalog.security_identifier
           WHERE security_identifier_id=NEW.security_identifier_id;
          IF identifier_row.security_id IS DISTINCT FROM NEW.security_id OR
             identifier_row.provider_scope IS DISTINCT FROM 'yahoo_yfinance' OR
             identifier_row.identifier_type IS DISTINCT FROM 'provider_symbol' OR
             identifier_row.identifier_value IS DISTINCT FROM NEW.provider_symbol OR
             NEW.coverage_start<plan_row.coverage_start OR
             NEW.coverage_end>plan_row.coverage_end OR
             (identifier_row.valid_from IS NOT NULL AND
              NEW.coverage_start<identifier_row.valid_from) OR
             (identifier_row.valid_to IS NOT NULL AND
              NEW.coverage_end>=identifier_row.valid_to) OR
             NOT EXISTS (
               SELECT 1
                 FROM catalog.universe_snapshot snapshot
                 JOIN catalog.universe_snapshot_member member
                   ON member.universe_snapshot_id=snapshot.universe_snapshot_id
                WHERE snapshot.universe_history_id=plan_row.universe_history_id
                  AND member.security_id=NEW.security_id
             ) THEN
            RAISE EXCEPTION 'Yahoo Ingestion Segment requires exact provider identity';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_yahoo_ingestion_segment_validate
          BEFORE INSERT ON data.v022_yahoo_ingestion_segment
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_yahoo_ingestion_segment();

        CREATE FUNCTION data.validate_v022_yahoo_ingestion_attempt()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE segment_row record; subject_row record; snapshot_row record;
                expected_ordinal integer;
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'v022-yahoo-attempt:' || NEW.yahoo_ingestion_segment_id::text,0
          ));
          IF EXISTS (
            SELECT 1 FROM data.v022_yahoo_ingestion_attempt prior
             WHERE prior.yahoo_ingestion_segment_id=NEW.yahoo_ingestion_segment_id
               AND prior.attempt_status IN ('fetched','unavailable')
          ) THEN
            RAISE EXCEPTION 'Completed Yahoo Ingestion Segment cannot be retried';
          END IF;
          SELECT coalesce(max(prior.attempt_ordinal),-1)+1 INTO expected_ordinal
            FROM data.v022_yahoo_ingestion_attempt prior
           WHERE prior.yahoo_ingestion_segment_id=NEW.yahoo_ingestion_segment_id;
          IF NEW.attempt_ordinal IS DISTINCT FROM expected_ordinal THEN
            RAISE EXCEPTION 'Yahoo Ingestion Attempt ordinals must be contiguous';
          END IF;
          SELECT security_id,security_identifier_id,provider_symbol,
                 coverage_start,coverage_end
            INTO segment_row FROM data.v022_yahoo_ingestion_segment
           WHERE yahoo_ingestion_segment_id=NEW.yahoo_ingestion_segment_id;
          IF NEW.attempt_status='fetched' THEN
            SELECT subject.source_snapshot_id,subject.security_id,
                   subject.security_identifier_id,subject.provider_symbol,
                   subject.fetch_status
              INTO subject_row
              FROM data.source_snapshot_security_subject subject
             WHERE subject.source_snapshot_security_subject_id=
                   NEW.source_snapshot_security_subject_id;
            SELECT snapshot.request_parameters,artifact.status
              INTO snapshot_row
              FROM data.source_snapshot snapshot
              JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id
             WHERE snapshot.source_snapshot_id=NEW.source_snapshot_id;
            IF subject_row.source_snapshot_id IS DISTINCT FROM NEW.source_snapshot_id OR
               subject_row.security_id IS DISTINCT FROM segment_row.security_id OR
               subject_row.security_identifier_id IS DISTINCT FROM
                 segment_row.security_identifier_id OR
               subject_row.provider_symbol IS DISTINCT FROM segment_row.provider_symbol OR
               subject_row.fetch_status IS DISTINCT FROM 'fetched' OR
               snapshot_row.status IS DISTINCT FROM 'published' OR
               snapshot_row.request_parameters->>'provider_ticker' IS DISTINCT FROM
                  segment_row.provider_symbol OR
               (snapshot_row.request_parameters->>'start')::date IS DISTINCT FROM
                  segment_row.coverage_start OR
               (snapshot_row.request_parameters->>'end')::date IS DISTINCT FROM
                  segment_row.coverage_end+1 THEN
              RAISE EXCEPTION 'Yahoo Ingestion Attempt requires exact published snapshot';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_yahoo_ingestion_attempt_validate
          BEFORE INSERT ON data.v022_yahoo_ingestion_attempt
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_yahoo_ingestion_attempt();

        CREATE FUNCTION data.validate_v022_yahoo_ingestion_plan_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_status_value varchar; actual_count integer;
                actual_document jsonb;
        BEGIN
          SELECT status INTO artifact_status_value FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          SELECT count(*),jsonb_build_object(
                   'contract_version','v0.22.yahoo_ingestion_plan.v1',
                   'plan_key',NEW.plan_key,
                   'version_number',NEW.version_number,
                   'universe_history_id',NEW.universe_history_id::text,
                   'universe_history_artifact_id',NEW.universe_history_artifact_id::text,
                   'data_series_version_id',NEW.data_series_version_id::text,
                   'data_series_artifact_id',NEW.data_series_artifact_id::text,
                   'provider_key',NEW.provider_key,
                   'coverage_start',to_char(NEW.coverage_start,'YYYY-MM-DD'),
                   'coverage_end',to_char(NEW.coverage_end,'YYYY-MM-DD'),
                   'segments',coalesce(jsonb_agg(jsonb_build_object(
                     'ordinal',segment.ordinal,
                     'security_id',segment.security_id::text,
                     'security_identifier_id',segment.security_identifier_id::text,
                     'provider_symbol',segment.provider_symbol,
                     'coverage_start',to_char(segment.coverage_start,'YYYY-MM-DD'),
                     'coverage_end',to_char(segment.coverage_end,'YYYY-MM-DD')
                   ) ORDER BY segment.ordinal),'[]'::jsonb)
                 )
            INTO actual_count,actual_document
            FROM data.v022_yahoo_ingestion_segment segment
           WHERE segment.yahoo_ingestion_plan_id=NEW.yahoo_ingestion_plan_id;
          IF artifact_status_value IS DISTINCT FROM 'published' OR
             actual_count<>NEW.segment_count OR
             actual_document IS DISTINCT FROM NEW.plan_document THEN
            RAISE EXCEPTION 'Yahoo Ingestion Plan projection is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_yahoo_ingestion_plan_complete
          AFTER INSERT ON data.v022_yahoo_ingestion_plan
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION data.validate_v022_yahoo_ingestion_plan_complete();

        CREATE TRIGGER trg_v022_yahoo_ingestion_plan_append_only
          BEFORE UPDATE OR DELETE ON data.v022_yahoo_ingestion_plan
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_yahoo_ingestion_segment_append_only
          BEFORE UPDATE OR DELETE ON data.v022_yahoo_ingestion_segment
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_yahoo_ingestion_attempt_append_only
          BEFORE UPDATE OR DELETE ON data.v022_yahoo_ingestion_attempt
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM data.v022_yahoo_ingestion_plan) OR
             EXISTS (SELECT 1 FROM data.v022_yahoo_ingestion_attempt) THEN
            RAISE EXCEPTION 'Cannot downgrade with v0.22 Yahoo ingestion records';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS data.validate_v022_yahoo_ingestion_plan_complete() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_v022_yahoo_ingestion_attempt() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_v022_yahoo_ingestion_segment() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_v022_yahoo_ingestion_plan() CASCADE;
        DROP TABLE data.v022_yahoo_ingestion_attempt;
        DROP TABLE data.v022_yahoo_ingestion_segment;
        DROP TABLE data.v022_yahoo_ingestion_plan;
        """
    )
