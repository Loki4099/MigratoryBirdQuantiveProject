from __future__ import annotations

import uuid
from datetime import date

import pytest

from style_rotation.v022.dataset_gate import (
    DatasetGateAssessmentSpec,
    DatasetGateEvidenceRef,
    DatasetGateFinding,
    DatasetGateUniformExclusion,
    _validate_gate_decisions,
)


def test_free_data_warnings_allow_rankable_research_product() -> None:
    evidence = (DatasetGateEvidenceRef(uuid.uuid4(), "supporting_evidence"),)
    spec = _spec(evidence=evidence)

    _validate_gate_decisions(spec, evidence)

    assert spec.ranking_eligibility == "rankable_research"
    assert spec.product_eligibility == "eligible_with_warnings"


def test_correctness_blocker_is_independent_from_ranking_decision() -> None:
    evidence = (DatasetGateEvidenceRef(uuid.uuid4(), "supporting_evidence"),)
    findings = _baseline_findings() + (
        DatasetGateFinding(
            "settlement_path_unresolved",
            "settlement",
            "blocker",
            "none",
            "ineligible",
            evidence_artifact_id=evidence[0].artifact_id,
        ),
    )
    spec = _spec(
        evidence=evidence,
        findings=findings,
        product_eligibility="ineligible",
    )

    _validate_gate_decisions(spec, evidence)

    assert spec.ranking_eligibility == "rankable_research"
    assert spec.product_eligibility == "ineligible"


def test_exploratory_ranking_can_still_allow_warning_product() -> None:
    evidence = (DatasetGateEvidenceRef(uuid.uuid4(), "supporting_evidence"),)
    findings = _baseline_findings() + (
        DatasetGateFinding(
            "membership_effective_date_estimated",
            "membership",
            "warning",
            "exploratory_only",
            "warning",
            evidence_artifact_id=evidence[0].artifact_id,
        ),
    )
    spec = _spec(
        evidence=evidence,
        findings=findings,
        ranking_eligibility="exploratory_only",
    )

    _validate_gate_decisions(spec, evidence)


def test_uniform_exclusion_requires_same_security_warning_and_evidence() -> None:
    evidence = (DatasetGateEvidenceRef(uuid.uuid4(), "supporting_evidence"),)
    security_id = uuid.uuid4()
    exclusion = DatasetGateUniformExclusion(
        security_id,
        date(2007, 1, 3),
        date(2026, 6, 30),
        "provider_unavailable_uniform_exclusion",
        evidence[0].artifact_id,
    )
    spec = _spec(evidence=evidence, exclusions=(exclusion,))

    with pytest.raises(ValueError, match="matching non-ranking warning"):
        _validate_gate_decisions(spec, evidence)


def test_declared_eligibility_must_match_finding_effects() -> None:
    evidence = (DatasetGateEvidenceRef(uuid.uuid4(), "supporting_evidence"),)
    spec = _spec(evidence=evidence, product_eligibility="eligible")

    with pytest.raises(ValueError, match="product_eligibility"):
        _validate_gate_decisions(spec, evidence)


def _spec(
    *,
    evidence: tuple[DatasetGateEvidenceRef, ...],
    findings: tuple[DatasetGateFinding, ...] | None = None,
    exclusions: tuple[DatasetGateUniformExclusion, ...] = (),
    ranking_eligibility: str = "rankable_research",
    product_eligibility: str = "eligible_with_warnings",
) -> DatasetGateAssessmentSpec:
    return DatasetGateAssessmentSpec(
        uuid.uuid4(),
        uuid.uuid4(),
        "sp500_free_data_gate_v1",
        1,
        date(2007, 1, 3),
        date(2026, 6, 30),
        ranking_eligibility,  # type: ignore[arg-type]
        product_eligibility,  # type: ignore[arg-type]
        evidence,
        findings if findings is not None else _baseline_findings(),
        exclusions,
        "reviewer",
    )


def _baseline_findings() -> tuple[DatasetGateFinding, ...]:
    return (
        DatasetGateFinding(
            "historical_membership_retrospective",
            "data_provenance",
            "warning",
            "none",
            "warning",
        ),
        DatasetGateFinding(
            "retrospective_price_snapshot",
            "data_provenance",
            "warning",
            "none",
            "warning",
        ),
    )
