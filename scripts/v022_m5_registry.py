from __future__ import annotations

import argparse
import json
from pathlib import Path

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.model_migration import (
    extract_model_migration_registry,
    model_registry_summary,
    validate_model_migration_registry,
)

ROOT = Path(__file__).parents[1]
ORACLE = ROOT / "v0.22/m0/v021-baseline-manifest.v0.22.0.json"
AGGREGATION = ROOT / "v0.22/catalogs/aggregation/deterministic.v0.22.0.json"
SIGNALS = ROOT / "v0.22/m4/migration-registry.v0.22.3.json"
OUTPUT = ROOT / "v0.22/m5/model-migration-registry.v0.22.0.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or verify the M5 Model Registry")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    oracle = _read(ORACLE)
    aggregation = _read(AGGREGATION)
    signals = _read(SIGNALS)
    if args.verify:
        registry = _read(args.output)
        fingerprint = registry.pop("registry_fingerprint", None)
        validate_model_migration_registry(
            registry,
            oracle_manifest=oracle,
            aggregation_catalog=aggregation,
            signal_registry=signals,
        )
        from style_rotation.core.canonical import sha256_hexdigest

        if fingerprint != sha256_hexdigest(registry):
            raise ValueError("Model Registry fingerprint drift")
        registry["registry_fingerprint"] = fingerprint
    else:
        engine = create_postgres_engine(get_settings().database_url)
        try:
            registry = extract_model_migration_registry(
                engine,
                oracle_manifest_path=ORACLE,
                aggregation_catalog_path=AGGREGATION,
                signal_registry_path=SIGNALS,
            )
        finally:
            engine.dispose()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(model_registry_summary(registry), indent=2, sort_keys=True))


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


if __name__ == "__main__":
    main()
