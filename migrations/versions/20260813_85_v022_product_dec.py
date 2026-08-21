"""Allow defense-null v0.22 Product Decisions and validate exact runtime inputs.

Revision ID: 20260813_85_v022_product_dec
Revises: 20260813_84_v022_dynamic_ctx
"""

from __future__ import annotations

from alembic import op

revision = "20260813_85_v022_product_dec"
down_revision = "20260813_84_v022_dynamic_ctx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE constraint_name text;
        BEGIN
          SELECT item.conname INTO constraint_name
            FROM pg_constraint item
           WHERE item.conrelid='product.v022_product_decision'::regclass
             AND item.contype='c'
             AND pg_get_constraintdef(item.oid) LIKE
                 '%defense_decision_artifact_id IS NOT NULL%';
          IF constraint_name IS NULL THEN
            RAISE EXCEPTION 'Existing Product Decision runtime check was not found';
          END IF;
          EXECUTE format(
            'ALTER TABLE product.v022_product_decision DROP CONSTRAINT %I',
            constraint_name
          );
        END $$;

        ALTER TABLE product.v022_product_decision
          ADD CONSTRAINT ck_v022_product_decision_runtime_artifacts_v2 CHECK (
            (decision_status='completed' AND input_manifest_artifact_id IS NOT NULL
              AND aggregation_run_artifact_id IS NOT NULL
              AND strategy_target_artifact_id IS NOT NULL
              AND merged_target_artifact_id IS NOT NULL
              AND jsonb_array_length(reason_codes)=0)
            OR
            (decision_status='missing' AND input_manifest_artifact_id IS NULL
              AND active_model_state_artifact_id IS NULL
              AND aggregation_run_artifact_id IS NULL
              AND strategy_target_artifact_id IS NULL
              AND defense_decision_artifact_id IS NULL
              AND merged_target_artifact_id IS NULL
              AND jsonb_array_length(reason_codes)>0)
          );

        CREATE OR REPLACE FUNCTION product.validate_v022_product_decision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE enrollment_row record; session_row record; execution_mode varchar;
                defense_required boolean; expected_oos boolean;
                dependency_count integer; expected_dependency_count integer;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT enrollment.*,artifact.status INTO enrollment_row
            FROM product.v022_product_enrollment enrollment
            JOIN lineage.artifact artifact ON artifact.artifact_id=enrollment.artifact_id
           WHERE enrollment.product_enrollment_id=NEW.product_enrollment_id;
          SELECT * INTO session_row FROM product.v022_decision_schedule_session
           WHERE decision_session_id=NEW.decision_session_id;
          IF enrollment_row.status <> 'published' OR
             enrollment_row.execution_version_id <> NEW.execution_version_id OR
             session_row.decision_schedule_version_id <>
               enrollment_row.decision_schedule_version_id THEN
            RAISE EXCEPTION
              'Product Decision must bind its exact Enrollment Execution and Schedule';
          END IF;
          expected_oos := NEW.evidence_class='prospective_oos' AND
            session_row.ordinal >= (
              SELECT ordinal FROM product.v022_decision_schedule_session
               WHERE decision_session_id=enrollment_row.first_eligible_decision_session_id
            );
          IF NEW.oos_eligible IS DISTINCT FROM expected_oos THEN
            RAISE EXCEPTION 'Product Decision OOS eligibility is not canonical';
          END IF;
          SELECT configuration.semantic_identity_document
                   #>> '{aggregation,execution_mode}',
                 coalesce(configuration.semantic_identity_document->'defense','null'::jsonb)
                   <> 'null'::jsonb
            INTO execution_mode,defense_required
            FROM product.v022_execution_version execution
            JOIN experiment.v022_research_configuration_snapshot configuration
              ON configuration.configuration_snapshot_id=execution.configuration_snapshot_id
           WHERE execution.execution_version_id=NEW.execution_version_id;
          IF execution_mode='deterministic' AND
             NEW.active_model_state_artifact_id IS NOT NULL THEN
            RAISE EXCEPTION
              'Deterministic Product Decision must have NULL active Model State';
          END IF;
          IF NEW.decision_status='completed' THEN
            IF defense_required IS DISTINCT FROM
               (NEW.defense_decision_artifact_id IS NOT NULL) THEN
              RAISE EXCEPTION
                'Product Decision Defense Artifact must match its Configuration';
            END IF;
            expected_dependency_count := CASE WHEN defense_required THEN 5 ELSE 4 END;
            SELECT count(*) INTO dependency_count FROM lineage.artifact
             WHERE artifact_id IN (
               NEW.input_manifest_artifact_id,NEW.aggregation_run_artifact_id,
               NEW.strategy_target_artifact_id,NEW.defense_decision_artifact_id,
               NEW.merged_target_artifact_id
             ) AND status='published';
            IF dependency_count <> expected_dependency_count THEN
              RAISE EXCEPTION
                'Completed Product Decision requires exact published runtime Artifacts';
            END IF;
            IF NEW.active_model_state_artifact_id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM lineage.artifact
               WHERE artifact_id=NEW.active_model_state_artifact_id
                 AND status='published'
            ) THEN
              RAISE EXCEPTION 'Active Model State must be a published Artifact';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM product.v022_product_decision
             WHERE decision_status='completed'
               AND defense_decision_artifact_id IS NULL
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade while defense-null completed Product Decisions exist';
          END IF;
        END $$;
        ALTER TABLE product.v022_product_decision
          DROP CONSTRAINT ck_v022_product_decision_runtime_artifacts_v2;
        ALTER TABLE product.v022_product_decision
          ADD CONSTRAINT ck_v022_product_decision_runtime_artifacts_v1 CHECK (
            (decision_status='completed' AND input_manifest_artifact_id IS NOT NULL
              AND aggregation_run_artifact_id IS NOT NULL
              AND strategy_target_artifact_id IS NOT NULL
              AND defense_decision_artifact_id IS NOT NULL
              AND merged_target_artifact_id IS NOT NULL
              AND jsonb_array_length(reason_codes)=0)
            OR
            (decision_status='missing' AND input_manifest_artifact_id IS NULL
              AND active_model_state_artifact_id IS NULL
              AND aggregation_run_artifact_id IS NULL
              AND strategy_target_artifact_id IS NULL
              AND defense_decision_artifact_id IS NULL
              AND merged_target_artifact_id IS NULL
              AND jsonb_array_length(reason_codes)>0)
          );
        """
    )
