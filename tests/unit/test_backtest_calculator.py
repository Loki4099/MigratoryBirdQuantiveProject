from datetime import date
from decimal import Decimal

import pytest

from style_rotation.backtest.calculator import RESERVE_SLEEVE, BacktestQualityError, run_backtest
from style_rotation.backtest.types import ExecutionTarget
from style_rotation.data.types import CleanMarketPriceRecord, ReserveDailyRecord


def _price(symbol: str, day: date, open_value: str, close_value: str) -> CleanMarketPriceRecord:
    open_price = Decimal(open_value)
    close_price = Decimal(close_value)
    return CleanMarketPriceRecord(
        symbol=symbol,
        trade_date=day,
        open_adj=open_price,
        high_adj=max(open_price, close_price),
        low_adj=min(open_price, close_price),
        close_adj=close_price,
        adj_factor=Decimal(1),
        volume_raw=1_000,
        dividends=Decimal(0),
        stock_splits=Decimal(0),
    )


def _reserve(day: date, factor: str = "1") -> ReserveDailyRecord:
    return ReserveDailyRecord(
        nav_date=day,
        series_id="DGS3MO",
        source_observation_date=day,
        source_available_date=day,
        annual_rate_percent=Decimal(0),
        calendar_daily_factor=Decimal(factor),
    )


def test_initial_open_trade_cost_and_intraday_return_are_separated() -> None:
    first = date(2026, 1, 5)
    second = date(2026, 1, 6)
    prices = (
        _price("AAA", first, "100", "110"),
        _price("AAA", second, "110", "110"),
    )
    target = ExecutionTarget(date(2026, 1, 2), first, {"AAA": Decimal(1)}, Decimal(0))
    result = run_backtest(
        prices=prices,
        reserve_returns=(_reserve(first), _reserve(second)),
        targets=(target,),
        symbols=("AAA",),
        transaction_cost_bps=Decimal(5),
    )
    assert result.executions[0].turnover == Decimal(1)
    assert result.daily_nav[0].gross_nav == Decimal("1.1")
    assert result.daily_nav[0].net_nav == Decimal("0.9995") * Decimal("1.1")
    assert result.trades[0].execution_price == Decimal(100)
    assert result.trades[0].side == "buy"


def test_pretrade_weights_drift_to_open_before_rebalance() -> None:
    first = date(2026, 1, 5)
    second = date(2026, 1, 6)
    prices = (
        _price("AAA", first, "100", "100"),
        _price("AAA", second, "120", "120"),
        _price("BBB", first, "100", "100"),
        _price("BBB", second, "100", "100"),
    )
    targets = (
        ExecutionTarget(
            date(2026, 1, 2),
            first,
            {"AAA": Decimal("0.5"), "BBB": Decimal("0.5")},
            Decimal(0),
        ),
        ExecutionTarget(
            first,
            second,
            {"AAA": Decimal("0.5"), "BBB": Decimal("0.5")},
            Decimal(0),
        ),
    )
    result = run_backtest(
        prices=prices,
        reserve_returns=(_reserve(first), _reserve(second)),
        targets=targets,
        symbols=("AAA", "BBB"),
        transaction_cost_bps=Decimal(0),
    )
    second_execution = result.executions[1]
    expected_pretrade_aaa = Decimal("0.6") / Decimal("1.1")
    expected_turnover = abs(expected_pretrade_aaa - Decimal("0.5"))
    assert second_execution.turnover == pytest.approx(expected_turnover)


def test_reserve_uses_prior_known_factor_over_calendar_gap() -> None:
    friday = date(2026, 1, 2)
    monday = date(2026, 1, 5)
    prices = (
        _price("AAA", friday, "100", "100"),
        _price("AAA", monday, "100", "100"),
    )
    target = ExecutionTarget(date(2026, 1, 1), friday, {"AAA": Decimal(0)}, Decimal(1))
    result = run_backtest(
        prices=prices,
        reserve_returns=(_reserve(friday, "1.01"), _reserve(monday, "1.50")),
        targets=(target,),
        symbols=("AAA",),
        transaction_cost_bps=Decimal(0),
    )
    assert result.daily_nav[1].gross_nav == Decimal("1.01") ** 3
    monday_weights = {
        item.sleeve: item.close_weight for item in result.daily_positions if item.nav_date == monday
    }
    assert monday_weights[RESERVE_SLEEVE] == 1


def test_terminal_position_is_not_liquidated() -> None:
    first = date(2026, 1, 5)
    second = date(2026, 1, 6)
    result = run_backtest(
        prices=(
            _price("AAA", first, "100", "100"),
            _price("AAA", second, "100", "100"),
        ),
        reserve_returns=(_reserve(first), _reserve(second)),
        targets=(ExecutionTarget(date(2026, 1, 2), first, {"AAA": Decimal(1)}, Decimal(0)),),
        symbols=("AAA",),
        transaction_cost_bps=Decimal(10),
    )
    assert len(result.executions) == 1
    assert len(result.trades) == 1
    final_asset_position = next(
        item
        for item in result.daily_positions
        if item.nav_date == second and item.sleeve == "AAA"
    )
    assert final_asset_position.close_weight == 1


def test_all_execution_dates_must_exist_in_the_price_calendar() -> None:
    first = date(2026, 1, 5)
    missing = date(2026, 1, 6)
    targets = (
        ExecutionTarget(date(2026, 1, 2), first, {"AAA": Decimal(1)}, Decimal(0)),
        ExecutionTarget(first, missing, {"AAA": Decimal(1)}, Decimal(0)),
    )
    with pytest.raises(BacktestQualityError, match="Every execution date"):
        run_backtest(
            prices=(_price("AAA", first, "100", "100"),),
            reserve_returns=(_reserve(first),),
            targets=targets,
            symbols=("AAA",),
            transaction_cost_bps=Decimal(0),
        )
