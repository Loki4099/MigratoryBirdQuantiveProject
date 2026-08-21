# ruff: noqa: E501
"""Bind new Product Decisions to their exact Product runtime chain.

Revision ID: 20260817_113_v022_prod_decision
Revises: 20260817_112_v022_prod_stages
"""

from __future__ import annotations

from alembic import op

revision = "20260817_113_v022_prod_decision"
down_revision = "20260817_112_v022_prod_stages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE product.v022_product_decision_runtime_binding (
          product_decision_id uuid PRIMARY KEY
            REFERENCES product.v022_product_decision,
          product_input_snapshot_id uuid NOT NULL UNIQUE
            REFERENCES product.v022_product_input_snapshot,
          product_runtime_execution_id uuid NOT NULL UNIQUE
            REFERENCES product.v022_product_runtime_execution,
          aggregation_stage_id uuid NOT NULL UNIQUE
            REFERENCES product.v022_product_runtime_stage,
          strategy_stage_id uuid NOT NULL UNIQUE
            REFERENCES product.v022_product_runtime_stage,
          defense_stage_id uuid NULL UNIQUE
            REFERENCES product.v022_product_runtime_stage,
          merge_stage_id uuid NOT NULL UNIQUE
            REFERENCES product.v022_product_runtime_stage,
          binding_document jsonb NOT NULL CHECK (jsonb_typeof(binding_document)='object'),
          binding_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK ((
            binding_document->>'contract_version'=
              'v0.22.product_decision_runtime_binding.v1' AND
            binding_document->>'product_decision_id'=product_decision_id::text AND
            binding_document->>'product_input_snapshot_id'=
              product_input_snapshot_id::text AND
            binding_document->>'product_runtime_execution_id'=
              product_runtime_execution_id::text AND
            binding_document->>'aggregation_stage_id'=aggregation_stage_id::text AND
            binding_document->>'strategy_stage_id'=strategy_stage_id::text AND
            binding_document->>'merge_stage_id'=merge_stage_id::text AND
            (binding_document->>'defense_stage_id') IS NOT DISTINCT FROM
              defense_stage_id::text
          ) IS TRUE)
        );

        CREATE FUNCTION product.validate_v022_product_decision_runtime_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE decision_row record; snapshot_row record; execution_row record;
                configuration_id uuid; aggregation_row record; strategy_row record;
                defense_row record; merge_row record; expected_dependency_count integer;
                actual_dependency_count integer;
        BEGIN
          SELECT decision.*,artifact.artifact_type AS decision_artifact_type
            INTO decision_row FROM product.v022_product_decision decision
            JOIN lineage.artifact artifact ON artifact.artifact_id=decision.artifact_id
           WHERE decision.product_decision_id=NEW.product_decision_id;
          SELECT snapshot.*,artifact.artifact_id AS snapshot_artifact_id,
                 artifact.status AS snapshot_status
            INTO snapshot_row FROM product.v022_product_input_snapshot snapshot
            JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id
           WHERE snapshot.product_input_snapshot_id=NEW.product_input_snapshot_id;
          SELECT execution.*,artifact.artifact_id AS execution_artifact_id,
                 artifact.status AS execution_status
            INTO execution_row FROM product.v022_product_runtime_execution execution
            JOIN lineage.artifact artifact ON artifact.artifact_id=execution.artifact_id
           WHERE execution.product_runtime_execution_id=NEW.product_runtime_execution_id;
          SELECT configuration_snapshot_id INTO configuration_id
            FROM product.v022_execution_version
           WHERE execution_version_id=decision_row.execution_version_id;
          SELECT * INTO aggregation_row FROM product.v022_product_runtime_stage
           WHERE product_runtime_stage_id=NEW.aggregation_stage_id;
          SELECT * INTO strategy_row FROM product.v022_product_runtime_stage
           WHERE product_runtime_stage_id=NEW.strategy_stage_id;
          SELECT * INTO defense_row FROM product.v022_product_runtime_stage
           WHERE product_runtime_stage_id=NEW.defense_stage_id;
          SELECT * INTO merge_row FROM product.v022_product_runtime_stage
           WHERE product_runtime_stage_id=NEW.merge_stage_id;
          IF decision_row.decision_status IS DISTINCT FROM 'completed' OR
             snapshot_row.snapshot_status IS DISTINCT FROM 'published' OR
             snapshot_row.product_enrollment_id IS DISTINCT FROM
               decision_row.product_enrollment_id OR
             snapshot_row.execution_version_id IS DISTINCT FROM
               decision_row.execution_version_id OR
             snapshot_row.decision_session_id IS DISTINCT FROM
               decision_row.decision_session_id OR
             execution_row.execution_status IS DISTINCT FROM 'published' OR
             execution_row.product_input_snapshot_id IS DISTINCT FROM
               NEW.product_input_snapshot_id OR
             execution_row.configuration_snapshot_id IS DISTINCT FROM configuration_id OR
             execution_row.decision_session_id IS DISTINCT FROM
               decision_row.decision_session_id OR
             aggregation_row.product_runtime_execution_id IS DISTINCT FROM
               NEW.product_runtime_execution_id OR
             aggregation_row.stage_kind IS DISTINCT FROM 'aggregation' OR
             aggregation_row.artifact_id IS DISTINCT FROM
               decision_row.aggregation_run_artifact_id OR
             strategy_row.product_runtime_execution_id IS DISTINCT FROM
               NEW.product_runtime_execution_id OR
             strategy_row.stage_kind IS DISTINCT FROM 'strategy' OR
             strategy_row.artifact_id IS DISTINCT FROM
               decision_row.strategy_target_artifact_id OR
             merge_row.product_runtime_execution_id IS DISTINCT FROM
               NEW.product_runtime_execution_id OR
             merge_row.stage_kind IS DISTINCT FROM 'merge' OR
             merge_row.artifact_id IS DISTINCT FROM
               decision_row.merged_target_artifact_id OR
             ((NEW.defense_stage_id IS NULL) IS DISTINCT FROM
               (decision_row.defense_decision_artifact_id IS NULL)) OR
             (NEW.defense_stage_id IS NOT NULL AND (
               defense_row.product_runtime_execution_id IS DISTINCT FROM
                 NEW.product_runtime_execution_id OR
               defense_row.stage_kind IS DISTINCT FROM 'defense' OR
               defense_row.artifact_id IS DISTINCT FROM
                 decision_row.defense_decision_artifact_id
             )) OR
             NOT EXISTS (
               SELECT 1 FROM product.v022_product_runtime_stage_input input
                WHERE input.product_runtime_stage_id=NEW.aggregation_stage_id
                  AND input.role='processing_manifest'
                  AND input.input_artifact_id=decision_row.input_manifest_artifact_id
             ) THEN
            RAISE EXCEPTION 'Product Decision exact Product Runtime binding invalid';
          END IF;
          expected_dependency_count := 7 +
            CASE WHEN NEW.defense_stage_id IS NULL THEN 0 ELSE 1 END +
            CASE WHEN decision_row.active_model_state_artifact_id IS NULL THEN 0 ELSE 1 END;
          SELECT count(*) INTO actual_dependency_count
            FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=decision_row.artifact_id;
          IF actual_dependency_count<>expected_dependency_count OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=decision_row.artifact_id
                 AND dependency.depends_on_artifact_id=snapshot_row.snapshot_artifact_id
                 AND dependency.role='product_input_snapshot') OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=decision_row.artifact_id
                 AND dependency.depends_on_artifact_id=execution_row.execution_artifact_id
                 AND dependency.role='product_runtime_execution') THEN
            RAISE EXCEPTION 'Product Decision exact Product Runtime lineage invalid';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_product_decision_runtime_binding_validate
          BEFORE INSERT ON product.v022_product_decision_runtime_binding
          FOR EACH ROW EXECUTE FUNCTION
            product.validate_v022_product_decision_runtime_binding();
        CREATE TRIGGER trg_v022_product_decision_runtime_binding_append_only
          BEFORE UPDATE OR DELETE ON product.v022_product_decision_runtime_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();

        CREATE FUNCTION product.validate_v022_product_decision_new_runtime_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE aggregation_type varchar;
        BEGIN
          SELECT artifact_type INTO aggregation_type FROM lineage.artifact
           WHERE artifact_id=NEW.aggregation_run_artifact_id;
          IF NEW.decision_status='completed' AND
             aggregation_type='v022_product_aggregation_output' AND NOT EXISTS (
               SELECT 1 FROM product.v022_product_decision_runtime_binding binding
                WHERE binding.product_decision_id=NEW.product_decision_id
             ) THEN
            RAISE EXCEPTION 'New Product Runtime Decision requires exact runtime binding';
          END IF;
          RETURN NEW;
        END $$;

        CREATE CONSTRAINT TRIGGER trg_v022_product_decision_new_runtime_complete
          AFTER INSERT ON product.v022_product_decision
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION product.validate_v022_product_decision_new_runtime_complete();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM product.v022_product_decision_runtime_binding) THEN
            RAISE EXCEPTION 'Cannot downgrade nonempty Product Decision Runtime bindings';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS
          product.validate_v022_product_decision_new_runtime_complete() CASCADE;
        DROP FUNCTION IF EXISTS
          product.validate_v022_product_decision_runtime_binding() CASCADE;
        DROP TABLE product.v022_product_decision_runtime_binding;
        """
    )
