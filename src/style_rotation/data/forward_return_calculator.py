from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from style_rotation.data.forward_return_contracts import ForwardReturnSeed

RETURN_QUANTUM = Decimal("0.000000000000000001")


@dataclass(frozen=True, slots=True)
class ForwardOpen:
    asset_id: uuid.UUID
    asset_key: str
    session_date: date
    open_adj: Decimal


@dataclass(frozen=True, slots=True)
class ForwardReturnPoint:
    asset_id: uuid.UUID
    asset_key: str
    decision_date: date
    start_date: date
    end_date: date
    forward_return: Decimal


@dataclass(frozen=True, slots=True)
class ForwardReturnCalculation:
    target: ForwardReturnSeed
    coverage_start: date
    coverage_end: date
    points: tuple[ForwardReturnPoint, ...]


class ForwardReturnCalculationError(RuntimeError):
    """Raised when an evaluation label cannot be built without future-data ambiguity."""


def calculate_forward_returns(
    target: ForwardReturnSeed,
    sessions: tuple[date, ...],
    opens: tuple[ForwardOpen, ...],
    *,
    requested_start: date,
    requested_end: date,
) -> ForwardReturnCalculation:
    if requested_start > requested_end:
        raise ValueError("Forward-return requested start must not follow end")
    ordered_sessions = tuple(sorted(sessions))
    if len(ordered_sessions) != len(set(ordered_sessions)):
        raise ForwardReturnCalculationError("Forward-return calendar contains duplicate sessions")
    if len(ordered_sessions) < 3:
        raise ForwardReturnCalculationError("Forward-return calendar is too short")
    decisions = _decision_dates(ordered_sessions, target.frequency)
    session_index = {day: index for index, day in enumerate(ordered_sessions)}
    intervals: list[tuple[date, date, date]] = []
    for current, following in zip(decisions, decisions[1:], strict=False):
        current_index = session_index[current]
        following_index = session_index[following]
        if current_index + target.execution_lag_sessions >= len(ordered_sessions):
            continue
        if following_index + target.execution_lag_sessions >= len(ordered_sessions):
            continue
        start_date = ordered_sessions[current_index + target.execution_lag_sessions]
        end_date = ordered_sessions[following_index + target.execution_lag_sessions]
        if current >= requested_start and end_date <= requested_end:
            intervals.append((current, start_date, end_date))
    if not intervals:
        raise ForwardReturnCalculationError("Requested range contains no complete target interval")
    open_by_key: dict[tuple[uuid.UUID, date], ForwardOpen] = {}
    asset_keys: dict[uuid.UUID, str] = {}
    for item in opens:
        if item.open_adj <= 0:
            raise ForwardReturnCalculationError("Adjusted open must be positive")
        key = (item.asset_id, item.session_date)
        if key in open_by_key:
            raise ForwardReturnCalculationError("Duplicate adjusted-open observation")
        open_by_key[key] = item
        previous = asset_keys.setdefault(item.asset_id, item.asset_key)
        if previous != item.asset_key:
            raise ForwardReturnCalculationError("Unstable asset identity in adjusted opens")
    if not asset_keys:
        raise ForwardReturnCalculationError("Forward-return target has no included assets")
    points: list[ForwardReturnPoint] = []
    for decision_date, start_date, end_date in intervals:
        for asset_id in sorted(asset_keys, key=lambda item: (asset_keys[item], str(item))):
            start = open_by_key.get((asset_id, start_date))
            end = open_by_key.get((asset_id, end_date))
            if start is None or end is None:
                raise ForwardReturnCalculationError(
                    f"Missing adjusted open for {asset_keys[asset_id]} target interval"
                )
            value = (end.open_adj / start.open_adj - 1).quantize(
                RETURN_QUANTUM, rounding=ROUND_HALF_EVEN
            )
            points.append(
                ForwardReturnPoint(
                    asset_id,
                    asset_keys[asset_id],
                    decision_date,
                    start_date,
                    end_date,
                    value,
                )
            )
    points.sort(key=lambda item: (item.asset_key, item.decision_date, str(item.asset_id)))
    decision_dates = [item.decision_date for item in points]
    return ForwardReturnCalculation(target, min(decision_dates), max(decision_dates), tuple(points))


def _decision_dates(sessions: tuple[date, ...], frequency: str) -> tuple[date, ...]:
    grouped: dict[tuple[int, int], date] = {}
    for session in sessions:
        if frequency == "weekly":
            iso = session.isocalendar()
            key = (iso.year, iso.week)
        elif frequency == "monthly":
            key = (session.year, session.month)
        else:
            raise ForwardReturnCalculationError(f"Unsupported target frequency: {frequency}")
        grouped[key] = session
    return tuple(grouped[key] for key in sorted(grouped))
