# ruff: noqa: E501
"""Add persistent v0.22 Graph Draft roots and immutable revisions.

Revision ID: 20260810_56_v022_graph_draft
Revises: 20260810_55_v022_projection
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260810_56_v022_graph_draft"
down_revision: str | None = "20260810_55_v022_projection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_graph_draft (
          graph_draft_id uuid PRIMARY KEY,
          catalog_release_id uuid NOT NULL REFERENCES workspace.v022_catalog_release,
          researcher_key varchar(160) NOT NULL,
          draft_key varchar(180) NOT NULL,
          name varchar(240) NOT NULL,
          current_revision integer NOT NULL CHECK (current_revision >= 1),
          status varchar(24) NOT NULL CHECK (status IN ('draft','archived')),
          asset_context_fingerprint varchar(64) NOT NULL CHECK (asset_context_fingerprint ~ '^[0-9a-f]{64}$'),
          resolved_data_binding_fingerprint varchar(64) NOT NULL CHECK (resolved_data_binding_fingerprint ~ '^[0-9a-f]{64}$'),
          last_compiled_research_graph_id uuid NULL REFERENCES workspace.compiled_research_graph,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (researcher_key, draft_key)
        );
        CREATE TABLE workspace.v022_graph_draft_revision (
          graph_draft_id uuid NOT NULL REFERENCES workspace.v022_graph_draft,
          revision integer NOT NULL CHECK (revision >= 1),
          intent_document jsonb NOT NULL,
          selection_fingerprint varchar(64) NOT NULL CHECK (selection_fingerprint ~ '^[0-9a-f]{64}$'),
          derived_state_fingerprint varchar(64) NOT NULL CHECK (derived_state_fingerprint ~ '^[0-9a-f]{64}$'),
          derived_view_document jsonb NOT NULL,
          created_by varchar(160) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (graph_draft_id, revision)
        );
        CREATE TABLE workspace.v022_graph_draft_event (
          graph_draft_event_id uuid PRIMARY KEY,
          graph_draft_id uuid NOT NULL REFERENCES workspace.v022_graph_draft,
          base_revision integer NOT NULL CHECK (base_revision >= 1),
          resulting_revision integer NOT NULL CHECK (resulting_revision >= 1),
          event_type varchar(100) NOT NULL,
          event_document jsonb NOT NULL,
          actor_key varchar(160) NOT NULL,
          idempotency_key uuid NOT NULL,
          request_fingerprint varchar(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          response_document jsonb NOT NULL,
          applied boolean NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (graph_draft_id, base_revision)
            REFERENCES workspace.v022_graph_draft_revision (graph_draft_id, revision),
          FOREIGN KEY (graph_draft_id, resulting_revision)
            REFERENCES workspace.v022_graph_draft_revision (graph_draft_id, revision),
          UNIQUE (graph_draft_id, actor_key, idempotency_key)
        );
        CREATE TABLE workspace.v022_graph_change_preview (
          graph_change_preview_id uuid PRIMARY KEY,
          graph_draft_id uuid NOT NULL REFERENCES workspace.v022_graph_draft,
          base_revision integer NOT NULL CHECK (base_revision >= 1),
          impact_token varchar(64) NOT NULL CHECK (impact_token ~ '^[0-9a-f]{64}$'),
          request_document jsonb NOT NULL,
          next_intent_document jsonb NOT NULL,
          impact_document jsonb NOT NULL,
          created_by varchar(160) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          consumed_at timestamptz NULL,
          FOREIGN KEY (graph_draft_id, base_revision)
            REFERENCES workspace.v022_graph_draft_revision (graph_draft_id, revision),
          UNIQUE (impact_token)
        );
        CREATE INDEX ix_v022_graph_draft_event_history
          ON workspace.v022_graph_draft_event (graph_draft_id, occurred_at, graph_draft_event_id);
        """
    )
    for table in ("v022_graph_draft_revision", "v022_graph_draft_event"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON workspace.{table} FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def downgrade() -> None:
    for table in ("v022_graph_draft_event", "v022_graph_draft_revision"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON workspace.{table}")
    op.execute("DROP INDEX IF EXISTS workspace.ix_v022_graph_draft_event_history")
    for table in (
        "v022_graph_change_preview",
        "v022_graph_draft_event",
        "v022_graph_draft_revision",
        "v022_graph_draft",
    ):
        op.drop_table(table, schema="workspace")
