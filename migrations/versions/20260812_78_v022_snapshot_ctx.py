# ruff: noqa: E501
"""Bind composed Configuration Snapshots to exact execution contexts.

Revision ID: 20260812_78_v022_snapshot_ctx
Revises: 20260812_77_v022_defense_package
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_78_v022_snapshot_ctx"
down_revision: str | None = "20260812_77_v022_defense_package"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _create_binding_table()
    _create_composed_graph_helper()
    _reject_preexisting_unbound_composed_snapshots()
    _create_binding_guard()
    _create_snapshot_completeness_guard()
    _create_suite_admission_guards()
    _create_append_only_guard()


def _create_binding_table() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_configuration_execution_context_binding (
          configuration_snapshot_id uuid PRIMARY KEY,
          compiled_research_graph_id uuid NOT NULL,
          compiled_strategy_branch_id uuid NOT NULL
            REFERENCES strategy.v022_compiled_strategy_branch,
          compiled_execution_data_context_id uuid NOT NULL,
          execution_data_context_artifact_id uuid NOT NULL
            REFERENCES lineage.artifact,
          execution_data_context_fingerprint varchar(64) NOT NULL
            CHECK (execution_data_context_fingerprint ~ '^[0-9a-f]{64}$'),
          defense_version_id uuid NULL,
          defense_package_artifact_id uuid NULL REFERENCES lineage.artifact,
          timing_policy_version_id uuid NULL,
          timing_policy_artifact_id uuid NULL,
          allocation_policy_version_id uuid NULL,
          allocation_policy_artifact_id uuid NULL,
          compiled_defense_execution_context_id uuid NULL,
          defense_execution_context_artifact_id uuid NULL
            REFERENCES lineage.artifact,
          defense_execution_context_fingerprint varchar(64) NULL
            CHECK (
              defense_execution_context_fingerprint IS NULL OR
              defense_execution_context_fingerprint ~ '^[0-9a-f]{64}$'
            ),
          binding_document jsonb NOT NULL,
          binding_fingerprint varchar(64) NOT NULL
            CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (
            configuration_snapshot_id,compiled_research_graph_id
          ) REFERENCES experiment.v022_research_configuration_snapshot (
            configuration_snapshot_id,compiled_research_graph_id
          ),
          FOREIGN KEY (
            compiled_execution_data_context_id,compiled_research_graph_id
          ) REFERENCES workspace.v022_compiled_execution_data_context (
            compiled_execution_data_context_id,compiled_research_graph_id
          ),
          FOREIGN KEY (defense_version_id)
            REFERENCES defense.v022_defense_package_policy_binding,
          FOREIGN KEY (timing_policy_version_id,timing_policy_artifact_id)
            REFERENCES defense.v022_timing_policy_version (
              timing_policy_version_id,artifact_id
            ),
          FOREIGN KEY (allocation_policy_version_id,allocation_policy_artifact_id)
            REFERENCES defense.v022_allocation_policy_version (
              allocation_policy_version_id,artifact_id
            ),
          FOREIGN KEY (
            compiled_defense_execution_context_id,defense_version_id
          ) REFERENCES defense.v022_compiled_defense_execution_context (
            compiled_defense_execution_context_id,defense_version_id
          ),
          CHECK (
            jsonb_typeof(binding_document)='object' AND
            binding_document<>'{}'::jsonb
          ),
          CHECK (
            binding_fingerprint=
              strategy.v022_strategy_parameter_fingerprint(binding_document)
          ),
          CHECK (
            num_nonnulls(
              defense_version_id,
              defense_package_artifact_id,
              timing_policy_version_id,
              timing_policy_artifact_id,
              allocation_policy_version_id,
              allocation_policy_artifact_id,
              compiled_defense_execution_context_id,
              defense_execution_context_artifact_id,
              defense_execution_context_fingerprint
            ) IN (0,9)
          )
        );
        """
    )


def _create_composed_graph_helper() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.v022_graph_uses_composed_defense(
          graph_id uuid
        ) RETURNS boolean
        LANGUAGE sql STABLE STRICT PARALLEL SAFE AS $$
          SELECT EXISTS (
            SELECT 1
              FROM workspace.compiled_research_graph graph
              JOIN workspace.v022_catalog_release_component component
                ON component.catalog_release_id=graph.catalog_release_id
             WHERE graph.compiled_research_graph_id=graph_id
               AND component.component_kind IN (
                 'defense_timing_version','defense_allocation_version'
               )
          )
        $$;
        """
    )


def _reject_preexisting_unbound_composed_snapshots() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
              FROM experiment.v022_research_configuration_snapshot snapshot
             WHERE experiment.v022_graph_uses_composed_defense(
                     snapshot.compiled_research_graph_id
                   )
          ) THEN
            RAISE EXCEPTION 'Cannot grandfather a composed Configuration Snapshot without exact Execution Context binding';
          END IF;
        END $$;
        """
    )


def _create_binding_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.validate_v022_configuration_execution_context_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE snapshot_row record; branch_row record; risk_row record;
                package_row record; defense_context_row record;
                composed_component_count integer; expected_document jsonb;
                expected_defense_document jsonb; expected_dependency_count integer;
                actual_dependency_count integer; preset_artifact_id_value uuid;
        BEGIN
          SELECT snapshot.compiled_research_graph_id,
                 snapshot.compiled_strategy_branch_id,
                 snapshot.artifact_id,
                 snapshot.configuration_fingerprint,
                 snapshot.semantic_identity_document,
                 graph.catalog_release_id,
                 graph.artifact_id AS graph_artifact_id,
                 graph_artifact.status AS graph_artifact_status,
                 snapshot_artifact.artifact_type AS snapshot_artifact_type,
                 snapshot_artifact.artifact_key AS snapshot_artifact_key,
                 snapshot_artifact.version_number AS snapshot_artifact_version,
                 snapshot_artifact.status AS snapshot_artifact_status
            INTO snapshot_row
            FROM experiment.v022_research_configuration_snapshot snapshot
            JOIN workspace.compiled_research_graph graph
              ON graph.compiled_research_graph_id=
                 snapshot.compiled_research_graph_id
            JOIN lineage.artifact graph_artifact
              ON graph_artifact.artifact_id=graph.artifact_id
            JOIN lineage.artifact snapshot_artifact
              ON snapshot_artifact.artifact_id=snapshot.artifact_id
           WHERE snapshot.configuration_snapshot_id=
                 NEW.configuration_snapshot_id;
          IF snapshot_row.compiled_research_graph_id IS NULL THEN
            RAISE EXCEPTION 'Configuration Execution Context Binding requires its exact Snapshot';
          END IF;
          IF NOT experiment.v022_graph_uses_composed_defense(
                   snapshot_row.compiled_research_graph_id
                 ) THEN
            RAISE EXCEPTION 'Legacy Configuration Snapshot must not receive a composed Execution Context Binding';
          END IF;
          SELECT count(DISTINCT component.component_kind)
            INTO composed_component_count
            FROM workspace.v022_catalog_release_component component
           WHERE component.catalog_release_id=snapshot_row.catalog_release_id
             AND component.component_kind IN (
               'defense_timing_version','defense_allocation_version'
             );
          IF composed_component_count<>2 THEN
            RAISE EXCEPTION 'Composed Configuration Snapshot requires complete Timing and Allocation Catalog identities';
          END IF;
          IF snapshot_row.compiled_research_graph_id IS DISTINCT FROM
               NEW.compiled_research_graph_id OR
             snapshot_row.compiled_strategy_branch_id IS DISTINCT FROM
               NEW.compiled_strategy_branch_id OR
             snapshot_row.snapshot_artifact_type IS DISTINCT FROM
               'v022_research_configuration_snapshot' OR
             snapshot_row.snapshot_artifact_key IS DISTINCT FROM
               snapshot_row.configuration_fingerprint OR
             snapshot_row.snapshot_artifact_version IS DISTINCT FROM 1 OR
             snapshot_row.snapshot_artifact_status IS DISTINCT FROM 'draft' OR
             snapshot_row.graph_artifact_status IS DISTINCT FROM 'published' OR
             snapshot_row.semantic_identity_document IS NULL THEN
            RAISE EXCEPTION 'Configuration Execution Context Binding differs from its Snapshot, Branch, or Graph';
          END IF;

          SELECT branch.compiled_research_graph_id,branch.defense_version_id
            INTO branch_row
            FROM strategy.v022_compiled_strategy_branch branch
           WHERE branch.compiled_strategy_branch_id=
                 NEW.compiled_strategy_branch_id;
          IF branch_row.compiled_research_graph_id IS DISTINCT FROM
               NEW.compiled_research_graph_id THEN
            RAISE EXCEPTION 'Configuration Execution Context Binding Branch and Graph differ';
          END IF;

          SELECT context.compiled_research_graph_id,context.artifact_id,
                 context.context_fingerprint,artifact.artifact_type,
                 artifact.status AS artifact_status
            INTO risk_row
            FROM workspace.v022_compiled_execution_data_context context
            JOIN lineage.artifact artifact
              ON artifact.artifact_id=context.artifact_id
           WHERE context.compiled_execution_data_context_id=
                 NEW.compiled_execution_data_context_id;
          IF risk_row.compiled_research_graph_id IS DISTINCT FROM
               NEW.compiled_research_graph_id OR
             risk_row.artifact_id IS DISTINCT FROM
               NEW.execution_data_context_artifact_id OR
             risk_row.context_fingerprint IS DISTINCT FROM
               NEW.execution_data_context_fingerprint OR
             risk_row.artifact_type IS DISTINCT FROM
               'v022_compiled_execution_data_context' OR
             risk_row.artifact_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Configuration Snapshot requires its exact published Risk Execution Context';
          END IF;

          IF branch_row.defense_version_id IS NULL THEN
            IF num_nonnulls(
                 NEW.defense_version_id,
                 NEW.defense_package_artifact_id,
                 NEW.timing_policy_version_id,
                 NEW.timing_policy_artifact_id,
                 NEW.allocation_policy_version_id,
                 NEW.allocation_policy_artifact_id,
                 NEW.compiled_defense_execution_context_id,
                 NEW.defense_execution_context_artifact_id,
                 NEW.defense_execution_context_fingerprint
               )<>0 THEN
              RAISE EXCEPTION 'No-defense Configuration Snapshot forbids Defense Package and Context identities';
            END IF;
            expected_defense_document := 'null'::jsonb;
            expected_dependency_count := 3;
          ELSE
            IF NEW.defense_version_id IS DISTINCT FROM
                 branch_row.defense_version_id THEN
              RAISE EXCEPTION 'Configuration Snapshot Defense Package differs from its Branch';
            END IF;
            SELECT version.artifact_id AS package_artifact_id,
                   package.timing_policy_version_id,
                   package.timing_policy_artifact_id,
                   package.allocation_policy_version_id,
                   package.allocation_policy_artifact_id,
                   package_artifact.status AS package_artifact_status,
                   timing_artifact.status AS timing_artifact_status,
                   allocation_artifact.status AS allocation_artifact_status
              INTO package_row
              FROM defense.v022_defense_package_policy_binding package
              JOIN defense.defense_version version
                ON version.defense_version_id=package.defense_version_id
              JOIN lineage.artifact package_artifact
                ON package_artifact.artifact_id=version.artifact_id
              JOIN lineage.artifact timing_artifact
                ON timing_artifact.artifact_id=package.timing_policy_artifact_id
              JOIN lineage.artifact allocation_artifact
                ON allocation_artifact.artifact_id=
                   package.allocation_policy_artifact_id
             WHERE package.defense_version_id=NEW.defense_version_id;
            SELECT context.compiled_execution_data_context_id,
                   context.defense_version_id,
                   context.defense_package_artifact_id,
                   context.timing_policy_version_id,
                   context.timing_policy_artifact_id,
                   context.allocation_policy_version_id,
                   context.allocation_policy_artifact_id,
                   context.artifact_id,context.context_fingerprint,
                   artifact.artifact_type,artifact.status AS artifact_status
              INTO defense_context_row
              FROM defense.v022_compiled_defense_execution_context context
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=context.artifact_id
             WHERE context.compiled_defense_execution_context_id=
                   NEW.compiled_defense_execution_context_id;
            IF package_row.package_artifact_id IS NULL OR
               package_row.package_artifact_id IS DISTINCT FROM
                 NEW.defense_package_artifact_id OR
               package_row.timing_policy_version_id IS DISTINCT FROM
                 NEW.timing_policy_version_id OR
               package_row.timing_policy_artifact_id IS DISTINCT FROM
                 NEW.timing_policy_artifact_id OR
               package_row.allocation_policy_version_id IS DISTINCT FROM
                 NEW.allocation_policy_version_id OR
               package_row.allocation_policy_artifact_id IS DISTINCT FROM
                 NEW.allocation_policy_artifact_id OR
               package_row.package_artifact_status IS DISTINCT FROM 'published' OR
               package_row.timing_artifact_status IS DISTINCT FROM 'published' OR
               package_row.allocation_artifact_status IS DISTINCT FROM 'published' THEN
              RAISE EXCEPTION 'Configuration Snapshot does not reproduce its exact published Defense Package';
            END IF;
            IF defense_context_row.compiled_execution_data_context_id IS DISTINCT FROM
                 NEW.compiled_execution_data_context_id OR
               defense_context_row.defense_version_id IS DISTINCT FROM
                 NEW.defense_version_id OR
               defense_context_row.defense_package_artifact_id IS DISTINCT FROM
                 NEW.defense_package_artifact_id OR
               defense_context_row.timing_policy_version_id IS DISTINCT FROM
                 NEW.timing_policy_version_id OR
               defense_context_row.timing_policy_artifact_id IS DISTINCT FROM
                 NEW.timing_policy_artifact_id OR
               defense_context_row.allocation_policy_version_id IS DISTINCT FROM
                 NEW.allocation_policy_version_id OR
               defense_context_row.allocation_policy_artifact_id IS DISTINCT FROM
                 NEW.allocation_policy_artifact_id OR
               defense_context_row.artifact_id IS DISTINCT FROM
                 NEW.defense_execution_context_artifact_id OR
               defense_context_row.context_fingerprint IS DISTINCT FROM
                 NEW.defense_execution_context_fingerprint OR
               defense_context_row.artifact_type IS DISTINCT FROM
                 'v022_compiled_defense_execution_context' OR
               defense_context_row.artifact_status IS DISTINCT FROM 'published' THEN
              RAISE EXCEPTION 'Configuration Snapshot requires its exact published Defense Execution Context';
            END IF;
            expected_defense_document := jsonb_build_object(
              'defense_version_id',NEW.defense_version_id,
              'package_artifact_id',NEW.defense_package_artifact_id,
              'timing_policy_version_id',NEW.timing_policy_version_id,
              'timing_policy_artifact_id',NEW.timing_policy_artifact_id,
              'allocation_policy_version_id',NEW.allocation_policy_version_id,
              'allocation_policy_artifact_id',NEW.allocation_policy_artifact_id,
              'execution_context',jsonb_build_object(
                'compiled_defense_execution_context_id',
                  NEW.compiled_defense_execution_context_id,
                'artifact_id',NEW.defense_execution_context_artifact_id,
                'context_fingerprint',
                  NEW.defense_execution_context_fingerprint
              )
            );
            expected_dependency_count := 7;
          END IF;

          expected_document := jsonb_build_object(
            'contract_version','v0.22.0',
            'compiled_research_graph_id',NEW.compiled_research_graph_id,
            'compiled_strategy_branch_id',NEW.compiled_strategy_branch_id,
            'risk_execution_context',jsonb_build_object(
              'compiled_execution_data_context_id',
                NEW.compiled_execution_data_context_id,
              'artifact_id',NEW.execution_data_context_artifact_id,
              'context_fingerprint',NEW.execution_data_context_fingerprint
            ),
            'defense',expected_defense_document
          );
          IF NEW.binding_document IS DISTINCT FROM expected_document OR
             snapshot_row.semantic_identity_document->'execution_contexts'
               IS DISTINCT FROM expected_document THEN
            RAISE EXCEPTION 'Configuration Snapshot semantic document does not reproduce its exact Execution Context Binding';
          END IF;

          SELECT version.artifact_id
            INTO preset_artifact_id_value
            FROM strategy.v022_compiled_strategy_branch_preset_binding binding
            JOIN strategy.v022_strategy_parameter_preset_version version
              ON version.strategy_parameter_preset_version_id=
                 binding.strategy_parameter_preset_version_id
           WHERE binding.compiled_strategy_branch_id=
                 NEW.compiled_strategy_branch_id;
          SELECT count(*) INTO actual_dependency_count
            FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=snapshot_row.artifact_id;
          IF preset_artifact_id_value IS NULL OR
             actual_dependency_count<>expected_dependency_count OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=snapshot_row.artifact_id
                  AND dependency.depends_on_artifact_id=
                      snapshot_row.graph_artifact_id
                  AND dependency.role='compiled_graph'
                  AND dependency.ordinal=0
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=snapshot_row.artifact_id
                  AND dependency.depends_on_artifact_id=
                      preset_artifact_id_value
                  AND dependency.role='strategy_parameter_preset'
                  AND dependency.ordinal=0
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=snapshot_row.artifact_id
                  AND dependency.depends_on_artifact_id=
                      NEW.execution_data_context_artifact_id
                  AND dependency.role='compiled_execution_data_context'
                  AND dependency.ordinal=0
             ) THEN
            RAISE EXCEPTION 'Configuration Snapshot Execution Context lineage is incomplete';
          END IF;
          IF NEW.defense_version_id IS NOT NULL AND (
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=snapshot_row.artifact_id
                  AND dependency.depends_on_artifact_id=
                      NEW.defense_package_artifact_id
                  AND dependency.role='defense_package'
                  AND dependency.ordinal=0
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=snapshot_row.artifact_id
                  AND dependency.depends_on_artifact_id=
                      NEW.timing_policy_artifact_id
                  AND dependency.role='defense_timing_policy_version'
                  AND dependency.ordinal=0
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=snapshot_row.artifact_id
                  AND dependency.depends_on_artifact_id=
                      NEW.allocation_policy_artifact_id
                  AND dependency.role='defense_allocation_policy_version'
                  AND dependency.ordinal=0
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=snapshot_row.artifact_id
                  AND dependency.depends_on_artifact_id=
                      NEW.defense_execution_context_artifact_id
                  AND dependency.role='compiled_defense_execution_context'
                  AND dependency.ordinal=0
             )
          ) THEN
            RAISE EXCEPTION 'Configuration Snapshot Defense lineage is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_configuration_execution_context_binding_validate
          BEFORE INSERT
          ON experiment.v022_configuration_execution_context_binding
          FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_configuration_execution_context_binding();
        """
    )


def _create_snapshot_completeness_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.validate_v022_configuration_execution_context_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE binding_count integer; artifact_status_value varchar;
        BEGIN
          SELECT count(*) INTO binding_count
            FROM experiment.v022_configuration_execution_context_binding binding
           WHERE binding.configuration_snapshot_id=
                 NEW.configuration_snapshot_id;
          IF experiment.v022_graph_uses_composed_defense(
               NEW.compiled_research_graph_id
             ) AND binding_count<>1 THEN
            RAISE EXCEPTION 'Composed Configuration Snapshot requires exactly one Execution Context Binding';
          END IF;
          IF NOT experiment.v022_graph_uses_composed_defense(
                   NEW.compiled_research_graph_id
                 ) AND binding_count<>0 THEN
            RAISE EXCEPTION 'Legacy Configuration Snapshot must remain context-binding free';
          END IF;
          SELECT status INTO artifact_status_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF artifact_status_value IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Configuration Snapshot Artifact is not exactly published';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_configuration_execution_context_complete
          AFTER INSERT
          ON experiment.v022_research_configuration_snapshot
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_configuration_execution_context_complete();
        """
    )


def _create_suite_admission_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.require_v022_suite_branch_execution_context_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF experiment.v022_graph_uses_composed_defense(
               NEW.compiled_research_graph_id
             ) AND NOT EXISTS (
               SELECT 1
                 FROM experiment.v022_configuration_execution_context_binding binding
                WHERE binding.configuration_snapshot_id=
                      NEW.configuration_snapshot_id
                  AND binding.compiled_research_graph_id=
                      NEW.compiled_research_graph_id
                  AND binding.compiled_strategy_branch_id=
                      NEW.compiled_strategy_branch_id
             ) THEN
            RAISE EXCEPTION 'Composed Suite Branch requires its exact Configuration Execution Context Binding';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_suite_branch_execution_context_binding
          BEFORE INSERT ON experiment.v022_research_suite_branch
          FOR EACH ROW
          EXECUTE FUNCTION experiment.require_v022_suite_branch_execution_context_binding();

        CREATE FUNCTION experiment.require_v022_suite_graph_run_context_bindings()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF experiment.v022_graph_uses_composed_defense(
               NEW.compiled_research_graph_id
             ) AND EXISTS (
               SELECT 1
                 FROM experiment.v022_research_suite_branch suite_branch
                 LEFT JOIN experiment.v022_configuration_execution_context_binding binding
                   ON binding.configuration_snapshot_id=
                      suite_branch.configuration_snapshot_id
                  AND binding.compiled_research_graph_id=
                      suite_branch.compiled_research_graph_id
                  AND binding.compiled_strategy_branch_id=
                      suite_branch.compiled_strategy_branch_id
                WHERE suite_branch.research_suite_id=NEW.research_suite_id
                  AND binding.configuration_snapshot_id IS NULL
             ) THEN
            RAISE EXCEPTION 'Composed Graph Run requires exact Configuration Execution Context Bindings for every Suite Branch';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_suite_graph_run_context_bindings
          BEFORE INSERT ON experiment.v022_research_suite_graph_run_binding
          FOR EACH ROW
          EXECUTE FUNCTION experiment.require_v022_suite_graph_run_context_bindings();
        """
    )


def _create_append_only_guard() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_v022_configuration_execution_context_binding_append_only
          BEFORE UPDATE OR DELETE
          ON experiment.v022_configuration_execution_context_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1
              FROM experiment.v022_configuration_execution_context_binding
          ) THEN
            RAISE EXCEPTION 'Cannot downgrade Snapshot Context identity while exact bindings exist';
          END IF;
        END $$;
        """
    )
    for function in (
        "require_v022_suite_graph_run_context_bindings()",
        "require_v022_suite_branch_execution_context_binding()",
        "validate_v022_configuration_execution_context_complete()",
        "validate_v022_configuration_execution_context_binding()",
        "v022_graph_uses_composed_defense(uuid)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS experiment.{function} CASCADE")
    op.drop_table(
        "v022_configuration_execution_context_binding",
        schema="experiment",
    )
