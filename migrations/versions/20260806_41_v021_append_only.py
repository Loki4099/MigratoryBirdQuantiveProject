"""Enforce append-only audit records in PostgreSQL.

Revision ID: 20260806_41_v021_append_only
Revises: 20260806_40_v021_idempotency
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260806_41_v021_append_only"
down_revision: str | None = "20260806_40_v021_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = (
    "monitoring_snapshot",
    "product_alert_event",
)


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION product.reject_audit_record_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP
                USING ERRCODE = '55000';
        END;
        $$
    """)
    for table in _TABLES:
        op.execute(f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON product.{table}
            FOR EACH ROW EXECUTE FUNCTION product.reject_audit_record_mutation()
        """)
    op.execute("""
        CREATE OR REPLACE FUNCTION product.allow_lifecycle_event_application()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.applied_at IS NULL AND NEW.applied_at IS NOT NULL
               AND (to_jsonb(NEW) - 'applied_at') = (to_jsonb(OLD) - 'applied_at') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP
                USING ERRCODE = '55000';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER trg_product_lifecycle_event_append_only
        BEFORE UPDATE OR DELETE ON product.product_lifecycle_event
        FOR EACH ROW EXECUTE FUNCTION product.allow_lifecycle_event_application()
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_product_lifecycle_event_append_only "
        "ON product.product_lifecycle_event"
    )
    op.execute("DROP FUNCTION IF EXISTS product.allow_lifecycle_event_application()")
    for table in reversed(_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON product.{table}")
    op.execute("DROP FUNCTION IF EXISTS product.reject_audit_record_mutation()")
