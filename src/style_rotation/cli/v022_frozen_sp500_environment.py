from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.frozen_sp500_environment import (
    FrozenSp500CohortPublication,
    FrozenSp500EnvironmentPublication,
    FrozenSp500EnvironmentPublicationService,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish fixed weekly/monthly v0.22 S&P500 Evaluation Cohorts"
    )
    parser.add_argument("--created-by", required=True)
    parser.add_argument(
        "--phase",
        choices=("cohorts", "runtimes", "all"),
        default="all",
        help=(
            "Publish Cohort v10 before Gate v4, runtime contracts after Gate v4, "
            "or both as an idempotent wrapper"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = create_postgres_engine(get_settings().database_url)
    try:
        service = FrozenSp500EnvironmentPublicationService(engine)
        publication: FrozenSp500CohortPublication | FrozenSp500EnvironmentPublication
        if args.phase == "cohorts":
            publication = service.publish_cohorts(created_by=args.created_by)
        elif args.phase == "runtimes":
            publication = service.publish_runtimes(created_by=args.created_by)
        else:
            publication = service.publish(created_by=args.created_by)
    finally:
        engine.dispose()
    print(json.dumps(asdict(publication), default=str, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
