# ruff: noqa: E501
"""Add the exact Product-session member set and Raw Payload bindings.

Revision ID: 20260817_111_v022_prod_runtime
Revises: 20260817_110_v022_product_input
"""

from __future__ import annotations

from alembic import op

revision = "20260817_111_v022_prod_runtime"
down_revision = "20260817_110_v022_product_input"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM product.v022_product_input_snapshot) THEN
            RAISE EXCEPTION 'M111 requires empty M110 Product Input Snapshot state';
          END IF;
        END $$;

        CREATE TABLE product.v022_product_input_member (
          product_input_snapshot_id uuid NOT NULL
            REFERENCES product.v022_product_input_snapshot,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          universe_snapshot_id uuid NOT NULL REFERENCES catalog.universe_snapshot,
          security_id uuid NOT NULL REFERENCES catalog.security,
          legacy_asset_id uuid NOT NULL REFERENCES catalog.asset,
          asset_key varchar(160) NOT NULL CHECK (btrim(asset_key)<>''),
          observed_session_count integer NOT NULL CHECK (observed_session_count>=0),
          required_history_sessions integer NOT NULL CHECK (required_history_sessions=504),
          is_uniformly_excluded boolean NOT NULL,
          is_terminal boolean NOT NULL,
          is_warmup_ready boolean NOT NULL,
          is_selectable boolean NOT NULL,
          reason_codes jsonb NOT NULL CHECK (jsonb_typeof(reason_codes)='array'),
          member_document jsonb NOT NULL CHECK (jsonb_typeof(member_document)='object'),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (product_input_snapshot_id,ordinal),
          UNIQUE (product_input_snapshot_id,security_id),
          CHECK (is_warmup_ready=(observed_session_count>=required_history_sessions)),
          CHECK (NOT is_selectable OR
                 (is_warmup_ready AND NOT is_uniformly_excluded AND NOT is_terminal)),
          CHECK ((
            (member_document->>'ordinal')::integer=ordinal AND
            member_document->>'universe_snapshot_id'=universe_snapshot_id::text AND
            member_document->>'security_id'=security_id::text AND
            member_document->>'legacy_asset_id'=legacy_asset_id::text AND
            member_document->>'asset_key'=asset_key AND
            (member_document->>'observed_session_count')::integer=observed_session_count AND
            (member_document->>'required_history_sessions')::integer=
              required_history_sessions AND
            (member_document->>'is_uniformly_excluded')::boolean=
              is_uniformly_excluded AND
            (member_document->>'is_terminal')::boolean=is_terminal AND
            (member_document->>'is_warmup_ready')::boolean=is_warmup_ready AND
            (member_document->>'is_selectable')::boolean=is_selectable AND
            member_document->'reason_codes'=reason_codes
          ) IS TRUE)
        );

        CREATE TABLE data.v022_product_input_payload_binding (
          product_input_payload_binding_id uuid PRIMARY KEY,
          product_input_snapshot_id uuid NOT NULL
            REFERENCES product.v022_product_input_snapshot,
          dataset_publication_id uuid NOT NULL REFERENCES data.dataset_publication,
          feature_version_id uuid NOT NULL REFERENCES processing.feature_version,
          payload_manifest_id uuid NOT NULL UNIQUE REFERENCES data.payload_manifest,
          coverage_start date NOT NULL,
          coverage_end date NOT NULL,
          binding_document jsonb NOT NULL CHECK (jsonb_typeof(binding_document)='object'),
          binding_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (product_input_snapshot_id,feature_version_id),
          CHECK (coverage_start<=coverage_end),
          CHECK ((
            binding_document->>'contract_version'=
              'v0.22.product_input_payload_binding.v1' AND
            binding_document->>'product_input_snapshot_id'=
              product_input_snapshot_id::text AND
            binding_document->>'dataset_publication_id'=dataset_publication_id::text AND
            binding_document->>'feature_version_id'=feature_version_id::text AND
            binding_document->>'payload_manifest_id'=payload_manifest_id::text AND
            (binding_document->>'coverage_start')::date=coverage_start AND
            (binding_document->>'coverage_end')::date=coverage_end
          ) IS TRUE)
        );

        CREATE FUNCTION product.validate_v022_product_input_member()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE input_row record; expected_snapshot_id uuid; expected_member record;
                expected_observed integer; expected_excluded boolean;
                expected_terminal boolean;
        BEGIN
          SELECT input.*,artifact.status INTO input_row
            FROM product.v022_product_input_snapshot input
            JOIN lineage.artifact artifact ON artifact.artifact_id=input.artifact_id
           WHERE input.product_input_snapshot_id=NEW.product_input_snapshot_id;
          SELECT snapshot.universe_snapshot_id INTO expected_snapshot_id
            FROM catalog.universe_snapshot snapshot
           WHERE snapshot.universe_history_id=input_row.universe_history_id
             AND snapshot.effective_session<=input_row.input_end
           ORDER BY snapshot.effective_session DESC,snapshot.universe_snapshot_id DESC
           LIMIT 1;
          SELECT member.ordinal,security.legacy_asset_id,security.security_key
            INTO expected_member
            FROM catalog.universe_snapshot_member member
            JOIN catalog.security security ON security.security_id=member.security_id
           WHERE member.universe_snapshot_id=expected_snapshot_id
             AND member.security_id=NEW.security_id;
          SELECT count(DISTINCT bar.session_date) INTO expected_observed
            FROM data.daily_bar bar
           WHERE bar.dataset_publication_id=input_row.dataset_publication_id
             AND bar.asset_id=expected_member.legacy_asset_id
             AND bar.session_date BETWEEN input_row.input_start AND input_row.input_end;
          SELECT EXISTS (
            SELECT 1 FROM data.v022_dataset_gate_uniform_exclusion exclusion
             WHERE exclusion.dataset_gate_assessment_id=
                   input_row.dataset_gate_assessment_id
               AND exclusion.security_id=NEW.security_id
               AND exclusion.exclusion_start<=input_row.input_end
               AND exclusion.exclusion_end>=input_row.input_end
          ) INTO expected_excluded;
          SELECT EXISTS (
            SELECT 1 FROM catalog.security_terminal_event terminal
             WHERE terminal.security_id=NEW.security_id
               AND terminal.effective_session<=input_row.input_end
               AND terminal.status='confirmed'
          ) INTO expected_terminal;
          IF input_row.status IS DISTINCT FROM 'draft' OR
             expected_snapshot_id IS NULL OR
             NEW.universe_snapshot_id IS DISTINCT FROM expected_snapshot_id OR
             expected_member.ordinal IS DISTINCT FROM NEW.ordinal OR
             expected_member.legacy_asset_id IS DISTINCT FROM NEW.legacy_asset_id OR
             expected_member.security_key IS DISTINCT FROM NEW.asset_key OR
             expected_observed IS DISTINCT FROM NEW.observed_session_count OR
             expected_excluded IS DISTINCT FROM NEW.is_uniformly_excluded OR
             expected_terminal IS DISTINCT FROM NEW.is_terminal OR
             NEW.required_history_sessions<>504 OR
             NEW.is_warmup_ready IS DISTINCT FROM (expected_observed>=504) OR
             NEW.is_selectable IS DISTINCT FROM
               (expected_observed>=504 AND NOT expected_excluded AND NOT expected_terminal) THEN
            RAISE EXCEPTION 'Product Input member does not match its exact decision-session universe';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION data.validate_v022_product_input_payload_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE input_row record; manifest_row record; feature_status varchar;
        BEGIN
          SELECT input.*,artifact.status INTO input_row
            FROM product.v022_product_input_snapshot input
            JOIN lineage.artifact artifact ON artifact.artifact_id=input.artifact_id
           WHERE input.product_input_snapshot_id=NEW.product_input_snapshot_id;
          SELECT manifest.*,artifact.status AS artifact_status
            INTO manifest_row FROM data.payload_manifest manifest
            JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
           WHERE manifest.payload_manifest_id=NEW.payload_manifest_id;
          SELECT artifact.status INTO feature_status
            FROM processing.feature_version feature
            JOIN lineage.artifact artifact ON artifact.artifact_id=feature.artifact_id
           WHERE feature.feature_version_id=NEW.feature_version_id;
          IF input_row.status IS DISTINCT FROM 'published' OR
             input_row.dataset_publication_id IS DISTINCT FROM NEW.dataset_publication_id OR
             manifest_row.artifact_status IS DISTINCT FROM 'published' OR
             manifest_row.materialization_state IS DISTINCT FROM 'materialized' OR
             manifest_row.producer_artifact_id IS DISTINCT FROM input_row.dataset_artifact_id OR
             feature_status IS DISTINCT FROM 'published' OR
             NEW.coverage_start>input_row.input_start OR
             NEW.coverage_end<input_row.input_end OR
             (manifest_row.coverage_document->>'start')::date IS DISTINCT FROM
               NEW.coverage_start OR
             (manifest_row.coverage_document->>'end')::date IS DISTINCT FROM
               NEW.coverage_end OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=manifest_row.artifact_id
                  AND dependency.depends_on_artifact_id=input_row.artifact_id
                  AND dependency.role='product_input_snapshot'
             ) THEN
            RAISE EXCEPTION 'Product Input Payload binding does not close its exact Snapshot';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION product.validate_v022_product_input_member_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_count integer; actual_count integer; max_ordinal integer;
        BEGIN
          SELECT snapshot.member_count INTO expected_count
            FROM product.v022_product_input_member member
            JOIN catalog.universe_snapshot snapshot
              ON snapshot.universe_snapshot_id=member.universe_snapshot_id
           WHERE member.product_input_snapshot_id=NEW.product_input_snapshot_id
           LIMIT 1;
          SELECT count(*),max(ordinal) INTO actual_count,max_ordinal
            FROM product.v022_product_input_member
           WHERE product_input_snapshot_id=NEW.product_input_snapshot_id;
          IF expected_count IS NULL OR actual_count<>expected_count OR
             max_ordinal<>expected_count-1 THEN
            RAISE EXCEPTION 'Product Input Snapshot member set is incomplete';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_product_input_member_validate
          BEFORE INSERT ON product.v022_product_input_member
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_product_input_member();
        CREATE CONSTRAINT TRIGGER trg_v022_product_input_member_complete
          AFTER INSERT ON product.v022_product_input_snapshot
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION product.validate_v022_product_input_member_complete();
        CREATE TRIGGER trg_v022_product_input_member_append_only
          BEFORE UPDATE OR DELETE ON product.v022_product_input_member
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_product_input_payload_binding_validate
          BEFORE INSERT ON data.v022_product_input_payload_binding
          FOR EACH ROW EXECUTE FUNCTION data.validate_v022_product_input_payload_binding();
        CREATE TRIGGER trg_v022_product_input_payload_binding_append_only
          BEFORE UPDATE OR DELETE ON data.v022_product_input_payload_binding
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM product.v022_product_input_member) OR
             EXISTS (SELECT 1 FROM data.v022_product_input_payload_binding) THEN
            RAISE EXCEPTION 'Cannot downgrade nonempty Product runtime input state';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS product.validate_v022_product_input_member_complete() CASCADE;
        DROP FUNCTION IF EXISTS data.validate_v022_product_input_payload_binding() CASCADE;
        DROP FUNCTION IF EXISTS product.validate_v022_product_input_member() CASCADE;
        DROP TABLE data.v022_product_input_payload_binding;
        DROP TABLE product.v022_product_input_member;
        """
    )
