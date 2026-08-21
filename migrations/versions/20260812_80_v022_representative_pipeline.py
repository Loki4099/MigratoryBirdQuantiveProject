# ruff: noqa: E501
"""Bind frozen Dataset snapshots to v0.22 Raw Payload Manifests.

Revision ID: 20260812_80_v022_representative
Revises: 20260812_79_v022_suite_runtime
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_80_v022_representative"
down_revision: str | None = "20260812_79_v022_suite_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE data.v022_dataset_payload_binding (
          dataset_payload_binding_id uuid PRIMARY KEY,
          dataset_publication_id uuid NOT NULL
            REFERENCES data.dataset_publication ON DELETE RESTRICT,
          feature_version_id uuid NOT NULL
            REFERENCES processing.feature_version ON DELETE RESTRICT,
          payload_manifest_id uuid NOT NULL UNIQUE
            REFERENCES data.payload_manifest ON DELETE RESTRICT,
          known_at_start timestamptz NOT NULL,
          known_at_end timestamptz NOT NULL,
          snapshot_semantics jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (dataset_publication_id,feature_version_id),
          CHECK (known_at_start <= known_at_end),
          CHECK (
            jsonb_typeof(snapshot_semantics)='object' AND
            snapshot_semantics->>'semantic_mode'=
              'back_adjusted_historical_research' AND
            snapshot_semantics->>'known_at_rule'=
              'xnys_session_close_at_utc' AND
            snapshot_semantics->>'input_revision_rule'=
              'dataset_publication_id' AND
            snapshot_semantics->>'price_basis'='back_adjusted' AND
            snapshot_semantics->'product_warning_required'='true'::jsonb
          )
        );
        CREATE TRIGGER trg_v022_dataset_payload_binding_append_only
          BEFORE UPDATE OR DELETE ON data.v022_dataset_payload_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v022_dataset_payload_binding_append_only "
        "ON data.v022_dataset_payload_binding"
    )
    op.drop_table("v022_dataset_payload_binding", schema="data")
