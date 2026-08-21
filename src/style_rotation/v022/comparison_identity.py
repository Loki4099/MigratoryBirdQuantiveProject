# ruff: noqa: E501
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

ComparisonScope = Literal["predictive", "portfolio", "replication_audit"]
ComparisonClassification = Literal["replication", "controlled", "multi_axis", "incompatible"]
BaselineKind = Literal["defense_none", "deterministic_aggregation"]

TREATMENT_DIMENSIONS = (
    "aggregation_algorithm",
    "aggregation_direct_inputs",
    "aggregation_training_target",
    "aggregation_hyperparameters",
    "strategy_selection",
    "defense_package",
)


@dataclass(frozen=True, slots=True)
class ComparisonPublication:
    result_comparison_id: uuid.UUID
    artifact_id: uuid.UUID
    comparison_fingerprint: str
    comparison_scope: ComparisonScope
    classification: ComparisonClassification
    changed_dimensions: tuple[str, ...]
    reused: bool


@dataclass(frozen=True, slots=True)
class BaselineAssessmentPublication:
    matched_baseline_assessment_id: uuid.UUID
    artifact_id: uuid.UUID
    assessment_fingerprint: str
    status: Literal["matched", "missing"]
    baseline_result_evidence_snapshot_id: uuid.UUID | None
    reused: bool


class ResultComparisonService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        *,
        left_result_evidence_snapshot_id: uuid.UUID,
        right_result_evidence_snapshot_id: uuid.UUID,
        comparison_scope: ComparisonScope,
    ) -> ComparisonPublication:
        if left_result_evidence_snapshot_id == right_result_evidence_snapshot_id:
            raise ValueError("Comparison requires two different Result Evidence Snapshots")
        if comparison_scope not in {"predictive", "portfolio", "replication_audit"}:
            raise ValueError(f"Unsupported Comparison Scope: {comparison_scope}")
        ordered_ids = tuple(
            sorted(
                (left_result_evidence_snapshot_id, right_result_evidence_snapshot_id),
                key=str,
            )
        )
        with self._engine.connect() as connection:
            left = _evidence_identity(connection, ordered_ids[0])
            right = _evidence_identity(connection, ordered_ids[1])
        left_context = _protected_context(left, comparison_scope)
        right_context = _protected_context(right, comparison_scope)
        left_context_fingerprint = sha256_hexdigest(left_context)
        right_context_fingerprint = sha256_hexdigest(right_context)
        left_treatments = _treatments(cast(dict[str, Any], left["semantic_identity_document"]))
        right_treatments = _treatments(cast(dict[str, Any], right["semantic_identity_document"]))
        changed = tuple(
            dimension
            for dimension in TREATMENT_DIMENSIONS
            if left_treatments[dimension] != right_treatments[dimension]
        )
        classification = _classify_comparison(
            left_context_fingerprint=left_context_fingerprint,
            right_context_fingerprint=right_context_fingerprint,
            left_configuration_fingerprint=left["configuration_fingerprint"],
            right_configuration_fingerprint=right["configuration_fingerprint"],
            changed_dimensions=changed,
        )
        document = {
            "contract_version": "v0.22.0",
            "comparison_scope": comparison_scope,
            "classification": classification,
            "left_result_evidence_fingerprint": left["evidence_fingerprint"],
            "right_result_evidence_fingerprint": right["evidence_fingerprint"],
            "left_protected_context_fingerprint": left_context_fingerprint,
            "right_protected_context_fingerprint": right_context_fingerprint,
            "changed_dimensions": list(changed),
        }
        fingerprint = sha256_hexdigest(document)
        existing = self._existing(fingerprint)
        if existing is not None:
            return existing
        comparison_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:comparison:{fingerprint}")
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_result_comparison",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=document,
            content_payload=document,
            dependencies=(
                DependencyInput(left["artifact_id"], "left_evidence", 0),
                DependencyInput(right["artifact_id"], "right_evidence", 0),
            ),
            reason="publish immutable v0.22 Result Comparison",
            draft_writer=partial(
                self._write,
                comparison_id=comparison_id,
                left_id=ordered_ids[0],
                right_id=ordered_ids[1],
                scope=comparison_scope,
                classification=classification,
                left_context_fingerprint=left_context_fingerprint,
                right_context_fingerprint=right_context_fingerprint,
                changed=changed,
                fingerprint=fingerprint,
                document=document,
            ),
        )
        if publication.reused:
            reused = self._existing(fingerprint)
            if reused is None:
                raise ValueError("Reused Comparison Artifact has no Comparison row")
            return reused
        return ComparisonPublication(
            comparison_id,
            publication.artifact_id,
            fingerprint,
            comparison_scope,
            classification,
            changed,
            False,
        )

    def _existing(self, fingerprint: str) -> ComparisonPublication | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT comparison.*,artifact.status FROM experiment.v022_result_comparison comparison "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id=comparison.artifact_id "
                        "WHERE comparison.comparison_fingerprint=:fingerprint"
                    ),
                    {"fingerprint": fingerprint},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("Comparison Artifact is not published")
        return ComparisonPublication(
            row["result_comparison_id"],
            row["artifact_id"],
            row["comparison_fingerprint"],
            cast(ComparisonScope, row["comparison_scope"]),
            cast(ComparisonClassification, row["classification"]),
            tuple(row["changed_dimensions"]),
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        comparison_id: uuid.UUID,
        left_id: uuid.UUID,
        right_id: uuid.UUID,
        scope: ComparisonScope,
        classification: ComparisonClassification,
        left_context_fingerprint: str,
        right_context_fingerprint: str,
        changed: tuple[str, ...],
        fingerprint: str,
        document: dict[str, Any],
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO experiment.v022_result_comparison (
                  result_comparison_id,artifact_id,left_result_evidence_snapshot_id,
                  right_result_evidence_snapshot_id,comparison_scope,classification,
                  left_protected_context_fingerprint,right_protected_context_fingerprint,
                  changed_dimensions,comparison_fingerprint,comparison_document
                ) VALUES (:id,:artifact,:left,:right,:scope,:classification,:left_context,
                          :right_context,CAST(:changed AS jsonb),:fingerprint,CAST(:document AS jsonb))
                """
            ),
            {
                "id": comparison_id,
                "artifact": artifact_id,
                "left": left_id,
                "right": right_id,
                "scope": scope,
                "classification": classification,
                "left_context": left_context_fingerprint,
                "right_context": right_context_fingerprint,
                "changed": json.dumps(changed),
                "fingerprint": fingerprint,
                "document": json.dumps(document, sort_keys=True),
            },
        )


class MatchedBaselineService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        *,
        subject_result_evidence_snapshot_id: uuid.UUID,
        baseline_kind: BaselineKind,
        assessment_version: int,
        result_comparison_id: uuid.UUID | None = None,
        reason_codes: tuple[str, ...] = (),
    ) -> BaselineAssessmentPublication:
        if baseline_kind not in {"defense_none", "deterministic_aggregation"}:
            raise ValueError(f"Unsupported Baseline Kind: {baseline_kind}")
        if assessment_version < 1:
            raise ValueError("Baseline assessment_version must be positive")
        if any(not code.strip() for code in reason_codes) or len(reason_codes) != len(
            set(reason_codes)
        ):
            raise ValueError("Baseline reason_codes must be nonblank and unique")
        with self._engine.connect() as connection:
            subject = _evidence_identity(connection, subject_result_evidence_snapshot_id)
            comparison = (
                _comparison_identity(connection, result_comparison_id)
                if result_comparison_id is not None
                else None
            )
        status: Literal["matched", "missing"] = "matched" if comparison else "missing"
        baseline_id: uuid.UUID | None = None
        if comparison is not None:
            pair = {
                comparison["left_result_evidence_snapshot_id"],
                comparison["right_result_evidence_snapshot_id"],
            }
            if subject_result_evidence_snapshot_id not in pair:
                raise ValueError("Baseline Comparison does not contain the subject Result")
            baseline_id = next(item for item in pair if item != subject_result_evidence_snapshot_id)
            with self._engine.connect() as connection:
                baseline = _evidence_identity(connection, baseline_id)
            expected = (
                "defense_package" if baseline_kind == "defense_none" else "aggregation_algorithm"
            )
            if comparison["classification"] != "controlled" or comparison["changed_dimensions"] != [
                expected
            ]:
                raise ValueError("Baseline Comparison changes the wrong treatment dimension")
            subject_treatments = _treatments(
                cast(dict[str, Any], subject["semantic_identity_document"])
            )
            baseline_treatments = _treatments(
                cast(dict[str, Any], baseline["semantic_identity_document"])
            )
            if baseline_kind == "defense_none" and not (
                subject_treatments["defense_package"] is not None
                and baseline_treatments["defense_package"] is None
            ):
                raise ValueError("Defense-none baseline must point from Defense to none")
            if baseline_kind == "deterministic_aggregation" and not (
                subject_treatments["aggregation_algorithm"]["execution_mode"] != "deterministic"
                and baseline_treatments["aggregation_algorithm"]["execution_mode"]
                == "deterministic"
            ):
                raise ValueError(
                    "Deterministic Aggregation baseline must point from trainable to deterministic"
                )
            if reason_codes:
                raise ValueError("Matched baseline cannot carry missing reason codes")
        elif not reason_codes:
            raise ValueError("Missing baseline requires explicit reason_codes")
        semantic = {
            "contract_version": "v0.22.0",
            "subject_evidence_fingerprint": subject["evidence_fingerprint"],
            "baseline_kind": baseline_kind,
            "assessment_version": assessment_version,
            "status": status,
            "comparison_fingerprint": (
                comparison["comparison_fingerprint"] if comparison is not None else None
            ),
            "baseline_result_evidence_snapshot_id": str(baseline_id) if baseline_id else None,
            "reason_codes": list(reason_codes),
        }
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(
            subject_result_evidence_snapshot_id,
            baseline_kind,
            assessment_version,
        )
        if existing is not None:
            if existing.assessment_fingerprint != fingerprint:
                raise ValueError(
                    "Baseline assessment version is already bound to different evidence"
                )
            return existing
        assessment_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bird:v0.22:matched-baseline:{fingerprint}",
        )
        dependencies = [DependencyInput(subject["artifact_id"], "subject_evidence", 0)]
        if comparison is not None:
            dependencies.append(DependencyInput(comparison["artifact_id"], "comparison", 0))
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_matched_baseline_assessment",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=tuple(dependencies),
            reason="publish immutable v0.22 matched baseline assessment",
            draft_writer=partial(
                self._write,
                assessment_id=assessment_id,
                subject_id=subject_result_evidence_snapshot_id,
                baseline_id=baseline_id,
                comparison_id=result_comparison_id,
                baseline_kind=baseline_kind,
                assessment_version=assessment_version,
                status=status,
                reason_codes=reason_codes,
                fingerprint=fingerprint,
            ),
        )
        if publication.reused:
            reused = self._existing(
                subject_result_evidence_snapshot_id,
                baseline_kind,
                assessment_version,
            )
            if reused is None:
                raise ValueError("Reused Baseline Artifact has no assessment row")
            return reused
        return BaselineAssessmentPublication(
            assessment_id,
            publication.artifact_id,
            fingerprint,
            status,
            baseline_id,
            False,
        )

    def _existing(
        self,
        subject_id: uuid.UUID,
        baseline_kind: BaselineKind,
        assessment_version: int,
    ) -> BaselineAssessmentPublication | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT assessment.*,artifact.status AS artifact_status FROM "
                        "experiment.v022_matched_baseline_assessment assessment "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id=assessment.artifact_id "
                        "WHERE assessment.subject_result_evidence_snapshot_id=:subject "
                        "AND assessment.baseline_kind=:kind AND assessment.assessment_version=:version"
                    ),
                    {"subject": subject_id, "kind": baseline_kind, "version": assessment_version},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if row["artifact_status"] != "published":
            raise ValueError("Matched Baseline Assessment Artifact is not published")
        return BaselineAssessmentPublication(
            row["matched_baseline_assessment_id"],
            row["artifact_id"],
            row["assessment_fingerprint"],
            row["status"],
            row["baseline_result_evidence_snapshot_id"],
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        assessment_id: uuid.UUID,
        subject_id: uuid.UUID,
        baseline_id: uuid.UUID | None,
        comparison_id: uuid.UUID | None,
        baseline_kind: BaselineKind,
        assessment_version: int,
        status: Literal["matched", "missing"],
        reason_codes: tuple[str, ...],
        fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO experiment.v022_matched_baseline_assessment (
                  matched_baseline_assessment_id,artifact_id,
                  subject_result_evidence_snapshot_id,baseline_result_evidence_snapshot_id,
                  result_comparison_id,baseline_kind,assessment_version,status,reason_codes,
                  assessment_fingerprint
                ) VALUES (:id,:artifact,:subject,:baseline,:comparison,:kind,:version,:status,
                          CAST(:reasons AS jsonb),:fingerprint)
                """
            ),
            {
                "id": assessment_id,
                "artifact": artifact_id,
                "subject": subject_id,
                "baseline": baseline_id,
                "comparison": comparison_id,
                "kind": baseline_kind,
                "version": assessment_version,
                "status": status,
                "reasons": json.dumps(reason_codes),
                "fingerprint": fingerprint,
            },
        )


def _evidence_identity(connection: Connection, evidence_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
                SELECT evidence.*,artifact.status,configuration.configuration_fingerprint,
                       configuration.semantic_identity_document,
                       panel.panel_fingerprint
                  FROM experiment.v022_result_evidence_snapshot evidence
                  JOIN lineage.artifact artifact ON artifact.artifact_id=evidence.artifact_id
                  JOIN experiment.v022_research_configuration_snapshot configuration
                    ON configuration.configuration_snapshot_id=evidence.configuration_snapshot_id
                  LEFT JOIN experiment.v022_common_evaluation_panel panel
                    ON panel.common_evaluation_panel_id=evidence.common_evaluation_panel_id
                 WHERE evidence.result_evidence_snapshot_id=:evidence
                """
            ),
            {"evidence": evidence_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"Result Evidence Snapshot not found: {evidence_id}")
    if row["status"] != "published":
        raise ValueError("Comparison requires published Result Evidence")
    return row


def _comparison_identity(connection: Connection, comparison_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT comparison.*,artifact.status AS artifact_status FROM "
                "experiment.v022_result_comparison comparison "
                "JOIN lineage.artifact artifact ON artifact.artifact_id=comparison.artifact_id "
                "WHERE comparison.result_comparison_id=:comparison"
            ),
            {"comparison": comparison_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"Result Comparison not found: {comparison_id}")
    if row["artifact_status"] != "published":
        raise ValueError("Matched baseline requires a published Comparison")
    return row


def _protected_context(evidence: RowMapping, scope: ComparisonScope) -> dict[str, Any]:
    document = cast(dict[str, Any], evidence["evidence_document"])
    contexts = document.get("comparison_contexts")
    if not isinstance(contexts, dict) or not isinstance(contexts.get(scope), dict):
        raise ValueError(f"Result Evidence lacks protected context for scope: {scope}")
    return {
        "scope": scope,
        "evidence_class": evidence["evidence_class"],
        "common_evaluation_panel_fingerprint": evidence["panel_fingerprint"],
        "context": contexts[scope],
    }


def _treatments(configuration: dict[str, Any]) -> dict[str, Any]:
    aggregation = cast(dict[str, Any], configuration["aggregation"])
    strategy = cast(dict[str, Any], configuration["strategy"])
    return {
        "aggregation_algorithm": {
            "family_key": aggregation["family_key"],
            "version_id": aggregation["version_id"],
            "execution_mode": aggregation["execution_mode"],
        },
        "aggregation_direct_inputs": configuration["direct_inputs"],
        "aggregation_training_target": aggregation["target_version_id"],
        "aggregation_hyperparameters": {
            "parameter_preset_version_id": aggregation["parameter_preset_version_id"],
            "training_preset_version_id": aggregation["training_preset_version_id"],
        },
        "strategy_selection": strategy,
        "defense_package": configuration["defense"],
    }


def _classify_comparison(
    *,
    left_context_fingerprint: str,
    right_context_fingerprint: str,
    left_configuration_fingerprint: str,
    right_configuration_fingerprint: str,
    changed_dimensions: tuple[str, ...],
) -> ComparisonClassification:
    if left_context_fingerprint != right_context_fingerprint:
        return "incompatible"
    if left_configuration_fingerprint == right_configuration_fingerprint:
        return "replication"
    if len(changed_dimensions) == 1:
        return "controlled"
    if len(changed_dimensions) > 1:
        return "multi_axis"
    return "incompatible"
