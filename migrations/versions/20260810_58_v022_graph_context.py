# ruff: noqa: E501
"""Persist resolved Asset Context and Data Binding documents on Graph Drafts.

Revision ID: 20260810_58_v022_graph_context
Revises: 20260810_57_v022_compile_bridge
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260810_58_v022_graph_context"
down_revision: str | None = "20260810_57_v022_compile_bridge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE workspace.v022_graph_draft
          ADD COLUMN asset_context_document jsonb NULL,
          ADD COLUMN resolved_data_binding_document jsonb NULL;
        UPDATE workspace.v022_graph_draft
           SET asset_context_document = jsonb_build_object(
                 'contract_version','legacy_fingerprint_only',
                 'fingerprint',asset_context_fingerprint
               ),
               resolved_data_binding_document = jsonb_build_object(
                 'contract_version','legacy_fingerprint_only',
                 'fingerprint',resolved_data_binding_fingerprint
               );
        ALTER TABLE workspace.v022_graph_draft
          ALTER COLUMN asset_context_document SET NOT NULL,
          ALTER COLUMN resolved_data_binding_document SET NOT NULL;
        """
    )


def downgrade() -> None:
    op.drop_column("v022_graph_draft", "resolved_data_binding_document", schema="workspace")
    op.drop_column("v022_graph_draft", "asset_context_document", schema="workspace")
