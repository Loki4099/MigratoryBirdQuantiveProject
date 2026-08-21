from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

WorkerRuntimeState = Literal["ready", "working", "stopped", "error"]
_MAX_ERROR_SUMMARY_LENGTH = 1000


def _safe_error_summary(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return normalized[:_MAX_ERROR_SUMMARY_LENGTH]


def default_heartbeat_path() -> Path:
    override = os.environ.get("STYLE_ROTATION_V022_WORKER_HEARTBEAT_PATH")
    if override:
        return Path(override).expanduser().resolve()
    local_root = os.environ.get("LOCALAPPDATA")
    base = Path(local_root) if local_root else Path(tempfile.gettempdir())
    return (base / "style-rotation" / "v022-services" / "suite-worker.json").resolve()


@dataclass(frozen=True, slots=True)
class SuiteWorkerReadiness:
    ready: bool
    state: Literal["ready", "working", "stopped", "stale", "unavailable", "error"]
    worker_key: str | None
    process_id: int | None
    heartbeat_at: datetime | None
    max_age_seconds: int
    error_summary: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "state": self.state,
            "worker_key": self.worker_key,
            "process_id": self.process_id,
            "heartbeat_at": (
                self.heartbeat_at.isoformat() if self.heartbeat_at is not None else None
            ),
            "max_age_seconds": self.max_age_seconds,
            "error_summary": self.error_summary,
        }


class LocalSuiteWorkerHeartbeat:
    """Atomic local heartbeat shared by the API and detached Suite worker."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_heartbeat_path()).resolve()

    def write(
        self,
        *,
        worker_key: str,
        state: WorkerRuntimeState,
        error_summary: str | None = None,
    ) -> None:
        if not worker_key.strip():
            raise ValueError("v0.22 Suite worker key is required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "contract_version": "v0.22.0",
            "worker_key": worker_key,
            "process_id": os.getpid(),
            "state": state,
            "heartbeat_at": datetime.now(UTC).isoformat(),
            "error_summary": (
                _safe_error_summary(error_summary) if state == "error" else None
            ),
        }
        temporary = self.path.with_name(
            f".{self.path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
        )
        try:
            temporary.write_text(
                json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            for attempt in range(5):
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.01 * (attempt + 1))
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, *, max_age_seconds: int = 10) -> SuiteWorkerReadiness:
        if max_age_seconds < 1:
            raise ValueError("Suite worker heartbeat max age must be positive")
        if not self.path.is_file():
            return SuiteWorkerReadiness(
                False, "unavailable", None, None, None, max_age_seconds
            )
        try:
            document = cast(
                dict[str, object],
                json.loads(self.path.read_text(encoding="utf-8")),
            )
            if document.get("contract_version") != "v0.22.0":
                raise ValueError("Suite worker heartbeat contract is unsupported")
            worker_key = document.get("worker_key")
            process_id = document.get("process_id")
            raw_state = document.get("state")
            raw_error_summary = document.get("error_summary")
            heartbeat_at = datetime.fromisoformat(str(document["heartbeat_at"]))
            if heartbeat_at.tzinfo is None:
                raise ValueError("Suite worker heartbeat must be timezone-aware")
            if not isinstance(worker_key, str) or not worker_key.strip():
                raise ValueError("Suite worker heartbeat worker key is invalid")
            if isinstance(process_id, bool) or not isinstance(process_id, int):
                raise ValueError("Suite worker heartbeat process id is invalid")
            if raw_state not in {"ready", "working", "stopped", "error"}:
                raise ValueError("Suite worker heartbeat state is invalid")
            if raw_error_summary is not None and not isinstance(raw_error_summary, str):
                raise ValueError("Suite worker heartbeat error summary is invalid")
            state = cast(WorkerRuntimeState, raw_state)
            error_summary = _safe_error_summary(raw_error_summary)
            if state in {"stopped", "error"}:
                return SuiteWorkerReadiness(
                    False,
                    state,
                    worker_key,
                    process_id,
                    heartbeat_at,
                    max_age_seconds,
                    error_summary if state == "error" else None,
                )
            age = datetime.now(UTC) - heartbeat_at.astimezone(UTC)
            if age > timedelta(seconds=max_age_seconds):
                return SuiteWorkerReadiness(
                    False,
                    "stale",
                    worker_key,
                    process_id,
                    heartbeat_at,
                    max_age_seconds,
                )
            return SuiteWorkerReadiness(
                state in {"ready", "working"},
                state,
                worker_key,
                process_id,
                heartbeat_at,
                max_age_seconds,
                None,
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return SuiteWorkerReadiness(
                False, "error", None, None, None, max_age_seconds
            )
