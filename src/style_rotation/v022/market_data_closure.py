from __future__ import annotations

import json
import uuid
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

Disposition = Literal["blocker", "exclude_candidate", "review"]
SessionRole = Literal["warmup", "evaluation"]

_SCHEMA_VERSION = "v0.22.market_data_closure_audit.v1"
_LARGE_RETURN_THRESHOLD = Decimal("0.50")
_SETTLEMENT_EVENT_TYPES = frozenset(
    {
        "delisting",
        "cash_merger",
        "stock_merger",
        "share_class_conversion",
        "spinoff",
        "bankruptcy",
        "liquidation",
    }
)
_TERMINAL_EVENT_TYPES = frozenset(
    {
        "delisting",
        "cash_merger",
        "stock_merger",
        "share_class_conversion",
        "bankruptcy",
        "liquidation",
    }
)


@dataclass(frozen=True, slots=True)
class ClosureSession:
    session_date: date
    session_role: SessionRole
    is_decision_session: bool


@dataclass(frozen=True, slots=True)
class ClosureMaskInterval:
    security_id: uuid.UUID
    effective_start: date
    effective_end: date
    is_selectable: bool
    is_tradable: bool

    def __post_init__(self) -> None:
        if self.effective_start > self.effective_end:
            raise ValueError("Closure mask interval is reversed")


@dataclass(frozen=True, slots=True)
class ClosureBar:
    security_id: uuid.UUID
    session_date: date
    open_raw: Decimal
    high_raw: Decimal
    low_raw: Decimal
    close_raw: Decimal
    open_adj: Decimal
    high_adj: Decimal
    low_adj: Decimal
    close_adj: Decimal
    volume_raw: int


@dataclass(frozen=True, slots=True)
class ClosureLifecycleEvent:
    security_id: uuid.UUID
    lifecycle_event_id: uuid.UUID
    event_type: str
    event_status: str
    effective_session: date
    settlement_session: date | None
    declared_settlement_leg_count: int
    actual_settlement_leg_count: int
    artifact_status: str = "published"


@dataclass(frozen=True, slots=True)
class ClosureSettlementInstruction:
    lifecycle_event_id: uuid.UUID
    settlement_session: date
    leg_count: int


@dataclass(frozen=True, slots=True)
class ClosureAuditInput:
    dataset_publication_id: uuid.UUID
    evaluation_cohort_version_id: uuid.UUID
    evaluation_cohort_runtime_contract_id: uuid.UUID | None
    required_history_sessions: int
    sessions: tuple[ClosureSession, ...]
    mask_intervals: tuple[ClosureMaskInterval, ...]
    bars: tuple[ClosureBar, ...]
    lifecycle_events: tuple[ClosureLifecycleEvent, ...] = ()
    settlement_instructions: tuple[ClosureSettlementInstruction, ...] = ()

    def __post_init__(self) -> None:
        if self.required_history_sessions < 1:
            raise ValueError("Closure audit requires positive consecutive warm-up sessions")
        dates = tuple(item.session_date for item in self.sessions)
        if not dates or dates != tuple(sorted(set(dates))):
            raise ValueError("Closure audit sessions must be nonempty, unique and ordered")


@dataclass(frozen=True, slots=True)
class ClosureAuditIssue:
    disposition: Disposition
    rule_code: str
    message: str
    security_id: str | None
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class ClosureAuditPass:
    rule_code: str
    message: str
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class MarketDataClosureAuditReport:
    schema_version: str
    dataset_publication_id: str
    evaluation_cohort_version_id: str
    evaluation_cohort_runtime_contract_id: str | None
    coverage_start: str
    coverage_end: str
    security_count: int
    session_count: int
    bar_count: int
    passed: bool
    blockers: tuple[ClosureAuditIssue, ...]
    exclude_candidates: tuple[ClosureAuditIssue, ...]
    review_findings: tuple[ClosureAuditIssue, ...]
    passes: tuple[ClosureAuditPass, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return only JSON-native values; suitable for a report file or API response."""
        return cast(dict[str, Any], json.loads(json.dumps(asdict(self), sort_keys=True)))


@dataclass(frozen=True, slots=True)
class _AuditHeader:
    dataset_publication_id: uuid.UUID
    evaluation_cohort_version_id: uuid.UUID
    evaluation_cohort_runtime_contract_id: uuid.UUID | None
    required_history_sessions: int
    sessions: tuple[ClosureSession, ...]


@dataclass(frozen=True, slots=True)
class _MaskState:
    is_selectable: bool
    is_tradable: bool


@dataclass(slots=True)
class _Accumulator:
    issues: list[ClosureAuditIssue]
    bar_count: int = 0


def audit_market_data_closure(inputs: ClosureAuditInput) -> MarketDataClosureAuditReport:
    """Audit a fully specified in-memory closure without performing any writes."""
    masks = _group_masks(inputs.mask_intervals)
    events = _group_events(inputs.lifecycle_events)
    bars = _group_bars(inputs.bars)
    security_ids = tuple(sorted(set(masks) | set(bars) | set(events), key=str))
    if not security_ids:
        raise ValueError("Closure audit contains no Securities")
    header = _AuditHeader(
        inputs.dataset_publication_id,
        inputs.evaluation_cohort_version_id,
        inputs.evaluation_cohort_runtime_contract_id,
        inputs.required_history_sessions,
        inputs.sessions,
    )
    accumulator = _Accumulator([])
    instructions = {
        item.lifecycle_event_id: item for item in inputs.settlement_instructions
    }
    if len(instructions) != len(inputs.settlement_instructions):
        raise ValueError("Closure settlement instructions contain duplicate events")
    _audit_lifecycle(
        header,
        events=events,
        instructions=instructions,
        accumulator=accumulator,
    )
    for security_id in security_ids:
        _audit_security(
            header,
            security_id=security_id,
            masks=masks.get(security_id, ()),
            bars=bars.get(security_id, ()),
            lifecycle_events=events.get(security_id, ()),
            accumulator=accumulator,
        )
    return _final_report(header, security_ids, accumulator)


class MarketDataClosureAuditor:
    """Read-only closure audit bound to one immutable Dataset and Cohort contract."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def audit(
        self,
        *,
        dataset_publication_id: uuid.UUID,
        evaluation_cohort_version_id: uuid.UUID | None = None,
        evaluation_cohort_runtime_contract_id: uuid.UUID | None = None,
    ) -> MarketDataClosureAuditReport:
        """Audit exact IDs; callers must choose either a Cohort or its runtime contract."""
        if (evaluation_cohort_version_id is None) == (
            evaluation_cohort_runtime_contract_id is None
        ):
            raise ValueError(
                "Provide exactly one Evaluation Cohort or Cohort runtime contract identity"
            )
        with self._engine.connect() as connection:
            header, runtime_masks = _load_header(
                connection,
                dataset_publication_id=dataset_publication_id,
                evaluation_cohort_version_id=evaluation_cohort_version_id,
                evaluation_cohort_runtime_contract_id=(
                    evaluation_cohort_runtime_contract_id
                ),
            )
            return self._audit_loaded(connection, header, runtime_masks)

    def audit_candidate_against_reference_cohort(
        self,
        *,
        candidate_dataset_publication_id: uuid.UUID,
        reference_evaluation_cohort_version_id: uuid.UUID | None = None,
        reference_evaluation_cohort_runtime_contract_id: uuid.UUID | None = None,
    ) -> MarketDataClosureAuditReport:
        """Audit a published candidate Dataset with an independent frozen Cohort shape.

        This is the explicit repair-review path. The report identity remains the
        candidate Dataset; only sessions, masks, lifecycle and settlement closure are
        borrowed from the named reference Cohort. The regular ``audit`` method keeps
        requiring an exact Dataset/Cohort binding.
        """
        if (reference_evaluation_cohort_version_id is None) == (
            reference_evaluation_cohort_runtime_contract_id is None
        ):
            raise ValueError(
                "Provide exactly one reference Cohort or runtime contract identity"
            )
        with self._engine.connect() as connection:
            _require_published_candidate_dataset(
                connection, candidate_dataset_publication_id
            )
            header, runtime_masks = _load_header(
                connection,
                dataset_publication_id=candidate_dataset_publication_id,
                evaluation_cohort_version_id=(
                    reference_evaluation_cohort_version_id
                ),
                evaluation_cohort_runtime_contract_id=(
                    reference_evaluation_cohort_runtime_contract_id
                ),
                require_dataset_match=False,
            )
            return self._audit_loaded(connection, header, runtime_masks)

    def _audit_loaded(
        self,
        connection: Connection,
        header: _AuditHeader,
        runtime_masks: bool,
    ) -> MarketDataClosureAuditReport:
        masks = _load_masks(
            connection,
            cohort_version_id=header.evaluation_cohort_version_id,
            runtime_contract_id=header.evaluation_cohort_runtime_contract_id,
            runtime_masks=runtime_masks,
        )
        if not masks:
            raise ValueError("Reference Cohort closure contains no mask intervals")
        security_ids = tuple(sorted(masks, key=str))
        events = _load_lifecycle_events(
            connection,
            security_ids=security_ids,
            evaluation_end=header.sessions[-1].session_date,
        )
        instructions = _load_settlement_instructions(
            connection,
            runtime_contract_id=header.evaluation_cohort_runtime_contract_id,
        )
        accumulator = _Accumulator([])
        _audit_lifecycle(
            header,
            events=events,
            instructions=instructions,
            accumulator=accumulator,
        )
        grouped_bars = _stream_bars(
            connection,
            dataset_publication_id=header.dataset_publication_id,
            security_ids=security_ids,
            coverage_start=header.sessions[0].session_date,
            coverage_end=header.sessions[-1].session_date,
        )
        current = next(grouped_bars, None)
        for security_id in security_ids:
            security_bars: tuple[ClosureBar, ...] = ()
            if current is not None and current[0] == security_id:
                security_bars = current[1]
                current = next(grouped_bars, None)
            _audit_security(
                header,
                security_id=security_id,
                masks=masks[security_id],
                bars=security_bars,
                lifecycle_events=events.get(security_id, ()),
                accumulator=accumulator,
            )
        if current is not None:
            raise ValueError("Dataset returned a Security outside the reference Cohort mask")
        return _final_report(header, security_ids, accumulator)


def _group_masks(
    rows: Iterable[ClosureMaskInterval],
) -> dict[uuid.UUID, tuple[ClosureMaskInterval, ...]]:
    grouped: dict[uuid.UUID, list[ClosureMaskInterval]] = defaultdict(list)
    for item in rows:
        grouped[item.security_id].append(item)
    result: dict[uuid.UUID, tuple[ClosureMaskInterval, ...]] = {}
    for security_id, items in grouped.items():
        ordered = tuple(sorted(items, key=lambda item: item.effective_start))
        for prior, current in zip(ordered, ordered[1:], strict=False):
            if prior.effective_end >= current.effective_start:
                raise ValueError(f"Closure mask intervals overlap for Security {security_id}")
        result[security_id] = ordered
    return result


def _group_bars(rows: Iterable[ClosureBar]) -> dict[uuid.UUID, tuple[ClosureBar, ...]]:
    grouped: dict[uuid.UUID, list[ClosureBar]] = defaultdict(list)
    seen: set[tuple[uuid.UUID, date]] = set()
    for item in rows:
        identity = (item.security_id, item.session_date)
        if identity in seen:
            raise ValueError("Closure bars contain a duplicate Security session")
        seen.add(identity)
        grouped[item.security_id].append(item)
    return {
        key: tuple(sorted(value, key=lambda item: item.session_date))
        for key, value in grouped.items()
    }


def _group_events(
    rows: Iterable[ClosureLifecycleEvent],
) -> dict[uuid.UUID, tuple[ClosureLifecycleEvent, ...]]:
    grouped: dict[uuid.UUID, list[ClosureLifecycleEvent]] = defaultdict(list)
    seen: set[uuid.UUID] = set()
    for item in rows:
        if item.lifecycle_event_id in seen:
            raise ValueError("Closure lifecycle events contain a duplicate identity")
        seen.add(item.lifecycle_event_id)
        grouped[item.security_id].append(item)
    return {
        key: tuple(sorted(value, key=lambda item: item.effective_session))
        for key, value in grouped.items()
    }


def _audit_security(
    header: _AuditHeader,
    *,
    security_id: uuid.UUID,
    masks: Sequence[ClosureMaskInterval],
    bars: Sequence[ClosureBar],
    lifecycle_events: Sequence[ClosureLifecycleEvent],
    accumulator: _Accumulator,
) -> None:
    accumulator.bar_count += len(bars)
    dates = tuple(item.session_date for item in header.sessions)
    date_indexes = {item: ordinal for ordinal, item in enumerate(dates)}
    states = _states_by_session(dates, masks)
    bars_by_date = {item.session_date: item for item in bars}
    _audit_bar_values(
        security_id=security_id,
        bars=bars,
        states=states,
        date_indexes=date_indexes,
        accumulator=accumulator,
    )
    required = _required_path_dates(
        header.sessions,
        states,
        settlement_cutoff=_complete_terminal_settlement_cutoff(lifecycle_events),
    )
    for rule_code, expected_dates in required.items():
        missing = tuple(item for item in expected_dates if item not in bars_by_date)
        if missing:
            _append_date_issue(
                accumulator,
                disposition="exclude_candidate",
                rule_code=rule_code,
                message="Required experiment path lacks canonical daily market data",
                security_id=security_id,
                dates=missing,
            )
    held_zero_volume = tuple(
        session
        for session in required["potential_held_path_market_gap"]
        if session in bars_by_date and bars_by_date[session].volume_raw == 0
    )
    if held_zero_volume:
        _append_date_issue(
            accumulator,
            disposition="exclude_candidate",
            rule_code="potential_held_path_zero_volume",
            message="Potential held path contains a zero-volume valuation session",
            security_id=security_id,
            dates=held_zero_volume,
        )
    _audit_strict_warmup(
        header,
        security_id=security_id,
        states=states,
        bars_by_date=bars_by_date,
        accumulator=accumulator,
    )
    required_union = set().union(*required.values()) if required else set()
    if required_union:
        last_bar = max(bars_by_date, default=None)
        tail = tuple(sorted(item for item in required_union if last_bar is None or item > last_bar))
        terminal_dates = tuple(
            item.effective_session
            for item in lifecycle_events
            if item.event_type in _TERMINAL_EVENT_TYPES
        )
        if tail and not any(item <= tail[-1] for item in terminal_dates):
            _append_date_issue(
                accumulator,
                disposition="exclude_candidate",
                rule_code="required_path_ends_without_lifecycle_closure",
                message="Required price history ends without a terminal lifecycle event",
                security_id=security_id,
                dates=tail,
            )


def _states_by_session(
    sessions: Sequence[date], masks: Sequence[ClosureMaskInterval]
) -> tuple[_MaskState | None, ...]:
    result: list[_MaskState | None] = []
    cursor = 0
    for session in sessions:
        while cursor < len(masks) and masks[cursor].effective_end < session:
            cursor += 1
        if cursor < len(masks) and masks[cursor].effective_start <= session:
            item = masks[cursor]
            result.append(_MaskState(item.is_selectable, item.is_tradable))
        else:
            result.append(None)
    return tuple(result)


def _required_path_dates(
    sessions: Sequence[ClosureSession],
    states: Sequence[_MaskState | None],
    *,
    settlement_cutoff: date | None,
) -> dict[str, set[date]]:
    selectable: set[date] = set()
    decisions: set[date] = set()
    executions: set[date] = set()
    held: set[date] = set()
    decision_indexes = tuple(
        index for index, item in enumerate(sessions) if item.is_decision_session
    )
    next_decision = {
        current: following
        for current, following in zip(decision_indexes, decision_indexes[1:], strict=False)
    }
    for index, (session, state) in enumerate(zip(sessions, states, strict=True)):
        if state is None or not state.is_selectable:
            continue
        selectable.add(session.session_date)
        if not session.is_decision_session:
            continue
        decisions.add(session.session_date)
        execution = index + 1
        if execution >= len(sessions):
            continue
        if (
            settlement_cutoff is None
            or sessions[execution].session_date < settlement_cutoff
        ):
            executions.add(sessions[execution].session_date)
        next_index = next_decision.get(index)
        held_end = (
            len(sessions) - 1
            if next_index is None
            else min(next_index, len(sessions) - 1)
        )
        for held_index in range(execution, held_end + 1):
            held_date = sessions[held_index].session_date
            if settlement_cutoff is not None and held_date >= settlement_cutoff:
                break
            held.add(held_date)
    return {
        "selectable_path_market_gap": selectable,
        "decision_session_market_gap": decisions,
        "next_execution_session_market_gap": executions,
        "potential_held_path_market_gap": held,
    }


def _complete_terminal_settlement_cutoff(
    lifecycle_events: Sequence[ClosureLifecycleEvent],
) -> date | None:
    settlements = tuple(
        item.settlement_session
        for item in lifecycle_events
        if item.event_type in _TERMINAL_EVENT_TYPES
        and item.event_status == "confirmed"
        and item.settlement_session is not None
        and item.declared_settlement_leg_count > 0
        and item.actual_settlement_leg_count
        == item.declared_settlement_leg_count
    )
    return min(settlements, default=None)


def _audit_bar_values(
    *,
    security_id: uuid.UUID,
    bars: Sequence[ClosureBar],
    states: Sequence[_MaskState | None],
    date_indexes: Mapping[date, int],
    accumulator: _Accumulator,
) -> None:
    nonpositive: list[date] = []
    raw_envelope: list[date] = []
    adjusted_envelope: list[date] = []
    zero_volume: list[date] = []
    large_returns: list[dict[str, object]] = []
    prior: ClosureBar | None = None
    for item in bars:
        prices = (
            item.open_raw,
            item.high_raw,
            item.low_raw,
            item.close_raw,
            item.open_adj,
            item.high_adj,
            item.low_adj,
            item.close_adj,
        )
        if any(value <= 0 for value in prices):
            nonpositive.append(item.session_date)
        if item.high_raw < max(item.open_raw, item.low_raw, item.close_raw) or item.low_raw > min(
            item.open_raw, item.high_raw, item.close_raw
        ):
            raw_envelope.append(item.session_date)
        if item.high_adj < max(item.open_adj, item.low_adj, item.close_adj) or item.low_adj > min(
            item.open_adj, item.high_adj, item.close_adj
        ):
            adjusted_envelope.append(item.session_date)
        index = date_indexes.get(item.session_date)
        if (
            item.volume_raw == 0
            and index is not None
            and states[index] is not None
            and cast(_MaskState, states[index]).is_selectable
        ):
            zero_volume.append(item.session_date)
        if prior is not None and prior.close_adj > 0 and item.close_adj > 0:
            prior_index = date_indexes.get(prior.session_date)
            current_index = date_indexes.get(item.session_date)
            if (
                prior_index is not None
                and current_index == prior_index + 1
                and abs(item.close_adj / prior.close_adj - Decimal(1))
                > _LARGE_RETURN_THRESHOLD
            ):
                large_returns.append(
                    {
                        "prior_session": prior.session_date.isoformat(),
                        "session": item.session_date.isoformat(),
                        "return": str(item.close_adj / prior.close_adj - Decimal(1)),
                    }
                )
        prior = item
    for rule_code, message, disposition, issue_dates in (
        (
            "nonpositive_market_price",
            "Canonical daily bar contains a nonpositive price",
            "blocker",
            nonpositive,
        ),
        (
            "raw_ohlc_envelope_violation",
            "Raw OHLC high/low envelope is invalid",
            "blocker",
            raw_envelope,
        ),
        (
            "adjusted_ohlc_envelope_violation",
            "Adjusted OHLC high/low envelope is invalid",
            "blocker",
            adjusted_envelope,
        ),
        (
            "selectable_zero_volume",
            "Selectable session has zero reported volume",
            "exclude_candidate",
            zero_volume,
        ),
    ):
        if issue_dates:
            _append_date_issue(
                accumulator,
                disposition=cast(Disposition, disposition),
                rule_code=rule_code,
                message=message,
                security_id=security_id,
                dates=tuple(issue_dates),
            )
    if large_returns:
        accumulator.issues.append(
            ClosureAuditIssue(
                "review",
                "adjusted_return_over_50_percent",
                "Consecutive-session adjusted return exceeds the review threshold",
                str(security_id),
                {
                    "count": len(large_returns),
                    "samples": large_returns[:10],
                },
            )
        )


def _audit_strict_warmup(
    header: _AuditHeader,
    *,
    security_id: uuid.UUID,
    states: Sequence[_MaskState | None],
    bars_by_date: Mapping[date, ClosureBar],
    accumulator: _Accumulator,
) -> None:
    starts = tuple(
        index
        for index, state in enumerate(states)
        if state is not None
        and state.is_selectable
        and (
            index == 0
            or states[index - 1] is None
            or not cast(_MaskState, states[index - 1]).is_selectable
        )
    )
    failures: list[dict[str, object]] = []
    dates = tuple(item.session_date for item in header.sessions)
    for index in starts:
        actual = 0
        # Cohort readiness is evaluated at the completed session close, so the
        # first selectable session is the final observation in its warm-up
        # window.  This must match EvaluationCohortService and the deferred DB
        # gate; starting at ``index - 1`` incorrectly rejected every exactly
        # 504-session history as having only 503 observations.
        cursor = index
        while cursor >= 0 and dates[cursor] in bars_by_date:
            actual += 1
            cursor -= 1
            if actual == header.required_history_sessions:
                break
        if actual < header.required_history_sessions:
            failures.append(
                {
                    "selection_start": dates[index].isoformat(),
                    "required_consecutive_sessions": header.required_history_sessions,
                    "actual_consecutive_sessions": actual,
                }
            )
    if failures:
        accumulator.issues.append(
            ClosureAuditIssue(
                "exclude_candidate",
                "strict_consecutive_warmup_incomplete",
                "Selection begins without the exact consecutive warm-up history",
                str(security_id),
                {"count": len(failures), "samples": failures[:10]},
            )
        )


def _audit_lifecycle(
    header: _AuditHeader,
    *,
    events: Mapping[uuid.UUID, Sequence[ClosureLifecycleEvent]],
    instructions: Mapping[uuid.UUID, ClosureSettlementInstruction],
    accumulator: _Accumulator,
) -> None:
    known_events: set[uuid.UUID] = set()
    coverage_start = header.sessions[0].session_date
    coverage_end = header.sessions[-1].session_date
    for security_id, rows in events.items():
        for item in rows:
            known_events.add(item.lifecycle_event_id)
            details: dict[str, object] = {
                "lifecycle_event_id": str(item.lifecycle_event_id),
                "event_type": item.event_type,
                "effective_session": item.effective_session.isoformat(),
            }
            if item.artifact_status != "published":
                _append_lifecycle_issue(
                    accumulator,
                    "lifecycle_event_not_published",
                    "Lifecycle closure depends on an unpublished event",
                    security_id,
                    details,
                )
            if item.event_type not in _SETTLEMENT_EVENT_TYPES:
                continue
            if item.event_status != "confirmed":
                _append_lifecycle_issue(
                    accumulator,
                    "lifecycle_event_not_confirmed",
                    "Settlement-bearing lifecycle event is not confirmed",
                    security_id,
                    details | {"event_status": item.event_status},
                )
            if (
                item.settlement_session is None
                or item.declared_settlement_leg_count < 1
                or item.actual_settlement_leg_count
                != item.declared_settlement_leg_count
            ):
                _append_lifecycle_issue(
                    accumulator,
                    "lifecycle_settlement_incomplete",
                    "Lifecycle event lacks complete settlement terms",
                    security_id,
                    details
                    | {
                        "settlement_session": (
                            None
                            if item.settlement_session is None
                            else item.settlement_session.isoformat()
                        ),
                        "declared_leg_count": item.declared_settlement_leg_count,
                        "actual_leg_count": item.actual_settlement_leg_count,
                    },
                )
                continue
            if (
                header.evaluation_cohort_runtime_contract_id is not None
                and coverage_start <= item.settlement_session <= coverage_end
            ):
                instruction = instructions.get(item.lifecycle_event_id)
                if (
                    instruction is None
                    or instruction.settlement_session != item.settlement_session
                    or instruction.leg_count != item.actual_settlement_leg_count
                ):
                    _append_lifecycle_issue(
                        accumulator,
                        "runtime_settlement_instruction_missing_or_mismatched",
                        "Runtime contract does not freeze the exact lifecycle settlement",
                        security_id,
                        details,
                    )
    if header.evaluation_cohort_runtime_contract_id is not None:
        for event_id, instruction in instructions.items():
            if event_id not in known_events:
                accumulator.issues.append(
                    ClosureAuditIssue(
                        "blocker",
                        "runtime_settlement_instruction_orphaned",
                        "Runtime contract contains an instruction without an active event",
                        None,
                        {
                            "lifecycle_event_id": str(event_id),
                            "settlement_session": instruction.settlement_session.isoformat(),
                        },
                    )
                )


def _append_lifecycle_issue(
    accumulator: _Accumulator,
    rule_code: str,
    message: str,
    security_id: uuid.UUID,
    details: dict[str, object],
) -> None:
    accumulator.issues.append(
        ClosureAuditIssue("blocker", rule_code, message, str(security_id), details)
    )


def _append_date_issue(
    accumulator: _Accumulator,
    *,
    disposition: Disposition,
    rule_code: str,
    message: str,
    security_id: uuid.UUID,
    dates: Sequence[date],
) -> None:
    ordered = tuple(sorted(set(dates)))
    accumulator.issues.append(
        ClosureAuditIssue(
            disposition,
            rule_code,
            message,
            str(security_id),
            {
                "count": len(ordered),
                "first_session": ordered[0].isoformat(),
                "last_session": ordered[-1].isoformat(),
                "sample_sessions": [item.isoformat() for item in ordered[:10]],
            },
        )
    )


def _final_report(
    header: _AuditHeader,
    security_ids: Sequence[uuid.UUID],
    accumulator: _Accumulator,
) -> MarketDataClosureAuditReport:
    issues = tuple(
        sorted(
            accumulator.issues,
            key=lambda item: (
                item.disposition,
                item.rule_code,
                item.security_id or "",
                json.dumps(item.details, sort_keys=True),
            ),
        )
    )
    blockers = tuple(item for item in issues if item.disposition == "blocker")
    candidates = tuple(item for item in issues if item.disposition == "exclude_candidate")
    review_findings = tuple(item for item in issues if item.disposition == "review")
    all_issue_codes = {item.rule_code for item in issues}
    checks = (
        ("positive_prices", "All canonical prices are positive", {"nonpositive_market_price"}),
        ("raw_ohlc_envelope", "All raw OHLC envelopes are valid", {"raw_ohlc_envelope_violation"}),
        (
            "adjusted_ohlc_envelope",
            "All adjusted OHLC envelopes are valid",
            {"adjusted_ohlc_envelope_violation"},
        ),
        (
            "selectable_volume",
            "Selectable sessions contain positive volume",
            {"selectable_zero_volume"},
        ),
        (
            "adjusted_return_integrity",
            "Adjusted returns stay within 50% per session",
            {"adjusted_return_over_50_percent"},
        ),
        (
            "selectable_path",
            "Selectable paths have complete daily bars",
            {"selectable_path_market_gap"},
        ),
        (
            "decision_path",
            "Decision paths have complete daily bars",
            {"decision_session_market_gap"},
        ),
        (
            "execution_path",
            "Execution paths have complete daily bars",
            {"next_execution_session_market_gap"},
        ),
        (
            "potential_held_path",
            "Potential held paths have complete positive-volume daily bars",
            {
                "potential_held_path_market_gap",
                "potential_held_path_zero_volume",
            },
        ),
        (
            "strict_consecutive_warmup",
            "Every selection start has strict consecutive warm-up coverage",
            {"strict_consecutive_warmup_incomplete"},
        ),
        (
            "lifecycle_settlement_closure",
            "Lifecycle evidence and runtime settlement instructions are complete",
            {
                "lifecycle_event_not_published",
                "lifecycle_event_not_confirmed",
                "lifecycle_settlement_incomplete",
                "runtime_settlement_instruction_missing_or_mismatched",
                "runtime_settlement_instruction_orphaned",
                "required_path_ends_without_lifecycle_closure",
            },
        ),
    )
    passes = tuple(
        ClosureAuditPass(
            rule_code,
            message,
            {"security_count": len(security_ids)},
        )
        for rule_code, message, check_failure_codes in checks
        if all_issue_codes.isdisjoint(check_failure_codes)
    )
    return MarketDataClosureAuditReport(
        _SCHEMA_VERSION,
        str(header.dataset_publication_id),
        str(header.evaluation_cohort_version_id),
        (
            None
            if header.evaluation_cohort_runtime_contract_id is None
            else str(header.evaluation_cohort_runtime_contract_id)
        ),
        header.sessions[0].session_date.isoformat(),
        header.sessions[-1].session_date.isoformat(),
        len(security_ids),
        len(header.sessions),
        accumulator.bar_count,
        not blockers and not candidates,
        blockers,
        candidates,
        review_findings,
        passes,
    )


def _load_header(
    connection: Connection,
    *,
    dataset_publication_id: uuid.UUID,
    evaluation_cohort_version_id: uuid.UUID | None,
    evaluation_cohort_runtime_contract_id: uuid.UUID | None,
    require_dataset_match: bool = True,
) -> tuple[_AuditHeader, bool]:
    if evaluation_cohort_runtime_contract_id is not None:
        row = (
            connection.execute(
                text(
                    """
                    SELECT cohort.evaluation_cohort_version_id,
                           cohort.dataset_publication_id,
                           cohort.required_history_sessions,
                           cohort_artifact.status AS cohort_status,
                           dataset_artifact.status AS dataset_status,
                           runtime_artifact.status AS runtime_status
                      FROM experiment.v022_evaluation_cohort_runtime_contract runtime
                      JOIN lineage.artifact runtime_artifact
                        ON runtime_artifact.artifact_id=runtime.artifact_id
                      JOIN experiment.v022_evaluation_cohort_version cohort
                        ON cohort.evaluation_cohort_version_id=
                           runtime.evaluation_cohort_version_id
                      JOIN lineage.artifact cohort_artifact
                        ON cohort_artifact.artifact_id=cohort.artifact_id
                      JOIN data.dataset_publication dataset
                        ON dataset.dataset_publication_id=cohort.dataset_publication_id
                      JOIN lineage.artifact dataset_artifact
                        ON dataset_artifact.artifact_id=dataset.artifact_id
                     WHERE runtime.evaluation_cohort_runtime_contract_id=:runtime
                    """
                ),
                {"runtime": evaluation_cohort_runtime_contract_id},
            )
            .mappings()
            .one_or_none()
        )
        runtime_masks = True
    else:
        row = (
            connection.execute(
                text(
                    """
                    SELECT cohort.evaluation_cohort_version_id,
                           cohort.dataset_publication_id,
                           cohort.required_history_sessions,
                           cohort_artifact.status AS cohort_status,
                           dataset_artifact.status AS dataset_status,
                           NULL::varchar AS runtime_status
                      FROM experiment.v022_evaluation_cohort_version cohort
                      JOIN lineage.artifact cohort_artifact
                        ON cohort_artifact.artifact_id=cohort.artifact_id
                      JOIN data.dataset_publication dataset
                        ON dataset.dataset_publication_id=cohort.dataset_publication_id
                      JOIN lineage.artifact dataset_artifact
                        ON dataset_artifact.artifact_id=dataset.artifact_id
                     WHERE cohort.evaluation_cohort_version_id=:cohort
                    """
                ),
                {"cohort": evaluation_cohort_version_id},
            )
            .mappings()
            .one_or_none()
        )
        runtime_masks = False
    if row is None:
        raise LookupError("Exact Evaluation Cohort closure was not found")
    if require_dataset_match and row["dataset_publication_id"] != dataset_publication_id:
        raise ValueError("Dataset identity does not exactly match the frozen Cohort")
    statuses = (row["cohort_status"], row["dataset_status"], row["runtime_status"])
    if any(status not in (None, "published") for status in statuses):
        raise ValueError("Closure audit requires published immutable inputs")
    cohort_id = cast(uuid.UUID, row["evaluation_cohort_version_id"])
    session_rows = connection.execute(
        text(
            """
            SELECT session_date,session_role,is_decision_session
              FROM experiment.v022_evaluation_cohort_session
             WHERE evaluation_cohort_version_id=:cohort
             ORDER BY ordinal
            """
        ),
        {"cohort": cohort_id},
    ).mappings().all()
    sessions = tuple(
        ClosureSession(
            cast(date, item["session_date"]),
            cast(SessionRole, item["session_role"]),
            cast(bool, item["is_decision_session"]),
        )
        for item in session_rows
    )
    if not sessions:
        raise ValueError("Exact Evaluation Cohort contains no sessions")
    return (
        _AuditHeader(
            dataset_publication_id,
            cohort_id,
            evaluation_cohort_runtime_contract_id,
            cast(int, row["required_history_sessions"]),
            sessions,
        ),
        runtime_masks,
    )


def _require_published_candidate_dataset(
    connection: Connection, dataset_publication_id: uuid.UUID
) -> None:
    row = (
        connection.execute(
            text(
                """
                SELECT publication.dataset_kind,publication.value_kind,
                       artifact.status AS artifact_status
                  FROM data.dataset_publication publication
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=publication.artifact_id
                 WHERE publication.dataset_publication_id=:dataset
                """
            ),
            {"dataset": dataset_publication_id},
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is None
        or row["artifact_status"] != "published"
        or row["dataset_kind"] != "canonical"
        or row["value_kind"] != "daily_bar"
    ):
        raise LookupError("Candidate Dataset must be published canonical daily-bar data")


def _load_masks(
    connection: Connection,
    *,
    cohort_version_id: uuid.UUID,
    runtime_contract_id: uuid.UUID | None,
    runtime_masks: bool,
) -> dict[uuid.UUID, tuple[ClosureMaskInterval, ...]]:
    if runtime_masks:
        query = text(
            """
            SELECT security_id,effective_start,effective_end,is_selectable,is_tradable
              FROM experiment.v022_cohort_runtime_mask_interval
             WHERE evaluation_cohort_runtime_contract_id=:identity
             ORDER BY security_id,ordinal
            """
        )
        identity = runtime_contract_id
    else:
        query = text(
            """
            SELECT security_id,effective_start,effective_end,is_selectable,is_tradable
              FROM experiment.v022_cohort_eligibility_interval
             WHERE evaluation_cohort_version_id=:identity
             ORDER BY security_id,ordinal
            """
        )
        identity = cohort_version_id
    return _group_masks(
        ClosureMaskInterval(
            cast(uuid.UUID, item["security_id"]),
            cast(date, item["effective_start"]),
            cast(date, item["effective_end"]),
            cast(bool, item["is_selectable"]),
            cast(bool, item["is_tradable"]),
        )
        for item in connection.execute(query, {"identity": identity}).mappings()
    )


def _load_lifecycle_events(
    connection: Connection,
    *,
    security_ids: tuple[uuid.UUID, ...],
    evaluation_end: date,
) -> dict[uuid.UUID, tuple[ClosureLifecycleEvent, ...]]:
    rows = connection.execute(
        text(
            """
            SELECT event.security_id,event.security_lifecycle_event_id,event.event_type,
                   event.event_status,event.effective_session,event.settlement_session,
                   event.settlement_leg_count,artifact.status AS artifact_status,
                   count(leg.ordinal) AS actual_leg_count
              FROM catalog.v022_security_lifecycle_event event
              JOIN lineage.artifact artifact ON artifact.artifact_id=event.artifact_id
              LEFT JOIN catalog.v022_security_settlement_leg leg
                ON leg.security_lifecycle_event_id=event.security_lifecycle_event_id
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
             GROUP BY event.security_lifecycle_event_id,artifact.status
             ORDER BY event.security_id,event.effective_session,event.event_key
            """
        ).bindparams(bindparam("security_ids", expanding=True)),
        {"security_ids": security_ids, "evaluation_end": evaluation_end},
    ).mappings().all()
    return _group_events(
        ClosureLifecycleEvent(
            cast(uuid.UUID, item["security_id"]),
            cast(uuid.UUID, item["security_lifecycle_event_id"]),
            cast(str, item["event_type"]),
            cast(str, item["event_status"]),
            cast(date, item["effective_session"]),
            cast(date | None, item["settlement_session"]),
            cast(int, item["settlement_leg_count"]),
            cast(int, item["actual_leg_count"]),
            cast(str, item["artifact_status"]),
        )
        for item in rows
    )


def _load_settlement_instructions(
    connection: Connection,
    *,
    runtime_contract_id: uuid.UUID | None,
) -> dict[uuid.UUID, ClosureSettlementInstruction]:
    if runtime_contract_id is None:
        return {}
    rows = connection.execute(
        text(
            """
            SELECT security_lifecycle_event_id,settlement_session,
                   jsonb_array_length(legs_document) AS leg_count
              FROM experiment.v022_cohort_settlement_instruction
             WHERE evaluation_cohort_runtime_contract_id=:runtime
             ORDER BY ordinal
            """
        ),
        {"runtime": runtime_contract_id},
    ).mappings().all()
    result = {
        cast(uuid.UUID, item["security_lifecycle_event_id"]): ClosureSettlementInstruction(
            cast(uuid.UUID, item["security_lifecycle_event_id"]),
            cast(date, item["settlement_session"]),
            cast(int, item["leg_count"]),
        )
        for item in rows
    }
    if len(result) != len(rows):
        raise ValueError("Runtime contract contains duplicate settlement instructions")
    return result


def _stream_bars(
    connection: Connection,
    *,
    dataset_publication_id: uuid.UUID,
    security_ids: tuple[uuid.UUID, ...],
    coverage_start: date,
    coverage_end: date,
) -> Iterator[tuple[uuid.UUID, tuple[ClosureBar, ...]]]:
    rows = connection.execution_options(stream_results=True).execute(
        text(
            """
            SELECT security.security_id,bar.session_date,
                   bar.open_raw,bar.high_raw,bar.low_raw,bar.close_raw,
                   bar.open_adj,bar.high_adj,bar.low_adj,bar.close_adj,bar.volume_raw
              FROM catalog.security security
              JOIN data.daily_bar bar ON bar.asset_id=security.legacy_asset_id
             WHERE bar.dataset_publication_id=:dataset
               AND security.security_id IN :security_ids
               AND bar.session_date BETWEEN :coverage_start AND :coverage_end
             ORDER BY security.security_id,bar.session_date
            """
        ).bindparams(bindparam("security_ids", expanding=True)),
        {
            "dataset": dataset_publication_id,
            "security_ids": security_ids,
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
        },
    ).mappings()
    current_security: uuid.UUID | None = None
    current_rows: list[ClosureBar] = []
    for item in rows:
        security_id = cast(uuid.UUID, item["security_id"])
        if current_security is not None and security_id != current_security:
            yield current_security, tuple(current_rows)
            current_rows = []
        current_security = security_id
        current_rows.append(_bar_from_row(item))
    if current_security is not None:
        yield current_security, tuple(current_rows)


def _bar_from_row(item: RowMapping) -> ClosureBar:
    return ClosureBar(
        cast(uuid.UUID, item["security_id"]),
        cast(date, item["session_date"]),
        cast(Decimal, item["open_raw"]),
        cast(Decimal, item["high_raw"]),
        cast(Decimal, item["low_raw"]),
        cast(Decimal, item["close_raw"]),
        cast(Decimal, item["open_adj"]),
        cast(Decimal, item["high_adj"]),
        cast(Decimal, item["low_adj"]),
        cast(Decimal, item["close_adj"]),
        cast(int, item["volume_raw"]),
    )
