from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.migration import load_migration_registry
from style_rotation.v022.model_migration import load_model_migration_registry
from style_rotation.v022.model_parity import (
    V021ModelParityHarness,
    validate_model_parity_evidence,
)

ROOT = Path(__file__).parents[1]
MODEL_REGISTRY = ROOT / "v0.22/m5/model-migration-registry.v0.22.0.json"
SIGNAL_REGISTRY = ROOT / "v0.22/m4/migration-registry.v0.22.3.json"
OUTPUT = ROOT / "v0.22/m5/model-parity-evidence.v0.22.0.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or verify M5 Model parity")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    model_registry = load_model_migration_registry(MODEL_REGISTRY)
    signal_registry = load_migration_registry(SIGNAL_REGISTRY)
    if args.verify:
        evidence = _read(args.output)
    else:
        engine = create_postgres_engine(get_settings().database_url)
        try:
            evidence = V021ModelParityHarness(
                engine, model_registry, signal_registry
            ).build_evidence()
        finally:
            engine.dispose()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    validate_model_parity_evidence(
        evidence,
        model_registry=model_registry,
        signal_registry=signal_registry,
    )
    print(json.dumps(evidence["summary"], indent=2, sort_keys=True))


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


if __name__ == "__main__":
    main()
