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
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.market_reconciliation import (
    DEFAULT_RECONSTRUCTION_POLICY,
    AlternateObservationService,
    AlternateObservationSetSpec,
    GapResolutionEvidenceRef,
    MarketGapResolutionService,
    MarketGapResolutionSpec,
    MarketReconciliationService,
    MarketReconciliationSpec,
    ReconstructionPolicy,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or publish v0.22 alternate observations and reconciliation"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "validate-observation",
        "publish-observation",
        "validate-resolution",
        "publish-resolution",
        "validate-reconciliation",
        "reconcile",
    ):
        command = commands.add_parser(name)
        command.add_argument("spec", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    document = _read_object(args.spec)
    if args.command.endswith("observation"):
        observation = observation_spec(document)
        if args.command.startswith("validate-"):
            print(json.dumps(asdict(observation), default=str, indent=2, sort_keys=True))
            return 0
        engine = create_postgres_engine(get_settings().database_url)
        result = AlternateObservationService(engine).publish(observation)
        print(json.dumps(asdict(result), default=str, sort_keys=True))
        return 0
    elif args.command.endswith("resolution"):
        resolution = resolution_spec(document)
        if args.command.startswith("validate-"):
            print(json.dumps(asdict(resolution), default=str, indent=2, sort_keys=True))
            return 0
        engine = create_postgres_engine(get_settings().database_url)
        result_resolution = MarketGapResolutionService(engine).publish(resolution)
        print(json.dumps(asdict(result_resolution), default=str, sort_keys=True))
        return 0
    reconciliation = reconciliation_spec(document)
    if args.command.startswith("validate-"):
        print(json.dumps(asdict(reconciliation), default=str, indent=2, sort_keys=True))
        return 0
    engine = create_postgres_engine(get_settings().database_url)
    result_reconciliation = MarketReconciliationService(engine).reconcile(reconciliation)
    print(json.dumps(asdict(result_reconciliation), default=str, sort_keys=True))
    return 0


def observation_spec(document: dict[str, Any]) -> AlternateObservationSetSpec:
    return AlternateObservationSetSpec(
        source_snapshot_security_subject_id=uuid.UUID(
            _text(document, "source_snapshot_security_subject_id")
        ),
        observation_key=_text(document, "observation_key"),
        version_number=_integer(document, "version_number"),
        created_by=_text(document, "created_by"),
    )


def resolution_spec(document: dict[str, Any]) -> MarketGapResolutionSpec:
    raw_evidence = document.get("evidence")
    if not isinstance(raw_evidence, list):
        raise ValueError("evidence must be a list")
    evidence = tuple(
        GapResolutionEvidenceRef(
            artifact_id=uuid.UUID(_text(_object(item), "artifact_id")),
            role=cast(Any, _text(_object(item), "role")),
        )
        for item in raw_evidence
    )
    return MarketGapResolutionSpec(
        primary_dataset_publication_id=uuid.UUID(
            _text(document, "primary_dataset_publication_id")
        ),
        security_id=uuid.UUID(_text(document, "security_id")),
        gap_key=_text(document, "gap_key"),
        version_number=_integer(document, "version_number"),
        gap_type=cast(Any, _text(document, "gap_type")),
        gap_start=date.fromisoformat(_text(document, "gap_start")),
        gap_end=date.fromisoformat(_text(document, "gap_end")),
        resolution_kind=cast(Any, _text(document, "resolution_kind")),
        evidence=evidence,
        created_by=_text(document, "created_by"),
        alternate_observation_set_id=_optional_uuid(
            document.get("alternate_observation_set_id")
        ),
        supersedes_market_gap_resolution_id=_optional_uuid(
            document.get("supersedes_market_gap_resolution_id")
        ),
        details=_object(document.get("details", {})),
    )


def reconciliation_spec(document: dict[str, Any]) -> MarketReconciliationSpec:
    raw_resolutions = document.get("resolution_ids")
    if not isinstance(raw_resolutions, list) or not all(
        isinstance(item, str) for item in raw_resolutions
    ):
        raise ValueError("resolution_ids must be a list of UUID strings")
    raw_policy = document.get("reconstruction_policy", DEFAULT_RECONSTRUCTION_POLICY)
    if not isinstance(raw_policy, str) or not raw_policy.strip():
        raise ValueError("reconstruction_policy must be non-empty text")
    return MarketReconciliationSpec(
        primary_dataset_publication_id=uuid.UUID(
            _text(document, "primary_dataset_publication_id")
        ),
        resolution_ids=tuple(uuid.UUID(item) for item in raw_resolutions),
        cleaning_version_id=uuid.UUID(_text(document, "cleaning_version_id")),
        calendar_version_id=uuid.UUID(_text(document, "calendar_version_id")),
        output_dataset_key=_text(document, "output_dataset_key"),
        output_version_number=_integer(document, "output_version_number"),
        created_by=_text(document, "created_by"),
        reconstruction_policy=cast(ReconstructionPolicy, raw_policy),
    )


def _read_object(path: Path) -> dict[str, Any]:
    return _object(json.loads(path.read_text(encoding="utf-8")))


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Reconciliation specification values must be JSON objects")
    return cast(dict[str, Any], value)


def _text(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be non-empty text")
    return value


def _integer(document: dict[str, Any], key: str) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_uuid(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Optional identity values must be UUID strings")
    return uuid.UUID(value)


def run() -> None:
    try:
        raise SystemExit(main())
    except (FileNotFoundError, LookupError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
