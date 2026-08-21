from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import Engine, text

from style_rotation.v022.product_candidate import ProductCandidateService
from style_rotation.v022.product_data_disclosure import ProductDataDisclosureService
from style_rotation.v022.product_enrollment_command import (
    ProductEnrollmentCommand,
    ProductEnrollmentCommandService,
)
from style_rotation.v022.product_ensemble_state import ProductEnsembleStateService
from style_rotation.v022.product_runtime import DecisionSessionInput
from style_rotation.v022.retention_lock import v022_retention_guard

Frequency = Literal["weekly", "monthly"]


class ProductPromotionService:
    """Promote one exact Result Evidence and immediately begin OOS observation."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._candidates = ProductCandidateService(engine)
        self._disclosures = ProductDataDisclosureService(engine)
        self._enrollments = ProductEnrollmentCommandService(engine)
        self._ensemble_states = ProductEnsembleStateService(engine)

    def promote_and_enroll(
        self,
        *,
        result_evidence_snapshot_id: uuid.UUID,
        actor_key: str,
        idempotency_key: uuid.UUID,
        product_key: str,
        name: str,
        description: str,
        version_number: int,
    ) -> dict[str, Any]:
        with v022_retention_guard(self._engine):
            return self._promote_and_enroll_locked(
                result_evidence_snapshot_id=result_evidence_snapshot_id,
                actor_key=actor_key,
                idempotency_key=idempotency_key,
                product_key=product_key,
                name=name,
                description=description,
                version_number=version_number,
            )

    def _promote_and_enroll_locked(
        self,
        *,
        result_evidence_snapshot_id: uuid.UUID,
        actor_key: str,
        idempotency_key: uuid.UUID,
        product_key: str,
        name: str,
        description: str,
        version_number: int,
    ) -> dict[str, Any]:
        source = self._source(result_evidence_snapshot_id)
        data_source = self._disclosures.source(result_evidence_snapshot_id)
        candidate = self._candidates.promote(
            result_evidence_snapshot_id=result_evidence_snapshot_id,
            actor_key=actor_key,
            idempotency_key=idempotency_key,
            product_key=product_key,
            name=name,
            description=description,
            version_number=version_number,
        )
        disclosure = self._disclosures.publish(
            source=data_source,
            execution_version_id=uuid.UUID(str(candidate["execution_version_id"])),
            qualification_version_id=uuid.UUID(str(candidate["qualification_version_id"])),
            created_by=actor_key,
        )
        anchor = self._execution_artifact_created_at(
            uuid.UUID(str(candidate["execution_version_artifact_id"]))
        )
        frequency = cast(Frequency, source["frequency"])
        sessions = self._future_sessions(
            calendar_version_id=source["calendar_version_id"],
            frequency=frequency,
            anchor=anchor,
        )
        enrollment = self._enrollments.enroll(
            command=ProductEnrollmentCommand(
                execution_version_id=uuid.UUID(str(candidate["execution_version_id"])),
                qualification_version_id=uuid.UUID(
                    str(candidate["qualification_version_id"])
                ),
                monitoring_policy_version_id=uuid.UUID(
                    str(candidate["monitoring_policy_version_id"])
                ),
                schedule_key=f"{product_key}_{frequency}_oos",
                schedule_version_number=version_number,
                frequency=frequency,
                sessions=sessions,
                oos_anchor_cutoff_at=anchor,
                activation_effective_at=anchor,
            ),
            actor_key=actor_key,
            idempotency_key=idempotency_key,
        )
        ensemble_state = self._ensemble_states.publish_initial(
            execution_version_id=uuid.UUID(str(candidate["execution_version_id"])),
            result_evidence_snapshot_id=result_evidence_snapshot_id,
            activated_decision_session_id=uuid.UUID(
                str(enrollment["first_eligible_decision_session_id"])
            ),
        )
        return {
            **candidate,
            "product_data_disclosure_id": str(disclosure.product_data_disclosure_id),
            "product_data_disclosure_artifact_id": str(disclosure.artifact_id),
            "product_data_disclosure_fingerprint": disclosure.disclosure_fingerprint,
            "product_eligibility": disclosure.product_eligibility,
            "warning_codes": list(disclosure.warning_codes),
            **enrollment,
            "product_ensemble_state_id": (
                None
                if ensemble_state is None
                else str(ensemble_state.product_ensemble_state_id)
            ),
            "product_ensemble_state_artifact_id": (
                None if ensemble_state is None else str(ensemble_state.artifact_id)
            ),
            "product_ensemble_state_fingerprint": (
                None if ensemble_state is None else ensemble_state.state_fingerprint
            ),
            "lifecycle": "active",
            "reused": bool(
                candidate["reused"]
                and disclosure.reused
                and enrollment["reused"]
                and (ensemble_state is None or ensemble_state.reused)
            ),
        }

    def _source(self, evidence_id: uuid.UUID) -> Any:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT evidence.quality_document,cohort.research_tier,
                               cohort.frequency,cohort.calendar_version_id
                          FROM experiment.v022_result_evidence_snapshot evidence
                          JOIN lineage.artifact evidence_artifact
                            ON evidence_artifact.artifact_id=evidence.artifact_id
                           AND evidence_artifact.status='published'
                          JOIN experiment.v022_evaluation_cohort_version cohort
                            ON cohort.evaluation_cohort_version_id=
                               evidence.evaluation_cohort_version_id
                          JOIN lineage.artifact cohort_artifact
                            ON cohort_artifact.artifact_id=cohort.artifact_id
                           AND cohort_artifact.status='published'
                         WHERE evidence.result_evidence_snapshot_id=:evidence
                        """
                    ),
                    {"evidence": evidence_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError(f"Rankable v0.22 Result Evidence not found: {evidence_id}")
        quality = dict(row["quality_document"])
        if quality.get("state") != "passed" or quality.get("outcome") != "accepted":
            raise ValueError("Product promotion requires accepted, passed Result Evidence")
        return row

    def _execution_artifact_created_at(self, artifact_id: uuid.UUID) -> datetime:
        with self._engine.connect() as connection:
            created_at = connection.execute(
                text(
                    "SELECT created_at FROM lineage.artifact "
                    "WHERE artifact_id=:artifact AND status='published'"
                ),
                {"artifact": artifact_id},
            ).scalar_one_or_none()
        if created_at is None or created_at.tzinfo is None:
            raise ValueError("Product Execution Artifact has no valid publication timestamp")
        return cast(datetime, created_at)

    def _future_sessions(
        self,
        *,
        calendar_version_id: uuid.UUID,
        frequency: Frequency,
        anchor: datetime,
    ) -> tuple[DecisionSessionInput, ...]:
        with self._engine.connect() as connection:
            rows = tuple(
                connection.execute(
                    text(
                        """
                        WITH frozen_definition AS (
                          SELECT calendar_definition_id
                            FROM catalog.calendar_version
                           WHERE calendar_version_id=:calendar
                        ), prospective_calendar AS (
                          SELECT version.calendar_version_id
                            FROM catalog.calendar_version version
                            JOIN frozen_definition frozen
                              ON frozen.calendar_definition_id=
                                 version.calendar_definition_id
                            JOIN lineage.artifact artifact
                              ON artifact.artifact_id=version.artifact_id
                             AND artifact.status='published'
                           WHERE EXISTS (
                             SELECT 1 FROM catalog.calendar_session future_session
                              WHERE future_session.calendar_version_id=
                                    version.calendar_version_id
                                AND future_session.close_at_utc>:anchor
                           )
                           ORDER BY version.coverage_end DESC,version.version_number DESC,
                                    version.calendar_version_id
                           LIMIT 1
                        )
                        SELECT session.session_date,session.close_at_utc
                          FROM prospective_calendar prospective
                          JOIN catalog.calendar_session session
                            ON session.calendar_version_id=
                               prospective.calendar_version_id
                         WHERE session.close_at_utc>:anchor
                         ORDER BY session.session_date
                        """
                    ),
                    {"calendar": calendar_version_id, "anchor": anchor},
                )
            )
        sessions = _decision_sessions(rows, frequency=frequency)
        if not sessions:
            raise ValueError(
                "Frozen Evaluation Cohort calendar has no prospective Product session; "
                "publish a later Calendar Version before promotion"
            )
        return sessions


def _decision_sessions(
    rows: tuple[Any, ...], *, frequency: Frequency
) -> tuple[DecisionSessionInput, ...]:
    grouped: dict[tuple[int, int], DecisionSessionInput] = {}
    for row in rows:
        session_date = row[0]
        close_at_utc = row[1]
        if close_at_utc.tzinfo is None:
            raise ValueError("Product Decision Schedule cutoff must be timezone-aware")
        if frequency == "weekly":
            iso = session_date.isocalendar()
            key = (iso.year, iso.week)
        else:
            key = (session_date.year, session_date.month)
        grouped[key] = DecisionSessionInput(session_date, close_at_utc)
    return tuple(grouped[key] for key in sorted(grouped))
