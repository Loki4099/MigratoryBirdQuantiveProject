from __future__ import annotations

import csv
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from style_rotation.v022.data_seed_import import (
    ExternalImportManifestService,
    ExternalImportManifestSpec,
    ExternalImportObjectSpec,
)
from style_rotation.v022.green_baseline_import import (
    GreenBaselineImportPlan,
    build_green_baseline_import_plan,
)
from style_rotation.v022.historical_universe import (
    HistoricalMembershipSnapshot,
    HistoricalSp500UniversePublicationService,
    HistoricalSp500UniverseSpec,
    MembershipSecurityMapping,
)

_CONTRACT = "migratory_bird_v022_green_baseline_universe_v1"
_LEDGER_TABLE = "catalog.v022_universe_membership_ledger"
_BATCH_TABLE = "catalog.v022_universe_change_batch"
_EVENT_TABLE = "catalog.v022_universe_membership_event"
_SOURCE_LOGICAL_KEY = f"metadata/{_LEDGER_TABLE}.csv"


@dataclass(frozen=True, slots=True)
class GreenBaselineUniverseSpec:
    transfer_root: Path
    plan: GreenBaselineImportPlan
    created_by: str


@dataclass(frozen=True, slots=True)
class GreenBaselineUniversePublication:
    contract: str
    source_manifest_artifact_id: str
    membership_ledger_artifact_id: str
    universe_methodology_artifact_id: str
    universe_history_artifact_id: str
    universe_history_id: str
    snapshot_count: int
    event_count: int
    security_count: int
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rows(root: Path, table: str) -> list[dict[str, str]]:
    csv.field_size_limit(min(sys.maxsize, 10_000_000))
    with (root / "metadata" / f"{table}.csv").open(
        "r", encoding="utf-8", newline=""
    ) as source:
        return list(csv.DictReader(source))


def _latest_ledger(root: Path) -> dict[str, str]:
    rows = _rows(root, _LEDGER_TABLE)
    candidates = [
        item
        for item in rows
        if item["universe_key"] == "sp500_historical_free_research_v1"
        and item["research_tier"] == "rankable_research"
    ]
    if not candidates:
        raise ValueError("transfer has no rankable historical S&P 500 ledger")
    return max(candidates, key=lambda item: int(item["version_number"]))


def _source_facts(
    root: Path,
) -> tuple[
    tuple[HistoricalMembershipSnapshot, ...],
    tuple[MembershipSecurityMapping, ...],
    datetime,
]:
    ledger = _latest_ledger(root)
    ledger_id = ledger["universe_membership_ledger_id"]
    document = json.loads(ledger["ledger_document"])
    mappings = tuple(
        MembershipSecurityMapping(
            source_symbol=item["source_symbol"],
            security_id=__import__("uuid").UUID(item["security_id"]),
            valid_from=date.fromisoformat(item["valid_from"]) if item["valid_from"] else None,
            valid_to=date.fromisoformat(item["valid_to"]) if item["valid_to"] else None,
        )
        for item in document["mappings"]
    )
    batches = {
        row["effective_session"]: row
        for row in _rows(root, _BATCH_TABLE)
        if row["universe_membership_ledger_id"] == ledger_id
    }
    events_by_batch: dict[str, list[dict[str, str]]] = {}
    for event in _rows(root, _EVENT_TABLE):
        events_by_batch.setdefault(event["universe_change_batch_id"], []).append(event)
    active: dict[str, str] = {}
    snapshots: list[HistoricalMembershipSnapshot] = []
    for source_row in document["source_rows"]:
        session = str(source_row["effective_session"])
        batch = batches.get(session)
        if batch is None:
            raise ValueError(f"membership source row has no exact change batch: {session}")
        events = sorted(
            events_by_batch.get(batch["universe_change_batch_id"], []),
            key=lambda item: int(item["ordinal"]),
        )
        for event in events:
            security_id = event["security_id"]
            if event["event_type"] in {"seed", "add"}:
                active[security_id] = event["source_symbol"]
            elif active.pop(security_id, None) is None:
                raise ValueError("membership remove event references an inactive Security")
        if len(active) != int(batch["source_member_count"]):
            raise ValueError("reconstructed membership count differs from the source fact")
        snapshots.append(
            HistoricalMembershipSnapshot(
                effective_session=date.fromisoformat(session),
                source_row_number=int(source_row["source_row_number"]),
                source_symbols=tuple(active.values()),
                evidence_status=batch["evidence_status"],  # type: ignore[arg-type]
                reason_code=batch["reason_code"],
            )
        )
    if len(snapshots) != int(ledger["snapshot_count"]):
        raise ValueError("reconstructed Snapshot count differs from the source fact")
    created_at = datetime.fromisoformat(ledger["created_at"])
    return tuple(snapshots), mappings, created_at


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_green_baseline_universe(
    engine: Engine, spec: GreenBaselineUniverseSpec
) -> GreenBaselineUniversePublication:
    expected = build_green_baseline_import_plan(spec.transfer_root)
    if expected.to_dict() != spec.plan.to_dict():
        raise ValueError("green baseline import plan is stale or does not match the transfer")
    source_path = spec.transfer_root / _SOURCE_LOGICAL_KEY
    source_manifest = ExternalImportManifestService(engine).publish(
        ExternalImportManifestSpec(
            manifest_key="v022_green_sp500_membership_source_facts",
            version_number=1,
            source_project_key="migratory_bird_clean_green",
            source_release_key=spec.plan.transfer_manifest_sha256,
            objects=(
                ExternalImportObjectSpec(
                    object_role="historical_membership_source_facts",
                    logical_key=_SOURCE_LOGICAL_KEY,
                    media_type="text/csv",
                    content_sha256=_sha256(source_path),
                    size_bytes=source_path.stat().st_size,
                    source_uri=f"content:sha256/{_sha256(source_path)}",
                    license_key="project_internal_free_research",
                    provenance_status="verified",
                    usage_scope="local_research",
                    metadata={
                        "contract": _CONTRACT,
                        "transfer_manifest_sha256": spec.plan.transfer_manifest_sha256,
                        "source_facts_not_direct_copy": True,
                    },
                ),
            ),
            created_by=spec.created_by,
        )
    )
    snapshots, mappings, data_cutoff = _source_facts(spec.transfer_root)
    publication = HistoricalSp500UniversePublicationService(engine).publish(
        HistoricalSp500UniverseSpec(
            external_import_manifest_artifact_id=source_manifest.artifact_id,
            source_object_logical_key=_SOURCE_LOGICAL_KEY,
            universe_key="sp500_historical_free_research_green_v1",
            version_number=1,
            methodology_key="sp500_source_backed_green_membership_v1",
            methodology_version=1,
            research_tier="rankable_research",
            snapshots=snapshots,
            mappings=mappings,
            data_cutoff_at=data_cutoff,
            published_at=datetime.now(UTC),
            created_by=spec.created_by,
        )
    )
    return GreenBaselineUniversePublication(
        contract=_CONTRACT,
        source_manifest_artifact_id=str(source_manifest.artifact_id),
        membership_ledger_artifact_id=str(publication.membership_ledger_artifact_id),
        universe_methodology_artifact_id=str(publication.universe_methodology_artifact_id),
        universe_history_artifact_id=str(publication.universe_history_artifact_id),
        universe_history_id=str(publication.universe_history_id),
        snapshot_count=publication.snapshot_count,
        event_count=publication.event_count,
        security_count=len(mappings),
        reused=source_manifest.reused and publication.reused,
    )
