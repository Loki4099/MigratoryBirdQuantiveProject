# ruff: noqa: E501
"""Bind v0.22 Common Panels and Result Evidence to exact Evaluation Cohorts.

Revision ID: 20260816_100_v022_evidence
Revises: 20260816_99_v022_cohort_runtime
"""

from __future__ import annotations

from alembic import op

revision = "20260816_100_v022_evidence"
down_revision = "20260816_99_v022_cohort_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE experiment.v022_common_evaluation_panel
          ADD COLUMN evaluation_cohort_version_id uuid NULL
            REFERENCES experiment.v022_evaluation_cohort_version,
          ADD COLUMN evaluation_cohort_fingerprint varchar(64) NULL
            CHECK (evaluation_cohort_fingerprint ~ '^[0-9a-f]{64}$'),
          ADD CONSTRAINT ck_v022_common_panel_cohort_identity CHECK (
            num_nonnulls(evaluation_cohort_version_id,
                         evaluation_cohort_fingerprint) IN (0,2)
          );
        CREATE UNIQUE INDEX uq_v022_common_panel_evaluation_cohort
          ON experiment.v022_common_evaluation_panel(evaluation_cohort_version_id)
          WHERE evaluation_cohort_version_id IS NOT NULL;

        ALTER TABLE experiment.v022_result_evidence_snapshot
          ADD COLUMN evaluation_cohort_version_id uuid NULL
            REFERENCES experiment.v022_evaluation_cohort_version,
          ADD COLUMN evaluation_cohort_fingerprint varchar(64) NULL
            CHECK (evaluation_cohort_fingerprint ~ '^[0-9a-f]{64}$'),
          ADD CONSTRAINT ck_v022_result_evidence_cohort_identity CHECK (
            num_nonnulls(evaluation_cohort_version_id,
                         evaluation_cohort_fingerprint) IN (0,2)
          );

        CREATE OR REPLACE FUNCTION experiment.validate_v022_common_panel()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE cohort_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          IF NEW.evaluation_cohort_version_id IS NULL THEN
            RETURN NEW;
          END IF;
          SELECT cohort.cohort_fingerprint,cohort.research_tier,cohort.frequency,
                 cohort.evaluation_start,cohort.evaluation_end,cohort.artifact_id,
                 artifact.status
            INTO cohort_row
            FROM experiment.v022_evaluation_cohort_version cohort
            JOIN lineage.artifact artifact ON artifact.artifact_id=cohort.artifact_id
           WHERE cohort.evaluation_cohort_version_id=NEW.evaluation_cohort_version_id;
          IF cohort_row.status IS DISTINCT FROM 'published' OR
             NEW.evaluation_cohort_fingerprint IS DISTINCT FROM
               cohort_row.cohort_fingerprint OR
             NEW.evidence_class IS DISTINCT FROM 'locked_historical_test' OR
             NEW.panel_document->>'mask_policy' IS DISTINCT FROM
               'exact_evaluation_cohort_eligibility_v1' OR
             NEW.panel_document->>'evaluation_cohort_version_id' IS DISTINCT FROM
               NEW.evaluation_cohort_version_id::text OR
             NEW.panel_document->>'evaluation_cohort_fingerprint' IS DISTINCT FROM
               cohort_row.cohort_fingerprint OR
             NEW.panel_document->>'research_tier' IS DISTINCT FROM
               cohort_row.research_tier OR
             NEW.panel_document->>'frequency' IS DISTINCT FROM cohort_row.frequency OR
             NEW.panel_document->>'evaluation_start' IS DISTINCT FROM
               cohort_row.evaluation_start::text OR
             NEW.panel_document->>'evaluation_end' IS DISTINCT FROM
               cohort_row.evaluation_end::text OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=cohort_row.artifact_id
                  AND dependency.role='evaluation_cohort' AND dependency.ordinal=0
             ) THEN
            RAISE EXCEPTION 'Common Panel must project its exact published Evaluation Cohort';
          END IF;
          RETURN NEW;
        END $$;

        CREATE OR REPLACE FUNCTION experiment.validate_v022_common_panel_completeness()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_count bigint; expected_count bigint;
        BEGIN
          SELECT count(*) INTO actual_count
            FROM experiment.v022_common_evaluation_panel_member
           WHERE common_evaluation_panel_id=NEW.common_evaluation_panel_id;
          IF actual_count <> NEW.observation_count THEN
            RAISE EXCEPTION 'Common Evaluation Panel member count differs from observation_count';
          END IF;
          IF NEW.evaluation_cohort_version_id IS NULL THEN
            RETURN NEW;
          END IF;
          SELECT count(*) INTO expected_count
            FROM experiment.v022_evaluation_cohort_session session
            JOIN experiment.v022_cohort_eligibility_interval eligibility
              ON eligibility.evaluation_cohort_version_id=
                 session.evaluation_cohort_version_id
             AND session.session_date BETWEEN
                 eligibility.effective_start AND eligibility.effective_end
            JOIN catalog.security security ON security.security_id=eligibility.security_id
           WHERE session.evaluation_cohort_version_id=NEW.evaluation_cohort_version_id
             AND session.session_role='evaluation'
             AND session.is_decision_session AND eligibility.is_selectable;
          IF expected_count<>actual_count OR EXISTS (
            (SELECT session.session_date,security.security_key
               FROM experiment.v022_evaluation_cohort_session session
               JOIN experiment.v022_cohort_eligibility_interval eligibility
                 ON eligibility.evaluation_cohort_version_id=
                    session.evaluation_cohort_version_id
                AND session.session_date BETWEEN
                    eligibility.effective_start AND eligibility.effective_end
               JOIN catalog.security security ON security.security_id=eligibility.security_id
              WHERE session.evaluation_cohort_version_id=NEW.evaluation_cohort_version_id
                AND session.session_role='evaluation'
                AND session.is_decision_session AND eligibility.is_selectable
             EXCEPT
             SELECT member.decision_session,member.asset_key
               FROM experiment.v022_common_evaluation_panel_member member
              WHERE member.common_evaluation_panel_id=NEW.common_evaluation_panel_id)
            UNION ALL
            (SELECT member.decision_session,member.asset_key
               FROM experiment.v022_common_evaluation_panel_member member
              WHERE member.common_evaluation_panel_id=NEW.common_evaluation_panel_id
             EXCEPT
             SELECT session.session_date,security.security_key
               FROM experiment.v022_evaluation_cohort_session session
               JOIN experiment.v022_cohort_eligibility_interval eligibility
                 ON eligibility.evaluation_cohort_version_id=
                    session.evaluation_cohort_version_id
                AND session.session_date BETWEEN
                    eligibility.effective_start AND eligibility.effective_end
               JOIN catalog.security security ON security.security_id=eligibility.security_id
              WHERE session.evaluation_cohort_version_id=NEW.evaluation_cohort_version_id
                AND session.session_role='evaluation'
                AND session.is_decision_session AND eligibility.is_selectable)
          ) THEN
            RAISE EXCEPTION 'Common Evaluation Panel differs from its exact Cohort mask';
          END IF;
          RETURN NEW;
        END $$;

        CREATE OR REPLACE FUNCTION experiment.validate_v022_result_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE result_status varchar; panel_row record; cohort_row record;
                result_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT status INTO result_status FROM lineage.artifact
           WHERE artifact_id=NEW.result_artifact_id;
          IF result_status <> 'published' THEN
            RAISE EXCEPTION 'Result Evidence requires a published Result Artifact';
          END IF;
          IF NEW.common_evaluation_panel_id IS NOT NULL THEN
            SELECT evidence_class,evaluation_cohort_version_id,
                   evaluation_cohort_fingerprint INTO panel_row
              FROM experiment.v022_common_evaluation_panel
             WHERE common_evaluation_panel_id=NEW.common_evaluation_panel_id;
            IF panel_row.evidence_class IS DISTINCT FROM NEW.evidence_class THEN
              RAISE EXCEPTION 'Result Evidence and Common Panel evidence classes differ';
            END IF;
          END IF;
          IF NEW.evaluation_cohort_version_id IS NULL THEN
            IF panel_row.evaluation_cohort_version_id IS NOT NULL THEN
              RAISE EXCEPTION 'Cohort-backed Common Panel requires Cohort-backed Evidence';
            END IF;
            RETURN NEW;
          END IF;
          SELECT cohort.cohort_fingerprint,cohort.frequency,cohort.evaluation_start,
                 cohort.evaluation_end,cohort.artifact_id,artifact.status
            INTO cohort_row
            FROM experiment.v022_evaluation_cohort_version cohort
            JOIN lineage.artifact artifact ON artifact.artifact_id=cohort.artifact_id
           WHERE cohort.evaluation_cohort_version_id=NEW.evaluation_cohort_version_id;
          SELECT effective_start,effective_end INTO result_row
            FROM experiment.v022_portfolio_cell_runtime_result
           WHERE artifact_id=NEW.result_artifact_id;
          IF cohort_row.status IS DISTINCT FROM 'published' OR
             NEW.evaluation_cohort_fingerprint IS DISTINCT FROM
               cohort_row.cohort_fingerprint OR
             panel_row.evaluation_cohort_version_id IS DISTINCT FROM
               NEW.evaluation_cohort_version_id OR
             panel_row.evaluation_cohort_fingerprint IS DISTINCT FROM
               NEW.evaluation_cohort_fingerprint OR
             result_row.effective_start IS DISTINCT FROM cohort_row.evaluation_start OR
             result_row.effective_end IS DISTINCT FROM cohort_row.evaluation_end OR
             NEW.evidence_document->>'evaluation_cohort_version_id' IS DISTINCT FROM
               NEW.evaluation_cohort_version_id::text OR
             NEW.evidence_document->>'evaluation_cohort_fingerprint' IS DISTINCT FROM
               cohort_row.cohort_fingerprint OR
             NEW.evidence_document->>'frequency' IS DISTINCT FROM cohort_row.frequency OR
             NEW.evidence_document->'interval' IS DISTINCT FROM
               jsonb_build_array(cohort_row.evaluation_start::text,
                                 cohort_row.evaluation_end::text) OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=cohort_row.artifact_id
                  AND dependency.role='evaluation_cohort' AND dependency.ordinal=0
             ) THEN
            RAISE EXCEPTION 'Result Evidence must project its exact Evaluation Cohort';
          END IF;
          RETURN NEW;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM experiment.v022_common_evaluation_panel
                      WHERE evaluation_cohort_version_id IS NOT NULL) OR
             EXISTS (SELECT 1 FROM experiment.v022_result_evidence_snapshot
                      WHERE evaluation_cohort_version_id IS NOT NULL)
          THEN RAISE EXCEPTION 'Cannot downgrade with Cohort-backed Result Evidence';
          END IF;
        END $$;

        CREATE OR REPLACE FUNCTION experiment.validate_v022_common_panel()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          RETURN NEW;
        END $$;
        CREATE OR REPLACE FUNCTION experiment.validate_v022_common_panel_completeness()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_count bigint;
        BEGIN
          SELECT count(*) INTO actual_count
            FROM experiment.v022_common_evaluation_panel_member
           WHERE common_evaluation_panel_id=NEW.common_evaluation_panel_id;
          IF actual_count <> NEW.observation_count THEN
            RAISE EXCEPTION 'Common Evaluation Panel member count differs from observation_count';
          END IF;
          RETURN NEW;
        END $$;
        CREATE OR REPLACE FUNCTION experiment.validate_v022_result_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE result_status varchar; panel_class varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT status INTO result_status FROM lineage.artifact
           WHERE artifact_id=NEW.result_artifact_id;
          IF result_status <> 'published' THEN
            RAISE EXCEPTION 'Result Evidence requires a published Result Artifact';
          END IF;
          IF NEW.common_evaluation_panel_id IS NOT NULL THEN
            SELECT evidence_class INTO panel_class
              FROM experiment.v022_common_evaluation_panel
             WHERE common_evaluation_panel_id=NEW.common_evaluation_panel_id;
            IF panel_class IS DISTINCT FROM NEW.evidence_class THEN
              RAISE EXCEPTION 'Result Evidence and Common Panel evidence classes differ';
            END IF;
          END IF;
          RETURN NEW;
        END $$;

        DROP INDEX experiment.uq_v022_common_panel_evaluation_cohort;
        ALTER TABLE experiment.v022_result_evidence_snapshot
          DROP CONSTRAINT ck_v022_result_evidence_cohort_identity,
          DROP COLUMN evaluation_cohort_fingerprint,
          DROP COLUMN evaluation_cohort_version_id;
        ALTER TABLE experiment.v022_common_evaluation_panel
          DROP CONSTRAINT ck_v022_common_panel_cohort_identity,
          DROP COLUMN evaluation_cohort_fingerprint,
          DROP COLUMN evaluation_cohort_version_id;
        """
    )
