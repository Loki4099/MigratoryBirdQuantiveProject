from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.providers.snapshots import RawFetch, snapshot_key
from style_rotation.data.service import SourceSnapshotService
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.data_seed_import import SourceSnapshotSecuritySubjectService

_PLAN_CONTRACT_VERSION = "v0.22.yahoo_ingestion_plan.v1"
_PROVIDER_KEY = "yahoo_yfinance"
_SERIES_KEY = "us_equity_daily_market_yahoo"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class YahooEquitySeriesVersion(_StrictModel):
    version_number: int = Field(ge=1)
    provider_series_key: str
    interval_unit: str
    interval_count: int = Field(ge=1)
    calendar_key: str
    timestamp_semantics: str
    availability_semantics: str
    parser_version: str
    request_template: dict[str, Any]
    field_mapping: dict[str, str]


class YahooEquitySeriesContract(_StrictModel):
    catalog_type: str
    catalog_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    release_key: str
    provider_key: str
    series_key: str
    name: str
    description: str
    subject_type: str
    value_kind: str
    price_semantics: str
    historical_pit_claimed: bool
    version: YahooEquitySeriesVersion


@dataclass(frozen=True, slots=True)
class YahooEquityContractPublication:
    release_artifact_id: uuid.UUID
    series_artifact_id: uuid.UUID
    data_series_version_id: uuid.UUID
    reused: bool


def load_yahoo_equity_contract(path: Path) -> YahooEquitySeriesContract:
    contract = YahooEquitySeriesContract.model_validate_json(path.read_text(encoding="utf-8"))
    if (
        contract.catalog_type != "v022_yahoo_equity_series_contract"
        or contract.provider_key != _PROVIDER_KEY
        or contract.series_key != _SERIES_KEY
        or contract.subject_type != "asset_listing"
        or contract.value_kind != "market_bar"
        or contract.historical_pit_claimed
    ):
        raise ValueError("Yahoo equity contract has unsupported frozen semantics")
    return contract


class YahooEquityContractService:
    """Publish the v0.22 equity series without changing the frozen v0.2 release."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, contract: YahooEquitySeriesContract) -> YahooEquityContractPublication:
        version_number = semantic_version_number(contract.catalog_version)
        release_semantic = {
            "catalog_type": contract.catalog_type,
            "catalog_version": contract.catalog_version,
            "release_key": contract.release_key,
            "provider_key": contract.provider_key,
            "series": contract.model_dump(mode="json", exclude={"version"}),
        }
        release = self._artifacts.publish(
            artifact_type="data_contract_release",
            artifact_key=contract.release_key,
            version_number=version_number,
            semantic_payload=release_semantic,
            content_payload=release_semantic,
            reason=f"publish {contract.release_key} {contract.catalog_version}",
            draft_writer=lambda connection, artifact_id: _write_equity_contract_release(
                connection, artifact_id, contract, version_number
            ),
        )
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT definition.data_series_definition_id,
                           provider.source_provider_id,
                           provider_release.artifact_id AS provider_release_artifact_id
                      FROM data.data_series_definition definition
                      JOIN data.data_contract_release release
                        ON release.data_contract_release_id=definition.data_contract_release_id
                      JOIN data.source_provider provider ON provider.provider_key=:provider
                      JOIN data.data_contract_release provider_release
                        ON provider_release.data_contract_release_id=
                           provider.data_contract_release_id
                     WHERE release.artifact_id=:release AND definition.series_key=:series
                    """
                ),
                {
                    "release": release.artifact_id,
                    "provider": contract.provider_key,
                    "series": contract.series_key,
                },
            ).mappings().one()
        version_semantic = {
            "series_key": contract.series_key,
            "catalog_version": contract.catalog_version,
            "provider_key": contract.provider_key,
            **contract.version.model_dump(mode="json"),
            "price_semantics": contract.price_semantics,
            "historical_pit_claimed": contract.historical_pit_claimed,
        }
        series = self._artifacts.publish(
            artifact_type="data_series_version",
            artifact_key=contract.series_key,
            version_number=contract.version.version_number,
            semantic_payload=version_semantic,
            content_payload=version_semantic,
            dependencies=(
                DependencyInput(release.artifact_id, "data_contract_release", 0),
                DependencyInput(rows["provider_release_artifact_id"], "source_provider_release", 1),
            ),
            reason=f"publish {contract.series_key} v{contract.version.version_number}",
            draft_writer=lambda connection, artifact_id: _write_equity_series_version(
                connection,
                artifact_id,
                contract,
                cast(uuid.UUID, rows["data_series_definition_id"]),
                cast(uuid.UUID, rows["source_provider_id"]),
            ),
        )
        with self._engine.connect() as connection:
            data_series_version_id = connection.execute(
                text(
                    "SELECT data_series_version_id FROM data.data_series_version "
                    "WHERE artifact_id=:artifact"
                ),
                {"artifact": series.artifact_id},
            ).scalar_one()
        return YahooEquityContractPublication(
            release.artifact_id,
            series.artifact_id,
            cast(uuid.UUID, data_series_version_id),
            release.reused and series.reused,
        )


@dataclass(frozen=True, slots=True)
class YahooIngestionPlanSpec:
    plan_key: str
    version_number: int
    universe_history_id: uuid.UUID
    data_series_version_id: uuid.UUID
    coverage_start: date
    coverage_end: date
    created_by: str

    def __post_init__(self) -> None:
        if not self.plan_key.strip() or not self.created_by.strip():
            raise ValueError("Yahoo ingestion plan key and creator are required")
        if self.version_number < 1:
            raise ValueError("Yahoo ingestion plan version must be positive")
        if self.coverage_start > self.coverage_end:
            raise ValueError("Yahoo ingestion coverage start must not follow end")


@dataclass(frozen=True, slots=True)
class YahooIngestionSegment:
    ordinal: int
    security_id: uuid.UUID
    security_identifier_id: uuid.UUID
    provider_symbol: str
    coverage_start: date
    coverage_end: date

    def document(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "security_id": str(self.security_id),
            "security_identifier_id": str(self.security_identifier_id),
            "provider_symbol": self.provider_symbol,
            "coverage_start": self.coverage_start.isoformat(),
            "coverage_end": self.coverage_end.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class YahooIngestionPlanPublication:
    yahoo_ingestion_plan_id: uuid.UUID
    artifact_id: uuid.UUID
    plan_fingerprint: str
    segment_count: int
    reused: bool


class YahooIngestionPlanService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, spec: YahooIngestionPlanSpec) -> YahooIngestionPlanPublication:
        with self._engine.connect() as connection:
            history = _history_row(connection, spec.universe_history_id)
            series = _series_row(connection, spec.data_series_version_id)
            segments = _resolve_segments(connection, spec)
        document = {
            "contract_version": _PLAN_CONTRACT_VERSION,
            "plan_key": spec.plan_key,
            "version_number": spec.version_number,
            "universe_history_id": str(spec.universe_history_id),
            "universe_history_artifact_id": str(history["artifact_id"]),
            "data_series_version_id": str(spec.data_series_version_id),
            "data_series_artifact_id": str(series["artifact_id"]),
            "provider_key": _PROVIDER_KEY,
            "coverage_start": spec.coverage_start.isoformat(),
            "coverage_end": spec.coverage_end.isoformat(),
            "segments": [segment.document() for segment in segments],
        }
        fingerprint = sha256_hexdigest(document)
        plan_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:yahoo-plan:{fingerprint}")
        result = self._artifacts.publish(
            artifact_type="v022_yahoo_ingestion_plan",
            artifact_key=f"v022_yahoo_ingestion_plan__{spec.plan_key}",
            version_number=spec.version_number,
            semantic_payload=document,
            content_payload=document,
            dependencies=(
                DependencyInput(history["artifact_id"], "universe_history", 0),
                DependencyInput(series["artifact_id"], "data_series_version", 1),
            ),
            reason=f"publish Yahoo ingestion plan {spec.plan_key}",
            draft_writer=lambda connection, artifact_id: _write_ingestion_plan(
                connection,
                artifact_id,
                plan_id,
                spec,
                history,
                series,
                document,
                fingerprint,
                segments,
            ),
        )
        return YahooIngestionPlanPublication(
            plan_id, result.artifact_id, fingerprint, len(segments), result.reused
        )


class MarketSnapshotAdapter(Protocol):
    def fetch(self, symbol: str, start: date, end_exclusive: date) -> RawFetch: ...


Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class YahooIngestionAttemptResult:
    yahoo_ingestion_segment_id: uuid.UUID
    attempt_ordinal: int
    status: str
    source_snapshot_artifact_id: uuid.UUID | None
    failure_reason: str | None


class YahooIngestionExecutionService:
    """Execute only unfinished segments; every retry is an append-only fact."""

    def __init__(self, engine: Engine, adapter: MarketSnapshotAdapter, *, clock: Clock = _utc_now):
        self._engine = engine
        self._adapter = adapter
        self._clock = clock
        self._snapshots = SourceSnapshotService(engine)
        self._subjects = SourceSnapshotSecuritySubjectService(engine)

    def pending_segment_ids(
        self, yahoo_ingestion_plan_id: uuid.UUID
    ) -> tuple[uuid.UUID, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT segment.yahoo_ingestion_segment_id
                      FROM data.v022_yahoo_ingestion_segment segment
                     WHERE segment.yahoo_ingestion_plan_id=:plan
                       AND NOT EXISTS (
                         SELECT 1 FROM data.v022_yahoo_ingestion_attempt attempt
                          WHERE attempt.yahoo_ingestion_segment_id=
                                segment.yahoo_ingestion_segment_id
                            AND attempt.attempt_status IN ('fetched','unavailable')
                       )
                     ORDER BY segment.ordinal
                    """
                ),
                {"plan": yahoo_ingestion_plan_id},
            ).scalars().all()
        return tuple(cast(uuid.UUID, item) for item in rows)

    def execute_pending(
        self, yahoo_ingestion_plan_id: uuid.UUID, *, limit: int | None = None
    ) -> tuple[YahooIngestionAttemptResult, ...]:
        if limit is not None and limit < 1:
            raise ValueError("Yahoo ingestion limit must be positive")
        segment_ids = self.pending_segment_ids(yahoo_ingestion_plan_id)
        if limit is not None:
            segment_ids = segment_ids[:limit]
        return tuple(self.execute_segment(segment_id) for segment_id in segment_ids)

    def execute_segment(
        self, yahoo_ingestion_segment_id: uuid.UUID
    ) -> YahooIngestionAttemptResult:
        with self._engine.connect() as connection:
            segment = _segment_row(connection, yahoo_ingestion_segment_id)
            attempt_ordinal = _next_attempt_ordinal(connection, yahoo_ingestion_segment_id)
        started_at = self._clock()
        try:
            fetched = self._adapter.fetch(
                str(segment["provider_symbol"]),
                cast(date, segment["coverage_start"]),
                cast(date, segment["coverage_end"]) + timedelta(days=1),
            )
        except Exception as error:
            completed_at = self._clock()
            reason = _failure_reason(error)
            _insert_attempt(
                self._engine,
                yahoo_ingestion_segment_id,
                attempt_ordinal,
                "failed",
                started_at,
                completed_at,
                None,
                None,
                reason,
            )
            return YahooIngestionAttemptResult(
                yahoo_ingestion_segment_id, attempt_ordinal, "failed", None, reason
            )
        published = self._snapshots.publish(
            fetched.snapshot_input(
                series_key=_SERIES_KEY,
                series_version=1,
                snapshot_key=snapshot_key(
                    f"{segment['security_id']}:{segment['provider_symbol']}:"
                    f"{segment['coverage_start']}:{segment['coverage_end']}",
                    fetched.fetched_at,
                ),
            )
        )
        with self._engine.connect() as connection:
            source_snapshot_id = connection.execute(
                text(
                    "SELECT source_snapshot_id FROM data.source_snapshot "
                    "WHERE artifact_id=:artifact"
                ),
                {"artifact": published.artifact_id},
            ).scalar_one()
        subject = self._subjects.bind(
            source_snapshot_id=cast(uuid.UUID, source_snapshot_id),
            security_id=cast(uuid.UUID, segment["security_id"]),
            security_identifier_id=cast(uuid.UUID, segment["security_identifier_id"]),
            fetch_status="fetched",
        )
        _insert_attempt(
            self._engine,
            yahoo_ingestion_segment_id,
            attempt_ordinal,
            "fetched",
            fetched.requested_at,
            fetched.fetched_at,
            cast(uuid.UUID, source_snapshot_id),
            subject.source_snapshot_security_subject_id,
            None,
        )
        return YahooIngestionAttemptResult(
            yahoo_ingestion_segment_id,
            attempt_ordinal,
            "fetched",
            published.artifact_id,
            None,
        )

    def mark_unavailable(
        self, yahoo_ingestion_segment_id: uuid.UUID, *, reason: str
    ) -> YahooIngestionAttemptResult:
        if not reason.strip():
            raise ValueError("Unavailable Yahoo segment requires a reason")
        with self._engine.connect() as connection:
            _segment_row(connection, yahoo_ingestion_segment_id)
            ordinal = _next_attempt_ordinal(connection, yahoo_ingestion_segment_id)
        now = self._clock()
        _insert_attempt(
            self._engine,
            yahoo_ingestion_segment_id,
            ordinal,
            "unavailable",
            now,
            now,
            None,
            None,
            reason.strip(),
        )
        return YahooIngestionAttemptResult(
            yahoo_ingestion_segment_id, ordinal, "unavailable", None, reason.strip()
        )


def _write_equity_contract_release(
    connection: Connection,
    artifact_id: uuid.UUID,
    contract: YahooEquitySeriesContract,
    version_number: int,
) -> None:
    release_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"bird:v0.22:data-contract-release:{contract.catalog_version}"
    )
    definition_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"bird:v0.22:data-series-definition:{contract.series_key}"
    )
    connection.execute(
        text(
            """
            INSERT INTO data.data_contract_release (
              data_contract_release_id,artifact_id,release_key,version_number
            ) VALUES (:id,:artifact,:key,:version)
            """
        ),
        {
            "id": release_id,
            "artifact": artifact_id,
            "key": contract.release_key,
            "version": version_number,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO data.data_series_definition (
              data_series_definition_id,data_contract_release_id,series_key,name,
              description,subject_type,value_kind
            ) VALUES (:id,:release,:key,:name,:description,:subject_type,:value_kind)
            """
        ),
        {
            "id": definition_id,
            "release": release_id,
            "key": contract.series_key,
            "name": contract.name,
            "description": contract.description,
            "subject_type": contract.subject_type,
            "value_kind": contract.value_kind,
        },
    )


def _write_equity_series_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    contract: YahooEquitySeriesContract,
    definition_id: uuid.UUID,
    provider_id: uuid.UUID,
) -> None:
    version = contract.version
    connection.execute(
        text(
            """
            INSERT INTO data.data_series_version (
              data_series_version_id,data_series_definition_id,source_provider_id,
              artifact_id,version_number,provider_series_key,interval_unit,
              interval_count,calendar_key,timestamp_semantics,availability_semantics,
              parser_version,request_template,field_mapping
            ) VALUES (
              :id,:definition,:provider,:artifact,:version,:provider_series_key,
              :interval_unit,:interval_count,:calendar_key,:timestamp_semantics,
              :availability_semantics,:parser_version,CAST(:request_template AS jsonb),
              CAST(:field_mapping AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"bird:v0.22:data-series-version:{contract.series_key}:{version.version_number}",
            ),
            "definition": definition_id,
            "provider": provider_id,
            "artifact": artifact_id,
            "version": version.version_number,
            "provider_series_key": version.provider_series_key,
            "interval_unit": version.interval_unit,
            "interval_count": version.interval_count,
            "calendar_key": version.calendar_key,
            "timestamp_semantics": version.timestamp_semantics,
            "availability_semantics": version.availability_semantics,
            "parser_version": version.parser_version,
            "request_template": json.dumps(version.request_template, sort_keys=True),
            "field_mapping": json.dumps(version.field_mapping, sort_keys=True),
        },
    )


def _history_row(connection: Connection, history_id: uuid.UUID) -> RowMapping:
    row = connection.execute(
        text(
            """
            SELECT history.artifact_id,artifact.status
              FROM catalog.universe_history history
              JOIN lineage.artifact artifact ON artifact.artifact_id=history.artifact_id
              JOIN catalog.v022_universe_history_ledger_binding binding
                ON binding.universe_history_id=history.universe_history_id
             WHERE history.universe_history_id=:history
            """
        ),
        {"history": history_id},
    ).mappings().one_or_none()
    if row is None or row["status"] != "published":
        raise LookupError("Published source-backed Universe History not found")
    return row


def _series_row(connection: Connection, version_id: uuid.UUID) -> RowMapping:
    row = connection.execute(
        text(
            """
            SELECT version.artifact_id,artifact.status,definition.series_key,
                   provider.provider_key
              FROM data.data_series_version version
              JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
              JOIN data.data_series_definition definition
                ON definition.data_series_definition_id=version.data_series_definition_id
              JOIN data.source_provider provider
                ON provider.source_provider_id=version.source_provider_id
             WHERE version.data_series_version_id=:version
            """
        ),
        {"version": version_id},
    ).mappings().one_or_none()
    if (
        row is None
        or row["status"] != "published"
        or row["series_key"] != _SERIES_KEY
        or row["provider_key"] != _PROVIDER_KEY
    ):
        raise LookupError("Published v0.22 Yahoo equity series not found")
    return row


def _resolve_segments(
    connection: Connection, spec: YahooIngestionPlanSpec
) -> tuple[YahooIngestionSegment, ...]:
    security_ids = tuple(
        connection.execute(
            text(
                """
                SELECT DISTINCT member.security_id
                  FROM catalog.universe_snapshot snapshot
                  JOIN catalog.universe_snapshot_member member
                    ON member.universe_snapshot_id=snapshot.universe_snapshot_id
                 WHERE snapshot.universe_history_id=:history
                 ORDER BY member.security_id
                """
            ),
            {"history": spec.universe_history_id},
        ).scalars()
    )
    if not security_ids:
        raise ValueError("Yahoo ingestion requires a non-empty Universe History")
    rows = connection.execute(
        text(
            """
            SELECT security_identifier_id,security_id,identifier_value,valid_from,valid_to
              FROM catalog.security_identifier
             WHERE security_id=ANY(:security_ids)
               AND provider_scope=:provider AND identifier_type='provider_symbol'
               AND coalesce(valid_from,'-infinity'::date)<=:coverage_end
               AND coalesce(valid_to,'infinity'::date)>:coverage_start
             ORDER BY security_id,coalesce(valid_from,'-infinity'::date),
                      coalesce(valid_to,'infinity'::date),lower(identifier_value)
            """
        ),
        {
            "security_ids": list(security_ids),
            "provider": _PROVIDER_KEY,
            "coverage_start": spec.coverage_start,
            "coverage_end": spec.coverage_end,
        },
    ).mappings().all()
    by_security: dict[uuid.UUID, list[tuple[date, date, RowMapping]]] = {}
    for row in rows:
        start = max(spec.coverage_start, row["valid_from"] or spec.coverage_start)
        end = min(
            spec.coverage_end,
            (row["valid_to"] - timedelta(days=1))
            if row["valid_to"] is not None
            else spec.coverage_end,
        )
        by_security.setdefault(cast(uuid.UUID, row["security_id"]), []).append((start, end, row))
    missing = set(security_ids).difference(by_security)
    if missing:
        raise ValueError(f"Yahoo provider identity is missing for {len(missing)} Securities")
    segments: list[YahooIngestionSegment] = []
    for security_id in security_ids:
        prior_end: date | None = None
        for start, end, row in by_security[cast(uuid.UUID, security_id)]:
            if prior_end is not None and start <= prior_end:
                raise ValueError("Yahoo provider identifier periods overlap for one Security")
            prior_end = end
            segments.append(
                YahooIngestionSegment(
                    len(segments),
                    cast(uuid.UUID, security_id),
                    cast(uuid.UUID, row["security_identifier_id"]),
                    str(row["identifier_value"]),
                    start,
                    end,
                )
            )
    return tuple(segments)


def _write_ingestion_plan(
    connection: Connection,
    artifact_id: uuid.UUID,
    plan_id: uuid.UUID,
    spec: YahooIngestionPlanSpec,
    history: RowMapping,
    series: RowMapping,
    document: dict[str, object],
    fingerprint: str,
    segments: tuple[YahooIngestionSegment, ...],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO data.v022_yahoo_ingestion_plan (
              yahoo_ingestion_plan_id,artifact_id,plan_key,version_number,
              universe_history_id,universe_history_artifact_id,data_series_version_id,
              data_series_artifact_id,provider_key,coverage_start,coverage_end,
              segment_count,plan_document,plan_fingerprint,created_by
            ) VALUES (
              :id,:artifact,:key,:version,:history,:history_artifact,:series,
              :series_artifact,:provider,:start,:end,:count,CAST(:document AS jsonb),
              :fingerprint,:created_by
            )
            """
        ),
        {
            "id": plan_id,
            "artifact": artifact_id,
            "key": spec.plan_key,
            "version": spec.version_number,
            "history": spec.universe_history_id,
            "history_artifact": history["artifact_id"],
            "series": spec.data_series_version_id,
            "series_artifact": series["artifact_id"],
            "provider": _PROVIDER_KEY,
            "start": spec.coverage_start,
            "end": spec.coverage_end,
            "count": len(segments),
            "document": json.dumps(document, sort_keys=True),
            "fingerprint": fingerprint,
            "created_by": spec.created_by,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO data.v022_yahoo_ingestion_segment (
              yahoo_ingestion_segment_id,yahoo_ingestion_plan_id,ordinal,security_id,
              security_identifier_id,provider_symbol,coverage_start,coverage_end
            ) VALUES (:id,:plan,:ordinal,:security,:identifier,:symbol,:start,:end)
            """
        ),
        [
            {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"bird:v0.22:yahoo-segment:{plan_id}:{segment.ordinal}",
                ),
                "plan": plan_id,
                "ordinal": segment.ordinal,
                "security": segment.security_id,
                "identifier": segment.security_identifier_id,
                "symbol": segment.provider_symbol,
                "start": segment.coverage_start,
                "end": segment.coverage_end,
            }
            for segment in segments
        ],
    )


def _segment_row(connection: Connection, segment_id: uuid.UUID) -> RowMapping:
    row = connection.execute(
        text(
            """
            SELECT segment.*,plan.data_series_version_id
              FROM data.v022_yahoo_ingestion_segment segment
              JOIN data.v022_yahoo_ingestion_plan plan
                ON plan.yahoo_ingestion_plan_id=segment.yahoo_ingestion_plan_id
             WHERE segment.yahoo_ingestion_segment_id=:segment
            """
        ),
        {"segment": segment_id},
    ).mappings().one_or_none()
    if row is None:
        raise LookupError("Yahoo ingestion segment not found")
    return row


def _next_attempt_ordinal(connection: Connection, segment_id: uuid.UUID) -> int:
    terminal = connection.execute(
        text(
            """
            SELECT bool_or(attempt_status IN ('fetched','unavailable')) AS terminal,
                   coalesce(max(attempt_ordinal),-1)+1 AS next_ordinal
              FROM data.v022_yahoo_ingestion_attempt
             WHERE yahoo_ingestion_segment_id=:segment
            """
        ),
        {"segment": segment_id},
    ).mappings().one()
    if terminal["terminal"]:
        raise ValueError("Completed Yahoo ingestion segment cannot be retried")
    return int(terminal["next_ordinal"])


def _insert_attempt(
    engine: Engine,
    segment_id: uuid.UUID,
    ordinal: int,
    status: str,
    requested_at: datetime,
    completed_at: datetime,
    snapshot_id: uuid.UUID | None,
    subject_id: uuid.UUID | None,
    failure_reason: str | None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO data.v022_yahoo_ingestion_attempt (
                  yahoo_ingestion_attempt_id,yahoo_ingestion_segment_id,attempt_ordinal,
                  attempt_status,requested_at,completed_at,source_snapshot_id,
                  source_snapshot_security_subject_id,failure_reason
                ) VALUES (
                  :id,:segment,:ordinal,:status,:requested,:completed,:snapshot,
                  :subject,:reason
                )
                """
            ),
            {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL, f"bird:v0.22:yahoo-attempt:{segment_id}:{ordinal}"
                ),
                "segment": segment_id,
                "ordinal": ordinal,
                "status": status,
                "requested": requested_at,
                "completed": completed_at,
                "snapshot": snapshot_id,
                "subject": subject_id,
                "reason": failure_reason,
            },
        )


def _failure_reason(error: Exception) -> str:
    message = " ".join(str(error).split()) or "no error message"
    return f"{type(error).__name__}: {message}"[:1000]
