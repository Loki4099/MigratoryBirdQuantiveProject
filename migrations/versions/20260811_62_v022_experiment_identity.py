# ruff: noqa: E501
"""Add v0.22 Experiment configuration, evidence, and common-panel identity.

Revision ID: 20260811_62_v022_exp_identity
Revises: 20260811_61_v022_checkpoint
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_62_v022_exp_identity"
down_revision: str | None = "20260811_61_v022_checkpoint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_research_configuration_snapshot (
          configuration_snapshot_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          compiled_research_graph_id uuid NOT NULL REFERENCES workspace.compiled_research_graph,
          compiled_strategy_branch_id uuid NOT NULL REFERENCES strategy.v022_compiled_strategy_branch,
          configuration_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (configuration_fingerprint ~ '^[0-9a-f]{64}$'),
          semantic_identity_document jsonb NOT NULL,
          provenance_document jsonb NOT NULL,
          display_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (configuration_snapshot_id,compiled_research_graph_id)
        );
        CREATE TABLE experiment.v022_configuration_direct_input (
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          compiled_feature_occurrence_id uuid NOT NULL
            REFERENCES workspace.compiled_feature_occurrence,
          display_document jsonb NOT NULL,
          PRIMARY KEY (configuration_snapshot_id,ordinal),
          UNIQUE (configuration_snapshot_id,compiled_feature_occurrence_id)
        );
        CREATE TABLE experiment.v022_common_evaluation_panel (
          common_evaluation_panel_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          panel_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (panel_fingerprint ~ '^[0-9a-f]{64}$'),
          evidence_class varchar(32) NOT NULL CHECK (
            evidence_class IN ('walk_forward_backtest','locked_historical_test','prospective_oos')
          ),
          observation_count integer NOT NULL CHECK (observation_count > 0),
          panel_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE experiment.v022_common_evaluation_panel_member (
          common_evaluation_panel_id uuid NOT NULL
            REFERENCES experiment.v022_common_evaluation_panel,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          decision_session date NOT NULL,
          asset_key varchar(240) NOT NULL,
          PRIMARY KEY (common_evaluation_panel_id,ordinal),
          UNIQUE (common_evaluation_panel_id,decision_session,asset_key)
        );
        CREATE TABLE experiment.v022_result_evidence_snapshot (
          result_evidence_snapshot_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          result_artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          common_evaluation_panel_id uuid NULL
            REFERENCES experiment.v022_common_evaluation_panel,
          evidence_class varchar(32) NOT NULL CHECK (
            evidence_class IN ('walk_forward_backtest','locked_historical_test','prospective_oos')
          ),
          evidence_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (evidence_fingerprint ~ '^[0-9a-f]{64}$'),
          evidence_document jsonb NOT NULL,
          quality_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION experiment.validate_v022_configuration_snapshot()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE branch_graph uuid;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT compiled_research_graph_id INTO branch_graph
            FROM strategy.v022_compiled_strategy_branch
           WHERE compiled_strategy_branch_id=NEW.compiled_strategy_branch_id;
          IF branch_graph IS DISTINCT FROM NEW.compiled_research_graph_id THEN
            RAISE EXCEPTION 'Configuration Snapshot branch and graph differ';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_configuration_snapshot_validate
          BEFORE INSERT ON experiment.v022_research_configuration_snapshot
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_configuration_snapshot();

        CREATE FUNCTION experiment.validate_v022_configuration_direct_input()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_occurrence uuid;
        BEGIN
          SELECT input.compiled_feature_occurrence_id INTO expected_occurrence
            FROM experiment.v022_research_configuration_snapshot snapshot
            JOIN strategy.v022_compiled_strategy_branch branch
              ON branch.compiled_strategy_branch_id=snapshot.compiled_strategy_branch_id
            JOIN workspace.compiled_aggregation_input input
              ON input.compiled_aggregation_instance_id=branch.compiled_aggregation_instance_id
             AND input.ordinal=NEW.ordinal
           WHERE snapshot.configuration_snapshot_id=NEW.configuration_snapshot_id;
          IF expected_occurrence IS DISTINCT FROM NEW.compiled_feature_occurrence_id THEN
            RAISE EXCEPTION 'Configuration direct input is not the exact ordered Aggregation input';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_configuration_direct_input_validate
          BEFORE INSERT ON experiment.v022_configuration_direct_input
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_configuration_direct_input();

        CREATE FUNCTION experiment.validate_v022_configuration_input_completeness()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_count bigint; actual_count bigint;
        BEGIN
          SELECT count(*) INTO expected_count
            FROM strategy.v022_compiled_strategy_branch branch
            JOIN workspace.compiled_aggregation_input input
              ON input.compiled_aggregation_instance_id=branch.compiled_aggregation_instance_id
           WHERE branch.compiled_strategy_branch_id=NEW.compiled_strategy_branch_id;
          SELECT count(*) INTO actual_count
            FROM experiment.v022_configuration_direct_input input
           WHERE input.configuration_snapshot_id=NEW.configuration_snapshot_id;
          IF expected_count <> actual_count THEN
            RAISE EXCEPTION 'Configuration Snapshot direct input set is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_configuration_input_complete
          AFTER INSERT ON experiment.v022_research_configuration_snapshot
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_configuration_input_completeness();

        CREATE FUNCTION experiment.validate_v022_common_panel()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_common_panel_validate
          BEFORE INSERT ON experiment.v022_common_evaluation_panel
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_common_panel();

        CREATE FUNCTION experiment.validate_v022_common_panel_completeness()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_count bigint;
        BEGIN
          SELECT count(*) INTO actual_count
            FROM experiment.v022_common_evaluation_panel_member
           WHERE common_evaluation_panel_id=NEW.common_evaluation_panel_id;
          IF actual_count <> NEW.observation_count THEN
            RAISE EXCEPTION 'Common Evaluation Panel member count differs from observation_count';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_common_panel_complete
          AFTER INSERT ON experiment.v022_common_evaluation_panel
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_common_panel_completeness();

        CREATE FUNCTION experiment.validate_v022_result_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE result_status varchar; panel_class varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT status INTO result_status FROM lineage.artifact
           WHERE artifact_id=NEW.result_artifact_id;
          IF result_status <> 'published' THEN
            RAISE EXCEPTION 'Result Evidence requires a published Result Artifact';
          END IF;
          IF NEW.common_evaluation_panel_id IS NOT NULL THEN
            SELECT evidence_class INTO panel_class
              FROM experiment.v022_common_evaluation_panel
             WHERE common_evaluation_panel_id=NEW.common_evaluation_panel_id;
            IF panel_class IS DISTINCT FROM NEW.evidence_class THEN
              RAISE EXCEPTION 'Result Evidence and Common Panel evidence classes differ';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_result_evidence_validate
          BEFORE INSERT ON experiment.v022_result_evidence_snapshot
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_result_evidence();
        """
    )
    for table in (
        "v022_research_configuration_snapshot",
        "v022_configuration_direct_input",
        "v022_common_evaluation_panel",
        "v022_common_evaluation_panel_member",
        "v022_result_evidence_snapshot",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON experiment.{table} FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def downgrade() -> None:
    for function in (
        "validate_v022_result_evidence",
        "validate_v022_common_panel_completeness",
        "validate_v022_common_panel",
        "validate_v022_configuration_input_completeness",
        "validate_v022_configuration_direct_input",
        "validate_v022_configuration_snapshot",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS experiment.{function}() CASCADE")
    for table in (
        "v022_result_evidence_snapshot",
        "v022_common_evaluation_panel_member",
        "v022_common_evaluation_panel",
        "v022_configuration_direct_input",
        "v022_research_configuration_snapshot",
    ):
        op.drop_table(table, schema="experiment")
