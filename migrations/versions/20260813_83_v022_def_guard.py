"""Make the v0.22 Defense input-role guard branch-safe.

Revision ID: 20260813_83_v022_def_guard
Revises: 20260813_82_v022_eval_bundle
"""

from __future__ import annotations

from alembic import op

revision = "20260813_83_v022_def_guard"
down_revision = "20260813_82_v022_eval_bundle"
branch_labels = None
depends_on = None

_SOURCE = """          IF NEW.input_role='timing_reference' AND (
               timing_rule->>'rule_type' IS DISTINCT FROM
                 'moving_average_tiered_budget' OR
               dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
               security_key_value IS DISTINCT FROM
                 timing_rule->>'reference_asset_key'
             ) THEN
            RAISE EXCEPTION 'Timing reference input does not match the Timing Policy';
          END IF;
          IF NEW.input_role='defensive_asset' AND (
               member_row.component_role IS DISTINCT FROM 'defensive_asset' OR
               dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
               member_row.security_id IS DISTINCT FROM security_id_value
             ) THEN
            RAISE EXCEPTION 'Defensive asset input does not match its Allocation member';
          END IF;
          IF NEW.input_role='reserve_accrual' AND (
               member_row.component_role IS DISTINCT FROM 'reserve' OR
               NEW.reserve_return_model_version_id IS DISTINCT FROM
                 context_row.reserve_return_model_version_id OR
               NEW.reserve_return_model_artifact_id IS DISTINCT FROM
                 context_row.reserve_return_model_artifact_id OR
               dataset_row.value_kind IS DISTINCT FROM 'reserve_return' OR
               NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=NEW.dataset_artifact_id
                    AND dependency.depends_on_artifact_id=
                        NEW.reserve_return_model_artifact_id
                    AND dependency.role='reserve_model'
               )
             ) THEN
            RAISE EXCEPTION 'Reserve accrual input does not match its Allocation and Reserve Model';
          END IF;"""

_TARGET = """          IF NEW.input_role='timing_reference' THEN
            IF timing_rule->>'rule_type' IS DISTINCT FROM
                 'moving_average_tiered_budget' OR
               dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
               security_key_value IS DISTINCT FROM
                 timing_rule->>'reference_asset_key' THEN
              RAISE EXCEPTION 'Timing reference input does not match the Timing Policy';
            END IF;
          ELSIF NEW.input_role='defensive_asset' THEN
            IF member_row.component_role IS DISTINCT FROM 'defensive_asset' OR
               dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
               member_row.security_id IS DISTINCT FROM security_id_value THEN
              RAISE EXCEPTION 'Defensive asset input does not match its Allocation member';
            END IF;
          ELSIF NEW.input_role='reserve_accrual' THEN
            IF member_row.component_role IS DISTINCT FROM 'reserve' OR
               NEW.reserve_return_model_version_id IS DISTINCT FROM
                 context_row.reserve_return_model_version_id OR
               NEW.reserve_return_model_artifact_id IS DISTINCT FROM
                 context_row.reserve_return_model_artifact_id OR
               dataset_row.value_kind IS DISTINCT FROM 'reserve_return' OR
               NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=NEW.dataset_artifact_id
                    AND dependency.depends_on_artifact_id=
                        NEW.reserve_return_model_artifact_id
                    AND dependency.role='reserve_model'
               ) THEN
              RAISE EXCEPTION 'Reserve accrual input does not match its frozen identities';
            END IF;
          ELSE
            RAISE EXCEPTION 'Compiled Defense Data Input role is unsupported';
          END IF;"""


def _replace(source: str, target: str) -> None:
    op.execute(
        f"""
        DO $migration$
        DECLARE definition text;
        BEGIN
          SELECT pg_get_functiondef(
                   'defense.validate_v022_compiled_defense_execution_data_input()'
                   ::regprocedure
                 ) INTO definition;
          IF position($source${source}$source$ IN definition)=0 THEN
            RAISE EXCEPTION 'Expected Defense input validator fragment is absent';
          END IF;
          definition := replace(
            definition,
            $source${source}$source$,
            $target${target}$target$
          );
          EXECUTE definition;
        END
        $migration$;
        """
    )


def upgrade() -> None:
    _replace(_SOURCE, _TARGET)


def downgrade() -> None:
    _replace(_TARGET, _SOURCE)
