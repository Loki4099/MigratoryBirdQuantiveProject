"""Persist controlled weekly/monthly v0.22 Suite launch batches.

Revision ID: 20260818_120_v022_launch_batch
Revises: 20260818_119_v022_restore_root
"""

from __future__ import annotations

from alembic import op

revision = "20260818_120_v022_launch_batch"
down_revision = "20260818_119_v022_restore_root"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_suite_launch_batch (
          suite_launch_batch_id uuid PRIMARY KEY,
          actor_key text NOT NULL CHECK (btrim(actor_key) <> ''),
          idempotency_key uuid NOT NULL,
          source_graph_draft_id uuid NOT NULL
            REFERENCES workspace.v022_graph_draft(graph_draft_id),
          source_graph_draft_revision integer NOT NULL
            CHECK (source_graph_draft_revision > 0),
          source_compiled_research_graph_id uuid NOT NULL
            REFERENCES workspace.compiled_research_graph(compiled_research_graph_id),
          suite_mode text NOT NULL CHECK (suite_mode = 'exploratory'),
          requested_frequencies jsonb NOT NULL,
          batch_fingerprint char(64) NOT NULL
            CHECK (batch_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (actor_key,idempotency_key),
          CHECK (
            requested_frequencies IN (
              '["weekly"]'::jsonb,
              '["monthly"]'::jsonb,
              '["weekly","monthly"]'::jsonb
            )
          )
        );

        CREATE TABLE experiment.v022_suite_launch_batch_child (
          suite_launch_batch_id uuid NOT NULL
            REFERENCES experiment.v022_suite_launch_batch(suite_launch_batch_id),
          frequency text NOT NULL CHECK (frequency IN ('weekly','monthly')),
          submission_key uuid NOT NULL,
          graph_draft_id uuid REFERENCES workspace.v022_graph_draft(graph_draft_id),
          graph_draft_revision integer CHECK (graph_draft_revision > 0),
          compiled_research_graph_id uuid
            REFERENCES workspace.compiled_research_graph(compiled_research_graph_id),
          research_suite_id uuid UNIQUE
            REFERENCES experiment.v022_research_suite(research_suite_id),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (suite_launch_batch_id,frequency),
          CHECK (
            (graph_draft_id IS NULL AND graph_draft_revision IS NULL AND
             compiled_research_graph_id IS NULL) OR
            (graph_draft_id IS NOT NULL AND graph_draft_revision IS NOT NULL AND
             compiled_research_graph_id IS NOT NULL)
          ),
          CHECK (research_suite_id IS NULL OR compiled_research_graph_id IS NOT NULL)
        );

        CREATE UNIQUE INDEX v022_suite_launch_batch_child_submission_uq
          ON experiment.v022_suite_launch_batch_child(
            suite_launch_batch_id,submission_key
          );

        CREATE FUNCTION experiment.guard_v022_suite_launch_batch_child_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.suite_launch_batch_id IS DISTINCT FROM OLD.suite_launch_batch_id OR
             NEW.frequency IS DISTINCT FROM OLD.frequency OR
             NEW.submission_key IS DISTINCT FROM OLD.submission_key OR
             (OLD.graph_draft_id IS NOT NULL AND
              ROW(NEW.graph_draft_id,NEW.graph_draft_revision,
                  NEW.compiled_research_graph_id) IS DISTINCT FROM
              ROW(OLD.graph_draft_id,OLD.graph_draft_revision,
                  OLD.compiled_research_graph_id)) OR
             (OLD.research_suite_id IS NOT NULL AND
              NEW.research_suite_id IS DISTINCT FROM OLD.research_suite_id) THEN
            RAISE EXCEPTION 'v0.22 Suite Launch Batch child identity is immutable';
          END IF;
          NEW.updated_at := now();
          RETURN NEW;
        END $$;

        CREATE TRIGGER guard_v022_suite_launch_batch_child_update
          BEFORE UPDATE ON experiment.v022_suite_launch_batch_child
          FOR EACH ROW EXECUTE FUNCTION
            experiment.guard_v022_suite_launch_batch_child_update();

        CREATE FUNCTION experiment.reject_v022_suite_launch_batch_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'v0.22 Suite Launch Batch identity is append-only';
        END $$;

        CREATE TRIGGER reject_v022_suite_launch_batch_update
          BEFORE UPDATE OR DELETE ON experiment.v022_suite_launch_batch
          FOR EACH ROW EXECUTE FUNCTION
            experiment.reject_v022_suite_launch_batch_mutation();

        CREATE TRIGGER reject_v022_suite_launch_batch_child_delete
          BEFORE DELETE ON experiment.v022_suite_launch_batch_child
          FOR EACH ROW EXECUTE FUNCTION
            experiment.reject_v022_suite_launch_batch_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM experiment.v022_suite_launch_batch) THEN
            RAISE EXCEPTION
              'Cannot downgrade nonempty v0.22 Suite Launch Batch identities';
          END IF;
        END $$;
        DROP TRIGGER reject_v022_suite_launch_batch_child_delete
          ON experiment.v022_suite_launch_batch_child;
        DROP TRIGGER reject_v022_suite_launch_batch_update
          ON experiment.v022_suite_launch_batch;
        DROP TRIGGER guard_v022_suite_launch_batch_child_update
          ON experiment.v022_suite_launch_batch_child;
        DROP FUNCTION experiment.reject_v022_suite_launch_batch_mutation();
        DROP FUNCTION experiment.guard_v022_suite_launch_batch_child_update();
        DROP TABLE experiment.v022_suite_launch_batch_child;
        DROP TABLE experiment.v022_suite_launch_batch;
        """
    )
