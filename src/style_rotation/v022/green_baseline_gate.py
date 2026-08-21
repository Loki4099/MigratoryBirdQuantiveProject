from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, cast

from sqlalchemy import Engine, text

from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.dataset_gate import (
    DatasetGateAssessmentPublication,
    DatasetGateAssessmentService,
    DatasetGateAssessmentSpec,
    DatasetGateEvidenceRef,
    DatasetGateFinding,
    DatasetGateUniformExclusion,
)
from style_rotation.v022.market_data_closure import (
    MarketDataClosureAuditor,
    MarketDataClosureAuditReport,
)

_CLOSURE_CONTRACT = "migratory_bird_v022_green_closure_review_v1"
_UNAVAILABLE_RULE = "security_uniformly_excluded_provider_unavailable"
_LARGE_MOVE_RULE = "adjusted_return_over_50_percent_reviewed_not_excluded"


@dataclass(frozen=True, slots=True)
class GreenBaselineGateSpec:
    dataset_publication_id: uuid.UUID
    quality_report_id: uuid.UUID
    universe_history_id: uuid.UUID
    weekly_cohort_id: uuid.UUID
    monthly_cohort_id: uuid.UUID
    gate_key: str = "sp500_free_research_v1"
    version_number: int = 5
    created_by: str = "codex-green-baseline-gate5"


@dataclass(frozen=True, slots=True)
class GreenBaselineGatePublication:
    weekly_closure_review_artifact_id: str
    monthly_closure_review_artifact_id: str
    assessment: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _Inputs:
    dataset_artifact_id: uuid.UUID
    ledger_id: uuid.UUID
    quality_artifact_id: uuid.UUID
    quality_document: dict[str, Any]
    weekly_cohort_artifact_id: uuid.UUID
    monthly_cohort_artifact_id: uuid.UUID
    lifecycle_artifact_ids: tuple[uuid.UUID, ...]


class GreenBaselineGateService:
    """Publish Gate 5 only after both clean-green Cohort clocks pass closure."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)
        self._gate = DatasetGateAssessmentService(engine)
        self._auditor = MarketDataClosureAuditor(engine)

    def publish(self, spec: GreenBaselineGateSpec) -> GreenBaselineGatePublication:
        inputs = self._inputs(spec)
        weekly = self._auditor.audit(
            dataset_publication_id=spec.dataset_publication_id,
            evaluation_cohort_version_id=spec.weekly_cohort_id,
        )
        monthly = self._auditor.audit(
            dataset_publication_id=spec.dataset_publication_id,
            evaluation_cohort_version_id=spec.monthly_cohort_id,
        )
        _validate_closure_pair(spec, weekly, monthly)
        weekly_review = self._publish_review(
            frequency="weekly",
            version=spec.version_number,
            dataset_artifact_id=inputs.dataset_artifact_id,
            cohort_artifact_id=inputs.weekly_cohort_artifact_id,
            quality_artifact_id=inputs.quality_artifact_id,
            report=weekly,
        )
        monthly_review = self._publish_review(
            frequency="monthly",
            version=spec.version_number,
            dataset_artifact_id=inputs.dataset_artifact_id,
            cohort_artifact_id=inputs.monthly_cohort_artifact_id,
            quality_artifact_id=inputs.quality_artifact_id,
            report=monthly,
        )
        if weekly_review == monthly_review:
            raise ValueError("Weekly and monthly closure review identities collided")
        evidence = (
            DatasetGateEvidenceRef(weekly_review, "supporting_evidence"),
            DatasetGateEvidenceRef(monthly_review, "supporting_evidence"),
        ) + tuple(
            DatasetGateEvidenceRef(item, "lifecycle_event")
            for item in inputs.lifecycle_artifact_ids
        )
        findings, exclusions = _quality_projection(
            inputs.quality_document,
            evidence_artifact_id=weekly_review,
            monthly_evidence_artifact_id=monthly_review,
        )
        assessment = self._gate.publish(
            DatasetGateAssessmentSpec(
                dataset_publication_id=spec.dataset_publication_id,
                universe_membership_ledger_id=inputs.ledger_id,
                gate_key=spec.gate_key,
                version_number=spec.version_number,
                assessed_coverage_start=date(2004, 12, 31),
                assessed_coverage_end=date(2026, 6, 30),
                ranking_eligibility="rankable_research",
                product_eligibility="eligible_with_warnings",
                evidence=evidence,
                findings=findings,
                uniform_exclusions=exclusions,
                created_by=spec.created_by,
            )
        )
        return GreenBaselineGatePublication(
            str(weekly_review),
            str(monthly_review),
            _assessment_document(assessment),
        )

    def _inputs(self, spec: GreenBaselineGateSpec) -> _Inputs:
        with self._engine.connect() as connection:
            dataset = connection.execute(
                text(
                    """
                    SELECT publication.artifact_id,artifact.status
                      FROM data.dataset_publication publication
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=publication.artifact_id
                     WHERE publication.dataset_publication_id=:dataset
                       AND publication.dataset_kind='canonical'
                       AND publication.value_kind='daily_bar'
                    """
                ),
                {"dataset": spec.dataset_publication_id},
            ).mappings().one_or_none()
            quality = connection.execute(
                text(
                    """
                    SELECT report.artifact_id,report.report_document,report.error_count,
                           artifact.status
                      FROM data.v022_security_market_quality_report report
                      JOIN lineage.artifact artifact ON artifact.artifact_id=report.artifact_id
                     WHERE report.security_market_quality_report_id=:report
                       AND report.source_dataset_publication_id=:dataset
                    """
                ),
                {"report": spec.quality_report_id, "dataset": spec.dataset_publication_id},
            ).mappings().one_or_none()
            ledger = connection.execute(
                text(
                    """
                    SELECT binding.universe_membership_ledger_id,ledger_artifact.status
                      FROM catalog.v022_universe_history_ledger_binding binding
                      JOIN catalog.v022_universe_membership_ledger ledger
                        ON ledger.universe_membership_ledger_id=
                           binding.universe_membership_ledger_id
                      JOIN lineage.artifact ledger_artifact
                        ON ledger_artifact.artifact_id=ledger.artifact_id
                     WHERE binding.universe_history_id=:history
                    """
                ),
                {"history": spec.universe_history_id},
            ).mappings().one_or_none()
            cohorts = connection.execute(
                text(
                    """
                    SELECT cohort.evaluation_cohort_version_id,cohort.artifact_id,
                           cohort.frequency,cohort.dataset_publication_id,
                           cohort.security_market_quality_report_id,artifact.status
                      FROM experiment.v022_evaluation_cohort_version cohort
                      JOIN lineage.artifact artifact ON artifact.artifact_id=cohort.artifact_id
                     WHERE cohort.evaluation_cohort_version_id=ANY(:ids)
                    """
                ),
                {"ids": [spec.weekly_cohort_id, spec.monthly_cohort_id]},
            ).mappings().all()
            lifecycle = connection.execute(
                text(
                    """
                    SELECT event.artifact_id
                      FROM catalog.v022_security_lifecycle_event event
                      JOIN lineage.artifact artifact ON artifact.artifact_id=event.artifact_id
                     WHERE event.event_status='confirmed' AND artifact.status='published'
                     ORDER BY event.security_id,event.effective_session,event.event_key
                    """
                )
            ).scalars().all()
        if dataset is None or dataset["status"] != "published":
            raise LookupError("Published clean-green risk Dataset not found")
        if quality is None or quality["status"] != "published" or quality["error_count"] != 0:
            raise ValueError("Clean-green quality report is absent, unpublished or blocked")
        if ledger is None or ledger["status"] != "published":
            raise LookupError("Published clean-green Universe Ledger not found")
        by_frequency = {row["frequency"]: row for row in cohorts}
        if set(by_frequency) != {"weekly", "monthly"} or any(
            row["status"] != "published"
            or row["dataset_publication_id"] != spec.dataset_publication_id
            or row["security_market_quality_report_id"] != spec.quality_report_id
            for row in cohorts
        ):
            raise ValueError("Gate 5 requires exact published weekly/monthly Cohort 11 inputs")
        if (
            by_frequency["weekly"]["evaluation_cohort_version_id"] != spec.weekly_cohort_id
            or by_frequency["monthly"]["evaluation_cohort_version_id"] != spec.monthly_cohort_id
        ):
            raise ValueError("Gate 5 Cohort frequencies are reversed")
        if not lifecycle:
            raise ValueError("Gate 5 requires published confirmed lifecycle evidence")
        return _Inputs(
            cast(uuid.UUID, dataset["artifact_id"]),
            cast(uuid.UUID, ledger["universe_membership_ledger_id"]),
            cast(uuid.UUID, quality["artifact_id"]),
            cast(dict[str, Any], quality["report_document"]),
            cast(uuid.UUID, by_frequency["weekly"]["artifact_id"]),
            cast(uuid.UUID, by_frequency["monthly"]["artifact_id"]),
            tuple(cast(list[uuid.UUID], lifecycle)),
        )

    def _publish_review(
        self,
        *,
        frequency: str,
        version: int,
        dataset_artifact_id: uuid.UUID,
        cohort_artifact_id: uuid.UUID,
        quality_artifact_id: uuid.UUID,
        report: MarketDataClosureAuditReport,
    ) -> uuid.UUID:
        document = {
            "contract_version": _CLOSURE_CONTRACT,
            "frequency": frequency,
            "dataset_artifact_id": str(dataset_artifact_id),
            "evaluation_cohort_artifact_id": str(cohort_artifact_id),
            "quality_report_artifact_id": str(quality_artifact_id),
            "closure_report": report.to_dict(),
        }
        publication = self._artifacts.publish(
            artifact_type="v022_market_closure_review",
            artifact_key=f"v022_market_closure_review__green_v5_{frequency}",
            version_number=version,
            semantic_payload=document,
            content_payload=document,
            dependencies=(
                DependencyInput(dataset_artifact_id, "market_dataset", 0),
                DependencyInput(cohort_artifact_id, "evaluation_cohort", 1),
                DependencyInput(quality_artifact_id, "quality_report", 2),
            ),
            reason=f"publish clean-green {frequency} closure review",
        )
        return publication.artifact_id


def _validate_closure_pair(
    spec: GreenBaselineGateSpec,
    weekly: MarketDataClosureAuditReport,
    monthly: MarketDataClosureAuditReport,
) -> None:
    for frequency, cohort_id, report in (
        ("weekly", spec.weekly_cohort_id, weekly),
        ("monthly", spec.monthly_cohort_id, monthly),
    ):
        if (
            not report.passed
            or report.blockers
            or report.exclude_candidates
            or report.dataset_publication_id != str(spec.dataset_publication_id)
            or report.evaluation_cohort_version_id != str(cohort_id)
            or report.coverage_start != "2004-12-31"
            or report.coverage_end != "2026-06-30"
        ):
            raise ValueError(f"Clean-green {frequency} closure is not exact and clean")


def _quality_projection(
    document: dict[str, Any],
    *,
    evidence_artifact_id: uuid.UUID,
    monthly_evidence_artifact_id: uuid.UUID,
) -> tuple[tuple[DatasetGateFinding, ...], tuple[DatasetGateUniformExclusion, ...]]:
    issues = document.get("issues")
    if not isinstance(issues, list):
        raise ValueError("Quality report issues are malformed")
    findings: list[DatasetGateFinding] = [
        DatasetGateFinding(
            "historical_membership_retrospective",
            "membership",
            "warning",
            "none",
            "warning",
            evidence_artifact_id=evidence_artifact_id,
            details={"historical_pit_claimed": False},
        ),
        DatasetGateFinding(
            "retrospective_price_snapshot",
            "data_provenance",
            "warning",
            "none",
            "warning",
            evidence_artifact_id=monthly_evidence_artifact_id,
            details={"source_class": "free_retrospective_market_data"},
        ),
        DatasetGateFinding(
            "dual_frequency_closure_verified",
            "market_coverage",
            "notice",
            "none",
            "none",
            evidence_artifact_id=evidence_artifact_id,
        ),
    ]
    exclusions: list[DatasetGateUniformExclusion] = []
    for raw in issues:
        if not isinstance(raw, dict):
            raise ValueError("Quality report issue is malformed")
        rule = raw.get("rule_code")
        subject = raw.get("subject_key")
        raw_details = raw.get("details")
        details: dict[str, Any] = (
            dict(raw_details) if isinstance(raw_details, dict) else {}
        )
        if rule == _UNAVAILABLE_RULE:
            security_id = uuid.UUID(str(subject))
            findings.append(
                DatasetGateFinding(
                    "green_baseline_uniform_exclusion",
                    "uniform_exclusion",
                    "warning",
                    "none",
                    "warning",
                    security_id=security_id,
                    evidence_artifact_id=evidence_artifact_id,
                    details=details,
                )
            )
            exclusions.append(
                DatasetGateUniformExclusion(
                    security_id,
                    date(2004, 12, 31),
                    date(2026, 6, 30),
                    str(details.get("policy_reason_code", "free_source_unavailable")),
                    evidence_artifact_id,
                    {**details, "preserve_security_identity": True},
                )
            )
        elif rule == _LARGE_MOVE_RULE:
            findings.append(
                DatasetGateFinding(
                    "adjusted_return_over_50_percent_reviewed",
                    "market_coverage",
                    "warning",
                    "none",
                    "warning",
                    security_id=uuid.UUID(str(subject)),
                    evidence_artifact_id=monthly_evidence_artifact_id,
                    details=details,
                )
            )
    expected_exclusions = int(document.get("uniformly_excluded_security_count", -1))
    expected_moves = int(document.get("large_move_security_count", -1))
    observed_moves = sum(
        item.finding_code == "adjusted_return_over_50_percent_reviewed"
        for item in findings
    )
    if len(exclusions) != expected_exclusions or observed_moves != expected_moves:
        raise ValueError("Gate projection differs from the frozen quality report")
    return tuple(findings), tuple(exclusions)


def _assessment_document(publication: DatasetGateAssessmentPublication) -> dict[str, Any]:
    return {
        "dataset_gate_assessment_id": str(publication.dataset_gate_assessment_id),
        "artifact_id": str(publication.artifact_id),
        "assessment_fingerprint": publication.assessment_fingerprint,
        "ranking_eligibility": publication.ranking_eligibility,
        "product_eligibility": publication.product_eligibility,
        "warning_count": publication.warning_count,
        "blocker_count": publication.blocker_count,
        "uniform_exclusion_count": publication.uniform_exclusion_count,
        "reused": publication.reused,
    }
