from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext

from style_rotation.metrics.types import (
    DiagnosticEventInput,
    FactorDiagnosticPeriod,
    FactorDiagnosticSummary,
    MetricValue,
    OpenPrice,
    PerformanceMetricResult,
    RunMetricInput,
    SeriesPoint,
)

ANNUALIZATION_DAYS = Decimal(252)
CALENDAR_DAYS_PER_YEAR = Decimal("365.2425")
CANDIDATE_SYMBOLS = ("IWF", "IWD", "IWO", "IWN")
ZERO = Decimal(0)
ONE = Decimal(1)


class MetricQualityError(RuntimeError):
    """Raised when published upstream inputs cannot support deterministic metrics."""


def build_risk_free_returns(
    nav_dates: tuple[date, ...], calendar_daily_factor_by_date: dict[date, Decimal]
) -> tuple[Decimal, ...]:
    if not nav_dates:
        raise MetricQualityError("Risk-free construction requires at least one NAV date")
    if nav_dates != tuple(sorted(nav_dates)) or len(nav_dates) != len(set(nav_dates)):
        raise MetricQualityError("NAV dates for risk-free construction must be unique and sorted")
    returns = [ZERO]
    for previous_date, nav_date in zip(nav_dates[:-1], nav_dates[1:], strict=True):
        factor = calendar_daily_factor_by_date.get(previous_date)
        if factor is None:
            raise MetricQualityError("Prior known reserve factor is missing")
        if factor <= 0 or not factor.is_finite():
            raise MetricQualityError("Prior known reserve factor must be finite and positive")
        returns.append(factor ** (nav_date - previous_date).days - ONE)
    return tuple(returns)


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise MetricQualityError("Mean requires at least one observation")
    return sum(values, ZERO) / Decimal(len(values))


def _sample_std(values: tuple[Decimal, ...]) -> Decimal | None:
    if len(values) < 2:
        return None
    mean_value = _mean(values)
    variance = sum(((value - mean_value) ** 2 for value in values), ZERO) / Decimal(
        len(values) - 1
    )
    return variance.sqrt()


def _defined(value: Decimal, count: int) -> MetricValue:
    return MetricValue(value, None, count)


def _undefined(reason_code: str, count: int) -> MetricValue:
    return MetricValue(None, reason_code, count)


def _elapsed_years(start_date: date, end_date: date) -> Decimal | None:
    elapsed_days = (end_date - start_date).days
    if elapsed_days <= 0:
        return None
    return Decimal(elapsed_days) / CALENDAR_DAYS_PER_YEAR


def _validate_series(points: tuple[SeriesPoint, ...]) -> None:
    if not points:
        raise MetricQualityError("Performance series cannot be empty")
    dates = tuple(point.nav_date for point in points)
    if dates != tuple(sorted(dates)) or len(dates) != len(set(dates)):
        raise MetricQualityError("Performance dates must be unique and sorted")
    if any(point.nav <= 0 for point in points):
        raise MetricQualityError("Performance NAV must stay positive")
    if any(
        not point.daily_return.is_finite() or not point.nav.is_finite() for point in points
    ):
        raise MetricQualityError("Performance values must be finite")


def calculate_core_metrics(
    points: tuple[SeriesPoint, ...],
    risk_free_returns: tuple[Decimal, ...],
    *,
    first_execution_date: date,
    official_end_date: date,
) -> dict[str, MetricValue]:
    _validate_series(points)
    count = len(points)
    if len(risk_free_returns) != count:
        raise MetricQualityError("Risk-free and performance observations must align exactly")
    if any(not value.is_finite() for value in risk_free_returns):
        raise MetricQualityError("Risk-free returns must be finite")
    returns = tuple(point.daily_return for point in points)
    excess_returns = tuple(
        daily_return - risk_free
        for daily_return, risk_free in zip(returns, risk_free_returns, strict=True)
    )
    total_return = points[-1].nav - ONE
    elapsed_years = _elapsed_years(first_execution_date, official_end_date)
    if elapsed_years is None:
        cagr = _undefined("nonpositive_elapsed_years", count)
    else:
        with localcontext() as context:
            context.prec = 40
            cagr = _defined(points[-1].nav ** (ONE / elapsed_years) - ONE, count)

    daily_std = _sample_std(returns)
    if daily_std is None:
        annualized_volatility = _undefined("insufficient_observations", count)
    else:
        annualized_volatility = _defined(daily_std * ANNUALIZATION_DAYS.sqrt(), count)

    running_peak = ONE
    max_drawdown = ZERO
    for point in points:
        running_peak = max(running_peak, point.nav)
        max_drawdown = min(max_drawdown, point.nav / running_peak - ONE)

    excess_std = _sample_std(excess_returns)
    if excess_std is None:
        sharpe = _undefined("insufficient_observations", count)
    elif excess_std == 0:
        sharpe = _undefined("zero_excess_volatility", count)
    else:
        sharpe = _defined(
            _mean(excess_returns) / excess_std * ANNUALIZATION_DAYS.sqrt(), count
        )

    downside_deviation = (
        sum((min(value, ZERO) ** 2 for value in excess_returns), ZERO) / Decimal(count)
    ).sqrt()
    if downside_deviation == 0:
        sortino = _undefined("zero_downside_deviation", count)
    else:
        sortino = _defined(
            _mean(excess_returns) / downside_deviation * ANNUALIZATION_DAYS.sqrt(), count
        )

    if cagr.value is None:
        calmar = _undefined(cagr.reason_code or "undefined_cagr", count)
    elif max_drawdown == 0:
        calmar = _undefined("zero_max_drawdown", count)
    else:
        calmar = _defined(cagr.value / abs(max_drawdown), count)

    return {
        "cumulative_return": _defined(total_return, count),
        "cagr": cagr,
        "annualized_volatility": annualized_volatility,
        "max_drawdown": _defined(max_drawdown, count),
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
    }


def calculate_relative_metrics(
    strategy: tuple[SeriesPoint, ...], benchmark: tuple[SeriesPoint, ...]
) -> dict[str, MetricValue]:
    _validate_series(strategy)
    _validate_series(benchmark)
    strategy_dates = tuple(point.nav_date for point in strategy)
    benchmark_dates = tuple(point.nav_date for point in benchmark)
    if strategy_dates != benchmark_dates:
        raise MetricQualityError("Strategy and benchmark dates must align exactly")
    count = len(strategy)
    active_returns = tuple(
        strategy_point.daily_return - benchmark_point.daily_return
        for strategy_point, benchmark_point in zip(strategy, benchmark, strict=True)
    )
    active_std = _sample_std(active_returns)
    relative_return = strategy[-1].nav / benchmark[-1].nav - ONE
    if active_std is None:
        tracking_error = _undefined("insufficient_observations", count)
        information_ratio = _undefined("insufficient_observations", count)
    else:
        tracking_error = _defined(active_std * ANNUALIZATION_DAYS.sqrt(), count)
        if active_std == 0:
            information_ratio = _undefined("zero_tracking_error", count)
        else:
            information_ratio = _defined(
                _mean(active_returns) / active_std * ANNUALIZATION_DAYS.sqrt(), count
            )
    return {
        "cumulative_relative_return": _defined(relative_return, count),
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
    }


_CORE_UNITS = {
    "cumulative_return": "decimal_return",
    "cagr": "decimal_return_per_year",
    "annualized_volatility": "decimal_return_per_sqrt_year",
    "max_drawdown": "decimal_return",
    "sharpe_ratio": "ratio",
    "sortino_ratio": "ratio",
    "calmar_ratio": "ratio",
}

_RELATIVE_UNITS = {
    "cumulative_relative_return": "decimal_return",
    "tracking_error": "decimal_return_per_sqrt_year",
    "information_ratio": "ratio",
}


def _result(
    series_type: str,
    return_basis: str,
    metric_key: str,
    metric: MetricValue,
    unit: str,
) -> PerformanceMetricResult:
    return PerformanceMetricResult(
        series_type=series_type,
        return_basis=return_basis,
        metric_key=metric_key,
        value=metric.value,
        value_status="defined" if metric.value is not None else "undefined",
        reason_code=metric.reason_code,
        observation_count=metric.observation_count,
        unit=unit,
    )


def calculate_run_metrics(run: RunMetricInput) -> tuple[PerformanceMetricResult, ...]:
    series_by_scope = {
        ("strategy", "gross"): run.strategy_gross,
        ("strategy", "net"): run.strategy_net,
        ("four_etf_equal_weight", "gross"): run.equal_weight_gross,
        ("four_etf_equal_weight", "net"): run.equal_weight_net,
        ("spy_buy_hold", "gross"): run.spy_gross,
        ("spy_buy_hold", "net"): run.spy_net,
    }
    expected_dates = tuple(point.nav_date for point in run.strategy_gross)
    results: list[PerformanceMetricResult] = []
    for (series_type, basis), points in series_by_scope.items():
        if tuple(point.nav_date for point in points) != expected_dates:
            raise MetricQualityError("All strategy and benchmark series must align exactly")
        core = calculate_core_metrics(
            points,
            run.risk_free_returns,
            first_execution_date=run.first_execution_date,
            official_end_date=run.official_end_date,
        )
        results.extend(
            _result(series_type, basis, key, value, _CORE_UNITS[key])
            for key, value in core.items()
        )

    for basis, strategy, benchmark in (
        ("gross", run.strategy_gross, run.equal_weight_gross),
        ("net", run.strategy_net, run.equal_weight_net),
    ):
        relative = calculate_relative_metrics(strategy, benchmark)
        results.extend(
            _result(
                "strategy_vs_four_etf_equal_weight",
                basis,
                key,
                value,
                _RELATIVE_UNITS[key],
            )
            for key, value in relative.items()
        )

    count = len(run.strategy_gross)
    if not (
        len(run.daily_turnover)
        == len(run.transaction_cost_amounts)
        == len(run.reserve_close_weights)
        == count
    ):
        raise MetricQualityError("Trading behavior inputs must align with strategy NAV")
    elapsed_years = _elapsed_years(run.first_execution_date, run.official_end_date)
    turnover_metric = (
        _undefined("nonpositive_elapsed_years", count)
        if elapsed_years is None
        else _defined(sum(run.daily_turnover, ZERO) / elapsed_years, count)
    )
    results.append(
        _result(
            "strategy",
            "cost_independent",
            "annualized_turnover",
            turnover_metric,
            "single_sided_turnover_per_year",
        )
    )
    results.append(
        _result(
            "strategy",
            "net",
            "cumulative_transaction_cost",
            _defined(sum(run.transaction_cost_amounts, ZERO), count),
            "initial_nav_wealth_units",
        )
    )
    if run.strategy_template == "trend_filtered":
        reserve_metric = _defined(_mean(run.reserve_close_weights), count)
        results.append(
            _result(
                "strategy",
                "cost_independent",
                "average_reserve_weight",
                reserve_metric,
                "portfolio_weight",
            )
        )
    else:
        results.append(
            PerformanceMetricResult(
                series_type="strategy",
                return_basis="cost_independent",
                metric_key="average_reserve_weight",
                value=None,
                value_status="not_applicable",
                reason_code="cross_sectional_template_has_no_reserve_budget",
                observation_count=count,
                unit="portfolio_weight",
            )
        )
    return tuple(results)


def _average_ranks(values: dict[str, Decimal]) -> dict[str, Decimal]:
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    ranks: dict[str, Decimal] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (Decimal(index + 1) + Decimal(end)) / Decimal(2)
        for symbol, _value in ordered[index:end]:
            ranks[symbol] = average_rank
        index = end
    return ranks


def _pearson(values_x: tuple[Decimal, ...], values_y: tuple[Decimal, ...]) -> Decimal | None:
    mean_x = _mean(values_x)
    mean_y = _mean(values_y)
    centered_x = tuple(value - mean_x for value in values_x)
    centered_y = tuple(value - mean_y for value in values_y)
    denominator = (
        sum((value**2 for value in centered_x), ZERO)
        * sum((value**2 for value in centered_y), ZERO)
    ).sqrt()
    if denominator == 0:
        return None
    return sum(
        (left * right for left, right in zip(centered_x, centered_y, strict=True)), ZERO
    ) / denominator


def calculate_factor_diagnostics(
    events: tuple[DiagnosticEventInput, ...], prices: tuple[OpenPrice, ...]
) -> tuple[FactorDiagnosticPeriod, ...]:
    price_by_symbol_date = {(price.symbol, price.trade_date): price.open_adj for price in prices}
    grouped: dict[tuple[uuid.UUID, str], list[DiagnosticEventInput]] = defaultdict(list)
    for event in events:
        grouped[(event.factor_variant_id, event.rebalance_frequency)].append(event)
    periods: list[FactorDiagnosticPeriod] = []
    required_symbols = set(CANDIDATE_SYMBOLS)
    for group_events in grouped.values():
        ordered = sorted(group_events, key=lambda event: event.execution_date)
        if len({event.execution_date for event in ordered}) != len(ordered):
            raise MetricQualityError("Diagnostic execution dates must be unique within a group")
        for event, next_event in zip(ordered[:-1], ordered[1:], strict=True):
            if set(event.oriented_values) != required_symbols:
                raise MetricQualityError("Rank IC requires exactly four candidate ETF values")
            if set(event.deterministic_ranks) != required_symbols or set(
                event.deterministic_ranks.values()
            ) != {1, 2, 3, 4}:
                raise MetricQualityError("Top-Bottom requires deterministic ranks one through four")
            forward_returns: dict[str, Decimal] = {}
            for symbol in CANDIDATE_SYMBOLS:
                current_open = price_by_symbol_date.get((symbol, event.execution_date))
                next_open = price_by_symbol_date.get((symbol, next_event.execution_date))
                if current_open is None or next_open is None:
                    raise MetricQualityError("Diagnostic execution open price is missing")
                if current_open <= 0 or next_open <= 0:
                    raise MetricQualityError("Diagnostic execution open price must be positive")
                forward_returns[symbol] = next_open / current_open - ONE

            if len(set(event.oriented_values.values())) == 1:
                rank_ic = None
                rank_ic_reason = "constant_factor_cross_section"
            elif len(set(forward_returns.values())) == 1:
                rank_ic = None
                rank_ic_reason = "constant_forward_return_cross_section"
            else:
                factor_ranks = _average_ranks(event.oriented_values)
                return_ranks = _average_ranks(forward_returns)
                rank_ic = _pearson(
                    tuple(factor_ranks[symbol] for symbol in CANDIDATE_SYMBOLS),
                    tuple(return_ranks[symbol] for symbol in CANDIDATE_SYMBOLS),
                )
                if rank_ic is None:
                    raise MetricQualityError("Unexpected undefined Spearman correlation")
                rank_ic_reason = None

            top = tuple(
                forward_returns[symbol]
                for symbol in CANDIDATE_SYMBOLS
                if event.deterministic_ranks[symbol] <= 2
            )
            bottom = tuple(
                forward_returns[symbol]
                for symbol in CANDIDATE_SYMBOLS
                if event.deterministic_ranks[symbol] >= 3
            )
            periods.append(
                FactorDiagnosticPeriod(
                    factor_variant_id=event.factor_variant_id,
                    variant_key=event.variant_key,
                    rebalance_frequency=event.rebalance_frequency,
                    signal_date=event.signal_date,
                    execution_date=event.execution_date,
                    next_execution_date=next_event.execution_date,
                    rank_ic=rank_ic,
                    rank_ic_reason_code=rank_ic_reason,
                    top_bottom_return_spread=_mean(top) - _mean(bottom),
                )
            )
    return tuple(
        sorted(
            periods,
            key=lambda period: (
                period.variant_key,
                period.rebalance_frequency,
                period.signal_date,
            ),
        )
    )


def summarize_factor_diagnostics(
    periods: tuple[FactorDiagnosticPeriod, ...],
) -> tuple[FactorDiagnosticSummary, ...]:
    grouped: dict[tuple[uuid.UUID, str], list[FactorDiagnosticPeriod]] = defaultdict(list)
    for period in periods:
        grouped[(period.factor_variant_id, period.rebalance_frequency)].append(period)
    summaries: list[FactorDiagnosticSummary] = []
    for group_periods in grouped.values():
        first = group_periods[0]
        valid_ics = tuple(
            period.rank_ic for period in group_periods if period.rank_ic is not None
        )
        if valid_ics:
            mean_rank_ic = _mean(valid_ics)
            positive_ic_ratio = Decimal(sum(value > 0 for value in valid_ics)) / Decimal(
                len(valid_ics)
            )
            reason = None
        else:
            mean_rank_ic = None
            positive_ic_ratio = None
            reason = "no_valid_rank_ic_observations"
        summaries.append(
            FactorDiagnosticSummary(
                factor_variant_id=first.factor_variant_id,
                variant_key=first.variant_key,
                rebalance_frequency=first.rebalance_frequency,
                period_count=len(group_periods),
                valid_ic_count=len(valid_ics),
                undefined_ic_count=len(group_periods) - len(valid_ics),
                mean_rank_ic=mean_rank_ic,
                positive_ic_ratio=positive_ic_ratio,
                mean_top_bottom_return_spread=_mean(
                    tuple(period.top_bottom_return_spread for period in group_periods)
                ),
                ic_summary_reason_code=reason,
            )
        )
    return tuple(
        sorted(
            summaries,
            key=lambda summary: (summary.variant_key, summary.rebalance_frequency),
        )
    )
