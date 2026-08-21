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
    DatasetGateEvidenceRef,
    DatasetGateFinding,
    DatasetGateUniformExclusion,
)
from style_rotation.v022.frozen_sp500_data_repair import (
    PRIMARY_V3_DATASET_PUBLICATION_ID,
    REPAIRED_V4_GATE_VERSION,
    REPAIRED_V5_DATASET_VERSION,
    FrozenExclusionDecision,
    FrozenExclusionPolicy,
    FrozenPriorGateCarry,
    FrozenReviewedResolution,
    FrozenSp500DataRepairService,
    FrozenSp500DataRepairSpec,
    FrozenSp500PreparedRepair,
    validate_declared_exclusions,
    validate_post_repair_closure_pair,
    validate_prepared_repair,
)
from style_rotation.v022.market_data_closure import (
    ClosureAuditIssue,
    ClosureAuditPass,
    MarketDataClosureAuditReport,
)
from style_rotation.v022.market_reconciliation import (
    MarketGapResolutionPublication,
    MarketReconciliationPublication,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Review and publish the exact frozen S&P 500 v3-to-v5 repair"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-preparation", "prepare"):
        command = commands.add_parser(name)
        command.add_argument("spec", type=Path)
        command.add_argument("pre_repair_report", type=Path)
    for name in ("validate-gate", "publish-gate"):
        command = commands.add_parser(name)
        command.add_argument("spec", type=Path)
        command.add_argument("prepared_repair", type=Path)
        command.add_argument("weekly_post_repair_report", type=Path)
        command.add_argument("monthly_post_repair_report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    spec = repair_spec(_read_object(args.spec))
    if args.command in {"validate-preparation", "prepare"}:
        report = closure_report(_read_object(args.pre_repair_report))
        if args.command == "validate-preparation":
            engine = create_postgres_engine(get_settings().database_url)
            prior_gate, reviewed_resolutions = FrozenSp500DataRepairService(
                engine
            ).inspect_inputs(spec)
            decisions = validate_declared_exclusions(
                spec.exclusion_policy,
                report,
                reviewed_security_ids=frozenset(
                    {item.security_id for item in prior_gate.uniform_exclusions}
                    | {item.security_id for item in reviewed_resolutions}
                ),
            )
            print(
                json.dumps(
                    {
                        "primary_dataset_publication_id": str(
                            PRIMARY_V3_DATASET_PUBLICATION_ID
                        ),
                        "output_dataset_version": REPAIRED_V5_DATASET_VERSION,
                        "gate_version": REPAIRED_V4_GATE_VERSION,
                        "policy_fingerprint": spec.exclusion_policy.fingerprint,
                        "exclusion_security_ids": [
                            str(item.security_id) for item in decisions
                        ],
                        "prior_gate_assessment_id": str(
                            prior_gate.prior_gate_assessment_id
                        ),
                        "carried_uniform_exclusion_count": len(
                            prior_gate.uniform_exclusions
                        ),
                        "additional_reviewed_resolution_count": len(
                            reviewed_resolutions
                        ),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        engine = create_postgres_engine(get_settings().database_url)
        prepared = FrozenSp500DataRepairService(engine).prepare(spec, report)
        print(json.dumps(asdict(prepared), default=str, sort_keys=True))
        return 0

    prepared = prepared_repair(_read_object(args.prepared_repair))
    weekly_report = closure_report(
        _read_object(args.weekly_post_repair_report)
    )
    monthly_report = closure_report(
        _read_object(args.monthly_post_repair_report)
    )
    validate_prepared_repair(spec, prepared)
    validate_post_repair_closure_pair(
        weekly_report=weekly_report,
        monthly_report=monthly_report,
        repaired_dataset_publication_id=(
            prepared.reconciliation_publication.dataset_publication_id
        ),
        coverage_start=spec.exclusion_policy.coverage_start,
        coverage_end=spec.exclusion_policy.coverage_end,
    )
    if args.command == "validate-gate":
        engine = create_postgres_engine(get_settings().database_url)
        service = FrozenSp500DataRepairService(engine)
        current_prior, current_additional = service.inspect_inputs(spec)
        if current_prior != prepared.prior_gate_carry:
            raise ValueError("Prepared repair prior Gate projection has drifted")
        if current_additional != prepared.additional_reviewed_resolutions:
            raise ValueError("Prepared repair reviewed Resolution projection has drifted")
        weekly_closure, monthly_closure = service.inspect_post_repair_closures(
            spec,
            prepared,
            weekly_report,
            monthly_report,
        )
        print(
            json.dumps(
                {
                    "dataset_publication_id": weekly_report.dataset_publication_id,
                    "weekly_closure_passed": weekly_report.passed,
                    "monthly_closure_passed": monthly_report.passed,
                    "weekly_evaluation_cohort_version_id": str(
                        weekly_closure.evaluation_cohort_version_id
                    ),
                    "monthly_evaluation_cohort_version_id": str(
                        monthly_closure.evaluation_cohort_version_id
                    ),
                    "weekly_review_finding_count": len(
                        weekly_report.review_findings
                    ),
                    "monthly_review_finding_count": len(
                        monthly_report.review_findings
                    ),
                    "gate_version": REPAIRED_V4_GATE_VERSION,
                    "cohort_publication_allowed_after_gate": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    engine = create_postgres_engine(get_settings().database_url)
    publication = FrozenSp500DataRepairService(engine).publish_gate(
        spec,
        prepared,
        weekly_report,
        monthly_report,
    )
    print(json.dumps(asdict(publication), default=str, sort_keys=True))
    return 0


def repair_spec(document: dict[str, Any]) -> FrozenSp500DataRepairSpec:
    lifecycle = _uuid_list(document, "lifecycle_evidence_artifact_ids")
    return FrozenSp500DataRepairSpec(
        primary_dataset_artifact_id=uuid.UUID(
            _text(document, "primary_dataset_artifact_id")
        ),
        cleaning_version_id=uuid.UUID(_text(document, "cleaning_version_id")),
        calendar_version_id=uuid.UUID(_text(document, "calendar_version_id")),
        universe_membership_ledger_id=uuid.UUID(
            _text(document, "universe_membership_ledger_id")
        ),
        prior_gate_assessment_id=uuid.UUID(
            _text(document, "prior_gate_assessment_id")
        ),
        lifecycle_evidence_artifact_ids=lifecycle,
        exclusion_policy=exclusion_policy(
            _object(document.get("exclusion_policy"))
        ),
        created_by=_text(document, "created_by"),
        additional_reviewed_resolution_ids=_optional_uuid_list(
            document, "additional_reviewed_resolution_ids"
        ),
    )


def exclusion_policy(document: dict[str, Any]) -> FrozenExclusionPolicy:
    raw_decisions = document.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("exclusion_policy.decisions must be a list")
    decisions = tuple(
        FrozenExclusionDecision(
            uuid.UUID(_text(_object(item), "security_id")),
            _text(_object(item), "reason_code"),
            tuple(_text_list(_object(item), "basis_rule_codes")),
            _text(_object(item), "reviewer_note"),
        )
        for item in raw_decisions
    )
    return FrozenExclusionPolicy(
        policy_key=_text(document, "policy_key"),
        version_number=_integer(document, "version_number"),
        primary_dataset_publication_id=uuid.UUID(
            _text(document, "primary_dataset_publication_id")
        ),
        coverage_start=date.fromisoformat(_text(document, "coverage_start")),
        coverage_end=date.fromisoformat(_text(document, "coverage_end")),
        decisions=decisions,
        approved_by=_text(document, "approved_by"),
    )


def closure_report(document: dict[str, Any]) -> MarketDataClosureAuditReport:
    return MarketDataClosureAuditReport(
        schema_version=_text(document, "schema_version"),
        dataset_publication_id=_text(document, "dataset_publication_id"),
        evaluation_cohort_version_id=_text(
            document, "evaluation_cohort_version_id"
        ),
        evaluation_cohort_runtime_contract_id=_optional_text(
            document.get("evaluation_cohort_runtime_contract_id")
        ),
        coverage_start=_text(document, "coverage_start"),
        coverage_end=_text(document, "coverage_end"),
        security_count=_integer(document, "security_count"),
        session_count=_integer(document, "session_count"),
        bar_count=_integer(document, "bar_count"),
        passed=_boolean(document, "passed"),
        blockers=_issues(document, "blockers"),
        exclude_candidates=_issues(document, "exclude_candidates"),
        review_findings=_issues(document, "review_findings"),
        passes=_passes(document),
    )


def prepared_repair(document: dict[str, Any]) -> FrozenSp500PreparedRepair:
    resolution_rows = document.get("resolution_publications")
    if not isinstance(resolution_rows, list):
        raise ValueError("resolution_publications must be a list")
    resolutions = tuple(
        MarketGapResolutionPublication(
            uuid.UUID(_text(_object(item), "market_gap_resolution_id")),
            uuid.UUID(_text(_object(item), "artifact_id")),
            _text(_object(item), "resolution_fingerprint"),
            _boolean(_object(item), "reused"),
        )
        for item in resolution_rows
    )
    reconciliation = _object(document.get("reconciliation_publication"))
    prior = _object(document.get("prior_gate_carry"))
    additional_rows = document.get("additional_reviewed_resolutions", [])
    if not isinstance(additional_rows, list):
        raise ValueError("additional_reviewed_resolutions must be a list")
    return FrozenSp500PreparedRepair(
        policy_fingerprint=_text(document, "policy_fingerprint"),
        prior_gate_carry=FrozenPriorGateCarry(
            prior_gate_assessment_id=uuid.UUID(
                _text(prior, "prior_gate_assessment_id")
            ),
            prior_gate_artifact_id=uuid.UUID(
                _text(prior, "prior_gate_artifact_id")
            ),
            assessment_fingerprint=_text(prior, "assessment_fingerprint"),
            evidence=_gate_evidence(prior),
            findings=_gate_findings(prior),
            uniform_exclusions=_gate_exclusions(prior),
        ),
        pre_repair_review_artifact_id=uuid.UUID(
            _text(document, "pre_repair_review_artifact_id")
        ),
        resolution_publications=resolutions,
        reconciliation_publication=MarketReconciliationPublication(
            uuid.UUID(_text(reconciliation, "market_reconciliation_plan_id")),
            uuid.UUID(_text(reconciliation, "plan_artifact_id")),
            uuid.UUID(_text(reconciliation, "dataset_publication_id")),
            uuid.UUID(_text(reconciliation, "dataset_artifact_id")),
            _text(reconciliation, "plan_fingerprint"),
            _text(reconciliation, "binding_fingerprint"),
            _integer(reconciliation, "replaced_bar_count"),
            _integer(reconciliation, "excluded_security_count"),
            _boolean(reconciliation, "reused"),
        ),
        exclusion_security_ids=_uuid_list(document, "exclusion_security_ids"),
        additional_reviewed_resolutions=tuple(
            FrozenReviewedResolution(
                uuid.UUID(_text(_object(item), "market_gap_resolution_id")),
                uuid.UUID(_text(_object(item), "artifact_id")),
                uuid.UUID(_text(_object(item), "security_id")),
                cast(Any, _text(_object(item), "resolution_kind")),
            )
            for item in additional_rows
        ),
    )


def _gate_evidence(document: dict[str, Any]) -> tuple[DatasetGateEvidenceRef, ...]:
    rows = document.get("evidence")
    if not isinstance(rows, list):
        raise ValueError("prior_gate_carry.evidence must be a list")
    return tuple(
        DatasetGateEvidenceRef(
            uuid.UUID(_text(_object(item), "artifact_id")),
            cast(Any, _text(_object(item), "role")),
        )
        for item in rows
    )


def _gate_findings(document: dict[str, Any]) -> tuple[DatasetGateFinding, ...]:
    rows = document.get("findings")
    if not isinstance(rows, list):
        raise ValueError("prior_gate_carry.findings must be a list")
    return tuple(
        DatasetGateFinding(
            _text(_object(item), "finding_code"),
            cast(Any, _text(_object(item), "finding_category")),
            cast(Any, _text(_object(item), "severity")),
            cast(Any, _text(_object(item), "ranking_effect")),
            cast(Any, _text(_object(item), "product_effect")),
            security_id=uuid.UUID(_text(_object(item), "security_id")),
            evidence_artifact_id=uuid.UUID(
                _text(_object(item), "evidence_artifact_id")
            ),
            details=_object(_object(item).get("details", {})),
        )
        for item in rows
    )


def _gate_exclusions(
    document: dict[str, Any]
) -> tuple[DatasetGateUniformExclusion, ...]:
    rows = document.get("uniform_exclusions")
    if not isinstance(rows, list):
        raise ValueError("prior_gate_carry.uniform_exclusions must be a list")
    return tuple(
        DatasetGateUniformExclusion(
            uuid.UUID(_text(_object(item), "security_id")),
            date.fromisoformat(_text(_object(item), "exclusion_start")),
            date.fromisoformat(_text(_object(item), "exclusion_end")),
            _text(_object(item), "reason_code"),
            uuid.UUID(_text(_object(item), "evidence_artifact_id")),
            _object(_object(item).get("details", {})),
        )
        for item in rows
    )


def _issues(document: dict[str, Any], key: str) -> tuple[ClosureAuditIssue, ...]:
    value = document.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return tuple(
        ClosureAuditIssue(
            cast(Any, _text(_object(item), "disposition")),
            _text(_object(item), "rule_code"),
            _text(_object(item), "message"),
            _optional_text(_object(item).get("security_id")),
            _object(_object(item).get("details", {})),
        )
        for item in value
    )


def _passes(document: dict[str, Any]) -> tuple[ClosureAuditPass, ...]:
    value = document.get("passes", [])
    if not isinstance(value, list):
        raise ValueError("passes must be a list")
    return tuple(
        ClosureAuditPass(
            _text(_object(item), "rule_code"),
            _text(_object(item), "message"),
            _object(_object(item).get("details", {})),
        )
        for item in value
    )


def _read_object(path: Path) -> dict[str, Any]:
    return _object(json.loads(path.read_text(encoding="utf-8")))


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Repair specification values must be JSON objects")
    return cast(dict[str, Any], value)


def _text(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be non-empty text")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Optional text must be non-empty when supplied")
    return value.strip()


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


def _text_list(document: dict[str, Any], key: str) -> list[str]:
    value = document.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{key} must be a list of non-empty strings")
    return [cast(str, item).strip() for item in value]


def _uuid_list(document: dict[str, Any], key: str) -> tuple[uuid.UUID, ...]:
    return tuple(uuid.UUID(item) for item in _text_list(document, key))


def _optional_uuid_list(
    document: dict[str, Any], key: str
) -> tuple[uuid.UUID, ...]:
    if key not in document:
        return ()
    return _uuid_list(document, key)


def run() -> None:
    try:
        raise SystemExit(main())
    except (FileNotFoundError, LookupError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
