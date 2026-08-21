# ruff: noqa: E501
"""Allow diagnostics for materialized Stage 1-3 lineage elements.

Revision ID: 20260814_93_v022_diag_lineage
Revises: 20260814_92_v022_ctx_payload
"""

from __future__ import annotations

from alembic import op

revision = "20260814_93_v022_diag_lineage"
down_revision = "20260814_92_v022_ctx_payload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION experiment.validate_v022_result_element_diagnostic()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; result_row record; manifest_row record;
                target_artifact uuid; market_artifact uuid; calendar_artifact uuid;
                occurrence_is_upstream boolean;
                manifest_is_result_lineage boolean;
                manifest_matches_occurrence_output boolean;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF artifact_row.artifact_type IS DISTINCT FROM 'v022_result_element_diagnostic' OR
             artifact_row.artifact_key IS DISTINCT FROM NEW.diagnostic_fingerprint OR
             artifact_row.version_number IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'Element Diagnostic requires its exact draft Artifact';
          END IF;

          SELECT artifact.status,result.configuration_snapshot_id
            INTO result_row
            FROM experiment.v022_portfolio_cell_runtime_result result
            JOIN lineage.artifact artifact ON artifact.artifact_id=result.artifact_id
           WHERE result.artifact_id=NEW.result_artifact_id;
          IF result_row.status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Element Diagnostic requires a published Result Artifact';
          END IF;
          IF result_row.configuration_snapshot_id IS DISTINCT FROM
               NEW.configuration_snapshot_id THEN
            RAISE EXCEPTION 'Element Diagnostic Result and Configuration differ';
          END IF;

          SELECT manifest.artifact_id,artifact.status,manifest.materialization_state
            INTO manifest_row
            FROM data.payload_manifest manifest
            JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
           WHERE manifest.payload_manifest_id=NEW.payload_manifest_id;
          IF manifest_row.artifact_id IS DISTINCT FROM NEW.payload_manifest_artifact_id OR
             manifest_row.status IS DISTINCT FROM 'published' OR
             manifest_row.materialization_state IS DISTINCT FROM 'materialized' THEN
            RAISE EXCEPTION 'Element Diagnostic requires its exact materialized input Manifest';
          END IF;

          SELECT version.artifact_id INTO target_artifact
            FROM data.forward_return_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             AND artifact.status='published'
           WHERE version.forward_return_version_id=NEW.target_version_id;
          SELECT publication.artifact_id INTO market_artifact
            FROM data.dataset_publication publication
            JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
             AND artifact.status='published'
           WHERE publication.dataset_publication_id=NEW.market_dataset_publication_id;
          SELECT version.artifact_id INTO calendar_artifact
            FROM catalog.calendar_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             AND artifact.status='published'
           WHERE version.calendar_version_id=NEW.calendar_version_id;
          IF target_artifact IS DISTINCT FROM NEW.target_version_artifact_id OR
             market_artifact IS DISTINCT FROM NEW.market_dataset_artifact_id OR
             calendar_artifact IS DISTINCT FROM NEW.calendar_artifact_id THEN
            RAISE EXCEPTION 'Element Diagnostic physical identities do not match their Artifacts';
          END IF;

          WITH RECURSIVE occurrence_edge AS (
            SELECT occurrence.compiled_feature_occurrence_id AS downstream_occurrence_id,
                   occurrence.source_occurrence_id AS upstream_occurrence_id
              FROM workspace.compiled_feature_occurrence occurrence
             WHERE occurrence.source_occurrence_id IS NOT NULL
            UNION
            SELECT occurrence.compiled_feature_occurrence_id AS downstream_occurrence_id,
                   input.source_occurrence_id AS upstream_occurrence_id
              FROM workspace.compiled_feature_occurrence occurrence
              JOIN workspace.compiled_node_input input
                ON input.compiled_graph_node_id=occurrence.compiled_graph_node_id
             WHERE occurrence.production_kind='node_output'
          ), configuration_upstream AS (
            SELECT input.compiled_feature_occurrence_id
              FROM experiment.v022_configuration_direct_input input
             WHERE input.configuration_snapshot_id=NEW.configuration_snapshot_id
            UNION
            SELECT edge.upstream_occurrence_id
              FROM configuration_upstream upstream
              JOIN occurrence_edge edge
                ON edge.downstream_occurrence_id=
                   upstream.compiled_feature_occurrence_id
          )
          SELECT EXISTS (
            SELECT 1 FROM configuration_upstream upstream
             WHERE upstream.compiled_feature_occurrence_id=
                   NEW.compiled_feature_occurrence_id
          ) INTO occurrence_is_upstream;
          IF NOT occurrence_is_upstream THEN
            RAISE EXCEPTION
              'Element Diagnostic occurrence is not in the frozen recursive input lineage';
          END IF;

          WITH RECURSIVE direct_manifest AS (
            SELECT aggregation_input.payload_manifest_id
              FROM experiment.v022_portfolio_cell_runtime_result result
              JOIN experiment.v022_portfolio_cell_work_spec cell_spec
                ON cell_spec.graph_work_item_id=result.graph_work_item_id
              JOIN experiment.v022_suite_runtime_plan plan
                ON plan.suite_runtime_plan_id=cell_spec.suite_runtime_plan_id
              JOIN strategy.v022_strategy_target_work_spec strategy_spec
                ON strategy_spec.suite_runtime_plan_id=plan.suite_runtime_plan_id
               AND strategy_spec.research_suite_branch_id=
                   cell_spec.research_suite_branch_id
               AND strategy_spec.configuration_snapshot_id=
                   NEW.configuration_snapshot_id
              JOIN aggregation.graph_run_aggregation_binding aggregation_binding
                ON aggregation_binding.graph_run_id=plan.graph_run_id
               AND aggregation_binding.graph_work_item_id=
                   strategy_spec.source_aggregation_work_item_id
              JOIN aggregation.aggregation_run_input aggregation_input
                ON aggregation_input.aggregation_run_id=
                   aggregation_binding.aggregation_run_id
             WHERE result.artifact_id=NEW.result_artifact_id
               AND result.configuration_snapshot_id=NEW.configuration_snapshot_id
               AND cell_spec.configuration_snapshot_id=NEW.configuration_snapshot_id
          ), manifest_lineage AS (
            SELECT direct.payload_manifest_id FROM direct_manifest direct
            UNION
            SELECT node_input.payload_manifest_id
              FROM manifest_lineage lineage
              JOIN processing.node_run_output node_output
                ON node_output.payload_manifest_id=lineage.payload_manifest_id
              JOIN processing.node_run node_run
                ON node_run.node_run_id=node_output.node_run_id
               AND node_run.status='completed'
              JOIN processing.node_run_input node_input
                ON node_input.node_run_id=node_output.node_run_id
          )
          SELECT EXISTS (
            SELECT 1 FROM manifest_lineage lineage
             WHERE lineage.payload_manifest_id=NEW.payload_manifest_id
          ) INTO manifest_is_result_lineage;
          IF NOT manifest_is_result_lineage THEN
            RAISE EXCEPTION
              'Element Diagnostic Manifest is not in the exact Result input lineage';
          END IF;

          SELECT EXISTS (
            SELECT 1
              FROM experiment.v022_portfolio_cell_runtime_result result
              JOIN experiment.v022_portfolio_cell_work_spec cell_spec
                ON cell_spec.graph_work_item_id=result.graph_work_item_id
              JOIN experiment.v022_suite_runtime_plan plan
                ON plan.suite_runtime_plan_id=cell_spec.suite_runtime_plan_id
              JOIN workspace.compiled_feature_occurrence occurrence
                ON occurrence.compiled_feature_occurrence_id=
                   NEW.compiled_feature_occurrence_id
               AND occurrence.compiled_research_graph_id=
                   plan.compiled_research_graph_id
               AND occurrence.production_kind='node_output'
              JOIN processing.node_run_output node_output
                ON node_output.payload_manifest_id=NEW.payload_manifest_id
               AND node_output.output_port_key=occurrence.output_port_key
              JOIN processing.node_run node_run
                ON node_run.node_run_id=node_output.node_run_id
               AND node_run.status='completed'
             WHERE result.artifact_id=NEW.result_artifact_id
               AND result.configuration_snapshot_id=NEW.configuration_snapshot_id
               AND cell_spec.configuration_snapshot_id=NEW.configuration_snapshot_id
               AND EXISTS (
                 SELECT 1
                   FROM processing.graph_run_node_binding binding
                  WHERE binding.node_run_id=node_run.node_run_id
                    AND binding.compiled_graph_node_id=
                        occurrence.compiled_graph_node_id
               )
          ) INTO manifest_matches_occurrence_output;
          IF NOT manifest_matches_occurrence_output THEN
            RAISE EXCEPTION
              'Element Diagnostic Manifest is not the exact completed occurrence output';
          END IF;
          RETURN NEW;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1
              FROM experiment.v022_result_element_diagnostic diagnostic
             WHERE NOT EXISTS (
               SELECT 1
                 FROM experiment.v022_configuration_direct_input input
                WHERE input.configuration_snapshot_id=
                      diagnostic.configuration_snapshot_id
                  AND input.compiled_feature_occurrence_id=
                      diagnostic.compiled_feature_occurrence_id
             )
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade while non-direct v0.22 Element Diagnostics exist';
          END IF;
        END $$;

        CREATE OR REPLACE FUNCTION experiment.validate_v022_result_element_diagnostic()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; result_status varchar; manifest_row record;
                target_artifact uuid; market_artifact uuid; calendar_artifact uuid;
                direct_input_exists boolean;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF artifact_row.artifact_type IS DISTINCT FROM 'v022_result_element_diagnostic' OR
             artifact_row.artifact_key IS DISTINCT FROM NEW.diagnostic_fingerprint OR
             artifact_row.version_number IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'Element Diagnostic requires its exact draft Artifact';
          END IF;
          SELECT status INTO result_status FROM lineage.artifact
           WHERE artifact_id=NEW.result_artifact_id;
          IF result_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Element Diagnostic requires a published Result Artifact';
          END IF;
          SELECT manifest.artifact_id,artifact.status,manifest.materialization_state
            INTO manifest_row
            FROM data.payload_manifest manifest
            JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
           WHERE manifest.payload_manifest_id=NEW.payload_manifest_id;
          IF manifest_row.artifact_id IS DISTINCT FROM NEW.payload_manifest_artifact_id OR
             manifest_row.status IS DISTINCT FROM 'published' OR
             manifest_row.materialization_state IS DISTINCT FROM 'materialized' THEN
            RAISE EXCEPTION 'Element Diagnostic requires its exact materialized input Manifest';
          END IF;
          SELECT version.artifact_id INTO target_artifact
            FROM data.forward_return_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             AND artifact.status='published'
           WHERE version.forward_return_version_id=NEW.target_version_id;
          SELECT publication.artifact_id INTO market_artifact
            FROM data.dataset_publication publication
            JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
             AND artifact.status='published'
           WHERE publication.dataset_publication_id=NEW.market_dataset_publication_id;
          SELECT version.artifact_id INTO calendar_artifact
            FROM catalog.calendar_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             AND artifact.status='published'
           WHERE version.calendar_version_id=NEW.calendar_version_id;
          IF target_artifact IS DISTINCT FROM NEW.target_version_artifact_id OR
             market_artifact IS DISTINCT FROM NEW.market_dataset_artifact_id OR
             calendar_artifact IS DISTINCT FROM NEW.calendar_artifact_id THEN
            RAISE EXCEPTION 'Element Diagnostic physical identities do not match their Artifacts';
          END IF;
          SELECT EXISTS (
            SELECT 1 FROM experiment.v022_configuration_direct_input input
             WHERE input.configuration_snapshot_id=NEW.configuration_snapshot_id
               AND input.compiled_feature_occurrence_id=NEW.compiled_feature_occurrence_id
          ) INTO direct_input_exists;
          IF NOT direct_input_exists THEN
            RAISE EXCEPTION 'Element Diagnostic occurrence is not a frozen direct input';
          END IF;
          RETURN NEW;
        END $$;
        """
    )
