from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

Frequency = Literal["weekly", "monthly"]
ResearchTier = Literal["rankable_research", "exploratory_only"]

_COHORT_CONTRACT = "v0.22.evaluation_cohort.v1"
_REQUIRED_HISTORY_SESSIONS = 504


@dataclass(frozen=True, slots=True)
class EvaluationCohortSpec:
    cohort_key: str
    version_number: int
    research_tier: ResearchTier
    frequency: Frequency
    universe_history_id: uuid.UUID
    dataset_publication_id: uuid.UUID
    benchmark_dataset_publication_id: uuid.UUID
    security_market_quality_report_id: uuid.UUID
    calendar_version_id: uuid.UUID
    warmup_start: date
    evaluation_start: date
    evaluation_end: date
    cost_bps_per_side: Decimal
    created_by: str
    required_history_sessions: int = _REQUIRED_HISTORY_SESSIONS
    execution_delay_sessions: int = 1
    benchmark_key: str = "spy"

    def __post_init__(self) -> None:
        if not self.cohort_key.strip() or not self.created_by.strip():
            raise ValueError("Evaluation Cohort identity is incomplete")
        if self.version_number < 1:
            raise ValueError("Evaluation Cohort version must be positive")
        if self.required_history_sessions != _REQUIRED_HISTORY_SESSIONS:
            raise ValueError("v0.22 rankable Cohorts freeze exactly 504 warmup sessions")
        if self.execution_delay_sessions != 1:
            raise ValueError("v0.22 Evaluation Cohorts freeze next-session execution")
        if self.benchmark_key != "spy":
            raise ValueError("v0.22 Evaluation Cohorts freeze SPY as benchmark")
        if self.cost_bps_per_side < 0:
            raise ValueError("Evaluation cost cannot be negative")
        if not (self.warmup_start < self.evaluation_start <= self.evaluation_end):
            raise ValueError("Evaluation Cohort dates are not ordered")


@dataclass(frozen=True, slots=True)
class CohortSession:
    session_date: date
    session_role: Literal["warmup", "evaluation"]
    is_decision_session: bool


@dataclass(frozen=True, slots=True)
class CohortEligibilityInterval:
    security_id: uuid.UUID
    ordinal: int
    effective_start: date
    effective_end: date
    is_member: bool
    is_warmup_ready: bool
    is_selectable: bool
    is_tradable: bool
    valuation_state: Literal["live", "stale_confirmed", "terminal", "unavailable"]
    reason_codes: tuple[str, ...]
    evidence_artifact_ids: tuple[uuid.UUID, ...]

    @property
    def fingerprint(self) -> str:
        return sha256_hexdigest(asdict(self))


@dataclass(frozen=True, slots=True)
class EvaluationCohortPublication:
    evaluation_cohort_version_id: uuid.UUID
    artifact_id: uuid.UUID
    cohort_fingerprint: str
    session_count: int
    decision_session_count: int
    eligibility_interval_count: int
    reused: bool


@dataclass(frozen=True, slots=True)
class _FrozenInputs:
    history_artifact_id: uuid.UUID
    dataset_artifact_id: uuid.UUID
    benchmark_dataset_artifact_id: uuid.UUID
    quality_report_artifact_id: uuid.UUID
    calendar_artifact_id: uuid.UUID
    sessions: tuple[CohortSession, ...]
    security_ids: tuple[uuid.UUID, ...]
    members_by_session: dict[date, frozenset[uuid.UUID]]
    bars_by_security: dict[uuid.UUID, frozenset[date]]
    uniformly_unavailable: frozenset[uuid.UUID]
    terminal_by_security: dict[uuid.UUID, tuple[RowMapping, ...]]
    price_semantics: str


class EvaluationCohortPublicationService:
    """Publish one fixed-frequency comparison environment without moving its dates."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, spec: EvaluationCohortSpec) -> EvaluationCohortPublication:
        inputs = self._load(spec)
        intervals = _derive_eligibility(spec, inputs)
        session_documents = [asdict(item) for item in inputs.sessions]
        interval_documents = [asdict(item) for item in intervals]
        document = {
            "contract_version": _COHORT_CONTRACT,
            "cohort_key": spec.cohort_key,
            "version_number": spec.version_number,
            "research_tier": spec.research_tier,
            "frequency": spec.frequency,
            "universe_history_id": str(spec.universe_history_id),
            "dataset_publication_id": str(spec.dataset_publication_id),
            "benchmark_dataset_publication_id": str(
                spec.benchmark_dataset_publication_id
            ),
            "security_market_quality_report_id": str(
                spec.security_market_quality_report_id
            ),
            "calendar_version_id": str(spec.calendar_version_id),
            "warmup_start": spec.warmup_start.isoformat(),
            "evaluation_start": spec.evaluation_start.isoformat(),
            "evaluation_end": spec.evaluation_end.isoformat(),
            "required_history_sessions": spec.required_history_sessions,
            "cost_bps_per_side": str(spec.cost_bps_per_side),
            "execution_delay_sessions": spec.execution_delay_sessions,
            "benchmark_key": spec.benchmark_key,
            "price_semantics": inputs.price_semantics,
            "historical_pit_claimed": False,
            "session_count": len(inputs.sessions),
            "decision_session_count": sum(
                item.is_decision_session for item in inputs.sessions
            ),
            "eligibility_interval_count": len(intervals),
            "session_projection_fingerprint": sha256_hexdigest(session_documents),
            "eligibility_projection_fingerprint": sha256_hexdigest(interval_documents),
        }
        fingerprint = sha256_hexdigest(document)
        cohort_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:evaluation-cohort:{fingerprint}"
        )
        dependencies = (
            DependencyInput(inputs.history_artifact_id, "universe_history", 0),
            DependencyInput(inputs.dataset_artifact_id, "market_dataset", 1),
            DependencyInput(inputs.quality_report_artifact_id, "quality_report", 2),
            DependencyInput(inputs.calendar_artifact_id, "calendar_version", 3),
            DependencyInput(
                inputs.benchmark_dataset_artifact_id, "benchmark_dataset", 4
            ),
        )

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            _write_cohort(connection, cohort_id, artifact_id, spec, inputs, intervals, document)

        publication = self._artifacts.publish(
            artifact_type="v022_evaluation_cohort_version",
            artifact_key=f"v022_evaluation_cohort_version__{spec.cohort_key}",
            version_number=spec.version_number,
            semantic_payload=document,
            content_payload={
                "session_projection_fingerprint": document[
                    "session_projection_fingerprint"
                ],
                "eligibility_projection_fingerprint": document[
                    "eligibility_projection_fingerprint"
                ],
            },
            dependencies=dependencies,
            reason=f"publish v0.22 Evaluation Cohort {spec.cohort_key}",
            draft_writer=write,
        )
        return EvaluationCohortPublication(
            cohort_id,
            publication.artifact_id,
            fingerprint,
            len(inputs.sessions),
            cast(int, document["decision_session_count"]),
            len(intervals),
            publication.reused,
        )

    def bind_suite(
        self,
        *,
        research_suite_id: uuid.UUID,
        evaluation_cohort_version_id: uuid.UUID,
        frequency: Frequency,
        bound_by: str,
    ) -> str:
        if not bound_by.strip():
            raise ValueError("Suite Cohort binding actor is required")
        document = {
            "contract_version": "v0.22.suite_evaluation_cohort_binding.v1",
            "research_suite_id": str(research_suite_id),
            "evaluation_cohort_version_id": str(evaluation_cohort_version_id),
            "frequency": frequency,
        }
        fingerprint = sha256_hexdigest(document)
        with self._engine.begin() as connection:
            existing = connection.execute(
                text(
                    "SELECT binding_fingerprint FROM "
                    "experiment.v022_research_suite_evaluation_cohort_binding "
                    "WHERE research_suite_id=:suite"
                ),
                {"suite": research_suite_id},
            ).scalar_one_or_none()
            if existing is not None:
                if existing != fingerprint:
                    raise ValueError("Research Suite is already bound to another Cohort")
                return cast(str, existing)
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.v022_research_suite_evaluation_cohort_binding (
                      research_suite_id,evaluation_cohort_version_id,frequency,
                      binding_fingerprint,bound_by
                    ) VALUES (:suite,:cohort,:frequency,:fingerprint,:bound_by)
                    """
                ),
                {
                    "suite": research_suite_id,
                    "cohort": evaluation_cohort_version_id,
                    "frequency": frequency,
                    "fingerprint": fingerprint,
                    "bound_by": bound_by,
                },
            )
        return fingerprint

    def _load(self, spec: EvaluationCohortSpec) -> _FrozenInputs:
        with self._engine.connect() as connection:
            identity = _identity(connection, spec)
            sessions = _sessions(connection, spec)
            snapshots = _snapshots(connection, spec, sessions)
            security_ids = tuple(
                sorted(
                    {item for members in snapshots.values() for item in members},
                    key=str,
                )
            )
            bars = _bars(connection, spec.dataset_publication_id, security_ids)
            unavailable = _unavailable(identity["report_document"])
            terminal = _terminal_events(connection, security_ids, spec)
        if spec.research_tier == "rankable_research":
            unresolved = [
                row
                for rows in terminal.values()
                for row in rows
                if row["status"] != "confirmed"
            ]
            if unresolved:
                raise ValueError(
                    "Rankable Evaluation Cohort cannot contain unresolved Terminal Events"
                )
        return _FrozenInputs(
            identity["history_artifact_id"],
            identity["dataset_artifact_id"],
            identity["benchmark_dataset_artifact_id"],
            identity["quality_report_artifact_id"],
            identity["calendar_artifact_id"],
            sessions,
            security_ids,
            snapshots,
            bars,
            unavailable,
            terminal,
            _require_bound_price_semantics(identity["price_semantics"]),
        )


def _identity(connection: Connection, spec: EvaluationCohortSpec) -> RowMapping:
    row = connection.execute(
        text(
            """
            SELECT history.artifact_id AS history_artifact_id,
                   history_artifact.status AS history_status,
                   ledger.research_tier AS history_tier,
                   (SELECT count(*)
                      FROM catalog.v022_universe_change_batch batch
                     WHERE batch.universe_membership_ledger_id=
                           ledger.universe_membership_ledger_id
                       AND batch.evidence_status<>'confirmed') AS
                     unresolved_membership_count,
                   dataset.artifact_id AS dataset_artifact_id,
                   dataset.coverage_start,dataset.coverage_end,
                   dataset_artifact.status AS dataset_status,
                   benchmark.artifact_id AS benchmark_dataset_artifact_id,
                   benchmark.coverage_start AS benchmark_coverage_start,
                   benchmark.coverage_end AS benchmark_coverage_end,
                   benchmark_artifact.status AS benchmark_dataset_status,
                   report.artifact_id AS quality_report_artifact_id,
                   report.error_count,report.research_tier AS report_tier,
                   report.report_document,report_artifact.status AS report_status,
                   calendar.artifact_id AS calendar_artifact_id,
                   calendar_artifact.status AS calendar_status,
                   CASE
                     WHEN reconciled.dataset_publication_id IS NOT NULL
                       THEN reconciled.price_semantics
                     WHEN report.source_dataset_publication_id=
                            dataset.dataset_publication_id
                      AND report.source_dataset_artifact_id=dataset.artifact_id
                       THEN report.report_document->>'price_semantics'
                     ELSE market_binding.price_semantics
                   END AS price_semantics
              FROM catalog.universe_history history
              JOIN lineage.artifact history_artifact
                ON history_artifact.artifact_id=history.artifact_id
              JOIN catalog.v022_universe_history_ledger_binding history_binding
                ON history_binding.universe_history_id=history.universe_history_id
              JOIN catalog.v022_universe_membership_ledger ledger
                ON ledger.universe_membership_ledger_id=
                   history_binding.universe_membership_ledger_id
              JOIN data.dataset_publication dataset
                ON dataset.dataset_publication_id=:dataset
              JOIN lineage.artifact dataset_artifact
                ON dataset_artifact.artifact_id=dataset.artifact_id
              JOIN data.dataset_publication benchmark
                ON benchmark.dataset_publication_id=:benchmark_dataset
               AND benchmark.value_kind='daily_bar'
               AND benchmark.calendar_version_id=:calendar
              JOIN lineage.artifact benchmark_artifact
                ON benchmark_artifact.artifact_id=benchmark.artifact_id
              JOIN catalog.asset benchmark_asset
                ON benchmark_asset.asset_key='spy'
              JOIN data.v022_security_market_quality_report report
                ON report.security_market_quality_report_id=:report
              JOIN lineage.artifact report_artifact
                ON report_artifact.artifact_id=report.artifact_id
              LEFT JOIN data.v022_reconciled_market_dataset_binding reconciled
                ON reconciled.dataset_publication_id=dataset.dataset_publication_id
               AND reconciled.dataset_artifact_id=dataset.artifact_id
              LEFT JOIN data.v022_security_market_dataset_binding market_binding
                ON market_binding.dataset_publication_id=COALESCE(
                     reconciled.primary_dataset_publication_id,
                     dataset.dataset_publication_id)
               AND market_binding.security_market_quality_report_id=
                   report.security_market_quality_report_id
              JOIN catalog.calendar_version calendar
                ON calendar.calendar_version_id=:calendar
               AND dataset.calendar_version_id=calendar.calendar_version_id
              JOIN lineage.artifact calendar_artifact
                ON calendar_artifact.artifact_id=calendar.artifact_id
             WHERE history.universe_history_id=:history
               AND EXISTS (
                 SELECT 1 FROM data.daily_bar benchmark_bar
                  WHERE benchmark_bar.dataset_publication_id=
                        benchmark.dataset_publication_id
                    AND benchmark_bar.asset_id=benchmark_asset.asset_id
                    AND benchmark_bar.session_date BETWEEN :warmup AND :evaluation_end
               )
            """
        ),
        {
            "history": spec.universe_history_id,
            "dataset": spec.dataset_publication_id,
            "benchmark_dataset": spec.benchmark_dataset_publication_id,
            "report": spec.security_market_quality_report_id,
            "calendar": spec.calendar_version_id,
            "warmup": spec.warmup_start,
            "evaluation_end": spec.evaluation_end,
        },
    ).mappings().one_or_none()
    if row is None:
        raise LookupError("Evaluation Cohort frozen inputs are not exactly bound")
    if any(
        row[key] != "published"
        for key in (
            "history_status",
            "dataset_status",
            "benchmark_dataset_status",
            "report_status",
            "calendar_status",
        )
    ):
        raise ValueError("Evaluation Cohort inputs must be published")
    if row["error_count"] != 0:
        raise ValueError("Evaluation Cohort market quality report contains errors")
    if spec.research_tier == "rankable_research" and (
        row["history_tier"] != "rankable_research"
        or row["report_tier"] != "rankable_research"
        or row["unresolved_membership_count"] != 0
    ):
        raise ValueError("Rankable Cohort requires rankable Universe and market evidence")
    if row["coverage_start"] > spec.warmup_start or row["coverage_end"] < spec.evaluation_end:
        raise ValueError("Evaluation Cohort market Dataset does not cover the frozen range")
    if (
        row["benchmark_coverage_start"] > spec.warmup_start
        or row["benchmark_coverage_end"] < spec.evaluation_end
    ):
        raise ValueError("Evaluation Cohort SPY Dataset does not cover the frozen range")
    _require_bound_price_semantics(row["price_semantics"])
    return row


def _require_bound_price_semantics(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Evaluation Cohort risk Dataset price semantics are not exact")
    return value


def _sessions(connection: Connection, spec: EvaluationCohortSpec) -> tuple[CohortSession, ...]:
    rows = tuple(
        cast(date, item)
        for item in connection.execute(
            text(
                """
                SELECT session_date FROM catalog.calendar_session
                 WHERE calendar_version_id=:calendar
                   AND session_date BETWEEN :start AND :end
                 ORDER BY session_date
                """
            ),
            {
                "calendar": spec.calendar_version_id,
                "start": spec.warmup_start,
                "end": spec.evaluation_end,
            },
        ).scalars()
    )
    if not rows or rows[0] != spec.warmup_start or rows[-1] != spec.evaluation_end:
        raise ValueError("Frozen Cohort endpoints must be exact completed XNYS sessions")
    warmup = tuple(item for item in rows if item < spec.evaluation_start)
    evaluation = tuple(item for item in rows if item >= spec.evaluation_start)
    if len(warmup) != spec.required_history_sessions:
        raise ValueError(
            "Frozen Cohort must contain exactly 504 completed warmup sessions; dates are not moved"
        )
    if not evaluation or evaluation[0] != spec.evaluation_start:
        raise ValueError("Evaluation start must be an exact completed XNYS session")
    decisions = _decision_sessions(evaluation, spec.frequency)
    return tuple(
        CohortSession(
            item,
            "warmup" if item < spec.evaluation_start else "evaluation",
            item in decisions,
        )
        for item in rows
    )


def _decision_sessions(sessions: tuple[date, ...], frequency: Frequency) -> frozenset[date]:
    if not sessions:
        return frozenset()
    grouped: dict[tuple[int, int], date] = {}
    for session in sessions:
        if frequency == "weekly":
            iso = session.isocalendar()
            key = (iso.year, iso.week)
        else:
            key = (session.year, session.month)
        grouped[key] = session
    decisions = set(grouped.values())
    # Decisions execute on the next completed session.  The frozen evaluation
    # endpoint has no in-cohort successor and therefore cannot be executable.
    decisions.discard(sessions[-1])
    return frozenset(decisions)


def _snapshots(
    connection: Connection,
    spec: EvaluationCohortSpec,
    sessions: tuple[CohortSession, ...],
) -> dict[date, frozenset[uuid.UUID]]:
    rows = connection.execute(
        text(
            """
            SELECT snapshot.effective_session,member.security_id
              FROM catalog.universe_snapshot snapshot
              JOIN catalog.universe_snapshot_member member
                ON member.universe_snapshot_id=snapshot.universe_snapshot_id
             WHERE snapshot.universe_history_id=:history
               AND snapshot.effective_session<=:end
             ORDER BY snapshot.effective_session,member.security_id
            """
        ),
        {"history": spec.universe_history_id, "end": spec.evaluation_end},
    ).all()
    by_effective: dict[date, set[uuid.UUID]] = defaultdict(set)
    for effective, security_id in rows:
        by_effective[cast(date, effective)].add(cast(uuid.UUID, security_id))
    effective_sessions = tuple(sorted(by_effective))
    result: dict[date, frozenset[uuid.UUID]] = {}
    cursor = -1
    active: frozenset[uuid.UUID] | None = None
    for item in sessions:
        while (
            cursor + 1 < len(effective_sessions)
            and effective_sessions[cursor + 1] <= item.session_date
        ):
            cursor += 1
            active = frozenset(by_effective[effective_sessions[cursor]])
        if active is None:
            raise ValueError("Historical S&P membership does not cover Cohort warmup start")
        result[item.session_date] = active
    return result


def _bars(
    connection: Connection,
    dataset_publication_id: uuid.UUID,
    security_ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, frozenset[date]]:
    if not security_ids:
        raise ValueError("Evaluation Cohort Universe contains no Securities")
    rows = connection.execute(
        text(
            """
            SELECT security.security_id,bar.session_date
              FROM catalog.security security
              JOIN data.daily_bar bar ON bar.asset_id=security.legacy_asset_id
             WHERE bar.dataset_publication_id=:dataset
               AND security.security_id IN :security_ids
               AND bar.volume_raw>0
               AND LEAST(
                     bar.open_raw,bar.high_raw,bar.low_raw,bar.close_raw,
                     bar.adj_close,bar.open_adj,bar.high_adj,bar.low_adj,
                     bar.close_adj,bar.adjustment_factor
                   )>0
             ORDER BY security.security_id,bar.session_date
            """
        ).bindparams(bindparam("security_ids", expanding=True)),
        {"dataset": dataset_publication_id, "security_ids": security_ids},
    ).all()
    result: dict[uuid.UUID, set[date]] = defaultdict(set)
    for security_id, session in rows:
        result[cast(uuid.UUID, security_id)].add(cast(date, session))
    return {key: frozenset(value) for key, value in result.items()}


def _unavailable(document: object) -> frozenset[uuid.UUID]:
    if not isinstance(document, dict):
        raise ValueError("Security market quality report is malformed")
    issues = document.get("issues")
    if not isinstance(issues, list):
        raise ValueError("Security market quality report issues are missing")
    result: set[uuid.UUID] = set()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if issue.get("rule_code") != "security_uniformly_excluded_provider_unavailable":
            continue
        subject = issue.get("subject_key")
        if not isinstance(subject, str):
            raise ValueError("Uniform provider exclusion lacks a Security identity")
        result.add(uuid.UUID(subject))
    return frozenset(result)


def _terminal_events(
    connection: Connection,
    security_ids: tuple[uuid.UUID, ...],
    spec: EvaluationCohortSpec,
) -> dict[uuid.UUID, tuple[RowMapping, ...]]:
    rows = connection.execute(
        text(
            """
            SELECT event.security_id,event.effective_session,event.status,event.artifact_id,
                   event.event_type
              FROM catalog.security_terminal_event event
              JOIN catalog.v022_security_terminal_event_evidence_binding binding
                ON binding.security_terminal_event_id=event.security_terminal_event_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=event.artifact_id
             WHERE event.security_id IN :security_ids
               AND event.effective_session BETWEEN :start AND :end
               AND artifact.status='published'
             ORDER BY event.security_id,event.effective_session,event.security_terminal_event_id
            """
        ).bindparams(bindparam("security_ids", expanding=True)),
        {
            "security_ids": security_ids,
            "start": spec.warmup_start,
            "end": spec.evaluation_end,
        },
    ).mappings().all()
    grouped: dict[uuid.UUID, list[RowMapping]] = defaultdict(list)
    for row in rows:
        grouped[cast(uuid.UUID, row["security_id"])].append(row)
    return {key: tuple(value) for key, value in grouped.items()}


def _derive_eligibility(
    spec: EvaluationCohortSpec,
    inputs: _FrozenInputs,
) -> tuple[CohortEligibilityInterval, ...]:
    intervals: list[CohortEligibilityInterval] = []
    core_evidence = (
        inputs.history_artifact_id,
        inputs.dataset_artifact_id,
        inputs.quality_report_artifact_id,
        inputs.calendar_artifact_id,
    )
    for security_id in inputs.security_ids:
        bars = inputs.bars_by_security.get(security_id, frozenset())
        usable_streak = 0
        terminal_rows = inputs.terminal_by_security.get(security_id, ())
        status_rows: list[tuple[date, tuple[object, ...]]] = []
        for session in inputs.sessions:
            session_date = session.session_date
            if session_date in bars:
                usable_streak += 1
            else:
                usable_streak = 0
            terminal = next(
                (
                    row
                    for row in reversed(terminal_rows)
                    if row["effective_session"] <= session_date
                ),
                None,
            )
            member = security_id in inputs.members_by_session[session_date]
            unavailable = security_id in inputs.uniformly_unavailable
            ready = usable_streak >= spec.required_history_sessions and not unavailable
            if terminal is not None:
                valuation = "terminal"
            elif unavailable:
                valuation = "unavailable"
            elif session_date in bars:
                valuation = "live"
            else:
                valuation = "unavailable"
            # Index membership controls candidate selection, not whether an
            # already-held security can be traded.  A security that leaves the
            # S&P 500 remains live/tradable until lifecycle evidence says
            # otherwise, allowing the next rebalance to close the position.
            tradable = valuation == "live"
            selectable = member and ready and tradable and session.session_role == "evaluation"
            reasons: list[str] = []
            if not member:
                reasons.append("not_sp500_member")
            if unavailable:
                reasons.append("provider_unavailable_uniform_exclusion")
            elif not ready:
                reasons.append("warmup_504_incomplete")
            if terminal is not None:
                reasons.append(f"terminal_{terminal['event_type']}")
            evidence = core_evidence + (
                () if terminal is None else (cast(uuid.UUID, terminal["artifact_id"]),)
            )
            status_rows.append(
                (
                    session_date,
                    (
                        member,
                        ready,
                        selectable,
                        tradable,
                        valuation,
                        tuple(reasons),
                        evidence,
                    ),
                )
            )
        intervals.extend(_compress_security(security_id, status_rows))
    return tuple(intervals)


def _compress_security(
    security_id: uuid.UUID,
    rows: list[tuple[date, tuple[object, ...]]],
) -> tuple[CohortEligibilityInterval, ...]:
    if not rows:
        return ()
    result: list[CohortEligibilityInterval] = []
    start = rows[0][0]
    current = rows[0][1]
    ordinal = 0
    for index in range(1, len(rows) + 1):
        if index < len(rows) and rows[index][1] == current:
            continue
        end = rows[index - 1][0]
        member, ready, selectable, tradable, valuation, reasons, evidence = current
        result.append(
            CohortEligibilityInterval(
                security_id,
                ordinal,
                start,
                end,
                cast(bool, member),
                cast(bool, ready),
                cast(bool, selectable),
                cast(bool, tradable),
                cast(
                    Literal["live", "stale_confirmed", "terminal", "unavailable"],
                    valuation,
                ),
                cast(tuple[str, ...], reasons),
                cast(tuple[uuid.UUID, ...], evidence),
            )
        )
        ordinal += 1
        if index < len(rows):
            start = rows[index][0]
            current = rows[index][1]
    return tuple(result)


def _write_cohort(
    connection: Connection,
    cohort_id: uuid.UUID,
    artifact_id: uuid.UUID,
    spec: EvaluationCohortSpec,
    inputs: _FrozenInputs,
    intervals: tuple[CohortEligibilityInterval, ...],
    document: dict[str, object],
) -> None:
    fingerprint = sha256_hexdigest(document)
    connection.execute(
        text(
            """
            INSERT INTO experiment.v022_evaluation_cohort_version (
              evaluation_cohort_version_id,artifact_id,cohort_key,version_number,
              research_tier,frequency,universe_history_id,universe_history_artifact_id,
              dataset_publication_id,dataset_artifact_id,
              benchmark_dataset_publication_id,benchmark_dataset_artifact_id,
              security_market_quality_report_id,quality_report_artifact_id,
              calendar_version_id,calendar_artifact_id,warmup_start,evaluation_start,
              evaluation_end,required_history_sessions,cost_bps_per_side,
              execution_delay_sessions,benchmark_key,price_semantics,
              historical_pit_claimed,session_count,decision_session_count,
              eligibility_interval_count,cohort_document,cohort_fingerprint,created_by
            ) VALUES (
              :id,:artifact,:key,:version,:tier,:frequency,:history,:history_artifact,
              :dataset,:dataset_artifact,:benchmark_dataset,:benchmark_dataset_artifact,
              :report,:report_artifact,:calendar,
              :calendar_artifact,:warmup,:evaluation_start,:evaluation_end,:history_sessions,
              :cost,:delay,:benchmark,:price_semantics,false,:session_count,
              :decision_count,:interval_count,CAST(:document AS jsonb),:fingerprint,:created_by
            )
            """
        ),
        {
            "id": cohort_id,
            "artifact": artifact_id,
            "key": spec.cohort_key,
            "version": spec.version_number,
            "tier": spec.research_tier,
            "frequency": spec.frequency,
            "history": spec.universe_history_id,
            "history_artifact": inputs.history_artifact_id,
            "dataset": spec.dataset_publication_id,
            "dataset_artifact": inputs.dataset_artifact_id,
            "benchmark_dataset": spec.benchmark_dataset_publication_id,
            "benchmark_dataset_artifact": inputs.benchmark_dataset_artifact_id,
            "report": spec.security_market_quality_report_id,
            "report_artifact": inputs.quality_report_artifact_id,
            "calendar": spec.calendar_version_id,
            "calendar_artifact": inputs.calendar_artifact_id,
            "warmup": spec.warmup_start,
            "evaluation_start": spec.evaluation_start,
            "evaluation_end": spec.evaluation_end,
            "history_sessions": spec.required_history_sessions,
            "cost": spec.cost_bps_per_side,
            "delay": spec.execution_delay_sessions,
            "benchmark": spec.benchmark_key,
            "price_semantics": inputs.price_semantics,
            "session_count": len(inputs.sessions),
            "decision_count": sum(item.is_decision_session for item in inputs.sessions),
            "interval_count": len(intervals),
            "document": json.dumps(document, sort_keys=True),
            "fingerprint": fingerprint,
            "created_by": spec.created_by,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO experiment.v022_evaluation_cohort_session (
              evaluation_cohort_version_id,ordinal,session_date,session_role,
              is_decision_session
            ) VALUES (:cohort,:ordinal,:session,:role,:decision)
            """
        ),
        [
            {
                "cohort": cohort_id,
                "ordinal": ordinal,
                "session": item.session_date,
                "role": item.session_role,
                "decision": item.is_decision_session,
            }
            for ordinal, item in enumerate(inputs.sessions)
        ],
    )
    connection.execute(
        text(
            """
            INSERT INTO experiment.v022_cohort_eligibility_interval (
              cohort_eligibility_interval_id,evaluation_cohort_version_id,security_id,
              ordinal,effective_start,effective_end,is_member,is_warmup_ready,
              is_selectable,is_tradable,valuation_state,reason_codes,
              evidence_artifact_ids,interval_fingerprint
            ) VALUES (
              :id,:cohort,:security,:ordinal,:start,:end,:member,:ready,:selectable,
              :tradable,:valuation,CAST(:reasons AS jsonb),CAST(:evidence AS jsonb),
              :fingerprint
            )
            """
        ),
        [
            {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"bird:v0.22:cohort-eligibility:{cohort_id}:{item.security_id}:{item.ordinal}",
                ),
                "cohort": cohort_id,
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
                "fingerprint": sha256_hexdigest(
                    {
                        "evaluation_cohort_version_id": cohort_id,
                        "interval_fingerprint": item.fingerprint,
                    }
                ),
            }
            for item in intervals
        ],
    )
