"""Allow composed Defense for qualified explicit risk universes.

Revision ID: 20260814_89_v022_def_cap
Revises: 20260814_88_v022_element_diag
"""

# ruff: noqa: E501

from __future__ import annotations

from alembic import op

revision = "20260814_89_v022_def_cap"
down_revision = "20260814_88_v022_element_diag"
branch_labels = None
depends_on = None

_OLD_DECLARE = """                risk_asset_set_definition_id_value uuid;
                package_artifact_id_value uuid; package_artifact_status_value varchar;"""
_NEW_DECLARE = """                risk_asset_set_definition_id_value uuid;
                risk_selection_kind_value varchar; risk_selection_group_value varchar;
                package_artifact_id_value uuid; package_artifact_status_value varchar;"""

_OLD_SELECT = """                 context.asset_registry_artifact_id,
                 context.asset_set_definition_id
            INTO risk_fingerprint_value,risk_artifact_id_value,risk_artifact_status_value,
                 risk_registry_release_id_value,risk_registry_artifact_id_value,
                 risk_asset_set_definition_id_value"""
_NEW_SELECT = """                 context.asset_registry_artifact_id,
                 context.asset_set_definition_id,
                 context.asset_context_document->>'selection_kind',
                 context.asset_context_document->>'selection_group'
            INTO risk_fingerprint_value,risk_artifact_id_value,risk_artifact_status_value,
                 risk_registry_release_id_value,risk_registry_artifact_id_value,
                 risk_asset_set_definition_id_value,risk_selection_kind_value,
                 risk_selection_group_value"""

_OLD_GUARD = """          IF NOT EXISTS (
               SELECT 1
                 FROM defense.v022_defense_package_supported_asset_set supported
                WHERE supported.defense_version_id=NEW.defense_version_id
                  AND supported.asset_registry_release_id=
                      risk_registry_release_id_value
                  AND supported.asset_registry_artifact_id=
                      risk_registry_artifact_id_value
                  AND supported.asset_set_definition_id=
                      risk_asset_set_definition_id_value
             ) THEN
            RAISE EXCEPTION 'Compiled Defense Execution Context risk Asset Context is not supported by its Package';
          END IF;"""
_NEW_GUARD = """          IF risk_selection_kind_value='explicit_security_selection' THEN
            IF risk_selection_group_value NOT IN ('stock','fund') OR
               binding.asset_registry_release_id IS DISTINCT FROM
                 risk_registry_release_id_value OR
               binding.asset_registry_artifact_id IS DISTINCT FROM
                 risk_registry_artifact_id_value THEN
              RAISE EXCEPTION 'Compiled Defense Execution Context explicit risk universe is incompatible';
            END IF;
          ELSIF NOT EXISTS (
               SELECT 1
                 FROM defense.v022_defense_package_supported_asset_set supported
                WHERE supported.defense_version_id=NEW.defense_version_id
                  AND supported.asset_registry_release_id=
                      risk_registry_release_id_value
                  AND supported.asset_registry_artifact_id=
                      risk_registry_artifact_id_value
                  AND supported.asset_set_definition_id=
                      risk_asset_set_definition_id_value
             ) THEN
            RAISE EXCEPTION 'Compiled Defense Execution Context risk Asset Context is not supported by its Package';
          END IF;"""


def _replace(source: str, target: str) -> None:
    op.execute(
        f"""
        DO $migration$
        DECLARE definition text;
        BEGIN
          SELECT pg_get_functiondef(
                   'defense.validate_v022_compiled_defense_execution_context()'
                   ::regprocedure
                 ) INTO definition;
          IF position($source${source}$source$ IN definition)=0 THEN
            RAISE EXCEPTION 'Expected Defense Context validator fragment is absent';
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
    _replace(_OLD_DECLARE, _NEW_DECLARE)
    _replace(_OLD_SELECT, _NEW_SELECT)
    _replace(_OLD_GUARD, _NEW_GUARD)


def downgrade() -> None:
    _replace(_NEW_GUARD, _OLD_GUARD)
    _replace(_NEW_SELECT, _OLD_SELECT)
    _replace(_NEW_DECLARE, _OLD_DECLARE)
