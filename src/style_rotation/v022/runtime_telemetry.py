from __future__ import annotations

import ctypes
import json
import os
import tempfile
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, Thread
from time import perf_counter
from types import TracebackType
from typing import Literal

RuntimeTelemetryEventKind = Literal["started", "completed", "failed"]


def default_runtime_telemetry_path() -> Path:
    override = os.environ.get("STYLE_ROTATION_V022_RUNTIME_TELEMETRY_PATH")
    if override:
        return Path(override).expanduser().resolve()
    local_root = os.environ.get("LOCALAPPDATA")
    base = Path(local_root) if local_root else Path(tempfile.gettempdir())
    return (base / "style-rotation" / "v022-services" / "runtime-telemetry.jsonl").resolve()


def process_rss_bytes() -> int | None:
    """Return the current process RSS without adding a runtime dependency."""

    if os.name == "nt":
        try:
            from ctypes import wintypes

            class _ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("page_fault_count", wintypes.DWORD),
                    ("peak_working_set_size", ctypes.c_size_t),
                    ("working_set_size", ctypes.c_size_t),
                    ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                    ("quota_paged_pool_usage", ctypes.c_size_t),
                    ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                    ("quota_non_paged_pool_usage", ctypes.c_size_t),
                    ("pagefile_usage", ctypes.c_size_t),
                    ("peak_pagefile_usage", ctypes.c_size_t),
                ]

            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_ProcessMemoryCounters),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            process = kernel32.GetCurrentProcess()
            succeeded = psapi.GetProcessMemoryInfo(
                process,
                ctypes.byref(counters),
                counters.cb,
            )
            return int(counters.working_set_size) if succeeded else None
        except (AttributeError, OSError, ValueError):
            return None
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")  # type: ignore[attr-defined]
        resident_pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
        return resident_pages * int(page_size)
    except (AttributeError, IndexError, OSError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class RuntimeTelemetryIdentity:
    worker_key: str
    stage: str
    research_suite_id: uuid.UUID | None = None
    graph_run_id: uuid.UUID | None = None
    graph_work_item_id: uuid.UUID | None = None
    work_kind: str | None = None


class LocalRuntimeTelemetry:
    """Append-only local operational telemetry; never enters research identity."""

    def __init__(self, path: Path | None = None, *, sample_seconds: float = 0.5) -> None:
        if sample_seconds <= 0:
            raise ValueError("Runtime telemetry sample interval must be positive")
        self.path = (path or default_runtime_telemetry_path()).resolve()
        self.sample_seconds = sample_seconds
        self._write_lock = Lock()
        self.write_error_count = 0

    def span(
        self,
        identity: RuntimeTelemetryIdentity,
        *,
        details: dict[str, object] | None = None,
    ) -> RuntimeTelemetrySpan:
        return RuntimeTelemetrySpan(self, identity, details or {})

    def _append(self, document: dict[str, object]) -> None:
        encoded = json.dumps(document, default=str, ensure_ascii=False, sort_keys=True) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._write_lock, self.path.open(
                "a", encoding="utf-8", newline=""
            ) as stream:
                stream.write(encoded)
                stream.flush()
        except OSError:
            # Operational telemetry must never alter research execution semantics.
            # The counter keeps the failure observable to the hosting process/tests.
            with self._write_lock:
                self.write_error_count += 1


class RuntimeTelemetrySpan(AbstractContextManager["RuntimeTelemetrySpan"]):
    def __init__(
        self,
        telemetry: LocalRuntimeTelemetry,
        identity: RuntimeTelemetryIdentity,
        details: dict[str, object],
    ) -> None:
        self._telemetry = telemetry
        self._identity = identity
        self._details = details
        self._details_lock = Lock()
        self._span_id = uuid.uuid4()
        self._started_at = datetime.now(UTC)
        self._started_clock = perf_counter()
        self._peak_rss_bytes = process_rss_bytes()
        self._stop = Event()
        self._sampler = Thread(
            target=self._sample,
            name=f"v022-telemetry-{identity.stage}",
            daemon=True,
        )

    def __enter__(self) -> RuntimeTelemetrySpan:
        self._telemetry._append(self._document("started"))
        self._sampler.start()
        return self

    def record(self, **details: object) -> None:
        """Attach operational counters without changing any research identity."""

        with self._details_lock:
            self._details.update(details)

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        del traceback
        self._stop.set()
        self._sampler.join()
        current = process_rss_bytes()
        if current is not None:
            self._peak_rss_bytes = max(self._peak_rss_bytes or current, current)
        with self._details_lock:
            details = dict(self._details)
        if error is not None:
            details.update(
                {
                    "error_type": error_type.__name__ if error_type is not None else None,
                    "message": str(error),
                }
            )
        self._telemetry._append(
            self._document(
                "failed" if error is not None else "completed",
                details=details,
                wall_time_ms=max(0, round((perf_counter() - self._started_clock) * 1000)),
            )
        )
        return None

    def _sample(self) -> None:
        while not self._stop.wait(self._telemetry.sample_seconds):
            current = process_rss_bytes()
            if current is not None:
                self._peak_rss_bytes = max(self._peak_rss_bytes or current, current)

    def _document(
        self,
        event_kind: RuntimeTelemetryEventKind,
        *,
        details: dict[str, object] | None = None,
        wall_time_ms: int | None = None,
    ) -> dict[str, object]:
        identity = self._identity
        return {
            "contract_version": "v0.22.0",
            "span_id": str(self._span_id),
            "event_kind": event_kind,
            "occurred_at": datetime.now(UTC).isoformat(),
            "started_at": self._started_at.isoformat(),
            "worker_key": identity.worker_key,
            "stage": identity.stage,
            "research_suite_id": identity.research_suite_id,
            "graph_run_id": identity.graph_run_id,
            "graph_work_item_id": identity.graph_work_item_id,
            "work_kind": identity.work_kind,
            "wall_time_ms": wall_time_ms,
            "peak_rss_bytes": self._peak_rss_bytes,
            "details": details if details is not None else dict(self._details),
        }


class PeriodicLeaseHeartbeat(AbstractContextManager["PeriodicLeaseHeartbeat"]):
    """Renew a fenced Work lease while synchronous execution is in progress."""

    def __init__(self, renew: Callable[[], None], *, interval_seconds: float = 30.0) -> None:
        if interval_seconds <= 0:
            raise ValueError("Lease heartbeat interval must be positive")
        self._renew = renew
        self._interval_seconds = interval_seconds
        self._stop = Event()
        self._error: BaseException | None = None
        self._thread = Thread(
            target=self._run,
            name="v022-graph-work-lease-heartbeat",
            daemon=True,
        )

    @property
    def error(self) -> BaseException | None:
        return self._error

    def __enter__(self) -> PeriodicLeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del error_type, error, traceback
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._renew()
            except BaseException as error:  # fail closed after the executor yields
                self._error = error
                self._stop.set()
                return
