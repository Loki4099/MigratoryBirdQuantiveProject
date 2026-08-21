from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import uuid
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.domain.enums import WorkFailureClass
from style_rotation.ops.work_queue import WorkQueueService
from style_rotation.v022.workspace_context import active_v022_workspace_identity

ExportFormat = Literal["parquet", "csv"]
ExportStatus = Literal["queued", "running", "completed", "failed", "cancelled"]

REQUEST_CONTRACT = "v0.22.asset_data_export_request.v1"
PACKAGE_SCHEMA = "v022_asset_data_research_package_v1"
DEFAULT_FIELDS = (
    "open_raw",
    "high_raw",
    "low_raw",
    "close_raw",
    "open_adj",
    "high_adj",
    "low_adj",
    "close_adj",
    "adjustment_factor",
    "volume_raw",
)
MAX_BATCH_ROWS = 100_000
CSV_MAX_ASSETS = 10
CSV_MAX_ESTIMATED_BYTES = 100 * 1024 * 1024
MIN_FREE_SPACE_BYTES = 500 * 1024 * 1024


class AssetDataExportCancelled(RuntimeError):
    pass


def default_asset_export_cache_directory() -> Path:
    override = os.environ.get("STYLE_ROTATION_V022_ASSET_EXPORT_DIRECTORY")
    if override:
        return Path(override).expanduser().resolve()
    local = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    return (local / "MigratoryBird" / "asset_data_exports").resolve()


def default_asset_export_delivery_directory() -> Path:
    override = os.environ.get("STYLE_ROTATION_V022_ASSET_EXPORT_DOWNLOAD_DIRECTORY")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / "Downloads" / "MigratoryBirdExports").resolve()


@dataclass(frozen=True, slots=True)
class AssetDataExportPreview:
    graph_draft_id: uuid.UUID
    graph_draft_revision: int
    asset_registry_release_id: uuid.UUID
    dataset_publication_id: uuid.UUID
    dataset_gate_assessment_id: uuid.UUID
    dataset_key: str
    dataset_version_number: int
    price_semantics: str
    security_ids: tuple[uuid.UUID, ...]
    start_date: date
    end_date: date
    row_count: int
    estimated_bytes: int
    export_format: ExportFormat
    fields: tuple[str, ...]
    warning_codes: tuple[str, ...]
    request_fingerprint: str
    request_document: dict[str, object]


@dataclass(frozen=True, slots=True)
class AssetDataExportJob:
    export_job_id: uuid.UUID
    work_item_id: uuid.UUID
    status: ExportStatus
    stage: str
    processed_rows: int
    processed_bytes: int
    total_rows: int
    estimated_bytes: int
    request_fingerprint: str
    created_at: datetime
    updated_at: datetime
    content_hash: str | None = None
    byte_size: int | None = None
    filename: str | None = None
    expires_at: datetime | None = None
    local_delivery_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class ValidatedAssetDataExport:
    path: Path
    filename: str
    content_hash: str
    byte_size: int


class AssetDataExportService:
    def __init__(self, engine: Engine, *, directory: Path | str | None = None) -> None:
        self._engine = engine
        self._directory = Path(directory or default_asset_export_cache_directory()).resolve()
        self._queue = WorkQueueService(engine)

    def preview(
        self,
        *,
        researcher_key: str,
        graph_draft_id: uuid.UUID,
        graph_draft_revision: int,
        export_format: ExportFormat = "parquet",
        start_date: date | None = None,
        end_date: date | None = None,
        fields: Sequence[str] = DEFAULT_FIELDS,
    ) -> AssetDataExportPreview:
        normalized_fields = _normalize_fields(fields)
        if export_format not in {"parquet", "csv"}:
            raise ValueError("asset_export_format_invalid")
        with self._engine.connect() as connection:
            preview = _resolve_preview(
                connection,
                researcher_key=researcher_key,
                graph_draft_id=graph_draft_id,
                graph_draft_revision=graph_draft_revision,
                export_format=export_format,
                start_date=start_date,
                end_date=end_date,
                fields=normalized_fields,
            )
        if export_format == "csv" and (
            len(preview.security_ids) > CSV_MAX_ASSETS
            or preview.estimated_bytes > CSV_MAX_ESTIMATED_BYTES
        ):
            raise ValueError("asset_export_csv_limit_exceeded")
        return preview

    def enqueue(self, preview: AssetDataExportPreview) -> AssetDataExportJob:
        self._ensure_disk_capacity(preview.estimated_bytes)
        prior = self._latest_job(preview)
        if prior is not None and prior.status in {"queued", "running"}:
            return prior
        if prior is not None and self._is_reusable(prior):
            return prior
        work_fingerprint = preview.request_fingerprint
        if prior is not None:
            work_fingerprint = sha256_hexdigest(
                {
                    "request_fingerprint": preview.request_fingerprint,
                    "superseded_export_job_id": str(prior.export_job_id),
                }
            )
        queued = self._queue.enqueue(
            specification_fingerprint=work_fingerprint,
            work_type="asset_export",
            priority=120,
            max_attempts=2,
        )
        with self._engine.begin() as connection:
            existing = (
                connection.execute(
                    text(
                        """
                        SELECT export_job_id
                          FROM workspace.v022_asset_data_export_job
                         WHERE work_item_id=:work
                        """
                    ),
                    {"work": queued.item.work_item_id},
                ).scalar_one_or_none()
            )
            if existing is None:
                export_job_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"bird-v022:asset-data-export:{queued.item.work_item_id}",
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO workspace.v022_asset_data_export_job (
                          export_job_id,work_item_id,researcher_key,graph_draft_id,
                          graph_draft_revision,asset_registry_release_id,
                          dataset_publication_id,dataset_gate_assessment_id,
                          request_fingerprint,request_document
                        ) VALUES (
                          :job,:work,:researcher,:draft,:revision,:registry,:dataset,:gate,
                          :fingerprint,CAST(:document AS jsonb)
                        )
                        """
                    ),
                    {
                        "job": export_job_id,
                        "work": queued.item.work_item_id,
                        "researcher": str(preview.request_document["researcher_key"]),
                        "draft": preview.graph_draft_id,
                        "revision": preview.graph_draft_revision,
                        "registry": preview.asset_registry_release_id,
                        "dataset": preview.dataset_publication_id,
                        "gate": preview.dataset_gate_assessment_id,
                        "fingerprint": preview.request_fingerprint,
                        "document": _json(preview.request_document),
                    },
                )
            else:
                export_job_id = existing
        return self.get(
            export_job_id,
            researcher_key=str(preview.request_document["researcher_key"]),
        )

    def _latest_job(self, preview: AssetDataExportPreview) -> AssetDataExportJob | None:
        researcher_key = str(preview.request_document["researcher_key"])
        with self._engine.connect() as connection:
            export_job_id = connection.execute(
                text(
                    """
                    SELECT export_job_id
                      FROM workspace.v022_asset_data_export_job
                     WHERE researcher_key=:researcher AND request_fingerprint=:fingerprint
                     ORDER BY created_at DESC LIMIT 1
                    """
                ),
                {"researcher": researcher_key, "fingerprint": preview.request_fingerprint},
            ).scalar_one_or_none()
        if export_job_id is None:
            return None
        return self.get(export_job_id, researcher_key=researcher_key)

    def _is_reusable(self, job: AssetDataExportJob) -> bool:
        if (
            job.status != "completed"
            or job.expires_at is None
            or job.expires_at <= datetime.now(UTC)
            or not job.content_hash
            or not job.byte_size
        ):
            return False
        path = _content_path(self._directory, job.content_hash)
        return (
            path.is_file()
            and path.stat().st_size == job.byte_size
            and _file_sha256(path) == job.content_hash
        )

    def _ensure_disk_capacity(self, estimated_bytes: int) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        required = estimated_bytes * 2 + MIN_FREE_SPACE_BYTES
        if shutil.disk_usage(self._directory).free < required:
            raise RuntimeError("asset_export_insufficient_disk_space")

    def get(self, export_job_id: uuid.UUID, *, researcher_key: str) -> AssetDataExportJob:
        with self._engine.connect() as connection:
            row = _job_row(connection, export_job_id, researcher_key=researcher_key)
        if row is None:
            raise LookupError(f"Asset Data Export not found: {export_job_id}")
        return _job_from_row(row)

    def cancel(self, export_job_id: uuid.UUID, *, researcher_key: str) -> AssetDataExportJob:
        current = self.get(export_job_id, researcher_key=researcher_key)
        self._queue.request_cancel(current.work_item_id)
        return self.get(export_job_id, researcher_key=researcher_key)

    def validated_download(
        self, export_job_id: uuid.UUID, *, researcher_key: str
    ) -> ValidatedAssetDataExport:
        job = self.get(export_job_id, researcher_key=researcher_key)
        if job.status != "completed" or not job.content_hash or not job.byte_size:
            raise LookupError("Asset Data Export is not ready")
        path = _content_path(self._directory, job.content_hash)
        if not path.is_file() or path.stat().st_size != job.byte_size:
            raise RuntimeError("Asset Data Export file is missing or has an invalid size")
        if _file_sha256(path) != job.content_hash:
            raise RuntimeError("Asset Data Export content hash failed validation")
        with self._engine.begin() as connection:
            changed = connection.execute(
                text(
                    """
                    UPDATE workspace.v022_asset_data_export_result
                       SET last_accessed_at=now()
                     WHERE export_job_id=:job AND expires_at>now()
                    """
                ),
                {"job": export_job_id},
            )
            if changed.rowcount != 1:
                raise LookupError("Asset Data Export has expired")
        return ValidatedAssetDataExport(
            path=path,
            filename=job.filename or "migratory_bird_asset_data.zip",
            content_hash=job.content_hash,
            byte_size=job.byte_size,
        )


class AssetDataExportWorker:
    def __init__(
        self,
        engine: Engine,
        *,
        worker_id: str,
        directory: Path | str | None = None,
        delivery_directory: Path | str | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("Asset Data Export worker id is required")
        self._engine = engine
        self._worker_id = worker_id
        self._queue = WorkQueueService(engine)
        self._directory = Path(directory or default_asset_export_cache_directory()).resolve()
        self._delivery_directory = Path(
            delivery_directory or default_asset_export_delivery_directory()
        ).resolve()

    def _ensure_disk_capacity(self, estimated_bytes: int) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        self._delivery_directory.mkdir(parents=True, exist_ok=True)
        required = estimated_bytes * 2 + MIN_FREE_SPACE_BYTES
        cache_free = shutil.disk_usage(self._directory).free
        delivery_free = shutil.disk_usage(self._delivery_directory).free
        if min(cache_free, delivery_free) < required:
            raise RuntimeError("asset_export_insufficient_disk_space")

    def run_once(self) -> Literal["idle", "completed", "failed", "cancelled", "retrying"]:
        item = self._queue.claim(
            worker_id=self._worker_id,
            lease_seconds=300,
            work_types=("asset_export",),
        )
        if item is None:
            return "idle"
        stop = threading.Event()
        lease_error: list[BaseException] = []

        def renew() -> None:
            while not stop.wait(30):
                try:
                    self._queue.heartbeat(
                        item.work_item_id,
                        worker_id=self._worker_id,
                        lease_seconds=300,
                    )
                except BaseException as error:
                    lease_error.append(error)
                    stop.set()

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            job_id, request = self._request(item.work_item_id)
            if self._queue.cancellation_requested(item.work_item_id, worker_id=self._worker_id):
                self._queue.finish(item.work_item_id, worker_id=self._worker_id, status="cancelled")
                return "cancelled"
            result = self._build(job_id, item.work_item_id, request)
            if lease_error:
                raise RuntimeError("Asset Data Export lease was lost") from lease_error[0]
            if self._queue.cancellation_requested(item.work_item_id, worker_id=self._worker_id):
                self._queue.finish(item.work_item_id, worker_id=self._worker_id, status="cancelled")
                return "cancelled"
            self._publish(job_id, item.work_item_id, result)
            return "completed"
        except AssetDataExportCancelled:
            self._queue.finish(
                item.work_item_id,
                worker_id=self._worker_id,
                status="cancelled",
            )
            return "cancelled"
        except Exception as error:
            failure_class = (
                WorkFailureClass.CONTRACT
                if isinstance(error, ValueError)
                else WorkFailureClass.INFRASTRUCTURE
            )
            failed = self._queue.finish(
                item.work_item_id,
                worker_id=self._worker_id,
                status="failed",
                failure_class=failure_class,
                failure_details={"message": str(error), "type": type(error).__name__},
            )
            if (
                failure_class == WorkFailureClass.INFRASTRUCTURE
                and failed.attempt_count < failed.max_attempts
            ):
                self._queue.retry(item.work_item_id)
                return "retrying"
            return "failed"
        finally:
            stop.set()
            heartbeat.join()

    def _request(self, work_item_id: uuid.UUID) -> tuple[uuid.UUID, dict[str, Any]]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT export_job_id,request_document
                          FROM workspace.v022_asset_data_export_job
                         WHERE work_item_id=:work
                        """
                    ),
                    {"work": work_item_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None or not isinstance(row["request_document"], dict):
            raise ValueError("Asset Data Export Work has no valid request")
        return row["export_job_id"], dict(row["request_document"])

    def _build(
        self, export_job_id: uuid.UUID, work_item_id: uuid.UUID, request: dict[str, Any]
    ) -> _BuiltExport:
        expected = sha256_hexdigest(request)
        self._ensure_disk_capacity(int(request["estimated_bytes"]))
        security_ids = tuple(uuid.UUID(item) for item in cast(list[str], request["security_ids"]))
        dataset_id = uuid.UUID(str(request["dataset_publication_id"]))
        start = date.fromisoformat(str(request["start_date"]))
        end = date.fromisoformat(str(request["end_date"]))
        fields = _normalize_fields(cast(list[str], request["fields"]))
        export_format = cast(ExportFormat, request["export_format"])
        temporary_root = Path(tempfile.mkdtemp(prefix="v022-asset-export-"))
        try:
            package_root = temporary_root / "package"
            package_root.mkdir()
            checksums: dict[str, dict[str, object]] = {}
            with self._engine.connect() as connection:
                identities = _security_rows(connection, security_ids)
                if len(identities) != len(security_ids):
                    raise ValueError("asset_export_security_identity_missing")
                _write_small_parquet(
                    package_root / "securities.parquet",
                    identities,
                )
                _record_checksum(package_root, "securities.parquet", checksums)
                processed_rows = 0
                processed_bytes = 0
                for year in range(start.year, end.year + 1):
                    relative = (
                        f"prices/year={year}/part-0000.parquet"
                        if export_format == "parquet"
                        else f"prices/year={year}/part-0000.csv"
                    )
                    path = package_root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    year_rows = self._write_prices(
                        connection,
                        path=path,
                        dataset_id=dataset_id,
                        security_ids=security_ids,
                        start=max(start, date(year, 1, 1)),
                        end=min(end, date(year, 12, 31)),
                        fields=fields,
                        export_format=export_format,
                        export_job_id=export_job_id,
                        work_item_id=work_item_id,
                        processed_rows=processed_rows,
                        processed_bytes=processed_bytes,
                    )
                    processed_rows += year_rows
                    processed_bytes += path.stat().st_size
                    _record_checksum(package_root, relative, checksums)
                self._write_metadata_tables(
                    connection,
                    package_root=package_root,
                    request=request,
                    security_ids=security_ids,
                    dataset_id=dataset_id,
                    checksums=checksums,
                )
            dictionary = _data_dictionary(fields)
            _write_json(package_root / "data_dictionary.json", dictionary)
            _record_checksum(package_root, "data_dictionary.json", checksums)
            manifest = {
                "schema_version": PACKAGE_SCHEMA,
                "request_fingerprint": expected,
                "created_at": datetime.now(UTC).isoformat(),
                "asset_count": len(security_ids),
                "row_count": processed_rows,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "export_format": export_format,
                "dataset_publication_id": str(dataset_id),
                "asset_registry_release_id": request["asset_registry_release_id"],
                "dataset_gate_assessment_id": request["dataset_gate_assessment_id"],
                "price_semantics": request["price_semantics"],
                "warning_codes": request["warning_codes"],
                "files": checksums,
            }
            _write_json(package_root / "manifest.json", manifest)
            _write_json(package_root / "checksums.json", checksums)
            filename = (
                f"migratory_bird_assets_{len(security_ids)}_"
                f"{start.isoformat()}_{end.isoformat()}_{expected[:8]}.zip"
            )
            archive = temporary_root / filename
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
                for file in sorted(package_root.rglob("*")):
                    if file.is_file():
                        output.write(file, file.relative_to(package_root).as_posix())
            content_hash = _file_sha256(archive)
            target = _content_path(self._directory, content_hash)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if (
                    target.stat().st_size != archive.stat().st_size
                    or _file_sha256(target) != content_hash
                ):
                    raise RuntimeError("Existing Asset Data Export failed integrity validation")
            else:
                os.replace(archive, target)
            self._delivery_directory.mkdir(parents=True, exist_ok=True)
            delivered = self._delivery_directory / filename
            if delivered.exists() and _file_sha256(delivered) != content_hash:
                delivered = self._delivery_directory / (
                    f"{Path(filename).stem}_{content_hash[:8]}.zip"
                )
            if not delivered.exists():
                shutil.copy2(target, delivered)
            return _BuiltExport(
                path=target,
                filename=filename,
                content_hash=content_hash,
                byte_size=target.stat().st_size,
                manifest=manifest,
                local_delivery_path=str(delivered),
                row_count=processed_rows,
            )
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    def _write_prices(
        self,
        connection: Connection,
        *,
        path: Path,
        dataset_id: uuid.UUID,
        security_ids: tuple[uuid.UUID, ...],
        start: date,
        end: date,
        fields: tuple[str, ...],
        export_format: ExportFormat,
        export_job_id: uuid.UUID,
        work_item_id: uuid.UUID,
        processed_rows: int,
        processed_bytes: int,
    ) -> int:
        field_sql = ",".join(f"bar.{field}" for field in fields)
        statement = text(
            f"""
            SELECT security.security_id,bar.asset_id,security.security_key,
                   COALESCE(symbol.identifier_value,security.security_key) AS symbol,
                   bar.session_date,{field_sql}
              FROM data.daily_bar bar
              JOIN catalog.security security ON security.legacy_asset_id=bar.asset_id
              LEFT JOIN LATERAL (
                SELECT identifier.identifier_value
                  FROM catalog.security_identifier identifier
                 WHERE identifier.security_id=security.security_id
                   AND identifier.identifier_type IN ('yahoo_ticker','provider_symbol','symbol')
                   AND (identifier.valid_from IS NULL OR identifier.valid_from<=bar.session_date)
                   AND (identifier.valid_to IS NULL OR identifier.valid_to>=bar.session_date)
                 ORDER BY CASE identifier.identifier_type WHEN 'yahoo_ticker' THEN 0 ELSE 1 END,
                          identifier.valid_from DESC NULLS LAST
                 LIMIT 1
              ) symbol ON true
             WHERE bar.dataset_publication_id=:dataset
               AND security.security_id IN :securities
               AND bar.session_date BETWEEN :start AND :end
             ORDER BY security.security_id,bar.session_date
            """
        ).bindparams(bindparam("securities", expanding=True))
        result = connection.execution_options(
            stream_results=True,
            yield_per=MAX_BATCH_ROWS,
        ).execute(
            statement,
            {"dataset": dataset_id, "securities": security_ids, "start": start, "end": end},
        )
        names = ("security_id", "asset_id", "security_key", "symbol", "session_date", *fields)
        count = 0
        writer: pq.ParquetWriter | None = None
        csv_stream = None
        csv_writer = None
        try:
            if export_format == "csv":
                import csv

                csv_stream = path.open("w", encoding="utf-8", newline="")
                csv_writer = csv.writer(csv_stream)
                csv_writer.writerow(names)
            for partition in result.partitions(MAX_BATCH_ROWS):
                rows = list(partition)
                if not rows:
                    continue
                if self._queue.cancellation_requested(work_item_id, worker_id=self._worker_id):
                    raise AssetDataExportCancelled("asset_export_cancel_requested")
                if export_format == "parquet":
                    table = _price_table(rows, fields)
                    if writer is None:
                        writer = pq.ParquetWriter(path, table.schema, compression="zstd")
                    writer.write_table(table, row_group_size=MAX_BATCH_ROWS)
                else:
                    assert csv_writer is not None
                    csv_writer.writerows(tuple(row) for row in rows)
                count += len(rows)
                self._update_progress(
                    export_job_id,
                    stage="writing_prices",
                    processed_rows=processed_rows + count,
                    processed_bytes=processed_bytes + (path.stat().st_size if path.exists() else 0),
                )
        finally:
            if writer is not None:
                writer.close()
            if csv_stream is not None:
                csv_stream.close()
        if count == 0 and export_format == "parquet":
            # Preserve the annual partition inventory even for an empty boundary year.
            pq.write_table(_price_table([], fields), path, compression="zstd")
        return count

    def _write_metadata_tables(
        self,
        connection: Connection,
        *,
        package_root: Path,
        request: dict[str, Any],
        security_ids: tuple[uuid.UUID, ...],
        dataset_id: uuid.UUID,
        checksums: dict[str, dict[str, object]],
    ) -> None:
        queries = {
            "corporate_actions.parquet": (
                """
                SELECT security.security_id,action.asset_id,action.effective_date,
                       action.cash_dividend,action.split_ratio
                  FROM data.corporate_action action
                  JOIN catalog.security security ON security.legacy_asset_id=action.asset_id
                 WHERE action.dataset_publication_id=:dataset
                   AND security.security_id IN :securities
                 ORDER BY security.security_id,action.effective_date
                """,
                {"dataset": dataset_id, "securities": security_ids},
            ),
            "membership_intervals.parquet": (
                """
                SELECT interval.security_id,interval.effective_start,interval.effective_end
                  FROM experiment.v022_cohort_eligibility_interval interval
                  JOIN experiment.v022_evaluation_cohort_version cohort
                    ON cohort.evaluation_cohort_version_id=interval.evaluation_cohort_version_id
                 WHERE cohort.dataset_publication_id=:dataset
                   AND cohort.frequency='weekly' AND interval.security_id IN :securities
                   AND interval.is_member
                 ORDER BY interval.security_id,interval.effective_start
                """,
                {"dataset": dataset_id, "securities": security_ids},
            ),
            "eligibility_intervals.parquet": (
                """
                SELECT interval.security_id,interval.effective_start,interval.effective_end,
                       interval.is_member,interval.is_warmup_ready,interval.is_selectable,
                       interval.is_tradable,interval.valuation_state,interval.reason_codes
                  FROM experiment.v022_cohort_eligibility_interval interval
                  JOIN experiment.v022_evaluation_cohort_version cohort
                    ON cohort.evaluation_cohort_version_id=interval.evaluation_cohort_version_id
                 WHERE cohort.dataset_publication_id=:dataset
                   AND cohort.frequency='weekly' AND interval.security_id IN :securities
                 ORDER BY interval.security_id,interval.effective_start
                """,
                {"dataset": dataset_id, "securities": security_ids},
            ),
            "lifecycle_events.parquet": (
                """
                SELECT security_id,event_key,version_number,event_type,event_status,
                       announced_at,effective_session,last_trading_session,settlement_session,
                       selectable_after,tradable_after,valuation_state_after,event_document
                  FROM catalog.v022_security_lifecycle_event
                 WHERE security_id IN :securities
                 ORDER BY security_id,effective_session,event_key,version_number
                """,
                {"securities": security_ids},
            ),
        }
        for relative, (sql, parameters) in queries.items():
            statement = text(sql).bindparams(bindparam("securities", expanding=True))
            rows = connection.execute(
                statement,
                cast(Mapping[str, Any], parameters),
            ).mappings().all()
            _write_small_parquet(package_root / relative, rows)
            _record_checksum(package_root, relative, checksums)
        exclusions = (
            connection.execute(
                text(
                    """
                    SELECT security_id,exclusion_start,exclusion_end,reason_code,
                           evidence_artifact_id
                      FROM data.v022_dataset_gate_uniform_exclusion
                     WHERE dataset_gate_assessment_id=:gate AND security_id IN :securities
                     ORDER BY security_id,ordinal
                    """
                ).bindparams(bindparam("securities", expanding=True)),
                {
                    "gate": uuid.UUID(str(request["dataset_gate_assessment_id"])),
                    "securities": security_ids,
                },
            )
            .mappings()
            .all()
        )
        quality = {
            "warning_codes": request["warning_codes"],
            "uniform_exclusions": [_json_native(dict(row)) for row in exclusions],
        }
        _write_json(package_root / "quality_and_exclusions.json", quality)
        _record_checksum(package_root, "quality_and_exclusions.json", checksums)

    def _update_progress(
        self, export_job_id: uuid.UUID, *, stage: str, processed_rows: int, processed_bytes: int
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE workspace.v022_asset_data_export_job
                       SET progress_stage=:stage,processed_rows=:rows,
                           processed_bytes=:bytes,updated_at=now()
                     WHERE export_job_id=:job
                    """
                ),
                {
                    "job": export_job_id,
                    "stage": stage,
                    "rows": processed_rows,
                    "bytes": processed_bytes,
                },
            )

    def _publish(
        self, export_job_id: uuid.UUID, work_item_id: uuid.UUID, built: _BuiltExport
    ) -> None:
        with self._engine.begin() as connection:
            locked = connection.execute(
                text(
                    "SELECT status,lease_owner FROM ops.work_item "
                    "WHERE work_item_id=:work FOR UPDATE"
                ),
                {"work": work_item_id},
            ).mappings().one()
            if locked["status"] != "running" or locked["lease_owner"] != self._worker_id:
                raise RuntimeError("Only the active Asset Export worker may publish")
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_asset_data_export_result (
                      export_result_id,export_job_id,storage_uri,content_hash,byte_size,
                      filename,schema_version,manifest_document
                    ) VALUES (
                      :id,:job,:uri,:hash,:bytes,:filename,:schema,CAST(:manifest AS jsonb)
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "job": export_job_id,
                    "uri": f"asset-data-export://sha256/{built.content_hash}.zip",
                    "hash": built.content_hash,
                    "bytes": built.byte_size,
                    "filename": built.filename,
                    "schema": PACKAGE_SCHEMA,
                    "manifest": _json(
                        {
                            **built.manifest,
                            "local_delivery_path": built.local_delivery_path,
                        }
                    ),
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE workspace.v022_asset_data_export_job
                       SET progress_stage='completed',processed_rows=:rows,
                           processed_bytes=:bytes,updated_at=now()
                     WHERE export_job_id=:job
                    """
                ),
                {"job": export_job_id, "rows": built.row_count, "bytes": built.byte_size},
            )
            self._queue.finish_in_transaction(
                connection,
                work_item_id,
                worker_id=self._worker_id,
                status="completed",
            )


@dataclass(frozen=True, slots=True)
class _BuiltExport:
    path: Path
    filename: str
    content_hash: str
    byte_size: int
    manifest: dict[str, object]
    local_delivery_path: str
    row_count: int


def _resolve_preview(
    connection: Connection,
    *,
    researcher_key: str,
    graph_draft_id: uuid.UUID,
    graph_draft_revision: int,
    export_format: ExportFormat,
    start_date: date | None,
    end_date: date | None,
    fields: tuple[str, ...],
) -> AssetDataExportPreview:
    identity = active_v022_workspace_identity(connection)
    if identity is None:
        raise LookupError("asset_export_active_environment_unavailable")
    row = (
        connection.execute(
            text(
                """
                SELECT draft.researcher_key,revision.asset_context_document,
                       revision.resolved_data_binding_document
                  FROM workspace.v022_graph_draft draft
                  JOIN workspace.v022_graph_draft_revision revision
                    ON revision.graph_draft_id=draft.graph_draft_id
                   AND revision.revision=:revision
                 WHERE draft.graph_draft_id=:draft
                """
            ),
            {"draft": graph_draft_id, "revision": graph_draft_revision},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["researcher_key"] != researcher_key:
        raise LookupError("asset_export_saved_revision_not_found")
    context = row["asset_context_document"]
    binding = row["resolved_data_binding_document"]
    if not isinstance(context, dict) or not isinstance(binding, dict):
        raise ValueError("asset_export_revision_identity_invalid")
    members = context.get("members")
    bindings = binding.get("bindings")
    if (
        not isinstance(members, list)
        or not members
        or not isinstance(bindings, list)
        or len(bindings) != 1
    ):
        raise ValueError("asset_export_saved_selection_required")
    try:
        security_ids = tuple(
            sorted({uuid.UUID(str(item["security_id"])) for item in members}, key=str)
        )
        dataset_id = uuid.UUID(str(bindings[0]["dataset_publication_id"]))
        registry_id = uuid.UUID(str(context["asset_registry_release_id"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("asset_export_revision_identity_invalid") from error
    if len(security_ids) != len(members) or registry_id != identity.asset_registry_release_id:
        raise ValueError("asset_export_active_registry_mismatch")
    allowed_datasets = {
        identity.risk_dataset_publication_id,
        identity.benchmark_dataset_publication_id,
    }
    if dataset_id not in allowed_datasets:
        raise ValueError("asset_export_active_dataset_mismatch")
    dataset = connection.execute(
        text(
            """
            SELECT dataset.dataset_key,dataset.version_number,gate.price_semantics
              FROM data.dataset_publication dataset
              JOIN data.v022_dataset_gate_assessment gate
                ON gate.dataset_gate_assessment_id=:gate
             WHERE dataset.dataset_publication_id=:dataset
            """
        ),
        {"dataset": dataset_id, "gate": identity.dataset_gate_assessment_id},
    ).mappings().one()
    excluded = connection.execute(
        text(
            """
            SELECT security_id FROM data.v022_dataset_gate_uniform_exclusion
             WHERE dataset_gate_assessment_id=:gate AND security_id IN :securities
            """
        ).bindparams(bindparam("securities", expanding=True)),
        {"gate": identity.dataset_gate_assessment_id, "securities": security_ids},
    ).scalars().all()
    if excluded:
        raise ValueError("asset_export_selection_contains_excluded_security")
    coverage = connection.execute(
        text(
            """
            SELECT count(*) AS row_count,min(bar.session_date) AS start_date,
                   max(bar.session_date) AS end_date
              FROM data.daily_bar bar
              JOIN catalog.security security ON security.legacy_asset_id=bar.asset_id
             WHERE bar.dataset_publication_id=:dataset
               AND security.security_id IN :securities
            """
        ).bindparams(bindparam("securities", expanding=True)),
        {"dataset": dataset_id, "securities": security_ids},
    ).mappings().one()
    if not coverage["row_count"] or not coverage["start_date"] or not coverage["end_date"]:
        raise ValueError("asset_export_market_data_unavailable")
    actual_start = start_date or coverage["start_date"]
    actual_end = end_date or coverage["end_date"]
    if (
        actual_start > actual_end
        or actual_start < coverage["start_date"]
        or actual_end > coverage["end_date"]
    ):
        raise ValueError("asset_export_date_range_invalid")
    mapped_count = connection.execute(
        text(
            """
            SELECT count(DISTINCT security.security_id)
              FROM data.daily_bar bar
              JOIN catalog.security security ON security.legacy_asset_id=bar.asset_id
             WHERE bar.dataset_publication_id=:dataset
               AND security.security_id IN :securities
            """
        ).bindparams(bindparam("securities", expanding=True)),
        {"dataset": dataset_id, "securities": security_ids},
    ).scalar_one()
    if int(mapped_count) != len(security_ids):
        raise ValueError("asset_export_selected_security_coverage_incomplete")
    row_count = int(
        connection.execute(
            text(
                """
                SELECT count(*)
                  FROM data.daily_bar bar
                  JOIN catalog.security security
                    ON security.legacy_asset_id=bar.asset_id
                 WHERE bar.dataset_publication_id=:dataset
                   AND security.security_id IN :securities
                   AND bar.session_date BETWEEN :start AND :end
                """
            ).bindparams(bindparam("securities", expanding=True)),
            {
                "dataset": dataset_id,
                "securities": security_ids,
                "start": actual_start,
                "end": actual_end,
            },
        ).scalar_one()
    )
    estimated_bytes = row_count * (180 if export_format == "csv" else 56)
    warning_codes = (
        "free_source_retrospective_prices",
        "historical_membership_and_lifecycle_best_effort",
        "survivorship_bias_possible_due_to_uniform_exclusions",
    )
    document: dict[str, object] = {
        "contract_version": REQUEST_CONTRACT,
        "researcher_key": researcher_key,
        "graph_draft_id": str(graph_draft_id),
        "graph_draft_revision": graph_draft_revision,
        "asset_registry_release_id": str(identity.asset_registry_release_id),
        "dataset_publication_id": str(dataset_id),
        "dataset_gate_assessment_id": str(identity.dataset_gate_assessment_id),
        "security_ids": [str(item) for item in security_ids],
        "start_date": actual_start.isoformat(),
        "end_date": actual_end.isoformat(),
        "fields": list(fields),
        "export_format": export_format,
        "price_semantics": str(dataset["price_semantics"]),
        "warning_codes": list(warning_codes),
        "estimated_row_count": row_count,
        "estimated_bytes": estimated_bytes,
    }
    fingerprint = sha256_hexdigest(document)
    return AssetDataExportPreview(
        graph_draft_id=graph_draft_id,
        graph_draft_revision=graph_draft_revision,
        asset_registry_release_id=identity.asset_registry_release_id,
        dataset_publication_id=dataset_id,
        dataset_gate_assessment_id=identity.dataset_gate_assessment_id,
        dataset_key=str(dataset["dataset_key"]),
        dataset_version_number=int(dataset["version_number"]),
        price_semantics=str(dataset["price_semantics"]),
        security_ids=security_ids,
        start_date=actual_start,
        end_date=actual_end,
        row_count=row_count,
        estimated_bytes=estimated_bytes,
        export_format=export_format,
        fields=fields,
        warning_codes=warning_codes,
        request_fingerprint=fingerprint,
        request_document=document,
    )


def _job_row(
    connection: Connection, export_job_id: uuid.UUID, *, researcher_key: str
) -> RowMapping | None:
    return (
        connection.execute(
            text(
                """
                SELECT job.*,work.status,work.stage AS work_stage,work.failure_class,
                       work.failure_details,result.content_hash,result.byte_size,
                       result.filename,result.expires_at,result.manifest_document
                  FROM workspace.v022_asset_data_export_job job
                  JOIN ops.work_item work ON work.work_item_id=job.work_item_id
                  LEFT JOIN workspace.v022_asset_data_export_result result
                    ON result.export_job_id=job.export_job_id
                 WHERE job.export_job_id=:job AND job.researcher_key=:researcher
                """
            ),
            {"job": export_job_id, "researcher": researcher_key},
        )
        .mappings()
        .one_or_none()
    )


def _job_from_row(row: RowMapping) -> AssetDataExportJob:
    request = row["request_document"] if isinstance(row["request_document"], dict) else {}
    failure = row["failure_details"] if isinstance(row["failure_details"], dict) else {}
    manifest = row["manifest_document"] if isinstance(row["manifest_document"], dict) else {}
    return AssetDataExportJob(
        export_job_id=row["export_job_id"],
        work_item_id=row["work_item_id"],
        status=cast(ExportStatus, row["status"]),
        stage=str(row["progress_stage"] or row["work_stage"]),
        processed_rows=int(row["processed_rows"]),
        processed_bytes=int(row["processed_bytes"]),
        total_rows=int(request.get("estimated_row_count", 0)),
        estimated_bytes=int(request.get("estimated_bytes", 0)),
        request_fingerprint=str(row["request_fingerprint"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        content_hash=row["content_hash"],
        byte_size=row["byte_size"],
        filename=row["filename"],
        expires_at=row["expires_at"],
        local_delivery_path=cast(str | None, manifest.get("local_delivery_path")),
        error_code=row["failure_class"],
        error_message=cast(str | None, failure.get("message")),
    )


def _normalize_fields(fields: Sequence[str]) -> tuple[str, ...]:
    values = tuple(str(item) for item in fields)
    if (
        not values
        or len(values) != len(set(values))
        or any(item not in DEFAULT_FIELDS for item in values)
    ):
        raise ValueError("asset_export_field_selection_invalid")
    return values


def _security_rows(connection: Connection, security_ids: tuple[uuid.UUID, ...]) -> list[RowMapping]:
    return list(
        connection.execute(
            text(
                """
                SELECT security.security_id,security.legacy_asset_id AS asset_id,
                       security.security_key,security.name,security.instrument_type,
                       security.currency,security.status,
                       COALESCE(symbol.identifier_value,security.security_key) AS symbol
                  FROM catalog.security security
                  LEFT JOIN LATERAL (
                    SELECT identifier_value FROM catalog.security_identifier identifier
                     WHERE identifier.security_id=security.security_id
                       AND identifier.identifier_type IN ('yahoo_ticker','provider_symbol','symbol')
                     ORDER BY CASE identifier.identifier_type WHEN 'yahoo_ticker' THEN 0 ELSE 1 END,
                              identifier.valid_from DESC NULLS LAST LIMIT 1
                  ) symbol ON true
                 WHERE security.security_id IN :securities
                 ORDER BY security.security_id
                """
            ).bindparams(bindparam("securities", expanding=True)),
            {"securities": security_ids},
        ).mappings()
    )


def _price_table(rows: Sequence[Sequence[Any]], fields: tuple[str, ...]) -> pa.Table:
    names = ("security_id", "asset_id", "security_key", "symbol", "session_date", *fields)
    columns: list[Sequence[Any]] = list(zip(*rows, strict=True)) if rows else [() for _ in names]
    arrays: list[pa.Array] = []
    for index, name in enumerate(names):
        values = list(columns[index])
        if name in {"security_id", "asset_id", "security_key", "symbol"}:
            arrays.append(pa.array([str(item) for item in values], type=pa.string()))
        elif name == "session_date":
            arrays.append(pa.array(values, type=pa.date32()))
        elif name == "volume_raw":
            arrays.append(pa.array(values, type=pa.int64()))
        elif name == "adjustment_factor":
            arrays.append(pa.array(values, type=pa.decimal128(24, 14)))
        else:
            arrays.append(pa.array(values, type=pa.decimal128(24, 10)))
    return pa.Table.from_arrays(arrays, names=names)


def _write_small_parquet(path: Path, rows: Iterable[Mapping[str, Any] | RowMapping]) -> None:
    documents = [_json_native(dict(row)) for row in rows]
    if not documents:
        pq.write_table(pa.table({"empty": pa.array([], type=pa.bool_())}), path, compression="zstd")
        return
    columns = {
        key: [_parquet_scalar(item.get(key)) for item in documents]
        for key in documents[0]
    }
    pq.write_table(pa.table(columns), path, compression="zstd", row_group_size=MAX_BATCH_ROWS)


def _data_dictionary(fields: tuple[str, ...]) -> dict[str, object]:
    return {
        "schema_version": PACKAGE_SCHEMA,
        "price_rows": {
            "keys": ["security_id", "asset_id", "security_key", "symbol", "session_date"],
            "fields": list(fields),
            "ordering": ["security_id", "session_date"],
            "decimal_semantics": (
                "PostgreSQL NUMERIC values are written as Arrow Decimal128; "
                "no float conversion"
            ),
        },
    }


def _record_checksum(root: Path, relative: str, output: dict[str, dict[str, object]]) -> None:
    path = root / relative
    output[relative] = {"sha256": _file_sha256(path), "byte_size": path.stat().st_size}


def _write_json(path: Path, document: object) -> None:
    path.write_text(_json(document) + "\n", encoding="utf-8")


def _json(value: object) -> str:
    return json.dumps(
        _json_native(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parquet_scalar(value: object) -> object:
    native = _json_native(value)
    if isinstance(native, (dict, list)):
        return json.dumps(
            native,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return native


def _json_native(value: object) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_native(item) for item in value]
    if isinstance(value, (uuid.UUID, date, datetime, Decimal)):
        return str(value)
    return value


def _content_path(directory: Path, content_hash: str) -> Path:
    if len(content_hash) != 64 or any(item not in "0123456789abcdef" for item in content_hash):
        raise ValueError("Asset Data Export content hash is invalid")
    path = (directory / content_hash[:2] / f"{content_hash}.zip").resolve()
    if not path.is_relative_to(directory):
        raise ValueError("Asset Data Export path escaped its storage root")
    return path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
