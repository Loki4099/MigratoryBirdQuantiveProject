# ruff: noqa: E501
"""Scope mutable research state to an explicit Research Round.

Revision ID: 20260819_131_v022_research_round
Revises: 20260819_130_v022_launch_events
"""

from __future__ import annotations

from alembic import op

revision = "20260819_131_v022_research_round"
down_revision = "20260819_130_v022_launch_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_research_round (
          research_round_id uuid PRIMARY KEY,
          root_graph_draft_id uuid NOT NULL
            REFERENCES workspace.v022_graph_draft(graph_draft_id),
          ordinal integer NOT NULL CHECK (ordinal > 0),
          status text NOT NULL
            CHECK (status IN ('active','closed','gc_pending','gc_complete')),
          opened_at timestamptz NOT NULL DEFAULT now(),
          closed_at timestamptz,
          close_reason text,
          reset_idempotency_key uuid,
          created_by text NOT NULL CHECK (btrim(created_by) <> ''),
          UNIQUE (root_graph_draft_id,ordinal),
          CHECK ((status='active') = (closed_at IS NULL)),
          CHECK ((status='active') = (close_reason IS NULL))
        );
        CREATE UNIQUE INDEX v022_research_round_one_active_uq
          ON workspace.v022_research_round(root_graph_draft_id)
          WHERE status='active';

        CREATE TABLE workspace.v022_graph_draft_revision_round (
          graph_draft_id uuid NOT NULL,
          revision integer NOT NULL,
          research_round_id uuid NOT NULL
            REFERENCES workspace.v022_research_round(research_round_id),
          bound_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (graph_draft_id,revision),
          FOREIGN KEY (graph_draft_id,revision)
            REFERENCES workspace.v022_graph_draft_revision(graph_draft_id,revision)
        );

        CREATE TABLE experiment.v022_suite_launch_batch_round (
          suite_launch_batch_id uuid PRIMARY KEY
            REFERENCES experiment.v022_suite_launch_batch(suite_launch_batch_id),
          research_round_id uuid NOT NULL
            REFERENCES workspace.v022_research_round(research_round_id),
          bound_at timestamptz NOT NULL DEFAULT now()
        );

        WITH root_round_count AS (
          SELECT draft.graph_draft_id AS root_graph_draft_id,
                 1 + count(event.graph_draft_event_id) AS round_count
            FROM workspace.v022_graph_draft draft
            LEFT JOIN workspace.v022_graph_draft_event event
              ON event.graph_draft_id=draft.graph_draft_id
             AND event.event_type='reset_current_research'
             AND event.applied
           WHERE draft.cloned_from_graph_draft_id IS NULL
           GROUP BY draft.graph_draft_id
        ), generated AS (
          SELECT root.root_graph_draft_id,ordinal,
                 root.round_count
            FROM root_round_count root
            CROSS JOIN LATERAL generate_series(1,root.round_count) ordinal
        ), boundaries AS (
          SELECT generated.*,
                 coalesce(
                   (SELECT min(revision.created_at)
                      FROM workspace.v022_graph_draft_revision revision
                     WHERE revision.graph_draft_id=generated.root_graph_draft_id
                       AND 1 + (
                         SELECT count(*)
                           FROM workspace.v022_graph_draft_event reset_event
                          WHERE reset_event.graph_draft_id=generated.root_graph_draft_id
                            AND reset_event.event_type='reset_current_research'
                            AND reset_event.applied
                            AND reset_event.resulting_revision <= revision.revision
                       )=generated.ordinal),
                   now()
                 ) AS opened_at
            FROM generated
        )
        INSERT INTO workspace.v022_research_round (
          research_round_id,root_graph_draft_id,ordinal,status,
          opened_at,closed_at,close_reason,created_by
        )
        SELECT gen_random_uuid(),boundary.root_graph_draft_id,boundary.ordinal,
               CASE WHEN boundary.ordinal=boundary.round_count
                    THEN 'active' ELSE 'closed' END,
               boundary.opened_at,
               CASE WHEN boundary.ordinal=boundary.round_count THEN NULL
                    ELSE lead(boundary.opened_at) OVER (
                      PARTITION BY boundary.root_graph_draft_id
                      ORDER BY boundary.ordinal
                    ) END,
               CASE WHEN boundary.ordinal=boundary.round_count
                    THEN NULL ELSE 'legacy_reset_backfill' END,
               draft.researcher_key
          FROM boundaries boundary
          JOIN workspace.v022_graph_draft draft
            ON draft.graph_draft_id=boundary.root_graph_draft_id;

        INSERT INTO workspace.v022_graph_draft_revision_round (
          graph_draft_id,revision,research_round_id
        )
        SELECT revision.graph_draft_id,revision.revision,round.research_round_id
          FROM workspace.v022_graph_draft_revision revision
          JOIN workspace.v022_graph_draft draft
            ON draft.graph_draft_id=revision.graph_draft_id
           AND draft.cloned_from_graph_draft_id IS NULL
          JOIN workspace.v022_research_round round
            ON round.root_graph_draft_id=revision.graph_draft_id
           AND round.ordinal=1 + (
             SELECT count(*)
               FROM workspace.v022_graph_draft_event reset_event
              WHERE reset_event.graph_draft_id=revision.graph_draft_id
                AND reset_event.event_type='reset_current_research'
                AND reset_event.applied
                AND reset_event.resulting_revision <= revision.revision
           );

        DO $$
        DECLARE inserted_rows integer;
        BEGIN
          LOOP
            INSERT INTO workspace.v022_graph_draft_revision_round (
              graph_draft_id,revision,research_round_id
            )
            SELECT revision.graph_draft_id,revision.revision,
                   source_binding.research_round_id
              FROM workspace.v022_graph_draft_revision revision
              JOIN workspace.v022_graph_draft draft
                ON draft.graph_draft_id=revision.graph_draft_id
               AND draft.cloned_from_graph_draft_id IS NOT NULL
              JOIN workspace.v022_graph_draft_revision_round source_binding
                ON source_binding.graph_draft_id=draft.cloned_from_graph_draft_id
               AND source_binding.revision=draft.cloned_from_revision
              LEFT JOIN workspace.v022_graph_draft_revision_round existing
                ON existing.graph_draft_id=revision.graph_draft_id
               AND existing.revision=revision.revision
             WHERE existing.graph_draft_id IS NULL
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS inserted_rows = ROW_COUNT;
            EXIT WHEN inserted_rows=0;
          END LOOP;
          IF EXISTS (
            SELECT 1 FROM workspace.v022_graph_draft_revision revision
            LEFT JOIN workspace.v022_graph_draft_revision_round binding
              ON binding.graph_draft_id=revision.graph_draft_id
             AND binding.revision=revision.revision
            WHERE binding.graph_draft_id IS NULL
          ) THEN
            RAISE EXCEPTION 'Cannot backfill every Graph Draft revision to a Research Round';
          END IF;
        END $$;

        INSERT INTO experiment.v022_suite_launch_batch_round (
          suite_launch_batch_id,research_round_id
        )
        SELECT batch.suite_launch_batch_id,binding.research_round_id
          FROM experiment.v022_suite_launch_batch batch
          JOIN workspace.v022_graph_draft_revision_round binding
            ON binding.graph_draft_id=batch.source_graph_draft_id
           AND binding.revision=batch.source_graph_draft_revision;

        CREATE FUNCTION workspace.guard_v022_research_round_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(NEW.research_round_id,NEW.root_graph_draft_id,NEW.ordinal,
                 NEW.opened_at,NEW.created_by) IS DISTINCT FROM
             ROW(OLD.research_round_id,OLD.root_graph_draft_id,OLD.ordinal,
                 OLD.opened_at,OLD.created_by) THEN
            RAISE EXCEPTION 'v0.22 Research Round identity is immutable';
          END IF;
          IF NOT (
            (OLD.status='active' AND NEW.status IN ('closed','gc_pending')) OR
            (OLD.status='closed' AND NEW.status='gc_pending') OR
            (OLD.status='gc_pending' AND NEW.status='gc_complete') OR
            NEW.status=OLD.status
          ) THEN
            RAISE EXCEPTION 'Illegal v0.22 Research Round status transition';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER guard_v022_research_round_update
          BEFORE UPDATE ON workspace.v022_research_round
          FOR EACH ROW EXECUTE FUNCTION workspace.guard_v022_research_round_update();
        CREATE TRIGGER reject_v022_research_round_delete
          BEFORE DELETE ON workspace.v022_research_round
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER reject_v022_revision_round_mutation
          BEFORE UPDATE OR DELETE ON workspace.v022_graph_draft_revision_round
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER reject_v022_launch_batch_round_mutation
          BEFORE UPDATE OR DELETE ON experiment.v022_suite_launch_batch_round
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM workspace.v022_research_round) THEN
            RAISE EXCEPTION 'Cannot downgrade nonempty v0.22 Research Round identities';
          END IF;
        END $$;
        DROP TRIGGER reject_v022_launch_batch_round_mutation
          ON experiment.v022_suite_launch_batch_round;
        DROP TRIGGER reject_v022_revision_round_mutation
          ON workspace.v022_graph_draft_revision_round;
        DROP TRIGGER reject_v022_research_round_delete
          ON workspace.v022_research_round;
        DROP TRIGGER guard_v022_research_round_update
          ON workspace.v022_research_round;
        DROP FUNCTION workspace.guard_v022_research_round_update();
        DROP TABLE experiment.v022_suite_launch_batch_round;
        DROP TABLE workspace.v022_graph_draft_revision_round;
        DROP INDEX workspace.v022_research_round_one_active_uq;
        DROP TABLE workspace.v022_research_round;
        """
    )
