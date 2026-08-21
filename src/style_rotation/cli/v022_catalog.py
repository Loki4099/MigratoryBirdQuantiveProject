from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.catalog import (
    catalog_component_plan,
    diff_catalog_releases,
    lint_catalog_release,
    load_catalog_release,
)
from style_rotation.v022.publication import (
    CatalogPublicationContext,
    publish_catalog_release,
    verify_published_catalog,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage immutable v0.22 Catalog Releases")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint_parser = subparsers.add_parser("lint", help="Validate source contracts without DB writes")
    lint_parser.add_argument("manifest", type=Path)

    diff_parser = subparsers.add_parser("diff", help="Compare exact component identities")
    diff_parser.add_argument("before", type=Path)
    diff_parser.add_argument("after", type=Path)

    plan_parser = subparsers.add_parser("plan", help="Show canonical publication identities")
    plan_parser.add_argument("manifest", type=Path)

    publish_parser = subparsers.add_parser("publish", help="Atomically publish one Release")
    publish_parser.add_argument("manifest", type=Path)

    verify_parser = subparsers.add_parser("verify", help="Rebuild and verify a DB Release")
    verify_parser.add_argument("release_artifact_id", type=uuid.UUID)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: Any
    if args.command == "lint":
        result = lint_catalog_release(args.manifest)
    elif args.command == "diff":
        result = diff_catalog_releases(args.before, args.after).to_dict()
    elif args.command == "plan":
        loaded = load_catalog_release(args.manifest)
        plan = catalog_component_plan(loaded.bundle)
        result = {
            "release_key": loaded.bundle.release.release_key,
            "source_manifest_hash": loaded.bundle.source_manifest_hash,
            "component_count": len(plan),
            "components": [asdict(item) for item in plan],
        }
    elif args.command == "publish":
        settings = get_settings()
        engine = create_postgres_engine(settings.database_url)
        try:
            result = publish_catalog_release(
                engine,
                args.manifest,
                context=CatalogPublicationContext(
                    actor_key=settings.catalog_publisher_actor,
                    reviewer_actor=settings.catalog_publisher_actor,
                    trusted_local_authorization_bootstrap=(
                        settings.environment in {"local", "test"}
                    ),
                ),
            ).to_dict()
        finally:
            engine.dispose()
    else:
        engine = create_postgres_engine(get_settings().database_url)
        try:
            result = verify_published_catalog(engine, args.release_artifact_id)
        finally:
            engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


def run() -> None:
    try:
        raise SystemExit(main())
    except (ValueError, ValidationError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
