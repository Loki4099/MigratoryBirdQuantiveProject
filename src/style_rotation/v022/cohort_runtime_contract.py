from __future__ import annotations

import json
import uuid
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

ValuationState = Literal["live", "stale_confirmed", "terminal", "unavailable"]

_CONTRACT = "v0.22.evaluation_cohort_runtime.v2"


@dataclass(frozen=True, slots=True)
class RuntimeMaskInterval:
    security_id: uuid.UUID
    ordinal: int
    effective_start: date
    effective_end: date
    is_member: bool
    is_warmup_ready: bool
    is_selectable: bool
    is_tradable: bool
    valuation_state: ValuationState
    reason_codes: tuple[str, ...]
    evidence_artifact_ids: tuple[uuid.UUID, ...]

    @property
    def fingerprint(self) -> str:
        return sha256_hexdigest(asdict(self))


@dataclass(frozen=True, slots=True)
class SettlementInstruction:
    ordinal: int
    lifecycle_event_id: uuid.UUID
    lifecycle_event_artifact_id: uuid.UUID
    security_id: uuid.UUID
    event_type: str
    event_status: str
    effective_session: date
    settlement_session: date
    legs: tuple[dict[str, object], ...]

    @property
    def document(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "security_lifecycle_event_id": str(self.lifecycle_event_id),
            "lifecycle_event_artifact_id": str(self.lifecycle_event_artifact_id),
            "security_id": str(self.security_id),
            "event_type": self.event_type,
            "event_status": self.event_status,
            "effective_session": self.effective_session.isoformat(),
            "settlement_session": self.settlement_session.isoformat(),
            "legs": list(self.legs),
        }

    @property
    def fingerprint(self) -> str:
        return sha256_hexdigest(self.document)


@dataclass(frozen=True, slots=True)
class CohortRuntimeContractPublication:
    evaluation_cohort_runtime_contract_id: uuid.UUID
    artifact_id: uuid.UUID
    evaluation_cohort_version_id: uuid.UUID
    dataset_gate_assessment_id: uuid.UUID
    runtime_fingerprint: str
    mask_interval_count: int
    lifecycle_event_count: int
    settlement_instruction_count: int
    ranking_eligibility: str
    product_eligibility: str
    reused: bool


@dataclass(frozen=True, slots=True)
class _Inputs:
    cohort: RowMapping
    gate: Mapping[str, object]
    session_dates: tuple[date, ...]
    evaluation_dates: frozenset[date]
    base_intervals: dict[uuid.UUID, tuple[RowMapping, ...]]
    exclusions: dict[uuid.UUID, RowMapping]
    lifecycle_events: dict[uuid.UUID, tuple[RowMapping, ...]]
    lifecycle_artifacts: tuple[uuid.UUID, ...]
    settlement_instructions: tuple[SettlementInstruction, ...]


class CohortRuntimeContractService:
    """Freeze the exact Gate, decision mask and lifecycle settlement closure for a Cohort."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        evaluation_cohort_version_id: uuid.UUID,
        dataset_gate_assessment_id: uuid.UUID,
        created_by: str,
    ) -> CohortRuntimeContractPublication:
        if not created_by.strip():
            raise ValueError("Cohort runtime contract creator is blank")
        with self._engine.connect() as connection:
            inputs = _load_inputs(
                connection,
                evaluation_cohort_version_id=evaluation_cohort_version_id,
                dataset_gate_assessment_id=dataset_gate_assessment_id,
            )
        intervals = _derive_runtime_mask(inputs)
        interval_documents = tuple(_interval_document(item) for item in intervals)
        instruction_documents = tuple(
            item.document for item in inputs.settlement_instructions
        )
        document: dict[str, object] = {
            "contract_version": _CONTRACT,
            "evaluation_cohort_version_id": str(evaluation_cohort_version_id),
            "evaluation_cohort_fingerprint": inputs.cohort["cohort_fingerprint"],
            "dataset_gate_assessment_id": str(dataset_gate_assessment_id),
            "dataset_gate_fingerprint": inputs.gate["assessment_fingerprint"],
            "ranking_eligibility": inputs.gate["ranking_eligibility"],
            "product_eligibility": inputs.gate["product_eligibility"],
            "mask_interval_count": len(intervals),
            "lifecycle_event_count": len(inputs.lifecycle_artifacts),
            "settlement_instruction_count": len(inputs.settlement_instructions),
            "mask_projection_fingerprint": sha256_hexdigest(interval_documents),
            "settlement_projection_fingerprint": sha256_hexdigest(instruction_documents),
            "runtime_rules": {
                "candidate_selection": "exact_decision_session_is_selectable",
                "execution": "exact_execution_session_is_tradable",
                "valuation": "frozen_interval_valuation_state",
                "settlement": "exact_published_lifecycle_event_legs",
                "ordinary_index_removal": "no_new_open_existing_holding_may_close",
            },
        }
        fingerprint = sha256_hexdigest(document)
        contract_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:evaluation-cohort-runtime:{fingerprint}"
        )
        dependencies = [
            DependencyInput(cast(uuid.UUID, inputs.cohort["artifact_id"]), "evaluation_cohort", 0),
            DependencyInput(
                cast(uuid.UUID, inputs.gate["artifact_id"]),
                "dataset_gate_assessment",
                1,
            ),
        ]
        dependencies.extend(
            DependencyInput(artifact_id, "lifecycle_event", ordinal + 2)
            for ordinal, artifact_id in enumerate(inputs.lifecycle_artifacts)
        )

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            _write_projection(
                connection,
                contract_id=contract_id,
                artifact_id=artifact_id,
                inputs=inputs,
                intervals=intervals,
                document=document,
                fingerprint=fingerprint,
                created_by=created_by,
            )

        publication = self._artifacts.publish(
            artifact_type="v022_evaluation_cohort_runtime_contract",
            artifact_key=(
                "v022_evaluation_cohort_runtime_contract__" + inputs.cohort["cohort_key"]
            ),
            version_number=cast(int, inputs.cohort["version_number"]),
            semantic_payload=document,
            content_payload={
                "mask_projection_fingerprint": document["mask_projection_fingerprint"],
                "settlement_projection_fingerprint": document[
                    "settlement_projection_fingerprint"
                ],
            },
            dependencies=tuple(dependencies),
            reason=f"publish M106 Cohort runtime contract for {evaluation_cohort_version_id}",
            draft_writer=write,
        )
        if publication.reused:
            with self._engine.connect() as connection:
                existing = _existing(connection, fingerprint)
            if existing is None:
                raise ValueError("Reused Cohort runtime Artifact has no projection")
            return existing
        return CohortRuntimeContractPublication(
            contract_id,
            publication.artifact_id,
            evaluation_cohort_version_id,
            dataset_gate_assessment_id,
            fingerprint,
            len(intervals),
            len(inputs.lifecycle_artifacts),
            len(inputs.settlement_instructions),
            cast(str, inputs.gate["ranking_eligibility"]),
            cast(str, inputs.gate["product_eligibility"]),
            False,
        )


def _load_inputs(
    connection: Connection,
    *,
    evaluation_cohort_version_id: uuid.UUID,
    dataset_gate_assessment_id: uuid.UUID,
) -> _Inputs:
    row = (
        connection.execute(
            text(
                """
                SELECT cohort.*,cohort_artifact.status AS cohort_status,
                       gate.artifact_id AS gate_artifact_id,
                       gate.dataset_publication_id AS gate_dataset_publication_id,
                       gate.universe_history_id AS gate_universe_history_id,
                       gate.security_market_quality_report_id AS gate_quality_report_id,
                       gate.calendar_version_id AS gate_calendar_version_id,
                       gate.assessed_coverage_start,gate.assessed_coverage_end,
                       gate.ranking_eligibility,gate.product_eligibility,
                       gate.assessment_fingerprint,gate_artifact.status AS gate_status
                  FROM experiment.v022_evaluation_cohort_version cohort
                  JOIN lineage.artifact cohort_artifact
                    ON cohort_artifact.artifact_id=cohort.artifact_id
                  JOIN data.v022_dataset_gate_assessment gate
                    ON gate.dataset_gate_assessment_id=:gate
                  JOIN lineage.artifact gate_artifact
                    ON gate_artifact.artifact_id=gate.artifact_id
                 WHERE cohort.evaluation_cohort_version_id=:cohort
                """
            ),
            {"cohort": evaluation_cohort_version_id, "gate": dataset_gate_assessment_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Evaluation Cohort or Dataset Gate Assessment was not found")
    if row["cohort_status"] != "published" or row["gate_status"] != "published":
        raise ValueError("Cohort runtime inputs must be published")
    exact_pairs = (
        ("dataset_publication_id", "gate_dataset_publication_id"),
        ("universe_history_id", "gate_universe_history_id"),
        ("security_market_quality_report_id", "gate_quality_report_id"),
        ("calendar_version_id", "gate_calendar_version_id"),
    )
    if any(row[left] != row[right] for left, right in exact_pairs):
        raise ValueError("Dataset Gate Assessment does not describe the exact Cohort inputs")
    if row["research_tier"] != row["ranking_eligibility"]:
        raise ValueError("Cohort research tier must equal the frozen ranking Gate")
    if row["assessed_coverage_start"] > row["warmup_start"] or row[
        "assessed_coverage_end"
    ] < row["evaluation_end"]:
        raise ValueError("Dataset Gate Assessment does not cover the Cohort interval")

    session_rows = connection.execute(
        text(
            """
            SELECT session_date,session_role FROM experiment.v022_evaluation_cohort_session
             WHERE evaluation_cohort_version_id=:cohort ORDER BY ordinal
            """
        ),
        {"cohort": evaluation_cohort_version_id},
    ).mappings().all()
    if not session_rows:
        raise ValueError("Evaluation Cohort has no frozen sessions")
    session_dates = tuple(cast(date, item["session_date"]) for item in session_rows)
    evaluation_dates = frozenset(
        cast(date, item["session_date"])
        for item in session_rows
        if item["session_role"] == "evaluation"
    )

    base_grouped: dict[uuid.UUID, list[RowMapping]] = defaultdict(list)
    for item in connection.execute(
        text(
            """
            SELECT * FROM experiment.v022_cohort_eligibility_interval
             WHERE evaluation_cohort_version_id=:cohort
             ORDER BY security_id,ordinal
            """
        ),
        {"cohort": evaluation_cohort_version_id},
    ).mappings():
        base_grouped[cast(uuid.UUID, item["security_id"])].append(item)
    if not base_grouped:
        raise ValueError("Evaluation Cohort has no base eligibility projection")

    exclusions = {
        cast(uuid.UUID, item["security_id"]): item
        for item in connection.execute(
            text(
                """
                SELECT * FROM data.v022_dataset_gate_uniform_exclusion
                 WHERE dataset_gate_assessment_id=:gate ORDER BY ordinal
                """
            ),
            {"gate": dataset_gate_assessment_id},
        ).mappings()
    }
    security_ids = tuple(sorted(base_grouped, key=str))
    lifecycle_rows = connection.execute(
        text(
            """
            SELECT event.*,artifact.status AS artifact_status
              FROM catalog.v022_security_lifecycle_event event
              JOIN lineage.artifact artifact ON artifact.artifact_id=event.artifact_id
             WHERE event.security_id IN :security_ids
               AND event.effective_session<=:evaluation_end
               AND NOT EXISTS (
                 SELECT 1 FROM catalog.v022_security_lifecycle_event successor
                  JOIN lineage.artifact successor_artifact
                    ON successor_artifact.artifact_id=successor.artifact_id
                 WHERE successor.supersedes_lifecycle_event_id=
                       event.security_lifecycle_event_id
                   AND successor_artifact.status='published'
               )
             ORDER BY event.security_id,event.effective_session,event.event_key
            """
        ).bindparams(bindparam("security_ids", expanding=True)),
        {"security_ids": security_ids, "evaluation_end": row["evaluation_end"]},
    ).mappings().all()
    if any(item["artifact_status"] != "published" for item in lifecycle_rows):
        raise ValueError("Cohort lifecycle closure contains an unpublished event")
    if row["research_tier"] == "rankable_research" and any(
        item["event_status"] != "confirmed" for item in lifecycle_rows
    ):
        raise ValueError("Rankable Cohort runtime requires confirmed lifecycle events")
    lifecycle_grouped: dict[uuid.UUID, list[RowMapping]] = defaultdict(list)
    for item in lifecycle_rows:
        lifecycle_grouped[cast(uuid.UUID, item["security_id"])].append(item)

    event_ids = tuple(
        cast(uuid.UUID, item["security_lifecycle_event_id"])
        for item in lifecycle_rows
    )
    legs_by_event: dict[uuid.UUID, list[dict[str, object]]] = defaultdict(list)
    if event_ids:
        for leg in connection.execute(
            text(
                """
                SELECT security_lifecycle_event_id,leg_document
                  FROM catalog.v022_security_settlement_leg
                 WHERE security_lifecycle_event_id IN :event_ids
                 ORDER BY security_lifecycle_event_id,ordinal
                """
            ).bindparams(bindparam("event_ids", expanding=True)),
            {"event_ids": event_ids},
        ).mappings():
            legs_by_event[cast(uuid.UUID, leg["security_lifecycle_event_id"])].append(
                cast(dict[str, object], leg["leg_document"])
            )
    settlements: list[SettlementInstruction] = []
    for item in lifecycle_rows:
        event_id = cast(uuid.UUID, item["security_lifecycle_event_id"])
        legs = tuple(legs_by_event.get(event_id, ()))
        settlement_session = cast(date | None, item["settlement_session"])
        if not legs:
            continue
        if settlement_session is None:
            raise ValueError("Lifecycle settlement legs require an exact settlement session")
        if settlement_session < row["warmup_start"] or settlement_session > row["evaluation_end"]:
            continue
        settlements.append(
            SettlementInstruction(
                len(settlements),
                event_id,
                cast(uuid.UUID, item["artifact_id"]),
                cast(uuid.UUID, item["security_id"]),
                cast(str, item["event_type"]),
                cast(str, item["event_status"]),
                cast(date, item["effective_session"]),
                settlement_session,
                legs,
            )
        )
    lifecycle_artifacts = tuple(
        sorted({cast(uuid.UUID, item["artifact_id"]) for item in lifecycle_rows}, key=str)
    )
    return _Inputs(
        row,
        {
            "dataset_gate_assessment_id": dataset_gate_assessment_id,
            "artifact_id": row["gate_artifact_id"],
            "assessment_fingerprint": row["assessment_fingerprint"],
            "ranking_eligibility": row["ranking_eligibility"],
            "product_eligibility": row["product_eligibility"],
        },
        session_dates,
        evaluation_dates,
        {key: tuple(value) for key, value in base_grouped.items()},
        exclusions,
        {key: tuple(value) for key, value in lifecycle_grouped.items()},
        lifecycle_artifacts,
        tuple(settlements),
    )


def _derive_runtime_mask(inputs: _Inputs) -> tuple[RuntimeMaskInterval, ...]:
    result: list[RuntimeMaskInterval] = []
    gate_artifact = cast(uuid.UUID, inputs.gate["artifact_id"])
    sessions = inputs.session_dates
    for security_id in sorted(inputs.base_intervals, key=str):
        base_intervals = inputs.base_intervals[security_id]
        events = inputs.lifecycle_events.get(security_id, ())
        exclusion = inputs.exclusions.get(security_id)
        rows: list[tuple[date, tuple[object, ...]]] = []
        base_index = 0
        event_index = 0
        active_event: RowMapping | None = None
        first = bisect_left(sessions, cast(date, base_intervals[0]["effective_start"]))
        last = bisect_right(sessions, cast(date, base_intervals[-1]["effective_end"]))
        for session in sessions[first:last]:
            while (
                base_index + 1 < len(base_intervals)
                and cast(date, base_intervals[base_index]["effective_end"]) < session
            ):
                base_index += 1
            base = base_intervals[base_index]
            if not (base["effective_start"] <= session <= base["effective_end"]):
                continue
            while event_index < len(events) and events[event_index]["effective_session"] <= session:
                active_event = events[event_index]
                event_index += 1
            excluded = bool(
                exclusion is not None
                and exclusion["exclusion_start"] <= session <= exclusion["exclusion_end"]
            )
            member = cast(bool, base["is_member"])
            ready = cast(bool, base["is_warmup_ready"]) and not excluded
            base_valuation = cast(ValuationState, base["valuation_state"])
            valuation: ValuationState = base_valuation
            lifecycle_selectable = True
            lifecycle_tradable = base_valuation == "live"
            if active_event is not None:
                valuation = cast(ValuationState, active_event["valuation_state_after"])
                lifecycle_selectable = cast(bool, active_event["selectable_after"])
                lifecycle_tradable = cast(bool, active_event["tradable_after"])
            if excluded:
                valuation = "unavailable"
            tradable = lifecycle_tradable and valuation == "live" and not excluded
            selectable = (
                member
                and ready
                and lifecycle_selectable
                and tradable
                and session in inputs.evaluation_dates
            )
            reasons = list(cast(list[str], base["reason_codes"]))
            evidence = [
                uuid.UUID(value)
                for value in cast(list[str], base["evidence_artifact_ids"])
            ]
            evidence.append(gate_artifact)
            if excluded and exclusion is not None:
                reasons.append(cast(str, exclusion["reason_code"]))
                evidence.append(cast(uuid.UUID, exclusion["evidence_artifact_id"]))
            if active_event is not None:
                reasons.append(f"lifecycle_{active_event['event_type']}")
                evidence.append(cast(uuid.UUID, active_event["artifact_id"]))
            if not member and tradable:
                reasons.append("removed_member_close_only")
            state = (
                member,
                ready,
                selectable,
                tradable,
                valuation,
                tuple(sorted(set(reasons))),
                tuple(sorted(set(evidence), key=str)),
            )
            rows.append((session, state))
        result.extend(_compress(security_id, rows))
    if not result:
        raise ValueError("Cohort runtime mask is empty")
    return tuple(result)


def _compress(
    security_id: uuid.UUID, rows: list[tuple[date, tuple[object, ...]]]
) -> tuple[RuntimeMaskInterval, ...]:
    if not rows:
        return ()
    result: list[RuntimeMaskInterval] = []
    start = rows[0][0]
    state = rows[0][1]
    for index in range(1, len(rows) + 1):
        if index < len(rows) and rows[index][1] == state:
            continue
        member, ready, selectable, tradable, valuation, reasons, evidence = state
        result.append(
            RuntimeMaskInterval(
                security_id,
                len(result),
                start,
                rows[index - 1][0],
                cast(bool, member),
                cast(bool, ready),
                cast(bool, selectable),
                cast(bool, tradable),
                cast(ValuationState, valuation),
                cast(tuple[str, ...], reasons),
                cast(tuple[uuid.UUID, ...], evidence),
            )
        )
        if index < len(rows):
            start = rows[index][0]
            state = rows[index][1]
    return tuple(result)


def _interval_document(item: RuntimeMaskInterval) -> dict[str, object]:
    return {
        "security_id": str(item.security_id),
        "ordinal": item.ordinal,
        "effective_start": item.effective_start.isoformat(),
        "effective_end": item.effective_end.isoformat(),
        "is_member": item.is_member,
        "is_warmup_ready": item.is_warmup_ready,
        "is_selectable": item.is_selectable,
        "is_tradable": item.is_tradable,
        "valuation_state": item.valuation_state,
        "reason_codes": list(item.reason_codes),
        "evidence_artifact_ids": [str(value) for value in item.evidence_artifact_ids],
    }


def _write_projection(
    connection: Connection,
    *,
    contract_id: uuid.UUID,
    artifact_id: uuid.UUID,
    inputs: _Inputs,
    intervals: tuple[RuntimeMaskInterval, ...],
    document: dict[str, object],
    fingerprint: str,
    created_by: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO experiment.v022_evaluation_cohort_runtime_contract (
              evaluation_cohort_runtime_contract_id,artifact_id,
              evaluation_cohort_version_id,dataset_gate_assessment_id,
              dataset_gate_artifact_id,dataset_gate_fingerprint,ranking_eligibility,
              product_eligibility,mask_interval_count,lifecycle_event_count,
              settlement_instruction_count,runtime_document,runtime_fingerprint,created_by
            ) VALUES (
              :id,:artifact,:cohort,:gate,:gate_artifact,:gate_fingerprint,:ranking,
              :product,:mask_count,:lifecycle_count,:settlement_count,
              CAST(:document AS jsonb),:fingerprint,:created_by
            )
            """
        ),
        {
            "id": contract_id,
            "artifact": artifact_id,
            "cohort": inputs.cohort["evaluation_cohort_version_id"],
            "gate": inputs.gate["dataset_gate_assessment_id"],
            "gate_artifact": inputs.gate["artifact_id"],
            "gate_fingerprint": inputs.gate["assessment_fingerprint"],
            "ranking": inputs.gate["ranking_eligibility"],
            "product": inputs.gate["product_eligibility"],
            "mask_count": len(intervals),
            "lifecycle_count": len(inputs.lifecycle_artifacts),
            "settlement_count": len(inputs.settlement_instructions),
            "document": json.dumps(document, sort_keys=True),
            "fingerprint": fingerprint,
            "created_by": created_by,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO experiment.v022_cohort_runtime_mask_interval (
              cohort_runtime_mask_interval_id,evaluation_cohort_runtime_contract_id,
              security_id,ordinal,effective_start,effective_end,is_member,is_warmup_ready,
              is_selectable,is_tradable,valuation_state,reason_codes,evidence_artifact_ids,
              interval_fingerprint
            ) VALUES (
              :id,:contract,:security,:ordinal,:start,:end,:member,:ready,:selectable,
              :tradable,:valuation,CAST(:reasons AS jsonb),CAST(:evidence AS jsonb),:fingerprint
            )
            """
        ),
        [
            {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"bird:v0.22:cohort-runtime-mask:{contract_id}:{item.security_id}:{item.ordinal}",
                ),
                "contract": contract_id,
                "security": item.security_id,
                "ordinal": item.ordinal,
                "start": item.effective_start,
                "end": item.effective_end,
                "member": item.is_member,
                "ready": item.is_warmup_ready,
                "selectable": item.is_selectable,
                "tradable": item.is_tradable,
                "valuation": item.valuation_state,
                "reasons": json.dumps(item.reason_codes),
                "evidence": json.dumps([str(value) for value in item.evidence_artifact_ids]),
                "fingerprint": item.fingerprint,
            }
            for item in intervals
        ],
    )
    if inputs.settlement_instructions:
        connection.execute(
            text(
                """
                INSERT INTO experiment.v022_cohort_settlement_instruction (
                  cohort_settlement_instruction_id,evaluation_cohort_runtime_contract_id,
                  ordinal,security_lifecycle_event_id,lifecycle_event_artifact_id,security_id,
                  event_type,event_status,effective_session,settlement_session,legs_document,
                  instruction_fingerprint
                ) VALUES (
                  :id,:contract,:ordinal,:event,:event_artifact,:security,:event_type,
                  :event_status,:effective,:settlement,CAST(:legs AS jsonb),:fingerprint
                )
                """
            ),
            [
                {
                    "id": uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"bird:v0.22:cohort-settlement:{contract_id}:{item.lifecycle_event_id}",
                    ),
                    "contract": contract_id,
                    "ordinal": item.ordinal,
                    "event": item.lifecycle_event_id,
                    "event_artifact": item.lifecycle_event_artifact_id,
                    "security": item.security_id,
                    "event_type": item.event_type,
                    "event_status": item.event_status,
                    "effective": item.effective_session,
                    "settlement": item.settlement_session,
                    "legs": json.dumps(item.legs, sort_keys=True),
                    "fingerprint": item.fingerprint,
                }
                for item in inputs.settlement_instructions
            ],
        )


def _existing(connection: Connection, fingerprint: str) -> CohortRuntimeContractPublication | None:
    row = (
        connection.execute(
            text(
                """
                SELECT contract.*,artifact.status FROM
                  experiment.v022_evaluation_cohort_runtime_contract contract
                  JOIN lineage.artifact artifact ON artifact.artifact_id=contract.artifact_id
                 WHERE contract.runtime_fingerprint=:fingerprint
                """
            ),
            {"fingerprint": fingerprint},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["status"] != "published":
        return None
    return CohortRuntimeContractPublication(
        row["evaluation_cohort_runtime_contract_id"],
        row["artifact_id"],
        row["evaluation_cohort_version_id"],
        row["dataset_gate_assessment_id"],
        row["runtime_fingerprint"],
        row["mask_interval_count"],
        row["lifecycle_event_count"],
        row["settlement_instruction_count"],
        row["ranking_eligibility"],
        row["product_eligibility"],
        True,
    )
