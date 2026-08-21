from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import suppress
from dataclasses import asdict
from threading import Event, Thread

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.suite_runtime_worker import (
    SuiteRuntimeWorker,
    SuiteRuntimeWorkerOutcome,
)
from style_rotation.v022.suite_worker_readiness import LocalSuiteWorkerHeartbeat

_HEARTBEAT_REFRESH_SECONDS = 2.0


def _run_once_with_heartbeat(
    worker: SuiteRuntimeWorker,
    heartbeat: LocalSuiteWorkerHeartbeat,
    *,
    worker_key: str,
) -> SuiteRuntimeWorkerOutcome:
    stop = Event()
    heartbeat_errors: list[Exception] = []

    def refresh() -> None:
        while not stop.wait(_HEARTBEAT_REFRESH_SECONDS):
            try:
                heartbeat.write(worker_key=worker_key, state="working")
            except Exception as error:  # pragma: no cover - platform I/O failure
                heartbeat_errors.append(error)
                return

    heartbeat.write(worker_key=worker_key, state="working")
    refresh_thread = Thread(
        target=refresh,
        name=f"{worker_key}-heartbeat",
        daemon=True,
    )
    refresh_thread.start()
    try:
        outcome = worker.run_once()
    finally:
        stop.set()
        refresh_thread.join()
    if heartbeat_errors:
        raise RuntimeError("v0.22 Suite worker heartbeat refresh failed") from (
            heartbeat_errors[0]
        )
    return outcome


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Consume durable v0.22 Graph Suite runtime work"
    )
    parser.add_argument("--worker-id", default="v022-suite-worker")
    parser.add_argument("--max-items", type=int, default=1)
    parser.add_argument("--forever", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_items < 1:
        raise ValueError("max-items must be positive")
    if args.poll_seconds <= 0:
        raise ValueError("poll-seconds must be positive")
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    worker = SuiteRuntimeWorker(
        engine,
        payload_directory=settings.v022_payload_directory,
        worker_key=args.worker_id,
    )
    heartbeat = LocalSuiteWorkerHeartbeat()
    outcomes: list[dict[str, object]] = []
    stopped_cleanly = False
    try:
        heartbeat.write(worker_key=args.worker_id, state="ready")
        if args.forever:
            with suppress(KeyboardInterrupt):
                while True:
                    outcome = _run_once_with_heartbeat(
                        worker,
                        heartbeat,
                        worker_key=args.worker_id,
                    )
                    heartbeat.write(worker_key=args.worker_id, state="ready")
                    if outcome.status == "idle":
                        time.sleep(args.poll_seconds)
                        continue
                    print(json.dumps(asdict(outcome), default=str), flush=True)
            stopped_cleanly = True
            return 0
        for _ in range(args.max_items):
            outcome = _run_once_with_heartbeat(
                worker,
                heartbeat,
                worker_key=args.worker_id,
            )
            heartbeat.write(worker_key=args.worker_id, state="ready")
            outcomes.append(asdict(outcome))
            if outcome.status == "idle":
                break
        stopped_cleanly = True
    except Exception as error:
        heartbeat.write(
            worker_key=args.worker_id,
            state="error",
            error_summary=f"{type(error).__name__}: {error}",
        )
        raise
    finally:
        if stopped_cleanly:
            heartbeat.write(worker_key=args.worker_id, state="stopped")
        engine.dispose()
    print(json.dumps(outcomes, indent=2, default=str))
    return 0


def run() -> None:
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
