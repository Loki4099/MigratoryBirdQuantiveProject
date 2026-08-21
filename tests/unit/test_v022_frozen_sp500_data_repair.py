from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import date
from types import SimpleNamespace

import pytest

from style_rotation.cli.v022_frozen_sp500_data_repair import (
    build_parser,
    closure_report,
    prepared_repair,
    repair_spec,
)
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
    build_gate_assessment_spec,
    validate_declared_exclusions,
    validate_post_repair_closure,
    validate_post_repair_closure_pair,
    validate_prepared_repair,
)
from style_rotation.v022.market_data_closure import (
    ClosureAuditIssue,
    MarketDataClosureAuditReport,
)
from style_rotation.v022.market_reconciliation import (
    V2_RECONSTRUCTION_POLICY,
    MarketGapResolutionPublication,
    MarketReconciliationPublication,
)


def test_exclusion_candidates_must_be_explicitly_declared() -> None:
    security_id = uuid.uuid4()
    report = _report(
        dataset_id=PRIMARY_V3_DATASET_PUBLICATION_ID,
        passed=False,
        exclude_candidates=(
            _issue(
                "exclude_candidate",
                "selectable_zero_volume",
                security_id,
            ),
        ),
    )
    policy = _policy(
        (
            FrozenExclusionDecision(
                security_id,
                "reviewed_free_source_unusable",
                ("selectable_zero_volume",),
                "Frozen review confirmed the full Security history is unusable.",
            ),
        )
    )

    decisions = validate_declared_exclusions(policy, report)

    assert decisions[0].security_id == security_id
    with pytest.raises(ValueError, match="undeclared exclusion candidates"):
        validate_declared_exclusions(
            _policy(
                (
                    FrozenExclusionDecision(
                        uuid.uuid4(),
                        "different_security",
                        ("selectable_zero_volume",),
                        "This is an explicit but unrelated review decision.",
                    ),
                )
            ),
            report,
        )


def test_separately_reviewed_repair_satisfies_pre_repair_candidate() -> None:
    repaired_security = uuid.uuid4()
    policy_security = uuid.uuid4()
    policy = _policy(
        (
            FrozenExclusionDecision(
                policy_security,
                "reviewed_free_source_unusable",
                ("selectable_zero_volume",),
                "Separate reviewed exclusion.",
            ),
        )
    )
    report = _report(
        dataset_id=PRIMARY_V3_DATASET_PUBLICATION_ID,
        passed=False,
        exclude_candidates=(
            _issue("exclude_candidate", "held_path_missing_bar", repaired_security),
        ),
    )

    decisions = validate_declared_exclusions(
        policy,
        report,
        reviewed_security_ids=frozenset({repaired_security}),
    )

    assert decisions[0].security_id == policy_security


def test_prepare_publishes_full_range_resolutions_and_v2_reconciliation() -> None:
    security_id = uuid.uuid4()
    spec = _spec(
        _policy(
            (
                FrozenExclusionDecision(
                    security_id,
                    "provider_history_unusable",
                    ("provider_uniformly_unavailable",),
                    "Provider evidence was reviewed; preserve identity but exclude prices.",
                ),
            )
        )
    )
    service = object.__new__(FrozenSp500DataRepairService)
    artifacts = _Artifacts()
    resolutions = _Resolutions()
    reconciliation = _Reconciliation()
    service._artifacts = artifacts
    service._resolutions = resolutions
    service._reconciliation = reconciliation
    service._gate = _Gate()
    service.inspect_inputs = lambda item: (  # type: ignore[method-assign]
        _prior_carry(item.prior_gate_assessment_id),
        (),
    )

    prepared = service.prepare(
        spec,
        _report(dataset_id=PRIMARY_V3_DATASET_PUBLICATION_ID, passed=True),
    )

    resolution = resolutions.specs[0]
    assert resolution.primary_dataset_publication_id == PRIMARY_V3_DATASET_PUBLICATION_ID
    assert resolution.gap_start == date(2004, 12, 31)
    assert resolution.gap_end == date(2026, 6, 30)
    assert resolution.resolution_kind == "exclude_security"
    assert resolution.details["preserve_security_identity"] is True
    plan = reconciliation.specs[0]
    assert plan.output_version_number == REPAIRED_V5_DATASET_VERSION
    assert plan.reconstruction_policy == V2_RECONSTRUCTION_POLICY
    assert prepared.exclusion_security_ids == (security_id,)
    assert prepared.prior_gate_carry.prior_gate_assessment_id == spec.prior_gate_assessment_id
    assert artifacts.documents[0]["phase"] == "pre_repair"


def test_gate_is_fail_closed_but_preserves_review_findings_as_warnings() -> None:
    security_id = uuid.uuid4()
    spec = _spec(
        _policy(
            (
                FrozenExclusionDecision(
                    security_id,
                    "provider_history_unusable",
                    ("provider_uniformly_unavailable",),
                    "Reviewed exclusion with immutable Security preservation.",
                ),
            )
        )
    )
    prepared = _prepared(spec, security_id)
    repaired_id = prepared.reconciliation_publication.dataset_publication_id
    review = _issue("review", "adjusted_return_over_50_percent", uuid.uuid4())
    clean = _report(
        dataset_id=repaired_id,
        passed=True,
        review_findings=(review,),
    )
    monthly_clean = _report(dataset_id=repaired_id, passed=True)

    validate_post_repair_closure(
        report=clean,
        repaired_dataset_publication_id=repaired_id,
        coverage_start=date(2004, 12, 31),
        coverage_end=date(2026, 6, 30),
    )
    gate = build_gate_assessment_spec(
        spec,
        prepared=prepared,
        weekly_post_review_artifact_id=uuid.uuid4(),
        monthly_post_review_artifact_id=uuid.uuid4(),
        weekly_post_repair_report=clean,
        monthly_post_repair_report=monthly_clean,
    )

    assert gate.version_number == REPAIRED_V4_GATE_VERSION
    assert gate.ranking_eligibility == "rankable_research"
    assert gate.product_eligibility == "eligible_with_warnings"
    assert {item.finding_code for item in gate.findings}.issuperset(
        {
            "historical_membership_retrospective",
            "retrospective_price_snapshot",
            "closure_review_weekly_adjusted_return_over_50_percent",
            "dual_frequency_closure_verified",
        }
    )
    policy_exclusion = next(
        item for item in gate.uniform_exclusions if item.security_id == security_id
    )
    assert policy_exclusion.details["preserve_security_identity"] is True

    blocked = _report(
        dataset_id=repaired_id,
        passed=False,
        exclude_candidates=(
            _issue("exclude_candidate", "selectable_zero_volume", security_id),
        ),
    )
    with pytest.raises(ValueError, match="Cohort publication is blocked"):
        validate_post_repair_closure(
            report=blocked,
            repaired_dataset_publication_id=repaired_id,
            coverage_start=date(2004, 12, 31),
            coverage_end=date(2026, 6, 30),
        )


def test_dual_frequency_closure_requires_distinct_clean_cohort_identities() -> None:
    repaired_id = uuid.uuid4()
    repeated_cohort_id = uuid.uuid4()
    weekly = _report(
        dataset_id=repaired_id,
        cohort_id=repeated_cohort_id,
        passed=True,
    )
    monthly = _report(
        dataset_id=repaired_id,
        cohort_id=repeated_cohort_id,
        passed=True,
    )

    with pytest.raises(ValueError, match="distinct Cohorts"):
        validate_post_repair_closure_pair(
            weekly_report=weekly,
            monthly_report=monthly,
            repaired_dataset_publication_id=repaired_id,
            coverage_start=date(2004, 12, 31),
            coverage_end=date(2026, 6, 30),
        )
    with pytest.raises(ValueError, match="lacks a Cohort identity"):
        validate_post_repair_closure_pair(
            weekly_report=_report(
                dataset_id=repaired_id,
                cohort_id="",
                passed=True,
            ),
            monthly_report=_report(dataset_id=repaired_id, passed=True),
            repaired_dataset_publication_id=repaired_id,
            coverage_start=date(2004, 12, 31),
            coverage_end=date(2026, 6, 30),
        )
    with pytest.raises(ValueError, match="Cohort publication is blocked"):
        validate_post_repair_closure_pair(
            weekly_report=_report(dataset_id=repaired_id, passed=True),
            monthly_report=_report(dataset_id=repaired_id, passed=False),
            repaired_dataset_publication_id=repaired_id,
            coverage_start=date(2004, 12, 31),
            coverage_end=date(2026, 6, 30),
        )


def test_post_repair_closures_require_exact_weekly_monthly_frozen_pair() -> None:
    security_id = uuid.uuid4()
    spec = _spec(
        _policy(
            (
                FrozenExclusionDecision(
                    security_id,
                    "provider_history_unusable",
                    ("provider_uniformly_unavailable",),
                    "Reviewed full-range exclusion.",
                ),
            )
        )
    )
    prepared = _prepared(spec, security_id)
    repaired_id = prepared.reconciliation_publication.dataset_publication_id
    weekly_cohort_id = uuid.uuid4()
    monthly_cohort_id = uuid.uuid4()
    rows = (
        _cohort_row(weekly_cohort_id, "weekly"),
        _cohort_row(monthly_cohort_id, "monthly"),
    )
    service = object.__new__(FrozenSp500DataRepairService)
    service._engine = _CohortEngine(rows)

    weekly, monthly = service.inspect_post_repair_closures(
        spec,
        prepared,
        _report(
            dataset_id=repaired_id,
            cohort_id=weekly_cohort_id,
            passed=True,
        ),
        _report(
            dataset_id=repaired_id,
            cohort_id=monthly_cohort_id,
            passed=True,
        ),
    )

    assert (weekly.frequency, monthly.frequency) == ("weekly", "monthly")
    mismatched = dict(rows[1])
    mismatched["evaluation_start"] = date(2008, 1, 2)
    service._engine = _CohortEngine((rows[0], mismatched))
    with pytest.raises(ValueError, match="do not share one frozen environment"):
        service.inspect_post_repair_closures(
            spec,
            prepared,
            weekly.report,
            monthly.report,
        )
    wrong_frequency = dict(rows[1])
    wrong_frequency["frequency"] = "weekly"
    service._engine = _CohortEngine((rows[0], wrong_frequency))
    with pytest.raises(ValueError, match="does not name a monthly Cohort"):
        service.inspect_post_repair_closures(
            spec,
            prepared,
            weekly.report,
            monthly.report,
        )


def test_publish_gate_persists_distinct_weekly_monthly_closure_evidence() -> None:
    security_id = uuid.uuid4()
    spec = _spec(
        _policy(
            (
                FrozenExclusionDecision(
                    security_id,
                    "provider_history_unusable",
                    ("provider_uniformly_unavailable",),
                    "Reviewed full-range exclusion.",
                ),
            )
        )
    )
    prepared = _prepared(spec, security_id)
    repaired_id = prepared.reconciliation_publication.dataset_publication_id
    weekly_cohort_id = uuid.uuid4()
    monthly_cohort_id = uuid.uuid4()
    artifacts = _Artifacts()
    service = object.__new__(FrozenSp500DataRepairService)
    service._engine = _CohortEngine(
        (
            _cohort_row(weekly_cohort_id, "weekly"),
            _cohort_row(monthly_cohort_id, "monthly"),
        )
    )
    service._artifacts = artifacts
    service._gate = _Gate()
    service.inspect_inputs = lambda item: (  # type: ignore[method-assign]
        prepared.prior_gate_carry,
        prepared.additional_reviewed_resolutions,
    )
    weekly = _report(
        dataset_id=repaired_id,
        cohort_id=weekly_cohort_id,
        passed=True,
    )
    monthly = _report(
        dataset_id=repaired_id,
        cohort_id=monthly_cohort_id,
        passed=True,
    )

    with pytest.raises(ValueError, match="Cohort publication is blocked"):
        service.publish_gate(
            spec,
            prepared,
            weekly,
            _report(
                dataset_id=repaired_id,
                cohort_id=monthly_cohort_id,
                passed=False,
            ),
        )
    assert artifacts.calls == []

    publication = service.publish_gate(spec, prepared, weekly, monthly)

    assert publication.weekly_post_repair_review_artifact_id != (
        publication.monthly_post_repair_review_artifact_id
    )
    assert [item["frequency"] for item in artifacts.documents] == [
        "weekly",
        "monthly",
    ]
    assert {call["artifact_key"] for call in artifacts.calls} == {
        "v022_market_closure_review__sp500_repaired_v5_weekly",
        "v022_market_closure_review__sp500_repaired_v5_monthly",
    }
    assert all(
        "evaluation_cohort"
        in {dependency.role for dependency in call["dependencies"]}
        for call in artifacts.calls
    )
    evidence_ids = {item.artifact_id for item in service._gate.specs[0].evidence}
    assert publication.weekly_post_repair_review_artifact_id in evidence_ids
    assert publication.monthly_post_repair_review_artifact_id in evidence_ids


def test_gate_cli_requires_weekly_and_monthly_report_paths() -> None:
    parsed = build_parser().parse_args(
        ["validate-gate", "spec.json", "prepared.json", "week.json", "month.json"]
    )

    assert parsed.weekly_post_repair_report.name == "week.json"
    assert parsed.monthly_post_repair_report.name == "month.json"


def test_prior_gate_exclusions_are_carried_without_becoming_new_resolutions() -> None:
    policy_security = uuid.uuid4()
    carried_security = uuid.uuid4()
    carried_resolution_id = uuid.uuid4()
    carried_resolution_artifact = uuid.uuid4()
    policy = _policy(
        (
            FrozenExclusionDecision(
                policy_security,
                "provider_history_unusable",
                ("provider_uniformly_unavailable",),
                "Reviewed full-range exclusion.",
            ),
        )
    )
    spec = _spec(policy, additional=(carried_resolution_id,))
    carry = _prior_carry(
        spec.prior_gate_assessment_id,
        security_id=carried_security,
        evidence_artifact_id=carried_resolution_artifact,
        evidence_role="gap_resolution",
        reason_code="reviewed_execution_day_market_gap",
        finding_code="reviewed_execution_day_market_gap",
    )
    additional = FrozenReviewedResolution(
        carried_resolution_id,
        carried_resolution_artifact,
        carried_security,
        "exclude_security",
    )
    prepared = _prepared_with(
        spec,
        carry=carry,
        reconciled_exclusions=(policy_security, carried_security),
        excluded_count=2,
        additional=(additional,),
    )

    validate_prepared_repair(spec, prepared)
    gate = build_gate_assessment_spec(
        spec,
        prepared=prepared,
        weekly_post_review_artifact_id=uuid.uuid4(),
        monthly_post_review_artifact_id=uuid.uuid4(),
        weekly_post_repair_report=_report(
            dataset_id=prepared.reconciliation_publication.dataset_publication_id,
            passed=True,
        ),
        monthly_post_repair_report=_report(
            dataset_id=prepared.reconciliation_publication.dataset_publication_id,
            passed=True,
        ),
    )

    assert {item.security_id for item in gate.uniform_exclusions} == {
        policy_security,
        carried_security,
    }
    assert gate.findings[3].finding_code == (
        "prior_uniform_exclusions_carried_forward"
    )
    assert gate.findings[3].details["uniform_exclusion_count"] == 1
    assert carried_resolution_artifact not in {
        item.artifact_id for item in gate.evidence
    }
    assert len(prepared.resolution_publications) == 1


def test_additional_replacement_does_not_increment_excluded_security_count() -> None:
    policy_security = uuid.uuid4()
    replacement_id = uuid.uuid4()
    policy = _policy(
        (
            FrozenExclusionDecision(
                policy_security,
                "provider_history_unusable",
                ("provider_uniformly_unavailable",),
                "Reviewed full-range exclusion.",
            ),
        )
    )
    spec = _spec(policy, additional=(replacement_id,))
    replacement = FrozenReviewedResolution(
        replacement_id,
        uuid.uuid4(),
        uuid.uuid4(),
        "replace_with_alternate",
    )
    prepared = _prepared_with(
        spec,
        carry=_prior_carry(spec.prior_gate_assessment_id),
        reconciled_exclusions=(policy_security,),
        excluded_count=1,
        additional=(replacement,),
    )

    validate_prepared_repair(spec, prepared)


def test_policy_and_additional_resolution_security_conflict_fails_closed() -> None:
    security_id = uuid.uuid4()
    resolution_id = uuid.uuid4()
    policy = _policy(
        (
            FrozenExclusionDecision(
                security_id,
                "provider_history_unusable",
                ("provider_uniformly_unavailable",),
                "Reviewed full-range exclusion.",
            ),
        )
    )
    spec = _spec(policy, additional=(resolution_id,))
    prepared = _prepared_with(
        spec,
        carry=_prior_carry(spec.prior_gate_assessment_id),
        reconciled_exclusions=(security_id,),
        excluded_count=1,
        additional=(
            FrozenReviewedResolution(
                resolution_id,
                uuid.uuid4(),
                security_id,
                "replace_with_alternate",
            ),
        ),
    )

    with pytest.raises(ValueError, match="both a new full-range exclusion"):
        validate_prepared_repair(spec, prepared)


def test_repair_cli_parses_frozen_policy_and_closure_report() -> None:
    security_id = uuid.uuid4()
    primary_artifact = uuid.uuid4()
    lifecycle_artifact = uuid.uuid4()
    document = {
        "primary_dataset_artifact_id": str(primary_artifact),
        "cleaning_version_id": str(uuid.uuid4()),
        "calendar_version_id": str(uuid.uuid4()),
        "universe_membership_ledger_id": str(uuid.uuid4()),
        "prior_gate_assessment_id": str(uuid.uuid4()),
        "lifecycle_evidence_artifact_ids": [str(lifecycle_artifact)],
        "created_by": "data-reviewer",
        "exclusion_policy": {
            "policy_key": "sp500_primary_v3_full_range_review",
            "version_number": 1,
            "primary_dataset_publication_id": str(
                PRIMARY_V3_DATASET_PUBLICATION_ID
            ),
            "coverage_start": "2004-12-31",
            "coverage_end": "2026-06-30",
            "approved_by": "data-reviewer",
            "decisions": [
                {
                    "security_id": str(security_id),
                    "reason_code": "provider_history_unusable",
                    "basis_rule_codes": ["provider_uniformly_unavailable"],
                    "reviewer_note": "Explicit full-range review decision.",
                }
            ],
        },
    }
    report_document = _report(
        dataset_id=PRIMARY_V3_DATASET_PUBLICATION_ID,
        passed=True,
    ).to_dict()

    parsed_spec = repair_spec(document)
    parsed_report = closure_report(report_document)

    assert parsed_spec.primary_dataset_artifact_id == primary_artifact
    assert parsed_spec.exclusion_policy.decisions[0].security_id == security_id
    assert parsed_spec.lifecycle_evidence_artifact_ids == (lifecycle_artifact,)
    assert parsed_spec.prior_gate_assessment_id == uuid.UUID(
        document["prior_gate_assessment_id"]
    )
    assert parsed_report.dataset_publication_id == str(
        PRIMARY_V3_DATASET_PUBLICATION_ID
    )


def test_prepared_repair_cli_round_trips_carried_gate_and_resolution_kinds() -> None:
    policy_security = uuid.uuid4()
    additional_id = uuid.uuid4()
    spec = _spec(
        _policy(
            (
                FrozenExclusionDecision(
                    policy_security,
                    "provider_history_unusable",
                    ("provider_uniformly_unavailable",),
                    "Reviewed full-range exclusion.",
                ),
            )
        ),
        additional=(additional_id,),
    )
    additional = FrozenReviewedResolution(
        additional_id,
        uuid.uuid4(),
        uuid.uuid4(),
        "replace_with_alternate",
    )
    source = _prepared_with(
        spec,
        carry=_prior_carry(spec.prior_gate_assessment_id),
        reconciled_exclusions=(policy_security,),
        excluded_count=1,
        additional=(additional,),
    )

    parsed = prepared_repair(json.loads(json.dumps(asdict(source), default=str)))

    assert parsed == source


def _policy(
    decisions: tuple[FrozenExclusionDecision, ...],
) -> FrozenExclusionPolicy:
    return FrozenExclusionPolicy(
        "sp500_primary_v3_full_range_review",
        1,
        PRIMARY_V3_DATASET_PUBLICATION_ID,
        date(2004, 12, 31),
        date(2026, 6, 30),
        decisions,
        "data-reviewer",
    )


def _spec(
    policy: FrozenExclusionPolicy,
    *,
    additional: tuple[uuid.UUID, ...] = (),
) -> FrozenSp500DataRepairSpec:
    return FrozenSp500DataRepairSpec(
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        (uuid.uuid4(),),
        policy,
        "data-reviewer",
        additional,
    )


def _report(
    *,
    dataset_id: uuid.UUID,
    passed: bool,
    cohort_id: uuid.UUID | str | None = None,
    session_count: int = 5_412,
    blockers: tuple[ClosureAuditIssue, ...] = (),
    exclude_candidates: tuple[ClosureAuditIssue, ...] = (),
    review_findings: tuple[ClosureAuditIssue, ...] = (),
) -> MarketDataClosureAuditReport:
    return MarketDataClosureAuditReport(
        "v0.22.market_data_closure_audit.v1",
        str(dataset_id),
        str(uuid.uuid4() if cohort_id is None else cohort_id),
        None,
        "2004-12-31",
        "2026-06-30",
        686,
        session_count,
        3_000_000,
        passed,
        blockers,
        exclude_candidates,
        review_findings,
        (),
    )


def _issue(
    disposition: str, rule_code: str, security_id: uuid.UUID
) -> ClosureAuditIssue:
    return ClosureAuditIssue(
        disposition,  # type: ignore[arg-type]
        rule_code,
        "reviewed test issue",
        str(security_id),
        {"count": 1},
    )


def _prepared(
    spec: FrozenSp500DataRepairSpec, security_id: uuid.UUID
) -> FrozenSp500PreparedRepair:
    resolution = MarketGapResolutionPublication(
        uuid.uuid4(), uuid.uuid4(), "a" * 64, False
    )
    reconciliation = MarketReconciliationPublication(
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        "b" * 64,
        "c" * 64,
        0,
        1,
        False,
    )
    return FrozenSp500PreparedRepair(
        spec.exclusion_policy.fingerprint,
        _prior_carry(spec.prior_gate_assessment_id),
        uuid.uuid4(),
        (resolution,),
        reconciliation,
        (security_id,),
    )


def _prior_carry(
    assessment_id: uuid.UUID | None = None,
    *,
    security_id: uuid.UUID | None = None,
    evidence_artifact_id: uuid.UUID | None = None,
    evidence_role: str = "supporting_evidence",
    reason_code: str = "frozen_free_source_provider_unavailable",
    finding_code: str = "provider_uniformly_unavailable",
) -> FrozenPriorGateCarry:
    security = security_id or uuid.uuid4()
    evidence = evidence_artifact_id or uuid.uuid4()
    return FrozenPriorGateCarry(
        assessment_id or uuid.uuid4(),
        uuid.uuid4(),
        "a" * 64,
        (DatasetGateEvidenceRef(evidence, evidence_role),),  # type: ignore[arg-type]
        (
            DatasetGateFinding(
                finding_code,
                "uniform_exclusion",
                "warning",
                "none",
                "warning",
                security_id=security,
                evidence_artifact_id=evidence,
                details={"reason": "provider unavailable"},
            ),
        ),
        (
            DatasetGateUniformExclusion(
                security,
                date(2004, 12, 31),
                date(2026, 6, 30),
                reason_code,
                evidence,
                {"reason": "provider unavailable"},
            ),
        ),
    )


def _prepared_with(
    spec: FrozenSp500DataRepairSpec,
    *,
    carry: FrozenPriorGateCarry,
    reconciled_exclusions: tuple[uuid.UUID, ...],
    excluded_count: int,
    additional: tuple[FrozenReviewedResolution, ...],
) -> FrozenSp500PreparedRepair:
    resolution = MarketGapResolutionPublication(
        uuid.uuid4(), uuid.uuid4(), "a" * 64, False
    )
    reconciliation = MarketReconciliationPublication(
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        "b" * 64,
        "c" * 64,
        0,
        excluded_count,
        False,
    )
    return FrozenSp500PreparedRepair(
        spec.exclusion_policy.fingerprint,
        carry,
        uuid.uuid4(),
        (resolution,),
        reconciliation,
        reconciled_exclusions,
        additional,
    )


def _cohort_row(
    cohort_id: uuid.UUID, frequency: str
) -> dict[str, object]:
    return {
        "evaluation_cohort_version_id": cohort_id,
        "artifact_id": uuid.uuid4(),
        "cohort_key": f"frozen_{frequency}",
        "version_number": 10,
        "research_tier": "rankable_research",
        "frequency": frequency,
        "universe_history_id": uuid.UUID(int=101),
        "dataset_publication_id": PRIMARY_V3_DATASET_PUBLICATION_ID,
        "benchmark_dataset_publication_id": uuid.UUID(int=102),
        "security_market_quality_report_id": uuid.UUID(int=103),
        "calendar_version_id": uuid.UUID(int=104),
        "warmup_start": date(2004, 12, 31),
        "evaluation_start": date(2007, 1, 3),
        "evaluation_end": date(2026, 6, 30),
        "required_history_sessions": 504,
        "cost_bps_per_side": 5,
        "execution_delay_sessions": 1,
        "benchmark_key": "spy",
        "price_semantics": "total_return_adjusted",
        "historical_pit_claimed": False,
        "session_count": 5_412,
        "artifact_status": "published",
    }


class _Rows:
    def __init__(self, rows: tuple[dict[str, object], ...]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, object]]:
        return list(self._rows)


class _CohortConnection:
    def __init__(self, rows: tuple[dict[str, object], ...]) -> None:
        self._rows = rows

    def __enter__(self) -> _CohortConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, *args: object, **kwargs: object) -> _Rows:
        return _Rows(self._rows)


class _CohortEngine:
    def __init__(self, rows: tuple[dict[str, object], ...]) -> None:
        self._rows = rows

    def connect(self) -> _CohortConnection:
        return _CohortConnection(self._rows)


class _Artifacts:
    def __init__(self) -> None:
        self.documents: list[dict[str, object]] = []
        self.calls: list[dict[str, object]] = []

    def publish(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        self.documents.append(kwargs["semantic_payload"])  # type: ignore[arg-type]
        return SimpleNamespace(artifact_id=uuid.uuid4())


class _Resolutions:
    def __init__(self) -> None:
        self.specs: list[object] = []

    def publish(self, spec: object) -> MarketGapResolutionPublication:
        self.specs.append(spec)
        return MarketGapResolutionPublication(
            uuid.uuid4(), uuid.uuid4(), "d" * 64, False
        )


class _Reconciliation:
    def __init__(self) -> None:
        self.specs: list[object] = []

    def reconcile(self, spec: object) -> MarketReconciliationPublication:
        self.specs.append(spec)
        return MarketReconciliationPublication(
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "e" * 64,
            "f" * 64,
            0,
            1,
            False,
        )


class _Gate:
    def __init__(self) -> None:
        self.specs: list[object] = []

    def publish(self, spec: object) -> SimpleNamespace:
        self.specs.append(spec)
        return SimpleNamespace(dataset_gate_assessment_id=uuid.uuid4())
