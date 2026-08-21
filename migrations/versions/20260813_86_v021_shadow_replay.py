"""Freeze executable v0.21 Product Versions for v0.22 Shadow-only replay.

Revision ID: 20260813_86_v021_shadow_replay
Revises: 20260813_85_v022_product_dec
"""

from __future__ import annotations

from alembic import op

revision = "20260813_86_v021_shadow_replay"
down_revision = "20260813_85_v022_product_dec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE compatibility.v022_shadow_v021_execution_spec (
          artifact_id uuid PRIMARY KEY REFERENCES lineage.artifact,
          product_version_id uuid NOT NULL REFERENCES product.product_version,
          data_bundle_selection_policy varchar(64) NOT NULL
            CHECK (data_bundle_selection_policy='latest_published_at_decision_cutoff'),
          prior_holdings jsonb NOT NULL DEFAULT '[]'::jsonb
            CHECK (jsonb_typeof(prior_holdings)='array'),
          spec_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (spec_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE FUNCTION compatibility.validate_v022_shadow_v021_execution_spec()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_row record; product_artifact_id uuid;
                dependency_count integer;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,version_number,status INTO artifact_row
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT artifact_id INTO product_artifact_id
            FROM product.product_version
           WHERE product_version_id=NEW.product_version_id;
          SELECT count(*) INTO dependency_count
            FROM lineage.artifact_dependency
           WHERE artifact_id=NEW.artifact_id
             AND depends_on_artifact_id=product_artifact_id
             AND role='product_version' AND ordinal=0;
          IF artifact_row.artifact_type IS DISTINCT FROM 'v021_shadow_execution_spec' OR
             artifact_row.version_number<1 OR dependency_count<>1 OR
             jsonb_array_length(NEW.prior_holdings)<>
               (SELECT count(DISTINCT value)
                  FROM jsonb_array_elements_text(NEW.prior_holdings)) THEN
            RAISE EXCEPTION 'Invalid executable v0.21 Shadow replay specification';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_v021_execution_spec_validate
          BEFORE INSERT ON compatibility.v022_shadow_v021_execution_spec
          FOR EACH ROW EXECUTE FUNCTION
            compatibility.validate_v022_shadow_v021_execution_spec();
        CREATE TRIGGER trg_v022_shadow_v021_execution_spec_immutable
          BEFORE UPDATE OR DELETE ON compatibility.v022_shadow_v021_execution_spec
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM compatibility.v022_shadow_v021_execution_spec) THEN
            RAISE EXCEPTION 'Cannot downgrade while v0.21 Shadow replay specs exist';
          END IF;
        END $$;
        DROP TABLE compatibility.v022_shadow_v021_execution_spec;
        DROP FUNCTION compatibility.validate_v022_shadow_v021_execution_spec();
        """
    )
