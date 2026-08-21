"""Qualify the Evaluation Cohort Artifact in the Ranking release guard.

Revision ID: 20260817_109_v022_ranking_guard
Revises: 20260817_108_v022_cohort_bench
"""

from __future__ import annotations

from alembic import op

revision = "20260817_109_v022_ranking_guard"
down_revision = "20260817_108_v022_cohort_bench"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _replace_guard(qualified=True)


def downgrade() -> None:
    _replace_guard(qualified=False)


def _replace_guard(*, qualified: bool) -> None:
    cohort_artifact = "cohort.artifact_id" if qualified else "artifact_id"
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION experiment.validate_v022_ranking_cohort_release()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; cohort_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT {cohort_artifact},cohort_key,cohort_fingerprint,frequency,research_tier,
                 artifact.status INTO cohort_row
            FROM experiment.v022_evaluation_cohort_version cohort
            JOIN lineage.artifact artifact ON artifact.artifact_id=cohort.artifact_id
           WHERE cohort.evaluation_cohort_version_id=NEW.evaluation_cohort_version_id;
          IF artifact_row.artifact_type IS DISTINCT FROM 'v022_ranking_cohort_release' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_ranking_cohort__' || cohort_row.cohort_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
             cohort_row.status IS DISTINCT FROM 'published' OR
             cohort_row.research_tier IS DISTINCT FROM 'rankable_research' OR
             NEW.evaluation_cohort_artifact_id IS DISTINCT FROM cohort_row.artifact_id OR
             NEW.evaluation_cohort_fingerprint IS DISTINCT FROM
               cohort_row.cohort_fingerprint OR
             NEW.cohort_key IS DISTINCT FROM cohort_row.cohort_key OR
             NEW.frequency IS DISTINCT FROM cohort_row.frequency OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>NEW.member_count+1 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=cohort_row.artifact_id
                 AND dependency.role='evaluation_cohort' AND dependency.ordinal=0) THEN
            RAISE EXCEPTION 'Ranking Cohort Release requires its exact rankable Evaluation Cohort';
          END IF;
          RETURN NEW;
        END $$;
        """
    )
