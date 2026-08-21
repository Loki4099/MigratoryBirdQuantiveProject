"""Add immutable explicit Asset selections and revision-scoped contexts.

Revision ID: 20260813_87_v022_asset_sel
Revises: 20260813_86_v021_shadow_replay
"""

from __future__ import annotations

from alembic import op

revision = "20260813_87_v022_asset_sel"
down_revision = "20260813_86_v021_shadow_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.v022_explicit_asset_selection (
          explicit_asset_selection_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          contract_version varchar(40) NOT NULL
            CHECK (contract_version='v0.22.0'),
          asset_registry_release_id uuid NOT NULL
            REFERENCES catalog.asset_registry_release,
          asset_registry_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          selection_group varchar(40) NOT NULL
            CHECK (selection_group IN ('stock','fund')),
          member_count integer NOT NULL CHECK (member_count >= 2),
          selection_document jsonb NOT NULL,
          selection_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (selection_fingerprint ~ '^[0-9a-f]{64}$'),
          created_by varchar(160) NOT NULL CHECK (btrim(created_by)<>''),
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (jsonb_typeof(selection_document)='object')
        );
        CREATE TABLE workspace.v022_explicit_asset_selection_member (
          explicit_asset_selection_id uuid NOT NULL
            REFERENCES workspace.v022_explicit_asset_selection,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          security_id uuid NOT NULL REFERENCES catalog.security,
          security_key varchar(240) NOT NULL CHECK (btrim(security_key)<>''),
          instrument_type varchar(160) NOT NULL CHECK (btrim(instrument_type)<>''),
          member_role varchar(40) NOT NULL CHECK (member_role='candidate'),
          PRIMARY KEY (explicit_asset_selection_id,ordinal),
          UNIQUE (explicit_asset_selection_id,security_id),
          UNIQUE (explicit_asset_selection_id,security_key)
        );

        CREATE FUNCTION workspace.validate_v022_explicit_asset_selection()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_type_value varchar;
                artifact_key_value varchar;
                artifact_version_value integer;
                registry_artifact_value uuid;
                registry_status_value varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number
            INTO artifact_type_value,artifact_key_value,artifact_version_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT release.artifact_id,artifact.status
            INTO registry_artifact_value,registry_status_value
            FROM catalog.asset_registry_release release
            JOIN lineage.artifact artifact ON artifact.artifact_id=release.artifact_id
           WHERE release.asset_registry_release_id=NEW.asset_registry_release_id;
          IF artifact_type_value IS DISTINCT FROM 'v022_explicit_asset_selection' OR
             artifact_version_value IS DISTINCT FROM 1 OR
             artifact_key_value IS DISTINCT FROM
               'explicit_asset_selection__' || NEW.selection_fingerprint OR
             registry_artifact_value IS DISTINCT FROM NEW.asset_registry_artifact_id OR
             registry_status_value IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Explicit Asset Selection requires exact Artifact identities';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_explicit_asset_selection_validate
          BEFORE INSERT ON workspace.v022_explicit_asset_selection
          FOR EACH ROW EXECUTE FUNCTION workspace.validate_v022_explicit_asset_selection();

        CREATE FUNCTION workspace.validate_v022_explicit_asset_selection_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_status_value varchar;
                artifact_fingerprint_value varchar;
                actual_count integer;
                actual_document jsonb;
        BEGIN
          SELECT status,semantic_fingerprint
            INTO artifact_status_value,artifact_fingerprint_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT count(*),jsonb_build_object(
                   'contract_version','v0.22.0',
                   'selection_kind','explicit_security_selection',
                   'asset_context_key','explicit_' || left(NEW.selection_fingerprint,16),
                   'asset_registry_release_id',NEW.asset_registry_release_id::text,
                   'asset_registry_artifact_id',NEW.asset_registry_artifact_id::text,
                   'asset_registry_catalog_version',
                     NEW.selection_document->>'asset_registry_catalog_version',
                   'explicit_asset_selection_id',
                     NEW.explicit_asset_selection_id::text,
                   'explicit_asset_selection_artifact_id',NEW.artifact_id::text,
                   'selection_group',NEW.selection_group,
                   'members',coalesce(jsonb_agg(jsonb_build_object(
                     'ordinal',member.ordinal,
                     'security_id',member.security_id::text,
                     'security_key',member.security_key,
                     'instrument_type',member.instrument_type
                   ) ORDER BY member.ordinal),'[]'::jsonb)
                 )
            INTO actual_count,actual_document
            FROM workspace.v022_explicit_asset_selection_member member
           WHERE member.explicit_asset_selection_id=NEW.explicit_asset_selection_id;
          IF artifact_status_value IS DISTINCT FROM 'published' OR
             artifact_fingerprint_value IS NULL OR
             actual_count<>NEW.member_count OR
             actual_document IS DISTINCT FROM NEW.selection_document OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=NEW.asset_registry_artifact_id
                  AND dependency.role='asset_registry_release'
                  AND dependency.ordinal=0
             ) OR (
               SELECT count(*) FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
             )<>1 THEN
            RAISE EXCEPTION 'Explicit Asset Selection projection is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_explicit_asset_selection_complete
          AFTER INSERT ON workspace.v022_explicit_asset_selection
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION workspace.validate_v022_explicit_asset_selection_complete();

        CREATE TRIGGER trg_v022_explicit_asset_selection_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_explicit_asset_selection
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();
        CREATE TRIGGER trg_v022_explicit_asset_selection_member_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_explicit_asset_selection_member
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();

        DROP TRIGGER IF EXISTS trg_v022_graph_draft_revision_append_only
          ON workspace.v022_graph_draft_revision;
        ALTER TABLE workspace.v022_graph_draft_revision
          ADD COLUMN asset_context_fingerprint varchar(64) NULL,
          ADD COLUMN resolved_data_binding_fingerprint varchar(64) NULL,
          ADD COLUMN asset_context_document jsonb NULL,
          ADD COLUMN resolved_data_binding_document jsonb NULL;
        UPDATE workspace.v022_graph_draft_revision revision
           SET asset_context_fingerprint=draft.asset_context_fingerprint,
               resolved_data_binding_fingerprint=draft.resolved_data_binding_fingerprint,
               asset_context_document=draft.asset_context_document,
               resolved_data_binding_document=draft.resolved_data_binding_document
          FROM workspace.v022_graph_draft draft
         WHERE draft.graph_draft_id=revision.graph_draft_id;
        ALTER TABLE workspace.v022_graph_draft_revision
          ALTER COLUMN asset_context_fingerprint SET NOT NULL,
          ALTER COLUMN resolved_data_binding_fingerprint SET NOT NULL,
          ALTER COLUMN asset_context_document SET NOT NULL,
          ALTER COLUMN resolved_data_binding_document SET NOT NULL,
          ADD CONSTRAINT ck_v022_graph_revision_asset_fp
            CHECK (asset_context_fingerprint ~ '^[0-9a-f]{64}$'),
          ADD CONSTRAINT ck_v022_graph_revision_binding_fp
            CHECK (resolved_data_binding_fingerprint ~ '^[0-9a-f]{64}$'),
          ADD CONSTRAINT ck_v022_graph_revision_asset_document
            CHECK (jsonb_typeof(asset_context_document)='object'),
          ADD CONSTRAINT ck_v022_graph_revision_binding_document
            CHECK (jsonb_typeof(resolved_data_binding_document)='object');
        CREATE TRIGGER trg_v022_graph_draft_revision_append_only
          BEFORE UPDATE OR DELETE ON workspace.v022_graph_draft_revision
          FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation();

        ALTER TABLE workspace.v022_compiled_execution_data_context
          ALTER COLUMN asset_set_definition_id DROP NOT NULL,
          ADD COLUMN explicit_asset_selection_id uuid NULL
            REFERENCES workspace.v022_explicit_asset_selection,
          ADD COLUMN explicit_asset_selection_artifact_id uuid NULL
            REFERENCES lineage.artifact,
          ADD CONSTRAINT ck_v022_execution_context_asset_source CHECK (
            (asset_set_definition_id IS NOT NULL AND
             explicit_asset_selection_id IS NULL AND
             explicit_asset_selection_artifact_id IS NULL) OR
            (asset_set_definition_id IS NULL AND
             explicit_asset_selection_id IS NOT NULL AND
             explicit_asset_selection_artifact_id IS NOT NULL)
          );
        """
    )
    _extend_expected_asset_document()
    _patch_execution_context_guards(forward=True)


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM workspace.v022_compiled_execution_data_context
             WHERE explicit_asset_selection_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'Cannot downgrade with explicit Asset Execution Contexts';
          END IF;
        END $$;
        """
    )
    _patch_execution_context_guards(forward=False)
    _restore_expected_asset_document()
    op.execute(
        """
        ALTER TABLE workspace.v022_compiled_execution_data_context
          DROP CONSTRAINT IF EXISTS ck_v022_execution_context_asset_source,
          DROP COLUMN IF EXISTS explicit_asset_selection_artifact_id,
          DROP COLUMN IF EXISTS explicit_asset_selection_id,
          ALTER COLUMN asset_set_definition_id SET NOT NULL;
        ALTER TABLE workspace.v022_graph_draft_revision
          DROP CONSTRAINT IF EXISTS ck_v022_graph_revision_binding_document,
          DROP CONSTRAINT IF EXISTS ck_v022_graph_revision_asset_document,
          DROP CONSTRAINT IF EXISTS ck_v022_graph_revision_binding_fp,
          DROP CONSTRAINT IF EXISTS ck_v022_graph_revision_asset_fp,
          DROP COLUMN IF EXISTS resolved_data_binding_document,
          DROP COLUMN IF EXISTS asset_context_document,
          DROP COLUMN IF EXISTS resolved_data_binding_fingerprint,
          DROP COLUMN IF EXISTS asset_context_fingerprint;
        DROP FUNCTION IF EXISTS
          workspace.validate_v022_explicit_asset_selection_complete() CASCADE;
        DROP FUNCTION IF EXISTS
          workspace.validate_v022_explicit_asset_selection() CASCADE;
        DROP TABLE IF EXISTS workspace.v022_explicit_asset_selection_member;
        DROP TABLE IF EXISTS workspace.v022_explicit_asset_selection;
        """
    )


def _replace_function_fragment(function: str, source: str, target: str) -> None:
    op.execute(
        f"""
        DO $migration$
        DECLARE definition text;
        BEGIN
          SELECT pg_get_functiondef('{function}'::regprocedure) INTO definition;
          IF position($source${source}$source$ IN definition)=0 THEN
            RAISE EXCEPTION 'Expected function fragment is absent: {function}';
          END IF;
          definition := replace(
            definition,$source${source}$source$,$target${target}$target$
          );
          EXECUTE definition;
        END
        $migration$;
        """
    )


def _extend_expected_asset_document() -> None:
    _replace_function_fragment(
        "workspace.v022_expected_asset_context_document(uuid,jsonb)",
        """          END IF;
          RETURN result;""",
        """          ELSIF claimed->>'selection_kind'='explicit_security_selection' THEN
            SELECT selection.selection_document INTO result
              FROM workspace.v022_explicit_asset_selection selection
              JOIN lineage.artifact artifact ON artifact.artifact_id=selection.artifact_id
               AND artifact.status='published'
             WHERE selection.explicit_asset_selection_id=
                   (claimed->>'explicit_asset_selection_id')::uuid
               AND selection.artifact_id=
                   (claimed->>'explicit_asset_selection_artifact_id')::uuid
               AND selection.asset_registry_release_id=
                   (claimed->>'asset_registry_release_id')::uuid
               AND selection.asset_registry_artifact_id=
                   (claimed->>'asset_registry_artifact_id')::uuid;
          END IF;
          RETURN result;""",
    )


def _restore_expected_asset_document() -> None:
    _replace_function_fragment(
        "workspace.v022_expected_asset_context_document(uuid,jsonb)",
        """          ELSIF claimed->>'selection_kind'='explicit_security_selection' THEN
            SELECT selection.selection_document INTO result
              FROM workspace.v022_explicit_asset_selection selection
              JOIN lineage.artifact artifact ON artifact.artifact_id=selection.artifact_id
               AND artifact.status='published'
             WHERE selection.explicit_asset_selection_id=
                   (claimed->>'explicit_asset_selection_id')::uuid
               AND selection.artifact_id=
                   (claimed->>'explicit_asset_selection_artifact_id')::uuid
               AND selection.asset_registry_release_id=
                   (claimed->>'asset_registry_release_id')::uuid
               AND selection.asset_registry_artifact_id=
                   (claimed->>'asset_registry_artifact_id')::uuid;
          END IF;
          RETURN result;""",
        """          END IF;
          RETURN result;""",
    )


_ASSET_SET_LOOKUP = """          SELECT definition.asset_registry_release_id,definition.set_type
            INTO asset_set_release_id_value,asset_set_type_value
            FROM catalog.asset_set_definition definition
           WHERE definition.asset_set_definition_id=NEW.asset_set_definition_id;"""

_ASSET_SOURCE_LOOKUP = """          IF NEW.explicit_asset_selection_id IS NULL THEN
            SELECT definition.asset_registry_release_id,definition.set_type
              INTO asset_set_release_id_value,asset_set_type_value
              FROM catalog.asset_set_definition definition
             WHERE definition.asset_set_definition_id=NEW.asset_set_definition_id;
          ELSE
            SELECT selection.asset_registry_release_id,'explicit_security_selection'
              INTO asset_set_release_id_value,asset_set_type_value
              FROM workspace.v022_explicit_asset_selection selection
              JOIN lineage.artifact artifact ON artifact.artifact_id=selection.artifact_id
               AND artifact.status='published'
             WHERE selection.explicit_asset_selection_id=
                   NEW.explicit_asset_selection_id
               AND selection.artifact_id=
                   NEW.explicit_asset_selection_artifact_id;
          END IF;"""

_OLD_EXPECTED_CALL = """          SELECT workspace.v022_expected_asset_context_document(
                   NEW.asset_set_definition_id,
                   NEW.asset_context_document
                 )
            INTO expected_asset_document;"""

_NEW_EXPECTED_CALL = """          SELECT workspace.v022_expected_asset_context_document(
                   coalesce(NEW.asset_set_definition_id,
                            NEW.explicit_asset_selection_id),
                   NEW.asset_context_document
                 )
            INTO expected_asset_document;"""

_OLD_SOURCE_TYPE = "asset_set_type_value NOT IN ('fixed','dynamic_methodology') THEN"
_NEW_SOURCE_TYPE = (
    "asset_set_type_value NOT IN "
    "('fixed','dynamic_methodology','explicit_security_selection') THEN"
)

_OLD_DEPENDENCY_COUNT = """          SELECT 2 + count(DISTINCT input.dataset_artifact_id) +
                     count(DISTINCT input.calendar_artifact_id)
            INTO expected_dependency_count"""

_NEW_DEPENDENCY_COUNT = """          SELECT 2 + CASE WHEN NEW.explicit_asset_selection_id IS NULL
                            THEN 0 ELSE 1 END +
                     count(DISTINCT input.dataset_artifact_id) +
                     count(DISTINCT input.calendar_artifact_id)
            INTO expected_dependency_count"""

_OLD_REGISTRY_DEPENDENCY = """             ) OR EXISTS (
               SELECT 1
                 FROM (
                   SELECT input.dataset_artifact_id,"""

_NEW_REGISTRY_DEPENDENCY = """             ) OR (
               NEW.explicit_asset_selection_id IS NOT NULL AND NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=NEW.artifact_id
                    AND dependency.depends_on_artifact_id=
                        NEW.explicit_asset_selection_artifact_id
                    AND dependency.role='asset_selection'
                    AND dependency.ordinal=0
               )
             ) OR EXISTS (
               SELECT 1
                 FROM (
                   SELECT input.dataset_artifact_id,"""


def _patch_execution_context_guards(*, forward: bool) -> None:
    pairs = (
        (_ASSET_SET_LOOKUP, _ASSET_SOURCE_LOOKUP),
        (_OLD_EXPECTED_CALL, _NEW_EXPECTED_CALL),
        (_OLD_SOURCE_TYPE, _NEW_SOURCE_TYPE),
    )
    for old, new in pairs:
        _replace_function_fragment(
            "workspace.validate_v022_compiled_execution_data_context()",
            old if forward else new,
            new if forward else old,
        )
    completeness_pairs = (
        (_OLD_DEPENDENCY_COUNT, _NEW_DEPENDENCY_COUNT),
        (_OLD_REGISTRY_DEPENDENCY, _NEW_REGISTRY_DEPENDENCY),
    )
    for old, new in completeness_pairs:
        _replace_function_fragment(
            "workspace.validate_v022_compiled_execution_data_context_complete()",
            old if forward else new,
            new if forward else old,
        )
