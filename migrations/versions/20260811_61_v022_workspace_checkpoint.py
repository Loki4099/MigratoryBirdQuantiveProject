# ruff: noqa: E501
"""Pin Catalog identity per Graph Draft revision and record revision clones.

Revision ID: 20260811_61_v022_checkpoint
Revises: 20260811_60_v022_bundle
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_61_v022_checkpoint"
down_revision: str | None = "20260811_60_v022_bundle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE workspace.v022_graph_draft_revision
          ADD COLUMN catalog_release_id uuid NULL;
        UPDATE workspace.v022_graph_draft_revision revision
           SET catalog_release_id=draft.catalog_release_id
          FROM workspace.v022_graph_draft draft
         WHERE draft.graph_draft_id=revision.graph_draft_id;
        ALTER TABLE workspace.v022_graph_draft_revision
          ALTER COLUMN catalog_release_id SET NOT NULL,
          ADD CONSTRAINT fk_v022_graph_revision_catalog
            FOREIGN KEY (catalog_release_id)
            REFERENCES workspace.v022_catalog_release (catalog_release_id);

        ALTER TABLE workspace.v022_graph_draft
          ADD COLUMN cloned_from_graph_draft_id uuid NULL,
          ADD COLUMN cloned_from_revision integer NULL,
          ADD CONSTRAINT ck_v022_graph_clone_source_pair CHECK (
            (cloned_from_graph_draft_id IS NULL) = (cloned_from_revision IS NULL)
          ),
          ADD CONSTRAINT fk_v022_graph_clone_source
            FOREIGN KEY (cloned_from_graph_draft_id,cloned_from_revision)
            REFERENCES workspace.v022_graph_draft_revision (graph_draft_id,revision);
        CREATE INDEX ix_v022_graph_draft_clone_source
          ON workspace.v022_graph_draft (cloned_from_graph_draft_id,cloned_from_revision)
          WHERE cloned_from_graph_draft_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS workspace.ix_v022_graph_draft_clone_source;
        ALTER TABLE workspace.v022_graph_draft
          DROP CONSTRAINT IF EXISTS fk_v022_graph_clone_source,
          DROP CONSTRAINT IF EXISTS ck_v022_graph_clone_source_pair,
          DROP COLUMN IF EXISTS cloned_from_revision,
          DROP COLUMN IF EXISTS cloned_from_graph_draft_id;
        ALTER TABLE workspace.v022_graph_draft_revision
          DROP CONSTRAINT IF EXISTS fk_v022_graph_revision_catalog,
          DROP COLUMN IF EXISTS catalog_release_id;
        """
    )
