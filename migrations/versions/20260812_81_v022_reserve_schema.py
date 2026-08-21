"""Correct the v0.22 Portfolio evaluation Reserve projection schema.

Revision ID: 20260812_81_v022_reserve_schema
Revises: 20260812_80_v022_representative
"""

from __future__ import annotations

from alembic import op

revision = "20260812_81_v022_reserve_schema"
down_revision = "20260812_80_v022_representative"
branch_labels = None
depends_on = None


def _replace_reserve_schema(source: str, target: str) -> None:
    op.execute(
        f"""
        DO $migration$
        DECLARE definition text;
        BEGIN
          SELECT pg_get_functiondef(
                   'experiment.validate_v022_portfolio_evaluation_data_context()'::regprocedure
                 )
            INTO definition;
          IF position('FROM {source}.reserve_return reserve' IN definition)=0 THEN
            RAISE EXCEPTION 'Expected Reserve relation is absent from evaluation validator';
          END IF;
          definition := replace(
            definition,
            'FROM {source}.reserve_return reserve',
            'FROM {target}.reserve_return reserve'
          );
          EXECUTE definition;
        END
        $migration$;
        """
    )


def upgrade() -> None:
    _replace_reserve_schema("experiment", "data")


def downgrade() -> None:
    _replace_reserve_schema("data", "experiment")
