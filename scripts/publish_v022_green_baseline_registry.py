from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine

from style_rotation.v022.green_baseline_registry import GreenBaselineRegistryService


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish clean-green Registry 0.22.4")
    parser.add_argument("output", type=Path)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    engine = create_engine(args.database_url)
    try:
        publication = GreenBaselineRegistryService(engine).publish(
            created_by="codex-green-baseline-registry-0.22.4"
        )
        document = publication.to_dict()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(document, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
