# ruff: noqa: E501
"""Allow Suite Runtime Plans for Catalogs without composed defense.

Revision ID: 20260821_141_simple_runtime
Revises: 20260821_140_gate_import
"""

from __future__ import annotations

from alembic import op

revision = "20260821_141_simple_runtime"
down_revision = "20260821_140_gate_import"
branch_labels = None
depends_on = None


_PLAN_CATALOG_OLD = """IF graph_catalog_release_id IS DISTINCT FROM NEW.catalog_release_id OR
             catalog_release_status IS DISTINCT FROM 'published' OR
             NOT experiment.v022_graph_uses_composed_defense(
               NEW.compiled_research_graph_id
             ) THEN
            RAISE EXCEPTION 'Suite Runtime Plan requires its exact composed Graph Catalog Release';
          END IF;"""

_PLAN_CATALOG_NEW = """IF graph_catalog_release_id IS DISTINCT FROM NEW.catalog_release_id OR
             catalog_release_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Suite Runtime Plan requires its exact published Graph Catalog Release';
          END IF;"""

_PLAN_BINDING_OLD = """IF EXISTS (
            SELECT 1
              FROM experiment.v022_research_suite_branch suite_branch
              LEFT JOIN experiment.v022_configuration_execution_context_binding binding
                ON binding.configuration_snapshot_id=
                   suite_branch.configuration_snapshot_id
             WHERE suite_branch.research_suite_id=NEW.research_suite_id
               AND binding.configuration_snapshot_id IS NULL
          ) THEN
            RAISE EXCEPTION 'Suite Runtime Plan requires every Branch exact execution-context binding';
          END IF;"""

_PLAN_BINDING_NEW = """IF experiment.v022_graph_uses_composed_defense(
               NEW.compiled_research_graph_id
             ) AND EXISTS (
            SELECT 1
              FROM experiment.v022_research_suite_branch suite_branch
              LEFT JOIN experiment.v022_configuration_execution_context_binding binding
                ON binding.configuration_snapshot_id=
                   suite_branch.configuration_snapshot_id
             WHERE suite_branch.research_suite_id=NEW.research_suite_id
               AND binding.configuration_snapshot_id IS NULL
          ) THEN
            RAISE EXCEPTION 'Composed-defense Suite Runtime Plan requires every Branch exact execution-context binding';
          END IF;"""

_WORK_BINDING_OLD = """IF context_binding_row.compiled_execution_data_context_id IS DISTINCT FROM
               plan_row.compiled_execution_data_context_id THEN
            RAISE EXCEPTION 'Runtime Work Spec Risk Context drifted';
          END IF;"""

_WORK_BINDING_NEW = """IF experiment.v022_graph_uses_composed_defense(
               plan_row.compiled_research_graph_id
             ) THEN
            IF context_binding_row.compiled_execution_data_context_id IS DISTINCT FROM
                 plan_row.compiled_execution_data_context_id THEN
              RAISE EXCEPTION 'Runtime Work Spec Risk Context drifted';
            END IF;
          ELSIF EXISTS (
            SELECT 1
              FROM experiment.v022_configuration_execution_context_binding binding
             WHERE binding.configuration_snapshot_id=NEW.configuration_snapshot_id
          ) THEN
            RAISE EXCEPTION 'Simple Runtime Work Spec cannot bind a composed-defense execution context';
          END IF;"""


def _replace_function_fragment(
    *, function_name: str, old: str, new: str, error: str
) -> str:
    return f"""
        DO $patch$
        DECLARE definition text;
        BEGIN
          SELECT pg_get_functiondef('{function_name}'::regprocedure)
            INTO definition;
          IF position($old${old}$old$ in definition)=0 THEN
            RAISE EXCEPTION '{error}';
          END IF;
          definition := replace(definition,$old${old}$old$,$new${new}$new$);
          EXECUTE definition;
        END $patch$;
    """


def upgrade() -> None:
    op.execute(
        _replace_function_fragment(
            function_name="experiment.validate_v022_suite_runtime_plan()",
            old=_PLAN_CATALOG_OLD,
            new=_PLAN_CATALOG_NEW,
            error="M141 could not locate the exact Suite Runtime Plan Catalog guard",
        )
    )
    op.execute(
        _replace_function_fragment(
            function_name="experiment.validate_v022_suite_runtime_plan()",
            old=_PLAN_BINDING_OLD,
            new=_PLAN_BINDING_NEW,
            error="M141 could not locate the exact Suite Runtime Plan binding guard",
        )
    )
    op.execute(
        _replace_function_fragment(
            function_name="experiment.validate_v022_suite_runtime_work_spec()",
            old=_WORK_BINDING_OLD,
            new=_WORK_BINDING_NEW,
            error="M141 could not locate the exact Runtime Work Spec binding guard",
        )
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1
              FROM experiment.v022_suite_runtime_plan plan
             WHERE NOT experiment.v022_graph_uses_composed_defense(
               plan.compiled_research_graph_id
             )
          ) THEN
            RAISE EXCEPTION 'Cannot downgrade while simple Suite Runtime Plans exist';
          END IF;
        END $$;
        """
    )
    op.execute(
        _replace_function_fragment(
            function_name="experiment.validate_v022_suite_runtime_work_spec()",
            old=_WORK_BINDING_NEW,
            new=_WORK_BINDING_OLD,
            error="M141 could not restore the Runtime Work Spec binding guard",
        )
    )
    op.execute(
        _replace_function_fragment(
            function_name="experiment.validate_v022_suite_runtime_plan()",
            old=_PLAN_BINDING_NEW,
            new=_PLAN_BINDING_OLD,
            error="M141 could not restore the Suite Runtime Plan binding guard",
        )
    )
    op.execute(
        _replace_function_fragment(
            function_name="experiment.validate_v022_suite_runtime_plan()",
            old=_PLAN_CATALOG_NEW,
            new=_PLAN_CATALOG_OLD,
            error="M141 could not restore the Suite Runtime Plan Catalog guard",
        )
    )
