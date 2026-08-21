# ruff: noqa: E501
"""Add immutable v0.22 Evaluation Cohorts and asset-date eligibility.

Revision ID: 20260816_98_v022_eval_cohort
Revises: 20260816_97_v022_security_market
"""

from __future__ import annotations

from alembic import op

revision = "20260816_98_v022_eval_cohort"
down_revision = "20260816_97_v022_security_market"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_evaluation_cohort_version (
          evaluation_cohort_version_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          cohort_key varchar(200) NOT NULL CHECK (btrim(cohort_key)<>''),
          version_number integer NOT NULL CHECK (version_number>=1),
          research_tier varchar(32) NOT NULL
            CHECK (research_tier IN ('rankable_research','exploratory_only')),
          frequency varchar(16) NOT NULL CHECK (frequency IN ('weekly','monthly')),
          universe_history_id uuid NOT NULL REFERENCES catalog.universe_history,
          universe_history_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          dataset_publication_id uuid NOT NULL REFERENCES data.dataset_publication,
          dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          security_market_quality_report_id uuid NOT NULL
            REFERENCES data.v022_security_market_quality_report,
          quality_report_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          calendar_version_id uuid NOT NULL REFERENCES catalog.calendar_version,
          calendar_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          warmup_start date NOT NULL,
          evaluation_start date NOT NULL,
          evaluation_end date NOT NULL,
          required_history_sessions integer NOT NULL
            CHECK (required_history_sessions=504),
          cost_bps_per_side numeric(12,6) NOT NULL CHECK (cost_bps_per_side>=0),
          execution_delay_sessions integer NOT NULL CHECK (execution_delay_sessions=1),
          benchmark_key varchar(80) NOT NULL CHECK (benchmark_key='spy'),
          price_semantics varchar(180) NOT NULL CHECK (price_semantics=
            'historical_constituent_pit__frozen_retrospective_yahoo_prices'),
          historical_pit_claimed boolean NOT NULL CHECK (historical_pit_claimed=false),
          session_count integer NOT NULL CHECK (session_count>504),
          decision_session_count integer NOT NULL CHECK (decision_session_count>0),
          eligibility_interval_count integer NOT NULL CHECK (eligibility_interval_count>0),
          cohort_document jsonb NOT NULL CHECK (jsonb_typeof(cohort_document)='object'),
          cohort_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (cohort_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (cohort_key,version_number),
          UNIQUE (evaluation_cohort_version_id,frequency),
          CHECK (warmup_start<evaluation_start AND evaluation_start<=evaluation_end)
        );

        CREATE TABLE experiment.v022_evaluation_cohort_session (
          evaluation_cohort_version_id uuid NOT NULL
            REFERENCES experiment.v022_evaluation_cohort_version,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          session_date date NOT NULL,
          session_role varchar(16) NOT NULL
            CHECK (session_role IN ('warmup','evaluation')),
          is_decision_session boolean NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (evaluation_cohort_version_id,ordinal),
          UNIQUE (evaluation_cohort_version_id,session_date),
          CHECK (session_role='evaluation' OR is_decision_session=false)
        );

        CREATE TABLE experiment.v022_cohort_eligibility_interval (
          cohort_eligibility_interval_id uuid PRIMARY KEY,
          evaluation_cohort_version_id uuid NOT NULL
            REFERENCES experiment.v022_evaluation_cohort_version,
          security_id uuid NOT NULL REFERENCES catalog.security,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          effective_start date NOT NULL,
          effective_end date NOT NULL,
          is_member boolean NOT NULL,
          is_warmup_ready boolean NOT NULL,
          is_selectable boolean NOT NULL,
          is_tradable boolean NOT NULL,
          valuation_state varchar(24) NOT NULL
            CHECK (valuation_state IN
              ('live','stale_confirmed','terminal','unavailable')),
          reason_codes jsonb NOT NULL CHECK (jsonb_typeof(reason_codes)='array'),
          evidence_artifact_ids jsonb NOT NULL
            CHECK (jsonb_typeof(evidence_artifact_ids)='array'),
          interval_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (interval_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (evaluation_cohort_version_id,security_id,ordinal),
          CHECK (effective_start<=effective_end),
          CHECK (is_selectable=false OR
            (is_member AND is_warmup_ready AND is_tradable AND valuation_state='live')),
          CHECK (is_tradable=false OR valuation_state='live')
        );

        CREATE TABLE experiment.v022_research_suite_evaluation_cohort_binding (
          research_suite_id uuid PRIMARY KEY REFERENCES experiment.v022_research_suite,
          evaluation_cohort_version_id uuid NOT NULL,
          frequency varchar(16) NOT NULL CHECK (frequency IN ('weekly','monthly')),
          binding_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$'),
          bound_by varchar(160) NOT NULL CHECK (btrim(bound_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (evaluation_cohort_version_id,frequency)
            REFERENCES experiment.v022_evaluation_cohort_version
              (evaluation_cohort_version_id,frequency)
        );

        CREATE FUNCTION experiment.validate_v022_evaluation_cohort_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; history_row record; dataset_row record;
                report_row record; calendar_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT history.artifact_id,artifact.status,ledger.research_tier,
                 (SELECT count(*)
                    FROM catalog.v022_universe_change_batch batch
                   WHERE batch.universe_membership_ledger_id=
                         ledger.universe_membership_ledger_id
                     AND batch.evidence_status<>'confirmed') AS unresolved_count
            INTO history_row
            FROM catalog.universe_history history
            JOIN lineage.artifact artifact ON artifact.artifact_id=history.artifact_id
            JOIN catalog.v022_universe_history_ledger_binding history_binding
              ON history_binding.universe_history_id=history.universe_history_id
            JOIN catalog.v022_universe_membership_ledger ledger
              ON ledger.universe_membership_ledger_id=
                 history_binding.universe_membership_ledger_id
           WHERE history.universe_history_id=NEW.universe_history_id;
          SELECT publication.artifact_id,publication.calendar_version_id,
                 publication.coverage_start,publication.coverage_end,artifact.status
            INTO dataset_row FROM data.dataset_publication publication
            JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
           WHERE publication.dataset_publication_id=NEW.dataset_publication_id;
          SELECT report.artifact_id,report.error_count,report.research_tier,artifact.status
            INTO report_row FROM data.v022_security_market_quality_report report
            JOIN lineage.artifact artifact ON artifact.artifact_id=report.artifact_id
           WHERE report.security_market_quality_report_id=
                 NEW.security_market_quality_report_id;
          SELECT version.artifact_id,artifact.status INTO calendar_row
            FROM catalog.calendar_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.calendar_version_id=NEW.calendar_version_id;
          IF artifact_row.artifact_type IS DISTINCT FROM 'v022_evaluation_cohort_version' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_evaluation_cohort_version__' || NEW.cohort_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
             history_row.artifact_id IS DISTINCT FROM NEW.universe_history_artifact_id OR
             history_row.status IS DISTINCT FROM 'published' OR
             (NEW.research_tier='rankable_research' AND
               (history_row.research_tier IS DISTINCT FROM 'rankable_research' OR
                history_row.unresolved_count<>0)) OR
             dataset_row.artifact_id IS DISTINCT FROM NEW.dataset_artifact_id OR
             dataset_row.status IS DISTINCT FROM 'published' OR
             dataset_row.calendar_version_id IS DISTINCT FROM NEW.calendar_version_id OR
             dataset_row.coverage_start>NEW.warmup_start OR
             dataset_row.coverage_end<NEW.evaluation_end OR
             report_row.artifact_id IS DISTINCT FROM NEW.quality_report_artifact_id OR
             report_row.status IS DISTINCT FROM 'published' OR
             report_row.error_count IS DISTINCT FROM 0 OR
             (NEW.research_tier='rankable_research' AND
               report_row.research_tier IS DISTINCT FROM 'rankable_research') OR
             calendar_row.artifact_id IS DISTINCT FROM NEW.calendar_artifact_id OR
             calendar_row.status IS DISTINCT FROM 'published' OR
             NOT EXISTS (SELECT 1 FROM data.v022_security_market_dataset_binding binding
               WHERE binding.dataset_publication_id=NEW.dataset_publication_id
                 AND binding.dataset_artifact_id=NEW.dataset_artifact_id
                 AND binding.security_market_quality_report_id=
                     NEW.security_market_quality_report_id
                 AND binding.quality_report_artifact_id=NEW.quality_report_artifact_id) OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>4 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.universe_history_artifact_id
                 AND dependency.role='universe_history' AND dependency.ordinal=0) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.dataset_artifact_id
                 AND dependency.role='market_dataset' AND dependency.ordinal=1) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.quality_report_artifact_id
                 AND dependency.role='quality_report' AND dependency.ordinal=2) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.calendar_artifact_id
                 AND dependency.role='calendar_version' AND dependency.ordinal=3) THEN
            RAISE EXCEPTION 'Evaluation Cohort requires exact published frozen inputs';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_evaluation_cohort_version_validate
          BEFORE INSERT ON experiment.v022_evaluation_cohort_version
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_evaluation_cohort_version();

        CREATE FUNCTION experiment.validate_v022_evaluation_cohort_child()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE owner_artifact_id uuid; owner_start date; owner_eval date; owner_end date;
                overlap_exists boolean;
        BEGIN
          SELECT artifact_id,warmup_start,evaluation_start,evaluation_end
            INTO owner_artifact_id,owner_start,owner_eval,owner_end
            FROM experiment.v022_evaluation_cohort_version
           WHERE evaluation_cohort_version_id=NEW.evaluation_cohort_version_id;
          PERFORM data.assert_artifact_draft(owner_artifact_id);
          IF TG_TABLE_NAME='v022_evaluation_cohort_session' THEN
            IF NEW.session_date<owner_start OR NEW.session_date>owner_end OR
               (NEW.session_role='warmup') IS DISTINCT FROM
                 (NEW.session_date<owner_eval) THEN
              RAISE EXCEPTION 'Evaluation Cohort session is outside its frozen range';
            END IF;
          ELSE
            IF NEW.effective_start<owner_start OR NEW.effective_end>owner_end THEN
              RAISE EXCEPTION 'Eligibility interval is outside its frozen Cohort';
            END IF;
            SELECT EXISTS (
              SELECT 1 FROM experiment.v022_cohort_eligibility_interval existing
               WHERE existing.evaluation_cohort_version_id=
                     NEW.evaluation_cohort_version_id
                 AND existing.security_id=NEW.security_id
                 AND daterange(existing.effective_start,
                               existing.effective_end,'[]') &&
                     daterange(NEW.effective_start,NEW.effective_end,'[]')
            ) INTO overlap_exists;
            IF overlap_exists THEN
              RAISE EXCEPTION 'Eligibility intervals cannot overlap';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_evaluation_cohort_session_validate
          BEFORE INSERT ON experiment.v022_evaluation_cohort_session
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_evaluation_cohort_child();
        CREATE TRIGGER trg_v022_cohort_eligibility_interval_validate
          BEFORE INSERT ON experiment.v022_cohort_eligibility_interval
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_evaluation_cohort_child();

        CREATE FUNCTION experiment.validate_v022_evaluation_cohort_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_status_value varchar; actual_sessions integer;
                actual_decisions integer; actual_warmup integer; actual_intervals integer;
                min_session date; max_session date; min_ordinal integer; max_ordinal integer;
        BEGIN
          SELECT status INTO artifact_status_value FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          SELECT count(*),count(*) FILTER (WHERE is_decision_session),
                 count(*) FILTER (WHERE session_role='warmup'),
                 min(session_date),max(session_date),min(ordinal),max(ordinal)
            INTO actual_sessions,actual_decisions,actual_warmup,
                 min_session,max_session,min_ordinal,max_ordinal
            FROM experiment.v022_evaluation_cohort_session
           WHERE evaluation_cohort_version_id=NEW.evaluation_cohort_version_id;
          SELECT count(*) INTO actual_intervals
            FROM experiment.v022_cohort_eligibility_interval
           WHERE evaluation_cohort_version_id=NEW.evaluation_cohort_version_id;
          IF artifact_status_value IS DISTINCT FROM 'published' OR
             actual_sessions<>NEW.session_count OR actual_decisions<>NEW.decision_session_count OR
             actual_warmup<>NEW.required_history_sessions OR
             actual_intervals<>NEW.eligibility_interval_count OR
             min_session IS DISTINCT FROM NEW.warmup_start OR
             max_session IS DISTINCT FROM NEW.evaluation_end OR
             min_ordinal<>0 OR max_ordinal<>NEW.session_count-1 OR
             NOT EXISTS (SELECT 1 FROM experiment.v022_evaluation_cohort_session session
               WHERE session.evaluation_cohort_version_id=NEW.evaluation_cohort_version_id
                 AND session.session_date=NEW.evaluation_start
                 AND session.session_role='evaluation') THEN
            RAISE EXCEPTION 'Evaluation Cohort projection is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_evaluation_cohort_complete
          AFTER INSERT ON experiment.v022_evaluation_cohort_version
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_evaluation_cohort_complete();

        CREATE FUNCTION experiment.validate_v022_suite_cohort_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE suite_row record; cohort_row record;
        BEGIN
          SELECT suite.compiled_research_graph_id,suite.suite_mode,artifact.status,
                 graph.frequency INTO suite_row
            FROM experiment.v022_research_suite suite
            JOIN lineage.artifact artifact ON artifact.artifact_id=suite.artifact_id
            JOIN workspace.compiled_research_graph graph
              ON graph.compiled_research_graph_id=suite.compiled_research_graph_id
           WHERE suite.research_suite_id=NEW.research_suite_id;
          SELECT cohort.frequency,cohort.research_tier,artifact.status INTO cohort_row
            FROM experiment.v022_evaluation_cohort_version cohort
            JOIN lineage.artifact artifact ON artifact.artifact_id=cohort.artifact_id
           WHERE cohort.evaluation_cohort_version_id=NEW.evaluation_cohort_version_id;
          IF suite_row.status IS DISTINCT FROM 'published' OR
             cohort_row.status IS DISTINCT FROM 'published' OR
             NEW.frequency IS DISTINCT FROM suite_row.frequency OR
             NEW.frequency IS DISTINCT FROM cohort_row.frequency THEN
            RAISE EXCEPTION 'Research Suite and Evaluation Cohort are incompatible';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_suite_cohort_binding_validate
          BEFORE INSERT ON experiment.v022_research_suite_evaluation_cohort_binding
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_suite_cohort_binding();

        CREATE TRIGGER trg_v022_evaluation_cohort_version_append_only
          BEFORE UPDATE OR DELETE ON experiment.v022_evaluation_cohort_version
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_evaluation_cohort_session_append_only
          BEFORE UPDATE OR DELETE ON experiment.v022_evaluation_cohort_session
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_cohort_eligibility_interval_append_only
          BEFORE UPDATE OR DELETE ON experiment.v022_cohort_eligibility_interval
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_suite_cohort_binding_append_only
          BEFORE UPDATE OR DELETE ON experiment.v022_research_suite_evaluation_cohort_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM experiment.v022_evaluation_cohort_version) OR
             EXISTS (SELECT 1 FROM experiment.v022_research_suite_evaluation_cohort_binding)
          THEN RAISE EXCEPTION 'Cannot downgrade with v0.22 Evaluation Cohorts';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS experiment.validate_v022_suite_cohort_binding() CASCADE;
        DROP FUNCTION IF EXISTS experiment.validate_v022_evaluation_cohort_complete() CASCADE;
        DROP FUNCTION IF EXISTS experiment.validate_v022_evaluation_cohort_child() CASCADE;
        DROP FUNCTION IF EXISTS experiment.validate_v022_evaluation_cohort_version() CASCADE;
        DROP TABLE experiment.v022_research_suite_evaluation_cohort_binding;
        DROP TABLE experiment.v022_cohort_eligibility_interval;
        DROP TABLE experiment.v022_evaluation_cohort_session;
        DROP TABLE experiment.v022_evaluation_cohort_version;
        """
    )
