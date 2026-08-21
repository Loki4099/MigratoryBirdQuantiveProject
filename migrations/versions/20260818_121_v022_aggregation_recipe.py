# ruff: noqa: E501
"""Add native hierarchical taxonomy and compiled Recipe identities.

Revision ID: 20260818_121_v022_agg_recipe
Revises: 20260818_120_v022_launch_batch
"""

from __future__ import annotations

from alembic import op

revision = "20260818_121_v022_agg_recipe"
down_revision = "20260818_120_v022_launch_batch"
branch_labels = None
depends_on = None


_COMPONENT_KIND_CONSTRAINT = (
    "ck_v022_catalog_release_component_ck_v022_component_kind"
)
_COMPONENT_KINDS = (
    "'payload_contract_family','payload_contract_version','payload_compatibility',"
    "'physical_encoding_version','feature_family','feature_variant','feature_version',"
    "'processing_node_definition','processing_node_variant','processing_node_version',"
    "'aggregation_family','aggregation_version',"
    "'aggregation_parameter_preset_definition',"
    "'aggregation_parameter_preset_version','aggregation_target_definition',"
    "'aggregation_target_version','aggregation_training_preset_definition',"
    "'aggregation_training_preset_version','strategy_family',"
    "'strategy_variant','strategy_version','defense_family','defense_variant',"
    "'defense_version','strategy_parameter_preset_definition',"
    "'strategy_parameter_preset_version','defense_timing_family',"
    "'defense_timing_variant','defense_timing_version','defense_allocation_family',"
    "'defense_allocation_variant','defense_allocation_version'"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workspace.v022_catalog_release_component "
        f"DROP CONSTRAINT {_COMPONENT_KIND_CONSTRAINT}"
    )
    op.execute(
        "ALTER TABLE workspace.v022_catalog_release_component "
        f"ADD CONSTRAINT {_COMPONENT_KIND_CONSTRAINT} CHECK (component_kind IN ("
        f"{_COMPONENT_KINDS},'aggregation_feature_taxonomy_version'))"
    )
    op.execute(
        """
        CREATE TABLE aggregation.v022_feature_taxonomy_version (
          feature_taxonomy_version_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          taxonomy_key varchar(180) NOT NULL CHECK (btrim(taxonomy_key)<>''),
          version_number integer NOT NULL CHECK (version_number >= 1),
          taxonomy_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (taxonomy_fingerprint ~ '^[0-9a-f]{64}$'),
          taxonomy_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (taxonomy_key,version_number),
          CHECK (jsonb_typeof(taxonomy_document)='object'),
          CHECK (taxonomy_document->>'taxonomy_key'=taxonomy_key),
          CHECK ((taxonomy_document->>'version_number')::integer=version_number),
          CHECK (jsonb_typeof(taxonomy_document->'entries')='array'),
          CHECK (jsonb_array_length(taxonomy_document->'entries')>0),
          CHECK (
            taxonomy_fingerprint=
              strategy.v022_strategy_parameter_fingerprint(taxonomy_document)
          )
        );

        CREATE TABLE workspace.v022_compiled_aggregation_recipe (
          compiled_aggregation_instance_id uuid PRIMARY KEY
            REFERENCES workspace.compiled_aggregation_instance,
          artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          feature_taxonomy_version_id uuid NOT NULL
            REFERENCES aggregation.v022_feature_taxonomy_version,
          recipe_fingerprint varchar(64) NOT NULL
            CHECK (recipe_fingerprint ~ '^[0-9a-f]{64}$'),
          recipe_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (jsonb_typeof(recipe_document)='object'),
          CHECK (recipe_document->>'recipe_kind'='native_hierarchical_equal_v2'),
          CHECK (recipe_document->>'family_key'='hierarchical_weighted_mean'),
          CHECK (
            recipe_document->>'parameter_preset_key'=
              'active_dimension_equal_component_equal_v1'
          ),
          CHECK (jsonb_typeof(recipe_document->'dimensions')='array'),
          CHECK (jsonb_array_length(recipe_document->'dimensions')>0),
          CHECK (
            recipe_fingerprint=
              strategy.v022_strategy_parameter_fingerprint(recipe_document)
          )
        );

        CREATE FUNCTION workspace.guard_v022_compiled_aggregation_recipe()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row lineage.artifact%ROWTYPE;
        DECLARE family_key_value text;
        DECLARE preset_key_value text;
        DECLARE taxonomy_fingerprint_value text;
        DECLARE taxonomy_artifact_id_value uuid;
        BEGIN
          SELECT * INTO artifact_row FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          IF artifact_row.artifact_type<>'v022_compiled_aggregation_recipe' OR
             artifact_row.artifact_key<>NEW.recipe_fingerprint OR
             artifact_row.version_number<>1 OR artifact_row.status<>'published' OR
             artifact_row.semantic_fingerprint<>NEW.recipe_fingerprint THEN
            RAISE EXCEPTION 'compiled Aggregation Recipe Artifact identity is invalid';
          END IF;

          SELECT family.family_key,definition.parameter_preset_key
            INTO family_key_value,preset_key_value
            FROM workspace.compiled_aggregation_instance instance
            JOIN aggregation.aggregation_version version
              ON version.aggregation_version_id=instance.aggregation_version_id
            JOIN aggregation.aggregation_family family
              ON family.aggregation_family_id=version.aggregation_family_id
            LEFT JOIN aggregation.parameter_preset_version preset_version
              ON preset_version.parameter_preset_version_id=
                 instance.parameter_preset_version_id
            LEFT JOIN aggregation.parameter_preset_definition definition
              ON definition.parameter_preset_definition_id=
                 preset_version.parameter_preset_definition_id
           WHERE instance.compiled_aggregation_instance_id=
                 NEW.compiled_aggregation_instance_id;
          IF family_key_value<>'hierarchical_weighted_mean' OR
             preset_key_value<>
               'hierarchical_weighted_mean__active_dimension_equal_component_equal_v1' THEN
            RAISE EXCEPTION 'compiled Aggregation Recipe owner is not native hierarchical v2';
          END IF;

          SELECT taxonomy_fingerprint,artifact_id
            INTO taxonomy_fingerprint_value,taxonomy_artifact_id_value
            FROM aggregation.v022_feature_taxonomy_version
           WHERE feature_taxonomy_version_id=NEW.feature_taxonomy_version_id;
          IF NEW.recipe_document->>'taxonomy_fingerprint' IS DISTINCT FROM
             taxonomy_fingerprint_value THEN
            RAISE EXCEPTION 'compiled Aggregation Recipe taxonomy identity drift';
          END IF;
          IF (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>1 OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=taxonomy_artifact_id_value
                  AND dependency.role='feature_taxonomy'
                  AND dependency.ordinal=0
             ) THEN
            RAISE EXCEPTION 'compiled Aggregation Recipe taxonomy lineage is incomplete';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER guard_v022_compiled_aggregation_recipe
          BEFORE INSERT ON workspace.v022_compiled_aggregation_recipe
          FOR EACH ROW EXECUTE FUNCTION
            workspace.guard_v022_compiled_aggregation_recipe();

        CREATE FUNCTION aggregation.reject_v022_aggregation_recipe_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'v0.22 Aggregation taxonomy and Recipe identities are append-only';
        END $$;

        CREATE TRIGGER reject_v022_feature_taxonomy_mutation
          BEFORE UPDATE OR DELETE ON aggregation.v022_feature_taxonomy_version
          FOR EACH ROW EXECUTE FUNCTION
            aggregation.reject_v022_aggregation_recipe_mutation();
        CREATE TRIGGER reject_v022_compiled_aggregation_recipe_mutation
          BEFORE UPDATE OR DELETE ON workspace.v022_compiled_aggregation_recipe
          FOR EACH ROW EXECUTE FUNCTION
            aggregation.reject_v022_aggregation_recipe_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM workspace.v022_compiled_aggregation_recipe) OR
             EXISTS (SELECT 1 FROM aggregation.v022_feature_taxonomy_version) THEN
            RAISE EXCEPTION
              'Cannot downgrade nonempty v0.22 Aggregation taxonomy/Recipe identities';
          END IF;
        END $$;
        DROP TRIGGER reject_v022_compiled_aggregation_recipe_mutation
          ON workspace.v022_compiled_aggregation_recipe;
        DROP TRIGGER reject_v022_feature_taxonomy_mutation
          ON aggregation.v022_feature_taxonomy_version;
        DROP TRIGGER guard_v022_compiled_aggregation_recipe
          ON workspace.v022_compiled_aggregation_recipe;
        DROP FUNCTION aggregation.reject_v022_aggregation_recipe_mutation();
        DROP FUNCTION workspace.guard_v022_compiled_aggregation_recipe();
        DROP TABLE workspace.v022_compiled_aggregation_recipe;
        DROP TABLE aggregation.v022_feature_taxonomy_version;
        """
    )
    op.execute(
        "ALTER TABLE workspace.v022_catalog_release_component "
        f"DROP CONSTRAINT {_COMPONENT_KIND_CONSTRAINT}"
    )
    op.execute(
        "ALTER TABLE workspace.v022_catalog_release_component "
        f"ADD CONSTRAINT {_COMPONENT_KIND_CONSTRAINT} "
        f"CHECK (component_kind IN ({_COMPONENT_KINDS}))"
    )
