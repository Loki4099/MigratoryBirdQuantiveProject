from __future__ import annotations

import math
import statistics
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class FactorBar:
    asset_id: uuid.UUID
    asset_key: str
    session_date: date
    close_adj: Decimal
    close_raw: Decimal
    volume_raw: int


@dataclass(frozen=True, slots=True)
class FactorVariantInput:
    factor_variant_id: uuid.UUID
    artifact_id: uuid.UUID
    variant_key: str
    implementation_key: str
    parameters: dict[str, Any]
    required_price_observations: int


@dataclass(frozen=True, slots=True)
class FactorPoint:
    asset_id: uuid.UUID
    asset_key: str
    observation_date: date
    value: float


@dataclass(frozen=True, slots=True)
class VariantCalculation:
    variant: FactorVariantInput
    coverage_start: date
    coverage_end: date
    points: tuple[FactorPoint, ...]


class FactorCalculationError(RuntimeError):
    """Raised when a formal factor series is incomplete or non-finite."""


def calculate_variant(
    bars_by_asset: dict[uuid.UUID, tuple[FactorBar, ...]],
    variant: FactorVariantInput,
    *,
    coverage_start: date,
    coverage_end: date,
) -> VariantCalculation:
    if coverage_start > coverage_end:
        raise ValueError("Factor coverage start must not be after end")
    calculator = IMPLEMENTATIONS.get(variant.implementation_key)
    if calculator is None:
        raise FactorCalculationError(
            f"Unsupported factor implementation: {variant.implementation_key}"
        )
    points: list[FactorPoint] = []
    reference_dates: tuple[date, ...] | None = None
    for asset_id in sorted(bars_by_asset, key=str):
        bars = tuple(sorted(bars_by_asset[asset_id], key=lambda item: item.session_date))
        if not bars:
            raise FactorCalculationError(f"No bars for asset {asset_id}")
        if len({item.session_date for item in bars}) != len(bars):
            raise FactorCalculationError(f"Duplicate factor input date for asset {asset_id}")
        values = calculator(bars, variant.parameters)
        requested_dates = tuple(
            item.session_date
            for item in bars
            if coverage_start <= item.session_date <= coverage_end
        )
        if reference_dates is None:
            reference_dates = requested_dates
        elif requested_dates != reference_dates:
            raise FactorCalculationError("Factor assets are not aligned over requested coverage")
        if not requested_dates:
            raise FactorCalculationError("Factor coverage contains no observations")
        for index, bar in enumerate(bars):
            if not coverage_start <= bar.session_date <= coverage_end:
                continue
            value = values[index]
            if value is None or not math.isfinite(value):
                raise FactorCalculationError(
                    f"Undefined factor value: {variant.variant_key} "
                    f"{bar.asset_key} {bar.session_date}"
                )
            points.append(FactorPoint(bar.asset_id, bar.asset_key, bar.session_date, value))
    points.sort(key=lambda item: (item.asset_key, item.observation_date, str(item.asset_id)))
    return VariantCalculation(variant, coverage_start, coverage_end, tuple(points))


def _total_return(bars: tuple[FactorBar, ...], parameters: dict[str, Any]) -> list[float | None]:
    window = _positive_int(parameters, "window")
    closes = [item.close_adj for item in bars]
    return [
        None if index < window else float(closes[index] / closes[index - window] - 1)
        for index in range(len(bars))
    ]


def _lagged_return(bars: tuple[FactorBar, ...], parameters: dict[str, Any]) -> list[float | None]:
    long_window = _positive_int(parameters, "long_window")
    skip_window = _positive_int(parameters, "skip_window")
    if skip_window >= long_window:
        raise FactorCalculationError("skip_window must be smaller than long_window")
    closes = [item.close_adj for item in bars]
    return [
        None
        if index < long_window
        else float(closes[index - skip_window] / closes[index - long_window] - 1)
        for index in range(len(bars))
    ]


def _moving_average_ratio(
    bars: tuple[FactorBar, ...], parameters: dict[str, Any]
) -> list[float | None]:
    short_window = _positive_int(parameters, "short_window")
    long_window = _positive_int(parameters, "long_window")
    if short_window > long_window:
        raise FactorCalculationError("short_window must not exceed long_window")
    closes = [item.close_adj for item in bars]
    values: list[float | None] = [None] * len(bars)
    for index in range(long_window - 1, len(bars)):
        short_mean = _decimal_mean(closes[index - short_window + 1 : index + 1])
        long_mean = _decimal_mean(closes[index - long_window + 1 : index + 1])
        values[index] = float(short_mean / long_mean - 1)
    return values


def _rsi_wilder(bars: tuple[FactorBar, ...], parameters: dict[str, Any]) -> list[float | None]:
    window = _positive_int(parameters, "window")
    values: list[float | None] = [None] * len(bars)
    if len(bars) <= window:
        return values
    changes = [bars[index].close_adj - bars[index - 1].close_adj for index in range(1, len(bars))]
    gains = [max(change, Decimal(0)) for change in changes]
    losses = [max(-change, Decimal(0)) for change in changes]
    average_gain = _decimal_mean(gains[:window])
    average_loss = _decimal_mean(losses[:window])
    values[window] = _rsi_value(average_gain, average_loss)
    for index in range(window + 1, len(bars)):
        average_gain = (average_gain * (window - 1) + gains[index - 1]) / window
        average_loss = (average_loss * (window - 1) + losses[index - 1]) / window
        values[index] = _rsi_value(average_gain, average_loss)
    return values


def _rsi_value(average_gain: Decimal, average_loss: Decimal) -> float:
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return float(Decimal(100) - Decimal(100) / (1 + average_gain / average_loss))


def _log_returns(bars: tuple[FactorBar, ...]) -> list[float]:
    return [
        math.log(float(bars[index].close_adj / bars[index - 1].close_adj))
        for index in range(1, len(bars))
    ]


def _rolling_returns(
    bars: tuple[FactorBar, ...],
    window: int,
    statistic: Callable[[list[float]], float | None],
) -> list[float | None]:
    returns = _log_returns(bars)
    values: list[float | None] = [None] * len(bars)
    for index in range(window, len(bars)):
        values[index] = statistic(returns[index - window : index])
    return values


def _skewness(bars: tuple[FactorBar, ...], parameters: dict[str, Any]) -> list[float | None]:
    window = _positive_int(parameters, "window")

    def adjusted(values: list[float]) -> float | None:
        count = len(values)
        if count < 3:
            return None
        mean = statistics.fmean(values)
        sample_stdev = statistics.stdev(values)
        if sample_stdev == 0:
            return None
        standardized_cube_sum = sum(((item - mean) / sample_stdev) ** 3 for item in values)
        return count * standardized_cube_sum / ((count - 1) * (count - 2))

    return _rolling_returns(bars, window, adjusted)


def _excess_kurtosis(bars: tuple[FactorBar, ...], parameters: dict[str, Any]) -> list[float | None]:
    window = _positive_int(parameters, "window")

    def adjusted(values: list[float]) -> float | None:
        count = len(values)
        if count < 4:
            return None
        mean = statistics.fmean(values)
        deviations = [item - mean for item in values]
        second = statistics.fmean(item**2 for item in deviations)
        if second == 0:
            return None
        biased_excess = statistics.fmean(item**4 for item in deviations) / second**2 - 3
        return (count - 1) * ((count + 1) * biased_excess + 6) / ((count - 2) * (count - 3))

    return _rolling_returns(bars, window, adjusted)


def _realized_volatility(
    bars: tuple[FactorBar, ...], parameters: dict[str, Any]
) -> list[float | None]:
    window = _positive_int(parameters, "window")
    return _rolling_returns(bars, window, lambda values: statistics.stdev(values) * math.sqrt(252))


def _downside_deviation(
    bars: tuple[FactorBar, ...], parameters: dict[str, Any]
) -> list[float | None]:
    window = _positive_int(parameters, "window")
    return _rolling_returns(
        bars,
        window,
        lambda values: math.sqrt(252 * statistics.fmean(min(item, 0.0) ** 2 for item in values)),
    )


def _maximum_drawdown(
    bars: tuple[FactorBar, ...], parameters: dict[str, Any]
) -> list[float | None]:
    window = _positive_int(parameters, "window")
    closes = [item.close_adj for item in bars]
    values: list[float | None] = [None] * len(bars)
    for index in range(window - 1, len(bars)):
        peak = Decimal(0)
        minimum_drawdown = Decimal(0)
        for close in closes[index - window + 1 : index + 1]:
            peak = max(peak, close)
            minimum_drawdown = min(minimum_drawdown, close / peak - 1)
        values[index] = float(abs(minimum_drawdown))
    return values


def _relative_dollar_volume(
    bars: tuple[FactorBar, ...], parameters: dict[str, Any]
) -> list[float | None]:
    window = _positive_int(parameters, "window")
    dollar_volumes = [item.close_raw * item.volume_raw for item in bars]
    values: list[float | None] = [None] * len(bars)
    for index in range(window - 1, len(bars)):
        average = _decimal_mean(dollar_volumes[index - window + 1 : index + 1])
        values[index] = None if average == 0 else float(dollar_volumes[index] / average - 1)
    return values


def _amihud_illiquidity(
    bars: tuple[FactorBar, ...], parameters: dict[str, Any]
) -> list[float | None]:
    window = _positive_int(parameters, "window")
    observations: list[Decimal | None] = []
    for index in range(1, len(bars)):
        denominator = bars[index].close_raw * bars[index].volume_raw
        observations.append(
            None
            if denominator == 0
            else abs(bars[index].close_adj / bars[index - 1].close_adj - 1) / denominator
        )
    values: list[float | None] = [None] * len(bars)
    for index in range(window, len(bars)):
        sample = observations[index - window : index]
        if any(item is None for item in sample):
            continue
        values[index] = float(_decimal_mean([item for item in sample if item is not None]))
    return values


def _ppo_histogram(bars: tuple[FactorBar, ...], parameters: dict[str, Any]) -> list[float | None]:
    fast_window = _positive_int(parameters, "fast_window")
    slow_window = _positive_int(parameters, "slow_window")
    signal_window = _positive_int(parameters, "signal_window")
    if fast_window >= slow_window:
        raise FactorCalculationError("PPO fast_window must be smaller than slow_window")
    closes = [float(item.close_adj) for item in bars]
    fast = _ema_sma_seed(closes, fast_window)
    slow = _ema_sma_seed(closes, slow_window)
    ppo: list[float | None] = [None] * len(bars)
    for index in range(slow_window - 1, len(bars)):
        fast_value = fast[index]
        slow_value = slow[index]
        if fast_value is not None and slow_value is not None and slow_value != 0:
            ppo[index] = 100 * (fast_value - slow_value) / slow_value
    ppo_start = slow_window - 1
    available_ppo = [item for item in ppo[ppo_start:] if item is not None]
    signal_values = _ema_sma_seed(available_ppo, signal_window)
    values: list[float | None] = [None] * len(bars)
    for relative_index, signal in enumerate(signal_values):
        absolute_index = ppo_start + relative_index
        ppo_value = ppo[absolute_index]
        if signal is not None and ppo_value is not None:
            values[absolute_index] = ppo_value - signal
    return values


def _ema_sma_seed(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < window:
        return result
    ema = statistics.fmean(values[:window])
    result[window - 1] = ema
    alpha = 2 / (window + 1)
    for index in range(window, len(values)):
        ema = alpha * values[index] + (1 - alpha) * ema
        result[index] = ema
    return result


def _decimal_mean(values: list[Decimal]) -> Decimal:
    if not values:
        raise FactorCalculationError("Cannot average an empty factor window")
    return sum(values, Decimal(0)) / len(values)


def _positive_int(parameters: dict[str, Any], key: str) -> int:
    value = parameters.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FactorCalculationError(f"Factor parameter {key} must be a positive integer")
    return value


IMPLEMENTATIONS: dict[
    str, Callable[[tuple[FactorBar, ...], dict[str, Any]], list[float | None]]
] = {
    "total_return_v1": _total_return,
    "lagged_return_v1": _lagged_return,
    "moving_average_ratio_v1": _moving_average_ratio,
    "rsi_wilder_v1": _rsi_wilder,
    "return_skewness_fisher_pearson_v1": _skewness,
    "return_excess_kurtosis_fisher_v1": _excess_kurtosis,
    "realized_volatility_v1": _realized_volatility,
    "downside_deviation_v1": _downside_deviation,
    "maximum_drawdown_v1": _maximum_drawdown,
    "relative_dollar_volume_v1": _relative_dollar_volume,
    "amihud_illiquidity_v1": _amihud_illiquidity,
    "ppo_histogram_sma_seed_v1": _ppo_histogram,
}
