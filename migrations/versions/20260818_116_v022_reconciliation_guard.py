# ruff: noqa: E501
"""Allow reviewed non-alternate market gap resolutions.

Revision ID: 20260818_116_v022_recon_guard
Revises: 20260817_115_v022_restore
"""

from __future__ import annotations

from alembic import op

revision = "20260818_116_v022_recon_guard"
down_revision = "20260817_115_v022_restore"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(_fixed_guard())


def downgrade() -> None:
    op.execute(_legacy_guard())


def _fixed_guard() -> str:
    return """
    CREATE OR REPLACE FUNCTION data.validate_v022_market_gap_resolution()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE artifact_row record; dataset_row record; alternate_row record;
            prior_row record;
    BEGIN
      PERFORM data.assert_artifact_draft(NEW.artifact_id);
      SELECT artifact_type,artifact_key,version_number INTO artifact_row
        FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
      SELECT publication.artifact_id,publication.value_kind,artifact.status
        INTO dataset_row FROM data.dataset_publication publication
        JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
       WHERE publication.dataset_publication_id=NEW.primary_dataset_publication_id;
      IF artifact_row.artifact_type IS DISTINCT FROM 'v022_market_gap_resolution' OR
         artifact_row.artifact_key IS DISTINCT FROM
           'v022_market_gap_resolution__' || NEW.gap_key OR
         artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
         dataset_row.status IS DISTINCT FROM 'published' OR
         dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
         dataset_row.artifact_id IS DISTINCT FROM NEW.primary_dataset_artifact_id THEN
        RAISE EXCEPTION 'Market Gap Resolution identity is incomplete';
      END IF;
      IF NEW.resolution_kind='replace_with_alternate' THEN
        SELECT item.artifact_id,item.security_id,item.coverage_start,item.coverage_end,
               artifact.status INTO alternate_row
          FROM data.v022_alternate_observation_set item
          JOIN lineage.artifact artifact ON artifact.artifact_id=item.artifact_id
         WHERE item.alternate_observation_set_id=NEW.alternate_observation_set_id;
        IF alternate_row.status IS DISTINCT FROM 'published' OR
           alternate_row.artifact_id IS DISTINCT FROM
             NEW.alternate_observation_artifact_id OR
           alternate_row.security_id IS DISTINCT FROM NEW.security_id OR
           alternate_row.coverage_start>NEW.gap_end OR
           alternate_row.coverage_end<NEW.gap_start THEN
          RAISE EXCEPTION 'Market Gap Resolution alternate observation is incomplete';
        END IF;
      END IF;
      IF NEW.version_number=1 AND
         NEW.supersedes_market_gap_resolution_id IS NOT NULL THEN
        RAISE EXCEPTION 'First Gap Resolution version cannot supersede another';
      ELSIF NEW.version_number>1 THEN
        SELECT item.primary_dataset_publication_id,item.gap_key,item.version_number,
               artifact.status INTO prior_row
          FROM data.v022_market_gap_resolution item
          JOIN lineage.artifact artifact ON artifact.artifact_id=item.artifact_id
         WHERE item.market_gap_resolution_id=
               NEW.supersedes_market_gap_resolution_id;
        IF prior_row.status IS DISTINCT FROM 'published' OR
           prior_row.primary_dataset_publication_id IS DISTINCT FROM
             NEW.primary_dataset_publication_id OR
           prior_row.gap_key IS DISTINCT FROM NEW.gap_key OR
           prior_row.version_number IS DISTINCT FROM NEW.version_number-1 THEN
          RAISE EXCEPTION 'Gap Resolution supersession is not exact';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    """


def _legacy_guard() -> str:
    return """
    CREATE OR REPLACE FUNCTION data.validate_v022_market_gap_resolution()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE artifact_row record; dataset_row record; alternate_row record;
            prior_row record;
    BEGIN
      PERFORM data.assert_artifact_draft(NEW.artifact_id);
      SELECT artifact_type,artifact_key,version_number INTO artifact_row
        FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
      SELECT publication.artifact_id,publication.value_kind,artifact.status
        INTO dataset_row FROM data.dataset_publication publication
        JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
       WHERE publication.dataset_publication_id=NEW.primary_dataset_publication_id;
      IF NEW.alternate_observation_set_id IS NOT NULL THEN
        SELECT item.artifact_id,item.security_id,item.coverage_start,item.coverage_end,
               artifact.status INTO alternate_row
          FROM data.v022_alternate_observation_set item
          JOIN lineage.artifact artifact ON artifact.artifact_id=item.artifact_id
         WHERE item.alternate_observation_set_id=NEW.alternate_observation_set_id;
      END IF;
      IF artifact_row.artifact_type IS DISTINCT FROM 'v022_market_gap_resolution' OR
         artifact_row.artifact_key IS DISTINCT FROM
           'v022_market_gap_resolution__' || NEW.gap_key OR
         artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
         dataset_row.status IS DISTINCT FROM 'published' OR
         dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
         dataset_row.artifact_id IS DISTINCT FROM NEW.primary_dataset_artifact_id OR
         (NEW.resolution_kind='replace_with_alternate' AND (
           alternate_row.status IS DISTINCT FROM 'published' OR
           alternate_row.artifact_id IS DISTINCT FROM
             NEW.alternate_observation_artifact_id OR
           alternate_row.security_id IS DISTINCT FROM NEW.security_id OR
           alternate_row.coverage_start>NEW.gap_end OR
           alternate_row.coverage_end<NEW.gap_start
         )) THEN
        RAISE EXCEPTION 'Market Gap Resolution identity is incomplete';
      END IF;
      IF NEW.version_number=1 AND
         NEW.supersedes_market_gap_resolution_id IS NOT NULL THEN
        RAISE EXCEPTION 'First Gap Resolution version cannot supersede another';
      ELSIF NEW.version_number>1 THEN
        SELECT item.primary_dataset_publication_id,item.gap_key,item.version_number,
               artifact.status INTO prior_row
          FROM data.v022_market_gap_resolution item
          JOIN lineage.artifact artifact ON artifact.artifact_id=item.artifact_id
         WHERE item.market_gap_resolution_id=
               NEW.supersedes_market_gap_resolution_id;
        IF prior_row.status IS DISTINCT FROM 'published' OR
           prior_row.primary_dataset_publication_id IS DISTINCT FROM
             NEW.primary_dataset_publication_id OR
           prior_row.gap_key IS DISTINCT FROM NEW.gap_key OR
           prior_row.version_number IS DISTINCT FROM NEW.version_number-1 THEN
          RAISE EXCEPTION 'Gap Resolution supersession is not exact';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    """
