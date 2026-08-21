# ruff: noqa: E501
"""Make the v0.22 database fingerprint probe search-path independent.

Revision ID: 20260817_115_v022_restore
Revises: 20260817_114_v022_prod_raw_guard
"""

from __future__ import annotations

from alembic import op

revision = "20260817_115_v022_restore"
down_revision = "20260817_114_v022_prod_raw_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(_portable_fingerprint_function())


def downgrade() -> None:
    op.execute(_legacy_fingerprint_function())


def _portable_fingerprint_function() -> str:
    return """
    CREATE OR REPLACE FUNCTION strategy.v022_strategy_parameter_fingerprint(value jsonb)
    RETURNS varchar(64)
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    SET search_path = pg_catalog, public, strategy
    AS $$
      SELECT pg_catalog.encode(
        public.digest(
          pg_catalog.convert_to(
            '{"$canonical":"canonical-json-v2","$value":' ||
            strategy.v022_canonical_jsonb(value) || '}',
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      )::varchar(64)
    $$;
    """


def _legacy_fingerprint_function() -> str:
    return """
    CREATE OR REPLACE FUNCTION strategy.v022_strategy_parameter_fingerprint(value jsonb)
    RETURNS varchar(64)
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
      SELECT encode(
        digest(
          convert_to(
            '{"$canonical":"canonical-json-v2","$value":' ||
            strategy.v022_canonical_jsonb(value) || '}',
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      )::varchar(64)
    $$;
    """
