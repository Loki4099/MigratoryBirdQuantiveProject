from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput


@dataclass(frozen=True, slots=True)
class ProductInputSnapshotSpec:
    product_enrollment_id: uuid.UUID
    decision_session_id: uuid.UUID
    dataset_gate_assessment_id: uuid.UUID
    created_by: str

    def __post_init__(self) -> None:
        if not self.created_by.strip():
            raise ValueError("Product Input Snapshot created_by must be nonblank")


@dataclass(frozen=True, slots=True)
class ProductInputSnapshotPublication:
    product_input_snapshot_id: uuid.UUID
    artifact_id: uuid.UUID
    snapshot_fingerprint: str
    dataset_publication_id: uuid.UUID
    input_start: date
    input_end: date
    inputs_available_at: datetime
    member_count: int
    reused: bool


class ProductInputSnapshotService:
    """Publish the exact data evidence available for one Product decision session."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, spec: ProductInputSnapshotSpec) -> ProductInputSnapshotPublication:
        with self._engine.connect() as connection:
            source = _load_source(connection, spec)
            warning_codes = tuple(
                connection.execute(
                    text(
                        """
                        SELECT finding_code
                          FROM data.v022_dataset_gate_finding
                         WHERE dataset_gate_assessment_id=:gate
                           AND product_effect='warning'
                         ORDER BY ordinal
                        """
                    ),
                    {"gate": spec.dataset_gate_assessment_id},
                ).scalars()
            )
        _validate_source(source)
        with self._engine.connect() as connection:
            members = _load_members(connection, source)
        if not members:
            raise ValueError("Product Input Snapshot has no exact decision-session members")
        member_documents = tuple(_member_document(item) for item in members)
        member_set_fingerprint = sha256_hexdigest(member_documents)
        document: dict[str, object] = {
            "contract_version": "v0.22.product_input_snapshot.v1",
            "product_enrollment_id": str(spec.product_enrollment_id),
            "execution_version_id": str(source["execution_version_id"]),
            "decision_session_id": str(spec.decision_session_id),
            "product_data_disclosure_id": str(source["product_data_disclosure_id"]),
            "dataset_gate_assessment_id": str(spec.dataset_gate_assessment_id),
            "dataset_publication_id": str(source["dataset_publication_id"]),
            "universe_history_id": str(source["universe_history_id"]),
            "calendar_version_id": str(source["calendar_version_id"]),
            "input_start": source["warmup_start"].isoformat(),
            "input_end": source["session_date"].isoformat(),
            "decision_cutoff_at": source["decision_cutoff_at"].isoformat(),
            "inputs_available_at": source["inputs_available_at"].isoformat(),
            "price_semantics": str(source["price_semantics"]),
            "product_eligibility": str(source["product_eligibility"]),
            "warning_codes": list(warning_codes),
            "universe_snapshot_id": str(members[0]["universe_snapshot_id"]),
            "member_count": len(members),
            "member_set_fingerprint": member_set_fingerprint,
            "runtime_network_access": False,
            "input_policy": "published_exact_product_inputs_v1",
        }
        fingerprint = sha256_hexdigest(document)
        snapshot_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:product-input-snapshot:{fingerprint}"
        )
        dependencies = (
            DependencyInput(source["enrollment_artifact_id"], "enrollment", 0),
            DependencyInput(source["disclosure_artifact_id"], "product_data_disclosure", 1),
            DependencyInput(source["dataset_artifact_id"], "market_dataset", 2),
            DependencyInput(source["universe_history_artifact_id"], "universe_history", 3),
            DependencyInput(source["calendar_artifact_id"], "calendar_version", 4),
            DependencyInput(source["dataset_gate_artifact_id"], "dataset_gate", 5),
        )

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO product.v022_product_input_snapshot (
                      product_input_snapshot_id,artifact_id,product_enrollment_id,
                      execution_version_id,decision_session_id,product_data_disclosure_id,
                      dataset_gate_assessment_id,dataset_gate_artifact_id,
                      dataset_publication_id,dataset_artifact_id,universe_history_id,
                      universe_history_artifact_id,calendar_version_id,calendar_artifact_id,
                      input_start,input_end,decision_cutoff_at,inputs_available_at,
                      price_semantics,product_eligibility,warning_codes,snapshot_document,
                      snapshot_fingerprint,created_by
                    ) VALUES (
                      :snapshot,:artifact,:enrollment,:execution,:session,:disclosure,
                      :gate,:gate_artifact,:dataset,:dataset_artifact,:history,
                      :history_artifact,:calendar,:calendar_artifact,:input_start,:input_end,
                      :decision_cutoff,:available_at,:price_semantics,:product_eligibility,
                      CAST(:warnings AS jsonb),CAST(:document AS jsonb),:fingerprint,:created_by
                    )
                    """
                ),
                {
                    "snapshot": snapshot_id,
                    "artifact": artifact_id,
                    "enrollment": spec.product_enrollment_id,
                    "execution": source["execution_version_id"],
                    "session": spec.decision_session_id,
                    "disclosure": source["product_data_disclosure_id"],
                    "gate": spec.dataset_gate_assessment_id,
                    "gate_artifact": source["dataset_gate_artifact_id"],
                    "dataset": source["dataset_publication_id"],
                    "dataset_artifact": source["dataset_artifact_id"],
                    "history": source["universe_history_id"],
                    "history_artifact": source["universe_history_artifact_id"],
                    "calendar": source["calendar_version_id"],
                    "calendar_artifact": source["calendar_artifact_id"],
                    "input_start": source["warmup_start"],
                    "input_end": source["session_date"],
                    "decision_cutoff": source["decision_cutoff_at"],
                    "available_at": source["inputs_available_at"],
                    "price_semantics": source["price_semantics"],
                    "product_eligibility": source["product_eligibility"],
                    "warnings": json.dumps(warning_codes),
                    "document": json.dumps(document, sort_keys=True),
                    "fingerprint": fingerprint,
                    "created_by": spec.created_by.strip(),
                },
            )
            for item, member_document in zip(members, member_documents, strict=True):
                connection.execute(
                    text(
                        """
                        INSERT INTO product.v022_product_input_member (
                          product_input_snapshot_id,ordinal,universe_snapshot_id,
                          security_id,legacy_asset_id,asset_key,observed_session_count,
                          required_history_sessions,is_uniformly_excluded,is_terminal,
                          is_warmup_ready,is_selectable,reason_codes,member_document
                        ) VALUES (
                          :snapshot,:ordinal,:universe_snapshot,:security,:asset,:asset_key,
                          :observed,:required,:excluded,:terminal,:ready,:selectable,
                          CAST(:reasons AS jsonb),CAST(:document AS jsonb)
                        )
                        """
                    ),
                    {
                        "snapshot": snapshot_id,
                        "ordinal": item["ordinal"],
                        "universe_snapshot": item["universe_snapshot_id"],
                        "security": item["security_id"],
                        "asset": item["legacy_asset_id"],
                        "asset_key": item["security_key"],
                        "observed": item["observed_session_count"],
                        "required": source["required_history_sessions"],
                        "excluded": item["is_uniformly_excluded"],
                        "terminal": item["is_terminal"],
                        "ready": member_document["is_warmup_ready"],
                        "selectable": member_document["is_selectable"],
                        "reasons": json.dumps(member_document["reason_codes"]),
                        "document": json.dumps(member_document, sort_keys=True),
                    },
                )

        publication = self._artifacts.publish(
            artifact_type="v022_product_input_snapshot",
            artifact_key=(
                "v022_product_input_snapshot__"
                f"{spec.product_enrollment_id}__{spec.decision_session_id}"
            ),
            version_number=1,
            semantic_payload=document,
            content_payload=document,
            dependencies=dependencies,
            reason="publish immutable v0.22 Product Input Snapshot",
            draft_writer=writer,
        )
        return ProductInputSnapshotPublication(
            snapshot_id,
            publication.artifact_id,
            fingerprint,
            source["dataset_publication_id"],
            source["warmup_start"],
            source["session_date"],
            source["inputs_available_at"],
            len(members),
            publication.reused,
        )


def _load_source(connection: Connection, spec: ProductInputSnapshotSpec) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
                SELECT enrollment.execution_version_id,
                       enrollment.artifact_id AS enrollment_artifact_id,
                       enrollment_artifact.status AS enrollment_status,
                       session.session_date,session.decision_cutoff_at,session.ordinal,
                       first_session.ordinal AS first_ordinal,
                       coalesce(lifecycle.to_lifecycle,'active') AS lifecycle,
                       disclosure.product_data_disclosure_id,
                       disclosure.artifact_id AS disclosure_artifact_id,
                       disclosure.disclosure_document,
                       disclosure_artifact.status AS disclosure_status,
                       cohort.warmup_start,
                       cohort.required_history_sessions,
                       baseline_dataset.dataset_key AS baseline_dataset_key,
                       baseline_history.universe_methodology_id AS baseline_methodology_id,
                       baseline_calendar.calendar_definition_id AS baseline_calendar_definition_id,
                       gate.dataset_gate_assessment_id,
                       gate.dataset_publication_id,gate.dataset_artifact_id,
                       gate.universe_history_id,gate.universe_history_artifact_id,
                       gate.calendar_version_id,gate.calendar_artifact_id,
                       gate.artifact_id AS dataset_gate_artifact_id,
                       gate.price_semantics,gate.product_eligibility,gate.blocker_count,
                       gate.assessed_coverage_start,gate.assessed_coverage_end,
                       gate_artifact.status AS gate_status,
                       dataset.dataset_key,dataset.dataset_kind,dataset.value_kind,
                       dataset.coverage_start AS dataset_coverage_start,
                       dataset.coverage_end AS dataset_coverage_end,
                       dataset_artifact.status AS dataset_status,
                       history.universe_methodology_id,history_artifact.status AS history_status,
                       calendar.calendar_definition_id,
                       calendar.coverage_start AS calendar_coverage_start,
                       calendar.coverage_end AS calendar_coverage_end,
                       calendar_artifact.status AS calendar_status,
                       greatest(dataset_artifact.published_at,history_artifact.published_at,
                                calendar_artifact.published_at,gate_artifact.published_at)
                         AS inputs_available_at,
                       EXISTS (SELECT 1 FROM catalog.calendar_session exact_session
                         WHERE exact_session.calendar_version_id=gate.calendar_version_id
                           AND exact_session.session_date=session.session_date)
                         AS calendar_session_exists
                  FROM product.v022_product_enrollment enrollment
                  JOIN lineage.artifact enrollment_artifact
                    ON enrollment_artifact.artifact_id=enrollment.artifact_id
                  JOIN product.v022_decision_schedule_session session
                    ON session.decision_session_id=:session
                   AND session.decision_schedule_version_id=
                       enrollment.decision_schedule_version_id
                  JOIN product.v022_decision_schedule_session first_session
                    ON first_session.decision_session_id=
                       enrollment.first_eligible_decision_session_id
                  JOIN product.v022_product_data_disclosure disclosure
                    ON disclosure.execution_version_id=enrollment.execution_version_id
                  JOIN lineage.artifact disclosure_artifact
                    ON disclosure_artifact.artifact_id=disclosure.artifact_id
                  JOIN experiment.v022_evaluation_cohort_version cohort
                    ON cohort.evaluation_cohort_version_id=
                       disclosure.evaluation_cohort_version_id
                  JOIN data.dataset_publication baseline_dataset
                    ON baseline_dataset.dataset_publication_id=cohort.dataset_publication_id
                  JOIN catalog.universe_history baseline_history
                    ON baseline_history.universe_history_id=cohort.universe_history_id
                  JOIN catalog.calendar_version baseline_calendar
                    ON baseline_calendar.calendar_version_id=cohort.calendar_version_id
                  JOIN data.v022_dataset_gate_assessment gate
                    ON gate.dataset_gate_assessment_id=:gate
                  JOIN lineage.artifact gate_artifact
                    ON gate_artifact.artifact_id=gate.artifact_id
                  JOIN data.dataset_publication dataset
                    ON dataset.dataset_publication_id=gate.dataset_publication_id
                  JOIN lineage.artifact dataset_artifact
                    ON dataset_artifact.artifact_id=dataset.artifact_id
                  JOIN catalog.universe_history history
                    ON history.universe_history_id=gate.universe_history_id
                  JOIN lineage.artifact history_artifact
                    ON history_artifact.artifact_id=history.artifact_id
                  JOIN catalog.calendar_version calendar
                    ON calendar.calendar_version_id=gate.calendar_version_id
                  JOIN lineage.artifact calendar_artifact
                    ON calendar_artifact.artifact_id=calendar.artifact_id
                  LEFT JOIN LATERAL (
                    SELECT event.to_lifecycle
                      FROM product.v022_enrollment_lifecycle_event event
                     WHERE event.product_enrollment_id=enrollment.product_enrollment_id
                       AND event.effective_at<=greatest(
                         dataset_artifact.published_at,history_artifact.published_at,
                         calendar_artifact.published_at,gate_artifact.published_at)
                     ORDER BY event.effective_at DESC,event.sequence_number DESC LIMIT 1
                  ) lifecycle ON true
                 WHERE enrollment.product_enrollment_id=:enrollment
                """
            ),
            {
                "enrollment": spec.product_enrollment_id,
                "session": spec.decision_session_id,
                "gate": spec.dataset_gate_assessment_id,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Exact Enrollment, Session, Disclosure, or Dataset Gate was not found")
    return row


def _validate_source(row: RowMapping | dict[str, Any]) -> None:
    if any(
        row[key] != "published"
        for key in (
            "enrollment_status",
            "disclosure_status",
            "gate_status",
            "dataset_status",
            "history_status",
            "calendar_status",
        )
    ):
        raise ValueError("Product Input Snapshot requires published frozen inputs")
    future_policy = cast(dict[str, Any], row["disclosure_document"]).get(
        "future_input_policy"
    )
    if not isinstance(future_policy, dict) or any(
        future_policy.get(key) is not expected
        for key, expected in (
            ("require_published_dataset_universe_manifest", True),
            ("require_gate_assessment", True),
            ("stop_on_new_product_ineligible_blocker", True),
            ("preserve_prior_decisions_and_evidence", True),
            ("runtime_network_access", False),
        )
    ):
        raise ValueError("Product disclosure does not authorize exact offline future inputs")
    if row["ordinal"] < row["first_ordinal"] or row["lifecycle"] != "active":
        raise ValueError("Product Input Snapshot Session is not active and eligible")
    if row["product_eligibility"] == "ineligible" or row["blocker_count"] != 0:
        raise ValueError("Dataset Gate makes this Product input ineligible")
    if (
        row["dataset_kind"] != "canonical"
        or row["value_kind"] != "daily_bar"
        or row["dataset_key"] != row["baseline_dataset_key"]
        or row["universe_methodology_id"] != row["baseline_methodology_id"]
        or row["calendar_definition_id"] != row["baseline_calendar_definition_id"]
    ):
        raise ValueError("Product Input Snapshot changed its frozen data methodology")
    start = cast(date, row["warmup_start"])
    end = cast(date, row["session_date"])
    if any(
        row[start_key] > start or row[end_key] < end
        for start_key, end_key in (
            ("assessed_coverage_start", "assessed_coverage_end"),
            ("dataset_coverage_start", "dataset_coverage_end"),
            ("calendar_coverage_start", "calendar_coverage_end"),
        )
    ) or row["calendar_session_exists"] is not True:
        raise ValueError("Product Input Snapshot does not cover its exact decision range")
    if row["inputs_available_at"] < row["decision_cutoff_at"]:
        raise ValueError("Product inputs were not available after the decision cutoff")


def _load_members(connection: Connection, source: RowMapping) -> tuple[RowMapping, ...]:
    return tuple(
        connection.execute(
            text(
                """
                WITH exact_snapshot AS (
                  SELECT snapshot.universe_snapshot_id
                    FROM catalog.universe_snapshot snapshot
                   WHERE snapshot.universe_history_id=:history
                     AND snapshot.effective_session<=:input_end
                   ORDER BY snapshot.effective_session DESC,
                            snapshot.universe_snapshot_id DESC
                   LIMIT 1
                )
                SELECT member.ordinal,member.universe_snapshot_id,member.security_id,
                       security.legacy_asset_id,security.security_key,
                       count(DISTINCT bar.session_date) AS observed_session_count,
                       EXISTS (
                         SELECT 1 FROM data.v022_dataset_gate_uniform_exclusion exclusion
                          WHERE exclusion.dataset_gate_assessment_id=:gate
                            AND exclusion.security_id=member.security_id
                            AND exclusion.exclusion_start<=:input_end
                            AND exclusion.exclusion_end>=:input_end
                       ) AS is_uniformly_excluded,
                       EXISTS (
                         SELECT 1 FROM catalog.security_terminal_event terminal
                          WHERE terminal.security_id=member.security_id
                            AND terminal.effective_session<=:input_end
                            AND terminal.status='confirmed'
                       ) AS is_terminal
                  FROM exact_snapshot
                  JOIN catalog.universe_snapshot_member member
                    ON member.universe_snapshot_id=exact_snapshot.universe_snapshot_id
                  JOIN catalog.security security ON security.security_id=member.security_id
                  LEFT JOIN data.daily_bar bar
                    ON bar.dataset_publication_id=:dataset
                   AND bar.asset_id=security.legacy_asset_id
                   AND bar.session_date BETWEEN :input_start AND :input_end
                 WHERE security.legacy_asset_id IS NOT NULL
                 GROUP BY member.ordinal,member.universe_snapshot_id,member.security_id,
                          security.legacy_asset_id,security.security_key
                 ORDER BY member.ordinal
                """
            ),
            {
                "history": source["universe_history_id"],
                "gate": source["dataset_gate_assessment_id"],
                "dataset": source["dataset_publication_id"],
                "input_start": source["warmup_start"],
                "input_end": source["session_date"],
            },
        ).mappings()
    )


def _member_document(item: RowMapping) -> dict[str, object]:
    observed = int(item["observed_session_count"])
    excluded = bool(item["is_uniformly_excluded"])
    terminal = bool(item["is_terminal"])
    ready = observed >= 504
    selectable = ready and not excluded and not terminal
    reasons: list[str] = []
    if not ready:
        reasons.append("warmup_504_incomplete")
    if excluded:
        reasons.append("uniform_provider_exclusion")
    if terminal:
        reasons.append("confirmed_terminal_event")
    return {
        "ordinal": int(item["ordinal"]),
        "universe_snapshot_id": str(item["universe_snapshot_id"]),
        "security_id": str(item["security_id"]),
        "legacy_asset_id": str(item["legacy_asset_id"]),
        "asset_key": str(item["security_key"]),
        "observed_session_count": observed,
        "required_history_sessions": 504,
        "is_uniformly_excluded": excluded,
        "is_terminal": terminal,
        "is_warmup_ready": ready,
        "is_selectable": selectable,
        "reason_codes": reasons,
    }
