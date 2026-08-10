"""Externalize new v0.21 Cell result payloads.

Revision ID: 20260809_46_v021_cell_payload
Revises: 20260808_45_v021_candidate
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_46_v021_cell_payload"
down_revision: str | None = "20260808_45_v021_candidate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HASH_PATTERN = "^[0-9a-f]{64}$"


def upgrade() -> None:
    op.add_column(
        "cell_result",
        sa.Column("payload_storage_uri", sa.Text(), nullable=True),
        schema="experiment",
    )
    op.add_column(
        "cell_result",
        sa.Column("payload_content_hash", sa.String(64), nullable=True),
        schema="experiment",
    )
    op.add_column(
        "cell_result",
        sa.Column("payload_storage_format", sa.String(32), nullable=True),
        schema="experiment",
    )
    op.add_column(
        "cell_result",
        sa.Column("payload_schema_version", sa.String(40), nullable=True),
        schema="experiment",
    )
    op.add_column(
        "cell_result",
        sa.Column("payload_byte_size", sa.BigInteger(), nullable=True),
        schema="experiment",
    )
    op.create_check_constraint(
        "ck_cell_result_external_payload_complete",
        "cell_result",
        "(payload_storage_uri IS NULL AND payload_content_hash IS NULL "
        "AND payload_storage_format IS NULL AND payload_schema_version IS NULL "
        "AND payload_byte_size IS NULL) OR "
        "(payload_storage_uri IS NOT NULL AND payload_content_hash IS NOT NULL "
        "AND payload_storage_format IS NOT NULL AND payload_schema_version IS NOT NULL "
        "AND payload_byte_size IS NOT NULL)",
        schema="experiment",
    )
    op.create_check_constraint(
        "ck_cell_result_external_payload_hash",
        "cell_result",
        f"payload_content_hash IS NULL OR payload_content_hash ~ '{HASH_PATTERN}'",
        schema="experiment",
    )
    op.create_check_constraint(
        "ck_cell_result_external_payload_uri",
        "cell_result",
        "payload_storage_uri IS NULL OR payload_storage_uri = "
        "'cell-result://sha256/' || payload_content_hash || '.parquet'",
        schema="experiment",
    )
    op.create_check_constraint(
        "ck_cell_result_external_payload_size",
        "cell_result",
        "payload_byte_size IS NULL OR payload_byte_size > 0",
        schema="experiment",
    )
    op.create_check_constraint(
        "ck_cell_result_external_payload_format",
        "cell_result",
        "payload_storage_format IS NULL OR payload_storage_format = 'parquet_zstd_json_v1'",
        schema="experiment",
    )
    op.create_index(
        "ix_cell_result_payload_content_hash",
        "cell_result",
        ["payload_content_hash"],
        unique=False,
        schema="experiment",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cell_result_payload_content_hash",
        table_name="cell_result",
        schema="experiment",
    )
    op.drop_constraint(
        "ck_cell_result_external_payload_format",
        "cell_result",
        schema="experiment",
        type_="check",
    )
    op.drop_constraint(
        "ck_cell_result_external_payload_size",
        "cell_result",
        schema="experiment",
        type_="check",
    )
    op.drop_constraint(
        "ck_cell_result_external_payload_uri",
        "cell_result",
        schema="experiment",
        type_="check",
    )
    op.drop_constraint(
        "ck_cell_result_external_payload_hash",
        "cell_result",
        schema="experiment",
        type_="check",
    )
    op.drop_constraint(
        "ck_cell_result_external_payload_complete",
        "cell_result",
        schema="experiment",
        type_="check",
    )
    op.drop_column("cell_result", "payload_byte_size", schema="experiment")
    op.drop_column("cell_result", "payload_schema_version", schema="experiment")
    op.drop_column("cell_result", "payload_storage_format", schema="experiment")
    op.drop_column("cell_result", "payload_content_hash", schema="experiment")
    op.drop_column("cell_result", "payload_storage_uri", schema="experiment")
