from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, Protocol, TypeVar, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.exc import IntegrityError

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.draft_service import GraphDraftService
from style_rotation.v022.frozen_sp500_environment import FROZEN_SP500_COHORT_VERSION

Frequency = Literal["weekly", "monthly"]
SuiteMode = Literal["exploratory"]

_BATCH_NAMESPACE = uuid.UUID("63d86d0e-fc9d-4e36-9ab0-ef0d67ca68c9")
_FREQUENCY_ORDER: tuple[Frequency, ...] = ("weekly", "monthly")
_Stage = Literal["prepare_graph", "admit_graph", "submit_suite", "lock_source", "complete"]
_T = TypeVar("_T")


class SuiteLaunchBatchStageError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        stage: _Stage,
        frequency: Frequency | None,
        summary: str,
    ) -> None:
        self.code = code
        self.stage = stage
        self.frequency = frequency
        self.summary = summary
        scope = f" ({frequency})" if frequency is not None else ""
        super().__init__(f"Suite Launch Batch {stage}{scope} failed: {summary}")


class SuiteLaunchCommands(Protocol):
    def replay(
        self,
        *,
        actor_key: str,
        idempotency_key: uuid.UUID,
        compiled_research_graph_id: uuid.UUID,
        suite_mode: SuiteMode,
    ) -> dict[str, Any] | None: ...

    def submit(
        self,
        *,
        actor_key: str,
        idempotency_key: uuid.UUID,
        compiled_research_graph_id: uuid.UUID,
        suite_mode: SuiteMode,
    ) -> dict[str, Any]: ...

    def status(self, research_suite_id: uuid.UUID) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SuiteLaunchBatchRequest:
    actor_key: str
    idempotency_key: uuid.UUID
    source_graph_draft_id: uuid.UUID
    source_graph_draft_revision: int
    source_compiled_research_graph_id: uuid.UUID
    frequencies: tuple[Frequency, ...] = _FREQUENCY_ORDER
    suite_mode: SuiteMode = "exploratory"


class SuiteLaunchBatchService:
    """Compile and submit frequency-specific Suites under one durable command identity."""

    def __init__(
        self,
        engine: Engine,
        *,
        graph_drafts: GraphDraftService,
        graph_suites: SuiteLaunchCommands,
    ) -> None:
        self._engine = engine
        self._drafts = graph_drafts
        self._suites = graph_suites

    def submit(self, request: SuiteLaunchBatchRequest) -> dict[str, Any]:
        frequencies = _normalize_frequencies(request.frequencies)
        normalized = SuiteLaunchBatchRequest(
            actor_key=request.actor_key,
            idempotency_key=request.idempotency_key,
            source_graph_draft_id=request.source_graph_draft_id,
            source_graph_draft_revision=request.source_graph_draft_revision,
            source_compiled_research_graph_id=request.source_compiled_research_graph_id,
            frequencies=frequencies,
            suite_mode=request.suite_mode,
        )
        batch_id, reused = self._ensure_batch(normalized)
        self._prepare_frequency_graphs(batch_id, normalized)
        self._admit_all_frequency_graphs(batch_id)
        self._submit_missing_suites(batch_id, normalized)
        self._run_stage(
            batch_id,
            stage="lock_source",
            frequency=None,
            operation=lambda: self._drafts.lock_for_experiment(
                normalized.source_graph_draft_id,
                expected_revision=normalized.source_graph_draft_revision,
                actor_key=normalized.actor_key,
                compiled_research_graph_id=(normalized.source_compiled_research_graph_id),
            ),
        )
        self._run_stage(
            batch_id,
            stage="complete",
            frequency=None,
            operation=lambda: None,
        )
        return self._document(batch_id, reused=reused)

    def status(self, suite_launch_batch_id: uuid.UUID) -> dict[str, Any]:
        return self._document(suite_launch_batch_id, reused=True)

    def _ensure_batch(self, request: SuiteLaunchBatchRequest) -> tuple[uuid.UUID, bool]:
        if not request.actor_key.strip():
            raise ValueError("Suite Launch Batch actor is required")
        if request.source_graph_draft_revision < 1:
            raise ValueError("Suite Launch Batch source revision must be positive")
        batch_id = uuid.uuid5(
            _BATCH_NAMESPACE,
            f"{request.actor_key}:{request.idempotency_key}",
        )
        fingerprint = sha256_hexdigest(
            {
                "actor_key": request.actor_key,
                "source_graph_draft_id": request.source_graph_draft_id,
                "source_graph_draft_revision": request.source_graph_draft_revision,
                "source_compiled_research_graph_id": (request.source_compiled_research_graph_id),
                "frequencies": request.frequencies,
                "suite_mode": request.suite_mode,
            }
        )
        with self._engine.begin() as connection:
            existing = (
                connection.execute(
                    text(
                        "SELECT * FROM experiment.v022_suite_launch_batch "
                        "WHERE actor_key=:actor AND idempotency_key=:key FOR UPDATE"
                    ),
                    {"actor": request.actor_key, "key": request.idempotency_key},
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if (
                    existing["suite_launch_batch_id"] != batch_id
                    or existing["batch_fingerprint"] != fingerprint
                ):
                    raise ValueError("Suite Launch Batch idempotency key has different semantics")
                bound_round = connection.scalar(
                    text(
                        "SELECT research_round_id "
                        "FROM experiment.v022_suite_launch_batch_round "
                        "WHERE suite_launch_batch_id=:batch"
                    ),
                    {"batch": batch_id},
                )
                if bound_round is None:
                    raise RuntimeError("Suite Launch Batch has no Research Round")
                return batch_id, True
            source = (
                connection.execute(
                    text(
                        """
                    SELECT draft.researcher_key,draft.current_revision,draft.status,
                           draft.last_compiled_research_graph_id,
                           graph.frequency,revision_round.research_round_id,
                           research_round.status AS research_round_status
                      FROM workspace.v022_graph_draft draft
                      JOIN workspace.compiled_research_graph graph
                        ON graph.compiled_research_graph_id=
                           draft.last_compiled_research_graph_id
                      JOIN workspace.v022_graph_draft_revision_round revision_round
                        ON revision_round.graph_draft_id=draft.graph_draft_id
                       AND revision_round.revision=:revision
                      JOIN workspace.v022_research_round research_round
                        ON research_round.research_round_id=
                           revision_round.research_round_id
                     WHERE draft.graph_draft_id=:draft
                     FOR UPDATE OF draft
                    """
                    ),
                    {
                        "draft": request.source_graph_draft_id,
                        "revision": request.source_graph_draft_revision,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if source is None:
                raise LookupError("Compiled source Graph Draft not found")
            if source["researcher_key"] != request.actor_key:
                raise ValueError("Suite Launch Batch source owner differs from actor")
            if (
                source["current_revision"] != request.source_graph_draft_revision
                or source["last_compiled_research_graph_id"]
                != request.source_compiled_research_graph_id
            ):
                raise ValueError("Suite Launch Batch requires the exact current compile")
            if source["status"] != "draft":
                raise ValueError("Suite Launch Batch source Draft is not editable")
            if source["research_round_status"] != "active":
                raise ValueError("Suite Launch Batch source Research Round is closed")
            source_frequency = cast(Frequency, source["frequency"])
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.v022_suite_launch_batch (
                      suite_launch_batch_id,actor_key,idempotency_key,
                      source_graph_draft_id,source_graph_draft_revision,
                      source_compiled_research_graph_id,suite_mode,
                      requested_frequencies,batch_fingerprint
                    ) VALUES (
                      :batch,:actor,:key,:draft,:revision,:graph,:mode,
                      CAST(:frequencies AS jsonb),:fingerprint
                    )
                    """
                ),
                {
                    "batch": batch_id,
                    "actor": request.actor_key,
                    "key": request.idempotency_key,
                    "draft": request.source_graph_draft_id,
                    "revision": request.source_graph_draft_revision,
                    "graph": request.source_compiled_research_graph_id,
                    "mode": request.suite_mode,
                    "frequencies": json.dumps(request.frequencies),
                    "fingerprint": fingerprint,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO experiment.v022_suite_launch_batch_round ("
                    "suite_launch_batch_id,research_round_id) VALUES (:batch,:round)"
                ),
                {
                    "batch": batch_id,
                    "round": source["research_round_id"],
                },
            )
            for frequency in request.frequencies:
                is_source = frequency == source_frequency
                connection.execute(
                    text(
                        """
                        INSERT INTO experiment.v022_suite_launch_batch_child (
                          suite_launch_batch_id,frequency,submission_key,
                          graph_draft_id,graph_draft_revision,
                          compiled_research_graph_id
                        ) VALUES (
                          :batch,:frequency,:submission,
                          :draft,:revision,:graph
                        )
                        """
                    ),
                    {
                        "batch": batch_id,
                        "frequency": frequency,
                        "submission": _child_command_id(batch_id, frequency, "suite"),
                        "draft": request.source_graph_draft_id if is_source else None,
                        "revision": (request.source_graph_draft_revision if is_source else None),
                        "graph": (request.source_compiled_research_graph_id if is_source else None),
                    },
                )
        return batch_id, False

    def _prepare_frequency_graphs(
        self,
        batch_id: uuid.UUID,
        request: SuiteLaunchBatchRequest,
    ) -> None:
        for child in self._child_rows(batch_id):
            if child["compiled_research_graph_id"] is not None:
                continue
            frequency = cast(Frequency, child["frequency"])
            self._run_stage(
                batch_id,
                stage="prepare_graph",
                frequency=frequency,
                operation=partial(self._prepare_frequency_graph, batch_id, request, frequency),
            )

    def _prepare_frequency_graph(
        self,
        batch_id: uuid.UUID,
        request: SuiteLaunchBatchRequest,
        frequency: Frequency,
    ) -> None:
        clone = self._drafts.clone_revision(
            request.source_graph_draft_id,
            source_revision=request.source_graph_draft_revision,
            researcher_key=request.actor_key,
            draft_key=f"launch_batch_{batch_id}_{frequency}",
            name=f"Launch Batch {str(batch_id)[:8]} / {frequency}",
            idempotency_key=_child_command_id(batch_id, frequency, "clone"),
        )
        selected = clone
        if clone.intent["frequency"] != frequency:
            selected = self._drafts.apply_event(
                clone.graph_draft_id,
                expected_revision=clone.revision,
                actor_key=request.actor_key,
                idempotency_key=_child_command_id(batch_id, frequency, "frequency"),
                event_type="set_frequency",
                event={"frequency": frequency},
            ).snapshot
        compiled = self._drafts.compile(
            selected.graph_draft_id,
            expected_revision=selected.revision,
            actor_key=request.actor_key,
            idempotency_key=_child_command_id(batch_id, frequency, "compile"),
        )
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE experiment.v022_suite_launch_batch_child
                       SET graph_draft_id=:draft,
                           graph_draft_revision=:revision,
                           compiled_research_graph_id=:graph
                     WHERE suite_launch_batch_id=:batch
                       AND frequency=:frequency
                       AND compiled_research_graph_id IS NULL
                    """
                ),
                {
                    "draft": selected.graph_draft_id,
                    "revision": selected.revision,
                    "graph": compiled.compiled_research_graph_id,
                    "batch": batch_id,
                    "frequency": frequency,
                },
            )

    def _admit_all_frequency_graphs(self, batch_id: uuid.UUID) -> None:
        children = self._child_rows(batch_id)
        if not children or any(item["compiled_research_graph_id"] is None for item in children):
            raise RuntimeError("Suite Launch Batch frequency graphs are incomplete")
        for child in children:
            frequency = cast(Frequency, child["frequency"])
            self._run_stage(
                batch_id,
                stage="admit_graph",
                frequency=frequency,
                operation=partial(self._admit_frequency_graph, child, frequency),
            )

    def _admit_frequency_graph(self, child: RowMapping, frequency: Frequency) -> None:
        with self._engine.connect() as connection:
            _require_rankable_frequency_graph(
                connection,
                compiled_research_graph_id=cast(uuid.UUID, child["compiled_research_graph_id"]),
                frequency=frequency,
            )

    def _submit_missing_suites(
        self,
        batch_id: uuid.UUID,
        request: SuiteLaunchBatchRequest,
    ) -> None:
        for child in self._child_rows(batch_id):
            if child["research_suite_id"] is not None:
                continue
            frequency = cast(Frequency, child["frequency"])
            self._run_stage(
                batch_id,
                stage="submit_suite",
                frequency=frequency,
                operation=partial(self._submit_frequency_suite, batch_id, request, child),
            )

    def _submit_frequency_suite(
        self,
        batch_id: uuid.UUID,
        request: SuiteLaunchBatchRequest,
        child: RowMapping,
    ) -> None:
        graph_id = cast(uuid.UUID, child["compiled_research_graph_id"])
        submission_key = cast(uuid.UUID, child["submission_key"])
        submitted = self._suites.replay(
            actor_key=request.actor_key,
            idempotency_key=submission_key,
            compiled_research_graph_id=graph_id,
            suite_mode=request.suite_mode,
        )
        if submitted is None:
            submitted = self._suites.submit(
                actor_key=request.actor_key,
                idempotency_key=submission_key,
                compiled_research_graph_id=graph_id,
                suite_mode=request.suite_mode,
            )
        research_suite_id = uuid.UUID(str(submitted["research_suite_id"]))
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE experiment.v022_suite_launch_batch_child
                       SET research_suite_id=:suite
                     WHERE suite_launch_batch_id=:batch
                       AND frequency=:frequency
                       AND research_suite_id IS NULL
                    """
                ),
                {
                    "suite": research_suite_id,
                    "batch": batch_id,
                    "frequency": child["frequency"],
                },
            )
        self._drafts.lock_for_experiment(
            cast(uuid.UUID, child["graph_draft_id"]),
            expected_revision=int(child["graph_draft_revision"]),
            actor_key=request.actor_key,
            compiled_research_graph_id=graph_id,
        )

    def _run_stage(
        self,
        batch_id: uuid.UUID,
        *,
        stage: _Stage,
        frequency: Frequency | None,
        operation: Callable[[], _T],
    ) -> _T:
        self._record_event(batch_id, stage=stage, frequency=frequency, outcome="started")
        try:
            result = operation()
        except SuiteLaunchBatchStageError:
            raise
        except Exception as error:
            code = _launch_error_code(error)
            summary = _safe_error_summary(error)
            self._record_event(
                batch_id,
                stage=stage,
                frequency=frequency,
                outcome="failed",
                error_code=code,
                error_summary=summary,
            )
            raise SuiteLaunchBatchStageError(
                code=code,
                stage=stage,
                frequency=frequency,
                summary=summary,
            ) from error
        self._record_event(batch_id, stage=stage, frequency=frequency, outcome="succeeded")
        return result

    def _record_event(
        self,
        batch_id: uuid.UUID,
        *,
        stage: _Stage,
        frequency: Frequency | None,
        outcome: Literal["started", "succeeded", "failed"],
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT suite_launch_batch_id "
                    "FROM experiment.v022_suite_launch_batch "
                    "WHERE suite_launch_batch_id=:batch FOR UPDATE"
                ),
                {"batch": batch_id},
            ).one()
            ordinal = connection.scalar(
                text(
                    "SELECT coalesce(max(ordinal),-1)+1 "
                    "FROM experiment.v022_suite_launch_batch_event "
                    "WHERE suite_launch_batch_id=:batch"
                ),
                {"batch": batch_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.v022_suite_launch_batch_event (
                      suite_launch_batch_id,ordinal,frequency,stage,outcome,
                      error_code,error_summary
                    ) VALUES (
                      :batch,:ordinal,:frequency,:stage,:outcome,:code,:summary
                    )
                    """
                ),
                {
                    "batch": batch_id,
                    "ordinal": ordinal,
                    "frequency": frequency,
                    "stage": stage,
                    "outcome": outcome,
                    "code": error_code,
                    "summary": error_summary,
                },
            )

    def _child_rows(self, batch_id: uuid.UUID) -> tuple[RowMapping, ...]:
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    text(
                        "SELECT * FROM experiment.v022_suite_launch_batch_child "
                        "WHERE suite_launch_batch_id=:batch "
                        "ORDER BY CASE frequency WHEN 'weekly' THEN 1 ELSE 2 END"
                    ),
                    {"batch": batch_id},
                ).mappings()
            )

    def _document(self, batch_id: uuid.UUID, *, reused: bool) -> dict[str, Any]:
        with self._engine.connect() as connection:
            batch = (
                connection.execute(
                    text(
                        "SELECT * FROM experiment.v022_suite_launch_batch "
                        "WHERE suite_launch_batch_id=:batch"
                    ),
                    {"batch": batch_id},
                )
                .mappings()
                .one_or_none()
            )
            latest_event = (
                connection.execute(
                    text(
                        "SELECT * FROM experiment.v022_suite_launch_batch_event "
                        "WHERE suite_launch_batch_id=:batch ORDER BY ordinal DESC LIMIT 1"
                    ),
                    {"batch": batch_id},
                )
                .mappings()
                .one_or_none()
            )
        if batch is None:
            raise LookupError(f"Suite Launch Batch not found: {batch_id}")
        child_documents: list[dict[str, Any]] = []
        for child in self._child_rows(batch_id):
            suite_id = cast(uuid.UUID | None, child["research_suite_id"])
            status = self._suites.status(suite_id) if suite_id is not None else None
            failed_child = (
                latest_event is not None
                and latest_event["outcome"] == "failed"
                and latest_event["frequency"] == child["frequency"]
            )
            failure_event = latest_event if failed_child else None
            child_documents.append(
                {
                    "frequency": child["frequency"],
                    "graph_draft_id": child["graph_draft_id"],
                    "graph_draft_revision": child["graph_draft_revision"],
                    "compiled_research_graph_id": child["compiled_research_graph_id"],
                    "research_suite_id": suite_id,
                    "status": (
                        status["status"]
                        if status is not None
                        else ("failed" if failed_child else "planning")
                    ),
                    "total": status["total"] if status is not None else 0,
                    "terminal": status["terminal"] if status is not None else 0,
                    "status_counts": (status["status_counts"] if status is not None else {}),
                    "complete": status["complete"] if status is not None else False,
                    "stage": (failure_event["stage"] if failure_event is not None else None),
                    "failure_code": (
                        failure_event["error_code"] if failure_event is not None else None
                    ),
                    "failure_summary": (
                        failure_event["error_summary"] if failure_event is not None else None
                    ),
                }
            )
        current_failure = (
            latest_event
            if latest_event is not None and latest_event["outcome"] == "failed"
            else None
        )
        return {
            "suite_launch_batch_id": batch_id,
            "source_graph_draft_id": batch["source_graph_draft_id"],
            "source_graph_draft_revision": batch["source_graph_draft_revision"],
            "batch_fingerprint": batch["batch_fingerprint"],
            "status": ("failed" if current_failure is not None else _batch_status(child_documents)),
            "stage": latest_event["stage"] if latest_event is not None else None,
            "failed_frequency": (
                current_failure["frequency"] if current_failure is not None else None
            ),
            "failure_code": (
                current_failure["error_code"] if current_failure is not None else None
            ),
            "failure_summary": (
                current_failure["error_summary"] if current_failure is not None else None
            ),
            "children": child_documents,
            "reused": reused,
        }


def _normalize_frequencies(values: Sequence[str]) -> tuple[Frequency, ...]:
    requested = set(values)
    if not requested or not requested.issubset(set(_FREQUENCY_ORDER)):
        raise ValueError("Suite Launch Batch frequencies must be weekly and/or monthly")
    return tuple(item for item in _FREQUENCY_ORDER if item in requested)


def _launch_error_code(error: Exception) -> str:
    if isinstance(error, IntegrityError):
        return "suite_launch_integrity_conflict"
    if isinstance(error, LookupError):
        return "suite_launch_identity_missing"
    if isinstance(error, ValueError):
        return "suite_launch_contract_rejected"
    return "suite_launch_stage_failed"


def _safe_error_summary(error: Exception) -> str:
    summary = " ".join(str(error).split()) or type(error).__name__
    return f"{type(error).__name__}: {summary}"[:1000]


def _child_command_id(batch_id: uuid.UUID, frequency: Frequency, role: str) -> uuid.UUID:
    return uuid.uuid5(batch_id, f"{frequency}:{role}")


def _require_rankable_frequency_graph(
    connection: Connection,
    *,
    compiled_research_graph_id: uuid.UUID,
    frequency: Frequency,
) -> None:
    rows = (
        connection.execute(
            text(
                """
            SELECT graph.frequency,graph_artifact.status AS graph_status,
                   cohort.evaluation_cohort_version_id,
                   cohort_artifact.status AS cohort_status,
                   runtime_artifact.status AS runtime_status,
                   gate_artifact.status AS gate_status
              FROM workspace.compiled_research_graph graph
              JOIN lineage.artifact graph_artifact
                ON graph_artifact.artifact_id=graph.artifact_id
              JOIN experiment.v022_evaluation_cohort_version cohort
                ON cohort.frequency=graph.frequency
              JOIN lineage.artifact cohort_artifact
                ON cohort_artifact.artifact_id=cohort.artifact_id
              JOIN experiment.v022_evaluation_cohort_runtime_contract runtime
                ON runtime.evaluation_cohort_version_id=
                   cohort.evaluation_cohort_version_id
              JOIN lineage.artifact runtime_artifact
                ON runtime_artifact.artifact_id=runtime.artifact_id
              JOIN data.v022_dataset_gate_assessment gate
                ON gate.dataset_gate_assessment_id=
                   runtime.dataset_gate_assessment_id
              JOIN lineage.artifact gate_artifact
                ON gate_artifact.artifact_id=gate.artifact_id
             WHERE graph.compiled_research_graph_id=:graph
               AND cohort.version_number=:cohort_version
               AND cohort.cohort_key=(
                   'sp500_free_research_2007_2026_' || graph.frequency ||
                   '_v' || CAST(:cohort_version AS text)
               )
               AND cohort.research_tier='rankable_research'
               AND runtime.ranking_eligibility='rankable_research'
               AND gate.ranking_eligibility=cohort.research_tier
               AND gate.dataset_publication_id=cohort.dataset_publication_id
               AND gate.universe_history_id=cohort.universe_history_id
               AND gate.security_market_quality_report_id=
                   cohort.security_market_quality_report_id
               AND gate.calendar_version_id=cohort.calendar_version_id
            """
            ),
            {
                "graph": compiled_research_graph_id,
                "cohort_version": FROZEN_SP500_COHORT_VERSION,
            },
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise ValueError("Compiled frequency Graph has no exact rankable Evaluation Cohort")
    row = rows[0]
    if (
        row["frequency"] != frequency
        or row["graph_status"] != "published"
        or row["cohort_status"] != "published"
        or row["runtime_status"] != "published"
        or row["gate_status"] != "published"
    ):
        raise ValueError("Compiled frequency Graph or exact Cohort runtime/Gate is not published")


def _batch_status(children: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(item["status"]) for item in children}
    if not children or "planning" in statuses:
        return "planning"
    if statuses == {"completed"}:
        return "completed"
    if "failed" in statuses:
        return "failed"
    if "cancelled" in statuses:
        return "cancelled"
    if statuses == {"not_started"}:
        return "submitted"
    return "running"
