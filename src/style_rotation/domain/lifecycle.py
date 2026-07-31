from __future__ import annotations

from style_rotation.domain.enums import ArchiveStatus, RunStatus

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


def ensure_run_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in _RUN_TRANSITIONS[current]:
        raise ValueError(f"Invalid run status transition: {current.value} -> {target.value}")


def ensure_archive_transition(current: ArchiveStatus, target: ArchiveStatus) -> None:
    if target not in _ARCHIVE_TRANSITIONS[current]:
        raise ValueError(f"Invalid archive status transition: {current.value} -> {target.value}")
