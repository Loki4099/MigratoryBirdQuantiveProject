# ruff: noqa: E501
"""Add reusable v0.22 Graph Run DAG scheduling and fencing.

Revision ID: 20260810_52_v022_graph_dag
Revises: 20260810_51_v022_workspace
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260810_52_v022_graph_dag"
down_revision: str | None = "20260810_51_v022_workspace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_graph_run (
          graph_run_id uuid PRIMARY KEY,
          compiled_research_graph_id uuid NOT NULL REFERENCES workspace.compiled_research_graph,
          run_fingerprint varchar(64) NOT NULL UNIQUE CHECK (run_fingerprint ~ '^[0-9a-f]{64}$'),
          status varchar(24) NOT NULL CHECK (status IN ('planning','ready','running','completed','failed','cancelled')),
          requested_by varchar(160) NOT NULL,
          requested_range jsonb NOT NULL,
          environment_fingerprint varchar(64) NOT NULL CHECK (environment_fingerprint ~ '^[0-9a-f]{64}$'),
          ready_at timestamptz NULL, started_at timestamptz NULL, completed_at timestamptz NULL,
          failure_details jsonb NULL, cancel_requested_at timestamptz NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE workspace.v022_graph_work_item (
          graph_work_item_id uuid PRIMARY KEY,
          execution_fingerprint varchar(64) NOT NULL UNIQUE CHECK (execution_fingerprint ~ '^[0-9a-f]{64}$'),
          work_kind varchar(24) NOT NULL CHECK (work_kind IN ('node','aggregation')),
          status varchar(40) NOT NULL CHECK (status IN (
            'queued','running','completed','failed','cancelled','reused',
            'blocked_upstream_failed','blocked_upstream_cancelled')),
          priority integer NOT NULL DEFAULT 100,
          lease_owner varchar(160) NULL, lease_expires_at timestamptz NULL,
          cancel_requested_at timestamptz NULL,
          lease_generation bigint NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
          fencing_token bigint NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
          attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
          failure_details jsonb NULL,
          created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
          CHECK ((status = 'running') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))
        );
        CREATE INDEX ix_v022_graph_work_claim ON workspace.v022_graph_work_item
          (status, priority, created_at);
        CREATE TABLE workspace.v022_graph_work_dependency (
          upstream_work_item_id uuid NOT NULL REFERENCES workspace.v022_graph_work_item,
          downstream_work_item_id uuid NOT NULL REFERENCES workspace.v022_graph_work_item,
          dependency_kind varchar(20) NOT NULL CHECK (dependency_kind IN ('required','optional')),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (upstream_work_item_id, downstream_work_item_id),
          CHECK (upstream_work_item_id <> downstream_work_item_id)
        );
        CREATE TABLE workspace.v022_graph_work_consumer (
          graph_run_id uuid NOT NULL REFERENCES workspace.v022_graph_run,
          graph_work_item_id uuid NOT NULL REFERENCES workspace.v022_graph_work_item,
          occurrence_kind varchar(24) NOT NULL CHECK (occurrence_kind IN ('node','aggregation')),
          occurrence_key varchar(500) NOT NULL,
          binding_disposition varchar(16) NOT NULL CHECK (binding_disposition IN ('execute','reuse')),
          released_at timestamptz NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (graph_run_id, graph_work_item_id),
          UNIQUE (graph_run_id, occurrence_kind, occurrence_key)
        );
        """
    )
    _create_scheduler_functions()


def _create_scheduler_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION workspace.v022_mark_graph_ready(run_id uuid, expected_work_count integer)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE actual_count integer; cycle_found boolean; dangling integer;
        BEGIN
          PERFORM 1 FROM workspace.v022_graph_run WHERE graph_run_id = run_id AND status = 'planning' FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'graph run is not planning'; END IF;
          SELECT count(*) INTO actual_count FROM workspace.v022_graph_work_consumer WHERE graph_run_id = run_id;
          IF actual_count <> expected_work_count OR actual_count < 1 THEN
            RAISE EXCEPTION 'graph run work count mismatch';
          END IF;
          SELECT count(*) INTO dangling
          FROM workspace.v022_graph_work_dependency d
          JOIN workspace.v022_graph_work_consumer c ON c.graph_work_item_id = d.downstream_work_item_id AND c.graph_run_id = run_id
          LEFT JOIN workspace.v022_graph_work_consumer u ON u.graph_work_item_id = d.upstream_work_item_id AND u.graph_run_id = run_id
          WHERE d.dependency_kind = 'required' AND u.graph_work_item_id IS NULL;
          IF dangling > 0 THEN RAISE EXCEPTION 'graph run contains dangling required dependencies'; END IF;
          WITH RECURSIVE edges AS (
            SELECT d.upstream_work_item_id AS start_id, d.downstream_work_item_id AS current_id,
                   ARRAY[d.upstream_work_item_id, d.downstream_work_item_id] AS path, false AS cycle
            FROM workspace.v022_graph_work_dependency d
            JOIN workspace.v022_graph_work_consumer c1 ON c1.graph_work_item_id=d.upstream_work_item_id AND c1.graph_run_id=run_id
            JOIN workspace.v022_graph_work_consumer c2 ON c2.graph_work_item_id=d.downstream_work_item_id AND c2.graph_run_id=run_id
            UNION ALL
            SELECT e.start_id, d.downstream_work_item_id, e.path || d.downstream_work_item_id,
                   d.downstream_work_item_id = ANY(e.path)
            FROM edges e JOIN workspace.v022_graph_work_dependency d ON d.upstream_work_item_id=e.current_id
            JOIN workspace.v022_graph_work_consumer c ON c.graph_work_item_id=d.downstream_work_item_id AND c.graph_run_id=run_id
            WHERE NOT e.cycle
          ) SELECT coalesce(bool_or(cycle), false) INTO cycle_found FROM edges;
          IF cycle_found THEN RAISE EXCEPTION 'graph run DAG contains a cycle'; END IF;
          UPDATE workspace.v022_graph_run SET status='ready', ready_at=now() WHERE graph_run_id=run_id;
        END $$;

        CREATE FUNCTION workspace.v022_claim_graph_work(run_id uuid, worker_key varchar, lease_seconds integer)
        RETURNS TABLE (graph_work_item_id uuid, fencing_token bigint, work_kind varchar)
        LANGUAGE plpgsql AS $$
        DECLARE claimed uuid;
        BEGIN
          IF lease_seconds < 1 THEN RAISE EXCEPTION 'lease_seconds must be positive'; END IF;
          SELECT item.graph_work_item_id INTO claimed
          FROM workspace.v022_graph_work_item item
          JOIN workspace.v022_graph_work_consumer consumer ON consumer.graph_work_item_id=item.graph_work_item_id
          JOIN workspace.v022_graph_run run ON run.graph_run_id=consumer.graph_run_id
          WHERE consumer.graph_run_id=run_id AND consumer.released_at IS NULL
            AND run.status IN ('ready','running') AND run.cancel_requested_at IS NULL
            AND item.status='queued' AND item.cancel_requested_at IS NULL
            AND NOT EXISTS (
              SELECT 1 FROM workspace.v022_graph_work_dependency dependency
              JOIN workspace.v022_graph_work_item upstream ON upstream.graph_work_item_id=dependency.upstream_work_item_id
              WHERE dependency.downstream_work_item_id=item.graph_work_item_id
                AND dependency.dependency_kind='required'
                AND upstream.status NOT IN ('completed','reused'))
          ORDER BY item.priority, item.created_at, item.graph_work_item_id
          FOR UPDATE OF item SKIP LOCKED LIMIT 1;
          IF claimed IS NULL THEN RETURN; END IF;
          UPDATE workspace.v022_graph_work_item AS claimed_item
          SET status='running', lease_owner=worker_key,
            lease_expires_at=now()+make_interval(secs=>lease_seconds),
            lease_generation=claimed_item.lease_generation+1,
            fencing_token=claimed_item.fencing_token+1,
            attempt_count=claimed_item.attempt_count+1, updated_at=now()
          WHERE claimed_item.graph_work_item_id=claimed
          RETURNING claimed_item.graph_work_item_id,
                    claimed_item.fencing_token,
                    claimed_item.work_kind
          INTO graph_work_item_id, fencing_token, work_kind;
          UPDATE workspace.v022_graph_run SET status='running', started_at=coalesce(started_at,now())
            WHERE workspace.v022_graph_run.graph_run_id=run_id AND status='ready';
          RETURN NEXT;
        END $$;

        CREATE FUNCTION workspace.v022_finish_graph_work(
          item_id uuid, worker_key varchar, expected_fence bigint, terminal_status varchar, details jsonb
        ) RETURNS void LANGUAGE plpgsql AS $$
        DECLARE current_run uuid; upstream_status varchar;
        BEGIN
          IF terminal_status NOT IN ('completed','failed','cancelled') THEN
            RAISE EXCEPTION 'invalid terminal work status';
          END IF;
          IF terminal_status = 'completed' AND EXISTS (
            SELECT 1 FROM workspace.v022_graph_work_item
            WHERE graph_work_item_id=item_id AND cancel_requested_at IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'cancelled graph work cannot publish completion';
          END IF;
          UPDATE workspace.v022_graph_work_item SET status=terminal_status,
            lease_owner=NULL, lease_expires_at=NULL, failure_details=details, updated_at=now()
          WHERE graph_work_item_id=item_id AND status='running' AND lease_owner=worker_key
            AND fencing_token=expected_fence AND lease_expires_at >= now();
          IF NOT FOUND THEN RAISE EXCEPTION 'stale or invalid graph work fencing token'; END IF;
          IF terminal_status IN ('failed','cancelled') THEN
            WITH RECURSIVE affected(id) AS (
              SELECT downstream_work_item_id FROM workspace.v022_graph_work_dependency
               WHERE upstream_work_item_id=item_id AND dependency_kind='required'
              UNION
              SELECT d.downstream_work_item_id FROM workspace.v022_graph_work_dependency d
               JOIN affected a ON a.id=d.upstream_work_item_id WHERE d.dependency_kind='required'
            )
            UPDATE workspace.v022_graph_work_item target SET
              status=CASE terminal_status WHEN 'failed' THEN 'blocked_upstream_failed' ELSE 'blocked_upstream_cancelled' END,
              failure_details=jsonb_build_object('upstream_work_item_id',item_id), updated_at=now()
            WHERE target.graph_work_item_id IN (SELECT id FROM affected) AND target.status='queued';
          END IF;
          FOR current_run IN SELECT graph_run_id FROM workspace.v022_graph_work_consumer WHERE graph_work_item_id=item_id LOOP
            SELECT status INTO upstream_status FROM workspace.v022_graph_work_item
              WHERE graph_work_item_id IN (
                SELECT graph_work_item_id FROM workspace.v022_graph_work_consumer WHERE graph_run_id=current_run)
                AND status IN ('failed','cancelled','blocked_upstream_failed','blocked_upstream_cancelled') LIMIT 1;
            IF upstream_status IS NOT NULL THEN
              UPDATE workspace.v022_graph_run SET status=CASE WHEN upstream_status IN ('cancelled','blocked_upstream_cancelled') THEN 'cancelled' ELSE 'failed' END,
                completed_at=now(), failure_details=jsonb_build_object('work_status',upstream_status)
                WHERE graph_run_id=current_run AND status IN ('ready','running');
            ELSIF NOT EXISTS (
              SELECT 1 FROM workspace.v022_graph_work_item w JOIN workspace.v022_graph_work_consumer c ON c.graph_work_item_id=w.graph_work_item_id
              WHERE c.graph_run_id=current_run AND w.status NOT IN ('completed','reused')) THEN
              UPDATE workspace.v022_graph_run SET status='completed', completed_at=now()
                WHERE graph_run_id=current_run AND status IN ('ready','running');
            END IF;
          END LOOP;
        END $$;

        CREATE FUNCTION workspace.v022_release_graph_run(run_id uuid)
        RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM 1 FROM workspace.v022_graph_run WHERE graph_run_id=run_id FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'graph run not found'; END IF;
          UPDATE workspace.v022_graph_run
            SET cancel_requested_at=coalesce(cancel_requested_at,now()),
                status=CASE WHEN status IN ('planning','ready') THEN 'cancelled' ELSE status END,
                completed_at=CASE WHEN status IN ('planning','ready') THEN now() ELSE completed_at END
            WHERE graph_run_id=run_id AND status NOT IN ('completed','failed','cancelled');
          UPDATE workspace.v022_graph_work_consumer SET released_at=coalesce(released_at,now())
            WHERE graph_run_id=run_id;
          UPDATE workspace.v022_graph_work_item item SET
            status=CASE WHEN item.status='queued' THEN 'cancelled' ELSE item.status END,
            cancel_requested_at=CASE WHEN item.status IN ('queued','running') THEN now() ELSE item.cancel_requested_at END,
            updated_at=now()
          WHERE item.graph_work_item_id IN (
            SELECT graph_work_item_id FROM workspace.v022_graph_work_consumer WHERE graph_run_id=run_id
          ) AND item.status IN ('queued','running') AND NOT EXISTS (
            SELECT 1 FROM workspace.v022_graph_work_consumer active
            WHERE active.graph_work_item_id=item.graph_work_item_id AND active.released_at IS NULL
          );
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS workspace.v022_release_graph_run(uuid)")
    op.execute("DROP FUNCTION IF EXISTS workspace.v022_finish_graph_work(uuid,varchar,bigint,varchar,jsonb)")
    op.execute("DROP FUNCTION IF EXISTS workspace.v022_claim_graph_work(uuid,varchar,integer)")
    op.execute("DROP FUNCTION IF EXISTS workspace.v022_mark_graph_ready(uuid,integer)")
    op.drop_table("v022_graph_work_consumer", schema="workspace")
    op.drop_table("v022_graph_work_dependency", schema="workspace")
    op.drop_index("ix_v022_graph_work_claim", table_name="v022_graph_work_item", schema="workspace")
    op.drop_table("v022_graph_work_item", schema="workspace")
    op.drop_table("v022_graph_run", schema="workspace")
