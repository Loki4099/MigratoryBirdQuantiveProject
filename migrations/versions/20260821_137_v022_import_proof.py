"""Allow a clean-green Dataset to use one external-import source proof.

Revision ID: 20260821_137_v022_import_proof
Revises: 20260821_136_v022_calc_ctx
"""

from __future__ import annotations

from alembic import op

revision = "20260821_137_v022_import_proof"
down_revision = "20260821_136_v022_calc_ctx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION processing.validate_v022_calculation_context()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE dataset_row record; calendar_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT publication.artifact_id,publication.calendar_version_id,
                 artifact.status
            INTO dataset_row
            FROM data.dataset_publication publication
            JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
           WHERE publication.dataset_publication_id=NEW.dataset_publication_id;
          SELECT version.artifact_id,artifact.status
            INTO calendar_row
            FROM catalog.calendar_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.calendar_version_id=NEW.calendar_version_id;
          IF dataset_row.artifact_id IS DISTINCT FROM NEW.dataset_artifact_id OR
             dataset_row.calendar_version_id IS DISTINCT FROM NEW.calendar_version_id OR
             dataset_row.status IS DISTINCT FROM 'published' OR
             calendar_row.artifact_id IS DISTINCT FROM NEW.calendar_artifact_id OR
             calendar_row.status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Calculation Context requires exact published Dataset and Calendar';
          END IF;
          IF EXISTS (
            SELECT 1
              FROM jsonb_array_elements_text(NEW.raw_feature_version_ids) item(value)
              LEFT JOIN processing.feature_version feature
                ON feature.feature_version_id=item.value::uuid
              LEFT JOIN lineage.artifact artifact ON artifact.artifact_id=feature.artifact_id
             WHERE feature.feature_version_id IS NULL OR artifact.status<>'published'
          ) THEN
            RAISE EXCEPTION 'Calculation Context Raw Features must be published';
          END IF;
          IF EXISTS (
            SELECT 1
              FROM jsonb_array_elements_text(NEW.source_snapshot_artifact_ids) item(value)
              LEFT JOIN data.source_snapshot snapshot
                ON snapshot.artifact_id=item.value::uuid
              LEFT JOIN data.dataset_input input
                ON input.source_snapshot_id=snapshot.source_snapshot_id
               AND input.dataset_publication_id=NEW.dataset_publication_id
              LEFT JOIN lineage.artifact snapshot_artifact
                ON snapshot_artifact.artifact_id=snapshot.artifact_id
              LEFT JOIN data.v022_external_import_manifest import_manifest
                ON import_manifest.artifact_id=item.value::uuid
              LEFT JOIN lineage.artifact import_artifact
                ON import_artifact.artifact_id=import_manifest.artifact_id
              LEFT JOIN lineage.artifact_dependency import_dependency
                ON import_dependency.artifact_id=NEW.dataset_artifact_id
               AND import_dependency.depends_on_artifact_id=import_manifest.artifact_id
               AND import_dependency.role='external_import_manifest'
             WHERE NOT (
               (snapshot.source_snapshot_id IS NOT NULL AND
                input.dataset_input_id IS NOT NULL AND
                snapshot_artifact.status='published') OR
               (import_manifest.external_import_manifest_id IS NOT NULL AND
                import_dependency.artifact_id IS NOT NULL AND
                import_artifact.status='published')
             )
          ) THEN
            RAISE EXCEPTION
              'Calculation Context source proofs must be published Dataset inputs';
          END IF;
          RETURN NEW;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        r"""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1
              FROM processing.v022_calculation_context context
              JOIN LATERAL jsonb_array_elements_text(
                context.source_snapshot_artifact_ids
              ) item(value) ON true
              JOIN data.v022_external_import_manifest import_manifest
                ON import_manifest.artifact_id=item.value::uuid
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade while Calculation Context uses import source proofs';
          END IF;
        END $$;

        CREATE OR REPLACE FUNCTION processing.validate_v022_calculation_context()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE dataset_row record; calendar_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT publication.artifact_id,publication.calendar_version_id,
                 artifact.status
            INTO dataset_row
            FROM data.dataset_publication publication
            JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
           WHERE publication.dataset_publication_id=NEW.dataset_publication_id;
          SELECT version.artifact_id,artifact.status
            INTO calendar_row
            FROM catalog.calendar_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.calendar_version_id=NEW.calendar_version_id;
          IF dataset_row.artifact_id IS DISTINCT FROM NEW.dataset_artifact_id OR
             dataset_row.calendar_version_id IS DISTINCT FROM NEW.calendar_version_id OR
             dataset_row.status IS DISTINCT FROM 'published' OR
             calendar_row.artifact_id IS DISTINCT FROM NEW.calendar_artifact_id OR
             calendar_row.status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Calculation Context requires exact published Dataset and Calendar';
          END IF;
          IF EXISTS (
            SELECT 1
              FROM jsonb_array_elements_text(NEW.raw_feature_version_ids) item(value)
              LEFT JOIN processing.feature_version feature
                ON feature.feature_version_id=item.value::uuid
              LEFT JOIN lineage.artifact artifact ON artifact.artifact_id=feature.artifact_id
             WHERE feature.feature_version_id IS NULL OR artifact.status<>'published'
          ) THEN
            RAISE EXCEPTION 'Calculation Context Raw Features must be published';
          END IF;
          IF EXISTS (
            SELECT 1
              FROM jsonb_array_elements_text(NEW.source_snapshot_artifact_ids) item(value)
              LEFT JOIN data.source_snapshot snapshot
                ON snapshot.artifact_id=item.value::uuid
              LEFT JOIN data.dataset_input input
                ON input.source_snapshot_id=snapshot.source_snapshot_id
               AND input.dataset_publication_id=NEW.dataset_publication_id
              LEFT JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id
             WHERE snapshot.source_snapshot_id IS NULL OR
                   input.dataset_input_id IS NULL OR artifact.status<>'published'
          ) THEN
            RAISE EXCEPTION 'Calculation Context snapshots must be published Dataset inputs';
          END IF;
          RETURN NEW;
        END $$;
        """
    )
