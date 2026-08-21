"""Allow exact Dynamic Universe snapshots as v0.22 Asset Contexts.

Revision ID: 20260813_84_v022_dynamic_ctx
Revises: 20260813_83_v022_def_guard
"""

from __future__ import annotations

from alembic import op

revision = "20260813_84_v022_dynamic_ctx"
down_revision = "20260813_83_v022_def_guard"
branch_labels = None
depends_on = None

_FIXED_DOCUMENT = """          SELECT jsonb_build_object(
                   'contract_version','v0.22.0',
                   'selection_kind','fixed_asset_set',
                   'asset_context_key',definition.set_key,
                   'asset_registry_release_id',release.asset_registry_release_id::text,
                   'asset_registry_artifact_id',release.artifact_id::text,
                   'asset_registry_catalog_version',release.catalog_version,
                   'asset_set_definition_id',definition.asset_set_definition_id::text,
                   'members',coalesce(members.document,'[]'::jsonb)
                 )
            INTO expected_asset_document
            FROM catalog.asset_set_definition definition
            JOIN catalog.asset_registry_release release
              ON release.asset_registry_release_id=definition.asset_registry_release_id
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(
                       jsonb_build_object(
                         'ordinal',member.ordinal,
                         'security_id',security.security_id::text,
                         'security_key',security.security_key,
                         'instrument_type',profile.instrument_type
                       ) ORDER BY member.ordinal
                     ) AS document
                FROM catalog.asset_set_member member
                JOIN catalog.security security
                  ON security.security_id=member.security_id
                JOIN catalog.security_profile profile
                  ON profile.asset_registry_release_id=
                     definition.asset_registry_release_id
                 AND profile.security_id=member.security_id
               WHERE member.asset_set_definition_id=
                     definition.asset_set_definition_id
            ) members ON true
           WHERE definition.asset_set_definition_id=NEW.asset_set_definition_id;"""

_DYNAMIC_DOCUMENT = """          SELECT workspace.v022_expected_asset_context_document(
                   NEW.asset_set_definition_id,
                   NEW.asset_context_document
                 )
            INTO expected_asset_document;"""

_FIXED_TYPE = """             asset_set_type_value IS DISTINCT FROM 'fixed' THEN"""
_SUPPORTED_TYPE = (
    "             asset_set_type_value NOT IN ('fixed','dynamic_methodology') THEN"
)


def _replace(source: str, target: str) -> None:
    op.execute(
        f"""
        DO $migration$
        DECLARE definition text;
        BEGIN
          SELECT pg_get_functiondef(
                   'workspace.validate_v022_compiled_execution_data_context()'
                   ::regprocedure
                 ) INTO definition;
          IF position($source${source}$source$ IN definition)=0 THEN
            RAISE EXCEPTION 'Expected Execution Context validator fragment is absent';
          END IF;
          definition := replace(
            definition,
            $source${source}$source$,
            $target${target}$target$
          );
          EXECUTE definition;
        END
        $migration$;
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION workspace.v022_expected_asset_context_document(
          asset_set_id uuid, claimed jsonb
        ) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
        DECLARE result jsonb;
        BEGIN
          IF claimed->>'selection_kind'='fixed_asset_set' THEN
            SELECT jsonb_build_object(
                     'contract_version','v0.22.0',
                     'selection_kind','fixed_asset_set',
                     'asset_context_key',definition.set_key,
                     'asset_registry_release_id',release.asset_registry_release_id::text,
                     'asset_registry_artifact_id',release.artifact_id::text,
                     'asset_registry_catalog_version',release.catalog_version,
                     'asset_set_definition_id',definition.asset_set_definition_id::text,
                     'members',coalesce(members.document,'[]'::jsonb)
                   ) INTO result
              FROM catalog.asset_set_definition definition
              JOIN catalog.asset_registry_release release
                ON release.asset_registry_release_id=definition.asset_registry_release_id
              LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                         jsonb_build_object(
                           'ordinal',member.ordinal,
                           'security_id',security.security_id::text,
                           'security_key',security.security_key,
                           'instrument_type',profile.instrument_type
                         ) ORDER BY member.ordinal
                       ) AS document
                  FROM catalog.asset_set_member member
                  JOIN catalog.security security
                    ON security.security_id=member.security_id
                  JOIN catalog.security_profile profile
                    ON profile.asset_registry_release_id=
                       definition.asset_registry_release_id
                   AND profile.security_id=member.security_id
                 WHERE member.asset_set_definition_id=
                       definition.asset_set_definition_id
              ) members ON true
             WHERE definition.asset_set_definition_id=asset_set_id
               AND definition.set_type='fixed';
          ELSIF claimed->>'selection_kind'='dynamic_universe_snapshot' THEN
            SELECT jsonb_build_object(
                     'contract_version','v0.22.0',
                     'selection_kind','dynamic_universe_snapshot',
                     'asset_context_key',definition.set_key,
                     'asset_registry_release_id',release.asset_registry_release_id::text,
                     'asset_registry_artifact_id',release.artifact_id::text,
                     'asset_registry_catalog_version',release.catalog_version,
                     'asset_set_definition_id',definition.asset_set_definition_id::text,
                     'universe_methodology_id',methodology.universe_methodology_id::text,
                     'universe_methodology_artifact_id',methodology.artifact_id::text,
                     'universe_history_id',history.universe_history_id::text,
                     'universe_history_artifact_id',history.artifact_id::text,
                     'universe_snapshot_id',snapshot.universe_snapshot_id::text,
                     'universe_effective_session',snapshot.effective_session::text,
                     'members',coalesce(members.document,'[]'::jsonb)
                   ) INTO result
              FROM catalog.asset_set_definition definition
              JOIN catalog.asset_registry_release release
                ON release.asset_registry_release_id=definition.asset_registry_release_id
              JOIN catalog.universe_methodology methodology
                ON methodology.methodology_key=definition.set_key
               AND methodology.universe_methodology_id=
                   (claimed->>'universe_methodology_id')::uuid
               AND methodology.artifact_id=
                   (claimed->>'universe_methodology_artifact_id')::uuid
              JOIN lineage.artifact methodology_artifact
                ON methodology_artifact.artifact_id=methodology.artifact_id
               AND methodology_artifact.status='published'
              JOIN catalog.universe_history history
                ON history.universe_methodology_id=methodology.universe_methodology_id
               AND history.universe_history_id=
                   (claimed->>'universe_history_id')::uuid
               AND history.artifact_id=
                   (claimed->>'universe_history_artifact_id')::uuid
              JOIN lineage.artifact history_artifact
                ON history_artifact.artifact_id=history.artifact_id
               AND history_artifact.status='published'
              JOIN catalog.universe_snapshot snapshot
                ON snapshot.universe_history_id=history.universe_history_id
               AND snapshot.universe_snapshot_id=
                   (claimed->>'universe_snapshot_id')::uuid
              LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                         jsonb_build_object(
                           'ordinal',member.ordinal,
                           'security_id',security.security_id::text,
                           'security_key',security.security_key,
                           'instrument_type',profile.instrument_type
                         ) ORDER BY member.ordinal
                       ) AS document
                  FROM catalog.universe_snapshot_member member
                  JOIN catalog.security security
                    ON security.security_id=member.security_id
                  JOIN catalog.security_profile profile
                    ON profile.asset_registry_release_id=
                       definition.asset_registry_release_id
                   AND profile.security_id=member.security_id
                 WHERE member.universe_snapshot_id=snapshot.universe_snapshot_id
              ) members ON true
             WHERE definition.asset_set_definition_id=asset_set_id
               AND definition.set_type='dynamic_methodology';
          END IF;
          RETURN result;
        END $$;
        """
    )
    _replace(_FIXED_DOCUMENT, _DYNAMIC_DOCUMENT)
    _replace(_FIXED_TYPE, _SUPPORTED_TYPE)


def downgrade() -> None:
    _replace(_SUPPORTED_TYPE, _FIXED_TYPE)
    _replace(_DYNAMIC_DOCUMENT, _FIXED_DOCUMENT)
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "workspace.v022_expected_asset_context_document(uuid,jsonb)"
    )
