# ruff: noqa: E501
"""Add v0.22 Decision Schedule, Enrollment, and Product Decision identity.

Revision ID: 20260811_65_v022_product_run
Revises: 20260811_64_v022_product
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_65_v022_product_run"
down_revision: str | None = "20260811_64_v022_product"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE product.v022_decision_schedule_version (
          decision_schedule_version_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          schedule_key varchar(160) NOT NULL,
          version_number integer NOT NULL CHECK (version_number >= 1),
          frequency varchar(16) NOT NULL CHECK (frequency IN ('weekly','monthly')),
          schedule_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (schedule_fingerprint ~ '^[0-9a-f]{64}$'),
          session_count integer NOT NULL CHECK (session_count > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (schedule_key,version_number)
        );
        CREATE TABLE product.v022_decision_schedule_session (
          decision_session_id uuid PRIMARY KEY,
          decision_schedule_version_id uuid NOT NULL
            REFERENCES product.v022_decision_schedule_version,
          ordinal integer NOT NULL CHECK (ordinal >= 1),
          session_date date NOT NULL,
          decision_cutoff_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (decision_schedule_version_id,ordinal),
          UNIQUE (decision_schedule_version_id,session_date)
        );
        CREATE TABLE product.v022_product_enrollment (
          product_enrollment_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          execution_version_id uuid NOT NULL UNIQUE REFERENCES product.v022_execution_version,
          qualification_version_id uuid NOT NULL REFERENCES product.v022_qualification_version,
          monitoring_policy_version_id uuid NOT NULL
            REFERENCES product.v022_monitoring_policy_version,
          decision_schedule_version_id uuid NOT NULL
            REFERENCES product.v022_decision_schedule_version,
          oos_anchor_cutoff_at timestamptz NOT NULL,
          activation_effective_at timestamptz NOT NULL,
          first_eligible_decision_session_id uuid NOT NULL
            REFERENCES product.v022_decision_schedule_session,
          enrollment_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (enrollment_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE product.v022_product_decision (
          product_decision_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          product_enrollment_id uuid NOT NULL REFERENCES product.v022_product_enrollment,
          execution_version_id uuid NOT NULL REFERENCES product.v022_execution_version,
          decision_session_id uuid NOT NULL REFERENCES product.v022_decision_schedule_session,
          decision_status varchar(16) NOT NULL CHECK (decision_status IN ('completed','missing')),
          evidence_class varchar(32) NOT NULL CHECK (
            evidence_class IN ('qualification_bridge','historical_backfill','prospective_oos')
          ),
          oos_eligible boolean NOT NULL,
          input_manifest_artifact_id uuid NULL REFERENCES lineage.artifact,
          active_model_state_artifact_id uuid NULL REFERENCES lineage.artifact,
          aggregation_run_artifact_id uuid NULL REFERENCES lineage.artifact,
          strategy_target_artifact_id uuid NULL REFERENCES lineage.artifact,
          defense_decision_artifact_id uuid NULL REFERENCES lineage.artifact,
          merged_target_artifact_id uuid NULL REFERENCES lineage.artifact,
          decision_document jsonb NOT NULL,
          quality_document jsonb NOT NULL,
          reason_codes jsonb NOT NULL,
          decision_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (decision_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (execution_version_id,decision_session_id),
          CHECK (jsonb_typeof(decision_document)='object' AND decision_document <> '{}'::jsonb),
          CHECK (jsonb_typeof(quality_document)='object' AND quality_document <> '{}'::jsonb),
          CHECK (
            (decision_status='completed' AND input_manifest_artifact_id IS NOT NULL
              AND aggregation_run_artifact_id IS NOT NULL AND strategy_target_artifact_id IS NOT NULL
              AND defense_decision_artifact_id IS NOT NULL AND merged_target_artifact_id IS NOT NULL
              AND jsonb_array_length(reason_codes)=0)
            OR
            (decision_status='missing' AND input_manifest_artifact_id IS NULL
              AND active_model_state_artifact_id IS NULL AND aggregation_run_artifact_id IS NULL
              AND strategy_target_artifact_id IS NULL AND defense_decision_artifact_id IS NULL
              AND merged_target_artifact_id IS NULL AND jsonb_array_length(reason_codes)>0)
          )
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION product.validate_v022_decision_schedule_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_decision_schedule_version_validate
          BEFORE INSERT ON product.v022_decision_schedule_version
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_decision_schedule_version();

        CREATE FUNCTION product.validate_v022_decision_schedule_session()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE schedule_artifact uuid;
        BEGIN
          SELECT artifact_id INTO schedule_artifact
            FROM product.v022_decision_schedule_version
           WHERE decision_schedule_version_id=NEW.decision_schedule_version_id;
          PERFORM data.assert_artifact_draft(schedule_artifact);
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_decision_schedule_session_validate
          BEFORE INSERT ON product.v022_decision_schedule_session
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_decision_schedule_session();

        CREATE FUNCTION product.validate_v022_decision_schedule_completeness()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_count integer; minimum_ordinal integer; maximum_ordinal integer;
                ordering_valid boolean;
        BEGIN
          SELECT count(*),min(ordinal),max(ordinal) INTO actual_count,minimum_ordinal,maximum_ordinal
            FROM product.v022_decision_schedule_session
           WHERE decision_schedule_version_id=NEW.decision_schedule_version_id;
          SELECT coalesce(bool_and(previous_date < session_date AND previous_cutoff < decision_cutoff_at),true)
            INTO ordering_valid FROM (
              SELECT ordinal,session_date,decision_cutoff_at,
                     lag(session_date) OVER (ORDER BY ordinal) AS previous_date,
                     lag(decision_cutoff_at) OVER (ORDER BY ordinal) AS previous_cutoff
                FROM product.v022_decision_schedule_session
               WHERE decision_schedule_version_id=NEW.decision_schedule_version_id
            ) ordered WHERE ordinal > 1;
          IF actual_count <> NEW.session_count OR minimum_ordinal <> 1 OR
             maximum_ordinal <> NEW.session_count OR NOT ordering_valid THEN
            RAISE EXCEPTION 'Decision Schedule sessions are incomplete or noncanonical';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_decision_schedule_complete
          AFTER INSERT ON product.v022_decision_schedule_version
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION product.validate_v022_decision_schedule_completeness();

        CREATE FUNCTION product.validate_v022_product_enrollment()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE execution_row record; qualification_row record; monitoring_row record;
                schedule_row record; first_row record; configuration_frequency varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT execution.*,artifact.status,configuration.semantic_identity_document
            INTO execution_row FROM product.v022_execution_version execution
            JOIN lineage.artifact artifact ON artifact.artifact_id=execution.artifact_id
            JOIN experiment.v022_research_configuration_snapshot configuration
              ON configuration.configuration_snapshot_id=execution.configuration_snapshot_id
           WHERE execution.execution_version_id=NEW.execution_version_id;
          SELECT qualification.*,artifact.status INTO qualification_row
            FROM product.v022_qualification_version qualification
            JOIN lineage.artifact artifact ON artifact.artifact_id=qualification.artifact_id
           WHERE qualification.qualification_version_id=NEW.qualification_version_id;
          SELECT monitoring.*,artifact.status INTO monitoring_row
            FROM product.v022_monitoring_policy_version monitoring
            JOIN lineage.artifact artifact ON artifact.artifact_id=monitoring.artifact_id
           WHERE monitoring.monitoring_policy_version_id=NEW.monitoring_policy_version_id;
          SELECT schedule.*,artifact.status INTO schedule_row
            FROM product.v022_decision_schedule_version schedule
            JOIN lineage.artifact artifact ON artifact.artifact_id=schedule.artifact_id
           WHERE schedule.decision_schedule_version_id=NEW.decision_schedule_version_id;
          SELECT * INTO first_row FROM product.v022_decision_schedule_session
           WHERE decision_session_id=NEW.first_eligible_decision_session_id;
          IF execution_row.status <> 'published' OR qualification_row.status <> 'published' OR
             monitoring_row.status <> 'published' OR schedule_row.status <> 'published' THEN
            RAISE EXCEPTION 'Enrollment requires published Execution, Qualification, Monitoring, and Schedule';
          END IF;
          IF qualification_row.execution_version_id <> NEW.execution_version_id OR
             qualification_row.product_definition_id <> execution_row.product_definition_id OR
             monitoring_row.product_definition_id <> execution_row.product_definition_id THEN
            RAISE EXCEPTION 'Enrollment versions must belong to the exact Product Execution';
          END IF;
          IF first_row.decision_schedule_version_id <> NEW.decision_schedule_version_id OR
             first_row.decision_cutoff_at <= greatest(NEW.oos_anchor_cutoff_at,NEW.activation_effective_at) THEN
            RAISE EXCEPTION 'Enrollment first eligible session violates its frozen OOS anchor';
          END IF;
          configuration_frequency := execution_row.semantic_identity_document->>'frequency';
          IF schedule_row.frequency <> configuration_frequency THEN
            RAISE EXCEPTION 'Enrollment Schedule frequency must match Execution Configuration';
          END IF;
          IF EXISTS (
            SELECT 1 FROM product.v022_decision_schedule_session earlier
             WHERE earlier.decision_schedule_version_id=NEW.decision_schedule_version_id
               AND earlier.ordinal < first_row.ordinal
               AND earlier.decision_cutoff_at > greatest(NEW.oos_anchor_cutoff_at,NEW.activation_effective_at)
          ) THEN
            RAISE EXCEPTION 'Enrollment must use the earliest eligible Decision Session';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_product_enrollment_validate
          BEFORE INSERT ON product.v022_product_enrollment
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_product_enrollment();

        CREATE FUNCTION product.validate_v022_product_decision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE enrollment_row record; session_row record; execution_mode varchar;
                expected_oos boolean; dependency_count integer;
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
             session_row.decision_schedule_version_id <> enrollment_row.decision_schedule_version_id THEN
            RAISE EXCEPTION 'Product Decision must bind its exact Enrollment Execution and Schedule';
          END IF;
          expected_oos := NEW.evidence_class='prospective_oos' AND session_row.ordinal >= (
            SELECT ordinal FROM product.v022_decision_schedule_session
             WHERE decision_session_id=enrollment_row.first_eligible_decision_session_id
          );
          IF NEW.oos_eligible IS DISTINCT FROM expected_oos THEN
            RAISE EXCEPTION 'Product Decision OOS eligibility is not canonical';
          END IF;
          SELECT configuration.semantic_identity_document #>> '{aggregation,execution_mode}'
            INTO execution_mode
            FROM product.v022_execution_version execution
            JOIN experiment.v022_research_configuration_snapshot configuration
              ON configuration.configuration_snapshot_id=execution.configuration_snapshot_id
           WHERE execution.execution_version_id=NEW.execution_version_id;
          IF execution_mode='deterministic' AND NEW.active_model_state_artifact_id IS NOT NULL THEN
            RAISE EXCEPTION 'Deterministic Product Decision must have NULL active Model State';
          END IF;
          IF NEW.decision_status='completed' THEN
            SELECT count(*) INTO dependency_count FROM lineage.artifact
             WHERE artifact_id IN (
               NEW.input_manifest_artifact_id,NEW.aggregation_run_artifact_id,
               NEW.strategy_target_artifact_id,NEW.defense_decision_artifact_id,
               NEW.merged_target_artifact_id
             ) AND status='published';
            IF dependency_count <> 5 THEN
              RAISE EXCEPTION 'Completed Product Decision requires five published runtime Artifacts';
            END IF;
            IF NEW.active_model_state_artifact_id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM lineage.artifact WHERE artifact_id=NEW.active_model_state_artifact_id
                AND status='published'
            ) THEN
              RAISE EXCEPTION 'Active Model State must be a published Artifact';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_product_decision_validate
          BEFORE INSERT ON product.v022_product_decision
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_product_decision();
        """
    )
    for table in (
        "v022_decision_schedule_version",
        "v022_decision_schedule_session",
        "v022_product_enrollment",
        "v022_product_decision",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON product.{table} FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS product.validate_v022_product_decision() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS product.validate_v022_product_enrollment() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS product.validate_v022_decision_schedule_completeness() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS product.validate_v022_decision_schedule_session() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS product.validate_v022_decision_schedule_version() CASCADE")
    op.drop_table("v022_product_decision", schema="product")
    op.drop_table("v022_product_enrollment", schema="product")
    op.drop_table("v022_decision_schedule_session", schema="product")
    op.drop_table("v022_decision_schedule_version", schema="product")
