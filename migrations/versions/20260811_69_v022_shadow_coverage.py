# ruff: noqa: E501
"""Add per-session Shadow Comparison and Coverage Snapshot identity.

Revision ID: 20260811_69_v022_coverage
Revises: 20260811_68_v022_shadow
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_69_v022_coverage"
down_revision: str | None = "20260811_68_v022_shadow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_shadow_decision_comparison (
          shadow_decision_comparison_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          shadow_representative_id uuid NOT NULL REFERENCES workspace.v022_shadow_representative,
          decision_session_id uuid NOT NULL REFERENCES product.v022_decision_schedule_session,
          v022_product_decision_id uuid NOT NULL REFERENCES product.v022_product_decision,
          v021_reference_artifact_id uuid NULL REFERENCES lineage.artifact,
          comparator_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          outcome varchar(24) NOT NULL CHECK (outcome IN ('matched','different','missing_v021')),
          explained_difference boolean NOT NULL,
          explanation_codes jsonb NOT NULL,
          comparison_document jsonb NOT NULL,
          known_at timestamptz NOT NULL,
          comparison_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (comparison_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (shadow_representative_id,decision_session_id,comparator_artifact_id),
          CHECK (jsonb_typeof(explanation_codes)='array'),
          CHECK (jsonb_typeof(comparison_document)='object' AND comparison_document <> '{}'::jsonb),
          CHECK ((outcome='missing_v021')=(v021_reference_artifact_id IS NULL)),
          CHECK ((explained_difference AND jsonb_array_length(explanation_codes)>0) OR
                 (NOT explained_difference AND jsonb_array_length(explanation_codes)=0)),
          CHECK (outcome<>'matched' OR NOT explained_difference)
        );
        CREATE TABLE workspace.v022_shadow_coverage_snapshot (
          shadow_coverage_snapshot_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          shadow_plan_id uuid NOT NULL REFERENCES workspace.v022_shadow_plan,
          comparator_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          known_at timestamptz NOT NULL,
          ready_for_default boolean NOT NULL,
          representative_count integer NOT NULL CHECK (representative_count > 0),
          ready_representative_count integer NOT NULL CHECK (
            ready_representative_count >= 0 AND ready_representative_count <= representative_count
          ),
          blocker_codes jsonb NOT NULL,
          coverage_document jsonb NOT NULL,
          coverage_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (coverage_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (shadow_plan_id,comparator_artifact_id,known_at),
          CHECK (jsonb_typeof(blocker_codes)='array'),
          CHECK (jsonb_typeof(coverage_document)='object' AND coverage_document <> '{}'::jsonb),
          CHECK (ready_for_default=(ready_representative_count=representative_count AND
                                    jsonb_array_length(blocker_codes)=0))
        );
        CREATE TABLE workspace.v022_shadow_coverage_member (
          shadow_coverage_snapshot_id uuid NOT NULL
            REFERENCES workspace.v022_shadow_coverage_snapshot,
          ordinal integer NOT NULL CHECK (ordinal >= 1),
          shadow_representative_id uuid NOT NULL REFERENCES workspace.v022_shadow_representative,
          eligible_session_count integer NOT NULL CHECK (eligible_session_count >= 0),
          comparison_count integer NOT NULL CHECK (comparison_count >= 0),
          matched_count integer NOT NULL CHECK (matched_count >= 0),
          explained_difference_count integer NOT NULL CHECK (explained_difference_count >= 0),
          unexplained_difference_count integer NOT NULL CHECK (unexplained_difference_count >= 0),
          missing_v021_count integer NOT NULL CHECK (missing_v021_count >= 0),
          missing_v022_count integer NOT NULL CHECK (missing_v022_count >= 0),
          ready boolean NOT NULL,
          blocker_codes jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (shadow_coverage_snapshot_id,ordinal),
          UNIQUE (shadow_coverage_snapshot_id,shadow_representative_id),
          CHECK (jsonb_typeof(blocker_codes)='array'),
          CHECK (comparison_count=matched_count+explained_difference_count+
                                  unexplained_difference_count+missing_v021_count),
          CHECK (comparison_count <= eligible_session_count),
          CHECK (ready=(jsonb_array_length(blocker_codes)=0))
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION workspace.validate_v022_shadow_comparison()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE representative_row record; decision_row record; session_cutoff timestamptz;
                reference_status varchar; comparator_status varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT representative.*,plan_artifact.status AS plan_status
            INTO representative_row
            FROM workspace.v022_shadow_representative representative
            JOIN workspace.v022_shadow_plan plan ON plan.shadow_plan_id=representative.shadow_plan_id
            JOIN lineage.artifact plan_artifact ON plan_artifact.artifact_id=plan.artifact_id
           WHERE representative.shadow_representative_id=NEW.shadow_representative_id;
          SELECT decision.*,decision_artifact.status INTO decision_row
            FROM product.v022_product_decision decision
            JOIN lineage.artifact decision_artifact ON decision_artifact.artifact_id=decision.artifact_id
           WHERE decision.product_decision_id=NEW.v022_product_decision_id;
          SELECT decision_cutoff_at INTO session_cutoff
            FROM product.v022_decision_schedule_session WHERE decision_session_id=NEW.decision_session_id;
          SELECT status INTO reference_status FROM lineage.artifact
           WHERE artifact_id=NEW.v021_reference_artifact_id;
          SELECT status INTO comparator_status FROM lineage.artifact
           WHERE artifact_id=NEW.comparator_artifact_id;
          IF representative_row.plan_status <> 'published' OR decision_row.status <> 'published' OR
             comparator_status <> 'published' THEN
            RAISE EXCEPTION 'Shadow Comparison requires published Plan, Decision, and Comparator';
          END IF;
          IF decision_row.product_enrollment_id <> representative_row.product_enrollment_id OR
             decision_row.execution_version_id <> representative_row.execution_version_id OR
             decision_row.decision_session_id <> NEW.decision_session_id OR NOT decision_row.oos_eligible THEN
            RAISE EXCEPTION 'Shadow Comparison does not bind the Representative exact OOS Decision';
          END IF;
          IF NEW.v021_reference_artifact_id IS NOT NULL AND reference_status <> 'published' THEN
            RAISE EXCEPTION 'Shadow Comparison v0.21 reference is not published';
          END IF;
          IF NEW.known_at < session_cutoff THEN
            RAISE EXCEPTION 'Shadow Comparison cannot be known before its Decision cutoff';
          END IF;
          IF decision_row.decision_status='missing' AND NEW.outcome='matched' THEN
            RAISE EXCEPTION 'A missing v0.22 Decision cannot be a matched Shadow Comparison';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_comparison_validate
          BEFORE INSERT ON workspace.v022_shadow_decision_comparison
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_shadow_comparison();

        CREATE FUNCTION workspace.validate_v022_shadow_coverage_snapshot()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE plan_status varchar; comparator_status varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact.status INTO plan_status FROM workspace.v022_shadow_plan plan
            JOIN lineage.artifact artifact ON artifact.artifact_id=plan.artifact_id
           WHERE plan.shadow_plan_id=NEW.shadow_plan_id;
          SELECT status INTO comparator_status FROM lineage.artifact
           WHERE artifact_id=NEW.comparator_artifact_id;
          IF plan_status <> 'published' OR comparator_status <> 'published' THEN
            RAISE EXCEPTION 'Shadow Coverage requires published Plan and Comparator';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_coverage_validate
          BEFORE INSERT ON workspace.v022_shadow_coverage_snapshot
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_shadow_coverage_snapshot();

        CREATE FUNCTION workspace.validate_v022_shadow_coverage_member()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE snapshot_plan uuid; representative_plan uuid; snapshot_artifact uuid;
        BEGIN
          SELECT shadow_plan_id,artifact_id INTO snapshot_plan,snapshot_artifact
            FROM workspace.v022_shadow_coverage_snapshot
           WHERE shadow_coverage_snapshot_id=NEW.shadow_coverage_snapshot_id;
          PERFORM data.assert_artifact_draft(snapshot_artifact);
          SELECT shadow_plan_id INTO representative_plan FROM workspace.v022_shadow_representative
           WHERE shadow_representative_id=NEW.shadow_representative_id;
          IF representative_plan <> snapshot_plan THEN
            RAISE EXCEPTION 'Shadow Coverage member belongs to another Plan';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_coverage_member_validate
          BEFORE INSERT ON workspace.v022_shadow_coverage_member
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_shadow_coverage_member();

        CREATE FUNCTION workspace.validate_v022_shadow_coverage_completeness()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_count integer; ready_count integer; minimum_ordinal integer;
                maximum_ordinal integer;
        BEGIN
          SELECT count(*),count(*) FILTER (WHERE ready),min(ordinal),max(ordinal)
            INTO actual_count,ready_count,minimum_ordinal,maximum_ordinal
            FROM workspace.v022_shadow_coverage_member
           WHERE shadow_coverage_snapshot_id=NEW.shadow_coverage_snapshot_id;
          IF actual_count <> NEW.representative_count OR ready_count <> NEW.ready_representative_count OR
             minimum_ordinal <> 1 OR maximum_ordinal <> NEW.representative_count THEN
            RAISE EXCEPTION 'Shadow Coverage membership is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_shadow_coverage_complete
          AFTER INSERT ON workspace.v022_shadow_coverage_snapshot
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION workspace.validate_v022_shadow_coverage_completeness();

        CREATE FUNCTION workspace.protect_v022_shadow_coverage_member()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE snapshot_artifact uuid;
        BEGIN
          SELECT artifact_id INTO snapshot_artifact FROM workspace.v022_shadow_coverage_snapshot
           WHERE shadow_coverage_snapshot_id=OLD.shadow_coverage_snapshot_id;
          PERFORM data.assert_artifact_draft(snapshot_artifact);
          RETURN OLD;
        END $$;
        CREATE TRIGGER trg_v022_shadow_coverage_member_protect
          BEFORE UPDATE OR DELETE ON workspace.v022_shadow_coverage_member
          FOR EACH ROW EXECUTE FUNCTION workspace.protect_v022_shadow_coverage_member();
        CREATE TRIGGER trg_v022_shadow_comparison_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_shadow_decision_comparison
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_shadow_coverage_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_shadow_coverage_snapshot
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS workspace.protect_v022_shadow_coverage_member() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_shadow_coverage_completeness() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_shadow_coverage_member() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_shadow_coverage_snapshot() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_shadow_comparison() CASCADE")
    op.drop_table("v022_shadow_coverage_member", schema="workspace")
    op.drop_table("v022_shadow_coverage_snapshot", schema="workspace")
    op.drop_table("v022_shadow_decision_comparison", schema="workspace")
