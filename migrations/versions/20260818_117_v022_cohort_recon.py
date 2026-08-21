# ruff: noqa: E501
"""Allow Evaluation Cohorts to consume an exactly reconciled market Dataset.

Revision ID: 20260818_117_v022_cohort_recon
Revises: 20260818_116_v022_recon_guard
"""

from __future__ import annotations

from alembic import op

revision = "20260818_117_v022_cohort_recon"
down_revision = "20260818_116_v022_recon_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(_validator(allow_reconciled=True))


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM experiment.v022_evaluation_cohort_version cohort
            JOIN data.v022_reconciled_market_dataset_binding binding
              ON binding.dataset_publication_id=cohort.dataset_publication_id
          ) THEN
            RAISE EXCEPTION 'Cannot downgrade with reconciled Evaluation Cohorts';
          END IF;
        END $$;
        """
    )
    op.execute(_validator(allow_reconciled=False))


def _validator(*, allow_reconciled: bool) -> str:
    if allow_reconciled:
        market_binding_guard = """
        NOT (
          EXISTS (
            SELECT 1 FROM data.v022_security_market_dataset_binding binding
             WHERE binding.dataset_publication_id=NEW.dataset_publication_id
               AND binding.dataset_artifact_id=NEW.dataset_artifact_id
               AND binding.security_market_quality_report_id=
                   NEW.security_market_quality_report_id
               AND binding.quality_report_artifact_id=NEW.quality_report_artifact_id
          ) OR EXISTS (
            SELECT 1 FROM data.v022_reconciled_market_dataset_binding reconciled
            JOIN data.v022_security_market_dataset_binding primary_binding
              ON primary_binding.dataset_publication_id=
                 reconciled.primary_dataset_publication_id
             WHERE reconciled.dataset_publication_id=NEW.dataset_publication_id
               AND reconciled.dataset_artifact_id=NEW.dataset_artifact_id
               AND primary_binding.security_market_quality_report_id=
                   NEW.security_market_quality_report_id
               AND primary_binding.quality_report_artifact_id=
                   NEW.quality_report_artifact_id
          )
        )
        """
    else:
        market_binding_guard = """
        NOT EXISTS (
          SELECT 1 FROM data.v022_security_market_dataset_binding binding
           WHERE binding.dataset_publication_id=NEW.dataset_publication_id
             AND binding.dataset_artifact_id=NEW.dataset_artifact_id
             AND binding.security_market_quality_report_id=
                 NEW.security_market_quality_report_id
             AND binding.quality_report_artifact_id=NEW.quality_report_artifact_id
        )
        """
    return f"""
    CREATE OR REPLACE FUNCTION experiment.validate_v022_evaluation_cohort_version()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE artifact_row record; history_row record; dataset_row record;
            benchmark_row record; report_row record; calendar_row record;
    BEGIN
      PERFORM data.assert_artifact_draft(NEW.artifact_id);
      SELECT artifact_type,artifact_key,version_number INTO artifact_row
        FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
      SELECT history.artifact_id,artifact.status,ledger.research_tier,
             (SELECT count(*)
                FROM catalog.v022_universe_change_batch batch
               WHERE batch.universe_membership_ledger_id=
                     ledger.universe_membership_ledger_id
                 AND batch.evidence_status<>'confirmed') AS unresolved_count
        INTO history_row
        FROM catalog.universe_history history
        JOIN lineage.artifact artifact ON artifact.artifact_id=history.artifact_id
        JOIN catalog.v022_universe_history_ledger_binding history_binding
          ON history_binding.universe_history_id=history.universe_history_id
        JOIN catalog.v022_universe_membership_ledger ledger
          ON ledger.universe_membership_ledger_id=
             history_binding.universe_membership_ledger_id
       WHERE history.universe_history_id=NEW.universe_history_id;
      SELECT publication.artifact_id,publication.calendar_version_id,
             publication.coverage_start,publication.coverage_end,artifact.status
        INTO dataset_row FROM data.dataset_publication publication
        JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
       WHERE publication.dataset_publication_id=NEW.dataset_publication_id;
      SELECT publication.artifact_id,publication.calendar_version_id,
             publication.coverage_start,publication.coverage_end,artifact.status,
             EXISTS (
               SELECT 1 FROM data.daily_bar bar
               JOIN catalog.asset asset ON asset.asset_id=bar.asset_id
              WHERE bar.dataset_publication_id=publication.dataset_publication_id
                AND asset.asset_key='spy'
                AND bar.session_date BETWEEN NEW.warmup_start AND NEW.evaluation_end
             ) AS has_spy
        INTO benchmark_row FROM data.dataset_publication publication
        JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
       WHERE publication.dataset_publication_id=NEW.benchmark_dataset_publication_id;
      SELECT report.artifact_id,report.error_count,report.research_tier,artifact.status
        INTO report_row FROM data.v022_security_market_quality_report report
        JOIN lineage.artifact artifact ON artifact.artifact_id=report.artifact_id
       WHERE report.security_market_quality_report_id=
             NEW.security_market_quality_report_id;
      SELECT version.artifact_id,artifact.status INTO calendar_row
        FROM catalog.calendar_version version
        JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
       WHERE version.calendar_version_id=NEW.calendar_version_id;
      IF artifact_row.artifact_type IS DISTINCT FROM 'v022_evaluation_cohort_version' OR
         artifact_row.artifact_key IS DISTINCT FROM
           'v022_evaluation_cohort_version__' || NEW.cohort_key OR
         artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
         history_row.artifact_id IS DISTINCT FROM NEW.universe_history_artifact_id OR
         history_row.status IS DISTINCT FROM 'published' OR
         (NEW.research_tier='rankable_research' AND
           (history_row.research_tier IS DISTINCT FROM 'rankable_research' OR
            history_row.unresolved_count<>0)) OR
         dataset_row.artifact_id IS DISTINCT FROM NEW.dataset_artifact_id OR
         dataset_row.status IS DISTINCT FROM 'published' OR
         dataset_row.calendar_version_id IS DISTINCT FROM NEW.calendar_version_id OR
         dataset_row.coverage_start>NEW.warmup_start OR
         dataset_row.coverage_end<NEW.evaluation_end OR
         NEW.benchmark_dataset_publication_id IS NULL OR
         NEW.benchmark_dataset_artifact_id IS NULL OR
         benchmark_row.artifact_id IS DISTINCT FROM NEW.benchmark_dataset_artifact_id OR
         benchmark_row.status IS DISTINCT FROM 'published' OR
         benchmark_row.calendar_version_id IS DISTINCT FROM NEW.calendar_version_id OR
         benchmark_row.coverage_start>NEW.warmup_start OR
         benchmark_row.coverage_end<NEW.evaluation_end OR
         benchmark_row.has_spy IS DISTINCT FROM true OR
         report_row.artifact_id IS DISTINCT FROM NEW.quality_report_artifact_id OR
         report_row.status IS DISTINCT FROM 'published' OR
         report_row.error_count IS DISTINCT FROM 0 OR
         (NEW.research_tier='rankable_research' AND
           report_row.research_tier IS DISTINCT FROM 'rankable_research') OR
         calendar_row.artifact_id IS DISTINCT FROM NEW.calendar_artifact_id OR
         calendar_row.status IS DISTINCT FROM 'published' OR
         {market_binding_guard} OR
         (SELECT count(*) FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id)<>5 OR
         NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id
             AND dependency.depends_on_artifact_id=NEW.universe_history_artifact_id
             AND dependency.role='universe_history' AND dependency.ordinal=0) OR
         NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id
             AND dependency.depends_on_artifact_id=NEW.dataset_artifact_id
             AND dependency.role='market_dataset' AND dependency.ordinal=1) OR
         NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id
             AND dependency.depends_on_artifact_id=NEW.quality_report_artifact_id
             AND dependency.role='quality_report' AND dependency.ordinal=2) OR
         NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id
             AND dependency.depends_on_artifact_id=NEW.calendar_artifact_id
             AND dependency.role='calendar_version' AND dependency.ordinal=3) OR
         NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id
             AND dependency.depends_on_artifact_id=NEW.benchmark_dataset_artifact_id
             AND dependency.role='benchmark_dataset' AND dependency.ordinal=4) THEN
        RAISE EXCEPTION 'Evaluation Cohort requires exact risk and SPY frozen inputs';
      END IF;
      RETURN NEW;
    END $$;
    """
