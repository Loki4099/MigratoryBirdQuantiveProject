from __future__ import annotations

from alembic import op

revision = "20260817_107_v022_prod_disclose"
down_revision = "20260817_106_v022_cohort_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE product.v022_product_data_disclosure (
          product_data_disclosure_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          execution_version_id uuid NOT NULL UNIQUE
            REFERENCES product.v022_execution_version,
          qualification_version_id uuid NOT NULL UNIQUE
            REFERENCES product.v022_qualification_version,
          result_evidence_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_result_evidence_snapshot,
          evaluation_cohort_version_id uuid NOT NULL
            REFERENCES experiment.v022_evaluation_cohort_version,
          evaluation_cohort_runtime_contract_id uuid NOT NULL
            REFERENCES experiment.v022_evaluation_cohort_runtime_contract,
          dataset_gate_assessment_id uuid NOT NULL
            REFERENCES data.v022_dataset_gate_assessment,
          dataset_gate_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          dataset_gate_fingerprint varchar(64) NOT NULL
            CHECK (dataset_gate_fingerprint ~ '^[0-9a-f]{64}$'),
          ranking_eligibility varchar(32) NOT NULL CHECK (
            ranking_eligibility IN ('rankable_research','exploratory_only')
          ),
          product_eligibility varchar(32) NOT NULL CHECK (
            product_eligibility IN ('eligible','eligible_with_warnings')
          ),
          warning_codes jsonb NOT NULL CHECK (jsonb_typeof(warning_codes)='array'),
          disclosure_document jsonb NOT NULL
            CHECK (jsonb_typeof(disclosure_document)='object'),
          disclosure_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (disclosure_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK ((
            disclosure_document->>'contract_version'='v0.22.product_data_disclosure.v1' AND
            disclosure_document->>'execution_version_id'=execution_version_id::text AND
            disclosure_document->>'qualification_version_id'=qualification_version_id::text AND
            disclosure_document->>'result_evidence_snapshot_id'=
              result_evidence_snapshot_id::text AND
            disclosure_document->>'evaluation_cohort_version_id'=
              evaluation_cohort_version_id::text AND
            disclosure_document->>'evaluation_cohort_runtime_contract_id'=
              evaluation_cohort_runtime_contract_id::text AND
            disclosure_document->>'dataset_gate_assessment_id'=
              dataset_gate_assessment_id::text AND
            disclosure_document->>'dataset_gate_fingerprint'=dataset_gate_fingerprint AND
            disclosure_document->>'ranking_eligibility'=ranking_eligibility AND
            disclosure_document->>'product_eligibility'=product_eligibility AND
            disclosure_document->'warning_codes'=warning_codes
          ) IS TRUE)
        );

        CREATE FUNCTION product.validate_v022_product_data_disclosure()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE execution_row record; qualification_row record; evidence_row record;
                cohort_row record; runtime_row record; gate_row record; artifact_row record;
        BEGIN
          SELECT execution.*,artifact.status,artifact.artifact_id AS execution_artifact_id
            INTO execution_row FROM product.v022_execution_version execution
            JOIN lineage.artifact artifact ON artifact.artifact_id=execution.artifact_id
           WHERE execution.execution_version_id=NEW.execution_version_id;
          SELECT qualification.*,artifact.status,
                 artifact.artifact_id AS qualification_artifact_id
            INTO qualification_row FROM product.v022_qualification_version qualification
            JOIN lineage.artifact artifact ON artifact.artifact_id=qualification.artifact_id
           WHERE qualification.qualification_version_id=NEW.qualification_version_id;
          SELECT evidence.*,artifact.status,artifact.artifact_id AS evidence_artifact_id
            INTO evidence_row FROM experiment.v022_result_evidence_snapshot evidence
            JOIN lineage.artifact artifact ON artifact.artifact_id=evidence.artifact_id
           WHERE evidence.result_evidence_snapshot_id=NEW.result_evidence_snapshot_id;
          SELECT cohort.*,artifact.status INTO cohort_row
            FROM experiment.v022_evaluation_cohort_version cohort
            JOIN lineage.artifact artifact ON artifact.artifact_id=cohort.artifact_id
           WHERE cohort.evaluation_cohort_version_id=NEW.evaluation_cohort_version_id;
          SELECT runtime.*,artifact.status,
                 artifact.artifact_id AS runtime_artifact_id INTO runtime_row
            FROM experiment.v022_evaluation_cohort_runtime_contract runtime
            JOIN lineage.artifact artifact ON artifact.artifact_id=runtime.artifact_id
           WHERE runtime.evaluation_cohort_runtime_contract_id=
                 NEW.evaluation_cohort_runtime_contract_id;
          SELECT gate.*,artifact.status INTO gate_row
            FROM data.v022_dataset_gate_assessment gate
            JOIN lineage.artifact artifact ON artifact.artifact_id=gate.artifact_id
           WHERE gate.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id;
          SELECT artifact_type,artifact_key,version_number,status INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF execution_row.status IS DISTINCT FROM 'published' OR
             qualification_row.status IS DISTINCT FROM 'published' OR
             evidence_row.status IS DISTINCT FROM 'published' OR
             cohort_row.status IS DISTINCT FROM 'published' OR
             runtime_row.status IS DISTINCT FROM 'published' OR
             gate_row.status IS DISTINCT FROM 'published' OR
             execution_row.promotion_result_evidence_snapshot_id IS DISTINCT FROM
               NEW.result_evidence_snapshot_id OR
             qualification_row.execution_version_id IS DISTINCT FROM NEW.execution_version_id OR
             qualification_row.result_evidence_snapshot_id IS DISTINCT FROM
               NEW.result_evidence_snapshot_id OR
             evidence_row.evaluation_cohort_version_id IS DISTINCT FROM
               NEW.evaluation_cohort_version_id OR
             runtime_row.evaluation_cohort_version_id IS DISTINCT FROM
               NEW.evaluation_cohort_version_id OR
             runtime_row.dataset_gate_assessment_id IS DISTINCT FROM
               NEW.dataset_gate_assessment_id OR
             NEW.dataset_gate_artifact_id IS DISTINCT FROM gate_row.artifact_id OR
             NEW.dataset_gate_fingerprint IS DISTINCT FROM gate_row.assessment_fingerprint OR
             NEW.ranking_eligibility IS DISTINCT FROM gate_row.ranking_eligibility OR
             NEW.product_eligibility IS DISTINCT FROM gate_row.product_eligibility OR
             NEW.product_eligibility='ineligible' THEN
            RAISE EXCEPTION 'Product Data Disclosure exact input closure invalid';
          END IF;
          IF artifact_row.artifact_type IS DISTINCT FROM 'v022_product_data_disclosure' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_product_data_disclosure__' || NEW.execution_version_id::text OR
             artifact_row.version_number IS DISTINCT FROM execution_row.version_number OR
             artifact_row.status IS DISTINCT FROM 'draft' OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>5 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=execution_row.execution_artifact_id
                 AND dependency.role='execution_version' AND dependency.ordinal=0) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=
                   qualification_row.qualification_artifact_id
                 AND dependency.role='qualification' AND dependency.ordinal=1) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=evidence_row.evidence_artifact_id
                 AND dependency.role='result_evidence' AND dependency.ordinal=2) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=runtime_row.runtime_artifact_id
                 AND dependency.role='cohort_runtime' AND dependency.ordinal=3) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=gate_row.artifact_id
                 AND dependency.role='dataset_gate' AND dependency.ordinal=4) THEN
            RAISE EXCEPTION 'Product Data Disclosure Artifact identity invalid';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_product_data_disclosure_validate
          BEFORE INSERT ON product.v022_product_data_disclosure
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_product_data_disclosure();
        CREATE TRIGGER trg_v022_product_data_disclosure_append_only
          BEFORE UPDATE OR DELETE ON product.v022_product_data_disclosure
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM product.v022_product_data_disclosure) THEN
            RAISE EXCEPTION 'Cannot downgrade nonempty Product Data Disclosure state';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS product.validate_v022_product_data_disclosure() CASCADE;
        DROP TABLE product.v022_product_data_disclosure;
        """
    )
