# ruff: noqa: E501
"""Add formal Shadow Comparator and v0.21 reference decision identity.

Revision ID: 20260811_71_v022_comparator
Revises: 20260811_70_v022_dual_run
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_71_v022_comparator"
down_revision: str | None = "20260811_70_v022_dual_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_shadow_comparator_version (
          shadow_comparator_version_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          comparator_key varchar(160) NOT NULL,
          version_number integer NOT NULL CHECK (version_number >= 1),
          algorithm_key varchar(120) NOT NULL CHECK (
            algorithm_key IN ('canonical_projection_equal_v1')
          ),
          policy_document jsonb NOT NULL,
          comparator_fingerprint varchar(64) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (comparator_key,version_number),
          CHECK (btrim(comparator_key) <> ''),
          CHECK (jsonb_typeof(policy_document)='object' AND policy_document<>'{}'::jsonb),
          CHECK (comparator_fingerprint ~ '^[0-9a-f]{64}$')
        );

        CREATE TABLE workspace.v022_shadow_v021_reference_decision (
          shadow_v021_reference_decision_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          shadow_runtime_binding_id uuid NOT NULL
            REFERENCES workspace.v022_shadow_runtime_binding,
          shadow_representative_id uuid NOT NULL
            REFERENCES workspace.v022_shadow_representative,
          decision_session_id uuid NOT NULL REFERENCES product.v022_decision_schedule_session,
          decision_document jsonb NOT NULL,
          known_at timestamptz NOT NULL,
          reference_fingerprint varchar(64) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (shadow_representative_id,decision_session_id),
          CHECK (jsonb_typeof(decision_document)='object' AND decision_document<>'{}'::jsonb),
          CHECK (reference_fingerprint ~ '^[0-9a-f]{64}$')
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION workspace.validate_v022_shadow_comparator_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type INTO artifact_row FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          IF artifact_row.artifact_type IS DISTINCT FROM 'v022_shadow_comparator_version' THEN
            RAISE EXCEPTION 'Shadow Comparator row requires its formal Artifact type';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_comparator_version_validate
          BEFORE INSERT ON workspace.v022_shadow_comparator_version
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_shadow_comparator_version();

        CREATE FUNCTION workspace.validate_v022_shadow_v021_reference_decision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_type_value varchar; binding_row record; representative_row record;
                session_row record; first_ordinal integer;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type INTO artifact_type_value FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
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
          IF artifact_type_value IS DISTINCT FROM 'v021_shadow_reference_decision' OR
             binding_row.status IS DISTINCT FROM 'published' OR
             binding_row.shadow_representative_id IS DISTINCT FROM NEW.shadow_representative_id OR
             session_row.decision_schedule_version_id IS DISTINCT FROM
               representative_row.decision_schedule_version_id OR
             session_row.ordinal<representative_row.first_ordinal OR
             NEW.known_at<session_row.decision_cutoff_at THEN
            RAISE EXCEPTION 'v0.21 Shadow Reference violates exact Binding or Session identity';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_v021_reference_validate
          BEFORE INSERT ON workspace.v022_shadow_v021_reference_decision
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_shadow_v021_reference_decision();

        CREATE FUNCTION workspace.validate_v022_shadow_runtime_binding_comparator()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE comparator_status varchar; comparator_exists boolean;
        BEGIN
          SELECT artifact.status,comparator.shadow_comparator_version_id IS NOT NULL
            INTO comparator_status,comparator_exists
            FROM lineage.artifact artifact
            LEFT JOIN workspace.v022_shadow_comparator_version comparator
              ON comparator.artifact_id=artifact.artifact_id
           WHERE artifact.artifact_id=NEW.comparator_artifact_id;
          IF comparator_status IS DISTINCT FROM 'published' OR
             NOT coalesce(comparator_exists,false) THEN
            RAISE EXCEPTION 'Shadow Runtime Binding requires a formal Comparator Version';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_runtime_binding_comparator
          BEFORE INSERT ON workspace.v022_shadow_runtime_binding
          FOR EACH ROW EXECUTE FUNCTION
            workspace.validate_v022_shadow_runtime_binding_comparator();

        CREATE FUNCTION ops.validate_v022_shadow_work_reference()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE reference_row record;
        BEGIN
          IF NEW.status='completed' AND NEW.runtime_contract='v0.21' THEN
            SELECT reference.shadow_runtime_binding_id,reference.decision_session_id,
                   intent.shadow_runtime_binding_id AS expected_binding_id,
                   intent.decision_session_id AS expected_session_id,artifact.status
              INTO reference_row
              FROM workspace.v022_shadow_v021_reference_decision reference
              JOIN lineage.artifact artifact ON artifact.artifact_id=reference.artifact_id
              JOIN workspace.v022_shadow_dual_run_intent intent
                ON intent.shadow_dual_run_intent_id=NEW.shadow_dual_run_intent_id
             WHERE reference.artifact_id=NEW.v021_reference_artifact_id;
            IF reference_row.status IS DISTINCT FROM 'published' OR
               reference_row.shadow_runtime_binding_id IS DISTINCT FROM
                 reference_row.expected_binding_id OR
               reference_row.decision_session_id IS DISTINCT FROM
                 reference_row.expected_session_id THEN
              RAISE EXCEPTION 'v0.21 Shadow completion requires its exact Reference Decision';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_work_reference
          BEFORE INSERT OR UPDATE ON ops.v022_shadow_work_item
          FOR EACH ROW EXECUTE FUNCTION ops.validate_v022_shadow_work_reference();

        CREATE TRIGGER trg_v022_shadow_comparator_version_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_shadow_comparator_version
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_shadow_v021_reference_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_shadow_v021_reference_decision
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS ops.validate_v022_shadow_work_reference() CASCADE")
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "workspace.validate_v022_shadow_runtime_binding_comparator() CASCADE"
    )
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_shadow_v021_reference_decision() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_shadow_comparator_version() CASCADE")
    op.drop_table("v022_shadow_v021_reference_decision", schema="workspace")
    op.drop_table("v022_shadow_comparator_version", schema="workspace")
