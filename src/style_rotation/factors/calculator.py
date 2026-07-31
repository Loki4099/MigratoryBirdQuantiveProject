from __future__ import annotations

import hashlib
import math
import statistics
from collections import defaultdict
from datetime import date
from decimal import Decimal

from style_rotation.data.types import CleanMarketPriceRecord
from style_rotation.factors.registry import DEFINITION_BY_KEY, VARIANTS
from style_rotation.factors.types import FactorComputationResult, FactorPoint, FactorVariantSpec

CANDIDATE_SYMBOLS = ("IWF", "IWD", "IWO", "IWN")


class FactorQualityError(RuntimeError):
    """Raised when formal factor values are incomplete or invalid."""


def _decimal(value: float | Decimal) -> Decimal:
    result = value if isinstance(value, Decimal) else Decimal(str(value))
    if not result.is_finite():
        raise ArithmeticError("Factor value must be finite")
    return result


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _log_returns(closes: list[Decimal], index: int, window: int) -> list[float] | None:
    if index < window:
        return None
    return [
        math.log(float(closes[position] / closes[position - 1]))
        for position in range(index - window + 1, index + 1)
    ]


def _annualized_volatility(closes: list[Decimal], index: int, window: int) -> float | None:
    returns = _log_returns(closes, index, window)
    if returns is None:
        return None
    return statistics.stdev(returns) * math.sqrt(252)


def _wilder_atr(bars: list[CleanMarketPriceRecord], window: int) -> list[Decimal | None]:
    values: list[Decimal | None] = [None] * len(bars)
    true_ranges: list[Decimal | None] = [None]
    for index in range(1, len(bars)):
        previous_close = bars[index - 1].close_adj
        true_ranges.append(
            max(
                bars[index].high_adj - bars[index].low_adj,
                abs(bars[index].high_adj - previous_close),
                abs(bars[index].low_adj - previous_close),
            )
        )
    if len(bars) <= window:
        return values
    seed = [value for value in true_ranges[1 : window + 1] if value is not None]
    atr = _mean(seed)
    values[window] = atr
    for index in range(window + 1, len(bars)):
        current = true_ranges[index]
        if current is None:
            raise FactorQualityError("Unexpected missing true range")
        atr = (atr * Decimal(window - 1) + current) / Decimal(window)
        values[index] = atr
    return values


def _maximum_drawdown(closes: list[Decimal], index: int, window: int) -> Decimal | None:
    if index + 1 < window:
        return None
    peak = Decimal(0)
    maximum = Decimal(0)
    for close in closes[index - window + 1 : index + 1]:
        peak = max(peak, close)
        maximum = max(maximum, Decimal(1) - close / peak)
    return maximum


def _value_for_variant(
    variant: FactorVariantSpec,
    closes: list[Decimal],
    volumes: list[Decimal],
    index: int,
    atr_cache: dict[int, list[Decimal | None]],
) -> Decimal | None:
    definition = DEFINITION_BY_KEY[variant.definition_key]
    parameters = variant.parameters
    implementation = definition.implementation_key

    if implementation in {"momentum", "short_term_reversal"}:
        window = parameters["window"]
        if index < window:
            return None
        value = closes[index] / closes[index - window] - Decimal(1)
        return -value if implementation == "short_term_reversal" else value

    if implementation == "skip_momentum":
        long_window = parameters["long_window"]
        skip_window = parameters["skip_window"]
        if index < long_window:
            return None
        return closes[index - skip_window] / closes[index - long_window] - Decimal(1)

    if implementation in {"moving_average_trend", "volume_trend"}:
        short_window = parameters["short_window"]
        long_window = parameters["long_window"]
        if index + 1 < long_window:
            return None
        source = volumes if implementation == "volume_trend" else closes
        short_mean = _mean(source[index - short_window + 1 : index + 1])
        long_mean = _mean(source[index - long_window + 1 : index + 1])
        if long_mean == 0:
            return None
        return short_mean / long_mean - Decimal(1)

    if implementation == "distance_to_high":
        window = parameters["window"]
        if index + 1 < window:
            return None
        rolling_high = max(closes[index - window + 1 : index + 1])
        return closes[index] / rolling_high - Decimal(1)

    if implementation == "historical_volatility":
        volatility = _annualized_volatility(closes, index, parameters["window"])
        return None if volatility is None else _decimal(volatility)

    if implementation == "downside_volatility":
        returns = _log_returns(closes, index, parameters["window"])
        if returns is None:
            return None
        downside_value = math.sqrt(252 * statistics.fmean(min(item, 0.0) ** 2 for item in returns))
        return _decimal(downside_value)

    if implementation == "maximum_drawdown":
        return _maximum_drawdown(closes, index, parameters["window"])

    if implementation == "risk_adjusted_momentum":
        window = parameters["window"]
        if index < window:
            return None
        volatility = _annualized_volatility(closes, index, window)
        if volatility is None or volatility == 0:
            return None
        momentum = closes[index] / closes[index - window] - Decimal(1)
        return momentum / _decimal(volatility)

    if implementation == "atr_ratio":
        atr = atr_cache[parameters["window"]][index]
        return None if atr is None else atr / closes[index]

    raise KeyError(f"Unknown factor implementation: {implementation}")


def _content_hash(points: list[FactorPoint], common_start: date, coverage_end: date) -> str:
    digest = hashlib.sha256()
    digest.update(f"{common_start.isoformat()}|{coverage_end.isoformat()}\n".encode())
    for point in points:
        digest.update(
            (
                f"{point.variant_key}|{point.symbol}|{point.trade_date.isoformat()}|"
                f"{point.raw_value}\n"
            ).encode()
        )
    return digest.hexdigest()


def calculate_factors(
    prices: tuple[CleanMarketPriceRecord, ...],
) -> FactorComputationResult:
    by_symbol: dict[str, list[CleanMarketPriceRecord]] = defaultdict(list)
    for price in prices:
        if price.symbol in CANDIDATE_SYMBOLS:
            by_symbol[price.symbol].append(price)
    if set(by_symbol) != set(CANDIDATE_SYMBOLS):
        raise FactorQualityError("All four candidate ETF series are required")
    for bars in by_symbol.values():
        bars.sort(key=lambda item: item.trade_date)
    reference_dates = [item.trade_date for item in by_symbol[CANDIDATE_SYMBOLS[0]]]
    if not reference_dates or any(
        [item.trade_date for item in by_symbol[symbol]] != reference_dates
        for symbol in CANDIDATE_SYMBOLS[1:]
    ):
        raise FactorQualityError("Candidate ETF dates must be non-empty and exactly aligned")

    points: list[FactorPoint] = []
    first_dates: dict[tuple[str, str], date] = {}
    point_dates: dict[tuple[str, str], set[date]] = defaultdict(set)
    for symbol in CANDIDATE_SYMBOLS:
        bars = by_symbol[symbol]
        closes = [item.close_adj for item in bars]
        volumes = [Decimal(item.volume_raw) for item in bars]
        atr_cache = {
            window: _wilder_atr(bars, window)
            for window in sorted(
                {
                    variant.parameters["window"]
                    for variant in VARIANTS
                    if variant.definition_key == "atr_ratio"
                }
            )
        }
        for variant in VARIANTS:
            for index, bar in enumerate(bars):
                value = _value_for_variant(variant, closes, volumes, index, atr_cache)
                if value is None:
                    continue
                point = FactorPoint(variant.key, symbol, bar.trade_date, _decimal(value))
                points.append(point)
                point_dates[(variant.key, symbol)].add(bar.trade_date)
                first_dates.setdefault((variant.key, symbol), bar.trade_date)

    expected_pairs = {(variant.key, symbol) for variant in VARIANTS for symbol in CANDIDATE_SYMBOLS}
    if set(first_dates) != expected_pairs:
        missing = sorted(expected_pairs.difference(first_dates))
        raise FactorQualityError(f"Factor series never became valid: {missing}")
    common_start = max(first_dates.values())
    expected_dates = {item for item in reference_dates if item >= common_start}
    incomplete = sorted(
        pair for pair in expected_pairs if not expected_dates.issubset(point_dates[pair])
    )
    if incomplete:
        raise FactorQualityError(f"Factor values are incomplete after common start: {incomplete}")
    points.sort(key=lambda item: (item.variant_key, item.symbol, item.trade_date))
    coverage_end = reference_dates[-1]
    return FactorComputationResult(
        tuple(points),
        common_start,
        coverage_end,
        _content_hash(points, common_start, coverage_end),
    )
