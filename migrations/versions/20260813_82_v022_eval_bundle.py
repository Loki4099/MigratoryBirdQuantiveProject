"""Freeze the exact Data Bundle in v0.22 Portfolio evaluation contexts.

Revision ID: 20260813_82_v022_eval_bundle
Revises: 20260812_81_v022_reserve_schema
"""

from __future__ import annotations

from alembic import op

revision = "20260813_82_v022_eval_bundle"
down_revision = "20260812_81_v022_reserve_schema"
branch_labels = None
depends_on = None


def _replace_validator_fragment(source: str, target: str) -> None:
    op.execute(
        f"""
        DO $migration$
        DECLARE definition text;
        BEGIN
          SELECT pg_get_functiondef(
                   'experiment.validate_v022_portfolio_evaluation_data_context()'::regprocedure
                 )
            INTO definition;
          IF position($source${source}$source$ IN definition)=0 THEN
            RAISE EXCEPTION 'Expected evaluation-context validator fragment is absent';
          END IF;
          definition := replace(definition, $source${source}$source$, $target${target}$target$);
          EXECUTE definition;
        END
        $migration$;
        """
    )


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE experiment.v022_portfolio_evaluation_data_context
          ADD COLUMN data_bundle_version_id uuid NULL
            REFERENCES data.data_bundle_version,
          ADD COLUMN data_bundle_artifact_id uuid NULL REFERENCES lineage.artifact,
          ADD CONSTRAINT ck_v022_portfolio_eval_bundle_identity_pair
            CHECK ((data_bundle_version_id IS NULL)=(data_bundle_artifact_id IS NULL));
        """
    )
    _replace_validator_fragment(
        "expected_dependency_count := CASE\n"
        "            WHEN NEW.reserve_calendar_artifact_id IS NULL THEN 5 ELSE 6 END;",
        "expected_dependency_count := CASE\n"
        "            WHEN NEW.reserve_calendar_artifact_id IS NULL THEN 6 ELSE 7 END;",
    )
    _replace_validator_fragment(
        "AND dependency.role='reserve_calendar' AND dependency.ordinal=5",
        "AND dependency.role='reserve_calendar' AND dependency.ordinal=6",
    )
    op.execute(
        """
        CREATE FUNCTION experiment.validate_v022_portfolio_evaluation_data_bundle()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE bundle_row record;
        BEGIN
          IF NEW.data_bundle_version_id IS NULL OR NEW.data_bundle_artifact_id IS NULL OR
             NEW.pit_document->>'data_bundle_version_id' IS DISTINCT FROM
               NEW.data_bundle_version_id::text OR
             NEW.pit_document->>'data_bundle_artifact_id' IS DISTINCT FROM
               NEW.data_bundle_artifact_id::text THEN
            RAISE EXCEPTION 'Portfolio Evaluation Data Context requires one exact Data Bundle';
          END IF;
          SELECT bundle.artifact_id,definition.bundle_key,
                 artifact.status AS artifact_status,
                 EXISTS (
                   SELECT 1 FROM data.data_bundle_member member
                    WHERE member.data_bundle_version_id=bundle.data_bundle_version_id
                      AND member.role='canonical_market'
                      AND member.dataset_publication_id=
                          NEW.benchmark_dataset_publication_id
                 ) AS has_market,
                 EXISTS (
                   SELECT 1 FROM data.data_bundle_member member
                    WHERE member.data_bundle_version_id=bundle.data_bundle_version_id
                      AND member.role='reserve_return'
                      AND member.dataset_publication_id=NEW.reserve_dataset_publication_id
                 ) AS has_reserve
            INTO bundle_row
            FROM data.data_bundle_version bundle
            JOIN data.data_bundle_definition definition
              ON definition.data_bundle_definition_id=bundle.data_bundle_definition_id
            JOIN lineage.artifact artifact ON artifact.artifact_id=bundle.artifact_id
           WHERE bundle.data_bundle_version_id=NEW.data_bundle_version_id;
          IF bundle_row.artifact_id IS DISTINCT FROM NEW.data_bundle_artifact_id OR
             bundle_row.bundle_key IS DISTINCT FROM 'us_style_daily_research_bundle' OR
             bundle_row.artifact_status IS DISTINCT FROM 'published' OR
             bundle_row.has_market IS DISTINCT FROM true OR
             bundle_row.has_reserve IS DISTINCT FROM true OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=NEW.data_bundle_artifact_id
                  AND dependency.role='evaluation_data_bundle'
                  AND dependency.ordinal=5
             ) THEN
            RAISE EXCEPTION
              'Portfolio Evaluation Data Context Data Bundle identity or lineage drifted';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_portfolio_evaluation_data_bundle_validate
          BEFORE INSERT ON experiment.v022_portfolio_evaluation_data_context
          FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_portfolio_evaluation_data_bundle();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM experiment.v022_portfolio_evaluation_data_context
             WHERE data_bundle_version_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'Cannot downgrade exact Evaluation Data Bundle identities';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS
          experiment.validate_v022_portfolio_evaluation_data_bundle() CASCADE;
        """
    )
    _replace_validator_fragment(
        "expected_dependency_count := CASE\n"
        "            WHEN NEW.reserve_calendar_artifact_id IS NULL THEN 6 ELSE 7 END;",
        "expected_dependency_count := CASE\n"
        "            WHEN NEW.reserve_calendar_artifact_id IS NULL THEN 5 ELSE 6 END;",
    )
    _replace_validator_fragment(
        "AND dependency.role='reserve_calendar' AND dependency.ordinal=6",
        "AND dependency.role='reserve_calendar' AND dependency.ordinal=5",
    )
    op.execute(
        """
        ALTER TABLE experiment.v022_portfolio_evaluation_data_context
          DROP CONSTRAINT ck_v022_portfolio_eval_bundle_identity_pair,
          DROP COLUMN data_bundle_artifact_id,
          DROP COLUMN data_bundle_version_id;
        """
    )
