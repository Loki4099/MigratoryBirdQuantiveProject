from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.product_input_refresh import ProductInputRefreshService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare exact published inputs for v0.22 Product decisions"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    pending = subparsers.add_parser("pending", help="List due Product input sessions")
    pending.add_argument("--observed-at")
    pending.add_argument("--limit", type=int, default=50)
    prepare = subparsers.add_parser(
        "prepare", help="Bind one exact Dataset Gate to one Product session"
    )
    prepare.add_argument("--product-enrollment-id", type=uuid.UUID, required=True)
    prepare.add_argument("--decision-session-id", type=uuid.UUID, required=True)
    prepare.add_argument("--dataset-gate-assessment-id", type=uuid.UUID, required=True)
    prepare.add_argument("--actor-key", default="v022-product-input-operator")
    return parser


def _aware_datetime(value: str | None) -> datetime:
    parsed = datetime.now(UTC) if value is None else datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("observed-at must include an explicit timezone")
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    service = ProductInputRefreshService(engine)
    try:
        if args.command == "pending":
            result = service.pending(
                observed_at=_aware_datetime(args.observed_at), limit=args.limit
            )
            print(json.dumps([asdict(item) for item in result], indent=2, default=str))
            return 0
        publication = service.prepare(
            product_enrollment_id=args.product_enrollment_id,
            decision_session_id=args.decision_session_id,
            dataset_gate_assessment_id=args.dataset_gate_assessment_id,
            actor_key=args.actor_key,
        )
        print(json.dumps(asdict(publication), indent=2, default=str))
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
