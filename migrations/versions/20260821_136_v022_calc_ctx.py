"""Add frequency-neutral Processing Calculation Context identities.

Revision ID: 20260821_136_v022_calc_ctx
Revises: 20260820_135_v022_cohort_gate
"""

from __future__ import annotations

from alembic import op

revision = "20260821_136_v022_calc_ctx"
down_revision = "20260820_135_v022_cohort_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE processing.v022_calculation_context (
          calculation_context_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          context_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (context_fingerprint ~ '^[0-9a-f]{64}$'),
          dataset_publication_id uuid NOT NULL REFERENCES data.dataset_publication,
          dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          calendar_version_id uuid NOT NULL REFERENCES catalog.calendar_version,
          calendar_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          coverage_start date NOT NULL,
          coverage_end date NOT NULL,
          security_ids jsonb NOT NULL,
          raw_feature_version_ids jsonb NOT NULL,
          source_snapshot_artifact_ids jsonb NOT NULL,
          context_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (coverage_start<=coverage_end),
          CHECK (jsonb_typeof(security_ids)='array' AND
                 jsonb_array_length(security_ids)>0),
          CHECK (jsonb_typeof(raw_feature_version_ids)='array' AND
                 jsonb_array_length(raw_feature_version_ids)>0),
          CHECK (jsonb_typeof(source_snapshot_artifact_ids)='array' AND
                 jsonb_array_length(source_snapshot_artifact_ids)>0),
          CHECK (jsonb_typeof(context_document)='object'),
          CHECK (context_fingerprint=
            strategy.v022_strategy_parameter_fingerprint(context_document)),
          CHECK ((
            context_document->>'contract_version'=
              'v0.22.processing_calculation_context.v1' AND
            context_document->>'dataset_publication_id'=
              dataset_publication_id::text AND
            context_document->>'calendar_version_id'=calendar_version_id::text AND
            context_document->>'coverage_start'=coverage_start::text AND
            context_document->>'coverage_end'=coverage_end::text AND
            context_document->'security_ids'=security_ids AND
            context_document->'raw_feature_version_ids'=raw_feature_version_ids AND
            context_document->'source_snapshot_artifact_ids'=
              source_snapshot_artifact_ids
          ) IS TRUE)
        );

        CREATE TABLE processing.v022_compiled_context_calculation_binding (
          compiled_execution_data_context_id uuid PRIMARY KEY
            REFERENCES workspace.v022_compiled_execution_data_context,
          calculation_context_id uuid NOT NULL
            REFERENCES processing.v022_calculation_context,
          binding_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$'),
          binding_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (jsonb_typeof(binding_document)='object'),
          CHECK (binding_fingerprint=
            strategy.v022_strategy_parameter_fingerprint(binding_document)),
          CHECK ((
            binding_document->>'contract_version'=
              'v0.22.compiled_context_calculation_binding.v1' AND
            binding_document->>'compiled_execution_data_context_id'=
              compiled_execution_data_context_id::text AND
            binding_document->>'calculation_context_id'=calculation_context_id::text
          ) IS TRUE)
        );

        CREATE TABLE data.v022_calculation_context_payload_binding (
          calculation_context_payload_binding_id uuid PRIMARY KEY,
          calculation_context_id uuid NOT NULL
            REFERENCES processing.v022_calculation_context,
          dataset_publication_id uuid NOT NULL REFERENCES data.dataset_publication,
          feature_version_id uuid NOT NULL REFERENCES processing.feature_version,
          payload_manifest_id uuid NOT NULL UNIQUE REFERENCES data.payload_manifest,
          known_at_start timestamptz NOT NULL,
          known_at_end timestamptz NOT NULL,
          snapshot_semantics jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (calculation_context_id,feature_version_id),
          CHECK (known_at_start<=known_at_end),
          CHECK (jsonb_typeof(snapshot_semantics)='object'),
          CHECK ((
            snapshot_semantics->>'semantic_mode'=
              'back_adjusted_historical_research' AND
            snapshot_semantics->>'known_at_rule'='xnys_session_close_at_utc' AND
            snapshot_semantics->>'input_revision_rule'='dataset_publication_id' AND
            snapshot_semantics->>'calculation_context_id'=
              calculation_context_id::text AND
            snapshot_semantics->>'dataset_publication_id'=
              dataset_publication_id::text AND
            snapshot_semantics->>'price_basis'='back_adjusted' AND
            snapshot_semantics->'product_warning_required'='true'::jsonb
          ) IS TRUE)
        );

        CREATE FUNCTION processing.validate_v022_calculation_context()
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
             WHERE snapshot.source_snapshot_id IS NULL OR input.dataset_input_id IS NULL OR
                   artifact.status<>'published'
          ) THEN
            RAISE EXCEPTION 'Calculation Context snapshots must be published Dataset inputs';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION processing.validate_v022_compiled_context_calculation_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE calculation_row record; input_row record;
                expected_raw_features jsonb;
        BEGIN
          SELECT context.*,artifact.status AS artifact_status
            INTO calculation_row
            FROM processing.v022_calculation_context context
            JOIN lineage.artifact artifact ON artifact.artifact_id=context.artifact_id
           WHERE context.calculation_context_id=NEW.calculation_context_id;
          SELECT input.dataset_publication_id,input.calendar_version_id,
                 input.coverage_start,input.coverage_end,input.security_ids
            INTO input_row
            FROM workspace.v022_compiled_execution_data_input input
           WHERE input.compiled_execution_data_context_id=
                   NEW.compiled_execution_data_context_id
             AND input.input_key='canonical_market_bars';
          SELECT jsonb_agg(to_jsonb(occurrence.feature_version_id::text)
                           ORDER BY occurrence.feature_version_id::text)
            INTO expected_raw_features
            FROM workspace.v022_compiled_execution_data_context compiled_context
            JOIN workspace.compiled_feature_occurrence occurrence
              ON occurrence.compiled_research_graph_id=
                   compiled_context.compiled_research_graph_id
             AND occurrence.production_kind='raw_input'
           WHERE compiled_context.compiled_execution_data_context_id=
                   NEW.compiled_execution_data_context_id;
          IF calculation_row.calculation_context_id IS NULL OR
             calculation_row.artifact_status IS DISTINCT FROM 'published' OR
             input_row.dataset_publication_id IS DISTINCT FROM
               calculation_row.dataset_publication_id OR
             input_row.calendar_version_id IS DISTINCT FROM
               calculation_row.calendar_version_id OR
             input_row.coverage_start IS DISTINCT FROM calculation_row.coverage_start OR
             input_row.coverage_end IS DISTINCT FROM calculation_row.coverage_end OR
             input_row.security_ids IS DISTINCT FROM calculation_row.security_ids OR
             expected_raw_features IS DISTINCT FROM
               calculation_row.raw_feature_version_ids THEN
            RAISE EXCEPTION
              'Compiled Context does not reproduce the Processing Calculation Context';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION data.validate_v022_calculation_context_payload_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE calculation_row record; manifest_row record;
        BEGIN
          SELECT context.*,artifact.status AS artifact_status
            INTO calculation_row
            FROM processing.v022_calculation_context context
            JOIN lineage.artifact artifact ON artifact.artifact_id=context.artifact_id
           WHERE context.calculation_context_id=NEW.calculation_context_id;
          SELECT manifest.artifact_id,manifest.producer_artifact_id,
                 artifact.status,artifact.artifact_type
            INTO manifest_row
            FROM data.payload_manifest manifest
            JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
           WHERE manifest.payload_manifest_id=NEW.payload_manifest_id;
          IF calculation_row.calculation_context_id IS NULL OR
             calculation_row.artifact_status IS DISTINCT FROM 'published' OR
             calculation_row.dataset_publication_id IS DISTINCT FROM
               NEW.dataset_publication_id OR
             NOT calculation_row.raw_feature_version_ids ?
               NEW.feature_version_id::text OR
             manifest_row.producer_artifact_id IS DISTINCT FROM
               calculation_row.dataset_artifact_id OR
             manifest_row.status IS DISTINCT FROM 'draft' OR
             manifest_row.artifact_type IS DISTINCT FROM 'v022_payload_manifest' OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=manifest_row.artifact_id
                  AND dependency.depends_on_artifact_id=calculation_row.artifact_id
                  AND dependency.role='processing_calculation_context'
             ) THEN
            RAISE EXCEPTION
              'Raw Payload must bind its exact published Calculation Context';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_calculation_context_validate
          BEFORE INSERT ON processing.v022_calculation_context
          FOR EACH ROW EXECUTE FUNCTION processing.validate_v022_calculation_context();
        CREATE TRIGGER trg_v022_calculation_context_append_only
          BEFORE UPDATE OR DELETE ON processing.v022_calculation_context
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_compiled_context_calculation_validate
          BEFORE INSERT ON processing.v022_compiled_context_calculation_binding
          FOR EACH ROW EXECUTE FUNCTION
            processing.validate_v022_compiled_context_calculation_binding();
        CREATE TRIGGER trg_v022_compiled_context_calculation_append_only
          BEFORE UPDATE OR DELETE ON processing.v022_compiled_context_calculation_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_calculation_context_payload_validate
          BEFORE INSERT ON data.v022_calculation_context_payload_binding
          FOR EACH ROW EXECUTE FUNCTION
            data.validate_v022_calculation_context_payload_binding();
        CREATE TRIGGER trg_v022_calculation_context_payload_append_only
          BEFORE UPDATE OR DELETE ON data.v022_calculation_context_payload_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        r"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM processing.v022_calculation_context) OR
             EXISTS (SELECT 1 FROM processing.v022_compiled_context_calculation_binding) OR
             EXISTS (SELECT 1 FROM data.v022_calculation_context_payload_binding) THEN
            RAISE EXCEPTION 'Cannot downgrade with Processing Calculation Context evidence';
          END IF;
        END $$;
        DROP TRIGGER IF EXISTS trg_v022_calculation_context_payload_append_only
          ON data.v022_calculation_context_payload_binding;
        DROP TRIGGER IF EXISTS trg_v022_calculation_context_payload_validate
          ON data.v022_calculation_context_payload_binding;
        DROP TRIGGER IF EXISTS trg_v022_compiled_context_calculation_append_only
          ON processing.v022_compiled_context_calculation_binding;
        DROP TRIGGER IF EXISTS trg_v022_compiled_context_calculation_validate
          ON processing.v022_compiled_context_calculation_binding;
        DROP TRIGGER IF EXISTS trg_v022_calculation_context_append_only
          ON processing.v022_calculation_context;
        DROP TRIGGER IF EXISTS trg_v022_calculation_context_validate
          ON processing.v022_calculation_context;
        DROP FUNCTION IF EXISTS data.validate_v022_calculation_context_payload_binding();
        DROP FUNCTION IF EXISTS processing.validate_v022_compiled_context_calculation_binding();
        DROP FUNCTION IF EXISTS processing.validate_v022_calculation_context();
        DROP TABLE data.v022_calculation_context_payload_binding;
        DROP TABLE processing.v022_compiled_context_calculation_binding;
        DROP TABLE processing.v022_calculation_context;
        """
    )
