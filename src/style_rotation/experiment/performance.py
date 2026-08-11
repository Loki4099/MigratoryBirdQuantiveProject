from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from style_rotation.experiment.intervals import IntervalSeries
from style_rotation.metrics.types import MetricValue, SeriesPoint

ZERO = Decimal(0)
ONE = Decimal(1)
ANNUALIZATION_DAYS = Decimal(252)
CALENDAR_DAYS_PER_YEAR = Decimal("365.2425")


class PerformanceCalculationError(RuntimeError):
    """Raised when interval performance inputs cannot be compared deterministically."""


@dataclass(frozen=True, slots=True)
class IntervalPerformance:
    observation_count: int
    quality_status: str
    metrics: dict[str, MetricValue]


def calculate_absolute_performance(
    series: IntervalSeries, risk_free_returns: tuple[Decimal, ...]
) -> IntervalPerformance:
    points = series.points
    _validate_points(points)
    count = len(points)
    if len(risk_free_returns) != count or any(not value.is_finite() for value in risk_free_returns):
        raise PerformanceCalculationError("Risk-free returns must align and remain finite")
    returns = tuple(item.daily_return for item in points)
    excess = tuple(
        value - risk_free for value, risk_free in zip(returns, risk_free_returns, strict=True)
    )
    elapsed_years = _elapsed_years(series.annualization_start, points[-1].nav_date)
    cumulative = points[-1].nav - ONE
    cagr = (
        _undefined("nonpositive_elapsed_years", count)
        if elapsed_years is None
        else _defined(_power_return(points[-1].nav, elapsed_years), count)
    )
    daily_std = _sample_std(returns)
    volatility = (
        _undefined("insufficient_observations", count)
        if daily_std is None
        else _defined(daily_std * ANNUALIZATION_DAYS.sqrt(), count)
    )
    excess_std = _sample_std(excess)
    if excess_std is None:
        sharpe = _undefined("insufficient_observations", count)
    elif excess_std == 0:
        sharpe = _undefined("zero_excess_volatility", count)
    else:
        sharpe = _defined(_mean(excess) / excess_std * ANNUALIZATION_DAYS.sqrt(), count)
    downside = (sum((min(value, ZERO) ** 2 for value in excess), ZERO) / Decimal(count)).sqrt()
    sortino = (
        _undefined("zero_downside_deviation", count)
        if downside == 0
        else _defined(_mean(excess) / downside * ANNUALIZATION_DAYS.sqrt(), count)
    )
    max_drawdown, duration_days = _drawdown(points, series.annualization_start)
    if cagr.value is None:
        calmar = _undefined(cagr.reason_code or "undefined_cagr", count)
    elif max_drawdown == 0:
        calmar = _undefined("zero_max_drawdown", count)
    else:
        calmar = _defined(cagr.value / abs(max_drawdown), count)
    monthly = _monthly_returns(points)
    metrics = {
        "cumulative_return": _defined(cumulative, count),
        "cagr": cagr,
        "annualized_volatility": volatility,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "maximum_drawdown": _defined(max_drawdown, count),
        "maximum_drawdown_duration_days": _defined(Decimal(duration_days), count),
        "calmar_ratio": calmar,
        "positive_daily_return_ratio": _defined(_positive_ratio(returns), count),
        "best_daily_return": _defined(max(returns), count),
        "worst_daily_return": _defined(min(returns), count),
        "positive_monthly_return_ratio": _defined(_positive_ratio(monthly), len(monthly)),
        "best_monthly_return": _defined(max(monthly), len(monthly)),
        "worst_monthly_return": _defined(min(monthly), len(monthly)),
    }
    return IntervalPerformance(count, _quality_status(count), metrics)


def calculate_relative_performance(
    strategy: IntervalSeries,
    benchmark: IntervalSeries,
    risk_free_returns: tuple[Decimal, ...],
) -> IntervalPerformance:
    _validate_points(strategy.points)
    _validate_points(benchmark.points)
    if strategy.annualization_start != benchmark.annualization_start:
        raise PerformanceCalculationError("Strategy and benchmark annualization starts must align")
    strategy_dates = tuple(item.nav_date for item in strategy.points)
    if strategy_dates != tuple(item.nav_date for item in benchmark.points):
        raise PerformanceCalculationError("Strategy and benchmark dates must align")
    count = len(strategy.points)
    if len(risk_free_returns) != count:
        raise PerformanceCalculationError("Risk-free returns must align")
    strategy_returns = tuple(item.daily_return for item in strategy.points)
    benchmark_returns = tuple(item.daily_return for item in benchmark.points)
    active = tuple(
        left - right for left, right in zip(strategy_returns, benchmark_returns, strict=True)
    )
    elapsed_years = _elapsed_years(strategy.annualization_start, strategy.points[-1].nav_date)
    relative_wealth = strategy.points[-1].nav / benchmark.points[-1].nav
    annualized_relative = (
        _undefined("nonpositive_elapsed_years", count)
        if elapsed_years is None
        else _defined(_power_return(relative_wealth, elapsed_years), count)
    )
    strategy_cagr = (
        None if elapsed_years is None else _power_return(strategy.points[-1].nav, elapsed_years)
    )
    benchmark_cagr = (
        None if elapsed_years is None else _power_return(benchmark.points[-1].nav, elapsed_years)
    )
    cagr_spread = (
        _undefined("nonpositive_elapsed_years", count)
        if strategy_cagr is None or benchmark_cagr is None
        else _defined(strategy_cagr - benchmark_cagr, count)
    )
    active_std = _sample_std(active)
    if active_std is None:
        tracking_error = _undefined("insufficient_observations", count)
        information_ratio = _undefined("insufficient_observations", count)
    else:
        tracking_error = _defined(active_std * ANNUALIZATION_DAYS.sqrt(), count)
        information_ratio = (
            _undefined("zero_tracking_error", count)
            if active_std == 0
            else _defined(_mean(active) / active_std * ANNUALIZATION_DAYS.sqrt(), count)
        )
    correlation = _correlation(strategy_returns, benchmark_returns)
    strategy_excess = tuple(
        value - risk_free
        for value, risk_free in zip(strategy_returns, risk_free_returns, strict=True)
    )
    benchmark_excess = tuple(
        value - risk_free
        for value, risk_free in zip(benchmark_returns, risk_free_returns, strict=True)
    )
    benchmark_variance = _sample_variance(benchmark_excess)
    if benchmark_variance is None:
        beta = _undefined("insufficient_observations", count)
        alpha = _undefined("insufficient_observations", count)
    elif benchmark_variance == 0:
        beta = _undefined("zero_benchmark_excess_variance", count)
        alpha = _undefined("zero_benchmark_excess_variance", count)
    else:
        beta_value = _sample_covariance(strategy_excess, benchmark_excess) / benchmark_variance
        beta = _defined(beta_value, count)
        alpha = _defined(
            (_mean(strategy_excess) - beta_value * _mean(benchmark_excess)) * ANNUALIZATION_DAYS,
            count,
        )
    metrics = {
        "cumulative_relative_return": _defined(relative_wealth - ONE, count),
        "annualized_relative_wealth_growth": annualized_relative,
        "cagr_spread": cagr_spread,
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
        "return_correlation": (
            _undefined("zero_return_variance", count)
            if correlation is None
            else _defined(correlation, count)
        ),
        "beta": beta,
        "annualized_alpha": alpha,
    }
    return IntervalPerformance(count, _quality_status(count), metrics)


def _validate_points(points: tuple[SeriesPoint, ...]) -> None:
    if not points:
        raise PerformanceCalculationError("Performance series cannot be empty")
    dates = tuple(item.nav_date for item in points)
    if dates != tuple(sorted(set(dates))):
        raise PerformanceCalculationError("Performance dates must be unique and sorted")
    if any(
        item.nav <= 0 or not item.nav.is_finite() or not item.daily_return.is_finite()
        for item in points
    ):
        raise PerformanceCalculationError("Performance values must be finite and NAV positive")


def _defined(value: Decimal, count: int) -> MetricValue:
    return MetricValue(value, None, count)


def _undefined(reason: str, count: int) -> MetricValue:
    return MetricValue(None, reason, count)


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    return sum(values, ZERO) / Decimal(len(values))


def _sample_variance(values: tuple[Decimal, ...]) -> Decimal | None:
    if len(values) < 2:
        return None
    mean = _mean(values)
    return sum(((item - mean) ** 2 for item in values), ZERO) / Decimal(len(values) - 1)


def _sample_std(values: tuple[Decimal, ...]) -> Decimal | None:
    variance = _sample_variance(values)
    return None if variance is None else variance.sqrt()


def _sample_covariance(left: tuple[Decimal, ...], right: tuple[Decimal, ...]) -> Decimal:
    left_mean, right_mean = _mean(left), _mean(right)
    return sum(
        ((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True)),
        ZERO,
    ) / Decimal(len(left) - 1)


def _correlation(left: tuple[Decimal, ...], right: tuple[Decimal, ...]) -> Decimal | None:
    left_std, right_std = _sample_std(left), _sample_std(right)
    if left_std is None or right_std is None or left_std == ZERO or right_std == ZERO:
        return None
    return _sample_covariance(left, right) / (left_std * right_std)


def _elapsed_years(start: date, end: date) -> Decimal | None:
    days = (end - start).days
    return None if days <= 0 else Decimal(days) / CALENDAR_DAYS_PER_YEAR


def _power_return(terminal_nav: Decimal, years: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 40
        return terminal_nav ** (ONE / years) - ONE


def _drawdown(points: tuple[SeriesPoint, ...], baseline_date: date) -> tuple[Decimal, int]:
    peak_nav = ONE
    peak_date = baseline_date
    max_drawdown = ZERO
    longest_duration = 0
    for point in points:
        if point.nav >= peak_nav:
            peak_nav = point.nav
            peak_date = point.nav_date
        else:
            max_drawdown = min(max_drawdown, point.nav / peak_nav - ONE)
            longest_duration = max(longest_duration, (point.nav_date - peak_date).days)
    return max_drawdown, longest_duration


def _monthly_returns(points: tuple[SeriesPoint, ...]) -> tuple[Decimal, ...]:
    grouped: dict[tuple[int, int], list[Decimal]] = defaultdict(list)
    for point in points:
        grouped[(point.nav_date.year, point.nav_date.month)].append(point.daily_return)
    return tuple(_compound(tuple(grouped[key])) for key in sorted(grouped))


def _compound(values: tuple[Decimal, ...]) -> Decimal:
    result = ONE
    for value in values:
        result *= ONE + value
    return result - ONE


def _positive_ratio(values: tuple[Decimal, ...]) -> Decimal:
    return Decimal(sum(value > 0 for value in values)) / Decimal(len(values))


def _quality_status(count: int) -> str:
    if count < 60:
        return "very_short_sample_warning"
    if count < 252:
        return "short_sample_warning"
    return "normal"
