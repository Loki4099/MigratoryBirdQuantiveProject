# ruff: noqa: E501
"""Allow exact supervised axes on the shared Aggregation Run identity.

Revision ID: 20260818_124_v022_supervised_run
Revises: 20260818_123_v022_train_artifact
"""

from __future__ import annotations

from alembic import op

revision = "20260818_124_v022_supervised_run"
down_revision = "20260818_123_v022_train_artifact"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE aggregation.aggregation_run
          ADD COLUMN target_version_id uuid NULL
            REFERENCES aggregation.target_version,
          ADD COLUMN training_preset_version_id uuid NULL
            REFERENCES aggregation.training_preset_version;

        DROP TRIGGER trg_validate_v022_deterministic_run
          ON aggregation.aggregation_run;
        DROP FUNCTION aggregation.validate_v022_deterministic_run();

        CREATE FUNCTION aggregation.validate_v022_aggregation_run()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar;
        DECLARE aggregation_family uuid;
        DECLARE target_family uuid;
        DECLARE training_family uuid;
        DECLARE target_status varchar;
        DECLARE training_status varchar;
        BEGIN
          IF TG_OP='UPDATE' AND (
            OLD.aggregation_version_id IS DISTINCT FROM NEW.aggregation_version_id OR
            OLD.parameter_preset_version_id IS DISTINCT FROM
              NEW.parameter_preset_version_id OR
            OLD.target_version_id IS DISTINCT FROM NEW.target_version_id OR
            OLD.training_preset_version_id IS DISTINCT FROM
              NEW.training_preset_version_id OR
            OLD.execution_fingerprint IS DISTINCT FROM NEW.execution_fingerprint OR
            OLD.resolved_parameters IS DISTINCT FROM NEW.resolved_parameters OR
            OLD.executor_version IS DISTINCT FROM NEW.executor_version OR
            OLD.environment_fingerprint IS DISTINCT FROM NEW.environment_fingerprint
          ) THEN
            RAISE EXCEPTION 'Aggregation Run immutable execution identity changed';
          END IF;

          SELECT version.execution_mode,version.aggregation_family_id
            INTO mode,aggregation_family
            FROM aggregation.aggregation_version version
           WHERE version.aggregation_version_id=NEW.aggregation_version_id;
          IF mode='deterministic' THEN
            IF NEW.target_version_id IS NOT NULL OR
               NEW.training_preset_version_id IS NOT NULL THEN
              RAISE EXCEPTION
                'Deterministic Aggregation Run cannot bind supervised axes';
            END IF;
          ELSIF mode='supervised' THEN
            IF NEW.target_version_id IS NULL OR
               NEW.training_preset_version_id IS NULL THEN
              RAISE EXCEPTION
                'Supervised Aggregation Run requires Target and Training Preset';
            END IF;
            SELECT definition.aggregation_family_id,artifact.status
              INTO target_family,target_status
              FROM aggregation.target_version version
              JOIN aggregation.target_definition definition
                ON definition.target_definition_id=version.target_definition_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             WHERE version.target_version_id=NEW.target_version_id;
            SELECT definition.aggregation_family_id,artifact.status
              INTO training_family,training_status
              FROM aggregation.training_preset_version version
              JOIN aggregation.training_preset_definition definition
                ON definition.training_preset_definition_id=
                   version.training_preset_definition_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             WHERE version.training_preset_version_id=
                   NEW.training_preset_version_id;
            IF target_family IS DISTINCT FROM aggregation_family OR
               training_family IS DISTINCT FROM aggregation_family OR
               target_status IS DISTINCT FROM 'published' OR
               training_status IS DISTINCT FROM 'published' THEN
              RAISE EXCEPTION
                'Supervised Aggregation Run axes are not exact published family components';
            END IF;
          ELSE
            RAISE EXCEPTION 'Aggregation Run execution mode is invalid';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_validate_v022_aggregation_run
          BEFORE INSERT OR UPDATE ON aggregation.aggregation_run
          FOR EACH ROW EXECUTE FUNCTION
            aggregation.validate_v022_aggregation_run();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM aggregation.aggregation_run
             WHERE target_version_id IS NOT NULL OR
                   training_preset_version_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade M124 with supervised Aggregation Runs';
          END IF;
        END $$;

        DROP TRIGGER trg_validate_v022_aggregation_run
          ON aggregation.aggregation_run;
        DROP FUNCTION aggregation.validate_v022_aggregation_run();

        ALTER TABLE aggregation.aggregation_run
          DROP COLUMN training_preset_version_id,
          DROP COLUMN target_version_id;

        CREATE FUNCTION aggregation.validate_v022_deterministic_run()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar;
        BEGIN
          SELECT execution_mode INTO mode FROM aggregation.aggregation_version
            WHERE aggregation_version_id=NEW.aggregation_version_id;
          IF mode <> 'deterministic' THEN
            RAISE EXCEPTION 'M2 runtime enables deterministic aggregation only';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_validate_v022_deterministic_run
          BEFORE INSERT ON aggregation.aggregation_run
          FOR EACH ROW EXECUTE FUNCTION
            aggregation.validate_v022_deterministic_run();
        """
    )
