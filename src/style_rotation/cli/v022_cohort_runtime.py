from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.cohort_runtime_contract import CohortRuntimeContractService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish the immutable v0.22 Evaluation Cohort runtime contract"
    )
    parser.add_argument("evaluation_cohort_version_id", type=uuid.UUID)
    parser.add_argument("dataset_gate_assessment_id", type=uuid.UUID)
    parser.add_argument("--created-by", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = create_postgres_engine(get_settings().database_url)
    publication = CohortRuntimeContractService(engine).publish(
        evaluation_cohort_version_id=args.evaluation_cohort_version_id,
        dataset_gate_assessment_id=args.dataset_gate_assessment_id,
        created_by=args.created_by,
    )
    print(json.dumps(asdict(publication), default=str, sort_keys=True))
    return 0


def run() -> None:
    try:
        raise SystemExit(main())
    except (LookupError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
