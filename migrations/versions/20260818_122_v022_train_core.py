# ruff: noqa: E501
"""Add immutable trainable Aggregation matrix, Fold, Model State, and OOF identities.

Revision ID: 20260818_122_v022_train_core
Revises: 20260818_121_v022_agg_recipe
"""

from __future__ import annotations

from alembic import op

revision = "20260818_122_v022_train_core"
down_revision = "20260818_121_v022_agg_recipe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE aggregation.v022_feature_schema_version (
          feature_schema_version_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          compiled_research_graph_id uuid NOT NULL
            REFERENCES workspace.compiled_research_graph,
          aggregation_version_id uuid NOT NULL
            REFERENCES aggregation.aggregation_version,
          version_number integer NOT NULL CHECK (version_number >= 1),
          ordered_feature_document jsonb NOT NULL,
          input_count integer NOT NULL CHECK (input_count BETWEEN 1 AND 32),
          feature_schema_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (feature_schema_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (
            compiled_research_graph_id,aggregation_version_id,
            feature_schema_fingerprint
          ),
          CHECK (jsonb_typeof(ordered_feature_document)='object'),
          CHECK (
            jsonb_typeof(ordered_feature_document->'ordered_feature_keys')='array'
          ),
          CHECK (
            jsonb_array_length(ordered_feature_document->'ordered_feature_keys')=
              input_count
          )
        );

        CREATE TABLE aggregation.v022_training_matrix (
          training_matrix_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          feature_schema_version_id uuid NOT NULL
            REFERENCES aggregation.v022_feature_schema_version,
          target_version_id uuid NOT NULL REFERENCES aggregation.target_version,
          evaluation_cohort_version_id uuid NOT NULL
            REFERENCES experiment.v022_evaluation_cohort_version,
          payload_manifest_id uuid NOT NULL UNIQUE REFERENCES data.payload_manifest,
          observation_grid varchar(40) NOT NULL
            CHECK (observation_grid='xnys_completed_session_daily'),
          coverage_start date NOT NULL,
          coverage_end date NOT NULL,
          row_count bigint NOT NULL CHECK (row_count > 0),
          group_count integer NOT NULL CHECK (group_count > 0),
          matrix_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (matrix_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (coverage_start<=coverage_end)
        );

        CREATE TABLE aggregation.v022_fold_policy_version (
          fold_policy_version_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          policy_key varchar(180) NOT NULL CHECK (btrim(policy_key)<>''),
          version_number integer NOT NULL CHECK (version_number >= 1),
          policy_document jsonb NOT NULL,
          policy_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (policy_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (policy_key,version_number),
          CHECK (jsonb_typeof(policy_document)='object'),
          CHECK (policy_document->>'mode'='expanding_walk_forward'),
          CHECK ((policy_document->>'random_split')::boolean=false),
          CHECK ((policy_document->>'minimum_train_groups')::integer>=2),
          CHECK ((policy_document->>'validation_groups')::integer>=1),
          CHECK ((policy_document->>'prediction_groups')::integer>=1),
          CHECK ((policy_document->>'embargo_groups')::integer>=0)
        );

        CREATE TABLE aggregation.v022_training_fold (
          training_fold_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          training_matrix_id uuid NOT NULL
            REFERENCES aggregation.v022_training_matrix,
          fold_policy_version_id uuid NOT NULL
            REFERENCES aggregation.v022_fold_policy_version,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          train_range daterange NOT NULL,
          validation_range daterange NOT NULL,
          prediction_range daterange NOT NULL,
          train_group_count integer NOT NULL CHECK (train_group_count >= 2),
          validation_group_count integer NOT NULL CHECK (validation_group_count >= 1),
          prediction_group_count integer NOT NULL CHECK (prediction_group_count >= 1),
          fold_document jsonb NOT NULL,
          fold_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (fold_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (training_matrix_id,fold_policy_version_id,ordinal),
          CHECK (NOT isempty(train_range)),
          CHECK (NOT isempty(validation_range)),
          CHECK (NOT isempty(prediction_range)),
          CHECK (upper(train_range)<=lower(validation_range)),
          CHECK (upper(validation_range)<=lower(prediction_range)),
          CHECK (jsonb_typeof(fold_document)='object')
        );

        CREATE TABLE aggregation.v022_base_learner_spec (
          base_learner_spec_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          aggregation_version_id uuid NOT NULL
            REFERENCES aggregation.aggregation_version,
          feature_schema_version_id uuid NOT NULL
            REFERENCES aggregation.v022_feature_schema_version,
          target_version_id uuid NOT NULL REFERENCES aggregation.target_version,
          training_preset_version_id uuid NOT NULL
            REFERENCES aggregation.training_preset_version,
          fold_policy_version_id uuid NOT NULL
            REFERENCES aggregation.v022_fold_policy_version,
          adapter_key varchar(180) NOT NULL CHECK (btrim(adapter_key)<>''),
          adapter_version varchar(120) NOT NULL CHECK (btrim(adapter_version)<>''),
          hyperparameter_document jsonb NOT NULL,
          random_seed bigint NOT NULL,
          spec_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (spec_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (jsonb_typeof(hyperparameter_document)='object')
        );

        CREATE TABLE aggregation.v022_fitted_model_state (
          fitted_model_state_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          base_learner_spec_id uuid NOT NULL
            REFERENCES aggregation.v022_base_learner_spec,
          training_fold_id uuid NOT NULL REFERENCES aggregation.v022_training_fold,
          model_payload_manifest_id uuid NOT NULL UNIQUE REFERENCES data.payload_manifest,
          trained_through date NOT NULL,
          labels_known_through timestamptz NOT NULL,
          environment_fingerprint varchar(64) NOT NULL
            CHECK (environment_fingerprint ~ '^[0-9a-f]{64}$'),
          state_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (state_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (base_learner_spec_id,training_fold_id)
        );

        CREATE TABLE aggregation.v022_oof_prediction (
          oof_prediction_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          base_learner_spec_id uuid NOT NULL
            REFERENCES aggregation.v022_base_learner_spec,
          prediction_payload_manifest_id uuid NOT NULL UNIQUE
            REFERENCES data.payload_manifest,
          coverage_start date NOT NULL,
          coverage_end date NOT NULL,
          row_count bigint NOT NULL CHECK (row_count > 0),
          group_count integer NOT NULL CHECK (group_count > 0),
          prediction_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (prediction_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (coverage_start<=coverage_end)
        );

        CREATE TABLE aggregation.v022_oof_prediction_fold (
          oof_prediction_id uuid NOT NULL
            REFERENCES aggregation.v022_oof_prediction,
          training_fold_id uuid NOT NULL REFERENCES aggregation.v022_training_fold,
          fitted_model_state_id uuid NOT NULL
            REFERENCES aggregation.v022_fitted_model_state,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          PRIMARY KEY (oof_prediction_id,training_fold_id),
          UNIQUE (oof_prediction_id,ordinal),
          UNIQUE (oof_prediction_id,fitted_model_state_id)
        );

        CREATE FUNCTION aggregation.guard_v022_trainable_identity()
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

        CREATE FUNCTION aggregation.reject_v022_trainable_identity_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'v0.22 trainable Aggregation identities are append-only';
        END $$;
        """
    )
    for table in (
        "v022_feature_schema_version",
        "v022_training_matrix",
        "v022_fold_policy_version",
        "v022_training_fold",
        "v022_base_learner_spec",
        "v022_fitted_model_state",
        "v022_oof_prediction",
    ):
        op.execute(
            f"CREATE TRIGGER guard_{table} BEFORE INSERT ON aggregation.{table} "
            "FOR EACH ROW EXECUTE FUNCTION aggregation.guard_v022_trainable_identity();"
        )
        op.execute(
            f"CREATE TRIGGER reject_{table}_mutation BEFORE UPDATE OR DELETE "
            f"ON aggregation.{table} FOR EACH ROW EXECUTE FUNCTION "
            "aggregation.reject_v022_trainable_identity_mutation();"
        )
    op.execute(
        "CREATE TRIGGER reject_v022_oof_prediction_fold_mutation "
        "BEFORE UPDATE OR DELETE ON aggregation.v022_oof_prediction_fold "
        "FOR EACH ROW EXECUTE FUNCTION "
        "aggregation.reject_v022_trainable_identity_mutation();"
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
             EXISTS (SELECT 1 FROM aggregation.v022_oof_prediction) OR
             EXISTS (SELECT 1 FROM aggregation.v022_oof_prediction_fold) THEN
            RAISE EXCEPTION
              'Cannot downgrade nonempty v0.22 trainable Aggregation identities';
          END IF;
        END $$;
        """
    )
    op.execute(
        "DROP TRIGGER reject_v022_oof_prediction_fold_mutation "
        "ON aggregation.v022_oof_prediction_fold"
    )
    for table in reversed(
        (
            "v022_feature_schema_version",
            "v022_training_matrix",
            "v022_fold_policy_version",
            "v022_training_fold",
            "v022_base_learner_spec",
            "v022_fitted_model_state",
            "v022_oof_prediction",
        )
    ):
        op.execute(f"DROP TRIGGER reject_{table}_mutation ON aggregation.{table}")
        op.execute(f"DROP TRIGGER guard_{table} ON aggregation.{table}")
    op.execute("DROP FUNCTION aggregation.reject_v022_trainable_identity_mutation()")
    op.execute("DROP FUNCTION aggregation.guard_v022_trainable_identity()")
    op.execute("DROP TABLE aggregation.v022_oof_prediction_fold")
    op.execute("DROP TABLE aggregation.v022_oof_prediction")
    op.execute("DROP TABLE aggregation.v022_fitted_model_state")
    op.execute("DROP TABLE aggregation.v022_base_learner_spec")
    op.execute("DROP TABLE aggregation.v022_training_fold")
    op.execute("DROP TABLE aggregation.v022_fold_policy_version")
    op.execute("DROP TABLE aggregation.v022_training_matrix")
    op.execute("DROP TABLE aggregation.v022_feature_schema_version")
