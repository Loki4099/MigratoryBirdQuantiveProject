# ruff: noqa: E501
"""Add v0.22 Product definition and three independent version identities.

Revision ID: 20260811_64_v022_product
Revises: 20260811_63_v022_comparison
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_64_v022_product"
down_revision: str | None = "20260811_63_v022_comparison"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE product.v022_product_definition (
          product_definition_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          product_key varchar(160) NOT NULL UNIQUE,
          name varchar(240) NOT NULL,
          description text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (btrim(product_key) <> '' AND btrim(name) <> '')
        );
        CREATE TABLE product.v022_execution_version (
          execution_version_id uuid PRIMARY KEY,
          product_definition_id uuid NOT NULL REFERENCES product.v022_product_definition,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          version_number integer NOT NULL CHECK (version_number >= 1),
          configuration_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_research_configuration_snapshot,
          promotion_result_evidence_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_result_evidence_snapshot,
          runtime_policy_document jsonb NOT NULL,
          execution_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (execution_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (jsonb_typeof(runtime_policy_document)='object' AND
                 runtime_policy_document <> '{}'::jsonb),
          UNIQUE (product_definition_id,version_number)
        );
        CREATE TABLE product.v022_qualification_version (
          qualification_version_id uuid PRIMARY KEY,
          product_definition_id uuid NOT NULL REFERENCES product.v022_product_definition,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          version_number integer NOT NULL CHECK (version_number >= 1),
          execution_version_id uuid NOT NULL REFERENCES product.v022_execution_version,
          result_evidence_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_result_evidence_snapshot,
          qualification_document jsonb NOT NULL,
          qualification_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (qualification_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (jsonb_typeof(qualification_document)='object' AND
                 qualification_document <> '{}'::jsonb),
          UNIQUE (product_definition_id,version_number)
        );
        CREATE TABLE product.v022_monitoring_policy_version (
          monitoring_policy_version_id uuid PRIMARY KEY,
          product_definition_id uuid NOT NULL REFERENCES product.v022_product_definition,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          version_number integer NOT NULL CHECK (version_number >= 1),
          monitoring_policy_document jsonb NOT NULL,
          monitoring_policy_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (monitoring_policy_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (jsonb_typeof(monitoring_policy_document)='object' AND
                 monitoring_policy_document <> '{}'::jsonb),
          UNIQUE (product_definition_id,version_number)
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION product.validate_v022_product_definition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_product_definition_validate
          BEFORE INSERT ON product.v022_product_definition
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_product_definition();

        CREATE FUNCTION product.validate_v022_execution_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE definition_status varchar; configuration_status varchar;
                evidence_status varchar; evidence_configuration uuid;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact.status INTO definition_status
            FROM product.v022_product_definition definition
            JOIN lineage.artifact artifact ON artifact.artifact_id=definition.artifact_id
           WHERE definition.product_definition_id=NEW.product_definition_id;
          SELECT artifact.status INTO configuration_status
            FROM experiment.v022_research_configuration_snapshot configuration
            JOIN lineage.artifact artifact ON artifact.artifact_id=configuration.artifact_id
           WHERE configuration.configuration_snapshot_id=NEW.configuration_snapshot_id;
          SELECT artifact.status,evidence.configuration_snapshot_id
            INTO evidence_status,evidence_configuration
            FROM experiment.v022_result_evidence_snapshot evidence
            JOIN lineage.artifact artifact ON artifact.artifact_id=evidence.artifact_id
           WHERE evidence.result_evidence_snapshot_id=NEW.promotion_result_evidence_snapshot_id;
          IF definition_status <> 'published' OR configuration_status <> 'published' OR
             evidence_status <> 'published' THEN
            RAISE EXCEPTION 'Execution Version requires published Product, Configuration, and Evidence';
          END IF;
          IF evidence_configuration <> NEW.configuration_snapshot_id THEN
            RAISE EXCEPTION 'Execution Version Evidence must bind the exact Configuration';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_execution_version_validate
          BEFORE INSERT ON product.v022_execution_version
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_execution_version();

        CREATE FUNCTION product.validate_v022_qualification_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE definition_status varchar; execution_status varchar; evidence_status varchar;
                execution_product uuid; execution_configuration uuid; evidence_configuration uuid;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact.status INTO definition_status
            FROM product.v022_product_definition definition
            JOIN lineage.artifact artifact ON artifact.artifact_id=definition.artifact_id
           WHERE definition.product_definition_id=NEW.product_definition_id;
          SELECT artifact.status,execution.product_definition_id,execution.configuration_snapshot_id
            INTO execution_status,execution_product,execution_configuration
            FROM product.v022_execution_version execution
            JOIN lineage.artifact artifact ON artifact.artifact_id=execution.artifact_id
           WHERE execution.execution_version_id=NEW.execution_version_id;
          SELECT artifact.status,evidence.configuration_snapshot_id
            INTO evidence_status,evidence_configuration
            FROM experiment.v022_result_evidence_snapshot evidence
            JOIN lineage.artifact artifact ON artifact.artifact_id=evidence.artifact_id
           WHERE evidence.result_evidence_snapshot_id=NEW.result_evidence_snapshot_id;
          IF definition_status <> 'published' OR execution_status <> 'published' OR
             evidence_status <> 'published' THEN
            RAISE EXCEPTION 'Qualification Version requires published Product, Execution, and Evidence';
          END IF;
          IF execution_product <> NEW.product_definition_id OR
             evidence_configuration <> execution_configuration THEN
            RAISE EXCEPTION 'Qualification Version must bind the exact Product Execution configuration';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_qualification_version_validate
          BEFORE INSERT ON product.v022_qualification_version
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_qualification_version();

        CREATE FUNCTION product.validate_v022_monitoring_policy_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE definition_status varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact.status INTO definition_status
            FROM product.v022_product_definition definition
            JOIN lineage.artifact artifact ON artifact.artifact_id=definition.artifact_id
           WHERE definition.product_definition_id=NEW.product_definition_id;
          IF definition_status <> 'published' THEN
            RAISE EXCEPTION 'Monitoring Policy Version requires published Product Definition';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_monitoring_policy_version_validate
          BEFORE INSERT ON product.v022_monitoring_policy_version
          FOR EACH ROW EXECUTE FUNCTION product.validate_v022_monitoring_policy_version();
        """
    )
    for table in (
        "v022_product_definition",
        "v022_execution_version",
        "v022_qualification_version",
        "v022_monitoring_policy_version",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON product.{table} FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS product.validate_v022_monitoring_policy_version() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS product.validate_v022_qualification_version() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS product.validate_v022_execution_version() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS product.validate_v022_product_definition() CASCADE")
    op.drop_table("v022_monitoring_policy_version", schema="product")
    op.drop_table("v022_qualification_version", schema="product")
    op.drop_table("v022_execution_version", schema="product")
    op.drop_table("v022_product_definition", schema="product")
