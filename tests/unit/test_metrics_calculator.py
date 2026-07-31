from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from style_rotation.metrics.calculator import (
    build_risk_free_returns,
    calculate_core_metrics,
    calculate_factor_diagnostics,
    calculate_relative_metrics,
    calculate_run_metrics,
    summarize_factor_diagnostics,
)
from style_rotation.metrics.types import (
    DiagnosticEventInput,
    OpenPrice,
    RunMetricInput,
    SeriesPoint,
)

SYMBOLS = ("IWF", "IWD", "IWO", "IWN")


def _series(start: date, returns: tuple[str, ...]) -> tuple[SeriesPoint, ...]:
    nav = Decimal(1)
    points: list[SeriesPoint] = []
    for index, raw_return in enumerate(returns):
        daily_return = Decimal(raw_return)
        nav *= Decimal(1) + daily_return
        points.append(SeriesPoint(start + timedelta(days=index), daily_return, nav))
    return tuple(points)


def test_performance_metrics_match_hand_calculated_sample() -> None:
    start = date(2025, 1, 1)
    strategy = _series(start, ("0.01", "-0.01", "0.03", "-0.01"))
    metrics = calculate_core_metrics(
        strategy,
        (Decimal(0),) * 4,
        first_execution_date=start,
        official_end_date=start + timedelta(days=3),
    )
    assert metrics["cumulative_return"].value == Decimal("0.01959803")
    assert float(metrics["annualized_volatility"].value or 0) == pytest.approx(
        0.303973683071413
    )
    assert float(metrics["sharpe_ratio"].value or 0) == pytest.approx(4.14509567824654)
    assert float(metrics["sortino_ratio"].value or 0) == pytest.approx(11.2249721603218)
    assert metrics["max_drawdown"].value == Decimal("-0.01")

    benchmark = _series(start, ("0", "-0.01", "0.01", "-0.01"))
    relative = calculate_relative_metrics(strategy, benchmark)
    assert float(relative["tracking_error"].value or 0) == pytest.approx(0.151986841535707)
    assert float(relative["information_ratio"].value or 0) == pytest.approx(
        12.4352870347396
    )
    assert relative["cumulative_relative_return"].value == Decimal("0.03")


def test_undefined_ratios_return_reason_codes_not_infinity() -> None:
    start = date(2025, 1, 1)
    points = _series(start, ("0.01", "0.01"))
    metrics = calculate_core_metrics(
        points,
        (Decimal(0), Decimal(0)),
        first_execution_date=start,
        official_end_date=start + timedelta(days=1),
    )
    assert metrics["sharpe_ratio"].value is None
    assert metrics["sharpe_ratio"].reason_code == "zero_excess_volatility"
    assert metrics["sortino_ratio"].value is None
    assert metrics["sortino_ratio"].reason_code == "zero_downside_deviation"
    assert metrics["calmar_ratio"].value is None
    assert metrics["calmar_ratio"].reason_code == "zero_max_drawdown"


def test_drawdown_includes_initial_wealth_peak() -> None:
    start = date(2025, 1, 1)
    points = _series(start, ("-0.10", "0.20"))
    metrics = calculate_core_metrics(
        points,
        (Decimal(0), Decimal(0)),
        first_execution_date=start,
        official_end_date=start + timedelta(days=1),
    )
    assert metrics["max_drawdown"].value == Decimal("-0.10")


def test_run_metrics_include_calendar_annualized_trading_behavior() -> None:
    start = date(2025, 1, 1)
    end = date(2025, 1, 11)
    series = (
        SeriesPoint(start, Decimal("0.01"), Decimal("1.01")),
        SeriesPoint(end, Decimal("0"), Decimal("1.01")),
    )
    run = RunMetricInput(
        run_id=uuid.uuid4(),
        factor_variant_id=uuid.uuid4(),
        factor_variant_key="golden_factor",
        rebalance_frequency="weekly",
        strategy_template="trend_filtered",
        transaction_cost_bps=Decimal(5),
        first_execution_date=start,
        official_end_date=end,
        strategy_gross=series,
        strategy_net=series,
        equal_weight_gross=series,
        equal_weight_net=series,
        spy_gross=series,
        spy_net=series,
        risk_free_returns=(Decimal(0), Decimal(0)),
        daily_turnover=(Decimal(1), Decimal("0.25")),
        transaction_cost_amounts=(Decimal("0.001"), Decimal("0.002")),
        reserve_close_weights=(Decimal(0), Decimal(1)),
        run_fingerprint="a" * 64,
        input_manifest_hash="b" * 64,
    )
    results = calculate_run_metrics(run)
    assert len(results) == 51
    keyed = {(result.return_basis, result.metric_key): result for result in results}
    expected_turnover = Decimal("1.25") / (Decimal(10) / Decimal("365.2425"))
    assert keyed[("cost_independent", "annualized_turnover")].value == expected_turnover
    assert keyed[("net", "cumulative_transaction_cost")].value == Decimal("0.003")
    assert keyed[("cost_independent", "average_reserve_weight")].value == Decimal("0.5")


def test_risk_free_return_uses_prior_factor_over_calendar_gap() -> None:
    friday = date(2025, 1, 3)
    monday = date(2025, 1, 6)
    returns = build_risk_free_returns(
        (friday, monday),
        {friday: Decimal("1.001"), monday: Decimal("1.5")},
    )
    assert returns == (Decimal(0), Decimal("1.001") ** 3 - 1)


def _event(
    variant_id: uuid.UUID,
    index: int,
    execution_date: date,
    values: tuple[str, str, str, str] = ("4", "3", "2", "1"),
) -> DiagnosticEventInput:
    return DiagnosticEventInput(
        factor_variant_id=variant_id,
        variant_key="golden_factor",
        rebalance_frequency="weekly",
        signal_date=execution_date - timedelta(days=1),
        execution_date=execution_date,
        oriented_values={
            symbol: Decimal(value) for symbol, value in zip(SYMBOLS, values, strict=True)
        },
        deterministic_ranks={symbol: rank for rank, symbol in enumerate(SYMBOLS, start=1)},
    )


def test_rank_ic_and_top_bottom_diagnostics_match_golden_periods() -> None:
    variant_id = uuid.uuid4()
    dates = tuple(date(2025, 1, 6) + timedelta(days=7 * index) for index in range(4))
    interval_returns = (
        (Decimal("0.4"), Decimal("0.3"), Decimal("0.2"), Decimal("0.1")),
        (Decimal("0.2"), Decimal("0.4"), Decimal("0.1"), Decimal("0.3")),
        (Decimal("0.1"), Decimal("0.2"), Decimal("0.3"), Decimal("0.4")),
    )
    opens = {symbol: Decimal(100) for symbol in SYMBOLS}
    prices: list[OpenPrice] = [OpenPrice(symbol, dates[0], opens[symbol]) for symbol in SYMBOLS]
    for next_date, returns in zip(dates[1:], interval_returns, strict=True):
        for symbol, forward_return in zip(SYMBOLS, returns, strict=True):
            opens[symbol] *= Decimal(1) + forward_return
            prices.append(OpenPrice(symbol, next_date, opens[symbol]))
    events = tuple(
        _event(variant_id, index, execution_date)
        for index, execution_date in enumerate(dates)
    )
    periods = calculate_factor_diagnostics(events, tuple(prices))
    assert [period.rank_ic for period in periods] == [Decimal(1), Decimal(0), Decimal(-1)]
    assert [period.top_bottom_return_spread for period in periods] == [
        Decimal("0.2"),
        Decimal("0.1"),
        Decimal("-0.2"),
    ]
    summary = summarize_factor_diagnostics(periods)[0]
    assert summary.mean_rank_ic == 0
    assert summary.positive_ic_ratio == Decimal(1) / Decimal(3)
    assert summary.mean_top_bottom_return_spread == Decimal(1) / Decimal(30)
    assert summary.period_count == 3


def test_rank_ic_uses_average_ranks_for_factor_ties() -> None:
    variant_id = uuid.uuid4()
    first = date(2025, 1, 6)
    second = date(2025, 1, 13)
    events = (
        _event(variant_id, 0, first, ("4", "4", "2", "1")),
        _event(variant_id, 1, second),
    )
    prices = tuple(
        [OpenPrice(symbol, first, Decimal(100)) for symbol in SYMBOLS]
        + [
            OpenPrice(symbol, second, Decimal(100) * (Decimal(1) + forward_return))
            for symbol, forward_return in zip(
                SYMBOLS,
                (Decimal("0.4"), Decimal("0.3"), Decimal("0.2"), Decimal("0.1")),
                strict=True,
            )
        ]
    )
    period = calculate_factor_diagnostics(events, prices)[0]
    assert float(period.rank_ic or 0) == pytest.approx(0.9486832980505138)


def test_constant_factor_cross_section_produces_explicit_undefined_ic() -> None:
    variant_id = uuid.uuid4()
    first = date(2025, 1, 6)
    second = date(2025, 1, 13)
    events = (
        _event(variant_id, 0, first, ("1", "1", "1", "1")),
        _event(variant_id, 1, second),
    )
    prices = tuple(
        [OpenPrice(symbol, first, Decimal(100)) for symbol in SYMBOLS]
        + [
            OpenPrice(symbol, second, Decimal(100 + index))
            for index, symbol in enumerate(SYMBOLS)
        ]
    )
    period = calculate_factor_diagnostics(events, prices)[0]
    assert period.rank_ic is None
    assert period.rank_ic_reason_code == "constant_factor_cross_section"
