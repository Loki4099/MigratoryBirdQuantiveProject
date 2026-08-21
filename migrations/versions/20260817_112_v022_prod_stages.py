# ruff: noqa: E501
"""Add immutable Product runtime execution and stage projections.

Revision ID: 20260817_112_v022_prod_stages
Revises: 20260817_111_v022_prod_runtime
"""

from __future__ import annotations

from alembic import op

revision = "20260817_112_v022_prod_stages"
down_revision = "20260817_111_v022_prod_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM product.v022_product_input_snapshot) THEN
            RAISE EXCEPTION 'M112 requires empty Product Input Snapshot state';
          END IF;
        END $$;

        CREATE TABLE product.v022_product_runtime_execution (
          product_runtime_execution_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          product_input_snapshot_id uuid NOT NULL
            REFERENCES product.v022_product_input_snapshot,
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          decision_session_id uuid NOT NULL
            REFERENCES product.v022_decision_schedule_session,
          runtime_version varchar(160) NOT NULL CHECK (btrim(runtime_version)<>''),
          execution_document jsonb NOT NULL CHECK (jsonb_typeof(execution_document)='object'),
          execution_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (execution_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (product_input_snapshot_id,runtime_version),
          CHECK ((
            execution_document->>'contract_version'=
              'v0.22.product_runtime_execution.v1' AND
            execution_document->>'product_input_snapshot_id'=
              product_input_snapshot_id::text AND
            execution_document->>'configuration_snapshot_id'=
              configuration_snapshot_id::text AND
            execution_document->>'decision_session_id'=decision_session_id::text AND
            execution_document->>'runtime_version'=runtime_version AND
            execution_document->>'runtime_network_access'='false'
          ) IS TRUE)
        );

        CREATE TABLE product.v022_product_runtime_stage (
          product_runtime_stage_id uuid PRIMARY KEY,
          product_runtime_execution_id uuid NOT NULL
            REFERENCES product.v022_product_runtime_execution,
          stage_kind varchar(24) NOT NULL CHECK (
            stage_kind IN ('aggregation','strategy','defense','merge')
          ),
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          input_count integer NOT NULL CHECK (input_count>=1),
          stage_document jsonb NOT NULL CHECK (jsonb_typeof(stage_document)='object'),
          stage_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (stage_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (product_runtime_execution_id,stage_kind),
          CHECK ((
            stage_document->>'contract_version'='v0.22.product_runtime_stage.v1' AND
            stage_document->>'product_runtime_execution_id'=
              product_runtime_execution_id::text AND
            stage_document->>'stage_kind'=stage_kind AND
            (stage_document->>'input_count')::integer=input_count
          ) IS TRUE)
        );

        CREATE TABLE product.v022_product_runtime_stage_input (
          product_runtime_stage_id uuid NOT NULL
            REFERENCES product.v022_product_runtime_stage,
          ordinal integer NOT NULL CHECK (ordinal>=0),
          role varchar(80) NOT NULL CHECK (btrim(role)<>''),
          input_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          PRIMARY KEY (product_runtime_stage_id,ordinal),
          UNIQUE (product_runtime_stage_id,role,ordinal)
        );

        CREATE FUNCTION product.validate_v022_product_runtime_execution()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; snapshot_row record; configuration_row record;
                dependency_count integer;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number,status INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT snapshot.*,artifact.status,
                 artifact.artifact_id AS snapshot_artifact_id,
                 execution.configuration_snapshot_id
            INTO snapshot_row
            FROM product.v022_product_input_snapshot snapshot
            JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id
            JOIN product.v022_execution_version execution
              ON execution.execution_version_id=snapshot.execution_version_id
           WHERE snapshot.product_input_snapshot_id=NEW.product_input_snapshot_id;
          SELECT snapshot.artifact_id,artifact.status INTO configuration_row
            FROM experiment.v022_research_configuration_snapshot snapshot
            JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id
           WHERE snapshot.configuration_snapshot_id=NEW.configuration_snapshot_id;
          IF artifact_row.artifact_type IS DISTINCT FROM
               'v022_product_runtime_execution' OR
             artifact_row.artifact_key IS DISTINCT FROM
               'v022_product_runtime_execution__' || NEW.product_input_snapshot_id::text ||
               '__' || NEW.runtime_version OR
             artifact_row.version_number IS DISTINCT FROM 1 OR
             artifact_row.status IS DISTINCT FROM 'draft' OR
             snapshot_row.status IS DISTINCT FROM 'published' OR
             snapshot_row.configuration_snapshot_id IS DISTINCT FROM
               NEW.configuration_snapshot_id OR
             snapshot_row.decision_session_id IS DISTINCT FROM NEW.decision_session_id OR
             configuration_row.status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Product Runtime Execution exact identity invalid';
          END IF;
          SELECT count(*) INTO dependency_count FROM lineage.artifact_dependency dependency
           WHERE dependency.artifact_id=NEW.artifact_id;
          IF dependency_count<>2 OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=snapshot_row.snapshot_artifact_id
                 AND dependency.role='product_input_snapshot' AND dependency.ordinal=0) OR
             NOT EXISTS (SELECT 1 FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id
                 AND dependency.depends_on_artifact_id=configuration_row.artifact_id
                 AND dependency.role='configuration_snapshot' AND dependency.ordinal=1) THEN
            RAISE EXCEPTION 'Product Runtime Execution exact lineage invalid';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_product_runtime_execution_validate
          BEFORE INSERT ON product.v022_product_runtime_execution
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_product_runtime_execution();
        CREATE TRIGGER trg_v022_product_runtime_execution_append_only
          BEFORE UPDATE OR DELETE ON product.v022_product_runtime_execution
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();

        CREATE FUNCTION product.validate_v022_product_runtime_stage()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; execution_row record;
                expected_artifact_type varchar; expected_artifact_key varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT execution.*,artifact.status,
                 artifact.artifact_id AS execution_artifact_id
            INTO execution_row
            FROM product.v022_product_runtime_execution execution
            JOIN lineage.artifact artifact ON artifact.artifact_id=execution.artifact_id
           WHERE execution.product_runtime_execution_id=
                 NEW.product_runtime_execution_id;
          SELECT artifact_type,artifact_key,version_number,status INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          expected_artifact_type := CASE NEW.stage_kind
            WHEN 'aggregation' THEN 'v022_product_aggregation_output'
            WHEN 'strategy' THEN 'v022_product_strategy_target'
            WHEN 'defense' THEN 'v022_product_defense_decision'
            WHEN 'merge' THEN 'v022_product_merged_target'
          END;
          expected_artifact_key := expected_artifact_type || '__' ||
            execution_row.execution_fingerprint;
          IF execution_row.status IS DISTINCT FROM 'published' OR
             artifact_row.artifact_type IS DISTINCT FROM expected_artifact_type OR
             artifact_row.artifact_key IS DISTINCT FROM expected_artifact_key OR
             artifact_row.version_number IS DISTINCT FROM 1 OR
             artifact_row.status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'Product Runtime Stage exact identity invalid';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_product_runtime_stage_validate
          BEFORE INSERT ON product.v022_product_runtime_stage
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_product_runtime_stage();
        CREATE TRIGGER trg_v022_product_runtime_stage_append_only
          BEFORE UPDATE OR DELETE ON product.v022_product_runtime_stage
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();

        CREATE FUNCTION product.validate_v022_product_runtime_stage_input()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE stage_artifact uuid; input_status varchar;
        BEGIN
          SELECT artifact_id INTO stage_artifact
            FROM product.v022_product_runtime_stage
           WHERE product_runtime_stage_id=NEW.product_runtime_stage_id;
          PERFORM data.assert_artifact_draft(stage_artifact);
          SELECT status INTO input_status FROM lineage.artifact
           WHERE artifact_id=NEW.input_artifact_id;
          IF input_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Product Runtime Stage input must be published';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_product_runtime_stage_input_validate
          BEFORE INSERT ON product.v022_product_runtime_stage_input
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_product_runtime_stage_input();
        CREATE TRIGGER trg_v022_product_runtime_stage_input_append_only
          BEFORE UPDATE OR DELETE ON product.v022_product_runtime_stage_input
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();

        CREATE FUNCTION product.validate_v022_product_runtime_stage_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE execution_artifact uuid; actual_count integer;
                minimum_ordinal integer; maximum_ordinal integer;
                input_lineage_matches boolean; topology_valid boolean;
        BEGIN
          SELECT artifact_id INTO execution_artifact
            FROM product.v022_product_runtime_execution
           WHERE product_runtime_execution_id=NEW.product_runtime_execution_id;
          SELECT count(*),min(ordinal),max(ordinal),
                 coalesce(bool_and(EXISTS (
                   SELECT 1 FROM lineage.artifact_dependency dependency
                    WHERE dependency.artifact_id=NEW.artifact_id
                      AND dependency.depends_on_artifact_id=input.input_artifact_id
                      AND dependency.role=input.role
                      AND dependency.ordinal=input.ordinal
                 )),false)
            INTO actual_count,minimum_ordinal,maximum_ordinal,input_lineage_matches
            FROM product.v022_product_runtime_stage_input input
           WHERE input.product_runtime_stage_id=NEW.product_runtime_stage_id;
          topology_valid := EXISTS (
            SELECT 1 FROM product.v022_product_runtime_stage_input root
             WHERE root.product_runtime_stage_id=NEW.product_runtime_stage_id
               AND root.ordinal=0 AND root.role='runtime_execution'
               AND root.input_artifact_id=execution_artifact
          );
          IF NEW.stage_kind='aggregation' THEN
            topology_valid := topology_valid AND NEW.input_count>=2 AND NOT EXISTS (
              SELECT 1 FROM product.v022_product_runtime_stage_input input
               WHERE input.product_runtime_stage_id=NEW.product_runtime_stage_id
                 AND input.ordinal>0 AND input.role<>'processing_manifest'
            );
          ELSIF NEW.stage_kind='strategy' THEN
            topology_valid := topology_valid AND NEW.input_count=2 AND EXISTS (
              SELECT 1 FROM product.v022_product_runtime_stage_input input
              JOIN product.v022_product_runtime_stage prior
                ON prior.artifact_id=input.input_artifact_id
               AND prior.product_runtime_execution_id=NEW.product_runtime_execution_id
               AND prior.stage_kind='aggregation'
             WHERE input.product_runtime_stage_id=NEW.product_runtime_stage_id
               AND input.ordinal=1 AND input.role='aggregation_output'
            );
          ELSIF NEW.stage_kind='defense' THEN
            topology_valid := topology_valid AND NEW.input_count=1;
          ELSIF NEW.stage_kind='merge' THEN
            topology_valid := topology_valid AND NEW.input_count IN (2,3) AND EXISTS (
              SELECT 1 FROM product.v022_product_runtime_stage_input input
              JOIN product.v022_product_runtime_stage prior
                ON prior.artifact_id=input.input_artifact_id
               AND prior.product_runtime_execution_id=NEW.product_runtime_execution_id
               AND prior.stage_kind='strategy'
             WHERE input.product_runtime_stage_id=NEW.product_runtime_stage_id
               AND input.ordinal=1 AND input.role='strategy_target'
            ) AND (
              (NEW.input_count=2 AND NOT EXISTS (
                SELECT 1 FROM product.v022_product_runtime_stage_input input
                 WHERE input.product_runtime_stage_id=NEW.product_runtime_stage_id
                   AND input.role='defense_decision'
              )) OR
              (NEW.input_count=3 AND EXISTS (
                SELECT 1 FROM product.v022_product_runtime_stage_input input
                JOIN product.v022_product_runtime_stage prior
                  ON prior.artifact_id=input.input_artifact_id
                 AND prior.product_runtime_execution_id=NEW.product_runtime_execution_id
                 AND prior.stage_kind='defense'
               WHERE input.product_runtime_stage_id=NEW.product_runtime_stage_id
                 AND input.ordinal=2 AND input.role='defense_decision'
              ))
            );
          END IF;
          IF actual_count<>NEW.input_count OR minimum_ordinal<>0 OR
             maximum_ordinal<>NEW.input_count-1 OR NOT input_lineage_matches OR
             (SELECT count(*) FROM lineage.artifact_dependency dependency
               WHERE dependency.artifact_id=NEW.artifact_id)<>NEW.input_count OR
             NOT topology_valid THEN
            RAISE EXCEPTION 'Product Runtime Stage exact input closure invalid';
          END IF;
          RETURN NEW;
        END $$;

        CREATE CONSTRAINT TRIGGER trg_v022_product_runtime_stage_complete
          AFTER INSERT ON product.v022_product_runtime_stage
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION product.validate_v022_product_runtime_stage_complete();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM product.v022_product_runtime_execution) OR
             EXISTS (SELECT 1 FROM product.v022_product_runtime_stage) OR
             EXISTS (SELECT 1 FROM product.v022_product_runtime_stage_input) THEN
            RAISE EXCEPTION 'Cannot downgrade nonempty Product Runtime Stage state';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS product.validate_v022_product_runtime_stage_complete() CASCADE;
        DROP FUNCTION IF EXISTS product.validate_v022_product_runtime_stage_input() CASCADE;
        DROP FUNCTION IF EXISTS product.validate_v022_product_runtime_stage() CASCADE;
        DROP FUNCTION IF EXISTS product.validate_v022_product_runtime_execution() CASCADE;
        DROP TABLE product.v022_product_runtime_stage_input;
        DROP TABLE product.v022_product_runtime_stage;
        DROP TABLE product.v022_product_runtime_execution;
        """
    )
