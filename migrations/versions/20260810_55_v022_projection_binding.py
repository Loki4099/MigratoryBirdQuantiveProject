# ruff: noqa: E501
"""Make fixed Catalog bindings projection-aware for M3 representative chains.

Revision ID: 20260810_55_v022_projection
Revises: 20260810_54_v022_deterministic
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260810_55_v022_projection"
down_revision: str | None = "20260810_54_v022_deterministic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _replace_guard("source_stage >= node_stage", "processing source must originate in an earlier stage")


def downgrade() -> None:
    _replace_guard("source_stage <> node_stage - 1", "processing edges must connect adjacent stages")


def _replace_guard(stage_predicate: str, message: str) -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION processing.validate_node_input_binding() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            node_stage smallint;
            source_stage smallint;
            port_owner uuid;
            port_direction text;
            reader_contract uuid;
            writer_contract uuid;
        BEGIN
            SELECT stage_no INTO node_stage FROM processing.node_version
             WHERE node_version_id = NEW.node_version_id;
            SELECT origin_stage, payload_contract_version_id
              INTO source_stage, writer_contract FROM processing.feature_version
             WHERE feature_version_id = NEW.source_feature_version_id;
            SELECT node_version_id, direction, payload_contract_version_id
              INTO port_owner, port_direction, reader_contract FROM processing.node_port
             WHERE node_port_id = NEW.input_port_id;
            IF port_owner <> NEW.node_version_id OR port_direction <> 'input' THEN
                RAISE EXCEPTION 'input binding port must belong to the target node version';
            END IF;
            IF {stage_predicate} THEN
                RAISE EXCEPTION '{message}';
            END IF;
            IF reader_contract <> writer_contract AND NOT EXISTS (
                SELECT 1 FROM data.payload_contract_compatibility c
                 WHERE c.reader_contract_version_id = reader_contract
                   AND c.writer_contract_version_id = writer_contract
                   AND c.compatibility_result = 'compatible'
            ) THEN
                RAISE EXCEPTION 'payload contract incompatible for processing edge';
            END IF;
            RETURN NEW;
        END $$;
        """
    )
