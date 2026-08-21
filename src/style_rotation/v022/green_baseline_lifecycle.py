from __future__ import annotations

import csv
import hashlib
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.data_seed_import import (
    ExternalImportManifestService,
    ExternalImportManifestSpec,
    ExternalImportObjectSpec,
)
from style_rotation.v022.green_baseline_import import (
    GreenBaselineImportPlan,
    build_green_baseline_import_plan,
)
from style_rotation.v022.security_lifecycle import (
    LifecycleEvidenceRef,
    SecurityLifecycleEventService,
    SecurityLifecycleEventSpec,
    SecuritySettlementLegSpec,
)

_CONTRACT = "migratory_bird_v022_green_baseline_lifecycle_v1"
_EVENT_TABLE = "catalog.v022_security_lifecycle_event"
_LEG_TABLE = "catalog.v022_security_settlement_leg"


@dataclass(frozen=True, slots=True)
class GreenBaselineLifecycleSpec:
    transfer_root: Path
    plan: GreenBaselineImportPlan
    created_by: str


@dataclass(frozen=True, slots=True)
class GreenBaselineLifecyclePublication:
    contract: str
    source_manifest_artifact_id: str
    event_artifact_ids: tuple[str, ...]
    event_count: int
    settlement_leg_count: int
    reused_event_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rows(root: Path, table: str) -> list[dict[str, str]]:
    csv.field_size_limit(min(sys.maxsize, 10_000_000))
    with (root / "metadata" / f"{table}.csv").open(
        "r", encoding="utf-8", newline=""
    ) as source:
        return list(csv.DictReader(source))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_manifest(engine: Engine, spec: GreenBaselineLifecycleSpec):  # type: ignore[no-untyped-def]
    objects = []
    for table in (_EVENT_TABLE, _LEG_TABLE):
        logical_key = f"metadata/{table}.csv"
        path = spec.transfer_root / logical_key
        digest = _sha256(path)
        objects.append(
            ExternalImportObjectSpec(
                object_role="lifecycle_source_facts",
                logical_key=logical_key,
                media_type="text/csv",
                content_sha256=digest,
                size_bytes=path.stat().st_size,
                source_uri=f"content:sha256/{digest}",
                license_key="project_internal_free_research",
                provenance_status="verified",
                usage_scope="local_research",
                metadata={
                    "contract": _CONTRACT,
                    "transfer_manifest_sha256": spec.plan.transfer_manifest_sha256,
                    "source_facts_not_direct_copy": True,
                },
            )
        )
    return ExternalImportManifestService(engine).publish(
        ExternalImportManifestSpec(
            manifest_key="v022_green_security_lifecycle_source_facts",
            version_number=1,
            source_project_key="migratory_bird_clean_green",
            source_release_key=spec.plan.transfer_manifest_sha256,
            objects=tuple(objects),
            created_by=spec.created_by,
        )
    )


def _leg(row: dict[str, str]) -> SecuritySettlementLegSpec:
    return SecuritySettlementLegSpec(
        leg_kind=row["leg_kind"],  # type: ignore[arg-type]
        target_security_id=uuid.UUID(row["target_security_id"])
        if row["target_security_id"]
        else None,
        quantity_per_source_share=Decimal(row["quantity_per_source_share"])
        if row["quantity_per_source_share"]
        else None,
        cash_amount_per_source_share=Decimal(row["cash_amount_per_source_share"])
        if row["cash_amount_per_source_share"]
        else None,
        currency=row["currency"] or None,
        valuation_policy=row["valuation_policy"],  # type: ignore[arg-type]
    )


def _event_spec(
    row: dict[str, str],
    legs: tuple[SecuritySettlementLegSpec, ...],
    evidence_artifact_id: uuid.UUID,
    created_by: str,
) -> SecurityLifecycleEventSpec:
    document = json.loads(row["event_document"])
    details = dict(document.get("details", {}))
    details.update(
        {
            "green_republication_contract": _CONTRACT,
            "transferred_event_fingerprint": row["event_fingerprint"],
            "source_evidence_scope": "verified_transfer_lifecycle_source_facts",
        }
    )
    return SecurityLifecycleEventSpec(
        security_id=uuid.UUID(row["security_id"]),
        event_key=row["event_key"],
        version_number=int(row["version_number"]),
        event_type=row["event_type"],  # type: ignore[arg-type]
        event_status=row["event_status"],  # type: ignore[arg-type]
        announced_at=datetime.fromisoformat(row["announced_at"]),
        effective_session=date.fromisoformat(row["effective_session"]),
        last_trading_session=date.fromisoformat(row["last_trading_session"])
        if row["last_trading_session"]
        else None,
        settlement_session=date.fromisoformat(row["settlement_session"])
        if row["settlement_session"]
        else None,
        selectable_after=row["selectable_after"].casefold() in {"t", "true", "1"},
        tradable_after=row["tradable_after"].casefold() in {"t", "true", "1"},
        valuation_state_after=row["valuation_state_after"],  # type: ignore[arg-type]
        evidence=(LifecycleEvidenceRef(evidence_artifact_id, "corporate_action_terms"),),
        settlement_legs=legs,
        created_by=created_by,
        details=details,
    )


def publish_green_baseline_lifecycle(
    engine: Engine, spec: GreenBaselineLifecycleSpec
) -> GreenBaselineLifecyclePublication:
    expected = build_green_baseline_import_plan(spec.transfer_root)
    if expected.to_dict() != spec.plan.to_dict():
        raise ValueError("green baseline import plan is stale or does not match the transfer")
    source_manifest = _source_manifest(engine, spec)
    event_rows = sorted(_rows(spec.transfer_root, _EVENT_TABLE), key=lambda item: item["event_key"])
    leg_rows = _rows(spec.transfer_root, _LEG_TABLE)
    artifact_ids: list[str] = []
    reused = 0
    for event_row in event_rows:
        source_event_id = event_row["security_lifecycle_event_id"]
        selected_legs = tuple(
            _leg(item)
            for item in sorted(
                (
                    item
                    for item in leg_rows
                    if item["security_lifecycle_event_id"] == source_event_id
                ),
                key=lambda item: int(item["ordinal"]),
            )
        )
        evidence_document = {
            "contract": _CONTRACT,
            "event_key": event_row["event_key"],
            "source_event_document": json.loads(event_row["event_document"]),
            "source_event_fingerprint": event_row["event_fingerprint"],
            "source_settlement_legs": [
                json.loads(item["leg_document"])
                for item in leg_rows
                if item["security_lifecycle_event_id"] == source_event_id
            ],
        }
        evidence = ArtifactService(engine).publish(
            artifact_type="v022_green_lifecycle_source_evidence",
            artifact_key=f"v022_green_lifecycle_source__{event_row['event_key']}",
            version_number=1,
            semantic_payload=evidence_document,
            content_payload=evidence_document,
            dependencies=(
                DependencyInput(source_manifest.artifact_id, "external_import_manifest", 0),
            ),
            reason=f"publish clean-green lifecycle source facts {event_row['event_key']}",
        )
        event = SecurityLifecycleEventService(engine).publish(
            _event_spec(
                event_row,
                selected_legs,
                evidence.artifact_id,
                spec.created_by,
            )
        )
        artifact_ids.append(str(event.artifact_id))
        reused += int(evidence.reused and event.reused)
    return GreenBaselineLifecyclePublication(
        contract=_CONTRACT,
        source_manifest_artifact_id=str(source_manifest.artifact_id),
        event_artifact_ids=tuple(artifact_ids),
        event_count=len(event_rows),
        settlement_leg_count=len(leg_rows),
        reused_event_count=reused,
    )
