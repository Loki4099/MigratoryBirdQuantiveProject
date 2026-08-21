from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from style_rotation.v022.sp500_data_audit import (
    Sp500CandidateDates,
    audit_sp500_seed,
    audit_unmapped_historical_identities,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit S&P 500 historical seed evidence without database or network writes"
    )
    parser.add_argument("runtime_root", type=Path)
    parser.add_argument("source_project_root", type=Path)
    parser.add_argument("--warmup-start", type=date.fromisoformat, default=date(2004, 12, 31))
    parser.add_argument(
        "--evaluation-start", type=date.fromisoformat, default=date(2007, 1, 3)
    )
    parser.add_argument(
        "--evaluation-end", type=date.fromisoformat, default=date(2026, 6, 30)
    )
    parser.add_argument("--required-warmup-sessions", type=int, default=504)
    parser.add_argument(
        "--include-identity-review",
        action="store_true",
        help="Include unresolved symbols with first/last observation and episode counts",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    candidate_dates = Sp500CandidateDates(
        args.warmup_start,
        args.evaluation_start,
        args.evaluation_end,
        args.required_warmup_sessions,
    )
    report = audit_sp500_seed(
        runtime_root=args.runtime_root,
        source_project_root=args.source_project_root,
        candidate_dates=candidate_dates,
    )
    payload = report.to_dict()
    if args.include_identity_review:
        payload["identity_review"] = [
            asdict(item)
            for item in audit_unmapped_historical_identities(
                runtime_root=args.runtime_root,
                candidate_dates=candidate_dates,
            )
        ]
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if report.decision == "eligible_for_publication_review" else 2


def run() -> None:
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
