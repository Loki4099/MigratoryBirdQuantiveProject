from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.domain.enums import WorkFailureClass
from style_rotation.ops.work_queue import WorkQueueService
from style_rotation.ops.worker import (
    CancellationRequested,
    ClassifiedWorkFailure,
    WorkerOutcome,
)
from style_rotation.signal.research_export import SignalResearchExport, SignalResearchExportService

ExportStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
EXPORT_SCHEMA_VERSION = "signal_research_export_zip_v1"
EXPORT_FILENAME = "migratory_bird_signal_research.zip"


@dataclass(frozen=True, slots=True)
class SignalExportJob:
    export_job_id: uuid.UUID
    work_item_id: uuid.UUID
    request_fingerprint: str
    status: ExportStatus
    stage: str
    attempt_count: int
    max_attempts: int
    failure_class: str | None
    failure_details: dict[str, Any]
    content_hash: str | None
    byte_size: int | None
    filename: str | None
    expires_at: datetime | None

    @classmethod
    def from_row(cls, row: RowMapping) -> SignalExportJob:
        details = row["failure_details"]
        return cls(
            export_job_id=row["export_job_id"],
            work_item_id=row["work_item_id"],
            request_fingerprint=row["request_fingerprint"],
            status=cast(ExportStatus, row["status"]),
            stage=row["stage"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            failure_class=row["failure_class"],
            failure_details=dict(details) if isinstance(details, dict) else {},
            content_hash=row["content_hash"],
            byte_size=row["byte_size"],
            filename=row["filename"],
            expires_at=row["expires_at"],
        )


@dataclass(frozen=True, slots=True)
class ValidatedSignalExport:
    path: Path
    filename: str
    content_hash: str
    byte_size: int


class SignalResearchExportJobService:
    """Persistent export queue and fail-closed download resolver."""

    def __init__(self, engine: Engine, *, directory: Path | str) -> None:
        self._engine = engine
        self._directory = Path(directory).resolve()

    def enqueue(self, request_document: dict[str, Any]) -> SignalExportJob:
        fingerprint = sha256_hexdigest(request_document)
        request_json = json.dumps(request_document, sort_keys=True, separators=(",", ":"))
        export_job_id = uuid.uuid4()
        work_item_id = uuid.uuid4()
        with self._engine.begin() as connection:
            # Serialize equal requests so a double click cannot create two large exports.
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:fingerprint, 0))"),
                {"fingerprint": fingerprint},
            )
            existing = (
                connection.execute(
                    text(
                        """
                        SELECT job.export_job_id, job.work_item_id, job.request_fingerprint,
                               work.status, work.stage, work.attempt_count, work.max_attempts,
                               work.failure_class, work.failure_details,
                               result.content_hash, result.byte_size, result.filename,
                               result.expires_at
                        FROM signal.research_export_job job
                        JOIN ops.work_item work ON work.work_item_id = job.work_item_id
                        LEFT JOIN signal.research_export_result result
                          ON result.export_job_id = job.export_job_id
                        WHERE job.request_fingerprint = :fingerprint
                          AND (
                            work.status IN ('queued', 'running')
                            OR (
                              work.status = 'completed'
                              AND result.export_result_id IS NOT NULL
                              AND result.expires_at > now()
                            )
                          )
                        ORDER BY job.created_at DESC
                        LIMIT 1
                        """
                    ),
                    {"fingerprint": fingerprint},
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                existing_job = SignalExportJob.from_row(existing)
                if existing_job.status != "completed" or self._completed_file_is_valid(
                    existing_job
                ):
                    return existing_job
            connection.execute(
                text(
                    """
                    INSERT INTO ops.work_item (
                        work_item_id, specification_fingerprint, work_type, priority, max_attempts
                    ) VALUES (:work_item_id, :fingerprint, 'export', 100, 3)
                    """
                ),
                {"work_item_id": work_item_id, "fingerprint": fingerprint},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO ops.work_item_event (
                        work_item_event_id, work_item_id, sequence_number, event_type,
                        from_status, to_status, details
                    ) VALUES (:event_id, :work_item_id, 1, 'enqueued', NULL, 'queued', '{}'::jsonb)
                    """
                ),
                {"event_id": uuid.uuid4(), "work_item_id": work_item_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO signal.research_export_job (
                        export_job_id, work_item_id, request_fingerprint, request_document
                    ) VALUES (
                        :export_job_id, :work_item_id, :fingerprint, CAST(:request AS jsonb)
                    )
                    """
                ),
                {
                    "export_job_id": export_job_id,
                    "work_item_id": work_item_id,
                    "fingerprint": fingerprint,
                    "request": request_json,
                },
            )
        return self.get(export_job_id)

    def get(self, export_job_id: uuid.UUID) -> SignalExportJob:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT job.export_job_id, job.work_item_id, job.request_fingerprint,
                               work.status, work.stage, work.attempt_count, work.max_attempts,
                               work.failure_class, work.failure_details,
                               result.content_hash, result.byte_size, result.filename,
                               result.expires_at
                        FROM signal.research_export_job job
                        JOIN ops.work_item work ON work.work_item_id = job.work_item_id
                        LEFT JOIN signal.research_export_result result
                          ON result.export_job_id = job.export_job_id
                        WHERE job.export_job_id = :export_job_id
                        """
                    ),
                    {"export_job_id": export_job_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError(f"Signal export job not found: {export_job_id}")
        return SignalExportJob.from_row(row)

    def validated_download(self, export_job_id: uuid.UUID) -> ValidatedSignalExport:
        job = self.get(export_job_id)
        if (
            job.status != "completed"
            or not job.content_hash
            or not job.byte_size
            or job.expires_at is None
            or job.expires_at <= datetime.now(UTC)
        ):
            raise LookupError(f"Signal export is not available: {export_job_id}")
        path = self._path_for_hash(job.content_hash)
        expected_uri = f"signal-export://sha256/{job.content_hash}.zip"
        with self._engine.connect() as connection:
            storage_uri = connection.execute(
                text(
                    "SELECT storage_uri FROM signal.research_export_result "
                    "WHERE export_job_id = :export_job_id"
                ),
                {"export_job_id": export_job_id},
            ).scalar_one_or_none()
        if storage_uri != expected_uri:
            raise RuntimeError("Signal export storage URI failed validation")
        if not path.is_file() or path.stat().st_size != job.byte_size:
            raise RuntimeError("Signal export file is missing or has an invalid size")
        if _file_sha256(path) != job.content_hash:
            raise RuntimeError("Signal export file content hash failed validation")
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    "UPDATE signal.research_export_result SET last_accessed_at = now() "
                    "WHERE export_job_id = :export_job_id AND expires_at > now()"
                ),
                {"export_job_id": export_job_id},
            )
            if updated.rowcount != 1:
                raise LookupError(f"Signal export expired during validation: {export_job_id}")
        return ValidatedSignalExport(
            path=path,
            filename=job.filename or EXPORT_FILENAME,
            content_hash=job.content_hash,
            byte_size=job.byte_size,
        )

    def _path_for_hash(self, content_hash: str) -> Path:
        if len(content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in content_hash
        ):
            raise RuntimeError("Signal export content hash is invalid")
        path = (self._directory / content_hash[:2] / f"{content_hash}.zip").resolve()
        if not path.is_relative_to(self._directory):
            raise RuntimeError("Signal export path escaped its storage root")
        return path

    def _completed_file_is_valid(self, job: SignalExportJob) -> bool:
        if (
            not job.content_hash
            or not job.byte_size
            or job.expires_at is None
            or job.expires_at <= datetime.now(UTC)
        ):
            return False
        try:
            path = self._path_for_hash(job.content_hash)
            return (
                path.is_file()
                and path.stat().st_size == job.byte_size
                and _file_sha256(path) == job.content_hash
            )
        except OSError:
            return False


class SignalResearchExportWorker:
    """Independent persistent worker for queued Signal research packages."""

    def __init__(
        self,
        engine: Engine,
        *,
        worker_id: str,
        directory: Path | str,
        export_service: SignalResearchExportService | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("Worker id is required")
        self._engine = engine
        self._worker_id = worker_id
        self._queue = WorkQueueService(engine)
        self._jobs = SignalResearchExportJobService(engine, directory=directory)
        self._exports = export_service or SignalResearchExportService(engine)

    def run_once(self) -> WorkerOutcome:
        item = self._queue.claim(worker_id=self._worker_id, work_types=("export",))
        if item is None:
            return WorkerOutcome(uuid.UUID(int=0), "idle")
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()

        def keep_lease() -> None:
            while not stop_heartbeat.wait(30):
                try:
                    self._queue.heartbeat(item.work_item_id, worker_id=self._worker_id)
                except RuntimeError:
                    lease_lost.set()
                    return

        heartbeat = threading.Thread(
            target=keep_lease,
            name=f"signal-export-heartbeat-{item.work_item_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            export_job_id, request = self._request_for_work_item(item.work_item_id)
            if self._queue.cancellation_requested(item.work_item_id, worker_id=self._worker_id):
                raise CancellationRequested
            package = self._build(request)
            content_hash = hashlib.sha256(package.content).hexdigest()
            path = self._jobs._path_for_hash(content_hash)
            self._write_content_addressed(path, package.content, content_hash)
            if lease_lost.is_set():
                raise ClassifiedWorkFailure(
                    WorkFailureClass.INTERRUPTED, "Export Work Item lease was lost"
                )
            if self._queue.cancellation_requested(item.work_item_id, worker_id=self._worker_id):
                raise CancellationRequested
            self._complete(
                export_job_id=export_job_id,
                work_item_id=item.work_item_id,
                package=package,
                content_hash=content_hash,
                byte_size=len(package.content),
            )
            return WorkerOutcome(item.work_item_id, "completed")
        except CancellationRequested:
            self._queue.finish(item.work_item_id, worker_id=self._worker_id, status="cancelled")
            return WorkerOutcome(item.work_item_id, "cancelled")
        except ClassifiedWorkFailure as error:
            return self._fail(item.work_item_id, error.failure_class, str(error), error.details)
        except LookupError as error:
            return self._fail(item.work_item_id, WorkFailureClass.DATA_QUALITY, str(error), {})
        except ValueError as error:
            return self._fail(item.work_item_id, WorkFailureClass.CONTRACT, str(error), {})
        except Exception as error:
            return self._fail(
                item.work_item_id,
                WorkFailureClass.INFRASTRUCTURE,
                str(error),
                {"type": type(error).__name__},
            )
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2)

    def _fail(
        self,
        work_item_id: uuid.UUID,
        failure_class: WorkFailureClass,
        message: str,
        details: dict[str, Any],
    ) -> WorkerOutcome:
        failed = self._queue.finish(
            work_item_id,
            worker_id=self._worker_id,
            status="failed",
            failure_class=failure_class,
            failure_details={"message": message, **details},
        )
        retryable = failure_class in {
            WorkFailureClass.INFRASTRUCTURE,
            WorkFailureClass.INTERRUPTED,
        }
        if retryable and failed.attempt_count < failed.max_attempts:
            self._queue.retry(work_item_id)
            return WorkerOutcome(work_item_id, "retrying")
        return WorkerOutcome(work_item_id, "failed")

    def _request_for_work_item(
        self, work_item_id: uuid.UUID
    ) -> tuple[uuid.UUID, dict[str, Any]]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT export_job_id, request_document "
                        "FROM signal.research_export_job WHERE work_item_id = :work_item_id"
                    ),
                    {"work_item_id": work_item_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ClassifiedWorkFailure(
                WorkFailureClass.CONTRACT, "Export Work Item has no export job binding"
            )
        document = row["request_document"]
        if not isinstance(document, dict):
            raise ClassifiedWorkFailure(
                WorkFailureClass.CONTRACT, "Export request document is invalid"
            )
        return row["export_job_id"], dict(document)

    def _build(self, request: dict[str, Any]) -> SignalResearchExport:
        try:
            raw_security_ids = request["asset_security_ids"]
            raw_inputs = request["asset_data_inputs"]
            raw_signal_keys = request["signal_version_keys"]
            frequency = request["frequency"]
            include_targets = request["include_targets"]
        except KeyError as error:
            raise ValueError(f"Export request is missing {error.args[0]}") from error
        if not isinstance(raw_security_ids, list) or not raw_security_ids:
            raise ValueError("Export asset selection must be a non-empty list")
        if not isinstance(raw_signal_keys, list) or not raw_signal_keys:
            raise ValueError("Export Signal selection must be a non-empty list")
        if not isinstance(include_targets, bool):
            raise ValueError("Export include_targets must be boolean")
        try:
            security_ids = tuple(uuid.UUID(str(value)) for value in raw_security_ids)
        except (TypeError, ValueError) as error:
            raise ValueError("Export asset selection contains an invalid UUID") from error
        signal_keys = tuple(str(value) for value in raw_signal_keys)
        if len(security_ids) != len(set(security_ids)) or len(signal_keys) != len(
            set(signal_keys)
        ):
            raise ValueError("Export selections must be unique")
        if any(not value.strip() for value in signal_keys):
            raise ValueError("Export Signal selection contains an empty key")
        if not isinstance(raw_inputs, dict):
            raise ValueError("Export asset data inputs must be a mapping")
        try:
            asset_data_inputs = {
                uuid.UUID(str(security_id)): tuple(str(value) for value in input_keys)
                for security_id, input_keys in raw_inputs.items()
            }
        except (TypeError, ValueError) as error:
            raise ValueError("Export asset data-input selection is invalid") from error
        if set(asset_data_inputs) != set(security_ids):
            raise ValueError("Export asset data inputs must exactly match selected assets")
        for input_keys in asset_data_inputs.values():
            if len(input_keys) != len(set(input_keys)) or input_keys != (
                "canonical_market_bars",
            ):
                raise ValueError("Export requires canonical_market_bars for every asset")
        if frequency not in {"weekly", "monthly"}:
            raise ValueError("Export frequency is invalid")
        return self._exports.build(
            security_ids=security_ids,
            asset_data_inputs=asset_data_inputs,
            signal_version_keys=signal_keys,
            frequency=cast(Literal["weekly", "monthly"], frequency),
            include_targets=include_targets,
        )

    def _write_content_addressed(
        self, path: Path, content: bytes, content_hash: str
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.stat().st_size != len(content) or _file_sha256(path) != content_hash:
                raise RuntimeError("Existing Signal export artifact failed validation")
            return
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(content)
            if _file_sha256(temporary) != content_hash:
                raise RuntimeError("Signal export artifact changed while being written")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _complete(
        self,
        *,
        export_job_id: uuid.UUID,
        work_item_id: uuid.UUID,
        package: SignalResearchExport,
        content_hash: str,
        byte_size: int,
    ) -> None:
        with self._engine.begin() as connection:
            item = (
                connection.execute(
                    text("SELECT * FROM ops.work_item WHERE work_item_id = :id FOR UPDATE"),
                    {"id": work_item_id},
                )
                .mappings()
                .one()
            )
            if item["status"] != "running" or item["lease_owner"] != self._worker_id:
                raise ClassifiedWorkFailure(
                    WorkFailureClass.INTERRUPTED,
                    "Only the active lease owner may publish an export",
                )
            if item["cancel_requested_at"] is not None:
                raise CancellationRequested
            connection.execute(
                text(
                    """
                    INSERT INTO signal.research_export_result (
                        export_result_id, export_job_id, storage_uri, content_hash,
                        byte_size, filename, schema_version
                    ) VALUES (
                        :result_id, :export_job_id, :storage_uri, :content_hash,
                        :byte_size, :filename, :schema_version
                    )
                    """
                ),
                {
                    "result_id": uuid.uuid4(),
                    "export_job_id": export_job_id,
                    "storage_uri": f"signal-export://sha256/{content_hash}.zip",
                    "content_hash": content_hash,
                    "byte_size": byte_size,
                    "filename": package.filename,
                    "schema_version": EXPORT_SCHEMA_VERSION,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE ops.work_item
                    SET status = 'completed', stage = 'completed', lease_owner = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL, updated_at = now()
                    WHERE work_item_id = :work_item_id
                    """
                ),
                {"work_item_id": work_item_id},
            )
            sequence = connection.execute(
                text(
                    "SELECT COALESCE(max(sequence_number), 0) + 1 "
                    "FROM ops.work_item_event WHERE work_item_id = :work_item_id"
                ),
                {"work_item_id": work_item_id},
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO ops.work_item_event (
                        work_item_event_id, work_item_id, sequence_number, event_type,
                        from_status, to_status, details
                    ) VALUES (
                        :event_id, :work_item_id, :sequence, 'completed',
                        'running', 'completed', CAST(:details AS jsonb)
                    )
                    """
                ),
                {
                    "event_id": uuid.uuid4(),
                    "work_item_id": work_item_id,
                    "sequence": sequence,
                    "details": json.dumps(
                        {"export_job_id": str(export_job_id), "content_hash": content_hash},
                        sort_keys=True,
                    ),
                },
            )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
