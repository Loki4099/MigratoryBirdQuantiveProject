from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from style_rotation.v022.green_baseline_import import (
    DatasetImportPlan,
    FreshIdentity,
    GreenBaselineImportPlan,
)
from style_rotation.v022.green_baseline_universe import (
    GreenBaselineUniverseSpec,
    publish_green_baseline_universe,
)


def _plan(path: Path) -> GreenBaselineImportPlan:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    payload["identities"] = tuple(FreshIdentity(**item) for item in payload["identities"])
    payload["datasets"] = tuple(DatasetImportPlan(**item) for item in payload["datasets"])
    payload["dependency_order"] = tuple(payload["dependency_order"])
    payload["forbidden_source_identity_domains"] = tuple(
        payload["forbidden_source_identity_domains"]
    )
    return GreenBaselineImportPlan(**payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish the clean-green S&P 500 Universe")
    parser.add_argument("transfer_root", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--created-by", default="codex_green_baseline_g2")
    args = parser.parse_args()
    publication = publish_green_baseline_universe(
        create_engine(args.database_url),
        GreenBaselineUniverseSpec(
            transfer_root=args.transfer_root,
            plan=_plan(args.plan),
            created_by=args.created_by,
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(publication.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(publication.to_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
