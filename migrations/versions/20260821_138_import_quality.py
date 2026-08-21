"""Allow source-exact quality reports for imported green baselines.

Revision ID: 20260821_138_import_quality
Revises: 20260821_137_v022_import_proof
"""

from __future__ import annotations

from alembic import op

revision = "20260821_138_import_quality"
down_revision = "20260821_137_v022_import_proof"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE data.v022_security_market_quality_report
          ALTER COLUMN yahoo_ingestion_plan_id DROP NOT NULL,
          ALTER COLUMN yahoo_ingestion_plan_artifact_id DROP NOT NULL,
          ADD COLUMN source_dataset_publication_id uuid NULL
            REFERENCES data.dataset_publication,
          ADD COLUMN source_dataset_artifact_id uuid NULL REFERENCES lineage.artifact,
          ADD COLUMN external_import_manifest_id uuid NULL
            REFERENCES data.v022_external_import_manifest,
          ADD COLUMN external_import_manifest_artifact_id uuid NULL
            REFERENCES lineage.artifact;

        ALTER TABLE data.v022_security_market_quality_report
          ADD CONSTRAINT ck_v022_quality_report_source_identity CHECK (
            (
              yahoo_ingestion_plan_id IS NOT NULL AND
              yahoo_ingestion_plan_artifact_id IS NOT NULL AND
              source_dataset_publication_id IS NULL AND
              source_dataset_artifact_id IS NULL AND
              external_import_manifest_id IS NULL AND
              external_import_manifest_artifact_id IS NULL
            ) OR (
              yahoo_ingestion_plan_id IS NULL AND
              yahoo_ingestion_plan_artifact_id IS NULL AND
              source_dataset_publication_id IS NOT NULL AND
              source_dataset_artifact_id IS NOT NULL AND
              external_import_manifest_id IS NOT NULL AND
              external_import_manifest_artifact_id IS NOT NULL
            )
          );

        CREATE OR REPLACE FUNCTION data.validate_v022_security_market_quality_report()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; plan_row record; calendar_row record;
                dataset_row record; import_row record; dependency_count integer;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT version.artifact_id,artifact.status INTO calendar_row
            FROM catalog.calendar_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.calendar_version_id=NEW.calendar_version_id;
          SELECT count(*) INTO dependency_count
            FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id;

          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_security_market_quality_report' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_security_market_quality_report__' || NEW.report_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
             calendar_row.status IS DISTINCT FROM 'published' OR
             calendar_row.artifact_id IS DISTINCT FROM NEW.calendar_artifact_id THEN
            RAISE EXCEPTION 'Security Market Quality Report identity is incomplete';
          END IF;

          IF NEW.yahoo_ingestion_plan_id IS NOT NULL THEN
            SELECT plan.artifact_id,artifact.status INTO plan_row
              FROM data.v022_yahoo_ingestion_plan plan
              JOIN lineage.artifact artifact ON artifact.artifact_id=plan.artifact_id
             WHERE plan.yahoo_ingestion_plan_id=NEW.yahoo_ingestion_plan_id;
            IF plan_row.status IS DISTINCT FROM 'published' OR
               plan_row.artifact_id IS DISTINCT FROM NEW.yahoo_ingestion_plan_artifact_id OR
               dependency_count<>2 OR
               NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
                 WHERE dependency.artifact_id=NEW.artifact_id
                   AND dependency.depends_on_artifact_id=NEW.yahoo_ingestion_plan_artifact_id
                   AND dependency.role='yahoo_ingestion_plan' AND dependency.ordinal=0) OR
               NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
                 WHERE dependency.artifact_id=NEW.artifact_id
                   AND dependency.depends_on_artifact_id=NEW.calendar_artifact_id
                   AND dependency.role='calendar_version' AND dependency.ordinal=1) THEN
              RAISE EXCEPTION 'Yahoo Quality Report source identity is incomplete';
            END IF;
          ELSE
            SELECT publication.artifact_id,publication.dataset_kind,
                   publication.value_kind,artifact.status
              INTO dataset_row
              FROM data.dataset_publication publication
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=publication.artifact_id
             WHERE publication.dataset_publication_id=NEW.source_dataset_publication_id;
            SELECT manifest.artifact_id,artifact.status INTO import_row
              FROM data.v022_external_import_manifest manifest
              JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
             WHERE manifest.external_import_manifest_id=NEW.external_import_manifest_id;
            IF dataset_row.status IS DISTINCT FROM 'published' OR
               dataset_row.dataset_kind IS DISTINCT FROM 'canonical' OR
               dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
               dataset_row.artifact_id IS DISTINCT FROM NEW.source_dataset_artifact_id OR
               import_row.status IS DISTINCT FROM 'published' OR
               import_row.artifact_id IS DISTINCT FROM
                 NEW.external_import_manifest_artifact_id OR
               dependency_count<>3 OR
               NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
                 WHERE dependency.artifact_id=NEW.artifact_id
                   AND dependency.depends_on_artifact_id=NEW.source_dataset_artifact_id
                   AND dependency.role='market_dataset' AND dependency.ordinal=0) OR
               NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
                 WHERE dependency.artifact_id=NEW.artifact_id
                   AND dependency.depends_on_artifact_id=
                     NEW.external_import_manifest_artifact_id
                   AND dependency.role='external_import_manifest'
                   AND dependency.ordinal=1) OR
               NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
                 WHERE dependency.artifact_id=NEW.artifact_id
                   AND dependency.depends_on_artifact_id=NEW.calendar_artifact_id
                   AND dependency.role='calendar_version' AND dependency.ordinal=2) OR
               NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
                 WHERE dependency.artifact_id=NEW.source_dataset_artifact_id
                   AND dependency.depends_on_artifact_id=
                     NEW.external_import_manifest_artifact_id) THEN
              RAISE EXCEPTION 'Imported Quality Report source identity is incomplete';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM data.v022_security_market_quality_report
             WHERE source_dataset_publication_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'Cannot downgrade imported Security market quality reports';
          END IF;
        END $$;

        CREATE OR REPLACE FUNCTION data.validate_v022_security_market_quality_report()
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

        ALTER TABLE data.v022_security_market_quality_report
          DROP CONSTRAINT ck_v022_quality_report_source_identity,
          DROP COLUMN external_import_manifest_artifact_id,
          DROP COLUMN external_import_manifest_id,
          DROP COLUMN source_dataset_artifact_id,
          DROP COLUMN source_dataset_publication_id,
          ALTER COLUMN yahoo_ingestion_plan_id SET NOT NULL,
          ALTER COLUMN yahoo_ingestion_plan_artifact_id SET NOT NULL;
        """
    )
