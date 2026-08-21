# ruff: noqa: E501
"""Add immutable alternate observations and deterministic market reconciliation.

Revision ID: 20260817_104_v022_reconciliation
Revises: 20260817_103_v022_lifecycle
"""

from __future__ import annotations

from alembic import op

revision = "20260817_104_v022_reconciliation"
down_revision = "20260817_103_v022_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE data.v022_alternate_observation_set (
          alternate_observation_set_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          source_snapshot_id uuid NOT NULL REFERENCES data.source_snapshot,
          source_snapshot_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          source_snapshot_security_subject_id uuid NOT NULL
            REFERENCES data.source_snapshot_security_subject,
          security_id uuid NOT NULL REFERENCES catalog.security,
          observation_key varchar(200) NOT NULL CHECK (btrim(observation_key)<>''),
          version_number integer NOT NULL CHECK (version_number>=1),
          provider_key varchar(100) NOT NULL CHECK (btrim(provider_key)<>''),
          coverage_start date NOT NULL,
          coverage_end date NOT NULL,
          bar_count integer NOT NULL CHECK (bar_count>=1),
          action_count integer NOT NULL CHECK (action_count>=0),
          observation_document jsonb NOT NULL
            CHECK (jsonb_typeof(observation_document)='object'),
          observation_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (observation_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (observation_key,version_number),
          CHECK (coverage_start<=coverage_end),
          CHECK ((
            observation_document->>'contract_version'=
              'v0.22.alternate_market_observation.v1' AND
            observation_document->>'source_snapshot_id'=source_snapshot_id::text AND
            observation_document->>'security_id'=security_id::text AND
            observation_document->>'observation_key'=observation_key AND
            (observation_document->>'version_number')::integer=version_number AND
            observation_document->>'provider_key'=provider_key AND
            (observation_document->>'coverage_start')::date=coverage_start AND
            (observation_document->>'coverage_end')::date=coverage_end AND
            (observation_document->>'bar_count')::integer=bar_count AND
            (observation_document->>'action_count')::integer=action_count AND
            observation_document->>'price_input_semantics'='raw_ohlcv_and_actions'
          ) IS TRUE)
        );

        CREATE TABLE data.v022_alternate_market_bar (
          alternate_observation_set_id uuid NOT NULL
            REFERENCES data.v022_alternate_observation_set,
          session_date date NOT NULL,
          open_raw numeric(24,10) NOT NULL,
          high_raw numeric(24,10) NOT NULL,
          low_raw numeric(24,10) NOT NULL,
          close_raw numeric(24,10) NOT NULL,
          volume_raw bigint NOT NULL,
          provider_adjusted_close numeric(24,10) NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (alternate_observation_set_id,session_date),
          CHECK (least(open_raw,high_raw,low_raw,close_raw)>0),
          CHECK (high_raw>=greatest(open_raw,close_raw)),
          CHECK (low_raw<=least(open_raw,close_raw)),
          CHECK (volume_raw>=0),
          CHECK (provider_adjusted_close IS NULL OR provider_adjusted_close>0)
        );

        CREATE TABLE data.v022_alternate_corporate_action (
          alternate_observation_set_id uuid NOT NULL
            REFERENCES data.v022_alternate_observation_set,
          effective_date date NOT NULL,
          cash_dividend numeric(24,10) NOT NULL CHECK (cash_dividend>=0),
          split_ratio numeric(24,10) NOT NULL CHECK (split_ratio>=0),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (alternate_observation_set_id,effective_date),
          CHECK (cash_dividend>0 OR split_ratio>0)
        );

        CREATE TABLE data.v022_market_gap_resolution (
          market_gap_resolution_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          primary_dataset_publication_id uuid NOT NULL
            REFERENCES data.dataset_publication,
          primary_dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          security_id uuid NOT NULL REFERENCES catalog.security,
          gap_key varchar(200) NOT NULL CHECK (btrim(gap_key)<>''),
          version_number integer NOT NULL CHECK (version_number>=1),
          gap_type varchar(40) NOT NULL CHECK (gap_type IN (
            'missing_bar','provider_conflict','corporate_action_conflict',
            'ticker_boundary','abnormal_last_day','uniform_exclusion'
          )),
          gap_start date NOT NULL,
          gap_end date NOT NULL,
          resolution_kind varchar(40) NOT NULL CHECK (resolution_kind IN (
            'replace_with_alternate','retain_primary','exclude_security','unresolved'
          )),
          alternate_observation_set_id uuid NULL
            REFERENCES data.v022_alternate_observation_set,
          alternate_observation_artifact_id uuid NULL REFERENCES lineage.artifact,
          evidence_count integer NOT NULL CHECK (evidence_count>=1),
          supersedes_market_gap_resolution_id uuid NULL
            REFERENCES data.v022_market_gap_resolution,
          resolution_document jsonb NOT NULL
            CHECK (jsonb_typeof(resolution_document)='object'),
          resolution_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (resolution_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (primary_dataset_publication_id,gap_key,version_number),
          CHECK (gap_start<=gap_end),
          CHECK ((resolution_kind='replace_with_alternate' AND
                  alternate_observation_set_id IS NOT NULL AND
                  alternate_observation_artifact_id IS NOT NULL) OR
                 (resolution_kind<>'replace_with_alternate' AND
                  alternate_observation_set_id IS NULL AND
                  alternate_observation_artifact_id IS NULL)),
          CHECK ((
            resolution_document->>'contract_version'='v0.22.market_gap_resolution.v1' AND
            resolution_document->>'primary_dataset_publication_id'=
              primary_dataset_publication_id::text AND
            resolution_document->>'security_id'=security_id::text AND
            resolution_document->>'gap_key'=gap_key AND
            (resolution_document->>'version_number')::integer=version_number AND
            resolution_document->>'gap_type'=gap_type AND
            (resolution_document->>'gap_start')::date=gap_start AND
            (resolution_document->>'gap_end')::date=gap_end AND
            resolution_document->>'resolution_kind'=resolution_kind AND
            (resolution_document->>'evidence_count')::integer=evidence_count AND
            CASE WHEN alternate_observation_set_id IS NULL
              THEN resolution_document->'alternate_observation_set_id'='null'::jsonb
              ELSE resolution_document->>'alternate_observation_set_id'=
                   alternate_observation_set_id::text
            END
          ) IS TRUE)
        );

        CREATE TABLE data.v022_market_gap_resolution_evidence (
          market_gap_resolution_id uuid NOT NULL
            REFERENCES data.v022_market_gap_resolution,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          evidence_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          evidence_role varchar(40) NOT NULL CHECK (evidence_role IN (
            'review_note','provider_comparison','exchange_notice',
            'corporate_action_terms','other_public_record'
          )),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (market_gap_resolution_id,ordinal),
          UNIQUE (market_gap_resolution_id,evidence_artifact_id)
        );

        CREATE TABLE data.v022_market_reconciliation_plan (
          market_reconciliation_plan_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          primary_dataset_publication_id uuid NOT NULL
            REFERENCES data.dataset_publication,
          primary_dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          cleaning_version_id uuid NOT NULL REFERENCES data.cleaning_version,
          cleaning_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          calendar_version_id uuid NOT NULL REFERENCES catalog.calendar_version,
          calendar_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          output_dataset_key varchar(140) NOT NULL CHECK (btrim(output_dataset_key)<>''),
          output_version_number integer NOT NULL CHECK (output_version_number>=1),
          reconstruction_policy varchar(100) NOT NULL CHECK (
            reconstruction_policy='raw_ohlcv_actions_backward_total_return_v1'
          ),
          resolution_count integer NOT NULL CHECK (resolution_count>=1),
          excluded_security_count integer NOT NULL CHECK (excluded_security_count>=0),
          plan_document jsonb NOT NULL CHECK (jsonb_typeof(plan_document)='object'),
          plan_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (plan_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (output_dataset_key,output_version_number),
          CHECK ((
            plan_document->>'contract_version'='v0.22.market_reconciliation_plan.v1' AND
            plan_document->>'primary_dataset_publication_id'=
              primary_dataset_publication_id::text AND
            plan_document->>'output_dataset_key'=output_dataset_key AND
            (plan_document->>'output_version_number')::integer=output_version_number AND
            plan_document->>'reconstruction_policy'=reconstruction_policy AND
            (plan_document->>'resolution_count')::integer=resolution_count AND
            (plan_document->>'excluded_security_count')::integer=
              excluded_security_count
          ) IS TRUE)
        );

        CREATE TABLE data.v022_market_reconciliation_plan_resolution (
          market_reconciliation_plan_id uuid NOT NULL
            REFERENCES data.v022_market_reconciliation_plan,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          market_gap_resolution_id uuid NOT NULL
            REFERENCES data.v022_market_gap_resolution,
          resolution_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (market_reconciliation_plan_id,ordinal),
          UNIQUE (market_reconciliation_plan_id,market_gap_resolution_id)
        );

        CREATE TABLE data.v022_reconciled_market_dataset_binding (
          dataset_publication_id uuid PRIMARY KEY REFERENCES data.dataset_publication,
          dataset_artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          market_reconciliation_plan_id uuid NOT NULL
            REFERENCES data.v022_market_reconciliation_plan,
          plan_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          primary_dataset_publication_id uuid NOT NULL
            REFERENCES data.dataset_publication,
          primary_dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          price_semantics varchar(180) NOT NULL CHECK (price_semantics=
            'historical_constituent_pit__frozen_reconciled_retrospective_prices'),
          reconstruction_policy varchar(100) NOT NULL CHECK (
            reconstruction_policy='raw_ohlcv_actions_backward_total_return_v1'
          ),
          replaced_bar_count integer NOT NULL CHECK (replaced_bar_count>=0),
          excluded_security_count integer NOT NULL CHECK (excluded_security_count>=0),
          binding_document jsonb NOT NULL CHECK (jsonb_typeof(binding_document)='object'),
          binding_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE FUNCTION data.validate_v022_alternate_observation_set()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; snapshot_row record; subject_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT snapshot.artifact_id,artifact.status INTO snapshot_row
            FROM data.source_snapshot snapshot
            JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id
           WHERE snapshot.source_snapshot_id=NEW.source_snapshot_id;
          SELECT subject.source_snapshot_id,subject.security_id,subject.provider_scope,
                 subject.fetch_status INTO subject_row
            FROM data.source_snapshot_security_subject subject
           WHERE subject.source_snapshot_security_subject_id=
                 NEW.source_snapshot_security_subject_id;
          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_alternate_observation_set' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_alternate_observation__' || NEW.observation_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
             snapshot_row.status IS DISTINCT FROM 'published' OR
             snapshot_row.artifact_id IS DISTINCT FROM NEW.source_snapshot_artifact_id OR
             subject_row.source_snapshot_id IS DISTINCT FROM NEW.source_snapshot_id OR
             subject_row.security_id IS DISTINCT FROM NEW.security_id OR
             subject_row.provider_scope IS DISTINCT FROM NEW.provider_key OR
             subject_row.fetch_status IS DISTINCT FROM 'fetched' OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>1 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.source_snapshot_artifact_id
                 AND dependency.role='source_snapshot' AND dependency.ordinal=0) THEN
            RAISE EXCEPTION 'Alternate Observation identity is incomplete';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION data.validate_v022_alternate_observation_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_bars integer; actual_actions integer;
                actual_start date; actual_end date;
        BEGIN
          SELECT count(*),min(session_date),max(session_date)
            INTO actual_bars,actual_start,actual_end
            FROM data.v022_alternate_market_bar
           WHERE alternate_observation_set_id=NEW.alternate_observation_set_id;
          SELECT count(*) INTO actual_actions
            FROM data.v022_alternate_corporate_action
           WHERE alternate_observation_set_id=NEW.alternate_observation_set_id;
          IF actual_bars IS DISTINCT FROM NEW.bar_count OR
             actual_actions IS DISTINCT FROM NEW.action_count OR
             actual_start IS DISTINCT FROM NEW.coverage_start OR
             actual_end IS DISTINCT FROM NEW.coverage_end THEN
            RAISE EXCEPTION 'Alternate Observation rows are incomplete';
          END IF;
          RETURN NULL;
        END $$;

        CREATE FUNCTION data.validate_v022_market_gap_resolution()
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

        CREATE FUNCTION data.validate_v022_market_gap_resolution_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE resolution_artifact uuid; evidence_status varchar;
        BEGIN
          SELECT artifact_id INTO resolution_artifact
            FROM data.v022_market_gap_resolution
           WHERE market_gap_resolution_id=NEW.market_gap_resolution_id;
          PERFORM data.assert_artifact_draft(resolution_artifact);
          SELECT status INTO evidence_status FROM lineage.artifact
           WHERE artifact_id=NEW.evidence_artifact_id;
          IF evidence_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Gap Resolution evidence must be published';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION data.validate_v022_market_gap_resolution_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE evidence_actual integer; dependency_actual integer;
                alternate_offset integer;
        BEGIN
          SELECT count(*) INTO evidence_actual
            FROM data.v022_market_gap_resolution_evidence evidence
           WHERE evidence.market_gap_resolution_id=NEW.market_gap_resolution_id;
          alternate_offset := CASE WHEN NEW.alternate_observation_set_id IS NULL
                                   THEN 0 ELSE 1 END;
          SELECT count(*) INTO dependency_actual
            FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id;
          IF evidence_actual IS DISTINCT FROM NEW.evidence_count OR
             dependency_actual IS DISTINCT FROM 1+alternate_offset+NEW.evidence_count OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.primary_dataset_artifact_id
                 AND dependency.role='primary_dataset' AND dependency.ordinal=0) OR
             (alternate_offset=1 AND NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=
                      NEW.alternate_observation_artifact_id
                  AND dependency.role='alternate_observation' AND dependency.ordinal=1
             )) OR EXISTS (
               SELECT 1 FROM data.v022_market_gap_resolution_evidence evidence
                WHERE evidence.market_gap_resolution_id=NEW.market_gap_resolution_id
                  AND NOT EXISTS (
                    SELECT 1 FROM lineage.artifact_dependency dependency
                     WHERE dependency.artifact_id=NEW.artifact_id
                       AND dependency.depends_on_artifact_id=evidence.evidence_artifact_id
                       AND dependency.role='review_evidence'
                       AND dependency.ordinal=1+alternate_offset+evidence.ordinal
                  )
             ) THEN
            RAISE EXCEPTION 'Gap Resolution dependency closure is incomplete';
          END IF;
          RETURN NULL;
        END $$;

        CREATE FUNCTION data.validate_v022_market_reconciliation_plan()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; primary_row record; cleaning_row record;
                calendar_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT publication.artifact_id,publication.value_kind,artifact.status
            INTO primary_row FROM data.dataset_publication publication
            JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
           WHERE publication.dataset_publication_id=NEW.primary_dataset_publication_id;
          SELECT version.artifact_id,artifact.status INTO cleaning_row
            FROM data.cleaning_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.cleaning_version_id=NEW.cleaning_version_id;
          SELECT version.artifact_id,artifact.status INTO calendar_row
            FROM catalog.calendar_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.calendar_version_id=NEW.calendar_version_id;
          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_market_reconciliation_plan' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_market_reconciliation_plan__' || NEW.output_dataset_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.output_version_number OR
             primary_row.status IS DISTINCT FROM 'published' OR
             primary_row.value_kind IS DISTINCT FROM 'daily_bar' OR
             primary_row.artifact_id IS DISTINCT FROM NEW.primary_dataset_artifact_id OR
             cleaning_row.status IS DISTINCT FROM 'published' OR
             cleaning_row.artifact_id IS DISTINCT FROM NEW.cleaning_artifact_id OR
             calendar_row.status IS DISTINCT FROM 'published' OR
             calendar_row.artifact_id IS DISTINCT FROM NEW.calendar_artifact_id THEN
            RAISE EXCEPTION 'Market Reconciliation Plan identity is incomplete';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION data.validate_v022_market_reconciliation_plan_resolution()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE plan_artifact uuid; resolution_row record;
        BEGIN
          SELECT artifact_id INTO plan_artifact
            FROM data.v022_market_reconciliation_plan
           WHERE market_reconciliation_plan_id=NEW.market_reconciliation_plan_id;
          PERFORM data.assert_artifact_draft(plan_artifact);
          SELECT item.artifact_id,artifact.status INTO resolution_row
            FROM data.v022_market_gap_resolution item
            JOIN lineage.artifact artifact ON artifact.artifact_id=item.artifact_id
           WHERE item.market_gap_resolution_id=NEW.market_gap_resolution_id;
          IF resolution_row.status IS DISTINCT FROM 'published' OR
             resolution_row.artifact_id IS DISTINCT FROM NEW.resolution_artifact_id THEN
            RAISE EXCEPTION 'Reconciliation Plan resolution must be published and exact';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION data.validate_v022_market_reconciliation_plan_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE resolution_actual integer; exclusion_actual integer;
                dependency_actual integer;
        BEGIN
          SELECT count(*),count(*) FILTER (
                   WHERE resolution.resolution_kind='exclude_security')
            INTO resolution_actual,exclusion_actual
            FROM data.v022_market_reconciliation_plan_resolution binding
            JOIN data.v022_market_gap_resolution resolution
              ON resolution.market_gap_resolution_id=binding.market_gap_resolution_id
           WHERE binding.market_reconciliation_plan_id=
                 NEW.market_reconciliation_plan_id;
          SELECT count(*) INTO dependency_actual
            FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id;
          IF resolution_actual IS DISTINCT FROM NEW.resolution_count OR
             exclusion_actual IS DISTINCT FROM NEW.excluded_security_count OR
             dependency_actual IS DISTINCT FROM 3+NEW.resolution_count OR
             EXISTS (
               SELECT 1 FROM data.v022_market_reconciliation_plan_resolution binding
               JOIN data.v022_market_gap_resolution resolution
                 ON resolution.market_gap_resolution_id=binding.market_gap_resolution_id
              WHERE binding.market_reconciliation_plan_id=
                    NEW.market_reconciliation_plan_id
                AND (resolution.primary_dataset_publication_id IS DISTINCT FROM
                       NEW.primary_dataset_publication_id OR
                     resolution.resolution_kind='unresolved')
             ) OR
             EXISTS (
               SELECT 1 FROM data.v022_market_reconciliation_plan_resolution left_binding
               JOIN data.v022_market_gap_resolution left_resolution
                 ON left_resolution.market_gap_resolution_id=
                    left_binding.market_gap_resolution_id
               JOIN data.v022_market_reconciliation_plan_resolution right_binding
                 ON right_binding.market_reconciliation_plan_id=
                    left_binding.market_reconciliation_plan_id
                AND right_binding.ordinal>left_binding.ordinal
               JOIN data.v022_market_gap_resolution right_resolution
                 ON right_resolution.market_gap_resolution_id=
                    right_binding.market_gap_resolution_id
              WHERE left_binding.market_reconciliation_plan_id=
                    NEW.market_reconciliation_plan_id
                AND left_resolution.security_id=right_resolution.security_id
                AND daterange(left_resolution.gap_start,left_resolution.gap_end,'[]') &&
                    daterange(right_resolution.gap_start,right_resolution.gap_end,'[]')
             ) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.primary_dataset_artifact_id
                 AND dependency.role='primary_dataset' AND dependency.ordinal=0) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.cleaning_artifact_id
                 AND dependency.role='cleaning_version' AND dependency.ordinal=1) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.calendar_artifact_id
                 AND dependency.role='calendar_version' AND dependency.ordinal=2) OR
             EXISTS (
               SELECT 1 FROM data.v022_market_reconciliation_plan_resolution binding
                WHERE binding.market_reconciliation_plan_id=
                      NEW.market_reconciliation_plan_id
                  AND NOT EXISTS (
                    SELECT 1 FROM lineage.artifact_dependency dependency
                     WHERE dependency.artifact_id=NEW.artifact_id
                       AND dependency.depends_on_artifact_id=
                           binding.resolution_artifact_id
                       AND dependency.role='gap_resolution'
                       AND dependency.ordinal=3+binding.ordinal
                  )
             ) THEN
            RAISE EXCEPTION 'Market Reconciliation Plan closure is incomplete';
          END IF;
          RETURN NULL;
        END $$;

        CREATE FUNCTION data.validate_v022_reconciled_market_dataset_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE dataset_row record; plan_row record;
        BEGIN
          SELECT publication.artifact_id,publication.dataset_key,
                 publication.version_number,publication.value_kind,artifact.status
            INTO dataset_row FROM data.dataset_publication publication
            JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
           WHERE publication.dataset_publication_id=NEW.dataset_publication_id;
          SELECT plan.artifact_id,plan.primary_dataset_publication_id,
                 plan.primary_dataset_artifact_id,plan.reconstruction_policy,
                 plan.output_dataset_key,plan.output_version_number,artifact.status
            INTO plan_row FROM data.v022_market_reconciliation_plan plan
            JOIN lineage.artifact artifact ON artifact.artifact_id=plan.artifact_id
           WHERE plan.market_reconciliation_plan_id=
                 NEW.market_reconciliation_plan_id;
          IF dataset_row.status IS DISTINCT FROM 'draft' OR
             dataset_row.artifact_id IS DISTINCT FROM NEW.dataset_artifact_id OR
             dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
             dataset_row.dataset_key IS DISTINCT FROM plan_row.output_dataset_key OR
             dataset_row.version_number IS DISTINCT FROM plan_row.output_version_number OR
             plan_row.status IS DISTINCT FROM 'published' OR
             plan_row.artifact_id IS DISTINCT FROM NEW.plan_artifact_id OR
             plan_row.primary_dataset_publication_id IS DISTINCT FROM
               NEW.primary_dataset_publication_id OR
             plan_row.primary_dataset_artifact_id IS DISTINCT FROM
               NEW.primary_dataset_artifact_id OR
             plan_row.reconstruction_policy IS DISTINCT FROM
               NEW.reconstruction_policy OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.dataset_artifact_id)<>1 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.dataset_artifact_id
                 AND dependency.depends_on_artifact_id=NEW.plan_artifact_id
                 AND dependency.role='reconciliation_plan' AND dependency.ordinal=0) THEN
            RAISE EXCEPTION 'Reconciled Market Dataset binding is incomplete';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_alternate_observation_set_validate
          BEFORE INSERT ON data.v022_alternate_observation_set
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_alternate_observation_set();
        CREATE CONSTRAINT TRIGGER trg_v022_alternate_observation_complete
          AFTER INSERT ON data.v022_alternate_observation_set
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION data.validate_v022_alternate_observation_complete();
        CREATE TRIGGER trg_v022_market_gap_resolution_validate
          BEFORE INSERT ON data.v022_market_gap_resolution
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_market_gap_resolution();
        CREATE TRIGGER trg_v022_market_gap_resolution_evidence_validate
          BEFORE INSERT ON data.v022_market_gap_resolution_evidence
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_market_gap_resolution_evidence();
        CREATE CONSTRAINT TRIGGER trg_v022_market_gap_resolution_complete
          AFTER INSERT ON data.v022_market_gap_resolution
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION data.validate_v022_market_gap_resolution_complete();
        CREATE TRIGGER trg_v022_market_reconciliation_plan_validate
          BEFORE INSERT ON data.v022_market_reconciliation_plan
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_market_reconciliation_plan();
        CREATE TRIGGER trg_v022_market_reconciliation_plan_resolution_validate
          BEFORE INSERT ON data.v022_market_reconciliation_plan_resolution
          FOR EACH ROW EXECUTE FUNCTION
            data.validate_v022_market_reconciliation_plan_resolution();
        CREATE CONSTRAINT TRIGGER trg_v022_market_reconciliation_plan_complete
          AFTER INSERT ON data.v022_market_reconciliation_plan
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION data.validate_v022_market_reconciliation_plan_complete();
        CREATE TRIGGER trg_v022_reconciled_market_dataset_binding_validate
          BEFORE INSERT ON data.v022_reconciled_market_dataset_binding
          FOR EACH ROW EXECUTE FUNCTION
            data.validate_v022_reconciled_market_dataset_binding();

        CREATE TRIGGER trg_v022_alternate_observation_set_append_only
          BEFORE UPDATE OR DELETE ON data.v022_alternate_observation_set
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_alternate_market_bar_append_only
          BEFORE UPDATE OR DELETE ON data.v022_alternate_market_bar
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_alternate_corporate_action_append_only
          BEFORE UPDATE OR DELETE ON data.v022_alternate_corporate_action
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_market_gap_resolution_append_only
          BEFORE UPDATE OR DELETE ON data.v022_market_gap_resolution
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_market_gap_resolution_evidence_append_only
          BEFORE UPDATE OR DELETE ON data.v022_market_gap_resolution_evidence
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_market_reconciliation_plan_append_only
          BEFORE UPDATE OR DELETE ON data.v022_market_reconciliation_plan
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_market_reconciliation_plan_resolution_append_only
          BEFORE UPDATE OR DELETE ON data.v022_market_reconciliation_plan_resolution
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_reconciled_market_dataset_binding_append_only
          BEFORE UPDATE OR DELETE ON data.v022_reconciled_market_dataset_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM data.v022_alternate_observation_set) OR
             EXISTS (SELECT 1 FROM data.v022_market_gap_resolution) OR
             EXISTS (SELECT 1 FROM data.v022_market_reconciliation_plan) OR
             EXISTS (SELECT 1 FROM data.v022_reconciled_market_dataset_binding) THEN
            RAISE EXCEPTION 'Cannot downgrade with v0.22 reconciliation evidence';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS
          data.validate_v022_reconciled_market_dataset_binding() CASCADE;
        DROP FUNCTION IF EXISTS
          data.validate_v022_market_reconciliation_plan_complete() CASCADE;
        DROP FUNCTION IF EXISTS
          data.validate_v022_market_reconciliation_plan_resolution() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_v022_market_reconciliation_plan() CASCADE;
        DROP FUNCTION IF EXISTS
          data.validate_v022_market_gap_resolution_complete() CASCADE;
        DROP FUNCTION IF EXISTS
          data.validate_v022_market_gap_resolution_evidence() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_v022_market_gap_resolution() CASCADE;
        DROP FUNCTION IF EXISTS
          data.validate_v022_alternate_observation_complete() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_v022_alternate_observation_set() CASCADE;
        DROP TABLE data.v022_reconciled_market_dataset_binding;
        DROP TABLE data.v022_market_reconciliation_plan_resolution;
        DROP TABLE data.v022_market_reconciliation_plan;
        DROP TABLE data.v022_market_gap_resolution_evidence;
        DROP TABLE data.v022_market_gap_resolution;
        DROP TABLE data.v022_alternate_corporate_action;
        DROP TABLE data.v022_alternate_market_bar;
        DROP TABLE data.v022_alternate_observation_set;
        """
    )
