from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.parity import V021ParityHarness

PROJECT_ROOT = Path(__file__).parents[1]
REGISTRY = PROJECT_ROOT / "v0.22/m4/migration-registry.v0.22.3.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "v0.22/m4/parity-evidence.v0.22.0.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or verify M4 point parity Evidence")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    engine = create_postgres_engine(get_settings().database_url)
    try:
        document = V021ParityHarness.from_registry_path(engine, REGISTRY).build_evidence()
    finally:
        engine.dispose()
    if args.verify:
        if _read(args.output) != document:
            raise ValueError("Committed M4 parity Evidence differs from frozen Oracle replay")
    else:
        args.output.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(json.dumps(document["summary"], indent=2, sort_keys=True))
    print(f'evidence_fingerprint={document["evidence_fingerprint"]}')
    return 0 if document["summary"]["passed"] else 1


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
