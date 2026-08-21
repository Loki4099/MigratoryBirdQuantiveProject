from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.strategy_product_parity import (
    build_strategy_product_parity_evidence,
    validate_strategy_product_parity_evidence,
)

ROOT = Path(__file__).parents[1]
ORACLE = ROOT / "v0.22/m0/v021-baseline-manifest.v0.22.0.json"
REGISTRY = ROOT / "v0.22/m5/strategy-product-migration-registry.v0.22.0.json"
MODEL_PARITY = ROOT / "v0.22/m5/model-parity-evidence.v0.22.0.json"
OUTPUT = ROOT / "v0.22/m5/strategy-defense-product-parity-evidence.v0.22.0.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or verify M5 Strategy parity")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry = _read(REGISTRY)
    model_parity = _read(MODEL_PARITY)
    if args.verify:
        evidence = _read(args.output)
    else:
        engine = create_postgres_engine(get_settings().database_url)
        try:
            evidence = build_strategy_product_parity_evidence(
                engine,
                oracle_path=ORACLE,
                strategy_registry_path=REGISTRY,
                model_parity_path=MODEL_PARITY,
            )
        finally:
            engine.dispose()
        args.output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    validate_strategy_product_parity_evidence(
        evidence,
        strategy_registry=registry,
        model_parity_evidence=model_parity,
    )
    print(json.dumps(evidence["summary"], indent=2, sort_keys=True))


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


if __name__ == "__main__":
    main()
