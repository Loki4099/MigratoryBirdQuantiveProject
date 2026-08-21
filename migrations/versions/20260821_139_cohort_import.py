# ruff: noqa: E501
"""Bind Evaluation Cohorts to imported baseline quality evidence.

Revision ID: 20260821_139_cohort_import
Revises: 20260821_138_import_quality
"""

from __future__ import annotations

from alembic import op

revision = "20260821_139_cohort_import"
down_revision = "20260821_138_import_quality"
branch_labels = None
depends_on = None


_ADMISSION = r"""
CREATE OR REPLACE FUNCTION experiment.validate_v022_evaluation_cohort_admission_version()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE expected_price_semantics varchar(180);
BEGIN
  SELECT CASE
           WHEN reconciled.dataset_publication_id IS NOT NULL
             THEN reconciled.price_semantics
           WHEN report.source_dataset_publication_id=risk_dataset.dataset_publication_id
            AND report.source_dataset_artifact_id=risk_dataset.artifact_id
             THEN report.report_document->>'price_semantics'
           ELSE market_binding.price_semantics
         END
    INTO expected_price_semantics
    FROM data.dataset_publication risk_dataset
    JOIN data.v022_security_market_quality_report report
      ON report.security_market_quality_report_id=
           NEW.security_market_quality_report_id
     AND report.artifact_id=NEW.quality_report_artifact_id
    LEFT JOIN data.v022_reconciled_market_dataset_binding reconciled
      ON reconciled.dataset_publication_id=risk_dataset.dataset_publication_id
     AND reconciled.dataset_artifact_id=risk_dataset.artifact_id
    LEFT JOIN data.v022_security_market_dataset_binding market_binding
      ON market_binding.dataset_publication_id=COALESCE(
           reconciled.primary_dataset_publication_id,
           risk_dataset.dataset_publication_id)
     AND market_binding.security_market_quality_report_id=
           NEW.security_market_quality_report_id
     AND market_binding.quality_report_artifact_id=NEW.quality_report_artifact_id
   WHERE risk_dataset.dataset_publication_id=NEW.dataset_publication_id
     AND risk_dataset.artifact_id=NEW.dataset_artifact_id;
  IF expected_price_semantics IS NULL OR
     NEW.price_semantics IS DISTINCT FROM expected_price_semantics OR
     NEW.cohort_document->>'price_semantics' IS DISTINCT FROM
       NEW.price_semantics THEN
    RAISE EXCEPTION
      'Evaluation Cohort must inherit exact risk Dataset price semantics';
  END IF;
  RETURN NEW;
END $$;
"""


_LEGACY_ADMISSION = r"""
CREATE OR REPLACE FUNCTION experiment.validate_v022_evaluation_cohort_admission_version()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE expected_price_semantics varchar(180);
BEGIN
  SELECT CASE
           WHEN reconciled.dataset_publication_id IS NULL
             THEN market_binding.price_semantics
           ELSE reconciled.price_semantics
         END
    INTO expected_price_semantics
    FROM data.dataset_publication risk_dataset
    LEFT JOIN data.v022_reconciled_market_dataset_binding reconciled
      ON reconciled.dataset_publication_id=risk_dataset.dataset_publication_id
     AND reconciled.dataset_artifact_id=risk_dataset.artifact_id
    JOIN data.v022_security_market_dataset_binding market_binding
      ON market_binding.dataset_publication_id=COALESCE(
           reconciled.primary_dataset_publication_id,
           risk_dataset.dataset_publication_id)
     AND market_binding.security_market_quality_report_id=
           NEW.security_market_quality_report_id
     AND market_binding.quality_report_artifact_id=NEW.quality_report_artifact_id
   WHERE risk_dataset.dataset_publication_id=NEW.dataset_publication_id
     AND risk_dataset.artifact_id=NEW.dataset_artifact_id
     AND (
       reconciled.dataset_publication_id IS NOT NULL OR
       market_binding.dataset_artifact_id=NEW.dataset_artifact_id
     );
  IF expected_price_semantics IS NULL OR
     NEW.price_semantics IS DISTINCT FROM expected_price_semantics OR
     NEW.cohort_document->>'price_semantics' IS DISTINCT FROM
       NEW.price_semantics THEN
    RAISE EXCEPTION
      'Evaluation Cohort must inherit exact risk Dataset price semantics';
  END IF;
  RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION data.v022_imported_quality_report_binds_dataset(
          report_id uuid,report_artifact_id uuid,
          dataset_id uuid,dataset_artifact_id uuid
        ) RETURNS boolean LANGUAGE sql STABLE AS $$
          SELECT EXISTS (
            SELECT 1
              FROM data.v022_security_market_quality_report report
              JOIN lineage.artifact artifact ON artifact.artifact_id=report.artifact_id
             WHERE report.security_market_quality_report_id=report_id
               AND report.artifact_id=report_artifact_id
               AND report.source_dataset_publication_id=dataset_id
               AND report.source_dataset_artifact_id=dataset_artifact_id
               AND report.error_count=0
               AND artifact.status='published'
          )
        $$;

        DO $$ DECLARE definition text; needle text; replacement text;
        BEGIN
          definition := pg_get_functiondef(
            'experiment.validate_v022_evaluation_cohort_version()'::regprocedure
          );
          needle := E'          )\n        )\n         OR\n         (SELECT count(*)';
          replacement := E'          ) OR\n            data.v022_imported_quality_report_binds_dataset(\n              NEW.security_market_quality_report_id,\n              NEW.quality_report_artifact_id,\n              NEW.dataset_publication_id,NEW.dataset_artifact_id\n            )\n        )\n         OR\n         (SELECT count(*)';
          IF position(needle IN definition)=0 THEN
            RAISE EXCEPTION 'Current Evaluation Cohort validator shape is unknown';
          END IF;
          EXECUTE replace(definition,needle,replacement);
        END $$;
        """
    )
    op.execute(_ADMISSION)


def downgrade() -> None:
    op.execute(
        r"""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1
              FROM experiment.v022_evaluation_cohort_version cohort
              JOIN data.v022_security_market_quality_report report
                ON report.security_market_quality_report_id=
                     cohort.security_market_quality_report_id
             WHERE report.source_dataset_publication_id=cohort.dataset_publication_id
          ) THEN
            RAISE EXCEPTION 'Cannot downgrade imported Evaluation Cohorts';
          END IF;
        END $$;

        DO $$ DECLARE definition text; needle text; replacement text;
        BEGIN
          definition := pg_get_functiondef(
            'experiment.validate_v022_evaluation_cohort_version()'::regprocedure
          );
          needle := E'          ) OR\n            data.v022_imported_quality_report_binds_dataset(\n              NEW.security_market_quality_report_id,\n              NEW.quality_report_artifact_id,\n              NEW.dataset_publication_id,NEW.dataset_artifact_id\n            )\n        )\n         OR\n         (SELECT count(*)';
          replacement := E'          )\n        )\n         OR\n         (SELECT count(*)';
          IF position(needle IN definition)=0 THEN
            RAISE EXCEPTION 'Imported Evaluation Cohort validator shape is unknown';
          END IF;
          EXECUTE replace(definition,needle,replacement);
        END $$;
        """
    )
    op.execute(_LEGACY_ADMISSION)
    op.execute("DROP FUNCTION data.v022_imported_quality_report_binds_dataset(uuid,uuid,uuid,uuid)")
