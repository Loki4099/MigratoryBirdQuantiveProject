from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import NoReturn

from style_rotation import __version__
from style_rotation.architecture import DOMAIN_BOUNDARIES
from style_rotation.catalog.bootstrap import publish_catalogs
from style_rotation.catalog.scope import publish_research_scope
from style_rotation.config.settings import get_settings
from style_rotation.lineage.service import ArtifactService
from style_rotation.persistence.database import database_status, reset_database, upgrade_database
from style_rotation.persistence.session import create_postgres_engine


@dataclass(frozen=True, slots=True)
class PlannedCommand:
    key: str
    summary: str
    delivery_milestone: str


PLANNED_COMMANDS = (
    PlannedCommand("bootstrap", "Publish versioned research catalogs", "M1C"),
    PlannedCommand("data", "Ingest, validate, and publish market data", "M2"),
    PlannedCommand("factor", "Calculate and publish factor datasets", "M3"),
    PlannedCommand("signal", "Calculate and publish signal datasets", "M4"),
    PlannedCommand("model", "Calculate and publish model datasets", "M5"),
    PlannedCommand("strategy", "Generate strategy products and target paths", "M6"),
    PlannedCommand("experiment", "Plan and run versioned experiments", "M7"),
    PlannedCommand("lineage", "Inspect artifact dependencies and manifests", "M1C"),
    PlannedCommand("artifact", "Inspect publication identity and status", "M1C"),
    PlannedCommand("backup", "Create and verify database backups", "M9"),
    PlannedCommand("api", "Run the local read-only API and application", "M1D"),
)


def _planned(command: PlannedCommand) -> int:
    print(
        f"'{command.key}' is registered but not implemented; "
        f"delivery milestone: {command.delivery_milestone}.",
        file=sys.stderr,
    )
    return 2


def _show_modules(as_json: bool) -> int:
    if as_json:
        print(json.dumps([asdict(item) for item in DOMAIN_BOUNDARIES], indent=2))
        return 0
    for boundary in DOMAIN_BOUNDARIES:
        upstream = ",".join(boundary.upstream) if boundary.upstream else "-"
        print(
            f"{boundary.key:10} milestone={boundary.delivery_milestone:3} "
            f"upstream={upstream} purpose={boundary.purpose}"
        )
    return 0


def _db_status(as_json: bool) -> int:
    status = database_status(get_settings().database_url)
    if as_json:
        print(json.dumps(status.to_dict(), indent=2))
    else:
        revision = status.current_revision or "unversioned"
        print(f"database={status.database_name} revision={revision}")
        print(f"schemas={','.join(status.present_schemas) or '-'}")
        print(f"missing={','.join(status.missing_schemas) or '-'}")
    return 0


def _db_upgrade() -> int:
    settings = get_settings()
    upgrade_database(settings.database_url)
    return _db_status(as_json=False)


def _db_reset(confirmation: str) -> int:
    settings = get_settings()
    reset_database(settings.database_url, confirmation, settings.environment)
    return _db_status(as_json=False)


def _artifact_service() -> ArtifactService:
    return ArtifactService(create_postgres_engine(get_settings().database_url))


def _bootstrap_catalogs(catalog_directory: str) -> int:
    results = publish_catalogs(_artifact_service(), Path(catalog_directory))
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


def _bootstrap_scope(catalog_file: str) -> int:
    engine = create_postgres_engine(get_settings().database_url)
    results = publish_research_scope(engine, Path(catalog_file))
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


def _artifact_list() -> int:
    print(json.dumps(_artifact_service().list_artifacts(), indent=2, ensure_ascii=False))
    return 0


def _artifact_show(artifact_id: str) -> int:
    payload = _artifact_service().describe(uuid.UUID(artifact_id))
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _artifact_invalidate(artifact_id: str, reason: str, replacement_artifact_id: str | None) -> int:
    tainted = _artifact_service().invalidate(
        uuid.UUID(artifact_id),
        reason,
        uuid.UUID(replacement_artifact_id) if replacement_artifact_id else None,
    )
    print(
        json.dumps(
            {"invalidated_artifact_id": artifact_id, "tainted_dependents": list(map(str, tainted))},
            indent=2,
        )
    )
    return 0


def _run_api(host: str, port: int) -> int:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The unauthenticated v0.2 API may only bind to a loopback address")
    import uvicorn

    uvicorn.run("style_rotation.api.app:app", host=host, port=port, reload=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="style-rotation",
        description="Versioned US style rotation research platform",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    modules_parser = subparsers.add_parser("modules", help="Show v0.2 domain boundaries")
    modules_parser.add_argument("--json", action="store_true", dest="as_json")
    modules_parser.set_defaults(handler=lambda args: _show_modules(args.as_json))

    db_parser = subparsers.add_parser("db", help="Database migration, reset, and status")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    db_status_parser = db_subparsers.add_parser("status", help="Show migration and schema status")
    db_status_parser.add_argument("--json", action="store_true", dest="as_json")
    db_status_parser.set_defaults(handler=lambda args: _db_status(args.as_json))
    db_upgrade_parser = db_subparsers.add_parser("upgrade", help="Upgrade to the v0.2 head")
    db_upgrade_parser.set_defaults(handler=lambda _args: _db_upgrade())
    db_reset_parser = db_subparsers.add_parser("reset", help="Destructively rebuild a local DB")
    db_reset_parser.add_argument("--confirm-database", required=True)
    db_reset_parser.set_defaults(handler=lambda args: _db_reset(args.confirm_database))

    bootstrap_parser = subparsers.add_parser("bootstrap", help="Publish research catalogs")
    bootstrap_subparsers = bootstrap_parser.add_subparsers(dest="bootstrap_command", required=True)
    catalogs_parser = bootstrap_subparsers.add_parser(
        "catalogs", help="Publish the M0 machine-readable catalogs"
    )
    catalogs_parser.add_argument("--catalog-dir", default="v0.2/catalogs")
    catalogs_parser.set_defaults(handler=lambda args: _bootstrap_catalogs(args.catalog_dir))
    scope_parser = bootstrap_subparsers.add_parser(
        "scope", help="Publish the M2A asset, universe, and data-requirement scope"
    )
    scope_parser.add_argument("--catalog-file", default="v0.2/catalogs/research_scope.v0.2.0.json")
    scope_parser.set_defaults(handler=lambda args: _bootstrap_scope(args.catalog_file))

    artifact_parser = subparsers.add_parser("artifact", help="Inspect artifact identity/status")
    artifact_subparsers = artifact_parser.add_subparsers(dest="artifact_command", required=True)
    artifact_list_parser = artifact_subparsers.add_parser("list", help="List artifacts")
    artifact_list_parser.set_defaults(handler=lambda _args: _artifact_list())
    artifact_show_parser = artifact_subparsers.add_parser("show", help="Show one artifact")
    artifact_show_parser.add_argument("artifact_id")
    artifact_show_parser.set_defaults(handler=lambda args: _artifact_show(args.artifact_id))
    invalidate_parser = artifact_subparsers.add_parser(
        "invalidate", help="Invalidate one artifact and taint downstream dependents"
    )
    invalidate_parser.add_argument("artifact_id")
    invalidate_parser.add_argument("--reason", required=True)
    invalidate_parser.add_argument("--replacement-artifact-id")
    invalidate_parser.set_defaults(
        handler=lambda args: _artifact_invalidate(
            args.artifact_id, args.reason, args.replacement_artifact_id
        )
    )

    lineage_parser = subparsers.add_parser("lineage", help="Inspect immutable lineage manifests")
    lineage_subparsers = lineage_parser.add_subparsers(dest="lineage_command", required=True)
    lineage_show_parser = lineage_subparsers.add_parser("show", help="Show expanded lineage")
    lineage_show_parser.add_argument("artifact_id")
    lineage_show_parser.set_defaults(handler=lambda args: _artifact_show(args.artifact_id))

    api_parser = subparsers.add_parser("api", help="Run the local read-only API and application")
    api_parser.add_argument("--host", default=get_settings().api_host)
    api_parser.add_argument("--port", type=int, default=get_settings().api_port)
    api_parser.set_defaults(handler=lambda args: _run_api(args.host, args.port))

    for command in PLANNED_COMMANDS:
        if command.key in {"bootstrap", "artifact", "lineage", "api"}:
            continue
        command_parser = subparsers.add_parser(command.key, help=command.summary)
        command_parser.set_defaults(handler=lambda _args, item=command: _planned(item))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = args.handler
    result: int = handler(args)
    return result


def run() -> NoReturn:
    try:
        exit_code = main()
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        exit_code = 2
    raise SystemExit(exit_code)


if __name__ == "__main__":
    run()
