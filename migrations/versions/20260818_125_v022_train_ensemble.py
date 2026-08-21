# ruff: noqa: E501
"""Freeze multi-member supervised Aggregation ensemble specifications.

Revision ID: 20260818_125_v022_train_ensemble
Revises: 20260818_124_v022_supervised_run
"""

from __future__ import annotations

from alembic import op

revision = "20260818_125_v022_train_ensemble"
down_revision = "20260818_124_v022_supervised_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE aggregation.v022_trainable_ensemble_spec (
          ensemble_spec_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          aggregation_version_id uuid NOT NULL
            REFERENCES aggregation.aggregation_version,
          feature_schema_version_id uuid NOT NULL
            REFERENCES aggregation.v022_feature_schema_version,
          ensemble_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (ensemble_fingerprint ~ '^[0-9a-f]{64}$'),
          artifact_semantic_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (artifact_semantic_fingerprint ~ '^[0-9a-f]{64}$'),
          member_count integer NOT NULL CHECK (member_count BETWEEN 2 AND 12),
          target_group_count integer NOT NULL
            CHECK (target_group_count BETWEEN 1 AND 12 AND
                   target_group_count <= member_count),
          ensemble_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK ((
            jsonb_typeof(ensemble_document)='object' AND
            ensemble_document->>'contract_version'='v0.22.0' AND
            ensemble_document->>'member_policy'=
              'explicit_target_training_cartesian_v1' AND
            ensemble_document->>'combination_policy'=
              'equal_within_target_equal_across_targets_v1' AND
            ensemble_document->>'missing_member_policy'='fail_closed' AND
            ensemble_document->>'final_transform'=
              'decision_date_cross_section_average_rank_centered' AND
            (ensemble_document->>'member_count')::integer=member_count AND
            (ensemble_document->>'target_group_count')::integer=
              target_group_count AND
            ensemble_document->>'aggregation_version_id'=
              aggregation_version_id::text AND
            jsonb_typeof(ensemble_document->'target_groups')='array' AND
            jsonb_array_length(ensemble_document->'target_groups')=
              target_group_count
          ) IS TRUE)
        );

        CREATE TABLE aggregation.v022_trainable_ensemble_member (
          ensemble_spec_id uuid NOT NULL
            REFERENCES aggregation.v022_trainable_ensemble_spec,
          ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 11),
          target_group_ordinal integer NOT NULL
            CHECK (target_group_ordinal BETWEEN 0 AND 11),
          member_ordinal_within_target integer NOT NULL
            CHECK (member_ordinal_within_target BETWEEN 0 AND 11),
          target_version_id uuid NOT NULL REFERENCES aggregation.target_version,
          training_preset_version_id uuid NOT NULL
            REFERENCES aggregation.training_preset_version,
          PRIMARY KEY (ensemble_spec_id,ordinal),
          UNIQUE (ensemble_spec_id,target_version_id,training_preset_version_id),
          UNIQUE (
            ensemble_spec_id,target_group_ordinal,
            member_ordinal_within_target
          )
        );

        CREATE TABLE workspace.v022_compiled_trainable_ensemble_binding (
          compiled_aggregation_instance_id uuid PRIMARY KEY
            REFERENCES workspace.compiled_aggregation_instance,
          ensemble_spec_id uuid NOT NULL
            REFERENCES aggregation.v022_trainable_ensemble_spec
        );

        DROP TRIGGER trg_validate_v022_aggregation_instance
          ON workspace.compiled_aggregation_instance;
        DROP FUNCTION workspace.validate_v022_aggregation_instance();

        CREATE FUNCTION workspace.validate_v022_aggregation_instance()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar;
        DECLARE output_contract uuid;
        BEGIN
          SELECT execution_mode,output_payload_contract_version_id
            INTO mode,output_contract
            FROM aggregation.aggregation_version
           WHERE aggregation_version_id=NEW.aggregation_version_id;
          IF mode='deterministic' AND (
            NEW.target_version_id IS NOT NULL OR
            NEW.training_preset_version_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION
              'deterministic aggregation cannot bind Target or Training Preset';
          ELSIF mode='supervised' AND (
            (NEW.target_version_id IS NULL) <>
            (NEW.training_preset_version_id IS NULL)
          ) THEN
            RAISE EXCEPTION
              'supervised aggregation axes must be both direct or both internal';
          ELSIF mode NOT IN ('deterministic','supervised') THEN
            RAISE EXCEPTION 'aggregation execution mode is invalid';
          END IF;
          IF output_contract IS DISTINCT FROM
             NEW.output_payload_contract_version_id THEN
            RAISE EXCEPTION 'aggregation output contract mismatch';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_validate_v022_aggregation_instance
          BEFORE INSERT ON workspace.compiled_aggregation_instance
          FOR EACH ROW EXECUTE FUNCTION
            workspace.validate_v022_aggregation_instance();

        CREATE FUNCTION aggregation.validate_v022_trainable_ensemble_spec()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar;
        DECLARE schema_version uuid;
        DECLARE schema_fingerprint text;
        DECLARE identity_artifact lineage.artifact%ROWTYPE;
        BEGIN
          SELECT execution_mode INTO mode
            FROM aggregation.aggregation_version
           WHERE aggregation_version_id=NEW.aggregation_version_id;
          SELECT aggregation_version_id,feature_schema_fingerprint
            INTO schema_version,schema_fingerprint
            FROM aggregation.v022_feature_schema_version
           WHERE feature_schema_version_id=NEW.feature_schema_version_id;
          SELECT * INTO identity_artifact FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          IF mode IS DISTINCT FROM 'supervised' OR
             schema_version IS DISTINCT FROM NEW.aggregation_version_id OR
             NEW.ensemble_document->>'feature_schema_fingerprint'
               IS DISTINCT FROM schema_fingerprint OR
             identity_artifact.artifact_type IS DISTINCT FROM
               'v022_trainable_ensemble_spec' OR
             identity_artifact.artifact_key IS DISTINCT FROM
               NEW.ensemble_fingerprint OR
             identity_artifact.semantic_fingerprint IS DISTINCT FROM
               NEW.artifact_semantic_fingerprint OR
             identity_artifact.status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION
              'Trainable Ensemble Spec identity is not exact and published';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_validate_v022_trainable_ensemble_spec
          BEFORE INSERT ON aggregation.v022_trainable_ensemble_spec
          FOR EACH ROW EXECUTE FUNCTION
            aggregation.validate_v022_trainable_ensemble_spec();

        CREATE FUNCTION workspace.validate_v022_compiled_trainable_ensemble_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar;
        DECLARE instance_version uuid;
        DECLARE instance_target uuid;
        DECLARE instance_training uuid;
        DECLARE spec_version uuid;
        BEGIN
          SELECT instance.aggregation_version_id,
                 instance.target_version_id,
                 instance.training_preset_version_id,
                 version.execution_mode
            INTO instance_version,instance_target,instance_training,mode
            FROM workspace.compiled_aggregation_instance instance
            JOIN aggregation.aggregation_version version
              ON version.aggregation_version_id=instance.aggregation_version_id
           WHERE instance.compiled_aggregation_instance_id=
                 NEW.compiled_aggregation_instance_id;
          SELECT aggregation_version_id INTO spec_version
            FROM aggregation.v022_trainable_ensemble_spec
           WHERE ensemble_spec_id=NEW.ensemble_spec_id;
          IF mode IS DISTINCT FROM 'supervised' OR
             instance_version IS DISTINCT FROM spec_version OR
             instance_target IS NOT NULL OR instance_training IS NOT NULL THEN
            RAISE EXCEPTION
              'Compiled Trainable Ensemble binding is not exact';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_validate_v022_compiled_trainable_ensemble_binding
          BEFORE INSERT
          ON workspace.v022_compiled_trainable_ensemble_binding
          FOR EACH ROW EXECUTE FUNCTION
            workspace.validate_v022_compiled_trainable_ensemble_binding();

        CREATE FUNCTION aggregation.validate_v022_trainable_ensemble_member()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE aggregation_family uuid;
        DECLARE target_family uuid;
        DECLARE training_family uuid;
        DECLARE target_status varchar;
        DECLARE training_status varchar;
        BEGIN
          SELECT version.aggregation_family_id
            INTO aggregation_family
            FROM aggregation.v022_trainable_ensemble_spec spec
            JOIN aggregation.aggregation_version version
              ON version.aggregation_version_id=spec.aggregation_version_id
           WHERE spec.ensemble_spec_id=NEW.ensemble_spec_id;
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
              'Trainable Ensemble member axes are not exact published family components';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_validate_v022_trainable_ensemble_member
          BEFORE INSERT ON aggregation.v022_trainable_ensemble_member
          FOR EACH ROW EXECUTE FUNCTION
            aggregation.validate_v022_trainable_ensemble_member();

        CREATE FUNCTION aggregation.close_v022_trainable_ensemble_spec()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_members integer;
        DECLARE expected_targets integer;
        DECLARE actual_members integer;
        DECLARE actual_targets integer;
        DECLARE contiguous_groups boolean;
        BEGIN
          SELECT member_count,target_group_count
            INTO expected_members,expected_targets
            FROM aggregation.v022_trainable_ensemble_spec
           WHERE ensemble_spec_id=NEW.ensemble_spec_id;
          SELECT sum(group_count),count(*),
                 bool_and(group_count=max_member_ordinal+1)
            INTO actual_members,actual_targets,contiguous_groups
            FROM (
              SELECT target_group_ordinal,count(*) AS group_count,
                     max(member_ordinal_within_target) AS max_member_ordinal
                FROM aggregation.v022_trainable_ensemble_member
               WHERE ensemble_spec_id=NEW.ensemble_spec_id
               GROUP BY target_group_ordinal
            ) grouped;
          IF actual_members IS DISTINCT FROM expected_members OR
             actual_targets IS DISTINCT FROM expected_targets OR
             contiguous_groups IS DISTINCT FROM true OR
             NOT EXISTS (
               SELECT 1
                 FROM aggregation.v022_trainable_ensemble_member
                WHERE ensemble_spec_id=NEW.ensemble_spec_id
                GROUP BY ensemble_spec_id
               HAVING min(ordinal)=0 AND max(ordinal)=count(*)-1 AND
                      min(target_group_ordinal)=0 AND
                      max(target_group_ordinal)=count(DISTINCT target_group_ordinal)-1
             ) THEN
            RAISE EXCEPTION
              'Trainable Ensemble member closure is incomplete or non-contiguous';
          END IF;
          RETURN NEW;
        END $$;

        CREATE CONSTRAINT TRIGGER trg_close_v022_trainable_ensemble_member
          AFTER INSERT ON aggregation.v022_trainable_ensemble_member
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION
            aggregation.close_v022_trainable_ensemble_spec();

        CREATE FUNCTION workspace.close_v022_supervised_aggregation_instance()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar;
        DECLARE ensemble_count integer;
        BEGIN
          SELECT execution_mode INTO mode
            FROM aggregation.aggregation_version
           WHERE aggregation_version_id=NEW.aggregation_version_id;
          SELECT count(*) INTO ensemble_count
            FROM workspace.v022_compiled_trainable_ensemble_binding
           WHERE compiled_aggregation_instance_id=
                 NEW.compiled_aggregation_instance_id;
          IF mode='deterministic' AND ensemble_count<>0 THEN
            RAISE EXCEPTION
              'Deterministic Aggregation cannot bind a Trainable Ensemble';
          ELSIF mode='supervised' AND
                NEW.target_version_id IS NULL AND ensemble_count<>1 THEN
            RAISE EXCEPTION
              'Multi-member supervised Aggregation requires one exact Ensemble Spec';
          ELSIF mode='supervised' AND
                NEW.target_version_id IS NOT NULL AND ensemble_count<>0 THEN
            RAISE EXCEPTION
              'Direct supervised Aggregation cannot also bind an Ensemble Spec';
          END IF;
          RETURN NEW;
        END $$;

        CREATE CONSTRAINT TRIGGER trg_close_v022_supervised_aggregation_instance
          AFTER INSERT ON workspace.compiled_aggregation_instance
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION
            workspace.close_v022_supervised_aggregation_instance();

        CREATE TRIGGER trg_v022_trainable_ensemble_spec_append_only
          BEFORE UPDATE OR DELETE
          ON aggregation.v022_trainable_ensemble_spec
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_trainable_ensemble_member_append_only
          BEFORE UPDATE OR DELETE
          ON aggregation.v022_trainable_ensemble_member
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_compiled_trainable_ensemble_binding_append_only
          BEFORE UPDATE OR DELETE
          ON workspace.v022_compiled_trainable_ensemble_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM aggregation.v022_trainable_ensemble_spec) OR
             EXISTS (SELECT 1 FROM aggregation.v022_trainable_ensemble_member) OR
             EXISTS (
               SELECT 1
                 FROM workspace.v022_compiled_trainable_ensemble_binding
             ) THEN
            RAISE EXCEPTION
              'Cannot downgrade M125 with Trainable Ensemble identities';
          END IF;
        END $$;

        DROP TRIGGER trg_v022_compiled_trainable_ensemble_binding_append_only
          ON workspace.v022_compiled_trainable_ensemble_binding;
        DROP TRIGGER trg_v022_trainable_ensemble_member_append_only
          ON aggregation.v022_trainable_ensemble_member;
        DROP TRIGGER trg_v022_trainable_ensemble_spec_append_only
          ON aggregation.v022_trainable_ensemble_spec;
        DROP TRIGGER trg_close_v022_supervised_aggregation_instance
          ON workspace.compiled_aggregation_instance;
        DROP FUNCTION workspace.close_v022_supervised_aggregation_instance();
        DROP TRIGGER trg_close_v022_trainable_ensemble_member
          ON aggregation.v022_trainable_ensemble_member;
        DROP FUNCTION aggregation.close_v022_trainable_ensemble_spec();
        DROP TRIGGER trg_validate_v022_trainable_ensemble_member
          ON aggregation.v022_trainable_ensemble_member;
        DROP FUNCTION aggregation.validate_v022_trainable_ensemble_member();
        DROP TRIGGER trg_validate_v022_compiled_trainable_ensemble_binding
          ON workspace.v022_compiled_trainable_ensemble_binding;
        DROP FUNCTION workspace.validate_v022_compiled_trainable_ensemble_binding();
        DROP TRIGGER trg_validate_v022_trainable_ensemble_spec
          ON aggregation.v022_trainable_ensemble_spec;
        DROP FUNCTION aggregation.validate_v022_trainable_ensemble_spec();

        DROP TABLE workspace.v022_compiled_trainable_ensemble_binding;
        DROP TABLE aggregation.v022_trainable_ensemble_member;
        DROP TABLE aggregation.v022_trainable_ensemble_spec;

        DROP TRIGGER trg_validate_v022_aggregation_instance
          ON workspace.compiled_aggregation_instance;
        DROP FUNCTION workspace.validate_v022_aggregation_instance();

        CREATE FUNCTION workspace.validate_v022_aggregation_instance()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar;
        DECLARE output_contract uuid;
        BEGIN
          SELECT execution_mode,output_payload_contract_version_id
            INTO mode,output_contract
            FROM aggregation.aggregation_version
           WHERE aggregation_version_id=NEW.aggregation_version_id;
          IF mode='deterministic' AND (
            NEW.target_version_id IS NOT NULL OR
            NEW.training_preset_version_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION
              'deterministic aggregation cannot bind Target or Training Preset';
          ELSIF mode='supervised' AND (
            NEW.target_version_id IS NULL OR
            NEW.training_preset_version_id IS NULL
          ) THEN
            RAISE EXCEPTION
              'supervised aggregation requires Target and Training Preset';
          END IF;
          IF output_contract IS DISTINCT FROM
             NEW.output_payload_contract_version_id THEN
            RAISE EXCEPTION 'aggregation output contract mismatch';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_validate_v022_aggregation_instance
          BEFORE INSERT ON workspace.compiled_aggregation_instance
          FOR EACH ROW EXECUTE FUNCTION
            workspace.validate_v022_aggregation_instance();
        """
    )
