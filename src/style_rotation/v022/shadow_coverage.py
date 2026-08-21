from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

ComparisonOutcome = Literal["matched", "different", "missing_v021"]


@dataclass(frozen=True, slots=True)
class ShadowObservation:
    decision_session_id: uuid.UUID
    decision_status: Literal["completed", "missing"]
    outcome: ComparisonOutcome
    explained_difference: bool


@dataclass(frozen=True, slots=True)
class ShadowCoverageInput:
    shadow_representative_id: uuid.UUID
    ordinal: int
    asset_context_key: str
    asset_context_class: Literal["etf", "large_cap", "other"]
    frequency: Literal["weekly", "monthly"]
    minimum_required_sessions: int
    eligible_session_ids: tuple[uuid.UUID, ...]
    observations: tuple[ShadowObservation, ...]


@dataclass(frozen=True, slots=True)
class ShadowCoverageStats:
    shadow_representative_id: uuid.UUID
    ordinal: int
    eligible_session_count: int
    comparison_count: int
    matched_count: int
    explained_difference_count: int
    unexplained_difference_count: int
    missing_v021_count: int
    missing_v022_count: int
    ready: bool
    blocker_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ShadowComparisonPublication:
    shadow_decision_comparison_id: uuid.UUID
    artifact_id: uuid.UUID
    comparison_fingerprint: str
    outcome: ComparisonOutcome
    reused: bool


@dataclass(frozen=True, slots=True)
class ShadowCoveragePublication:
    shadow_coverage_snapshot_id: uuid.UUID
    artifact_id: uuid.UUID
    coverage_fingerprint: str
    ready_for_default: bool
    blocker_codes: tuple[str, ...]
    member_stats: tuple[ShadowCoverageStats, ...]
    reused: bool


def evaluate_shadow_coverage(
    inputs: tuple[ShadowCoverageInput, ...],
) -> tuple[tuple[ShadowCoverageStats, ...], tuple[str, ...]]:
    if not inputs:
        raise ValueError("Shadow Coverage requires at least one Representative")
    stats: list[ShadowCoverageStats] = []
    for item in inputs:
        eligible = set(item.eligible_session_ids)
        observed_ids = [observation.decision_session_id for observation in item.observations]
        if len(observed_ids) != len(set(observed_ids)):
            raise ValueError("Shadow observations contain duplicate Decision Sessions")
        if not set(observed_ids).issubset(eligible):
            raise ValueError("Shadow observation is outside the Representative eligible Sessions")
        matched = sum(observation.outcome == "matched" for observation in item.observations)
        missing_v021 = sum(
            observation.outcome == "missing_v021" for observation in item.observations
        )
        explained = sum(
            observation.outcome == "different" and observation.explained_difference
            for observation in item.observations
        )
        unexplained = sum(
            observation.outcome == "different" and not observation.explained_difference
            for observation in item.observations
        )
        missing_v022 = sum(
            observation.decision_status == "missing" for observation in item.observations
        )
        blockers: list[str] = []
        if len(eligible) < item.minimum_required_sessions:
            blockers.append("insufficient_prospective_sessions")
        if len(observed_ids) < len(eligible):
            blockers.append("missing_shadow_comparisons")
        if missing_v021:
            blockers.append("missing_v021_reference")
        if missing_v022:
            blockers.append("missing_v022_decision")
        if unexplained:
            blockers.append("unexplained_shadow_difference")
        stats.append(
            ShadowCoverageStats(
                item.shadow_representative_id,
                item.ordinal,
                len(eligible),
                len(observed_ids),
                matched,
                explained,
                unexplained,
                missing_v021,
                missing_v022,
                not blockers,
                tuple(blockers),
            )
        )
    plan_blockers: list[str] = []
    classes = {item.asset_context_class for item in inputs}
    frequencies = {item.frequency for item in inputs}
    if "etf" not in classes:
        plan_blockers.append("shadow_plan_missing_etf")
    if "large_cap" not in classes:
        plan_blockers.append("shadow_plan_missing_large_cap")
    if "weekly" not in frequencies:
        plan_blockers.append("shadow_plan_missing_weekly")
    if "monthly" not in frequencies:
        plan_blockers.append("shadow_plan_missing_monthly")
    matrix: dict[str, set[str]] = {}
    for item in inputs:
        matrix.setdefault(item.asset_context_key, set()).add(item.frequency)
    if any(value != {"weekly", "monthly"} for value in matrix.values()):
        plan_blockers.append("shadow_plan_incomplete_frequency_matrix")
    if any(not item.ready for item in stats):
        plan_blockers.append("shadow_representative_not_ready")
    return tuple(stats), tuple(plan_blockers)


class ShadowComparisonService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        shadow_representative_id: uuid.UUID,
        v022_product_decision_id: uuid.UUID,
        comparator_artifact_id: uuid.UUID,
        outcome: ComparisonOutcome,
        comparison_document: dict[str, object],
        v021_reference_artifact_id: uuid.UUID | None = None,
        explanation_codes: tuple[str, ...] = (),
        known_at: datetime | None = None,
    ) -> ShadowComparisonPublication:
        if not comparison_document:
            raise ValueError("Shadow Comparison document must be nonempty")
        if outcome not in {"matched", "different", "missing_v021"}:
            raise ValueError(f"Unsupported Shadow Comparison outcome: {outcome}")
        if len(explanation_codes) != len(set(explanation_codes)):
            raise ValueError("Shadow Comparison explanation codes must be unique")
        explained = bool(explanation_codes)
        if (outcome == "missing_v021") != (v021_reference_artifact_id is None):
            raise ValueError(
                "missing_v021 must have no v0.21 reference; other outcomes require one"
            )
        if outcome == "matched" and explained:
            raise ValueError("Matched Shadow Comparison cannot carry difference explanations")
        row = self._identity(shadow_representative_id, v022_product_decision_id)
        comparator = self._published(comparator_artifact_id, "Comparator")
        reference = (
            self._published(v021_reference_artifact_id, "v0.21 reference")
            if v021_reference_artifact_id
            else None
        )
        if row["decision_status"] == "missing" and outcome == "matched":
            raise ValueError("A missing v0.22 Decision cannot be matched")
        occurred_at = known_at or datetime.now(UTC)
        if occurred_at.tzinfo is None:
            raise ValueError("Shadow Comparison known-at must be timezone-aware")
        semantic = {
            "contract_version": "v0.22.0",
            "shadow_representative_id": str(shadow_representative_id),
            "decision_session_id": str(row["decision_session_id"]),
            "v022_decision_fingerprint": row["decision_fingerprint"],
            "v021_reference_fingerprint": (
                reference["semantic_fingerprint"] if reference else None
            ),
            "comparator_fingerprint": comparator["semantic_fingerprint"],
            "outcome": outcome,
            "explained_difference": explained,
            "explanation_codes": list(explanation_codes),
            "comparison_document": comparison_document,
            "known_at": occurred_at,
        }
        fingerprint = sha256_hexdigest(semantic)
        comparison_id = uuid.uuid4()
        dependencies = [
            DependencyInput(row["plan_artifact_id"], "shadow_plan", 0),
            DependencyInput(row["decision_artifact_id"], "v022_product_decision", 1),
        ]
        if reference is not None:
            dependencies.append(DependencyInput(reference["artifact_id"], "v021_reference", 2))
        dependencies.append(
            DependencyInput(comparator["artifact_id"], "shadow_comparator", len(dependencies))
        )
        publication = self._artifacts.publish(
            artifact_type="v022_shadow_decision_comparison",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=tuple(dependencies),
            reason="publish exact-session v0.21/v0.22 Shadow Comparison",
            draft_writer=partial(
                self._write,
                comparison_id=comparison_id,
                representative_id=shadow_representative_id,
                decision_id=v022_product_decision_id,
                decision_session_id=row["decision_session_id"],
                reference_id=v021_reference_artifact_id,
                comparator_id=comparator_artifact_id,
                outcome=outcome,
                explained=explained,
                explanation_codes=explanation_codes,
                comparison_document=comparison_document,
                known_at=occurred_at,
                fingerprint=fingerprint,
            ),
        )
        with self._engine.connect() as connection:
            frozen = connection.execute(
                text(
                    "SELECT * FROM workspace.v022_shadow_decision_comparison "
                    "WHERE artifact_id=:artifact"
                ),
                {"artifact": publication.artifact_id},
            ).mappings().one()
        return ShadowComparisonPublication(
            frozen["shadow_decision_comparison_id"],
            frozen["artifact_id"],
            frozen["comparison_fingerprint"],
            frozen["outcome"],
            publication.reused,
        )

    def _identity(self, representative_id: uuid.UUID, decision_id: uuid.UUID) -> RowMapping:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT representative.product_enrollment_id,
                           representative.execution_version_id,
                           plan.artifact_id AS plan_artifact_id,
                           decision.artifact_id AS decision_artifact_id,
                           decision.product_enrollment_id AS decision_enrollment_id,
                           decision.execution_version_id AS decision_execution_id,
                           decision.decision_session_id,decision.decision_status,
                           decision.decision_fingerprint,decision.oos_eligible
                      FROM workspace.v022_shadow_representative representative
                      JOIN workspace.v022_shadow_plan plan
                        ON plan.shadow_plan_id=representative.shadow_plan_id
                      JOIN lineage.artifact plan_artifact
                        ON plan_artifact.artifact_id=plan.artifact_id
                       AND plan_artifact.status='published'
                      JOIN product.v022_product_decision decision
                        ON decision.product_decision_id=:decision
                      JOIN lineage.artifact decision_artifact
                        ON decision_artifact.artifact_id=decision.artifact_id
                       AND decision_artifact.status='published'
                     WHERE representative.shadow_representative_id=:representative
                    """
                ),
                {"representative": representative_id, "decision": decision_id},
            ).mappings().one_or_none()
        if row is None:
            raise ValueError("Shadow Comparison requires published Plan and Decision")
        if (
            row["product_enrollment_id"] != row["decision_enrollment_id"]
            or row["execution_version_id"] != row["decision_execution_id"]
            or not row["oos_eligible"]
        ):
            raise ValueError("Shadow Comparison Decision is not exact eligible Representative OOS")
        return row

    def _published(self, artifact_id: uuid.UUID, label: str) -> RowMapping:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT artifact_id,semantic_fingerprint FROM lineage.artifact "
                    "WHERE artifact_id=:artifact AND status='published'"
                ),
                {"artifact": artifact_id},
            ).mappings().one_or_none()
        if row is None:
            raise ValueError(f"Shadow Comparison {label} Artifact is not published")
        return row

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        comparison_id: uuid.UUID,
        representative_id: uuid.UUID,
        decision_id: uuid.UUID,
        decision_session_id: uuid.UUID,
        reference_id: uuid.UUID | None,
        comparator_id: uuid.UUID,
        outcome: ComparisonOutcome,
        explained: bool,
        explanation_codes: tuple[str, ...],
        comparison_document: dict[str, object],
        known_at: datetime,
        fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_shadow_decision_comparison (
                  shadow_decision_comparison_id,artifact_id,shadow_representative_id,
                  decision_session_id,v022_product_decision_id,v021_reference_artifact_id,
                  comparator_artifact_id,outcome,explained_difference,explanation_codes,
                  comparison_document,known_at,comparison_fingerprint
                ) VALUES (
                  :id,:artifact,:representative,:session,:decision,:reference,:comparator,:outcome,
                  :explained,CAST(:codes AS jsonb),CAST(:document AS jsonb),:known_at,:fingerprint
                )
                """
            ),
            {
                "id": comparison_id,
                "artifact": artifact_id,
                "representative": representative_id,
                "session": decision_session_id,
                "decision": decision_id,
                "reference": reference_id,
                "comparator": comparator_id,
                "outcome": outcome,
                "explained": explained,
                "codes": json.dumps(explanation_codes),
                "document": json.dumps(comparison_document, sort_keys=True),
                "known_at": known_at,
                "fingerprint": fingerprint,
            },
        )


class ShadowCoverageService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        shadow_plan_id: uuid.UUID,
        comparator_artifact_id: uuid.UUID,
        known_at: datetime,
    ) -> ShadowCoveragePublication:
        if known_at.tzinfo is None:
            raise ValueError("Shadow Coverage known-at must be timezone-aware")
        plan, inputs, comparison_artifacts = self._inputs(
            shadow_plan_id, comparator_artifact_id, known_at
        )
        stats, blockers = evaluate_shadow_coverage(inputs)
        ready_count = sum(item.ready for item in stats)
        ready = ready_count == len(stats) and not blockers
        coverage_document = {
            "contract_version": "v0.22.0",
            "plan_fingerprint": plan["plan_fingerprint"],
            "known_at": known_at,
            "members": [asdict(item) for item in stats],
            "plan_blockers": list(blockers),
        }
        fingerprint = sha256_hexdigest(coverage_document)
        snapshot_id = uuid.uuid4()
        dependencies = [
            DependencyInput(plan["artifact_id"], "shadow_plan", 0),
            DependencyInput(comparator_artifact_id, "shadow_comparator", 1),
        ]
        dependencies.extend(
            DependencyInput(artifact_id, "shadow_comparison", ordinal)
            for ordinal, artifact_id in enumerate(comparison_artifacts, start=2)
        )
        publication = self._artifacts.publish(
            artifact_type="v022_shadow_coverage_evidence",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=coverage_document,
            content_payload=coverage_document,
            dependencies=tuple(dependencies),
            reason="publish per-Representative v0.22 Shadow Coverage Snapshot",
            draft_writer=partial(
                self._write,
                snapshot_id=snapshot_id,
                plan_id=shadow_plan_id,
                comparator_id=comparator_artifact_id,
                known_at=known_at,
                ready=ready,
                stats=stats,
                blockers=blockers,
                document=coverage_document,
                fingerprint=fingerprint,
            ),
        )
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM workspace.v022_shadow_coverage_snapshot "
                    "WHERE artifact_id=:artifact"
                ),
                {"artifact": publication.artifact_id},
            ).mappings().one()
        return ShadowCoveragePublication(
            row["shadow_coverage_snapshot_id"],
            row["artifact_id"],
            row["coverage_fingerprint"],
            row["ready_for_default"],
            tuple(row["blocker_codes"]),
            stats,
            publication.reused,
        )

    def _inputs(
        self, plan_id: uuid.UUID, comparator_id: uuid.UUID, known_at: datetime
    ) -> tuple[RowMapping, tuple[ShadowCoverageInput, ...], tuple[uuid.UUID, ...]]:
        with self._engine.connect() as connection:
            plan = connection.execute(
                text(
                    "SELECT plan.* FROM workspace.v022_shadow_plan plan "
                    "JOIN lineage.artifact artifact ON artifact.artifact_id=plan.artifact_id "
                    "WHERE plan.shadow_plan_id=:plan AND artifact.status='published'"
                ),
                {"plan": plan_id},
            ).mappings().one_or_none()
            comparator_exists = connection.scalar(
                text(
                    "SELECT count(*) FROM lineage.artifact "
                    "WHERE artifact_id=:artifact AND status='published'"
                ),
                {"artifact": comparator_id},
            )
            if plan is None or comparator_exists != 1:
                raise ValueError("Shadow Coverage requires published Plan and Comparator")
            representatives = tuple(
                connection.execute(
                    text(
                        "SELECT * FROM workspace.v022_shadow_representative "
                        "WHERE shadow_plan_id=:plan ORDER BY ordinal"
                    ),
                    {"plan": plan_id},
                ).mappings()
            )
            inputs: list[ShadowCoverageInput] = []
            artifact_ids: list[uuid.UUID] = []
            for representative in representatives:
                sessions = tuple(
                    connection.execute(
                        text(
                            """
                            SELECT session.decision_session_id
                              FROM product.v022_product_enrollment enrollment
                              JOIN product.v022_decision_schedule_session first_session
                                ON first_session.decision_session_id=
                                   enrollment.first_eligible_decision_session_id
                              JOIN product.v022_decision_schedule_session session
                                ON session.decision_schedule_version_id=
                                   enrollment.decision_schedule_version_id
                               AND session.ordinal>=first_session.ordinal
                               AND session.decision_cutoff_at<=:known_at
                             WHERE enrollment.product_enrollment_id=:enrollment
                             ORDER BY session.ordinal
                            """
                        ),
                        {
                            "enrollment": representative["product_enrollment_id"],
                            "known_at": known_at,
                        },
                    ).scalars()
                )
                comparisons = tuple(
                    connection.execute(
                        text(
                            """
                            SELECT comparison.artifact_id,comparison.decision_session_id,
                                   comparison.outcome,comparison.explained_difference,
                                   decision.decision_status
                              FROM workspace.v022_shadow_decision_comparison comparison
                              JOIN lineage.artifact artifact
                                ON artifact.artifact_id=comparison.artifact_id
                               AND artifact.status='published'
                              JOIN product.v022_product_decision decision
                                ON decision.product_decision_id=comparison.v022_product_decision_id
                             WHERE comparison.shadow_representative_id=:representative
                               AND comparison.comparator_artifact_id=:comparator
                               AND comparison.known_at<=:known_at
                             ORDER BY comparison.known_at,comparison.decision_session_id
                            """
                        ),
                        {
                            "representative": representative["shadow_representative_id"],
                            "comparator": comparator_id,
                            "known_at": known_at,
                        },
                    ).mappings()
                )
                artifact_ids.extend(item["artifact_id"] for item in comparisons)
                inputs.append(
                    ShadowCoverageInput(
                        representative["shadow_representative_id"],
                        representative["ordinal"],
                        representative["asset_context_key"],
                        representative["asset_context_class"],
                        representative["frequency"],
                        representative["minimum_required_sessions"],
                        sessions,
                        tuple(
                            ShadowObservation(
                                item["decision_session_id"],
                                cast(Literal["completed", "missing"], item["decision_status"]),
                                cast(ComparisonOutcome, item["outcome"]),
                                item["explained_difference"],
                            )
                            for item in comparisons
                        ),
                    )
                )
        return plan, tuple(inputs), tuple(artifact_ids)

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        snapshot_id: uuid.UUID,
        plan_id: uuid.UUID,
        comparator_id: uuid.UUID,
        known_at: datetime,
        ready: bool,
        stats: tuple[ShadowCoverageStats, ...],
        blockers: tuple[str, ...],
        document: dict[str, object],
        fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_shadow_coverage_snapshot (
                  shadow_coverage_snapshot_id,artifact_id,shadow_plan_id,comparator_artifact_id,
                  known_at,ready_for_default,representative_count,ready_representative_count,
                  blocker_codes,coverage_document,coverage_fingerprint
                ) VALUES (
                  :id,:artifact,:plan,:comparator,:known_at,:ready,:count,:ready_count,
                  CAST(:blockers AS jsonb),CAST(:document AS jsonb),:fingerprint
                )
                """
            ),
            {
                "id": snapshot_id,
                "artifact": artifact_id,
                "plan": plan_id,
                "comparator": comparator_id,
                "known_at": known_at,
                "ready": ready,
                "count": len(stats),
                "ready_count": sum(item.ready for item in stats),
                "blockers": json.dumps(blockers),
                "document": json.dumps(document, sort_keys=True, default=str),
                "fingerprint": fingerprint,
            },
        )
        for item in stats:
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_shadow_coverage_member (
                      shadow_coverage_snapshot_id,ordinal,shadow_representative_id,
                      eligible_session_count,comparison_count,matched_count,
                      explained_difference_count,unexplained_difference_count,
                      missing_v021_count,missing_v022_count,ready,blocker_codes
                    ) VALUES (
                      :snapshot,:ordinal,:representative,:eligible,:comparisons,:matched,
                      :explained,:unexplained,:missing_v021,:missing_v022,:ready,
                      CAST(:blockers AS jsonb)
                    )
                    """
                ),
                {
                    "snapshot": snapshot_id,
                    "ordinal": item.ordinal,
                    "representative": item.shadow_representative_id,
                    "eligible": item.eligible_session_count,
                    "comparisons": item.comparison_count,
                    "matched": item.matched_count,
                    "explained": item.explained_difference_count,
                    "unexplained": item.unexplained_difference_count,
                    "missing_v021": item.missing_v021_count,
                    "missing_v022": item.missing_v022_count,
                    "ready": item.ready,
                    "blockers": json.dumps(item.blocker_codes),
                },
            )
