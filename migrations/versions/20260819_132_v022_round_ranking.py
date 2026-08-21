# ruff: noqa: E501
"""Bind immutable Ranking Cohort releases to one Research Round.

Revision ID: 20260819_132_v022_round_ranking
Revises: 20260819_131_v022_research_round
"""

from __future__ import annotations

from alembic import op

revision = "20260819_132_v022_round_ranking"
down_revision = "20260819_131_v022_research_round"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_ranking_cohort_release_round (
          ranking_cohort_release_id uuid PRIMARY KEY
            REFERENCES experiment.v022_ranking_cohort_release(ranking_cohort_release_id),
          research_round_id uuid NOT NULL
            REFERENCES workspace.v022_research_round(research_round_id),
          bound_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_v022_ranking_release_round
          ON experiment.v022_ranking_cohort_release_round(research_round_id);
        CREATE TRIGGER reject_v022_ranking_release_round_mutation
          BEFORE UPDATE OR DELETE ON experiment.v022_ranking_cohort_release_round
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM experiment.v022_ranking_cohort_release_round) THEN
            RAISE EXCEPTION 'Cannot downgrade nonempty v0.22 Round Ranking bindings';
          END IF;
        END $$;
        DROP TRIGGER reject_v022_ranking_release_round_mutation
          ON experiment.v022_ranking_cohort_release_round;
        DROP INDEX experiment.ix_v022_ranking_release_round;
        DROP TABLE experiment.v022_ranking_cohort_release_round;
        """
    )
