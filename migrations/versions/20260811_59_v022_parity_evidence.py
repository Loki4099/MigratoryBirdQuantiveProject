# ruff: noqa: E501
"""Publish append-only v0.22 migration registries and parity Evidence.

Revision ID: 20260811_59_v022_parity
Revises: 20260810_58_v022_graph_context
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_59_v022_parity"
down_revision: str | None = "20260810_58_v022_graph_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE SCHEMA IF NOT EXISTS compatibility;

        CREATE TABLE compatibility.v022_parity_evidence (
          parity_evidence_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact ON DELETE RESTRICT,
          catalog_release_id uuid NOT NULL
            REFERENCES workspace.v022_catalog_release ON DELETE RESTRICT,
          mapped_component_artifact_id uuid NOT NULL
            REFERENCES lineage.artifact ON DELETE RESTRICT,
          evidence_record_id uuid NOT NULL UNIQUE,
          source_registry_fingerprint varchar(64) NOT NULL
            CHECK (source_registry_fingerprint ~ '^[0-9a-f]{64}$'),
          evidence_document_fingerprint varchar(64) NOT NULL
            CHECK (evidence_document_fingerprint ~ '^[0-9a-f]{64}$'),
          component_kind varchar(30) NOT NULL
            CHECK (component_kind IN ('factor_variant','signal_version')),
          legacy_key varchar(360) NOT NULL,
          mapped_variant_key varchar(240) NOT NULL,
          comparator_version varchar(40) NOT NULL,
          comparison_count smallint NOT NULL CHECK (comparison_count = 2),
          comparisons jsonb NOT NULL CHECK (jsonb_array_length(comparisons) = 2),
          passed boolean NOT NULL CHECK (passed),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (source_registry_fingerprint, component_kind, legacy_key)
        );

        CREATE TABLE compatibility.v022_migration_registry (
          migration_registry_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact ON DELETE RESTRICT,
          catalog_release_id uuid NOT NULL
            REFERENCES workspace.v022_catalog_release ON DELETE RESTRICT,
          registry_version varchar(40) NOT NULL UNIQUE,
          source_registry_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (source_registry_fingerprint ~ '^[0-9a-f]{64}$'),
          oracle_baseline_id varchar(160) NOT NULL,
          evidence_document_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (evidence_document_fingerprint ~ '^[0-9a-f]{64}$'),
          runtime_contract_fingerprint varchar(64) NOT NULL
            CHECK (runtime_contract_fingerprint ~ '^[0-9a-f]{64}$'),
          migration_status varchar(30) NOT NULL CHECK (migration_status = 'parity_passed'),
          factor_variant_count integer NOT NULL CHECK (factor_variant_count = 28),
          signal_version_count integer NOT NULL CHECK (signal_version_count = 51),
          comparison_count integer NOT NULL CHECK (comparison_count = 158),
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE compatibility.v022_migration_registry_member (
          migration_registry_id uuid NOT NULL
            REFERENCES compatibility.v022_migration_registry ON DELETE RESTRICT,
          parity_evidence_id uuid NOT NULL UNIQUE
            REFERENCES compatibility.v022_parity_evidence ON DELETE RESTRICT,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          component_kind varchar(30) NOT NULL
            CHECK (component_kind IN ('factor_variant','signal_version')),
          legacy_key varchar(360) NOT NULL,
          PRIMARY KEY (migration_registry_id, parity_evidence_id),
          UNIQUE (migration_registry_id, ordinal),
          UNIQUE (migration_registry_id, component_kind, legacy_key)
        );

        CREATE FUNCTION compatibility.validate_v022_migration_member() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE evidence_record compatibility.v022_parity_evidence%ROWTYPE;
        DECLARE registry_record compatibility.v022_migration_registry%ROWTYPE;
        BEGIN
          SELECT * INTO evidence_record
            FROM compatibility.v022_parity_evidence
           WHERE parity_evidence_id=NEW.parity_evidence_id;
          SELECT * INTO registry_record
            FROM compatibility.v022_migration_registry
           WHERE migration_registry_id=NEW.migration_registry_id;
          IF evidence_record.source_registry_fingerprint
               <> registry_record.source_registry_fingerprint
             OR evidence_record.evidence_document_fingerprint
               <> registry_record.evidence_document_fingerprint
             OR evidence_record.component_kind <> NEW.component_kind
             OR evidence_record.legacy_key <> NEW.legacy_key
             OR NOT evidence_record.passed THEN
            RAISE EXCEPTION 'Migration Registry member does not match passed Evidence';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_v022_migration_registry_member_validate
          BEFORE INSERT ON compatibility.v022_migration_registry_member
          FOR EACH ROW EXECUTE FUNCTION compatibility.validate_v022_migration_member();

        CREATE TRIGGER trg_v022_parity_evidence_append_only
          BEFORE UPDATE OR DELETE ON compatibility.v022_parity_evidence
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_migration_registry_append_only
          BEFORE UPDATE OR DELETE ON compatibility.v022_migration_registry
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_migration_registry_member_append_only
          BEFORE UPDATE OR DELETE ON compatibility.v022_migration_registry_member
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS compatibility.validate_v022_migration_member() CASCADE"
    )
    op.drop_table("v022_migration_registry_member", schema="compatibility")
    op.drop_table("v022_migration_registry", schema="compatibility")
    op.drop_table("v022_parity_evidence", schema="compatibility")
    op.execute("DROP SCHEMA compatibility")
