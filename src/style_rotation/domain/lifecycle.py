from __future__ import annotations

from style_rotation.domain.enums import (
    AlertStatus,
    ArchiveStatus,
    ProductLifecycle,
    RunStatus,
    WorkItemStatus,
)

_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.RUNNING, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset({RunStatus.COMPLETED, RunStatus.FAILED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
}

_ARCHIVE_TRANSITIONS: dict[ArchiveStatus, frozenset[ArchiveStatus]] = {
    ArchiveStatus.PENDING: frozenset({ArchiveStatus.VERIFIED, ArchiveStatus.FAILED}),
    ArchiveStatus.VERIFIED: frozenset({ArchiveStatus.RESTORE_TESTED, ArchiveStatus.FAILED}),
    ArchiveStatus.RESTORE_TESTED: frozenset(),
    ArchiveStatus.FAILED: frozenset(),
}

_WORK_ITEM_TRANSITIONS: dict[WorkItemStatus, frozenset[WorkItemStatus]] = {
    WorkItemStatus.QUEUED: frozenset(
        {WorkItemStatus.RUNNING, WorkItemStatus.CANCELLED, WorkItemStatus.REUSED}
    ),
    WorkItemStatus.RUNNING: frozenset(
        {WorkItemStatus.COMPLETED, WorkItemStatus.FAILED, WorkItemStatus.CANCELLED}
    ),
    WorkItemStatus.COMPLETED: frozenset(),
    WorkItemStatus.FAILED: frozenset({WorkItemStatus.QUEUED}),
    WorkItemStatus.CANCELLED: frozenset(),
    WorkItemStatus.REUSED: frozenset(),
}

_PRODUCT_TRANSITIONS: dict[ProductLifecycle, frozenset[ProductLifecycle]] = {
    ProductLifecycle.ACTIVE: frozenset(
        {ProductLifecycle.SUSPENDED, ProductLifecycle.RETIRED, ProductLifecycle.INVALIDATED}
    ),
    ProductLifecycle.SUSPENDED: frozenset(
        {ProductLifecycle.ACTIVE, ProductLifecycle.RETIRED, ProductLifecycle.INVALIDATED}
    ),
    ProductLifecycle.RETIRED: frozenset(),
    ProductLifecycle.INVALIDATED: frozenset(),
}

_ALERT_TRANSITIONS: dict[AlertStatus, frozenset[AlertStatus]] = {
    AlertStatus.OPEN: frozenset(
        {AlertStatus.ACKNOWLEDGED, AlertStatus.RESOLVED, AlertStatus.SUPERSEDED}
    ),
    AlertStatus.ACKNOWLEDGED: frozenset({AlertStatus.RESOLVED, AlertStatus.SUPERSEDED}),
    AlertStatus.RESOLVED: frozenset(),
    AlertStatus.SUPERSEDED: frozenset(),
}


def ensure_run_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in _RUN_TRANSITIONS[current]:
        raise ValueError(f"Invalid run status transition: {current.value} -> {target.value}")


def ensure_archive_transition(current: ArchiveStatus, target: ArchiveStatus) -> None:
    if target not in _ARCHIVE_TRANSITIONS[current]:
        raise ValueError(f"Invalid archive status transition: {current.value} -> {target.value}")


def ensure_work_item_transition(current: WorkItemStatus, target: WorkItemStatus) -> None:
    if target not in _WORK_ITEM_TRANSITIONS[current]:
        raise ValueError(f"Invalid work item transition: {current.value} -> {target.value}")


def ensure_product_transition(current: ProductLifecycle, target: ProductLifecycle) -> None:
    if target not in _PRODUCT_TRANSITIONS[current]:
        raise ValueError(f"Invalid Product lifecycle transition: {current.value} -> {target.value}")


def ensure_alert_transition(current: AlertStatus, target: AlertStatus) -> None:
    if target not in _ALERT_TRANSITIONS[current]:
        raise ValueError(f"Invalid alert transition: {current.value} -> {target.value}")
