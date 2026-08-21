"""Bind Raw Payload Manifests to the exact compiled Execution Context.

Revision ID: 20260814_92_v022_ctx_payload
Revises: 20260814_91_v022_def_coverage
"""

from __future__ import annotations

from alembic import op

revision = "20260814_92_v022_ctx_payload"
down_revision = "20260814_91_v022_def_coverage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE data.v022_execution_context_payload_binding (
          execution_context_payload_binding_id uuid PRIMARY KEY,
          compiled_execution_data_context_id uuid NOT NULL
            REFERENCES workspace.v022_compiled_execution_data_context,
          dataset_publication_id uuid NOT NULL
            REFERENCES data.dataset_publication ON DELETE RESTRICT,
          feature_version_id uuid NOT NULL
            REFERENCES processing.feature_version ON DELETE RESTRICT,
          payload_manifest_id uuid NOT NULL UNIQUE
            REFERENCES data.payload_manifest ON DELETE RESTRICT,
          known_at_start timestamptz NOT NULL,
          known_at_end timestamptz NOT NULL,
          snapshot_semantics jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (compiled_execution_data_context_id,feature_version_id),
          CHECK (known_at_start <= known_at_end),
          CHECK ((
            jsonb_typeof(snapshot_semantics)='object' AND
            snapshot_semantics->>'semantic_mode'=
              'back_adjusted_historical_research' AND
            snapshot_semantics->>'known_at_rule'=
              'xnys_session_close_at_utc' AND
            snapshot_semantics->>'input_revision_rule'=
              'dataset_publication_id' AND
            snapshot_semantics->>'compiled_execution_data_context_id'=
              compiled_execution_data_context_id::text AND
            snapshot_semantics->>'dataset_publication_id'=
              dataset_publication_id::text AND
            snapshot_semantics->>'price_basis'='back_adjusted' AND
            snapshot_semantics->'product_warning_required'='true'::jsonb
          ) IS TRUE)
        );

        CREATE FUNCTION data.validate_v022_execution_context_payload_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_dataset_id uuid;
                expected_dataset_artifact_id uuid;
                context_artifact_id_value uuid;
                context_artifact_status_value varchar;
                manifest_artifact_id_value uuid;
                manifest_producer_artifact_id_value uuid;
                manifest_artifact_type_value varchar;
                manifest_artifact_status_value varchar;
        BEGIN
          SELECT input.dataset_publication_id,input.dataset_artifact_id,
                 context.artifact_id,context_artifact.status
            INTO expected_dataset_id,expected_dataset_artifact_id,
                 context_artifact_id_value,context_artifact_status_value
            FROM workspace.v022_compiled_execution_data_context context
            JOIN workspace.v022_compiled_execution_data_input input
              ON input.compiled_execution_data_context_id=
                 context.compiled_execution_data_context_id
             AND input.input_key='canonical_market_bars'
            JOIN lineage.artifact context_artifact
              ON context_artifact.artifact_id=context.artifact_id
           WHERE context.compiled_execution_data_context_id=
                 NEW.compiled_execution_data_context_id;
          IF expected_dataset_id IS NULL OR
             expected_dataset_id IS DISTINCT FROM NEW.dataset_publication_id OR
             context_artifact_status_value IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION
              'Raw Payload binding must use the exact published Execution Context Dataset';
          END IF;

          IF NOT EXISTS (
            SELECT 1
              FROM workspace.v022_compiled_execution_data_context context
              JOIN workspace.compiled_feature_occurrence occurrence
                ON occurrence.compiled_research_graph_id=
                   context.compiled_research_graph_id
               AND occurrence.feature_version_id=NEW.feature_version_id
               AND occurrence.production_kind='raw_input'
             WHERE context.compiled_execution_data_context_id=
                   NEW.compiled_execution_data_context_id
          ) THEN
            RAISE EXCEPTION
              'Raw Payload binding Feature is not a frozen Raw occurrence';
          END IF;

          SELECT manifest.artifact_id,manifest.producer_artifact_id,
                 artifact.artifact_type,artifact.status
            INTO manifest_artifact_id_value,manifest_producer_artifact_id_value,
                 manifest_artifact_type_value,manifest_artifact_status_value
            FROM data.payload_manifest manifest
            JOIN lineage.artifact artifact
              ON artifact.artifact_id=manifest.artifact_id
           WHERE manifest.payload_manifest_id=NEW.payload_manifest_id;
          IF manifest_artifact_id_value IS NULL OR
             manifest_producer_artifact_id_value IS DISTINCT FROM
               expected_dataset_artifact_id OR
             manifest_artifact_type_value IS DISTINCT FROM
               'v022_payload_manifest' OR
             manifest_artifact_status_value IS DISTINCT FROM 'draft' OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=manifest_artifact_id_value
                  AND dependency.depends_on_artifact_id=
                      context_artifact_id_value
                  AND dependency.role='compiled_execution_data_context'
                  AND dependency.ordinal=3
             ) THEN
            RAISE EXCEPTION
              'Raw Payload Manifest does not bind the exact Execution Context lineage';
          END IF;
          RETURN NEW;
        END
        $$;

        CREATE TRIGGER trg_v022_execution_context_payload_binding_validate
          BEFORE INSERT ON data.v022_execution_context_payload_binding
          FOR EACH ROW EXECUTE FUNCTION
            data.validate_v022_execution_context_payload_binding();
        CREATE TRIGGER trg_v022_execution_context_payload_binding_append_only
          BEFORE UPDATE OR DELETE ON data.v022_execution_context_payload_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM data.v022_execution_context_payload_binding
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade with Execution Context Payload bindings';
          END IF;
        END
        $$;
        DROP TRIGGER IF EXISTS
          trg_v022_execution_context_payload_binding_append_only
          ON data.v022_execution_context_payload_binding;
        DROP TRIGGER IF EXISTS
          trg_v022_execution_context_payload_binding_validate
          ON data.v022_execution_context_payload_binding;
        DROP FUNCTION IF EXISTS
          data.validate_v022_execution_context_payload_binding();
        """
    )
    op.drop_table("v022_execution_context_payload_binding", schema="data")
