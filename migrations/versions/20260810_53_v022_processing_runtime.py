# ruff: noqa: E501
"""Add reusable v0.22 Processing runtime and graph bindings.

Revision ID: 20260810_53_v022_processing
Revises: 20260810_52_v022_graph_dag
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260810_53_v022_processing"
down_revision: str | None = "20260810_52_v022_graph_dag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE processing.node_run (
          node_run_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          node_version_id uuid NOT NULL REFERENCES processing.node_version,
          execution_fingerprint varchar(64) NOT NULL UNIQUE CHECK (execution_fingerprint ~ '^[0-9a-f]{64}$'),
          resolved_parameters jsonb NOT NULL, requested_range jsonb NOT NULL,
          executor_version varchar(120) NOT NULL,
          environment_fingerprint varchar(64) NOT NULL CHECK (environment_fingerprint ~ '^[0-9a-f]{64}$'),
          status varchar(24) NOT NULL CHECK (status IN ('running','completed','failed','cancelled','invalidated')),
          cache_eligible boolean NOT NULL,
          started_at timestamptz NOT NULL, completed_at timestamptz NULL,
          invalidated_at timestamptz NULL, failure_details jsonb NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE processing.node_run_input (
          node_run_id uuid NOT NULL REFERENCES processing.node_run,
          input_port_key varchar(160) NOT NULL,
          payload_manifest_id uuid NOT NULL REFERENCES data.payload_manifest,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          manifest_hash varchar(64) NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
          PRIMARY KEY (node_run_id,input_port_key,ordinal)
        );
        CREATE TABLE processing.node_run_output (
          node_run_id uuid NOT NULL REFERENCES processing.node_run,
          output_port_key varchar(160) NOT NULL,
          payload_manifest_id uuid NOT NULL UNIQUE REFERENCES data.payload_manifest,
          PRIMARY KEY (node_run_id,output_port_key)
        );
        CREATE TABLE processing.node_run_partition (
          node_run_id uuid NOT NULL REFERENCES processing.node_run,
          partition_key_hash varchar(64) NOT NULL CHECK (partition_key_hash ~ '^[0-9a-f]{64}$'),
          partition_document jsonb NOT NULL,
          status varchar(20) NOT NULL CHECK (status IN ('planned','running','completed','failed','reused')),
          source_revision_fingerprint varchar(64) NOT NULL CHECK (source_revision_fingerprint ~ '^[0-9a-f]{64}$'),
          PRIMARY KEY (node_run_id,partition_key_hash)
        );
        CREATE TABLE processing.node_checkpoint (
          node_checkpoint_id uuid PRIMARY KEY,
          node_run_id uuid NOT NULL REFERENCES processing.node_run,
          checkpoint_key varchar(200) NOT NULL,
          checkpoint_manifest_id uuid NOT NULL REFERENCES data.payload_manifest,
          watermark_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (node_run_id,checkpoint_key)
        );
        CREATE TABLE processing.node_run_cache_entry (
          execution_fingerprint varchar(64) PRIMARY KEY CHECK (execution_fingerprint ~ '^[0-9a-f]{64}$'),
          node_run_id uuid NOT NULL UNIQUE REFERENCES processing.node_run,
          cache_state varchar(20) NOT NULL CHECK (cache_state IN ('eligible','invalidated','evicted')),
          eligibility_checked_at timestamptz NOT NULL,
          invalidation_reason jsonb NULL
        );
        CREATE TABLE processing.graph_run_node_binding (
          graph_run_id uuid NOT NULL REFERENCES workspace.v022_graph_run,
          compiled_graph_node_id uuid NOT NULL REFERENCES workspace.compiled_graph_node,
          graph_work_item_id uuid NOT NULL REFERENCES workspace.v022_graph_work_item,
          node_run_id uuid NOT NULL REFERENCES processing.node_run,
          binding_disposition varchar(16) NOT NULL CHECK (binding_disposition IN ('executed','reused')),
          bound_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (graph_run_id,compiled_graph_node_id),
          UNIQUE (graph_run_id,graph_work_item_id)
        );
        CREATE TABLE processing.layer_export_request (
          layer_export_request_id uuid PRIMARY KEY,
          graph_run_id uuid NOT NULL REFERENCES workspace.v022_graph_run,
          stage_no smallint NOT NULL CHECK (stage_no BETWEEN 0 AND 3),
          request_fingerprint varchar(64) NOT NULL UNIQUE CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          status varchar(20) NOT NULL CHECK (status IN ('queued','running','completed','failed','cancelled')),
          output_manifest_id uuid NULL REFERENCES data.payload_manifest,
          requested_by varchar(160) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz NULL,
          CHECK ((status='completed')=(output_manifest_id IS NOT NULL))
        );
        """
    )


def downgrade() -> None:
    for table in (
        "layer_export_request", "graph_run_node_binding", "node_run_cache_entry",
        "node_checkpoint", "node_run_partition", "node_run_output", "node_run_input", "node_run",
    ):
        op.drop_table(table, schema="processing")
