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

    def data_overview(self) -> dict[str, Any]:
        """Return the published M2 data chain as one UI-oriented read model."""
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            sources = (
                connection.execute(
                    text(
                        """
                    SELECT snapshot.artifact_id, definition.series_key,
                           provider.provider_key, snapshot.snapshot_key,
                           snapshot.fetched_at, snapshot.as_of_at,
                           snapshot.raw_size_bytes, snapshot.payload_hash
                    FROM data.source_snapshot snapshot
                    JOIN data.data_series_version series
                      ON series.data_series_version_id = snapshot.data_series_version_id
                    JOIN data.data_series_definition definition
                      ON definition.data_series_definition_id = series.data_series_definition_id
                    JOIN data.source_provider provider
                      ON provider.source_provider_id = series.source_provider_id
                    JOIN lineage.artifact artifact ON artifact.artifact_id = snapshot.artifact_id
                    WHERE artifact.status = 'published'
                    ORDER BY snapshot.fetched_at DESC, definition.series_key
                    LIMIT 50
                    """
                    )
                )
                .mappings()
                .all()
            )
            datasets = (
                connection.execute(
                    text(
                        """
                    SELECT publication.dataset_publication_id, publication.artifact_id,
                           publication.dataset_key, publication.version_number,
                           publication.dataset_kind, publication.value_kind,
                           publication.coverage_start, publication.coverage_end,
                           publication.row_count
                    FROM data.dataset_publication publication
                    JOIN lineage.artifact artifact
                      ON artifact.artifact_id = publication.artifact_id
                    WHERE artifact.status = 'published'
                    ORDER BY publication.dataset_key, publication.version_number DESC
                    """
                    )
                )
                .mappings()
                .all()
            )
            dataset_ids = [row["dataset_publication_id"] for row in datasets]
            coverage_by_id: dict[uuid.UUID, list[dict[str, Any]]] = {
                dataset_id: [] for dataset_id in dataset_ids
            }
            issues_by_id: dict[uuid.UUID, list[dict[str, Any]]] = {
                dataset_id: [] for dataset_id in dataset_ids
            }
            if dataset_ids:
                coverage_rows = (
                    connection.execute(
                        text(
                            """
                        SELECT coverage.dataset_publication_id, coverage.subject_key,
                               asset.asset_key, coverage.coverage_start, coverage.coverage_end,
                               coverage.observation_count, coverage.missing_count
                        FROM data.dataset_coverage coverage
                        LEFT JOIN catalog.asset asset ON asset.asset_id = coverage.asset_id
                        WHERE coverage.dataset_publication_id IN :dataset_ids
                        ORDER BY coverage.dataset_publication_id, coverage.subject_key
                        """
                        ).bindparams(bindparam("dataset_ids", expanding=True)),
                        {"dataset_ids": tuple(dataset_ids)},
                    )
                    .mappings()
                    .all()
                )
                for row in coverage_rows:
                    item = dict(row)
                    dataset_id = item.pop("dataset_publication_id")
                    coverage_by_id[dataset_id].append(item)
                issue_rows = (
                    connection.execute(
                        text(
                            """
                        SELECT issue.dataset_publication_id, issue.severity, issue.rule_code,
                               asset.asset_key, issue.event_date, issue.message, issue.details
                        FROM data.quality_issue issue
                        LEFT JOIN catalog.asset asset ON asset.asset_id = issue.asset_id
                        WHERE issue.dataset_publication_id IN :dataset_ids
                        ORDER BY issue.dataset_publication_id,
                                 CASE issue.severity WHEN 'error' THEN 0
                                      WHEN 'warning' THEN 1 ELSE 2 END,
                                 issue.rule_code, issue.event_date NULLS FIRST
                        """
                        ).bindparams(bindparam("dataset_ids", expanding=True)),
                        {"dataset_ids": tuple(dataset_ids)},
                    )
                    .mappings()
                    .all()
                )
                for row in issue_rows:
                    item = dict(row)
                    dataset_id = item.pop("dataset_publication_id")
                    issues_by_id[dataset_id].append(item)

            dataset_items: list[dict[str, Any]] = []
            for row in datasets:
                item = dict(row)
                dataset_id = item.pop("dataset_publication_id")
                item["coverage"] = coverage_by_id[dataset_id]
                item["issues"] = issues_by_id[dataset_id]
                dataset_items.append(item)

            bundle = self._latest_bundle(connection)
            eligibility = self._latest_eligibility(connection)
            return {
                "sources": [dict(row) for row in sources],
                "datasets": dataset_items,
                "bundle": bundle,
                "eligibility": eligibility,
            }

    @staticmethod
    def _latest_bundle(connection: Any) -> dict[str, Any] | None:
        row = (
            connection.execute(
                text(
                    """
                SELECT version.data_bundle_version_id, version.artifact_id,
                       definition.bundle_key, definition.name, version.version_number,
                       version.coverage_start, version.coverage_end, version.member_count
                FROM data.data_bundle_version version
                JOIN data.data_bundle_definition definition
                  ON definition.data_bundle_definition_id = version.data_bundle_definition_id
                JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id
                WHERE artifact.status = 'published'
                ORDER BY version.version_number DESC, version.created_at DESC LIMIT 1
                """
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        members = (
            connection.execute(
                text(
                    """
                SELECT member.role, member.ordinal, artifact.artifact_id,
                       artifact.artifact_type, artifact.artifact_key,
                       artifact.version_number
                FROM data.data_bundle_member member
                JOIN lineage.artifact artifact ON artifact.artifact_id = COALESCE(
                    (SELECT publication.artifact_id FROM data.dataset_publication publication
                     WHERE publication.dataset_publication_id = member.dataset_publication_id),
                    (SELECT calendar.artifact_id FROM catalog.calendar_version calendar
                     WHERE calendar.calendar_version_id = member.calendar_version_id)
                )
                WHERE member.data_bundle_version_id = :version_id
                ORDER BY member.ordinal, member.role
                """
                ),
                {"version_id": row["data_bundle_version_id"]},
            )
            .mappings()
            .all()
        )
        result = dict(row)
        result.pop("data_bundle_version_id")
        result["members"] = [dict(member) for member in members]
        return result

    @staticmethod
    def _latest_eligibility(connection: Any) -> dict[str, Any] | None:
        row = (
            connection.execute(
                text(
                    """
                SELECT snapshot.eligibility_snapshot_id, snapshot.artifact_id,
                       snapshot.snapshot_key, snapshot.requested_start,
                       snapshot.requested_end, snapshot.warmup_observations,
                       snapshot.member_count, snapshot.eligible_count
                FROM catalog.eligibility_snapshot snapshot
                JOIN lineage.artifact artifact ON artifact.artifact_id = snapshot.artifact_id
                WHERE artifact.status = 'published'
                ORDER BY artifact.version_number DESC, snapshot.created_at DESC LIMIT 1
                """
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        items = (
            connection.execute(
                text(
                    """
                SELECT item.eligibility_item_id, item.asset_id, asset.asset_key,
                       symbol.symbol, item.role, item.is_eligible, item.available_start,
                       item.available_end, item.data_ready_date, item.observation_count
                FROM catalog.eligibility_item item
                JOIN catalog.asset asset ON asset.asset_id = item.asset_id
                JOIN LATERAL (
                    SELECT listing_symbol.symbol
                    FROM catalog.asset_listing listing
                    JOIN catalog.listing_symbol
                      ON listing_symbol.asset_listing_id = listing.asset_listing_id
                    WHERE listing.asset_id = asset.asset_id
                      AND listing_symbol.symbol_type = 'ticker'
                    ORDER BY listing_symbol.valid_to NULLS FIRST,
                             listing_symbol.valid_from DESC NULLS LAST
                    LIMIT 1
                ) symbol ON true
                WHERE item.eligibility_snapshot_id = :snapshot_id
                ORDER BY item.role, asset.asset_key
                """
                ),
                {"snapshot_id": row["eligibility_snapshot_id"]},
            )
            .mappings()
            .all()
        )
        item_ids = [item["eligibility_item_id"] for item in items]
        issues_by_item: dict[uuid.UUID, list[dict[str, Any]]] = {
            item_id: [] for item_id in item_ids
        }
        if item_ids:
            issues = (
                connection.execute(
                    text(
                        """
                    SELECT eligibility_item_id, severity, issue_code, message, details
                    FROM catalog.eligibility_issue
                    WHERE eligibility_item_id IN :item_ids
                    ORDER BY eligibility_item_id, severity DESC, issue_code
                    """
                    ).bindparams(bindparam("item_ids", expanding=True)),
                    {"item_ids": tuple(item_ids)},
                )
                .mappings()
                .all()
            )
            for issue in issues:
                issue_item = dict(issue)
                item_id = issue_item.pop("eligibility_item_id")
                issues_by_item[item_id].append(issue_item)
        eligibility_items: list[dict[str, Any]] = []
        for item in items:
            item_payload = dict(item)
            item_id = item_payload.pop("eligibility_item_id")
            item_payload["issues"] = issues_by_item[item_id]
            eligibility_items.append(item_payload)
        result = dict(row)
        result.pop("eligibility_snapshot_id")
        result["items"] = eligibility_items
        return result
