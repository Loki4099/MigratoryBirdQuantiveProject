# ruff: noqa: E501
"""Bind one exact Trainable Ensemble Spec to an Aggregation Run.

Revision ID: 20260818_126_v022_ensemble_run
Revises: 20260818_125_v022_train_ensemble
"""

from __future__ import annotations

from alembic import op

revision = "20260818_126_v022_ensemble_run"
down_revision = "20260818_125_v022_train_ensemble"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE aggregation.aggregation_run
          ADD COLUMN ensemble_spec_id uuid NULL
            REFERENCES aggregation.v022_trainable_ensemble_spec;

        DROP TRIGGER trg_validate_v022_aggregation_run
          ON aggregation.aggregation_run;
        DROP FUNCTION aggregation.validate_v022_aggregation_run();

        CREATE FUNCTION aggregation.validate_v022_aggregation_run()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar;
        DECLARE aggregation_family uuid;
        DECLARE target_family uuid;
        DECLARE training_family uuid;
        DECLARE target_status varchar;
        DECLARE training_status varchar;
        DECLARE spec_version uuid;
        DECLARE spec_status varchar;
        DECLARE direct_bound boolean;
        DECLARE ensemble_bound boolean;
        BEGIN
          IF TG_OP='UPDATE' AND (
            OLD.aggregation_version_id IS DISTINCT FROM NEW.aggregation_version_id OR
            OLD.parameter_preset_version_id IS DISTINCT FROM
              NEW.parameter_preset_version_id OR
            OLD.target_version_id IS DISTINCT FROM NEW.target_version_id OR
            OLD.training_preset_version_id IS DISTINCT FROM
              NEW.training_preset_version_id OR
            OLD.ensemble_spec_id IS DISTINCT FROM NEW.ensemble_spec_id OR
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
          direct_bound := NEW.target_version_id IS NOT NULL AND
                          NEW.training_preset_version_id IS NOT NULL;
          ensemble_bound := NEW.ensemble_spec_id IS NOT NULL;
          IF (NEW.target_version_id IS NULL) <>
             (NEW.training_preset_version_id IS NULL) THEN
            RAISE EXCEPTION
              'Aggregation Run direct supervised axes must be complete';
          END IF;
          IF mode='deterministic' THEN
            IF direct_bound OR ensemble_bound THEN
              RAISE EXCEPTION
                'Deterministic Aggregation Run cannot bind supervised identity';
            END IF;
          ELSIF mode='supervised' THEN
            IF direct_bound = ensemble_bound THEN
              RAISE EXCEPTION
                'Supervised Aggregation Run requires direct axes or one Ensemble Spec';
            END IF;
            IF direct_bound THEN
              SELECT definition.aggregation_family_id,artifact.status
                INTO target_family,target_status
                FROM aggregation.target_version version
                JOIN aggregation.target_definition definition
                  ON definition.target_definition_id=version.target_definition_id
                JOIN lineage.artifact artifact
                  ON artifact.artifact_id=version.artifact_id
               WHERE version.target_version_id=NEW.target_version_id;
              SELECT definition.aggregation_family_id,artifact.status
                INTO training_family,training_status
                FROM aggregation.training_preset_version version
                JOIN aggregation.training_preset_definition definition
                  ON definition.training_preset_definition_id=
                     version.training_preset_definition_id
                JOIN lineage.artifact artifact
                  ON artifact.artifact_id=version.artifact_id
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
              SELECT spec.aggregation_version_id,artifact.status
                INTO spec_version,spec_status
                FROM aggregation.v022_trainable_ensemble_spec spec
                JOIN lineage.artifact artifact
                  ON artifact.artifact_id=spec.artifact_id
               WHERE spec.ensemble_spec_id=NEW.ensemble_spec_id;
              IF spec_version IS DISTINCT FROM NEW.aggregation_version_id OR
                 spec_status IS DISTINCT FROM 'published' THEN
                RAISE EXCEPTION
                  'Aggregation Run Ensemble Spec is not exact and published';
              END IF;
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
             WHERE ensemble_spec_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade M126 with Trainable Ensemble Aggregation Runs';
          END IF;
        END $$;

        DROP TRIGGER trg_validate_v022_aggregation_run
          ON aggregation.aggregation_run;
        DROP FUNCTION aggregation.validate_v022_aggregation_run();

        ALTER TABLE aggregation.aggregation_run
          DROP COLUMN ensemble_spec_id;

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
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=version.artifact_id
             WHERE version.target_version_id=NEW.target_version_id;
            SELECT definition.aggregation_family_id,artifact.status
              INTO training_family,training_status
              FROM aggregation.training_preset_version version
              JOIN aggregation.training_preset_definition definition
                ON definition.training_preset_definition_id=
                   version.training_preset_definition_id
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=version.artifact_id
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
