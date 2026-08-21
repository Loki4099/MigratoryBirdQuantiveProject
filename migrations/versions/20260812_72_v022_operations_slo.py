# ruff: noqa: E501
"""Add formal operational SLO readiness evidence and alerts.

Revision ID: 20260812_72_v022_ops_slo
Revises: 20260811_71_v022_comparator
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_72_v022_ops_slo"
down_revision: str | None = "20260811_71_v022_comparator"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ops.v022_slo_policy_version (
          slo_policy_version_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          policy_key varchar(160) NOT NULL,
          version_number integer NOT NULL CHECK (version_number >= 1),
          rule_document jsonb NOT NULL,
          rule_count integer NOT NULL CHECK (rule_count >= 6),
          policy_fingerprint varchar(64) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (policy_key,version_number),
          CHECK (btrim(policy_key) <> ''),
          CHECK (jsonb_typeof(rule_document)='array' AND
                 jsonb_array_length(rule_document)=rule_count),
          CHECK (policy_fingerprint ~ '^[0-9a-f]{64}$')
        );

        CREATE TABLE ops.v022_slo_measurement (
          slo_measurement_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          metric_key varchar(160) NOT NULL,
          domain_key varchar(40) NOT NULL CHECK (
            domain_key IN ('compile','queue','cache','storage','export','product_freshness')
          ),
          observed_value numeric(38,12) NOT NULL,
          sample_count bigint NOT NULL CHECK (sample_count >= 1),
          window_start_at timestamptz NOT NULL,
          window_end_at timestamptz NOT NULL,
          measured_at timestamptz NOT NULL,
          probe_document jsonb NOT NULL,
          measurement_fingerprint varchar(64) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (metric_key,window_start_at,window_end_at),
          CHECK (btrim(metric_key) <> ''),
          CHECK (window_start_at < window_end_at AND measured_at >= window_end_at),
          CHECK (jsonb_typeof(probe_document)='object' AND probe_document<>'{}'::jsonb),
          CHECK (measurement_fingerprint ~ '^[0-9a-f]{64}$')
        );

        CREATE TABLE ops.v022_operations_readiness_snapshot (
          operations_readiness_snapshot_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          slo_policy_version_id uuid NOT NULL REFERENCES ops.v022_slo_policy_version,
          window_start_at timestamptz NOT NULL,
          window_end_at timestamptz NOT NULL,
          evaluated_at timestamptz NOT NULL,
          ready_for_default boolean NOT NULL,
          rule_count integer NOT NULL CHECK (rule_count >= 6),
          passed_rule_count integer NOT NULL CHECK (passed_rule_count >= 0),
          blocker_codes jsonb NOT NULL,
          readiness_document jsonb NOT NULL,
          readiness_fingerprint varchar(64) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (slo_policy_version_id,window_start_at,window_end_at),
          CHECK (window_start_at < window_end_at AND evaluated_at >= window_end_at),
          CHECK (passed_rule_count <= rule_count),
          CHECK (jsonb_typeof(blocker_codes)='array'),
          CHECK (jsonb_typeof(readiness_document)='object' AND readiness_document<>'{}'::jsonb),
          CHECK (ready_for_default = (passed_rule_count=rule_count AND
                                      jsonb_array_length(blocker_codes)=0)),
          CHECK (readiness_fingerprint ~ '^[0-9a-f]{64}$')
        );

        CREATE TABLE ops.v022_operations_readiness_member (
          operations_readiness_snapshot_id uuid NOT NULL
            REFERENCES ops.v022_operations_readiness_snapshot,
          ordinal integer NOT NULL CHECK (ordinal >= 1),
          metric_key varchar(160) NOT NULL,
          domain_key varchar(40) NOT NULL,
          slo_measurement_id uuid NULL REFERENCES ops.v022_slo_measurement,
          comparator varchar(8) NOT NULL CHECK (comparator IN ('lte','gte')),
          threshold numeric(38,12) NOT NULL,
          observed_value numeric(38,12) NULL,
          minimum_sample_count bigint NOT NULL CHECK (minimum_sample_count >= 1),
          actual_sample_count bigint NOT NULL CHECK (actual_sample_count >= 0),
          severity varchar(16) NOT NULL CHECK (severity IN ('warning','critical')),
          passed boolean NOT NULL,
          blocker_code varchar(200) NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (operations_readiness_snapshot_id,ordinal),
          UNIQUE (operations_readiness_snapshot_id,metric_key),
          CHECK ((passed AND blocker_code IS NULL) OR
                 (NOT passed AND btrim(blocker_code) <> ''))
        );

        CREATE TABLE ops.v022_operational_alert (
          operational_alert_id uuid PRIMARY KEY,
          operations_readiness_snapshot_id uuid NOT NULL,
          member_ordinal integer NOT NULL,
          metric_key varchar(160) NOT NULL,
          domain_key varchar(40) NOT NULL,
          slo_measurement_id uuid NULL REFERENCES ops.v022_slo_measurement,
          severity varchar(16) NOT NULL CHECK (severity IN ('warning','critical')),
          alert_code varchar(200) NOT NULL,
          alert_document jsonb NOT NULL,
          opened_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (operations_readiness_snapshot_id,member_ordinal)
            REFERENCES ops.v022_operations_readiness_member
              (operations_readiness_snapshot_id,ordinal),
          UNIQUE (operations_readiness_snapshot_id,metric_key),
          CHECK (btrim(alert_code) <> ''),
          CHECK (jsonb_typeof(alert_document)='object' AND alert_document<>'{}'::jsonb)
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION ops.validate_v022_slo_artifact_type()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_type varchar; expected_type varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          expected_type := CASE TG_TABLE_NAME
            WHEN 'v022_slo_policy_version' THEN 'v022_slo_policy_version'
            WHEN 'v022_slo_measurement' THEN 'v022_operational_slo_measurement'
            WHEN 'v022_operations_readiness_snapshot' THEN 'v022_operations_readiness_evidence'
          END;
          SELECT artifact_type INTO actual_type FROM lineage.artifact
           WHERE artifact_id=NEW.artifact_id;
          IF actual_type IS DISTINCT FROM expected_type THEN
            RAISE EXCEPTION 'Operational SLO row requires its formal Artifact type';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION ops.validate_v022_slo_policy_rules()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (
            (SELECT count(DISTINCT item->>'domain_key')
               FROM jsonb_array_elements(NEW.rule_document) item
              WHERE item->>'domain_key' IN
                ('compile','queue','cache','storage','export','product_freshness')) <> 6 OR
            (SELECT count(DISTINCT item->>'metric_key')
               FROM jsonb_array_elements(NEW.rule_document) item) <> NEW.rule_count OR
            EXISTS (
              SELECT 1 FROM jsonb_array_elements(NEW.rule_document) item
               WHERE coalesce(btrim(item->>'metric_key'),'')='' OR
                     coalesce(item->>'domain_key','') NOT IN
                       ('compile','queue','cache','storage','export','product_freshness') OR
                     coalesce(item->>'comparator','') NOT IN ('lte','gte') OR
                     coalesce(item->>'severity','') NOT IN ('warning','critical') OR
                     (item->>'minimum_sample_count')::bigint < 1
            )
          ) THEN
            RAISE EXCEPTION 'Operational SLO Policy must canonically cover all six domains';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_slo_policy_artifact
          BEFORE INSERT ON ops.v022_slo_policy_version
          FOR EACH ROW EXECUTE FUNCTION ops.validate_v022_slo_artifact_type();
        CREATE TRIGGER trg_v022_slo_policy_rules
          BEFORE INSERT ON ops.v022_slo_policy_version
          FOR EACH ROW EXECUTE FUNCTION ops.validate_v022_slo_policy_rules();
        CREATE TRIGGER trg_v022_slo_measurement_artifact
          BEFORE INSERT ON ops.v022_slo_measurement
          FOR EACH ROW EXECUTE FUNCTION ops.validate_v022_slo_artifact_type();
        CREATE TRIGGER trg_v022_operations_readiness_artifact
          BEFORE INSERT ON ops.v022_operations_readiness_snapshot
          FOR EACH ROW EXECUTE FUNCTION ops.validate_v022_slo_artifact_type();

        CREATE FUNCTION ops.validate_v022_operations_readiness_member()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE snapshot_row record; measurement_row record; policy_rule jsonb;
                expected_pass boolean; expected_blocker varchar;
        BEGIN
          SELECT snapshot.artifact_id,snapshot.window_start_at,snapshot.window_end_at,
                 policy.rule_document->(NEW.ordinal-1) AS policy_rule INTO snapshot_row
            FROM ops.v022_operations_readiness_snapshot snapshot
            JOIN ops.v022_slo_policy_version policy
              ON policy.slo_policy_version_id=snapshot.slo_policy_version_id
           WHERE operations_readiness_snapshot_id=NEW.operations_readiness_snapshot_id;
          PERFORM data.assert_artifact_draft(snapshot_row.artifact_id);
          policy_rule := snapshot_row.policy_rule;
          IF policy_rule->>'metric_key' IS DISTINCT FROM NEW.metric_key OR
             policy_rule->>'domain_key' IS DISTINCT FROM NEW.domain_key OR
             policy_rule->>'comparator' IS DISTINCT FROM NEW.comparator OR
             (policy_rule->>'threshold')::numeric IS DISTINCT FROM NEW.threshold OR
             (policy_rule->>'minimum_sample_count')::bigint IS DISTINCT FROM
               NEW.minimum_sample_count OR
             policy_rule->>'severity' IS DISTINCT FROM NEW.severity THEN
            RAISE EXCEPTION 'Operations Readiness member differs from its frozen SLO Rule';
          END IF;
          SELECT measurement.*,artifact.status INTO measurement_row
            FROM ops.v022_slo_measurement measurement
            JOIN lineage.artifact artifact ON artifact.artifact_id=measurement.artifact_id
           WHERE measurement.slo_measurement_id=NEW.slo_measurement_id;
          IF NEW.slo_measurement_id IS NULL THEN
            expected_pass := false;
            expected_blocker := 'missing_measurement:' || NEW.metric_key;
            IF NEW.observed_value IS NOT NULL OR NEW.actual_sample_count<>0 THEN
              RAISE EXCEPTION 'Missing SLO Measurement must not invent observations';
            END IF;
          ELSE
            IF measurement_row.status IS DISTINCT FROM 'published' OR
               measurement_row.metric_key IS DISTINCT FROM NEW.metric_key OR
               measurement_row.domain_key IS DISTINCT FROM NEW.domain_key OR
               measurement_row.observed_value IS DISTINCT FROM NEW.observed_value OR
               measurement_row.sample_count IS DISTINCT FROM NEW.actual_sample_count OR
               measurement_row.window_start_at IS DISTINCT FROM snapshot_row.window_start_at OR
               measurement_row.window_end_at IS DISTINCT FROM snapshot_row.window_end_at THEN
              RAISE EXCEPTION 'Operations Readiness Measurement identity or window is not exact';
            END IF;
            IF NEW.actual_sample_count<NEW.minimum_sample_count THEN
              expected_pass := false;
              expected_blocker := 'insufficient_samples:' || NEW.metric_key;
            ELSE
              expected_pass := (NEW.comparator='lte' AND NEW.observed_value<=NEW.threshold) OR
                               (NEW.comparator='gte' AND NEW.observed_value>=NEW.threshold);
              expected_blocker := CASE WHEN expected_pass THEN NULL
                ELSE 'slo_breach:' || NEW.metric_key END;
            END IF;
          END IF;
          IF NEW.passed IS DISTINCT FROM expected_pass OR
             NEW.blocker_code IS DISTINCT FROM expected_blocker THEN
            RAISE EXCEPTION 'Operations Readiness pass or blocker state is not canonical';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_operations_readiness_member_validate
          BEFORE INSERT ON ops.v022_operations_readiness_member
          FOR EACH ROW EXECUTE FUNCTION ops.validate_v022_operations_readiness_member();

        CREATE FUNCTION ops.validate_v022_operational_alert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE member_row record; snapshot_time timestamptz;
        BEGIN
          SELECT member.*,snapshot.evaluated_at INTO member_row
            FROM ops.v022_operations_readiness_member member
            JOIN ops.v022_operations_readiness_snapshot snapshot
              ON snapshot.operations_readiness_snapshot_id=
                 member.operations_readiness_snapshot_id
           WHERE member.operations_readiness_snapshot_id=NEW.operations_readiness_snapshot_id
             AND member.ordinal=NEW.member_ordinal;
          snapshot_time := member_row.evaluated_at;
          IF member_row.passed OR member_row.metric_key IS DISTINCT FROM NEW.metric_key OR
             member_row.domain_key IS DISTINCT FROM NEW.domain_key OR
             member_row.slo_measurement_id IS DISTINCT FROM NEW.slo_measurement_id OR
             member_row.severity IS DISTINCT FROM NEW.severity OR
             member_row.blocker_code IS DISTINCT FROM NEW.alert_code OR
             NEW.opened_at IS DISTINCT FROM snapshot_time THEN
            RAISE EXCEPTION 'Operational Alert must match one failed Readiness member';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_operational_alert_validate
          BEFORE INSERT ON ops.v022_operational_alert
          FOR EACH ROW EXECUTE FUNCTION ops.validate_v022_operational_alert();

        CREATE FUNCTION ops.validate_v022_operations_readiness_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE member_count integer; passed_count integer; alert_count integer;
                failed_count integer; canonical_blockers jsonb;
        BEGIN
          SELECT count(*),count(*) FILTER (WHERE passed),count(*) FILTER (WHERE NOT passed),
                 coalesce(jsonb_agg(blocker_code ORDER BY ordinal)
                   FILTER (WHERE NOT passed),'[]'::jsonb)
            INTO member_count,passed_count,failed_count,canonical_blockers
            FROM ops.v022_operations_readiness_member
           WHERE operations_readiness_snapshot_id=NEW.operations_readiness_snapshot_id;
          SELECT count(*) INTO alert_count FROM ops.v022_operational_alert
           WHERE operations_readiness_snapshot_id=NEW.operations_readiness_snapshot_id;
          IF member_count<>NEW.rule_count OR passed_count<>NEW.passed_rule_count OR
             alert_count<>failed_count OR canonical_blockers<>NEW.blocker_codes THEN
            RAISE EXCEPTION 'Operations Readiness members or alerts are incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_operations_readiness_complete
          AFTER INSERT ON ops.v022_operations_readiness_snapshot
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION ops.validate_v022_operations_readiness_complete();

        CREATE TRIGGER trg_v022_slo_policy_append_only
          BEFORE UPDATE OR DELETE ON ops.v022_slo_policy_version
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_slo_measurement_append_only
          BEFORE UPDATE OR DELETE ON ops.v022_slo_measurement
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_operations_readiness_append_only
          BEFORE UPDATE OR DELETE ON ops.v022_operations_readiness_snapshot
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_operations_readiness_member_append_only
          BEFORE UPDATE OR DELETE ON ops.v022_operations_readiness_member
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_operational_alert_append_only
          BEFORE UPDATE OR DELETE ON ops.v022_operational_alert
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS ops.validate_v022_operations_readiness_complete() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS ops.validate_v022_operational_alert() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS ops.validate_v022_operations_readiness_member() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS ops.validate_v022_slo_policy_rules() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS ops.validate_v022_slo_artifact_type() CASCADE")
    op.drop_table("v022_operational_alert", schema="ops")
    op.drop_table("v022_operations_readiness_member", schema="ops")
    op.drop_table("v022_operations_readiness_snapshot", schema="ops")
    op.drop_table("v022_slo_measurement", schema="ops")
    op.drop_table("v022_slo_policy_version", schema="ops")
