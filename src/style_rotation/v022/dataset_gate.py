from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

RankingEligibility = Literal["rankable_research", "exploratory_only"]
ProductEligibility = Literal["eligible", "eligible_with_warnings", "ineligible"]
GateEvidenceRole = Literal[
    "identity_resolution",
    "lifecycle_event",
    "gap_resolution",
    "reconciliation_plan",
    "supporting_evidence",
]
FindingCategory = Literal[
    "data_provenance",
    "identity",
    "membership",
    "market_coverage",
    "lifecycle",
    "settlement",
    "replay",
    "benchmark_calendar",
    "uniform_exclusion",
]
FindingSeverity = Literal["notice", "warning", "blocker"]
RankingEffect = Literal["none", "exploratory_only"]
ProductEffect = Literal["none", "warning", "ineligible"]

_CONTRACT = "v0.22.dataset_gate_assessment.v1"
_BASELINE_WARNING_CODES = frozenset(
    {"historical_membership_retrospective", "retrospective_price_snapshot"}
)


@dataclass(frozen=True, slots=True)
class DatasetGateEvidenceRef:
    artifact_id: uuid.UUID
    role: GateEvidenceRole


@dataclass(frozen=True, slots=True)
class DatasetGateFinding:
    finding_code: str
    finding_category: FindingCategory
    severity: FindingSeverity
    ranking_effect: RankingEffect
    product_effect: ProductEffect
    security_id: uuid.UUID | None = None
    evidence_artifact_id: uuid.UUID | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("finding_code", self.finding_code)
        _json_object(self.details, "finding details")
        if self.severity == "notice" and self.product_effect != "none":
            raise ValueError("Notice findings cannot affect Product eligibility")
        if self.severity == "warning" and self.product_effect not in {"none", "warning"}:
            raise ValueError("Warning findings cannot be correctness blockers")
        if self.severity == "blocker" and self.product_effect != "ineligible":
            raise ValueError("Blocker findings must make Product ineligible")


@dataclass(frozen=True, slots=True)
class DatasetGateUniformExclusion:
    security_id: uuid.UUID
    exclusion_start: date
    exclusion_end: date
    reason_code: str
    evidence_artifact_id: uuid.UUID
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("reason_code", self.reason_code)
        if self.exclusion_start > self.exclusion_end:
            raise ValueError("Uniform exclusion interval is reversed")
        _json_object(self.details, "uniform exclusion details")


@dataclass(frozen=True, slots=True)
class DatasetGateAssessmentSpec:
    dataset_publication_id: uuid.UUID
    universe_membership_ledger_id: uuid.UUID
    gate_key: str
    version_number: int
    assessed_coverage_start: date
    assessed_coverage_end: date
    ranking_eligibility: RankingEligibility
    product_eligibility: ProductEligibility
    evidence: tuple[DatasetGateEvidenceRef, ...]
    findings: tuple[DatasetGateFinding, ...]
    uniform_exclusions: tuple[DatasetGateUniformExclusion, ...]
    created_by: str

    def __post_init__(self) -> None:
        _require_text("gate_key", self.gate_key)
        _require_text("created_by", self.created_by)
        if self.version_number < 1:
            raise ValueError("Dataset Gate version_number must be positive")
        if self.assessed_coverage_start > self.assessed_coverage_end:
            raise ValueError("Dataset Gate coverage interval is reversed")
        if len({item.artifact_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("Dataset Gate evidence Artifacts must be unique")
        finding_keys = {(item.finding_code, item.security_id) for item in self.findings}
        if len(finding_keys) != len(self.findings):
            raise ValueError("Dataset Gate findings must have unique code and Security")
        exclusion_ids = {item.security_id for item in self.uniform_exclusions}
        if len(exclusion_ids) != len(self.uniform_exclusions):
            raise ValueError("A Security can be uniformly excluded only once")


@dataclass(frozen=True, slots=True)
class DatasetGateAssessmentPublication:
    dataset_gate_assessment_id: uuid.UUID
    artifact_id: uuid.UUID
    assessment_fingerprint: str
    ranking_eligibility: RankingEligibility
    product_eligibility: ProductEligibility
    warning_count: int
    blocker_count: int
    uniform_exclusion_count: int
    reused: bool


@dataclass(frozen=True, slots=True)
class _GateInputs:
    dataset: RowMapping
    ledger: RowMapping
    evidence: tuple[DatasetGateEvidenceRef, ...]
    reconciliation_plan_artifact_id: uuid.UUID | None
    gap_resolution_count: int
    alternate_observation_count: int


class DatasetGateAssessmentService:
    """Freeze independent ranking and Product conclusions for one exact Dataset."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, spec: DatasetGateAssessmentSpec) -> DatasetGateAssessmentPublication:
        inputs = self._load_inputs(spec)
        _validate_gate_decisions(spec, inputs.evidence)
        finding_documents = tuple(
            _finding_document(ordinal, item) for ordinal, item in enumerate(spec.findings)
        )
        exclusion_documents = tuple(
            _exclusion_document(ordinal, item)
            for ordinal, item in enumerate(spec.uniform_exclusions)
        )
        warnings = sum(item.severity == "warning" for item in spec.findings)
        blockers = sum(item.severity == "blocker" for item in spec.findings)
        identity_resolutions = sum(
            item.role == "identity_resolution" for item in inputs.evidence
        )
        lifecycle_events = sum(item.role == "lifecycle_event" for item in inputs.evidence)
        document = {
            "contract_version": _CONTRACT,
            "dataset_publication_id": str(spec.dataset_publication_id),
            "dataset_artifact_id": str(inputs.dataset["dataset_artifact_id"]),
            "universe_membership_ledger_id": str(spec.universe_membership_ledger_id),
            "universe_membership_ledger_artifact_id": str(inputs.ledger["ledger_artifact_id"]),
            "universe_history_id": str(inputs.ledger["universe_history_id"]),
            "universe_history_artifact_id": str(inputs.ledger["history_artifact_id"]),
            "quality_report_artifact_id": str(inputs.dataset["quality_report_artifact_id"]),
            "calendar_version_id": str(inputs.dataset["calendar_version_id"]),
            "cleaning_version_id": str(inputs.dataset["cleaning_version_id"]),
            "gate_key": spec.gate_key,
            "version_number": spec.version_number,
            "assessed_coverage_start": spec.assessed_coverage_start.isoformat(),
            "assessed_coverage_end": spec.assessed_coverage_end.isoformat(),
            "price_semantics": str(inputs.dataset["price_semantics"]),
            "historical_pit_claimed": False,
            "ranking_eligibility": spec.ranking_eligibility,
            "product_eligibility": spec.product_eligibility,
            "finding_count": len(spec.findings),
            "warning_count": warnings,
            "blocker_count": blockers,
            "evidence_count": len(inputs.evidence),
            "uniform_exclusion_count": len(spec.uniform_exclusions),
            "identity_resolution_count": identity_resolutions,
            "lifecycle_event_count": lifecycle_events,
            "gap_resolution_count": inputs.gap_resolution_count,
            "alternate_observation_count": inputs.alternate_observation_count,
            "evidence": [
                {
                    "ordinal": ordinal,
                    "artifact_id": str(item.artifact_id),
                    "role": item.role,
                }
                for ordinal, item in enumerate(inputs.evidence)
            ],
            "findings": list(finding_documents),
            "uniform_exclusions": list(exclusion_documents),
        }
        fingerprint = sha256_hexdigest(document)
        assessment_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:dataset-gate-assessment:{fingerprint}"
        )
        fixed_dependencies = (
            DependencyInput(inputs.dataset["dataset_artifact_id"], "market_dataset", 0),
            DependencyInput(inputs.ledger["ledger_artifact_id"], "universe_ledger", 1),
            DependencyInput(inputs.ledger["history_artifact_id"], "universe_history", 2),
            DependencyInput(inputs.dataset["quality_report_artifact_id"], "quality_report", 3),
            DependencyInput(inputs.dataset["calendar_artifact_id"], "calendar_version", 4),
            DependencyInput(inputs.dataset["cleaning_artifact_id"], "cleaning_version", 5),
        )
        dependencies = fixed_dependencies + tuple(
            DependencyInput(item.artifact_id, "gate_evidence", ordinal + 6)
            for ordinal, item in enumerate(inputs.evidence)
        )

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO data.v022_dataset_gate_assessment (
                      dataset_gate_assessment_id,artifact_id,dataset_publication_id,
                      dataset_artifact_id,universe_membership_ledger_id,
                      universe_membership_ledger_artifact_id,universe_history_id,
                      universe_history_artifact_id,security_market_quality_report_id,
                      quality_report_artifact_id,calendar_version_id,calendar_artifact_id,
                      cleaning_version_id,cleaning_artifact_id,gate_key,version_number,
                      assessed_coverage_start,assessed_coverage_end,price_semantics,
                      historical_pit_claimed,ranking_eligibility,product_eligibility,
                      finding_count,warning_count,blocker_count,evidence_count,
                      uniform_exclusion_count,identity_resolution_count,
                      lifecycle_event_count,gap_resolution_count,alternate_observation_count,
                      assessment_document,assessment_fingerprint,created_by
                    ) VALUES (
                      :id,:artifact,:dataset,:dataset_artifact,:ledger,:ledger_artifact,
                      :history,:history_artifact,:report,:report_artifact,:calendar,
                      :calendar_artifact,:cleaning,:cleaning_artifact,:gate_key,:version,
                      :start,:end,:semantics,false,:ranking,:product,:findings,:warnings,
                      :blockers,:evidence_count,:exclusions,:identity_count,:lifecycle_count,
                      :gap_count,:alternate_count,CAST(:document AS jsonb),:fingerprint,
                      :created_by
                    )
                    """
                ),
                {
                    "id": assessment_id,
                    "artifact": artifact_id,
                    "dataset": spec.dataset_publication_id,
                    "dataset_artifact": inputs.dataset["dataset_artifact_id"],
                    "ledger": spec.universe_membership_ledger_id,
                    "ledger_artifact": inputs.ledger["ledger_artifact_id"],
                    "history": inputs.ledger["universe_history_id"],
                    "history_artifact": inputs.ledger["history_artifact_id"],
                    "report": inputs.dataset["quality_report_id"],
                    "report_artifact": inputs.dataset["quality_report_artifact_id"],
                    "calendar": inputs.dataset["calendar_version_id"],
                    "calendar_artifact": inputs.dataset["calendar_artifact_id"],
                    "cleaning": inputs.dataset["cleaning_version_id"],
                    "cleaning_artifact": inputs.dataset["cleaning_artifact_id"],
                    "gate_key": spec.gate_key,
                    "version": spec.version_number,
                    "start": spec.assessed_coverage_start,
                    "end": spec.assessed_coverage_end,
                    "semantics": inputs.dataset["price_semantics"],
                    "ranking": spec.ranking_eligibility,
                    "product": spec.product_eligibility,
                    "findings": len(spec.findings),
                    "warnings": warnings,
                    "blockers": blockers,
                    "evidence_count": len(inputs.evidence),
                    "exclusions": len(spec.uniform_exclusions),
                    "identity_count": identity_resolutions,
                    "lifecycle_count": lifecycle_events,
                    "gap_count": inputs.gap_resolution_count,
                    "alternate_count": inputs.alternate_observation_count,
                    "document": json.dumps(document, sort_keys=True),
                    "fingerprint": fingerprint,
                    "created_by": spec.created_by,
                },
            )
            _write_evidence(connection, assessment_id, inputs.evidence)
            _write_findings(connection, assessment_id, spec.findings, finding_documents)
            _write_exclusions(
                connection, assessment_id, spec.uniform_exclusions, exclusion_documents
            )

        publication = self._artifacts.publish(
            artifact_type="v022_dataset_gate_assessment",
            artifact_key=f"v022_dataset_gate_assessment__{spec.gate_key}",
            version_number=spec.version_number,
            semantic_payload=document,
            content_payload=document,
            dependencies=dependencies,
            reason=f"publish Dataset Gate Assessment {spec.gate_key}",
            draft_writer=writer,
        )
        return DatasetGateAssessmentPublication(
            assessment_id,
            publication.artifact_id,
            fingerprint,
            spec.ranking_eligibility,
            spec.product_eligibility,
            warnings,
            blockers,
            len(spec.uniform_exclusions),
            publication.reused,
        )

    def _load_inputs(self, spec: DatasetGateAssessmentSpec) -> _GateInputs:
        with self._engine.connect() as connection:
            dataset = _load_dataset(connection, spec.dataset_publication_id)
            ledger = _load_ledger(connection, spec.universe_membership_ledger_id)
            if (
                cast(date, dataset["coverage_start"]) > spec.assessed_coverage_start
                or cast(date, dataset["coverage_end"]) < spec.assessed_coverage_end
            ):
                raise ValueError("Dataset does not cover the assessed Gate interval")
            if (
                cast(date, ledger["coverage_start"]) > spec.assessed_coverage_start
                or cast(date, ledger["coverage_end"]) < spec.assessed_coverage_end
            ):
                raise ValueError("Universe Ledger does not cover the assessed Gate interval")
            system_evidence, gap_count, alternate_count, plan_artifact = (
                _reconciliation_evidence(connection, dataset)
            )
            supplied = _published_evidence(connection, spec.evidence)
        fixed_artifacts = {
            cast(uuid.UUID, dataset["dataset_artifact_id"]),
            cast(uuid.UUID, dataset["quality_report_artifact_id"]),
            cast(uuid.UUID, dataset["calendar_artifact_id"]),
            cast(uuid.UUID, dataset["cleaning_artifact_id"]),
            cast(uuid.UUID, ledger["ledger_artifact_id"]),
            cast(uuid.UUID, ledger["history_artifact_id"]),
        }
        if any(item.artifact_id in fixed_artifacts for item in supplied):
            raise ValueError("Fixed Gate inputs must not be duplicated as evidence")
        evidence = system_evidence + supplied
        if not evidence:
            raise ValueError("Dataset Gate Assessment requires at least one evidence Artifact")
        if len({item.artifact_id for item in evidence}) != len(evidence):
            raise ValueError("Dataset Gate evidence Artifacts must be unique")
        return _GateInputs(
            dataset,
            ledger,
            evidence,
            plan_artifact,
            gap_count,
            alternate_count,
        )


def _validate_gate_decisions(
    spec: DatasetGateAssessmentSpec, evidence: tuple[DatasetGateEvidenceRef, ...]
) -> None:
    expected_ranking: RankingEligibility = (
        "exploratory_only"
        if any(item.ranking_effect == "exploratory_only" for item in spec.findings)
        else "rankable_research"
    )
    expected_product: ProductEligibility
    if any(item.product_effect == "ineligible" for item in spec.findings):
        expected_product = "ineligible"
    elif any(item.product_effect == "warning" for item in spec.findings):
        expected_product = "eligible_with_warnings"
    else:
        expected_product = "eligible"
    if spec.ranking_eligibility != expected_ranking:
        raise ValueError("ranking_eligibility does not match frozen finding effects")
    if spec.product_eligibility != expected_product:
        raise ValueError("product_eligibility does not match frozen finding effects")
    finding_codes = {item.finding_code for item in spec.findings}
    if not _BASELINE_WARNING_CODES.issubset(finding_codes):
        raise ValueError("Free historical Dataset Gate requires both baseline warnings")
    for code in _BASELINE_WARNING_CODES:
        baseline = next(item for item in spec.findings if item.finding_code == code)
        if baseline.severity != "warning" or baseline.product_effect != "warning":
            raise ValueError("Baseline free-data findings must remain Product warnings")
    evidence_ids = {item.artifact_id for item in evidence}
    for finding in spec.findings:
        if (
            finding.evidence_artifact_id is not None
            and finding.evidence_artifact_id not in evidence_ids
        ):
            raise ValueError("Finding references evidence outside the frozen Gate evidence")
    for exclusion in spec.uniform_exclusions:
        if exclusion.evidence_artifact_id not in evidence_ids:
            raise ValueError("Uniform exclusion evidence is outside the frozen Gate evidence")
        if not any(
            finding.security_id == exclusion.security_id
            and finding.finding_category == "uniform_exclusion"
            and finding.ranking_effect == "none"
            and finding.product_effect == "warning"
            for finding in spec.findings
        ):
            raise ValueError("Uniform exclusion requires a matching non-ranking warning")
        if (
            exclusion.exclusion_start < spec.assessed_coverage_start
            or exclusion.exclusion_end > spec.assessed_coverage_end
        ):
            raise ValueError("Uniform exclusion is outside the assessed Gate interval")


def _load_dataset(connection: Connection, dataset_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
                SELECT publication.dataset_publication_id,
                       publication.artifact_id AS dataset_artifact_id,
                       publication.cleaning_version_id,
                       cleaning.artifact_id AS cleaning_artifact_id,
                       publication.calendar_version_id,
                       calendar.artifact_id AS calendar_artifact_id,
                       publication.coverage_start,publication.coverage_end,
                       source.price_semantics,
                       source.security_market_quality_report_id AS quality_report_id,
                       source.quality_report_artifact_id,
                       dataset_artifact.status AS dataset_status,
                       cleaning_artifact.status AS cleaning_status,
                       calendar_artifact.status AS calendar_status,
                       report_artifact.status AS report_status,
                       reconciled.market_reconciliation_plan_id,
                       reconciled.plan_artifact_id
                  FROM data.dataset_publication publication
                  JOIN lineage.artifact dataset_artifact
                    ON dataset_artifact.artifact_id=publication.artifact_id
                  JOIN data.cleaning_version cleaning
                    ON cleaning.cleaning_version_id=publication.cleaning_version_id
                  JOIN lineage.artifact cleaning_artifact
                    ON cleaning_artifact.artifact_id=cleaning.artifact_id
                  JOIN catalog.calendar_version calendar
                    ON calendar.calendar_version_id=publication.calendar_version_id
                  JOIN lineage.artifact calendar_artifact
                    ON calendar_artifact.artifact_id=calendar.artifact_id
                  JOIN data.v022_dataset_gate_source_binding source
                    ON source.dataset_publication_id=publication.dataset_publication_id
                  LEFT JOIN data.v022_reconciled_market_dataset_binding reconciled
                    ON reconciled.dataset_publication_id=publication.dataset_publication_id
                  JOIN lineage.artifact report_artifact
                    ON report_artifact.artifact_id=source.quality_report_artifact_id
                 WHERE publication.dataset_publication_id=:dataset
                   AND publication.dataset_kind='canonical'
                   AND publication.value_kind='daily_bar'
                """
            ),
            {"dataset": dataset_id},
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is None
        or row["dataset_status"] != "published"
        or row["cleaning_status"] != "published"
        or row["calendar_status"] != "published"
        or row["report_status"] != "published"
        or row["price_semantics"] is None
        or row["quality_report_id"] is None
    ):
        raise LookupError("Published canonical Dataset with exact quality lineage not found")
    return row


def _load_ledger(connection: Connection, ledger_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
                SELECT ledger.universe_membership_ledger_id,
                       ledger.artifact_id AS ledger_artifact_id,
                       ledger.coverage_start,ledger.coverage_end,
                       binding.universe_history_id,
                       binding.universe_history_artifact_id AS history_artifact_id,
                       ledger_artifact.status AS ledger_status,
                       history_artifact.status AS history_status
                  FROM catalog.v022_universe_membership_ledger ledger
                  JOIN lineage.artifact ledger_artifact
                    ON ledger_artifact.artifact_id=ledger.artifact_id
                  JOIN catalog.v022_universe_history_ledger_binding binding
                    ON binding.universe_membership_ledger_id=
                       ledger.universe_membership_ledger_id
                  JOIN lineage.artifact history_artifact
                    ON history_artifact.artifact_id=binding.universe_history_artifact_id
                 WHERE ledger.universe_membership_ledger_id=:ledger
                """
            ),
            {"ledger": ledger_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["ledger_status"] != "published" or row["history_status"] != "published":
        raise LookupError("Published Universe Ledger and History binding not found")
    return row


def _reconciliation_evidence(
    connection: Connection, dataset: RowMapping
) -> tuple[tuple[DatasetGateEvidenceRef, ...], int, int, uuid.UUID | None]:
    plan_id = cast(uuid.UUID | None, dataset["market_reconciliation_plan_id"])
    plan_artifact = cast(uuid.UUID | None, dataset["plan_artifact_id"])
    if plan_id is None or plan_artifact is None:
        return (), 0, 0, None
    rows = (
        connection.execute(
            text(
                """
                SELECT resolution.resolution_artifact_id,
                       gap.alternate_observation_set_id,
                       artifact.status
                  FROM data.v022_market_reconciliation_plan_resolution resolution
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=resolution.resolution_artifact_id
                  JOIN data.v022_market_gap_resolution gap
                    ON gap.artifact_id=resolution.resolution_artifact_id
                 WHERE resolution.market_reconciliation_plan_id=:plan
                 ORDER BY resolution.ordinal
                """
            ),
            {"plan": plan_id},
        )
        .mappings()
        .all()
    )
    if any(row["status"] != "published" for row in rows):
        raise ValueError("Reconciliation Plan contains unpublished Gap Resolution evidence")
    evidence = (DatasetGateEvidenceRef(plan_artifact, "reconciliation_plan"),) + tuple(
        DatasetGateEvidenceRef(cast(uuid.UUID, row["resolution_artifact_id"]), "gap_resolution")
        for row in rows
    )
    alternate_count = len(
        {
            cast(uuid.UUID, row["alternate_observation_set_id"])
            for row in rows
            if row["alternate_observation_set_id"] is not None
        }
    )
    return evidence, len(rows), alternate_count, plan_artifact


def _published_evidence(
    connection: Connection, evidence: tuple[DatasetGateEvidenceRef, ...]
) -> tuple[DatasetGateEvidenceRef, ...]:
    if not evidence:
        return ()
    rows = connection.execute(
        text(
            """
            SELECT artifact_id,artifact_type,status FROM lineage.artifact
             WHERE artifact_id=ANY(:ids)
            """
        ),
        {"ids": [item.artifact_id for item in evidence]},
    ).mappings()
    by_id = {cast(uuid.UUID, row["artifact_id"]): row for row in rows}
    expected_types = {
        "identity_resolution": "v022_security_identity_resolution",
        "lifecycle_event": "v022_security_lifecycle_event",
        "gap_resolution": "v022_market_gap_resolution",
        "reconciliation_plan": "v022_market_reconciliation_plan",
    }
    for item in evidence:
        row = by_id.get(item.artifact_id)
        if row is None or row["status"] != "published":
            raise LookupError("Dataset Gate evidence Artifact is not published")
        expected_type = expected_types.get(item.role)
        if expected_type is not None and row["artifact_type"] != expected_type:
            raise ValueError("Dataset Gate evidence role does not match Artifact type")
    return evidence


def _finding_document(ordinal: int, finding: DatasetGateFinding) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "finding_code": finding.finding_code,
        "finding_category": finding.finding_category,
        "severity": finding.severity,
        "ranking_effect": finding.ranking_effect,
        "product_effect": finding.product_effect,
        "security_id": str(finding.security_id) if finding.security_id else None,
        "evidence_artifact_id": (
            str(finding.evidence_artifact_id) if finding.evidence_artifact_id else None
        ),
        "details": finding.details,
    }


def _exclusion_document(
    ordinal: int, exclusion: DatasetGateUniformExclusion
) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "security_id": str(exclusion.security_id),
        "exclusion_start": exclusion.exclusion_start.isoformat(),
        "exclusion_end": exclusion.exclusion_end.isoformat(),
        "reason_code": exclusion.reason_code,
        "evidence_artifact_id": str(exclusion.evidence_artifact_id),
        "details": exclusion.details,
    }


def _write_evidence(
    connection: Connection,
    assessment_id: uuid.UUID,
    evidence: tuple[DatasetGateEvidenceRef, ...],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO data.v022_dataset_gate_assessment_evidence (
              dataset_gate_assessment_id,ordinal,evidence_artifact_id,evidence_role
            ) VALUES (:assessment,:ordinal,:artifact,:role)
            """
        ),
        [
            {
                "assessment": assessment_id,
                "ordinal": ordinal,
                "artifact": item.artifact_id,
                "role": item.role,
            }
            for ordinal, item in enumerate(evidence)
        ],
    )


def _write_findings(
    connection: Connection,
    assessment_id: uuid.UUID,
    findings: tuple[DatasetGateFinding, ...],
    documents: tuple[dict[str, object], ...],
) -> None:
    if not findings:
        return
    connection.execute(
        text(
            """
            INSERT INTO data.v022_dataset_gate_finding (
              dataset_gate_assessment_id,ordinal,finding_code,finding_category,severity,
              ranking_effect,product_effect,security_id,evidence_artifact_id,
              finding_document
            ) VALUES (:assessment,:ordinal,:code,:category,:severity,:ranking,:product,
                      :security,:evidence,CAST(:document AS jsonb))
            """
        ),
        [
            {
                "assessment": assessment_id,
                "ordinal": ordinal,
                "code": item.finding_code,
                "category": item.finding_category,
                "severity": item.severity,
                "ranking": item.ranking_effect,
                "product": item.product_effect,
                "security": item.security_id,
                "evidence": item.evidence_artifact_id,
                "document": json.dumps(documents[ordinal], sort_keys=True),
            }
            for ordinal, item in enumerate(findings)
        ],
    )


def _write_exclusions(
    connection: Connection,
    assessment_id: uuid.UUID,
    exclusions: tuple[DatasetGateUniformExclusion, ...],
    documents: tuple[dict[str, object], ...],
) -> None:
    if not exclusions:
        return
    connection.execute(
        text(
            """
            INSERT INTO data.v022_dataset_gate_uniform_exclusion (
              dataset_gate_assessment_id,ordinal,security_id,exclusion_start,
              exclusion_end,reason_code,evidence_artifact_id,exclusion_document
            ) VALUES (:assessment,:ordinal,:security,:start,:end,:reason,:evidence,
                      CAST(:document AS jsonb))
            """
        ),
        [
            {
                "assessment": assessment_id,
                "ordinal": ordinal,
                "security": item.security_id,
                "start": item.exclusion_start,
                "end": item.exclusion_end,
                "reason": item.reason_code,
                "evidence": item.evidence_artifact_id,
                "document": json.dumps(documents[ordinal], sort_keys=True),
            }
            for ordinal, item in enumerate(exclusions)
        ],
    )


def _require_text(label: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{label} is required")


def _json_object(value: dict[str, Any], label: str) -> None:
    try:
        encoded = json.loads(json.dumps(value, sort_keys=True))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be JSON-compatible") from error
    if not isinstance(encoded, dict):
        raise ValueError(f"{label} must be an object")
