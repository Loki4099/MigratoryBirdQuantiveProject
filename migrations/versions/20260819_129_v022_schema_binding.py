# ruff: noqa: E501
"""Make trainable Feature Schemas global and bind them to compiled instances.

Revision ID: 20260819_129_v022_schema_binding
Revises: 20260819_128_v022_product_state
"""

from __future__ import annotations

from alembic import op

revision = "20260819_129_v022_schema_binding"
down_revision = "20260819_128_v022_product_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_compiled_feature_schema_binding (
          compiled_aggregation_instance_id uuid PRIMARY KEY
            REFERENCES workspace.compiled_aggregation_instance,
          feature_schema_version_id uuid NOT NULL
            REFERENCES aggregation.v022_feature_schema_version,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE FUNCTION workspace.validate_v022_compiled_feature_schema_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar;
        DECLARE expected_keys jsonb;
        DECLARE schema_keys jsonb;
        DECLARE schema_artifact_status varchar;
        BEGIN
          SELECT version.execution_mode,
                 (
                   SELECT jsonb_agg(variant.variant_key ORDER BY input.ordinal)
                     FROM workspace.compiled_aggregation_input input
                     JOIN workspace.compiled_feature_occurrence occurrence
                       ON occurrence.compiled_feature_occurrence_id=
                          input.compiled_feature_occurrence_id
                     JOIN processing.feature_version feature_version
                       ON feature_version.feature_version_id=
                          occurrence.feature_version_id
                     JOIN processing.feature_variant variant
                       ON variant.feature_variant_id=
                          feature_version.feature_variant_id
                    WHERE input.compiled_aggregation_instance_id=
                          instance.compiled_aggregation_instance_id
                 )
            INTO mode,expected_keys
            FROM workspace.compiled_aggregation_instance instance
            JOIN aggregation.aggregation_version version
              ON version.aggregation_version_id=instance.aggregation_version_id
           WHERE instance.compiled_aggregation_instance_id=
                 NEW.compiled_aggregation_instance_id;
          SELECT schema.ordered_feature_document->'ordered_feature_keys',
                 artifact.status
            INTO schema_keys,schema_artifact_status
            FROM aggregation.v022_feature_schema_version schema
            JOIN lineage.artifact artifact ON artifact.artifact_id=schema.artifact_id
           WHERE schema.feature_schema_version_id=NEW.feature_schema_version_id;
          IF mode IS DISTINCT FROM 'supervised' OR
             expected_keys IS NULL OR
             schema_keys IS DISTINCT FROM expected_keys OR
             schema_artifact_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION
              'Compiled Feature Schema binding is not exact and published';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_validate_v022_compiled_feature_schema_binding
          BEFORE INSERT ON workspace.v022_compiled_feature_schema_binding
          FOR EACH ROW EXECUTE FUNCTION
            workspace.validate_v022_compiled_feature_schema_binding();

        INSERT INTO workspace.v022_compiled_feature_schema_binding (
          compiled_aggregation_instance_id,feature_schema_version_id
        )
        SELECT instance.compiled_aggregation_instance_id,
               schema.feature_schema_version_id
          FROM workspace.compiled_aggregation_instance instance
          JOIN aggregation.aggregation_version version
            ON version.aggregation_version_id=instance.aggregation_version_id
           AND version.execution_mode='supervised'
          JOIN aggregation.v022_feature_schema_version schema
            ON schema.compiled_research_graph_id=
               instance.compiled_research_graph_id
           AND schema.aggregation_version_id=instance.aggregation_version_id
           AND schema.ordered_feature_document->'ordered_feature_keys'=(
             SELECT jsonb_agg(variant.variant_key ORDER BY input.ordinal)
               FROM workspace.compiled_aggregation_input input
               JOIN workspace.compiled_feature_occurrence occurrence
                 ON occurrence.compiled_feature_occurrence_id=
                    input.compiled_feature_occurrence_id
               JOIN processing.feature_version feature_version
                 ON feature_version.feature_version_id=
                    occurrence.feature_version_id
               JOIN processing.feature_variant variant
                 ON variant.feature_variant_id=feature_version.feature_variant_id
              WHERE input.compiled_aggregation_instance_id=
                    instance.compiled_aggregation_instance_id
           );

        DO $$ BEGIN
          IF EXISTS (
            SELECT 1
              FROM workspace.compiled_aggregation_instance instance
              JOIN aggregation.aggregation_version version
                ON version.aggregation_version_id=instance.aggregation_version_id
              LEFT JOIN workspace.v022_compiled_feature_schema_binding binding
                ON binding.compiled_aggregation_instance_id=
                   instance.compiled_aggregation_instance_id
             WHERE version.execution_mode='supervised'
               AND binding.compiled_aggregation_instance_id IS NULL
          ) OR EXISTS (
            SELECT 1
              FROM aggregation.v022_feature_schema_version schema
              LEFT JOIN workspace.v022_compiled_feature_schema_binding binding
                ON binding.feature_schema_version_id=schema.feature_schema_version_id
             WHERE binding.feature_schema_version_id IS NULL
          ) THEN
            RAISE EXCEPTION
              'Cannot globalize Feature Schemas without exact compiled bindings';
          END IF;
        END $$;

        CREATE OR REPLACE FUNCTION aggregation.validate_v022_trainable_ensemble_spec()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar;
        DECLARE schema_fingerprint text;
        DECLARE identity_artifact lineage.artifact%ROWTYPE;
        BEGIN
          SELECT execution_mode INTO mode
            FROM aggregation.aggregation_version
           WHERE aggregation_version_id=NEW.aggregation_version_id;
          SELECT feature_schema_fingerprint INTO schema_fingerprint
            FROM aggregation.v022_feature_schema_version
           WHERE feature_schema_version_id=NEW.feature_schema_version_id;
          SELECT * INTO identity_artifact FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          IF mode IS DISTINCT FROM 'supervised' OR
             NEW.ensemble_document->>'feature_schema_fingerprint'
               IS DISTINCT FROM schema_fingerprint OR
             identity_artifact.artifact_type IS DISTINCT FROM
               'v022_trainable_ensemble_spec' OR
             identity_artifact.artifact_key IS DISTINCT FROM
               NEW.ensemble_fingerprint OR
             identity_artifact.semantic_fingerprint IS DISTINCT FROM
               NEW.artifact_semantic_fingerprint OR
             identity_artifact.status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION
              'Trainable Ensemble Spec identity is not exact and published';
          END IF;
          RETURN NEW;
        END $$;

        ALTER TABLE aggregation.v022_feature_schema_version
          DROP COLUMN compiled_research_graph_id,
          DROP COLUMN aggregation_version_id;

        CREATE OR REPLACE FUNCTION workspace.close_v022_supervised_aggregation_instance()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar;
        DECLARE ensemble_count integer;
        DECLARE schema_count integer;
        BEGIN
          SELECT execution_mode INTO mode
            FROM aggregation.aggregation_version
           WHERE aggregation_version_id=NEW.aggregation_version_id;
          SELECT count(*) INTO ensemble_count
            FROM workspace.v022_compiled_trainable_ensemble_binding
           WHERE compiled_aggregation_instance_id=
                 NEW.compiled_aggregation_instance_id;
          SELECT count(*) INTO schema_count
            FROM workspace.v022_compiled_feature_schema_binding
           WHERE compiled_aggregation_instance_id=
                 NEW.compiled_aggregation_instance_id;
          IF mode='deterministic' AND (ensemble_count<>0 OR schema_count<>0) THEN
            RAISE EXCEPTION
              'Deterministic Aggregation cannot bind trainable identities';
          ELSIF mode='supervised' AND schema_count<>1 THEN
            RAISE EXCEPTION
              'Supervised Aggregation requires one exact Feature Schema';
          ELSIF mode='supervised' AND
                NEW.target_version_id IS NULL AND ensemble_count<>1 THEN
            RAISE EXCEPTION
              'Multi-member supervised Aggregation requires one exact Ensemble Spec';
          ELSIF mode='supervised' AND
                NEW.target_version_id IS NOT NULL AND ensemble_count<>0 THEN
            RAISE EXCEPTION
              'Direct supervised Aggregation cannot also bind an Ensemble Spec';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_compiled_feature_schema_binding_append_only
          BEFORE UPDATE OR DELETE
          ON workspace.v022_compiled_feature_schema_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )

def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM workspace.v022_compiled_feature_schema_binding
          ) OR EXISTS (
            SELECT 1 FROM aggregation.v022_feature_schema_version
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade M129 with global Feature Schema identities';
          END IF;
        END $$;

        DROP TRIGGER trg_v022_compiled_feature_schema_binding_append_only
          ON workspace.v022_compiled_feature_schema_binding;

        ALTER TABLE aggregation.v022_feature_schema_version
          ADD COLUMN compiled_research_graph_id uuid
            REFERENCES workspace.compiled_research_graph,
          ADD COLUMN aggregation_version_id uuid
            REFERENCES aggregation.aggregation_version;
        ALTER TABLE aggregation.v022_feature_schema_version
          ALTER COLUMN compiled_research_graph_id SET NOT NULL,
          ALTER COLUMN aggregation_version_id SET NOT NULL;
        ALTER TABLE aggregation.v022_feature_schema_version
          ADD UNIQUE (
            compiled_research_graph_id,aggregation_version_id,
            feature_schema_fingerprint
          );

        DROP TRIGGER trg_validate_v022_compiled_feature_schema_binding
          ON workspace.v022_compiled_feature_schema_binding;
        DROP FUNCTION workspace.validate_v022_compiled_feature_schema_binding();
        DROP TABLE workspace.v022_compiled_feature_schema_binding;
        """
    )
