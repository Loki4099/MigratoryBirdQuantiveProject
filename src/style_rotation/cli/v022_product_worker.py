from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.product_decision_worker import (
    ProductDecisionWorker,
    ProductDecisionWorkerOutcome,
)
from style_rotation.v022.product_input_refresh import ProductInputRefreshService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute due prospective v0.22 Product Decision sessions"
    )
    parser.add_argument("--worker-id", default="v022-product-worker")
    parser.add_argument("--max-items", type=int, default=1)
    parser.add_argument("--forever", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--input-refresh-limit", type=int, default=50)
    parser.add_argument("--input-actor-key")
    return parser


def _run_cycle(
    worker: ProductDecisionWorker,
    inputs: ProductInputRefreshService,
    *,
    observed_at: datetime,
    actor_key: str,
    refresh_limit: int,
) -> tuple[int, ProductDecisionWorkerOutcome]:
    prepared = inputs.prepare_pending(
        observed_at=observed_at,
        actor_key=actor_key,
        limit=refresh_limit,
    )
    return len(prepared), worker.run_once(observed_at=observed_at)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_items < 1:
        raise ValueError("max-items must be positive")
    if args.poll_seconds <= 0:
        raise ValueError("poll-seconds must be positive")
    if args.input_refresh_limit < 1 or args.input_refresh_limit > 500:
        raise ValueError("input-refresh-limit must be between 1 and 500")
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    worker = ProductDecisionWorker(
        engine,
        payload_directory=settings.v022_payload_directory,
        worker_key=args.worker_id,
    )
    inputs = ProductInputRefreshService(engine)
    input_actor_key = args.input_actor_key or f"{args.worker_id}:input-refresh"
    outcomes: list[dict[str, object]] = []
    try:
        if args.forever:
            with suppress(KeyboardInterrupt):
                while True:
                    prepared_count, outcome = _run_cycle(
                        worker,
                        inputs,
                        observed_at=datetime.now(UTC),
                        actor_key=input_actor_key,
                        refresh_limit=args.input_refresh_limit,
                    )
                    if prepared_count:
                        print(
                            json.dumps(
                                {"status": "inputs_prepared", "count": prepared_count}
                            ),
                            flush=True,
                        )
                    if outcome.status in {"idle", "waiting_for_input"}:
                        if outcome.status == "waiting_for_input":
                            print(json.dumps(asdict(outcome), default=str), flush=True)
                        time.sleep(args.poll_seconds)
                        continue
                    print(json.dumps(asdict(outcome), default=str), flush=True)
            return 0
        for _ in range(args.max_items):
            _prepared_count, outcome = _run_cycle(
                worker,
                inputs,
                observed_at=datetime.now(UTC),
                actor_key=input_actor_key,
                refresh_limit=args.input_refresh_limit,
            )
            outcomes.append(asdict(outcome))
            if outcome.status == "idle":
                break
    finally:
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
