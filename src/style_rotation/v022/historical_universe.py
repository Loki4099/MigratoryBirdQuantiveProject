from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Literal, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult

MembershipEvidenceStatus = Literal["confirmed", "estimated", "unresolved"]
ResearchTier = Literal["rankable_research", "exploratory_only"]

_LEDGER_CONTRACT = "v0.22.sp500_membership_ledger.v1"
_HISTORY_CONTRACT = "v0.22.source_backed_universe_history.v1"


@dataclass(frozen=True, slots=True)
class HistoricalMembershipSnapshot:
    effective_session: date
    source_row_number: int
    source_symbols: tuple[str, ...]
    announced_at: datetime | None = None
    evidence_status: MembershipEvidenceStatus = "confirmed"
    reason_code: str = "retrospective_full_snapshot_source"

    def __post_init__(self) -> None:
        if self.source_row_number < 2:
            raise ValueError("Membership source row numbers include the CSV header")
        if not self.source_symbols:
            raise ValueError("Historical membership Snapshot requires members")
        normalized = tuple(symbol.strip() for symbol in self.source_symbols)
        if any(not symbol for symbol in normalized):
            raise ValueError("Historical membership symbols cannot be blank")
        folded = tuple(symbol.casefold() for symbol in normalized)
        if len(folded) != len(set(folded)):
            raise ValueError("Historical membership Snapshot contains duplicate symbols")
        if self.announced_at is not None and self.announced_at.tzinfo is None:
            raise ValueError("Membership announcement timestamps must be timezone-aware")
        if not self.reason_code.strip():
            raise ValueError("Membership evidence reason_code is required")
        object.__setattr__(self, "source_symbols", tuple(sorted(normalized, key=str.casefold)))


@dataclass(frozen=True, slots=True)
class MembershipSecurityMapping:
    source_symbol: str
    security_id: uuid.UUID
    valid_from: date | None = None
    valid_to: date | None = None

    def __post_init__(self) -> None:
        if not self.source_symbol.strip():
            raise ValueError("Membership mapping source_symbol is required")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_from >= self.valid_to
        ):
            raise ValueError("Membership mapping interval must be half-open")


def resolve_membership_security_mapping(
    mappings: tuple[MembershipSecurityMapping, ...],
    *,
    source_symbol: str,
    effective_session: date,
) -> MembershipSecurityMapping:
    """Resolve one source ticker by its declared half-open Security intervals."""
    folded = source_symbol.strip().casefold()
    candidates = tuple(
        item
        for item in mappings
        if item.source_symbol.strip().casefold() == folded
        and _mapping_contains(item, effective_session)
    )
    if len(candidates) != 1:
        raise ValueError(
            "Membership mapping must resolve exactly once for "
            f"{source_symbol} on {effective_session.isoformat()}"
        )
    return candidates[0]


@dataclass(frozen=True, slots=True)
class HistoricalSp500UniverseSpec:
    external_import_manifest_artifact_id: uuid.UUID
    source_object_logical_key: str
    universe_key: str
    version_number: int
    methodology_key: str
    methodology_version: int
    research_tier: ResearchTier
    snapshots: tuple[HistoricalMembershipSnapshot, ...]
    mappings: tuple[MembershipSecurityMapping, ...]
    data_cutoff_at: datetime
    published_at: datetime
    created_by: str

    def __post_init__(self) -> None:
        for label, value in (
            ("source_object_logical_key", self.source_object_logical_key),
            ("universe_key", self.universe_key),
            ("methodology_key", self.methodology_key),
            ("created_by", self.created_by),
        ):
            if not value.strip():
                raise ValueError(f"{label} is required")
        if self.version_number < 1 or self.methodology_version < 1:
            raise ValueError("Universe and methodology versions must be positive")
        if self.data_cutoff_at.tzinfo is None or self.published_at.tzinfo is None:
            raise ValueError("Universe publication timestamps must be timezone-aware")
        if self.data_cutoff_at > self.published_at:
            raise ValueError("Universe data cutoff cannot follow publication")
        if not self.snapshots:
            raise ValueError("Historical Universe requires source Snapshots")
        sessions = tuple(item.effective_session for item in self.snapshots)
        if sessions != tuple(sorted(sessions)) or len(sessions) != len(set(sessions)):
            raise ValueError("Membership source sessions must be unique and ordered")


@dataclass(frozen=True, slots=True)
class HistoricalSp500UniversePublication:
    membership_ledger_artifact_id: uuid.UUID
    universe_methodology_artifact_id: uuid.UUID
    universe_history_artifact_id: uuid.UUID
    universe_history_id: uuid.UUID
    snapshot_count: int
    event_count: int
    reused: bool


@dataclass(frozen=True, slots=True)
class _ResolvedRow:
    seed: HistoricalMembershipSnapshot
    members: tuple[uuid.UUID, ...]
    symbol_by_security: dict[uuid.UUID, str]
    row_sha256: str


@dataclass(frozen=True, slots=True)
class _Event:
    event_type: Literal["seed", "add", "remove"]
    security_id: uuid.UUID
    source_symbol: str


@dataclass(frozen=True, slots=True)
class _Batch:
    source: _ResolvedRow
    events: tuple[_Event, ...]


def parse_fja_snapshot_csv(payload: str) -> tuple[HistoricalMembershipSnapshot, ...]:
    """Parse the frozen fja05680 date/tickers full-Snapshot contract."""

    reader = csv.DictReader(io.StringIO(payload))
    if reader.fieldnames not in (["date", "tickers"], ["Date", "tickers"]):
        raise ValueError("S&P membership CSV must contain exactly date,tickers")
    date_field = reader.fieldnames[0]
    rows: list[HistoricalMembershipSnapshot] = []
    for row_number, row in enumerate(reader, start=2):
        try:
            session = date.fromisoformat(row[date_field].strip())
        except (KeyError, ValueError) as error:
            raise ValueError(f"Invalid membership date on source row {row_number}") from error
        symbols = tuple(item.strip() for item in row["tickers"].split(",") if item.strip())
        rows.append(
            HistoricalMembershipSnapshot(
                effective_session=session,
                source_row_number=row_number,
                source_symbols=symbols,
            )
        )
    if not rows:
        raise ValueError("S&P membership CSV contains no Snapshots")
    sessions = tuple(row.effective_session for row in rows)
    if sessions != tuple(sorted(sessions)) or len(sessions) != len(set(sessions)):
        raise ValueError("S&P membership CSV dates must be unique and ordered")
    return tuple(rows)


class HistoricalSp500UniversePublicationService:
    """Publish a source ledger and exact runtime snapshots derived from full membership rows."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, spec: HistoricalSp500UniverseSpec) -> HistoricalSp500UniversePublication:
        source = self._source_object(spec)
        rows, used_mappings = self._resolve_rows(spec)
        batches = _derive_batches(rows)
        ledger_document = _ledger_document(
            spec, source["content_sha256"], rows, batches, used_mappings
        )
        ledger_fingerprint = sha256_hexdigest(ledger_document)
        ledger_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:membership-ledger:{ledger_fingerprint}"
        )
        event_count = sum(len(batch.events) for batch in batches)

        def write_ledger(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.v022_universe_membership_ledger (
                      universe_membership_ledger_id,artifact_id,
                      external_import_manifest_id,source_object_logical_key,
                      source_content_sha256,universe_key,version_number,research_tier,
                      source_row_count,snapshot_count,event_count,coverage_start,
                      coverage_end,ledger_document,ledger_fingerprint,created_by
                    ) VALUES (
                      :id,:artifact,:manifest,:logical_key,:content_sha256,:universe_key,
                      :version,:tier,:source_rows,:snapshots,:events,:coverage_start,
                      :coverage_end,CAST(:document AS jsonb),:fingerprint,:created_by
                    )
                    """
                ),
                {
                    "id": ledger_id,
                    "artifact": artifact_id,
                    "manifest": source["external_import_manifest_id"],
                    "logical_key": spec.source_object_logical_key,
                    "content_sha256": source["content_sha256"],
                    "universe_key": spec.universe_key,
                    "version": spec.version_number,
                    "tier": spec.research_tier,
                    "source_rows": len(rows),
                    "snapshots": len(batches),
                    "events": event_count,
                    "coverage_start": rows[0].seed.effective_session,
                    "coverage_end": rows[-1].seed.effective_session,
                    "document": _json(ledger_document),
                    "fingerprint": ledger_fingerprint,
                    "created_by": spec.created_by,
                },
            )
            for batch_ordinal, batch in enumerate(batches):
                batch_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"bird:v0.22:membership-batch:{ledger_fingerprint}:{batch_ordinal}",
                )
                added_count = sum(event.event_type in {"seed", "add"} for event in batch.events)
                removed_count = sum(event.event_type == "remove" for event in batch.events)
                connection.execute(
                    text(
                        """
                        INSERT INTO catalog.v022_universe_change_batch (
                          universe_change_batch_id,universe_membership_ledger_id,ordinal,
                          effective_session,announced_at,source_row_number,source_row_sha256,
                          source_member_count,added_count,removed_count,evidence_status,reason_code
                        ) VALUES (
                          :id,:ledger,:ordinal,:effective,:announced,:row_number,:row_sha256,
                          :member_count,:added,:removed,:status,:reason
                        )
                        """
                    ),
                    {
                        "id": batch_id,
                        "ledger": ledger_id,
                        "ordinal": batch_ordinal,
                        "effective": batch.source.seed.effective_session,
                        "announced": batch.source.seed.announced_at,
                        "row_number": batch.source.seed.source_row_number,
                        "row_sha256": batch.source.row_sha256,
                        "member_count": len(batch.source.members),
                        "added": added_count,
                        "removed": removed_count,
                        "status": batch.source.seed.evidence_status,
                        "reason": batch.source.seed.reason_code,
                    },
                )
                for event_ordinal, event in enumerate(batch.events):
                    connection.execute(
                        text(
                            """
                            INSERT INTO catalog.v022_universe_membership_event (
                              universe_membership_event_id,universe_change_batch_id,ordinal,
                              event_type,security_id,source_symbol,effective_session,
                              announced_at,source_row_number,evidence_status,reason_code
                            ) VALUES (
                              :id,:batch,:ordinal,:event_type,:security,:symbol,:effective,
                              :announced,:row_number,:status,:reason
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"bird:v0.22:membership-event:{ledger_fingerprint}:"
                                f"{batch_ordinal}:{event_ordinal}",
                            ),
                            "batch": batch_id,
                            "ordinal": event_ordinal,
                            "event_type": event.event_type,
                            "security": event.security_id,
                            "symbol": event.source_symbol,
                            "effective": batch.source.seed.effective_session,
                            "announced": batch.source.seed.announced_at,
                            "row_number": batch.source.seed.source_row_number,
                            "status": batch.source.seed.evidence_status,
                            "reason": batch.source.seed.reason_code,
                        },
                    )

        ledger = self._artifacts.publish(
            artifact_type="v022_universe_membership_ledger",
            artifact_key=f"v022_universe_membership_ledger__{spec.universe_key}",
            version_number=spec.version_number,
            semantic_payload=ledger_document,
            content_payload=ledger_document,
            dependencies=(
                DependencyInput(
                    spec.external_import_manifest_artifact_id,
                    "external_import_manifest",
                    0,
                ),
            ),
            reason=f"publish source-backed membership ledger {spec.universe_key}",
            draft_writer=write_ledger,
        )

        methodology = self._publish_methodology(spec)
        history = self._publish_history(
            spec, rows, batches, ledger_id, ledger.artifact_id, methodology
        )
        with self._engine.connect() as connection:
            persisted = connection.execute(
                text(
                    """
                    SELECT history.universe_history_id
                      FROM catalog.v022_universe_history_ledger_binding binding
                      JOIN catalog.universe_history history
                        ON history.universe_history_id=binding.universe_history_id
                     WHERE binding.universe_membership_ledger_id=:ledger
                       AND binding.universe_history_artifact_id=:artifact
                    """
                ),
                {"ledger": ledger_id, "artifact": history.artifact_id},
            ).scalar_one()
        return HistoricalSp500UniversePublication(
            ledger.artifact_id,
            methodology.artifact_id,
            history.artifact_id,
            cast(uuid.UUID, persisted),
            len(batches),
            event_count,
            ledger.reused and methodology.reused and history.reused,
        )

    def _source_object(self, spec: HistoricalSp500UniverseSpec) -> dict[str, object]:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT manifest.external_import_manifest_id,object.content_sha256
                      FROM data.v022_external_import_manifest manifest
                      JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
                      JOIN data.v022_external_import_object object
                        ON object.external_import_manifest_id=manifest.external_import_manifest_id
                     WHERE manifest.artifact_id=:artifact
                       AND object.logical_key=:logical_key
                       AND artifact.status='published'
                    """
                ),
                {
                    "artifact": spec.external_import_manifest_artifact_id,
                    "logical_key": spec.source_object_logical_key,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("Published membership source object not found")
        return dict(row)

    def _resolve_rows(
        self, spec: HistoricalSp500UniverseSpec
    ) -> tuple[tuple[_ResolvedRow, ...], tuple[MembershipSecurityMapping, ...]]:
        mappings_by_symbol: dict[str, list[MembershipSecurityMapping]] = {}
        for item in spec.mappings:
            folded = item.source_symbol.strip().casefold()
            mappings_by_symbol.setdefault(folded, []).append(item)
        used = {symbol.casefold() for row in spec.snapshots for symbol in row.source_symbols}
        if set(mappings_by_symbol) != used:
            missing = sorted(used - set(mappings_by_symbol))
            extra = sorted(set(mappings_by_symbol) - used)
            raise ValueError(f"Membership mappings must be exact; missing={missing}, extra={extra}")
        for symbol, items in mappings_by_symbol.items():
            ordered = sorted(items, key=_mapping_sort_key)
            for previous, current in zip(ordered, ordered[1:], strict=False):
                if previous.valid_to is None or current.valid_from is None:
                    raise ValueError(
                        f"Membership mapping intervals overlap for source symbol {symbol}"
                    )
                if previous.valid_to > current.valid_from:
                    raise ValueError(
                        f"Membership mapping intervals overlap for source symbol {symbol}"
                    )
            mappings_by_symbol[symbol] = ordered
        security_ids = tuple(item.security_id for item in spec.mappings)
        with self._engine.connect() as connection:
            existing = connection.execute(
                text(
                    "SELECT security_id FROM catalog.security WHERE security_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": security_ids},
            ).scalars().all()
        if set(existing) != set(security_ids):
            raise LookupError("Historical membership mappings reference unknown Securities")
        resolved: list[_ResolvedRow] = []
        selected_mappings: set[MembershipSecurityMapping] = set()
        for row in spec.snapshots:
            symbol_by_security: dict[uuid.UUID, str] = {}
            for symbol in row.source_symbols:
                selected = resolve_membership_security_mapping(
                    tuple(mappings_by_symbol[symbol.casefold()]),
                    source_symbol=symbol,
                    effective_session=row.effective_session,
                )
                selected_mappings.add(selected)
                if selected.security_id in symbol_by_security:
                    raise ValueError("Historical Snapshot maps multiple symbols to one Security")
                symbol_by_security[selected.security_id] = symbol
            if len(symbol_by_security) != len(row.source_symbols):
                raise ValueError("Historical Snapshot maps multiple symbols to one Security")
            members = tuple(sorted(symbol_by_security, key=str))
            row_document = {
                "effective_session": row.effective_session,
                "source_row_number": row.source_row_number,
                "source_symbols": row.source_symbols,
                "security_ids": [str(item) for item in members],
            }
            resolved.append(
                _ResolvedRow(row, members, symbol_by_security, sha256_hexdigest(row_document))
            )
        if selected_mappings != set(spec.mappings):
            raise ValueError("Membership mappings contain intervals unused by source Snapshots")
        used_mappings = tuple(sorted(spec.mappings, key=_mapping_sort_key))
        return tuple(resolved), used_mappings

    def _publish_methodology(self, spec: HistoricalSp500UniverseSpec) -> PublicationResult:
        payload = {
            "methodology_key": spec.methodology_key,
            "version_number": spec.methodology_version,
            "research_mode": (
                "formal" if spec.research_tier == "rankable_research" else "exploratory"
            ),
            "parameters": {
                "contract_version": _HISTORY_CONTRACT,
                "universe": "S&P 500",
                "membership_semantics": "retrospective_full_snapshot_effective_session",
                "selection": "source_membership_without_reconstitution",
            },
        }
        methodology_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:universe-methodology:{sha256_hexdigest(payload)}"
        )

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.universe_methodology (
                      universe_methodology_id,artifact_id,methodology_key,version_number,
                      research_mode,parameters
                    ) VALUES (:id,:artifact,:key,:version,:mode,CAST(:parameters AS jsonb))
                    """
                ),
                {
                    "id": methodology_id,
                    "artifact": artifact_id,
                    "key": spec.methodology_key,
                    "version": spec.methodology_version,
                    "mode": payload["research_mode"],
                    "parameters": _json(payload["parameters"]),
                },
            )

        return self._artifacts.publish(
            artifact_type="universe_methodology",
            artifact_key=f"{spec.methodology_key}__{sha256_hexdigest(payload)}",
            version_number=spec.methodology_version,
            semantic_payload=payload,
            content_payload=payload,
            reason=f"publish source-backed methodology {spec.methodology_key}",
            draft_writer=writer,
        )

    def _publish_history(
        self,
        spec: HistoricalSp500UniverseSpec,
        rows: tuple[_ResolvedRow, ...],
        batches: tuple[_Batch, ...],
        ledger_id: uuid.UUID,
        ledger_artifact_id: uuid.UUID,
        methodology: PublicationResult,
    ) -> PublicationResult:
        with self._engine.connect() as connection:
            methodology_id = connection.execute(
                text(
                    "SELECT universe_methodology_id FROM catalog.universe_methodology "
                    "WHERE artifact_id=:artifact"
                ),
                {"artifact": methodology.artifact_id},
            ).scalar_one()
        payload = {
            "contract_version": _HISTORY_CONTRACT,
            "universe_key": spec.universe_key,
            "version_number": spec.version_number,
            "methodology_artifact_id": str(methodology.artifact_id),
            "membership_ledger_artifact_id": str(ledger_artifact_id),
            "as_of_date": rows[-1].seed.effective_session,
            "snapshots": [
                {
                    "effective_session": batch.source.seed.effective_session,
                    "members": [str(item) for item in batch.source.members],
                }
                for batch in batches
            ],
        }
        history_fingerprint = sha256_hexdigest(payload)
        history_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:source-backed-universe-history:{history_fingerprint}"
        )
        binding_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:universe-history-ledger-binding:{history_fingerprint}"
        )

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.universe_history (
                      universe_history_id,artifact_id,universe_methodology_id,as_of_date,snapshot_count
                    ) VALUES (:id,:artifact,:methodology,:as_of,:count)
                    """
                ),
                {
                    "id": history_id,
                    "artifact": artifact_id,
                    "methodology": methodology_id,
                    "as_of": rows[-1].seed.effective_session,
                    "count": len(batches),
                },
            )
            for ordinal, batch in enumerate(batches):
                snapshot_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"bird:v0.22:source-backed-snapshot:{history_fingerprint}:{ordinal}",
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO catalog.universe_snapshot (
                          universe_snapshot_id,universe_history_id,rank_date,data_cutoff_at,
                          published_at,effective_session,member_count
                        ) VALUES (:id,:history,:rank_date,:cutoff,:published,:effective,:count)
                        """
                    ),
                    {
                        "id": snapshot_id,
                        "history": history_id,
                        "rank_date": batch.source.seed.effective_session,
                        "cutoff": spec.data_cutoff_at,
                        "published": spec.published_at,
                        "effective": batch.source.seed.effective_session,
                        "count": len(batch.source.members),
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO catalog.universe_snapshot_member (
                          universe_snapshot_id,security_id,issuer_id,ordinal,
                          primary_selection_security
                        ) VALUES (:snapshot,:security,NULL,:ordinal,true)
                        """
                    ),
                    [
                        {
                            "snapshot": snapshot_id,
                            "security": security_id,
                            "ordinal": member_ordinal,
                        }
                        for member_ordinal, security_id in enumerate(batch.source.members)
                    ],
                )
            binding_document = {
                "universe_membership_ledger_id": str(ledger_id),
                "universe_history_id": str(history_id),
                "universe_history_artifact_id": str(artifact_id),
            }
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.v022_universe_history_ledger_binding (
                      universe_history_ledger_binding_id,universe_membership_ledger_id,
                      universe_history_id,universe_history_artifact_id,binding_fingerprint
                    ) VALUES (:id,:ledger,:history,:artifact,:fingerprint)
                    """
                ),
                {
                    "id": binding_id,
                    "ledger": ledger_id,
                    "history": history_id,
                    "artifact": artifact_id,
                    "fingerprint": sha256_hexdigest(binding_document),
                },
            )

        return self._artifacts.publish(
            artifact_type="universe_history",
            artifact_key=f"{spec.universe_key}__history__{history_fingerprint}",
            version_number=spec.version_number,
            semantic_payload=payload,
            content_payload=payload,
            dependencies=(
                DependencyInput(methodology.artifact_id, "universe_methodology", 0),
                DependencyInput(ledger_artifact_id, "membership_ledger", 1),
            ),
            reason=f"publish source-backed Universe History {spec.universe_key}",
            draft_writer=writer,
        )


def _derive_batches(rows: tuple[_ResolvedRow, ...]) -> tuple[_Batch, ...]:
    batches: list[_Batch] = []
    previous: _ResolvedRow | None = None
    for row in rows:
        if previous is None:
            events = tuple(
                _Event("seed", security_id, row.symbol_by_security[security_id])
                for security_id in row.members
            )
        else:
            previous_ids = set(previous.members)
            current_ids = set(row.members)
            events = tuple(
                [
                    _Event("add", security_id, row.symbol_by_security[security_id])
                    for security_id in sorted(current_ids - previous_ids, key=str)
                ]
                + [
                    _Event("remove", security_id, previous.symbol_by_security[security_id])
                    for security_id in sorted(previous_ids - current_ids, key=str)
                ]
            )
        if events:
            batches.append(_Batch(row, events))
        previous = row
    return tuple(batches)


def _ledger_document(
    spec: HistoricalSp500UniverseSpec,
    source_content_sha256: object,
    rows: tuple[_ResolvedRow, ...],
    batches: tuple[_Batch, ...],
    mappings: tuple[MembershipSecurityMapping, ...],
) -> dict[str, object]:
    return {
        "contract_version": _LEDGER_CONTRACT,
        "universe_key": spec.universe_key,
        "version_number": spec.version_number,
        "research_tier": spec.research_tier,
        "external_import_manifest_artifact_id": str(spec.external_import_manifest_artifact_id),
        "source_object_logical_key": spec.source_object_logical_key,
        "source_content_sha256": source_content_sha256,
        "coverage_start": rows[0].seed.effective_session,
        "coverage_end": rows[-1].seed.effective_session,
        "source_rows": [
            {
                "effective_session": row.seed.effective_session,
                "source_row_number": row.seed.source_row_number,
                "row_sha256": row.row_sha256,
            }
            for row in rows
        ],
        "mappings": [
            {
                "source_symbol": item.source_symbol,
                "security_id": str(item.security_id),
                "valid_from": item.valid_from,
                "valid_to": item.valid_to,
            }
            for item in mappings
        ],
        "change_batches": [
            {
                "effective_session": batch.source.seed.effective_session,
                "source_row_number": batch.source.seed.source_row_number,
                "events": [asdict(event) for event in batch.events],
            }
            for batch in batches
        ],
    }


def _mapping_sort_key(
    item: MembershipSecurityMapping,
) -> tuple[str, date, date, str]:
    return (
        item.source_symbol.strip().casefold(),
        item.valid_from or date.min,
        item.valid_to or date.max,
        str(item.security_id),
    )


def _mapping_contains(item: MembershipSecurityMapping, session: date) -> bool:
    return (item.valid_from is None or item.valid_from <= session) and (
        item.valid_to is None or session < item.valid_to
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
