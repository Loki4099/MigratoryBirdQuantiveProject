# ruff: noqa: E501
"""Add immutable v0.22 Strategy Parameter Preset identities and Branch bindings.

Revision ID: 20260812_75_v022_strategy_preset
Revises: 20260812_74_v022_suite_identity
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_75_v022_strategy_preset"
down_revision: str | None = "20260812_74_v022_suite_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BASE_COMPONENT_KINDS = (
    "'payload_contract_family','payload_contract_version','payload_compatibility',"
    "'physical_encoding_version','feature_family','feature_variant','feature_version',"
    "'processing_node_definition','processing_node_variant','processing_node_version',"
    "'aggregation_family','aggregation_version',"
    "'aggregation_parameter_preset_definition',"
    "'aggregation_parameter_preset_version','aggregation_target_definition',"
    "'aggregation_target_version','aggregation_training_preset_definition',"
    "'aggregation_training_preset_version','strategy_family',"
    "'strategy_variant','strategy_version','defense_family','defense_variant',"
    "'defense_version'"
)
_STRATEGY_PRESET_COMPONENT_KINDS = (
    ",'strategy_parameter_preset_definition','strategy_parameter_preset_version'"
)
_COMPONENT_KIND_CONSTRAINT = (
    "ck_v022_catalog_release_component_ck_v022_component_kind"
)


def upgrade() -> None:
    _set_release_component_kinds(include_strategy_presets=True)
    _create_parameter_fingerprint_functions()
    op.execute(
        """
        CREATE TABLE strategy.v022_strategy_parameter_preset_definition (
          strategy_parameter_preset_definition_id uuid PRIMARY KEY,
          strategy_variant_id uuid NOT NULL
            REFERENCES strategy.v022_strategy_variant,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          preset_key varchar(240) NOT NULL CHECK (btrim(preset_key)<>''),
          name varchar(240) NOT NULL CHECK (btrim(name)<>''),
          description text NOT NULL CHECK (btrim(description)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (strategy_variant_id,preset_key),
          UNIQUE (strategy_parameter_preset_definition_id,strategy_variant_id)
        );
        CREATE TABLE strategy.v022_strategy_parameter_preset_version (
          strategy_parameter_preset_version_id uuid PRIMARY KEY,
          strategy_parameter_preset_definition_id uuid NOT NULL,
          strategy_variant_id uuid NOT NULL
            REFERENCES strategy.v022_strategy_variant,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          version_number integer NOT NULL CHECK (version_number >= 1),
          resolved_parameters jsonb NOT NULL,
          parameter_fingerprint varchar(64) NOT NULL
            CHECK (parameter_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (
            strategy_parameter_preset_definition_id,strategy_variant_id
          ) REFERENCES strategy.v022_strategy_parameter_preset_definition (
            strategy_parameter_preset_definition_id,strategy_variant_id
          ),
          UNIQUE (strategy_parameter_preset_definition_id,version_number),
          UNIQUE (strategy_variant_id,parameter_fingerprint),
          UNIQUE (strategy_parameter_preset_version_id,parameter_fingerprint),
          CHECK (jsonb_typeof(resolved_parameters)='object' AND
                 resolved_parameters<>'{}'::jsonb),
          CHECK (
            parameter_fingerprint=
              strategy.v022_strategy_parameter_fingerprint(resolved_parameters)
          )
        );
        CREATE TABLE strategy.v022_compiled_strategy_branch_preset_binding (
          compiled_strategy_branch_id uuid PRIMARY KEY
            REFERENCES strategy.v022_compiled_strategy_branch,
          strategy_parameter_preset_version_id uuid NOT NULL
            REFERENCES strategy.v022_strategy_parameter_preset_version,
          parameter_fingerprint varchar(64) NOT NULL
            CHECK (parameter_fingerprint ~ '^[0-9a-f]{64}$'),
          resolved_parameters jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (
            strategy_parameter_preset_version_id,parameter_fingerprint
          ) REFERENCES strategy.v022_strategy_parameter_preset_version (
            strategy_parameter_preset_version_id,parameter_fingerprint
          ),
          CHECK (jsonb_typeof(resolved_parameters)='object' AND
                 resolved_parameters<>'{}'::jsonb),
          CHECK (
            parameter_fingerprint=
              strategy.v022_strategy_parameter_fingerprint(resolved_parameters)
          )
        );
        """
    )
    _create_identity_guards()
    _create_identity_completeness_guards()
    _create_binding_guard()
    _create_new_graph_completeness_guard()
    _create_downstream_binding_guards()
    _create_append_only_guards()


def _create_parameter_fingerprint_functions() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        CREATE FUNCTION strategy.v022_canonical_jsonb(value jsonb)
        RETURNS text
        LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
        DECLARE serialized text;
        BEGIN
          CASE jsonb_typeof(value)
            WHEN 'object' THEN
              SELECT '{' || coalesce(
                string_agg(
                  to_jsonb(entry.key)::text || ':' ||
                  strategy.v022_canonical_jsonb(entry.value),
                  ',' ORDER BY entry.key COLLATE "C"
                ),
                ''
              ) || '}'
                INTO serialized
                FROM jsonb_each(value) AS entry;
              RETURN serialized;
            WHEN 'array' THEN
              SELECT '[' || coalesce(
                string_agg(
                  strategy.v022_canonical_jsonb(element.value),
                  ',' ORDER BY element.ordinal
                ),
                ''
              ) || ']'
                INTO serialized
                FROM jsonb_array_elements(value)
                  WITH ORDINALITY AS element(value,ordinal);
              RETURN serialized;
            ELSE
              RETURN value::text;
          END CASE;
        END $$;

        CREATE FUNCTION strategy.v022_strategy_parameter_fingerprint(value jsonb)
        RETURNS varchar(64)
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
          SELECT encode(
            digest(
              convert_to(
                '{"$canonical":"canonical-json-v2","$value":' ||
                strategy.v022_canonical_jsonb(value) || '}',
                'UTF8'
              ),
              'sha256'
            ),
            'hex'
          )::varchar(64)
        $$;
        """
    )


def _set_release_component_kinds(*, include_strategy_presets: bool) -> None:
    kinds = _BASE_COMPONENT_KINDS
    if include_strategy_presets:
        kinds += _STRATEGY_PRESET_COMPONENT_KINDS
    op.execute(
        "ALTER TABLE workspace.v022_catalog_release_component "
        f"DROP CONSTRAINT {_COMPONENT_KIND_CONSTRAINT}"
    )
    op.execute(
        "ALTER TABLE workspace.v022_catalog_release_component "
        f"ADD CONSTRAINT {_COMPONENT_KIND_CONSTRAINT} "
        f"CHECK (component_kind IN ({kinds}))"
    )


def _create_identity_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION strategy.validate_v022_strategy_parameter_preset_definition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE preset_artifact_type varchar; preset_artifact_version integer;
                preset_artifact_key varchar; variant_key_value varchar;
                variant_artifact_type varchar; variant_artifact_status varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,version_number,artifact_key
            INTO preset_artifact_type,preset_artifact_version,preset_artifact_key
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT variant.variant_key,artifact.artifact_type,artifact.status
            INTO variant_key_value,variant_artifact_type,variant_artifact_status
            FROM strategy.v022_strategy_variant variant
            JOIN lineage.artifact artifact ON artifact.artifact_id=variant.artifact_id
           WHERE variant.strategy_variant_id=NEW.strategy_variant_id;
          IF preset_artifact_type IS DISTINCT FROM
               'v022_strategy_parameter_preset_definition' OR
             preset_artifact_version IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'Strategy Parameter Preset Definition requires its exact v1 Artifact';
          END IF;
          IF variant_artifact_type IS DISTINCT FROM 'v022_strategy_variant' OR
             variant_artifact_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Strategy Parameter Preset Definition requires its exact published Variant';
          END IF;
          IF preset_artifact_key IS DISTINCT FROM
               variant_key_value || '__' || NEW.preset_key THEN
            RAISE EXCEPTION 'Strategy Parameter Preset Definition key does not match its Variant-scoped Artifact key';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_strategy_parameter_preset_definition_validate
          BEFORE INSERT ON strategy.v022_strategy_parameter_preset_definition
          FOR EACH ROW
          EXECUTE FUNCTION strategy.validate_v022_strategy_parameter_preset_definition();

        CREATE FUNCTION strategy.validate_v022_strategy_parameter_preset_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE preset_artifact_type varchar; preset_artifact_version integer;
                preset_artifact_key varchar; definition_artifact_key varchar;
                definition_preset_key varchar; variant_key_value varchar;
                definition_artifact_type varchar; definition_artifact_status varchar;
                variant_artifact_type varchar; variant_artifact_status varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,version_number,artifact_key
            INTO preset_artifact_type,preset_artifact_version,preset_artifact_key
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT artifact.artifact_key,definition.preset_key,
                 artifact.artifact_type,artifact.status
            INTO definition_artifact_key,definition_preset_key,
                 definition_artifact_type,definition_artifact_status
            FROM strategy.v022_strategy_parameter_preset_definition definition
            JOIN lineage.artifact artifact ON artifact.artifact_id=definition.artifact_id
           WHERE definition.strategy_parameter_preset_definition_id=
                 NEW.strategy_parameter_preset_definition_id;
          SELECT variant.variant_key,artifact.artifact_type,artifact.status
            INTO variant_key_value,variant_artifact_type,variant_artifact_status
            FROM strategy.v022_strategy_variant variant
            JOIN lineage.artifact artifact ON artifact.artifact_id=variant.artifact_id
           WHERE variant.strategy_variant_id=NEW.strategy_variant_id;
          IF preset_artifact_type IS DISTINCT FROM
               'v022_strategy_parameter_preset_version' OR
             preset_artifact_version IS DISTINCT FROM NEW.version_number THEN
            RAISE EXCEPTION 'Strategy Parameter Preset Version requires its exact versioned Artifact';
          END IF;
          IF definition_artifact_type IS DISTINCT FROM
               'v022_strategy_parameter_preset_definition' OR
             definition_artifact_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Strategy Parameter Preset Version requires its exact published Definition';
          END IF;
          IF variant_artifact_type IS DISTINCT FROM 'v022_strategy_variant' OR
             variant_artifact_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Strategy Parameter Preset Version requires its exact published Variant';
          END IF;
          IF preset_artifact_key IS DISTINCT FROM definition_artifact_key OR
             preset_artifact_key IS DISTINCT FROM
               variant_key_value || '__' || definition_preset_key THEN
            RAISE EXCEPTION 'Strategy Parameter Preset Version key does not match its Definition and Variant';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_strategy_parameter_preset_version_validate
          BEFORE INSERT ON strategy.v022_strategy_parameter_preset_version
          FOR EACH ROW
          EXECUTE FUNCTION strategy.validate_v022_strategy_parameter_preset_version();
        """
    )


def _create_identity_completeness_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION strategy.validate_v022_strategy_parameter_preset_definition_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE preset_artifact_type_value varchar;
                preset_artifact_status_value varchar;
                preset_artifact_version_value integer;
                variant_artifact_id_value uuid;
                variant_artifact_type_value varchar;
                variant_artifact_status_value varchar;
                dependency_count_value integer;
        BEGIN
          SELECT artifact_type,status,version_number
            INTO preset_artifact_type_value,preset_artifact_status_value,
                 preset_artifact_version_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT variant.artifact_id,artifact.artifact_type,artifact.status
            INTO variant_artifact_id_value,variant_artifact_type_value,
                 variant_artifact_status_value
            FROM strategy.v022_strategy_variant variant
            JOIN lineage.artifact artifact ON artifact.artifact_id=variant.artifact_id
           WHERE variant.strategy_variant_id=NEW.strategy_variant_id;
          SELECT count(*) INTO dependency_count_value
            FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id;
          IF preset_artifact_type_value IS DISTINCT FROM
               'v022_strategy_parameter_preset_definition' OR
             preset_artifact_status_value IS DISTINCT FROM 'published' OR
             preset_artifact_version_value IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'Strategy Parameter Preset Definition Artifact is not exactly published';
          END IF;
          IF variant_artifact_type_value IS DISTINCT FROM 'v022_strategy_variant' OR
             variant_artifact_status_value IS DISTINCT FROM 'published' OR
             dependency_count_value<>1 OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=variant_artifact_id_value
                  AND dependency.role='strategy_variant'
                  AND dependency.ordinal=0
             ) THEN
            RAISE EXCEPTION 'Strategy Parameter Preset Definition lineage is not exact';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_strategy_parameter_preset_definition_complete
          AFTER INSERT ON strategy.v022_strategy_parameter_preset_definition
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION strategy.validate_v022_strategy_parameter_preset_definition_complete();

        CREATE FUNCTION strategy.validate_v022_strategy_parameter_preset_version_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE preset_artifact_type_value varchar;
                preset_artifact_status_value varchar;
                preset_artifact_version_value integer;
                definition_artifact_id_value uuid;
                definition_artifact_type_value varchar;
                definition_artifact_status_value varchar;
                variant_artifact_type_value varchar;
                variant_artifact_status_value varchar;
                dependency_count_value integer;
        BEGIN
          SELECT artifact_type,status,version_number
            INTO preset_artifact_type_value,preset_artifact_status_value,
                 preset_artifact_version_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT definition.artifact_id,artifact.artifact_type,artifact.status
            INTO definition_artifact_id_value,definition_artifact_type_value,
                 definition_artifact_status_value
            FROM strategy.v022_strategy_parameter_preset_definition definition
            JOIN lineage.artifact artifact
              ON artifact.artifact_id=definition.artifact_id
           WHERE definition.strategy_parameter_preset_definition_id=
                 NEW.strategy_parameter_preset_definition_id;
          SELECT artifact.artifact_type,artifact.status
            INTO variant_artifact_type_value,variant_artifact_status_value
            FROM strategy.v022_strategy_variant variant
            JOIN lineage.artifact artifact ON artifact.artifact_id=variant.artifact_id
           WHERE variant.strategy_variant_id=NEW.strategy_variant_id;
          SELECT count(*) INTO dependency_count_value
            FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id;
          IF preset_artifact_type_value IS DISTINCT FROM
               'v022_strategy_parameter_preset_version' OR
             preset_artifact_status_value IS DISTINCT FROM 'published' OR
             preset_artifact_version_value IS DISTINCT FROM NEW.version_number THEN
            RAISE EXCEPTION 'Strategy Parameter Preset Version Artifact is not exactly published';
          END IF;
          IF definition_artifact_type_value IS DISTINCT FROM
               'v022_strategy_parameter_preset_definition' OR
             definition_artifact_status_value IS DISTINCT FROM 'published' OR
             variant_artifact_type_value IS DISTINCT FROM 'v022_strategy_variant' OR
             variant_artifact_status_value IS DISTINCT FROM 'published' OR
             dependency_count_value<>1 OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=definition_artifact_id_value
                  AND dependency.role='strategy_parameter_preset_definition'
                  AND dependency.ordinal=0
             ) THEN
            RAISE EXCEPTION 'Strategy Parameter Preset Version lineage is not exact';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_strategy_parameter_preset_version_complete
          AFTER INSERT ON strategy.v022_strategy_parameter_preset_version
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION strategy.validate_v022_strategy_parameter_preset_version_complete();
        """
    )


def _create_binding_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION strategy.validate_v022_compiled_strategy_branch_preset_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE graph_id_value uuid; aggregation_graph_id_value uuid;
                graph_artifact_id_value uuid; graph_catalog_release_id_value uuid;
                graph_artifact_type_value varchar; graph_artifact_status_value varchar;
                release_artifact_type_value varchar; release_artifact_status_value varchar;
                branch_variant_id_value uuid; variant_artifact_id_value uuid;
                variant_artifact_type_value varchar; variant_artifact_status_value varchar;
                strategy_version_artifact_id_value uuid;
                strategy_version_artifact_type_value varchar;
                strategy_version_artifact_status_value varchar;
                preset_variant_id_value uuid; preset_fingerprint_value varchar;
                preset_parameters_value jsonb; preset_artifact_id_value uuid;
                preset_artifact_type_value varchar; preset_artifact_status_value varchar;
                definition_artifact_id_value uuid;
                definition_artifact_type_value varchar;
                definition_artifact_status_value varchar;
        BEGIN
          SELECT graph.compiled_research_graph_id,
                 aggregation_instance.compiled_research_graph_id,
                 graph.artifact_id,graph.catalog_release_id,
                 graph_artifact.artifact_type,graph_artifact.status,
                 release_artifact.artifact_type,release_artifact.status,
                 strategy_version.strategy_variant_id,variant.artifact_id,
                 variant_artifact.artifact_type,variant_artifact.status,
                 strategy_version.artifact_id,strategy_artifact.artifact_type,
                 strategy_artifact.status
            INTO graph_id_value,aggregation_graph_id_value,
                 graph_artifact_id_value,graph_catalog_release_id_value,
                 graph_artifact_type_value,graph_artifact_status_value,
                 release_artifact_type_value,release_artifact_status_value,
                 branch_variant_id_value,variant_artifact_id_value,
                 variant_artifact_type_value,variant_artifact_status_value,
                 strategy_version_artifact_id_value,
                 strategy_version_artifact_type_value,
                 strategy_version_artifact_status_value
            FROM strategy.v022_compiled_strategy_branch branch
            JOIN workspace.compiled_research_graph graph
              ON graph.compiled_research_graph_id=branch.compiled_research_graph_id
            JOIN workspace.compiled_aggregation_instance aggregation_instance
              ON aggregation_instance.compiled_aggregation_instance_id=
                 branch.compiled_aggregation_instance_id
            JOIN lineage.artifact graph_artifact
              ON graph_artifact.artifact_id=graph.artifact_id
            JOIN workspace.v022_catalog_release release
              ON release.catalog_release_id=graph.catalog_release_id
            JOIN lineage.artifact release_artifact
              ON release_artifact.artifact_id=release.artifact_id
            JOIN strategy.v022_strategy_version strategy_version
              ON strategy_version.strategy_version_id=branch.strategy_version_id
            JOIN lineage.artifact strategy_artifact
              ON strategy_artifact.artifact_id=strategy_version.artifact_id
            JOIN strategy.v022_strategy_variant variant
              ON variant.strategy_variant_id=strategy_version.strategy_variant_id
            JOIN lineage.artifact variant_artifact
              ON variant_artifact.artifact_id=variant.artifact_id
           WHERE branch.compiled_strategy_branch_id=NEW.compiled_strategy_branch_id;
          IF graph_artifact_id_value IS NULL THEN
            RAISE EXCEPTION 'Strategy Parameter Preset binding requires an exact compiled Branch';
          END IF;
          IF aggregation_graph_id_value IS DISTINCT FROM graph_id_value THEN
            RAISE EXCEPTION 'Strategy Parameter Preset binding requires a same-Graph Aggregation Branch';
          END IF;
          PERFORM data.assert_artifact_draft(graph_artifact_id_value);
          SELECT version.strategy_variant_id,version.parameter_fingerprint,
                 version.resolved_parameters,version.artifact_id,
                 version_artifact.artifact_type,version_artifact.status,
                 definition.artifact_id,definition_artifact.artifact_type,
                 definition_artifact.status
            INTO preset_variant_id_value,preset_fingerprint_value,
                 preset_parameters_value,preset_artifact_id_value,
                 preset_artifact_type_value,preset_artifact_status_value,
                 definition_artifact_id_value,definition_artifact_type_value,
                 definition_artifact_status_value
            FROM strategy.v022_strategy_parameter_preset_version version
            JOIN lineage.artifact version_artifact
              ON version_artifact.artifact_id=version.artifact_id
            JOIN strategy.v022_strategy_parameter_preset_definition definition
              ON definition.strategy_parameter_preset_definition_id=
                 version.strategy_parameter_preset_definition_id
            JOIN lineage.artifact definition_artifact
              ON definition_artifact.artifact_id=definition.artifact_id
           WHERE version.strategy_parameter_preset_version_id=
                 NEW.strategy_parameter_preset_version_id;
          IF graph_artifact_type_value IS DISTINCT FROM
               'v022_compiled_research_graph' OR
             graph_artifact_status_value IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'Strategy Parameter Preset binding requires its exact draft Graph';
          END IF;
          IF release_artifact_type_value IS DISTINCT FROM 'v022_catalog_release' OR
             release_artifact_status_value IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Strategy Parameter Preset binding requires an exact published Catalog Release';
          END IF;
          IF NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=graph_artifact_id_value
                  AND dependency.depends_on_artifact_id=(
                    SELECT artifact_id FROM workspace.v022_catalog_release
                     WHERE catalog_release_id=graph_catalog_release_id_value
                  )
                  AND dependency.role='catalog_release'
                  AND dependency.ordinal=0
             ) THEN
            RAISE EXCEPTION 'Strategy Parameter Preset binding requires exact Graph Catalog lineage';
          END IF;
          IF strategy_version_artifact_type_value IS DISTINCT FROM
               'v022_strategy_version' OR
             strategy_version_artifact_status_value IS DISTINCT FROM 'published' OR
             variant_artifact_type_value IS DISTINCT FROM 'v022_strategy_variant' OR
             variant_artifact_status_value IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Strategy Parameter Preset binding requires its exact published Strategy identities';
          END IF;
          IF preset_artifact_type_value IS DISTINCT FROM
               'v022_strategy_parameter_preset_version' OR
             preset_artifact_status_value IS DISTINCT FROM 'published' OR
             definition_artifact_type_value IS DISTINCT FROM
               'v022_strategy_parameter_preset_definition' OR
             definition_artifact_status_value IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Strategy Parameter Preset binding requires its exact published Preset identities';
          END IF;
          IF branch_variant_id_value IS DISTINCT FROM preset_variant_id_value THEN
            RAISE EXCEPTION 'Strategy Branch and Parameter Preset belong to different Variants';
          END IF;
          IF NEW.parameter_fingerprint IS DISTINCT FROM preset_fingerprint_value OR
             NEW.resolved_parameters IS DISTINCT FROM preset_parameters_value THEN
            RAISE EXCEPTION 'Strategy Parameter Preset binding does not reproduce the exact resolved parameters';
          END IF;
          IF NOT EXISTS (
               SELECT 1 FROM workspace.v022_catalog_release_component component
                JOIN lineage.artifact artifact
                  ON artifact.artifact_id=component.component_artifact_id
               WHERE component.catalog_release_id=graph_catalog_release_id_value
                 AND component.component_artifact_id=variant_artifact_id_value
                 AND component.component_kind='strategy_variant'
                 AND component.component_key=artifact.artifact_key
                 AND component.component_version=artifact.version_number
                 AND component.component_fingerprint=artifact.semantic_fingerprint
             ) OR NOT EXISTS (
               SELECT 1 FROM workspace.v022_catalog_release_component component
                JOIN lineage.artifact artifact
                  ON artifact.artifact_id=component.component_artifact_id
               WHERE component.catalog_release_id=graph_catalog_release_id_value
                 AND component.component_artifact_id=strategy_version_artifact_id_value
                 AND component.component_kind='strategy_version'
                 AND component.component_key=artifact.artifact_key
                 AND component.component_version=artifact.version_number
                 AND component.component_fingerprint=artifact.semantic_fingerprint
             ) OR NOT EXISTS (
               SELECT 1 FROM workspace.v022_catalog_release_component component
                JOIN lineage.artifact artifact
                  ON artifact.artifact_id=component.component_artifact_id
               WHERE component.catalog_release_id=graph_catalog_release_id_value
                 AND component.component_artifact_id=definition_artifact_id_value
                 AND component.component_kind=
                     'strategy_parameter_preset_definition'
                 AND component.component_key=artifact.artifact_key
                 AND component.component_version=artifact.version_number
                 AND component.component_fingerprint=artifact.semantic_fingerprint
             ) OR NOT EXISTS (
               SELECT 1 FROM workspace.v022_catalog_release_component component
                JOIN lineage.artifact artifact
                  ON artifact.artifact_id=component.component_artifact_id
               WHERE component.catalog_release_id=graph_catalog_release_id_value
                 AND component.component_artifact_id=preset_artifact_id_value
                 AND component.component_kind='strategy_parameter_preset_version'
                 AND component.component_key=artifact.artifact_key
                 AND component.component_version=artifact.version_number
                 AND component.component_fingerprint=artifact.semantic_fingerprint
             ) THEN
            RAISE EXCEPTION 'Strategy Branch or Parameter Preset is outside the Graph Catalog Release';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_compiled_strategy_branch_preset_binding_validate
          BEFORE INSERT
          ON strategy.v022_compiled_strategy_branch_preset_binding
          FOR EACH ROW
          EXECUTE FUNCTION strategy.validate_v022_compiled_strategy_branch_preset_binding();
        """
    )


def _create_new_graph_completeness_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION workspace.validate_v022_new_graph_strategy_preset_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_branch_count integer; actual_binding_count integer;
                missing_binding_count integer; graph_artifact_type_value varchar;
                graph_artifact_status_value varchar; preset_component_count integer;
        BEGIN
          SELECT count(*) INTO preset_component_count
            FROM workspace.v022_catalog_release_component component
           WHERE component.catalog_release_id=NEW.catalog_release_id
             AND component.component_kind=
                 'strategy_parameter_preset_version';
          IF preset_component_count=0 THEN
            RETURN NEW;
          END IF;
          SELECT count(*) INTO actual_branch_count
            FROM strategy.v022_compiled_strategy_branch branch
           WHERE branch.compiled_research_graph_id=NEW.compiled_research_graph_id;
          SELECT count(*) INTO actual_binding_count
            FROM strategy.v022_compiled_strategy_branch branch
            JOIN strategy.v022_compiled_strategy_branch_preset_binding binding
              ON binding.compiled_strategy_branch_id=branch.compiled_strategy_branch_id
           WHERE branch.compiled_research_graph_id=NEW.compiled_research_graph_id;
          SELECT count(*) INTO missing_binding_count
            FROM strategy.v022_compiled_strategy_branch branch
           WHERE branch.compiled_research_graph_id=NEW.compiled_research_graph_id
             AND NOT EXISTS (
               SELECT 1
                 FROM strategy.v022_compiled_strategy_branch_preset_binding binding
                WHERE binding.compiled_strategy_branch_id=
                      branch.compiled_strategy_branch_id
             );
          SELECT artifact_type,status
            INTO graph_artifact_type_value,graph_artifact_status_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF actual_branch_count<>NEW.strategy_branch_count OR
             actual_binding_count<>NEW.strategy_branch_count OR
             missing_binding_count<>0 THEN
            RAISE EXCEPTION 'New v0.22 Graph requires one exact Parameter Preset binding for every Strategy Branch';
          END IF;
          IF graph_artifact_type_value IS DISTINCT FROM
               'v022_compiled_research_graph' OR
             graph_artifact_status_value IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'New v0.22 Graph requires its exact published Artifact';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_new_graph_strategy_preset_complete
          AFTER INSERT ON workspace.compiled_research_graph
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION workspace.validate_v022_new_graph_strategy_preset_complete();
        """
    )


def _create_downstream_binding_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.validate_v022_configuration_strategy_parameter_preset()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_version_id uuid; expected_version_number integer;
                expected_preset_key varchar; expected_fingerprint varchar;
                expected_parameters jsonb; expected_artifact_id uuid;
                actual_preset jsonb;
        BEGIN
          SELECT binding.strategy_parameter_preset_version_id,
                 version.version_number,definition.preset_key,
                 binding.parameter_fingerprint,binding.resolved_parameters,
                 version.artifact_id
            INTO expected_version_id,expected_version_number,expected_preset_key,
                 expected_fingerprint,expected_parameters,expected_artifact_id
            FROM strategy.v022_compiled_strategy_branch_preset_binding binding
            JOIN strategy.v022_strategy_parameter_preset_version version
              ON version.strategy_parameter_preset_version_id=
                 binding.strategy_parameter_preset_version_id
            JOIN strategy.v022_strategy_parameter_preset_definition definition
              ON definition.strategy_parameter_preset_definition_id=
                 version.strategy_parameter_preset_definition_id
           WHERE binding.compiled_strategy_branch_id=
                 NEW.compiled_strategy_branch_id;
          actual_preset := NEW.semantic_identity_document #>
                           '{strategy,parameter_preset}';
          IF expected_version_id IS NULL THEN
            RAISE EXCEPTION 'New v0.22 Configuration Snapshot requires an exact Strategy Parameter Preset binding';
          END IF;
          IF actual_preset IS DISTINCT FROM jsonb_build_object(
               'preset_key',expected_preset_key,
               'version_id',expected_version_id::text,
               'version_number',expected_version_number,
               'parameter_fingerprint',expected_fingerprint,
               'resolved_parameters',expected_parameters
             ) THEN
            RAISE EXCEPTION 'Configuration Snapshot does not reproduce its exact Strategy Parameter Preset';
          END IF;
          IF NOT EXISTS (
               SELECT 1
                 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=expected_artifact_id
                  AND dependency.role='strategy_parameter_preset'
                  AND dependency.ordinal=0
             ) THEN
            RAISE EXCEPTION 'Configuration Snapshot requires exact Strategy Parameter Preset lineage';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_configuration_snapshot_strategy_preset
          BEFORE INSERT
          ON experiment.v022_research_configuration_snapshot
          FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_configuration_strategy_parameter_preset();

        CREATE FUNCTION experiment.require_v022_branch_strategy_parameter_preset()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM strategy.v022_compiled_strategy_branch_preset_binding binding
             WHERE binding.compiled_strategy_branch_id=
                   NEW.compiled_strategy_branch_id
          ) THEN
            RAISE EXCEPTION 'New v0.22 Experiment identity requires an exact Strategy Parameter Preset binding';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_suite_branch_strategy_preset
          BEFORE INSERT
          ON experiment.v022_research_suite_branch
          FOR EACH ROW
          EXECUTE FUNCTION experiment.require_v022_branch_strategy_parameter_preset();

        CREATE FUNCTION experiment.require_v022_graph_strategy_parameter_presets()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (
            SELECT 1
              FROM strategy.v022_compiled_strategy_branch branch
             WHERE branch.compiled_research_graph_id=
                   NEW.compiled_research_graph_id
               AND NOT EXISTS (
                 SELECT 1
                   FROM strategy.v022_compiled_strategy_branch_preset_binding binding
                  WHERE binding.compiled_strategy_branch_id=
                        branch.compiled_strategy_branch_id
               )
          ) THEN
            RAISE EXCEPTION 'New v0.22 Graph Run binding requires exact Strategy Parameter Presets';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_suite_graph_run_strategy_presets
          BEFORE INSERT
          ON experiment.v022_research_suite_graph_run_binding
          FOR EACH ROW
          EXECUTE FUNCTION experiment.require_v022_graph_strategy_parameter_presets();
        """
    )


def _create_append_only_guards() -> None:
    for table in (
        "v022_strategy_parameter_preset_definition",
        "v022_strategy_parameter_preset_version",
        "v022_compiled_strategy_branch_preset_binding",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON strategy.{table} FOR EACH ROW "
            "EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "experiment.require_v022_graph_strategy_parameter_presets() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "experiment.require_v022_branch_strategy_parameter_preset() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "experiment.validate_v022_configuration_strategy_parameter_preset() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "workspace.validate_v022_new_graph_strategy_preset_complete() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "strategy.validate_v022_compiled_strategy_branch_preset_binding() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "strategy.validate_v022_strategy_parameter_preset_version_complete() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "strategy.validate_v022_strategy_parameter_preset_definition_complete() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "strategy.validate_v022_strategy_parameter_preset_version() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "strategy.validate_v022_strategy_parameter_preset_definition() CASCADE"
    )
    op.drop_table("v022_compiled_strategy_branch_preset_binding", schema="strategy")
    op.drop_table("v022_strategy_parameter_preset_version", schema="strategy")
    op.drop_table("v022_strategy_parameter_preset_definition", schema="strategy")
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "strategy.v022_strategy_parameter_fingerprint(jsonb)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS strategy.v022_canonical_jsonb(jsonb)"
    )
    _set_release_component_kinds(include_strategy_presets=False)
