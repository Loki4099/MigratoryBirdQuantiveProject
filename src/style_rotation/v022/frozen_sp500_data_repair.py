from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.dataset_gate import (
    DatasetGateAssessmentPublication,
    DatasetGateAssessmentService,
    DatasetGateAssessmentSpec,
    DatasetGateEvidenceRef,
    DatasetGateFinding,
    DatasetGateUniformExclusion,
    GateEvidenceRole,
)
from style_rotation.v022.market_data_closure import MarketDataClosureAuditReport
from style_rotation.v022.market_reconciliation import (
    V2_RECONSTRUCTION_POLICY,
    GapResolutionEvidenceRef,
    MarketGapResolutionPublication,
    MarketGapResolutionService,
    MarketGapResolutionSpec,
    MarketReconciliationPublication,
    MarketReconciliationService,
    MarketReconciliationSpec,
    ResolutionKind,
)

PRIMARY_V3_DATASET_PUBLICATION_ID = uuid.UUID(
    "22753730-d9cd-539d-bb04-b9ce72da6e93"
)
REPAIRED_V5_DATASET_KEY = "us_sp500_historical_daily_free_research_v1"
REPAIRED_V5_DATASET_VERSION = 5
REPAIRED_V4_GATE_KEY = "sp500_free_research_v1"
REPAIRED_V4_GATE_VERSION = 4
PRIOR_V3_GATE_VERSION = 3
PRIOR_V3_GATE_UNIFORM_EXCLUSION_COUNT = 288

_PRE_REVIEW_ARTIFACT_KEY = "v022_market_closure_review__sp500_primary_v3"
_POST_REVIEW_ARTIFACT_KEYS = {
    "weekly": "v022_market_closure_review__sp500_repaired_v5_weekly",
    "monthly": "v022_market_closure_review__sp500_repaired_v5_monthly",
}
_REVIEW_CONTRACT = "v0.22.frozen_sp500_closure_review.v1"
_POLICY_CONTRACT = "v0.22.frozen_sp500_exclusion_policy.v1"


@dataclass(frozen=True, slots=True)
class FrozenExclusionDecision:
    security_id: uuid.UUID
    reason_code: str
    basis_rule_codes: tuple[str, ...]
    reviewer_note: str

    def __post_init__(self) -> None:
        if not self.reason_code.strip():
            raise ValueError("Exclusion reason_code is blank")
        if not self.basis_rule_codes or any(
            not item.strip() for item in self.basis_rule_codes
        ):
            raise ValueError("Exclusion decision requires explicit basis rule codes")
        if len(set(self.basis_rule_codes)) != len(self.basis_rule_codes):
            raise ValueError("Exclusion basis rule codes must be unique")
        if not self.reviewer_note.strip():
            raise ValueError("Exclusion decision requires a reviewer note")


@dataclass(frozen=True, slots=True)
class FrozenExclusionPolicy:
    policy_key: str
    version_number: int
    primary_dataset_publication_id: uuid.UUID
    coverage_start: date
    coverage_end: date
    decisions: tuple[FrozenExclusionDecision, ...]
    approved_by: str

    def __post_init__(self) -> None:
        if not self.policy_key.strip() or not self.approved_by.strip():
            raise ValueError("Frozen exclusion policy identity and approver are required")
        if self.version_number < 1:
            raise ValueError("Frozen exclusion policy version must be positive")
        if self.primary_dataset_publication_id != PRIMARY_V3_DATASET_PUBLICATION_ID:
            raise ValueError("Frozen exclusion policy must bind the exact primary v3 Dataset")
        if self.coverage_start > self.coverage_end:
            raise ValueError("Frozen exclusion policy coverage is reversed")
        if not self.decisions:
            raise ValueError("Frozen exclusion policy contains no explicit decisions")
        security_ids = {item.security_id for item in self.decisions}
        if len(security_ids) != len(self.decisions):
            raise ValueError("Frozen exclusion policy repeats a Security identity")

    @property
    def document(self) -> dict[str, object]:
        return {
            "contract_version": _POLICY_CONTRACT,
            "policy_key": self.policy_key,
            "version_number": self.version_number,
            "primary_dataset_publication_id": str(self.primary_dataset_publication_id),
            "coverage_start": self.coverage_start.isoformat(),
            "coverage_end": self.coverage_end.isoformat(),
            "approved_by": self.approved_by,
            "decisions": [
                {
                    "ordinal": ordinal,
                    "security_id": str(item.security_id),
                    "disposition": "exclude_full_range",
                    "reason_code": item.reason_code,
                    "basis_rule_codes": list(item.basis_rule_codes),
                    "reviewer_note": item.reviewer_note,
                    "preserve_security_identity": True,
                }
                for ordinal, item in enumerate(self.decisions)
            ],
        }

    @property
    def fingerprint(self) -> str:
        return sha256_hexdigest(self.document)


@dataclass(frozen=True, slots=True)
class FrozenSp500DataRepairSpec:
    primary_dataset_artifact_id: uuid.UUID
    cleaning_version_id: uuid.UUID
    calendar_version_id: uuid.UUID
    universe_membership_ledger_id: uuid.UUID
    prior_gate_assessment_id: uuid.UUID
    lifecycle_evidence_artifact_ids: tuple[uuid.UUID, ...]
    exclusion_policy: FrozenExclusionPolicy
    created_by: str
    additional_reviewed_resolution_ids: tuple[uuid.UUID, ...] = ()

    def __post_init__(self) -> None:
        if not self.created_by.strip():
            raise ValueError("Frozen S&P 500 repair creator is blank")
        if not self.lifecycle_evidence_artifact_ids:
            raise ValueError("Repair Gate requires exact lifecycle evidence Artifacts")
        if len(set(self.lifecycle_evidence_artifact_ids)) != len(
            self.lifecycle_evidence_artifact_ids
        ):
            raise ValueError("Lifecycle evidence Artifacts must be unique")
        if len(set(self.additional_reviewed_resolution_ids)) != len(
            self.additional_reviewed_resolution_ids
        ):
            raise ValueError("Additional reviewed Resolution identities must be unique")


@dataclass(frozen=True, slots=True)
class FrozenReviewedResolution:
    market_gap_resolution_id: uuid.UUID
    artifact_id: uuid.UUID
    security_id: uuid.UUID
    resolution_kind: ResolutionKind


@dataclass(frozen=True, slots=True)
class FrozenPriorGateCarry:
    prior_gate_assessment_id: uuid.UUID
    prior_gate_artifact_id: uuid.UUID
    assessment_fingerprint: str
    evidence: tuple[DatasetGateEvidenceRef, ...]
    findings: tuple[DatasetGateFinding, ...]
    uniform_exclusions: tuple[DatasetGateUniformExclusion, ...]

    def __post_init__(self) -> None:
        if not self.assessment_fingerprint.strip():
            raise ValueError("Prior Gate fingerprint is blank")
        exclusion_ids = {item.security_id for item in self.uniform_exclusions}
        if len(exclusion_ids) != len(self.uniform_exclusions):
            raise ValueError("Prior Gate carry repeats a Security exclusion")
        finding_ids = {item.security_id for item in self.findings}
        if (
            len(self.findings) != len(self.uniform_exclusions)
            or None in finding_ids
            or finding_ids != exclusion_ids
        ):
            raise ValueError("Prior Gate carry requires one Security finding per exclusion")
        evidence_ids = {item.artifact_id for item in self.evidence}
        if any(
            item.evidence_artifact_id not in evidence_ids
            for item in self.findings + self.uniform_exclusions
        ):
            raise ValueError("Prior Gate carry references evidence outside its projection")


@dataclass(frozen=True, slots=True)
class FrozenSp500PreparedRepair:
    policy_fingerprint: str
    prior_gate_carry: FrozenPriorGateCarry
    pre_repair_review_artifact_id: uuid.UUID
    resolution_publications: tuple[MarketGapResolutionPublication, ...]
    reconciliation_publication: MarketReconciliationPublication
    exclusion_security_ids: tuple[uuid.UUID, ...]
    additional_reviewed_resolutions: tuple[FrozenReviewedResolution, ...] = ()


ClosureFrequency = Literal["weekly", "monthly"]


@dataclass(frozen=True, slots=True)
class FrozenPostRepairClosure:
    frequency: ClosureFrequency
    evaluation_cohort_version_id: uuid.UUID
    cohort_artifact_id: uuid.UUID
    report: MarketDataClosureAuditReport

    def __post_init__(self) -> None:
        if self.report.evaluation_cohort_version_id != str(
            self.evaluation_cohort_version_id
        ):
            raise ValueError("Post-repair closure Cohort identity is inconsistent")


@dataclass(frozen=True, slots=True)
class FrozenSp500GatePublication:
    weekly_post_repair_review_artifact_id: uuid.UUID
    monthly_post_repair_review_artifact_id: uuid.UUID
    assessment: DatasetGateAssessmentPublication


def validate_declared_exclusions(
    policy: FrozenExclusionPolicy,
    report: MarketDataClosureAuditReport,
    *,
    reviewed_security_ids: frozenset[uuid.UUID] = frozenset(),
) -> tuple[FrozenExclusionDecision, ...]:
    """Validate explicit exclusions while honoring separately reviewed repairs."""
    if report.dataset_publication_id != str(PRIMARY_V3_DATASET_PUBLICATION_ID):
        raise ValueError("Closure review does not describe the exact primary v3 Dataset")
    if (
        report.coverage_start != policy.coverage_start.isoformat()
        or report.coverage_end != policy.coverage_end.isoformat()
    ):
        raise ValueError("Closure review coverage does not match the frozen policy")
    by_security = {item.security_id: item for item in policy.decisions}
    undeclared: dict[str, set[str]] = {}
    for item in report.exclude_candidates:
        if item.security_id is None:
            raise ValueError("Closure exclude-candidate lacks a stable Security identity")
        security_id = uuid.UUID(item.security_id)
        if security_id in reviewed_security_ids:
            continue
        decision = by_security.get(security_id)
        if decision is None:
            undeclared.setdefault(item.security_id, set()).add(item.rule_code)
            continue
        if item.rule_code not in decision.basis_rule_codes:
            raise ValueError(
                "Closure candidate rule is absent from its explicit exclusion decision"
            )
    if undeclared:
        raise ValueError(
            "Closure review contains undeclared exclusion candidates: "
            + ",".join(sorted(undeclared))
        )
    return tuple(sorted(policy.decisions, key=lambda item: str(item.security_id)))


def validate_post_repair_closure(
    *,
    report: MarketDataClosureAuditReport,
    repaired_dataset_publication_id: uuid.UUID,
    coverage_start: date,
    coverage_end: date,
) -> None:
    if report.dataset_publication_id != str(repaired_dataset_publication_id):
        raise ValueError("Post-repair closure audit describes another Dataset")
    if (
        report.coverage_start != coverage_start.isoformat()
        or report.coverage_end != coverage_end.isoformat()
    ):
        raise ValueError("Post-repair closure audit coverage is not exact")
    if not report.passed or report.blockers or report.exclude_candidates:
        raise ValueError(
            "Cohort publication is blocked until post-repair closure has no issues"
        )


def validate_post_repair_closure_pair(
    *,
    weekly_report: MarketDataClosureAuditReport,
    monthly_report: MarketDataClosureAuditReport,
    repaired_dataset_publication_id: uuid.UUID,
    coverage_start: date,
    coverage_end: date,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Validate the two report envelopes before consulting Cohort provenance."""
    weekly_cohort_id = _closure_cohort_id(weekly_report, "weekly")
    monthly_cohort_id = _closure_cohort_id(monthly_report, "monthly")
    if weekly_cohort_id == monthly_cohort_id:
        raise ValueError("Weekly and monthly closure reports must name distinct Cohorts")
    for report in (weekly_report, monthly_report):
        validate_post_repair_closure(
            report=report,
            repaired_dataset_publication_id=repaired_dataset_publication_id,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
    return weekly_cohort_id, monthly_cohort_id


def _closure_cohort_id(
    report: MarketDataClosureAuditReport, frequency: ClosureFrequency
) -> uuid.UUID:
    value = report.evaluation_cohort_version_id.strip()
    if not value:
        raise ValueError(f"Post-repair {frequency} closure report lacks a Cohort identity")
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise ValueError(
            f"Post-repair {frequency} closure report has an invalid Cohort identity"
        ) from error


class FrozenSp500DataRepairService:
    """Orchestrate reviewed v3 exclusions, immutable v5 repair and v4 Gate."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)
        self._resolutions = MarketGapResolutionService(engine)
        self._reconciliation = MarketReconciliationService(engine)
        self._gate = DatasetGateAssessmentService(engine)

    def prepare(
        self,
        spec: FrozenSp500DataRepairSpec,
        pre_repair_report: MarketDataClosureAuditReport,
    ) -> FrozenSp500PreparedRepair:
        prior_gate, additional_resolutions = self.inspect_inputs(spec)
        reviewed_security_ids = frozenset(
            {item.security_id for item in prior_gate.uniform_exclusions}
            | {item.security_id for item in additional_resolutions}
        )
        decisions = validate_declared_exclusions(
            spec.exclusion_policy,
            pre_repair_report,
            reviewed_security_ids=reviewed_security_ids,
        )
        _validate_resolution_security_conflicts(decisions, additional_resolutions)
        review_artifact_id = self._publish_review(
            artifact_key=_PRE_REVIEW_ARTIFACT_KEY,
            version_number=spec.exclusion_policy.version_number,
            dataset_publication_id=PRIMARY_V3_DATASET_PUBLICATION_ID,
            dataset_artifact_id=spec.primary_dataset_artifact_id,
            policy=spec.exclusion_policy,
            report=pre_repair_report,
            phase="pre_repair",
            prior_review_artifact_id=None,
            frequency=None,
            cohort_artifact_id=None,
        )
        resolutions = tuple(
            self._resolutions.publish(
                _resolution_spec(
                    spec,
                    decision=decision,
                    review_artifact_id=review_artifact_id,
                )
            )
            for decision in decisions
        )
        reconciliation = self._reconciliation.reconcile(
            MarketReconciliationSpec(
                primary_dataset_publication_id=PRIMARY_V3_DATASET_PUBLICATION_ID,
                resolution_ids=tuple(
                    item.market_gap_resolution_id for item in resolutions
                )
                + spec.additional_reviewed_resolution_ids,
                cleaning_version_id=spec.cleaning_version_id,
                calendar_version_id=spec.calendar_version_id,
                output_dataset_key=REPAIRED_V5_DATASET_KEY,
                output_version_number=REPAIRED_V5_DATASET_VERSION,
                reconstruction_policy=V2_RECONSTRUCTION_POLICY,
                created_by=spec.created_by,
            )
        )
        return FrozenSp500PreparedRepair(
            spec.exclusion_policy.fingerprint,
            prior_gate,
            review_artifact_id,
            resolutions,
            reconciliation,
            tuple(
                sorted(
                    {item.security_id for item in decisions}
                    | {
                        item.security_id
                        for item in additional_resolutions
                        if item.resolution_kind == "exclude_security"
                    },
                    key=str,
                )
            ),
            additional_resolutions,
        )

    def inspect_inputs(
        self, spec: FrozenSp500DataRepairSpec
    ) -> tuple[FrozenPriorGateCarry, tuple[FrozenReviewedResolution, ...]]:
        """Load the exact immutable carry-forward inputs without publishing anything."""
        with self._engine.connect() as connection:
            additional = _load_reviewed_resolutions(connection, spec)
            prior_gate = _load_prior_gate_carry(connection, spec, additional)
        return prior_gate, additional

    def inspect_post_repair_closures(
        self,
        spec: FrozenSp500DataRepairSpec,
        prepared: FrozenSp500PreparedRepair,
        weekly_report: MarketDataClosureAuditReport,
        monthly_report: MarketDataClosureAuditReport,
    ) -> tuple[FrozenPostRepairClosure, FrozenPostRepairClosure]:
        """Read and freeze the exact weekly/monthly reference Cohort projections."""
        repaired_dataset_id = (
            prepared.reconciliation_publication.dataset_publication_id
        )
        weekly_cohort_id, monthly_cohort_id = validate_post_repair_closure_pair(
            weekly_report=weekly_report,
            monthly_report=monthly_report,
            repaired_dataset_publication_id=repaired_dataset_id,
            coverage_start=spec.exclusion_policy.coverage_start,
            coverage_end=spec.exclusion_policy.coverage_end,
        )
        with self._engine.connect() as connection:
            return _load_post_repair_closures(
                connection,
                weekly_report=weekly_report,
                monthly_report=monthly_report,
                weekly_cohort_id=weekly_cohort_id,
                monthly_cohort_id=monthly_cohort_id,
            )

    def publish_gate(
        self,
        spec: FrozenSp500DataRepairSpec,
        prepared: FrozenSp500PreparedRepair,
        weekly_post_repair_report: MarketDataClosureAuditReport,
        monthly_post_repair_report: MarketDataClosureAuditReport,
    ) -> FrozenSp500GatePublication:
        validate_prepared_repair(spec, prepared)
        current_prior_gate, current_additional = self.inspect_inputs(spec)
        if current_prior_gate != prepared.prior_gate_carry:
            raise ValueError("Prepared repair prior Gate projection has drifted")
        if current_additional != prepared.additional_reviewed_resolutions:
            raise ValueError("Prepared repair reviewed Resolution projection has drifted")
        repaired = prepared.reconciliation_publication
        weekly_closure, monthly_closure = self.inspect_post_repair_closures(
            spec,
            prepared,
            weekly_post_repair_report,
            monthly_post_repair_report,
        )
        weekly_review_artifact_id = self._publish_review(
            artifact_key=_POST_REVIEW_ARTIFACT_KEYS["weekly"],
            version_number=REPAIRED_V5_DATASET_VERSION,
            dataset_publication_id=repaired.dataset_publication_id,
            dataset_artifact_id=repaired.dataset_artifact_id,
            policy=spec.exclusion_policy,
            report=weekly_closure.report,
            phase="post_repair",
            prior_review_artifact_id=prepared.pre_repair_review_artifact_id,
            frequency="weekly",
            cohort_artifact_id=weekly_closure.cohort_artifact_id,
        )
        monthly_review_artifact_id = self._publish_review(
            artifact_key=_POST_REVIEW_ARTIFACT_KEYS["monthly"],
            version_number=REPAIRED_V5_DATASET_VERSION,
            dataset_publication_id=repaired.dataset_publication_id,
            dataset_artifact_id=repaired.dataset_artifact_id,
            policy=spec.exclusion_policy,
            report=monthly_closure.report,
            phase="post_repair",
            prior_review_artifact_id=prepared.pre_repair_review_artifact_id,
            frequency="monthly",
            cohort_artifact_id=monthly_closure.cohort_artifact_id,
        )
        if weekly_review_artifact_id == monthly_review_artifact_id:
            raise ValueError("Weekly and monthly closure evidence identities collided")
        assessment = self._gate.publish(
            build_gate_assessment_spec(
                spec,
                prepared=prepared,
                weekly_post_review_artifact_id=weekly_review_artifact_id,
                monthly_post_review_artifact_id=monthly_review_artifact_id,
                weekly_post_repair_report=weekly_closure.report,
                monthly_post_repair_report=monthly_closure.report,
            )
        )
        return FrozenSp500GatePublication(
            weekly_review_artifact_id,
            monthly_review_artifact_id,
            assessment,
        )

    def _publish_review(
        self,
        *,
        artifact_key: str,
        version_number: int,
        dataset_publication_id: uuid.UUID,
        dataset_artifact_id: uuid.UUID,
        policy: FrozenExclusionPolicy,
        report: MarketDataClosureAuditReport,
        phase: str,
        prior_review_artifact_id: uuid.UUID | None,
        frequency: ClosureFrequency | None,
        cohort_artifact_id: uuid.UUID | None,
    ) -> uuid.UUID:
        if (frequency is None) != (cohort_artifact_id is None):
            raise ValueError(
                "Closure review frequency and Cohort Artifact must be supplied together"
            )
        document = {
            "contract_version": _REVIEW_CONTRACT,
            "phase": phase,
            "frequency": frequency,
            "dataset_publication_id": str(dataset_publication_id),
            "dataset_artifact_id": str(dataset_artifact_id),
            "evaluation_cohort_artifact_id": (
                None if cohort_artifact_id is None else str(cohort_artifact_id)
            ),
            "policy_fingerprint": policy.fingerprint,
            "policy": policy.document,
            "closure_report": report.to_dict(),
        }
        dependencies = [DependencyInput(dataset_artifact_id, "market_dataset", 0)]
        if prior_review_artifact_id is not None:
            dependencies.append(
                DependencyInput(prior_review_artifact_id, "pre_repair_review", 1)
            )
        if cohort_artifact_id is not None:
            dependencies.append(
                DependencyInput(cohort_artifact_id, "evaluation_cohort", 2)
            )
        publication = self._artifacts.publish(
            artifact_type="v022_market_closure_review",
            artifact_key=artifact_key,
            version_number=version_number,
            semantic_payload=document,
            content_payload=document,
            dependencies=tuple(dependencies),
            reason=f"publish frozen S&P 500 {phase} closure review",
        )
        return publication.artifact_id


def _load_post_repair_closures(
    connection: Connection,
    *,
    weekly_report: MarketDataClosureAuditReport,
    monthly_report: MarketDataClosureAuditReport,
    weekly_cohort_id: uuid.UUID,
    monthly_cohort_id: uuid.UUID,
) -> tuple[FrozenPostRepairClosure, FrozenPostRepairClosure]:
    rows = connection.execute(
        text(
            """
            SELECT cohort.evaluation_cohort_version_id,cohort.artifact_id,
                   cohort.cohort_key,cohort.version_number,cohort.research_tier,
                   cohort.frequency,cohort.universe_history_id,
                   cohort.dataset_publication_id,
                   cohort.benchmark_dataset_publication_id,
                   cohort.security_market_quality_report_id,
                   cohort.calendar_version_id,cohort.warmup_start,
                   cohort.evaluation_start,cohort.evaluation_end,
                   cohort.required_history_sessions,cohort.cost_bps_per_side,
                   cohort.execution_delay_sessions,cohort.benchmark_key,
                   cohort.price_semantics,cohort.historical_pit_claimed,
                   cohort.session_count,artifact.status AS artifact_status
              FROM experiment.v022_evaluation_cohort_version cohort
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=cohort.artifact_id
             WHERE cohort.evaluation_cohort_version_id IN (:weekly,:monthly)
            """
        ),
        {"weekly": weekly_cohort_id, "monthly": monthly_cohort_id},
    ).mappings().all()
    if len(rows) != 2:
        raise LookupError(
            "Both exact weekly and monthly Evaluation Cohorts must exist"
        )
    rows_by_id = {
        cast(uuid.UUID, item["evaluation_cohort_version_id"]): item for item in rows
    }
    if set(rows_by_id) != {weekly_cohort_id, monthly_cohort_id}:
        raise LookupError("Post-repair closure Cohort projection is incomplete")
    weekly = _post_repair_closure(
        rows_by_id[weekly_cohort_id], frequency="weekly", report=weekly_report
    )
    monthly = _post_repair_closure(
        rows_by_id[monthly_cohort_id], frequency="monthly", report=monthly_report
    )
    _validate_matching_cohort_environments(
        rows_by_id[weekly_cohort_id], rows_by_id[monthly_cohort_id]
    )
    return weekly, monthly


def _post_repair_closure(
    row: RowMapping,
    *,
    frequency: ClosureFrequency,
    report: MarketDataClosureAuditReport,
) -> FrozenPostRepairClosure:
    if row["artifact_status"] != "published":
        raise ValueError(f"Post-repair {frequency} Cohort Artifact is not published")
    if row["frequency"] != frequency:
        raise ValueError(
            f"Post-repair {frequency} report does not name a {frequency} Cohort"
        )
    if (
        report.coverage_start != cast(date, row["warmup_start"]).isoformat()
        or report.coverage_end != cast(date, row["evaluation_end"]).isoformat()
    ):
        raise ValueError(
            f"Post-repair {frequency} report does not cover its exact Cohort"
        )
    if report.session_count != row["session_count"]:
        raise ValueError(
            f"Post-repair {frequency} report session count differs from its Cohort"
        )
    return FrozenPostRepairClosure(
        frequency,
        cast(uuid.UUID, row["evaluation_cohort_version_id"]),
        cast(uuid.UUID, row["artifact_id"]),
        report,
    )


def _validate_matching_cohort_environments(
    weekly: RowMapping, monthly: RowMapping
) -> None:
    comparable_fields = (
        "version_number",
        "research_tier",
        "universe_history_id",
        "dataset_publication_id",
        "benchmark_dataset_publication_id",
        "security_market_quality_report_id",
        "calendar_version_id",
        "warmup_start",
        "evaluation_start",
        "evaluation_end",
        "required_history_sessions",
        "cost_bps_per_side",
        "execution_delay_sessions",
        "benchmark_key",
        "price_semantics",
        "historical_pit_claimed",
        "session_count",
    )
    mismatched = tuple(
        field for field in comparable_fields if weekly[field] != monthly[field]
    )
    if mismatched:
        raise ValueError(
            "Weekly and monthly closure Cohorts do not share one frozen environment: "
            + ",".join(mismatched)
        )


def _resolution_spec(
    spec: FrozenSp500DataRepairSpec,
    *,
    decision: FrozenExclusionDecision,
    review_artifact_id: uuid.UUID,
) -> MarketGapResolutionSpec:
    policy = spec.exclusion_policy
    return MarketGapResolutionSpec(
        primary_dataset_publication_id=PRIMARY_V3_DATASET_PUBLICATION_ID,
        security_id=decision.security_id,
        gap_key=f"sp500_v3_full_range_exclusion__{decision.security_id.hex}",
        version_number=1,
        gap_type="uniform_exclusion",
        gap_start=policy.coverage_start,
        gap_end=policy.coverage_end,
        resolution_kind="exclude_security",
        evidence=(GapResolutionEvidenceRef(review_artifact_id, "review_note"),),
        details={
            "policy_key": policy.policy_key,
            "policy_fingerprint": policy.fingerprint,
            "reason_code": decision.reason_code,
            "basis_rule_codes": list(decision.basis_rule_codes),
            "reviewer_note": decision.reviewer_note,
            "preserve_security_identity": True,
        },
        created_by=spec.created_by,
    )


def build_gate_assessment_spec(
    spec: FrozenSp500DataRepairSpec,
    *,
    prepared: FrozenSp500PreparedRepair,
    weekly_post_review_artifact_id: uuid.UUID,
    monthly_post_review_artifact_id: uuid.UUID,
    weekly_post_repair_report: MarketDataClosureAuditReport,
    monthly_post_repair_report: MarketDataClosureAuditReport,
) -> DatasetGateAssessmentSpec:
    if weekly_post_review_artifact_id == monthly_post_review_artifact_id:
        raise ValueError("Weekly and monthly Gate evidence Artifacts must be distinct")
    validate_post_repair_closure_pair(
        weekly_report=weekly_post_repair_report,
        monthly_report=monthly_post_repair_report,
        repaired_dataset_publication_id=(
            prepared.reconciliation_publication.dataset_publication_id
        ),
        coverage_start=spec.exclusion_policy.coverage_start,
        coverage_end=spec.exclusion_policy.coverage_end,
    )
    policy = spec.exclusion_policy
    supplied_evidence = (
        DatasetGateEvidenceRef(
            prepared.pre_repair_review_artifact_id, "supporting_evidence"
        ),
        DatasetGateEvidenceRef(
            weekly_post_review_artifact_id, "supporting_evidence"
        ),
        DatasetGateEvidenceRef(
            monthly_post_review_artifact_id, "supporting_evidence"
        ),
        DatasetGateEvidenceRef(
            prepared.prior_gate_carry.prior_gate_artifact_id,
            "supporting_evidence",
        ),
    ) + tuple(
        DatasetGateEvidenceRef(item, "lifecycle_event")
        for item in spec.lifecycle_evidence_artifact_ids
    ) + tuple(
        item
        for item in prepared.prior_gate_carry.evidence
        if item.role != "gap_resolution"
    )
    evidence = _merge_gate_evidence(supplied_evidence)
    baseline = (
        DatasetGateFinding(
            "historical_membership_retrospective",
            "membership",
            "warning",
            "none",
            "warning",
            evidence_artifact_id=weekly_post_review_artifact_id,
            details={"historical_pit_claimed": False},
        ),
        DatasetGateFinding(
            "retrospective_price_snapshot",
            "data_provenance",
            "warning",
            "none",
            "warning",
            evidence_artifact_id=monthly_post_review_artifact_id,
            details={"source_class": "free_retrospective_market_data"},
        ),
        DatasetGateFinding(
            "free_source_market_gaps",
            "market_coverage",
            "warning",
            "none",
            "warning",
            evidence_artifact_id=prepared.pre_repair_review_artifact_id,
            details={"resolution_policy_fingerprint": policy.fingerprint},
        ),
        DatasetGateFinding(
            "prior_uniform_exclusions_carried_forward",
            "data_provenance",
            "notice",
            "none",
            "none",
            evidence_artifact_id=(
                prepared.prior_gate_carry.prior_gate_artifact_id
            ),
            details={
                "prior_gate_assessment_id": str(
                    prepared.prior_gate_carry.prior_gate_assessment_id
                ),
                "prior_gate_fingerprint": (
                    prepared.prior_gate_carry.assessment_fingerprint
                ),
                "uniform_exclusion_count": len(
                    prepared.prior_gate_carry.uniform_exclusions
                ),
            },
        ),
        DatasetGateFinding(
            "dual_frequency_closure_verified",
            "market_coverage",
            "notice",
            "none",
            "none",
            evidence_artifact_id=weekly_post_review_artifact_id,
            details={
                "weekly_closure_artifact_id": str(
                    weekly_post_review_artifact_id
                ),
                "monthly_closure_artifact_id": str(
                    monthly_post_review_artifact_id
                ),
                "weekly_evaluation_cohort_version_id": (
                    weekly_post_repair_report.evaluation_cohort_version_id
                ),
                "monthly_evaluation_cohort_version_id": (
                    monthly_post_repair_report.evaluation_cohort_version_id
                ),
            },
        ),
    )
    new_exclusion_findings = tuple(
        DatasetGateFinding(
            "frozen_data_repair_uniform_exclusion",
            "uniform_exclusion",
            "warning",
            "none",
            "warning",
            security_id=item.security_id,
            evidence_artifact_id=prepared.pre_repair_review_artifact_id,
            details={"reason_code": item.reason_code},
        )
        for item in policy.decisions
    )
    review_findings = tuple(
        DatasetGateFinding(
            f"closure_review_{frequency}_{item.rule_code}",
            "market_coverage",
            "warning",
            "none",
            "warning",
            security_id=(
                None if item.security_id is None else uuid.UUID(item.security_id)
            ),
            evidence_artifact_id=artifact_id,
            details={
                "frequency": frequency,
                "message": item.message,
                "closure_details": item.details,
            },
        )
        for frequency, report, artifact_id in (
            (
                "weekly",
                weekly_post_repair_report,
                weekly_post_review_artifact_id,
            ),
            (
                "monthly",
                monthly_post_repair_report,
                monthly_post_review_artifact_id,
            ),
        )
        for item in report.review_findings
    )
    new_exclusions = tuple(
        DatasetGateUniformExclusion(
            item.security_id,
            policy.coverage_start,
            policy.coverage_end,
            item.reason_code,
            prepared.pre_repair_review_artifact_id,
            {
                "policy_fingerprint": policy.fingerprint,
                "basis_rule_codes": list(item.basis_rule_codes),
                "preserve_security_identity": True,
            },
        )
        for item in policy.decisions
    )
    exclusion_findings = _merge_gate_findings(
        prepared.prior_gate_carry.findings + new_exclusion_findings
    )
    exclusions = _merge_gate_exclusions(
        prepared.prior_gate_carry.uniform_exclusions + new_exclusions
    )
    return DatasetGateAssessmentSpec(
        dataset_publication_id=(
            prepared.reconciliation_publication.dataset_publication_id
        ),
        universe_membership_ledger_id=spec.universe_membership_ledger_id,
        gate_key=REPAIRED_V4_GATE_KEY,
        version_number=REPAIRED_V4_GATE_VERSION,
        assessed_coverage_start=policy.coverage_start,
        assessed_coverage_end=policy.coverage_end,
        ranking_eligibility="rankable_research",
        product_eligibility="eligible_with_warnings",
        evidence=evidence,
        findings=baseline + exclusion_findings + review_findings,
        uniform_exclusions=exclusions,
        created_by=spec.created_by,
    )


def validate_prepared_repair(
    spec: FrozenSp500DataRepairSpec, prepared: FrozenSp500PreparedRepair
) -> None:
    if prepared.policy_fingerprint != spec.exclusion_policy.fingerprint:
        raise ValueError("Prepared repair belongs to another frozen exclusion policy")
    if (
        prepared.prior_gate_carry.prior_gate_assessment_id
        != spec.prior_gate_assessment_id
    ):
        raise ValueError("Prepared repair carries another prior Dataset Gate")
    expected_policy = tuple(
        sorted(
            (item.security_id for item in spec.exclusion_policy.decisions), key=str
        )
    )
    if len(prepared.resolution_publications) != len(expected_policy):
        raise ValueError("Prepared repair does not contain one Resolution per exclusion")
    additional_ids = tuple(
        item.market_gap_resolution_id
        for item in prepared.additional_reviewed_resolutions
    )
    if additional_ids != spec.additional_reviewed_resolution_ids:
        raise ValueError("Prepared repair targeted Resolutions differ from the repair Spec")
    if any(
        item.resolution_kind == "unresolved"
        for item in prepared.additional_reviewed_resolutions
    ):
        raise ValueError("Prepared repair contains an unresolved additional Resolution")
    _validate_resolution_security_conflicts(
        spec.exclusion_policy.decisions,
        prepared.additional_reviewed_resolutions,
    )
    expected_reconciled = tuple(
        sorted(
            set(expected_policy)
            | {
                item.security_id
                for item in prepared.additional_reviewed_resolutions
                if item.resolution_kind == "exclude_security"
            },
            key=str,
        )
    )
    if tuple(sorted(prepared.exclusion_security_ids, key=str)) != expected_reconciled:
        raise ValueError("Prepared repair reconciled exclusions are incomplete")
    if (
        prepared.reconciliation_publication.excluded_security_count
        != len(expected_reconciled)
    ):
        raise ValueError("Reconciled Dataset exclusion count differs from its Resolutions")
    additional_artifacts = {
        item.artifact_id: item for item in prepared.additional_reviewed_resolutions
    }
    for evidence in prepared.prior_gate_carry.evidence:
        if evidence.role != "gap_resolution":
            continue
        resolution = additional_artifacts.get(evidence.artifact_id)
        if resolution is None or resolution.resolution_kind != "exclude_security":
            raise ValueError(
                "Prior Gate gap exclusion is absent from the v5 reconciliation"
            )
    if not prepared.resolution_publications:
        raise ValueError("Prepared repair contains no reviewed Gap Resolutions")


def _load_reviewed_resolutions(
    connection: Connection, spec: FrozenSp500DataRepairSpec
) -> tuple[FrozenReviewedResolution, ...]:
    if not spec.additional_reviewed_resolution_ids:
        return ()
    rows = connection.execute(
        text(
            """
            SELECT resolution.market_gap_resolution_id,resolution.artifact_id,
                   resolution.primary_dataset_publication_id,resolution.security_id,
                   resolution.resolution_kind,artifact.status
              FROM data.v022_market_gap_resolution resolution
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=resolution.artifact_id
             WHERE resolution.market_gap_resolution_id=ANY(:ids)
            """
        ),
        {"ids": list(spec.additional_reviewed_resolution_ids)},
    ).mappings().all()
    by_id = {
        cast(uuid.UUID, row["market_gap_resolution_id"]): row for row in rows
    }
    if set(by_id) != set(spec.additional_reviewed_resolution_ids):
        raise LookupError("One or more additional reviewed Resolutions were not found")
    result: list[FrozenReviewedResolution] = []
    for resolution_id in spec.additional_reviewed_resolution_ids:
        row = by_id[resolution_id]
        if row["status"] != "published":
            raise ValueError("Additional reviewed Resolution is not published")
        if row["primary_dataset_publication_id"] != PRIMARY_V3_DATASET_PUBLICATION_ID:
            raise ValueError("Additional reviewed Resolution belongs to another primary Dataset")
        kind = cast(ResolutionKind, row["resolution_kind"])
        if kind == "unresolved":
            raise ValueError("Unresolved additional Resolution cannot enter v5")
        result.append(
            FrozenReviewedResolution(
                resolution_id,
                cast(uuid.UUID, row["artifact_id"]),
                cast(uuid.UUID, row["security_id"]),
                kind,
            )
        )
    return tuple(result)


def _load_prior_gate_carry(
    connection: Connection,
    spec: FrozenSp500DataRepairSpec,
    additional_resolutions: tuple[FrozenReviewedResolution, ...],
) -> FrozenPriorGateCarry:
    gate = connection.execute(
        text(
            """
            SELECT gate.*,artifact.status AS artifact_status,
                   reconciled.primary_dataset_publication_id
              FROM data.v022_dataset_gate_assessment gate
              JOIN lineage.artifact artifact ON artifact.artifact_id=gate.artifact_id
              LEFT JOIN data.v022_reconciled_market_dataset_binding reconciled
                ON reconciled.dataset_publication_id=gate.dataset_publication_id
             WHERE gate.dataset_gate_assessment_id=:gate
            """
        ),
        {"gate": spec.prior_gate_assessment_id},
    ).mappings().one_or_none()
    if gate is None:
        raise LookupError("Prior Dataset Gate Assessment was not found")
    policy = spec.exclusion_policy
    if (
        gate["artifact_status"] != "published"
        or gate["gate_key"] != REPAIRED_V4_GATE_KEY
        or gate["version_number"] != PRIOR_V3_GATE_VERSION
        or gate["primary_dataset_publication_id"] != PRIMARY_V3_DATASET_PUBLICATION_ID
        or gate["universe_membership_ledger_id"]
        != spec.universe_membership_ledger_id
        or gate["assessed_coverage_start"] != policy.coverage_start
        or gate["assessed_coverage_end"] != policy.coverage_end
        or gate["ranking_eligibility"] != "rankable_research"
        or gate["product_eligibility"] != "eligible_with_warnings"
        or gate["blocker_count"] != 0
        or gate["uniform_exclusion_count"]
        != PRIOR_V3_GATE_UNIFORM_EXCLUSION_COUNT
    ):
        raise ValueError("Prior Dataset Gate does not match the frozen v3 carry contract")
    evidence_rows = connection.execute(
        text(
            """
            SELECT evidence.evidence_artifact_id,evidence.evidence_role,
                   artifact.status
              FROM data.v022_dataset_gate_assessment_evidence evidence
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=evidence.evidence_artifact_id
             WHERE evidence.dataset_gate_assessment_id=:gate
             ORDER BY evidence.ordinal
            """
        ),
        {"gate": spec.prior_gate_assessment_id},
    ).mappings().all()
    evidence_by_id = {
        cast(uuid.UUID, row["evidence_artifact_id"]): row for row in evidence_rows
    }
    if any(row["status"] != "published" for row in evidence_rows):
        raise ValueError("Prior Dataset Gate contains unpublished evidence")
    exclusion_rows = connection.execute(
        text(
            """
            SELECT ordinal,security_id,exclusion_start,exclusion_end,reason_code,
                   evidence_artifact_id,exclusion_document->'details' AS details
              FROM data.v022_dataset_gate_uniform_exclusion
             WHERE dataset_gate_assessment_id=:gate
             ORDER BY ordinal
            """
        ),
        {"gate": spec.prior_gate_assessment_id},
    ).mappings().all()
    if len(exclusion_rows) != PRIOR_V3_GATE_UNIFORM_EXCLUSION_COUNT:
        raise ValueError("Prior Dataset Gate exclusion projection is incomplete")
    if any(
        row["exclusion_start"] != policy.coverage_start
        or row["exclusion_end"] != policy.coverage_end
        for row in exclusion_rows
    ):
        raise ValueError("Prior Dataset Gate exclusion interval drifted")
    security_ids = [cast(uuid.UUID, row["security_id"]) for row in exclusion_rows]
    finding_rows = connection.execute(
        text(
            """
            SELECT finding_code,finding_category,severity,ranking_effect,
                   product_effect,security_id,evidence_artifact_id,
                   finding_document->'details' AS details
              FROM data.v022_dataset_gate_finding
             WHERE dataset_gate_assessment_id=:gate
               AND finding_category='uniform_exclusion'
               AND security_id=ANY(:security_ids)
             ORDER BY ordinal
            """
        ),
        {"gate": spec.prior_gate_assessment_id, "security_ids": security_ids},
    ).mappings().all()
    findings_by_security: dict[uuid.UUID, list[Any]] = {}
    for row in finding_rows:
        findings_by_security.setdefault(
            cast(uuid.UUID, row["security_id"]), []
        ).append(row)
    findings: list[DatasetGateFinding] = []
    exclusions: list[DatasetGateUniformExclusion] = []
    referenced_evidence_ids: set[uuid.UUID] = set()
    for row in exclusion_rows:
        security_id = cast(uuid.UUID, row["security_id"])
        matching = findings_by_security.get(security_id, [])
        if len(matching) != 1:
            raise ValueError("Prior Dataset Gate exclusion lacks one exact warning finding")
        finding = matching[0]
        if (
            finding["severity"] != "warning"
            or finding["ranking_effect"] != "none"
            or finding["product_effect"] != "warning"
        ):
            raise ValueError("Prior Dataset Gate exclusion finding is not carry-safe")
        exclusion_evidence = cast(uuid.UUID, row["evidence_artifact_id"])
        finding_evidence = cast(uuid.UUID, finding["evidence_artifact_id"])
        referenced_evidence_ids.update((exclusion_evidence, finding_evidence))
        exclusions.append(
            DatasetGateUniformExclusion(
                security_id,
                cast(date, row["exclusion_start"]),
                cast(date, row["exclusion_end"]),
                str(row["reason_code"]),
                exclusion_evidence,
                cast(dict[str, Any], row["details"] or {}),
            )
        )
        findings.append(
            DatasetGateFinding(
                str(finding["finding_code"]),
                "uniform_exclusion",
                "warning",
                "none",
                "warning",
                security_id=security_id,
                evidence_artifact_id=finding_evidence,
                details=cast(dict[str, Any], finding["details"] or {}),
            )
        )
    if not referenced_evidence_ids.issubset(evidence_by_id):
        raise ValueError("Prior Dataset Gate exclusion evidence projection is incomplete")
    additional_by_artifact = {item.artifact_id: item for item in additional_resolutions}
    carry_evidence: list[DatasetGateEvidenceRef] = []
    for artifact_id in sorted(referenced_evidence_ids, key=str):
        row = evidence_by_id[artifact_id]
        role = cast(GateEvidenceRole, row["evidence_role"])
        if role == "reconciliation_plan":
            raise ValueError("A prior reconciliation plan cannot be carried into v5")
        if role == "gap_resolution":
            resolution = additional_by_artifact.get(artifact_id)
            if resolution is None or resolution.resolution_kind != "exclude_security":
                raise ValueError(
                    "Prior Gate gap exclusion must be an exact v5 exclude Resolution"
                )
        carry_evidence.append(DatasetGateEvidenceRef(artifact_id, role))
    _validate_absent_provider_exclusions(connection, exclusions)
    return FrozenPriorGateCarry(
        spec.prior_gate_assessment_id,
        cast(uuid.UUID, gate["artifact_id"]),
        str(gate["assessment_fingerprint"]),
        tuple(carry_evidence),
        tuple(findings),
        tuple(exclusions),
    )


def _validate_absent_provider_exclusions(
    connection: Connection,
    exclusions: list[DatasetGateUniformExclusion],
) -> None:
    provider_ids = tuple(
        item.security_id
        for item in exclusions
        if item.reason_code == "frozen_free_source_provider_unavailable"
    )
    if len(provider_ids) != PRIOR_V3_GATE_UNIFORM_EXCLUSION_COUNT - 1:
        raise ValueError("Prior Gate provider-unavailable exclusion count drifted")
    present_count = connection.execute(
        text(
            """
            SELECT count(DISTINCT security.security_id)
              FROM catalog.security security
              JOIN data.daily_bar bar ON bar.asset_id=security.legacy_asset_id
             WHERE bar.dataset_publication_id=:dataset
               AND security.security_id=ANY(:security_ids)
            """
        ),
        {
            "dataset": PRIMARY_V3_DATASET_PUBLICATION_ID,
            "security_ids": list(provider_ids),
        },
    ).scalar_one()
    if int(present_count) != 0:
        raise ValueError(
            "Provider-unavailable identities unexpectedly contain primary v3 bars"
        )


def _validate_resolution_security_conflicts(
    decisions: tuple[FrozenExclusionDecision, ...],
    additional: tuple[FrozenReviewedResolution, ...],
) -> None:
    policy_ids = {item.security_id for item in decisions}
    additional_by_security: dict[uuid.UUID, list[FrozenReviewedResolution]] = {}
    for item in additional:
        additional_by_security.setdefault(item.security_id, []).append(item)
    if policy_ids.intersection(additional_by_security):
        raise ValueError(
            "A Security cannot have both a new full-range exclusion and an additional Resolution"
        )
    for security_id, rows in additional_by_security.items():
        if len(rows) > 1 and any(
            item.resolution_kind == "exclude_security" for item in rows
        ):
            raise ValueError(
                f"Additional exclude Resolution conflicts for Security {security_id}"
            )


def _merge_gate_evidence(
    values: tuple[DatasetGateEvidenceRef, ...]
) -> tuple[DatasetGateEvidenceRef, ...]:
    result: dict[uuid.UUID, DatasetGateEvidenceRef] = {}
    for item in values:
        previous = result.get(item.artifact_id)
        if previous is not None and previous != item:
            raise ValueError("Dataset Gate evidence Artifact has conflicting roles")
        result[item.artifact_id] = item
    return tuple(result.values())


def _merge_gate_findings(
    values: tuple[DatasetGateFinding, ...]
) -> tuple[DatasetGateFinding, ...]:
    result: dict[tuple[str, uuid.UUID | None], DatasetGateFinding] = {}
    for item in values:
        key = (item.finding_code, item.security_id)
        previous = result.get(key)
        if previous is not None and previous != item:
            raise ValueError("Dataset Gate finding carry-forward conflict")
        result[key] = item
    return tuple(result.values())


def _merge_gate_exclusions(
    values: tuple[DatasetGateUniformExclusion, ...]
) -> tuple[DatasetGateUniformExclusion, ...]:
    result: dict[uuid.UUID, DatasetGateUniformExclusion] = {}
    for item in values:
        previous = result.get(item.security_id)
        if previous is not None and previous != item:
            raise ValueError("Dataset Gate uniform exclusion carry-forward conflict")
        result[item.security_id] = item
    return tuple(result.values())


def prepared_repair_to_dict(prepared: FrozenSp500PreparedRepair) -> dict[str, Any]:
    return asdict(prepared)
