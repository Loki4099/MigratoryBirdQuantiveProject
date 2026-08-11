from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.config.settings import get_settings
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.experiment.result_payload import (
    PAYLOAD_SCHEMA_VERSION,
    PAYLOAD_STORAGE_FORMAT,
    CellResultPayloadStore,
)
from style_rotation.product.evidence import load_product_qualification_evidence

DEFAULT_CACHE_TTL = timedelta(days=14)
DEFAULT_CACHE_QUOTA_BYTES = 10 * 1024**3
DEFAULT_RECENT_ACCESS_GRACE = timedelta(minutes=15)
DEFAULT_ARCHIVE_SHARD_BYTES = 256 * 1024**2
DEFAULT_CELL_PAYLOAD_GRACE = timedelta(days=7)
_CELL_PAYLOAD_FILE_PATTERN = re.compile(r"^([0-9a-f]{64})[.]parquet$")
_CELL_PAYLOAD_PENDING_PATTERN = re.compile(
    r"^[.]([0-9a-f]{64})[.]([0-9a-f]{32})[.]pending$"
)

# These are experiment-owned publication roots, not canonical market/catalog
# definitions.  Exact Product/current closures are classified first, so only
# their superseded, unreferenced remainder is eligible for cold archival.
_EXPERIMENT_ARCHIVE_ARTIFACT_TYPES = frozenset(
    {
        "benchmark_target_path",
        "comparison_cohort_version",
        "compiled_research_spec",
        "compiled_strategy_version",
        "experiment_result",
        "experiment_specification",
        "experiment_suite",
        "gross_portfolio_path",
        "interval_performance_result",
        "net_cost_path",
        "portfolio_cell_specification",
        "predictive_cell_specification",
        "research_suite",
        "strategy_product_version",
        "strategy_target_path",
        "v021_cell_result",
        "v021_execution_policy",
    }
)


class ManifestRetentionClass(StrEnum):
    PRODUCT_PIN = "product_pin"
    CURRENT_EXPERIMENT = "current_experiment"
    RECLAIMABLE_OLD_EXPERIMENT = "reclaimable_old_experiment"
    SHARED_OR_UNCLASSIFIED = "shared_or_unclassified"


class CacheAction(StrEnum):
    KEEP = "keep"
    DELETE = "delete"


class CellPayloadAction(StrEnum):
    KEEP = "keep"
    QUARANTINE = "quarantine"


@dataclass(frozen=True, slots=True)
class CellPayloadMaintenancePolicy:
    grace_period: timedelta = DEFAULT_CELL_PAYLOAD_GRACE

    def __post_init__(self) -> None:
        if self.grace_period <= timedelta(0):
            raise ValueError("Cell payload grace period must be positive")


@dataclass(frozen=True, slots=True)
class CellPayloadFileSnapshot:
    content_hash: str
    storage_path: str
    modified_at: datetime
    modified_at_ns: int
    byte_size: int
    file_sha256: str
    product_referenced: bool = False
    active_referenced: bool = False
    current_referenced: bool = False
    database_referenced: bool = False
    path_valid: bool = True


@dataclass(frozen=True, slots=True)
class CellPayloadDecision:
    content_hash: str
    action: CellPayloadAction
    reason: str
    storage_path: str
    modified_at: datetime
    modified_at_ns: int
    byte_size: int
    file_sha256: str


@dataclass(frozen=True, slots=True)
class CellPayloadPendingMarkerSnapshot:
    marker_name: str
    content_hash: str
    owner_work_item_id: uuid.UUID | None
    storage_path: str
    modified_at: datetime
    modified_at_ns: int
    byte_size: int
    file_sha256: str


@dataclass(frozen=True, slots=True)
class CellPayloadPendingMarkerDecision:
    marker_name: str
    content_hash: str
    owner_work_item_id: uuid.UUID | None
    action: CellPayloadAction
    reason: str
    storage_path: str
    modified_at: datetime
    modified_at_ns: int
    byte_size: int
    file_sha256: str


@dataclass(frozen=True, slots=True)
class CellPayloadMaintenancePlan:
    generated_at: datetime
    plan_id: str
    root_directory: str
    policy: CellPayloadMaintenancePolicy
    decisions: tuple[CellPayloadDecision, ...]
    pending_markers: tuple[CellPayloadPendingMarkerDecision, ...] = ()
    blocked_reasons: tuple[str, ...] = ()

    @property
    def confirmation_token(self) -> str:
        return f"cell-payload-maintenance:{self.plan_id}"

    @property
    def quarantine_hashes(self) -> tuple[str, ...]:
        if self.blocked_reasons:
            return ()
        return tuple(
            item.content_hash
            for item in self.decisions
            if item.action == CellPayloadAction.QUARANTINE
        )

    def summary(self) -> dict[str, Any]:
        quarantine = [
            item for item in self.decisions if item.action == CellPayloadAction.QUARANTINE
        ]
        return {
            "generated_at": self.generated_at.isoformat(),
            "plan_id": self.plan_id,
            "root_directory": self.root_directory,
            "grace_period_seconds": int(self.policy.grace_period.total_seconds()),
            "file_count": len(self.decisions),
            "quarantine_count": len(quarantine) if not self.blocked_reasons else 0,
            "quarantine_marker_count": (
                sum(
                    item.action == CellPayloadAction.QUARANTINE
                    for item in self.pending_markers
                )
                if not self.blocked_reasons
                else 0
            ),
            "quarantine_bytes": (
                sum(item.byte_size for item in quarantine) if not self.blocked_reasons else 0
            ),
            "blocked_reasons": list(self.blocked_reasons),
            "confirmation_token": self.confirmation_token,
            "decisions": [
                {
                    "content_hash": item.content_hash,
                    "action": item.action.value,
                    "reason": item.reason,
                    "modified_at": item.modified_at.isoformat(),
                    "modified_at_ns": item.modified_at_ns,
                    "byte_size": item.byte_size,
                }
                for item in self.decisions
            ],
            "pending_markers": [
                {
                    "marker_name": item.marker_name,
                    "content_hash": item.content_hash,
                    "owner_work_item_id": (
                        str(item.owner_work_item_id)
                        if item.owner_work_item_id is not None
                        else None
                    ),
                    "action": item.action.value,
                    "reason": item.reason,
                    "modified_at": item.modified_at.isoformat(),
                    "modified_at_ns": item.modified_at_ns,
                    "byte_size": item.byte_size,
                }
                for item in self.pending_markers
            ],
        }

    def to_document(self) -> dict[str, Any]:
        """Return the complete, hash-bound plan used for cross-process execution."""

        return {
            "schema_version": "cell_payload_maintenance_plan_v1",
            "generated_at": self.generated_at.isoformat(),
            "plan_id": self.plan_id,
            "root_directory": self.root_directory,
            "grace_period_seconds": int(self.policy.grace_period.total_seconds()),
            "blocked_reasons": list(self.blocked_reasons),
            "decisions": [
                {
                    "content_hash": item.content_hash,
                    "action": item.action.value,
                    "reason": item.reason,
                    "storage_path": item.storage_path,
                    "modified_at": item.modified_at.isoformat(),
                    "modified_at_ns": item.modified_at_ns,
                    "byte_size": item.byte_size,
                    "file_sha256": item.file_sha256,
                }
                for item in self.decisions
            ],
            "pending_markers": [
                {
                    "marker_name": item.marker_name,
                    "content_hash": item.content_hash,
                    "owner_work_item_id": (
                        str(item.owner_work_item_id)
                        if item.owner_work_item_id is not None
                        else None
                    ),
                    "action": item.action.value,
                    "reason": item.reason,
                    "storage_path": item.storage_path,
                    "modified_at": item.modified_at.isoformat(),
                    "modified_at_ns": item.modified_at_ns,
                    "byte_size": item.byte_size,
                    "file_sha256": item.file_sha256,
                }
                for item in self.pending_markers
            ],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> CellPayloadMaintenancePlan:
        if document.get("schema_version") != "cell_payload_maintenance_plan_v1":
            raise ValueError("Unsupported Cell payload maintenance plan schema")
        try:
            generated_at = datetime.fromisoformat(str(document["generated_at"]))
            policy = CellPayloadMaintenancePolicy(
                grace_period=timedelta(seconds=int(document["grace_period_seconds"]))
            )
            decisions = tuple(
                CellPayloadDecision(
                    content_hash=str(item["content_hash"]),
                    action=CellPayloadAction(str(item["action"])),
                    reason=str(item["reason"]),
                    storage_path=str(item["storage_path"]),
                    modified_at=datetime.fromisoformat(str(item["modified_at"])),
                    modified_at_ns=int(item["modified_at_ns"]),
                    byte_size=int(item["byte_size"]),
                    file_sha256=str(item["file_sha256"]),
                )
                for item in document["decisions"]
            )
            pending_markers = tuple(
                CellPayloadPendingMarkerDecision(
                    marker_name=str(item["marker_name"]),
                    content_hash=str(item["content_hash"]),
                    owner_work_item_id=(
                        uuid.UUID(str(item["owner_work_item_id"]))
                        if item.get("owner_work_item_id") is not None
                        else None
                    ),
                    action=CellPayloadAction(str(item["action"])),
                    reason=str(item["reason"]),
                    storage_path=str(item["storage_path"]),
                    modified_at=datetime.fromisoformat(str(item["modified_at"])),
                    modified_at_ns=int(item["modified_at_ns"]),
                    byte_size=int(item["byte_size"]),
                    file_sha256=str(item["file_sha256"]),
                )
                for item in document.get("pending_markers", ())
            )
            plan = cls(
                generated_at=generated_at,
                plan_id=str(document["plan_id"]),
                root_directory=str(document["root_directory"]),
                policy=policy,
                decisions=decisions,
                pending_markers=pending_markers,
                blocked_reasons=tuple(str(item) for item in document["blocked_reasons"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Malformed Cell payload maintenance plan") from exc
        if (
            generated_at.tzinfo is None
            or any(item.modified_at.tzinfo is None for item in decisions)
            or any(item.modified_at.tzinfo is None for item in pending_markers)
        ):
            raise ValueError("Cell payload plan timestamps must be timezone-aware")
        expected = _cell_payload_plan_id(
            generated_at=plan.generated_at,
            root_directory=plan.root_directory,
            policy=plan.policy,
            decisions=plan.decisions,
            pending_markers=plan.pending_markers,
            blocked_reasons=plan.blocked_reasons,
        )
        if plan.plan_id != expected:
            raise ValueError("Cell payload maintenance plan hash mismatch")
        return plan


@dataclass(frozen=True, slots=True)
class CellPayloadQuarantineItem:
    kind: str
    content_hash: str
    source_path: str
    quarantine_path: str
    byte_size: int
    file_sha256: str


@dataclass(frozen=True, slots=True)
class CellPayloadQuarantineReceipt:
    receipt_id: str
    plan_id: str
    quarantine_directory: str
    executed_at: datetime
    items: tuple[CellPayloadQuarantineItem, ...]

    @property
    def rollback_token(self) -> str:
        return f"cell-payload-rollback:{self.receipt_id}"

    def summary(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "receipt_id": self.receipt_id,
            "quarantine_directory": self.quarantine_directory,
            "executed_at": self.executed_at.isoformat(),
            "file_count": len(self.items),
            "byte_size": sum(item.byte_size for item in self.items),
            "rollback_token": self.rollback_token,
            "items": [asdict(item) for item in self.items],
        }

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": "cell_payload_quarantine_receipt_v1",
            "receipt_id": self.receipt_id,
            "plan_id": self.plan_id,
            "quarantine_directory": self.quarantine_directory,
            "executed_at": self.executed_at.isoformat(),
            "items": [asdict(item) for item in self.items],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> CellPayloadQuarantineReceipt:
        if document.get("schema_version") != "cell_payload_quarantine_receipt_v1":
            raise ValueError("Unsupported Cell payload quarantine receipt schema")
        try:
            receipt = cls(
                receipt_id=str(document["receipt_id"]),
                plan_id=str(document["plan_id"]),
                quarantine_directory=str(document["quarantine_directory"]),
                executed_at=datetime.fromisoformat(str(document["executed_at"])),
                items=tuple(
                    CellPayloadQuarantineItem(
                        kind=str(item["kind"]),
                        content_hash=str(item["content_hash"]),
                        source_path=str(item["source_path"]),
                        quarantine_path=str(item["quarantine_path"]),
                        byte_size=int(item["byte_size"]),
                        file_sha256=str(item["file_sha256"]),
                    )
                    for item in document["items"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Malformed Cell payload quarantine receipt") from exc
        if receipt.executed_at.tzinfo is None:
            raise ValueError("Cell payload receipt timestamp must be timezone-aware")
        expected = _cell_payload_receipt_id(
            plan_id=receipt.plan_id,
            quarantine_directory=receipt.quarantine_directory,
            executed_at=receipt.executed_at,
            items=receipt.items,
        )
        if receipt.receipt_id != expected:
            raise ValueError("Cell payload quarantine receipt hash mismatch")
        return receipt


@dataclass(frozen=True, slots=True)
class CacheMaintenancePolicy:
    ttl: timedelta = DEFAULT_CACHE_TTL
    quota_bytes: int = DEFAULT_CACHE_QUOTA_BYTES
    recent_access_grace: timedelta = DEFAULT_RECENT_ACCESS_GRACE

    def __post_init__(self) -> None:
        if self.ttl <= timedelta(0):
            raise ValueError("Cache TTL must be positive")
        if self.quota_bytes < 0:
            raise ValueError("Cache quota cannot be negative")
        if self.recent_access_grace < timedelta(0):
            raise ValueError("Recent-access grace cannot be negative")


@dataclass(frozen=True, slots=True)
class CacheEntrySnapshot:
    cache_key: str
    storage_uri: str
    created_at: datetime
    last_accessed_at: datetime
    parquet_bytes: int
    manifest_bytes: int
    product_referenced: bool = False
    active_referenced: bool = False
    path_valid: bool = True

    @property
    def total_bytes(self) -> int:
        return self.parquet_bytes + self.manifest_bytes


@dataclass(frozen=True, slots=True)
class CacheDecision:
    cache_key: str
    action: CacheAction
    reason: str
    bytes: int
    storage_uri: str
    last_accessed_at: datetime


@dataclass(frozen=True, slots=True)
class CacheMaintenancePlan:
    policy: CacheMaintenancePolicy
    decisions: tuple[CacheDecision, ...]
    before_bytes: int
    after_bytes: int
    blocked_reasons: tuple[str, ...] = ()

    @property
    def reclaim_bytes(self) -> int:
        return self.before_bytes - self.after_bytes

    @property
    def delete_keys(self) -> tuple[str, ...]:
        return tuple(
            item.cache_key for item in self.decisions if item.action == CacheAction.DELETE
        )


@dataclass(frozen=True, slots=True)
class LineageManifestSnapshot:
    lineage_manifest_id: uuid.UUID
    root_artifact_id: uuid.UUID
    artifact_type: str
    artifact_key: str
    semantic_fingerprint: str
    content_hash: str
    manifest_hash: str
    canonical_version: str
    created_at: datetime
    estimated_bytes: int
    retention_class: ManifestRetentionClass


@dataclass(frozen=True, slots=True)
class StorageMaintenancePlan:
    generated_at: datetime
    plan_id: str
    product_pinned_artifact_count: int
    current_suite_ids: tuple[uuid.UUID, ...]
    manifests: tuple[LineageManifestSnapshot, ...]
    cache: CacheMaintenancePlan
    blocked_reasons: tuple[str, ...] = ()

    @property
    def archive_manifest_ids(self) -> tuple[uuid.UUID, ...]:
        if self.blocked_reasons:
            return ()
        return tuple(
            item.lineage_manifest_id
            for item in self.manifests
            if item.retention_class == ManifestRetentionClass.RECLAIMABLE_OLD_EXPERIMENT
        )

    @property
    def confirmation_token(self) -> str:
        return f"storage-maintenance:{self.plan_id}"

    def summary(self) -> dict[str, Any]:
        classes: dict[str, dict[str, int]] = {}
        for item in self.manifests:
            bucket = classes.setdefault(item.retention_class.value, {"count": 0, "bytes": 0})
            bucket["count"] += 1
            bucket["bytes"] += item.estimated_bytes
        return {
            "generated_at": self.generated_at.isoformat(),
            "plan_id": self.plan_id,
            "product_pinned_artifact_count": self.product_pinned_artifact_count,
            "current_suite_ids": [str(value) for value in self.current_suite_ids],
            "manifest_classes": classes,
            "cache": {
                "before_bytes": self.cache.before_bytes,
                "after_bytes": self.cache.after_bytes,
                "reclaim_bytes": self.cache.reclaim_bytes,
                "delete_count": len(self.cache.delete_keys),
                "blocked_reasons": list(self.cache.blocked_reasons),
            },
            "blocked_reasons": list(self.blocked_reasons),
            "confirmation_token": self.confirmation_token,
        }


@dataclass(frozen=True, slots=True)
class ArtifactTombstone:
    lineage_manifest_id: uuid.UUID
    root_artifact_id: uuid.UUID
    artifact_type: str
    artifact_key: str
    semantic_fingerprint: str
    content_hash: str
    manifest_hash: str
    canonical_version: str
    created_at: datetime
    archive_uri: str
    archive_sha256: str


@dataclass(frozen=True, slots=True)
class LineageArchiveReceipt:
    plan_id: str
    archive_uri: str
    archive_sha256: str
    receipt_uri: str
    manifest_count: int
    archived_at: datetime
    tombstones: tuple[ArtifactTombstone, ...]
    database_compaction_executed: bool = False


@dataclass(slots=True)
class _ProductPinInventory:
    closure_roots: set[uuid.UUID] = field(default_factory=set)
    direct_roots: set[uuid.UUID] = field(default_factory=set)
    qualification_roots: set[uuid.UUID] = field(default_factory=set)
    source_suite_ids: set[uuid.UUID] = field(default_factory=set)
    complete: bool = True
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _CellPayloadReferenceMarks:
    product: set[str] = field(default_factory=set)
    active: set[str] = field(default_factory=set)
    current: set[str] = field(default_factory=set)
    referenced: set[str] = field(default_factory=set)
    active_work_items: set[uuid.UUID] = field(default_factory=set)
    complete: bool = True
    reasons: list[str] = field(default_factory=list)


def plan_cache_retention(
    entries: Sequence[CacheEntrySnapshot],
    *,
    policy: CacheMaintenancePolicy,
    now: datetime,
    inventory_complete: bool = True,
) -> CacheMaintenancePlan:
    """Apply pin -> TTL -> LRU/quota in that order.

    Missing or malformed paths and an incomplete reference inventory fail closed.
    The planner is pure; deletion requires a separate, explicitly confirmed call.
    """

    if now.tzinfo is None:
        raise ValueError("Cache planning time must be timezone-aware")
    blocked: list[str] = []
    if not inventory_complete:
        blocked.append("cache_reference_inventory_incomplete")
    actions: dict[str, CacheDecision] = {}
    deletable: list[CacheEntrySnapshot] = []
    total = sum(item.total_bytes for item in entries)
    for item in entries:
        pinned_reason: str | None = None
        if not inventory_complete:
            pinned_reason = "fail_closed_reference_inventory"
        elif item.product_referenced:
            pinned_reason = "product_reference"
        elif item.active_referenced:
            pinned_reason = "active_work_reference"
        elif not item.path_valid:
            pinned_reason = "invalid_storage_path_fail_closed"
        elif item.last_accessed_at > now:
            pinned_reason = "future_access_timestamp_fail_closed"
        elif now - item.last_accessed_at <= policy.recent_access_grace:
            pinned_reason = "recently_accessed"
        if pinned_reason is not None:
            actions[item.cache_key] = CacheDecision(
                item.cache_key,
                CacheAction.KEEP,
                pinned_reason,
                item.total_bytes,
                item.storage_uri,
                item.last_accessed_at,
            )
        else:
            deletable.append(item)

    expired = sorted(
        (item for item in deletable if now - item.last_accessed_at >= policy.ttl),
        key=lambda item: (item.last_accessed_at, item.cache_key),
    )
    deleted: set[str] = set()
    after = total
    for item in expired:
        deleted.add(item.cache_key)
        after -= item.total_bytes
        actions[item.cache_key] = CacheDecision(
            item.cache_key,
            CacheAction.DELETE,
            "ttl_expired",
            item.total_bytes,
            item.storage_uri,
            item.last_accessed_at,
        )

    quota_candidates = sorted(
        (item for item in deletable if item.cache_key not in deleted),
        key=lambda item: (item.last_accessed_at, item.cache_key),
    )
    for item in quota_candidates:
        if after <= policy.quota_bytes:
            break
        deleted.add(item.cache_key)
        after -= item.total_bytes
        actions[item.cache_key] = CacheDecision(
            item.cache_key,
            CacheAction.DELETE,
            "lru_quota",
            item.total_bytes,
            item.storage_uri,
            item.last_accessed_at,
        )

    for item in deletable:
        if item.cache_key not in actions:
            actions[item.cache_key] = CacheDecision(
                item.cache_key,
                CacheAction.KEEP,
                "within_ttl_and_quota",
                item.total_bytes,
                item.storage_uri,
                item.last_accessed_at,
            )
    if after > policy.quota_bytes:
        blocked.append("quota_exceeded_by_pinned_or_fail_closed_entries")
    return CacheMaintenancePlan(
        policy=policy,
        decisions=tuple(actions[key] for key in sorted(actions)),
        before_bytes=total,
        after_bytes=after,
        blocked_reasons=tuple(blocked),
    )


def plan_cell_payload_retention(
    entries: Sequence[CellPayloadFileSnapshot],
    *,
    pending_markers: Sequence[CellPayloadPendingMarkerSnapshot] = (),
    referenced_hashes: set[str] | None = None,
    active_work_item_ids: set[uuid.UUID] | None = None,
    root_directory: Path,
    policy: CellPayloadMaintenancePolicy,
    now: datetime,
    inventory_complete: bool = True,
    blocked_reasons: Sequence[str] = (),
) -> CellPayloadMaintenancePlan:
    """Mark referenced files and quarantine only old, unreferenced strict payloads."""

    if now.tzinfo is None:
        raise ValueError("Cell payload planning time must be timezone-aware")
    blocked = list(blocked_reasons)
    if not inventory_complete:
        blocked.append("cell_payload_reference_inventory_incomplete")
    decisions: list[CellPayloadDecision] = []
    for item in sorted(entries, key=lambda value: value.content_hash):
        if not inventory_complete:
            action, reason = CellPayloadAction.KEEP, "fail_closed_reference_inventory"
        elif not item.path_valid or _CELL_PAYLOAD_FILE_PATTERN.fullmatch(
            Path(item.storage_path).name
        ) is None:
            action, reason = CellPayloadAction.KEEP, "invalid_storage_path_fail_closed"
        elif item.product_referenced:
            action, reason = CellPayloadAction.KEEP, "product_reference"
        elif item.active_referenced:
            action, reason = CellPayloadAction.KEEP, "active_work_reference"
        elif item.current_referenced:
            action, reason = CellPayloadAction.KEEP, "current_suite_reference"
        elif item.database_referenced:
            action, reason = CellPayloadAction.KEEP, "database_reference"
        elif item.modified_at > now:
            action, reason = CellPayloadAction.KEEP, "future_mtime_fail_closed"
        elif now - item.modified_at < policy.grace_period:
            action, reason = CellPayloadAction.KEEP, "within_grace_period"
        else:
            action, reason = CellPayloadAction.QUARANTINE, "unreferenced_grace_expired"
        decisions.append(
            CellPayloadDecision(
                content_hash=item.content_hash,
                action=action,
                reason=reason,
                storage_path=item.storage_path,
                modified_at=item.modified_at,
                modified_at_ns=item.modified_at_ns,
                byte_size=item.byte_size,
                file_sha256=item.file_sha256,
            )
        )
    referenced = referenced_hashes or set()
    active_work_items = active_work_item_ids or set()
    marker_decisions: list[CellPayloadPendingMarkerDecision] = []
    for marker in sorted(pending_markers, key=lambda value: value.marker_name):
        if not inventory_complete:
            action, reason = CellPayloadAction.KEEP, "fail_closed_reference_inventory"
        elif marker.modified_at > now:
            action, reason = CellPayloadAction.KEEP, "future_mtime_fail_closed"
        elif now - marker.modified_at < policy.grace_period:
            action, reason = CellPayloadAction.KEEP, "publication_in_progress"
        elif marker.owner_work_item_id is None:
            action, reason = CellPayloadAction.KEEP, "marker_owner_unknown_fail_closed"
        elif marker.owner_work_item_id in active_work_items:
            action, reason = CellPayloadAction.KEEP, "active_work_publication"
        elif marker.content_hash in referenced:
            action, reason = CellPayloadAction.QUARANTINE, "stale_marker_reference_committed"
        else:
            action, reason = CellPayloadAction.QUARANTINE, "stale_marker_unreferenced"
        marker_decisions.append(
            CellPayloadPendingMarkerDecision(
                marker_name=marker.marker_name,
                content_hash=marker.content_hash,
                owner_work_item_id=marker.owner_work_item_id,
                action=action,
                reason=reason,
                storage_path=marker.storage_path,
                modified_at=marker.modified_at,
                modified_at_ns=marker.modified_at_ns,
                byte_size=marker.byte_size,
                file_sha256=marker.file_sha256,
            )
        )
    unique_blocked = tuple(dict.fromkeys(blocked))
    root = root_directory.resolve().as_posix()
    plan_id = _cell_payload_plan_id(
        generated_at=now,
        root_directory=root,
        policy=policy,
        decisions=tuple(decisions),
        pending_markers=tuple(marker_decisions),
        blocked_reasons=unique_blocked,
    )
    return CellPayloadMaintenancePlan(
        generated_at=now,
        plan_id=plan_id,
        root_directory=root,
        policy=policy,
        decisions=tuple(decisions),
        pending_markers=tuple(marker_decisions),
        blocked_reasons=unique_blocked,
    )


def _cell_payload_plan_id(
    *,
    generated_at: datetime,
    root_directory: str,
    policy: CellPayloadMaintenancePolicy,
    decisions: Sequence[CellPayloadDecision],
    pending_markers: Sequence[CellPayloadPendingMarkerDecision],
    blocked_reasons: Sequence[str],
) -> str:
    return sha256_hexdigest(
        {
            "schema_version": "cell_payload_maintenance_plan_v1",
            "generated_at": generated_at.isoformat(),
            "root_directory": root_directory,
            "grace_period_seconds": int(policy.grace_period.total_seconds()),
            "blocked_reasons": list(blocked_reasons),
            "decisions": [
                {
                    "content_hash": item.content_hash,
                    "action": item.action.value,
                    "reason": item.reason,
                    "storage_path": item.storage_path,
                    "modified_at": item.modified_at.isoformat(),
                    "modified_at_ns": item.modified_at_ns,
                    "byte_size": item.byte_size,
                    "file_sha256": item.file_sha256,
                }
                for item in decisions
            ],
            "pending_markers": [
                {
                    "marker_name": item.marker_name,
                    "content_hash": item.content_hash,
                    "owner_work_item_id": (
                        str(item.owner_work_item_id)
                        if item.owner_work_item_id is not None
                        else None
                    ),
                    "action": item.action.value,
                    "reason": item.reason,
                    "storage_path": item.storage_path,
                    "modified_at": item.modified_at.isoformat(),
                    "modified_at_ns": item.modified_at_ns,
                    "byte_size": item.byte_size,
                    "file_sha256": item.file_sha256,
                }
                for item in pending_markers
            ],
        }
    )


def _cell_payload_receipt_id(
    *,
    plan_id: str,
    quarantine_directory: str,
    executed_at: datetime,
    items: Sequence[CellPayloadQuarantineItem],
) -> str:
    return sha256_hexdigest(
        {
            "schema_version": "cell_payload_quarantine_receipt_v1",
            "plan_id": plan_id,
            "quarantine_directory": quarantine_directory,
            "executed_at": executed_at.isoformat(),
            "items": [asdict(item) for item in items],
        }
    )


class StorageMaintenanceService:
    """Read-only planning plus explicitly gated cache/archive execution.

    This service deliberately does not delete immutable ``lineage_manifest`` rows.
    Their database trigger is an important audit boundary.  ``archive_lineage``
    writes and restores/verifies a Zstd Parquet archive and a compact tombstone
    receipt.  A later dedicated migration may replace archived rows only after
    the receipt has passed an external restore test.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        cache_directory: Path | None = None,
        archive_directory: Path | None = None,
        cell_payload_directory: Path | None = None,
    ) -> None:
        self._engine = engine
        self._cache_directory = (
            cache_directory
            or Path(
                os.environ.get(
                    "STYLE_ROTATION_RESEARCH_CACHE_DIRECTORY",
                    "artifacts/research_materialization_cache",
                )
            )
        ).resolve()
        self._archive_directory = (
            archive_directory
            or Path(
                os.environ.get(
                    "STYLE_ROTATION_LINEAGE_ARCHIVE_DIRECTORY",
                    "artifacts/cold_archive/lineage",
                )
            )
        ).resolve()
        self._cell_payload_directory = (
            cell_payload_directory or Path(get_settings().cell_result_directory)
        ).resolve()
        self._cell_payload_store = CellResultPayloadStore(self._cell_payload_directory)

    def dry_run(
        self,
        *,
        policy: CacheMaintenancePolicy | None = None,
        retain_suite_ids: Iterable[uuid.UUID] = (),
        now: datetime | None = None,
    ) -> StorageMaintenancePlan:
        generated_at = now or datetime.now(UTC)
        if generated_at.tzinfo is None:
            raise ValueError("Storage planning time must be timezone-aware")
        blocked: list[str] = []
        with self._engine.connect() as connection:
            pins = self._product_pin_inventory(connection)
            if not pins.complete:
                blocked.extend(pins.reasons or ["product_pin_inventory_incomplete"])
            closure = self._artifact_closure(
                connection,
                pins.closure_roots,
                excluded_source_suite_roots=pins.qualification_roots,
            )
            product_pins = closure | pins.direct_roots
            current_suites = self._current_suite_ids(
                connection,
                explicit=set(retain_suite_ids),
            )
            current_roots = self._suite_artifacts(connection, current_suites)
            current_roots.update(
                connection.execute(text("""
                    SELECT last_compiled_artifact_id
                    FROM workspace.research_draft
                    WHERE last_compiled_artifact_id IS NOT NULL
                """)).scalars()
            )
            current_artifacts = self._artifact_closure(
                connection,
                current_roots,
                excluded_source_suite_roots=set(),
            )
            old_suite_ids = set(
                connection.execute(text("SELECT research_suite_id FROM experiment.research_suite"))
                .scalars()
                .all()
            ) - current_suites
            old_artifacts = self._suite_artifacts(connection, old_suite_ids)
            old_artifacts.update(self._orphan_experiment_artifacts(connection))
            manifests = self._manifest_inventory(
                connection,
                product_pins=product_pins,
                current_artifacts=current_artifacts,
                old_artifacts=old_artifacts,
                reclaim_blocked=not pins.complete,
            )
            cache_entries, cache_inventory_complete = self._cache_inventory(connection)
        cache_plan = plan_cache_retention(
            cache_entries,
            policy=policy or CacheMaintenancePolicy(),
            now=generated_at,
            inventory_complete=cache_inventory_complete,
        )
        identity = {
            "generated_at": generated_at.isoformat(),
            "product_pins": sorted(str(value) for value in product_pins),
            "current_suites": sorted(str(value) for value in current_suites),
            "manifests": [
                {
                    "id": str(item.lineage_manifest_id),
                    "hash": item.manifest_hash,
                    "class": item.retention_class.value,
                }
                for item in manifests
            ],
            "cache": [
                {
                    "key": item.cache_key,
                    "action": item.action.value,
                    "accessed": item.last_accessed_at.isoformat(),
                }
                for item in cache_plan.decisions
            ],
            "blocked": blocked,
        }
        return StorageMaintenancePlan(
            generated_at=generated_at,
            plan_id=sha256_hexdigest(identity),
            product_pinned_artifact_count=len(product_pins),
            current_suite_ids=tuple(sorted(current_suites, key=str)),
            manifests=tuple(manifests),
            cache=cache_plan,
            blocked_reasons=tuple(dict.fromkeys(blocked)),
        )

    def dry_run_cell_payloads(
        self,
        *,
        policy: CellPayloadMaintenancePolicy | None = None,
        now: datetime | None = None,
    ) -> CellPayloadMaintenancePlan:
        """Build a read-only, hash-bound quarantine plan for orphan payload files."""

        generated_at = now or datetime.now(UTC)
        if generated_at.tzinfo is None:
            raise ValueError("Cell payload planning time must be timezone-aware")
        blocked: list[str] = []
        marks = _CellPayloadReferenceMarks()
        files: tuple[CellPayloadFileSnapshot, ...] = ()
        markers: tuple[CellPayloadPendingMarkerSnapshot, ...] = ()
        effective_policy = policy or CellPayloadMaintenancePolicy()
        with self._cell_payload_store.directory_lock():
            try:
                files = self._cell_payload_file_inventory()
                markers = self._cell_payload_pending_marker_inventory()
            except Exception as exc:
                blocked.append(f"cell_payload_file_inventory_failed:{type(exc).__name__}")
            try:
                with self._engine.connect() as connection:
                    marks = self._cell_payload_reference_marks(connection)
            except Exception as exc:
                marks.complete = False
                marks.reasons.append(
                    f"cell_payload_reference_query_failed:{type(exc).__name__}"
                )
            marks.active.update(
                marker.content_hash
                for marker in markers
                if marker.modified_at > generated_at
                or generated_at - marker.modified_at < effective_policy.grace_period
                or marker.owner_work_item_id in marks.active_work_items
            )
        blocked.extend(marks.reasons)
        if not marks.complete:
            blocked.append("cell_payload_reference_inventory_incomplete")
        available = {item.content_hash for item in files}
        missing = marks.referenced.difference(available)
        if missing:
            blocked.append("referenced_cell_payload_files_missing")
        marked = tuple(
            replace(
                item,
                product_referenced=item.content_hash in marks.product,
                active_referenced=item.content_hash in marks.active,
                current_referenced=item.content_hash in marks.current,
                database_referenced=item.content_hash in marks.referenced,
            )
            for item in files
        )
        return plan_cell_payload_retention(
            marked,
            pending_markers=markers,
            referenced_hashes=marks.referenced,
            active_work_item_ids=marks.active_work_items,
            root_directory=self._cell_payload_directory,
            policy=effective_policy,
            now=generated_at,
            inventory_complete=marks.complete and not missing and not blocked,
            blocked_reasons=tuple(blocked),
        )

    @staticmethod
    def write_cell_payload_plan(plan: CellPayloadMaintenancePlan, path: Path) -> None:
        if path.exists():
            raise FileExistsError(f"Cell payload plan already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        StorageMaintenanceService._atomic_write_json(path, plan.to_document())

    @staticmethod
    def load_cell_payload_plan(path: Path) -> CellPayloadMaintenancePlan:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("Cell payload maintenance plan must be a JSON object")
        return CellPayloadMaintenancePlan.from_document(document)

    @staticmethod
    def write_cell_payload_receipt(
        receipt: CellPayloadQuarantineReceipt, path: Path
    ) -> None:
        if path.exists():
            raise FileExistsError(f"Cell payload receipt already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        StorageMaintenanceService._atomic_write_json(path, receipt.to_document())

    @staticmethod
    def load_cell_payload_receipt(path: Path) -> CellPayloadQuarantineReceipt:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("Cell payload quarantine receipt must be a JSON object")
        return CellPayloadQuarantineReceipt.from_document(document)

    def execute_cell_payload_quarantine(
        self,
        plan: CellPayloadMaintenancePlan,
        *,
        confirmation_token: str,
    ) -> CellPayloadQuarantineReceipt:
        """Move the fully revalidated plan into quarantine; never unlink payloads."""

        self._validate_cell_payload_plan(plan)
        if plan.blocked_reasons:
            raise RuntimeError(
                "Cell payload plan is fail-closed: " + ", ".join(plan.blocked_reasons)
            )
        if confirmation_token != plan.confirmation_token:
            raise PermissionError(
                "Cell payload execution requires the exact full-plan confirmation token"
            )
        payloads = {
            item.content_hash: item
            for item in plan.decisions
            if item.action == CellPayloadAction.QUARANTINE
        }
        markers = {
            item.marker_name: item
            for item in plan.pending_markers
            if item.action == CellPayloadAction.QUARANTINE
        }
        if not payloads and not markers:
            raise ValueError("Plan has no Cell payloads or stale markers to quarantine")

        moved: list[tuple[Path, Path, str, str]] = []
        quarantine_directory = (
            self._cell_payload_directory / ".maintenance-quarantine" / plan.plan_id
        )
        with self._cell_payload_store.directory_lock():
            with self._engine.connect() as connection:
                marks = self._cell_payload_reference_marks(connection)
            if not marks.complete:
                raise RuntimeError("Cell payload references could not be revalidated")

            current_markers = {
                item.marker_name: item
                for item in self._cell_payload_pending_marker_inventory()
            }
            fresh_pending_hashes = {
                item.content_hash
                for item in current_markers.values()
                if item.modified_at > plan.generated_at
                or plan.generated_at - item.modified_at < plan.policy.grace_period
            }
            protected = marks.product | marks.active | marks.current | marks.referenced
            newly_protected = set(payloads).intersection(protected | fresh_pending_hashes)
            if newly_protected:
                raise RuntimeError(
                    "Cell payload became referenced or in-flight after dry-run: "
                    + ", ".join(sorted(newly_protected))
                )
            active_marker_owners = {
                item.owner_work_item_id
                for item in markers.values()
                if item.owner_work_item_id in marks.active_work_items
            }
            if active_marker_owners:
                raise RuntimeError(
                    "Cell payload marker owner became active after dry-run: "
                    + ", ".join(sorted(str(value) for value in active_marker_owners))
                )

            current_payloads = {
                item.content_hash: item for item in self._cell_payload_file_inventory()
            }
            for content_hash, planned in payloads.items():
                current = current_payloads.get(content_hash)
                if current is None or not self._same_payload_snapshot(planned, current):
                    raise RuntimeError(
                        f"Cell payload changed after dry-run: {content_hash}"
                    )
            for marker_name, marker_planned in markers.items():
                marker_current = current_markers.get(marker_name)
                if marker_current is None or not self._same_marker_snapshot(
                    marker_planned, marker_current
                ):
                    raise RuntimeError(
                        f"Cell payload pending marker changed after dry-run: {marker_name}"
                    )

            quarantine_parent = quarantine_directory.parent
            if quarantine_parent.exists() and quarantine_parent.is_symlink():
                raise RuntimeError("Cell payload quarantine root cannot be a symlink")
            if quarantine_directory.exists():
                raise FileExistsError(
                    f"Cell payload quarantine directory already exists: {quarantine_directory}"
                )
            quarantine_directory.mkdir(parents=True, exist_ok=False)
            self._atomic_write_json(
                quarantine_directory / "plan.json", plan.to_document()
            )
            try:
                for _content_hash, planned in sorted(payloads.items()):
                    source = Path(planned.storage_path)
                    destination = quarantine_directory / source.name
                    source.replace(destination)
                    moved.append(
                        (source, destination, "payload", planned.content_hash)
                    )
                for marker_name, marker_planned in sorted(markers.items()):
                    source = Path(marker_planned.storage_path)
                    destination = quarantine_directory / marker_name
                    source.replace(destination)
                    moved.append(
                        (
                            source,
                            destination,
                            "pending_marker",
                            marker_planned.content_hash,
                        )
                    )

                executed_at = datetime.now(UTC)
                items = tuple(
                    CellPayloadQuarantineItem(
                        kind=kind,
                        content_hash=content_hash,
                        source_path=source.resolve().as_posix(),
                        quarantine_path=destination.resolve().as_posix(),
                        byte_size=destination.stat().st_size,
                        file_sha256=self._sha256_file(destination),
                    )
                    for source, destination, kind, content_hash in moved
                )
                quarantine_uri = quarantine_directory.resolve().as_posix()
                receipt_id = _cell_payload_receipt_id(
                    plan_id=plan.plan_id,
                    quarantine_directory=quarantine_uri,
                    executed_at=executed_at,
                    items=items,
                )
                receipt = CellPayloadQuarantineReceipt(
                    receipt_id=receipt_id,
                    plan_id=plan.plan_id,
                    quarantine_directory=quarantine_uri,
                    executed_at=executed_at,
                    items=items,
                )
                self._atomic_write_json(
                    quarantine_directory / "receipt.json", receipt.to_document()
                )
            except BaseException:
                for source, destination, _kind, _content_hash in reversed(moved):
                    if destination.exists() and not source.exists():
                        destination.replace(source)
                raise
        return receipt

    def rollback_cell_payload_quarantine(
        self,
        receipt: CellPayloadQuarantineReceipt,
        *,
        confirmation_token: str,
    ) -> int:
        """Restore a quarantine receipt without overwriting any live file."""

        self._validate_cell_payload_receipt(receipt)
        if confirmation_token != receipt.rollback_token:
            raise PermissionError(
                "Cell payload rollback requires the exact receipt rollback token"
            )
        restored: list[tuple[Path, Path]] = []
        with self._cell_payload_store.directory_lock():
            for item in receipt.items:
                source = Path(item.source_path)
                quarantine = Path(item.quarantine_path)
                self._validate_receipt_paths(receipt, item, source, quarantine)
                if source.exists():
                    raise FileExistsError(f"Rollback would overwrite live file: {source}")
                if not quarantine.is_file() or quarantine.is_symlink():
                    raise RuntimeError(f"Quarantined Cell payload is missing: {quarantine}")
                if (
                    quarantine.stat().st_size != item.byte_size
                    or self._sha256_file(quarantine) != item.file_sha256
                ):
                    raise RuntimeError(f"Quarantined Cell payload changed: {quarantine}")
            try:
                for item in receipt.items:
                    source = Path(item.source_path)
                    quarantine = Path(item.quarantine_path)
                    quarantine.replace(source)
                    restored.append((source, quarantine))
                rollback_path = Path(receipt.quarantine_directory) / "rollback.json"
                if rollback_path.exists():
                    raise FileExistsError(f"Rollback evidence already exists: {rollback_path}")
                self._atomic_write_json(
                    rollback_path,
                    {
                        "schema_version": "cell_payload_quarantine_rollback_v1",
                        "receipt_id": receipt.receipt_id,
                        "rolled_back_at": datetime.now(UTC).isoformat(),
                        "file_count": len(restored),
                    },
                )
            except BaseException:
                for source, quarantine in reversed(restored):
                    if source.exists() and not quarantine.exists():
                        source.replace(quarantine)
                raise
        return len(restored)

    def archive_lineage(
        self,
        plan: StorageMaintenancePlan,
        *,
        confirmation_token: str,
        manifest_ids: Sequence[uuid.UUID] | None = None,
        max_archive_bytes: int = DEFAULT_ARCHIVE_SHARD_BYTES,
    ) -> LineageArchiveReceipt:
        self._require_confirmation(plan, confirmation_token)
        if max_archive_bytes < 1:
            raise ValueError("Archive shard size must be positive")
        eligible = {
            item.lineage_manifest_id: item
            for item in plan.manifests
            if item.retention_class == ManifestRetentionClass.RECLAIMABLE_OLD_EXPERIMENT
        }
        if manifest_ids is None:
            selected: list[uuid.UUID] = []
            selected_bytes = 0
            for identifier, snapshot in eligible.items():
                if selected and selected_bytes + snapshot.estimated_bytes > max_archive_bytes:
                    break
                selected.append(identifier)
                selected_bytes += snapshot.estimated_bytes
            manifest_ids = tuple(selected)
        else:
            manifest_ids = tuple(manifest_ids)
        if not manifest_ids:
            raise ValueError("Plan has no safely reclaimable lineage manifests")
        unknown = set(manifest_ids).difference(eligible)
        if unknown:
            raise ValueError("Archive selection contains non-reclaimable manifests")
        snapshots = {item.lineage_manifest_id: item for item in plan.manifests}
        with self._engine.connect() as connection:
            rows = tuple(
                connection.execute(
                    text("""
                        SELECT manifest.lineage_manifest_id,
                               manifest.root_artifact_id,
                               artifact.artifact_type,
                               artifact.artifact_key,
                               artifact.semantic_fingerprint,
                               artifact.content_hash,
                               manifest.manifest_hash,
                               manifest.canonical_version,
                               manifest.manifest,
                               manifest.created_at
                        FROM lineage.lineage_manifest manifest
                        JOIN lineage.artifact artifact
                          ON artifact.artifact_id = manifest.root_artifact_id
                        WHERE manifest.lineage_manifest_id IN :manifest_ids
                        ORDER BY manifest.lineage_manifest_id
                    """).bindparams(bindparam("manifest_ids", expanding=True)),
                    {"manifest_ids": manifest_ids},
                ).mappings()
            )
        if {row["lineage_manifest_id"] for row in rows} != set(manifest_ids):
            raise RuntimeError("Lineage inventory changed after dry-run; create a new plan")
        records: list[dict[str, Any]] = []
        for row in rows:
            snapshot = snapshots[row["lineage_manifest_id"]]
            if row["manifest_hash"] != snapshot.manifest_hash:
                raise RuntimeError("Lineage manifest hash changed after dry-run")
            manifest = row["manifest"]
            if not isinstance(manifest, dict):
                raise RuntimeError("Lineage manifest must be a JSON object")
            if sha256_hexdigest(manifest) != row["manifest_hash"]:
                raise RuntimeError(f"Corrupt lineage manifest: {row['lineage_manifest_id']}")
            records.append(self._archive_record(row, manifest))

        self._archive_directory.mkdir(parents=True, exist_ok=True)
        shard_id = sha256_hexdigest([str(value) for value in manifest_ids])[:16]
        archive_path = (
            self._archive_directory / f"lineage-{plan.plan_id[:16]}-{shard_id}.parquet"
        )
        if archive_path.exists():
            raise FileExistsError(f"Archive already exists: {archive_path}")
        temporary = archive_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        pd.DataFrame.from_records(records).to_parquet(
            temporary,
            engine="pyarrow",
            compression="zstd",
            index=False,
        )
        temporary.replace(archive_path)
        archive_hash = self._sha256_file(archive_path)
        self._verify_archive(archive_path, expected=records, archive_sha256=archive_hash)
        archived_at = datetime.now(UTC)
        tombstones = tuple(
            ArtifactTombstone(
                lineage_manifest_id=uuid.UUID(item["lineage_manifest_id"]),
                root_artifact_id=uuid.UUID(item["root_artifact_id"]),
                artifact_type=item["artifact_type"],
                artifact_key=item["artifact_key"],
                semantic_fingerprint=item["semantic_fingerprint"],
                content_hash=item["content_hash"],
                manifest_hash=item["manifest_hash"],
                canonical_version=item["canonical_version"],
                created_at=datetime.fromisoformat(item["created_at"]),
                archive_uri=archive_path.as_posix(),
                archive_sha256=archive_hash,
            )
            for item in records
        )
        receipt_path = archive_path.with_suffix(".receipt.json")
        receipt_payload = {
            "schema_version": "lineage_cold_archive_receipt_v1",
            "plan_id": plan.plan_id,
            "archive_uri": archive_path.as_posix(),
            "archive_sha256": archive_hash,
            "manifest_count": len(records),
            "archived_at": archived_at.isoformat(),
            "database_compaction_executed": False,
            "restore_verified": True,
            "tombstones": [self._jsonable_tombstone(item) for item in tombstones],
        }
        self._atomic_write_json(receipt_path, receipt_payload)
        return LineageArchiveReceipt(
            plan_id=plan.plan_id,
            archive_uri=archive_path.as_posix(),
            archive_sha256=archive_hash,
            receipt_uri=receipt_path.as_posix(),
            manifest_count=len(records),
            archived_at=archived_at,
            tombstones=tombstones,
        )

    def execute_cache_cleanup(
        self,
        plan: StorageMaintenancePlan,
        *,
        confirmation_token: str,
    ) -> int:
        """Remove planned cache entries after lock/revalidation and quarantine.

        The immutable lineage archive is intentionally a separate operation.
        This method only handles recomputable Workspace materialization files.
        """

        self._require_confirmation(plan, confirmation_token)
        keys = plan.cache.delete_keys
        if not keys:
            return 0
        snapshots = {item.cache_key: item for item in plan.cache.decisions}
        moved: list[tuple[Path, Path]] = []
        trash = self._cache_directory / ".maintenance-trash"
        trash.mkdir(parents=True, exist_ok=True)
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "SELECT pg_advisory_xact_lock("
                        "hashtextextended('workspace-materialization-cache-gc', 0))"
                    )
                )
                rows = tuple(
                    connection.execute(
                        text("""
                            SELECT cache_key, storage_uri, last_accessed_at
                            FROM workspace.research_materialization_cache
                            WHERE cache_key IN :keys
                            FOR UPDATE
                        """).bindparams(bindparam("keys", expanding=True)),
                        {"keys": keys},
                    ).mappings()
                )
                protected_groups, complete = self._cache_reference_keys(connection, keys)
                if not complete:
                    raise RuntimeError("Cache references could not be revalidated; cleanup aborted")
                for row in rows:
                    key = str(row["cache_key"])
                    snapshot = snapshots[key]
                    if key in protected_groups[0] or key in protected_groups[1]:
                        raise RuntimeError(f"Cache became referenced after dry-run: {key}")
                    if row["last_accessed_at"] != snapshot.last_accessed_at:
                        raise RuntimeError(f"Cache was accessed after dry-run: {key}")
                    paths, valid = self._cache_paths(key, str(row["storage_uri"]))
                    if not valid:
                        raise RuntimeError(f"Cache path failed revalidation: {key}")
                    for source in paths:
                        if not source.exists():
                            continue
                        destination = trash / f"{plan.plan_id[:16]}-{source.name}"
                        source.replace(destination)
                        moved.append((source, destination))
                connection.execute(
                    text(
                        "DELETE FROM workspace.research_materialization_cache "
                        "WHERE cache_key IN :keys"
                    ).bindparams(bindparam("keys", expanding=True)),
                    {"keys": keys},
                )
        except BaseException:
            for source, destination in reversed(moved):
                if destination.exists() and not source.exists():
                    destination.replace(source)
            raise
        for _source, destination in moved:
            destination.unlink(missing_ok=True)
        return len(keys)

    @staticmethod
    def _require_confirmation(
        plan: StorageMaintenancePlan,
        confirmation_token: str,
    ) -> None:
        if plan.blocked_reasons:
            raise RuntimeError(
                "Storage plan is fail-closed: " + ", ".join(plan.blocked_reasons)
            )
        if confirmation_token != plan.confirmation_token:
            raise PermissionError("Storage execution requires the exact dry-run confirmation token")

    @staticmethod
    def _product_pin_inventory(connection: Connection) -> _ProductPinInventory:
        inventory = _ProductPinInventory()
        exact_evidence = {
            item.qualification_bundle_id: item
            for item in load_product_qualification_evidence(connection)
        }
        rows = tuple(
            connection.execute(text("""
                SELECT qualification.qualification_bundle_id,
                       qualification.artifact_id AS qualification_artifact_id,
                       qualification.portfolio_cell_count,
                       qualification.result_artifact_ids,
                       qualification.cell_artifact_ids,
                       qualification.compiled_strategy_version_id,
                       qualification.source_suite_artifact_id,
                       suite.research_suite_id,
                       strategy.compiled_model_instance_id,
                       strategy.artifact_id AS strategy_artifact_id,
                       spec.artifact_id AS compiled_spec_artifact_id,
                       spec.normalized_selection,
                       qualification.selection_context,
                       product_version.artifact_id AS product_artifact_id,
                       monitoring.artifact_id AS monitoring_policy_artifact_id,
                       benchmark.artifact_id AS benchmark_artifact_id,
                       product_version.capital_policy_artifact_id,
                       product_version.cost_model_artifact_id,
                       context.artifact_id AS comparison_context_artifact_id,
                       context.data_bundle_artifact_id,
                       context.universe_history_artifact_id
                FROM product.product_version product_version
                JOIN experiment.qualification_bundle qualification
                  ON qualification.qualification_bundle_id =
                     product_version.qualification_bundle_id
                JOIN experiment.research_suite suite
                  ON suite.artifact_id = qualification.source_suite_artifact_id
                JOIN strategy.compiled_strategy_version strategy
                  ON strategy.compiled_strategy_version_id =
                     qualification.compiled_strategy_version_id
                JOIN workspace.compiled_research_spec spec
                  ON spec.compiled_research_spec_id = strategy.compiled_research_spec_id
                JOIN product.monitoring_policy monitoring
                  ON monitoring.monitoring_policy_id = product_version.monitoring_policy_id
                JOIN experiment.benchmark_set benchmark
                  ON benchmark.benchmark_set_id = product_version.benchmark_set_id
                JOIN experiment.comparison_context context
                  ON context.comparison_context_id = qualification.comparison_context_id
                ORDER BY product_version.product_version_id
            """)).mappings()
        )
        for row in rows:
            evidence = exact_evidence.get(row["qualification_bundle_id"])
            if evidence is None or not evidence.complete:
                inventory.complete = False
                inventory.reasons.append(
                    f"incomplete_product_evidence:{row['qualification_bundle_id']}"
                )
            inventory.qualification_roots.add(row["qualification_artifact_id"])
            inventory.source_suite_ids.add(row["research_suite_id"])
            inventory.direct_roots.add(row["source_suite_artifact_id"])
            if evidence is not None:
                # The immutable Qualification edges pin exactly six Portfolio
                # Results plus one Predictive Result.  Do not infer/pin sibling
                # Cells (including failed Predictive experiments) from the Suite.
                inventory.closure_roots.update(evidence.cell_artifact_ids)
                inventory.closure_roots.update(evidence.result_artifact_ids)
            for key in (
                "qualification_artifact_id",
                "strategy_artifact_id",
                "compiled_spec_artifact_id",
                "product_artifact_id",
                "monitoring_policy_artifact_id",
                "benchmark_artifact_id",
                "capital_policy_artifact_id",
                "cost_model_artifact_id",
                "comparison_context_artifact_id",
                "data_bundle_artifact_id",
                "universe_history_artifact_id",
            ):
                value = row[key]
                if value is not None:
                    inventory.closure_roots.add(value)
            selection_context = row["selection_context"]
            exact_selection = (
                selection_context.get("exact_selection")
                if isinstance(selection_context, dict)
                else None
            )
            legacy_selection = (
                selection_context.get("normalized_selection")
                if isinstance(selection_context, dict)
                else None
            )
            selection_roots, selection_complete = (
                StorageMaintenanceService._selection_artifact_roots(
                    connection,
                    exact_selection
                    if isinstance(exact_selection, dict)
                    else legacy_selection
                    if isinstance(legacy_selection, dict)
                    else row["normalized_selection"],
                )
            )
            inventory.closure_roots.update(selection_roots)
            if not selection_complete:
                inventory.complete = False
                inventory.reasons.append(
                    f"incomplete_product_selection_lineage:{row['qualification_bundle_id']}"
                )
        snapshot_roots = connection.execute(
            text("SELECT artifact_id FROM product.monitoring_snapshot")
        ).scalars()
        inventory.closure_roots.update(snapshot_roots)
        return inventory

    @staticmethod
    def _selection_artifact_roots(
        connection: Connection,
        selection: Any,
    ) -> tuple[set[uuid.UUID], bool]:
        if not isinstance(selection, dict):
            return set(), False
        factor_keys = tuple(str(value) for value in selection.get("factor_variant_keys", ()))
        signal_keys = tuple(str(value) for value in selection.get("signal_version_keys", ()))
        security_ids: tuple[uuid.UUID, ...]
        try:
            security_ids = tuple(
                uuid.UUID(str(value)) for value in selection.get("asset_security_ids", ())
            )
        except (TypeError, ValueError):
            return set(), False
        roots: set[uuid.UUID] = set()
        complete = bool(factor_keys and signal_keys and security_ids)
        if factor_keys:
            factor_rows = tuple(
                connection.execute(
                    text("""
                        SELECT DISTINCT variant.variant_key, variant.artifact_id
                        FROM factor.factor_variant variant
                        JOIN lineage.artifact artifact
                          ON artifact.artifact_id = variant.artifact_id
                         AND artifact.status = 'published'
                        WHERE variant.variant_key IN :keys
                    """).bindparams(bindparam("keys", expanding=True)),
                    {"keys": factor_keys},
                ).mappings()
            )
            roots.update(row["artifact_id"] for row in factor_rows)
            complete = complete and {row["variant_key"] for row in factor_rows} == set(
                factor_keys
            )
        if signal_keys:
            signal_rows = tuple(
                connection.execute(
                    text("""
                        SELECT DISTINCT definition.signal_key, version.artifact_id
                        FROM signal.signal_definition definition
                        JOIN signal.signal_version version
                          ON version.signal_definition_id = definition.signal_definition_id
                        JOIN lineage.artifact artifact
                          ON artifact.artifact_id = version.artifact_id
                         AND artifact.status = 'published'
                        WHERE definition.signal_key IN :keys
                    """).bindparams(bindparam("keys", expanding=True)),
                    {"keys": signal_keys},
                ).mappings()
            )
            roots.update(row["artifact_id"] for row in signal_rows)
            complete = complete and {row["signal_key"] for row in signal_rows} == set(
                signal_keys
            )
        if security_ids:
            release_rows = tuple(
                connection.execute(
                    text("""
                        SELECT release.artifact_id,
                               count(DISTINCT profile.security_id) AS security_count
                        FROM catalog.asset_registry_release release
                        JOIN catalog.security_profile profile
                          ON profile.asset_registry_release_id = release.asset_registry_release_id
                        JOIN lineage.artifact artifact
                          ON artifact.artifact_id = release.artifact_id
                         AND artifact.status = 'published'
                        WHERE profile.security_id IN :security_ids
                        GROUP BY release.artifact_id
                        HAVING count(DISTINCT profile.security_id) = :security_count
                    """).bindparams(bindparam("security_ids", expanding=True)),
                    {
                        "security_ids": security_ids,
                        "security_count": len(set(security_ids)),
                    },
                ).mappings()
            )
            roots.update(row["artifact_id"] for row in release_rows)
            complete = complete and bool(release_rows)
        # Model/Strategy presets are versioned inside the component catalog.
        # The current compiled contract does not persist the exact catalog ID,
        # so retain every published catalog rather than guessing a version.
        component_catalogs = tuple(
            connection.execute(text("""
                SELECT catalog.artifact_id
                FROM workspace.component_catalog catalog
                JOIN lineage.artifact artifact
                  ON artifact.artifact_id = catalog.artifact_id
                 AND artifact.status = 'published'
            """)).scalars()
        )
        roots.update(component_catalogs)
        complete = complete and bool(component_catalogs)
        return roots, complete

    @staticmethod
    def _artifact_closure(
        connection: Connection,
        roots: set[uuid.UUID],
        *,
        excluded_source_suite_roots: set[uuid.UUID],
    ) -> set[uuid.UUID]:
        if not roots:
            return set()
        return set(
            connection.execute(
                text("""
                    WITH RECURSIVE pinned(artifact_id) AS (
                        SELECT unnest(CAST(:roots AS uuid[]))
                        UNION
                        SELECT dependency.depends_on_artifact_id
                        FROM lineage.artifact_dependency dependency
                        JOIN pinned ON pinned.artifact_id = dependency.artifact_id
                        WHERE NOT (
                            dependency.artifact_id = ANY(CAST(:qualifications AS uuid[]))
                            AND dependency.role = 'source_suite'
                        )
                    )
                    SELECT artifact_id FROM pinned
                """),
                {
                    "roots": list(roots),
                    "qualifications": list(excluded_source_suite_roots),
                },
            ).scalars()
        )

    @staticmethod
    def _current_suite_ids(
        connection: Connection,
        *,
        explicit: set[uuid.UUID],
    ) -> set[uuid.UUID]:
        current = set(explicit)
        current.update(
            connection.execute(text("""
                SELECT DISTINCT link.research_suite_id
                FROM experiment.research_suite_work_item link
                JOIN ops.work_item work ON work.work_item_id = link.work_item_id
                WHERE work.status IN ('queued','running')
            """)).scalars()
        )
        latest = connection.execute(
            text(
                "SELECT research_suite_id FROM experiment.research_suite "
                "ORDER BY created_at DESC, research_suite_id DESC LIMIT 1"
            )
        ).scalar_one_or_none()
        if latest is not None:
            current.add(latest)
        return current

    @staticmethod
    def _suite_artifacts(
        connection: Connection,
        suite_ids: set[uuid.UUID],
    ) -> set[uuid.UUID]:
        if not suite_ids:
            return set()
        return set(
            connection.execute(
                text("""
                    SELECT artifact_id FROM experiment.research_suite
                    WHERE research_suite_id IN :suite_ids
                    UNION
                    SELECT artifact_id FROM experiment.predictive_cell_specification
                    WHERE research_suite_id IN :suite_ids
                    UNION
                    SELECT artifact_id FROM experiment.portfolio_cell_specification
                    WHERE research_suite_id IN :suite_ids
                    UNION
                    SELECT result.artifact_id
                    FROM experiment.cell_result result
                    WHERE result.cell_artifact_id IN (
                        SELECT artifact_id FROM experiment.predictive_cell_specification
                        WHERE research_suite_id IN :suite_ids
                        UNION
                        SELECT artifact_id FROM experiment.portfolio_cell_specification
                        WHERE research_suite_id IN :suite_ids
                    )
                """).bindparams(bindparam("suite_ids", expanding=True)),
                {"suite_ids": tuple(suite_ids)},
            ).scalars()
        )

    @staticmethod
    def _orphan_experiment_artifacts(connection: Connection) -> set[uuid.UUID]:
        return set(
            connection.execute(
                text("""
                    SELECT artifact_id
                    FROM lineage.artifact
                    WHERE artifact_type IN :artifact_types
                """).bindparams(bindparam("artifact_types", expanding=True)),
                {"artifact_types": tuple(sorted(_EXPERIMENT_ARCHIVE_ARTIFACT_TYPES))},
            ).scalars()
        )

    @staticmethod
    def _manifest_inventory(
        connection: Connection,
        *,
        product_pins: set[uuid.UUID],
        current_artifacts: set[uuid.UUID],
        old_artifacts: set[uuid.UUID],
        reclaim_blocked: bool,
    ) -> list[LineageManifestSnapshot]:
        rows = connection.execute(text("""
            SELECT manifest.lineage_manifest_id, manifest.root_artifact_id,
                   manifest.manifest_hash, manifest.canonical_version,
                   manifest.created_at, artifact.artifact_type,
                   artifact.artifact_key, artifact.semantic_fingerprint,
                   artifact.content_hash,
                   pg_column_size(manifest.manifest) AS estimated_bytes
            FROM lineage.lineage_manifest manifest
            JOIN lineage.artifact artifact
              ON artifact.artifact_id = manifest.root_artifact_id
            ORDER BY manifest.created_at, manifest.lineage_manifest_id
        """)).mappings()
        result: list[LineageManifestSnapshot] = []
        for row in rows:
            root = row["root_artifact_id"]
            if root in product_pins:
                retention = ManifestRetentionClass.PRODUCT_PIN
            elif root in current_artifacts:
                retention = ManifestRetentionClass.CURRENT_EXPERIMENT
            elif root in old_artifacts and not reclaim_blocked:
                retention = ManifestRetentionClass.RECLAIMABLE_OLD_EXPERIMENT
            else:
                retention = ManifestRetentionClass.SHARED_OR_UNCLASSIFIED
            result.append(
                LineageManifestSnapshot(
                    lineage_manifest_id=row["lineage_manifest_id"],
                    root_artifact_id=root,
                    artifact_type=str(row["artifact_type"]),
                    artifact_key=str(row["artifact_key"]),
                    semantic_fingerprint=str(row["semantic_fingerprint"]),
                    content_hash=str(row["content_hash"]),
                    manifest_hash=str(row["manifest_hash"]),
                    canonical_version=str(row["canonical_version"]),
                    created_at=row["created_at"],
                    estimated_bytes=int(row["estimated_bytes"]),
                    retention_class=retention,
                )
            )
        return result

    def _cell_payload_file_inventory(self) -> tuple[CellPayloadFileSnapshot, ...]:
        if not self._cell_payload_directory.exists():
            return ()
        result: list[CellPayloadFileSnapshot] = []
        for path in sorted(self._cell_payload_directory.iterdir(), key=lambda item: item.name):
            match = _CELL_PAYLOAD_FILE_PATTERN.fullmatch(path.name)
            if match is None:
                continue
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f"Strict Cell payload path is not a regular file: {path}")
            resolved = path.resolve()
            if resolved.parent != self._cell_payload_directory:
                raise RuntimeError(f"Cell payload path escaped configured root: {path}")
            stat = path.stat()
            result.append(
                CellPayloadFileSnapshot(
                    content_hash=match.group(1),
                    storage_path=resolved.as_posix(),
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    modified_at_ns=stat.st_mtime_ns,
                    byte_size=stat.st_size,
                    file_sha256=self._sha256_file(path),
                )
            )
        return tuple(result)

    def _cell_payload_pending_marker_inventory(
        self,
    ) -> tuple[CellPayloadPendingMarkerSnapshot, ...]:
        if not self._cell_payload_directory.exists():
            return ()
        result: list[CellPayloadPendingMarkerSnapshot] = []
        for path in sorted(self._cell_payload_directory.iterdir(), key=lambda item: item.name):
            match = _CELL_PAYLOAD_PENDING_PATTERN.fullmatch(path.name)
            if match is None:
                continue
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(
                    f"Strict Cell payload pending marker is not a regular file: {path}"
                )
            resolved = path.resolve()
            if resolved.parent != self._cell_payload_directory:
                raise RuntimeError(f"Cell payload marker escaped configured root: {path}")
            stat = path.stat()
            try:
                marker_payload = json.loads(path.read_text(encoding="utf-8"))
                owner_value = marker_payload.get("owner_work_item_id")
                owner_work_item_id = (
                    uuid.UUID(str(owner_value)) if owner_value is not None else None
                )
                if marker_payload.get("content_hash") != match.group(1):
                    raise ValueError("marker content hash mismatch")
            except (AttributeError, json.JSONDecodeError, TypeError, ValueError):
                owner_work_item_id = None
            result.append(
                CellPayloadPendingMarkerSnapshot(
                    marker_name=path.name,
                    content_hash=match.group(1),
                    owner_work_item_id=owner_work_item_id,
                    storage_path=resolved.as_posix(),
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    modified_at_ns=stat.st_mtime_ns,
                    byte_size=stat.st_size,
                    file_sha256=self._sha256_file(path),
                )
            )
        return tuple(result)

    @staticmethod
    def _cell_payload_reference_marks(
        connection: Connection,
    ) -> _CellPayloadReferenceMarks:
        marks = _CellPayloadReferenceMarks()
        rows = tuple(
            connection.execute(text("""
                SELECT artifact_id, payload_content_hash, payload_storage_uri,
                       payload_storage_format, payload_schema_version,
                       payload_byte_size
                FROM experiment.cell_result
                WHERE payload_content_hash IS NOT NULL
                ORDER BY artifact_id
            """)).mappings()
        )
        artifact_hashes: dict[uuid.UUID, str] = {}
        for row in rows:
            content_hash = str(row["payload_content_hash"])
            artifact_hashes[row["artifact_id"]] = content_hash
            if _CELL_PAYLOAD_FILE_PATTERN.fullmatch(f"{content_hash}.parquet") is None:
                marks.complete = False
                marks.reasons.append(f"invalid_cell_payload_hash:{row['artifact_id']}")
                continue
            marks.referenced.add(content_hash)
            expected_uri = f"cell-result://sha256/{content_hash}.parquet"
            if (
                row["payload_storage_uri"] != expected_uri
                or row["payload_storage_format"] != PAYLOAD_STORAGE_FORMAT
                or row["payload_schema_version"] != PAYLOAD_SCHEMA_VERSION
                or int(row["payload_byte_size"] or 0) <= 0
            ):
                marks.complete = False
                marks.reasons.append(f"invalid_cell_payload_reference:{row['artifact_id']}")

        product_result_ids = set(
            connection.execute(text("""
                SELECT DISTINCT unnest(qualification.result_artifact_ids)
                FROM product.product_version product_version
                JOIN experiment.qualification_bundle qualification
                  ON qualification.qualification_bundle_id =
                     product_version.qualification_bundle_id
            """)).scalars()
        )
        marks.product.update(
            artifact_hashes[artifact_id]
            for artifact_id in product_result_ids
            if artifact_id in artifact_hashes
        )

        active_suite_ids = set(
            connection.execute(text("""
                SELECT DISTINCT link.research_suite_id
                FROM experiment.research_suite_work_item link
                JOIN ops.work_item work ON work.work_item_id = link.work_item_id
                WHERE work.status IN ('queued', 'running')
            """)).scalars()
        )
        marks.active_work_items.update(
            connection.execute(text("""
                SELECT work_item_id
                FROM ops.work_item
                WHERE status IN ('queued', 'running')
            """)).scalars()
        )
        current_suite_ids = StorageMaintenanceService._current_suite_ids(
            connection, explicit=set()
        )
        marks.active.update(
            StorageMaintenanceService._cell_payload_hashes_for_suites(
                connection, active_suite_ids
            )
        )
        marks.current.update(
            StorageMaintenanceService._cell_payload_hashes_for_suites(
                connection, current_suite_ids
            )
        )
        return marks

    @staticmethod
    def _cell_payload_hashes_for_suites(
        connection: Connection,
        suite_ids: set[uuid.UUID],
    ) -> set[str]:
        if not suite_ids:
            return set()
        return set(
            connection.execute(
                text("""
                    SELECT DISTINCT result.payload_content_hash
                    FROM experiment.cell_result result
                    WHERE result.payload_content_hash IS NOT NULL
                      AND result.cell_artifact_id IN (
                          SELECT artifact_id
                          FROM experiment.predictive_cell_specification
                          WHERE research_suite_id IN :suite_ids
                          UNION
                          SELECT artifact_id
                          FROM experiment.portfolio_cell_specification
                          WHERE research_suite_id IN :suite_ids
                      )
                """).bindparams(bindparam("suite_ids", expanding=True)),
                {"suite_ids": tuple(suite_ids)},
            ).scalars()
        )

    def _validate_cell_payload_plan(self, plan: CellPayloadMaintenancePlan) -> None:
        expected_id = _cell_payload_plan_id(
            generated_at=plan.generated_at,
            root_directory=plan.root_directory,
            policy=plan.policy,
            decisions=plan.decisions,
            pending_markers=plan.pending_markers,
            blocked_reasons=plan.blocked_reasons,
        )
        if plan.plan_id != expected_id:
            raise ValueError("Cell payload maintenance plan hash mismatch")
        if Path(plan.root_directory).resolve() != self._cell_payload_directory:
            raise ValueError("Cell payload maintenance plan targets another root")
        payload_hashes: set[str] = set()
        for item in plan.decisions:
            expected = self._cell_payload_directory / f"{item.content_hash}.parquet"
            if (
                _CELL_PAYLOAD_FILE_PATTERN.fullmatch(expected.name) is None
                or Path(item.storage_path).resolve() != expected.resolve()
                or item.content_hash in payload_hashes
            ):
                raise ValueError("Cell payload maintenance plan contains invalid paths")
            payload_hashes.add(item.content_hash)
        marker_names: set[str] = set()
        for marker_item in plan.pending_markers:
            match = _CELL_PAYLOAD_PENDING_PATTERN.fullmatch(marker_item.marker_name)
            expected = self._cell_payload_directory / marker_item.marker_name
            if (
                match is None
                or match.group(1) != marker_item.content_hash
                or Path(marker_item.storage_path).resolve() != expected.resolve()
                or marker_item.marker_name in marker_names
            ):
                raise ValueError("Cell payload plan contains invalid pending markers")
            marker_names.add(marker_item.marker_name)

    def _validate_cell_payload_receipt(
        self, receipt: CellPayloadQuarantineReceipt
    ) -> None:
        expected_id = _cell_payload_receipt_id(
            plan_id=receipt.plan_id,
            quarantine_directory=receipt.quarantine_directory,
            executed_at=receipt.executed_at,
            items=receipt.items,
        )
        if receipt.receipt_id != expected_id:
            raise ValueError("Cell payload quarantine receipt hash mismatch")
        expected_directory = (
            self._cell_payload_directory / ".maintenance-quarantine" / receipt.plan_id
        ).resolve()
        if Path(receipt.quarantine_directory).resolve() != expected_directory:
            raise ValueError("Cell payload quarantine receipt targets another root")

    @staticmethod
    def _same_payload_snapshot(
        planned: CellPayloadDecision, current: CellPayloadFileSnapshot
    ) -> bool:
        return (
            planned.storage_path == current.storage_path
            and planned.modified_at_ns == current.modified_at_ns
            and planned.byte_size == current.byte_size
            and planned.file_sha256 == current.file_sha256
        )

    @staticmethod
    def _same_marker_snapshot(
        planned: CellPayloadPendingMarkerDecision,
        current: CellPayloadPendingMarkerSnapshot,
    ) -> bool:
        return (
            planned.storage_path == current.storage_path
            and planned.modified_at_ns == current.modified_at_ns
            and planned.byte_size == current.byte_size
            and planned.file_sha256 == current.file_sha256
        )

    def _validate_receipt_paths(
        self,
        receipt: CellPayloadQuarantineReceipt,
        item: CellPayloadQuarantineItem,
        source: Path,
        quarantine: Path,
    ) -> None:
        if item.kind == "payload":
            expected_name = f"{item.content_hash}.parquet"
            pattern = _CELL_PAYLOAD_FILE_PATTERN
        elif item.kind == "pending_marker":
            expected_name = quarantine.name
            pattern = _CELL_PAYLOAD_PENDING_PATTERN
        else:
            raise ValueError(f"Unknown Cell payload quarantine item kind: {item.kind}")
        match = pattern.fullmatch(expected_name)
        if match is None or match.group(1) != item.content_hash:
            raise ValueError("Invalid Cell payload quarantine item name")
        expected_source = (self._cell_payload_directory / expected_name).resolve()
        expected_quarantine = (
            Path(receipt.quarantine_directory) / expected_name
        ).resolve()
        if source.resolve() != expected_source or quarantine.resolve() != expected_quarantine:
            raise ValueError("Cell payload receipt path escaped maintenance roots")

    def _cache_inventory(
        self,
        connection: Connection,
    ) -> tuple[list[CacheEntrySnapshot], bool]:
        rows = tuple(
            connection.execute(text("""
                SELECT cache_key, storage_uri, created_at, last_accessed_at
                FROM workspace.research_materialization_cache
                ORDER BY cache_key
            """)).mappings()
        )
        try:
            protected, complete = self._cache_reference_keys(
                connection,
                tuple(str(row["cache_key"]) for row in rows),
            )
        except Exception:
            protected, complete = (set(), set()), False
        entries: list[CacheEntrySnapshot] = []
        for row in rows:
            key = str(row["cache_key"])
            paths, valid = self._cache_paths(key, str(row["storage_uri"]))
            entries.append(
                CacheEntrySnapshot(
                    cache_key=key,
                    storage_uri=str(row["storage_uri"]),
                    created_at=row["created_at"],
                    last_accessed_at=row["last_accessed_at"],
                    parquet_bytes=paths[0].stat().st_size if paths[0].exists() else 0,
                    manifest_bytes=paths[1].stat().st_size if paths[1].exists() else 0,
                    product_referenced=key in protected[0],
                    active_referenced=key in protected[1],
                    path_valid=valid,
                )
            )
        return entries, complete

    @staticmethod
    def _cache_reference_keys(
        connection: Connection,
        cache_keys: Sequence[str],
    ) -> tuple[tuple[set[str], set[str]], bool]:
        if not cache_keys:
            return (set(), set()), True
        product_documents = connection.execute(text("""
            SELECT qualification.selection_context AS document
            FROM product.product_version product_version
            JOIN experiment.qualification_bundle qualification
              ON qualification.qualification_bundle_id =
                 product_version.qualification_bundle_id
            UNION ALL
            SELECT spec.normalized_selection AS document
            FROM product.product_version product_version
            JOIN strategy.compiled_strategy_version strategy
              ON strategy.compiled_strategy_version_id =
                 product_version.compiled_strategy_version_id
            JOIN workspace.compiled_research_spec spec
              ON spec.compiled_research_spec_id = strategy.compiled_research_spec_id
        """)).scalars()
        active_documents = connection.execute(text("""
            SELECT event.details AS document
            FROM ops.work_item work
            JOIN ops.work_item_event event ON event.work_item_id = work.work_item_id
            WHERE work.status IN ('queued','running')
            UNION ALL
            SELECT work.failure_details AS document
            FROM ops.work_item work
            WHERE work.status IN ('queued','running') AND work.failure_details IS NOT NULL
        """)).scalars()
        product = StorageMaintenanceService._keys_in_documents(cache_keys, product_documents)
        active = StorageMaintenanceService._keys_in_documents(cache_keys, active_documents)
        return (product, active), True

    @staticmethod
    def _keys_in_documents(
        cache_keys: Sequence[str],
        documents: Iterable[Any],
    ) -> set[str]:
        remaining = set(cache_keys)
        found: set[str] = set()
        for document in documents:
            if not remaining:
                break
            serialized = json.dumps(document, sort_keys=True, default=str)
            matches = {key for key in remaining if key in serialized}
            found.update(matches)
            remaining.difference_update(matches)
        return found

    def _cache_paths(self, cache_key: str, storage_uri: str) -> tuple[tuple[Path, Path], bool]:
        expected = (self._cache_directory / f"{cache_key}.parquet").resolve()
        manifest = (self._cache_directory / f"{cache_key}.manifest.json").resolve()
        supplied = Path(storage_uri)
        if not supplied.is_absolute():
            supplied = (Path.cwd() / supplied).resolve()
        else:
            supplied = supplied.resolve()
        valid = (
            len(cache_key) == 64
            and all(character in "0123456789abcdef" for character in cache_key)
            and expected.parent == self._cache_directory
            and manifest.parent == self._cache_directory
            and supplied == expected
        )
        return (expected, manifest), valid

    @staticmethod
    def _archive_record(row: RowMapping, manifest: Mapping[str, Any]) -> dict[str, str]:
        return {
            "lineage_manifest_id": str(row["lineage_manifest_id"]),
            "root_artifact_id": str(row["root_artifact_id"]),
            "artifact_type": str(row["artifact_type"]),
            "artifact_key": str(row["artifact_key"]),
            "semantic_fingerprint": str(row["semantic_fingerprint"]),
            "content_hash": str(row["content_hash"]),
            "manifest_hash": str(row["manifest_hash"]),
            "canonical_version": str(row["canonical_version"]),
            "created_at": row["created_at"].isoformat(),
            "manifest_json": json.dumps(
                manifest,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            ),
        }

    @staticmethod
    def _verify_archive(
        path: Path,
        *,
        expected: Sequence[Mapping[str, str]],
        archive_sha256: str,
    ) -> None:
        if StorageMaintenanceService._sha256_file(path) != archive_sha256:
            raise RuntimeError("Lineage archive file hash mismatch")
        restored = pd.read_parquet(path).to_dict(orient="records")
        if len(restored) != len(expected):
            raise RuntimeError("Lineage archive restore count mismatch")
        expected_by_id = {item["lineage_manifest_id"]: item for item in expected}
        for item in restored:
            identifier = str(item["lineage_manifest_id"])
            source = expected_by_id.get(identifier)
            if source is None:
                raise RuntimeError(f"Unexpected lineage archive row: {identifier}")
            manifest = json.loads(str(item["manifest_json"]))
            if sha256_hexdigest(manifest) != str(item["manifest_hash"]):
                raise RuntimeError(f"Lineage archive restore hash mismatch: {identifier}")
            for field_name in (
                "root_artifact_id",
                "artifact_type",
                "artifact_key",
                "semantic_fingerprint",
                "content_hash",
                "manifest_hash",
                "canonical_version",
                "created_at",
            ):
                if str(item[field_name]) != source[field_name]:
                    raise RuntimeError(
                        f"Lineage archive restore metadata mismatch: {identifier}/{field_name}"
                    )

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _jsonable_tombstone(item: ArtifactTombstone) -> dict[str, Any]:
        payload = asdict(item)
        for key in ("lineage_manifest_id", "root_artifact_id"):
            payload[key] = str(payload[key])
        payload["created_at"] = item.created_at.isoformat()
        return payload

    @staticmethod
    def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
