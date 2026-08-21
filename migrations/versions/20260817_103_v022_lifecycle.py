# ruff: noqa: E501
"""Add immutable lifecycle, tradability, and settlement evidence.

Revision ID: 20260817_103_v022_lifecycle
Revises: 20260817_102_v022_identity
"""

from __future__ import annotations

from alembic import op

revision = "20260817_103_v022_lifecycle"
down_revision = "20260817_102_v022_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE catalog.v022_security_lifecycle_event (
          security_lifecycle_event_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          security_id uuid NOT NULL REFERENCES catalog.security,
          event_key varchar(200) NOT NULL CHECK (btrim(event_key)<>''),
          version_number integer NOT NULL CHECK (version_number>=1),
          event_type varchar(40) NOT NULL CHECK (event_type IN (
            'trading_halt','trading_resume','delisting','otc_transition',
            'cash_merger','stock_merger','share_class_conversion','spinoff',
            'bankruptcy','liquidation'
          )),
          event_status varchar(20) NOT NULL
            CHECK (event_status IN ('confirmed','estimated','unresolved')),
          announced_at timestamptz NOT NULL,
          effective_session date NOT NULL,
          last_trading_session date NULL,
          settlement_session date NULL,
          selectable_after boolean NOT NULL,
          tradable_after boolean NOT NULL,
          valuation_state_after varchar(24) NOT NULL CHECK (
            valuation_state_after IN ('live','stale_confirmed','terminal','unavailable')
          ),
          evidence_count integer NOT NULL CHECK (evidence_count>=1),
          settlement_leg_count integer NOT NULL CHECK (settlement_leg_count>=0),
          supersedes_lifecycle_event_id uuid NULL
            REFERENCES catalog.v022_security_lifecycle_event,
          event_document jsonb NOT NULL CHECK (jsonb_typeof(event_document)='object'),
          event_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (event_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (security_id,event_key,version_number),
          CHECK (announced_at::date<=effective_session),
          CHECK (last_trading_session IS NULL OR
                 last_trading_session<=effective_session),
          CHECK (settlement_session IS NULL OR
                 settlement_session>=effective_session),
          CHECK ((settlement_leg_count=0 AND settlement_session IS NULL) OR
                 (settlement_leg_count>0 AND settlement_session IS NOT NULL)),
          CHECK ((event_type='trading_halt' AND
                  selectable_after=false AND tradable_after=false AND
                  valuation_state_after='stale_confirmed' AND
                  settlement_leg_count=0) OR event_type<>'trading_halt'),
          CHECK ((event_type='trading_resume' AND
                  selectable_after=true AND tradable_after=true AND
                  valuation_state_after='live' AND settlement_leg_count=0) OR
                 event_type<>'trading_resume'),
          CHECK ((event_type IN (
                    'delisting','cash_merger','stock_merger',
                    'share_class_conversion','bankruptcy','liquidation'
                  ) AND selectable_after=false AND tradable_after=false AND
                  valuation_state_after='terminal') OR
                 event_type NOT IN (
                    'delisting','cash_merger','stock_merger',
                    'share_class_conversion','bankruptcy','liquidation'
                 )),
          CHECK ((event_type='spinoff' AND
                  selectable_after=true AND tradable_after=true AND
                  valuation_state_after='live') OR event_type<>'spinoff'),
          CHECK ((event_status='confirmed' AND event_type IN (
                    'delisting','cash_merger','stock_merger',
                    'share_class_conversion','spinoff','bankruptcy','liquidation'
                  ) AND settlement_leg_count>0) OR
                 event_status<>'confirmed' OR event_type NOT IN (
                    'delisting','cash_merger','stock_merger',
                    'share_class_conversion','spinoff','bankruptcy','liquidation'
                 )),
          CHECK ((
            event_document->>'contract_version'='v0.22.security_lifecycle_event.v1' AND
            event_document->>'security_id'=security_id::text AND
            event_document->>'event_key'=event_key AND
            (event_document->>'version_number')::integer=version_number AND
            event_document->>'event_type'=event_type AND
            event_document->>'event_status'=event_status AND
            (event_document->>'announced_at')::timestamptz=announced_at AND
            (event_document->>'effective_session')::date=effective_session AND
            CASE WHEN last_trading_session IS NULL
              THEN event_document->'last_trading_session'='null'::jsonb
              ELSE (event_document->>'last_trading_session')::date=last_trading_session
            END AND
            CASE WHEN settlement_session IS NULL
              THEN event_document->'settlement_session'='null'::jsonb
              ELSE (event_document->>'settlement_session')::date=settlement_session
            END AND
            (event_document->>'selectable_after')::boolean=selectable_after AND
            (event_document->>'tradable_after')::boolean=tradable_after AND
            event_document->>'valuation_state_after'=valuation_state_after AND
            (event_document->>'evidence_count')::integer=evidence_count AND
            (event_document->>'settlement_leg_count')::integer=settlement_leg_count AND
            CASE WHEN supersedes_lifecycle_event_id IS NULL
              THEN event_document->'supersedes_lifecycle_event_id'='null'::jsonb
              ELSE event_document->>'supersedes_lifecycle_event_id'=
                   supersedes_lifecycle_event_id::text
            END
          ) IS TRUE)
        );

        CREATE TABLE catalog.v022_security_lifecycle_event_evidence (
          security_lifecycle_event_id uuid NOT NULL
            REFERENCES catalog.v022_security_lifecycle_event,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          evidence_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          evidence_role varchar(32) NOT NULL CHECK (evidence_role IN (
            'primary_notice','market_status','corporate_action_terms',
            'identity_resolution','other_public_record'
          )),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (security_lifecycle_event_id,ordinal),
          UNIQUE (security_lifecycle_event_id,evidence_artifact_id)
        );

        CREATE TABLE catalog.v022_security_settlement_leg (
          security_lifecycle_event_id uuid NOT NULL
            REFERENCES catalog.v022_security_lifecycle_event,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          leg_kind varchar(32) NOT NULL CHECK (leg_kind IN (
            'cash','successor_security','distributed_security','writeoff'
          )),
          target_security_id uuid NULL REFERENCES catalog.security,
          quantity_per_source_share numeric(36,18) NULL,
          cash_amount_per_source_share numeric(36,18) NULL,
          currency varchar(3) NULL,
          valuation_policy varchar(40) NOT NULL CHECK (valuation_policy IN (
            'fixed_cash','successor_market_value','distribution_market_value',
            'zero_recovery'
          )),
          leg_document jsonb NOT NULL CHECK (jsonb_typeof(leg_document)='object'),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (security_lifecycle_event_id,ordinal),
          CHECK ((leg_kind='cash' AND target_security_id IS NULL AND
                  quantity_per_source_share IS NULL AND
                  cash_amount_per_source_share IS NOT NULL AND
                  cash_amount_per_source_share>=0 AND currency IS NOT NULL AND
                  valuation_policy='fixed_cash') OR
                 (leg_kind IN ('successor_security','distributed_security') AND
                  target_security_id IS NOT NULL AND
                  quantity_per_source_share IS NOT NULL AND
                  quantity_per_source_share>0 AND
                  cash_amount_per_source_share IS NULL AND currency IS NULL AND
                  valuation_policy=CASE leg_kind
                    WHEN 'successor_security' THEN 'successor_market_value'
                    ELSE 'distribution_market_value' END) OR
                 (leg_kind='writeoff' AND target_security_id IS NULL AND
                  quantity_per_source_share IS NULL AND
                  cash_amount_per_source_share IS NULL AND currency IS NULL AND
                  valuation_policy='zero_recovery')),
          CHECK ((
            (leg_document->>'ordinal')::integer=ordinal AND
            leg_document->>'leg_kind'=leg_kind AND
            CASE WHEN target_security_id IS NULL
              THEN leg_document->'target_security_id'='null'::jsonb
              ELSE leg_document->>'target_security_id'=target_security_id::text
            END AND
            CASE WHEN quantity_per_source_share IS NULL
              THEN leg_document->'quantity_per_source_share'='null'::jsonb
              ELSE (leg_document->>'quantity_per_source_share')::numeric=
                   quantity_per_source_share
            END AND
            CASE WHEN cash_amount_per_source_share IS NULL
              THEN leg_document->'cash_amount_per_source_share'='null'::jsonb
              ELSE (leg_document->>'cash_amount_per_source_share')::numeric=
                   cash_amount_per_source_share
            END AND
            CASE WHEN currency IS NULL
              THEN leg_document->'currency'='null'::jsonb
              ELSE leg_document->>'currency'=currency
            END AND
            leg_document->>'valuation_policy'=valuation_policy
          ) IS TRUE)
        );

        CREATE FUNCTION catalog.validate_v022_security_lifecycle_event()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; security_key_value varchar; prior_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT security_key INTO security_key_value FROM catalog.security
           WHERE security_id=NEW.security_id;
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF security_key_value IS NULL OR
             artifact_row.artifact_type IS DISTINCT FROM
               'v022_security_lifecycle_event' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_security_lifecycle__' || security_key_value || '__' ||
               NEW.event_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number THEN
            RAISE EXCEPTION 'Security Lifecycle Event identity is incomplete';
          END IF;
          IF NEW.version_number=1 AND
             NEW.supersedes_lifecycle_event_id IS NOT NULL THEN
            RAISE EXCEPTION 'First Lifecycle Event version cannot supersede another';
          ELSIF NEW.version_number>1 THEN
            SELECT event.security_id,event.event_key,event.version_number,
                   artifact.status INTO prior_row
              FROM catalog.v022_security_lifecycle_event event
              JOIN lineage.artifact artifact ON artifact.artifact_id=event.artifact_id
             WHERE event.security_lifecycle_event_id=
                   NEW.supersedes_lifecycle_event_id;
            IF prior_row.status IS DISTINCT FROM 'published' OR
               prior_row.security_id IS DISTINCT FROM NEW.security_id OR
               prior_row.event_key IS DISTINCT FROM NEW.event_key OR
               prior_row.version_number IS DISTINCT FROM NEW.version_number-1 THEN
              RAISE EXCEPTION 'Lifecycle Event supersession is not exact';
            END IF;
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION catalog.validate_v022_security_lifecycle_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE event_artifact uuid; source_status varchar;
        BEGIN
          SELECT artifact_id INTO event_artifact
            FROM catalog.v022_security_lifecycle_event
           WHERE security_lifecycle_event_id=NEW.security_lifecycle_event_id;
          PERFORM data.assert_artifact_draft(event_artifact);
          SELECT status INTO source_status FROM lineage.artifact
           WHERE artifact_id=NEW.evidence_artifact_id;
          IF source_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Lifecycle Event Evidence must be published';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION catalog.validate_v022_security_settlement_leg()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE event_artifact uuid;
        BEGIN
          SELECT artifact_id INTO event_artifact
            FROM catalog.v022_security_lifecycle_event
           WHERE security_lifecycle_event_id=NEW.security_lifecycle_event_id;
          PERFORM data.assert_artifact_draft(event_artifact);
          IF NEW.target_security_id IS NOT NULL AND
             NEW.target_security_id=(SELECT security_id
               FROM catalog.v022_security_lifecycle_event
               WHERE security_lifecycle_event_id=NEW.security_lifecycle_event_id) THEN
            RAISE EXCEPTION 'Settlement successor cannot be the source Security';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION catalog.assert_v022_security_lifecycle_event_complete(
          target_event_id uuid
        ) RETURNS void LANGUAGE plpgsql AS $$
        DECLARE event_row record; evidence_json jsonb; evidence_ids_json jsonb;
                leg_json jsonb;
        BEGIN
          SELECT event.*,artifact.status INTO event_row
            FROM catalog.v022_security_lifecycle_event event
            JOIN lineage.artifact artifact ON artifact.artifact_id=event.artifact_id
           WHERE event.security_lifecycle_event_id=target_event_id;
          IF event_row.status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Security Lifecycle Event Artifact must be published';
          END IF;
          SELECT coalesce(jsonb_agg(jsonb_build_object(
                     'artifact_id',evidence.evidence_artifact_id::text,
                     'role',evidence.evidence_role
                   ) ORDER BY evidence.ordinal),'[]'::jsonb),
                 coalesce(jsonb_agg(evidence.evidence_artifact_id::text
                   ORDER BY evidence.ordinal),'[]'::jsonb)
            INTO evidence_json,evidence_ids_json
            FROM catalog.v022_security_lifecycle_event_evidence evidence
           WHERE evidence.security_lifecycle_event_id=target_event_id;
          SELECT coalesce(jsonb_agg(leg.leg_document ORDER BY leg.ordinal),'[]'::jsonb)
            INTO leg_json FROM catalog.v022_security_settlement_leg leg
           WHERE leg.security_lifecycle_event_id=target_event_id;
          IF (SELECT count(*) FROM catalog.v022_security_lifecycle_event_evidence
               WHERE security_lifecycle_event_id=target_event_id)<>
                 event_row.evidence_count OR
             (SELECT count(*) FROM catalog.v022_security_settlement_leg
               WHERE security_lifecycle_event_id=target_event_id)<>
                 event_row.settlement_leg_count OR
             event_row.event_document->'evidence' IS DISTINCT FROM evidence_json OR
             event_row.event_document->'evidence_artifact_ids' IS DISTINCT FROM
                 evidence_ids_json OR
             event_row.event_document->'settlement_legs' IS DISTINCT FROM leg_json OR
             (event_row.event_status='confirmed' AND
              event_row.event_type='cash_merger' AND NOT EXISTS (
                SELECT 1 FROM catalog.v022_security_settlement_leg leg
                 WHERE leg.security_lifecycle_event_id=target_event_id
                   AND leg.leg_kind='cash'
              )) OR
             (event_row.event_status='confirmed' AND
              event_row.event_type IN ('stock_merger','share_class_conversion') AND
              NOT EXISTS (
                SELECT 1 FROM catalog.v022_security_settlement_leg leg
                 WHERE leg.security_lifecycle_event_id=target_event_id
                   AND leg.leg_kind='successor_security'
              )) OR
             (event_row.event_status='confirmed' AND
              event_row.event_type='spinoff' AND NOT EXISTS (
                SELECT 1 FROM catalog.v022_security_settlement_leg leg
                 WHERE leg.security_lifecycle_event_id=target_event_id
                   AND leg.leg_kind='distributed_security'
              )) OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=event_row.artifact_id)<>
                 event_row.evidence_count +
                   (CASE WHEN event_row.supersedes_lifecycle_event_id IS NULL
                     THEN 0 ELSE 1 END) OR
             EXISTS (
               SELECT 1
                 FROM catalog.v022_security_lifecycle_event_evidence evidence
                WHERE evidence.security_lifecycle_event_id=target_event_id
                  AND NOT EXISTS (
                    SELECT 1 FROM lineage.artifact_dependency dependency
                     WHERE dependency.artifact_id=event_row.artifact_id
                       AND dependency.depends_on_artifact_id=
                           evidence.evidence_artifact_id
                       AND dependency.role='source_evidence'
                       AND dependency.ordinal=evidence.ordinal
                  )
             ) OR
             (event_row.supersedes_lifecycle_event_id IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
               JOIN catalog.v022_security_lifecycle_event prior
                 ON prior.artifact_id=dependency.depends_on_artifact_id
                WHERE dependency.artifact_id=event_row.artifact_id
                  AND prior.security_lifecycle_event_id=
                      event_row.supersedes_lifecycle_event_id
                  AND dependency.role='superseded_lifecycle_event'
                  AND dependency.ordinal=event_row.evidence_count
             )) THEN
            RAISE EXCEPTION 'Security Lifecycle Event closure is incomplete';
          END IF;
        END $$;

        CREATE FUNCTION catalog.validate_v022_security_lifecycle_event_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM catalog.assert_v022_security_lifecycle_event_complete(
            CASE TG_TABLE_NAME
              WHEN 'v022_security_lifecycle_event' THEN NEW.security_lifecycle_event_id
              ELSE NEW.security_lifecycle_event_id
            END
          );
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_security_lifecycle_event_validate
          BEFORE INSERT ON catalog.v022_security_lifecycle_event
          FOR EACH ROW EXECUTE FUNCTION
            catalog.validate_v022_security_lifecycle_event();
        CREATE TRIGGER trg_v022_security_lifecycle_evidence_validate
          BEFORE INSERT ON catalog.v022_security_lifecycle_event_evidence
          FOR EACH ROW EXECUTE FUNCTION
            catalog.validate_v022_security_lifecycle_evidence();
        CREATE TRIGGER trg_v022_security_settlement_leg_validate
          BEFORE INSERT ON catalog.v022_security_settlement_leg
          FOR EACH ROW EXECUTE FUNCTION
            catalog.validate_v022_security_settlement_leg();

        CREATE CONSTRAINT TRIGGER trg_v022_security_lifecycle_event_complete
          AFTER INSERT ON catalog.v022_security_lifecycle_event
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
            catalog.validate_v022_security_lifecycle_event_complete();
        CREATE CONSTRAINT TRIGGER trg_v022_security_lifecycle_evidence_complete
          AFTER INSERT ON catalog.v022_security_lifecycle_event_evidence
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
            catalog.validate_v022_security_lifecycle_event_complete();
        CREATE CONSTRAINT TRIGGER trg_v022_security_settlement_leg_complete
          AFTER INSERT ON catalog.v022_security_settlement_leg
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
            catalog.validate_v022_security_lifecycle_event_complete();

        CREATE TRIGGER trg_v022_security_lifecycle_event_append_only
          BEFORE UPDATE OR DELETE ON catalog.v022_security_lifecycle_event
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_security_lifecycle_evidence_append_only
          BEFORE UPDATE OR DELETE ON catalog.v022_security_lifecycle_event_evidence
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_security_settlement_leg_append_only
          BEFORE UPDATE OR DELETE ON catalog.v022_security_settlement_leg
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM catalog.v022_security_lifecycle_event) OR
             EXISTS (SELECT 1 FROM catalog.v022_security_lifecycle_event_evidence) OR
             EXISTS (SELECT 1 FROM catalog.v022_security_settlement_leg)
          THEN
            RAISE EXCEPTION 'Cannot downgrade with v0.22 Security Lifecycle evidence';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS
          catalog.validate_v022_security_lifecycle_event_complete() CASCADE;
        DROP FUNCTION IF EXISTS
          catalog.assert_v022_security_lifecycle_event_complete(uuid) CASCADE;
        DROP FUNCTION IF EXISTS
          catalog.validate_v022_security_settlement_leg() CASCADE;
        DROP FUNCTION IF EXISTS
          catalog.validate_v022_security_lifecycle_evidence() CASCADE;
        DROP FUNCTION IF EXISTS
          catalog.validate_v022_security_lifecycle_event() CASCADE;
        DROP TABLE catalog.v022_security_settlement_leg;
        DROP TABLE catalog.v022_security_lifecycle_event_evidence;
        DROP TABLE catalog.v022_security_lifecycle_event;
        """
    )
