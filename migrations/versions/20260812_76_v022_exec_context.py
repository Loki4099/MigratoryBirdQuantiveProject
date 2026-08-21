# ruff: noqa: E501
"""Add exact immutable v0.22 Compiled Execution Data Context identities.

Revision ID: 20260812_76_v022_exec_context
Revises: 20260812_75_v022_strategy_preset
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_76_v022_exec_context"
down_revision: str | None = "20260812_75_v022_strategy_preset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_compiled_execution_data_context (
          compiled_execution_data_context_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          compiled_research_graph_id uuid NOT NULL UNIQUE
            REFERENCES workspace.compiled_research_graph,
          contract_version varchar(40) NOT NULL
            CHECK (contract_version='v0.22.0'),
          asset_registry_release_id uuid NOT NULL
            REFERENCES catalog.asset_registry_release,
          asset_registry_artifact_id uuid NOT NULL
            REFERENCES lineage.artifact,
          asset_set_definition_id uuid NOT NULL
            REFERENCES catalog.asset_set_definition,
          asset_context_fingerprint varchar(64) NOT NULL
            CHECK (asset_context_fingerprint ~ '^[0-9a-f]{64}$'),
          resolved_data_binding_fingerprint varchar(64) NOT NULL
            CHECK (resolved_data_binding_fingerprint ~ '^[0-9a-f]{64}$'),
          asset_context_document jsonb NOT NULL,
          resolved_data_binding_document jsonb NOT NULL,
          input_count integer NOT NULL CHECK (input_count > 0),
          context_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (context_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (
            compiled_execution_data_context_id,compiled_research_graph_id
          ),
          CHECK (
            jsonb_typeof(asset_context_document)='object' AND
            asset_context_document<>'{}'::jsonb
          ),
          CHECK (
            jsonb_typeof(resolved_data_binding_document)='object' AND
            resolved_data_binding_document<>'{}'::jsonb
          ),
          CHECK (
            asset_context_fingerprint=
              strategy.v022_strategy_parameter_fingerprint(
                asset_context_document
              )
          ),
          CHECK (
            resolved_data_binding_fingerprint=
              strategy.v022_strategy_parameter_fingerprint(
                resolved_data_binding_document
              )
          )
        );
        CREATE TABLE workspace.v022_compiled_execution_data_input (
          compiled_execution_data_context_id uuid NOT NULL
            REFERENCES workspace.v022_compiled_execution_data_context,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          input_key varchar(160) NOT NULL CHECK (btrim(input_key)<>''),
          dataset_publication_id uuid NOT NULL
            REFERENCES data.dataset_publication,
          dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          calendar_version_id uuid NULL REFERENCES catalog.calendar_version,
          calendar_artifact_id uuid NULL REFERENCES lineage.artifact,
          coverage_start date NOT NULL,
          coverage_end date NOT NULL,
          security_ids jsonb NOT NULL,
          binding_document jsonb NOT NULL,
          binding_fingerprint varchar(64) NOT NULL
            CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (compiled_execution_data_context_id,ordinal),
          UNIQUE (compiled_execution_data_context_id,input_key),
          CHECK ((calendar_version_id IS NULL)=(calendar_artifact_id IS NULL)),
          CHECK (coverage_start <= coverage_end),
          CHECK (jsonb_typeof(security_ids)='array' AND
                 jsonb_array_length(security_ids)>0),
          CHECK (jsonb_typeof(binding_document)='object' AND
                 binding_document<>'{}'::jsonb),
          CHECK (
            binding_fingerprint=
              strategy.v022_strategy_parameter_fingerprint(binding_document)
          )
        );
        """
    )
    _create_context_insert_guard()
    _create_input_insert_guard()
    _create_context_completeness_guard()
    _create_runtime_admission_guard()
    _create_append_only_guards()


def _create_context_insert_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION workspace.validate_v022_compiled_execution_data_context()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE context_artifact_type_value varchar;
                context_artifact_key_value varchar;
                context_artifact_version_value integer;
                graph_artifact_id_value uuid;
                graph_artifact_type_value varchar;
                graph_artifact_status_value varchar;
                graph_fingerprint_value varchar;
                graph_asset_fingerprint_value varchar;
                graph_binding_fingerprint_value varchar;
                registry_artifact_id_value uuid;
                registry_artifact_type_value varchar;
                registry_artifact_status_value varchar;
                asset_set_release_id_value uuid;
                asset_set_type_value varchar;
                expected_asset_document jsonb;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number
            INTO context_artifact_type_value,context_artifact_key_value,
                 context_artifact_version_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT graph.artifact_id,artifact.artifact_type,artifact.status,
                 graph.graph_fingerprint,graph.asset_context_fingerprint,
                 graph.resolved_data_binding_fingerprint
            INTO graph_artifact_id_value,graph_artifact_type_value,
                 graph_artifact_status_value,graph_fingerprint_value,
                 graph_asset_fingerprint_value,graph_binding_fingerprint_value
            FROM workspace.compiled_research_graph graph
            JOIN lineage.artifact artifact ON artifact.artifact_id=graph.artifact_id
           WHERE graph.compiled_research_graph_id=NEW.compiled_research_graph_id;
          SELECT release.artifact_id,artifact.artifact_type,artifact.status
            INTO registry_artifact_id_value,registry_artifact_type_value,
                 registry_artifact_status_value
            FROM catalog.asset_registry_release release
            JOIN lineage.artifact artifact ON artifact.artifact_id=release.artifact_id
           WHERE release.asset_registry_release_id=NEW.asset_registry_release_id;
          SELECT definition.asset_registry_release_id,definition.set_type
            INTO asset_set_release_id_value,asset_set_type_value
            FROM catalog.asset_set_definition definition
           WHERE definition.asset_set_definition_id=NEW.asset_set_definition_id;
          SELECT jsonb_build_object(
                   'contract_version','v0.22.0',
                   'selection_kind','fixed_asset_set',
                   'asset_context_key',definition.set_key,
                   'asset_registry_release_id',release.asset_registry_release_id::text,
                   'asset_registry_artifact_id',release.artifact_id::text,
                   'asset_registry_catalog_version',release.catalog_version,
                   'asset_set_definition_id',definition.asset_set_definition_id::text,
                   'members',coalesce(members.document,'[]'::jsonb)
                 )
            INTO expected_asset_document
            FROM catalog.asset_set_definition definition
            JOIN catalog.asset_registry_release release
              ON release.asset_registry_release_id=definition.asset_registry_release_id
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(
                       jsonb_build_object(
                         'ordinal',member.ordinal,
                         'security_id',security.security_id::text,
                         'security_key',security.security_key,
                         'instrument_type',profile.instrument_type
                       ) ORDER BY member.ordinal
                     ) AS document
                FROM catalog.asset_set_member member
                JOIN catalog.security security
                  ON security.security_id=member.security_id
                JOIN catalog.security_profile profile
                  ON profile.asset_registry_release_id=
                     definition.asset_registry_release_id
                 AND profile.security_id=member.security_id
               WHERE member.asset_set_definition_id=
                     definition.asset_set_definition_id
            ) members ON true
           WHERE definition.asset_set_definition_id=NEW.asset_set_definition_id;
          IF context_artifact_type_value IS DISTINCT FROM
               'v022_compiled_execution_data_context' OR
             context_artifact_version_value IS DISTINCT FROM 1 OR
             context_artifact_key_value IS DISTINCT FROM
               'compiled_execution_data_context__' || graph_fingerprint_value THEN
            RAISE EXCEPTION 'Compiled Execution Data Context requires its exact v1 Artifact identity';
          END IF;
          IF graph_artifact_type_value IS DISTINCT FROM
               'v022_compiled_research_graph' OR
             graph_artifact_status_value IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Compiled Execution Data Context requires an exact published Graph';
          END IF;
          IF registry_artifact_id_value IS DISTINCT FROM
               NEW.asset_registry_artifact_id OR
             registry_artifact_type_value IS DISTINCT FROM
               'asset_registry_release' OR
             registry_artifact_status_value IS DISTINCT FROM 'published' OR
             asset_set_release_id_value IS DISTINCT FROM
               NEW.asset_registry_release_id OR
             asset_set_type_value IS DISTINCT FROM 'fixed' THEN
            RAISE EXCEPTION 'Compiled Execution Data Context requires its exact published Asset Registry identities';
          END IF;
          IF NEW.asset_context_fingerprint IS DISTINCT FROM
               graph_asset_fingerprint_value OR
             NEW.resolved_data_binding_fingerprint IS DISTINCT FROM
               graph_binding_fingerprint_value THEN
            RAISE EXCEPTION 'Compiled Execution Data Context fingerprints differ from its Graph';
          END IF;
          IF NEW.asset_context_document IS DISTINCT FROM
               expected_asset_document THEN
            RAISE EXCEPTION 'Compiled Execution Data Context does not reproduce its exact fixed Asset Context';
          END IF;
          IF NEW.resolved_data_binding_document->>'contract_version' IS DISTINCT FROM
               'v0.22.0' OR
             jsonb_typeof(NEW.resolved_data_binding_document->'bindings')
               IS DISTINCT FROM 'array' OR
             jsonb_array_length(NEW.resolved_data_binding_document->'bindings')
               <> NEW.input_count THEN
            RAISE EXCEPTION 'Compiled Execution Data Context requires an exact v0.22 binding document';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_compiled_execution_data_context_validate
          BEFORE INSERT ON workspace.v022_compiled_execution_data_context
          FOR EACH ROW
          EXECUTE FUNCTION workspace.validate_v022_compiled_execution_data_context();
        """
    )


def _create_input_insert_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION workspace.validate_v022_compiled_execution_data_input()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_binding jsonb;
                expected_security_ids jsonb;
                dataset_artifact_id_value uuid;
                dataset_artifact_type_value varchar;
                dataset_artifact_status_value varchar;
                dataset_calendar_id_value uuid;
                dataset_key_value varchar;
                dataset_version_value integer;
                dataset_coverage_start_value date;
                dataset_coverage_end_value date;
                calendar_artifact_id_value uuid;
                calendar_artifact_type_value varchar;
                calendar_artifact_status_value varchar;
        BEGIN
          SELECT context.resolved_data_binding_document->'bindings'->NEW.ordinal,
                 context.asset_context_document->'members'
            INTO expected_binding,expected_security_ids
            FROM workspace.v022_compiled_execution_data_context context
           WHERE context.compiled_execution_data_context_id=
                 NEW.compiled_execution_data_context_id;
          SELECT publication.artifact_id,artifact.artifact_type,artifact.status,
                 publication.calendar_version_id,publication.dataset_key,
                 publication.version_number,publication.coverage_start,
                 publication.coverage_end
            INTO dataset_artifact_id_value,dataset_artifact_type_value,
                 dataset_artifact_status_value,dataset_calendar_id_value,
                 dataset_key_value,dataset_version_value,
                 dataset_coverage_start_value,dataset_coverage_end_value
            FROM data.dataset_publication publication
            JOIN lineage.artifact artifact
              ON artifact.artifact_id=publication.artifact_id
           WHERE publication.dataset_publication_id=NEW.dataset_publication_id;
          IF NEW.calendar_version_id IS NOT NULL THEN
            SELECT version.artifact_id,artifact.artifact_type,artifact.status
              INTO calendar_artifact_id_value,calendar_artifact_type_value,
                   calendar_artifact_status_value
              FROM catalog.calendar_version version
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=version.artifact_id
             WHERE version.calendar_version_id=NEW.calendar_version_id;
          END IF;
          SELECT jsonb_agg(member->'security_id' ORDER BY member_ordinal)
            INTO expected_security_ids
            FROM jsonb_array_elements(expected_security_ids)
              WITH ORDINALITY AS item(member,member_ordinal);
          IF expected_binding IS NULL OR
             NEW.binding_document IS DISTINCT FROM expected_binding THEN
            RAISE EXCEPTION 'Compiled Execution Data Input does not reproduce its ordered binding document';
          END IF;
          IF dataset_artifact_id_value IS DISTINCT FROM NEW.dataset_artifact_id OR
             dataset_artifact_type_value IS DISTINCT FROM 'dataset_publication' OR
             dataset_artifact_status_value IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Compiled Execution Data Input requires its exact published Dataset Publication';
          END IF;
          IF dataset_calendar_id_value IS DISTINCT FROM NEW.calendar_version_id OR
             calendar_artifact_id_value IS DISTINCT FROM NEW.calendar_artifact_id OR
             (NEW.calendar_version_id IS NOT NULL AND (
               calendar_artifact_type_value IS DISTINCT FROM 'calendar_version' OR
               calendar_artifact_status_value IS DISTINCT FROM 'published'
             )) THEN
            RAISE EXCEPTION 'Compiled Execution Data Input requires its exact published Calendar';
          END IF;
          IF NEW.input_key IS DISTINCT FROM expected_binding->>'input_key' OR
             NEW.dataset_publication_id IS DISTINCT FROM
               (expected_binding->>'dataset_publication_id')::uuid OR
             NEW.dataset_artifact_id IS DISTINCT FROM
               (expected_binding->>'dataset_artifact_id')::uuid OR
             dataset_key_value IS DISTINCT FROM expected_binding->>'dataset_key' OR
             dataset_version_value IS DISTINCT FROM
               (expected_binding->>'dataset_version_number')::integer OR
             NEW.coverage_start IS DISTINCT FROM
               (expected_binding->>'coverage_start')::date OR
             NEW.coverage_end IS DISTINCT FROM
               (expected_binding->>'coverage_end')::date OR
             NEW.coverage_start IS DISTINCT FROM dataset_coverage_start_value OR
             NEW.coverage_end IS DISTINCT FROM dataset_coverage_end_value OR
             NEW.calendar_version_id IS DISTINCT FROM
               nullif(expected_binding->>'calendar_version_id','')::uuid OR
             NEW.calendar_artifact_id IS DISTINCT FROM
               nullif(expected_binding->>'calendar_artifact_id','')::uuid OR
             NEW.security_ids IS DISTINCT FROM expected_binding->'security_ids' OR
             NEW.security_ids IS DISTINCT FROM expected_security_ids THEN
            RAISE EXCEPTION 'Compiled Execution Data Input columns differ from its exact binding identities';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_compiled_execution_data_input_validate
          BEFORE INSERT ON workspace.v022_compiled_execution_data_input
          FOR EACH ROW
          EXECUTE FUNCTION workspace.validate_v022_compiled_execution_data_input();
        """
    )


def _create_context_completeness_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION workspace.validate_v022_compiled_execution_data_context_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE context_artifact_type_value varchar;
                context_artifact_status_value varchar;
                context_artifact_version_value integer;
                context_artifact_fingerprint_value varchar;
                graph_artifact_id_value uuid;
                graph_artifact_status_value varchar;
                expected_binding_document jsonb;
                actual_input_count integer;
                dependency_count_value integer;
                expected_dependency_count integer;
        BEGIN
          SELECT artifact_type,status,version_number,semantic_fingerprint
            INTO context_artifact_type_value,context_artifact_status_value,
                 context_artifact_version_value,
                 context_artifact_fingerprint_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT graph.artifact_id,artifact.status
            INTO graph_artifact_id_value,graph_artifact_status_value
            FROM workspace.compiled_research_graph graph
            JOIN lineage.artifact artifact ON artifact.artifact_id=graph.artifact_id
           WHERE graph.compiled_research_graph_id=NEW.compiled_research_graph_id;
          SELECT count(*),jsonb_build_object(
                   'contract_version','v0.22.0',
                   'bindings',coalesce(
                     jsonb_agg(input.binding_document ORDER BY input.ordinal),
                     '[]'::jsonb
                   )
                 )
            INTO actual_input_count,expected_binding_document
            FROM workspace.v022_compiled_execution_data_input input
           WHERE input.compiled_execution_data_context_id=
                 NEW.compiled_execution_data_context_id;
          SELECT count(*) INTO dependency_count_value
            FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id;
          SELECT 2 + count(DISTINCT input.dataset_artifact_id) +
                     count(DISTINCT input.calendar_artifact_id)
            INTO expected_dependency_count
            FROM workspace.v022_compiled_execution_data_input input
           WHERE input.compiled_execution_data_context_id=
                 NEW.compiled_execution_data_context_id;
          IF context_artifact_type_value IS DISTINCT FROM
               'v022_compiled_execution_data_context' OR
             context_artifact_status_value IS DISTINCT FROM 'published' OR
             context_artifact_version_value IS DISTINCT FROM 1 OR
             context_artifact_fingerprint_value IS DISTINCT FROM
               NEW.context_fingerprint THEN
            RAISE EXCEPTION 'Compiled Execution Data Context Artifact is not exactly published';
          END IF;
          IF graph_artifact_status_value IS DISTINCT FROM 'published' OR
             actual_input_count<>NEW.input_count OR
             NEW.resolved_data_binding_document IS DISTINCT FROM
               expected_binding_document THEN
            RAISE EXCEPTION 'Compiled Execution Data Context input projection is incomplete';
          END IF;
          IF dependency_count_value<>expected_dependency_count OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=graph_artifact_id_value
                  AND dependency.role='compiled_graph'
                  AND dependency.ordinal=0
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=
                      NEW.asset_registry_artifact_id
                  AND dependency.role='asset_context'
                  AND dependency.ordinal=0
             ) OR EXISTS (
               SELECT 1
                 FROM (
                   SELECT input.dataset_artifact_id,
                          min(input.ordinal) AS dependency_ordinal
                     FROM workspace.v022_compiled_execution_data_input input
                    WHERE input.compiled_execution_data_context_id=
                          NEW.compiled_execution_data_context_id
                    GROUP BY input.dataset_artifact_id
                 ) expected
                WHERE NOT EXISTS (
                  SELECT 1 FROM lineage.artifact_dependency dependency
                   WHERE dependency.artifact_id=NEW.artifact_id
                     AND dependency.depends_on_artifact_id=
                         expected.dataset_artifact_id
                     AND dependency.role='data_binding'
                     AND dependency.ordinal=expected.dependency_ordinal
                )
             ) OR EXISTS (
               SELECT 1
                 FROM (
                   SELECT input.calendar_artifact_id,
                          min(input.ordinal) AS dependency_ordinal
                     FROM workspace.v022_compiled_execution_data_input input
                    WHERE input.compiled_execution_data_context_id=
                          NEW.compiled_execution_data_context_id
                      AND input.calendar_artifact_id IS NOT NULL
                    GROUP BY input.calendar_artifact_id
                 ) expected
                WHERE NOT EXISTS (
                  SELECT 1 FROM lineage.artifact_dependency dependency
                   WHERE dependency.artifact_id=NEW.artifact_id
                     AND dependency.depends_on_artifact_id=
                         expected.calendar_artifact_id
                     AND dependency.role='calendar'
                     AND dependency.ordinal=expected.dependency_ordinal
                )
             ) THEN
            RAISE EXCEPTION 'Compiled Execution Data Context Artifact lineage is not exact';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_compiled_execution_data_context_complete
          AFTER INSERT ON workspace.v022_compiled_execution_data_context
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION workspace.validate_v022_compiled_execution_data_context_complete();
        """
    )


def _create_runtime_admission_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.require_v022_compiled_execution_data_context()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM workspace.v022_compiled_execution_data_context context
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=context.artifact_id
             WHERE context.compiled_research_graph_id=
                   NEW.compiled_research_graph_id
               AND artifact.status='published'
               AND artifact.semantic_fingerprint=context.context_fingerprint
          ) THEN
            RAISE EXCEPTION 'New v0.22 Graph Run binding requires an exact Compiled Execution Data Context';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_suite_graph_run_execution_data_context
          BEFORE INSERT
          ON experiment.v022_research_suite_graph_run_binding
          FOR EACH ROW
          EXECUTE FUNCTION experiment.require_v022_compiled_execution_data_context();
        """
    )


def _create_append_only_guards() -> None:
    for table in (
        "v022_compiled_execution_data_context",
        "v022_compiled_execution_data_input",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON workspace.{table} FOR EACH ROW "
            "EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "experiment.require_v022_compiled_execution_data_context() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "workspace.validate_v022_compiled_execution_data_context_complete() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "workspace.validate_v022_compiled_execution_data_input() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "workspace.validate_v022_compiled_execution_data_context() CASCADE"
    )
    op.drop_table("v022_compiled_execution_data_input", schema="workspace")
    op.drop_table("v022_compiled_execution_data_context", schema="workspace")
