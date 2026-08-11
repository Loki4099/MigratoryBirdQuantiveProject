from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Literal

from style_rotation.metrics.types import SeriesPoint

IntervalTemplateKey = Literal[
    "full_history",
    "trailing_10_years",
    "trailing_5_years",
    "trailing_3_years",
    "trailing_1_year",
    "custom",
]
InitializationPolicy = Literal["carry_in", "fresh_start"]


class IntervalResolutionError(RuntimeError):
    """Raised when an interval request is invalid or cannot be sliced honestly."""


@dataclass(frozen=True, slots=True)
class ResolvedInterval:
    template_key: IntervalTemplateKey
    as_of_date: date
    requested_start: date
    requested_end: date
    resolved_start: date | None
    resolved_end: date | None
    initialization_policy: InitializationPolicy
    underlying_simulation_start: date
    normalization_nav_date: date | None
    availability_status: Literal["eligible", "excluded"]
    exclusion_reason: str | None


@dataclass(frozen=True, slots=True)
class IntervalSeries:
    interval: ResolvedInterval
    annualization_start: date
    points: tuple[SeriesPoint, ...]


def resolve_interval(
    *,
    template_key: IntervalTemplateKey,
    path_dates: tuple[date, ...],
    as_of_date: date,
    initialization_policy: InitializationPolicy = "carry_in",
    custom_start: date | None = None,
    custom_end: date | None = None,
) -> ResolvedInterval:
    if not path_dates or path_dates != tuple(sorted(set(path_dates))):
        raise IntervalResolutionError("Path dates must be non-empty, unique, and sorted")
    available_end_dates = tuple(day for day in path_dates if day <= as_of_date)
    if not available_end_dates:
        raise IntervalResolutionError("As-of date precedes the continuous path")
    if template_key == "custom":
        if custom_start is None or custom_end is None:
            raise ValueError("Custom interval requires explicit start and end dates")
        requested_start = custom_start
        requested_end = custom_end
    elif custom_start is not None or custom_end is not None:
        raise ValueError("Preset interval templates cannot receive custom dates")
    else:
        requested_end = as_of_date
        if template_key == "full_history":
            requested_start = path_dates[0]
        else:
            years = {
                "trailing_10_years": 10,
                "trailing_5_years": 5,
                "trailing_3_years": 3,
                "trailing_1_year": 1,
            }[template_key]
            requested_start = _subtract_years(requested_end, years)
    if requested_end > as_of_date:
        raise IntervalResolutionError("Requested end cannot exceed the fixed as-of date")
    if requested_start > requested_end:
        raise ValueError("Interval start must not exceed interval end")

    if path_dates[0] > requested_start or path_dates[-1] < requested_end:
        return ResolvedInterval(
            template_key,
            as_of_date,
            requested_start,
            requested_end,
            None,
            None,
            initialization_policy,
            path_dates[0],
            None,
            "excluded",
            "insufficient_history",
        )
    start_index = next(index for index, day in enumerate(path_dates) if day >= requested_start)
    resolved_end = max(day for day in path_dates if day <= requested_end)
    resolved_start = path_dates[start_index]
    if resolved_start > resolved_end:
        raise IntervalResolutionError("Resolved interval contains no path observations")
    normalization_date = path_dates[start_index - 1] if start_index > 0 else None
    return ResolvedInterval(
        template_key,
        as_of_date,
        requested_start,
        requested_end,
        resolved_start,
        resolved_end,
        initialization_policy,
        path_dates[0],
        normalization_date,
        "eligible",
        None,
    )


def slice_carry_in_series(
    points: tuple[SeriesPoint, ...], interval: ResolvedInterval
) -> IntervalSeries:
    if interval.availability_status != "eligible":
        raise IntervalResolutionError("Excluded interval cannot produce a performance series")
    if interval.initialization_policy != "carry_in":
        raise IntervalResolutionError(
            "Fresh-start requires an independently simulated path from 100% reserve"
        )
    dates = tuple(item.nav_date for item in points)
    if dates != tuple(sorted(set(dates))) or any(item.nav <= 0 for item in points):
        raise IntervalResolutionError("Performance path must be unique, sorted, and positive")
    if dates[0] != interval.underlying_simulation_start:
        raise IntervalResolutionError("Series and interval underlying simulation starts mismatch")
    if interval.resolved_start not in dates or interval.resolved_end not in dates:
        raise IntervalResolutionError("Resolved interval dates must exist in the continuous path")
    by_date = {item.nav_date: item for item in points}
    baseline = (
        by_date[interval.normalization_nav_date].nav
        if interval.normalization_nav_date is not None
        else Decimal(1)
    )
    selected = tuple(
        item for item in points if interval.resolved_start <= item.nav_date <= interval.resolved_end
    )
    with localcontext() as context:
        context.prec = 40
        normalized = tuple(
            SeriesPoint(item.nav_date, item.daily_return, item.nav / baseline) for item in selected
        )
    annualization_start = interval.normalization_nav_date or interval.resolved_start
    return IntervalSeries(interval, annualization_start, normalized)


def _subtract_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, month=2, day=28)
