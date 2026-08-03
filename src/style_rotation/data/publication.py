from __future__ import annotations

import json
import uuid
import zlib
from dataclasses import asdict
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.data.canonical import (
    CanonicalQualityError,
    MarketCanonicalResult,
    RateCanonicalResult,
    SnapshotDocument,
    parse_fred_snapshot,
    parse_market_snapshots,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult


class CanonicalDataPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish_market(
        self,
        snapshot_artifact_ids: tuple[uuid.UUID, ...],
        calendar_artifact_id: uuid.UUID,
        *,
        version_number: int,
    ) -> PublicationResult:
        if len(snapshot_artifact_ids) != len(set(snapshot_artifact_ids)):
            raise ValueError("Market snapshot artifact ids must be unique")
        snapshots = self._load_snapshots(snapshot_artifact_ids, "us_etf_daily_market")
        calendar = self._load_calendar(calendar_artifact_id)
        documents = tuple(
            SnapshotDocument(_market_subject(item), _payload(item)) for item in snapshots
        )
        result = parse_market_snapshots(documents, frozenset(calendar["sessions"]))
        if result.has_errors:
            raise CanonicalQualityError(result.issues)
        cleaning = self._cleaning("adjusted_ohlc", 1)
        dependencies = tuple(
            DependencyInput(item["artifact_id"], "source_snapshot", ordinal)
            for ordinal, item in enumerate(snapshots)
        ) + (
            DependencyInput(calendar["artifact_id"], "calendar_version", len(snapshots)),
            DependencyInput(cleaning["artifact_id"], "cleaning_version", len(snapshots) + 1),
        )
        semantic = {
            "dataset_key": "us_etf_daily_market_canonical",
            "version_number": version_number,
            "source_snapshot_artifact_ids": [str(item["artifact_id"]) for item in snapshots],
            "calendar_artifact_id": str(calendar["artifact_id"]),
            "cleaning_artifact_id": str(cleaning["artifact_id"]),
        }
        content = {**semantic, "result": asdict(result)}
        return self._artifacts.publish(
            artifact_type="dataset_publication",
            artifact_key="us_etf_daily_market_canonical",
            version_number=version_number,
            semantic_payload=semantic,
            content_payload=content,
            dependencies=dependencies,
            reason=f"publish canonical market dataset v{version_number}",
            draft_writer=lambda connection, artifact_id: _write_market(
                connection,
                artifact_id,
                version_number,
                snapshots,
                calendar,
                cleaning,
                result,
            ),
        )

    def publish_rate(
        self, snapshot_artifact_id: uuid.UUID, *, version_number: int
    ) -> PublicationResult:
        snapshot = self._load_snapshots((snapshot_artifact_id,), "dgs3mo_daily")[0]
        result = parse_fred_snapshot(SnapshotDocument("DGS3MO", _payload(snapshot)))
        if result.has_errors or result.coverage is None:
            raise CanonicalQualityError(result.issues)
        cleaning = self._cleaning("dgs3mo_observation", 1)
        semantic = {
            "dataset_key": "dgs3mo_canonical",
            "version_number": version_number,
            "source_snapshot_artifact_id": str(snapshot["artifact_id"]),
            "cleaning_artifact_id": str(cleaning["artifact_id"]),
            "availability_rule": "observation_date_plus_one_calendar_day",
        }
        content = {**semantic, "result": asdict(result)}
        return self._artifacts.publish(
            artifact_type="dataset_publication",
            artifact_key="dgs3mo_canonical",
            version_number=version_number,
            semantic_payload=semantic,
            content_payload=content,
            dependencies=(
                DependencyInput(snapshot["artifact_id"], "source_snapshot", 0),
                DependencyInput(cleaning["artifact_id"], "cleaning_version", 1),
            ),
            reason=f"publish canonical DGS3MO dataset v{version_number}",
            draft_writer=lambda connection, artifact_id: _write_rate(
                connection,
                artifact_id,
                version_number,
                snapshot,
                cleaning,
                result,
            ),
        )

    def _load_snapshots(
        self, artifact_ids: tuple[uuid.UUID, ...], expected_series_key: str
    ) -> tuple[RowMapping, ...]:
        if not artifact_ids:
            raise ValueError("At least one source snapshot is required")
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                        SELECT snapshot.source_snapshot_id, snapshot.artifact_id,
                               snapshot.request_parameters, snapshot.compressed_payload,
                               definition.series_key
                        FROM data.source_snapshot snapshot
                        JOIN lineage.artifact artifact
                          ON artifact.artifact_id = snapshot.artifact_id
                         AND artifact.status = 'published'
                        JOIN data.data_series_version version
                          ON version.data_series_version_id = snapshot.data_series_version_id
                        JOIN data.data_series_definition definition ON
                          definition.data_series_definition_id = version.data_series_definition_id
                        WHERE snapshot.artifact_id = ANY(:artifact_ids)
                        """
                    ),
                    {"artifact_ids": list(artifact_ids)},
                )
                .mappings()
                .all()
            )
        by_id = {row["artifact_id"]: row for row in rows}
        if set(by_id) != set(artifact_ids):
            raise ValueError("One or more published source snapshots were not found")
        ordered = tuple(by_id[item] for item in artifact_ids)
        if any(row["series_key"] != expected_series_key for row in ordered):
            raise ValueError(f"Snapshot does not belong to {expected_series_key}")
        return ordered

    def _load_calendar(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT version.calendar_version_id, version.artifact_id
                        FROM catalog.calendar_version version
                        JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id
                        WHERE version.artifact_id = :artifact_id AND artifact.status = 'published'
                        """
                    ),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ValueError("Published calendar version not found")
            sessions = connection.execute(
                text(
                    "SELECT session_date FROM catalog.calendar_session "
                    "WHERE calendar_version_id = :version_id ORDER BY session_date"
                ),
                {"version_id": row["calendar_version_id"]},
            ).scalars()
            return {**dict(row), "sessions": tuple(sessions)}

    def _cleaning(self, key: str, version_number: int) -> RowMapping:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT version.cleaning_version_id, version.artifact_id
                        FROM data.cleaning_version version
                        JOIN data.cleaning_definition definition
                          ON definition.cleaning_definition_id = version.cleaning_definition_id
                        JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id
                        WHERE definition.cleaning_key = :key
                          AND version.version_number = :version_number
                          AND artifact.status = 'published'
                        """
                    ),
                    {"key": key, "version_number": version_number},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ValueError(f"Published cleaning version not found: {key} v{version_number}")
        return row


def _payload(row: RowMapping) -> bytes:
    return zlib.decompress(row["compressed_payload"])


def _market_subject(row: RowMapping) -> str:
    parameters = row["request_parameters"]
    subject = parameters.get("tickers") or parameters.get("ticker")
    if not isinstance(subject, str):
        raise ValueError("Market snapshot lacks one string ticker")
    return subject


def _publication_values(
    artifact_id: uuid.UUID,
    publication_id: uuid.UUID,
    version_number: int,
    dataset_key: str,
    value_kind: str,
    cleaning: RowMapping,
    coverage_start: object,
    coverage_end: object,
    row_count: int,
    calendar_version_id: uuid.UUID | None = None,
) -> dict[str, object]:
    return {
        "id": publication_id,
        "artifact_id": artifact_id,
        "cleaning_id": cleaning["cleaning_version_id"],
        "calendar_id": calendar_version_id,
        "dataset_key": dataset_key,
        "version": version_number,
        "value_kind": value_kind,
        "start": coverage_start,
        "end": coverage_end,
        "row_count": row_count,
    }


def _insert_publication(connection: Connection, values: dict[str, object]) -> None:
    connection.execute(
        text(
            """
            INSERT INTO data.dataset_publication (
                dataset_publication_id, artifact_id, cleaning_version_id, calendar_version_id,
                dataset_key, version_number, dataset_kind, value_kind,
                coverage_start, coverage_end, row_count
            ) VALUES (
                :id, :artifact_id, :cleaning_id, :calendar_id, :dataset_key, :version,
                'canonical', :value_kind, :start, :end, :row_count
            )
            """
        ),
        values,
    )


def _write_inputs(
    connection: Connection, publication_id: uuid.UUID, snapshots: tuple[RowMapping, ...]
) -> None:
    connection.execute(
        text(
            "INSERT INTO data.dataset_input "
            "(dataset_input_id, dataset_publication_id, source_snapshot_id, role, ordinal) "
            "VALUES (:id, :publication_id, :snapshot_id, 'source_snapshot', :ordinal)"
        ),
        [
            {
                "id": uuid.uuid4(),
                "publication_id": publication_id,
                "snapshot_id": item["source_snapshot_id"],
                "ordinal": ordinal,
            }
            for ordinal, item in enumerate(snapshots)
        ],
    )


def _asset_ids(connection: Connection) -> dict[str, uuid.UUID]:
    rows = connection.execute(text("SELECT upper(asset_key), asset_id FROM catalog.asset"))
    return {str(key): asset_id for key, asset_id in rows}


def _write_market(
    connection: Connection,
    artifact_id: uuid.UUID,
    version_number: int,
    snapshots: tuple[RowMapping, ...],
    calendar: dict[str, Any],
    cleaning: RowMapping,
    result: MarketCanonicalResult,
) -> None:
    publication_id = uuid.uuid4()
    start = min(item.coverage_start for item in result.coverage)
    end = max(item.coverage_end for item in result.coverage)
    _insert_publication(
        connection,
        _publication_values(
            artifact_id,
            publication_id,
            version_number,
            "us_etf_daily_market_canonical",
            "daily_bar",
            cleaning,
            start,
            end,
            len(result.bars),
            calendar["calendar_version_id"],
        ),
    )
    _write_inputs(connection, publication_id, snapshots)
    assets = _asset_ids(connection)
    connection.execute(
        text(
            """
            INSERT INTO data.daily_bar (
                dataset_publication_id, asset_id, session_date, open_raw, high_raw, low_raw,
                close_raw, adj_close, open_adj, high_adj, low_adj, close_adj,
                adjustment_factor, volume_raw
            ) VALUES (
                :publication_id, :asset_id, :session_date, :open_raw, :high_raw, :low_raw,
                :close_raw, :adj_close, :open_adj, :high_adj, :low_adj, :close_adj,
                :factor, :volume
            )
            """
        ),
        [
            {
                "publication_id": publication_id,
                "asset_id": assets[item.symbol],
                "session_date": item.session_date,
                "open_raw": item.open_raw,
                "high_raw": item.high_raw,
                "low_raw": item.low_raw,
                "close_raw": item.close_raw,
                "adj_close": item.adj_close,
                "open_adj": item.open_adj,
                "high_adj": item.high_adj,
                "low_adj": item.low_adj,
                "close_adj": item.close_adj,
                "factor": item.adjustment_factor,
                "volume": item.volume_raw,
            }
            for item in result.bars
        ],
    )
    if result.actions:
        connection.execute(
            text(
                "INSERT INTO data.corporate_action "
                "(dataset_publication_id, asset_id, effective_date, cash_dividend, split_ratio) "
                "VALUES (:publication_id, :asset_id, :date, :dividend, :split)"
            ),
            [
                {
                    "publication_id": publication_id,
                    "asset_id": assets[item.symbol],
                    "date": item.effective_date,
                    "dividend": item.cash_dividend,
                    "split": item.split_ratio,
                }
                for item in result.actions
            ],
        )
    _write_coverage(connection, publication_id, result.coverage, assets)
    _write_issues(connection, publication_id, result.issues, assets)


def _write_rate(
    connection: Connection,
    artifact_id: uuid.UUID,
    version_number: int,
    snapshot: RowMapping,
    cleaning: RowMapping,
    result: RateCanonicalResult,
) -> None:
    assert result.coverage is not None
    publication_id = uuid.uuid4()
    _insert_publication(
        connection,
        _publication_values(
            artifact_id,
            publication_id,
            version_number,
            "dgs3mo_canonical",
            "rate_observation",
            cleaning,
            result.coverage.coverage_start,
            result.coverage.coverage_end,
            len(result.observations),
        ),
    )
    _write_inputs(connection, publication_id, (snapshot,))
    connection.execute(
        text(
            "INSERT INTO data.rate_observation "
            "(dataset_publication_id, series_key, observation_date, available_date, "
            "annual_rate_percent) "
            "VALUES (:publication_id, :series_key, :date, :available, :value)"
        ),
        [
            {
                "publication_id": publication_id,
                "series_key": item.series_key,
                "date": item.observation_date,
                "available": item.available_date,
                "value": item.annual_rate_percent,
            }
            for item in result.observations
        ],
    )
    _write_coverage(connection, publication_id, (result.coverage,), {})
    _write_issues(connection, publication_id, result.issues, {})


def _write_coverage(
    connection: Connection,
    publication_id: uuid.UUID,
    coverage: tuple[Any, ...],
    assets: dict[str, uuid.UUID],
) -> None:
    connection.execute(
        text(
            "INSERT INTO data.dataset_coverage "
            "(dataset_coverage_id, dataset_publication_id, asset_id, subject_key, "
            "coverage_start, coverage_end, observation_count, missing_count) "
            "VALUES (:id, :publication_id, :asset_id, :subject, :start, :end, :count, :missing)"
        ),
        [
            {
                "id": uuid.uuid4(),
                "publication_id": publication_id,
                "asset_id": assets.get(item.subject_key),
                "subject": item.subject_key,
                "start": item.coverage_start,
                "end": item.coverage_end,
                "count": item.observation_count,
                "missing": item.missing_count,
            }
            for item in coverage
        ],
    )


def _write_issues(
    connection: Connection,
    publication_id: uuid.UUID,
    issues: tuple[Any, ...],
    assets: dict[str, uuid.UUID],
) -> None:
    if not issues:
        return
    connection.execute(
        text(
            "INSERT INTO data.quality_issue "
            "(quality_issue_id, dataset_publication_id, asset_id, severity, rule_code, "
            "event_date, message, details) "
            "VALUES (:id, :publication_id, :asset_id, :severity, :rule, :date, :message, "
            "CAST(:details AS jsonb))"
        ),
        [
            {
                "id": uuid.uuid4(),
                "publication_id": publication_id,
                "asset_id": assets.get(item.subject_key),
                "severity": item.severity,
                "rule": item.rule_code,
                "date": item.event_date,
                "message": item.message,
                "details": json.dumps(item.details or {}),
            }
            for item in issues
        ],
    )
