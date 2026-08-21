# ruff: noqa: E501
"""Add immutable v0.22 Dataset Gate Assessments.

Revision ID: 20260817_105_v022_dataset_gate
Revises: 20260817_104_v022_reconciliation
"""

from __future__ import annotations

from alembic import op

revision = "20260817_105_v022_dataset_gate"
down_revision = "20260817_104_v022_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE data.v022_dataset_gate_assessment (
          dataset_gate_assessment_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          dataset_publication_id uuid NOT NULL REFERENCES data.dataset_publication,
          dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          universe_membership_ledger_id uuid NOT NULL
            REFERENCES catalog.v022_universe_membership_ledger,
          universe_membership_ledger_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          universe_history_id uuid NOT NULL REFERENCES catalog.universe_history,
          universe_history_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          security_market_quality_report_id uuid NOT NULL
            REFERENCES data.v022_security_market_quality_report,
          quality_report_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          calendar_version_id uuid NOT NULL REFERENCES catalog.calendar_version,
          calendar_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          cleaning_version_id uuid NOT NULL REFERENCES data.cleaning_version,
          cleaning_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          gate_key varchar(200) NOT NULL CHECK (btrim(gate_key)<>''),
          version_number integer NOT NULL CHECK (version_number>=1),
          assessed_coverage_start date NOT NULL,
          assessed_coverage_end date NOT NULL,
          price_semantics varchar(180) NOT NULL CHECK (btrim(price_semantics)<>''),
          historical_pit_claimed boolean NOT NULL CHECK (historical_pit_claimed=false),
          ranking_eligibility varchar(32) NOT NULL CHECK (
            ranking_eligibility IN ('rankable_research','exploratory_only')
          ),
          product_eligibility varchar(32) NOT NULL CHECK (
            product_eligibility IN ('eligible','eligible_with_warnings','ineligible')
          ),
          finding_count integer NOT NULL CHECK (finding_count>=0),
          warning_count integer NOT NULL CHECK (warning_count>=0),
          blocker_count integer NOT NULL CHECK (blocker_count>=0),
          evidence_count integer NOT NULL CHECK (evidence_count>=1),
          uniform_exclusion_count integer NOT NULL CHECK (uniform_exclusion_count>=0),
          identity_resolution_count integer NOT NULL CHECK (identity_resolution_count>=0),
          lifecycle_event_count integer NOT NULL CHECK (lifecycle_event_count>=0),
          gap_resolution_count integer NOT NULL CHECK (gap_resolution_count>=0),
          alternate_observation_count integer NOT NULL CHECK (alternate_observation_count>=0),
          assessment_document jsonb NOT NULL CHECK (jsonb_typeof(assessment_document)='object'),
          assessment_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (assessment_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (gate_key,version_number),
          CHECK (assessed_coverage_start<=assessed_coverage_end),
          CHECK (warning_count+blocker_count<=finding_count),
          CHECK ((
            assessment_document->>'contract_version'='v0.22.dataset_gate_assessment.v1' AND
            assessment_document->>'dataset_publication_id'=dataset_publication_id::text AND
            assessment_document->>'universe_membership_ledger_id'=
              universe_membership_ledger_id::text AND
            assessment_document->>'universe_history_id'=universe_history_id::text AND
            assessment_document->>'quality_report_artifact_id'=
              quality_report_artifact_id::text AND
            assessment_document->>'calendar_version_id'=calendar_version_id::text AND
            assessment_document->>'cleaning_version_id'=cleaning_version_id::text AND
            assessment_document->>'gate_key'=gate_key AND
            (assessment_document->>'version_number')::integer=version_number AND
            (assessment_document->>'assessed_coverage_start')::date=assessed_coverage_start AND
            (assessment_document->>'assessed_coverage_end')::date=assessed_coverage_end AND
            assessment_document->>'price_semantics'=price_semantics AND
            (assessment_document->>'historical_pit_claimed')::boolean=false AND
            assessment_document->>'ranking_eligibility'=ranking_eligibility AND
            assessment_document->>'product_eligibility'=product_eligibility AND
            (assessment_document->>'finding_count')::integer=finding_count AND
            (assessment_document->>'warning_count')::integer=warning_count AND
            (assessment_document->>'blocker_count')::integer=blocker_count AND
            (assessment_document->>'evidence_count')::integer=evidence_count AND
            (assessment_document->>'uniform_exclusion_count')::integer=
              uniform_exclusion_count AND
            (assessment_document->>'identity_resolution_count')::integer=
              identity_resolution_count AND
            (assessment_document->>'lifecycle_event_count')::integer=
              lifecycle_event_count AND
            (assessment_document->>'gap_resolution_count')::integer=
              gap_resolution_count AND
            (assessment_document->>'alternate_observation_count')::integer=
              alternate_observation_count
          ) IS TRUE)
        );

        CREATE TABLE data.v022_dataset_gate_assessment_evidence (
          dataset_gate_assessment_id uuid NOT NULL
            REFERENCES data.v022_dataset_gate_assessment,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          evidence_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          evidence_role varchar(40) NOT NULL CHECK (evidence_role IN (
            'identity_resolution','lifecycle_event','gap_resolution',
            'reconciliation_plan','supporting_evidence'
          )),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (dataset_gate_assessment_id,ordinal),
          UNIQUE (dataset_gate_assessment_id,evidence_artifact_id)
        );

        CREATE TABLE data.v022_dataset_gate_finding (
          dataset_gate_assessment_id uuid NOT NULL
            REFERENCES data.v022_dataset_gate_assessment,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          finding_code varchar(120) NOT NULL CHECK (btrim(finding_code)<>''),
          finding_category varchar(40) NOT NULL CHECK (finding_category IN (
            'data_provenance','identity','membership','market_coverage','lifecycle',
            'settlement','replay','benchmark_calendar','uniform_exclusion'
          )),
          severity varchar(16) NOT NULL CHECK (severity IN ('notice','warning','blocker')),
          ranking_effect varchar(24) NOT NULL CHECK (
            ranking_effect IN ('none','exploratory_only')
          ),
          product_effect varchar(24) NOT NULL CHECK (
            product_effect IN ('none','warning','ineligible')
          ),
          security_id uuid NULL REFERENCES catalog.security,
          evidence_artifact_id uuid NULL REFERENCES lineage.artifact,
          finding_document jsonb NOT NULL CHECK (jsonb_typeof(finding_document)='object'),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (dataset_gate_assessment_id,ordinal),
          UNIQUE (dataset_gate_assessment_id,finding_code,security_id),
          CHECK ((severity='notice' AND product_effect='none') OR
                 (severity='warning' AND product_effect IN ('none','warning')) OR
                 (severity='blocker' AND product_effect='ineligible')),
          CHECK ((
            (finding_document->>'ordinal')::integer=ordinal AND
            finding_document->>'finding_code'=finding_code AND
            finding_document->>'finding_category'=finding_category AND
            finding_document->>'severity'=severity AND
            finding_document->>'ranking_effect'=ranking_effect AND
            finding_document->>'product_effect'=product_effect AND
            CASE WHEN security_id IS NULL
              THEN finding_document->'security_id'='null'::jsonb
              ELSE finding_document->>'security_id'=security_id::text
            END AND
            CASE WHEN evidence_artifact_id IS NULL
              THEN finding_document->'evidence_artifact_id'='null'::jsonb
              ELSE finding_document->>'evidence_artifact_id'=evidence_artifact_id::text
            END
          ) IS TRUE)
        );

        CREATE TABLE data.v022_dataset_gate_uniform_exclusion (
          dataset_gate_assessment_id uuid NOT NULL
            REFERENCES data.v022_dataset_gate_assessment,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          security_id uuid NOT NULL REFERENCES catalog.security,
          exclusion_start date NOT NULL,
          exclusion_end date NOT NULL,
          reason_code varchar(120) NOT NULL CHECK (btrim(reason_code)<>''),
          evidence_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          exclusion_document jsonb NOT NULL CHECK (jsonb_typeof(exclusion_document)='object'),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (dataset_gate_assessment_id,ordinal),
          UNIQUE (dataset_gate_assessment_id,security_id),
          CHECK (exclusion_start<=exclusion_end),
          CHECK ((
            (exclusion_document->>'ordinal')::integer=ordinal AND
            exclusion_document->>'security_id'=security_id::text AND
            (exclusion_document->>'exclusion_start')::date=exclusion_start AND
            (exclusion_document->>'exclusion_end')::date=exclusion_end AND
            exclusion_document->>'reason_code'=reason_code AND
            exclusion_document->>'evidence_artifact_id'=evidence_artifact_id::text
          ) IS TRUE)
        );

        CREATE FUNCTION data.validate_v022_dataset_gate_assessment()
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

        CREATE FUNCTION data.validate_v022_dataset_gate_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE assessment_artifact uuid; assessed_dataset uuid;
                evidence_type varchar; evidence_status varchar;
        BEGIN
          SELECT artifact_id,dataset_publication_id
            INTO assessment_artifact,assessed_dataset
            FROM data.v022_dataset_gate_assessment
           WHERE dataset_gate_assessment_id=NEW.dataset_gate_assessment_id;
          SELECT artifact_type,status INTO evidence_type,evidence_status
            FROM lineage.artifact WHERE artifact_id=NEW.evidence_artifact_id;
          IF evidence_status IS DISTINCT FROM 'published' OR
             (NEW.evidence_role='identity_resolution' AND
              evidence_type IS DISTINCT FROM 'v022_security_identity_resolution') OR
             (NEW.evidence_role='lifecycle_event' AND
              evidence_type IS DISTINCT FROM 'v022_security_lifecycle_event') OR
             (NEW.evidence_role='gap_resolution' AND
              evidence_type IS DISTINCT FROM 'v022_market_gap_resolution') OR
             (NEW.evidence_role='reconciliation_plan' AND
              evidence_type IS DISTINCT FROM 'v022_market_reconciliation_plan') OR
             (NEW.evidence_role='reconciliation_plan' AND NOT EXISTS (
               SELECT 1 FROM data.v022_reconciled_market_dataset_binding binding
                WHERE binding.dataset_publication_id=assessed_dataset
                  AND binding.plan_artifact_id=NEW.evidence_artifact_id
             )) OR
             (NEW.evidence_role='gap_resolution' AND NOT EXISTS (
               SELECT 1 FROM data.v022_reconciled_market_dataset_binding binding
               JOIN data.v022_market_reconciliation_plan_resolution resolution
                 ON resolution.market_reconciliation_plan_id=
                    binding.market_reconciliation_plan_id
                WHERE binding.dataset_publication_id=assessed_dataset
                  AND resolution.resolution_artifact_id=NEW.evidence_artifact_id
             )) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=assessment_artifact
                 AND dependency.depends_on_artifact_id=NEW.evidence_artifact_id
                 AND dependency.role='gate_evidence' AND dependency.ordinal=NEW.ordinal+6)
          THEN
            RAISE EXCEPTION 'Dataset Gate evidence is not exact or published';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION data.validate_v022_dataset_gate_finding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.evidence_artifact_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM data.v022_dataset_gate_assessment_evidence evidence
             WHERE evidence.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
               AND evidence.evidence_artifact_id=NEW.evidence_artifact_id
          ) THEN
            RAISE EXCEPTION 'Dataset Gate finding evidence is outside the frozen evidence set';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION data.validate_v022_dataset_gate_exclusion()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE assessment_row record;
        BEGIN
          SELECT assessed_coverage_start,assessed_coverage_end,
                 universe_membership_ledger_id INTO assessment_row
            FROM data.v022_dataset_gate_assessment
           WHERE dataset_gate_assessment_id=NEW.dataset_gate_assessment_id;
          IF NEW.exclusion_start<assessment_row.assessed_coverage_start OR
             NEW.exclusion_end>assessment_row.assessed_coverage_end OR
             NOT EXISTS (SELECT 1 FROM data.v022_dataset_gate_assessment_evidence evidence
               WHERE evidence.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
                 AND evidence.evidence_artifact_id=NEW.evidence_artifact_id) OR
             NOT EXISTS (
               SELECT 1 FROM catalog.v022_universe_membership_event event
               JOIN catalog.v022_universe_change_batch batch
                 ON batch.universe_change_batch_id=event.universe_change_batch_id
              WHERE batch.universe_membership_ledger_id=
                    assessment_row.universe_membership_ledger_id
                AND event.security_id=NEW.security_id
             )
          THEN
            RAISE EXCEPTION 'Uniform exclusion is outside the frozen Gate scope';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION data.validate_v022_dataset_gate_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_status_value varchar; actual_evidence integer;
                actual_findings integer; actual_warnings integer; actual_blockers integer;
                actual_exclusions integer; evidence_max integer; finding_max integer;
                exclusion_max integer; derived_ranking varchar; derived_product varchar;
                reconciled_row record; actual_gap_resolutions integer;
                actual_alternate_observations integer;
        BEGIN
          SELECT status INTO artifact_status_value FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          SELECT count(*),max(ordinal) INTO actual_evidence,evidence_max
            FROM data.v022_dataset_gate_assessment_evidence
           WHERE dataset_gate_assessment_id=NEW.dataset_gate_assessment_id;
          SELECT count(*),count(*) FILTER (WHERE severity='warning'),
                 count(*) FILTER (WHERE severity='blocker'),max(ordinal)
            INTO actual_findings,actual_warnings,actual_blockers,finding_max
            FROM data.v022_dataset_gate_finding
           WHERE dataset_gate_assessment_id=NEW.dataset_gate_assessment_id;
          SELECT count(*),max(ordinal) INTO actual_exclusions,exclusion_max
            FROM data.v022_dataset_gate_uniform_exclusion
           WHERE dataset_gate_assessment_id=NEW.dataset_gate_assessment_id;
          SELECT CASE WHEN EXISTS (
                   SELECT 1 FROM data.v022_dataset_gate_finding finding
                    WHERE finding.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
                      AND finding.ranking_effect='exploratory_only'
                 ) THEN 'exploratory_only' ELSE 'rankable_research' END
            INTO derived_ranking;
          SELECT CASE WHEN EXISTS (
                   SELECT 1 FROM data.v022_dataset_gate_finding finding
                    WHERE finding.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
                      AND finding.product_effect='ineligible'
                 ) THEN 'ineligible'
                 WHEN EXISTS (
                   SELECT 1 FROM data.v022_dataset_gate_finding finding
                    WHERE finding.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
                      AND finding.product_effect='warning'
                 ) THEN 'eligible_with_warnings' ELSE 'eligible' END
            INTO derived_product;
          SELECT binding.market_reconciliation_plan_id,binding.plan_artifact_id,
                 plan.resolution_count INTO reconciled_row
            FROM data.v022_reconciled_market_dataset_binding binding
            JOIN data.v022_market_reconciliation_plan plan
              ON plan.market_reconciliation_plan_id=binding.market_reconciliation_plan_id
           WHERE binding.dataset_publication_id=NEW.dataset_publication_id;
          SELECT count(*) INTO actual_gap_resolutions
            FROM data.v022_dataset_gate_assessment_evidence evidence
           WHERE evidence.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
             AND evidence.evidence_role='gap_resolution';
          SELECT count(DISTINCT resolution.alternate_observation_set_id)
            INTO actual_alternate_observations
            FROM data.v022_dataset_gate_assessment_evidence evidence
            JOIN data.v022_market_gap_resolution resolution
              ON resolution.artifact_id=evidence.evidence_artifact_id
           WHERE evidence.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
             AND evidence.evidence_role='gap_resolution'
             AND resolution.alternate_observation_set_id IS NOT NULL;
          IF artifact_status_value IS DISTINCT FROM 'published' OR
             actual_evidence<>NEW.evidence_count OR evidence_max<>NEW.evidence_count-1 OR
             actual_findings<>NEW.finding_count OR actual_warnings<>NEW.warning_count OR
             actual_blockers<>NEW.blocker_count OR
             (NEW.finding_count>0 AND finding_max<>NEW.finding_count-1) OR
             (NEW.finding_count=0 AND finding_max IS NOT NULL) OR
             actual_exclusions<>NEW.uniform_exclusion_count OR
             (NEW.uniform_exclusion_count>0 AND
              exclusion_max<>NEW.uniform_exclusion_count-1) OR
             (NEW.uniform_exclusion_count=0 AND exclusion_max IS NOT NULL) OR
             derived_ranking IS DISTINCT FROM NEW.ranking_eligibility OR
             derived_product IS DISTINCT FROM NEW.product_eligibility OR
             NEW.identity_resolution_count<>(SELECT count(*)
               FROM data.v022_dataset_gate_assessment_evidence evidence
              WHERE evidence.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
                AND evidence.evidence_role='identity_resolution') OR
             NEW.lifecycle_event_count<>(SELECT count(*)
               FROM data.v022_dataset_gate_assessment_evidence evidence
              WHERE evidence.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
                AND evidence.evidence_role='lifecycle_event') OR
             NEW.gap_resolution_count<>actual_gap_resolutions OR
             NEW.alternate_observation_count<>actual_alternate_observations OR
             NOT EXISTS (SELECT 1 FROM data.v022_dataset_gate_finding finding
               WHERE finding.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
                 AND finding.finding_code='historical_membership_retrospective'
                 AND finding.product_effect='warning') OR
             NOT EXISTS (SELECT 1 FROM data.v022_dataset_gate_finding finding
               WHERE finding.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
                 AND finding.finding_code='retrospective_price_snapshot'
                 AND finding.product_effect='warning') OR
             EXISTS (
               SELECT 1 FROM data.v022_dataset_gate_uniform_exclusion exclusion
                WHERE exclusion.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
                  AND NOT EXISTS (
                    SELECT 1 FROM data.v022_dataset_gate_finding finding
                     WHERE finding.dataset_gate_assessment_id=
                           exclusion.dataset_gate_assessment_id
                       AND finding.security_id=exclusion.security_id
                       AND finding.finding_category='uniform_exclusion'
                       AND finding.ranking_effect='none'
                       AND finding.product_effect='warning'
                  )
             ) OR
             (reconciled_row.market_reconciliation_plan_id IS NOT NULL AND (
               NOT EXISTS (SELECT 1 FROM data.v022_dataset_gate_assessment_evidence evidence
                 WHERE evidence.dataset_gate_assessment_id=NEW.dataset_gate_assessment_id
                   AND evidence.evidence_role='reconciliation_plan'
                   AND evidence.evidence_artifact_id=reconciled_row.plan_artifact_id) OR
               actual_gap_resolutions<>reconciled_row.resolution_count OR
               EXISTS (
                 SELECT 1 FROM data.v022_market_reconciliation_plan_resolution plan_resolution
                  WHERE plan_resolution.market_reconciliation_plan_id=
                        reconciled_row.market_reconciliation_plan_id
                    AND NOT EXISTS (
                      SELECT 1 FROM data.v022_dataset_gate_assessment_evidence evidence
                       WHERE evidence.dataset_gate_assessment_id=
                             NEW.dataset_gate_assessment_id
                         AND evidence.evidence_role='gap_resolution'
                         AND evidence.evidence_artifact_id=
                             plan_resolution.resolution_artifact_id
                    )
               )
             )) THEN
            RAISE EXCEPTION 'Dataset Gate Assessment projection is incomplete or inconsistent';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_dataset_gate_assessment_validate
          BEFORE INSERT ON data.v022_dataset_gate_assessment
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_dataset_gate_assessment();
        CREATE TRIGGER trg_v022_dataset_gate_evidence_validate
          BEFORE INSERT ON data.v022_dataset_gate_assessment_evidence
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_dataset_gate_evidence();
        CREATE TRIGGER trg_v022_dataset_gate_finding_validate
          BEFORE INSERT ON data.v022_dataset_gate_finding
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_dataset_gate_finding();
        CREATE TRIGGER trg_v022_dataset_gate_exclusion_validate
          BEFORE INSERT ON data.v022_dataset_gate_uniform_exclusion
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_dataset_gate_exclusion();
        CREATE CONSTRAINT TRIGGER trg_v022_dataset_gate_complete
          AFTER INSERT ON data.v022_dataset_gate_assessment
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION data.validate_v022_dataset_gate_complete();

        CREATE TRIGGER trg_v022_dataset_gate_assessment_append_only
          BEFORE UPDATE OR DELETE ON data.v022_dataset_gate_assessment
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_dataset_gate_evidence_append_only
          BEFORE UPDATE OR DELETE ON data.v022_dataset_gate_assessment_evidence
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_dataset_gate_finding_append_only
          BEFORE UPDATE OR DELETE ON data.v022_dataset_gate_finding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_dataset_gate_exclusion_append_only
          BEFORE UPDATE OR DELETE ON data.v022_dataset_gate_uniform_exclusion
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM data.v022_dataset_gate_assessment) THEN
            RAISE EXCEPTION 'Cannot downgrade with v0.22 Dataset Gate Assessments';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS data.validate_v022_dataset_gate_complete() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_v022_dataset_gate_exclusion() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_v022_dataset_gate_finding() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_v022_dataset_gate_evidence() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_v022_dataset_gate_assessment() CASCADE;
        DROP TABLE data.v022_dataset_gate_uniform_exclusion;
        DROP TABLE data.v022_dataset_gate_finding;
        DROP TABLE data.v022_dataset_gate_assessment_evidence;
        DROP TABLE data.v022_dataset_gate_assessment;
        """
    )
