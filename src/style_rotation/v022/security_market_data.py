from __future__ import annotations

import csv
import io
import json
import uuid
import zlib
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.canonical import (
    CanonicalBar,
    MarketCanonicalResult,
    SnapshotDocument,
    ValidationIssue,
    parse_market_snapshots,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput

PRICE_SEMANTICS = "historical_constituent_pit__frozen_retrospective_yahoo_prices"
_REPORT_CONTRACT = "v0.22.security_market_quality.v1"
_BINDING_CONTRACT = "v0.22.security_market_dataset_binding.v1"


MarketGapPolicy = Literal["strict", "free_source_warning"]


@dataclass(frozen=True, slots=True)
class SecurityMarketPublicationSpec:
    yahoo_ingestion_plan_id: uuid.UUID
    calendar_artifact_id: uuid.UUID
    cleaning_version_id: uuid.UUID
    dataset_key: str
    version_number: int
    research_tier: str
    created_by: str
    market_gap_policy: MarketGapPolicy = "strict"

    def __post_init__(self) -> None:
        if not self.dataset_key.strip() or self.version_number < 1 or not self.created_by.strip():
            raise ValueError("Security market publication identity is incomplete")
        if self.research_tier not in {"rankable_research", "exploratory_only"}:
            raise ValueError("Unsupported Security market research tier")
        if self.market_gap_policy not in {"strict", "free_source_warning"}:
            raise ValueError("Unsupported Security market gap policy")


@dataclass(frozen=True, slots=True)
class SecurityMarketPublication:
    quality_report_artifact_id: uuid.UUID
    quality_report_id: uuid.UUID
    error_count: int
    warning_count: int
    dataset_artifact_id: uuid.UUID | None
    dataset_publication_id: uuid.UUID | None
    reused: bool


@dataclass(frozen=True, slots=True)
class SecurityTerminalEventSpec:
    security_id: uuid.UUID
    event_type: str
    effective_session: date
    known_at: datetime
    status: str
    source_evidence_artifact_id: uuid.UUID
    details: dict[str, object]
    version_number: int = 1
    terminal_total_return: Decimal | None = None

    def __post_init__(self) -> None:
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise ValueError("Terminal event known_at must be timezone-aware")
        if self.known_at.date() > self.effective_session:
            raise ValueError("Terminal event cannot be known after its effective session")
        if self.status not in {"confirmed", "estimated", "unresolved"}:
            raise ValueError("Unsupported terminal event evidence status")
        if not self.event_type.strip() or self.version_number < 1:
            raise ValueError("Terminal event identity is incomplete")
        if self.event_type in {"stock_merger", "share_conversion", "spinoff"}:
            terms = self.details.get("settlement_terms")
            if not isinstance(terms, list) or not terms:
                raise ValueError("Reorganization events require explicit settlement terms")


@dataclass(frozen=True, slots=True)
class SecurityTerminalEventPublication:
    security_terminal_event_id: uuid.UUID
    artifact_id: uuid.UUID
    reused: bool


@dataclass(frozen=True, slots=True)
class _MarketInputs:
    plan: RowMapping
    calendar: RowMapping
    cleaning: RowMapping
    snapshots: tuple[RowMapping, ...]
    unavailable: tuple[RowMapping, ...]
    failed_or_pending: tuple[RowMapping, ...]
    uniformly_unavailable_security_ids: frozenset[uuid.UUID]
    assets: dict[uuid.UUID, uuid.UUID]
    result: MarketCanonicalResult


class SecurityMarketDataPublicationService:
    """Merge provider-symbol segments by stable Security and publish exact QA evidence."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, spec: SecurityMarketPublicationSpec) -> SecurityMarketPublication:
        inputs = self._load_and_validate(spec)
        issues = _quality_issues(inputs)
        report_document = _report_document(spec, inputs, issues)
        report_fingerprint = sha256_hexdigest(report_document)
        report_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:security-market-quality:{report_fingerprint}"
        )
        errors = sum(item.severity == "error" for item in issues)
        warnings = sum(item.severity == "warning" for item in issues)
        report_key = f"{spec.dataset_key}__quality"

        def write_report(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO data.v022_security_market_quality_report (
                      security_market_quality_report_id,artifact_id,
                      yahoo_ingestion_plan_id,yahoo_ingestion_plan_artifact_id,
                      calendar_version_id,calendar_artifact_id,report_key,version_number,
                      research_tier,error_count,warning_count,unavailable_segment_count,
                      report_document,report_fingerprint,created_by
                    ) VALUES (
                      :id,:artifact,:plan,:plan_artifact,:calendar,:calendar_artifact,
                      :key,:version,:tier,:errors,:warnings,:unavailable,
                      CAST(:document AS jsonb),:fingerprint,:created_by
                    )
                    """
                ),
                {
                    "id": report_id,
                    "artifact": artifact_id,
                    "plan": spec.yahoo_ingestion_plan_id,
                    "plan_artifact": inputs.plan["artifact_id"],
                    "calendar": inputs.calendar["calendar_version_id"],
                    "calendar_artifact": inputs.calendar["artifact_id"],
                    "key": report_key,
                    "version": spec.version_number,
                    "tier": spec.research_tier,
                    "errors": errors,
                    "warnings": warnings,
                    "unavailable": len(inputs.unavailable),
                    "document": json.dumps(report_document, sort_keys=True, default=str),
                    "fingerprint": report_fingerprint,
                    "created_by": spec.created_by,
                },
            )

        report = self._artifacts.publish(
            artifact_type="v022_security_market_quality_report",
            artifact_key=f"v022_security_market_quality_report__{report_key}",
            version_number=spec.version_number,
            semantic_payload=report_document,
            content_payload=report_document,
            dependencies=(
                DependencyInput(inputs.plan["artifact_id"], "yahoo_ingestion_plan", 0),
                DependencyInput(inputs.calendar["artifact_id"], "calendar_version", 1),
            ),
            reason=f"publish Security market quality report {report_key}",
            draft_writer=write_report,
        )
        if errors:
            return SecurityMarketPublication(
                report.artifact_id, report_id, errors, warnings, None, None, report.reused
            )
        if spec.research_tier != "rankable_research":
            return SecurityMarketPublication(
                report.artifact_id, report_id, 0, warnings, None, None, report.reused
            )

        dependencies = (
            DependencyInput(inputs.plan["artifact_id"], "yahoo_ingestion_plan", 0),
            DependencyInput(inputs.calendar["artifact_id"], "calendar_version", 1),
            DependencyInput(inputs.cleaning["artifact_id"], "cleaning_version", 2),
            DependencyInput(report.artifact_id, "quality_report", 3),
        ) + tuple(
            DependencyInput(row["artifact_id"], "source_snapshot", ordinal + 4)
            for ordinal, row in enumerate(inputs.snapshots)
        )
        binding_document = {
            "contract_version": _BINDING_CONTRACT,
            "dataset_key": spec.dataset_key,
            "version_number": spec.version_number,
            "yahoo_ingestion_plan_id": str(spec.yahoo_ingestion_plan_id),
            "quality_report_artifact_id": str(report.artifact_id),
            "price_semantics": PRICE_SEMANTICS,
            "historical_pit_claimed": False,
            "research_tier": spec.research_tier,
            "market_gap_policy": spec.market_gap_policy,
        }
        binding_fingerprint = sha256_hexdigest(binding_document)
        dataset_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bird:v0.22:security-market-dataset:{spec.dataset_key}:{spec.version_number}",
        )
        dataset_content = {
            **binding_document,
            "bars_hash": sha256_hexdigest([asdict(item) for item in inputs.result.bars]),
            "actions_hash": sha256_hexdigest([asdict(item) for item in inputs.result.actions]),
            "bar_count": len(inputs.result.bars),
            "action_count": len(inputs.result.actions),
            "warning_count": warnings,
        }

        def write_dataset(connection: Connection, artifact_id: uuid.UUID) -> None:
            _write_dataset(
                connection,
                artifact_id,
                dataset_id,
                spec,
                inputs,
                issues,
                report_id,
                report.artifact_id,
                binding_document,
                binding_fingerprint,
            )

        dataset = self._artifacts.publish(
            artifact_type="dataset_publication",
            artifact_key=spec.dataset_key,
            version_number=spec.version_number,
            semantic_payload=binding_document,
            content_payload=dataset_content,
            dependencies=dependencies,
            reason=f"publish Security-level canonical market dataset {spec.dataset_key}",
            draft_writer=write_dataset,
        )
        return SecurityMarketPublication(
            report.artifact_id,
            report_id,
            0,
            warnings,
            dataset.artifact_id,
            dataset_id,
            report.reused and dataset.reused,
        )

    def _load_and_validate(self, spec: SecurityMarketPublicationSpec) -> _MarketInputs:
        with self._engine.connect() as connection:
            plan = _plan(connection, spec.yahoo_ingestion_plan_id)
            calendar = _calendar(connection, spec.calendar_artifact_id, plan)
            cleaning = _cleaning(connection, spec.cleaning_version_id)
            snapshots, unavailable, incomplete = _attempts(connection, spec.yahoo_ingestion_plan_id)
            uniformly_unavailable = _uniformly_unavailable(
                snapshots, unavailable, incomplete
            )
            assets = _legacy_assets(connection, spec.yahoo_ingestion_plan_id)
            documents = _security_documents(snapshots)
            result = parse_market_snapshots(
                documents,
                frozenset(calendar["sessions"]),
                required_symbols=tuple(
                    sorted(
                        str(item).upper()
                        for item in assets
                        if item not in uniformly_unavailable
                    )
                ),
            )
            membership_missing = _membership_missing(
                connection,
                plan,
                calendar,
                result,
                uniformly_unavailable,
                market_gap_policy=spec.market_gap_policy,
            )
        return _MarketInputs(
            plan,
            calendar,
            cleaning,
            snapshots,
            unavailable,
            incomplete,
            uniformly_unavailable,
            assets,
            MarketCanonicalResult(
                result.bars,
                result.actions,
                result.coverage,
                result.issues + membership_missing,
            ),
        )


class SecurityTerminalEventPublicationService:
    """Publish terminal/reorganization facts only with exact source evidence."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, spec: SecurityTerminalEventSpec) -> SecurityTerminalEventPublication:
        with self._engine.connect() as connection:
            security_key = connection.execute(
                text("SELECT security_key FROM catalog.security WHERE security_id=:security"),
                {"security": spec.security_id},
            ).scalar_one_or_none()
        if security_key is None:
            raise LookupError("Security not found")
        document = {
            "contract_version": "v0.22.security_terminal_event.v1",
            "security_id": str(spec.security_id),
            "event_type": spec.event_type,
            "effective_session": spec.effective_session.isoformat(),
            "known_at": spec.known_at.astimezone(UTC).isoformat(),
            "status": spec.status,
            "terminal_total_return": (
                str(spec.terminal_total_return)
                if spec.terminal_total_return is not None
                else None
            ),
            "details": spec.details,
            "source_evidence_artifact_id": str(spec.source_evidence_artifact_id),
        }
        fingerprint = sha256_hexdigest(document)
        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:terminal-event:{fingerprint}"
        )
        key = (
            f"security_terminal_event__{security_key}__"
            f"{spec.effective_session}__{spec.event_type}"
        )

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.security_terminal_event (
                      security_terminal_event_id,artifact_id,security_id,event_type,
                      effective_session,known_at,terminal_total_return,status,details
                    ) VALUES (
                      :id,:artifact,:security,:event_type,:effective,:known_at,
                      :total_return,:status,CAST(:details AS jsonb)
                    )
                    """
                ),
                {
                    "id": event_id,
                    "artifact": artifact_id,
                    "security": spec.security_id,
                    "event_type": spec.event_type,
                    "effective": spec.effective_session,
                    "known_at": spec.known_at,
                    "total_return": spec.terminal_total_return,
                    "status": spec.status,
                    "details": json.dumps(spec.details, sort_keys=True, default=str),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.v022_security_terminal_event_evidence_binding (
                      security_terminal_event_id,terminal_event_artifact_id,
                      source_evidence_artifact_id,evidence_document,evidence_fingerprint
                    ) VALUES (:id,:artifact,:source,CAST(:document AS jsonb),:fingerprint)
                    """
                ),
                {
                    "id": event_id,
                    "artifact": artifact_id,
                    "source": spec.source_evidence_artifact_id,
                    "document": json.dumps(document, sort_keys=True, default=str),
                    "fingerprint": fingerprint,
                },
            )

        result = self._artifacts.publish(
            artifact_type="security_terminal_event",
            artifact_key=key,
            version_number=spec.version_number,
            semantic_payload=document,
            content_payload=document,
            dependencies=(DependencyInput(spec.source_evidence_artifact_id, "source_evidence", 0),),
            reason=f"publish source-backed terminal event {security_key}",
            draft_writer=writer,
        )
        return SecurityTerminalEventPublication(event_id, result.artifact_id, result.reused)


def _plan(connection: Connection, plan_id: uuid.UUID) -> RowMapping:
    row = connection.execute(
        text(
            """
            SELECT plan.*,artifact.status
              FROM data.v022_yahoo_ingestion_plan plan
              JOIN lineage.artifact artifact ON artifact.artifact_id=plan.artifact_id
             WHERE plan.yahoo_ingestion_plan_id=:plan
            """
        ),
        {"plan": plan_id},
    ).mappings().one_or_none()
    if row is None or row["status"] != "published":
        raise LookupError("Published Yahoo ingestion plan not found")
    return row


def _calendar(connection: Connection, artifact_id: uuid.UUID, plan: RowMapping) -> RowMapping:
    row = connection.execute(
        text(
            """
            SELECT version.calendar_version_id,version.artifact_id,artifact.status
              FROM catalog.calendar_version version
              JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             WHERE version.artifact_id=:artifact
            """
        ),
        {"artifact": artifact_id},
    ).mappings().one_or_none()
    if row is None or row["status"] != "published":
        raise LookupError("Published calendar version not found")
    sessions = tuple(
        connection.execute(
            text(
                """
                SELECT session_date FROM catalog.calendar_session
                 WHERE calendar_version_id=:version
                   AND session_date BETWEEN :start AND :end
                 ORDER BY session_date
                """
            ),
            {
                "version": row["calendar_version_id"],
                "start": plan["coverage_start"],
                "end": plan["coverage_end"],
            },
        ).scalars()
    )
    if not sessions:
        raise ValueError("Yahoo ingestion coverage contains no calendar sessions")
    return cast(RowMapping, {**dict(row), "sessions": sessions})


def _cleaning(connection: Connection, version_id: uuid.UUID) -> RowMapping:
    row = connection.execute(
        text(
            """
            SELECT version.cleaning_version_id,version.artifact_id,artifact.status,
                   definition.cleaning_key
              FROM data.cleaning_version version
              JOIN data.cleaning_definition definition
                ON definition.cleaning_definition_id=version.cleaning_definition_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             WHERE version.cleaning_version_id=:version
            """
        ),
        {"version": version_id},
    ).mappings().one_or_none()
    if row is None or row["status"] != "published" or row["cleaning_key"] != "adjusted_ohlc":
        raise LookupError("Published adjusted_ohlc Cleaning Version not found")
    return row


def _attempts(
    connection: Connection, plan_id: uuid.UUID
) -> tuple[tuple[RowMapping, ...], tuple[RowMapping, ...], tuple[RowMapping, ...]]:
    rows = connection.execute(
        text(
            """
            SELECT segment.yahoo_ingestion_segment_id,segment.ordinal,segment.security_id,
                   segment.provider_symbol,segment.coverage_start,segment.coverage_end,
                   attempt.attempt_status,attempt.failure_reason,
                   snapshot.source_snapshot_id,snapshot.artifact_id,
                   snapshot.compressed_payload
              FROM data.v022_yahoo_ingestion_segment segment
              LEFT JOIN LATERAL (
                SELECT item.* FROM data.v022_yahoo_ingestion_attempt item
                 WHERE item.yahoo_ingestion_segment_id=segment.yahoo_ingestion_segment_id
                 ORDER BY item.attempt_ordinal DESC LIMIT 1
              ) attempt ON true
              LEFT JOIN data.source_snapshot snapshot
                ON snapshot.source_snapshot_id=attempt.source_snapshot_id
             WHERE segment.yahoo_ingestion_plan_id=:plan
             ORDER BY segment.ordinal
            """
        ),
        {"plan": plan_id},
    ).mappings().all()
    fetched = tuple(row for row in rows if row["attempt_status"] == "fetched")
    unavailable = tuple(row for row in rows if row["attempt_status"] == "unavailable")
    incomplete = tuple(
        row for row in rows if row["attempt_status"] not in {"fetched", "unavailable"}
    )
    return fetched, unavailable, incomplete


def _legacy_assets(connection: Connection, plan_id: uuid.UUID) -> dict[uuid.UUID, uuid.UUID]:
    rows = connection.execute(
        text(
            """
            SELECT DISTINCT segment.security_id,security.legacy_asset_id
              FROM data.v022_yahoo_ingestion_segment segment
              JOIN catalog.security security ON security.security_id=segment.security_id
             WHERE segment.yahoo_ingestion_plan_id=:plan
            """
        ),
        {"plan": plan_id},
    ).all()
    return {cast(uuid.UUID, security): cast(uuid.UUID, asset) for security, asset in rows if asset}


def _security_documents(snapshots: tuple[RowMapping, ...]) -> tuple[SnapshotDocument, ...]:
    by_security: dict[uuid.UUID, list[dict[str, str]]] = defaultdict(list)
    fields: list[str] | None = None
    for snapshot in snapshots:
        payload = zlib.decompress(snapshot["compressed_payload"])
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
        if reader.fieldnames is None:
            raise ValueError("Yahoo snapshot CSV header is absent")
        if fields is None:
            fields = list(reader.fieldnames)
        elif list(reader.fieldnames) != fields:
            raise ValueError("Yahoo snapshot CSV schemas differ between ticker segments")
        by_security[cast(uuid.UUID, snapshot["security_id"])].extend(reader)
    if fields is None:
        return ()
    documents: list[SnapshotDocument] = []
    for security_id, rows in sorted(by_security.items(), key=lambda item: str(item[0])):
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        documents.append(SnapshotDocument(str(security_id), buffer.getvalue().encode()))
    return tuple(documents)


def _membership_missing(
    connection: Connection,
    plan: RowMapping,
    calendar: RowMapping,
    result: MarketCanonicalResult,
    uniformly_unavailable: frozenset[uuid.UUID],
    *,
    market_gap_policy: MarketGapPolicy,
) -> tuple[ValidationIssue, ...]:
    bars = {(uuid.UUID(item.symbol), item.session_date) for item in result.bars}
    rows = connection.execute(
        text(
            """
            SELECT session.session_date,member.security_id
              FROM unnest(CAST(:sessions AS date[])) session(session_date)
              JOIN LATERAL (
                SELECT snapshot.universe_snapshot_id
                  FROM catalog.universe_snapshot snapshot
                 WHERE snapshot.universe_history_id=:history
                   AND snapshot.effective_session<=session.session_date
                 ORDER BY snapshot.effective_session DESC LIMIT 1
              ) active ON true
              JOIN catalog.universe_snapshot_member member
                ON member.universe_snapshot_id=active.universe_snapshot_id
             ORDER BY session.session_date,member.security_id
            """
        ),
        {
            "sessions": list(calendar["sessions"]),
            "history": plan["universe_history_id"],
        },
    ).all()
    missing_by_security: dict[uuid.UUID, list[date]] = defaultdict(list)
    for session, security_id in rows:
        if (security_id, session) not in bars:
            missing_by_security[cast(uuid.UUID, security_id)].append(cast(date, session))
    return tuple(
        ValidationIssue(
            (
                "warning"
                if security_id in uniformly_unavailable
                or market_gap_policy == "free_source_warning"
                else "error"
            ),
            (
                "active_member_uniformly_excluded_provider_unavailable"
                if security_id in uniformly_unavailable
                else "active_member_market_bar_missing"
            ),
            (
                f"Active S&P 500 member is uniformly excluded for {len(sessions)} "
                "session(s) by explicit provider-unavailable evidence"
                if security_id in uniformly_unavailable
                else f"Active S&P 500 member lacks {len(sessions)} Yahoo market bar(s); "
                "no halt is inferred"
            ),
            str(security_id),
            sessions[0],
            {
                "classification": "unresolved_provider_or_lifecycle_gap",
                "missing_count": len(sessions),
                "first_missing": sessions[0].isoformat(),
                "last_missing": sessions[-1].isoformat(),
                "sample_sessions": [item.isoformat() for item in sessions[:10]],
            },
        )
        for security_id, sessions in sorted(
            missing_by_security.items(), key=lambda item: str(item[0])
        )
    )


def _quality_issues(inputs: _MarketInputs) -> tuple[ValidationIssue, ...]:
    issues = list(inputs.result.issues)
    for row in inputs.unavailable:
        security_id = cast(uuid.UUID, row["security_id"])
        uniform = security_id in inputs.uniformly_unavailable_security_ids
        issues.append(
            ValidationIssue(
                "warning" if uniform else "error",
                (
                    "security_uniformly_excluded_provider_unavailable"
                    if uniform
                    else "yahoo_segment_partially_unavailable"
                ),
                str(row["failure_reason"]),
                str(security_id),
                None,
                {"provider_symbol": str(row["provider_symbol"])},
            )
        )
    for row in inputs.failed_or_pending:
        issues.append(
            ValidationIssue(
                "error",
                "yahoo_segment_incomplete",
                str(row["failure_reason"] or "Yahoo ingestion segment has no terminal attempt"),
                str(row["security_id"]),
            )
        )
    fetched_security_ids = {
        cast(uuid.UUID, row["security_id"]) for row in inputs.snapshots
    }
    for missing in sorted(fetched_security_ids.difference(inputs.assets), key=str):
        issues.append(
            ValidationIssue(
                "error",
                "security_asset_bridge_missing",
                "Security lacks the canonical Asset bridge required by daily_bar",
                str(missing),
            )
        )
    return tuple(issues)


def _report_document(
    spec: SecurityMarketPublicationSpec,
    inputs: _MarketInputs,
    issues: tuple[ValidationIssue, ...],
) -> dict[str, object]:
    return {
        "contract_version": _REPORT_CONTRACT,
        "dataset_key": spec.dataset_key,
        "version_number": spec.version_number,
        "yahoo_ingestion_plan_id": str(spec.yahoo_ingestion_plan_id),
        "calendar_artifact_id": str(spec.calendar_artifact_id),
        "research_tier": spec.research_tier,
        "market_gap_policy": spec.market_gap_policy,
        "price_semantics": PRICE_SEMANTICS,
        "historical_pit_claimed": False,
        "fetched_segment_count": len(inputs.snapshots),
        "unavailable_segment_count": len(inputs.unavailable),
        "uniformly_excluded_security_count": len(
            inputs.uniformly_unavailable_security_ids
        ),
        "incomplete_segment_count": len(inputs.failed_or_pending),
        "bar_count": len(inputs.result.bars),
        "action_count": len(inputs.result.actions),
        "issues": [asdict(item) for item in issues],
    }


def _uniformly_unavailable(
    snapshots: tuple[RowMapping, ...],
    unavailable: tuple[RowMapping, ...],
    incomplete: tuple[RowMapping, ...],
) -> frozenset[uuid.UUID]:
    states: dict[uuid.UUID, set[str]] = defaultdict(set)
    for row in snapshots:
        states[cast(uuid.UUID, row["security_id"])].add("fetched")
    for row in unavailable:
        states[cast(uuid.UUID, row["security_id"])].add("unavailable")
    for row in incomplete:
        states[cast(uuid.UUID, row["security_id"])].add("incomplete")
    return frozenset(
        security_id
        for security_id, values in states.items()
        if values == {"unavailable"}
    )


def _write_dataset(
    connection: Connection,
    artifact_id: uuid.UUID,
    dataset_id: uuid.UUID,
    spec: SecurityMarketPublicationSpec,
    inputs: _MarketInputs,
    issues: tuple[ValidationIssue, ...],
    report_id: uuid.UUID,
    report_artifact_id: uuid.UUID,
    binding_document: dict[str, object],
    binding_fingerprint: str,
) -> None:
    bars = inputs.result.bars
    start = min(item.session_date for item in bars)
    end = max(item.session_date for item in bars)
    connection.execute(
        text(
            """
            INSERT INTO data.dataset_publication (
              dataset_publication_id,artifact_id,cleaning_version_id,calendar_version_id,
              dataset_key,version_number,dataset_kind,value_kind,coverage_start,
              coverage_end,row_count
            ) VALUES (:id,:artifact,:cleaning,:calendar,:key,:version,'canonical',
                      'daily_bar',:start,:end,:rows)
            """
        ),
        {
            "id": dataset_id,
            "artifact": artifact_id,
            "cleaning": inputs.cleaning["cleaning_version_id"],
            "calendar": inputs.calendar["calendar_version_id"],
            "key": spec.dataset_key,
            "version": spec.version_number,
            "start": start,
            "end": end,
            "rows": len(bars),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO data.dataset_input (
              dataset_input_id,dataset_publication_id,source_snapshot_id,role,ordinal
            ) VALUES (:id,:dataset,:snapshot,'source_snapshot',:ordinal)
            """
        ),
        [
            {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:dataset-input:{dataset_id}:{i}"),
                "dataset": dataset_id,
                "snapshot": row["source_snapshot_id"],
                "ordinal": i,
            }
            for i, row in enumerate(inputs.snapshots)
        ],
    )
    bar_sql = text(
        """
        INSERT INTO data.daily_bar (
          dataset_publication_id,asset_id,session_date,open_raw,high_raw,low_raw,
          close_raw,adj_close,open_adj,high_adj,low_adj,close_adj,
          adjustment_factor,volume_raw
        ) VALUES (:dataset,:asset,:session,:open_raw,:high_raw,:low_raw,:close_raw,
                  :adj_close,:open_adj,:high_adj,:low_adj,:close_adj,:factor,:volume)
        """
    )
    for offset in range(0, len(bars), 10_000):
        connection.execute(
            bar_sql,
            [
                {
                    "dataset": dataset_id,
                    "asset": inputs.assets[uuid.UUID(item.symbol)],
                    "session": item.session_date,
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
                for item in bars[offset : offset + 10_000]
            ],
        )
    if inputs.result.actions:
        connection.execute(
            text(
                """
                INSERT INTO data.corporate_action (
                  dataset_publication_id,asset_id,effective_date,cash_dividend,split_ratio
                ) VALUES (:dataset,:asset,:date,:dividend,:split)
                """
            ),
            [
                {
                    "dataset": dataset_id,
                    "asset": inputs.assets[uuid.UUID(item.symbol)],
                    "date": item.effective_date,
                    "dividend": item.cash_dividend,
                    "split": item.split_ratio,
                }
                for item in inputs.result.actions
            ],
        )
    by_security: dict[uuid.UUID, list[CanonicalBar]] = defaultdict(list)
    for item in bars:
        by_security[uuid.UUID(item.symbol)].append(item)
    connection.execute(
        text(
            """
            INSERT INTO data.dataset_coverage (
              dataset_coverage_id,dataset_publication_id,asset_id,subject_key,
              coverage_start,coverage_end,observation_count,missing_count
            ) VALUES (:id,:dataset,:asset,:subject,:start,:end,:count,:missing)
            """
        ),
        [
            {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"bird:v0.22:coverage:{dataset_id}:{security}",
                ),
                "dataset": dataset_id,
                "asset": inputs.assets[security],
                "subject": str(security),
                "start": min(item.session_date for item in items),
                "end": max(item.session_date for item in items),
                "count": len(items),
                "missing": next(
                    coverage.missing_count
                    for coverage in inputs.result.coverage
                    if coverage.subject_key == str(security).upper()
                ),
            }
            for security, items in sorted(by_security.items(), key=lambda item: str(item[0]))
        ],
    )
    warning_issues = [item for item in issues if item.severity != "error"]
    if warning_issues:
        connection.execute(
            text(
                """
                INSERT INTO data.quality_issue (
                  quality_issue_id,dataset_publication_id,asset_id,severity,rule_code,
                  event_date,message,details
                ) VALUES (:id,:dataset,:asset,:severity,:rule,:date,:message,
                          CAST(:details AS jsonb))
                """
            ),
            [
                {
                    "id": uuid.uuid4(),
                    "dataset": dataset_id,
                    "asset": (
                        inputs.assets.get(uuid.UUID(item.subject_key))
                        if item.subject_key
                        else None
                    ),
                    "severity": item.severity,
                    "rule": item.rule_code,
                    "date": item.event_date,
                    "message": item.message,
                    "details": json.dumps(item.details or {}, sort_keys=True, default=str),
                }
                for item in warning_issues
            ],
        )
    connection.execute(
        text(
            """
            INSERT INTO data.v022_security_market_dataset_binding (
              dataset_publication_id,dataset_artifact_id,yahoo_ingestion_plan_id,
              yahoo_ingestion_plan_artifact_id,security_market_quality_report_id,
              quality_report_artifact_id,price_semantics,historical_pit_claimed,
              research_tier,binding_document,binding_fingerprint
            ) VALUES (:dataset,:artifact,:plan,:plan_artifact,:report,:report_artifact,
                      :semantics,false,'rankable_research',CAST(:document AS jsonb),:fingerprint)
            """
        ),
        {
            "dataset": dataset_id,
            "artifact": artifact_id,
            "plan": spec.yahoo_ingestion_plan_id,
            "plan_artifact": inputs.plan["artifact_id"],
            "report": report_id,
            "report_artifact": report_artifact_id,
            "semantics": PRICE_SEMANTICS,
            "document": json.dumps(binding_document, sort_keys=True, default=str),
            "fingerprint": binding_fingerprint,
        },
    )
