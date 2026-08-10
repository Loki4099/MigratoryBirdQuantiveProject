from __future__ import annotations

import threading
import uuid

import pytest

from style_rotation.ops.worker import WorkerOutcome, run_persistent_worker


def test_persistent_worker_waits_through_idle_and_keeps_consuming() -> None:
    stop = threading.Event()
    pending = iter(
        (
            WorkerOutcome(uuid.UUID(int=0), "idle"),
            WorkerOutcome(uuid.uuid4(), "retrying"),
            WorkerOutcome(uuid.uuid4(), "completed", uuid.uuid4()),
        )
    )
    observed: list[str] = []

    def run_once() -> WorkerOutcome:
        return next(pending)

    def observe(outcome: WorkerOutcome) -> None:
        observed.append(outcome.status)
        if len(observed) == 2:
            stop.set()

    processed = run_persistent_worker(
        run_once,
        poll_seconds=0.001,
        stop_event=stop,
        on_outcome=observe,
    )

    assert processed == 2
    assert observed == ["retrying", "completed"]


def test_persistent_worker_rejects_nonpositive_poll_interval() -> None:
    with pytest.raises(ValueError, match="poll-seconds"):
        run_persistent_worker(
            lambda: WorkerOutcome(uuid.UUID(int=0), "idle"), poll_seconds=0
        )


def test_persistent_worker_runs_bounded_idle_maintenance() -> None:
    stop = threading.Event()
    maintenance_calls = 0

    def maintain() -> None:
        nonlocal maintenance_calls
        maintenance_calls += 1
        stop.set()

    processed = run_persistent_worker(
        lambda: WorkerOutcome(uuid.UUID(int=0), "idle"),
        poll_seconds=0.001,
        stop_event=stop,
        on_idle_maintenance=maintain,
    )

    assert processed == 0
    assert maintenance_calls == 1
