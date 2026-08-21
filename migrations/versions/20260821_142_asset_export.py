"""Add exact v0.22 asset-data export jobs.

Revision ID: 20260821_142_asset_export
Revises: 20260821_141_simple_runtime
"""

from __future__ import annotations

from alembic import op

revision = "20260821_142_asset_export"
down_revision = "20260821_141_simple_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        ALTER TABLE ops.work_item DROP CONSTRAINT ck_work_item_ck_work_item_type;
        ALTER TABLE ops.work_item ADD CONSTRAINT ck_work_item_ck_work_item_type CHECK (
          work_type IN ('predictive','portfolio','monitoring','export','asset_export')
        );

        CREATE TABLE workspace.v022_asset_data_export_job (
          export_job_id uuid PRIMARY KEY,
          work_item_id uuid NOT NULL UNIQUE REFERENCES ops.work_item,
          researcher_key varchar(160) NOT NULL,
          graph_draft_id uuid NOT NULL,
          graph_draft_revision integer NOT NULL,
          asset_registry_release_id uuid NOT NULL
            REFERENCES catalog.asset_registry_release,
          dataset_publication_id uuid NOT NULL REFERENCES data.dataset_publication,
          dataset_gate_assessment_id uuid NOT NULL
            REFERENCES data.v022_dataset_gate_assessment,
          request_fingerprint varchar(64) NOT NULL
            CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          request_document jsonb NOT NULL CHECK (jsonb_typeof(request_document)='object'),
          processed_rows bigint NOT NULL DEFAULT 0 CHECK (processed_rows>=0),
          processed_bytes bigint NOT NULL DEFAULT 0 CHECK (processed_bytes>=0),
          progress_stage varchar(80) NOT NULL DEFAULT 'queued',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (graph_draft_id,graph_draft_revision)
            REFERENCES workspace.v022_graph_draft_revision(graph_draft_id,revision),
          CHECK ((
            request_document->>'contract_version'='v0.22.asset_data_export_request.v1' AND
            request_document->>'graph_draft_id'=graph_draft_id::text AND
            (request_document->>'graph_draft_revision')::integer=graph_draft_revision AND
            request_document->>'asset_registry_release_id'=asset_registry_release_id::text AND
            request_document->>'dataset_publication_id'=dataset_publication_id::text AND
            request_document->>'dataset_gate_assessment_id'=
              dataset_gate_assessment_id::text
          ) IS TRUE)
        );
        CREATE INDEX ix_v022_asset_data_export_request
          ON workspace.v022_asset_data_export_job(request_fingerprint,created_at DESC);
        CREATE INDEX ix_v022_asset_data_export_owner
          ON workspace.v022_asset_data_export_job(researcher_key,created_at DESC);

        CREATE TABLE workspace.v022_asset_data_export_result (
          export_result_id uuid PRIMARY KEY,
          export_job_id uuid NOT NULL UNIQUE
            REFERENCES workspace.v022_asset_data_export_job,
          storage_uri text NOT NULL,
          content_hash varchar(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
          byte_size bigint NOT NULL CHECK (byte_size>0),
          filename varchar(240) NOT NULL,
          schema_version varchar(80) NOT NULL
            CHECK (schema_version='v022_asset_data_research_package_v1'),
          manifest_document jsonb NOT NULL CHECK (jsonb_typeof(manifest_document)='object'),
          expires_at timestamptz NOT NULL DEFAULT now()+interval '7 days',
          last_accessed_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (storage_uri='asset-data-export://sha256/' || content_hash || '.zip')
        );
        CREATE INDEX ix_v022_asset_data_export_result_expiry
          ON workspace.v022_asset_data_export_result(expires_at);

        CREATE FUNCTION workspace.v022_asset_data_export_result_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='UPDATE' AND
             NEW.last_accessed_at IS DISTINCT FROM OLD.last_accessed_at AND
             NEW.export_result_id=OLD.export_result_id AND
             NEW.export_job_id=OLD.export_job_id AND
             NEW.storage_uri=OLD.storage_uri AND NEW.content_hash=OLD.content_hash AND
             NEW.byte_size=OLD.byte_size AND NEW.filename=OLD.filename AND
             NEW.schema_version=OLD.schema_version AND
             NEW.manifest_document=OLD.manifest_document AND
             NEW.expires_at=OLD.expires_at AND NEW.created_at=OLD.created_at THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'v0.22 Asset Data Export Results are immutable';
        END $$;
        CREATE TRIGGER trg_v022_asset_data_export_result_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_asset_data_export_result
          FOR EACH ROW EXECUTE FUNCTION
            workspace.v022_asset_data_export_result_append_only();
        """
    )


def downgrade() -> None:
    op.execute(
        r"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM workspace.v022_asset_data_export_job) THEN
            RAISE EXCEPTION 'Cannot downgrade while v0.22 Asset Data Export evidence exists';
          END IF;
        END $$;
        DROP TRIGGER IF EXISTS trg_v022_asset_data_export_result_append_only
          ON workspace.v022_asset_data_export_result;
        DROP FUNCTION IF EXISTS workspace.v022_asset_data_export_result_append_only();
        DROP TABLE workspace.v022_asset_data_export_result;
        DROP TABLE workspace.v022_asset_data_export_job;
        ALTER TABLE ops.work_item DROP CONSTRAINT ck_work_item_ck_work_item_type;
        ALTER TABLE ops.work_item ADD CONSTRAINT ck_work_item_ck_work_item_type CHECK (
          work_type IN ('predictive','portfolio','monitoring','export')
        );
        """
    )
