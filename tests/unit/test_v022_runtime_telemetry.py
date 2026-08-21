from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest

from style_rotation.v022.runtime_telemetry import (
    LocalRuntimeTelemetry,
    PeriodicLeaseHeartbeat,
    RuntimeTelemetryIdentity,
)


def _documents(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_runtime_span_is_append_only_and_records_operational_counters(tmp_path: Path) -> None:
    path = tmp_path / "runtime.jsonl"
    work_id = uuid.uuid4()
    telemetry = LocalRuntimeTelemetry(path, sample_seconds=0.01)

    with telemetry.span(
        RuntimeTelemetryIdentity(
            worker_key="worker-a",
            stage="aggregation",
            graph_work_item_id=work_id,
            work_kind="aggregation",
        )
    ) as span:
        span.record(output_rows=123, output_bytes=456, cache_hit_count=1)

    documents = _documents(path)
    assert [item["event_kind"] for item in documents] == ["started", "completed"]
    assert documents[0]["span_id"] == documents[1]["span_id"]
    assert documents[1]["graph_work_item_id"] == str(work_id)
    assert documents[1]["wall_time_ms"] is not None
    assert documents[1]["details"] == {
        "cache_hit_count": 1,
        "output_bytes": 456,
        "output_rows": 123,
    }


def test_runtime_span_records_failure_without_swallowing_it(tmp_path: Path) -> None:
    path = tmp_path / "runtime.jsonl"
    telemetry = LocalRuntimeTelemetry(path, sample_seconds=0.01)

    with pytest.raises(ValueError, match="broken"), telemetry.span(
        RuntimeTelemetryIdentity(worker_key="worker-a", stage="portfolio_cell")
    ):
        raise ValueError("broken")

    failed = _documents(path)[-1]
    assert failed["event_kind"] == "failed"
    assert failed["details"] == {"error_type": "ValueError", "message": "broken"}


def test_periodic_lease_heartbeat_renews_and_stops_cleanly() -> None:
    renewals: list[int] = []

    with PeriodicLeaseHeartbeat(
        lambda: renewals.append(len(renewals) + 1), interval_seconds=0.01
    ) as heartbeat:
        deadline = time.monotonic() + 0.5
        while len(renewals) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)

    observed = len(renewals)
    time.sleep(0.03)
    assert observed >= 2
    assert len(renewals) == observed
    assert heartbeat.error is None


def test_periodic_lease_heartbeat_exposes_renewal_failure() -> None:
    def fail() -> None:
        raise RuntimeError("lost fence")

    with PeriodicLeaseHeartbeat(fail, interval_seconds=0.01) as heartbeat:
        deadline = time.monotonic() + 0.5
        while heartbeat.error is None and time.monotonic() < deadline:
            time.sleep(0.005)

    assert isinstance(heartbeat.error, RuntimeError)
    assert str(heartbeat.error) == "lost fence"
