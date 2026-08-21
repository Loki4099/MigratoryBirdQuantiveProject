# ruff: noqa: E501
"""Add exact-capability v0.21/v0.22 Shadow dual-run scheduling.

Revision ID: 20260811_70_v022_dual_run
Revises: 20260811_69_v022_coverage
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_70_v022_dual_run"
down_revision: str | None = "20260811_69_v022_coverage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_shadow_runtime_binding (
          shadow_runtime_binding_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          shadow_representative_id uuid NOT NULL UNIQUE
            REFERENCES workspace.v022_shadow_representative,
          v021_product_enrollment_id uuid NULL REFERENCES product.product_enrollment,
          v021_execution_spec_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          comparator_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          v021_compiler_version varchar(120) NOT NULL,
          v021_executor_version varchar(120) NOT NULL,
          v021_environment_fingerprint varchar(64) NOT NULL,
          v021_capability_key varchar(160) NOT NULL,
          v022_compiler_version varchar(120) NOT NULL,
          v022_executor_version varchar(120) NOT NULL,
          v022_environment_fingerprint varchar(64) NOT NULL,
          v022_capability_key varchar(160) NOT NULL,
          binding_fingerprint varchar(64) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (btrim(v021_compiler_version) <> '' AND btrim(v021_executor_version) <> ''),
          CHECK (btrim(v021_capability_key) <> '' AND btrim(v022_capability_key) <> ''),
          CHECK (btrim(v022_compiler_version) <> '' AND btrim(v022_executor_version) <> ''),
          CHECK (v021_environment_fingerprint ~ '^[0-9a-f]{64}$'),
          CHECK (v022_environment_fingerprint ~ '^[0-9a-f]{64}$'),
          CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$')
        );

        CREATE TABLE workspace.v022_shadow_dual_run_intent (
          shadow_dual_run_intent_id uuid PRIMARY KEY,
          shadow_runtime_binding_id uuid NOT NULL
            REFERENCES workspace.v022_shadow_runtime_binding,
          shadow_representative_id uuid NOT NULL
            REFERENCES workspace.v022_shadow_representative,
          decision_session_id uuid NOT NULL REFERENCES product.v022_decision_schedule_session,
          decision_cutoff_at timestamptz NOT NULL,
          intent_fingerprint varchar(64) NOT NULL UNIQUE,
          scheduled_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (shadow_representative_id,decision_session_id),
          CHECK (intent_fingerprint ~ '^[0-9a-f]{64}$'),
          CHECK (scheduled_at >= decision_cutoff_at)
        );

        CREATE TABLE ops.v022_worker_capability_lease (
          worker_id varchar(160) NOT NULL,
          service_principal varchar(160) NOT NULL,
          runtime_contract varchar(16) NOT NULL CHECK (runtime_contract IN ('v0.21','v0.22')),
          compiler_version varchar(120) NOT NULL,
          executor_version varchar(120) NOT NULL,
          environment_fingerprint varchar(64) NOT NULL,
          capability_key varchar(160) NOT NULL,
          registered_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          PRIMARY KEY (worker_id,runtime_contract,compiler_version,executor_version,
                       environment_fingerprint,capability_key),
          CHECK (btrim(worker_id) <> '' AND btrim(service_principal) <> ''),
          CHECK (btrim(compiler_version) <> '' AND btrim(executor_version) <> ''),
          CHECK (btrim(capability_key) <> ''),
          CHECK (environment_fingerprint ~ '^[0-9a-f]{64}$'),
          CHECK (expires_at > registered_at)
        );

        CREATE TABLE ops.v022_shadow_work_item (
          shadow_work_item_id uuid PRIMARY KEY,
          shadow_dual_run_intent_id uuid NOT NULL
            REFERENCES workspace.v022_shadow_dual_run_intent,
          runtime_contract varchar(16) NOT NULL CHECK (runtime_contract IN ('v0.21','v0.22')),
          compiler_version varchar(120) NOT NULL,
          executor_version varchar(120) NOT NULL,
          environment_fingerprint varchar(64) NOT NULL,
          capability_key varchar(160) NOT NULL,
          work_fingerprint varchar(64) NOT NULL UNIQUE,
          status varchar(16) NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued','running','completed','failed','cancelled')),
          attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
          max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
          lease_owner varchar(160) NULL,
          lease_service_principal varchar(160) NULL,
          lease_expires_at timestamptz NULL,
          fencing_token bigint NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
          v021_reference_artifact_id uuid NULL REFERENCES lineage.artifact,
          v022_product_decision_id uuid NULL REFERENCES product.v022_product_decision,
          failure_document jsonb NOT NULL DEFAULT '{}'::jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (shadow_dual_run_intent_id,runtime_contract),
          CHECK (environment_fingerprint ~ '^[0-9a-f]{64}$'),
          CHECK (work_fingerprint ~ '^[0-9a-f]{64}$'),
          CHECK (
            (status='running' AND lease_owner IS NOT NULL AND
             lease_service_principal IS NOT NULL AND lease_expires_at IS NOT NULL)
            OR
            (status<>'running' AND lease_owner IS NULL AND
             lease_service_principal IS NULL AND lease_expires_at IS NULL)
          ),
          CHECK (
            (status='completed' AND runtime_contract='v0.21' AND
             v021_reference_artifact_id IS NOT NULL AND v022_product_decision_id IS NULL)
            OR
            (status='completed' AND runtime_contract='v0.22' AND
             v021_reference_artifact_id IS NULL AND v022_product_decision_id IS NOT NULL)
            OR
            (status<>'completed' AND v021_reference_artifact_id IS NULL AND
             v022_product_decision_id IS NULL)
          )
        );
        CREATE INDEX ix_v022_shadow_work_claim ON ops.v022_shadow_work_item
          (status,created_at) WHERE status IN ('queued','running');
        """
    )
    op.execute(
        """
        CREATE FUNCTION workspace.validate_v022_shadow_runtime_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE representative_row record; plan_status varchar; v021_status varchar;
                comparator_status varchar; enrollment_lifecycle varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT representative.*,artifact.status INTO representative_row
            FROM workspace.v022_shadow_representative representative
            JOIN workspace.v022_shadow_plan plan
              ON plan.shadow_plan_id=representative.shadow_plan_id
            JOIN lineage.artifact artifact ON artifact.artifact_id=plan.artifact_id
           WHERE representative.shadow_representative_id=NEW.shadow_representative_id;
          SELECT status INTO v021_status FROM lineage.artifact
           WHERE artifact_id=NEW.v021_execution_spec_artifact_id;
          SELECT status INTO comparator_status FROM lineage.artifact
           WHERE artifact_id=NEW.comparator_artifact_id;
          IF representative_row.status IS DISTINCT FROM 'published' OR
             v021_status IS DISTINCT FROM 'published' OR
             comparator_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Shadow Runtime Binding requires published Plan and runtime Artifacts';
          END IF;
          IF representative_row.representative_role='active_product_shadow' THEN
            IF NEW.v021_product_enrollment_id IS NULL THEN
              RAISE EXCEPTION 'Active Product Shadow requires a v0.21 Product Enrollment';
            END IF;
            SELECT lifecycle INTO enrollment_lifecycle FROM product.product_enrollment
             WHERE product_enrollment_id=NEW.v021_product_enrollment_id;
            IF enrollment_lifecycle IS DISTINCT FROM 'active' THEN
              RAISE EXCEPTION 'Active Product Shadow requires an active v0.21 Enrollment';
            END IF;
          ELSIF NEW.v021_product_enrollment_id IS NOT NULL THEN
            RAISE EXCEPTION 'Shadow-only representative cannot claim a formal v0.21 Enrollment';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_runtime_binding_validate
          BEFORE INSERT ON workspace.v022_shadow_runtime_binding
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_shadow_runtime_binding();

        CREATE FUNCTION workspace.validate_v022_shadow_dual_run_intent()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE binding_row record; representative_row record; session_row record;
                first_ordinal integer;
        BEGIN
          SELECT binding.*,artifact.status INTO binding_row
            FROM workspace.v022_shadow_runtime_binding binding
            JOIN lineage.artifact artifact ON artifact.artifact_id=binding.artifact_id
           WHERE binding.shadow_runtime_binding_id=NEW.shadow_runtime_binding_id;
          SELECT representative.*,enrollment.decision_schedule_version_id,
                 first_session.ordinal AS first_ordinal INTO representative_row
            FROM workspace.v022_shadow_representative representative
            JOIN product.v022_product_enrollment enrollment
              ON enrollment.product_enrollment_id=representative.product_enrollment_id
            JOIN product.v022_decision_schedule_session first_session
              ON first_session.decision_session_id=enrollment.first_eligible_decision_session_id
           WHERE representative.shadow_representative_id=NEW.shadow_representative_id;
          SELECT * INTO session_row FROM product.v022_decision_schedule_session
           WHERE decision_session_id=NEW.decision_session_id;
          IF binding_row.status IS DISTINCT FROM 'published' OR
             binding_row.shadow_representative_id IS DISTINCT FROM NEW.shadow_representative_id OR
             session_row.decision_schedule_version_id IS DISTINCT FROM
               representative_row.decision_schedule_version_id OR
             session_row.ordinal<representative_row.first_ordinal OR
             session_row.decision_cutoff_at IS DISTINCT FROM NEW.decision_cutoff_at THEN
            RAISE EXCEPTION 'Shadow dual-run intent violates its frozen Binding or Schedule';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_dual_run_intent_validate
          BEFORE INSERT ON workspace.v022_shadow_dual_run_intent
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_shadow_dual_run_intent();

        CREATE FUNCTION ops.validate_v022_shadow_work_item()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE binding_row record; decision_row record; reference_status varchar;
        BEGIN
          SELECT binding.* INTO binding_row
            FROM workspace.v022_shadow_dual_run_intent intent
            JOIN workspace.v022_shadow_runtime_binding binding
              ON binding.shadow_runtime_binding_id=intent.shadow_runtime_binding_id
           WHERE intent.shadow_dual_run_intent_id=NEW.shadow_dual_run_intent_id;
          IF NEW.runtime_contract='v0.21' AND (
               NEW.compiler_version IS DISTINCT FROM binding_row.v021_compiler_version OR
               NEW.executor_version IS DISTINCT FROM binding_row.v021_executor_version OR
               NEW.environment_fingerprint IS DISTINCT FROM
                 binding_row.v021_environment_fingerprint OR
               NEW.capability_key IS DISTINCT FROM binding_row.v021_capability_key) THEN
            RAISE EXCEPTION 'v0.21 Shadow work does not match its frozen capability';
          END IF;
          IF NEW.runtime_contract='v0.22' AND (
               NEW.compiler_version IS DISTINCT FROM binding_row.v022_compiler_version OR
               NEW.executor_version IS DISTINCT FROM binding_row.v022_executor_version OR
               NEW.environment_fingerprint IS DISTINCT FROM
                 binding_row.v022_environment_fingerprint OR
               NEW.capability_key IS DISTINCT FROM binding_row.v022_capability_key) THEN
            RAISE EXCEPTION 'v0.22 Shadow work does not match its frozen capability';
          END IF;
          IF NEW.status='completed' AND NEW.runtime_contract='v0.21' THEN
            SELECT status INTO reference_status FROM lineage.artifact
             WHERE artifact_id=NEW.v021_reference_artifact_id;
            IF reference_status IS DISTINCT FROM 'published' THEN
              RAISE EXCEPTION 'v0.21 Shadow completion requires a published reference Artifact';
            END IF;
          ELSIF NEW.status='completed' AND NEW.runtime_contract='v0.22' THEN
            SELECT decision.decision_session_id AS actual_session_id,
                   intent.decision_session_id AS expected_session_id,
                   decision.product_enrollment_id AS actual_enrollment_id,
                   representative.product_enrollment_id AS expected_enrollment_id
              INTO decision_row
              FROM product.v022_product_decision decision
              JOIN workspace.v022_shadow_dual_run_intent intent
                ON intent.shadow_dual_run_intent_id=NEW.shadow_dual_run_intent_id
              JOIN workspace.v022_shadow_runtime_binding binding
                ON binding.shadow_runtime_binding_id=intent.shadow_runtime_binding_id
              JOIN workspace.v022_shadow_representative representative
                ON representative.shadow_representative_id=binding.shadow_representative_id
             WHERE decision.product_decision_id=NEW.v022_product_decision_id;
            IF decision_row.actual_session_id IS DISTINCT FROM decision_row.expected_session_id OR
               decision_row.actual_enrollment_id IS DISTINCT FROM
                 decision_row.expected_enrollment_id THEN
              RAISE EXCEPTION 'v0.22 Shadow completion Decision violates its exact identity';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_work_item_validate
          BEFORE INSERT OR UPDATE ON ops.v022_shadow_work_item
          FOR EACH ROW EXECUTE FUNCTION ops.validate_v022_shadow_work_item();

        CREATE TRIGGER trg_v022_shadow_runtime_binding_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_shadow_runtime_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_shadow_dual_run_intent_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_shadow_dual_run_intent
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS ops.validate_v022_shadow_work_item() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_shadow_dual_run_intent() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_shadow_runtime_binding() CASCADE")
    op.drop_index("ix_v022_shadow_work_claim", table_name="v022_shadow_work_item", schema="ops")
    op.drop_table("v022_shadow_work_item", schema="ops")
    op.drop_table("v022_worker_capability_lease", schema="ops")
    op.drop_table("v022_shadow_dual_run_intent", schema="workspace")
    op.drop_table("v022_shadow_runtime_binding", schema="workspace")
