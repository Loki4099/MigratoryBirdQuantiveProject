"""Add immutable publication, acyclic lineage, manifests, and invalidation.

Revision ID: 20260802_02_v02_lineage
Revises: 20260802_01_v02_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_02_v02_lineage"
down_revision: str | None = "20260802_01_v02_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HASH_PATTERN = "^[0-9a-f]{64}$"
STATUSES = "'draft', 'published', 'retired', 'superseded', 'invalidated', 'tainted'"


def upgrade() -> None:
    op.drop_constraint("ck_artifact_status_allowed", "artifact", schema="lineage", type_="check")
    op.create_check_constraint(
        "ck_artifact_status_allowed", "artifact", f"status IN ({STATUSES})", schema="lineage"
    )
    op.drop_constraint(
        "ck_artifact_status_event_from_status_allowed",
        "artifact_status_event",
        schema="lineage",
        type_="check",
    )
    op.drop_constraint(
        "ck_artifact_status_event_to_status_allowed",
        "artifact_status_event",
        schema="lineage",
        type_="check",
    )
    op.create_check_constraint(
        "ck_artifact_status_event_from_status_allowed",
        "artifact_status_event",
        f"from_status IS NULL OR from_status IN ({STATUSES})",
        schema="lineage",
    )
    op.create_check_constraint(
        "ck_artifact_status_event_to_status_allowed",
        "artifact_status_event",
        f"to_status IN ({STATUSES})",
        schema="lineage",
    )

    op.create_table(
        "lineage_manifest",
        sa.Column("lineage_manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_content_hash", sa.String(64), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("canonical_version", sa.String(40), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            f"root_content_hash ~ '{HASH_PATTERN}'",
            name="ck_lineage_manifest_root_content_hash_sha256",
        ),
        sa.CheckConstraint(
            f"manifest_hash ~ '{HASH_PATTERN}'", name="ck_lineage_manifest_manifest_hash_sha256"
        ),
        sa.ForeignKeyConstraint(
            ["root_artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_lineage_manifest_root_artifact_id_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("lineage_manifest_id", name="pk_lineage_manifest"),
        sa.UniqueConstraint("root_artifact_id", name="uq_lineage_manifest_root_artifact_id"),
        sa.UniqueConstraint("manifest_hash", name="uq_lineage_manifest_manifest_hash"),
        schema="lineage",
    )
    op.create_table(
        "artifact_invalidation",
        sa.Column("artifact_invalidation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("replacement_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "replacement_artifact_id IS NULL OR replacement_artifact_id <> artifact_id",
            name="ck_artifact_invalidation_replacement_is_different",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_artifact_invalidation_artifact_id_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replacement_artifact_id"],
            ["lineage.artifact.artifact_id"],
            name="fk_artifact_invalidation_replacement_artifact_id_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("artifact_invalidation_id", name="pk_artifact_invalidation"),
        sa.UniqueConstraint("artifact_id", name="uq_artifact_invalidation_artifact_id"),
        schema="lineage",
    )
    _create_artifact_lifecycle_trigger()
    _create_dependency_trigger()
    _create_immutable_record_trigger()


def _create_artifact_lifecycle_trigger() -> None:
    op.execute(
        """
        CREATE FUNCTION lineage.enforce_artifact_lifecycle() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            event_id_text text;
            event_reason text;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'draft' THEN
                    RAISE EXCEPTION 'artifacts must be inserted as draft';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.status <> 'draft' AND (
                NEW.artifact_type IS DISTINCT FROM OLD.artifact_type OR
                NEW.artifact_key IS DISTINCT FROM OLD.artifact_key OR
                NEW.version_number IS DISTINCT FROM OLD.version_number OR
                NEW.semantic_fingerprint IS DISTINCT FROM OLD.semantic_fingerprint OR
                NEW.content_hash IS DISTINCT FROM OLD.content_hash OR
                NEW.published_at IS DISTINCT FROM OLD.published_at OR
                NEW.created_at IS DISTINCT FROM OLD.created_at
            ) THEN
                RAISE EXCEPTION 'published artifact identity and content are immutable';
            END IF;

            IF NEW.status IS DISTINCT FROM OLD.status THEN
                IF NOT (
                    (OLD.status = 'draft' AND NEW.status = 'published') OR
                    (OLD.status = 'published' AND NEW.status IN
                        ('retired', 'superseded', 'invalidated', 'tainted')) OR
                    (OLD.status = 'tainted' AND NEW.status IN
                        ('published', 'retired', 'superseded', 'invalidated')) OR
                    (OLD.status IN ('retired', 'superseded') AND NEW.status = 'invalidated')
                ) THEN
                    RAISE EXCEPTION 'invalid artifact status transition: % -> %',
                        OLD.status, NEW.status;
                END IF;
                IF OLD.status = 'draft' AND (
                    NEW.semantic_fingerprint IS NULL OR NEW.content_hash IS NULL OR
                    NEW.published_at IS NULL
                ) THEN
                    RAISE EXCEPTION 'publication requires fingerprint, content hash, and time';
                END IF;
                event_id_text := current_setting('style_rotation.status_event_id', true);
                event_reason := current_setting('style_rotation.status_reason', true);
                IF event_id_text IS NULL OR event_id_text = '' OR
                   event_reason IS NULL OR event_reason = '' THEN
                    RAISE EXCEPTION 'status transition requires audit event id and reason';
                END IF;
                INSERT INTO lineage.artifact_status_event (
                    artifact_status_event_id, artifact_id, from_status, to_status,
                    reason, occurred_at
                ) VALUES (
                    event_id_text::uuid, NEW.artifact_id, OLD.status, NEW.status,
                    event_reason, now()
                );
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_artifact_lifecycle
        BEFORE INSERT OR UPDATE ON lineage.artifact
        FOR EACH ROW EXECUTE FUNCTION lineage.enforce_artifact_lifecycle();
        """
    )


def _create_dependency_trigger() -> None:
    op.execute(
        """
        CREATE FUNCTION lineage.enforce_artifact_dependency() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            owner_id uuid;
            owner_status text;
            dependency_status text;
        BEGIN
            owner_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.artifact_id ELSE NEW.artifact_id END;
            SELECT status INTO owner_status FROM lineage.artifact WHERE artifact_id = owner_id;
            IF owner_status <> 'draft' THEN
                RAISE EXCEPTION 'dependencies can only change while the owner is draft';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            SELECT status INTO dependency_status
            FROM lineage.artifact WHERE artifact_id = NEW.depends_on_artifact_id;
            IF dependency_status <> 'published' THEN
                RAISE EXCEPTION 'a dependency must be published before it can be referenced';
            END IF;
            IF EXISTS (
                WITH RECURSIVE reachable(artifact_id) AS (
                    SELECT NEW.depends_on_artifact_id
                    UNION
                    SELECT dependency.depends_on_artifact_id
                    FROM lineage.artifact_dependency dependency
                    JOIN reachable ON dependency.artifact_id = reachable.artifact_id
                    WHERE TG_OP <> 'UPDATE'
                       OR dependency.artifact_dependency_id <> OLD.artifact_dependency_id
                )
                SELECT 1 FROM reachable WHERE artifact_id = NEW.artifact_id
            ) THEN
                RAISE EXCEPTION 'artifact dependency would create a cycle';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_artifact_dependency
        BEFORE INSERT OR UPDATE OR DELETE ON lineage.artifact_dependency
        FOR EACH ROW EXECUTE FUNCTION lineage.enforce_artifact_dependency();
        """
    )


def _create_immutable_record_trigger() -> None:
    op.execute(
        """
        CREATE FUNCTION lineage.reject_record_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% records are append-only', TG_TABLE_NAME;
        END;
        $$;
        CREATE TRIGGER trg_lineage_manifest_immutable
        BEFORE UPDATE OR DELETE ON lineage.lineage_manifest
        FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_artifact_invalidation_immutable
        BEFORE UPDATE OR DELETE ON lineage.artifact_invalidation
        FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_artifact_status_event_immutable
        BEFORE UPDATE OR DELETE ON lineage.artifact_status_event
        FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_artifact_status_event_immutable "
        "ON lineage.artifact_status_event"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_artifact_invalidation_immutable "
        "ON lineage.artifact_invalidation"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_lineage_manifest_immutable ON lineage.lineage_manifest")
    op.execute("DROP FUNCTION IF EXISTS lineage.reject_record_mutation()")
    op.execute("DROP TRIGGER IF EXISTS trg_artifact_dependency ON lineage.artifact_dependency")
    op.execute("DROP FUNCTION IF EXISTS lineage.enforce_artifact_dependency()")
    op.execute("DROP TRIGGER IF EXISTS trg_artifact_lifecycle ON lineage.artifact")
    op.execute("DROP FUNCTION IF EXISTS lineage.enforce_artifact_lifecycle()")
    op.drop_table("artifact_invalidation", schema="lineage")
    op.drop_table("lineage_manifest", schema="lineage")
    op.drop_constraint(
        "ck_artifact_status_event_to_status_allowed",
        "artifact_status_event",
        schema="lineage",
        type_="check",
    )
    op.drop_constraint(
        "ck_artifact_status_event_from_status_allowed",
        "artifact_status_event",
        schema="lineage",
        type_="check",
    )
    op.create_check_constraint(
        "ck_artifact_status_event_from_status_allowed",
        "artifact_status_event",
        "from_status IS NULL OR from_status IN "
        "('draft', 'published', 'retired', 'superseded', 'invalidated')",
        schema="lineage",
    )
    op.create_check_constraint(
        "ck_artifact_status_event_to_status_allowed",
        "artifact_status_event",
        "to_status IN ('draft', 'published', 'retired', 'superseded', 'invalidated')",
        schema="lineage",
    )
    op.drop_constraint("ck_artifact_status_allowed", "artifact", schema="lineage", type_="check")
    op.create_check_constraint(
        "ck_artifact_status_allowed",
        "artifact",
        "status IN ('draft', 'published', 'retired', 'superseded', 'invalidated')",
        schema="lineage",
    )
