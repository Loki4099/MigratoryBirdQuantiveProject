# ruff: noqa: E501
"""Add immutable v0.22 Suite, Branch, Cell, and Graph Run bindings.

Revision ID: 20260812_74_v022_suite_identity
Revises: 20260812_73_v022_recovery
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_74_v022_suite_identity"
down_revision: str | None = "20260812_73_v022_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_evaluation_matrix_policy (
          evaluation_matrix_policy_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          policy_key varchar(200) NOT NULL,
          version_number integer NOT NULL CHECK (version_number >= 1),
          contract_version varchar(40) NOT NULL,
          suite_mode varchar(24) NOT NULL CHECK (suite_mode IN ('exploratory','formal')),
          context_count integer NOT NULL CHECK (context_count > 0),
          policy_document jsonb NOT NULL,
          policy_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (policy_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (policy_key,version_number),
          UNIQUE (evaluation_matrix_policy_id,contract_version),
          CHECK (jsonb_typeof(policy_document)='object' AND policy_document<>'{}'::jsonb)
        );
        CREATE TABLE experiment.v022_evaluation_matrix_policy_context (
          evaluation_matrix_policy_id uuid NOT NULL
            REFERENCES experiment.v022_evaluation_matrix_policy,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          context_key varchar(240) NOT NULL CHECK (btrim(context_key)<>''),
          context_fingerprint varchar(64) NOT NULL
            CHECK (context_fingerprint ~ '^[0-9a-f]{64}$'),
          semantic_context_document jsonb NOT NULL,
          display_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (evaluation_matrix_policy_id,ordinal),
          UNIQUE (evaluation_matrix_policy_id,context_key),
          UNIQUE (evaluation_matrix_policy_id,context_fingerprint),
          CHECK (jsonb_typeof(semantic_context_document)='object' AND
                 semantic_context_document<>'{}'::jsonb),
          CHECK (jsonb_typeof(display_document)='object')
        );
        CREATE TABLE experiment.v022_research_suite (
          research_suite_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          compiled_research_graph_id uuid NOT NULL
            REFERENCES workspace.compiled_research_graph,
          evaluation_matrix_policy_id uuid NOT NULL
            REFERENCES experiment.v022_evaluation_matrix_policy,
          contract_version varchar(40) NOT NULL,
          suite_key varchar(240) NOT NULL UNIQUE CHECK (btrim(suite_key)<>''),
          suite_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (suite_fingerprint ~ '^[0-9a-f]{64}$'),
          suite_mode varchar(24) NOT NULL CHECK (suite_mode IN ('exploratory','formal')),
          execution_policy_document jsonb NOT NULL,
          provenance_document jsonb NOT NULL,
          branch_count integer NOT NULL CHECK (branch_count > 0),
          cell_count integer NOT NULL CHECK (cell_count > 0),
          owner_key varchar(160) NOT NULL CHECK (btrim(owner_key)<>''),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (research_suite_id,compiled_research_graph_id),
          CHECK (jsonb_typeof(execution_policy_document)='object' AND
                 execution_policy_document<>'{}'::jsonb),
          CHECK (jsonb_typeof(provenance_document)='object')
        );
        CREATE TABLE experiment.v022_research_suite_branch (
          research_suite_branch_id uuid PRIMARY KEY,
          research_suite_id uuid NOT NULL,
          compiled_research_graph_id uuid NOT NULL
            REFERENCES workspace.compiled_research_graph,
          compiled_strategy_branch_id uuid NOT NULL
            REFERENCES strategy.v022_compiled_strategy_branch,
          configuration_snapshot_id uuid NOT NULL,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          branch_key varchar(500) NOT NULL CHECK (btrim(branch_key)<>''),
          branch_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (branch_fingerprint ~ '^[0-9a-f]{64}$'),
          provenance_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (research_suite_id,compiled_research_graph_id)
            REFERENCES experiment.v022_research_suite
              (research_suite_id,compiled_research_graph_id),
          FOREIGN KEY (configuration_snapshot_id,compiled_research_graph_id)
            REFERENCES experiment.v022_research_configuration_snapshot
              (configuration_snapshot_id,compiled_research_graph_id),
          UNIQUE (research_suite_id,compiled_strategy_branch_id),
          UNIQUE (research_suite_id,configuration_snapshot_id),
          UNIQUE (research_suite_id,ordinal),
          UNIQUE (research_suite_id,branch_key),
          UNIQUE (research_suite_branch_id,research_suite_id,compiled_research_graph_id),
          CHECK (jsonb_typeof(provenance_document)='object')
        );
        CREATE TABLE experiment.v022_research_cell (
          research_cell_id uuid PRIMARY KEY,
          research_suite_id uuid NOT NULL,
          research_suite_branch_id uuid NOT NULL,
          compiled_research_graph_id uuid NOT NULL
            REFERENCES workspace.compiled_research_graph,
          compiled_strategy_branch_id uuid NOT NULL
            REFERENCES strategy.v022_compiled_strategy_branch,
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          evaluation_matrix_policy_id uuid NOT NULL
            REFERENCES experiment.v022_evaluation_matrix_policy,
          evaluation_context_ordinal integer NOT NULL CHECK (evaluation_context_ordinal >= 0),
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          cell_key varchar(700) NOT NULL CHECK (btrim(cell_key)<>''),
          evaluation_context_fingerprint varchar(64) NOT NULL
            CHECK (evaluation_context_fingerprint ~ '^[0-9a-f]{64}$'),
          cell_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (cell_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (research_suite_id,compiled_research_graph_id)
            REFERENCES experiment.v022_research_suite
              (research_suite_id,compiled_research_graph_id),
          FOREIGN KEY (
            research_suite_branch_id,research_suite_id,compiled_research_graph_id
          ) REFERENCES experiment.v022_research_suite_branch (
            research_suite_branch_id,research_suite_id,compiled_research_graph_id
          ),
          FOREIGN KEY (evaluation_matrix_policy_id,evaluation_context_ordinal)
            REFERENCES experiment.v022_evaluation_matrix_policy_context
              (evaluation_matrix_policy_id,ordinal),
          UNIQUE (research_suite_id,ordinal),
          UNIQUE (research_suite_id,cell_key),
          UNIQUE (
            research_suite_id,research_suite_branch_id,evaluation_context_ordinal
          )
        );
        CREATE TABLE experiment.v022_research_suite_graph_run_binding (
          research_suite_graph_run_binding_id uuid PRIMARY KEY,
          research_suite_id uuid NOT NULL,
          compiled_research_graph_id uuid NOT NULL
            REFERENCES workspace.compiled_research_graph,
          graph_run_id uuid NOT NULL REFERENCES workspace.v022_graph_run,
          binding_ordinal integer NOT NULL CHECK (binding_ordinal >= 0),
          binding_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$'),
          bound_by varchar(160) NOT NULL CHECK (btrim(bound_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (research_suite_id,compiled_research_graph_id)
            REFERENCES experiment.v022_research_suite
              (research_suite_id,compiled_research_graph_id),
          UNIQUE (research_suite_id,graph_run_id),
          UNIQUE (research_suite_id,binding_ordinal)
        );
        """
    )
    _create_policy_guards()
    _create_suite_guards()
    _create_append_only_guards()


def _create_policy_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.validate_v022_evaluation_matrix_policy()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_type varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type INTO actual_type FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          IF actual_type IS DISTINCT FROM 'v022_evaluation_matrix_policy' THEN
            RAISE EXCEPTION 'v0.22 Evaluation Matrix Policy requires its exact Artifact type';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_evaluation_matrix_policy_validate
          BEFORE INSERT ON experiment.v022_evaluation_matrix_policy
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_evaluation_matrix_policy();

        CREATE FUNCTION experiment.validate_v022_evaluation_matrix_policy_context()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE owner_artifact_id uuid;
        BEGIN
          SELECT artifact_id INTO owner_artifact_id
            FROM experiment.v022_evaluation_matrix_policy
           WHERE evaluation_matrix_policy_id=NEW.evaluation_matrix_policy_id;
          PERFORM data.assert_artifact_draft(owner_artifact_id);
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_evaluation_matrix_policy_context_validate
          BEFORE INSERT ON experiment.v022_evaluation_matrix_policy_context
          FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_evaluation_matrix_policy_context();

        CREATE FUNCTION experiment.validate_v022_evaluation_matrix_policy_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_count integer; minimum_ordinal integer; maximum_ordinal integer;
                actual_artifact_type varchar; actual_artifact_status varchar;
                actual_artifact_fingerprint varchar;
        BEGIN
          SELECT count(*),min(ordinal),max(ordinal)
            INTO actual_count,minimum_ordinal,maximum_ordinal
            FROM experiment.v022_evaluation_matrix_policy_context
           WHERE evaluation_matrix_policy_id=NEW.evaluation_matrix_policy_id;
          SELECT artifact.artifact_type,artifact.status,artifact.semantic_fingerprint
            INTO actual_artifact_type,actual_artifact_status,actual_artifact_fingerprint
            FROM lineage.artifact artifact WHERE artifact.artifact_id=NEW.artifact_id;
          IF actual_count<>NEW.context_count OR minimum_ordinal<>0 OR
             maximum_ordinal<>NEW.context_count-1 THEN
            RAISE EXCEPTION 'v0.22 Evaluation Matrix Policy contexts are incomplete';
          END IF;
          IF actual_artifact_type IS DISTINCT FROM 'v022_evaluation_matrix_policy' OR
             actual_artifact_status IS DISTINCT FROM 'published' OR
             actual_artifact_fingerprint IS DISTINCT FROM NEW.policy_fingerprint THEN
            RAISE EXCEPTION 'v0.22 Evaluation Matrix Policy Artifact is not exactly published';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_evaluation_matrix_policy_complete
          AFTER INSERT ON experiment.v022_evaluation_matrix_policy
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_evaluation_matrix_policy_complete();
        """
    )


def _create_suite_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.validate_v022_research_suite()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE suite_artifact_type varchar; graph_contract varchar; graph_artifact_status varchar;
                policy_key varchar; policy_version integer; policy_contract varchar;
                policy_mode varchar; policy_artifact_status varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type INTO suite_artifact_type FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          SELECT graph.contract_version,artifact.status
            INTO graph_contract,graph_artifact_status
            FROM workspace.compiled_research_graph graph
            JOIN lineage.artifact artifact ON artifact.artifact_id=graph.artifact_id
           WHERE graph.compiled_research_graph_id=NEW.compiled_research_graph_id;
          SELECT policy.policy_key,policy.version_number,policy.contract_version,
                 policy.suite_mode,artifact.status
            INTO policy_key,policy_version,policy_contract,policy_mode,policy_artifact_status
            FROM experiment.v022_evaluation_matrix_policy policy
            JOIN lineage.artifact artifact ON artifact.artifact_id=policy.artifact_id
           WHERE policy.evaluation_matrix_policy_id=NEW.evaluation_matrix_policy_id;
          IF suite_artifact_type IS DISTINCT FROM 'v022_research_suite' THEN
            RAISE EXCEPTION 'v0.22 Research Suite requires its exact Artifact type';
          END IF;
          IF NEW.contract_version IS DISTINCT FROM 'v0.22.0' OR
             graph_contract IS DISTINCT FROM NEW.contract_version OR
             graph_artifact_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'v0.22 Research Suite requires an exact published v0.22.0 Graph';
          END IF;
          IF policy_key IS DISTINCT FROM 'v022_exploratory_baseline' OR
             policy_version IS DISTINCT FROM 1 OR
             policy_contract IS DISTINCT FROM NEW.contract_version OR
             policy_mode IS DISTINCT FROM NEW.suite_mode OR
             NEW.suite_mode IS DISTINCT FROM 'exploratory' OR
             policy_artifact_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'v0.22.0 first-slice Suite requires the published exploratory baseline policy v1';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_research_suite_validate
          BEFORE INSERT ON experiment.v022_research_suite
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_research_suite();

        CREATE FUNCTION experiment.validate_v022_research_suite_branch()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE suite_artifact_id uuid; suite_graph_id uuid; suite_execution jsonb;
                actual_branch_graph_id uuid; actual_branch_key varchar;
                snapshot_graph_id uuid; snapshot_branch_id uuid; snapshot_semantic jsonb;
                snapshot_artifact_status varchar; expected_ordinal integer;
        BEGIN
          SELECT artifact_id,compiled_research_graph_id,execution_policy_document
            INTO suite_artifact_id,suite_graph_id,suite_execution
            FROM experiment.v022_research_suite
           WHERE research_suite_id=NEW.research_suite_id;
          PERFORM data.assert_artifact_draft(suite_artifact_id);
          SELECT compiled_research_graph_id,branch_key
            INTO actual_branch_graph_id,actual_branch_key
            FROM strategy.v022_compiled_strategy_branch
           WHERE compiled_strategy_branch_id=NEW.compiled_strategy_branch_id;
          SELECT snapshot.compiled_research_graph_id,snapshot.compiled_strategy_branch_id,
                 snapshot.semantic_identity_document,artifact.status
            INTO snapshot_graph_id,snapshot_branch_id,snapshot_semantic,
                 snapshot_artifact_status
            FROM experiment.v022_research_configuration_snapshot snapshot
            JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id
           WHERE snapshot.configuration_snapshot_id=NEW.configuration_snapshot_id;
          SELECT count(*) INTO expected_ordinal
            FROM strategy.v022_compiled_strategy_branch branch
           WHERE branch.compiled_research_graph_id=suite_graph_id
             AND branch.branch_key<actual_branch_key;
          IF NEW.compiled_research_graph_id IS DISTINCT FROM suite_graph_id OR
             actual_branch_graph_id IS DISTINCT FROM suite_graph_id OR
             snapshot_graph_id IS DISTINCT FROM suite_graph_id OR
             snapshot_branch_id IS DISTINCT FROM NEW.compiled_strategy_branch_id THEN
            RAISE EXCEPTION 'v0.22 Suite, Branch, Snapshot, and Graph identities differ';
          END IF;
          IF NEW.branch_key IS DISTINCT FROM actual_branch_key OR
             NEW.ordinal IS DISTINCT FROM expected_ordinal THEN
            RAISE EXCEPTION 'v0.22 Suite Branch order is not the canonical compiled order';
          END IF;
          IF snapshot_artifact_status IS DISTINCT FROM 'published' OR
             snapshot_semantic->'execution_policy' IS DISTINCT FROM suite_execution THEN
            RAISE EXCEPTION 'v0.22 Suite Branch requires a published Snapshot with exact execution policy';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_research_suite_branch_validate
          BEFORE INSERT ON experiment.v022_research_suite_branch
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_research_suite_branch();

        CREATE FUNCTION experiment.validate_v022_research_cell()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE suite_artifact_id uuid; suite_graph_id uuid; suite_policy_id uuid;
                branch_suite_id uuid; branch_graph_id uuid; branch_strategy_id uuid;
                branch_snapshot_id uuid; branch_ordinal integer;
                policy_context_count integer; actual_context_fingerprint varchar;
                expected_ordinal integer;
        BEGIN
          SELECT artifact_id,compiled_research_graph_id,evaluation_matrix_policy_id
            INTO suite_artifact_id,suite_graph_id,suite_policy_id
            FROM experiment.v022_research_suite
           WHERE research_suite_id=NEW.research_suite_id;
          PERFORM data.assert_artifact_draft(suite_artifact_id);
          SELECT research_suite_id,compiled_research_graph_id,
                 compiled_strategy_branch_id,configuration_snapshot_id,ordinal
            INTO branch_suite_id,branch_graph_id,branch_strategy_id,
                 branch_snapshot_id,branch_ordinal
            FROM experiment.v022_research_suite_branch
           WHERE research_suite_branch_id=NEW.research_suite_branch_id;
          SELECT context_count INTO policy_context_count
            FROM experiment.v022_evaluation_matrix_policy
           WHERE evaluation_matrix_policy_id=suite_policy_id;
          SELECT context_fingerprint INTO actual_context_fingerprint
            FROM experiment.v022_evaluation_matrix_policy_context
           WHERE evaluation_matrix_policy_id=suite_policy_id
             AND ordinal=NEW.evaluation_context_ordinal;
          expected_ordinal := branch_ordinal*policy_context_count +
                              NEW.evaluation_context_ordinal;
          IF NEW.compiled_research_graph_id IS DISTINCT FROM suite_graph_id OR
             branch_suite_id IS DISTINCT FROM NEW.research_suite_id OR
             branch_graph_id IS DISTINCT FROM suite_graph_id OR
             NEW.compiled_strategy_branch_id IS DISTINCT FROM branch_strategy_id OR
             NEW.configuration_snapshot_id IS DISTINCT FROM branch_snapshot_id OR
             NEW.evaluation_matrix_policy_id IS DISTINCT FROM suite_policy_id THEN
            RAISE EXCEPTION 'v0.22 Research Cell does not bind its exact Suite Branch and Graph';
          END IF;
          IF actual_context_fingerprint IS NULL OR
             NEW.evaluation_context_fingerprint IS DISTINCT FROM actual_context_fingerprint OR
             NEW.ordinal IS DISTINCT FROM expected_ordinal THEN
            RAISE EXCEPTION 'v0.22 Research Cell is not the canonical server policy expansion';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_research_cell_validate
          BEFORE INSERT ON experiment.v022_research_cell
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_research_cell();

        CREATE FUNCTION experiment.validate_v022_research_suite_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_branch_count integer; actual_branch_count integer;
                context_count integer; expected_cell_count integer; actual_cell_count integer;
                missing_branch_count integer; missing_cell_count integer;
                suite_artifact_type varchar; suite_artifact_status varchar;
                suite_artifact_fingerprint varchar; graph_artifact_id uuid;
                policy_artifact_id uuid; missing_snapshot_dependency_count integer;
        BEGIN
          SELECT strategy_branch_count,artifact_id
            INTO expected_branch_count,graph_artifact_id
            FROM workspace.compiled_research_graph
           WHERE compiled_research_graph_id=NEW.compiled_research_graph_id;
          SELECT policy.context_count,policy.artifact_id
            INTO context_count,policy_artifact_id
            FROM experiment.v022_evaluation_matrix_policy policy
           WHERE policy.evaluation_matrix_policy_id=NEW.evaluation_matrix_policy_id;
          expected_cell_count := expected_branch_count*context_count;
          SELECT count(*) INTO actual_branch_count
            FROM experiment.v022_research_suite_branch
           WHERE research_suite_id=NEW.research_suite_id;
          SELECT count(*) INTO actual_cell_count
            FROM experiment.v022_research_cell
           WHERE research_suite_id=NEW.research_suite_id;
          SELECT count(*) INTO missing_branch_count
            FROM strategy.v022_compiled_strategy_branch branch
           WHERE branch.compiled_research_graph_id=NEW.compiled_research_graph_id
             AND NOT EXISTS (
               SELECT 1 FROM experiment.v022_research_suite_branch suite_branch
                WHERE suite_branch.research_suite_id=NEW.research_suite_id
                  AND suite_branch.compiled_strategy_branch_id=
                      branch.compiled_strategy_branch_id
             );
          SELECT count(*) INTO missing_cell_count
            FROM experiment.v022_research_suite_branch suite_branch
            CROSS JOIN experiment.v022_evaluation_matrix_policy_context context
           WHERE suite_branch.research_suite_id=NEW.research_suite_id
             AND context.evaluation_matrix_policy_id=NEW.evaluation_matrix_policy_id
             AND NOT EXISTS (
               SELECT 1 FROM experiment.v022_research_cell cell
                WHERE cell.research_suite_id=NEW.research_suite_id
                  AND cell.research_suite_branch_id=suite_branch.research_suite_branch_id
                  AND cell.evaluation_context_ordinal=context.ordinal
             );
          SELECT artifact_type,status,semantic_fingerprint
            INTO suite_artifact_type,suite_artifact_status,suite_artifact_fingerprint
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT count(*) INTO missing_snapshot_dependency_count
            FROM experiment.v022_research_suite_branch suite_branch
            JOIN experiment.v022_research_configuration_snapshot snapshot
              ON snapshot.configuration_snapshot_id=suite_branch.configuration_snapshot_id
           WHERE suite_branch.research_suite_id=NEW.research_suite_id
             AND NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=snapshot.artifact_id
                  AND dependency.role='configuration_snapshot'
                  AND dependency.ordinal=suite_branch.ordinal
             );
          IF NEW.branch_count<>expected_branch_count OR
             actual_branch_count<>expected_branch_count OR missing_branch_count<>0 THEN
            RAISE EXCEPTION 'v0.22 Research Suite does not contain the complete compiled Branch set';
          END IF;
          IF NEW.cell_count<>expected_cell_count OR
             actual_cell_count<>expected_cell_count OR missing_cell_count<>0 THEN
            RAISE EXCEPTION 'v0.22 Research Suite Cells do not completely expand the server policy';
          END IF;
          IF suite_artifact_type IS DISTINCT FROM 'v022_research_suite' OR
             suite_artifact_status IS DISTINCT FROM 'published' OR
             suite_artifact_fingerprint IS DISTINCT FROM NEW.suite_fingerprint THEN
            RAISE EXCEPTION 'v0.22 Research Suite Artifact is not exactly published';
          END IF;
          IF NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=graph_artifact_id
                  AND dependency.role='compiled_graph'
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=policy_artifact_id
                  AND dependency.role='evaluation_matrix_policy'
             ) OR missing_snapshot_dependency_count<>0 THEN
            RAISE EXCEPTION 'v0.22 Research Suite Artifact lineage is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_research_suite_complete
          AFTER INSERT ON experiment.v022_research_suite
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_research_suite_complete();

        CREATE FUNCTION experiment.validate_v022_research_suite_graph_run_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE suite_graph_id uuid; suite_artifact_status varchar; run_graph_id uuid;
        BEGIN
          SELECT suite.compiled_research_graph_id,artifact.status
            INTO suite_graph_id,suite_artifact_status
            FROM experiment.v022_research_suite suite
            JOIN lineage.artifact artifact ON artifact.artifact_id=suite.artifact_id
           WHERE suite.research_suite_id=NEW.research_suite_id;
          SELECT compiled_research_graph_id INTO run_graph_id
            FROM workspace.v022_graph_run WHERE graph_run_id=NEW.graph_run_id;
          IF suite_artifact_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'v0.22 Graph Run binding requires a published Research Suite';
          END IF;
          IF NEW.compiled_research_graph_id IS DISTINCT FROM suite_graph_id OR
             run_graph_id IS DISTINCT FROM suite_graph_id THEN
            RAISE EXCEPTION 'v0.22 Research Suite and Graph Run bind different Graphs';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_research_suite_graph_run_binding_validate
          BEFORE INSERT ON experiment.v022_research_suite_graph_run_binding
          FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_research_suite_graph_run_binding();
        """
    )


def _create_append_only_guards() -> None:
    for table in (
        "v022_evaluation_matrix_policy",
        "v022_evaluation_matrix_policy_context",
        "v022_research_suite",
        "v022_research_suite_branch",
        "v022_research_cell",
        "v022_research_suite_graph_run_binding",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON experiment.{table} FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def downgrade() -> None:
    for function in (
        "validate_v022_research_suite_graph_run_binding",
        "validate_v022_research_suite_complete",
        "validate_v022_research_cell",
        "validate_v022_research_suite_branch",
        "validate_v022_research_suite",
        "validate_v022_evaluation_matrix_policy_complete",
        "validate_v022_evaluation_matrix_policy_context",
        "validate_v022_evaluation_matrix_policy",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS experiment.{function}() CASCADE")
    op.drop_table("v022_research_suite_graph_run_binding", schema="experiment")
    op.drop_table("v022_research_cell", schema="experiment")
    op.drop_table("v022_research_suite_branch", schema="experiment")
    op.drop_table("v022_research_suite", schema="experiment")
    op.drop_table("v022_evaluation_matrix_policy_context", schema="experiment")
    op.drop_table("v022_evaluation_matrix_policy", schema="experiment")
