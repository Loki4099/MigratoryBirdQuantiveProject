# ruff: noqa: E501
"""Add v0.22 Draft Intent and immutable compiled graph identity.

Revision ID: 20260810_51_v022_workspace
Revises: 20260810_50_v022_release
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260810_51_v022_workspace"
down_revision: str | None = "20260810_50_v022_release"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_draft_intent (
          draft_intent_id uuid PRIMARY KEY,
          catalog_release_id uuid NOT NULL REFERENCES workspace.v022_catalog_release,
          draft_key varchar(180) NOT NULL,
          revision integer NOT NULL CHECK (revision >= 1),
          status varchar(24) NOT NULL CHECK (status IN ('draft','archived')),
          intent_document jsonb NOT NULL,
          intent_fingerprint varchar(64) NOT NULL CHECK (intent_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (draft_key, revision), UNIQUE (draft_intent_id, revision)
        );
        CREATE TABLE workspace.v022_draft_event (
          draft_event_id uuid PRIMARY KEY,
          draft_intent_id uuid NOT NULL REFERENCES workspace.v022_draft_intent,
          sequence_number integer NOT NULL CHECK (sequence_number >= 1),
          event_type varchar(100) NOT NULL,
          event_document jsonb NOT NULL,
          actor_key varchar(160) NOT NULL,
          idempotency_key uuid NOT NULL,
          request_fingerprint varchar(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          occurred_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (draft_intent_id, sequence_number), UNIQUE (actor_key, idempotency_key)
        );
        CREATE TABLE workspace.v022_draft_preview (
          draft_preview_id uuid PRIMARY KEY,
          draft_intent_id uuid NOT NULL REFERENCES workspace.v022_draft_intent,
          draft_revision integer NOT NULL,
          data_availability_revision varchar(160) NOT NULL,
          derived_state_fingerprint varchar(64) NOT NULL CHECK (derived_state_fingerprint ~ '^[0-9a-f]{64}$'),
          view_token varchar(64) NOT NULL CHECK (view_token ~ '^[0-9a-f]{64}$'),
          preview_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (draft_intent_id, draft_revision)
            REFERENCES workspace.v022_draft_intent (draft_intent_id, revision),
          UNIQUE (view_token)
        );
        CREATE TABLE workspace.v022_command_result (
          command_result_id uuid PRIMARY KEY,
          actor_key varchar(160) NOT NULL,
          command_kind varchar(100) NOT NULL,
          idempotency_key uuid NOT NULL,
          request_fingerprint varchar(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          response_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (actor_key, command_kind, idempotency_key)
        );
        CREATE TABLE workspace.compiled_research_graph (
          compiled_research_graph_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          graph_fingerprint varchar(64) NOT NULL UNIQUE CHECK (graph_fingerprint ~ '^[0-9a-f]{64}$'),
          contract_version varchar(40) NOT NULL CHECK (contract_version = 'v0.22.0'),
          compiler_version varchar(80) NOT NULL,
          catalog_release_id uuid NOT NULL REFERENCES workspace.v022_catalog_release,
          asset_context_fingerprint varchar(64) NOT NULL CHECK (asset_context_fingerprint ~ '^[0-9a-f]{64}$'),
          resolved_data_binding_fingerprint varchar(64) NOT NULL CHECK (resolved_data_binding_fingerprint ~ '^[0-9a-f]{64}$'),
          frequency varchar(40) NOT NULL,
          normalized_graph jsonb NOT NULL,
          node_count integer NOT NULL CHECK (node_count >= 0),
          occurrence_count integer NOT NULL CHECK (occurrence_count >= 1),
          edge_count integer NOT NULL CHECK (edge_count >= 0),
          projection_count integer NOT NULL CHECK (projection_count >= 0),
          aggregation_instance_count integer NOT NULL CHECK (aggregation_instance_count >= 1),
          strategy_branch_count integer NOT NULL CHECK (strategy_branch_count >= 1),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE workspace.v022_compile_attempt (
          compile_attempt_id uuid PRIMARY KEY,
          draft_intent_id uuid NOT NULL REFERENCES workspace.v022_draft_intent,
          draft_revision integer NOT NULL,
          catalog_release_id uuid NOT NULL REFERENCES workspace.v022_catalog_release,
          compiler_version varchar(80) NOT NULL,
          context_document jsonb NOT NULL,
          request_fingerprint varchar(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          status varchar(24) NOT NULL CHECK (status IN ('succeeded','rejected','failed')),
          diagnostics jsonb NOT NULL,
          compiled_research_graph_id uuid NULL REFERENCES workspace.compiled_research_graph,
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (draft_intent_id, draft_revision)
            REFERENCES workspace.v022_draft_intent (draft_intent_id, revision),
          CHECK ((status = 'succeeded') = (compiled_research_graph_id IS NOT NULL))
        );
        CREATE TABLE workspace.compiled_graph_node (
          compiled_graph_node_id uuid PRIMARY KEY,
          compiled_research_graph_id uuid NOT NULL REFERENCES workspace.compiled_research_graph,
          node_version_id uuid NOT NULL REFERENCES processing.node_version,
          stage_no smallint NOT NULL CHECK (stage_no BETWEEN 1 AND 3),
          node_fingerprint varchar(64) NOT NULL CHECK (node_fingerprint ~ '^[0-9a-f]{64}$'),
          UNIQUE (compiled_research_graph_id, node_fingerprint),
          UNIQUE (compiled_graph_node_id, compiled_research_graph_id)
        );
        CREATE TABLE workspace.compiled_feature_occurrence (
          compiled_feature_occurrence_id uuid PRIMARY KEY,
          compiled_research_graph_id uuid NOT NULL REFERENCES workspace.compiled_research_graph,
          feature_version_id uuid NOT NULL REFERENCES processing.feature_version,
          stage_no smallint NOT NULL CHECK (stage_no BETWEEN 0 AND 3),
          is_explicit boolean NOT NULL,
          is_required boolean NOT NULL,
          is_aggregation_input boolean NOT NULL,
          production_kind varchar(24) NOT NULL CHECK (production_kind IN ('raw_input','node_output','layer_projection')),
          source_occurrence_id uuid NULL REFERENCES workspace.compiled_feature_occurrence,
          compiled_graph_node_id uuid NULL REFERENCES workspace.compiled_graph_node,
          output_port_key varchar(160) NULL,
          occurrence_fingerprint varchar(64) NOT NULL CHECK (occurrence_fingerprint ~ '^[0-9a-f]{64}$'),
          UNIQUE (compiled_research_graph_id, feature_version_id, stage_no),
          UNIQUE (compiled_feature_occurrence_id, compiled_research_graph_id),
          CHECK (
            (production_kind = 'raw_input' AND stage_no = 0 AND source_occurrence_id IS NULL AND compiled_graph_node_id IS NULL AND output_port_key IS NULL) OR
            (production_kind = 'layer_projection' AND source_occurrence_id IS NOT NULL AND compiled_graph_node_id IS NULL AND output_port_key IS NULL) OR
            (production_kind = 'node_output' AND source_occurrence_id IS NULL AND compiled_graph_node_id IS NOT NULL AND output_port_key IS NOT NULL)
          ),
          CHECK (NOT is_aggregation_input OR (stage_no = 3 AND is_explicit))
        );
        CREATE TABLE workspace.compiled_node_input (
          compiled_graph_node_id uuid NOT NULL REFERENCES workspace.compiled_graph_node,
          input_port_key varchar(160) NOT NULL,
          source_occurrence_id uuid NOT NULL REFERENCES workspace.compiled_feature_occurrence,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          PRIMARY KEY (compiled_graph_node_id, input_port_key, ordinal)
        );
        CREATE TABLE workspace.compiled_aggregation_instance (
          compiled_aggregation_instance_id uuid PRIMARY KEY,
          compiled_research_graph_id uuid NOT NULL REFERENCES workspace.compiled_research_graph,
          aggregation_version_id uuid NOT NULL REFERENCES aggregation.aggregation_version,
          parameter_preset_version_id uuid NULL REFERENCES aggregation.parameter_preset_version,
          target_version_id uuid NULL REFERENCES aggregation.target_version,
          training_preset_version_id uuid NULL REFERENCES aggregation.training_preset_version,
          instance_key varchar(400) NOT NULL,
          instance_fingerprint varchar(64) NOT NULL UNIQUE CHECK (instance_fingerprint ~ '^[0-9a-f]{64}$'),
          output_payload_contract_version_id uuid NOT NULL REFERENCES data.payload_contract_version,
          UNIQUE (compiled_research_graph_id, instance_key),
          UNIQUE (compiled_aggregation_instance_id, compiled_research_graph_id)
        );
        CREATE TABLE workspace.compiled_aggregation_input (
          compiled_aggregation_instance_id uuid NOT NULL REFERENCES workspace.compiled_aggregation_instance,
          slot_key varchar(160) NOT NULL,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          compiled_feature_occurrence_id uuid NOT NULL REFERENCES workspace.compiled_feature_occurrence,
          PRIMARY KEY (compiled_aggregation_instance_id, slot_key, ordinal),
          UNIQUE (compiled_aggregation_instance_id, compiled_feature_occurrence_id)
        );
        CREATE TABLE strategy.v022_compiled_strategy_branch (
          compiled_strategy_branch_id uuid PRIMARY KEY,
          compiled_research_graph_id uuid NOT NULL REFERENCES workspace.compiled_research_graph,
          compiled_aggregation_instance_id uuid NOT NULL REFERENCES workspace.compiled_aggregation_instance,
          strategy_version_id uuid NOT NULL REFERENCES strategy.v022_strategy_version,
          defense_version_id uuid NULL REFERENCES defense.defense_version,
          branch_key varchar(500) NOT NULL,
          branch_fingerprint varchar(64) NOT NULL UNIQUE CHECK (branch_fingerprint ~ '^[0-9a-f]{64}$'),
          UNIQUE (compiled_research_graph_id, branch_key)
        );
        """
    )
    _create_guards()
    for schema, table in (
        ("workspace", "compiled_research_graph"),
        ("workspace", "v022_compile_attempt"),
        ("workspace", "compiled_graph_node"),
        ("workspace", "compiled_feature_occurrence"),
        ("workspace", "compiled_node_input"),
        ("workspace", "compiled_aggregation_instance"),
        ("workspace", "compiled_aggregation_input"),
        ("strategy", "v022_compiled_strategy_branch"),
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {schema}.{table} "
            "FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def _create_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION workspace.validate_v022_occurrence() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE source_graph uuid; source_stage smallint; node_graph uuid; node_stage smallint;
        BEGIN
          IF NEW.production_kind = 'layer_projection' THEN
            SELECT compiled_research_graph_id, stage_no INTO source_graph, source_stage
              FROM workspace.compiled_feature_occurrence
             WHERE compiled_feature_occurrence_id = NEW.source_occurrence_id;
            IF source_graph IS DISTINCT FROM NEW.compiled_research_graph_id OR source_stage + 1 <> NEW.stage_no THEN
              RAISE EXCEPTION 'v0.22 projection must stay in graph and cross exactly one stage';
            END IF;
          ELSIF NEW.production_kind = 'node_output' THEN
            SELECT compiled_research_graph_id, stage_no INTO node_graph, node_stage
              FROM workspace.compiled_graph_node WHERE compiled_graph_node_id = NEW.compiled_graph_node_id;
            IF node_graph IS DISTINCT FROM NEW.compiled_research_graph_id OR node_stage <> NEW.stage_no THEN
              RAISE EXCEPTION 'v0.22 node output must use a same-graph same-stage node';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_validate_v022_occurrence BEFORE INSERT ON workspace.compiled_feature_occurrence
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_occurrence();

        CREATE FUNCTION workspace.validate_v022_node_input() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE node_graph uuid; node_stage smallint; source_graph uuid; source_stage smallint;
        BEGIN
          SELECT compiled_research_graph_id, stage_no INTO node_graph, node_stage
            FROM workspace.compiled_graph_node WHERE compiled_graph_node_id = NEW.compiled_graph_node_id;
          SELECT compiled_research_graph_id, stage_no INTO source_graph, source_stage
            FROM workspace.compiled_feature_occurrence WHERE compiled_feature_occurrence_id = NEW.source_occurrence_id;
          IF node_graph IS DISTINCT FROM source_graph OR source_stage + 1 <> node_stage THEN
            RAISE EXCEPTION 'v0.22 node inputs must be same-graph adjacent-stage occurrences';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_validate_v022_node_input BEFORE INSERT ON workspace.compiled_node_input
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_node_input();

        CREATE FUNCTION workspace.validate_v022_aggregation_instance() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar; output_contract uuid;
        BEGIN
          SELECT execution_mode, output_payload_contract_version_id INTO mode, output_contract
            FROM aggregation.aggregation_version WHERE aggregation_version_id = NEW.aggregation_version_id;
          IF mode = 'deterministic' AND (NEW.target_version_id IS NOT NULL OR NEW.training_preset_version_id IS NOT NULL) THEN
            RAISE EXCEPTION 'deterministic aggregation cannot bind Target or Training Preset';
          ELSIF mode = 'supervised' AND (NEW.target_version_id IS NULL OR NEW.training_preset_version_id IS NULL) THEN
            RAISE EXCEPTION 'supervised aggregation requires Target and Training Preset';
          END IF;
          IF output_contract IS DISTINCT FROM NEW.output_payload_contract_version_id THEN
            RAISE EXCEPTION 'aggregation output contract mismatch';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_validate_v022_aggregation_instance BEFORE INSERT ON workspace.compiled_aggregation_instance
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_aggregation_instance();

        CREATE FUNCTION workspace.validate_v022_aggregation_input() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE instance_graph uuid; occurrence_graph uuid; occurrence_stage smallint; explicit boolean; selected boolean;
        BEGIN
          SELECT compiled_research_graph_id INTO instance_graph FROM workspace.compiled_aggregation_instance
            WHERE compiled_aggregation_instance_id = NEW.compiled_aggregation_instance_id;
          SELECT compiled_research_graph_id, stage_no, is_explicit, is_aggregation_input
            INTO occurrence_graph, occurrence_stage, explicit, selected
            FROM workspace.compiled_feature_occurrence
            WHERE compiled_feature_occurrence_id = NEW.compiled_feature_occurrence_id;
          IF instance_graph IS DISTINCT FROM occurrence_graph OR occurrence_stage <> 3 OR NOT explicit OR NOT selected THEN
            RAISE EXCEPTION 'aggregation input must be an explicit selected Stage 3 occurrence in the same graph';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_validate_v022_aggregation_input BEFORE INSERT ON workspace.compiled_aggregation_input
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_aggregation_input();
        """
    )


def downgrade() -> None:
    for schema, table in reversed((
        ("workspace", "compiled_research_graph"), ("workspace", "v022_compile_attempt"),
        ("workspace", "compiled_graph_node"), ("workspace", "compiled_feature_occurrence"),
        ("workspace", "compiled_node_input"), ("workspace", "compiled_aggregation_instance"),
        ("workspace", "compiled_aggregation_input"), ("strategy", "v022_compiled_strategy_branch"),
    )):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {schema}.{table}")
    op.execute("DROP TRIGGER IF EXISTS trg_validate_v022_aggregation_input ON workspace.compiled_aggregation_input")
    op.execute("DROP TRIGGER IF EXISTS trg_validate_v022_aggregation_instance ON workspace.compiled_aggregation_instance")
    op.execute("DROP TRIGGER IF EXISTS trg_validate_v022_node_input ON workspace.compiled_node_input")
    op.execute("DROP TRIGGER IF EXISTS trg_validate_v022_occurrence ON workspace.compiled_feature_occurrence")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_aggregation_input()")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_aggregation_instance()")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_node_input()")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_occurrence()")
    for schema, table in (
        ("strategy", "v022_compiled_strategy_branch"),
        ("workspace", "compiled_aggregation_input"),
        ("workspace", "compiled_aggregation_instance"),
        ("workspace", "compiled_node_input"),
        ("workspace", "compiled_feature_occurrence"),
        ("workspace", "compiled_graph_node"),
        ("workspace", "v022_compile_attempt"),
        ("workspace", "compiled_research_graph"),
        ("workspace", "v022_command_result"),
        ("workspace", "v022_draft_preview"),
        ("workspace", "v022_draft_event"),
        ("workspace", "v022_draft_intent"),
    ):
        op.drop_table(table, schema=schema)
