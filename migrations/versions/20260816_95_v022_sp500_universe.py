"""Add source-backed S&P 500 membership ledgers and history bindings.

Revision ID: 20260816_95_v022_sp500_universe
Revises: 20260816_94_v022_seed_import
"""

from __future__ import annotations

from alembic import op

revision = "20260816_95_v022_sp500_universe"
down_revision = "20260816_94_v022_seed_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE catalog.v022_universe_membership_ledger (
          universe_membership_ledger_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          external_import_manifest_id uuid NOT NULL
            REFERENCES data.v022_external_import_manifest,
          source_object_logical_key varchar(240) NOT NULL
            CHECK (btrim(source_object_logical_key)<>''),
          source_content_sha256 varchar(64) NOT NULL
            CHECK (source_content_sha256 ~ '^[0-9a-f]{64}$'),
          universe_key varchar(160) NOT NULL CHECK (btrim(universe_key)<>''),
          version_number integer NOT NULL CHECK (version_number>=1),
          research_tier varchar(32) NOT NULL
            CHECK (research_tier IN ('rankable_research','exploratory_only')),
          source_row_count integer NOT NULL CHECK (source_row_count>=1),
          snapshot_count integer NOT NULL CHECK (snapshot_count>=1),
          event_count integer NOT NULL CHECK (event_count>=1),
          coverage_start date NOT NULL,
          coverage_end date NOT NULL,
          ledger_document jsonb NOT NULL CHECK (jsonb_typeof(ledger_document)='object'),
          ledger_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (ledger_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (universe_key,version_number),
          CHECK (coverage_start<=coverage_end)
        );

        CREATE TABLE catalog.v022_universe_change_batch (
          universe_change_batch_id uuid PRIMARY KEY,
          universe_membership_ledger_id uuid NOT NULL
            REFERENCES catalog.v022_universe_membership_ledger,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          effective_session date NOT NULL,
          announced_at timestamptz NULL,
          source_row_number integer NOT NULL CHECK (source_row_number>=2),
          source_row_sha256 varchar(64) NOT NULL
            CHECK (source_row_sha256 ~ '^[0-9a-f]{64}$'),
          source_member_count integer NOT NULL CHECK (source_member_count>=1),
          added_count integer NOT NULL CHECK (added_count>=0),
          removed_count integer NOT NULL CHECK (removed_count>=0),
          evidence_status varchar(24) NOT NULL
            CHECK (evidence_status IN ('confirmed','estimated','unresolved')),
          reason_code varchar(120) NOT NULL CHECK (btrim(reason_code)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (universe_membership_ledger_id,ordinal),
          UNIQUE (universe_membership_ledger_id,effective_session),
          CHECK (ordinal=0 OR added_count+removed_count>=1)
        );

        CREATE TABLE catalog.v022_universe_membership_event (
          universe_membership_event_id uuid PRIMARY KEY,
          universe_change_batch_id uuid NOT NULL
            REFERENCES catalog.v022_universe_change_batch,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          event_type varchar(12) NOT NULL CHECK (event_type IN ('seed','add','remove')),
          security_id uuid NOT NULL REFERENCES catalog.security,
          source_symbol varchar(160) NOT NULL CHECK (btrim(source_symbol)<>''),
          effective_session date NOT NULL,
          announced_at timestamptz NULL,
          source_row_number integer NOT NULL CHECK (source_row_number>=2),
          evidence_status varchar(24) NOT NULL
            CHECK (evidence_status IN ('confirmed','estimated','unresolved')),
          reason_code varchar(120) NOT NULL CHECK (btrim(reason_code)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (universe_change_batch_id,ordinal),
          UNIQUE (universe_change_batch_id,security_id)
        );

        CREATE TABLE catalog.v022_universe_history_ledger_binding (
          universe_history_ledger_binding_id uuid PRIMARY KEY,
          universe_membership_ledger_id uuid NOT NULL UNIQUE
            REFERENCES catalog.v022_universe_membership_ledger,
          universe_history_id uuid NOT NULL UNIQUE REFERENCES catalog.universe_history,
          universe_history_artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          binding_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE FUNCTION catalog.validate_v022_universe_membership_ledger()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; source_row record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT object.content_sha256,object.provenance_status,object.usage_scope,
                 manifest.artifact_id AS manifest_artifact_id,
                 manifest_artifact.status AS manifest_status
            INTO source_row
            FROM data.v022_external_import_object object
            JOIN data.v022_external_import_manifest manifest
              ON manifest.external_import_manifest_id=object.external_import_manifest_id
            JOIN lineage.artifact manifest_artifact
              ON manifest_artifact.artifact_id=manifest.artifact_id
           WHERE object.external_import_manifest_id=NEW.external_import_manifest_id
             AND object.logical_key=NEW.source_object_logical_key;
          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_universe_membership_ledger' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_universe_membership_ledger__' || NEW.universe_key OR
             artifact_row.version_number IS DISTINCT FROM NEW.version_number OR
             source_row.manifest_status IS DISTINCT FROM 'published' OR
             source_row.content_sha256 IS DISTINCT FROM NEW.source_content_sha256 OR
             (NEW.research_tier='rankable_research' AND
               (source_row.provenance_status IS DISTINCT FROM 'verified' OR
                source_row.usage_scope IS NOT DISTINCT FROM 'unresolved')) THEN
            RAISE EXCEPTION 'Universe Membership Ledger requires exact source evidence';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM lineage.artifact_dependency dependency
             WHERE dependency.artifact_id=NEW.artifact_id
               AND dependency.depends_on_artifact_id=source_row.manifest_artifact_id
               AND dependency.role='external_import_manifest'
               AND dependency.ordinal=0
          ) OR (SELECT count(*) FROM lineage.artifact_dependency dependency
                 WHERE dependency.artifact_id=NEW.artifact_id)<>1 THEN
            RAISE EXCEPTION 'Universe Membership Ledger requires exact source lineage';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_universe_membership_ledger_validate
          BEFORE INSERT ON catalog.v022_universe_membership_ledger
          FOR EACH ROW EXECUTE FUNCTION catalog.validate_v022_universe_membership_ledger();

        CREATE FUNCTION catalog.validate_v022_universe_membership_ledger_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_status_value varchar; batch_count integer;
                event_count_value integer; max_ordinal integer; batch_mismatch boolean;
        BEGIN
          SELECT status INTO artifact_status_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT count(*),max(ordinal) INTO batch_count,max_ordinal
            FROM catalog.v022_universe_change_batch
           WHERE universe_membership_ledger_id=NEW.universe_membership_ledger_id;
          SELECT count(*) INTO event_count_value
            FROM catalog.v022_universe_membership_event event
            JOIN catalog.v022_universe_change_batch batch
              ON batch.universe_change_batch_id=event.universe_change_batch_id
           WHERE batch.universe_membership_ledger_id=NEW.universe_membership_ledger_id;
          SELECT EXISTS (
            SELECT 1 FROM catalog.v022_universe_change_batch batch
             WHERE batch.universe_membership_ledger_id=NEW.universe_membership_ledger_id
               AND (
                 (batch.ordinal=0 AND (
                   batch.added_count<>batch.source_member_count OR batch.removed_count<>0 OR
                   (SELECT count(*) FROM catalog.v022_universe_membership_event event
                     WHERE event.universe_change_batch_id=batch.universe_change_batch_id
                       AND event.event_type='seed')<>batch.source_member_count
                 )) OR
                 (batch.ordinal>0 AND (
                   (SELECT count(*) FROM catalog.v022_universe_membership_event event
                     WHERE event.universe_change_batch_id=batch.universe_change_batch_id
                       AND event.event_type='add')<>batch.added_count OR
                   (SELECT count(*) FROM catalog.v022_universe_membership_event event
                     WHERE event.universe_change_batch_id=batch.universe_change_batch_id
                       AND event.event_type='remove')<>batch.removed_count
                 ))
               )
          ) INTO batch_mismatch;
          IF artifact_status_value IS DISTINCT FROM 'published' OR
             batch_count<>NEW.snapshot_count OR max_ordinal<>NEW.snapshot_count-1 OR
             event_count_value<>NEW.event_count OR batch_mismatch IS TRUE THEN
            RAISE EXCEPTION 'Universe Membership Ledger projection is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_universe_membership_ledger_complete
          AFTER INSERT ON catalog.v022_universe_membership_ledger
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION catalog.validate_v022_universe_membership_ledger_complete();

        CREATE FUNCTION catalog.validate_v022_universe_membership_event()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE batch_row record;
        BEGIN
          SELECT effective_session,announced_at,source_row_number,evidence_status,reason_code,
                 ordinal AS batch_ordinal
            INTO batch_row FROM catalog.v022_universe_change_batch
           WHERE universe_change_batch_id=NEW.universe_change_batch_id;
          IF batch_row.effective_session IS DISTINCT FROM NEW.effective_session OR
             batch_row.announced_at IS DISTINCT FROM NEW.announced_at OR
             batch_row.source_row_number IS DISTINCT FROM NEW.source_row_number OR
             batch_row.evidence_status IS DISTINCT FROM NEW.evidence_status OR
             batch_row.reason_code IS DISTINCT FROM NEW.reason_code OR
             (batch_row.batch_ordinal=0 AND NEW.event_type<>'seed') OR
             (batch_row.batch_ordinal>0 AND NEW.event_type='seed') THEN
            RAISE EXCEPTION 'Universe Membership Event must match its exact source batch';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_universe_membership_event_validate
          BEFORE INSERT ON catalog.v022_universe_membership_event
          FOR EACH ROW EXECUTE FUNCTION catalog.validate_v022_universe_membership_event();

        CREATE FUNCTION catalog.validate_v022_universe_history_ledger_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE ledger_row record; history_row record; actual_snapshots integer;
                mismatch_exists boolean; dependency_count integer;
        BEGIN
          SELECT ledger.artifact_id,ledger.snapshot_count,ledger.coverage_end,
                 artifact.status AS ledger_status
            INTO ledger_row
            FROM catalog.v022_universe_membership_ledger ledger
            JOIN lineage.artifact artifact ON artifact.artifact_id=ledger.artifact_id
           WHERE ledger.universe_membership_ledger_id=NEW.universe_membership_ledger_id;
          SELECT history.artifact_id,history.snapshot_count,history.as_of_date,
                 methodology.artifact_id AS methodology_artifact_id,
                 artifact.status AS history_status
            INTO history_row
            FROM catalog.universe_history history
            JOIN lineage.artifact artifact ON artifact.artifact_id=history.artifact_id
            JOIN catalog.universe_methodology methodology
              ON methodology.universe_methodology_id=history.universe_methodology_id
           WHERE history.universe_history_id=NEW.universe_history_id;
          SELECT count(*) INTO actual_snapshots FROM catalog.universe_snapshot snapshot
           WHERE snapshot.universe_history_id=NEW.universe_history_id;
          SELECT count(*) INTO dependency_count FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.universe_history_artifact_id;
          WITH expected AS (
            SELECT batch.ordinal AS batch_ordinal,batch.effective_session,event.security_id,
                   event.event_type,
                   row_number() OVER (
                     PARTITION BY batch.ordinal,event.security_id
                     ORDER BY event_batch.ordinal DESC,event.ordinal DESC
                   ) AS recency
              FROM catalog.v022_universe_change_batch batch
              JOIN catalog.v022_universe_change_batch event_batch
                ON event_batch.universe_membership_ledger_id=
                   batch.universe_membership_ledger_id
               AND event_batch.ordinal<=batch.ordinal
              JOIN catalog.v022_universe_membership_event event
                ON event.universe_change_batch_id=event_batch.universe_change_batch_id
             WHERE batch.universe_membership_ledger_id=NEW.universe_membership_ledger_id
          ), expected_members AS (
            SELECT batch_ordinal,effective_session,security_id FROM expected
             WHERE recency=1 AND event_type IN ('seed','add')
          ), actual_members AS (
            SELECT batch.ordinal AS batch_ordinal,snapshot.effective_session,member.security_id
              FROM catalog.v022_universe_change_batch batch
              JOIN catalog.universe_snapshot snapshot
                ON snapshot.universe_history_id=NEW.universe_history_id
               AND snapshot.effective_session=batch.effective_session
              JOIN catalog.universe_snapshot_member member
                ON member.universe_snapshot_id=snapshot.universe_snapshot_id
             WHERE batch.universe_membership_ledger_id=NEW.universe_membership_ledger_id
          )
          SELECT EXISTS(
            (SELECT * FROM expected_members EXCEPT SELECT * FROM actual_members)
            UNION ALL
            (SELECT * FROM actual_members EXCEPT SELECT * FROM expected_members)
          ) INTO mismatch_exists;
          IF ledger_row.ledger_status IS DISTINCT FROM 'published' OR
             history_row.history_status IS DISTINCT FROM 'published' OR
             history_row.artifact_id IS DISTINCT FROM NEW.universe_history_artifact_id OR
             history_row.snapshot_count IS DISTINCT FROM ledger_row.snapshot_count OR
             history_row.as_of_date IS DISTINCT FROM ledger_row.coverage_end OR
             actual_snapshots IS DISTINCT FROM ledger_row.snapshot_count OR
             mismatch_exists IS TRUE OR dependency_count<>2 OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.universe_history_artifact_id
                  AND dependency.depends_on_artifact_id=history_row.methodology_artifact_id
                  AND dependency.role='universe_methodology'
                  AND dependency.ordinal=0
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.universe_history_artifact_id
                  AND dependency.depends_on_artifact_id=ledger_row.artifact_id
                  AND dependency.role='membership_ledger'
                  AND dependency.ordinal=1
             ) THEN
            RAISE EXCEPTION 'Universe History does not exactly derive from its Membership Ledger';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_universe_history_ledger_binding_validate
          AFTER INSERT ON catalog.v022_universe_history_ledger_binding
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION catalog.validate_v022_universe_history_ledger_binding();

        CREATE FUNCTION catalog.protect_v022_bound_universe_projection()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE history_id uuid;
        BEGIN
          IF TG_TABLE_NAME='universe_history' THEN
            history_id=CASE WHEN TG_OP='DELETE' THEN OLD.universe_history_id
                            ELSE NEW.universe_history_id END;
          ELSIF TG_TABLE_NAME='universe_snapshot' THEN
            history_id=CASE WHEN TG_OP='DELETE' THEN OLD.universe_history_id
                            ELSE NEW.universe_history_id END;
          ELSE
            SELECT snapshot.universe_history_id INTO history_id
              FROM catalog.universe_snapshot snapshot
             WHERE snapshot.universe_snapshot_id=
               CASE WHEN TG_OP='DELETE' THEN OLD.universe_snapshot_id
                    ELSE NEW.universe_snapshot_id END;
          END IF;
          IF EXISTS (
            SELECT 1 FROM catalog.v022_universe_history_ledger_binding binding
             WHERE binding.universe_history_id=history_id
          ) THEN
            RAISE EXCEPTION 'Source-backed v0.22 Universe projections are immutable';
          END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END $$;
        CREATE TRIGGER trg_v022_bound_universe_history_immutable
          BEFORE INSERT OR UPDATE OR DELETE ON catalog.universe_history
          FOR EACH ROW EXECUTE FUNCTION catalog.protect_v022_bound_universe_projection();
        CREATE TRIGGER trg_v022_bound_universe_snapshot_immutable
          BEFORE INSERT OR UPDATE OR DELETE ON catalog.universe_snapshot
          FOR EACH ROW EXECUTE FUNCTION catalog.protect_v022_bound_universe_projection();
        CREATE TRIGGER trg_v022_bound_universe_member_immutable
          BEFORE INSERT OR UPDATE OR DELETE ON catalog.universe_snapshot_member
          FOR EACH ROW EXECUTE FUNCTION catalog.protect_v022_bound_universe_projection();

        CREATE TRIGGER trg_v022_universe_membership_ledger_append_only
          BEFORE UPDATE OR DELETE ON catalog.v022_universe_membership_ledger
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_universe_change_batch_append_only
          BEFORE UPDATE OR DELETE ON catalog.v022_universe_change_batch
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_universe_membership_event_append_only
          BEFORE UPDATE OR DELETE ON catalog.v022_universe_membership_event
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_universe_history_ledger_binding_append_only
          BEFORE UPDATE OR DELETE ON catalog.v022_universe_history_ledger_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM catalog.v022_universe_membership_ledger) OR
             EXISTS (SELECT 1 FROM catalog.v022_universe_history_ledger_binding) THEN
            RAISE EXCEPTION 'Cannot downgrade with source-backed v0.22 Universe identities';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS catalog.protect_v022_bound_universe_projection() CASCADE;
        DROP FUNCTION IF EXISTS catalog.validate_v022_universe_history_ledger_binding() CASCADE;
        DROP FUNCTION IF EXISTS catalog.validate_v022_universe_membership_event() CASCADE;
        DROP FUNCTION IF EXISTS catalog.validate_v022_universe_membership_ledger_complete() CASCADE;
        DROP FUNCTION IF EXISTS catalog.validate_v022_universe_membership_ledger() CASCADE;
        DROP TABLE catalog.v022_universe_history_ledger_binding;
        DROP TABLE catalog.v022_universe_membership_event;
        DROP TABLE catalog.v022_universe_change_batch;
        DROP TABLE catalog.v022_universe_membership_ledger;
        """
    )
