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
from style_rotation.v022.dataset_gate import (
    DatasetGateAssessmentService,
    DatasetGateAssessmentSpec,
    DatasetGateEvidenceRef,
    DatasetGateFinding,
    DatasetGateUniformExclusion,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or publish an immutable v0.22 Dataset Gate Assessment"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "publish"):
        command = commands.add_parser(name)
        command.add_argument("spec", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    spec = assessment_spec(_read_object(args.spec))
    if args.command == "validate":
        print(json.dumps(asdict(spec), default=str, indent=2, sort_keys=True))
        return 0
    engine = create_postgres_engine(get_settings().database_url)
    publication = DatasetGateAssessmentService(engine).publish(spec)
    print(json.dumps(asdict(publication), default=str, sort_keys=True))
    return 0


def assessment_spec(document: dict[str, Any]) -> DatasetGateAssessmentSpec:
    evidence_value = document.get("evidence")
    findings_value = document.get("findings")
    exclusions_value = document.get("uniform_exclusions", [])
    if not isinstance(evidence_value, list):
        raise ValueError("evidence must be a list")
    if not isinstance(findings_value, list):
        raise ValueError("findings must be a list")
    if not isinstance(exclusions_value, list):
        raise ValueError("uniform_exclusions must be a list")
    evidence = tuple(
        DatasetGateEvidenceRef(
            uuid.UUID(_text(_object(item), "artifact_id")),
            cast(Any, _text(_object(item), "role")),
        )
        for item in evidence_value
    )
    findings = tuple(_finding(_object(item)) for item in findings_value)
    exclusions = tuple(_exclusion(_object(item)) for item in exclusions_value)
    return DatasetGateAssessmentSpec(
        dataset_publication_id=uuid.UUID(_text(document, "dataset_publication_id")),
        universe_membership_ledger_id=uuid.UUID(
            _text(document, "universe_membership_ledger_id")
        ),
        gate_key=_text(document, "gate_key"),
        version_number=_integer(document, "version_number"),
        assessed_coverage_start=date.fromisoformat(
            _text(document, "assessed_coverage_start")
        ),
        assessed_coverage_end=date.fromisoformat(
            _text(document, "assessed_coverage_end")
        ),
        ranking_eligibility=cast(Any, _text(document, "ranking_eligibility")),
        product_eligibility=cast(Any, _text(document, "product_eligibility")),
        evidence=evidence,
        findings=findings,
        uniform_exclusions=exclusions,
        created_by=_text(document, "created_by"),
    )


def _finding(document: dict[str, Any]) -> DatasetGateFinding:
    return DatasetGateFinding(
        finding_code=_text(document, "finding_code"),
        finding_category=cast(Any, _text(document, "finding_category")),
        severity=cast(Any, _text(document, "severity")),
        ranking_effect=cast(Any, _text(document, "ranking_effect")),
        product_effect=cast(Any, _text(document, "product_effect")),
        security_id=_optional_uuid(document.get("security_id")),
        evidence_artifact_id=_optional_uuid(document.get("evidence_artifact_id")),
        details=_object(document.get("details", {})),
    )


def _exclusion(document: dict[str, Any]) -> DatasetGateUniformExclusion:
    return DatasetGateUniformExclusion(
        security_id=uuid.UUID(_text(document, "security_id")),
        exclusion_start=date.fromisoformat(_text(document, "exclusion_start")),
        exclusion_end=date.fromisoformat(_text(document, "exclusion_end")),
        reason_code=_text(document, "reason_code"),
        evidence_artifact_id=uuid.UUID(_text(document, "evidence_artifact_id")),
        details=_object(document.get("details", {})),
    )


def _read_object(path: Path) -> dict[str, Any]:
    return _object(json.loads(path.read_text(encoding="utf-8")))


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Dataset Gate specification values must be JSON objects")
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
