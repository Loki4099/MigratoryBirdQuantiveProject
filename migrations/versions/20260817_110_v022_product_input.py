# ruff: noqa: E501
"""Add immutable per-session v0.22 Product Input Snapshots.

Revision ID: 20260817_110_v022_product_input
Revises: 20260817_109_v022_ranking_guard
"""

from __future__ import annotations

from alembic import op

revision = "20260817_110_v022_product_input"
down_revision = "20260817_109_v022_ranking_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE product.v022_product_input_snapshot (
          product_input_snapshot_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          product_enrollment_id uuid NOT NULL
            REFERENCES product.v022_product_enrollment,
          execution_version_id uuid NOT NULL REFERENCES product.v022_execution_version,
          decision_session_id uuid NOT NULL
            REFERENCES product.v022_decision_schedule_session,
          product_data_disclosure_id uuid NOT NULL
            REFERENCES product.v022_product_data_disclosure,
          dataset_gate_assessment_id uuid NOT NULL
            REFERENCES data.v022_dataset_gate_assessment,
          dataset_gate_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          dataset_publication_id uuid NOT NULL REFERENCES data.dataset_publication,
          dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          universe_history_id uuid NOT NULL REFERENCES catalog.universe_history,
          universe_history_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          calendar_version_id uuid NOT NULL REFERENCES catalog.calendar_version,
          calendar_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          input_start date NOT NULL,
          input_end date NOT NULL,
          decision_cutoff_at timestamptz NOT NULL,
          inputs_available_at timestamptz NOT NULL,
          price_semantics varchar(180) NOT NULL CHECK (btrim(price_semantics)<>''),
          product_eligibility varchar(32) NOT NULL CHECK (
            product_eligibility IN ('eligible','eligible_with_warnings')
          ),
          warning_codes jsonb NOT NULL CHECK (jsonb_typeof(warning_codes)='array'),
          snapshot_document jsonb NOT NULL CHECK (jsonb_typeof(snapshot_document)='object'),
          snapshot_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (snapshot_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (product_enrollment_id,decision_session_id),
          UNIQUE (execution_version_id,decision_session_id),
          CHECK (input_start<=input_end),
          CHECK (decision_cutoff_at<=inputs_available_at),
          CHECK ((
            snapshot_document->>'contract_version'='v0.22.product_input_snapshot.v1' AND
            snapshot_document->>'product_enrollment_id'=product_enrollment_id::text AND
            snapshot_document->>'execution_version_id'=execution_version_id::text AND
            snapshot_document->>'decision_session_id'=decision_session_id::text AND
            snapshot_document->>'product_data_disclosure_id'=
              product_data_disclosure_id::text AND
            snapshot_document->>'dataset_gate_assessment_id'=
              dataset_gate_assessment_id::text AND
            snapshot_document->>'dataset_publication_id'=dataset_publication_id::text AND
            snapshot_document->>'universe_history_id'=universe_history_id::text AND
            snapshot_document->>'calendar_version_id'=calendar_version_id::text AND
            (snapshot_document->>'input_start')::date=input_start AND
            (snapshot_document->>'input_end')::date=input_end AND
            (snapshot_document->>'decision_cutoff_at')::timestamptz=decision_cutoff_at AND
            (snapshot_document->>'inputs_available_at')::timestamptz=inputs_available_at AND
            snapshot_document->>'price_semantics'=price_semantics AND
            snapshot_document->>'product_eligibility'=product_eligibility AND
            snapshot_document->'warning_codes'=warning_codes AND
            snapshot_document->>'runtime_network_access'='false'
          ) IS TRUE)
        );

        CREATE FUNCTION product.validate_v022_product_input_snapshot()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; enrollment_row record; session_row record;
                first_row record; disclosure_row record; baseline_row record;
                gate_row record; dataset_row record; history_row record;
                calendar_row record; latest_lifecycle varchar; dependency_count integer;
                expected_available_at timestamptz;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number,status INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT enrollment.*,artifact.status,artifact.artifact_id AS enrollment_artifact_id
            INTO enrollment_row FROM product.v022_product_enrollment enrollment
            JOIN lineage.artifact artifact ON artifact.artifact_id=enrollment.artifact_id
           WHERE enrollment.product_enrollment_id=NEW.product_enrollment_id;
          SELECT * INTO session_row FROM product.v022_decision_schedule_session
           WHERE decision_session_id=NEW.decision_session_id;
          SELECT * INTO first_row FROM product.v022_decision_schedule_session
           WHERE decision_session_id=enrollment_row.first_eligible_decision_session_id;
          SELECT disclosure.*,artifact.status,
                 artifact.artifact_id AS disclosure_artifact_id
            INTO disclosure_row FROM product.v022_product_data_disclosure disclosure
            JOIN lineage.artifact artifact ON artifact.artifact_id=disclosure.artifact_id
           WHERE disclosure.execution_version_id=NEW.execution_version_id;
          SELECT cohort.warmup_start,cohort.dataset_publication_id,
                 baseline_dataset.dataset_key,
                 baseline_history.universe_methodology_id,
                 baseline_calendar.calendar_definition_id
            INTO baseline_row
            FROM experiment.v022_evaluation_cohort_version cohort
            JOIN data.dataset_publication baseline_dataset
              ON baseline_dataset.dataset_publication_id=cohort.dataset_publication_id
            JOIN catalog.universe_history baseline_history
              ON baseline_history.universe_history_id=cohort.universe_history_id
            JOIN catalog.calendar_version baseline_calendar
              ON baseline_calendar.calendar_version_id=cohort.calendar_version_id
           WHERE cohort.evaluation_cohort_version_id=
                 disclosure_row.evaluation_cohort_version_id;
          SELECT gate.*,artifact.status,artifact.published_at
            INTO gate_row FROM data.v022_dataset_gate_assessment gate
            JOIN lineage.artifact artifact ON artifact.artifact_id=gate.artifact_id
           WHERE gate.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id;
          SELECT publication.*,artifact.status,artifact.published_at
            INTO dataset_row FROM data.dataset_publication publication
            JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
           WHERE publication.dataset_publication_id=NEW.dataset_publication_id;
          SELECT history.*,artifact.status,artifact.published_at
            INTO history_row FROM catalog.universe_history history
            JOIN lineage.artifact artifact ON artifact.artifact_id=history.artifact_id
           WHERE history.universe_history_id=NEW.universe_history_id;
          SELECT version.*,artifact.status,artifact.published_at
            INTO calendar_row FROM catalog.calendar_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.calendar_version_id=NEW.calendar_version_id;
          SELECT event.to_lifecycle INTO latest_lifecycle
            FROM product.v022_enrollment_lifecycle_event event
           WHERE event.product_enrollment_id=NEW.product_enrollment_id
             AND event.effective_at<=NEW.inputs_available_at
           ORDER BY event.effective_at DESC,event.sequence_number DESC LIMIT 1;
          expected_available_at := greatest(
            dataset_row.published_at,history_row.published_at,
            calendar_row.published_at,gate_row.published_at
          );
          IF artifact_row.artifact_type IS DISTINCT FROM 'v022_product_input_snapshot' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_product_input_snapshot__' || NEW.product_enrollment_id::text ||
               '__' || NEW.decision_session_id::text OR
             artifact_row.version_number IS DISTINCT FROM 1 OR
             artifact_row.status IS DISTINCT FROM 'draft' OR
             enrollment_row.status IS DISTINCT FROM 'published' OR
             enrollment_row.execution_version_id IS DISTINCT FROM NEW.execution_version_id OR
             session_row.decision_schedule_version_id IS DISTINCT FROM
               enrollment_row.decision_schedule_version_id OR
             session_row.ordinal<first_row.ordinal OR
             session_row.decision_cutoff_at IS DISTINCT FROM NEW.decision_cutoff_at OR
             session_row.session_date IS DISTINCT FROM NEW.input_end OR
             coalesce(latest_lifecycle,'active') IS DISTINCT FROM 'active' OR
             disclosure_row.status IS DISTINCT FROM 'published' OR
             disclosure_row.product_data_disclosure_id IS DISTINCT FROM
               NEW.product_data_disclosure_id OR
             baseline_row.warmup_start IS DISTINCT FROM NEW.input_start OR
             gate_row.status IS DISTINCT FROM 'published' OR
             gate_row.artifact_id IS DISTINCT FROM NEW.dataset_gate_artifact_id OR
             gate_row.dataset_publication_id IS DISTINCT FROM NEW.dataset_publication_id OR
             gate_row.dataset_artifact_id IS DISTINCT FROM NEW.dataset_artifact_id OR
             gate_row.universe_history_id IS DISTINCT FROM NEW.universe_history_id OR
             gate_row.universe_history_artifact_id IS DISTINCT FROM
               NEW.universe_history_artifact_id OR
             gate_row.calendar_version_id IS DISTINCT FROM NEW.calendar_version_id OR
             gate_row.calendar_artifact_id IS DISTINCT FROM NEW.calendar_artifact_id OR
             gate_row.price_semantics IS DISTINCT FROM NEW.price_semantics OR
             gate_row.product_eligibility IS DISTINCT FROM NEW.product_eligibility OR
             gate_row.product_eligibility='ineligible' OR gate_row.blocker_count<>0 OR
             gate_row.assessed_coverage_start>NEW.input_start OR
             gate_row.assessed_coverage_end<NEW.input_end OR
             dataset_row.status IS DISTINCT FROM 'published' OR
             dataset_row.artifact_id IS DISTINCT FROM NEW.dataset_artifact_id OR
             dataset_row.dataset_kind IS DISTINCT FROM 'canonical' OR
             dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
             dataset_row.dataset_key IS DISTINCT FROM baseline_row.dataset_key OR
             dataset_row.coverage_start>NEW.input_start OR
             dataset_row.coverage_end<NEW.input_end OR
             history_row.status IS DISTINCT FROM 'published' OR
             history_row.artifact_id IS DISTINCT FROM NEW.universe_history_artifact_id OR
             history_row.universe_methodology_id IS DISTINCT FROM
               baseline_row.universe_methodology_id OR
             calendar_row.status IS DISTINCT FROM 'published' OR
             calendar_row.artifact_id IS DISTINCT FROM NEW.calendar_artifact_id OR
             calendar_row.calendar_definition_id IS DISTINCT FROM
               baseline_row.calendar_definition_id OR
             calendar_row.coverage_start>NEW.input_start OR
             calendar_row.coverage_end<NEW.input_end OR
             NOT EXISTS (SELECT 1 FROM catalog.calendar_session calendar_session
               WHERE calendar_session.calendar_version_id=NEW.calendar_version_id
                 AND calendar_session.session_date=NEW.input_end) OR
             expected_available_at IS DISTINCT FROM NEW.inputs_available_at OR
             NEW.inputs_available_at<NEW.decision_cutoff_at THEN
            RAISE EXCEPTION 'Product Input Snapshot exact frozen input closure invalid';
          END IF;
          SELECT count(*) INTO dependency_count FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id;
          IF dependency_count<>6 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=enrollment_row.enrollment_artifact_id
                 AND dependency.role='enrollment' AND dependency.ordinal=0) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=disclosure_row.disclosure_artifact_id
                 AND dependency.role='product_data_disclosure' AND dependency.ordinal=1) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.dataset_artifact_id
                 AND dependency.role='market_dataset' AND dependency.ordinal=2) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.universe_history_artifact_id
                 AND dependency.role='universe_history' AND dependency.ordinal=3) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.calendar_artifact_id
                 AND dependency.role='calendar_version' AND dependency.ordinal=4) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=NEW.dataset_gate_artifact_id
                 AND dependency.role='dataset_gate' AND dependency.ordinal=5) THEN
            RAISE EXCEPTION 'Product Input Snapshot Artifact lineage invalid';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_product_input_snapshot_validate
          BEFORE INSERT ON product.v022_product_input_snapshot
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_product_input_snapshot();
        CREATE TRIGGER trg_v022_product_input_snapshot_append_only
          BEFORE UPDATE OR DELETE ON product.v022_product_input_snapshot
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM product.v022_product_input_snapshot) THEN
            RAISE EXCEPTION 'Cannot downgrade nonempty Product Input Snapshot state';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS product.validate_v022_product_input_snapshot() CASCADE;
        DROP TABLE product.v022_product_input_snapshot;
        """
    )
