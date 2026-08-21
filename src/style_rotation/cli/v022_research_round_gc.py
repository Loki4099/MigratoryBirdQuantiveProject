from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import Engine, text

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore
from style_rotation.v022.research_round_gc import ResearchRoundGCService

_WAITING_FOR_TERMINAL_WORK = "research_round_gc_waiting_for_terminal_work"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan and execute Product-safe cleanup of closed v0.22 Research Rounds"
    )
    parser.add_argument("--round-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--forever", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    return parser


def _next_round(engine: Engine) -> uuid.UUID | None:
    with engine.connect() as connection:
        value = connection.scalar(
            text(
                "SELECT research_round_id FROM workspace.v022_research_round "
                "WHERE status='gc_pending' ORDER BY closed_at,research_round_id LIMIT 1"
            )
        )
    return None if value is None else uuid.UUID(str(value))


def _run_once(
    service: ResearchRoundGCService,
    engine: Engine,
    *,
    round_id: uuid.UUID | None,
    dry_run: bool,
) -> dict[str, object]:
    selected = round_id or _next_round(engine)
    if selected is None:
        return {"status": "idle"}
    plan = service.plan(selected)
    if dry_run:
        return {"status": "planned", **asdict(plan)}
    result = service.execute(plan.plan_id)
    return {"status": "completed", **asdict(result)}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.poll_seconds <= 0:
        raise ValueError("poll-seconds must be positive")
    requested_round = None if args.round_id is None else uuid.UUID(args.round_id)
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    service = ResearchRoundGCService(
        engine,
        LocalPayloadObjectStore(Path(settings.v022_payload_directory)),
    )
    try:
        if args.forever:
            if args.dry_run:
                raise ValueError("--forever cannot be combined with --dry-run")
            if requested_round is not None:
                raise ValueError("--forever cannot target one round-id")
            with suppress(KeyboardInterrupt):
                while True:
                    try:
                        outcome = _run_once(service, engine, round_id=None, dry_run=False)
                    except ValueError as error:
                        if str(error) != _WAITING_FOR_TERMINAL_WORK:
                            raise
                        print(
                            json.dumps(
                                {
                                    "status": "waiting",
                                    "reason": _WAITING_FOR_TERMINAL_WORK,
                                }
                            ),
                            file=sys.stderr,
                            flush=True,
                        )
                        time.sleep(args.poll_seconds)
                        continue
                    if outcome["status"] == "idle":
                        time.sleep(args.poll_seconds)
                    else:
                        print(json.dumps(outcome, default=str), flush=True)
            return 0
        print(
            json.dumps(
                _run_once(service, engine, round_id=requested_round, dry_run=args.dry_run),
                indent=2,
                default=str,
            )
        )
        return 0
    finally:
        engine.dispose()


def run() -> None:
    try:
        raise SystemExit(main())
    except (LookupError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
