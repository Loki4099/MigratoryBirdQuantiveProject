from datetime import date, timedelta
from decimal import Decimal

import pytest

from style_rotation.experiment.intervals import (
    IntervalResolutionError,
    resolve_interval,
    slice_carry_in_series,
)
from style_rotation.metrics.types import SeriesPoint


def _dates(count: int = 6) -> tuple[date, ...]:
    start = date(2024, 1, 1)
    return tuple(start + timedelta(days=index) for index in range(count))


def test_carry_in_slice_inherits_prior_wealth_and_renormalizes_boundary() -> None:
    dates = _dates(4)
    points = (
        SeriesPoint(dates[0], Decimal("0.10"), Decimal("1.10")),
        SeriesPoint(dates[1], Decimal("0.10"), Decimal("1.21")),
        SeriesPoint(dates[2], Decimal("-0.10"), Decimal("1.089")),
        SeriesPoint(dates[3], Decimal("0"), Decimal("1.089")),
    )
    interval = resolve_interval(
        template_key="custom",
        path_dates=dates,
        as_of_date=dates[-1],
        custom_start=dates[1],
        custom_end=dates[3],
    )
    sliced = slice_carry_in_series(points, interval)
    assert interval.normalization_nav_date == dates[0]
    assert sliced.annualization_start == dates[0]
    assert tuple(item.nav for item in sliced.points) == (
        Decimal("1.1"),
        Decimal("0.99"),
        Decimal("0.99"),
    )
    assert tuple(item.daily_return for item in sliced.points) == (
        Decimal("0.10"),
        Decimal("-0.10"),
        Decimal("0"),
    )


def test_full_history_uses_initial_wealth_as_boundary() -> None:
    dates = _dates(3)
    interval = resolve_interval(template_key="full_history", path_dates=dates, as_of_date=dates[-1])
    assert interval.resolved_start == dates[0]
    assert interval.normalization_nav_date is None


def test_incomplete_trailing_window_is_excluded_not_shortened() -> None:
    dates = (date(2022, 1, 3), date(2023, 1, 3), date(2024, 1, 3))
    interval = resolve_interval(
        template_key="trailing_5_years", path_dates=dates, as_of_date=dates[-1]
    )
    assert interval.availability_status == "excluded"
    assert interval.exclusion_reason == "insufficient_history"
    assert interval.resolved_start is None


def test_fresh_start_cannot_be_faked_by_slicing_continuous_path() -> None:
    dates = _dates(3)
    interval = resolve_interval(
        template_key="custom",
        path_dates=dates,
        as_of_date=dates[-1],
        initialization_policy="fresh_start",
        custom_start=dates[1],
        custom_end=dates[-1],
    )
    points = tuple(SeriesPoint(day, Decimal(0), Decimal(1)) for day in dates)
    with pytest.raises(IntervalResolutionError, match="independently simulated"):
        slice_carry_in_series(points, interval)


def test_trailing_year_resolution_handles_leap_day() -> None:
    dates = (date(2023, 2, 28), date(2024, 2, 29))
    interval = resolve_interval(
        template_key="trailing_1_year", path_dates=dates, as_of_date=date(2024, 2, 29)
    )
    assert interval.requested_start == date(2023, 2, 28)
