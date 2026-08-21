from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import suppress
from dataclasses import asdict

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.shadow_comparator import ShadowComparisonCoordinator
from style_rotation.v022.shadow_dual_run import (
    RuntimeCapability,
    ShadowV021ReferenceWorker,
    ShadowV022DecisionWorker,
)
from style_rotation.v022.shadow_runtime_worker import ShadowRuntimeCycle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Advance exact v0.21/v0.22 Shadow decisions and comparisons"
    )
    configure_parser(parser)
    return parser


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--service-principal", default="shadow-runtime")
    parser.add_argument("--v021-worker-id", default="v021-shadow-reference-worker")
    parser.add_argument("--v022-worker-id", default="v022-shadow-decision-worker")
    for runtime in ("v021", "v022"):
        parser.add_argument(f"--{runtime}-compiler-version", required=True)
        parser.add_argument(f"--{runtime}-executor-version", required=True)
        parser.add_argument(f"--{runtime}-environment-fingerprint", required=True)
        parser.add_argument(f"--{runtime}-capability-key", required=True)
    parser.add_argument("--max-items", type=int, default=1)
    parser.add_argument("--forever", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)


def main(argv: list[str] | None = None) -> int:
    return run_parsed(build_parser().parse_args(argv))


def run_parsed(args: argparse.Namespace) -> int:
    if args.max_items < 1:
        raise ValueError("max-items must be positive")
    if args.poll_seconds <= 0:
        raise ValueError("poll-seconds must be positive")
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    cycle = ShadowRuntimeCycle(
        v021_worker=ShadowV021ReferenceWorker(
            engine,
            service_principal=args.service_principal,
            worker_id=args.v021_worker_id,
            capability=_capability(args, "v021", "v0.21"),
        ),
        v022_worker=ShadowV022DecisionWorker(
            engine,
            service_principal=args.service_principal,
            worker_id=args.v022_worker_id,
            capability=_capability(args, "v022", "v0.22"),
        ),
        comparisons=ShadowComparisonCoordinator(engine),
    )
    outcomes: list[dict[str, object]] = []
    try:
        if args.forever:
            with suppress(KeyboardInterrupt):
                while True:
                    outcome = cycle.run_once()
                    if outcome.status == "idle":
                        time.sleep(args.poll_seconds)
                        continue
                    print(json.dumps(asdict(outcome), default=str), flush=True)
            return 0
        for _ in range(args.max_items):
            outcome = cycle.run_once()
            outcomes.append(asdict(outcome))
            if outcome.status == "idle":
                break
    finally:
        engine.dispose()
    print(json.dumps(outcomes, indent=2, default=str))
    return 0


def _capability(
    args: argparse.Namespace, prefix: str, contract: str
) -> RuntimeCapability:
    return RuntimeCapability(
        contract,  # type: ignore[arg-type]
        getattr(args, f"{prefix}_compiler_version"),
        getattr(args, f"{prefix}_executor_version"),
        getattr(args, f"{prefix}_environment_fingerprint"),
        getattr(args, f"{prefix}_capability_key"),
    ).validated()


def run() -> None:
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
