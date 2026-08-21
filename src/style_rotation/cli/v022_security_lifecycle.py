from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.security_lifecycle import (
    LifecycleEvidenceRef,
    SecurityLifecycleEventService,
    SecurityLifecycleEventSpec,
    SecuritySettlementLegSpec,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or publish append-only v0.22 Security lifecycle evidence"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "publish"):
        command = commands.add_parser(name)
        command.add_argument("spec", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    spec = event_spec(_read_object(args.spec))
    if args.command == "validate":
        print(json.dumps(spec.document(), indent=2, sort_keys=True))
        return 0
    engine = create_postgres_engine(get_settings().database_url)
    publication = SecurityLifecycleEventService(engine).publish(spec)
    print(json.dumps(asdict(publication), default=str, sort_keys=True))
    return 0


def event_spec(document: dict[str, Any]) -> SecurityLifecycleEventSpec:
    evidence_value = document.get("evidence")
    if not isinstance(evidence_value, list):
        raise ValueError("evidence must be a list")
    evidence = tuple(
        LifecycleEvidenceRef(
            artifact_id=uuid.UUID(_text(_object(item), "artifact_id")),
            role=cast(Any, _text(_object(item), "role")),
        )
        for item in evidence_value
    )
    legs_value = document.get("settlement_legs", [])
    if not isinstance(legs_value, list):
        raise ValueError("settlement_legs must be a list")
    legs = tuple(_settlement_leg(_object(item)) for item in legs_value)
    return SecurityLifecycleEventSpec(
        security_id=uuid.UUID(_text(document, "security_id")),
        event_key=_text(document, "event_key"),
        version_number=_integer(document, "version_number"),
        event_type=cast(Any, _text(document, "event_type")),
        event_status=cast(Any, _text(document, "event_status")),
        announced_at=datetime.fromisoformat(_text(document, "announced_at")),
        effective_session=date.fromisoformat(_text(document, "effective_session")),
        last_trading_session=_optional_date(document.get("last_trading_session")),
        settlement_session=_optional_date(document.get("settlement_session")),
        selectable_after=_boolean(document, "selectable_after"),
        tradable_after=_boolean(document, "tradable_after"),
        valuation_state_after=cast(Any, _text(document, "valuation_state_after")),
        evidence=evidence,
        settlement_legs=legs,
        supersedes_lifecycle_event_id=_optional_uuid(
            document.get("supersedes_lifecycle_event_id")
        ),
        created_by=_text(document, "created_by"),
        details=_object(document.get("details", {})),
    )


def _settlement_leg(document: dict[str, Any]) -> SecuritySettlementLegSpec:
    return SecuritySettlementLegSpec(
        leg_kind=cast(Any, _text(document, "leg_kind")),
        target_security_id=_optional_uuid(document.get("target_security_id")),
        quantity_per_source_share=_optional_decimal(
            document.get("quantity_per_source_share")
        ),
        cash_amount_per_source_share=_optional_decimal(
            document.get("cash_amount_per_source_share")
        ),
        currency=_optional_text(document.get("currency")),
        valuation_policy=cast(Any, _text(document, "valuation_policy")),
    )


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return _object(value)


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Lifecycle specification values must be JSON objects")
    return cast(dict[str, Any], value)


def _text(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be non-empty text")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Optional text values must be non-empty text")
    return value


def _integer(document: dict[str, Any], key: str) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _boolean(document: dict[str, Any], key: str) -> bool:
    value = document.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _optional_uuid(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Optional identity values must be UUID strings")
    return uuid.UUID(value)


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Optional date values must be ISO date strings")
    return date.fromisoformat(value)


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("Settlement numeric values must be decimal strings")
    return Decimal(value)


def run() -> None:
    try:
        raise SystemExit(main())
    except (FileNotFoundError, LookupError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
