"""Derive immutable v0.22 Object Store strong roots from published evidence.

Revision ID: 20260818_118_v022_strong_root
Revises: 20260818_117_v022_cohort_recon
"""

from __future__ import annotations

from alembic import op

revision = "20260818_118_v022_strong_root"
down_revision = "20260818_117_v022_cohort_recon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW data.v022_strong_payload_manifest AS
        SELECT DISTINCT root.payload_manifest_id
          FROM (
            SELECT manifest.payload_manifest_id
              FROM data.payload_manifest manifest
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=manifest.artifact_id
               AND artifact.status='published'
             WHERE manifest.retention_class IN
                   ('product','evidence','export','legal_hold')

            UNION ALL

            SELECT result.payload_manifest_id
              FROM experiment.v022_result_evidence_snapshot evidence
              JOIN lineage.artifact evidence_artifact
                ON evidence_artifact.artifact_id=evidence.artifact_id
               AND evidence_artifact.status='published'
              JOIN experiment.v022_portfolio_cell_runtime_result result
                ON result.artifact_id=evidence.result_artifact_id
              JOIN lineage.artifact result_artifact
                ON result_artifact.artifact_id=result.artifact_id
               AND result_artifact.status='published'

            UNION ALL

            SELECT diagnostic.payload_manifest_id
              FROM experiment.v022_result_element_diagnostic diagnostic
              JOIN lineage.artifact diagnostic_artifact
                ON diagnostic_artifact.artifact_id=diagnostic.artifact_id
               AND diagnostic_artifact.status='published'
              JOIN experiment.v022_result_evidence_snapshot evidence
                ON evidence.result_artifact_id=diagnostic.result_artifact_id
              JOIN lineage.artifact evidence_artifact
                ON evidence_artifact.artifact_id=evidence.artifact_id
               AND evidence_artifact.status='published'

            UNION ALL

            SELECT binding.payload_manifest_id
              FROM data.v022_product_input_payload_binding binding
              JOIN product.v022_product_input_snapshot snapshot
                ON snapshot.product_input_snapshot_id=
                   binding.product_input_snapshot_id
              JOIN lineage.artifact snapshot_artifact
                ON snapshot_artifact.artifact_id=snapshot.artifact_id
               AND snapshot_artifact.status='published'
          ) root
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW data.v022_strong_payload_manifest")
