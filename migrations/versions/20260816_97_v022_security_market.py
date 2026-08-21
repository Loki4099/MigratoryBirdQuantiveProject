"""Add v0.22 Security-level market quality and lifecycle evidence.

Revision ID: 20260816_97_v022_security_market
Revises: 20260816_96_v022_yahoo_ingestion
"""

from __future__ import annotations

from alembic import op

revision = "20260816_97_v022_security_market"
down_revision = "20260816_96_v022_yahoo_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE data.v022_security_market_quality_report (
          security_market_quality_report_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          yahoo_ingestion_plan_id uuid NOT NULL
            REFERENCES data.v022_yahoo_ingestion_plan,
          yahoo_ingestion_plan_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          calendar_version_id uuid NOT NULL REFERENCES catalog.calendar_version,
          calendar_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          report_key varchar(180) NOT NULL CHECK (btrim(report_key)<>''),
          version_number integer NOT NULL CHECK (version_number>=1),
          research_tier varchar(32) NOT NULL
            CHECK (research_tier IN ('rankable_research','exploratory_only')),
          error_count integer NOT NULL CHECK (error_count>=0),
          warning_count integer NOT NULL CHECK (warning_count>=0),
          unavailable_segment_count integer NOT NULL
            CHECK (unavailable_segment_count>=0),
          report_document jsonb NOT NULL CHECK (jsonb_typeof(report_document)='object'),
          report_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (report_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (report_key,version_number)
        );

        CREATE TABLE data.v022_security_market_dataset_binding (
          dataset_publication_id uuid PRIMARY KEY REFERENCES data.dataset_publication,
          dataset_artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          yahoo_ingestion_plan_id uuid NOT NULL
            REFERENCES data.v022_yahoo_ingestion_plan,
          yahoo_ingestion_plan_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          security_market_quality_report_id uuid NOT NULL
            REFERENCES data.v022_security_market_quality_report,
          quality_report_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          price_semantics varchar(180) NOT NULL
            CHECK (price_semantics=
              'historical_constituent_pit__frozen_retrospective_yahoo_prices'),
          historical_pit_claimed boolean NOT NULL CHECK (historical_pit_claimed=false),
          research_tier varchar(32) NOT NULL CHECK (research_tier='rankable_research'),
          binding_document jsonb NOT NULL CHECK (jsonb_typeof(binding_document)='object'),
          binding_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE catalog.v022_security_terminal_event_evidence_binding (
          security_terminal_event_id uuid PRIMARY KEY
            REFERENCES catalog.security_terminal_event,
          terminal_event_artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          source_evidence_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          evidence_document jsonb NOT NULL CHECK (jsonb_typeof(evidence_document)='object'),
          evidence_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (evidence_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE FUNCTION data.validate_v022_security_market_quality_report()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; plan_row record; calendar_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT plan.artifact_id,artifact.status INTO plan_row
            FROM data.v022_yahoo_ingestion_plan plan
            JOIN lineage.artifact artifact ON artifact.artifact_id=plan.artifact_id
           WHERE plan.yahoo_ingestion_plan_id=NEW.yahoo_ingestion_plan_id;
          SELECT version.artifact_id,artifact.status INTO calendar_row
            FROM catalog.calendar_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.calendar_version_id=NEW.calendar_version_id;
          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_security_market_quality_report' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_security_market_quality_report__' || NEW.report_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
             plan_row.status IS DISTINCT FROM 'published' OR
             plan_row.artifact_id IS DISTINCT FROM NEW.yahoo_ingestion_plan_artifact_id OR
             calendar_row.status IS DISTINCT FROM 'published' OR
             calendar_row.artifact_id IS DISTINCT FROM NEW.calendar_artifact_id OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>2 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.yahoo_ingestion_plan_artifact_id
                 AND dependency.role='yahoo_ingestion_plan' AND dependency.ordinal=0) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.calendar_artifact_id
                 AND dependency.role='calendar_version' AND dependency.ordinal=1) THEN
            RAISE EXCEPTION 'Security Market Quality Report identity is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_security_market_quality_report_validate
          BEFORE INSERT ON data.v022_security_market_quality_report
          FOR EACH ROW EXECUTE FUNCTION
            data.validate_v022_security_market_quality_report();

        CREATE FUNCTION data.validate_v022_security_market_dataset_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE dataset_row record; plan_row record; report_row record;
        BEGIN
          SELECT publication.artifact_id,publication.dataset_kind,
                 publication.value_kind,artifact.status
            INTO dataset_row FROM data.dataset_publication publication
            JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
           WHERE publication.dataset_publication_id=NEW.dataset_publication_id;
          SELECT plan.artifact_id,artifact.status INTO plan_row
            FROM data.v022_yahoo_ingestion_plan plan
            JOIN lineage.artifact artifact ON artifact.artifact_id=plan.artifact_id
           WHERE plan.yahoo_ingestion_plan_id=NEW.yahoo_ingestion_plan_id;
          SELECT report.artifact_id,report.error_count,report.research_tier,
                 artifact.status INTO report_row
            FROM data.v022_security_market_quality_report report
            JOIN lineage.artifact artifact ON artifact.artifact_id=report.artifact_id
           WHERE report.security_market_quality_report_id=
                 NEW.security_market_quality_report_id;
          IF dataset_row.status IS DISTINCT FROM 'draft' OR
             dataset_row.artifact_id IS DISTINCT FROM NEW.dataset_artifact_id OR
             dataset_row.dataset_kind IS DISTINCT FROM 'canonical' OR
             dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
             plan_row.artifact_id IS DISTINCT FROM NEW.yahoo_ingestion_plan_artifact_id OR
             plan_row.status IS DISTINCT FROM 'published' OR
             report_row.artifact_id IS DISTINCT FROM NEW.quality_report_artifact_id OR
             report_row.status IS DISTINCT FROM 'published' OR
             report_row.error_count IS DISTINCT FROM 0 OR
             report_row.research_tier IS DISTINCT FROM 'rankable_research' OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.dataset_artifact_id
                 AND dependency.depends_on_artifact_id=
                     NEW.yahoo_ingestion_plan_artifact_id
                 AND dependency.role='yahoo_ingestion_plan') OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.dataset_artifact_id
                 AND dependency.depends_on_artifact_id=NEW.quality_report_artifact_id
                 AND dependency.role='quality_report') THEN
            RAISE EXCEPTION 'Security Market Dataset binding is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_security_market_dataset_binding_validate
          BEFORE INSERT ON data.v022_security_market_dataset_binding
          FOR EACH ROW EXECUTE FUNCTION
            data.validate_v022_security_market_dataset_binding();

        CREATE FUNCTION catalog.validate_v022_terminal_event_evidence_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE event_row record; source_status varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.terminal_event_artifact_id);
          SELECT event.artifact_id INTO event_row
            FROM catalog.security_terminal_event event
           WHERE event.security_terminal_event_id=NEW.security_terminal_event_id;
          SELECT status INTO source_status FROM lineage.artifact
           WHERE artifact_id=NEW.source_evidence_artifact_id;
          IF event_row.artifact_id IS DISTINCT FROM NEW.terminal_event_artifact_id OR
             source_status IS DISTINCT FROM 'published' OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.terminal_event_artifact_id)<>1 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.terminal_event_artifact_id
                 AND dependency.depends_on_artifact_id=NEW.source_evidence_artifact_id
                 AND dependency.role='source_evidence' AND dependency.ordinal=0) THEN
            RAISE EXCEPTION 'Terminal Event requires exact source evidence';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_terminal_event_evidence_binding_validate
          BEFORE INSERT ON catalog.v022_security_terminal_event_evidence_binding
          FOR EACH ROW EXECUTE FUNCTION
            catalog.validate_v022_terminal_event_evidence_binding();

        CREATE FUNCTION catalog.protect_v022_bound_terminal_event()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM catalog.v022_security_terminal_event_evidence_binding binding
             WHERE binding.security_terminal_event_id=OLD.security_terminal_event_id
          ) THEN
            RAISE EXCEPTION 'Source-backed v0.22 Terminal Events are immutable';
          END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END $$;
        CREATE TRIGGER trg_v022_bound_terminal_event_immutable
          BEFORE UPDATE OR DELETE ON catalog.security_terminal_event
          FOR EACH ROW EXECUTE FUNCTION catalog.protect_v022_bound_terminal_event();

        CREATE TRIGGER trg_v022_security_market_quality_report_append_only
          BEFORE UPDATE OR DELETE ON data.v022_security_market_quality_report
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_security_market_dataset_binding_append_only
          BEFORE UPDATE OR DELETE ON data.v022_security_market_dataset_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_terminal_event_evidence_binding_append_only
          BEFORE UPDATE OR DELETE ON catalog.v022_security_terminal_event_evidence_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM data.v022_security_market_quality_report) OR
             EXISTS (SELECT 1 FROM data.v022_security_market_dataset_binding) OR
             EXISTS (SELECT 1 FROM catalog.v022_security_terminal_event_evidence_binding)
          THEN
            RAISE EXCEPTION 'Cannot downgrade with v0.22 Security market evidence';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS
          catalog.validate_v022_terminal_event_evidence_binding() CASCADE;
        DROP FUNCTION IF EXISTS catalog.protect_v022_bound_terminal_event() CASCADE;
        DROP FUNCTION IF EXISTS
          data.validate_v022_security_market_dataset_binding() CASCADE;
        DROP FUNCTION IF EXISTS
          data.validate_v022_security_market_quality_report() CASCADE;
        DROP TABLE catalog.v022_security_terminal_event_evidence_binding;
        DROP TABLE data.v022_security_market_dataset_binding;
        DROP TABLE data.v022_security_market_quality_report;
        """
    )
