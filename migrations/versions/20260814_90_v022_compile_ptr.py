"""Bind a Graph Draft to its exact current compile command result.

Revision ID: 20260814_90_v022_compile_ptr
Revises: 20260814_89_v022_def_cap
"""

from __future__ import annotations

from alembic import op

revision = "20260814_90_v022_compile_ptr"
down_revision = "20260814_89_v022_def_cap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE workspace.v022_graph_draft
          ADD COLUMN last_compile_command_result_id uuid NULL
          REFERENCES workspace.v022_command_result(command_result_id);

        CREATE UNIQUE INDEX uq_v022_graph_draft_last_compile_command_result
          ON workspace.v022_graph_draft(last_compile_command_result_id)
          WHERE last_compile_command_result_id IS NOT NULL;

        WITH exact_candidates AS (
          SELECT draft.graph_draft_id,
                 min(command.command_result_id::text)::uuid AS command_result_id
            FROM workspace.v022_graph_draft draft
            JOIN workspace.v022_command_result command
              ON command.actor_key=draft.researcher_key
             AND command.command_kind='compile_graph_draft'
             AND command.response_document->>'graph_draft_id'=
                 draft.graph_draft_id::text
             AND command.response_document->>'graph_draft_revision'=
                 draft.current_revision::text
             AND command.response_document->>'compiled_research_graph_id'=
                 draft.last_compiled_research_graph_id::text
           WHERE draft.last_compiled_research_graph_id IS NOT NULL
           GROUP BY draft.graph_draft_id
          HAVING count(*)=1
        )
        UPDATE workspace.v022_graph_draft draft
           SET last_compile_command_result_id=candidate.command_result_id
          FROM exact_candidates candidate
         WHERE draft.graph_draft_id=candidate.graph_draft_id;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS workspace."
        "uq_v022_graph_draft_last_compile_command_result"
    )
    op.drop_column(
        "v022_graph_draft",
        "last_compile_command_result_id",
        schema="workspace",
    )
