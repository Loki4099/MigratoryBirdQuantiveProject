# ruff: noqa: E501
"""Add deterministic Aggregation runtime and disabled DB-7A projections.

Revision ID: 20260810_54_v022_deterministic
Revises: 20260810_53_v022_processing
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260810_54_v022_deterministic"
down_revision: str | None = "20260810_53_v022_processing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE aggregation.aggregation_run (
          aggregation_run_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          aggregation_version_id uuid NOT NULL REFERENCES aggregation.aggregation_version,
          parameter_preset_version_id uuid NULL REFERENCES aggregation.parameter_preset_version,
          execution_fingerprint varchar(64) NOT NULL UNIQUE CHECK (execution_fingerprint ~ '^[0-9a-f]{64}$'),
          resolved_parameters jsonb NOT NULL,
          executor_version varchar(120) NOT NULL,
          environment_fingerprint varchar(64) NOT NULL CHECK (environment_fingerprint ~ '^[0-9a-f]{64}$'),
          status varchar(24) NOT NULL CHECK (status IN ('running','completed','failed','cancelled','invalidated')),
          started_at timestamptz NOT NULL, completed_at timestamptz NULL,
          invalidated_at timestamptz NULL, failure_details jsonb NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE aggregation.aggregation_run_input (
          aggregation_run_id uuid NOT NULL REFERENCES aggregation.aggregation_run,
          slot_key varchar(160) NOT NULL,
          payload_manifest_id uuid NOT NULL REFERENCES data.payload_manifest,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          manifest_hash varchar(64) NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
          PRIMARY KEY (aggregation_run_id,slot_key,ordinal)
        );
        CREATE TABLE aggregation.aggregation_run_output (
          aggregation_run_id uuid PRIMARY KEY REFERENCES aggregation.aggregation_run,
          payload_manifest_id uuid NOT NULL UNIQUE REFERENCES data.payload_manifest
        );
        CREATE TABLE aggregation.aggregation_run_cache_entry (
          execution_fingerprint varchar(64) PRIMARY KEY CHECK (execution_fingerprint ~ '^[0-9a-f]{64}$'),
          aggregation_run_id uuid NOT NULL UNIQUE REFERENCES aggregation.aggregation_run,
          cache_state varchar(20) NOT NULL CHECK (cache_state IN ('eligible','invalidated','evicted')),
          eligibility_checked_at timestamptz NOT NULL,
          invalidation_reason jsonb NULL
        );
        CREATE TABLE aggregation.graph_run_aggregation_binding (
          graph_run_id uuid NOT NULL REFERENCES workspace.v022_graph_run,
          compiled_aggregation_instance_id uuid NOT NULL REFERENCES workspace.compiled_aggregation_instance,
          graph_work_item_id uuid NOT NULL REFERENCES workspace.v022_graph_work_item,
          aggregation_run_id uuid NOT NULL REFERENCES aggregation.aggregation_run,
          binding_disposition varchar(16) NOT NULL CHECK (binding_disposition IN ('executed','reused')),
          bound_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (graph_run_id,compiled_aggregation_instance_id),
          UNIQUE (graph_run_id,graph_work_item_id)
        );
        CREATE TABLE strategy.v022_strategy_target_runtime (
          strategy_target_runtime_id uuid PRIMARY KEY,
          graph_run_id uuid NOT NULL REFERENCES workspace.v022_graph_run,
          compiled_strategy_branch_id uuid NOT NULL REFERENCES strategy.v022_compiled_strategy_branch,
          runtime_enabled boolean NOT NULL DEFAULT false CHECK (runtime_enabled = false),
          target_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (graph_run_id,compiled_strategy_branch_id)
        );
        CREATE TABLE defense.v022_defense_run (
          defense_run_id uuid PRIMARY KEY,
          graph_run_id uuid NOT NULL REFERENCES workspace.v022_graph_run,
          defense_version_id uuid NOT NULL REFERENCES defense.defense_version,
          runtime_enabled boolean NOT NULL DEFAULT false CHECK (runtime_enabled = false),
          decision_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE strategy.v022_sleeve_merge_runtime (
          sleeve_merge_runtime_id uuid PRIMARY KEY,
          graph_run_id uuid NOT NULL REFERENCES workspace.v022_graph_run,
          runtime_enabled boolean NOT NULL DEFAULT false CHECK (runtime_enabled = false),
          merge_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (graph_run_id)
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION aggregation.validate_v022_deterministic_run() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mode varchar;
        BEGIN
          SELECT execution_mode INTO mode FROM aggregation.aggregation_version
            WHERE aggregation_version_id=NEW.aggregation_version_id;
          IF mode <> 'deterministic' THEN
            RAISE EXCEPTION 'M2 runtime enables deterministic aggregation only';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_validate_v022_deterministic_run BEFORE INSERT ON aggregation.aggregation_run
          FOR EACH ROW EXECUTE FUNCTION aggregation.validate_v022_deterministic_run();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_validate_v022_deterministic_run ON aggregation.aggregation_run")
    op.execute("DROP FUNCTION IF EXISTS aggregation.validate_v022_deterministic_run()")
    op.drop_table("v022_sleeve_merge_runtime", schema="strategy")
    op.drop_table("v022_defense_run", schema="defense")
    op.drop_table("v022_strategy_target_runtime", schema="strategy")
    op.drop_table("graph_run_aggregation_binding", schema="aggregation")
    op.drop_table("aggregation_run_cache_entry", schema="aggregation")
    op.drop_table("aggregation_run_output", schema="aggregation")
    op.drop_table("aggregation_run_input", schema="aggregation")
    op.drop_table("aggregation_run", schema="aggregation")
