# ruff: noqa: E501
"""Add formal restore and rollback drill evidence.

Revision ID: 20260812_73_v022_recovery
Revises: 20260812_72_v022_ops_slo
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_73_v022_recovery"
down_revision: str | None = "20260812_72_v022_ops_slo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ops.v022_restore_drill_snapshot (
          restore_drill_snapshot_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          backup_record_id uuid NOT NULL REFERENCES ops.backup_record,
          started_at timestamptz NOT NULL,
          completed_at timestamptz NOT NULL,
          expected_object_count integer NOT NULL CHECK (expected_object_count >= 0),
          verified_object_count integer NOT NULL CHECK (verified_object_count >= 0),
          ready_for_gate boolean NOT NULL,
          blocker_codes jsonb NOT NULL,
          drill_document jsonb NOT NULL,
          drill_fingerprint varchar(64) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (started_at < completed_at),
          CHECK (verified_object_count <= expected_object_count),
          CHECK (jsonb_typeof(blocker_codes)='array'),
          CHECK (jsonb_typeof(drill_document)='object' AND drill_document<>'{}'::jsonb),
          CHECK (ready_for_gate = (expected_object_count > 0 AND
                                   verified_object_count=expected_object_count AND
                                   jsonb_array_length(blocker_codes)=0)),
          CHECK (drill_fingerprint ~ '^[0-9a-f]{64}$')
        );
        CREATE TABLE ops.v022_restore_drill_object (
          restore_drill_snapshot_id uuid NOT NULL REFERENCES ops.v022_restore_drill_snapshot,
          ordinal integer NOT NULL CHECK (ordinal >= 1),
          payload_manifest_id uuid NOT NULL REFERENCES data.payload_manifest,
          manifest_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          payload_object_id uuid NOT NULL REFERENCES data.payload_object,
          expected_content_hash varchar(64) NOT NULL,
          observed_content_hash varchar(64) NULL,
          expected_byte_size bigint NOT NULL CHECK (expected_byte_size >= 0),
          observed_byte_size bigint NULL CHECK (observed_byte_size >= 0),
          passed boolean NOT NULL,
          blocker_code varchar(200) NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (restore_drill_snapshot_id,ordinal),
          UNIQUE (restore_drill_snapshot_id,payload_manifest_id,payload_object_id),
          CHECK (expected_content_hash ~ '^[0-9a-f]{64}$'),
          CHECK (observed_content_hash IS NULL OR observed_content_hash ~ '^[0-9a-f]{64}$'),
          CHECK ((passed AND blocker_code IS NULL AND
                  observed_content_hash=expected_content_hash AND
                  observed_byte_size=expected_byte_size) OR
                 (NOT passed AND btrim(blocker_code)<>''))
        );
        CREATE TABLE ops.v022_rollback_drill_snapshot (
          rollback_drill_snapshot_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          rollback_transition_artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          completed_at timestamptz NOT NULL,
          duplicate_product_decision_count integer NOT NULL
            CHECK (duplicate_product_decision_count >= 0),
          post_rollback_product_decision_count integer NOT NULL
            CHECK (post_rollback_product_decision_count >= 0),
          v021_read_probe_passed boolean NOT NULL,
          v022_submission_rejected boolean NOT NULL,
          exact_pinned_replay_passed boolean NOT NULL,
          ready_for_gate boolean NOT NULL,
          blocker_codes jsonb NOT NULL,
          probe_document jsonb NOT NULL,
          drill_fingerprint varchar(64) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (jsonb_typeof(blocker_codes)='array'),
          CHECK (jsonb_typeof(probe_document)='object' AND probe_document<>'{}'::jsonb),
          CHECK (ready_for_gate = (duplicate_product_decision_count=0 AND
                                   post_rollback_product_decision_count=0 AND
                                   v021_read_probe_passed AND v022_submission_rejected AND
                                   exact_pinned_replay_passed AND
                                   jsonb_array_length(blocker_codes)=0)),
          CHECK (drill_fingerprint ~ '^[0-9a-f]{64}$')
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION ops.validate_v022_recovery_artifact_type()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_type varchar; expected_type varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          expected_type := CASE TG_TABLE_NAME
            WHEN 'v022_restore_drill_snapshot' THEN 'v022_restore_drill_evidence'
            WHEN 'v022_rollback_drill_snapshot' THEN 'v022_rollback_drill_evidence'
          END;
          SELECT artifact_type INTO actual_type FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          IF actual_type IS DISTINCT FROM expected_type THEN
            RAISE EXCEPTION 'Recovery drill row requires its formal Artifact type';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_restore_drill_artifact
          BEFORE INSERT ON ops.v022_restore_drill_snapshot
          FOR EACH ROW EXECUTE FUNCTION ops.validate_v022_recovery_artifact_type();
        CREATE TRIGGER trg_v022_rollback_drill_artifact
          BEFORE INSERT ON ops.v022_rollback_drill_snapshot
          FOR EACH ROW EXECUTE FUNCTION ops.validate_v022_recovery_artifact_type();

        CREATE FUNCTION ops.validate_v022_restore_drill_object()
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
             source.retention_class NOT IN ('product','evidence','export','legal_hold') OR
             source.materialization_state IS DISTINCT FROM 'materialized' OR
             source.object_content_hash IS DISTINCT FROM NEW.expected_content_hash OR
             source.byte_size IS DISTINCT FROM NEW.expected_byte_size OR
             source.object_state IS DISTINCT FROM 'published' OR
             source.verification_status IS DISTINCT FROM 'verified' THEN
            RAISE EXCEPTION 'Restore drill object is not an exact verified strong-root object';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_restore_drill_object_validate
          BEFORE INSERT ON ops.v022_restore_drill_object
          FOR EACH ROW EXECUTE FUNCTION ops.validate_v022_restore_drill_object();

        CREATE FUNCTION ops.validate_v022_restore_drill_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_count integer; actual_passed integer; canonical_blockers jsonb;
                backup_status varchar; database_restored_at timestamptz;
        BEGIN
          SELECT count(*),count(*) FILTER (WHERE passed),
                 coalesce(jsonb_agg(blocker_code ORDER BY ordinal)
                   FILTER (WHERE NOT passed),'[]'::jsonb)
            INTO actual_count,actual_passed,canonical_blockers
            FROM ops.v022_restore_drill_object
           WHERE restore_drill_snapshot_id=NEW.restore_drill_snapshot_id;
          SELECT status,restore_tested_at INTO backup_status,database_restored_at
            FROM ops.backup_record WHERE backup_record_id=NEW.backup_record_id;
          IF backup_status IS DISTINCT FROM 'restore_tested' OR database_restored_at IS NULL THEN
            RAISE EXCEPTION 'Restore drill requires a successfully restored DB backup';
          END IF;
          IF actual_count=0 THEN
            canonical_blockers := canonical_blockers ||
              jsonb_build_array('no_materialized_strong_root_objects');
          END IF;
          IF database_restored_at<NEW.started_at OR database_restored_at>NEW.completed_at THEN
            canonical_blockers := canonical_blockers ||
              jsonb_build_array('database_restore_outside_drill_window');
          END IF;
          IF actual_count<>NEW.expected_object_count OR
             actual_passed<>NEW.verified_object_count OR
             canonical_blockers<>NEW.blocker_codes THEN
            RAISE EXCEPTION 'Restore drill object evidence is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_restore_drill_complete
          AFTER INSERT ON ops.v022_restore_drill_snapshot
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION ops.validate_v022_restore_drill_complete();

        CREATE FUNCTION ops.validate_v022_rollback_drill()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE transition record; duplicate_count integer; post_count integer;
                canonical_blockers jsonb := '[]'::jsonb;
        BEGIN
          SELECT release.* INTO transition FROM workspace.v022_release_transition release
            JOIN lineage.artifact artifact ON artifact.artifact_id=release.artifact_id
           WHERE release.artifact_id=NEW.rollback_transition_artifact_id
             AND artifact.status='published';
          IF transition.to_state IS DISTINCT FROM 'maintenance_read_only' THEN
            RAISE EXCEPTION 'Rollback drill requires a published maintenance transition';
          END IF;
          SELECT count(*) INTO duplicate_count FROM (
            SELECT execution_version_id,decision_session_id
              FROM product.v022_product_decision
             GROUP BY execution_version_id,decision_session_id HAVING count(*)>1
          ) duplicate;
          SELECT count(*) INTO post_count FROM product.v022_product_decision
           WHERE created_at>transition.requested_at;
          IF duplicate_count>0 THEN
            canonical_blockers := canonical_blockers ||
              jsonb_build_array('duplicate_product_decision_identity');
          END IF;
          IF post_count>0 THEN
            canonical_blockers := canonical_blockers ||
              jsonb_build_array('product_decision_published_after_rollback');
          END IF;
          IF NOT NEW.v021_read_probe_passed THEN
            canonical_blockers := canonical_blockers ||
              jsonb_build_array('v021_read_probe_failed');
          END IF;
          IF NOT NEW.v022_submission_rejected THEN
            canonical_blockers := canonical_blockers ||
              jsonb_build_array('v022_submission_not_rejected');
          END IF;
          IF NOT NEW.exact_pinned_replay_passed THEN
            canonical_blockers := canonical_blockers ||
              jsonb_build_array('exact_pinned_replay_failed');
          END IF;
          IF NEW.duplicate_product_decision_count IS DISTINCT FROM duplicate_count OR
             NEW.post_rollback_product_decision_count IS DISTINCT FROM post_count OR
             NEW.completed_at<transition.requested_at OR
             NEW.blocker_codes<>canonical_blockers THEN
            RAISE EXCEPTION 'Rollback drill Product Decision counts are not canonical';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_rollback_drill_validate
          BEFORE INSERT ON ops.v022_rollback_drill_snapshot
          FOR EACH ROW EXECUTE FUNCTION ops.validate_v022_rollback_drill();

        CREATE TRIGGER trg_v022_restore_drill_append_only
          BEFORE UPDATE OR DELETE ON ops.v022_restore_drill_snapshot
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_restore_drill_object_append_only
          BEFORE UPDATE OR DELETE ON ops.v022_restore_drill_object
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_rollback_drill_append_only
          BEFORE UPDATE OR DELETE ON ops.v022_rollback_drill_snapshot
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS ops.validate_v022_rollback_drill() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS ops.validate_v022_restore_drill_complete() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS ops.validate_v022_restore_drill_object() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS ops.validate_v022_recovery_artifact_type() CASCADE")
    op.drop_table("v022_rollback_drill_snapshot", schema="ops")
    op.drop_table("v022_restore_drill_object", schema="ops")
    op.drop_table("v022_restore_drill_snapshot", schema="ops")
