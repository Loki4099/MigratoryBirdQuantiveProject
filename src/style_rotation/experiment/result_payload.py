from __future__ import annotations

import hmac
import importlib
import json
import os
import re
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from style_rotation.config.settings import get_settings
from style_rotation.core.canonical import sha256_hexdigest

PAYLOAD_SCHEMA_VERSION = "v021_cell_payload_v1"
PAYLOAD_STORAGE_FORMAT = "parquet_zstd_json_v1"
_URI_PATTERN = re.compile(r"^cell-result://sha256/([0-9a-f]{64})\.parquet$")
_PENDING_PATTERN = re.compile(r"^\.([0-9a-f]{64})\.([0-9a-f]{32})\.pending$")
_DIAGNOSTIC_SUMMARY_KEYS = (
    "executor",
    "data_bundle_artifact_id",
    "predictive_result_artifact_id",
    "signal_dataset_artifact_ids",
    "forward_return_dataset_artifact_id",
    "pit_gate_artifact_id",
    "terminal_gate_artifact_id",
    "impact_gate_artifact_id",
    "suite_mode",
    "requested_start",
    "requested_end",
    "resolved_start",
    "resolved_end",
    "normalization_nav_date",
    "observation_count",
    "normalization_policy",
    "tie_policy",
    "missing_policy",
    "capacity_status",
    "quality_checks",
)


class CellResultPayloadError(RuntimeError):
    """Raised when an external Cell payload is missing, corrupt, or inconsistent."""


@dataclass(frozen=True, slots=True)
class ExternalCellPayload:
    storage_uri: str
    content_hash: str
    storage_format: str
    schema_version: str
    byte_size: int
    series_summary: dict[str, Any]
    diagnostics_summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CellPayloadPublicationLease:
    payload: ExternalCellPayload
    marker_name: str


class CellResultPayloadStore:
    """Content-addressed storage for the large part of a v0.21 Cell Result.

    Parquet contains one compressed binary JSON document for `series` and one for
    `diagnostics`.  Keeping the JSON boundary preserves the exact API contract while
    avoiding repeated multi-megabyte JSONB values in PostgreSQL.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory or Path(get_settings().cell_result_directory)

    def externalize(
        self, *, series: Mapping[str, Any], diagnostics: Mapping[str, Any]
    ) -> ExternalCellPayload:
        with self.directory_lock():
            return self._externalize_unlocked(series=series, diagnostics=diagnostics)

    def stage_publication(
        self,
        *,
        series: Mapping[str, Any],
        diagnostics: Mapping[str, Any],
        owner_work_item_id: uuid.UUID,
    ) -> CellPayloadPublicationLease:
        """Publish a payload plus an in-flight marker consumed by safe sweepers."""

        with self.directory_lock():
            payload = self._externalize_unlocked(series=series, diagnostics=diagnostics)
            token = uuid.uuid4().hex
            marker_name = f".{payload.content_hash}.{token}.pending"
            marker = self._directory / marker_name
            marker.write_text(
                json.dumps(
                    {
                        "schema_version": "v021_cell_payload_pending_v1",
                        "content_hash": payload.content_hash,
                        "owner_work_item_id": str(owner_work_item_id),
                        "created_at_epoch": time.time(),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            return CellPayloadPublicationLease(payload=payload, marker_name=marker_name)

    def finalize_publication(self, lease: CellPayloadPublicationLease) -> None:
        """Idempotently clear a publication marker after database commit or rollback."""

        match = _PENDING_PATTERN.fullmatch(lease.marker_name)
        if match is None or match.group(1) != lease.payload.content_hash:
            raise CellResultPayloadError("Invalid Cell payload publication marker")
        try:
            with self.directory_lock():
                (self._directory / lease.marker_name).unlink(missing_ok=True)
        except (OSError, TimeoutError):
            # A stale marker leaks storage but remains fail-safe: sweepers keep the
            # hash pinned until an operator audits the marker.
            return

    @contextmanager
    def directory_lock(self, *, timeout_seconds: float = 30.0) -> Iterator[None]:
        """Hold the cross-process lock shared by Result publication and sweeping."""

        if timeout_seconds <= 0:
            raise ValueError("Cell payload lock timeout must be positive")
        self._directory.mkdir(parents=True, exist_ok=True)
        lock_path = self._directory / ".cell-payload.lock"
        with lock_path.open("a+b") as stream:
            if stream.tell() == 0 and lock_path.stat().st_size == 0:
                stream.write(b"\0")
                stream.flush()
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    _lock_stream(stream)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Timed out waiting for Cell payload directory lock"
                        ) from exc
                    time.sleep(0.05)
            try:
                yield
            finally:
                _unlock_stream(stream)

    def pending_content_hashes(self) -> set[str]:
        """Return in-flight hashes. Callers performing a sweep must already hold the lock."""

        pending: set[str] = set()
        if not self._directory.exists():
            return pending
        for path in self._directory.iterdir():
            match = _PENDING_PATTERN.fullmatch(path.name)
            if match is not None and path.is_file() and not path.is_symlink():
                pending.add(match.group(1))
        return pending

    def ensure_reference_available(self, payload: ExternalCellPayload) -> None:
        """Cheap post-lock check used immediately before inserting a DB reference."""

        match = _URI_PATTERN.fullmatch(payload.storage_uri)
        if match is None or match.group(1) != payload.content_hash:
            raise CellResultPayloadError("Cell payload URI does not match its content hash")
        path = self._path(payload.content_hash)
        if not path.is_file() or path.is_symlink():
            raise CellResultPayloadError(f"Cell payload file is missing: {path}")
        if path.stat().st_size != payload.byte_size:
            raise CellResultPayloadError("Cell payload byte size changed before publication")

    def _externalize_unlocked(
        self, *, series: Mapping[str, Any], diagnostics: Mapping[str, Any]
    ) -> ExternalCellPayload:
        normalized_series, series_json = _normalized_json(series)
        normalized_diagnostics, diagnostics_json = _normalized_json(diagnostics)
        content_hash = sha256_hexdigest(
            {
                "schema_version": PAYLOAD_SCHEMA_VERSION,
                "series": normalized_series,
                "diagnostics": normalized_diagnostics,
            }
        )
        storage_uri = f"cell-result://sha256/{content_hash}.parquet"
        destination = self._path(content_hash)
        self._directory.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._read_and_verify(destination, content_hash)
        else:
            temporary = self._directory / f"{content_hash}.{uuid.uuid4().hex}.tmp"
            try:
                table = pa.table(
                    {
                        "schema_version": [PAYLOAD_SCHEMA_VERSION],
                        "content_hash": [content_hash],
                        "series_json": [series_json],
                        "diagnostics_json": [diagnostics_json],
                    }
                )
                pq.write_table(
                    table,
                    temporary,
                    compression="zstd",
                    compression_level=9,
                    use_dictionary=False,
                    write_statistics=False,
                )
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            self._read_and_verify(destination, content_hash)
        return ExternalCellPayload(
            storage_uri=storage_uri,
            content_hash=content_hash,
            storage_format=PAYLOAD_STORAGE_FORMAT,
            schema_version=PAYLOAD_SCHEMA_VERSION,
            byte_size=destination.stat().st_size,
            series_summary=_series_summary(normalized_series),
            diagnostics_summary=_diagnostics_summary(normalized_diagnostics),
        )

    def load(
        self,
        *,
        storage_uri: str,
        content_hash: str,
        storage_format: str,
        schema_version: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if storage_format != PAYLOAD_STORAGE_FORMAT:
            raise CellResultPayloadError(f"Unsupported Cell payload format: {storage_format}")
        if schema_version != PAYLOAD_SCHEMA_VERSION:
            raise CellResultPayloadError(f"Unsupported Cell payload schema: {schema_version}")
        match = _URI_PATTERN.fullmatch(storage_uri)
        if match is None or match.group(1) != content_hash:
            raise CellResultPayloadError("Cell payload URI does not match its content hash")
        return self._read_and_verify(self._path(content_hash), content_hash)

    def _path(self, content_hash: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
            raise CellResultPayloadError("Invalid Cell payload content hash")
        return self._directory / f"{content_hash}.parquet"

    @staticmethod
    def _read_and_verify(
        path: Path, expected_hash: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not path.is_file():
            raise CellResultPayloadError(f"Cell payload file is missing: {path}")
        try:
            table = pq.read_table(path)
            if table.num_rows != 1:
                raise CellResultPayloadError("Cell payload Parquet must contain exactly one row")
            row = table.to_pylist()[0]
            if row.get("schema_version") != PAYLOAD_SCHEMA_VERSION:
                raise CellResultPayloadError("Cell payload Parquet schema version mismatch")
            if row.get("content_hash") != expected_hash:
                raise CellResultPayloadError("Cell payload Parquet hash metadata mismatch")
            series = json.loads(bytes(row["series_json"]))
            diagnostics = json.loads(bytes(row["diagnostics_json"]))
        except CellResultPayloadError:
            raise
        except Exception as exc:
            raise CellResultPayloadError(f"Cannot read Cell payload: {path}") from exc
        if not isinstance(series, dict) or not isinstance(diagnostics, dict):
            raise CellResultPayloadError("Cell payload roots must be JSON objects")
        observed_hash = sha256_hexdigest(
            {
                "schema_version": PAYLOAD_SCHEMA_VERSION,
                "series": series,
                "diagnostics": diagnostics,
            }
        )
        if not hmac.compare_digest(observed_hash, expected_hash):
            raise CellResultPayloadError("Cell payload content hash verification failed")
        return series, diagnostics


def hydrate_cell_result_row(
    row: Mapping[Any, Any], *, store: CellResultPayloadStore | None = None
) -> dict[str, Any]:
    """Return a mutable Result row with full payload, inline or externalized."""

    result = dict(row)
    storage_uri = result.get("payload_storage_uri")
    if storage_uri is None:
        result["series"] = dict(result.get("series") or {})
        result["diagnostics"] = dict(result.get("diagnostics") or {})
        return result
    payload_store = store or CellResultPayloadStore()
    series, diagnostics = payload_store.load(
        storage_uri=str(storage_uri),
        content_hash=str(result["payload_content_hash"]),
        storage_format=str(result["payload_storage_format"]),
        schema_version=str(result["payload_schema_version"]),
    )
    result["series"] = series
    result["diagnostics"] = diagnostics
    return result


def _normalized_json(value: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    encoded = json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    normalized = json.loads(encoded)
    if not isinstance(normalized, dict):
        raise TypeError("Cell payload roots must be JSON objects")
    return normalized, encoded


def _series_summary(series: Mapping[str, Any]) -> dict[str, Any]:
    collections: dict[str, dict[str, Any]] = {}
    for key, value in series.items():
        if isinstance(value, (list, dict)):
            collections[key] = {"kind": type(value).__name__, "count": len(value)}
        else:
            collections[key] = {"kind": type(value).__name__}
    return {
        "externalized": True,
        "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
        "collections": collections,
    }


def _diagnostics_summary(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    summary = {
        key: diagnostics[key]
        for key in _DIAGNOSTIC_SUMMARY_KEYS
        if key in diagnostics
    }
    summary["externalized"] = True
    summary["payload_schema_version"] = PAYLOAD_SCHEMA_VERSION
    summary["available_keys"] = sorted(diagnostics)
    return summary


def _lock_stream(stream: Any) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return
    fcntl: Any = importlib.import_module("fcntl")
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_stream(stream: Any) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl: Any = importlib.import_module("fcntl")
    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
