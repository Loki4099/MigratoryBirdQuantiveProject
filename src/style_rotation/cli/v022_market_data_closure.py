from __future__ import annotations

import argparse
import json
import sys
import uuid

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.market_data_closure import MarketDataClosureAuditor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a read-only v0.22 market-data closure audit"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    exact = commands.add_parser("exact")
    exact.add_argument("dataset_publication_id", type=uuid.UUID)
    exact_reference = exact.add_mutually_exclusive_group(required=True)
    exact_reference.add_argument("--cohort", type=uuid.UUID)
    exact_reference.add_argument("--runtime-contract", type=uuid.UUID)

    candidate = commands.add_parser("candidate-against-reference")
    candidate.add_argument("candidate_dataset_publication_id", type=uuid.UUID)
    candidate_reference = candidate.add_mutually_exclusive_group(required=True)
    candidate_reference.add_argument("--reference-cohort", type=uuid.UUID)
    candidate_reference.add_argument("--reference-runtime-contract", type=uuid.UUID)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = create_postgres_engine(get_settings().database_url)
    auditor = MarketDataClosureAuditor(engine)
    if args.command == "exact":
        report = auditor.audit(
            dataset_publication_id=args.dataset_publication_id,
            evaluation_cohort_version_id=args.cohort,
            evaluation_cohort_runtime_contract_id=args.runtime_contract,
        )
    else:
        report = auditor.audit_candidate_against_reference_cohort(
            candidate_dataset_publication_id=(
                args.candidate_dataset_publication_id
            ),
            reference_evaluation_cohort_version_id=args.reference_cohort,
            reference_evaluation_cohort_runtime_contract_id=(
                args.reference_runtime_contract
            ),
        )
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0


def run() -> None:
    try:
        raise SystemExit(main())
    except (LookupError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
