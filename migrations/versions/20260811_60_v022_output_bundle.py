# ruff: noqa: E501
"""Add atomic v0.22 Node output bundles.

Revision ID: 20260811_60_v022_bundle
Revises: 20260811_59_v022_parity
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_60_v022_bundle"
down_revision: str | None = "20260811_59_v022_parity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE processing.node_output_bundle (
          node_output_bundle_id uuid PRIMARY KEY,
          node_run_id uuid NOT NULL UNIQUE REFERENCES processing.node_run ON DELETE RESTRICT,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact ON DELETE RESTRICT,
          bundle_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (bundle_fingerprint ~ '^[0-9a-f]{64}$'),
          output_count integer NOT NULL CHECK (output_count >= 1),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE processing.node_output_bundle_member (
          node_output_bundle_id uuid NOT NULL
            REFERENCES processing.node_output_bundle ON DELETE RESTRICT,
          output_port_key varchar(160) NOT NULL,
          payload_manifest_id uuid NOT NULL UNIQUE
            REFERENCES data.payload_manifest ON DELETE RESTRICT,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          PRIMARY KEY (node_output_bundle_id,output_port_key),
          UNIQUE (node_output_bundle_id,ordinal)
        );
        CREATE TRIGGER trg_node_output_bundle_append_only
          BEFORE UPDATE OR DELETE ON processing.node_output_bundle
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_node_output_bundle_member_append_only
          BEFORE UPDATE OR DELETE ON processing.node_output_bundle_member
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_node_output_bundle_member_append_only "
        "ON processing.node_output_bundle_member"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_node_output_bundle_append_only "
        "ON processing.node_output_bundle"
    )
    op.drop_table("node_output_bundle_member", schema="processing")
    op.drop_table("node_output_bundle", schema="processing")
