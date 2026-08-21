from __future__ import annotations

import argparse
import json
from pathlib import Path

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.strategy_product_migration import (
    extract_strategy_product_registry,
    load_strategy_product_registry,
    strategy_product_summary,
)

ROOT = Path(__file__).parents[1]
ORACLE = ROOT / "v0.22/m0/v021-baseline-manifest.v0.22.0.json"
STRATEGY = ROOT / "v0.22/catalogs/strategies/cross_section.v0.22.1.json"
DEFENSE = ROOT / "v0.22/catalogs/defense/parity.v0.22.1.json"
AGGREGATION = ROOT / "v0.22/catalogs/aggregation/deterministic.v0.22.0.json"
SIGNALS = ROOT / "v0.22/m4/migration-registry.v0.22.3.json"
OUTPUT = ROOT / "v0.22/m5/strategy-product-migration-registry.v0.22.0.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or verify M5 Strategy/Product Registry")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        registry = load_strategy_product_registry(args.output)
    else:
        engine = create_postgres_engine(get_settings().database_url)
        try:
            registry = extract_strategy_product_registry(
                engine,
                oracle_path=ORACLE,
                strategy_catalog_path=STRATEGY,
                defense_catalog_path=DEFENSE,
                aggregation_catalog_path=AGGREGATION,
                signal_registry_path=SIGNALS,
            )
        finally:
            engine.dispose()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(strategy_product_summary(registry), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
