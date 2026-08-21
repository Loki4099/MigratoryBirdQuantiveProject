# ruff: noqa: E501
"""Separate intrinsic trainable fingerprints from Artifact semantic fingerprints.

Revision ID: 20260818_123_v022_train_artifact
Revises: 20260818_122_v022_train_core
"""

from __future__ import annotations

from alembic import op

revision = "20260818_123_v022_train_artifact"
down_revision = "20260818_122_v022_train_core"
branch_labels = None
depends_on = None


_IDENTITY_TABLES = (
    "v022_feature_schema_version",
    "v022_training_matrix",
    "v022_fold_policy_version",
    "v022_training_fold",
    "v022_base_learner_spec",
    "v022_fitted_model_state",
    "v022_oof_prediction",
)


def upgrade() -> None:
    # No public trainable runtime is enabled at M122.  Refuse to invent the
    # missing Artifact semantic identity if a deployment has nevertheless
    # written rows through a private path.
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM aggregation.v022_feature_schema_version) OR
             EXISTS (SELECT 1 FROM aggregation.v022_training_matrix) OR
             EXISTS (SELECT 1 FROM aggregation.v022_fold_policy_version) OR
             EXISTS (SELECT 1 FROM aggregation.v022_training_fold) OR
             EXISTS (SELECT 1 FROM aggregation.v022_base_learner_spec) OR
             EXISTS (SELECT 1 FROM aggregation.v022_fitted_model_state) OR
             EXISTS (SELECT 1 FROM aggregation.v022_oof_prediction) THEN
            RAISE EXCEPTION
              'M123 requires empty unpublished trainable Aggregation identities';
          END IF;
        END $$;
        """
    )
    for table in _IDENTITY_TABLES:
        op.execute(
            f"""
            ALTER TABLE aggregation.{table}
              ADD COLUMN artifact_semantic_fingerprint varchar(64) NOT NULL UNIQUE
              CHECK (artifact_semantic_fingerprint ~ '^[0-9a-f]{{64}}$')
            """
        )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aggregation.guard_v022_trainable_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE row_document jsonb := to_jsonb(NEW);
        DECLARE identity_artifact lineage.artifact%ROWTYPE;
        DECLARE expected_type text;
        DECLARE fingerprint_value text;
        DECLARE manifest_value uuid;
        DECLARE manifest_state text;
        DECLARE manifest_artifact_status text;
        DECLARE manifest_producer_artifact_id uuid;
        DECLARE manifest_artifact_semantic_fingerprint text;
        BEGIN
          expected_type := CASE TG_TABLE_NAME
            WHEN 'v022_feature_schema_version' THEN 'v022_feature_schema_version'
            WHEN 'v022_training_matrix' THEN 'v022_training_matrix'
            WHEN 'v022_fold_policy_version' THEN 'v022_fold_policy_version'
            WHEN 'v022_training_fold' THEN 'v022_training_fold'
            WHEN 'v022_base_learner_spec' THEN 'v022_base_learner_spec'
            WHEN 'v022_fitted_model_state' THEN 'v022_fitted_model_state'
            WHEN 'v022_oof_prediction' THEN 'v022_oof_prediction'
          END;
          fingerprint_value := COALESCE(
            row_document->>'feature_schema_fingerprint',
            row_document->>'matrix_fingerprint',
            row_document->>'policy_fingerprint',
            row_document->>'fold_fingerprint',
            row_document->>'spec_fingerprint',
            row_document->>'state_fingerprint',
            row_document->>'prediction_fingerprint'
          );
          SELECT * INTO identity_artifact FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          IF identity_artifact.artifact_type IS DISTINCT FROM expected_type OR
             identity_artifact.artifact_key IS DISTINCT FROM fingerprint_value OR
             identity_artifact.semantic_fingerprint IS DISTINCT FROM
               NEW.artifact_semantic_fingerprint OR
             identity_artifact.status<>'published' THEN
            RAISE EXCEPTION 'trainable Aggregation Artifact identity is invalid';
          END IF;

          manifest_value := COALESCE(
            (row_document->>'payload_manifest_id')::uuid,
            (row_document->>'model_payload_manifest_id')::uuid,
            (row_document->>'prediction_payload_manifest_id')::uuid
          );
          IF manifest_value IS NOT NULL THEN
            SELECT manifest.materialization_state,manifest.producer_artifact_id,
                   artifact.status,artifact.semantic_fingerprint
              INTO manifest_state,manifest_producer_artifact_id,
                   manifest_artifact_status,
                   manifest_artifact_semantic_fingerprint
              FROM data.payload_manifest manifest
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=manifest.artifact_id
             WHERE manifest.payload_manifest_id=manifest_value;
            IF manifest_state IS DISTINCT FROM 'materialized' OR
               manifest_artifact_status IS DISTINCT FROM 'published' OR
               manifest_producer_artifact_id IS DISTINCT FROM NEW.artifact_id OR
               manifest_artifact_semantic_fingerprint IS NULL THEN
              RAISE EXCEPTION
                'trainable Aggregation Payload Manifest is not exact and published';
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
          IF EXISTS (SELECT 1 FROM aggregation.v022_feature_schema_version) OR
             EXISTS (SELECT 1 FROM aggregation.v022_training_matrix) OR
             EXISTS (SELECT 1 FROM aggregation.v022_fold_policy_version) OR
             EXISTS (SELECT 1 FROM aggregation.v022_training_fold) OR
             EXISTS (SELECT 1 FROM aggregation.v022_base_learner_spec) OR
             EXISTS (SELECT 1 FROM aggregation.v022_fitted_model_state) OR
             EXISTS (SELECT 1 FROM aggregation.v022_oof_prediction) THEN
            RAISE EXCEPTION
              'Cannot downgrade M123 with published trainable Aggregation identities';
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aggregation.guard_v022_trainable_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE row_document jsonb := to_jsonb(NEW);
        DECLARE identity_artifact lineage.artifact%ROWTYPE;
        DECLARE expected_type text;
        DECLARE fingerprint_value text;
        DECLARE manifest_value uuid;
        DECLARE manifest_state text;
        DECLARE manifest_artifact_status text;
        BEGIN
          expected_type := CASE TG_TABLE_NAME
            WHEN 'v022_feature_schema_version' THEN 'v022_feature_schema_version'
            WHEN 'v022_training_matrix' THEN 'v022_training_matrix'
            WHEN 'v022_fold_policy_version' THEN 'v022_fold_policy_version'
            WHEN 'v022_training_fold' THEN 'v022_training_fold'
            WHEN 'v022_base_learner_spec' THEN 'v022_base_learner_spec'
            WHEN 'v022_fitted_model_state' THEN 'v022_fitted_model_state'
            WHEN 'v022_oof_prediction' THEN 'v022_oof_prediction'
          END;
          fingerprint_value := COALESCE(
            row_document->>'feature_schema_fingerprint',
            row_document->>'matrix_fingerprint',
            row_document->>'policy_fingerprint',
            row_document->>'fold_fingerprint',
            row_document->>'spec_fingerprint',
            row_document->>'state_fingerprint',
            row_document->>'prediction_fingerprint'
          );
          SELECT * INTO identity_artifact FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          IF identity_artifact.artifact_type IS DISTINCT FROM expected_type OR
             identity_artifact.artifact_key IS DISTINCT FROM fingerprint_value OR
             identity_artifact.semantic_fingerprint IS DISTINCT FROM fingerprint_value OR
             identity_artifact.status<>'published' THEN
            RAISE EXCEPTION 'trainable Aggregation Artifact identity is invalid';
          END IF;
          manifest_value := COALESCE(
            (row_document->>'payload_manifest_id')::uuid,
            (row_document->>'model_payload_manifest_id')::uuid,
            (row_document->>'prediction_payload_manifest_id')::uuid
          );
          IF manifest_value IS NOT NULL THEN
            SELECT manifest.materialization_state,artifact.status
              INTO manifest_state,manifest_artifact_status
              FROM data.payload_manifest manifest
              JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
             WHERE manifest.payload_manifest_id=manifest_value;
            IF manifest_state IS DISTINCT FROM 'materialized' OR
               manifest_artifact_status IS DISTINCT FROM 'published' THEN
              RAISE EXCEPTION 'trainable Aggregation Payload Manifest is not published';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        """
    )
    for table in reversed(_IDENTITY_TABLES):
        op.execute(
            f"ALTER TABLE aggregation.{table} "
            "DROP COLUMN artifact_semantic_fingerprint"
        )
