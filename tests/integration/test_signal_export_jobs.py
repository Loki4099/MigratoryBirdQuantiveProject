from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import text

from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.signal.export_jobs import (
    SignalResearchExportJobService,
    SignalResearchExportWorker,
)
from style_rotation.signal.research_export import SignalResearchExport, SignalResearchExportService

pytestmark = pytest.mark.integration


def _request() -> dict[str, Any]:
    security_id = str(uuid.uuid4())
    return {
        "frequency": "weekly",
        "asset_security_ids": [security_id],
        "asset_data_inputs": {security_id: ["canonical_market_bars"]},
        "signal_version_keys": [f"test_signal__{uuid.uuid4().hex}"],
        "include_targets": True,
    }


class _FixedExportService:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.calls = 0

    def build(self, **_kwargs: object) -> SignalResearchExport:
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise RuntimeError("temporary export storage outage")
        return SignalResearchExport(b"PK\x03\x04persistent-test-export", "research.zip")


@pytest.mark.skipif(
    not os.getenv("STYLE_ROTATION_TEST_DATABASE_URL"),
    reason="STYLE_ROTATION_TEST_DATABASE_URL is not set",
)
def test_export_survives_service_restart_reuses_valid_result_and_rejects_corruption(
    tmp_path: Path,
) -> None:
    database_url = os.environ["STYLE_ROTATION_TEST_DATABASE_URL"]
    reset_database(database_url, "style_rotation_test", "test")
    engine = create_postgres_engine(database_url)
    request = _request()
    first_service = SignalResearchExportJobService(engine, directory=tmp_path)
    queued = first_service.enqueue(request)
    assert first_service.enqueue(request).export_job_id == queued.export_job_id
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE ops.work_item SET priority = -2000000000 WHERE work_item_id = :id"),
            {"id": queued.work_item_id},
        )

    # New service/worker instances emulate an API and worker process restart.
    export_builder = _FixedExportService()
    worker = SignalResearchExportWorker(
        engine,
        worker_id=f"export-test-{uuid.uuid4()}",
        directory=tmp_path,
        export_service=cast(SignalResearchExportService, export_builder),
    )
    assert worker.run_once().status == "completed"
    restarted_service = SignalResearchExportJobService(engine, directory=tmp_path)
    completed = restarted_service.get(queued.export_job_id)
    assert completed.status == "completed"
    artifact = restarted_service.validated_download(queued.export_job_id)
    assert artifact.path.read_bytes() == b"PK\x03\x04persistent-test-export"
    assert restarted_service.enqueue(request).export_job_id == queued.export_job_id

    artifact.path.write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="invalid size|content hash"):
        restarted_service.validated_download(queued.export_job_id)
    replacement = restarted_service.enqueue(request)
    assert replacement.export_job_id != queued.export_job_id
    assert replacement.status == "queued"
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE ops.work_item SET status = 'cancelled', stage = 'cancelled' "
                "WHERE work_item_id = :id"
            ),
            {"id": replacement.work_item_id},
        )


@pytest.mark.skipif(
    not os.getenv("STYLE_ROTATION_TEST_DATABASE_URL"),
    reason="STYLE_ROTATION_TEST_DATABASE_URL is not set",
)
def test_export_worker_retries_infrastructure_failure_then_completes(tmp_path: Path) -> None:
    database_url = os.environ["STYLE_ROTATION_TEST_DATABASE_URL"]
    reset_database(database_url, "style_rotation_test", "test")
    engine = create_postgres_engine(database_url)
    service = SignalResearchExportJobService(engine, directory=tmp_path)
    queued = service.enqueue(_request())
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE ops.work_item SET priority = -2000000001 WHERE work_item_id = :id"),
            {"id": queued.work_item_id},
        )
    builder = _FixedExportService(fail_once=True)
    worker = SignalResearchExportWorker(
        engine,
        worker_id=f"retry-test-{uuid.uuid4()}",
        directory=tmp_path,
        export_service=cast(SignalResearchExportService, builder),
    )
    assert worker.run_once().status == "retrying"
    assert service.get(queued.export_job_id).status == "queued"
    assert worker.run_once().status == "completed"
    assert service.get(queued.export_job_id).status == "completed"
    assert builder.calls == 2


@pytest.mark.skipif(
    not os.getenv("STYLE_ROTATION_TEST_DATABASE_URL"),
    reason="STYLE_ROTATION_TEST_DATABASE_URL is not set",
)
def test_tampered_persisted_export_request_fails_as_contract(tmp_path: Path) -> None:
    database_url = os.environ["STYLE_ROTATION_TEST_DATABASE_URL"]
    reset_database(database_url, "style_rotation_test", "test")
    engine = create_postgres_engine(database_url)
    service = SignalResearchExportJobService(engine, directory=tmp_path)
    queued = service.enqueue(_request())
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE signal.research_export_job "
                "SET request_document = request_document - 'signal_version_keys' "
                "WHERE export_job_id = :id"
            ),
            {"id": queued.export_job_id},
        )
        connection.execute(
            text("UPDATE ops.work_item SET priority = -2000000002 WHERE work_item_id = :id"),
            {"id": queued.work_item_id},
        )
    worker = SignalResearchExportWorker(
        engine,
        worker_id=f"contract-test-{uuid.uuid4()}",
        directory=tmp_path,
        export_service=cast(SignalResearchExportService, _FixedExportService()),
    )
    assert worker.run_once().status == "failed"
    failed = service.get(queued.export_job_id)
    assert failed.failure_class == "contract"
    assert failed.attempt_count == 1
