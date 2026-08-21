# ruff: noqa: E501
"""Add immutable v0.22 ranking Cohort releases.

Revision ID: 20260816_101_v022_ranking
Revises: 20260816_100_v022_evidence
"""

from __future__ import annotations

from alembic import op

revision = "20260816_101_v022_ranking"
down_revision = "20260816_100_v022_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_ranking_cohort_release (
          ranking_cohort_release_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          evaluation_cohort_version_id uuid NOT NULL
            REFERENCES experiment.v022_evaluation_cohort_version,
          evaluation_cohort_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          evaluation_cohort_fingerprint varchar(64) NOT NULL
            CHECK (evaluation_cohort_fingerprint ~ '^[0-9a-f]{64}$'),
          cohort_key varchar(200) NOT NULL CHECK (btrim(cohort_key)<>''),
          frequency varchar(16) NOT NULL CHECK (frequency IN ('weekly','monthly')),
          version_number integer NOT NULL CHECK (version_number>=1),
          member_count integer NOT NULL CHECK (member_count>0),
          release_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (release_fingerprint ~ '^[0-9a-f]{64}$'),
          released_by varchar(160) NOT NULL CHECK (btrim(released_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (evaluation_cohort_version_id,version_number)
        );

        CREATE TABLE experiment.v022_ranking_cohort_member (
          ranking_cohort_release_id uuid NOT NULL
            REFERENCES experiment.v022_ranking_cohort_release,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          result_evidence_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_result_evidence_snapshot,
          result_evidence_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          result_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          cagr numeric NULL,
          benchmark_cagr numeric NULL,
          cagr_spread numeric NULL,
          sharpe_ratio numeric NULL,
          maximum_drawdown numeric NULL,
          member_fingerprint varchar(64) NOT NULL
            CHECK (member_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (ranking_cohort_release_id,ordinal),
          UNIQUE (ranking_cohort_release_id,result_evidence_snapshot_id),
          UNIQUE (ranking_cohort_release_id,member_fingerprint)
        );

        CREATE FUNCTION experiment.validate_v022_ranking_cohort_release()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; cohort_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT artifact_id,cohort_key,cohort_fingerprint,frequency,research_tier,
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
        CREATE TRIGGER trg_v022_ranking_cohort_release_validate
          BEFORE INSERT ON experiment.v022_ranking_cohort_release
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_ranking_cohort_release();

        CREATE FUNCTION experiment.validate_v022_ranking_cohort_member()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE release_row record; evidence_row record;
                expected_cagr numeric; expected_spread numeric;
                expected_sharpe numeric; expected_drawdown numeric;
        BEGIN
          SELECT artifact_id,evaluation_cohort_version_id INTO release_row
            FROM experiment.v022_ranking_cohort_release
           WHERE ranking_cohort_release_id=NEW.ranking_cohort_release_id;
          PERFORM data.assert_artifact_draft(release_row.artifact_id);
          SELECT evidence.artifact_id,evidence.result_artifact_id,
                 evidence.configuration_snapshot_id,evidence.evaluation_cohort_version_id,
                 evidence.quality_document,artifact.status INTO evidence_row
            FROM experiment.v022_result_evidence_snapshot evidence
            JOIN lineage.artifact artifact ON artifact.artifact_id=evidence.artifact_id
           WHERE evidence.result_evidence_snapshot_id=NEW.result_evidence_snapshot_id;
          SELECT (metric->>'value')::numeric INTO expected_cagr
            FROM jsonb_array_elements(
              evidence_row.quality_document->'metric_document'->'absolute_metrics') metric
           WHERE metric->>'metric_key'='cagr';
          SELECT (metric->>'value')::numeric INTO expected_sharpe
            FROM jsonb_array_elements(
              evidence_row.quality_document->'metric_document'->'absolute_metrics') metric
           WHERE metric->>'metric_key'='sharpe_ratio';
          SELECT (metric->>'value')::numeric INTO expected_drawdown
            FROM jsonb_array_elements(
              evidence_row.quality_document->'metric_document'->'absolute_metrics') metric
           WHERE metric->>'metric_key'='maximum_drawdown';
          SELECT (metric->>'value')::numeric INTO expected_spread
            FROM jsonb_array_elements(
              evidence_row.quality_document->'metric_document'->'relative_metrics') metric
           WHERE metric->>'metric_key'='cagr_spread';
          IF evidence_row.status IS DISTINCT FROM 'published' OR
             evidence_row.evaluation_cohort_version_id IS DISTINCT FROM
               release_row.evaluation_cohort_version_id OR
             evidence_row.quality_document->>'state' IS DISTINCT FROM 'passed' OR
             evidence_row.quality_document->>'outcome' IS DISTINCT FROM 'accepted' OR
             NEW.result_evidence_artifact_id IS DISTINCT FROM evidence_row.artifact_id OR
             NEW.result_artifact_id IS DISTINCT FROM evidence_row.result_artifact_id OR
             NEW.configuration_snapshot_id IS DISTINCT FROM
               evidence_row.configuration_snapshot_id OR
             NEW.cagr IS DISTINCT FROM expected_cagr OR
             NEW.cagr_spread IS DISTINCT FROM expected_spread OR
             NEW.sharpe_ratio IS DISTINCT FROM expected_sharpe OR
             NEW.maximum_drawdown IS DISTINCT FROM expected_drawdown OR
             NEW.benchmark_cagr IS DISTINCT FROM expected_cagr-expected_spread OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=release_row.artifact_id
                 AND dependency.depends_on_artifact_id=evidence_row.artifact_id
                 AND dependency.role='result_evidence'
                 AND dependency.ordinal=NEW.ordinal+1) THEN
            RAISE EXCEPTION 'Ranking member must project one exact accepted Cohort Result';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_ranking_cohort_member_validate
          BEFORE INSERT ON experiment.v022_ranking_cohort_member
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_ranking_cohort_member();

        CREATE FUNCTION experiment.validate_v022_ranking_cohort_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_count integer; min_ordinal integer; max_ordinal integer;
        BEGIN
          SELECT count(*),min(ordinal),max(ordinal)
            INTO actual_count,min_ordinal,max_ordinal
            FROM experiment.v022_ranking_cohort_member
           WHERE ranking_cohort_release_id=NEW.ranking_cohort_release_id;
          IF actual_count<>NEW.member_count OR min_ordinal<>0 OR
             max_ordinal<>NEW.member_count-1 THEN
            RAISE EXCEPTION 'Ranking Cohort member projection is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_ranking_cohort_complete
          AFTER INSERT ON experiment.v022_ranking_cohort_release
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION experiment.validate_v022_ranking_cohort_complete();

        CREATE TRIGGER trg_v022_ranking_cohort_release_append_only
          BEFORE UPDATE OR DELETE ON experiment.v022_ranking_cohort_release
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_ranking_cohort_member_append_only
          BEFORE UPDATE OR DELETE ON experiment.v022_ranking_cohort_member
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM experiment.v022_ranking_cohort_release)
          THEN RAISE EXCEPTION 'Cannot downgrade with v0.22 Ranking Cohort Releases';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS experiment.validate_v022_ranking_cohort_complete() CASCADE;
        DROP FUNCTION IF EXISTS experiment.validate_v022_ranking_cohort_member() CASCADE;
        DROP FUNCTION IF EXISTS experiment.validate_v022_ranking_cohort_release() CASCADE;
        DROP TABLE experiment.v022_ranking_cohort_member;
        DROP TABLE experiment.v022_ranking_cohort_release;
        """
    )
