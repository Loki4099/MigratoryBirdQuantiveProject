from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, cast

from style_rotation.config.settings import get_settings
from style_rotation.data.providers.snapshots import YahooYFinanceSnapshotAdapter
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.targeted_yahoo_market_repair import (
    PRIMARY_V3_DATASET_PUBLICATION_ID,
    RepairGapType,
    TargetedYahooMarketRepairService,
    TargetedYahooRepairEntry,
    TargetedYahooRepairSpec,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or publish explicit, reviewed Yahoo replacements against the "
            "frozen primary v3 market Dataset"
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "publish"):
        command = commands.add_parser(name)
        command.add_argument("spec", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    spec = repair_spec(_read_object(args.spec))
    if args.command == "validate":
        print(json.dumps(asdict(spec), default=str, indent=2, sort_keys=True))
        return 0
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    publications = TargetedYahooMarketRepairService(
        engine,
        YahooYFinanceSnapshotAdapter(settings.yahoo_timeout_seconds),
    ).publish(spec)
    print(
        json.dumps(
            [asdict(item) for item in publications],
            default=str,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def repair_spec(document: dict[str, Any]) -> TargetedYahooRepairSpec:
    primary = document.get("primary_dataset_publication_id", str(PRIMARY_V3_DATASET_PUBLICATION_ID))
    if not isinstance(primary, str):
        raise ValueError("primary_dataset_publication_id must be a UUID string")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("entries must be a list")
    return TargetedYahooRepairSpec(
        primary_dataset_publication_id=uuid.UUID(primary),
        entries=tuple(repair_entry(_object(item)) for item in raw_entries),
        created_by=_text(document, "created_by"),
    )


def repair_entry(document: dict[str, Any]) -> TargetedYahooRepairEntry:
    raw_sessions = document.get("expected_sessions")
    if not isinstance(raw_sessions, list) or not all(
        isinstance(item, str) for item in raw_sessions
    ):
        raise ValueError("expected_sessions must be a list of ISO date strings")
    return TargetedYahooRepairEntry(
        security_id=uuid.UUID(_text(document, "security_id")),
        provider_symbol=_text(document, "provider_symbol"),
        gap_key=_text(document, "gap_key"),
        gap_type=cast(RepairGapType, _text(document, "gap_type")),
        gap_start=date.fromisoformat(_text(document, "gap_start")),
        gap_end=date.fromisoformat(_text(document, "gap_end")),
        expected_sessions=tuple(date.fromisoformat(item) for item in raw_sessions),
        reason=_text(document, "reason"),
        version_number=_integer(document.get("version_number", 1), "version_number"),
    )


def _read_object(path: Path) -> dict[str, Any]:
    return _object(json.loads(path.read_text(encoding="utf-8")))


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Targeted repair specification values must be JSON objects")
    return cast(dict[str, Any], value)


def _text(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be non-empty text")
    return value


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def run() -> None:
    try:
        raise SystemExit(main())
    except (FileNotFoundError, LookupError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
