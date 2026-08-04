from datetime import date, timedelta
from decimal import Decimal

import pytest

from style_rotation.experiment.intervals import IntervalSeries, resolve_interval
from style_rotation.experiment.performance import (
    PerformanceCalculationError,
    calculate_absolute_performance,
    calculate_relative_performance,
)
from style_rotation.metrics.types import SeriesPoint

D = Decimal


def _series(returns: tuple[str, ...], *, start: date = date(2024, 1, 1)) -> IntervalSeries:
    nav = D(1)
    points: list[SeriesPoint] = []
    for index, raw in enumerate(returns):
        daily_return = D(raw)
        nav *= D(1) + daily_return
        points.append(SeriesPoint(start + timedelta(days=index), daily_return, nav))
    dates = tuple(item.nav_date for item in points)
    interval = resolve_interval(template_key="full_history", path_dates=dates, as_of_date=dates[-1])
    return IntervalSeries(interval, dates[0], tuple(points))


def test_absolute_performance_includes_complete_return_risk_and_drawdown_family() -> None:
    series = _series(("0.10", "-0.20", "0.05", "0.02"))
    result = calculate_absolute_performance(series, (D(0),) * 4)
    assert len(result.metrics) == 14
    assert result.quality_status == "very_short_sample_warning"
    assert result.metrics["cumulative_return"].value == D("-0.05752")
    assert result.metrics["maximum_drawdown"].value == D("-0.20")
    assert result.metrics["maximum_drawdown_duration_days"].value == D(3)
    assert result.metrics["positive_daily_return_ratio"].value == D("0.75")
    assert result.metrics["best_daily_return"].value == D("0.10")
    assert result.metrics["worst_daily_return"].value == D("-0.20")
    assert result.metrics["positive_monthly_return_ratio"].value == D(0)


def test_relative_performance_distinguishes_relative_wealth_growth_and_cagr_spread() -> None:
    strategy = _series(("0.02", "0.00", "0.01", "-0.01"))
    benchmark = _series(("0.01", "0.00", "0.00", "-0.01"))
    result = calculate_relative_performance(strategy, benchmark, (D(0),) * 4)
    metrics = result.metrics
    expected_relative = strategy.points[-1].nav / benchmark.points[-1].nav - D(1)
    assert metrics["cumulative_relative_return"].value == expected_relative
    assert metrics["annualized_relative_wealth_growth"].value is not None
    assert metrics["cagr_spread"].value is not None
    assert metrics["annualized_relative_wealth_growth"].value != metrics["cagr_spread"].value
    assert metrics["return_correlation"].value is not None
    assert metrics["beta"].value is not None
    assert metrics["annualized_alpha"].value is not None


def test_zero_variance_returns_are_undefined_not_infinite() -> None:
    strategy = _series(("0.01", "0.01", "0.01"))
    benchmark = _series(("0.01", "0.01", "0.01"))
    absolute = calculate_absolute_performance(strategy, (D(0),) * 3)
    relative = calculate_relative_performance(strategy, benchmark, (D(0),) * 3)
    assert absolute.metrics["sharpe_ratio"].value is None
    assert absolute.metrics["sharpe_ratio"].reason_code == "zero_excess_volatility"
    assert relative.metrics["information_ratio"].value is None
    assert relative.metrics["information_ratio"].reason_code == "zero_tracking_error"
    assert relative.metrics["beta"].value is None
    assert relative.metrics["beta"].reason_code == "zero_benchmark_excess_variance"


def test_short_sample_quality_thresholds_are_explicit() -> None:
    short = calculate_absolute_performance(_series(("0",) * 60), (D(0),) * 60)
    normal = calculate_absolute_performance(_series(("0",) * 252), (D(0),) * 252)
    assert short.quality_status == "short_sample_warning"
    assert normal.quality_status == "normal"
    assert short.metrics["calmar_ratio"].reason_code == "zero_max_drawdown"


def test_relative_metrics_require_aligned_dates() -> None:
    strategy = _series(("0.01", "0.02"))
    benchmark = _series(("0.01", "0.02"), start=date(2024, 1, 2))
    with pytest.raises(PerformanceCalculationError, match="must align"):
        calculate_relative_performance(strategy, benchmark, (D(0), D(0)))
