from __future__ import annotations

from alembic import op

revision = "20260817_106_v022_cohort_runtime"
down_revision = "20260817_105_v022_dataset_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_evaluation_cohort_runtime_contract (
          evaluation_cohort_runtime_contract_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          evaluation_cohort_version_id uuid NOT NULL UNIQUE
            REFERENCES experiment.v022_evaluation_cohort_version,
          dataset_gate_assessment_id uuid NOT NULL
            REFERENCES data.v022_dataset_gate_assessment,
          dataset_gate_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          dataset_gate_fingerprint varchar(64) NOT NULL
            CHECK (dataset_gate_fingerprint ~ '^[0-9a-f]{64}$'),
          ranking_eligibility varchar(32) NOT NULL CHECK (
            ranking_eligibility IN ('rankable_research','exploratory_only')
          ),
          product_eligibility varchar(32) NOT NULL CHECK (
            product_eligibility IN ('eligible','eligible_with_warnings','ineligible')
          ),
          mask_interval_count integer NOT NULL CHECK (mask_interval_count>0),
          lifecycle_event_count integer NOT NULL CHECK (lifecycle_event_count>=0),
          settlement_instruction_count integer NOT NULL
            CHECK (settlement_instruction_count>=0),
          runtime_document jsonb NOT NULL CHECK (jsonb_typeof(runtime_document)='object'),
          runtime_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (runtime_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK ((
            runtime_document->>'contract_version'=
              'v0.22.evaluation_cohort_runtime.v2' AND
            runtime_document->>'evaluation_cohort_version_id'=
              evaluation_cohort_version_id::text AND
            runtime_document->>'dataset_gate_assessment_id'=
              dataset_gate_assessment_id::text AND
            runtime_document->>'dataset_gate_fingerprint'=dataset_gate_fingerprint AND
            runtime_document->>'ranking_eligibility'=ranking_eligibility AND
            runtime_document->>'product_eligibility'=product_eligibility AND
            (runtime_document->>'mask_interval_count')::integer=mask_interval_count AND
            (runtime_document->>'lifecycle_event_count')::integer=lifecycle_event_count AND
            (runtime_document->>'settlement_instruction_count')::integer=
              settlement_instruction_count
          ) IS TRUE)
        );

        CREATE TABLE experiment.v022_cohort_runtime_mask_interval (
          cohort_runtime_mask_interval_id uuid PRIMARY KEY,
          evaluation_cohort_runtime_contract_id uuid NOT NULL
            REFERENCES experiment.v022_evaluation_cohort_runtime_contract,
          security_id uuid NOT NULL REFERENCES catalog.security,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          effective_start date NOT NULL,
          effective_end date NOT NULL,
          is_member boolean NOT NULL,
          is_warmup_ready boolean NOT NULL,
          is_selectable boolean NOT NULL,
          is_tradable boolean NOT NULL,
          valuation_state varchar(24) NOT NULL CHECK (
            valuation_state IN ('live','stale_confirmed','terminal','unavailable')
          ),
          reason_codes jsonb NOT NULL CHECK (jsonb_typeof(reason_codes)='array'),
          evidence_artifact_ids jsonb NOT NULL
            CHECK (jsonb_typeof(evidence_artifact_ids)='array'),
          interval_fingerprint varchar(64) NOT NULL
            CHECK (interval_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (evaluation_cohort_runtime_contract_id,security_id,ordinal),
          UNIQUE (evaluation_cohort_runtime_contract_id,security_id,effective_start),
          CHECK (effective_start<=effective_end),
          CHECK (NOT is_selectable OR (is_member AND is_warmup_ready AND is_tradable)),
          CHECK (NOT is_tradable OR valuation_state='live')
        );

        CREATE TABLE experiment.v022_cohort_settlement_instruction (
          cohort_settlement_instruction_id uuid PRIMARY KEY,
          evaluation_cohort_runtime_contract_id uuid NOT NULL
            REFERENCES experiment.v022_evaluation_cohort_runtime_contract,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          security_lifecycle_event_id uuid NOT NULL
            REFERENCES catalog.v022_security_lifecycle_event,
          lifecycle_event_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          security_id uuid NOT NULL REFERENCES catalog.security,
          event_type varchar(40) NOT NULL,
          event_status varchar(20) NOT NULL,
          effective_session date NOT NULL,
          settlement_session date NOT NULL,
          legs_document jsonb NOT NULL CHECK (jsonb_typeof(legs_document)='array'),
          instruction_fingerprint varchar(64) NOT NULL
            CHECK (instruction_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (evaluation_cohort_runtime_contract_id,ordinal),
          UNIQUE (evaluation_cohort_runtime_contract_id,security_lifecycle_event_id)
        );

        CREATE FUNCTION experiment.validate_v022_cohort_runtime_contract()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE cohort_row record; gate_row record; artifact_row record;
        BEGIN
          SELECT cohort.*,cohort_artifact.status AS cohort_status
            INTO cohort_row
            FROM experiment.v022_evaluation_cohort_version cohort
            JOIN lineage.artifact cohort_artifact
              ON cohort_artifact.artifact_id=cohort.artifact_id
           WHERE cohort.evaluation_cohort_version_id=NEW.evaluation_cohort_version_id;
          SELECT gate.*,gate_artifact.status AS gate_status INTO gate_row
            FROM data.v022_dataset_gate_assessment gate
            JOIN lineage.artifact gate_artifact ON gate_artifact.artifact_id=gate.artifact_id
           WHERE gate.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id;
          SELECT artifact_type,artifact_key,version_number,status INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF cohort_row IS NULL OR gate_row IS NULL OR
             cohort_row.cohort_status IS DISTINCT FROM 'published' OR
             gate_row.gate_status IS DISTINCT FROM 'published' OR
             NEW.dataset_gate_artifact_id IS DISTINCT FROM gate_row.artifact_id OR
             NEW.dataset_gate_fingerprint IS DISTINCT FROM gate_row.assessment_fingerprint OR
             NEW.ranking_eligibility IS DISTINCT FROM gate_row.ranking_eligibility OR
             NEW.product_eligibility IS DISTINCT FROM gate_row.product_eligibility OR
             cohort_row.research_tier IS DISTINCT FROM gate_row.ranking_eligibility OR
             cohort_row.dataset_publication_id IS DISTINCT FROM gate_row.dataset_publication_id OR
             cohort_row.universe_history_id IS DISTINCT FROM gate_row.universe_history_id OR
             cohort_row.security_market_quality_report_id IS DISTINCT FROM
               gate_row.security_market_quality_report_id OR
             cohort_row.calendar_version_id IS DISTINCT FROM gate_row.calendar_version_id OR
             gate_row.assessed_coverage_start>cohort_row.warmup_start OR
             gate_row.assessed_coverage_end<cohort_row.evaluation_end THEN
            RAISE EXCEPTION 'Evaluation Cohort runtime contract input drift';
          END IF;
          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_evaluation_cohort_runtime_contract' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_evaluation_cohort_runtime_contract__' || cohort_row.cohort_key OR
             artifact_row.version_number IS DISTINCT FROM cohort_row.version_number OR
             artifact_row.status IS DISTINCT FROM 'draft' OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<2 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=cohort_row.artifact_id
                 AND dependency.role='evaluation_cohort' AND dependency.ordinal=0) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=gate_row.artifact_id
                 AND dependency.role='dataset_gate_assessment' AND dependency.ordinal=1) THEN
            RAISE EXCEPTION 'Evaluation Cohort runtime Artifact closure invalid';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION experiment.validate_v022_cohort_runtime_mask()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE cohort_row record;
        BEGIN
          SELECT cohort.warmup_start,cohort.evaluation_end INTO cohort_row
            FROM experiment.v022_evaluation_cohort_runtime_contract contract
            JOIN experiment.v022_evaluation_cohort_version cohort
              ON cohort.evaluation_cohort_version_id=contract.evaluation_cohort_version_id
           WHERE contract.evaluation_cohort_runtime_contract_id=
                 NEW.evaluation_cohort_runtime_contract_id;
          IF cohort_row IS NULL OR NEW.effective_start<cohort_row.warmup_start OR
             NEW.effective_end>cohort_row.evaluation_end OR
             EXISTS (SELECT 1 FROM experiment.v022_cohort_runtime_mask_interval prior
               WHERE prior.evaluation_cohort_runtime_contract_id=
                     NEW.evaluation_cohort_runtime_contract_id
                 AND prior.security_id=NEW.security_id
                 AND daterange(prior.effective_start,prior.effective_end,'[]') &&
                     daterange(NEW.effective_start,NEW.effective_end,'[]')) THEN
            RAISE EXCEPTION 'Evaluation Cohort runtime mask interval invalid';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION experiment.validate_v022_cohort_settlement_instruction()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE event_row record; expected_legs jsonb;
        BEGIN
          SELECT event.*,artifact.status AS artifact_status INTO event_row
            FROM catalog.v022_security_lifecycle_event event
            JOIN lineage.artifact artifact ON artifact.artifact_id=event.artifact_id
           WHERE event.security_lifecycle_event_id=NEW.security_lifecycle_event_id;
          SELECT COALESCE(jsonb_agg(leg.leg_document ORDER BY leg.ordinal),'[]'::jsonb)
            INTO expected_legs FROM catalog.v022_security_settlement_leg leg
           WHERE leg.security_lifecycle_event_id=NEW.security_lifecycle_event_id;
          IF event_row IS NULL OR event_row.artifact_status IS DISTINCT FROM 'published' OR
             NEW.lifecycle_event_artifact_id IS DISTINCT FROM event_row.artifact_id OR
             NEW.security_id IS DISTINCT FROM event_row.security_id OR
             NEW.event_type IS DISTINCT FROM event_row.event_type OR
             NEW.event_status IS DISTINCT FROM event_row.event_status OR
             NEW.effective_session IS DISTINCT FROM event_row.effective_session OR
             NEW.settlement_session IS DISTINCT FROM event_row.settlement_session OR
             NEW.legs_document IS DISTINCT FROM expected_legs OR
             jsonb_array_length(NEW.legs_document)=0 THEN
            RAISE EXCEPTION 'Evaluation Cohort settlement instruction drift';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION experiment.validate_v022_cohort_runtime_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mask_count integer; instruction_count integer; artifact_status varchar;
        BEGIN
          SELECT count(*) INTO mask_count
            FROM experiment.v022_cohort_runtime_mask_interval
           WHERE evaluation_cohort_runtime_contract_id=
                 NEW.evaluation_cohort_runtime_contract_id;
          SELECT count(*) INTO instruction_count
            FROM experiment.v022_cohort_settlement_instruction
           WHERE evaluation_cohort_runtime_contract_id=
                 NEW.evaluation_cohort_runtime_contract_id;
          SELECT status INTO artifact_status FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          IF mask_count<>NEW.mask_interval_count OR
             instruction_count<>NEW.settlement_instruction_count OR
             artifact_status IS DISTINCT FROM 'published' OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>
               NEW.lifecycle_event_count+2 OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.role='lifecycle_event')<>NEW.lifecycle_event_count OR
             EXISTS (SELECT 1 FROM experiment.v022_cohort_settlement_instruction instruction
               WHERE instruction.evaluation_cohort_runtime_contract_id=
                     NEW.evaluation_cohort_runtime_contract_id
                 AND NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
                   WHERE dependency.artifact_id=NEW.artifact_id
                     AND dependency.depends_on_artifact_id=
                         instruction.lifecycle_event_artifact_id
                     AND dependency.role='lifecycle_event')) THEN
            RAISE EXCEPTION 'Evaluation Cohort runtime projection incomplete';
          END IF;
          RETURN NULL;
        END $$;

        CREATE TRIGGER trg_v022_cohort_runtime_contract_validate
          BEFORE INSERT ON experiment.v022_evaluation_cohort_runtime_contract
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_cohort_runtime_contract();
        CREATE TRIGGER trg_v022_cohort_runtime_mask_validate
          BEFORE INSERT ON experiment.v022_cohort_runtime_mask_interval
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_cohort_runtime_mask();
        CREATE TRIGGER trg_v022_cohort_settlement_instruction_validate
          BEFORE INSERT ON experiment.v022_cohort_settlement_instruction
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_cohort_settlement_instruction();
        CREATE CONSTRAINT TRIGGER trg_v022_cohort_runtime_complete
          AFTER INSERT ON experiment.v022_evaluation_cohort_runtime_contract
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_cohort_runtime_complete();
        CREATE TRIGGER trg_v022_cohort_runtime_contract_append_only
          BEFORE UPDATE OR DELETE ON experiment.v022_evaluation_cohort_runtime_contract
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_cohort_runtime_mask_append_only
          BEFORE UPDATE OR DELETE ON experiment.v022_cohort_runtime_mask_interval
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_cohort_settlement_instruction_append_only
          BEFORE UPDATE OR DELETE ON experiment.v022_cohort_settlement_instruction
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM experiment.v022_evaluation_cohort_runtime_contract) THEN
            RAISE EXCEPTION 'Cannot downgrade M106 with published Cohort runtime contracts';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS experiment.validate_v022_cohort_runtime_complete() CASCADE;
        DROP FUNCTION IF EXISTS experiment.validate_v022_cohort_settlement_instruction() CASCADE;
        DROP FUNCTION IF EXISTS experiment.validate_v022_cohort_runtime_mask() CASCADE;
        DROP FUNCTION IF EXISTS experiment.validate_v022_cohort_runtime_contract() CASCADE;
        DROP TABLE experiment.v022_cohort_settlement_instruction;
        DROP TABLE experiment.v022_cohort_runtime_mask_interval;
        DROP TABLE experiment.v022_evaluation_cohort_runtime_contract;
        """
    )
