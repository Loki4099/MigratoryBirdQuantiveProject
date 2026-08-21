"""Use the exact common interval for composed Defense inputs.

Revision ID: 20260814_91_v022_def_coverage
Revises: 20260814_90_v022_compile_ptr
"""

from __future__ import annotations

from alembic import op

revision = "20260814_91_v022_def_coverage"
down_revision = "20260814_90_v022_compile_ptr"
branch_labels = None
depends_on = None


_STRICT_COVERAGE = """                    input.coverage_start>risk_coverage_start_value OR
                    input.coverage_end<risk_coverage_end_value"""

_COMMON_INTERVAL_COVERAGE = """                    (
                      input.input_role<>'reserve_accrual' AND (
                        input.coverage_start>risk_coverage_start_value OR
                        input.coverage_end<risk_coverage_end_value
                      )
                    ) OR (
                      input.input_role='reserve_accrual' AND (
                        input.coverage_end<risk_coverage_start_value OR
                        input.coverage_start>risk_coverage_end_value
                      )
                    )"""


def _replace_guard_fragment(old: str, new: str) -> None:
    escaped_old = old.replace("'", "''")
    escaped_new = new.replace("'", "''")
    op.execute(
        f"""
        DO $migration$
        DECLARE definition text; replaced text;
        BEGIN
          SELECT pg_get_functiondef(
                   'defense.validate_v022_compiled_defense_execution_context_complete()'
                   ::regprocedure
                 )
            INTO definition;
          IF definition IS NULL OR
             length(definition)-length(replace(definition,'{escaped_old}',''))<>
               length('{escaped_old}') THEN
            RAISE EXCEPTION 'Unexpected Defense execution context guard definition';
          END IF;
          replaced := replace(definition,'{escaped_old}','{escaped_new}');
          EXECUTE replaced;
        END
        $migration$;
        """
    )


def upgrade() -> None:
    _replace_guard_fragment(_STRICT_COVERAGE, _COMMON_INTERVAL_COVERAGE)


def downgrade() -> None:
    _replace_guard_fragment(_COMMON_INTERVAL_COVERAGE, _STRICT_COVERAGE)
