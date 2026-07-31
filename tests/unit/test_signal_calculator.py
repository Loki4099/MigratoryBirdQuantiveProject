from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from style_rotation.data.types import CleanMarketPriceRecord
from style_rotation.domain.enums import (
    FactorDirection,
    RebalanceFrequency,
    StrategyTemplate,
)
from style_rotation.factors.calculator import CANDIDATE_SYMBOLS
from style_rotation.signals.calculator import calculate_target_positions
from style_rotation.signals.types import FactorSignalPoint


def _inputs(
    *, tied: bool = False
) -> tuple[tuple[CleanMarketPriceRecord, ...], tuple[FactorSignalPoint, ...], date]:
    start = date(2025, 1, 1)
    count = 230
    prices: list[CleanMarketPriceRecord] = []
    factors: list[FactorSignalPoint] = []
    raw_values = {
        "IWF": Decimal(4),
        "IWD": Decimal(4 if tied else 3),
        "IWO": Decimal(2),
        "IWN": Decimal(1),
    }
    for symbol in CANDIDATE_SYMBOLS:
        for index in range(count):
            if symbol == "IWF":
                close = Decimal(100 + index)
            elif symbol == "IWD":
                close = Decimal(200)
            elif symbol == "IWO":
                close = Decimal(400 - index)
            else:
                close = Decimal(150)
            trade_date = start + timedelta(days=index)
            prices.append(
                CleanMarketPriceRecord(
                    symbol=symbol,
                    trade_date=trade_date,
                    open_adj=close,
                    high_adj=close + 1,
                    low_adj=close - 1,
                    close_adj=close,
                    adj_factor=Decimal(1),
                    volume_raw=1_000,
                    dividends=Decimal(0),
                    stock_splits=Decimal(0),
                )
            )
            if index >= 200:
                factors.append(
                    FactorSignalPoint(
                        "momentum_20",
                        symbol,
                        trade_date,
                        raw_values[symbol],
                        FactorDirection.HIGHER_IS_BETTER,
                    )
                )
                factors.append(
                    FactorSignalPoint(
                        "historical_volatility_20",
                        symbol,
                        trade_date,
                        raw_values[symbol],
                        FactorDirection.LOWER_IS_BETTER,
                    )
                )
    return tuple(prices), tuple(factors), start + timedelta(days=200)


def test_cross_sectional_top_two_and_lower_is_better_direction() -> None:
    prices, factors, common_start = _inputs()
    result = calculate_target_positions(prices, factors, common_start)
    higher_event = next(
        event
        for event in result.events
        if event.variant_key == "momentum_20"
        and event.frequency is RebalanceFrequency.WEEKLY
        and event.strategy_template is StrategyTemplate.CROSS_SECTIONAL
    )
    higher_weights = {item.symbol: item.target_weight for item in higher_event.positions}
    assert higher_weights == {
        "IWF": Decimal("0.5"),
        "IWD": Decimal("0.5"),
        "IWO": Decimal(0),
        "IWN": Decimal(0),
    }
    assert higher_event.reserve_target_weight == 0

    lower_event = next(
        event
        for event in result.events
        if event.variant_key == "historical_volatility_20"
        and event.frequency is RebalanceFrequency.WEEKLY
        and event.strategy_template is StrategyTemplate.CROSS_SECTIONAL
    )
    lower_ranks = {item.symbol: item.rank for item in lower_event.positions}
    assert lower_ranks == {"IWF": 4, "IWD": 3, "IWO": 2, "IWN": 1}


def test_strict_sma200_filter_keeps_unused_budget_in_reserve() -> None:
    prices, factors, common_start = _inputs()
    result = calculate_target_positions(prices, factors, common_start)
    event = next(
        item
        for item in result.events
        if item.variant_key == "momentum_20"
        and item.frequency is RebalanceFrequency.WEEKLY
        and item.strategy_template is StrategyTemplate.TREND_FILTERED
    )
    positions = {item.symbol: item for item in event.positions}
    assert event.eligible_count == 1
    assert positions["IWF"].selected
    assert positions["IWF"].target_weight == Decimal("0.5")
    assert positions["IWD"].rank is None
    assert not positions["IWD"].trend_eligible
    assert event.reserve_target_weight == Decimal("0.5")


def test_ties_use_fixed_ticker_order_and_are_flagged() -> None:
    prices, factors, common_start = _inputs(tied=True)
    result = calculate_target_positions(prices, factors, common_start)
    event = next(
        item
        for item in result.events
        if item.variant_key == "momentum_20"
        and item.frequency is RebalanceFrequency.WEEKLY
        and item.strategy_template is StrategyTemplate.CROSS_SECTIONAL
    )
    positions = {item.symbol: item for item in event.positions}
    assert positions["IWF"].rank == 1
    assert positions["IWD"].rank == 2
    assert positions["IWF"].tie_flag and positions["IWD"].tie_flag
    assert event.tie_flag


def test_signal_hash_and_execution_mapping_are_repeatable() -> None:
    prices, factors, common_start = _inputs()
    first = calculate_target_positions(prices, factors, common_start)
    second = calculate_target_positions(prices, factors, common_start)
    assert first.content_hash == second.content_hash
    assert all(event.signal_date < event.execution_date for event in first.events)
    assert first.position_count == len(first.events) * 4
