from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.frozen_sp500_market import FrozenSp500MarketPublicationService
from style_rotation.v022.frozen_sp500_seed import (
    FrozenSp500PreparationService,
    load_frozen_sp500_seed,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare immutable v0.22 frozen S&P evidence, identities and membership"
    )
    parser.add_argument("runtime_root", type=Path)
    parser.add_argument("source_project_root", type=Path)
    parser.add_argument("--created-by", required=True)
    parser.add_argument("--publish-market", action="store_true")
    parser.add_argument(
        "--market-contract",
        type=Path,
        default=Path("v0.22/catalogs/data_contracts/equity_market.v0.22.0.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    seed = load_frozen_sp500_seed(args.runtime_root, args.source_project_root)
    engine = create_postgres_engine(get_settings().database_url)
    try:
        publication = FrozenSp500PreparationService(engine).prepare(
            seed, created_by=args.created_by
        )
        market = (
            FrozenSp500MarketPublicationService(engine).publish(
                seed,
                publication,
                contract_path=args.market_contract.resolve(),
                created_by=args.created_by,
            )
            if args.publish_market
            else None
        )
    finally:
        engine.dispose()
    result = {"preparation": asdict(publication)}
    if market is not None:
        result["market"] = asdict(market)
    print(json.dumps(result, default=str, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
