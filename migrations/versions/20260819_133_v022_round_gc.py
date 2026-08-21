"""Add Product-safe Research Round garbage-collection planning.

Revision ID: 20260819_133_v022_round_gc
Revises: 20260819_132_v022_round_ranking
"""

from __future__ import annotations

from alembic import op

revision = "20260819_133_v022_round_gc"
down_revision = "20260819_132_v022_round_ranking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW ops.v022_research_round_artifact AS
        SELECT DISTINCT root.research_round_id,root.artifact_id
          FROM (
            SELECT binding.research_round_id,plan.artifact_id
              FROM experiment.v022_suite_launch_batch_round binding
              JOIN experiment.v022_suite_launch_batch_child child
                ON child.suite_launch_batch_id=binding.suite_launch_batch_id
              JOIN experiment.v022_suite_runtime_plan plan
                ON plan.research_suite_id=child.research_suite_id
             WHERE child.research_suite_id IS NOT NULL
            UNION ALL
            SELECT binding.research_round_id,result.artifact_id
              FROM experiment.v022_suite_launch_batch_round binding
              JOIN experiment.v022_suite_launch_batch_child child
                ON child.suite_launch_batch_id=binding.suite_launch_batch_id
              JOIN experiment.v022_research_suite_graph_run_binding suite_run
                ON suite_run.research_suite_id=child.research_suite_id
              JOIN workspace.v022_graph_work_consumer consumer
                ON consumer.graph_run_id=suite_run.graph_run_id
              JOIN experiment.v022_portfolio_cell_runtime_result result
                ON result.graph_work_item_id=consumer.graph_work_item_id
             WHERE child.research_suite_id IS NOT NULL
            UNION ALL
            SELECT binding.research_round_id,evidence.artifact_id
              FROM experiment.v022_suite_launch_batch_round binding
              JOIN experiment.v022_suite_launch_batch_child child
                ON child.suite_launch_batch_id=binding.suite_launch_batch_id
              JOIN experiment.v022_research_suite_graph_run_binding suite_run
                ON suite_run.research_suite_id=child.research_suite_id
              JOIN workspace.v022_graph_work_consumer consumer
                ON consumer.graph_run_id=suite_run.graph_run_id
              JOIN experiment.v022_portfolio_cell_runtime_result result
                ON result.graph_work_item_id=consumer.graph_work_item_id
              JOIN experiment.v022_result_evidence_snapshot evidence
                ON evidence.result_artifact_id=result.artifact_id
             WHERE child.research_suite_id IS NOT NULL
            UNION ALL
            SELECT binding.research_round_id,diagnostic.artifact_id
              FROM experiment.v022_suite_launch_batch_round binding
              JOIN experiment.v022_suite_launch_batch_child child
                ON child.suite_launch_batch_id=binding.suite_launch_batch_id
              JOIN experiment.v022_research_suite_graph_run_binding suite_run
                ON suite_run.research_suite_id=child.research_suite_id
              JOIN workspace.v022_graph_work_consumer consumer
                ON consumer.graph_run_id=suite_run.graph_run_id
              JOIN experiment.v022_portfolio_cell_runtime_result result
                ON result.graph_work_item_id=consumer.graph_work_item_id
              JOIN experiment.v022_result_element_diagnostic diagnostic
                ON diagnostic.result_artifact_id=result.artifact_id
             WHERE child.research_suite_id IS NOT NULL
            UNION ALL
            SELECT binding.research_round_id,path.artifact_id
              FROM experiment.v022_suite_launch_batch_round binding
              JOIN experiment.v022_suite_launch_batch_child child
                ON child.suite_launch_batch_id=binding.suite_launch_batch_id
              JOIN experiment.v022_research_suite_graph_run_binding suite_run
                ON suite_run.research_suite_id=child.research_suite_id
              JOIN workspace.v022_graph_work_consumer consumer
                ON consumer.graph_run_id=suite_run.graph_run_id
              JOIN strategy.v022_strategy_target_path path
                ON path.graph_work_item_id=consumer.graph_work_item_id
             WHERE child.research_suite_id IS NOT NULL
            UNION ALL
            SELECT binding.research_round_id,path.artifact_id
              FROM experiment.v022_suite_launch_batch_round binding
              JOIN experiment.v022_suite_launch_batch_child child
                ON child.suite_launch_batch_id=binding.suite_launch_batch_id
              JOIN experiment.v022_research_suite_graph_run_binding suite_run
                ON suite_run.research_suite_id=child.research_suite_id
              JOIN workspace.v022_graph_work_consumer consumer
                ON consumer.graph_run_id=suite_run.graph_run_id
              JOIN defense.v022_defense_decision_path path
                ON path.graph_work_item_id=consumer.graph_work_item_id
             WHERE child.research_suite_id IS NOT NULL
            UNION ALL
            SELECT binding.research_round_id,path.artifact_id
              FROM experiment.v022_suite_launch_batch_round binding
              JOIN experiment.v022_suite_launch_batch_child child
                ON child.suite_launch_batch_id=binding.suite_launch_batch_id
              JOIN experiment.v022_research_suite_graph_run_binding suite_run
                ON suite_run.research_suite_id=child.research_suite_id
              JOIN workspace.v022_graph_work_consumer consumer
                ON consumer.graph_run_id=suite_run.graph_run_id
              JOIN strategy.v022_merged_portfolio_target_path path
                ON path.graph_work_item_id=consumer.graph_work_item_id
             WHERE child.research_suite_id IS NOT NULL
            UNION ALL
            SELECT binding.research_round_id,run.artifact_id
              FROM experiment.v022_suite_launch_batch_round binding
              JOIN experiment.v022_suite_launch_batch_child child
                ON child.suite_launch_batch_id=binding.suite_launch_batch_id
              JOIN experiment.v022_research_suite_graph_run_binding suite_run
                ON suite_run.research_suite_id=child.research_suite_id
              JOIN processing.graph_run_node_binding graph_node
                ON graph_node.graph_run_id=suite_run.graph_run_id
              JOIN processing.node_run run ON run.node_run_id=graph_node.node_run_id
             WHERE child.research_suite_id IS NOT NULL
            UNION ALL
            SELECT binding.research_round_id,run.artifact_id
              FROM experiment.v022_suite_launch_batch_round binding
              JOIN experiment.v022_suite_launch_batch_child child
                ON child.suite_launch_batch_id=binding.suite_launch_batch_id
              JOIN experiment.v022_research_suite_graph_run_binding suite_run
                ON suite_run.research_suite_id=child.research_suite_id
              JOIN aggregation.graph_run_aggregation_binding graph_aggregation
                ON graph_aggregation.graph_run_id=suite_run.graph_run_id
              JOIN aggregation.aggregation_run run
                ON run.aggregation_run_id=graph_aggregation.aggregation_run_id
             WHERE child.research_suite_id IS NOT NULL
          ) root
        """
    )
    op.execute(
        """
        CREATE VIEW product.v022_product_strong_artifact AS
        SELECT DISTINCT artifact_id FROM (
          SELECT artifact_id FROM product.v022_product_definition
          UNION ALL SELECT artifact_id FROM product.v022_execution_version
          UNION ALL SELECT artifact_id FROM product.v022_qualification_version
          UNION ALL SELECT artifact_id FROM product.v022_monitoring_policy_version
          UNION ALL SELECT artifact_id FROM product.v022_product_enrollment
          UNION ALL SELECT artifact_id FROM product.v022_decision_schedule_version
          UNION ALL SELECT artifact_id FROM product.v022_enrollment_lifecycle_event
          UNION ALL SELECT artifact_id FROM product.v022_oos_monitoring_snapshot
          UNION ALL SELECT artifact_id FROM product.v022_product_data_disclosure
          UNION ALL SELECT artifact_id FROM product.v022_product_input_snapshot
          UNION ALL SELECT artifact_id FROM product.v022_product_runtime_execution
          UNION ALL SELECT artifact_id FROM product.v022_product_runtime_stage
          UNION ALL SELECT artifact_id FROM product.v022_product_decision
          UNION ALL SELECT artifact_id FROM product.v022_product_ensemble_state
        ) root
        """
    )
    op.execute("DROP VIEW data.v022_strong_payload_manifest")
    op.execute(
        """
        CREATE VIEW data.v022_strong_payload_manifest AS
        WITH RECURSIVE root_artifact(artifact_id) AS (
          SELECT artifact_id FROM product.v022_product_strong_artifact
          UNION
          SELECT round_artifact.artifact_id
            FROM ops.v022_research_round_artifact round_artifact
            JOIN workspace.v022_research_round round
              ON round.research_round_id=round_artifact.research_round_id
           WHERE round.status='active'
        ), artifact_closure(artifact_id) AS (
          SELECT artifact_id FROM root_artifact
          UNION
          SELECT dependency.depends_on_artifact_id
            FROM artifact_closure closure
            JOIN lineage.artifact_dependency dependency
              ON dependency.artifact_id=closure.artifact_id
        )
        SELECT DISTINCT root.payload_manifest_id
          FROM (
            SELECT manifest.payload_manifest_id
              FROM data.payload_manifest manifest
              JOIN artifact_closure closure ON closure.artifact_id=manifest.artifact_id
            UNION ALL
            SELECT manifest.payload_manifest_id
              FROM data.payload_manifest manifest
             WHERE manifest.retention_class IN ('product','evidence','export','legal_hold')
            UNION ALL
            SELECT payload_manifest_id FROM data.v022_dataset_payload_binding
            UNION ALL
            SELECT payload_manifest_id FROM data.v022_execution_context_payload_binding
            UNION ALL
            SELECT payload_manifest_id FROM data.v022_product_input_payload_binding
            UNION ALL
            SELECT state.model_payload_manifest_id
              FROM aggregation.v022_fitted_model_state state
              JOIN product.v022_product_ensemble_state_member member
                ON member.fitted_model_state_id=state.fitted_model_state_id
          ) root
        """
    )
    op.execute(
        """
        CREATE TABLE ops.v022_research_round_gc_plan (
          research_round_gc_plan_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact(artifact_id),
          research_round_id uuid NOT NULL
            REFERENCES workspace.v022_research_round(research_round_id),
          plan_fingerprint char(64) NOT NULL UNIQUE,
          status varchar(20) NOT NULL CHECK (status IN ('planned','running','completed','failed')),
          object_count integer NOT NULL CHECK (object_count >= 0),
          manifest_count integer NOT NULL CHECK (manifest_count >= 0),
          estimated_bytes bigint NOT NULL CHECK (estimated_bytes >= 0),
          deleted_object_count integer NOT NULL DEFAULT 0 CHECK (deleted_object_count >= 0),
          deleted_bytes bigint NOT NULL DEFAULT 0 CHECK (deleted_bytes >= 0),
          failure_summary text,
          created_at timestamptz NOT NULL DEFAULT now(),
          started_at timestamptz,
          completed_at timestamptz,
          UNIQUE (research_round_id,plan_fingerprint)
        );
        CREATE TABLE ops.v022_research_round_gc_object (
          research_round_gc_plan_id uuid NOT NULL
            REFERENCES ops.v022_research_round_gc_plan(research_round_gc_plan_id),
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          payload_object_id uuid NOT NULL REFERENCES data.payload_object(payload_object_id),
          storage_uri text NOT NULL,
          expected_content_hash char(64) NOT NULL,
          expected_byte_size bigint NOT NULL CHECK (expected_byte_size >= 0),
          PRIMARY KEY (research_round_gc_plan_id,ordinal),
          UNIQUE (research_round_gc_plan_id,payload_object_id)
        );
        CREATE TABLE ops.v022_research_round_gc_tombstone (
          research_round_id uuid PRIMARY KEY
            REFERENCES workspace.v022_research_round(research_round_id),
          research_round_gc_plan_id uuid NOT NULL UNIQUE
            REFERENCES ops.v022_research_round_gc_plan(research_round_gc_plan_id),
          reset_idempotency_key uuid NOT NULL,
          deleted_object_count integer NOT NULL,
          deleted_bytes bigint NOT NULL,
          completed_at timestamptz NOT NULL,
          summary_fingerprint char(64) NOT NULL UNIQUE
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION ops.guard_v022_research_round_gc_plan_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.research_round_gc_plan_id IS DISTINCT FROM NEW.research_round_gc_plan_id OR
             OLD.artifact_id IS DISTINCT FROM NEW.artifact_id OR
             OLD.research_round_id IS DISTINCT FROM NEW.research_round_id OR
             OLD.plan_fingerprint IS DISTINCT FROM NEW.plan_fingerprint OR
             OLD.object_count IS DISTINCT FROM NEW.object_count OR
             OLD.manifest_count IS DISTINCT FROM NEW.manifest_count OR
             OLD.estimated_bytes IS DISTINCT FROM NEW.estimated_bytes OR
             OLD.created_at IS DISTINCT FROM NEW.created_at OR
             OLD.status='completed' OR
             (OLD.status='planned' AND NEW.status NOT IN ('planned','running','failed')) OR
             (OLD.status='running' AND NEW.status NOT IN ('running','completed','failed')) OR
             (OLD.status='failed' AND NEW.status NOT IN ('failed','running')) THEN
            RAISE EXCEPTION 'Research Round GC Plan is immutable or transition is illegal';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_research_round_gc_plan_update
        BEFORE UPDATE OR DELETE ON ops.v022_research_round_gc_plan
        FOR EACH ROW EXECUTE FUNCTION ops.guard_v022_research_round_gc_plan_update();
        CREATE TRIGGER trg_v022_research_round_gc_object_append_only
        BEFORE UPDATE OR DELETE ON ops.v022_research_round_gc_object
        FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_research_round_gc_tombstone_append_only
        BEFORE UPDATE OR DELETE ON ops.v022_research_round_gc_tombstone
        FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM ops.v022_research_round_gc_plan) THEN
            RAISE EXCEPTION 'Cannot downgrade after Research Round GC planning';
          END IF;
        END $$;
        DROP TRIGGER trg_v022_research_round_gc_tombstone_append_only
          ON ops.v022_research_round_gc_tombstone;
        DROP TRIGGER trg_v022_research_round_gc_object_append_only
          ON ops.v022_research_round_gc_object;
        DROP TRIGGER trg_v022_research_round_gc_plan_update ON ops.v022_research_round_gc_plan;
        DROP FUNCTION ops.guard_v022_research_round_gc_plan_update();
        DROP TABLE ops.v022_research_round_gc_tombstone;
        DROP TABLE ops.v022_research_round_gc_object;
        DROP TABLE ops.v022_research_round_gc_plan;
        DROP VIEW data.v022_strong_payload_manifest;
        """
    )
    op.execute(
        """
        CREATE VIEW data.v022_strong_payload_manifest AS
        SELECT DISTINCT root.payload_manifest_id
          FROM (
            SELECT manifest.payload_manifest_id FROM data.payload_manifest manifest
             WHERE manifest.retention_class IN ('product','evidence','export','legal_hold')
            UNION ALL
            SELECT result.payload_manifest_id
              FROM experiment.v022_result_evidence_snapshot evidence
              JOIN experiment.v022_portfolio_cell_runtime_result result
                ON result.artifact_id=evidence.result_artifact_id
            UNION ALL
            SELECT diagnostic.payload_manifest_id
              FROM experiment.v022_result_element_diagnostic diagnostic
              JOIN experiment.v022_result_evidence_snapshot evidence
                ON evidence.result_artifact_id=diagnostic.result_artifact_id
            UNION ALL
            SELECT payload_manifest_id FROM data.v022_product_input_payload_binding
          ) root
        """
    )
    op.execute("DROP VIEW product.v022_product_strong_artifact")
    op.execute("DROP VIEW ops.v022_research_round_artifact")
