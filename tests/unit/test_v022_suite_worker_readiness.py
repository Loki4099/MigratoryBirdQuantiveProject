from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from time import sleep
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from style_rotation.api.app import create_app
from style_rotation.cli import v022_suite_worker as worker_cli
from style_rotation.v022.suite_worker_readiness import LocalSuiteWorkerHeartbeat


class _Reader:
    def database_revision(self) -> None:
        return None


def test_local_suite_worker_heartbeat_reports_ready_stopped_and_stale(tmp_path) -> None:
    heartbeat = LocalSuiteWorkerHeartbeat(tmp_path / "suite-worker.json")
    assert heartbeat.read().state == "unavailable"

    heartbeat.write(worker_key="worker-a", state="ready")
    ready = heartbeat.read()
    assert ready.ready is True
    assert ready.state == "ready"
    assert ready.worker_key == "worker-a"

    heartbeat.write(worker_key="worker-a", state="stopped")
    assert heartbeat.read().state == "stopped"

    heartbeat.write(
        worker_key="worker-a",
        state="error",
        error_summary="ValueError: first line\nsecond line",
    )
    document = json.loads(heartbeat.path.read_text(encoding="utf-8"))
    document["heartbeat_at"] = (
        datetime.now(UTC) - timedelta(seconds=30)
    ).isoformat()
    heartbeat.path.write_text(json.dumps(document), encoding="utf-8")
    failed = heartbeat.read(max_age_seconds=10)
    assert failed.ready is False
    assert failed.state == "error"
    assert failed.error_summary == "ValueError: first line second line"

    heartbeat.write(worker_key="worker-a", state="working")
    document = json.loads(heartbeat.path.read_text(encoding="utf-8"))
    document["state"] = "working"
    document["heartbeat_at"] = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    heartbeat.path.write_text(json.dumps(document), encoding="utf-8")
    stale = heartbeat.read(max_age_seconds=10)
    assert stale.ready is False
    assert stale.state == "stale"


def test_local_suite_worker_heartbeat_fails_closed_on_malformed_document(tmp_path) -> None:
    heartbeat = LocalSuiteWorkerHeartbeat(tmp_path / "suite-worker.json")
    heartbeat.path.write_text("not-json", encoding="utf-8")
    assert heartbeat.read().state == "error"


def test_local_suite_worker_heartbeat_supports_concurrent_refreshes(tmp_path) -> None:
    heartbeat = LocalSuiteWorkerHeartbeat(tmp_path / "suite-worker.json")
    errors: list[Exception] = []

    def write_many() -> None:
        try:
            for _ in range(40):
                heartbeat.write(worker_key="worker-a", state="working")
        except Exception as error:  # pragma: no cover - assertion reports the error
            errors.append(error)

    threads = [Thread(target=write_many) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert heartbeat.read().state == "working"
    assert list(tmp_path.glob(".suite-worker.json.tmp-*")) == []


def test_runtime_readiness_api_uses_the_injected_heartbeat(tmp_path) -> None:
    heartbeat = LocalSuiteWorkerHeartbeat(tmp_path / "suite-worker.json")
    heartbeat.write(worker_key="worker-api", state="working")
    client = TestClient(
        create_app(  # type: ignore[arg-type]
            _Reader(),
            suite_worker_heartbeat=heartbeat,
        )
    )

    response = client.get("/api/v2/workspace/graph-suite-runtime/readiness")

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["state"] == "working"
    assert response.json()["worker_key"] == "worker-api"
    assert response.json()["error_summary"] is None

    heartbeat.write(
        worker_key="worker-api",
        state="error",
        error_summary="ValueError: representative snapshot proof missing",
    )
    failed_response = client.get("/api/v2/workspace/graph-suite-runtime/readiness")

    assert failed_response.status_code == 200
    assert failed_response.json()["ready"] is False
    assert failed_response.json()["state"] == "error"
    assert failed_response.json()["quality"]["codes"] == ["suite_worker.error"]
    assert failed_response.json()["error_summary"] == (
        "ValueError: representative snapshot proof missing"
    )


def test_suite_worker_records_a_safe_error_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Engine:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    refreshed = Event()
    writes: list[dict[str, object]] = []

    class _ExplodingWorker:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run_once(self) -> None:
            assert refreshed.wait(1)
            raise ValueError("line one\nline two")

    class _Heartbeat:
        def write(self, **values: object) -> None:
            writes.append(values)
            if (
                values.get("state") == "working"
                and sum(item.get("state") == "working" for item in writes) >= 2
            ):
                refreshed.set()

    engine = _Engine()
    monkeypatch.setattr(
        worker_cli,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="postgresql://unused",
            v022_payload_directory=tmp_path,
        ),
    )
    monkeypatch.setattr(worker_cli, "create_postgres_engine", lambda _url: engine)
    monkeypatch.setattr(worker_cli, "SuiteRuntimeWorker", _ExplodingWorker)
    monkeypatch.setattr(worker_cli, "LocalSuiteWorkerHeartbeat", _Heartbeat)
    monkeypatch.setattr(worker_cli, "_HEARTBEAT_REFRESH_SECONDS", 0.01)

    with pytest.raises(ValueError, match="line one"):
        worker_cli.main(["--max-items", "1"])

    assert writes[-1] == {
        "worker_key": "v022-suite-worker",
        "state": "error",
        "error_summary": "ValueError: line one\nline two",
    }
    write_count = len(writes)
    sleep(0.03)
    assert len(writes) == write_count
    assert engine.disposed is True


def test_suite_worker_refreshes_working_heartbeat_until_run_once_completes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Engine:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    refreshed = Event()
    writes: list[dict[str, object]] = []

    class _SlowWorker:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run_once(self) -> object:
            assert refreshed.wait(1)
            return worker_cli.SuiteRuntimeWorkerOutcome("idle")

    class _Heartbeat:
        def write(self, **values: object) -> None:
            writes.append(values)
            if (
                values.get("state") == "working"
                and sum(item.get("state") == "working" for item in writes) >= 2
            ):
                refreshed.set()

    engine = _Engine()
    monkeypatch.setattr(
        worker_cli,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="postgresql://unused",
            v022_payload_directory=tmp_path,
        ),
    )
    monkeypatch.setattr(worker_cli, "create_postgres_engine", lambda _url: engine)
    monkeypatch.setattr(worker_cli, "SuiteRuntimeWorker", _SlowWorker)
    monkeypatch.setattr(worker_cli, "LocalSuiteWorkerHeartbeat", _Heartbeat)
    monkeypatch.setattr(worker_cli, "_HEARTBEAT_REFRESH_SECONDS", 0.01)

    assert worker_cli.main(["--max-items", "1"]) == 0

    states = [item["state"] for item in writes]
    assert states[:2] == ["ready", "working"]
    assert states.count("working") >= 2
    assert states[-2:] == ["ready", "stopped"]
    write_count = len(writes)
    sleep(0.03)
    assert len(writes) == write_count
    assert engine.disposed is True
