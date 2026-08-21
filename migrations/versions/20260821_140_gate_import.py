# ruff: noqa: E501
"""Allow Dataset Gate Assessments to bind import-native quality lineage.

Revision ID: 20260821_140_gate_import
Revises: 20260821_139_cohort_import
"""

from __future__ import annotations

from alembic import op

revision = "20260821_140_gate_import"
down_revision = "20260821_139_cohort_import"
branch_labels = None
depends_on = None


def _source_view(include_imported: bool) -> str:
    imported = """
        UNION ALL
        SELECT report.source_dataset_publication_id AS dataset_publication_id,
               report.report_document->>'price_semantics' AS price_semantics,
               report.security_market_quality_report_id,
               report.artifact_id AS quality_report_artifact_id
          FROM data.v022_security_market_quality_report report
          JOIN lineage.artifact artifact ON artifact.artifact_id=report.artifact_id
         WHERE report.source_dataset_publication_id IS NOT NULL
           AND report.source_dataset_artifact_id IS NOT NULL
           AND report.external_import_manifest_id IS NOT NULL
           AND report.external_import_manifest_artifact_id IS NOT NULL
           AND artifact.status='published'
           AND data.v022_imported_quality_report_binds_dataset(
                 report.security_market_quality_report_id,report.artifact_id,
                 report.source_dataset_publication_id,
                 report.source_dataset_artifact_id)
           AND NOT EXISTS (
                 SELECT 1
                   FROM data.v022_security_market_dataset_binding legacy
                  WHERE legacy.dataset_publication_id=report.source_dataset_publication_id)
    """ if include_imported else ""
    return f"""
        CREATE OR REPLACE VIEW data.v022_dataset_gate_source_binding AS
        SELECT publication.dataset_publication_id,
               COALESCE(reconciled.price_semantics,market.price_semantics)::text
                 AS price_semantics,
               market.security_market_quality_report_id,
               market.quality_report_artifact_id
          FROM data.dataset_publication publication
          LEFT JOIN data.v022_reconciled_market_dataset_binding reconciled
            ON reconciled.dataset_publication_id=publication.dataset_publication_id
          JOIN data.v022_security_market_dataset_binding market
            ON market.dataset_publication_id=COALESCE(
                 reconciled.primary_dataset_publication_id,
                 publication.dataset_publication_id)
        {imported};
    """


def _validator(*, use_source_view: bool) -> str:
    source_lookup = (
        """
          SELECT source.price_semantics,source.security_market_quality_report_id,
                 source.quality_report_artifact_id
            INTO inferred_semantics,inferred_report_id,inferred_report_artifact
            FROM data.v022_dataset_gate_source_binding source
           WHERE source.dataset_publication_id=NEW.dataset_publication_id
             AND source.security_market_quality_report_id=
                 NEW.security_market_quality_report_id;
        """
        if use_source_view
        else """
          SELECT COALESCE(reconciled.price_semantics,market.price_semantics),
                 market.security_market_quality_report_id,
                 market.quality_report_artifact_id
            INTO inferred_semantics,inferred_report_id,inferred_report_artifact
            FROM data.dataset_publication publication
            LEFT JOIN data.v022_reconciled_market_dataset_binding reconciled
              ON reconciled.dataset_publication_id=publication.dataset_publication_id
            LEFT JOIN data.v022_security_market_dataset_binding market
              ON market.dataset_publication_id=COALESCE(
                   reconciled.primary_dataset_publication_id,
                   publication.dataset_publication_id)
           WHERE publication.dataset_publication_id=NEW.dataset_publication_id;
        """
    )
    return f"""
        CREATE OR REPLACE FUNCTION data.validate_v022_dataset_gate_assessment()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; dataset_row record; ledger_row record;
                history_row record; report_row record; calendar_row record;
                cleaning_row record; inferred_semantics varchar;
                inferred_report_id uuid; inferred_report_artifact uuid;
                fixed_dependencies integer;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT publication.artifact_id,publication.cleaning_version_id,
                 publication.calendar_version_id,publication.dataset_kind,
                 publication.value_kind,publication.coverage_start,publication.coverage_end,
                 artifact.status INTO dataset_row
            FROM data.dataset_publication publication
            JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
           WHERE publication.dataset_publication_id=NEW.dataset_publication_id;
          SELECT ledger.artifact_id,ledger.coverage_start,ledger.coverage_end,artifact.status
            INTO ledger_row FROM catalog.v022_universe_membership_ledger ledger
            JOIN lineage.artifact artifact ON artifact.artifact_id=ledger.artifact_id
           WHERE ledger.universe_membership_ledger_id=NEW.universe_membership_ledger_id;
          SELECT binding.universe_history_id,binding.universe_history_artifact_id,
                 artifact.status INTO history_row
            FROM catalog.v022_universe_history_ledger_binding binding
            JOIN lineage.artifact artifact
              ON artifact.artifact_id=binding.universe_history_artifact_id
           WHERE binding.universe_membership_ledger_id=NEW.universe_membership_ledger_id;
          SELECT report.artifact_id,artifact.status INTO report_row
            FROM data.v022_security_market_quality_report report
            JOIN lineage.artifact artifact ON artifact.artifact_id=report.artifact_id
           WHERE report.security_market_quality_report_id=
                 NEW.security_market_quality_report_id;
          SELECT version.artifact_id,version.coverage_start,version.coverage_end,
                 artifact.status INTO calendar_row
            FROM catalog.calendar_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.calendar_version_id=NEW.calendar_version_id;
          SELECT version.artifact_id,artifact.status INTO cleaning_row
            FROM data.cleaning_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.cleaning_version_id=NEW.cleaning_version_id;
          {source_lookup}
          IF artifact_row.artifact_type IS DISTINCT FROM 'v022_dataset_gate_assessment' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_dataset_gate_assessment__' || NEW.gate_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
             dataset_row.status IS DISTINCT FROM 'published' OR
             dataset_row.artifact_id IS DISTINCT FROM NEW.dataset_artifact_id OR
             dataset_row.dataset_kind IS DISTINCT FROM 'canonical' OR
             dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
             dataset_row.cleaning_version_id IS DISTINCT FROM NEW.cleaning_version_id OR
             dataset_row.calendar_version_id IS DISTINCT FROM NEW.calendar_version_id OR
             dataset_row.coverage_start>NEW.assessed_coverage_start OR
             dataset_row.coverage_end<NEW.assessed_coverage_end OR
             ledger_row.status IS DISTINCT FROM 'published' OR
             ledger_row.artifact_id IS DISTINCT FROM
               NEW.universe_membership_ledger_artifact_id OR
             ledger_row.coverage_start>NEW.assessed_coverage_start OR
             ledger_row.coverage_end<NEW.assessed_coverage_end OR
             history_row.status IS DISTINCT FROM 'published' OR
             history_row.universe_history_id IS DISTINCT FROM NEW.universe_history_id OR
             history_row.universe_history_artifact_id IS DISTINCT FROM
               NEW.universe_history_artifact_id OR
             report_row.status IS DISTINCT FROM 'published' OR
             report_row.artifact_id IS DISTINCT FROM NEW.quality_report_artifact_id OR
             inferred_report_id IS DISTINCT FROM NEW.security_market_quality_report_id OR
             inferred_report_artifact IS DISTINCT FROM NEW.quality_report_artifact_id OR
             calendar_row.status IS DISTINCT FROM 'published' OR
             calendar_row.artifact_id IS DISTINCT FROM NEW.calendar_artifact_id OR
             calendar_row.coverage_start>NEW.assessed_coverage_start OR
             calendar_row.coverage_end<NEW.assessed_coverage_end OR
             cleaning_row.status IS DISTINCT FROM 'published' OR
             cleaning_row.artifact_id IS DISTINCT FROM NEW.cleaning_artifact_id OR
             inferred_semantics IS NULL OR inferred_semantics IS DISTINCT FROM NEW.price_semantics
          THEN
            RAISE EXCEPTION 'Dataset Gate Assessment inputs are not exact or published';
          END IF;
          SELECT count(*) INTO fixed_dependencies FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id AND (
             (dependency.depends_on_artifact_id=NEW.dataset_artifact_id AND
              dependency.role='market_dataset' AND dependency.ordinal=0) OR
             (dependency.depends_on_artifact_id=NEW.universe_membership_ledger_artifact_id AND
              dependency.role='universe_ledger' AND dependency.ordinal=1) OR
             (dependency.depends_on_artifact_id=NEW.universe_history_artifact_id AND
              dependency.role='universe_history' AND dependency.ordinal=2) OR
             (dependency.depends_on_artifact_id=NEW.quality_report_artifact_id AND
              dependency.role='quality_report' AND dependency.ordinal=3) OR
             (dependency.depends_on_artifact_id=NEW.calendar_artifact_id AND
              dependency.role='calendar_version' AND dependency.ordinal=4) OR
             (dependency.depends_on_artifact_id=NEW.cleaning_artifact_id AND
              dependency.role='cleaning_version' AND dependency.ordinal=5)
           );
          IF fixed_dependencies<>6 OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>NEW.evidence_count+6 THEN
            RAISE EXCEPTION 'Dataset Gate Assessment lineage is incomplete';
          END IF;
          RETURN NEW;
        END $$;
    """


def upgrade() -> None:
    op.execute(_source_view(include_imported=True))
    op.execute(_validator(use_source_view=True))


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1
              FROM data.v022_dataset_gate_assessment assessment
              JOIN data.v022_security_market_quality_report report
                ON report.security_market_quality_report_id=
                   assessment.security_market_quality_report_id
             WHERE report.source_dataset_publication_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'Cannot downgrade import-native Dataset Gate Assessments';
          END IF;
        END $$;
        """
    )
    # Restore the pre-140 validator before removing the helper view introduced
    # by this revision. Leaving the view behind blocks later downgrades when
    # migration 104 drops its reconciliation binding table.
    op.execute(_validator(use_source_view=False))
    op.execute("DROP VIEW data.v022_dataset_gate_source_binding")
