# ruff: noqa: E501
"""Add authoritative v0.22 release and rollback control.

Revision ID: 20260811_67_v022_release
Revises: 20260811_66_v022_monitoring
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_67_v022_release"
down_revision: str | None = "20260811_66_v022_monitoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_release_transition (
          release_transition_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          sequence_number integer NOT NULL UNIQUE CHECK (sequence_number >= 1),
          from_state varchar(32) NOT NULL CHECK (
            from_state IN ('hidden','shadow','explicit_eligible','default','maintenance_read_only')
          ),
          to_state varchar(32) NOT NULL CHECK (
            to_state IN ('shadow','explicit_eligible','default','maintenance_read_only')
          ),
          reason_code varchar(120) NOT NULL,
          reason text NOT NULL,
          requested_by varchar(160) NOT NULL,
          requested_at timestamptz NOT NULL,
          gate_evidence_document jsonb NOT NULL,
          incident_document jsonb NOT NULL,
          transition_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (transition_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (from_state <> to_state),
          CHECK (btrim(reason_code) <> '' AND btrim(reason) <> '' AND btrim(requested_by) <> ''),
          CHECK (jsonb_typeof(gate_evidence_document) = 'object'),
          CHECK (jsonb_typeof(incident_document) = 'object')
        );

        CREATE FUNCTION workspace.validate_v022_release_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE prior_row record; expected_from varchar(32); allowed boolean;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT transition.* INTO prior_row
            FROM workspace.v022_release_transition transition
            JOIN lineage.artifact artifact ON artifact.artifact_id=transition.artifact_id
           WHERE artifact.status='published'
           ORDER BY transition.sequence_number DESC LIMIT 1;
          expected_from := coalesce(prior_row.to_state,'hidden');
          IF NEW.sequence_number <> coalesce(prior_row.sequence_number,0)+1 OR
             NEW.from_state <> expected_from THEN
            RAISE EXCEPTION 'Release transition does not extend the exact published state';
          END IF;
          allowed := CASE NEW.from_state
            WHEN 'hidden' THEN NEW.to_state='shadow'
            WHEN 'shadow' THEN NEW.to_state IN ('explicit_eligible','maintenance_read_only')
            WHEN 'explicit_eligible' THEN NEW.to_state IN ('default','shadow','maintenance_read_only')
            WHEN 'default' THEN NEW.to_state IN ('explicit_eligible','shadow','maintenance_read_only')
            WHEN 'maintenance_read_only' THEN NEW.to_state IN ('shadow','explicit_eligible','default')
            ELSE false
          END;
          IF NOT allowed THEN RAISE EXCEPTION 'Illegal v0.22 release transition'; END IF;
          IF NEW.to_state='maintenance_read_only' AND NEW.incident_document='{}'::jsonb THEN
            RAISE EXCEPTION 'Rollback requires an incident document';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_release_transition_validate
          BEFORE INSERT ON workspace.v022_release_transition
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_release_transition();
        CREATE TRIGGER trg_v022_release_transition_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_release_transition
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_release_transition() CASCADE")
    op.drop_table("v022_release_transition", schema="workspace")
