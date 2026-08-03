from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import RowMapping


class ArtifactQueryService:
    """Read-only, domain-specific queries for the M1D API."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def database_revision(self) -> str | None:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            return connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()

    def list_artifacts(
        self,
        *,
        statuses: Sequence[str],
        artifact_type: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = ["status IN :statuses"]
        parameters: dict[str, Any] = {
            "statuses": tuple(statuses),
            "limit": limit,
            "offset": offset,
        }
        if artifact_type:
            conditions.append("artifact_type = :artifact_type")
            parameters["artifact_type"] = artifact_type
        where_clause = " AND ".join(conditions)
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            count_statement = text(
                f"SELECT count(*) FROM lineage.artifact WHERE {where_clause}"
            ).bindparams(bindparam("statuses", expanding=True))
            total = int(connection.execute(count_statement, parameters).scalar_one())
            rows_statement = text(
                f"""
                SELECT artifact_id, artifact_type, artifact_key, version_number, status,
                       semantic_fingerprint, content_hash, published_at, created_at
                FROM lineage.artifact WHERE {where_clause}
                ORDER BY artifact_type, artifact_key, version_number DESC, artifact_id
                LIMIT :limit OFFSET :offset
                """
            ).bindparams(bindparam("statuses", expanding=True))
            rows = connection.execute(rows_statement, parameters).mappings().all()
            return [dict(row) for row in rows], total

    def artifact_detail(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            artifact = (
                connection.execute(
                    text(
                        """
                    SELECT artifact_id, artifact_type, artifact_key, version_number, status,
                           semantic_fingerprint, content_hash, published_at, created_at
                    FROM lineage.artifact WHERE artifact_id = :artifact_id
                    """
                    ),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if artifact is None:
                raise LookupError(f"Artifact not found: {artifact_id}")
            dependencies = (
                connection.execute(
                    text(
                        """
                    SELECT artifact_id, depends_on_artifact_id, role, ordinal
                    FROM lineage.artifact_dependency WHERE artifact_id = :artifact_id
                    ORDER BY role, ordinal, depends_on_artifact_id
                    """
                    ),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .all()
            )
            dependents = (
                connection.execute(
                    text(
                        """
                    SELECT artifact_id, depends_on_artifact_id, role, ordinal
                    FROM lineage.artifact_dependency WHERE depends_on_artifact_id = :artifact_id
                    ORDER BY role, ordinal, artifact_id
                    """
                    ),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .all()
            )
            has_manifest = connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM lineage.lineage_manifest "
                    "WHERE root_artifact_id = :artifact_id)"
                ),
                {"artifact_id": artifact_id},
            ).scalar_one()
            return {
                "artifact": dict(artifact),
                "direct_dependencies": [dict(row) for row in dependencies],
                "direct_dependents": [dict(row) for row in dependents],
                "has_manifest": bool(has_manifest),
            }

    def lineage_manifest(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        detail = self.artifact_detail(artifact_id)
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            row: RowMapping | None = (
                connection.execute(
                    text(
                        """
                    SELECT manifest_hash, canonical_version, manifest, created_at
                    FROM lineage.lineage_manifest WHERE root_artifact_id = :artifact_id
                    """
                    ),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError(f"Lineage manifest not found: {artifact_id}")
            return {"artifact": detail["artifact"], **dict(row)}

    def asset_catalog(self) -> dict[str, Any]:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            release = (
                connection.execute(
                    text(
                        """
                    SELECT release.master_data_release_id, release.artifact_id,
                           release.version_number, release.as_of_date
                    FROM catalog.master_data_release release
                    JOIN lineage.artifact artifact ON artifact.artifact_id = release.artifact_id
                    WHERE artifact.status = 'published'
                    ORDER BY release.version_number DESC LIMIT 1
                    """
                    )
                )
                .mappings()
                .one_or_none()
            )
            if release is None:
                raise LookupError("Published research-scope catalog not found")
            universe = (
                connection.execute(
                    text(
                        """
                    SELECT definition.universe_key, version.universe_version_id
                    FROM catalog.universe_version version
                    JOIN catalog.universe_definition definition
                      ON definition.universe_definition_id = version.universe_definition_id
                    JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id
                    WHERE artifact.status = 'published'
                    ORDER BY version.version_number DESC LIMIT 1
                    """
                    )
                )
                .mappings()
                .one_or_none()
            )
            if universe is None:
                raise LookupError("Published research universe not found")
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT asset.asset_id, asset.asset_key, asset.name, asset.asset_type,
                           asset.status, symbol.symbol, listing.venue_mic, listing.currency,
                           listing.timezone, calendar.calendar_key,
                           member.role AS universe_role, member.ordinal AS universe_ordinal,
                           COALESCE(jsonb_object_agg(scheme.scheme_key, value.value_key)
                               FILTER (WHERE scheme.scheme_key IS NOT NULL), '{}'::jsonb)
                               AS classifications
                    FROM catalog.asset asset
                    JOIN catalog.asset_listing listing ON listing.asset_id = asset.asset_id
                    JOIN catalog.listing_symbol symbol
                      ON symbol.asset_listing_id = listing.asset_listing_id
                     AND symbol.symbol_type = 'ticker'
                    JOIN catalog.calendar_definition calendar
                      ON calendar.calendar_definition_id = listing.calendar_definition_id
                    LEFT JOIN catalog.asset_classification assignment
                      ON assignment.asset_id = asset.asset_id
                    LEFT JOIN catalog.classification_value value
                      ON value.classification_value_id = assignment.classification_value_id
                    LEFT JOIN catalog.classification_scheme scheme
                      ON scheme.classification_scheme_id = value.classification_scheme_id
                    LEFT JOIN catalog.universe_member member
                      ON member.asset_id = asset.asset_id
                     AND member.universe_version_id = :universe_version_id
                    WHERE asset.master_data_release_id = :release_id
                    GROUP BY asset.asset_id, symbol.symbol, listing.venue_mic, listing.currency,
                             listing.timezone, calendar.calendar_key, member.role, member.ordinal
                    ORDER BY member.ordinal NULLS LAST, asset.asset_key
                    """
                    ),
                    {
                        "release_id": release["master_data_release_id"],
                        "universe_version_id": universe["universe_version_id"],
                    },
                )
                .mappings()
                .all()
            )
            return {
                "release_artifact_id": release["artifact_id"],
                "release_version_number": release["version_number"],
                "as_of_date": release["as_of_date"].isoformat(),
                "universe_key": universe["universe_key"],
                "items": [dict(row) for row in rows],
            }

    def data_requirements(self) -> dict[str, Any]:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            version = (
                connection.execute(
                    text(
                        """
                    SELECT version.data_requirement_version_id, version.artifact_id,
                           version.version_number, definition.requirement_set_key
                    FROM catalog.data_requirement_version version
                    JOIN catalog.data_requirement_definition definition
                      ON definition.data_requirement_definition_id =
                         version.data_requirement_definition_id
                    JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id
                    WHERE artifact.status = 'published'
                    ORDER BY version.version_number DESC LIMIT 1
                    """
                    )
                )
                .mappings()
                .one_or_none()
            )
            if version is None:
                raise LookupError("Published data requirements not found")
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT requirement_key, subject, series_key, fields, interval_unit,
                           interval_count, calendar_type, session_type, timestamp_semantics
                    FROM catalog.data_requirement_member
                    WHERE data_requirement_version_id = :version_id
                    ORDER BY requirement_key
                    """
                    ),
                    {"version_id": version["data_requirement_version_id"]},
                )
                .mappings()
                .all()
            )
            return {
                "artifact_id": version["artifact_id"],
                "requirement_set_key": version["requirement_set_key"],
                "version_number": version["version_number"],
                "items": [dict(row) for row in rows],
            }
