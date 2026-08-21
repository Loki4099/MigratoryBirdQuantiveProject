# ruff: noqa: E501
"""Freeze complete trainable Ensemble States for prospective Products.

Revision ID: 20260819_128_v022_product_state
Revises: 20260818_127_v022_train_diag
"""

from __future__ import annotations

from alembic import op

revision = "20260819_128_v022_product_state"
down_revision = "20260818_127_v022_train_diag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE product.v022_product_ensemble_state (
          product_ensemble_state_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          execution_version_id uuid NOT NULL
            REFERENCES product.v022_execution_version,
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          source_result_evidence_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_result_evidence_snapshot,
          source_aggregation_run_id uuid NOT NULL
            REFERENCES aggregation.aggregation_run,
          ensemble_spec_id uuid NULL
            REFERENCES aggregation.v022_trainable_ensemble_spec,
          activated_decision_session_id uuid NOT NULL
            REFERENCES product.v022_decision_schedule_session,
          state_version_number integer NOT NULL CHECK (state_version_number>=1),
          member_count integer NOT NULL CHECK (member_count BETWEEN 1 AND 12),
          state_document jsonb NOT NULL,
          state_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (state_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (execution_version_id,state_version_number),
          UNIQUE (execution_version_id,activated_decision_session_id),
          CHECK ((
            jsonb_typeof(state_document)='object' AND
            state_document->>'contract_version'='v0.22.product_ensemble_state.v1' AND
            state_document->>'member_policy'='complete_atomic_member_set_v1' AND
            state_document->>'failure_policy'='retain_previous_complete_state' AND
            (state_document->>'member_count')::integer=member_count AND
            jsonb_typeof(state_document->'members')='array' AND
            jsonb_array_length(state_document->'members')=member_count
          ) IS TRUE)
        );

        CREATE TABLE product.v022_product_ensemble_state_member (
          product_ensemble_state_id uuid NOT NULL
            REFERENCES product.v022_product_ensemble_state,
          ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 11),
          target_version_id uuid NOT NULL REFERENCES aggregation.target_version,
          training_preset_version_id uuid NOT NULL
            REFERENCES aggregation.training_preset_version,
          fitted_model_state_id uuid NOT NULL
            REFERENCES aggregation.v022_fitted_model_state,
          fitted_model_state_artifact_id uuid NOT NULL
            REFERENCES lineage.artifact,
          PRIMARY KEY (product_ensemble_state_id,ordinal),
          UNIQUE (
            product_ensemble_state_id,target_version_id,
            training_preset_version_id
          )
        );

        CREATE FUNCTION product.validate_v022_product_ensemble_state()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE execution_configuration uuid;
        DECLARE evidence_configuration uuid;
        DECLARE run_mode varchar;
        DECLARE run_spec uuid;
        DECLARE artifact_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT configuration_snapshot_id INTO execution_configuration
            FROM product.v022_execution_version
           WHERE execution_version_id=NEW.execution_version_id;
          SELECT configuration_snapshot_id INTO evidence_configuration
            FROM experiment.v022_result_evidence_snapshot
           WHERE result_evidence_snapshot_id=
                 NEW.source_result_evidence_snapshot_id;
          SELECT version.execution_mode,run.ensemble_spec_id
            INTO run_mode,run_spec
            FROM aggregation.aggregation_run run
            JOIN aggregation.aggregation_version version
              ON version.aggregation_version_id=run.aggregation_version_id
           WHERE run.aggregation_run_id=NEW.source_aggregation_run_id;
          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_product_ensemble_state' OR
             artifact_row.artifact_key IS DISTINCT FROM NEW.state_fingerprint OR
             artifact_row.version_number IS DISTINCT FROM 1 OR
             execution_configuration IS DISTINCT FROM
               NEW.configuration_snapshot_id OR
             evidence_configuration IS DISTINCT FROM
               NEW.configuration_snapshot_id OR
             run_mode IS DISTINCT FROM 'supervised' OR
             run_spec IS DISTINCT FROM NEW.ensemble_spec_id OR
             (NEW.member_count=1 AND NEW.ensemble_spec_id IS NOT NULL) OR
             (NEW.member_count>1 AND NEW.ensemble_spec_id IS NULL) OR
             NOT EXISTS (
               SELECT 1
                 FROM product.v022_decision_schedule_session session
                 JOIN product.v022_product_enrollment enrollment
                   ON enrollment.decision_schedule_version_id=
                      session.decision_schedule_version_id
                WHERE session.decision_session_id=
                      NEW.activated_decision_session_id
                  AND enrollment.execution_version_id=
                      NEW.execution_version_id
             ) THEN
            RAISE EXCEPTION 'Product Ensemble State identity is not exact';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_validate_v022_product_ensemble_state
          BEFORE INSERT ON product.v022_product_ensemble_state
          FOR EACH ROW EXECUTE FUNCTION
            product.validate_v022_product_ensemble_state();

        CREATE FUNCTION product.validate_v022_product_ensemble_state_member()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_artifact uuid;
        DECLARE artifact_status varchar;
        BEGIN
          SELECT state.artifact_id,artifact.status
            INTO expected_artifact,artifact_status
            FROM aggregation.v022_fitted_model_state state
            JOIN aggregation.v022_base_learner_spec spec
              ON spec.base_learner_spec_id=state.base_learner_spec_id
            JOIN lineage.artifact artifact ON artifact.artifact_id=state.artifact_id
           WHERE state.fitted_model_state_id=NEW.fitted_model_state_id
             AND spec.target_version_id=NEW.target_version_id
             AND spec.training_preset_version_id=
                 NEW.training_preset_version_id;
          IF expected_artifact IS DISTINCT FROM
               NEW.fitted_model_state_artifact_id OR
             artifact_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION
              'Product Ensemble member is not an exact published Model State';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_validate_v022_product_ensemble_state_member
          BEFORE INSERT ON product.v022_product_ensemble_state_member
          FOR EACH ROW EXECUTE FUNCTION
            product.validate_v022_product_ensemble_state_member();

        CREATE FUNCTION product.close_v022_product_ensemble_state()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_count integer;
        DECLARE actual_count integer;
        DECLARE max_ordinal integer;
        BEGIN
          SELECT member_count INTO expected_count
            FROM product.v022_product_ensemble_state
           WHERE product_ensemble_state_id=NEW.product_ensemble_state_id;
          SELECT count(*),max(ordinal) INTO actual_count,max_ordinal
            FROM product.v022_product_ensemble_state_member
           WHERE product_ensemble_state_id=NEW.product_ensemble_state_id;
          IF actual_count IS DISTINCT FROM expected_count OR
             max_ordinal IS DISTINCT FROM expected_count-1 THEN
            RAISE EXCEPTION
              'Product Ensemble State member closure is incomplete';
          END IF;
          RETURN NEW;
        END $$;

        CREATE CONSTRAINT TRIGGER trg_close_v022_product_ensemble_state
          AFTER INSERT ON product.v022_product_ensemble_state_member
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION
            product.close_v022_product_ensemble_state();

        CREATE TRIGGER trg_v022_product_ensemble_state_append_only
          BEFORE UPDATE OR DELETE ON product.v022_product_ensemble_state
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_product_ensemble_state_member_append_only
          BEFORE UPDATE OR DELETE
          ON product.v022_product_ensemble_state_member
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();

        CREATE FUNCTION product.validate_v022_product_decision_ensemble_state()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE active_state_artifact uuid;
        BEGIN
          SELECT decision.active_model_state_artifact_id
            INTO active_state_artifact
            FROM product.v022_product_decision decision
           WHERE decision.product_decision_id=NEW.product_decision_id;
          IF active_state_artifact IS NOT NULL AND NOT EXISTS (
            SELECT 1
              FROM product.v022_product_runtime_stage_input input
              JOIN product.v022_product_ensemble_state state
                ON state.artifact_id=input.input_artifact_id
             WHERE input.product_runtime_stage_id=NEW.aggregation_stage_id
               AND input.role='active_model_state'
               AND input.input_artifact_id=active_state_artifact
          ) THEN
            RAISE EXCEPTION
              'Product Decision active Ensemble State is not bound to Aggregation';
          ELSIF active_state_artifact IS NULL AND EXISTS (
            SELECT 1 FROM product.v022_product_runtime_stage_input input
             WHERE input.product_runtime_stage_id=NEW.aggregation_stage_id
               AND input.role='active_model_state'
          ) THEN
            RAISE EXCEPTION
              'Deterministic Product Decision cannot bind an Ensemble State';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_validate_v022_product_decision_ensemble_state
          BEFORE INSERT ON product.v022_product_decision_runtime_binding
          FOR EACH ROW EXECUTE FUNCTION
            product.validate_v022_product_decision_ensemble_state();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM product.v022_product_ensemble_state) THEN
            RAISE EXCEPTION
              'Cannot downgrade M128 with Product Ensemble States';
          END IF;
        END $$;
        DROP FUNCTION product.validate_v022_product_decision_ensemble_state() CASCADE;
        DROP TABLE product.v022_product_ensemble_state_member;
        DROP TABLE product.v022_product_ensemble_state;
        DROP FUNCTION product.close_v022_product_ensemble_state();
        DROP FUNCTION product.validate_v022_product_ensemble_state_member();
        DROP FUNCTION product.validate_v022_product_ensemble_state();
        """
    )
