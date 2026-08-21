# ruff: noqa: E501
"""Add immutable v0.22 direct-element diagnostics.

Revision ID: 20260814_88_v022_element_diag
Revises: 20260813_87_v022_asset_sel
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260814_88_v022_element_diag"
down_revision: str | None = "20260813_87_v022_asset_sel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_result_element_diagnostic (
          result_element_diagnostic_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          result_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          compiled_feature_occurrence_id uuid NOT NULL
            REFERENCES workspace.compiled_feature_occurrence,
          payload_manifest_id uuid NOT NULL REFERENCES data.payload_manifest,
          payload_manifest_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          target_version_id uuid NOT NULL REFERENCES data.forward_return_version,
          target_version_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          market_dataset_publication_id uuid NOT NULL REFERENCES data.dataset_publication,
          market_dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          calendar_version_id uuid NOT NULL REFERENCES catalog.calendar_version,
          calendar_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          diagnostic_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (diagnostic_fingerprint ~ '^[0-9a-f]{64}$'),
          diagnostic_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (result_artifact_id,compiled_feature_occurrence_id),
          CHECK (jsonb_typeof(diagnostic_document)='object' AND diagnostic_document<>'{}'::jsonb)
        );

        CREATE FUNCTION experiment.validate_v022_result_element_diagnostic()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; result_status varchar; manifest_row record;
                target_artifact uuid; market_artifact uuid; calendar_artifact uuid;
                direct_input_exists boolean;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF artifact_row.artifact_type IS DISTINCT FROM 'v022_result_element_diagnostic' OR
             artifact_row.artifact_key IS DISTINCT FROM NEW.diagnostic_fingerprint OR
             artifact_row.version_number IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'Element Diagnostic requires its exact draft Artifact';
          END IF;
          SELECT status INTO result_status FROM lineage.artifact
           WHERE artifact_id=NEW.result_artifact_id;
          IF result_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Element Diagnostic requires a published Result Artifact';
          END IF;
          SELECT manifest.artifact_id,artifact.status,manifest.materialization_state
            INTO manifest_row
            FROM data.payload_manifest manifest
            JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
           WHERE manifest.payload_manifest_id=NEW.payload_manifest_id;
          IF manifest_row.artifact_id IS DISTINCT FROM NEW.payload_manifest_artifact_id OR
             manifest_row.status IS DISTINCT FROM 'published' OR
             manifest_row.materialization_state IS DISTINCT FROM 'materialized' THEN
            RAISE EXCEPTION 'Element Diagnostic requires its exact materialized input Manifest';
          END IF;
          SELECT version.artifact_id INTO target_artifact
            FROM data.forward_return_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             AND artifact.status='published'
           WHERE version.forward_return_version_id=NEW.target_version_id;
          SELECT publication.artifact_id INTO market_artifact
            FROM data.dataset_publication publication
            JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
             AND artifact.status='published'
           WHERE publication.dataset_publication_id=NEW.market_dataset_publication_id;
          SELECT version.artifact_id INTO calendar_artifact
            FROM catalog.calendar_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             AND artifact.status='published'
           WHERE version.calendar_version_id=NEW.calendar_version_id;
          IF target_artifact IS DISTINCT FROM NEW.target_version_artifact_id OR
             market_artifact IS DISTINCT FROM NEW.market_dataset_artifact_id OR
             calendar_artifact IS DISTINCT FROM NEW.calendar_artifact_id THEN
            RAISE EXCEPTION 'Element Diagnostic physical identities do not match their Artifacts';
          END IF;
          SELECT EXISTS (
            SELECT 1 FROM experiment.v022_configuration_direct_input input
             WHERE input.configuration_snapshot_id=NEW.configuration_snapshot_id
               AND input.compiled_feature_occurrence_id=NEW.compiled_feature_occurrence_id
          ) INTO direct_input_exists;
          IF NOT direct_input_exists THEN
            RAISE EXCEPTION 'Element Diagnostic occurrence is not a frozen direct input';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_result_element_diagnostic_validate
          BEFORE INSERT ON experiment.v022_result_element_diagnostic
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_result_element_diagnostic();
        CREATE TRIGGER trg_v022_result_element_diagnostic_append_only
          BEFORE UPDATE OR DELETE ON experiment.v022_result_element_diagnostic
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM experiment.v022_result_element_diagnostic) THEN
            RAISE EXCEPTION 'Cannot downgrade with published v0.22 Element Diagnostics';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS experiment.validate_v022_result_element_diagnostic() CASCADE;
        DROP TABLE experiment.v022_result_element_diagnostic;
        """
    )
