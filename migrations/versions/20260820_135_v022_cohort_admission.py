# ruff: noqa: E501
"""Harden v0.22 Cohort price identity and usable-bar admission.

Revision ID: 20260820_135_v022_cohort_gate
Revises: 20260820_134_v022_recon_v2
"""

from __future__ import annotations

from alembic import op

revision = "20260820_135_v022_cohort_gate"
down_revision = "20260820_134_v022_recon_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        DO $$ DECLARE constraint_name name;
        BEGIN
          SELECT constraint_item.conname INTO constraint_name
            FROM pg_constraint constraint_item
           WHERE constraint_item.conrelid=
                   'experiment.v022_evaluation_cohort_version'::regclass
             AND constraint_item.contype='c'
             AND pg_get_constraintdef(constraint_item.oid) LIKE
                   '%price_semantics%historical_constituent_pit__frozen_retrospective_yahoo_prices%';
          IF constraint_name IS NULL THEN
            RAISE EXCEPTION 'Legacy Evaluation Cohort price-semantics constraint not found';
          END IF;
          EXECUTE format(
            'ALTER TABLE experiment.v022_evaluation_cohort_version DROP CONSTRAINT %I',
            constraint_name
          );
        END $$;
        ALTER TABLE experiment.v022_evaluation_cohort_version
          ADD CONSTRAINT ck_v022_eval_cohort_price_semantics_m135
          CHECK (btrim(price_semantics)<>'' AND price_semantics=btrim(price_semantics));

        CREATE FUNCTION experiment.validate_v022_evaluation_cohort_admission_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_price_semantics varchar(180);
        BEGIN
          SELECT CASE
                   WHEN reconciled.dataset_publication_id IS NULL
                     THEN market_binding.price_semantics
                   ELSE reconciled.price_semantics
                 END
            INTO expected_price_semantics
            FROM data.dataset_publication risk_dataset
            LEFT JOIN data.v022_reconciled_market_dataset_binding reconciled
              ON reconciled.dataset_publication_id=
                   risk_dataset.dataset_publication_id
             AND reconciled.dataset_artifact_id=risk_dataset.artifact_id
            JOIN data.v022_security_market_dataset_binding market_binding
              ON market_binding.dataset_publication_id=COALESCE(
                   reconciled.primary_dataset_publication_id,
                   risk_dataset.dataset_publication_id)
             AND market_binding.security_market_quality_report_id=
                   NEW.security_market_quality_report_id
             AND market_binding.quality_report_artifact_id=
                   NEW.quality_report_artifact_id
           WHERE risk_dataset.dataset_publication_id=NEW.dataset_publication_id
             AND risk_dataset.artifact_id=NEW.dataset_artifact_id
             AND (
               reconciled.dataset_publication_id IS NOT NULL OR
               market_binding.dataset_artifact_id=NEW.dataset_artifact_id
             );
          IF expected_price_semantics IS NULL OR
             NEW.price_semantics IS DISTINCT FROM expected_price_semantics OR
             NEW.cohort_document->>'price_semantics' IS DISTINCT FROM
               NEW.price_semantics THEN
            RAISE EXCEPTION
              'Evaluation Cohort must inherit exact risk Dataset price semantics';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_eval_cohort_admission_version_m135
          BEFORE INSERT ON experiment.v022_evaluation_cohort_version
          FOR EACH ROW EXECUTE FUNCTION
            experiment.validate_v022_evaluation_cohort_admission_version();

        CREATE FUNCTION experiment.validate_v022_evaluation_cohort_admission_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE invalid_live_count bigint; invalid_ready_count bigint;
        BEGIN
          WITH cohort_security AS (
            SELECT DISTINCT interval.security_id
              FROM experiment.v022_cohort_eligibility_interval interval
             WHERE interval.evaluation_cohort_version_id=
                   NEW.evaluation_cohort_version_id
          ), session_bar AS (
            SELECT cohort_security.security_id,session.ordinal,session.session_date,
                   (
                     bar.dataset_publication_id IS NOT NULL AND
                     bar.volume_raw>0 AND
                     LEAST(
                       bar.open_raw,bar.high_raw,bar.low_raw,bar.close_raw,
                       bar.adj_close,bar.open_adj,bar.high_adj,bar.low_adj,
                       bar.close_adj,bar.adjustment_factor
                     )>0
                   ) AS is_usable
              FROM cohort_security
              JOIN catalog.security security
                ON security.security_id=cohort_security.security_id
              CROSS JOIN experiment.v022_evaluation_cohort_session session
              LEFT JOIN data.daily_bar bar
                ON bar.dataset_publication_id=NEW.dataset_publication_id
               AND bar.asset_id=security.legacy_asset_id
               AND bar.session_date=session.session_date
             WHERE session.evaluation_cohort_version_id=
                   NEW.evaluation_cohort_version_id
          ), rolling_readiness AS (
            SELECT session_bar.*,
                   count(*) OVER (
                     PARTITION BY security_id ORDER BY ordinal
                     ROWS BETWEEN 503 PRECEDING AND CURRENT ROW
                   ) AS rolling_session_count,
                   count(*) FILTER (WHERE is_usable) OVER (
                     PARTITION BY security_id ORDER BY ordinal
                     ROWS BETWEEN 503 PRECEDING AND CURRENT ROW
                   ) AS rolling_usable_count
              FROM session_bar
          )
          SELECT count(*) FILTER (
                   WHERE (interval.is_tradable OR
                          interval.valuation_state='live')
                     AND NOT rolling_readiness.is_usable
                 ),
                 count(*) FILTER (
                   WHERE interval.is_warmup_ready AND NOT (
                     rolling_readiness.rolling_session_count=
                       NEW.required_history_sessions AND
                     rolling_readiness.rolling_usable_count=
                       NEW.required_history_sessions
                   )
                 )
            INTO invalid_live_count,invalid_ready_count
            FROM experiment.v022_cohort_eligibility_interval interval
            JOIN rolling_readiness
              ON rolling_readiness.security_id=interval.security_id
             AND rolling_readiness.session_date BETWEEN
                   interval.effective_start AND interval.effective_end
           WHERE interval.evaluation_cohort_version_id=
                 NEW.evaluation_cohort_version_id;
          IF invalid_live_count<>0 OR invalid_ready_count<>0 THEN
            RAISE EXCEPTION
              'Evaluation Cohort admission requires consecutive usable positive-volume bars';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_eval_cohort_admission_complete_m135
          AFTER INSERT ON experiment.v022_evaluation_cohort_version
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION
            experiment.validate_v022_evaluation_cohort_admission_complete();

        COMMENT ON COLUMN experiment.v022_evaluation_cohort_version.price_semantics IS
          'Exact immutable semantics inherited from the bound direct or reconciled risk Dataset.';
        """
    )


def downgrade() -> None:
    op.execute(
        r"""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM experiment.v022_evaluation_cohort_version
             WHERE price_semantics<>
               'historical_constituent_pit__frozen_retrospective_yahoo_prices'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade with non-Yahoo Evaluation Cohort price semantics';
          END IF;
        END $$;

        DROP TRIGGER trg_v022_eval_cohort_admission_complete_m135
          ON experiment.v022_evaluation_cohort_version;
        DROP FUNCTION
          experiment.validate_v022_evaluation_cohort_admission_complete();
        DROP TRIGGER trg_v022_eval_cohort_admission_version_m135
          ON experiment.v022_evaluation_cohort_version;
        DROP FUNCTION
          experiment.validate_v022_evaluation_cohort_admission_version();

        ALTER TABLE experiment.v022_evaluation_cohort_version
          DROP CONSTRAINT ck_v022_eval_cohort_price_semantics_m135;
        ALTER TABLE experiment.v022_evaluation_cohort_version
          ADD CONSTRAINT ck_v022_eval_cohort_price_semantics_v1 CHECK (
            price_semantics=
              'historical_constituent_pit__frozen_retrospective_yahoo_prices'
          );
        COMMENT ON COLUMN experiment.v022_evaluation_cohort_version.price_semantics
          IS NULL;
        """
    )
