"""Freeze engine metadata used by formal factor publications.

Revision ID: 20260803_08_v02_factor_engine
Revises: 20260803_07_v02_factor
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260803_08_v02_factor_engine"
down_revision: str | None = "20260803_07_v02_factor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION ops.reject_engine_definition_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'engine definitions are append-only';
        END;
        $$;
        CREATE TRIGGER trg_engine_definition_append_only
        BEFORE UPDATE OR DELETE ON ops.engine_definition
        FOR EACH ROW EXECUTE FUNCTION ops.reject_engine_definition_mutation();

        CREATE TRIGGER trg_engine_version_draft
        BEFORE INSERT OR UPDATE OR DELETE ON ops.engine_version
        FOR EACH ROW EXECUTE FUNCTION data.enforce_artifact_owned_draft();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_engine_version_draft ON ops.engine_version")
    op.execute("DROP FUNCTION IF EXISTS ops.reject_engine_definition_mutation() CASCADE")
