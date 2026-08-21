from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.frozen_sp500_lifecycle import (
    FrozenSp500AetLifecyclePublicationService,
    FrozenSp500EsrxLifecyclePublicationService,
    FrozenSp500L3LifecyclePublicationService,
    FrozenSp500LifecyclePublicationService,
    FrozenSp500TwxLifecyclePublicationService,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish the source-backed frozen S&P 500 lifecycle repair set"
    )
    parser.add_argument("--created-by", required=True)
    args = parser.parse_args(argv)
    engine = create_postgres_engine(get_settings().database_url)
    try:
        publications = {
            "initial": FrozenSp500LifecyclePublicationService(engine).publish(
                created_by=args.created_by
            ),
            "l3harris": FrozenSp500L3LifecyclePublicationService(engine).publish(
                created_by=args.created_by
            ),
            "aetna": FrozenSp500AetLifecyclePublicationService(engine).publish(
                created_by=args.created_by
            ),
            "express_scripts": FrozenSp500EsrxLifecyclePublicationService(
                engine
            ).publish(created_by=args.created_by),
            "time_warner": FrozenSp500TwxLifecyclePublicationService(engine).publish(
                created_by=args.created_by
            ),
        }
    finally:
        engine.dispose()
    print(
        json.dumps(
            {key: asdict(value) for key, value in publications.items()},
            default=str,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
