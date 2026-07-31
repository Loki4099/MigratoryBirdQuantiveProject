from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from decimal import Decimal

import pytest

from style_rotation.data.types import CleanMarketPriceRecord
from style_rotation.factors.calculator import CANDIDATE_SYMBOLS, calculate_factors


def _prices(count: int = 300) -> tuple[CleanMarketPriceRecord, ...]:
    start = date(2025, 1, 1)
    records: list[CleanMarketPriceRecord] = []
    for symbol_index, symbol in enumerate(CANDIDATE_SYMBOLS):
        for index in range(count):
            close = (
                Decimal(100 + symbol_index * 10) + Decimal(index) + Decimal(index * index) / 1000
            )
            records.append(
                CleanMarketPriceRecord(
                    symbol=symbol,
                    trade_date=start + timedelta(days=index),
                    open_adj=close - Decimal("0.5"),
                    high_adj=close + Decimal("2"),
                    low_adj=close - Decimal("1"),
                    close_adj=close,
                    adj_factor=Decimal(1),
                    volume_raw=1_000 + symbol_index * 100 + index * index,
                    dividends=Decimal(0),
                    stock_splits=Decimal(0),
                )
            )
    return tuple(records)


def _point_map() -> tuple[dict[tuple[str, str, date], Decimal], tuple[CleanMarketPriceRecord, ...]]:
    prices = _prices()
    result = calculate_factors(prices)
    return {
        (point.variant_key, point.symbol, point.trade_date): point.raw_value
        for point in result.points
    }, prices


def test_all_variants_are_complete_after_longest_warmup() -> None:
    prices = _prices()
    result = calculate_factors(prices)
    expected_start = date(2025, 1, 1) + timedelta(days=252)
    assert result.common_valid_start == expected_start
    assert result.coverage_end == date(2025, 1, 1) + timedelta(days=299)
    for variant_key in {point.variant_key for point in result.points}:
        for symbol in CANDIDATE_SYMBOLS:
            dates = {
                point.trade_date
                for point in result.points
                if point.variant_key == variant_key and point.symbol == symbol
            }
            assert set(
                date(2025, 1, 1) + timedelta(days=index) for index in range(252, 300)
            ).issubset(dates)


def test_momentum_reversal_moving_average_and_high_distance_golden_values() -> None:
    points, prices = _point_map()
    iwf = [item for item in prices if item.symbol == "IWF"]
    index = 299
    day = iwf[index].trade_date
    closes = [item.close_adj for item in iwf]
    momentum = closes[index] / closes[index - 20] - 1
    assert points[("momentum_20", "IWF", day)] == momentum
    assert points[("short_term_reversal_5", "IWF", day)] == -(closes[index] / closes[index - 5] - 1)
    sma20 = sum(closes[index - 19 : index + 1]) / Decimal(20)
    sma60 = sum(closes[index - 59 : index + 1]) / Decimal(60)
    assert points[("moving_average_trend_20_60", "IWF", day)] == sma20 / sma60 - 1
    assert points[("distance_to_high_60", "IWF", day)] == Decimal(0)


def test_volatility_downside_and_atr_golden_values() -> None:
    points, prices = _point_map()
    iwf = [item for item in prices if item.symbol == "IWF"]
    index = 299
    day = iwf[index].trade_date
    closes = [item.close_adj for item in iwf]
    log_returns = [
        math.log(float(closes[position] / closes[position - 1]))
        for position in range(index - 19, index + 1)
    ]
    expected_volatility = statistics.stdev(log_returns) * math.sqrt(252)
    assert float(points[("historical_volatility_20", "IWF", day)]) == pytest.approx(
        expected_volatility
    )
    assert points[("downside_volatility_20", "IWF", day)] == Decimal(0)

    atr_day = iwf[14].trade_date
    true_ranges = []
    for position in range(1, 15):
        bar = iwf[position]
        previous_close = iwf[position - 1].close_adj
        true_ranges.append(
            max(
                bar.high_adj - bar.low_adj,
                abs(bar.high_adj - previous_close),
                abs(bar.low_adj - previous_close),
            )
        )
    expected_atr = sum(true_ranges) / Decimal(14) / iwf[14].close_adj
    assert points[("atr_ratio_14", "IWF", atr_day)] == expected_atr


def test_factor_content_hash_is_repeatable() -> None:
    first = calculate_factors(_prices())
    second = calculate_factors(_prices())
    assert first.content_hash == second.content_hash
