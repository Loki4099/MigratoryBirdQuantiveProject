# ruff: noqa: E501
"""Persist immutable strict-OOF diagnostics for trainable Aggregation Runs.

Revision ID: 20260818_127_v022_train_diag
Revises: 20260818_126_v022_ensemble_run
"""

from __future__ import annotations

from alembic import op

revision = "20260818_127_v022_train_diag"
down_revision = "20260818_126_v022_ensemble_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE aggregation.v022_trainable_aggregation_diagnostic (
          trainable_aggregation_diagnostic_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          aggregation_run_id uuid NOT NULL UNIQUE
            REFERENCES aggregation.aggregation_run,
          ensemble_spec_id uuid NULL
            REFERENCES aggregation.v022_trainable_ensemble_spec,
          diagnostic_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (diagnostic_fingerprint ~ '^[0-9a-f]{64}$'),
          member_count integer NOT NULL CHECK (member_count BETWEEN 1 AND 12),
          target_group_count integer NOT NULL
            CHECK (target_group_count BETWEEN 1 AND 12 AND
                   target_group_count <= member_count),
          diagnostic_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK ((
            jsonb_typeof(diagnostic_document)='object' AND
            diagnostic_document->>'contract_version'='v0.22.0' AND
            diagnostic_document->>'diagnostic_kind'=
              'strict_oof_trainable_ensemble_v1' AND
            (diagnostic_document->>'member_count')::integer=member_count AND
            (diagnostic_document->>'target_group_count')::integer=
              target_group_count AND
            diagnostic_document->>'portfolio_ablation_status'=
              'not_computed_requires_separate_frozen_run'
          ) IS TRUE)
        );

        CREATE FUNCTION aggregation.validate_v022_trainable_aggregation_diagnostic()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record;
        DECLARE run_mode varchar;
        DECLARE run_spec uuid;
        DECLARE document_spec text;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_trainable_aggregation_diagnostic' OR
             artifact_row.artifact_key IS DISTINCT FROM
               NEW.diagnostic_fingerprint OR
             artifact_row.version_number IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION
              'Trainable Diagnostic requires its exact draft Artifact';
          END IF;
          SELECT version.execution_mode,run.ensemble_spec_id
            INTO run_mode,run_spec
            FROM aggregation.aggregation_run run
            JOIN aggregation.aggregation_version version
              ON version.aggregation_version_id=run.aggregation_version_id
           WHERE run.aggregation_run_id=NEW.aggregation_run_id;
          document_spec := NULLIF(
            NEW.diagnostic_document->>'ensemble_fingerprint',''
          );
          IF run_mode IS DISTINCT FROM 'supervised' OR
             run_spec IS DISTINCT FROM NEW.ensemble_spec_id OR
             (NEW.member_count=1 AND NEW.ensemble_spec_id IS NOT NULL) OR
             (NEW.member_count>1 AND NEW.ensemble_spec_id IS NULL) THEN
            RAISE EXCEPTION
              'Trainable Diagnostic does not match its supervised Run identity';
          END IF;
          IF NEW.ensemble_spec_id IS NULL THEN
            IF document_spec IS NOT NULL THEN
              RAISE EXCEPTION
                'Direct Trainable Diagnostic cannot claim an Ensemble fingerprint';
            END IF;
          ELSIF NOT EXISTS (
            SELECT 1 FROM aggregation.v022_trainable_ensemble_spec spec
             WHERE spec.ensemble_spec_id=NEW.ensemble_spec_id
               AND spec.ensemble_fingerprint=document_spec
               AND spec.member_count=NEW.member_count
               AND spec.target_group_count=NEW.target_group_count
          ) THEN
            RAISE EXCEPTION
              'Trainable Diagnostic Ensemble identity is not exact';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_validate_v022_trainable_aggregation_diagnostic
          BEFORE INSERT
          ON aggregation.v022_trainable_aggregation_diagnostic
          FOR EACH ROW EXECUTE FUNCTION
            aggregation.validate_v022_trainable_aggregation_diagnostic();
        CREATE TRIGGER trg_v022_trainable_aggregation_diagnostic_append_only
          BEFORE UPDATE OR DELETE
          ON aggregation.v022_trainable_aggregation_diagnostic
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM aggregation.v022_trainable_aggregation_diagnostic
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade M127 with Trainable Aggregation Diagnostics';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS
          aggregation.validate_v022_trainable_aggregation_diagnostic() CASCADE;
        DROP TABLE aggregation.v022_trainable_aggregation_diagnostic;
        """
    )
