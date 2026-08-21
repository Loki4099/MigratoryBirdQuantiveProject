# ruff: noqa: E501
"""Add frozen representative v0.22 Shadow Plans.

Revision ID: 20260811_68_v022_shadow
Revises: 20260811_67_v022_release
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_68_v022_shadow"
down_revision: str | None = "20260811_67_v022_release"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_shadow_plan (
          shadow_plan_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          plan_key varchar(160) NOT NULL,
          version_number integer NOT NULL CHECK (version_number >= 1),
          supported_context_document jsonb NOT NULL,
          plan_fingerprint varchar(64) NOT NULL UNIQUE CHECK (plan_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (plan_key,version_number),
          CHECK (jsonb_typeof(supported_context_document)='array' AND
                 jsonb_array_length(supported_context_document)>0)
        );
        CREATE TABLE workspace.v022_shadow_representative (
          shadow_representative_id uuid PRIMARY KEY,
          shadow_plan_id uuid NOT NULL REFERENCES workspace.v022_shadow_plan,
          ordinal integer NOT NULL CHECK (ordinal >= 1),
          product_enrollment_id uuid NOT NULL REFERENCES product.v022_product_enrollment,
          execution_version_id uuid NOT NULL REFERENCES product.v022_execution_version,
          asset_context_key varchar(160) NOT NULL,
          asset_context_class varchar(24) NOT NULL CHECK (
            asset_context_class IN ('etf','large_cap','other')
          ),
          asset_context_fingerprint varchar(64) NOT NULL
            CHECK (asset_context_fingerprint ~ '^[0-9a-f]{64}$'),
          frequency varchar(16) NOT NULL CHECK (frequency IN ('weekly','monthly')),
          representative_role varchar(32) NOT NULL CHECK (
            representative_role IN ('active_product_shadow','shadow_only')
          ),
          minimum_required_sessions integer NOT NULL CHECK (
            (frequency='weekly' AND minimum_required_sessions=12) OR
            (frequency='monthly' AND minimum_required_sessions=3)
          ),
          drives_formal_capital boolean NOT NULL DEFAULT false CHECK (NOT drives_formal_capital),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (shadow_plan_id,ordinal),
          UNIQUE (shadow_plan_id,asset_context_key,frequency),
          UNIQUE (shadow_plan_id,product_enrollment_id),
          UNIQUE (shadow_plan_id,execution_version_id),
          CHECK (btrim(asset_context_key) <> '')
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION workspace.validate_v022_shadow_plan()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_plan_validate
          BEFORE INSERT ON workspace.v022_shadow_plan
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_shadow_plan();

        CREATE FUNCTION workspace.validate_v022_shadow_representative()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE plan_row record; enrollment_row record; execution_status varchar;
                configuration_frequency varchar; configuration_context varchar;
                canonical_context_key varchar; canonical_context_class varchar;
                declared_context boolean;
        BEGIN
          SELECT plan.*,artifact.status INTO plan_row
            FROM workspace.v022_shadow_plan plan
            JOIN lineage.artifact artifact ON artifact.artifact_id=plan.artifact_id
           WHERE plan.shadow_plan_id=NEW.shadow_plan_id;
          PERFORM data.assert_artifact_draft(plan_row.artifact_id);
          SELECT enrollment.execution_version_id,enrollment_artifact.status
            INTO enrollment_row
            FROM product.v022_product_enrollment enrollment
            JOIN lineage.artifact enrollment_artifact
              ON enrollment_artifact.artifact_id=enrollment.artifact_id
           WHERE enrollment.product_enrollment_id=NEW.product_enrollment_id;
          SELECT execution_artifact.status,
                 configuration.semantic_identity_document->>'frequency',
                 configuration.semantic_identity_document->>'asset_context_fingerprint'
            INTO execution_status,configuration_frequency,configuration_context
            FROM product.v022_execution_version execution
            JOIN lineage.artifact execution_artifact
              ON execution_artifact.artifact_id=execution.artifact_id
            JOIN experiment.v022_research_configuration_snapshot configuration
              ON configuration.configuration_snapshot_id=execution.configuration_snapshot_id
           WHERE execution.execution_version_id=NEW.execution_version_id;
          SELECT EXISTS (
            SELECT 1 FROM jsonb_array_elements(plan_row.supported_context_document) item
             WHERE item->>'asset_context_key'=NEW.asset_context_key
               AND item->>'asset_context_class'=NEW.asset_context_class
               AND item->>'frequency'=NEW.frequency
          ) INTO declared_context;
          SELECT draft.asset_context_document->>'asset_context_key',
                 CASE
                   WHEN NOT EXISTS (
                     SELECT 1 FROM jsonb_array_elements(
                       draft.asset_context_document->'members'
                     ) member
                      WHERE coalesce(member->>'instrument_type','') NOT ILIKE '%ETF%'
                   ) THEN 'etf'
                   WHEN draft.asset_context_document->>'asset_context_key' ~* 'large[_-]?cap'
                     THEN 'large_cap'
                   ELSE 'other'
                 END
            INTO canonical_context_key,canonical_context_class
            FROM workspace.v022_graph_draft draft
           WHERE draft.asset_context_fingerprint=NEW.asset_context_fingerprint
           ORDER BY draft.created_at LIMIT 1;
          IF enrollment_row.status <> 'published' OR execution_status <> 'published' OR
             enrollment_row.execution_version_id <> NEW.execution_version_id THEN
            RAISE EXCEPTION 'Shadow representative requires its exact published Enrollment and Execution';
          END IF;
          IF configuration_frequency <> NEW.frequency OR
             configuration_context <> NEW.asset_context_fingerprint THEN
            RAISE EXCEPTION 'Shadow representative context does not match frozen Configuration';
          END IF;
          IF canonical_context_key IS NULL OR canonical_context_key <> NEW.asset_context_key OR
             canonical_context_class <> NEW.asset_context_class THEN
            RAISE EXCEPTION 'Shadow representative key or class does not match frozen Asset Context';
          END IF;
          IF NOT declared_context THEN
            RAISE EXCEPTION 'Shadow representative is not declared by its Plan context matrix';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_shadow_representative_validate
          BEFORE INSERT ON workspace.v022_shadow_representative
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_shadow_representative();

        CREATE FUNCTION workspace.validate_v022_shadow_plan_completeness()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_count integer; actual_count integer; minimum_ordinal integer;
                maximum_ordinal integer;
        BEGIN
          expected_count := jsonb_array_length(NEW.supported_context_document);
          SELECT count(*),min(ordinal),max(ordinal)
            INTO actual_count,minimum_ordinal,maximum_ordinal
            FROM workspace.v022_shadow_representative
           WHERE shadow_plan_id=NEW.shadow_plan_id;
          IF actual_count <> expected_count OR minimum_ordinal <> 1 OR
             maximum_ordinal <> expected_count THEN
            RAISE EXCEPTION 'Shadow Plan requires exactly one ordered representative per context';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_shadow_plan_complete
          AFTER INSERT ON workspace.v022_shadow_plan
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION workspace.validate_v022_shadow_plan_completeness();

        CREATE FUNCTION workspace.protect_v022_shadow_representative()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE plan_artifact uuid;
        BEGIN
          SELECT artifact_id INTO plan_artifact FROM workspace.v022_shadow_plan
           WHERE shadow_plan_id=OLD.shadow_plan_id;
          PERFORM data.assert_artifact_draft(plan_artifact);
          RETURN OLD;
        END $$;
        CREATE TRIGGER trg_v022_shadow_representative_protect
          BEFORE UPDATE OR DELETE ON workspace.v022_shadow_representative
          FOR EACH ROW EXECUTE FUNCTION workspace.protect_v022_shadow_representative();
        CREATE TRIGGER trg_v022_shadow_plan_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_shadow_plan
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS workspace.protect_v022_shadow_representative() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_shadow_plan_completeness() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_shadow_representative() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS workspace.validate_v022_shadow_plan() CASCADE")
    op.drop_table("v022_shadow_representative", schema="workspace")
    op.drop_table("v022_shadow_plan", schema="workspace")
