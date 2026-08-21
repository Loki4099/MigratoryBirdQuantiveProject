from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import suppress

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.asset_data_export import AssetDataExportWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Consume durable v0.22 Asset Data Export work"
    )
    parser.add_argument("--worker-id", default="v022-asset-data-export-worker")
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
    worker = AssetDataExportWorker(engine, worker_id=args.worker_id)
    outcomes: list[str] = []
    try:
        if args.forever:
            with suppress(KeyboardInterrupt):
                while True:
                    outcome = worker.run_once()
                    if outcome == "idle":
                        time.sleep(args.poll_seconds)
                        continue
                    print(json.dumps({"status": outcome}), flush=True)
            return 0
        for _ in range(args.max_items):
            outcome = worker.run_once()
            outcomes.append(outcome)
            if outcome == "idle":
                break
    finally:
        engine.dispose()
    print(json.dumps(outcomes, indent=2))
    return 0


def run() -> None:
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
