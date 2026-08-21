# ruff: noqa: E501
"""Make Product Raw binding publication atomic and calendar-exact.

Revision ID: 20260817_114_v022_prod_raw_guard
Revises: 20260817_113_v022_prod_decision
"""

from __future__ import annotations

from alembic import op

revision = "20260817_114_v022_prod_raw_guard"
down_revision = "20260817_113_v022_prod_decision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(_atomic_raw_guard())


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM data.v022_product_input_payload_binding) THEN
            RAISE EXCEPTION 'Cannot restore the pre-M114 Product Raw coverage guard '
                            'with published Product input bindings';
          END IF;
        END $$;
        """
    )
    op.execute(_legacy_guard())


def _atomic_raw_guard() -> str:
    return """
    CREATE OR REPLACE FUNCTION data.validate_v022_product_input_payload_binding()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE input_row record; manifest_row record; feature_status varchar;
            expected_start date; expected_end date;
    BEGIN
      SELECT input.*,artifact.status INTO input_row
        FROM product.v022_product_input_snapshot input
        JOIN lineage.artifact artifact ON artifact.artifact_id=input.artifact_id
       WHERE input.product_input_snapshot_id=NEW.product_input_snapshot_id;
      SELECT min(session.session_date),max(session.session_date)
        INTO expected_start,expected_end
        FROM catalog.calendar_session session
       WHERE session.calendar_version_id=input_row.calendar_version_id
         AND session.session_date BETWEEN input_row.input_start AND input_row.input_end;
      SELECT manifest.*,artifact.status AS artifact_status
        INTO manifest_row FROM data.payload_manifest manifest
        JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
       WHERE manifest.payload_manifest_id=NEW.payload_manifest_id;
      SELECT artifact.status INTO feature_status
        FROM processing.feature_version feature
        JOIN lineage.artifact artifact ON artifact.artifact_id=feature.artifact_id
       WHERE feature.feature_version_id=NEW.feature_version_id;
      IF input_row.status IS DISTINCT FROM 'published' OR
         input_row.dataset_publication_id IS DISTINCT FROM NEW.dataset_publication_id OR
         expected_start IS NULL OR expected_end IS NULL OR
         NEW.coverage_start IS DISTINCT FROM expected_start OR
         NEW.coverage_end IS DISTINCT FROM expected_end OR
         manifest_row.artifact_status IS DISTINCT FROM 'draft' OR
         manifest_row.materialization_state IS DISTINCT FROM 'materialized' OR
         manifest_row.producer_artifact_id IS DISTINCT FROM input_row.dataset_artifact_id OR
         feature_status IS DISTINCT FROM 'published' OR
         (manifest_row.coverage_document->>'start')::date IS DISTINCT FROM
           NEW.coverage_start OR
         (manifest_row.coverage_document->>'end')::date IS DISTINCT FROM
           NEW.coverage_end OR
         NOT EXISTS (
           SELECT 1 FROM lineage.artifact_dependency dependency
            WHERE dependency.artifact_id=manifest_row.artifact_id
              AND dependency.depends_on_artifact_id=input_row.artifact_id
              AND dependency.role='product_input_snapshot'
         ) THEN
        RAISE EXCEPTION 'Product Input Payload binding does not close its exact Snapshot';
      END IF;
      RETURN NEW;
    END $$;
    """


def _legacy_guard() -> str:
    return """
    CREATE OR REPLACE FUNCTION data.validate_v022_product_input_payload_binding()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE input_row record; manifest_row record; feature_status varchar;
    BEGIN
      SELECT input.*,artifact.status INTO input_row
        FROM product.v022_product_input_snapshot input
        JOIN lineage.artifact artifact ON artifact.artifact_id=input.artifact_id
       WHERE input.product_input_snapshot_id=NEW.product_input_snapshot_id;
      SELECT manifest.*,artifact.status AS artifact_status
        INTO manifest_row FROM data.payload_manifest manifest
        JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
       WHERE manifest.payload_manifest_id=NEW.payload_manifest_id;
      SELECT artifact.status INTO feature_status
        FROM processing.feature_version feature
        JOIN lineage.artifact artifact ON artifact.artifact_id=feature.artifact_id
       WHERE feature.feature_version_id=NEW.feature_version_id;
      IF input_row.status IS DISTINCT FROM 'published' OR
         input_row.dataset_publication_id IS DISTINCT FROM NEW.dataset_publication_id OR
         manifest_row.artifact_status IS DISTINCT FROM 'published' OR
         manifest_row.materialization_state IS DISTINCT FROM 'materialized' OR
         manifest_row.producer_artifact_id IS DISTINCT FROM input_row.dataset_artifact_id OR
         feature_status IS DISTINCT FROM 'published' OR
         NEW.coverage_start>input_row.input_start OR
         NEW.coverage_end<input_row.input_end OR
         (manifest_row.coverage_document->>'start')::date IS DISTINCT FROM
           NEW.coverage_start OR
         (manifest_row.coverage_document->>'end')::date IS DISTINCT FROM
           NEW.coverage_end OR
         NOT EXISTS (
           SELECT 1 FROM lineage.artifact_dependency dependency
            WHERE dependency.artifact_id=manifest_row.artifact_id
              AND dependency.depends_on_artifact_id=input_row.artifact_id
              AND dependency.role='product_input_snapshot'
         ) THEN
        RAISE EXCEPTION 'Product Input Payload binding does not close its exact Snapshot';
      END IF;
      RETURN NEW;
    END $$;
    """
