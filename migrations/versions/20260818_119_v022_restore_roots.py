"""Validate restore evidence against the derived v0.22 strong-root view.

Revision ID: 20260818_119_v022_restore_root
Revises: 20260818_118_v022_strong_root
"""

from __future__ import annotations

from alembic import op

revision = "20260818_119_v022_restore_root"
down_revision = "20260818_118_v022_strong_root"
branch_labels = None
depends_on = None


_ORIGINAL_RETENTION_PREDICATE = (
    "source.retention_class NOT IN ('product','evidence','export','legal_hold') OR"
)


def _function(strong_root_predicate: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION ops.validate_v022_restore_drill_object()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE snapshot_artifact uuid; source record;
    BEGIN
      SELECT artifact_id INTO snapshot_artifact FROM ops.v022_restore_drill_snapshot
       WHERE restore_drill_snapshot_id=NEW.restore_drill_snapshot_id;
      PERFORM data.assert_artifact_draft(snapshot_artifact);
      SELECT manifest.artifact_id,manifest.retention_class,
             manifest.materialization_state,object.object_content_hash,
             object.byte_size,object.object_state,object.verification_status INTO source
        FROM data.payload_manifest manifest
        JOIN data.payload_manifest_partition link
          ON link.payload_manifest_id=manifest.payload_manifest_id
        JOIN data.payload_partition partition
          ON partition.payload_partition_id=link.payload_partition_id
        JOIN data.payload_object object
          ON object.payload_object_id=partition.payload_object_id
       WHERE manifest.payload_manifest_id=NEW.payload_manifest_id
         AND object.payload_object_id=NEW.payload_object_id;
      IF source.artifact_id IS DISTINCT FROM NEW.manifest_artifact_id OR
         {strong_root_predicate}
         source.materialization_state IS DISTINCT FROM 'materialized' OR
         source.object_content_hash IS DISTINCT FROM NEW.expected_content_hash OR
         source.byte_size IS DISTINCT FROM NEW.expected_byte_size OR
         source.object_state IS DISTINCT FROM 'published' OR
         source.verification_status IS DISTINCT FROM 'verified' THEN
        RAISE EXCEPTION 'Restore drill object is not an exact verified strong-root object';
      END IF;
      RETURN NEW;
    END $$;
    """


def upgrade() -> None:
    op.execute(
        _function(
            "NOT EXISTS (SELECT 1 FROM data.v022_strong_payload_manifest root "
            "WHERE root.payload_manifest_id=NEW.payload_manifest_id) OR"
        )
    )


def downgrade() -> None:
    op.execute(_function(_ORIGINAL_RETENTION_PREDICATE))
