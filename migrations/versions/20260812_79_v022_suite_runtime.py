# ruff: noqa: E501
"""Add typed v0.22 Suite runtime DAG identities.

Revision ID: 20260812_79_v022_suite_runtime
Revises: 20260812_78_v022_snapshot_ctx
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_79_v022_suite_runtime"
down_revision: str | None = "20260812_78_v022_snapshot_ctx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _extend_graph_work_kinds()
    _create_portfolio_evaluation_context()
    _create_portfolio_evaluation_context_binding()
    _create_runtime_plan()
    _create_typed_work_specs()
    _create_typed_outputs()
    _create_plan_and_work_guards()
    _create_output_guards()
    _create_completion_guard()
    _create_aggregation_runtime_guards()
    _replace_mark_ready_function_with_reuse_completion()
    _replace_claim_function_with_lease_recovery()
    _create_append_only_guards()


def _extend_graph_work_kinds() -> None:
    op.execute(
        """
        ALTER TABLE workspace.v022_graph_work_item
          DROP CONSTRAINT v022_graph_work_item_work_kind_check;
        ALTER TABLE workspace.v022_graph_work_item
          ADD CONSTRAINT v022_graph_work_item_work_kind_check CHECK (
            work_kind IN (
              'node','aggregation','strategy_target','defense_decision',
              'sleeve_merge','portfolio_cell'
            )
          );
        ALTER TABLE workspace.v022_graph_work_item
          ADD CONSTRAINT uq_v022_graph_work_item_id_kind
          UNIQUE (graph_work_item_id,work_kind);
        ALTER TABLE workspace.v022_graph_work_consumer
          DROP CONSTRAINT v022_graph_work_consumer_occurrence_kind_check;
        ALTER TABLE workspace.v022_graph_work_consumer
          ADD CONSTRAINT v022_graph_work_consumer_occurrence_kind_check CHECK (
            occurrence_kind IN (
              'node','aggregation','strategy_target','defense_decision',
              'sleeve_merge','portfolio_cell'
            )
          );
        """
    )


def _create_portfolio_evaluation_context() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_portfolio_evaluation_data_context (
          portfolio_evaluation_data_context_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          evaluation_matrix_policy_id uuid NOT NULL
            REFERENCES experiment.v022_evaluation_matrix_policy,
          evaluation_context_ordinal integer NOT NULL CHECK (evaluation_context_ordinal>=0),
          benchmark_asset_id uuid NOT NULL REFERENCES catalog.asset,
          benchmark_asset_key varchar(240) NOT NULL DEFAULT 'spy'
            CHECK (benchmark_asset_key='spy'),
          benchmark_dataset_publication_id uuid NOT NULL
            REFERENCES data.dataset_publication,
          benchmark_dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          benchmark_calendar_version_id uuid NOT NULL
            REFERENCES catalog.calendar_version,
          benchmark_calendar_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          reserve_return_model_version_id uuid NOT NULL
            REFERENCES experiment.reserve_return_model_version,
          reserve_return_model_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          reserve_dataset_publication_id uuid NOT NULL
            REFERENCES data.dataset_publication,
          reserve_dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          reserve_calendar_version_id uuid NULL REFERENCES catalog.calendar_version,
          reserve_calendar_artifact_id uuid NULL REFERENCES lineage.artifact,
          coverage_start date NOT NULL,
          coverage_end date NOT NULL CHECK (coverage_end>=coverage_start),
          pit_document jsonb NOT NULL,
          common_interval_document jsonb NOT NULL,
          context_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (context_fingerprint ~ '^[0-9a-f]{64}$'),
          artifact_semantic_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (artifact_semantic_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (evaluation_matrix_policy_id,evaluation_context_ordinal)
            REFERENCES experiment.v022_evaluation_matrix_policy_context (
              evaluation_matrix_policy_id,ordinal
            ),
          CHECK ((reserve_calendar_version_id IS NULL)=
                 (reserve_calendar_artifact_id IS NULL)),
          CHECK (jsonb_typeof(pit_document)='object' AND pit_document<>'{}'::jsonb),
          CHECK (jsonb_typeof(common_interval_document)='object' AND
                 common_interval_document<>'{}'::jsonb)
        );
        CREATE TABLE experiment.v022_portfolio_evaluation_data_input (
          portfolio_evaluation_data_context_id uuid NOT NULL
            REFERENCES experiment.v022_portfolio_evaluation_data_context,
          ordinal integer NOT NULL CHECK (ordinal IN (0,1)),
          input_role varchar(40) NOT NULL
            CHECK (input_role IN ('benchmark_daily_bar','reserve_return')),
          dataset_publication_id uuid NOT NULL REFERENCES data.dataset_publication,
          dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          calendar_version_id uuid NULL REFERENCES catalog.calendar_version,
          calendar_artifact_id uuid NULL REFERENCES lineage.artifact,
          coverage_start date NOT NULL,
          coverage_end date NOT NULL CHECK (coverage_end>=coverage_start),
          dataset_fingerprint varchar(64) NOT NULL
            CHECK (dataset_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (portfolio_evaluation_data_context_id,ordinal),
          UNIQUE (portfolio_evaluation_data_context_id,input_role),
          CHECK ((calendar_version_id IS NULL)=(calendar_artifact_id IS NULL))
        );
        """
    )


def _create_portfolio_evaluation_context_binding() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_research_cell_evaluation_data_context_binding (
          research_cell_id uuid PRIMARY KEY REFERENCES experiment.v022_research_cell,
          portfolio_evaluation_data_context_id uuid NOT NULL
            REFERENCES experiment.v022_portfolio_evaluation_data_context,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE FUNCTION experiment.validate_v022_research_cell_evaluation_context_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE context_status varchar;
        BEGIN
          SELECT artifact.status INTO context_status
            FROM experiment.v022_portfolio_evaluation_data_context context
            JOIN lineage.artifact artifact ON artifact.artifact_id=context.artifact_id
            JOIN experiment.v022_research_cell cell
              ON cell.research_cell_id=NEW.research_cell_id
             AND cell.evaluation_matrix_policy_id=context.evaluation_matrix_policy_id
             AND cell.evaluation_context_ordinal=context.evaluation_context_ordinal
           WHERE context.portfolio_evaluation_data_context_id=
                 NEW.portfolio_evaluation_data_context_id;
          IF context_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Portfolio Cell Evaluation Data Context binding is not exact and published';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_research_cell_evaluation_context_binding_validate
          BEFORE INSERT ON experiment.v022_research_cell_evaluation_data_context_binding
          FOR EACH ROW EXECUTE FUNCTION
            experiment.validate_v022_research_cell_evaluation_context_binding();
        CREATE TRIGGER trg_v022_research_cell_evaluation_context_binding_append_only
          BEFORE UPDATE OR DELETE
          ON experiment.v022_research_cell_evaluation_data_context_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def _create_runtime_plan() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_suite_runtime_plan (
          suite_runtime_plan_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          research_suite_graph_run_binding_id uuid NOT NULL UNIQUE
            REFERENCES experiment.v022_research_suite_graph_run_binding,
          research_suite_id uuid NOT NULL
            REFERENCES experiment.v022_research_suite,
          compiled_research_graph_id uuid NOT NULL
            REFERENCES workspace.compiled_research_graph,
          catalog_release_id uuid NOT NULL
            REFERENCES workspace.v022_catalog_release,
          graph_run_id uuid NOT NULL UNIQUE REFERENCES workspace.v022_graph_run,
          compiled_execution_data_context_id uuid NOT NULL,
          strategy_target_payload_contract_version_id uuid NOT NULL
            REFERENCES data.payload_contract_version,
          defense_decision_payload_contract_version_id uuid NOT NULL
            REFERENCES data.payload_contract_version,
          sleeve_merge_payload_contract_version_id uuid NOT NULL
            REFERENCES data.payload_contract_version,
          portfolio_cell_payload_contract_version_id uuid NOT NULL
            REFERENCES data.payload_contract_version,
          physical_encoding_version_id uuid NOT NULL
            REFERENCES data.physical_encoding_version,
          contract_version varchar(32) NOT NULL CHECK (contract_version='v0.22.0'),
          requested_range jsonb NOT NULL,
          effective_range jsonb NOT NULL,
          executor_version varchar(120) NOT NULL CHECK (btrim(executor_version)<>''),
          environment_fingerprint varchar(64) NOT NULL
            CHECK (environment_fingerprint ~ '^[0-9a-f]{64}$'),
          strategy_target_work_count integer NOT NULL
            CHECK (strategy_target_work_count > 0),
          defense_decision_work_count integer NOT NULL
            CHECK (defense_decision_work_count >= 0),
          sleeve_merge_work_count integer NOT NULL
            CHECK (sleeve_merge_work_count > 0),
          portfolio_cell_work_count integer NOT NULL
            CHECK (portfolio_cell_work_count > 0),
          total_work_count integer NOT NULL CHECK (total_work_count > 0),
          plan_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (plan_fingerprint ~ '^[0-9a-f]{64}$'),
          artifact_semantic_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (artifact_semantic_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (research_suite_id,compiled_research_graph_id)
            REFERENCES experiment.v022_research_suite (
              research_suite_id,compiled_research_graph_id
            ),
          FOREIGN KEY (
            compiled_execution_data_context_id,compiled_research_graph_id
          ) REFERENCES workspace.v022_compiled_execution_data_context (
            compiled_execution_data_context_id,compiled_research_graph_id
          ),
          CHECK (
            jsonb_typeof(requested_range)='object' AND requested_range<>'{}'::jsonb AND
            jsonb_typeof(effective_range)='object' AND effective_range<>'{}'::jsonb
          ),
          CHECK (
            total_work_count = strategy_target_work_count +
              defense_decision_work_count + sleeve_merge_work_count +
              portfolio_cell_work_count
          )
        );
        """
    )


def _create_typed_work_specs() -> None:
    op.execute(
        """
        CREATE TABLE strategy.v022_strategy_target_work_spec (
          strategy_target_work_spec_id uuid PRIMARY KEY,
          graph_work_item_id uuid NOT NULL,
          work_kind varchar(24) NOT NULL DEFAULT 'strategy_target'
            CHECK (work_kind='strategy_target'),
          suite_runtime_plan_id uuid NOT NULL
            REFERENCES experiment.v022_suite_runtime_plan,
          research_suite_branch_id uuid NOT NULL
            REFERENCES experiment.v022_research_suite_branch,
          compiled_strategy_branch_id uuid NOT NULL
            REFERENCES strategy.v022_compiled_strategy_branch,
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          compiled_execution_data_context_id uuid NOT NULL
            REFERENCES workspace.v022_compiled_execution_data_context,
          output_payload_contract_version_id uuid NOT NULL
            REFERENCES data.payload_contract_version,
          physical_encoding_version_id uuid NOT NULL
            REFERENCES data.physical_encoding_version,
          source_aggregation_work_item_id uuid NOT NULL
            REFERENCES workspace.v022_graph_work_item,
          occurrence_key varchar(500) NOT NULL,
          specification_document jsonb NOT NULL,
          specification_fingerprint varchar(64) NOT NULL
            CHECK (specification_fingerprint ~ '^[0-9a-f]{64}$'),
          plan_artifact_semantic_fingerprint varchar(64) NOT NULL
            CHECK (plan_artifact_semantic_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (graph_work_item_id,work_kind)
            REFERENCES workspace.v022_graph_work_item (
              graph_work_item_id,work_kind
            ),
          UNIQUE (suite_runtime_plan_id,research_suite_branch_id),
          UNIQUE (
            suite_runtime_plan_id,research_suite_branch_id,graph_work_item_id
          ),
          CHECK (
            jsonb_typeof(specification_document)='object' AND
            specification_document<>'{}'::jsonb
          )
        );

        CREATE TABLE defense.v022_defense_decision_work_spec (
          defense_decision_work_spec_id uuid PRIMARY KEY,
          graph_work_item_id uuid NOT NULL,
          work_kind varchar(24) NOT NULL DEFAULT 'defense_decision'
            CHECK (work_kind='defense_decision'),
          suite_runtime_plan_id uuid NOT NULL
            REFERENCES experiment.v022_suite_runtime_plan,
          research_suite_branch_id uuid NOT NULL
            REFERENCES experiment.v022_research_suite_branch,
          compiled_strategy_branch_id uuid NOT NULL
            REFERENCES strategy.v022_compiled_strategy_branch,
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          defense_version_id uuid NOT NULL REFERENCES defense.defense_version,
          timing_policy_version_id uuid NOT NULL
            REFERENCES defense.v022_timing_policy_version,
          allocation_policy_version_id uuid NOT NULL
            REFERENCES defense.v022_allocation_policy_version,
          compiled_defense_execution_context_id uuid NOT NULL
            REFERENCES defense.v022_compiled_defense_execution_context,
          output_payload_contract_version_id uuid NOT NULL
            REFERENCES data.payload_contract_version,
          physical_encoding_version_id uuid NOT NULL
            REFERENCES data.physical_encoding_version,
          source_strategy_work_item_id uuid NOT NULL,
          occurrence_key varchar(500) NOT NULL,
          specification_document jsonb NOT NULL,
          specification_fingerprint varchar(64) NOT NULL
            CHECK (specification_fingerprint ~ '^[0-9a-f]{64}$'),
          plan_artifact_semantic_fingerprint varchar(64) NOT NULL
            CHECK (plan_artifact_semantic_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (graph_work_item_id,work_kind)
            REFERENCES workspace.v022_graph_work_item (
              graph_work_item_id,work_kind
            ),
          UNIQUE (suite_runtime_plan_id,research_suite_branch_id),
          UNIQUE (
            suite_runtime_plan_id,research_suite_branch_id,graph_work_item_id
          ),
          FOREIGN KEY (
            suite_runtime_plan_id,research_suite_branch_id,
            source_strategy_work_item_id
          ) REFERENCES strategy.v022_strategy_target_work_spec (
            suite_runtime_plan_id,research_suite_branch_id,graph_work_item_id
          ),
          CHECK (
            jsonb_typeof(specification_document)='object' AND
            specification_document<>'{}'::jsonb
          )
        );

        CREATE TABLE strategy.v022_sleeve_merge_work_spec (
          sleeve_merge_work_spec_id uuid PRIMARY KEY,
          graph_work_item_id uuid NOT NULL,
          work_kind varchar(24) NOT NULL DEFAULT 'sleeve_merge'
            CHECK (work_kind='sleeve_merge'),
          suite_runtime_plan_id uuid NOT NULL
            REFERENCES experiment.v022_suite_runtime_plan,
          research_suite_branch_id uuid NOT NULL
            REFERENCES experiment.v022_research_suite_branch,
          compiled_strategy_branch_id uuid NOT NULL
            REFERENCES strategy.v022_compiled_strategy_branch,
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          output_payload_contract_version_id uuid NOT NULL
            REFERENCES data.payload_contract_version,
          physical_encoding_version_id uuid NOT NULL
            REFERENCES data.physical_encoding_version,
          source_strategy_work_item_id uuid NOT NULL,
          source_defense_work_item_id uuid NULL,
          occurrence_key varchar(500) NOT NULL,
          specification_document jsonb NOT NULL,
          specification_fingerprint varchar(64) NOT NULL
            CHECK (specification_fingerprint ~ '^[0-9a-f]{64}$'),
          plan_artifact_semantic_fingerprint varchar(64) NOT NULL
            CHECK (plan_artifact_semantic_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (graph_work_item_id,work_kind)
            REFERENCES workspace.v022_graph_work_item (
              graph_work_item_id,work_kind
            ),
          UNIQUE (suite_runtime_plan_id,research_suite_branch_id),
          UNIQUE (
            suite_runtime_plan_id,research_suite_branch_id,graph_work_item_id
          ),
          FOREIGN KEY (
            suite_runtime_plan_id,research_suite_branch_id,
            source_strategy_work_item_id
          ) REFERENCES strategy.v022_strategy_target_work_spec (
            suite_runtime_plan_id,research_suite_branch_id,graph_work_item_id
          ),
          FOREIGN KEY (
            suite_runtime_plan_id,research_suite_branch_id,
            source_defense_work_item_id
          ) REFERENCES defense.v022_defense_decision_work_spec (
            suite_runtime_plan_id,research_suite_branch_id,graph_work_item_id
          ),
          CHECK (
            jsonb_typeof(specification_document)='object' AND
            specification_document<>'{}'::jsonb
          )
        );

        CREATE TABLE experiment.v022_portfolio_cell_work_spec (
          portfolio_cell_work_spec_id uuid PRIMARY KEY,
          graph_work_item_id uuid NOT NULL,
          work_kind varchar(24) NOT NULL DEFAULT 'portfolio_cell'
            CHECK (work_kind='portfolio_cell'),
          suite_runtime_plan_id uuid NOT NULL
            REFERENCES experiment.v022_suite_runtime_plan,
          research_suite_branch_id uuid NOT NULL
            REFERENCES experiment.v022_research_suite_branch,
          research_cell_id uuid NOT NULL
            REFERENCES experiment.v022_research_cell,
          compiled_strategy_branch_id uuid NOT NULL
            REFERENCES strategy.v022_compiled_strategy_branch,
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          portfolio_evaluation_data_context_id uuid NOT NULL
            REFERENCES experiment.v022_portfolio_evaluation_data_context,
          output_payload_contract_version_id uuid NOT NULL
            REFERENCES data.payload_contract_version,
          physical_encoding_version_id uuid NOT NULL
            REFERENCES data.physical_encoding_version,
          source_merge_work_item_id uuid NOT NULL,
          occurrence_key varchar(500) NOT NULL,
          specification_document jsonb NOT NULL,
          specification_fingerprint varchar(64) NOT NULL
            CHECK (specification_fingerprint ~ '^[0-9a-f]{64}$'),
          plan_artifact_semantic_fingerprint varchar(64) NOT NULL
            CHECK (plan_artifact_semantic_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (graph_work_item_id,work_kind)
            REFERENCES workspace.v022_graph_work_item (
              graph_work_item_id,work_kind
            ),
          UNIQUE (suite_runtime_plan_id,research_cell_id),
          FOREIGN KEY (
            suite_runtime_plan_id,research_suite_branch_id,
            source_merge_work_item_id
          ) REFERENCES strategy.v022_sleeve_merge_work_spec (
            suite_runtime_plan_id,research_suite_branch_id,graph_work_item_id
          ),
          CHECK (
            jsonb_typeof(specification_document)='object' AND
            specification_document<>'{}'::jsonb
          )
        );

        CREATE INDEX ix_v022_strategy_target_work_spec_work_item
          ON strategy.v022_strategy_target_work_spec (graph_work_item_id);
        CREATE INDEX ix_v022_defense_decision_work_spec_work_item
          ON defense.v022_defense_decision_work_spec (graph_work_item_id);
        CREATE INDEX ix_v022_sleeve_merge_work_spec_work_item
          ON strategy.v022_sleeve_merge_work_spec (graph_work_item_id);
        CREATE INDEX ix_v022_portfolio_cell_work_spec_work_item
          ON experiment.v022_portfolio_cell_work_spec (graph_work_item_id);
        """
    )


def _create_typed_outputs() -> None:
    op.execute(
        """
        CREATE TABLE strategy.v022_strategy_target_path (
          strategy_target_path_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          graph_work_item_id uuid NOT NULL UNIQUE,
          work_kind varchar(24) NOT NULL DEFAULT 'strategy_target'
            CHECK (work_kind='strategy_target'),
          payload_manifest_id uuid NOT NULL UNIQUE REFERENCES data.payload_manifest,
          payload_manifest_artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          manifest_hash varchar(64) NOT NULL
            CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
          work_execution_fingerprint varchar(64) NOT NULL
            CHECK (work_execution_fingerprint ~ '^[0-9a-f]{64}$'),
          logical_payload_fingerprint varchar(64) NOT NULL
            CHECK (logical_payload_fingerprint ~ '^[0-9a-f]{64}$'),
          output_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (output_fingerprint ~ '^[0-9a-f]{64}$'),
          artifact_semantic_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (artifact_semantic_fingerprint ~ '^[0-9a-f]{64}$'),
          decision_count integer NOT NULL CHECK (decision_count > 0),
          target_document jsonb NOT NULL,
          worker_key varchar(160) NOT NULL CHECK (btrim(worker_key)<>''),
          fencing_token bigint NOT NULL CHECK (fencing_token > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (graph_work_item_id,work_kind)
            REFERENCES workspace.v022_graph_work_item (
              graph_work_item_id,work_kind
            ),
          CHECK (artifact_id<>payload_manifest_artifact_id),
          CHECK (output_fingerprint=work_execution_fingerprint),
          CHECK (jsonb_typeof(target_document)='object' AND target_document<>'{}'::jsonb)
        );

        CREATE TABLE defense.v022_defense_decision_path (
          defense_decision_path_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          graph_work_item_id uuid NOT NULL UNIQUE,
          work_kind varchar(24) NOT NULL DEFAULT 'defense_decision'
            CHECK (work_kind='defense_decision'),
          payload_manifest_id uuid NOT NULL UNIQUE REFERENCES data.payload_manifest,
          payload_manifest_artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          manifest_hash varchar(64) NOT NULL
            CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
          work_execution_fingerprint varchar(64) NOT NULL
            CHECK (work_execution_fingerprint ~ '^[0-9a-f]{64}$'),
          logical_payload_fingerprint varchar(64) NOT NULL
            CHECK (logical_payload_fingerprint ~ '^[0-9a-f]{64}$'),
          output_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (output_fingerprint ~ '^[0-9a-f]{64}$'),
          artifact_semantic_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (artifact_semantic_fingerprint ~ '^[0-9a-f]{64}$'),
          decision_count integer NOT NULL CHECK (decision_count > 0),
          decision_document jsonb NOT NULL,
          worker_key varchar(160) NOT NULL CHECK (btrim(worker_key)<>''),
          fencing_token bigint NOT NULL CHECK (fencing_token > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (graph_work_item_id,work_kind)
            REFERENCES workspace.v022_graph_work_item (
              graph_work_item_id,work_kind
            ),
          CHECK (artifact_id<>payload_manifest_artifact_id),
          CHECK (output_fingerprint=work_execution_fingerprint),
          CHECK (jsonb_typeof(decision_document)='object' AND decision_document<>'{}'::jsonb)
        );

        CREATE TABLE strategy.v022_merged_portfolio_target_path (
          merged_portfolio_target_path_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          graph_work_item_id uuid NOT NULL UNIQUE,
          work_kind varchar(24) NOT NULL DEFAULT 'sleeve_merge'
            CHECK (work_kind='sleeve_merge'),
          payload_manifest_id uuid NOT NULL UNIQUE REFERENCES data.payload_manifest,
          payload_manifest_artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          manifest_hash varchar(64) NOT NULL
            CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
          work_execution_fingerprint varchar(64) NOT NULL
            CHECK (work_execution_fingerprint ~ '^[0-9a-f]{64}$'),
          logical_payload_fingerprint varchar(64) NOT NULL
            CHECK (logical_payload_fingerprint ~ '^[0-9a-f]{64}$'),
          output_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (output_fingerprint ~ '^[0-9a-f]{64}$'),
          artifact_semantic_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (artifact_semantic_fingerprint ~ '^[0-9a-f]{64}$'),
          decision_count integer NOT NULL CHECK (decision_count > 0),
          target_document jsonb NOT NULL,
          worker_key varchar(160) NOT NULL CHECK (btrim(worker_key)<>''),
          fencing_token bigint NOT NULL CHECK (fencing_token > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (graph_work_item_id,work_kind)
            REFERENCES workspace.v022_graph_work_item (
              graph_work_item_id,work_kind
            ),
          CHECK (artifact_id<>payload_manifest_artifact_id),
          CHECK (output_fingerprint=work_execution_fingerprint),
          CHECK (jsonb_typeof(target_document)='object' AND target_document<>'{}'::jsonb)
        );

        CREATE TABLE experiment.v022_portfolio_cell_runtime_result (
          portfolio_cell_runtime_result_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          graph_work_item_id uuid NOT NULL UNIQUE,
          work_kind varchar(24) NOT NULL DEFAULT 'portfolio_cell'
            CHECK (work_kind='portfolio_cell'),
          payload_manifest_id uuid NOT NULL UNIQUE REFERENCES data.payload_manifest,
          payload_manifest_artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          manifest_hash varchar(64) NOT NULL
            CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
          work_execution_fingerprint varchar(64) NOT NULL
            CHECK (work_execution_fingerprint ~ '^[0-9a-f]{64}$'),
          logical_payload_fingerprint varchar(64) NOT NULL
            CHECK (logical_payload_fingerprint ~ '^[0-9a-f]{64}$'),
          result_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (result_fingerprint ~ '^[0-9a-f]{64}$'),
          artifact_semantic_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (artifact_semantic_fingerprint ~ '^[0-9a-f]{64}$'),
          compiled_strategy_branch_id uuid NOT NULL
            REFERENCES strategy.v022_compiled_strategy_branch,
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          evaluation_data_context_fingerprint varchar(64) NOT NULL
            CHECK (evaluation_data_context_fingerprint ~ '^[0-9a-f]{64}$'),
          outcome varchar(32) NOT NULL CHECK (
            outcome IN ('accepted','data_quality_failed','capacity_rejected')
          ),
          quality_status varchar(24) NOT NULL CHECK (
            quality_status IN ('passed','warning','failed')
          ),
          effective_start date NOT NULL,
          effective_end date NOT NULL,
          metric_document jsonb NOT NULL,
          result_document jsonb NOT NULL,
          worker_key varchar(160) NOT NULL CHECK (btrim(worker_key)<>''),
          fencing_token bigint NOT NULL CHECK (fencing_token > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (graph_work_item_id,work_kind)
            REFERENCES workspace.v022_graph_work_item (
              graph_work_item_id,work_kind
            ),
          CHECK (artifact_id<>payload_manifest_artifact_id),
          CHECK (result_fingerprint=work_execution_fingerprint),
          CHECK (effective_end>=effective_start),
          CHECK (jsonb_typeof(metric_document)='object'),
          CHECK (jsonb_typeof(result_document)='object' AND result_document<>'{}'::jsonb)
        );
        CREATE INDEX ix_v022_strategy_target_path_logical_payload
          ON strategy.v022_strategy_target_path (logical_payload_fingerprint);
        CREATE INDEX ix_v022_defense_decision_path_logical_payload
          ON defense.v022_defense_decision_path (logical_payload_fingerprint);
        CREATE INDEX ix_v022_merged_portfolio_target_path_logical_payload
          ON strategy.v022_merged_portfolio_target_path (logical_payload_fingerprint);
        CREATE INDEX ix_v022_portfolio_cell_result_logical_payload
          ON experiment.v022_portfolio_cell_runtime_result (logical_payload_fingerprint);
        """
    )


def _create_plan_and_work_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.validate_v022_portfolio_evaluation_data_context()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; benchmark_row record; reserve_row record;
                benchmark_count integer; expected_dependency_count integer;
        BEGIN
          SELECT artifact_type,artifact_key,version_number,status,semantic_fingerprint
            INTO artifact_row FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_portfolio_evaluation_data_context' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_portfolio_evaluation_data_context__'||NEW.context_fingerprint OR
             artifact_row.version_number IS DISTINCT FROM 1 OR
             artifact_row.status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'Portfolio Evaluation Data Context requires its exact draft Artifact';
          END IF;
          IF NEW.pit_document->>'policy_key' IS DISTINCT FROM
               'point_in_time_known_at_v1' OR
             NEW.pit_document->>'benchmark_dataset_publication_id' IS DISTINCT FROM
               NEW.benchmark_dataset_publication_id::text OR
             NEW.pit_document->>'reserve_dataset_publication_id' IS DISTINCT FROM
               NEW.reserve_dataset_publication_id::text OR
             NEW.pit_document->>'reserve_return_model_version_id' IS DISTINCT FROM
               NEW.reserve_return_model_version_id::text OR
             NEW.common_interval_document->>'policy_key' IS DISTINCT FROM
               'full_common_history_spy_v1' OR
             NEW.common_interval_document->>'evaluation_matrix_policy_id'
               IS DISTINCT FROM NEW.evaluation_matrix_policy_id::text OR
             NEW.common_interval_document->>'evaluation_context_ordinal'
               IS DISTINCT FROM NEW.evaluation_context_ordinal::text OR
             NEW.common_interval_document->>'coverage_start' IS DISTINCT FROM
               NEW.coverage_start::text OR
             NEW.common_interval_document->>'coverage_end' IS DISTINCT FROM
               NEW.coverage_end::text OR
             NEW.common_interval_document->>'benchmark_calendar_version_id'
               IS DISTINCT FROM NEW.benchmark_calendar_version_id::text OR
             NEW.common_interval_document->>'reserve_calendar_version_id'
               IS DISTINCT FROM (CASE WHEN NEW.reserve_calendar_version_id IS NULL
                 THEN NULL ELSE NEW.reserve_calendar_version_id::text END) THEN
            RAISE EXCEPTION 'Portfolio Evaluation Data Context documents drift from exact PIT and common-interval projections';
          END IF;
          SELECT publication.artifact_id,publication.calendar_version_id,
                 publication.value_kind,
                 dataset_artifact.status AS dataset_status,
                 calendar.artifact_id AS calendar_artifact_id,
                 calendar_artifact.status AS calendar_status,
                 publication.coverage_start,publication.coverage_end
            INTO benchmark_row
            FROM data.dataset_publication publication
            JOIN lineage.artifact dataset_artifact
              ON dataset_artifact.artifact_id=publication.artifact_id
            JOIN catalog.calendar_version calendar
              ON calendar.calendar_version_id=publication.calendar_version_id
            JOIN lineage.artifact calendar_artifact
              ON calendar_artifact.artifact_id=calendar.artifact_id
           WHERE publication.dataset_publication_id=
                 NEW.benchmark_dataset_publication_id;
          SELECT count(*) INTO benchmark_count
            FROM data.daily_bar bar
            JOIN catalog.asset asset ON asset.asset_id=bar.asset_id
             WHERE bar.dataset_publication_id=NEW.benchmark_dataset_publication_id
             AND bar.asset_id=NEW.benchmark_asset_id AND asset.asset_key='spy';
          SELECT publication.artifact_id,publication.calendar_version_id,
                 publication.dataset_key,publication.value_kind,
                 dataset_artifact.status AS dataset_status,
                 model.artifact_id AS model_artifact_id,
                 model_artifact.status AS model_status,
                 calendar.artifact_id AS calendar_artifact_id,
                 calendar_artifact.status AS calendar_status,
                 publication.coverage_start,publication.coverage_end,
                 EXISTS (
                   SELECT 1 FROM lineage.artifact_dependency dependency
                    WHERE dependency.artifact_id=publication.artifact_id
                      AND dependency.depends_on_artifact_id=model.artifact_id
                      AND dependency.role='reserve_model'
                      AND dependency.ordinal=2
                 ) AS has_exact_model_dependency
            INTO reserve_row
            FROM data.dataset_publication publication
            JOIN lineage.artifact dataset_artifact
              ON dataset_artifact.artifact_id=publication.artifact_id
            JOIN experiment.reserve_return_model_version model
              ON model.reserve_return_model_version_id=
                 NEW.reserve_return_model_version_id
            JOIN lineage.artifact model_artifact ON model_artifact.artifact_id=model.artifact_id
            LEFT JOIN catalog.calendar_version calendar
              ON calendar.calendar_version_id=publication.calendar_version_id
            LEFT JOIN lineage.artifact calendar_artifact
              ON calendar_artifact.artifact_id=calendar.artifact_id
           WHERE publication.dataset_publication_id=NEW.reserve_dataset_publication_id
             AND EXISTS (
               SELECT 1 FROM experiment.reserve_return reserve
                WHERE reserve.dataset_publication_id=publication.dataset_publication_id
             );
          IF benchmark_row.artifact_id IS DISTINCT FROM
               NEW.benchmark_dataset_artifact_id OR
             benchmark_row.calendar_version_id IS DISTINCT FROM
               NEW.benchmark_calendar_version_id OR
             benchmark_row.calendar_artifact_id IS DISTINCT FROM
               NEW.benchmark_calendar_artifact_id OR
             benchmark_row.value_kind IS DISTINCT FROM 'daily_bar' OR
             benchmark_row.dataset_status IS DISTINCT FROM 'published' OR
             benchmark_row.calendar_status IS DISTINCT FROM 'published' OR
             benchmark_count<1 OR
             reserve_row.artifact_id IS DISTINCT FROM NEW.reserve_dataset_artifact_id OR
             reserve_row.dataset_key IS DISTINCT FROM 'dgs3mo_reserve_return' OR
             reserve_row.value_kind IS DISTINCT FROM 'reserve_return' OR
             reserve_row.model_artifact_id IS DISTINCT FROM
               NEW.reserve_return_model_artifact_id OR
             reserve_row.has_exact_model_dependency IS DISTINCT FROM true OR
             reserve_row.dataset_status IS DISTINCT FROM 'published' OR
             reserve_row.model_status IS DISTINCT FROM 'published' OR
             reserve_row.calendar_version_id IS DISTINCT FROM
               NEW.reserve_calendar_version_id OR
             reserve_row.calendar_artifact_id IS DISTINCT FROM
               NEW.reserve_calendar_artifact_id OR
             (reserve_row.calendar_artifact_id IS NOT NULL AND
              reserve_row.calendar_status IS DISTINCT FROM 'published') OR
             NEW.coverage_start<>greatest(
               benchmark_row.coverage_start,reserve_row.coverage_start
             ) OR NEW.coverage_end<>least(
               benchmark_row.coverage_end,reserve_row.coverage_end
             ) THEN
            RAISE EXCEPTION 'Portfolio Evaluation Data Context inputs are not exact published spy and Reserve identities';
          END IF;
          expected_dependency_count := CASE
            WHEN NEW.reserve_calendar_artifact_id IS NULL THEN 5 ELSE 6 END;
          IF (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>
               expected_dependency_count OR NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=NEW.artifact_id
                    AND dependency.depends_on_artifact_id=
                        NEW.benchmark_dataset_artifact_id
                    AND dependency.role='benchmark_dataset' AND dependency.ordinal=0
               ) OR NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=NEW.artifact_id
                    AND dependency.depends_on_artifact_id=
                        NEW.benchmark_calendar_artifact_id
                    AND dependency.role='benchmark_calendar' AND dependency.ordinal=1
               ) OR NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=NEW.artifact_id
                    AND dependency.depends_on_artifact_id=
                        NEW.reserve_return_model_artifact_id
                    AND dependency.role='reserve_return_model' AND dependency.ordinal=2
               ) OR NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=NEW.artifact_id
                    AND dependency.depends_on_artifact_id=
                        NEW.reserve_dataset_artifact_id
                    AND dependency.role='reserve_dataset' AND dependency.ordinal=3
               ) OR NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                 JOIN experiment.v022_evaluation_matrix_policy policy
                   ON policy.artifact_id=dependency.depends_on_artifact_id
                  WHERE dependency.artifact_id=NEW.artifact_id
                    AND dependency.role='evaluation_policy' AND dependency.ordinal=4
                    AND policy.evaluation_matrix_policy_id=
                        NEW.evaluation_matrix_policy_id
               ) OR (
                 NEW.reserve_calendar_artifact_id IS NOT NULL AND NOT EXISTS (
                   SELECT 1 FROM lineage.artifact_dependency dependency
                    WHERE dependency.artifact_id=NEW.artifact_id
                      AND dependency.depends_on_artifact_id=
                          NEW.reserve_calendar_artifact_id
                      AND dependency.role='reserve_calendar' AND dependency.ordinal=5
                 )
               ) THEN
            RAISE EXCEPTION 'Portfolio Evaluation Data Context has incomplete or extra lineage';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_portfolio_evaluation_data_context_validate
          BEFORE INSERT ON experiment.v022_portfolio_evaluation_data_context
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_portfolio_evaluation_data_context();

        CREATE FUNCTION experiment.validate_v022_portfolio_evaluation_data_context_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_status varchar; artifact_fingerprint varchar; input_count integer;
        BEGIN
          SELECT status,semantic_fingerprint
            INTO artifact_status,artifact_fingerprint FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          SELECT count(*) INTO input_count
            FROM experiment.v022_portfolio_evaluation_data_input input
           WHERE input.portfolio_evaluation_data_context_id=
                 NEW.portfolio_evaluation_data_context_id;
          IF artifact_status IS DISTINCT FROM 'published' OR
             artifact_fingerprint IS DISTINCT FROM
               NEW.artifact_semantic_fingerprint OR input_count<>2 THEN
            RAISE EXCEPTION 'Portfolio Evaluation Data Context is not atomically published and complete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_portfolio_evaluation_data_context_complete
          AFTER INSERT ON experiment.v022_portfolio_evaluation_data_context
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_portfolio_evaluation_data_context_complete();

        CREATE FUNCTION experiment.validate_v022_portfolio_evaluation_data_input()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE context_row record; dataset_fingerprint varchar;
        BEGIN
          SELECT * INTO context_row
            FROM experiment.v022_portfolio_evaluation_data_context
           WHERE portfolio_evaluation_data_context_id=
                 NEW.portfolio_evaluation_data_context_id;
          SELECT semantic_fingerprint INTO dataset_fingerprint
            FROM lineage.artifact WHERE artifact_id=NEW.dataset_artifact_id;
          IF NEW.dataset_fingerprint IS DISTINCT FROM dataset_fingerprint OR
             NEW.ordinal=0 AND (
               NEW.input_role IS DISTINCT FROM 'benchmark_daily_bar' OR
               NEW.dataset_publication_id IS DISTINCT FROM
                 context_row.benchmark_dataset_publication_id OR
               NEW.dataset_artifact_id IS DISTINCT FROM
                 context_row.benchmark_dataset_artifact_id OR
               NEW.calendar_version_id IS DISTINCT FROM
                 context_row.benchmark_calendar_version_id OR
               NEW.calendar_artifact_id IS DISTINCT FROM
                 context_row.benchmark_calendar_artifact_id
             ) OR NEW.ordinal=1 AND (
               NEW.input_role IS DISTINCT FROM 'reserve_return' OR
               NEW.dataset_publication_id IS DISTINCT FROM
                 context_row.reserve_dataset_publication_id OR
               NEW.dataset_artifact_id IS DISTINCT FROM
                 context_row.reserve_dataset_artifact_id OR
               NEW.calendar_version_id IS DISTINCT FROM
                 context_row.reserve_calendar_version_id OR
               NEW.calendar_artifact_id IS DISTINCT FROM
                 context_row.reserve_calendar_artifact_id
             ) OR NEW.coverage_start<>context_row.coverage_start OR
                NEW.coverage_end<>context_row.coverage_end THEN
            RAISE EXCEPTION 'Portfolio Evaluation Data Input is not an exact ordered Context projection';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_portfolio_evaluation_data_input_validate
          BEFORE INSERT ON experiment.v022_portfolio_evaluation_data_input
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_portfolio_evaluation_data_input();

        CREATE FUNCTION experiment.validate_v022_suite_runtime_plan()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; suite_row record; run_row record;
                context_artifact_status varchar; binding_count integer;
                payload_component_count integer; encoding_component_count integer;
                graph_catalog_release_id uuid; catalog_release_status varchar;
        BEGIN
          SELECT artifact_type,artifact_key,version_number,status,
                 semantic_fingerprint
            INTO artifact_row FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          IF artifact_row.artifact_type IS DISTINCT FROM 'v022_suite_runtime_plan' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_suite_runtime_plan__'||NEW.plan_fingerprint OR
             artifact_row.version_number IS DISTINCT FROM 1 OR
             artifact_row.status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'Suite Runtime Plan requires its exact draft Artifact identity';
          END IF;
          IF NEW.requested_range->>'start' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR
             NEW.requested_range->>'end' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR
             NEW.effective_range->>'start' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR
             NEW.effective_range->>'end' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR
             (NEW.requested_range->>'start')::date>
               (NEW.requested_range->>'end')::date OR
             (NEW.effective_range->>'start')::date>
               (NEW.effective_range->>'end')::date OR
             (SELECT count(*) FROM jsonb_object_keys(NEW.requested_range))<>2 OR
             (SELECT count(*) FROM jsonb_object_keys(NEW.effective_range))<>2 OR
             (NEW.effective_range->>'start')::date<
               (NEW.requested_range->>'start')::date OR
             (NEW.effective_range->>'end')::date>
               (NEW.requested_range->>'end')::date THEN
            RAISE EXCEPTION 'Suite Runtime Plan requires exact ordered ISO date ranges';
          END IF;
          SELECT suite.compiled_research_graph_id,suite.artifact_id,
                 suite.branch_count,suite.cell_count,artifact.status AS artifact_status
            INTO suite_row
            FROM experiment.v022_research_suite suite
            JOIN lineage.artifact artifact ON artifact.artifact_id=suite.artifact_id
           WHERE suite.research_suite_id=NEW.research_suite_id;
          IF suite_row.compiled_research_graph_id IS DISTINCT FROM
               NEW.compiled_research_graph_id OR
             suite_row.artifact_status IS DISTINCT FROM 'published' OR
             suite_row.branch_count IS DISTINCT FROM NEW.strategy_target_work_count OR
             suite_row.branch_count IS DISTINCT FROM NEW.sleeve_merge_work_count OR
             suite_row.cell_count IS DISTINCT FROM NEW.portfolio_cell_work_count THEN
            RAISE EXCEPTION 'Suite Runtime Plan does not exactly cover its published Suite';
          END IF;
          SELECT compiled_research_graph_id,status,environment_fingerprint,
                 requested_range
            INTO run_row FROM workspace.v022_graph_run
           WHERE graph_run_id=NEW.graph_run_id;
          IF run_row.compiled_research_graph_id IS DISTINCT FROM
               NEW.compiled_research_graph_id OR
             run_row.status IS DISTINCT FROM 'planning' OR
             run_row.environment_fingerprint IS DISTINCT FROM
               NEW.environment_fingerprint OR
             run_row.requested_range IS DISTINCT FROM NEW.requested_range THEN
            RAISE EXCEPTION 'Suite Runtime Plan requires its exact planning Graph Run';
          END IF;
          SELECT graph.catalog_release_id,release_artifact.status
            INTO graph_catalog_release_id,catalog_release_status
            FROM workspace.compiled_research_graph graph
            JOIN workspace.v022_catalog_release release
              ON release.catalog_release_id=graph.catalog_release_id
            JOIN lineage.artifact release_artifact
              ON release_artifact.artifact_id=release.artifact_id
           WHERE graph.compiled_research_graph_id=NEW.compiled_research_graph_id;
          IF graph_catalog_release_id IS DISTINCT FROM NEW.catalog_release_id OR
             catalog_release_status IS DISTINCT FROM 'published' OR
             NOT experiment.v022_graph_uses_composed_defense(
               NEW.compiled_research_graph_id
             ) THEN
            RAISE EXCEPTION 'Suite Runtime Plan requires its exact composed Graph Catalog Release';
          END IF;
          SELECT count(*) INTO payload_component_count
            FROM (VALUES
              (NEW.strategy_target_payload_contract_version_id,
               'strategy_unit_risk_target'::varchar),
              (NEW.defense_decision_payload_contract_version_id,
               'defense_budget_decision'::varchar),
              (NEW.sleeve_merge_payload_contract_version_id,
               'merged_portfolio_target'::varchar),
              (NEW.portfolio_cell_payload_contract_version_id,
               'portfolio_cell_result'::varchar)
            ) expected(payload_contract_version_id,contract_key)
            JOIN data.payload_contract_version version
              ON version.payload_contract_version_id=
                 expected.payload_contract_version_id
             AND version.version_number=1
            JOIN data.payload_contract_family family
              ON family.payload_contract_family_id=
                 version.payload_contract_family_id
             AND family.contract_key=expected.contract_key
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             AND artifact.status='published'
            JOIN workspace.v022_catalog_release_component component
              ON component.catalog_release_id=NEW.catalog_release_id
             AND component.component_artifact_id=version.artifact_id
             AND component.component_kind='payload_contract_version'
             AND component.component_key=expected.contract_key
             AND component.component_version=1;
          SELECT count(*) INTO encoding_component_count
            FROM data.physical_encoding_version encoding
            JOIN lineage.artifact artifact ON artifact.artifact_id=encoding.artifact_id
             AND artifact.status='published'
            JOIN workspace.v022_catalog_release_component component
              ON component.catalog_release_id=NEW.catalog_release_id
             AND component.component_artifact_id=encoding.artifact_id
             AND component.component_kind='physical_encoding_version'
             AND component.component_key='canonical_parquet'
             AND component.component_version=1
           WHERE encoding.physical_encoding_version_id=
                 NEW.physical_encoding_version_id
             AND encoding.encoding_key='canonical_parquet'
             AND encoding.version_number=1;
          IF payload_component_count<>4 OR encoding_component_count<>1 THEN
            RAISE EXCEPTION 'Suite Runtime Plan payload identities are not exact pinned Catalog components';
          END IF;
          SELECT artifact.status INTO context_artifact_status
            FROM workspace.v022_compiled_execution_data_context context
            JOIN lineage.artifact artifact ON artifact.artifact_id=context.artifact_id
           WHERE context.compiled_execution_data_context_id=
                 NEW.compiled_execution_data_context_id
             AND context.compiled_research_graph_id=NEW.compiled_research_graph_id;
          IF context_artifact_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Suite Runtime Plan requires its exact published Execution Data Context';
          END IF;
          SELECT count(*) INTO binding_count
            FROM experiment.v022_research_suite_graph_run_binding binding
           WHERE binding.research_suite_graph_run_binding_id=
                 NEW.research_suite_graph_run_binding_id
             AND binding.research_suite_id=NEW.research_suite_id
             AND binding.compiled_research_graph_id=NEW.compiled_research_graph_id
             AND binding.graph_run_id=NEW.graph_run_id;
          IF binding_count<>1 THEN
            RAISE EXCEPTION 'Suite Runtime Plan requires its exact Suite-to-Graph-Run binding';
          END IF;
          IF NEW.defense_decision_work_count IS DISTINCT FROM (
            SELECT count(*) FROM experiment.v022_research_suite_branch suite_branch
            JOIN strategy.v022_compiled_strategy_branch branch
              ON branch.compiled_strategy_branch_id=
                 suite_branch.compiled_strategy_branch_id
            WHERE suite_branch.research_suite_id=NEW.research_suite_id
              AND branch.defense_version_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'Suite Runtime Plan Defense count does not match non-null Packages';
          END IF;
          IF EXISTS (
            SELECT 1
              FROM experiment.v022_research_suite_branch suite_branch
              LEFT JOIN experiment.v022_configuration_execution_context_binding binding
                ON binding.configuration_snapshot_id=
                   suite_branch.configuration_snapshot_id
             WHERE suite_branch.research_suite_id=NEW.research_suite_id
               AND binding.configuration_snapshot_id IS NULL
          ) THEN
            RAISE EXCEPTION 'Suite Runtime Plan requires every Branch exact execution-context binding';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_suite_runtime_plan_validate
          BEFORE INSERT ON experiment.v022_suite_runtime_plan
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_suite_runtime_plan();

        CREATE FUNCTION experiment.validate_v022_suite_runtime_work_spec()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE plan_row record; work_row record; suite_branch_row record;
                 branch_row record; context_binding_row record; cell_row record;
                 benchmark_row record;
                 expected_kind varchar; expected_occurrence_key varchar;
                 actual_incoming integer; expected_incoming integer;
                 output_artifact_status varchar;
        BEGIN
          SELECT plan.research_suite_id,plan.compiled_research_graph_id,
                 plan.graph_run_id,plan.compiled_execution_data_context_id,
                 plan.strategy_target_payload_contract_version_id,
                 plan.defense_decision_payload_contract_version_id,
                 plan.sleeve_merge_payload_contract_version_id,
                 plan.portfolio_cell_payload_contract_version_id,
                  plan.physical_encoding_version_id,plan.effective_range,
                 plan.artifact_semantic_fingerprint,
                 artifact.status AS plan_artifact_status
            INTO plan_row
            FROM experiment.v022_suite_runtime_plan plan
            JOIN lineage.artifact artifact ON artifact.artifact_id=plan.artifact_id
           WHERE plan.suite_runtime_plan_id=NEW.suite_runtime_plan_id;
          IF plan_row.plan_artifact_status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'Runtime Work Specs require a draft Plan Artifact';
          END IF;
          expected_kind := CASE TG_TABLE_NAME
            WHEN 'v022_strategy_target_work_spec' THEN 'strategy_target'
            WHEN 'v022_defense_decision_work_spec' THEN 'defense_decision'
            WHEN 'v022_sleeve_merge_work_spec' THEN 'sleeve_merge'
            WHEN 'v022_portfolio_cell_work_spec' THEN 'portfolio_cell'
            ELSE NULL END;
          SELECT item.work_kind,item.execution_fingerprint,item.status,
                 consumer.occurrence_kind,consumer.occurrence_key,
                 consumer.released_at
            INTO work_row
            FROM workspace.v022_graph_work_item item
            JOIN workspace.v022_graph_work_consumer consumer
              ON consumer.graph_work_item_id=item.graph_work_item_id
             AND consumer.graph_run_id=plan_row.graph_run_id
           WHERE item.graph_work_item_id=NEW.graph_work_item_id;
          IF work_row.work_kind IS DISTINCT FROM expected_kind OR
             work_row.occurrence_kind IS DISTINCT FROM expected_kind OR
             work_row.occurrence_key IS DISTINCT FROM NEW.occurrence_key OR
             work_row.execution_fingerprint IS DISTINCT FROM
               NEW.specification_fingerprint OR
             work_row.status NOT IN ('queued','running','completed','reused') OR
             work_row.released_at IS NOT NULL THEN
            RAISE EXCEPTION 'Runtime Work Spec does not match its reusable Graph Work Item';
          END IF;
          IF NEW.specification_document->>'work_execution_fingerprint' IS DISTINCT FROM
               NEW.specification_fingerprint OR
             NEW.specification_document->>'contract_version' IS DISTINCT FROM
               'v0.22.0' OR
             NEW.specification_document->>'work_kind' IS DISTINCT FROM expected_kind OR
             NEW.specification_document->>'occurrence_key' IS DISTINCT FROM
               NEW.occurrence_key OR
             NEW.specification_document->'effective_range' IS DISTINCT FROM
               plan_row.effective_range OR
             NEW.specification_document->>'compiled_strategy_branch_id' IS DISTINCT FROM
               NEW.compiled_strategy_branch_id::text OR
             NEW.specification_document->>'configuration_snapshot_id' IS DISTINCT FROM
               NEW.configuration_snapshot_id::text THEN
            RAISE EXCEPTION 'Runtime Work Spec document does not project its exact execution identity';
          END IF;
          IF work_row.status IN ('completed','reused') THEN
            IF TG_TABLE_NAME='v022_strategy_target_work_spec' THEN
              SELECT artifact.status INTO output_artifact_status
                FROM strategy.v022_strategy_target_path output
                JOIN lineage.artifact artifact ON artifact.artifact_id=output.artifact_id
               WHERE output.graph_work_item_id=NEW.graph_work_item_id;
            ELSIF TG_TABLE_NAME='v022_defense_decision_work_spec' THEN
              SELECT artifact.status INTO output_artifact_status
                FROM defense.v022_defense_decision_path output
                JOIN lineage.artifact artifact ON artifact.artifact_id=output.artifact_id
               WHERE output.graph_work_item_id=NEW.graph_work_item_id;
            ELSIF TG_TABLE_NAME='v022_sleeve_merge_work_spec' THEN
              SELECT artifact.status INTO output_artifact_status
                FROM strategy.v022_merged_portfolio_target_path output
                JOIN lineage.artifact artifact ON artifact.artifact_id=output.artifact_id
               WHERE output.graph_work_item_id=NEW.graph_work_item_id;
            ELSE
              SELECT artifact.status INTO output_artifact_status
                FROM experiment.v022_portfolio_cell_runtime_result output
                JOIN lineage.artifact artifact ON artifact.artifact_id=output.artifact_id
               WHERE output.graph_work_item_id=NEW.graph_work_item_id;
            END IF;
            IF output_artifact_status IS DISTINCT FROM 'published' THEN
              RAISE EXCEPTION 'Reusable Runtime Work requires its published typed output';
            END IF;
          END IF;
          IF NEW.plan_artifact_semantic_fingerprint IS DISTINCT FROM
               plan_row.artifact_semantic_fingerprint THEN
            RAISE EXCEPTION 'Runtime Work Spec semantic projection does not match its Plan Artifact';
          END IF;
          IF TG_TABLE_NAME='v022_strategy_target_work_spec' THEN
            IF EXISTS (
              SELECT 1 FROM strategy.v022_strategy_target_work_spec prior
               WHERE prior.graph_work_item_id=NEW.graph_work_item_id
                 AND (
                   prior.compiled_strategy_branch_id IS DISTINCT FROM NEW.compiled_strategy_branch_id OR
                   prior.configuration_snapshot_id IS DISTINCT FROM NEW.configuration_snapshot_id OR
                   prior.compiled_execution_data_context_id IS DISTINCT FROM NEW.compiled_execution_data_context_id OR
                   prior.output_payload_contract_version_id IS DISTINCT FROM NEW.output_payload_contract_version_id OR
                   prior.physical_encoding_version_id IS DISTINCT FROM NEW.physical_encoding_version_id OR
                   prior.source_aggregation_work_item_id IS DISTINCT FROM NEW.source_aggregation_work_item_id OR
                   prior.occurrence_key IS DISTINCT FROM NEW.occurrence_key OR
                   prior.specification_document IS DISTINCT FROM NEW.specification_document OR
                   prior.specification_fingerprint IS DISTINCT FROM NEW.specification_fingerprint
                 )
            ) THEN RAISE EXCEPTION 'Runtime Work Spec identity drifted across Plans'; END IF;
          ELSIF TG_TABLE_NAME='v022_defense_decision_work_spec' THEN
            IF EXISTS (
              SELECT 1 FROM defense.v022_defense_decision_work_spec prior
               WHERE prior.graph_work_item_id=NEW.graph_work_item_id
                 AND (
                   prior.compiled_strategy_branch_id IS DISTINCT FROM NEW.compiled_strategy_branch_id OR
                   prior.configuration_snapshot_id IS DISTINCT FROM NEW.configuration_snapshot_id OR
                   prior.defense_version_id IS DISTINCT FROM NEW.defense_version_id OR
                   prior.timing_policy_version_id IS DISTINCT FROM NEW.timing_policy_version_id OR
                   prior.allocation_policy_version_id IS DISTINCT FROM NEW.allocation_policy_version_id OR
                   prior.compiled_defense_execution_context_id IS DISTINCT FROM NEW.compiled_defense_execution_context_id OR
                   prior.output_payload_contract_version_id IS DISTINCT FROM NEW.output_payload_contract_version_id OR
                   prior.physical_encoding_version_id IS DISTINCT FROM NEW.physical_encoding_version_id OR
                   prior.source_strategy_work_item_id IS DISTINCT FROM NEW.source_strategy_work_item_id OR
                   prior.occurrence_key IS DISTINCT FROM NEW.occurrence_key OR
                   prior.specification_document IS DISTINCT FROM NEW.specification_document OR
                   prior.specification_fingerprint IS DISTINCT FROM NEW.specification_fingerprint
                 )
            ) THEN RAISE EXCEPTION 'Runtime Work Spec identity drifted across Plans'; END IF;
          ELSIF TG_TABLE_NAME='v022_sleeve_merge_work_spec' THEN
            IF EXISTS (
              SELECT 1 FROM strategy.v022_sleeve_merge_work_spec prior
               WHERE prior.graph_work_item_id=NEW.graph_work_item_id
                 AND (
                   prior.compiled_strategy_branch_id IS DISTINCT FROM NEW.compiled_strategy_branch_id OR
                   prior.configuration_snapshot_id IS DISTINCT FROM NEW.configuration_snapshot_id OR
                   prior.output_payload_contract_version_id IS DISTINCT FROM NEW.output_payload_contract_version_id OR
                   prior.physical_encoding_version_id IS DISTINCT FROM NEW.physical_encoding_version_id OR
                   prior.source_strategy_work_item_id IS DISTINCT FROM NEW.source_strategy_work_item_id OR
                   prior.source_defense_work_item_id IS DISTINCT FROM NEW.source_defense_work_item_id OR
                   prior.occurrence_key IS DISTINCT FROM NEW.occurrence_key OR
                   prior.specification_document IS DISTINCT FROM NEW.specification_document OR
                   prior.specification_fingerprint IS DISTINCT FROM NEW.specification_fingerprint
                 )
            ) THEN RAISE EXCEPTION 'Runtime Work Spec identity drifted across Plans'; END IF;
          ELSE
            IF EXISTS (
              SELECT 1 FROM experiment.v022_portfolio_cell_work_spec prior
               WHERE prior.graph_work_item_id=NEW.graph_work_item_id
                 AND (
                   prior.compiled_strategy_branch_id IS DISTINCT FROM NEW.compiled_strategy_branch_id OR
                   prior.configuration_snapshot_id IS DISTINCT FROM NEW.configuration_snapshot_id OR
                   prior.output_payload_contract_version_id IS DISTINCT FROM NEW.output_payload_contract_version_id OR
                   prior.physical_encoding_version_id IS DISTINCT FROM NEW.physical_encoding_version_id OR
                   prior.portfolio_evaluation_data_context_id IS DISTINCT FROM NEW.portfolio_evaluation_data_context_id OR
                   prior.source_merge_work_item_id IS DISTINCT FROM NEW.source_merge_work_item_id OR
                   prior.occurrence_key IS DISTINCT FROM NEW.occurrence_key OR
                   prior.specification_document IS DISTINCT FROM NEW.specification_document OR
                   prior.specification_fingerprint IS DISTINCT FROM NEW.specification_fingerprint
                 )
            ) THEN RAISE EXCEPTION 'Runtime Work Spec identity drifted across Plans'; END IF;
          END IF;
          SELECT suite_branch.research_suite_id,
                 suite_branch.compiled_research_graph_id,
                 suite_branch.compiled_strategy_branch_id,
                 suite_branch.configuration_snapshot_id
            INTO suite_branch_row
            FROM experiment.v022_research_suite_branch suite_branch
           WHERE suite_branch.research_suite_branch_id=
                 NEW.research_suite_branch_id;
          IF suite_branch_row.research_suite_id IS DISTINCT FROM
               plan_row.research_suite_id OR
             suite_branch_row.compiled_research_graph_id IS DISTINCT FROM
               plan_row.compiled_research_graph_id OR
             suite_branch_row.compiled_strategy_branch_id IS DISTINCT FROM
               NEW.compiled_strategy_branch_id OR
             suite_branch_row.configuration_snapshot_id IS DISTINCT FROM
               NEW.configuration_snapshot_id THEN
            RAISE EXCEPTION 'Runtime Work Spec does not match its exact Suite Branch';
          END IF;
          SELECT branch.compiled_aggregation_instance_id,
                 branch.defense_version_id
            INTO branch_row
            FROM strategy.v022_compiled_strategy_branch branch
           WHERE branch.compiled_strategy_branch_id=
                 NEW.compiled_strategy_branch_id;
          SELECT binding.compiled_execution_data_context_id,
                 binding.defense_version_id,
                 binding.timing_policy_version_id,
                 binding.allocation_policy_version_id,
                 binding.compiled_defense_execution_context_id
            INTO context_binding_row
            FROM experiment.v022_configuration_execution_context_binding binding
           WHERE binding.configuration_snapshot_id=NEW.configuration_snapshot_id;
          IF context_binding_row.compiled_execution_data_context_id IS DISTINCT FROM
               plan_row.compiled_execution_data_context_id THEN
            RAISE EXCEPTION 'Runtime Work Spec Risk Context drifted';
          END IF;

          IF TG_TABLE_NAME='v022_strategy_target_work_spec' THEN
            SELECT occurrence_key INTO expected_occurrence_key
              FROM workspace.v022_graph_work_consumer
             WHERE graph_run_id=plan_row.graph_run_id
               AND graph_work_item_id=NEW.source_aggregation_work_item_id
               AND occurrence_kind='aggregation' AND released_at IS NULL;
             IF expected_occurrence_key IS DISTINCT FROM
                  'aggregation:'||branch_row.compiled_aggregation_instance_id::text OR
                NEW.compiled_execution_data_context_id IS DISTINCT FROM
                  plan_row.compiled_execution_data_context_id OR
                NEW.output_payload_contract_version_id IS DISTINCT FROM
                  plan_row.strategy_target_payload_contract_version_id OR
                NEW.physical_encoding_version_id IS DISTINCT FROM
                  plan_row.physical_encoding_version_id THEN
              RAISE EXCEPTION 'Strategy Work requires its exact Aggregation and Risk Context';
            END IF;
            IF NEW.specification_document->>'compiled_execution_data_context_id'
                 IS DISTINCT FROM NEW.compiled_execution_data_context_id::text OR
               NEW.specification_document->>'source_aggregation_work_item_id'
                 IS DISTINCT FROM NEW.source_aggregation_work_item_id::text THEN
              RAISE EXCEPTION 'Strategy Work document does not project its exact sources';
            END IF;
            expected_incoming := 1;
          ELSIF TG_TABLE_NAME='v022_defense_decision_work_spec' THEN
            IF branch_row.defense_version_id IS NULL OR
               NEW.defense_version_id IS DISTINCT FROM branch_row.defense_version_id OR
               context_binding_row.defense_version_id IS DISTINCT FROM
                 NEW.defense_version_id OR
               context_binding_row.timing_policy_version_id IS DISTINCT FROM
                 NEW.timing_policy_version_id OR
               context_binding_row.allocation_policy_version_id IS DISTINCT FROM
                 NEW.allocation_policy_version_id OR
                context_binding_row.compiled_defense_execution_context_id IS DISTINCT FROM
                  NEW.compiled_defense_execution_context_id OR
                NEW.output_payload_contract_version_id IS DISTINCT FROM
                  plan_row.defense_decision_payload_contract_version_id OR
                NEW.physical_encoding_version_id IS DISTINCT FROM
                  plan_row.physical_encoding_version_id THEN
              RAISE EXCEPTION 'Defense Work requires its exact non-null Package Context';
            END IF;
            IF NEW.specification_document->>'defense_version_id' IS DISTINCT FROM
                 NEW.defense_version_id::text OR
               NEW.specification_document->>'timing_policy_version_id' IS DISTINCT FROM
                 NEW.timing_policy_version_id::text OR
               NEW.specification_document->>'allocation_policy_version_id' IS DISTINCT FROM
                 NEW.allocation_policy_version_id::text OR
               NEW.specification_document->>'compiled_defense_execution_context_id'
                 IS DISTINCT FROM NEW.compiled_defense_execution_context_id::text OR
               NEW.specification_document->>'source_strategy_work_item_id'
                 IS DISTINCT FROM NEW.source_strategy_work_item_id::text THEN
              RAISE EXCEPTION 'Defense Work document does not project its exact sources';
            END IF;
            expected_incoming := 1;
          ELSIF TG_TABLE_NAME='v022_sleeve_merge_work_spec' THEN
             IF (branch_row.defense_version_id IS NULL) IS DISTINCT FROM
                  (NEW.source_defense_work_item_id IS NULL) OR
                NEW.output_payload_contract_version_id IS DISTINCT FROM
                  plan_row.sleeve_merge_payload_contract_version_id OR
                NEW.physical_encoding_version_id IS DISTINCT FROM
                  plan_row.physical_encoding_version_id THEN
              RAISE EXCEPTION 'Sleeve Merge Defense source must follow nullable Package identity';
            END IF;
            IF NEW.specification_document->>'source_strategy_work_item_id'
                 IS DISTINCT FROM NEW.source_strategy_work_item_id::text OR
               NEW.specification_document->>'source_defense_work_item_id'
                 IS DISTINCT FROM (CASE
                   WHEN NEW.source_defense_work_item_id IS NULL THEN NULL
                   ELSE NEW.source_defense_work_item_id::text END) THEN
              RAISE EXCEPTION 'Sleeve Merge Work document does not project its exact sources';
            END IF;
            expected_incoming := CASE
              WHEN NEW.source_defense_work_item_id IS NULL THEN 1 ELSE 2 END;
          ELSE
          SELECT cell.research_suite_id,cell.research_suite_branch_id,
                    cell.compiled_strategy_branch_id,
                    cell.configuration_snapshot_id,
                    cell.evaluation_matrix_policy_id,
                    cell.evaluation_context_ordinal,
                    cell.evaluation_context_fingerprint
              INTO cell_row FROM experiment.v022_research_cell cell
             WHERE cell.research_cell_id=NEW.research_cell_id;
            IF cell_row.research_suite_id IS DISTINCT FROM
                 plan_row.research_suite_id OR
               cell_row.research_suite_branch_id IS DISTINCT FROM
                 NEW.research_suite_branch_id OR
               cell_row.compiled_strategy_branch_id IS DISTINCT FROM
                 NEW.compiled_strategy_branch_id OR
                cell_row.configuration_snapshot_id IS DISTINCT FROM
                  NEW.configuration_snapshot_id OR
                NEW.output_payload_contract_version_id IS DISTINCT FROM
                  plan_row.portfolio_cell_payload_contract_version_id OR
                NEW.physical_encoding_version_id IS DISTINCT FROM
                  plan_row.physical_encoding_version_id THEN
              RAISE EXCEPTION 'Portfolio Cell Work does not match its exact Research Cell';
            END IF;
            SELECT artifact.status AS artifact_status,
                   context.coverage_start,context.coverage_end
              INTO benchmark_row
              FROM experiment.v022_portfolio_evaluation_data_context context
              JOIN lineage.artifact artifact ON artifact.artifact_id=context.artifact_id
             WHERE context.portfolio_evaluation_data_context_id=
                   NEW.portfolio_evaluation_data_context_id;
            IF benchmark_row.artifact_status IS DISTINCT FROM 'published' OR
               benchmark_row.coverage_start>
                 (plan_row.effective_range->>'start')::date OR
               benchmark_row.coverage_end<
                 (plan_row.effective_range->>'end')::date OR
               NOT EXISTS (
                 SELECT 1
                   FROM experiment.v022_portfolio_evaluation_data_context context
                  WHERE context.portfolio_evaluation_data_context_id=
                        NEW.portfolio_evaluation_data_context_id
                    AND context.evaluation_matrix_policy_id=
                        cell_row.evaluation_matrix_policy_id
                     AND context.evaluation_context_ordinal=
                         cell_row.evaluation_context_ordinal
               ) OR NOT EXISTS (
                 SELECT 1
                   FROM experiment.v022_research_cell_evaluation_data_context_binding
                        binding
                  WHERE binding.research_cell_id=NEW.research_cell_id
                    AND binding.portfolio_evaluation_data_context_id=
                        NEW.portfolio_evaluation_data_context_id
               ) THEN
              RAISE EXCEPTION 'Portfolio Cell Work requires its exact published Evaluation Data Context';
            END IF;
            IF NEW.specification_document->>'source_merge_work_item_id'
                 IS DISTINCT FROM NEW.source_merge_work_item_id::text OR
               NEW.specification_document->>'portfolio_evaluation_data_context_id'
                 IS DISTINCT FROM NEW.portfolio_evaluation_data_context_id::text OR
               NEW.specification_document->>'evaluation_policy_context_fingerprint'
                 IS DISTINCT FROM cell_row.evaluation_context_fingerprint OR
               NEW.specification_document->>'evaluation_data_context_fingerprint'
                 IS DISTINCT FROM (
                   SELECT context.context_fingerprint
                     FROM experiment.v022_portfolio_evaluation_data_context context
                    WHERE context.portfolio_evaluation_data_context_id=
                          NEW.portfolio_evaluation_data_context_id
                 ) OR
               NEW.specification_document->>'evaluation_context_ordinal'
                 IS DISTINCT FROM cell_row.evaluation_context_ordinal::text THEN
              RAISE EXCEPTION 'Portfolio Cell Work document does not project its global inputs';
            END IF;
            expected_incoming := 1;
          END IF;

          SELECT count(*) INTO actual_incoming
            FROM workspace.v022_graph_work_dependency dependency
           WHERE dependency.downstream_work_item_id=NEW.graph_work_item_id;
          IF actual_incoming<>expected_incoming THEN
            RAISE EXCEPTION 'Runtime Work has an incomplete required dependency set';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_strategy_target_work_spec_validate
          BEFORE INSERT ON strategy.v022_strategy_target_work_spec
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_suite_runtime_work_spec();
        CREATE TRIGGER trg_v022_defense_decision_work_spec_validate
          BEFORE INSERT ON defense.v022_defense_decision_work_spec
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_suite_runtime_work_spec();
        CREATE TRIGGER trg_v022_sleeve_merge_work_spec_validate
          BEFORE INSERT ON strategy.v022_sleeve_merge_work_spec
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_suite_runtime_work_spec();
        CREATE TRIGGER trg_v022_portfolio_cell_work_spec_validate
          BEFORE INSERT ON experiment.v022_portfolio_cell_work_spec
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_suite_runtime_work_spec();

        CREATE FUNCTION experiment.validate_v022_suite_runtime_plan_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE plan_status varchar; strategy_count integer; defense_count integer;
                merge_count integer; cell_count integer; consumer_count integer;
                plan_artifact_fingerprint varchar; evaluation_context_count integer;
                matched_evaluation_context_count integer;
        BEGIN
          SELECT status,semantic_fingerprint
            INTO plan_status,plan_artifact_fingerprint FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          SELECT count(*) INTO strategy_count
            FROM strategy.v022_strategy_target_work_spec
           WHERE suite_runtime_plan_id=NEW.suite_runtime_plan_id;
          SELECT count(*) INTO defense_count
            FROM defense.v022_defense_decision_work_spec
           WHERE suite_runtime_plan_id=NEW.suite_runtime_plan_id;
          SELECT count(*) INTO merge_count
            FROM strategy.v022_sleeve_merge_work_spec
           WHERE suite_runtime_plan_id=NEW.suite_runtime_plan_id;
          SELECT count(*) INTO cell_count
            FROM experiment.v022_portfolio_cell_work_spec
           WHERE suite_runtime_plan_id=NEW.suite_runtime_plan_id;
          SELECT count(*) INTO consumer_count
            FROM workspace.v022_graph_work_consumer consumer
           WHERE consumer.graph_run_id=NEW.graph_run_id
             AND consumer.occurrence_kind IN (
               'strategy_target','defense_decision','sleeve_merge','portfolio_cell'
              ) AND consumer.released_at IS NULL;
          SELECT count(*) INTO evaluation_context_count FROM (
            SELECT DISTINCT work.portfolio_evaluation_data_context_id
              FROM experiment.v022_portfolio_cell_work_spec work
             WHERE work.suite_runtime_plan_id=NEW.suite_runtime_plan_id
          ) expected_context;
          IF plan_status IS DISTINCT FROM 'published' OR
             plan_artifact_fingerprint IS DISTINCT FROM
               NEW.artifact_semantic_fingerprint OR
             strategy_count<>NEW.strategy_target_work_count OR
             defense_count<>NEW.defense_decision_work_count OR
             merge_count<>NEW.sleeve_merge_work_count OR
             cell_count<>NEW.portfolio_cell_work_count OR
             consumer_count<>NEW.total_work_count THEN
            RAISE EXCEPTION 'Suite Runtime Plan is not a complete published typed DAG';
          END IF;
          IF (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>
                 3+evaluation_context_count OR
             NOT EXISTS (
               SELECT 1
                 FROM lineage.artifact_dependency dependency
                 JOIN experiment.v022_research_suite suite
                   ON suite.artifact_id=dependency.depends_on_artifact_id
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.role='research_suite'
                  AND dependency.ordinal=0
                  AND suite.research_suite_id=NEW.research_suite_id
             ) OR NOT EXISTS (
               SELECT 1
                 FROM lineage.artifact_dependency dependency
                 JOIN workspace.v022_compiled_execution_data_context context
                   ON context.artifact_id=dependency.depends_on_artifact_id
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.role='execution_data_context'
                  AND dependency.ordinal=1
                  AND context.compiled_execution_data_context_id=
                      NEW.compiled_execution_data_context_id
             ) OR NOT EXISTS (
               SELECT 1
                 FROM lineage.artifact_dependency dependency
                 JOIN workspace.v022_catalog_release release
                   ON release.artifact_id=dependency.depends_on_artifact_id
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.role='catalog_release'
                  AND dependency.ordinal=2
                  AND release.catalog_release_id=NEW.catalog_release_id
              ) THEN
            RAISE EXCEPTION 'Suite Runtime Plan has incomplete or extra exact lineage';
          END IF;
          SELECT count(*) INTO matched_evaluation_context_count
            FROM (
              SELECT context.artifact_id,
                     row_number() OVER (
                       ORDER BY context.evaluation_context_ordinal,
                                context.evaluation_matrix_policy_id,
                                context.portfolio_evaluation_data_context_id
                     )-1 AS dependency_ordinal
                FROM experiment.v022_portfolio_cell_work_spec work
                JOIN experiment.v022_portfolio_evaluation_data_context context
                  ON context.portfolio_evaluation_data_context_id=
                     work.portfolio_evaluation_data_context_id
               WHERE work.suite_runtime_plan_id=NEW.suite_runtime_plan_id
               GROUP BY context.artifact_id,context.evaluation_context_ordinal,
                        context.evaluation_matrix_policy_id,
                        context.portfolio_evaluation_data_context_id
            ) expected
            JOIN lineage.artifact_dependency dependency
              ON dependency.artifact_id=NEW.artifact_id
             AND dependency.depends_on_artifact_id=expected.artifact_id
             AND dependency.role='portfolio_evaluation_data_context'
             AND dependency.ordinal=expected.dependency_ordinal;
          IF matched_evaluation_context_count<>evaluation_context_count THEN
            RAISE EXCEPTION 'Suite Runtime Plan is missing its exact ordered Evaluation Data Context lineage';
          END IF;
          IF EXISTS (
            SELECT 1 FROM strategy.v022_strategy_target_work_spec work
             WHERE work.suite_runtime_plan_id=NEW.suite_runtime_plan_id
               AND NOT EXISTS (
                 SELECT 1 FROM workspace.v022_graph_work_dependency dependency
                  WHERE dependency.upstream_work_item_id=
                        work.source_aggregation_work_item_id
                    AND dependency.downstream_work_item_id=work.graph_work_item_id
                    AND dependency.dependency_kind='required'
               )
          ) OR EXISTS (
            SELECT 1 FROM defense.v022_defense_decision_work_spec work
             WHERE work.suite_runtime_plan_id=NEW.suite_runtime_plan_id
               AND NOT EXISTS (
                 SELECT 1 FROM workspace.v022_graph_work_dependency dependency
                  WHERE dependency.upstream_work_item_id=
                        work.source_strategy_work_item_id
                    AND dependency.downstream_work_item_id=work.graph_work_item_id
                    AND dependency.dependency_kind='required'
               )
          ) OR EXISTS (
            SELECT 1 FROM strategy.v022_sleeve_merge_work_spec work
             WHERE work.suite_runtime_plan_id=NEW.suite_runtime_plan_id
               AND (
                 NOT EXISTS (
                   SELECT 1 FROM workspace.v022_graph_work_dependency dependency
                    WHERE dependency.upstream_work_item_id=
                          work.source_strategy_work_item_id
                      AND dependency.downstream_work_item_id=work.graph_work_item_id
                      AND dependency.dependency_kind='required'
                 ) OR (
                   work.source_defense_work_item_id IS NOT NULL AND NOT EXISTS (
                     SELECT 1 FROM workspace.v022_graph_work_dependency dependency
                      WHERE dependency.upstream_work_item_id=
                            work.source_defense_work_item_id
                        AND dependency.downstream_work_item_id=work.graph_work_item_id
                        AND dependency.dependency_kind='required'
                   )
                 )
               )
          ) OR EXISTS (
            SELECT 1 FROM experiment.v022_portfolio_cell_work_spec work
             WHERE work.suite_runtime_plan_id=NEW.suite_runtime_plan_id
               AND NOT EXISTS (
                 SELECT 1 FROM workspace.v022_graph_work_dependency dependency
                  WHERE dependency.upstream_work_item_id=work.source_merge_work_item_id
                    AND dependency.downstream_work_item_id=work.graph_work_item_id
                    AND dependency.dependency_kind='required'
               )
          ) THEN
            RAISE EXCEPTION 'Suite Runtime Plan has incomplete typed dependency edges';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_suite_runtime_plan_complete
          AFTER INSERT ON experiment.v022_suite_runtime_plan
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_suite_runtime_plan_complete();
        """
    )


def _create_output_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION experiment.validate_v022_typed_runtime_output()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE work_row record; artifact_row record; manifest_row record; expected_kind varchar;
                expected_type varchar; expected_fingerprint varchar;
                expected_contract_id uuid; expected_encoding_id uuid;
                expected_output_port varchar; expected_branch_id uuid;
                expected_snapshot_id uuid; expected_evaluation_fingerprint varchar;
                expected_effective_range jsonb;
        BEGIN
          expected_kind := CASE TG_TABLE_NAME
            WHEN 'v022_strategy_target_path' THEN 'strategy_target'
            WHEN 'v022_defense_decision_path' THEN 'defense_decision'
            WHEN 'v022_merged_portfolio_target_path' THEN 'sleeve_merge'
            WHEN 'v022_portfolio_cell_runtime_result' THEN 'portfolio_cell'
            ELSE NULL END;
          expected_type := CASE expected_kind
            WHEN 'strategy_target' THEN 'v022_strategy_unit_risk_target_path'
            WHEN 'defense_decision' THEN 'v022_defense_decision_path'
            WHEN 'sleeve_merge' THEN 'v022_merged_portfolio_target_path'
            WHEN 'portfolio_cell' THEN 'v022_portfolio_cell_runtime_result'
            ELSE NULL END;
          expected_fingerprint := coalesce(
            to_jsonb(NEW)->>'result_fingerprint',
            to_jsonb(NEW)->>'output_fingerprint'
          );
          expected_output_port := CASE expected_kind
            WHEN 'strategy_target' THEN 'strategy_unit_risk_target'
            WHEN 'defense_decision' THEN 'defense_budget_decision'
            WHEN 'sleeve_merge' THEN 'merged_portfolio_target'
            WHEN 'portfolio_cell' THEN 'portfolio_cell_result'
            ELSE NULL END;
          SELECT work_kind,execution_fingerprint,status,lease_owner,lease_expires_at,
                 cancel_requested_at,fencing_token
            INTO work_row FROM workspace.v022_graph_work_item
           WHERE graph_work_item_id=NEW.graph_work_item_id;
          IF work_row.work_kind IS DISTINCT FROM expected_kind OR
             work_row.execution_fingerprint IS DISTINCT FROM
               NEW.work_execution_fingerprint OR
             work_row.status IS DISTINCT FROM 'running' OR
             work_row.lease_owner IS DISTINCT FROM NEW.worker_key OR
             work_row.fencing_token IS DISTINCT FROM NEW.fencing_token OR
             work_row.lease_expires_at < now() OR
             work_row.cancel_requested_at IS NOT NULL THEN
            RAISE EXCEPTION 'Typed Runtime output requires the active fenced lease';
          END IF;
          IF expected_kind='strategy_target' THEN
            SELECT output_payload_contract_version_id,physical_encoding_version_id
              INTO expected_contract_id,expected_encoding_id
              FROM strategy.v022_strategy_target_work_spec
             WHERE graph_work_item_id=NEW.graph_work_item_id LIMIT 1;
          ELSIF expected_kind='defense_decision' THEN
            SELECT output_payload_contract_version_id,physical_encoding_version_id
              INTO expected_contract_id,expected_encoding_id
              FROM defense.v022_defense_decision_work_spec
             WHERE graph_work_item_id=NEW.graph_work_item_id LIMIT 1;
          ELSIF expected_kind='sleeve_merge' THEN
            SELECT output_payload_contract_version_id,physical_encoding_version_id
              INTO expected_contract_id,expected_encoding_id
              FROM strategy.v022_sleeve_merge_work_spec
             WHERE graph_work_item_id=NEW.graph_work_item_id LIMIT 1;
          ELSE
            SELECT spec.output_payload_contract_version_id,
                   spec.physical_encoding_version_id,
                   spec.compiled_strategy_branch_id,
                   spec.configuration_snapshot_id,
                   context.context_fingerprint,
                   spec.specification_document->'effective_range'
              INTO expected_contract_id,expected_encoding_id,expected_branch_id,
                   expected_snapshot_id,expected_evaluation_fingerprint,
                   expected_effective_range
              FROM experiment.v022_portfolio_cell_work_spec spec
              JOIN experiment.v022_portfolio_evaluation_data_context context
                ON context.portfolio_evaluation_data_context_id=
                   spec.portfolio_evaluation_data_context_id
             WHERE spec.graph_work_item_id=NEW.graph_work_item_id LIMIT 1;
          END IF;
          IF expected_contract_id IS NULL OR expected_encoding_id IS NULL THEN
            RAISE EXCEPTION 'Typed Runtime output requires a corresponding Work Spec';
          END IF;
          IF expected_kind='portfolio_cell' AND (
               (to_jsonb(NEW)->>'compiled_strategy_branch_id')::uuid
                 IS DISTINCT FROM expected_branch_id OR
               (to_jsonb(NEW)->>'configuration_snapshot_id')::uuid
                 IS DISTINCT FROM expected_snapshot_id OR
               to_jsonb(NEW)->>'evaluation_data_context_fingerprint' IS DISTINCT FROM
                 expected_evaluation_fingerprint
             ) THEN
            RAISE EXCEPTION 'Portfolio Cell output is not its global execution identity projection';
          END IF;
          IF expected_kind='portfolio_cell' AND (
               (to_jsonb(NEW)->>'effective_start')::date IS DISTINCT FROM
                 (expected_effective_range->>'start')::date OR
               (to_jsonb(NEW)->>'effective_end')::date IS DISTINCT FROM
                 (expected_effective_range->>'end')::date
             ) THEN
            RAISE EXCEPTION 'Portfolio Cell output range does not match its exact accepted Work range';
          END IF;
          IF expected_kind='portfolio_cell' AND EXISTS (
            SELECT 1
              FROM experiment.v022_portfolio_cell_work_spec spec
              JOIN experiment.v022_portfolio_evaluation_data_context context
                ON context.portfolio_evaluation_data_context_id=
                   spec.portfolio_evaluation_data_context_id
             WHERE spec.graph_work_item_id=NEW.graph_work_item_id
               AND (
                 spec.compiled_strategy_branch_id IS DISTINCT FROM
                   (to_jsonb(NEW)->>'compiled_strategy_branch_id')::uuid OR
                 spec.configuration_snapshot_id IS DISTINCT FROM
                   (to_jsonb(NEW)->>'configuration_snapshot_id')::uuid OR
                 context.context_fingerprint IS DISTINCT FROM
                   to_jsonb(NEW)->>'evaluation_data_context_fingerprint'
               )
          ) THEN
            RAISE EXCEPTION 'Portfolio Cell output identity drifted across Plan bindings';
          END IF;
          SELECT artifact_type,artifact_key,version_number,status,
                 semantic_fingerprint
            INTO artifact_row FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          IF artifact_row.artifact_type IS DISTINCT FROM expected_type OR
             artifact_row.artifact_key IS DISTINCT FROM
               expected_type||'__'||expected_fingerprint OR
             artifact_row.version_number IS DISTINCT FROM 1 OR
             artifact_row.status IS DISTINCT FROM 'published' OR
             artifact_row.semantic_fingerprint IS DISTINCT FROM
               NEW.artifact_semantic_fingerprint THEN
            RAISE EXCEPTION 'Typed Runtime output requires its exact published Artifact identity';
          END IF;
          SELECT manifest.artifact_id,manifest.payload_contract_version_id,
                 manifest.physical_encoding_version_id,
                 manifest.producer_artifact_id,manifest.producer_output_port_key,
                 manifest.logical_payload_fingerprint,manifest.manifest_hash,
                 manifest.materialization_state,producer.status AS producer_status,
                 manifest_artifact.artifact_type AS manifest_artifact_type,
                 manifest_artifact.artifact_key AS manifest_artifact_key,
                 manifest_artifact.version_number AS manifest_artifact_version,
                 manifest_artifact.status AS manifest_artifact_status,
                 manifest_artifact.semantic_fingerprint AS
                   manifest_artifact_semantic_fingerprint
            INTO manifest_row
            FROM data.payload_manifest manifest
            JOIN lineage.artifact producer
              ON producer.artifact_id=manifest.producer_artifact_id
            JOIN lineage.artifact manifest_artifact
              ON manifest_artifact.artifact_id=manifest.artifact_id
           WHERE manifest.payload_manifest_id=NEW.payload_manifest_id;
          IF manifest_row.artifact_id IS DISTINCT FROM
               NEW.payload_manifest_artifact_id OR
             manifest_row.producer_artifact_id IS DISTINCT FROM NEW.artifact_id OR
             manifest_row.producer_status IS DISTINCT FROM 'published' OR
              manifest_row.manifest_artifact_type IS DISTINCT FROM
                'v022_payload_manifest' OR
             manifest_row.manifest_artifact_key IS DISTINCT FROM
               'v022_payload_manifest__'||NEW.manifest_hash OR
             manifest_row.manifest_artifact_version IS DISTINCT FROM 1 OR
              manifest_row.manifest_artifact_status IS DISTINCT FROM 'published' OR
             manifest_row.manifest_artifact_semantic_fingerprint IS NULL OR
             manifest_row.payload_contract_version_id IS DISTINCT FROM
               expected_contract_id OR
             manifest_row.physical_encoding_version_id IS DISTINCT FROM
               expected_encoding_id OR
             manifest_row.producer_output_port_key IS DISTINCT FROM
               expected_output_port OR
             manifest_row.logical_payload_fingerprint IS DISTINCT FROM
               NEW.logical_payload_fingerprint OR
             manifest_row.manifest_hash IS DISTINCT FROM NEW.manifest_hash OR
             manifest_row.materialization_state IS DISTINCT FROM 'materialized' THEN
            RAISE EXCEPTION 'Typed Runtime output requires its exact materialized Payload Manifest';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_strategy_target_path_validate
          BEFORE INSERT ON strategy.v022_strategy_target_path
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_typed_runtime_output();
        CREATE TRIGGER trg_v022_defense_decision_path_validate
          BEFORE INSERT ON defense.v022_defense_decision_path
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_typed_runtime_output();
        CREATE TRIGGER trg_v022_merged_portfolio_target_path_validate
          BEFORE INSERT ON strategy.v022_merged_portfolio_target_path
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_typed_runtime_output();
        CREATE TRIGGER trg_v022_portfolio_cell_runtime_result_validate
          BEFORE INSERT ON experiment.v022_portfolio_cell_runtime_result
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_typed_runtime_output();

        CREATE FUNCTION experiment.validate_v022_typed_runtime_output_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_kind varchar;
                output_artifact_status varchar; output_artifact_fingerprint varchar;
                expected_artifact_ids uuid[]; expected_roles text[];
                expected_dependency_count integer; actual_dependency_count integer;
                matched_dependency_count integer; final_work_status varchar;
                manifest_row record; actual_partition_count integer;
                actual_partition_bytes bigint; actual_partition_items bigint;
                bad_partition_count integer;
        BEGIN
          expected_kind := CASE TG_TABLE_NAME
            WHEN 'v022_strategy_target_path' THEN 'strategy_target'
            WHEN 'v022_defense_decision_path' THEN 'defense_decision'
            WHEN 'v022_merged_portfolio_target_path' THEN 'sleeve_merge'
            WHEN 'v022_portfolio_cell_runtime_result' THEN 'portfolio_cell'
            ELSE NULL END;
          SELECT status,semantic_fingerprint
            INTO output_artifact_status,output_artifact_fingerprint
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF output_artifact_status IS DISTINCT FROM 'published' OR
             output_artifact_fingerprint IS DISTINCT FROM
               NEW.artifact_semantic_fingerprint THEN
            RAISE EXCEPTION 'Typed Runtime output Artifact is not exactly published';
          END IF;
          SELECT status INTO final_work_status
            FROM workspace.v022_graph_work_item
           WHERE graph_work_item_id=NEW.graph_work_item_id;
          IF final_work_status IS DISTINCT FROM 'completed' THEN
            RAISE EXCEPTION 'Typed Runtime output and Work completion must commit atomically';
          END IF;

          SELECT manifest.manifest_hash,manifest.partition_count,
                 manifest.byte_size,manifest.row_or_item_count,
                 artifact.semantic_fingerprint
            INTO manifest_row
            FROM data.payload_manifest manifest
            JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
           WHERE manifest.payload_manifest_id=NEW.payload_manifest_id;
          SELECT count(*),coalesce(sum(partition.byte_size),0),
                 coalesce(sum(partition.row_or_item_count),0),
                 count(*) FILTER (
                   WHERE link.ordinal<0 OR object.object_state<>'published' OR
                         object.verification_status<>'verified' OR
                         object.verified_at IS NULL OR
                         partition.byte_size<>object.byte_size
                 )
            INTO actual_partition_count,actual_partition_bytes,
                 actual_partition_items,bad_partition_count
            FROM data.payload_manifest_partition link
            JOIN data.payload_partition partition
              ON partition.payload_partition_id=link.payload_partition_id
            JOIN data.payload_object object
              ON object.payload_object_id=partition.payload_object_id
           WHERE link.payload_manifest_id=NEW.payload_manifest_id;
          IF manifest_row.manifest_hash IS DISTINCT FROM NEW.manifest_hash OR
             actual_partition_count<>manifest_row.partition_count OR
             actual_partition_count<1 OR
             actual_partition_bytes<>manifest_row.byte_size OR
             actual_partition_items<>manifest_row.row_or_item_count OR
             bad_partition_count<>0 OR EXISTS (
               SELECT 1
                 FROM generate_series(0,manifest_row.partition_count-1) expected(ordinal)
                 LEFT JOIN data.payload_manifest_partition link
                   ON link.payload_manifest_id=NEW.payload_manifest_id
                  AND link.ordinal=expected.ordinal
                WHERE link.payload_manifest_id IS NULL
             ) THEN
            RAISE EXCEPTION 'Typed Runtime Payload Manifest has an incomplete or unverified object closure';
          END IF;

          IF expected_kind='strategy_target' THEN
            SELECT ARRAY[
                     aggregation_manifest.artifact_id,
                     strategy_version.artifact_id,
                     preset_version.artifact_id,
                     snapshot.artifact_id,
                     risk_context.artifact_id
                   ]::uuid[],
                   ARRAY[
                     'aggregation_output','strategy_version','strategy_parameter_preset',
                     'configuration_snapshot','execution_data_context'
                   ]::text[]
              INTO expected_artifact_ids,expected_roles
              FROM strategy.v022_strategy_target_work_spec spec
              JOIN experiment.v022_suite_runtime_plan plan
                ON plan.suite_runtime_plan_id=spec.suite_runtime_plan_id
              JOIN strategy.v022_compiled_strategy_branch branch
                ON branch.compiled_strategy_branch_id=spec.compiled_strategy_branch_id
              JOIN strategy.v022_strategy_version strategy_version
                ON strategy_version.strategy_version_id=branch.strategy_version_id
              JOIN strategy.v022_compiled_strategy_branch_preset_binding preset_binding
                ON preset_binding.compiled_strategy_branch_id=
                   spec.compiled_strategy_branch_id
              JOIN strategy.v022_strategy_parameter_preset_version preset_version
                ON preset_version.strategy_parameter_preset_version_id=
                   preset_binding.strategy_parameter_preset_version_id
              JOIN experiment.v022_research_configuration_snapshot snapshot
                ON snapshot.configuration_snapshot_id=spec.configuration_snapshot_id
              JOIN workspace.v022_compiled_execution_data_context risk_context
                ON risk_context.compiled_execution_data_context_id=
                   spec.compiled_execution_data_context_id
              JOIN aggregation.graph_run_aggregation_binding aggregation_binding
                ON aggregation_binding.graph_run_id=plan.graph_run_id
               AND aggregation_binding.graph_work_item_id=
                   spec.source_aggregation_work_item_id
              JOIN aggregation.aggregation_run_output aggregation_output
                ON aggregation_output.aggregation_run_id=
                   aggregation_binding.aggregation_run_id
              JOIN data.payload_manifest aggregation_manifest
                ON aggregation_manifest.payload_manifest_id=
                   aggregation_output.payload_manifest_id
             WHERE spec.graph_work_item_id=NEW.graph_work_item_id
             LIMIT 1;
          ELSIF expected_kind='defense_decision' THEN
            SELECT ARRAY[
                     strategy_output.artifact_id,
                     package.artifact_id,
                     timing.artifact_id,
                     allocation.artifact_id,
                     defense_context.artifact_id
                   ]::uuid[],
                   ARRAY[
                     'strategy_target','defense_package','timing_policy',
                     'allocation_policy','defense_execution_context'
                   ]::text[]
              INTO expected_artifact_ids,expected_roles
              FROM defense.v022_defense_decision_work_spec spec
              JOIN strategy.v022_strategy_target_path strategy_output
                ON strategy_output.graph_work_item_id=
                   spec.source_strategy_work_item_id
              JOIN defense.defense_version package
                ON package.defense_version_id=spec.defense_version_id
              JOIN defense.v022_timing_policy_version timing
                ON timing.timing_policy_version_id=spec.timing_policy_version_id
              JOIN defense.v022_allocation_policy_version allocation
                ON allocation.allocation_policy_version_id=
                   spec.allocation_policy_version_id
              JOIN defense.v022_compiled_defense_execution_context defense_context
                ON defense_context.compiled_defense_execution_context_id=
                   spec.compiled_defense_execution_context_id
             WHERE spec.graph_work_item_id=NEW.graph_work_item_id
             LIMIT 1;
          ELSIF expected_kind='sleeve_merge' THEN
            SELECT CASE
                     WHEN spec.source_defense_work_item_id IS NULL THEN
                       ARRAY[strategy_output.artifact_id]::uuid[]
                     ELSE ARRAY[
                       strategy_output.artifact_id,defense_output.artifact_id
                     ]::uuid[]
                   END,
                   CASE
                     WHEN spec.source_defense_work_item_id IS NULL THEN
                       ARRAY['strategy_target']::text[]
                     ELSE ARRAY['strategy_target','defense_decision']::text[]
                   END
              INTO expected_artifact_ids,expected_roles
              FROM strategy.v022_sleeve_merge_work_spec spec
              JOIN strategy.v022_strategy_target_path strategy_output
                ON strategy_output.graph_work_item_id=
                   spec.source_strategy_work_item_id
              LEFT JOIN defense.v022_defense_decision_path defense_output
                ON defense_output.graph_work_item_id=
                   spec.source_defense_work_item_id
             WHERE spec.graph_work_item_id=NEW.graph_work_item_id
             LIMIT 1;
          ELSE
            SELECT ARRAY[
                     merge_output.artifact_id,
                     evaluation_context.artifact_id,
                     snapshot.artifact_id
                   ]::uuid[],
                   ARRAY[
                     'merged_portfolio_target','portfolio_evaluation_data_context',
                     'configuration_snapshot'
                   ]::text[]
              INTO expected_artifact_ids,expected_roles
              FROM experiment.v022_portfolio_cell_work_spec spec
              JOIN strategy.v022_merged_portfolio_target_path merge_output
                ON merge_output.graph_work_item_id=spec.source_merge_work_item_id
              JOIN experiment.v022_research_cell cell
                ON cell.research_cell_id=spec.research_cell_id
              JOIN experiment.v022_portfolio_evaluation_data_context evaluation_context
                ON evaluation_context.portfolio_evaluation_data_context_id=
                   spec.portfolio_evaluation_data_context_id
              JOIN experiment.v022_research_configuration_snapshot snapshot
                ON snapshot.configuration_snapshot_id=spec.configuration_snapshot_id
             WHERE spec.graph_work_item_id=NEW.graph_work_item_id
             LIMIT 1;
          END IF;

          expected_dependency_count := coalesce(cardinality(expected_artifact_ids),0);
          SELECT count(*) INTO actual_dependency_count
            FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id;
          SELECT count(*) INTO matched_dependency_count
            FROM unnest(expected_artifact_ids,expected_roles)
                   WITH ORDINALITY AS expected(artifact_id,role,ordinal)
            JOIN lineage.artifact_dependency dependency
              ON dependency.artifact_id=NEW.artifact_id
             AND dependency.depends_on_artifact_id=expected.artifact_id
             AND dependency.role=expected.role
             AND dependency.ordinal=expected.ordinal-1;
          IF expected_dependency_count=0 OR
             actual_dependency_count<>expected_dependency_count OR
             matched_dependency_count<>expected_dependency_count THEN
            RAISE EXCEPTION 'Typed Runtime output has incomplete or extra exact lineage';
          END IF;
          IF (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.payload_manifest_artifact_id)<>1 OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.payload_manifest_artifact_id
                  AND dependency.depends_on_artifact_id=NEW.artifact_id
                  AND dependency.role='producer'
                  AND dependency.ordinal=0
             ) THEN
            RAISE EXCEPTION 'Typed Runtime Payload Manifest must depend exactly on its producer';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_strategy_target_path_complete
          AFTER INSERT ON strategy.v022_strategy_target_path
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_typed_runtime_output_complete();
        CREATE CONSTRAINT TRIGGER trg_v022_defense_decision_path_complete
          AFTER INSERT ON defense.v022_defense_decision_path
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_typed_runtime_output_complete();
        CREATE CONSTRAINT TRIGGER trg_v022_merged_portfolio_target_path_complete
          AFTER INSERT ON strategy.v022_merged_portfolio_target_path
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_typed_runtime_output_complete();
        CREATE CONSTRAINT TRIGGER trg_v022_portfolio_cell_runtime_result_complete
          AFTER INSERT ON experiment.v022_portfolio_cell_runtime_result
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_typed_runtime_output_complete();
        """
    )


def _create_completion_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION workspace.require_v022_typed_runtime_output_on_completion()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE output_artifact_id uuid; output_status varchar;
                output_worker_key varchar; output_fencing_token bigint;
        BEGIN
          IF NEW.status<>'completed' OR OLD.status='completed' OR
             NEW.work_kind NOT IN (
               'strategy_target','defense_decision','sleeve_merge','portfolio_cell'
             ) THEN
            RETURN NEW;
          END IF;
          IF NEW.work_kind='strategy_target' THEN
            SELECT artifact_id,worker_key,fencing_token
              INTO output_artifact_id,output_worker_key,output_fencing_token
              FROM strategy.v022_strategy_target_path
             WHERE graph_work_item_id=NEW.graph_work_item_id;
          ELSIF NEW.work_kind='defense_decision' THEN
            SELECT artifact_id,worker_key,fencing_token
              INTO output_artifact_id,output_worker_key,output_fencing_token
              FROM defense.v022_defense_decision_path
             WHERE graph_work_item_id=NEW.graph_work_item_id;
          ELSIF NEW.work_kind='sleeve_merge' THEN
            SELECT artifact_id,worker_key,fencing_token
              INTO output_artifact_id,output_worker_key,output_fencing_token
              FROM strategy.v022_merged_portfolio_target_path
             WHERE graph_work_item_id=NEW.graph_work_item_id;
          ELSE
            SELECT artifact_id,worker_key,fencing_token
              INTO output_artifact_id,output_worker_key,output_fencing_token
              FROM experiment.v022_portfolio_cell_runtime_result
             WHERE graph_work_item_id=NEW.graph_work_item_id;
          END IF;
          SELECT status INTO output_status FROM lineage.artifact
           WHERE artifact_id=output_artifact_id;
          IF output_artifact_id IS NULL OR output_status IS DISTINCT FROM 'published' OR
             output_worker_key IS DISTINCT FROM OLD.lease_owner OR
             output_fencing_token IS DISTINCT FROM OLD.fencing_token THEN
            RAISE EXCEPTION 'Typed Runtime Work cannot complete without its exact fenced published output';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_typed_runtime_output_on_completion
          BEFORE UPDATE OF status ON workspace.v022_graph_work_item
          FOR EACH ROW EXECUTE FUNCTION workspace.require_v022_typed_runtime_output_on_completion();
        """
    )


def _create_aggregation_runtime_guards() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_v022_aggregation_run_input_append_only
          BEFORE UPDATE OR DELETE ON aggregation.aggregation_run_input
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_aggregation_run_output_append_only
          BEFORE UPDATE OR DELETE ON aggregation.aggregation_run_output
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_graph_run_aggregation_binding_append_only
          BEFORE UPDATE OR DELETE ON aggregation.graph_run_aggregation_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();

        CREATE FUNCTION aggregation.protect_v022_aggregation_run_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' OR
             NEW.aggregation_run_id IS DISTINCT FROM OLD.aggregation_run_id OR
             NEW.artifact_id IS DISTINCT FROM OLD.artifact_id OR
             NEW.aggregation_version_id IS DISTINCT FROM OLD.aggregation_version_id OR
             NEW.parameter_preset_version_id IS DISTINCT FROM
               OLD.parameter_preset_version_id OR
             NEW.execution_fingerprint IS DISTINCT FROM OLD.execution_fingerprint OR
             NEW.resolved_parameters IS DISTINCT FROM OLD.resolved_parameters OR
             NEW.executor_version IS DISTINCT FROM OLD.executor_version OR
              NEW.environment_fingerprint IS DISTINCT FROM OLD.environment_fingerprint OR
              NEW.started_at IS DISTINCT FROM OLD.started_at OR
              NEW.created_at IS DISTINCT FROM OLD.created_at OR NOT (
                OLD.status='running' AND NEW.status='completed' AND
                  NEW.completed_at IS NOT NULL AND NEW.invalidated_at IS NULL AND
                  NEW.failure_details IS NULL OR
                OLD.status='running' AND NEW.status='failed' AND
                  NEW.completed_at IS NOT NULL AND NEW.invalidated_at IS NULL AND
                  jsonb_typeof(NEW.failure_details)='object' AND
                  NEW.failure_details<>'{}'::jsonb OR
                OLD.status='running' AND NEW.status='cancelled' AND
                  NEW.completed_at IS NOT NULL AND NEW.invalidated_at IS NULL OR
                OLD.status='completed' AND NEW.status='invalidated' AND
                  NEW.completed_at IS NOT DISTINCT FROM OLD.completed_at AND
                  NEW.invalidated_at IS NOT NULL AND
                  NEW.failure_details IS NOT DISTINCT FROM OLD.failure_details
              ) THEN
            RAISE EXCEPTION 'Aggregation Run identity is immutable or status transition is invalid';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_aggregation_run_identity
          BEFORE UPDATE OR DELETE ON aggregation.aggregation_run
          FOR EACH ROW EXECUTE FUNCTION aggregation.protect_v022_aggregation_run_identity();

        CREATE FUNCTION aggregation.protect_v022_aggregation_cache_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' OR
              NEW.execution_fingerprint IS DISTINCT FROM OLD.execution_fingerprint OR
              NEW.aggregation_run_id IS DISTINCT FROM OLD.aggregation_run_id OR
              NEW.eligibility_checked_at IS DISTINCT FROM OLD.eligibility_checked_at OR
              NOT (
                OLD.cache_state='eligible' AND NEW.cache_state='invalidated' AND
                  jsonb_typeof(NEW.invalidation_reason)='object' AND
                  NEW.invalidation_reason<>'{}'::jsonb OR
                OLD.cache_state='eligible' AND NEW.cache_state='evicted' AND
                  NEW.invalidation_reason IS NULL OR
                OLD.cache_state='invalidated' AND NEW.cache_state='evicted' AND
                  NEW.invalidation_reason IS NOT DISTINCT FROM OLD.invalidation_reason
              ) THEN
            RAISE EXCEPTION 'Aggregation Cache identity is immutable or state transition is invalid';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_aggregation_cache_identity
          BEFORE UPDATE OR DELETE ON aggregation.aggregation_run_cache_entry
          FOR EACH ROW EXECUTE FUNCTION aggregation.protect_v022_aggregation_cache_identity();
        """
    )


def _replace_claim_function_with_lease_recovery() -> None:
    op.execute(_claim_function_sql(include_expired=True))


def _replace_mark_ready_function_with_reuse_completion() -> None:
    op.execute(_mark_ready_function_sql(auto_complete=True))


def _mark_ready_function_sql(*, auto_complete: bool) -> str:
    typed_plan_gate = (
        """
          IF EXISTS (
            SELECT 1 FROM workspace.v022_graph_work_consumer consumer
             WHERE consumer.graph_run_id=run_id
               AND consumer.occurrence_kind IN (
                 'strategy_target','defense_decision','sleeve_merge','portfolio_cell'
               )
          ) AND NOT EXISTS (
            SELECT 1
              FROM experiment.v022_suite_runtime_plan plan
              JOIN lineage.artifact artifact ON artifact.artifact_id=plan.artifact_id
             WHERE plan.graph_run_id=run_id
               AND artifact.status='published'
               AND plan.total_work_count=(
                 SELECT count(*)
                   FROM workspace.v022_graph_work_consumer consumer
                  WHERE consumer.graph_run_id=run_id
                    AND consumer.occurrence_kind IN (
                      'strategy_target','defense_decision','sleeve_merge','portfolio_cell'
                    )
               )
          ) THEN
            RAISE EXCEPTION 'typed Suite Graph Run requires its complete Runtime Plan';
          END IF;
        """
        if auto_complete
        else ""
    )
    final_update = (
        """
          IF NOT EXISTS (
            SELECT 1
              FROM workspace.v022_graph_work_consumer consumer
              JOIN workspace.v022_graph_work_item item
                ON item.graph_work_item_id=consumer.graph_work_item_id
             WHERE consumer.graph_run_id=run_id
               AND consumer.released_at IS NULL
               AND item.status NOT IN ('completed','reused')
          ) THEN
            UPDATE workspace.v022_graph_run
               SET status='completed',ready_at=now(),completed_at=now()
             WHERE graph_run_id=run_id;
          ELSE
            UPDATE workspace.v022_graph_run
               SET status='ready',ready_at=now()
             WHERE graph_run_id=run_id;
          END IF;
        """
        if auto_complete
        else """
          UPDATE workspace.v022_graph_run SET status='ready',ready_at=now()
           WHERE graph_run_id=run_id;
        """
    )
    return f"""
        CREATE OR REPLACE FUNCTION workspace.v022_mark_graph_ready(
          run_id uuid, expected_work_count integer
        ) RETURNS void LANGUAGE plpgsql AS $$
        DECLARE actual_count integer; cycle_found boolean; dangling integer;
        BEGIN
          PERFORM 1 FROM workspace.v022_graph_run
           WHERE graph_run_id=run_id AND status='planning' FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'graph run is not planning'; END IF;
          SELECT count(*) INTO actual_count
            FROM workspace.v022_graph_work_consumer WHERE graph_run_id=run_id;
          IF actual_count<>expected_work_count OR actual_count<1 THEN
            RAISE EXCEPTION 'graph run work count mismatch';
          END IF;
          SELECT count(*) INTO dangling
            FROM workspace.v022_graph_work_dependency dependency
            JOIN workspace.v022_graph_work_consumer downstream
              ON downstream.graph_work_item_id=dependency.downstream_work_item_id
             AND downstream.graph_run_id=run_id
            LEFT JOIN workspace.v022_graph_work_consumer upstream
              ON upstream.graph_work_item_id=dependency.upstream_work_item_id
             AND upstream.graph_run_id=run_id
           WHERE dependency.dependency_kind='required'
             AND upstream.graph_work_item_id IS NULL;
          IF dangling>0 THEN
            RAISE EXCEPTION 'graph run contains dangling required dependencies';
          END IF;
          WITH RECURSIVE edges AS (
            SELECT dependency.upstream_work_item_id AS start_id,
                   dependency.downstream_work_item_id AS current_id,
                   ARRAY[
                     dependency.upstream_work_item_id,
                     dependency.downstream_work_item_id
                   ] AS path,
                   false AS cycle
              FROM workspace.v022_graph_work_dependency dependency
              JOIN workspace.v022_graph_work_consumer first_consumer
                ON first_consumer.graph_work_item_id=
                   dependency.upstream_work_item_id
               AND first_consumer.graph_run_id=run_id
              JOIN workspace.v022_graph_work_consumer second_consumer
                ON second_consumer.graph_work_item_id=
                   dependency.downstream_work_item_id
               AND second_consumer.graph_run_id=run_id
            UNION ALL
            SELECT edge.start_id,dependency.downstream_work_item_id,
                   edge.path||dependency.downstream_work_item_id,
                   dependency.downstream_work_item_id=ANY(edge.path)
              FROM edges edge
              JOIN workspace.v022_graph_work_dependency dependency
                ON dependency.upstream_work_item_id=edge.current_id
              JOIN workspace.v022_graph_work_consumer consumer
                ON consumer.graph_work_item_id=dependency.downstream_work_item_id
               AND consumer.graph_run_id=run_id
             WHERE NOT edge.cycle
          )
          SELECT coalesce(bool_or(cycle),false) INTO cycle_found FROM edges;
          IF cycle_found THEN RAISE EXCEPTION 'graph run DAG contains a cycle'; END IF;
          {typed_plan_gate}
          {final_update}
        END $$;
    """


def _claim_function_sql(*, include_expired: bool) -> str:
    claimable = (
        "(item.status='queued' OR "
        "(item.status='running' AND item.lease_expires_at < now()))"
        if include_expired
        else "item.status='queued'"
    )
    return f"""
        CREATE OR REPLACE FUNCTION workspace.v022_claim_graph_work(
          run_id uuid, worker_key varchar, lease_seconds integer
        ) RETURNS TABLE (graph_work_item_id uuid, fencing_token bigint, work_kind varchar)
        LANGUAGE plpgsql AS $$
        DECLARE claimed uuid;
        BEGIN
          IF lease_seconds < 1 THEN RAISE EXCEPTION 'lease_seconds must be positive'; END IF;
          SELECT item.graph_work_item_id INTO claimed
          FROM workspace.v022_graph_work_item item
          JOIN workspace.v022_graph_work_consumer consumer ON consumer.graph_work_item_id=item.graph_work_item_id
          JOIN workspace.v022_graph_run run ON run.graph_run_id=consumer.graph_run_id
          WHERE consumer.graph_run_id=run_id AND consumer.released_at IS NULL
            AND run.status IN ('ready','running') AND run.cancel_requested_at IS NULL
            AND {claimable} AND item.cancel_requested_at IS NULL
            AND NOT EXISTS (
              SELECT 1 FROM workspace.v022_graph_work_dependency dependency
              JOIN workspace.v022_graph_work_item upstream ON upstream.graph_work_item_id=dependency.upstream_work_item_id
              WHERE dependency.downstream_work_item_id=item.graph_work_item_id
                AND dependency.dependency_kind='required'
                AND upstream.status NOT IN ('completed','reused'))
          ORDER BY item.priority, item.created_at, item.graph_work_item_id
          FOR UPDATE OF item SKIP LOCKED LIMIT 1;
          IF claimed IS NULL THEN RETURN; END IF;
          UPDATE workspace.v022_graph_work_item AS claimed_item
          SET status='running', lease_owner=worker_key,
            lease_expires_at=now()+make_interval(secs=>lease_seconds),
            lease_generation=claimed_item.lease_generation+1,
            fencing_token=claimed_item.fencing_token+1,
            attempt_count=claimed_item.attempt_count+1, updated_at=now()
          WHERE claimed_item.graph_work_item_id=claimed
          RETURNING claimed_item.graph_work_item_id,
                    claimed_item.fencing_token,
                    claimed_item.work_kind
          INTO graph_work_item_id, fencing_token, work_kind;
          UPDATE workspace.v022_graph_run SET status='running', started_at=coalesce(started_at,now())
            WHERE workspace.v022_graph_run.graph_run_id=run_id AND status='ready';
          RETURN NEXT;
        END $$;
    """


def _create_append_only_guards() -> None:
    for schema, table in (
        ("experiment", "v022_portfolio_evaluation_data_context"),
        ("experiment", "v022_portfolio_evaluation_data_input"),
        ("experiment", "v022_suite_runtime_plan"),
        ("strategy", "v022_strategy_target_work_spec"),
        ("defense", "v022_defense_decision_work_spec"),
        ("strategy", "v022_sleeve_merge_work_spec"),
        ("experiment", "v022_portfolio_cell_work_spec"),
        ("strategy", "v022_strategy_target_path"),
        ("defense", "v022_defense_decision_path"),
        ("strategy", "v022_merged_portfolio_target_path"),
        ("experiment", "v022_portfolio_cell_runtime_result"),
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_append_only
              BEFORE UPDATE OR DELETE ON {schema}.{table}
              FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
            """
        )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM experiment.v022_suite_runtime_plan) OR
             EXISTS (
               SELECT 1
                 FROM experiment.v022_research_cell_evaluation_data_context_binding
             ) OR
             EXISTS (
               SELECT 1 FROM experiment.v022_portfolio_evaluation_data_context
             ) OR EXISTS (
               SELECT 1 FROM experiment.v022_portfolio_evaluation_data_input
             ) OR
             EXISTS (
               SELECT 1 FROM workspace.v022_graph_work_item
                WHERE work_kind IN (
                  'strategy_target','defense_decision','sleeve_merge','portfolio_cell'
                )
             ) OR EXISTS (
               SELECT 1 FROM workspace.v022_graph_work_consumer
                WHERE occurrence_kind IN (
                  'strategy_target','defense_decision','sleeve_merge','portfolio_cell'
                )
             ) THEN
            RAISE EXCEPTION 'Cannot downgrade typed Suite Runtime while published runtime identities exist';
          END IF;
        END $$;
        """
    )
    op.execute(_mark_ready_function_sql(auto_complete=False))
    op.execute(_claim_function_sql(include_expired=False))
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v022_aggregation_run_input_append_only "
        "ON aggregation.aggregation_run_input"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v022_aggregation_run_output_append_only "
        "ON aggregation.aggregation_run_output"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v022_graph_run_aggregation_binding_append_only "
        "ON aggregation.graph_run_aggregation_binding"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS aggregation.protect_v022_aggregation_run_identity() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS aggregation.protect_v022_aggregation_cache_identity() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS workspace.require_v022_typed_runtime_output_on_completion() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS experiment.validate_v022_typed_runtime_output() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS experiment.validate_v022_typed_runtime_output_complete() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS experiment.validate_v022_suite_runtime_plan_complete() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS experiment.validate_v022_suite_runtime_work_spec() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS experiment.validate_v022_suite_runtime_plan() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "experiment.validate_v022_research_cell_evaluation_context_binding() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "experiment.validate_v022_portfolio_evaluation_data_input() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "experiment.validate_v022_portfolio_evaluation_data_context_complete() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "experiment.validate_v022_portfolio_evaluation_data_context() CASCADE"
    )
    for schema, table in (
        ("experiment", "v022_portfolio_cell_runtime_result"),
        ("strategy", "v022_merged_portfolio_target_path"),
        ("defense", "v022_defense_decision_path"),
        ("strategy", "v022_strategy_target_path"),
        ("experiment", "v022_portfolio_cell_work_spec"),
        ("strategy", "v022_sleeve_merge_work_spec"),
        ("defense", "v022_defense_decision_work_spec"),
        ("strategy", "v022_strategy_target_work_spec"),
        ("experiment", "v022_suite_runtime_plan"),
        ("experiment", "v022_research_cell_evaluation_data_context_binding"),
        ("experiment", "v022_portfolio_evaluation_data_input"),
        ("experiment", "v022_portfolio_evaluation_data_context"),
    ):
        op.drop_table(table, schema=schema)
    op.execute(
        """
        ALTER TABLE workspace.v022_graph_work_consumer
          DROP CONSTRAINT v022_graph_work_consumer_occurrence_kind_check;
        ALTER TABLE workspace.v022_graph_work_consumer
          ADD CONSTRAINT v022_graph_work_consumer_occurrence_kind_check
          CHECK (occurrence_kind IN ('node','aggregation'));
        ALTER TABLE workspace.v022_graph_work_item
          DROP CONSTRAINT uq_v022_graph_work_item_id_kind;
        ALTER TABLE workspace.v022_graph_work_item
          DROP CONSTRAINT v022_graph_work_item_work_kind_check;
        ALTER TABLE workspace.v022_graph_work_item
          ADD CONSTRAINT v022_graph_work_item_work_kind_check
          CHECK (work_kind IN ('node','aggregation'));
        """
    )
