# ruff: noqa: E501
"""Bind exact Graph Draft revisions to immutable compiler input identities.

Revision ID: 20260810_57_v022_compile_bridge
Revises: 20260810_56_v022_graph_draft
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260810_57_v022_compile_bridge"
down_revision: str | None = "20260810_56_v022_graph_draft"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_graph_draft_compile_binding (
          graph_draft_id uuid NOT NULL,
          graph_draft_revision integer NOT NULL CHECK (graph_draft_revision >= 1),
          draft_intent_id uuid NOT NULL UNIQUE REFERENCES workspace.v022_draft_intent,
          bridge_contract_version varchar(40) NOT NULL CHECK (bridge_contract_version = 'v0.22.0'),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (graph_draft_id, graph_draft_revision),
          FOREIGN KEY (graph_draft_id, graph_draft_revision)
            REFERENCES workspace.v022_graph_draft_revision (graph_draft_id, revision)
        );
        CREATE TRIGGER trg_v022_graph_draft_compile_binding_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_graph_draft_compile_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v022_graph_draft_compile_binding_append_only "
        "ON workspace.v022_graph_draft_compile_binding"
    )
    op.drop_table("v022_graph_draft_compile_binding", schema="workspace")
