from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text

from style_rotation.v022.product_input_snapshot import (
    ProductInputSnapshotPublication,
    ProductInputSnapshotService,
    ProductInputSnapshotSpec,
)


@dataclass(frozen=True, slots=True)
class PendingProductInput:
    product_enrollment_id: uuid.UUID
    execution_version_id: uuid.UUID
    decision_session_id: uuid.UUID
    session_date: str
    decision_cutoff_at: datetime
    baseline_dataset_gate_assessment_id: uuid.UUID
    candidate_dataset_gate_assessment_id: uuid.UUID | None
    product_input_snapshot_id: uuid.UUID | None
    input_state: str


class ProductInputRefreshService:
    """Prepare exact offline Product inputs before the Decision worker runs."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._snapshots = ProductInputSnapshotService(engine)

    def prepare(
        self,
        *,
        product_enrollment_id: uuid.UUID,
        decision_session_id: uuid.UUID,
        dataset_gate_assessment_id: uuid.UUID,
        actor_key: str,
    ) -> ProductInputSnapshotPublication:
        return self._snapshots.publish(
            ProductInputSnapshotSpec(
                product_enrollment_id=product_enrollment_id,
                decision_session_id=decision_session_id,
                dataset_gate_assessment_id=dataset_gate_assessment_id,
                created_by=actor_key,
            )
        )

    def prepare_pending(
        self,
        *,
        observed_at: datetime,
        actor_key: str,
        limit: int = 50,
    ) -> tuple[ProductInputSnapshotPublication, ...]:
        """Publish every due input for which one exact eligible Gate is available."""
        publications: list[ProductInputSnapshotPublication] = []
        for item in self.pending(observed_at=observed_at, limit=limit):
            if item.product_input_snapshot_id is not None:
                continue
            gate_id = item.candidate_dataset_gate_assessment_id
            if gate_id is None:
                continue
            publications.append(
                self.prepare(
                    product_enrollment_id=item.product_enrollment_id,
                    decision_session_id=item.decision_session_id,
                    dataset_gate_assessment_id=gate_id,
                    actor_key=actor_key,
                )
            )
        return tuple(publications)

    def pending(
        self, *, observed_at: datetime, limit: int = 50
    ) -> tuple[PendingProductInput, ...]:
        if observed_at.utcoffset() is None:
            raise ValueError("Product input pending cutoff must be timezone-aware")
        if limit < 1 or limit > 500:
            raise ValueError("Product input pending limit must be between 1 and 500")
        with self._engine.connect() as connection:
            rows = tuple(
                connection.execute(
                    text(
                        """
                        SELECT enrollment.product_enrollment_id,
                               enrollment.execution_version_id,
                               session.decision_session_id,session.session_date,
                               session.decision_cutoff_at,
                               disclosure.dataset_gate_assessment_id,
                               candidate_gate.dataset_gate_assessment_id
                                 AS candidate_dataset_gate_assessment_id,
                               CASE WHEN snapshot_artifact.status='published'
                                    THEN snapshot.product_input_snapshot_id
                               END AS product_input_snapshot_id
                          FROM product.v022_product_enrollment enrollment
                          JOIN product.v022_decision_schedule_session session
                            ON session.decision_schedule_version_id=
                               enrollment.decision_schedule_version_id
                          JOIN product.v022_decision_schedule_session first_session
                            ON first_session.decision_session_id=
                               enrollment.first_eligible_decision_session_id
                          JOIN product.v022_product_data_disclosure disclosure
                            ON disclosure.execution_version_id=
                               enrollment.execution_version_id
                          JOIN lineage.artifact disclosure_artifact
                            ON disclosure_artifact.artifact_id=disclosure.artifact_id
                           AND disclosure_artifact.status='published'
                          JOIN experiment.v022_evaluation_cohort_version cohort
                            ON cohort.evaluation_cohort_version_id=
                               disclosure.evaluation_cohort_version_id
                          JOIN data.v022_dataset_gate_assessment baseline_gate
                            ON baseline_gate.dataset_gate_assessment_id=
                               disclosure.dataset_gate_assessment_id
                          JOIN data.dataset_publication baseline_dataset
                            ON baseline_dataset.dataset_publication_id=
                               baseline_gate.dataset_publication_id
                          JOIN catalog.universe_history baseline_history
                            ON baseline_history.universe_history_id=
                               baseline_gate.universe_history_id
                          JOIN catalog.calendar_version baseline_calendar
                            ON baseline_calendar.calendar_version_id=
                               baseline_gate.calendar_version_id
                          LEFT JOIN LATERAL (
                            SELECT event.to_lifecycle
                              FROM product.v022_enrollment_lifecycle_event event
                             WHERE event.product_enrollment_id=
                                   enrollment.product_enrollment_id
                               AND event.effective_at<=:observed_at
                             ORDER BY event.effective_at DESC,
                                      event.sequence_number DESC
                             LIMIT 1
                          ) lifecycle ON true
                          LEFT JOIN product.v022_product_input_snapshot snapshot
                            ON snapshot.product_enrollment_id=
                               enrollment.product_enrollment_id
                           AND snapshot.decision_session_id=session.decision_session_id
                          LEFT JOIN lineage.artifact snapshot_artifact
                            ON snapshot_artifact.artifact_id=snapshot.artifact_id
                          LEFT JOIN LATERAL (
                            SELECT candidate.dataset_gate_assessment_id,
                                   greatest(
                                     candidate_artifact.published_at,
                                     dataset_artifact.published_at,
                                     history_artifact.published_at,
                                     calendar_artifact.published_at
                                   ) AS inputs_available_at
                              FROM data.v022_dataset_gate_assessment candidate
                              JOIN lineage.artifact candidate_artifact
                                ON candidate_artifact.artifact_id=candidate.artifact_id
                               AND candidate_artifact.status='published'
                              JOIN data.dataset_publication dataset
                                ON dataset.dataset_publication_id=
                                   candidate.dataset_publication_id
                              JOIN lineage.artifact dataset_artifact
                                ON dataset_artifact.artifact_id=dataset.artifact_id
                               AND dataset_artifact.status='published'
                              JOIN catalog.universe_history history
                                ON history.universe_history_id=candidate.universe_history_id
                              JOIN lineage.artifact history_artifact
                                ON history_artifact.artifact_id=history.artifact_id
                               AND history_artifact.status='published'
                              JOIN catalog.calendar_version calendar
                                ON calendar.calendar_version_id=candidate.calendar_version_id
                              JOIN lineage.artifact calendar_artifact
                                ON calendar_artifact.artifact_id=calendar.artifact_id
                               AND calendar_artifact.status='published'
                             WHERE candidate.product_eligibility<>'ineligible'
                               AND candidate.blocker_count=0
                               AND dataset.dataset_kind='canonical'
                               AND dataset.value_kind='daily_bar'
                               AND dataset.dataset_key=baseline_dataset.dataset_key
                               AND history.universe_methodology_id=
                                   baseline_history.universe_methodology_id
                               AND calendar.calendar_definition_id=
                                   baseline_calendar.calendar_definition_id
                               AND candidate.assessed_coverage_start<=cohort.warmup_start
                               AND candidate.assessed_coverage_end>=session.session_date
                               AND dataset.coverage_start<=cohort.warmup_start
                               AND dataset.coverage_end>=session.session_date
                               AND calendar.coverage_start<=cohort.warmup_start
                               AND calendar.coverage_end>=session.session_date
                               AND EXISTS (
                                 SELECT 1 FROM catalog.calendar_session exact_session
                                  WHERE exact_session.calendar_version_id=
                                        candidate.calendar_version_id
                                    AND exact_session.session_date=session.session_date
                               )
                               AND greatest(
                                     candidate_artifact.published_at,
                                     dataset_artifact.published_at,
                                     history_artifact.published_at,
                                     calendar_artifact.published_at
                                   )>=session.decision_cutoff_at
                               AND greatest(
                                     candidate_artifact.published_at,
                                     dataset_artifact.published_at,
                                     history_artifact.published_at,
                                     calendar_artifact.published_at
                                   )<=:observed_at
                             ORDER BY inputs_available_at,
                                      candidate.version_number,
                                      candidate.dataset_gate_assessment_id
                             LIMIT 1
                          ) candidate_gate ON true
                         WHERE session.ordinal>=first_session.ordinal
                           AND session.decision_cutoff_at<=:observed_at
                           AND coalesce(lifecycle.to_lifecycle,'active')='active'
                           AND NOT EXISTS (
                             SELECT 1 FROM product.v022_product_decision decision
                              WHERE decision.execution_version_id=
                                    enrollment.execution_version_id
                                AND decision.decision_session_id=
                                    session.decision_session_id
                           )
                         ORDER BY session.decision_cutoff_at,
                                  enrollment.product_enrollment_id
                         LIMIT :limit
                        """
                    ),
                    {"observed_at": observed_at, "limit": limit},
                ).mappings()
            )
        return tuple(
            PendingProductInput(
                row["product_enrollment_id"],
                row["execution_version_id"],
                row["decision_session_id"],
                row["session_date"].isoformat(),
                row["decision_cutoff_at"],
                row["dataset_gate_assessment_id"],
                row["candidate_dataset_gate_assessment_id"],
                row["product_input_snapshot_id"],
                "prepared"
                if row["product_input_snapshot_id"] is not None
                else (
                    "ready_to_prepare"
                    if row["candidate_dataset_gate_assessment_id"] is not None
                    else "awaiting_published_input"
                ),
            )
            for row in rows
        )
