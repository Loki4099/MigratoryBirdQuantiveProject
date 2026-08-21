# ruff: noqa: E501
"""Persist exact Suite Launch Batch stage outcomes.

Revision ID: 20260819_130_v022_launch_events
Revises: 20260819_129_v022_schema_binding
"""

from __future__ import annotations

from alembic import op

revision = "20260819_130_v022_launch_events"
down_revision = "20260819_129_v022_schema_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_suite_launch_batch_event (
          suite_launch_batch_id uuid NOT NULL
            REFERENCES experiment.v022_suite_launch_batch,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          frequency text CHECK (frequency IN ('weekly','monthly')),
          stage text NOT NULL CHECK (stage IN (
            'prepare_graph','admit_graph','submit_suite','lock_source','complete'
          )),
          outcome text NOT NULL CHECK (outcome IN ('started','succeeded','failed')),
          error_code text,
          error_summary text,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (suite_launch_batch_id,ordinal),
          CHECK ((
            (outcome='failed' AND btrim(error_code)<>'' AND
             btrim(error_summary)<>'' AND length(error_summary)<=1000) OR
            (outcome<>'failed' AND error_code IS NULL AND error_summary IS NULL)
          ) IS TRUE)
        );

        CREATE INDEX v022_suite_launch_batch_event_latest_idx
          ON experiment.v022_suite_launch_batch_event (
            suite_launch_batch_id,ordinal DESC
          );

        CREATE TRIGGER trg_v022_suite_launch_batch_event_append_only
          BEFORE UPDATE OR DELETE
          ON experiment.v022_suite_launch_batch_event
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )

def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM experiment.v022_suite_launch_batch_event) THEN
            RAISE EXCEPTION
              'Cannot downgrade M130 with Suite Launch Batch events';
          END IF;
        END $$;
        DROP TRIGGER trg_v022_suite_launch_batch_event_append_only
          ON experiment.v022_suite_launch_batch_event;
        DROP TABLE experiment.v022_suite_launch_batch_event;
        """
    )
