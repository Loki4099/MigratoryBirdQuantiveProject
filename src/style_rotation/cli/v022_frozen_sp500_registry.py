from __future__ import annotations

import argparse
import json

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.frozen_sp500_registry import (
    FrozenSp500RegistryPublicationService,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish the executable v0.22 frozen S&P Asset Registry"
    )
    parser.add_argument("--created-by", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = create_postgres_engine(get_settings().database_url)
    try:
        publication = FrozenSp500RegistryPublicationService(engine).publish(
            created_by=args.created_by
        )
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "asset_registry_release_id": str(publication.asset_registry_release_id),
                "artifact_id": str(publication.artifact_id),
                "profile_count": publication.profile_count,
                "selected_security_count": publication.selected_security_count,
                "explicit_asset_selection_id": str(publication.selection.selection_id),
                "explicit_asset_selection_artifact_id": str(
                    publication.selection.artifact_id
                ),
                "selection_fingerprint": publication.selection.selection_fingerprint,
                "registry_reused": publication.reused,
                "selection_reused": publication.selection.reused,
            },
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
