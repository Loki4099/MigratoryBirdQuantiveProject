from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from style_rotation.data.types import CleanMarketPriceRecord
from style_rotation.domain.enums import RebalanceFrequency, StrategyTemplate
from style_rotation.factors.calculator import CANDIDATE_SYMBOLS
from style_rotation.signals.types import (
    FactorSignalPoint,
    RebalancePair,
    RebalanceTarget,
    SignalComputationResult,
    TargetPosition,
)

TOP_N = 2
ASSET_TARGET_WEIGHT = Decimal("0.5")
SMA_WINDOW = 200
TICKER_ORDER = {symbol: index for index, symbol in enumerate(CANDIDATE_SYMBOLS)}


class SignalQualityError(RuntimeError):
    """Raised when a formal signal dataset cannot be generated completely."""


def identify_rebalance_pairs(
    trading_dates: tuple[date, ...],
    frequency: RebalanceFrequency,
    not_before: date,
) -> tuple[RebalancePair, ...]:
    if tuple(sorted(set(trading_dates))) != trading_dates:
        raise SignalQualityError("Trading dates must be unique and strictly increasing")
    grouped: dict[tuple[int, int], list[date]] = defaultdict(list)
    for trading_date in trading_dates:
        if frequency is RebalanceFrequency.WEEKLY:
            iso_year, iso_week, _ = trading_date.isocalendar()
            key = (iso_year, iso_week)
        else:
            key = (trading_date.year, trading_date.month)
        grouped[key].append(trading_date)
    index_by_date = {trading_date: index for index, trading_date in enumerate(trading_dates)}
    pairs: list[RebalancePair] = []
    for dates in grouped.values():
        signal_date = dates[-1]
        signal_index = index_by_date[signal_date]
        if signal_date < not_before or signal_index + 1 >= len(trading_dates):
            continue
        pairs.append(RebalancePair(signal_date, trading_dates[signal_index + 1]))
    return tuple(pairs)


def _trend_eligibility(
    prices: dict[str, list[CleanMarketPriceRecord]],
) -> dict[tuple[str, date], bool]:
    result: dict[tuple[str, date], bool] = {}
    for symbol in CANDIDATE_SYMBOLS:
        bars = prices[symbol]
        running_sum = Decimal(0)
        for index, bar in enumerate(bars):
            running_sum += bar.close_adj
            if index >= SMA_WINDOW:
                running_sum -= bars[index - SMA_WINDOW].close_adj
            if index + 1 >= SMA_WINDOW:
                sma = running_sum / Decimal(SMA_WINDOW)
                result[(symbol, bar.trade_date)] = bar.close_adj > sma
    return result


def _rank_positions(
    values: dict[str, FactorSignalPoint],
    trend_eligible: dict[str, bool],
    template: StrategyTemplate,
) -> tuple[tuple[TargetPosition, ...], Decimal, bool, int]:
    oriented = {
        symbol: (
            point.raw_value if point.direction.value == "higher_is_better" else -point.raw_value
        )
        for symbol, point in values.items()
    }
    ranked_symbols = [
        symbol
        for symbol in CANDIDATE_SYMBOLS
        if template is StrategyTemplate.CROSS_SECTIONAL or trend_eligible[symbol]
    ]
    ranked_symbols.sort(key=lambda symbol: (-oriented[symbol], TICKER_ORDER[symbol]))
    ranks = {symbol: rank for rank, symbol in enumerate(ranked_symbols, start=1)}
    counts = Counter(oriented[symbol] for symbol in ranked_symbols)
    tied = {symbol for symbol in ranked_symbols if counts[oriented[symbol]] > 1}
    selected = set(ranked_symbols[:TOP_N])
    positions = tuple(
        TargetPosition(
            symbol=symbol,
            raw_factor_value=values[symbol].raw_value,
            oriented_factor_value=oriented[symbol],
            rank=ranks.get(symbol),
            trend_eligible=trend_eligible[symbol],
            tie_flag=symbol in tied,
            selected=symbol in selected,
            target_weight=ASSET_TARGET_WEIGHT if symbol in selected else Decimal(0),
        )
        for symbol in CANDIDATE_SYMBOLS
    )
    invested = sum((position.target_weight for position in positions), Decimal(0))
    reserve = Decimal(1) - invested
    if template is StrategyTemplate.CROSS_SECTIONAL and reserve != 0:
        raise SignalQualityError("Cross-sectional template must remain fully invested")
    return positions, reserve, bool(tied), len(ranked_symbols)


def _content_hash(events: list[RebalanceTarget]) -> str:
    digest = hashlib.sha256()
    for event in events:
        digest.update(
            (
                f"{event.variant_key}|{event.frequency.value}|{event.strategy_template.value}|"
                f"{event.signal_date}|{event.execution_date}|{event.reserve_target_weight}\n"
            ).encode()
        )
        for position in event.positions:
            digest.update(
                (
                    f"{position.symbol}|{position.raw_factor_value}|"
                    f"{position.oriented_factor_value}|{position.rank}|"
                    f"{position.trend_eligible}|{position.tie_flag}|"
                    f"{position.selected}|{position.target_weight}\n"
                ).encode()
            )
    return digest.hexdigest()


def calculate_target_positions(
    prices: tuple[CleanMarketPriceRecord, ...],
    factor_points: tuple[FactorSignalPoint, ...],
    factor_common_start: date,
) -> SignalComputationResult:
    prices_by_symbol: dict[str, list[CleanMarketPriceRecord]] = defaultdict(list)
    for price in prices:
        if price.symbol in CANDIDATE_SYMBOLS:
            prices_by_symbol[price.symbol].append(price)
    if set(prices_by_symbol) != set(CANDIDATE_SYMBOLS):
        raise SignalQualityError("All four candidate ETF price series are required")
    for bars in prices_by_symbol.values():
        bars.sort(key=lambda item: item.trade_date)
    trading_dates = tuple(item.trade_date for item in prices_by_symbol[CANDIDATE_SYMBOLS[0]])
    if any(
        tuple(item.trade_date for item in prices_by_symbol[symbol]) != trading_dates
        for symbol in CANDIDATE_SYMBOLS[1:]
    ):
        raise SignalQualityError("Candidate ETF calendars are not aligned")
    if len(trading_dates) < SMA_WINDOW + 1:
        raise SignalQualityError("At least 201 aligned trading dates are required")

    points_by_variant_date: dict[tuple[str, date], dict[str, FactorSignalPoint]] = defaultdict(dict)
    variant_keys: set[str] = set()
    for point in factor_points:
        variant_keys.add(point.variant_key)
        values = points_by_variant_date[(point.variant_key, point.trade_date)]
        if point.symbol in values:
            raise SignalQualityError("Duplicate factor value for variant, asset, and date")
        values[point.symbol] = point
    if not variant_keys:
        raise SignalQualityError("No factor variants are available")

    trend = _trend_eligibility(prices_by_symbol)
    events: list[RebalanceTarget] = []
    for frequency in (RebalanceFrequency.WEEKLY, RebalanceFrequency.MONTHLY):
        pairs = identify_rebalance_pairs(trading_dates, frequency, factor_common_start)
        if not pairs:
            raise SignalQualityError(f"No executable {frequency.value} rebalance dates")
        for variant_key in sorted(variant_keys):
            for pair in pairs:
                values = points_by_variant_date[(variant_key, pair.signal_date)]
                if set(values) != set(CANDIDATE_SYMBOLS):
                    raise SignalQualityError(
                        f"Incomplete factor cross-section: {variant_key} {pair.signal_date}"
                    )
                trend_flags = {
                    symbol: trend.get((symbol, pair.signal_date), False)
                    for symbol in CANDIDATE_SYMBOLS
                }
                for template in (
                    StrategyTemplate.CROSS_SECTIONAL,
                    StrategyTemplate.TREND_FILTERED,
                ):
                    positions, reserve, tie_flag, eligible_count = _rank_positions(
                        values, trend_flags, template
                    )
                    events.append(
                        RebalanceTarget(
                            variant_key,
                            frequency,
                            template,
                            pair.signal_date,
                            pair.execution_date,
                            eligible_count,
                            tie_flag,
                            reserve,
                            positions,
                        )
                    )
    events.sort(
        key=lambda item: (
            item.frequency.value,
            item.strategy_template.value,
            item.variant_key,
            item.signal_date,
        )
    )
    first_signal_date = min(event.signal_date for event in events)
    first_execution_date = min(event.execution_date for event in events)
    return SignalComputationResult(
        tuple(events),
        first_signal_date,
        first_execution_date,
        trading_dates[-1],
        _content_hash(events),
    )
