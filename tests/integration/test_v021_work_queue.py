from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from style_rotation.domain.enums import WorkFailureClass
from style_rotation.ops.idempotency import CommandIdempotencyService
from style_rotation.ops.work_queue import WorkQueueService
from style_rotation.persistence.session import create_postgres_engine

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.getenv("STYLE_ROTATION_TEST_DATABASE_URL"),
    reason="STYLE_ROTATION_TEST_DATABASE_URL is not set",
)
def test_persistent_queue_claim_retry_and_terminal_reuse() -> None:
    engine = create_postgres_engine(os.environ["STYLE_ROTATION_TEST_DATABASE_URL"])
    service = WorkQueueService(engine)
    fingerprint = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    enqueued = service.enqueue(
        specification_fingerprint=fingerprint, work_type="predictive", priority=-1_000_000
    )
    assert enqueued.item.status == "queued"
    worker = f"test-worker-{uuid.uuid4()}"
    claimed = service.claim(worker_id=worker)
    assert claimed is not None
    assert claimed.work_item_id == enqueued.item.work_item_id
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE ops.work_item SET lease_expires_at = now() - interval '1 second' "
            f"WHERE work_item_id = '{claimed.work_item_id}'"
        )
    takeover_worker = f"takeover-{uuid.uuid4()}"
    reclaimed = service.claim(worker_id=takeover_worker)
    assert reclaimed is not None
    assert reclaimed.work_item_id == claimed.work_item_id
    assert reclaimed.attempt_count == 2
    failed = service.finish(
        claimed.work_item_id,
        worker_id=takeover_worker,
        status="failed",
        failure_class=WorkFailureClass.INFRASTRUCTURE,
        failure_details={"test": True},
    )
    assert failed.status == "failed"
    assert service.retry(failed.work_item_id).status == "queued"
    claimed_again = service.claim(worker_id=worker)
    assert claimed_again is not None
    assert claimed_again.work_item_id == failed.work_item_id
    assert (
        service.finish(claimed_again.work_item_id, worker_id=worker, status="completed").status
        == "completed"
    )
    reused = service.enqueue(specification_fingerprint=fingerprint, work_type="predictive")
    assert reused.reused_terminal_result is True
    assert reused.item.work_item_id == enqueued.item.work_item_id


@pytest.mark.skipif(
    not os.getenv("STYLE_ROTATION_TEST_DATABASE_URL"),
    reason="STYLE_ROTATION_TEST_DATABASE_URL is not set",
)
def test_concurrent_enqueue_reuses_the_single_active_work_item() -> None:
    engine = create_postgres_engine(os.environ["STYLE_ROTATION_TEST_DATABASE_URL"])
    service = WorkQueueService(engine)
    fingerprint = hashlib.sha256(uuid.uuid4().bytes).hexdigest()

    def invoke(_index: int):
        return service.enqueue(
            specification_fingerprint=fingerprint,
            work_type="portfolio",
            priority=-1_000_001,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(invoke, range(16)))

    assert len({item.item.work_item_id for item in results}) == 1
    assert all(item.item.status == "queued" for item in results)
    with engine.connect() as connection:
        event_count = connection.exec_driver_sql(
            "SELECT count(*) FROM ops.work_item_event event "
            "JOIN ops.work_item work ON work.work_item_id = event.work_item_id "
            f"WHERE work.specification_fingerprint = '{fingerprint}' "
            "AND event.event_type = 'enqueued'"
        ).scalar_one()
    assert event_count == 1


@pytest.mark.skipif(
    not os.getenv("STYLE_ROTATION_TEST_DATABASE_URL"),
    reason="STYLE_ROTATION_TEST_DATABASE_URL is not set",
)
def test_concurrent_command_retries_execute_the_operation_once() -> None:
    engine = create_postgres_engine(os.environ["STYLE_ROTATION_TEST_DATABASE_URL"])
    service = CommandIdempotencyService(engine)
    key = uuid.uuid4()
    calls = 0
    lock = threading.Lock()

    def operation() -> dict[str, object]:
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.1)
        return {"ok": True, "command_id": str(key)}

    def invoke(_index: int) -> dict[str, object]:
        return service.execute(
            command_name="integration_concurrency",
            idempotency_key=key,
            request={"selection": "same"},
            operation=operation,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, range(2)))
    assert results[0] == results[1]
    assert calls == 1


@pytest.mark.skipif(
    not os.getenv("STYLE_ROTATION_TEST_DATABASE_URL"),
    reason="STYLE_ROTATION_TEST_DATABASE_URL is not set",
)
def test_interrupted_command_response_can_be_audited_and_repaired_without_rerun() -> None:
    engine = create_postgres_engine(os.environ["STYLE_ROTATION_TEST_DATABASE_URL"])
    service = CommandIdempotencyService(engine)
    key = uuid.uuid4()
    fingerprint = hashlib.sha256(b"audited-request").hexdigest()
    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO ops.command_result (
                    command_name, idempotency_key, request_fingerprint, response
                ) VALUES (:name, :key, :fingerprint, NULL)
            """),
            {"name": "audited_repair", "key": key, "fingerprint": fingerprint},
        )

    pending = service.pending_audit()
    assert any(item["idempotency_key"] == str(key) for item in pending)
    repaired = service.repair_response(
        command_name="audited_repair",
        idempotency_key=key,
        expected_request_fingerprint=fingerprint,
        response={"reconstructed": True, "business_id": str(key)},
    )
    assert repaired["reconstructed"] is True
    repeated = service.repair_response(
        command_name="audited_repair",
        idempotency_key=key,
        expected_request_fingerprint=fingerprint,
        response={"ignored": True},
    )
    assert repeated == repaired
