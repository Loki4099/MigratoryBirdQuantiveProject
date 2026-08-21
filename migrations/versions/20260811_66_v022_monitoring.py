# ruff: noqa: E501
"""Add v0.22 Enrollment lifecycle and OOS Monitoring Snapshot identity.

Revision ID: 20260811_66_v022_monitoring
Revises: 20260811_65_v022_product_run
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_66_v022_monitoring"
down_revision: str | None = "20260811_65_v022_product_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE product.v022_enrollment_lifecycle_event (
          enrollment_lifecycle_event_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          product_enrollment_id uuid NOT NULL REFERENCES product.v022_product_enrollment,
          sequence_number integer NOT NULL CHECK (sequence_number >= 1),
          from_lifecycle varchar(20) NOT NULL CHECK (
            from_lifecycle IN ('active','suspended','superseded','retired','invalidated')
          ),
          to_lifecycle varchar(20) NOT NULL CHECK (
            to_lifecycle IN ('active','suspended','superseded','retired','invalidated')
          ),
          reason_code varchar(120) NOT NULL,
          reason text NOT NULL,
          requested_by varchar(160) NOT NULL,
          requested_at timestamptz NOT NULL,
          effective_at timestamptz NOT NULL,
          event_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (event_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (product_enrollment_id,sequence_number),
          CHECK (from_lifecycle <> to_lifecycle),
          CHECK (requested_at <= effective_at),
          CHECK (btrim(reason_code) <> '' AND btrim(reason) <> '' AND btrim(requested_by) <> '')
        );
        CREATE TABLE product.v022_oos_monitoring_snapshot (
          oos_monitoring_snapshot_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          product_enrollment_id uuid NOT NULL REFERENCES product.v022_product_enrollment,
          monitoring_policy_version_id uuid NOT NULL
            REFERENCES product.v022_monitoring_policy_version,
          monitoring_engine_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          as_of_decision_session_id uuid NOT NULL
            REFERENCES product.v022_decision_schedule_session,
          known_at timestamptz NOT NULL,
          health varchar(24) NOT NULL CHECK (
            health IN ('observing','healthy','watch','warning','data_interrupted')
          ),
          eligible_decision_count integer NOT NULL CHECK (eligible_decision_count >= 0),
          completed_decision_count integer NOT NULL CHECK (completed_decision_count >= 0),
          missing_decision_count integer NOT NULL CHECK (missing_decision_count >= 0),
          metrics_document jsonb NOT NULL,
          health_document jsonb NOT NULL,
          snapshot_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (snapshot_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (product_enrollment_id,monitoring_policy_version_id,monitoring_engine_artifact_id,
                  as_of_decision_session_id,known_at),
          CHECK (eligible_decision_count=completed_decision_count+missing_decision_count),
          CHECK (jsonb_typeof(metrics_document)='object'),
          CHECK (jsonb_typeof(health_document)='object' AND health_document <> '{}'::jsonb)
        );
        CREATE TABLE product.v022_oos_monitoring_snapshot_decision (
          oos_monitoring_snapshot_id uuid NOT NULL
            REFERENCES product.v022_oos_monitoring_snapshot,
          ordinal integer NOT NULL CHECK (ordinal >= 1),
          product_decision_id uuid NOT NULL REFERENCES product.v022_product_decision,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (oos_monitoring_snapshot_id,ordinal),
          UNIQUE (oos_monitoring_snapshot_id,product_decision_id)
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION product.validate_v022_enrollment_lifecycle_event()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE enrollment_status varchar; prior_row record; expected_sequence integer;
                transition_allowed boolean;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact.status INTO enrollment_status
            FROM product.v022_product_enrollment enrollment
            JOIN lineage.artifact artifact ON artifact.artifact_id=enrollment.artifact_id
           WHERE enrollment.product_enrollment_id=NEW.product_enrollment_id;
          SELECT * INTO prior_row FROM product.v022_enrollment_lifecycle_event
           WHERE product_enrollment_id=NEW.product_enrollment_id
           ORDER BY sequence_number DESC LIMIT 1;
          expected_sequence := coalesce(prior_row.sequence_number,0)+1;
          IF enrollment_status <> 'published' OR NEW.sequence_number <> expected_sequence OR
             NEW.from_lifecycle <> coalesce(prior_row.to_lifecycle,'active') OR
             (prior_row.effective_at IS NOT NULL AND NEW.effective_at < prior_row.effective_at) THEN
            RAISE EXCEPTION 'Enrollment Lifecycle Event does not extend the exact prior state';
          END IF;
          transition_allowed := CASE NEW.from_lifecycle
            WHEN 'active' THEN NEW.to_lifecycle IN ('suspended','superseded','retired','invalidated')
            WHEN 'suspended' THEN NEW.to_lifecycle IN ('active','superseded','retired','invalidated')
            WHEN 'superseded' THEN NEW.to_lifecycle='retired'
            ELSE false
          END;
          IF NOT transition_allowed THEN
            RAISE EXCEPTION 'Illegal Enrollment lifecycle transition';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_enrollment_lifecycle_event_validate
          BEFORE INSERT ON product.v022_enrollment_lifecycle_event
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_enrollment_lifecycle_event();

        CREATE FUNCTION product.validate_v022_oos_monitoring_snapshot()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE enrollment_row record; policy_row record; session_row record;
                engine_status varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT enrollment.*,execution.product_definition_id,artifact.status INTO enrollment_row
            FROM product.v022_product_enrollment enrollment
            JOIN lineage.artifact artifact ON artifact.artifact_id=enrollment.artifact_id
            JOIN product.v022_execution_version execution
              ON execution.execution_version_id=enrollment.execution_version_id
           WHERE enrollment.product_enrollment_id=NEW.product_enrollment_id;
          SELECT policy.*,artifact.status INTO policy_row
            FROM product.v022_monitoring_policy_version policy
            JOIN lineage.artifact artifact ON artifact.artifact_id=policy.artifact_id
           WHERE policy.monitoring_policy_version_id=NEW.monitoring_policy_version_id;
          SELECT * INTO session_row FROM product.v022_decision_schedule_session
           WHERE decision_session_id=NEW.as_of_decision_session_id;
          SELECT status INTO engine_status FROM lineage.artifact
           WHERE artifact_id=NEW.monitoring_engine_artifact_id;
          IF enrollment_row.status <> 'published' OR policy_row.status <> 'published' OR
             engine_status <> 'published' OR
             policy_row.product_definition_id <> enrollment_row.product_definition_id THEN
            RAISE EXCEPTION 'Monitoring Snapshot requires published Enrollment, Policy, and Engine';
          END IF;
          IF session_row.decision_schedule_version_id <> enrollment_row.decision_schedule_version_id OR
             NEW.known_at < session_row.decision_cutoff_at THEN
            RAISE EXCEPTION 'Monitoring Snapshot as-of Session or known-at is invalid';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_oos_monitoring_snapshot_validate
          BEFORE INSERT ON product.v022_oos_monitoring_snapshot
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_oos_monitoring_snapshot();

        CREATE FUNCTION product.validate_v022_oos_monitoring_member()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE snapshot_row record; decision_row record; as_of_ordinal integer;
        BEGIN
          SELECT snapshot.*,artifact.status INTO snapshot_row
            FROM product.v022_oos_monitoring_snapshot snapshot
            JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id
           WHERE snapshot.oos_monitoring_snapshot_id=NEW.oos_monitoring_snapshot_id;
          PERFORM data.assert_artifact_draft(snapshot_row.artifact_id);
          SELECT decision.*,session.ordinal AS session_ordinal INTO decision_row
            FROM product.v022_product_decision decision
            JOIN product.v022_decision_schedule_session session
              ON session.decision_session_id=decision.decision_session_id
           WHERE decision.product_decision_id=NEW.product_decision_id;
          SELECT ordinal INTO as_of_ordinal FROM product.v022_decision_schedule_session
           WHERE decision_session_id=snapshot_row.as_of_decision_session_id;
          IF decision_row.product_enrollment_id <> snapshot_row.product_enrollment_id OR
             NOT decision_row.oos_eligible OR decision_row.session_ordinal > as_of_ordinal THEN
            RAISE EXCEPTION 'Monitoring Snapshot member is not eligible OOS evidence';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_oos_monitoring_member_validate
          BEFORE INSERT ON product.v022_oos_monitoring_snapshot_decision
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_oos_monitoring_member();

        CREATE FUNCTION product.validate_v022_oos_monitoring_completeness()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_count integer; completed_count integer; missing_count integer;
                minimum_ordinal integer; maximum_ordinal integer;
        BEGIN
          SELECT count(*),
                 count(*) FILTER (WHERE decision.decision_status='completed'),
                 count(*) FILTER (WHERE decision.decision_status='missing'),
                 min(member.ordinal),max(member.ordinal)
            INTO actual_count,completed_count,missing_count,minimum_ordinal,maximum_ordinal
            FROM product.v022_oos_monitoring_snapshot_decision member
            JOIN product.v022_product_decision decision
              ON decision.product_decision_id=member.product_decision_id
           WHERE member.oos_monitoring_snapshot_id=NEW.oos_monitoring_snapshot_id;
          IF actual_count <> NEW.eligible_decision_count OR
             completed_count <> NEW.completed_decision_count OR
             missing_count <> NEW.missing_decision_count OR
             (actual_count > 0 AND (minimum_ordinal <> 1 OR maximum_ordinal <> actual_count)) THEN
            RAISE EXCEPTION 'Monitoring Snapshot Decision membership is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_oos_monitoring_complete
          AFTER INSERT ON product.v022_oos_monitoring_snapshot
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION product.validate_v022_oos_monitoring_completeness();
        """
    )
    for table in (
        "v022_enrollment_lifecycle_event",
        "v022_oos_monitoring_snapshot",
        "v022_oos_monitoring_snapshot_decision",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON product.{table} FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def downgrade() -> None:
    for function in (
        "validate_v022_oos_monitoring_completeness",
        "validate_v022_oos_monitoring_member",
        "validate_v022_oos_monitoring_snapshot",
        "validate_v022_enrollment_lifecycle_event",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS product.{function}() CASCADE")
    op.drop_table("v022_oos_monitoring_snapshot_decision", schema="product")
    op.drop_table("v022_oos_monitoring_snapshot", schema="product")
    op.drop_table("v022_enrollment_lifecycle_event", schema="product")
