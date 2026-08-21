from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from style_rotation.v022.aggregation_runtime import flat_equal_weight_mean

PanelKey = tuple[str, date]
Panel = dict[PanelKey, Decimal | None]
QUANTUM = Decimal("1e-18")
TOTAL_RETURN_IMPLEMENTATION = "style_rotation.v022.processing.total_return_v1"
MOVING_AVERAGE_RATIO_IMPLEMENTATION = (
    "style_rotation.v022.processing.moving_average_ratio_v1"
)
AMIHUD_DAILY_PRIMITIVES_IMPLEMENTATION = (
    "style_rotation.v022.processing.amihud_daily_primitives_v1"
)
PRICE_CROSS_ABOVE_MA_IMPLEMENTATION = (
    "style_rotation.v022.processing.price_cross_above_ma_v1"
)
AMIHUD_ILLIQUIDITY_IMPLEMENTATION = "style_rotation.v022.processing.amihud_illiquidity_v1"
CONTINUOUS_CROSS_SECTIONAL_SIGNAL_IMPLEMENTATION = (
    "style_rotation.v022.processing.continuous_cross_sectional_signal_v1"
)
LAGGED_RETURN_IMPLEMENTATION = (
    "style_rotation.v022.processing.compat.lagged_return_v1"
)
MAXIMUM_DRAWDOWN_IMPLEMENTATION = (
    "style_rotation.v022.processing.compat.maximum_drawdown_v1"
)
REALIZED_VOLATILITY_IMPLEMENTATION = (
    "style_rotation.v022.processing.compat.realized_volatility_v1"
)
DOWNSIDE_DEVIATION_IMPLEMENTATION = (
    "style_rotation.v022.processing.compat.downside_deviation_v1"
)
RELATIVE_DOLLAR_VOLUME_IMPLEMENTATION = (
    "style_rotation.v022.processing.compat.relative_dollar_volume_v1"
)
RETURN_SKEWNESS_IMPLEMENTATION = (
    "style_rotation.v022.processing.compat.return_skewness_fisher_pearson_v1"
)
RETURN_EXCESS_KURTOSIS_IMPLEMENTATION = (
    "style_rotation.v022.processing.compat.return_excess_kurtosis_fisher_v1"
)
RSI_WILDER_IMPLEMENTATION = "style_rotation.v022.processing.compat.rsi_wilder_v1"
PPO_HISTOGRAM_IMPLEMENTATION = (
    "style_rotation.v022.processing.compat.ppo_histogram_sma_seed_v1"
)
COMPAT_CONTINUOUS_IMPLEMENTATION = (
    "style_rotation.v022.processing.compat.continuous_v1"
)
THRESHOLD_STATE_IMPLEMENTATION = (
    "style_rotation.v022.processing.compat.threshold_state_v1"
)
CROSSOVER_EVENT_IMPLEMENTATION = (
    "style_rotation.v022.processing.compat.crossover_event_v1"
)


@dataclass(frozen=True, slots=True)
class RepresentativeExecution:
    features: Mapping[str, Panel]
    aggregated_signal: Panel


@dataclass(frozen=True, slots=True)
class CatalogNodeExecution:
    implementation_key: str
    output_ports: Mapping[str, Panel]


def execute_catalog_node(
    implementation_key: str,
    *,
    parameters: Mapping[str, object],
    input_ports: Mapping[str, Mapping[PanelKey, Decimal | None]],
) -> CatalogNodeExecution:
    """Execute one supported Catalog Node through its exact typed port boundary."""

    if implementation_key == TOTAL_RETURN_IMPLEMENTATION:
        if set(input_ports) != {"close_adj"}:
            raise ValueError(f"{implementation_key} requires only the close_adj input port")
        close = input_ports["close_adj"]
        if set(parameters) != {"window"}:
            raise ValueError("Total Return parameters must contain only window")
        window = _positive_int(parameters["window"], "window")
        return CatalogNodeExecution(
            implementation_key,
            {"total_return": total_return(close, window=window)},
        )
    if implementation_key == MOVING_AVERAGE_RATIO_IMPLEMENTATION:
        if set(input_ports) != {"close_adj"}:
            raise ValueError(f"{implementation_key} requires only the close_adj input port")
        close = input_ports["close_adj"]
        if set(parameters) != {"short_window", "long_window"}:
            raise ValueError(
                "Moving-average parameters must contain short_window and long_window"
            )
        short_window = _positive_int(parameters["short_window"], "short_window")
        long_window = _positive_int(parameters["long_window"], "long_window")
        if short_window > long_window:
            raise ValueError("short_window cannot exceed long_window")
        return CatalogNodeExecution(
            implementation_key,
            {
                "ma_ratio": moving_average_ratio(
                    close,
                    short_window=short_window,
                    long_window=long_window,
                )
            },
        )
    if implementation_key == AMIHUD_DAILY_PRIMITIVES_IMPLEMENTATION:
        if parameters:
            raise ValueError("Amihud daily primitives do not accept parameters")
        if set(input_ports) != {"close_adj", "close_raw", "volume_raw"}:
            raise ValueError(
                "Amihud daily primitives require close_adj, close_raw, and volume_raw"
            )
        simple_return, dollar_volume, daily_price_impact = amihud_daily_primitives(
            input_ports["close_adj"],
            input_ports["close_raw"],
            input_ports["volume_raw"],
        )
        return CatalogNodeExecution(
            implementation_key,
            {
                "simple_return": simple_return,
                "dollar_volume": dollar_volume,
                "daily_price_impact": daily_price_impact,
            },
        )
    if implementation_key == PRICE_CROSS_ABOVE_MA_IMPLEMENTATION:
        if set(input_ports) != {"ma_ratio"}:
            raise ValueError("Price crossover requires only the ma_ratio input port")
        if set(parameters) != {"short_window", "long_window"}:
            raise ValueError(
                "Price crossover parameters must contain short_window and long_window"
            )
        short_window = _positive_int(parameters["short_window"], "short_window")
        long_window = _positive_int(parameters["long_window"], "long_window")
        if short_window > long_window:
            raise ValueError("short_window cannot exceed long_window")
        return CatalogNodeExecution(
            implementation_key,
            {"event_score": price_cross_above_ma(input_ports["ma_ratio"])},
        )
    if implementation_key == AMIHUD_ILLIQUIDITY_IMPLEMENTATION:
        if set(input_ports) != {"daily_price_impact"}:
            raise ValueError(
                "Amihud illiquidity requires only the daily_price_impact input port"
            )
        if set(parameters) != {"window"}:
            raise ValueError("Amihud illiquidity parameters must contain only window")
        window = _positive_int(parameters["window"], "window")
        return CatalogNodeExecution(
            implementation_key,
            {
                "rolling_mean_impact": rolling_mean(
                    input_ports["daily_price_impact"], window=window
                )
            },
        )
    if implementation_key == CONTINUOUS_CROSS_SECTIONAL_SIGNAL_IMPLEMENTATION:
        if set(input_ports) != {"feature"}:
            raise ValueError("Continuous cross-sectional signal requires only feature")
        if set(parameters) != {"window", "direction"}:
            raise ValueError(
                "Continuous signal parameters must contain window and direction"
            )
        _positive_int(parameters["window"], "window")
        direction = parameters["direction"]
        if type(direction) is not int or direction not in {-1, 1}:
            raise ValueError("direction must be -1 or 1")
        return CatalogNodeExecution(
            implementation_key,
            {
                "signal_score": cross_sectional_centered_rank(
                    input_ports["feature"], direction=direction
                )
            },
        )
    if implementation_key == LAGGED_RETURN_IMPLEMENTATION:
        if set(input_ports) != {"close_adj"}:
            raise ValueError("Lagged return requires only the close_adj input port")
        if set(parameters) != {"long_window", "skip_window"}:
            raise ValueError(
                "Lagged return parameters must contain long_window and skip_window"
            )
        long_window = _positive_int(parameters["long_window"], "long_window")
        skip_window = _positive_int(parameters["skip_window"], "skip_window")
        if skip_window >= long_window:
            raise ValueError("skip_window must be smaller than long_window")
        return CatalogNodeExecution(
            implementation_key,
            {
                "factor_value": lagged_return(
                    input_ports["close_adj"],
                    long_window=long_window,
                    skip_window=skip_window,
                )
            },
        )
    if implementation_key == MAXIMUM_DRAWDOWN_IMPLEMENTATION:
        if set(input_ports) != {"close_adj"}:
            raise ValueError("Maximum drawdown requires only the close_adj input port")
        if set(parameters) != {"window"}:
            raise ValueError("Maximum drawdown parameters must contain only window")
        window = _positive_int(parameters["window"], "window")
        return CatalogNodeExecution(
            implementation_key,
            {
                "factor_value": maximum_drawdown(
                    input_ports["close_adj"], window=window
                )
            },
        )
    if implementation_key == REALIZED_VOLATILITY_IMPLEMENTATION:
        if set(input_ports) != {"close_adj"}:
            raise ValueError("Realized volatility requires only the close_adj input port")
        if set(parameters) != {"window"}:
            raise ValueError("Realized volatility parameters must contain only window")
        window = _positive_int(parameters["window"], "window")
        if window < 2:
            raise ValueError("Realized volatility window must contain at least two returns")
        return CatalogNodeExecution(
            implementation_key,
            {
                "factor_value": realized_volatility(
                    input_ports["close_adj"], window=window
                )
            },
        )
    if implementation_key == DOWNSIDE_DEVIATION_IMPLEMENTATION:
        if set(input_ports) != {"close_adj"}:
            raise ValueError("Downside deviation requires only the close_adj input port")
        if set(parameters) != {"window"}:
            raise ValueError("Downside deviation parameters must contain only window")
        window = _positive_int(parameters["window"], "window")
        return CatalogNodeExecution(
            implementation_key,
            {
                "factor_value": downside_deviation(
                    input_ports["close_adj"], window=window
                )
            },
        )
    if implementation_key == RELATIVE_DOLLAR_VOLUME_IMPLEMENTATION:
        if set(input_ports) != {"close_raw", "volume_raw"}:
            raise ValueError(
                "Relative dollar volume requires close_raw and volume_raw input ports"
            )
        if set(parameters) != {"window"}:
            raise ValueError("Relative dollar volume parameters must contain only window")
        window = _positive_int(parameters["window"], "window")
        return CatalogNodeExecution(
            implementation_key,
            {
                "factor_value": relative_dollar_volume(
                    input_ports["close_raw"],
                    input_ports["volume_raw"],
                    window=window,
                )
            },
        )
    if implementation_key == RETURN_SKEWNESS_IMPLEMENTATION:
        if set(input_ports) != {"close_adj"}:
            raise ValueError("Return skewness requires only the close_adj input port")
        if set(parameters) != {"window"}:
            raise ValueError("Return skewness parameters must contain only window")
        window = _positive_int(parameters["window"], "window")
        return CatalogNodeExecution(
            implementation_key,
            {
                "factor_value": return_skewness(
                    input_ports["close_adj"], window=window
                )
            },
        )
    if implementation_key == RETURN_EXCESS_KURTOSIS_IMPLEMENTATION:
        if set(input_ports) != {"close_adj"}:
            raise ValueError(
                "Return excess kurtosis requires only the close_adj input port"
            )
        if set(parameters) != {"window"}:
            raise ValueError(
                "Return excess kurtosis parameters must contain only window"
            )
        window = _positive_int(parameters["window"], "window")
        return CatalogNodeExecution(
            implementation_key,
            {
                "factor_value": return_excess_kurtosis(
                    input_ports["close_adj"], window=window
                )
            },
        )
    if implementation_key == RSI_WILDER_IMPLEMENTATION:
        if set(input_ports) != {"close_adj"}:
            raise ValueError("RSI requires only the close_adj input port")
        if set(parameters) != {"window"}:
            raise ValueError("RSI parameters must contain only window")
        window = _positive_int(parameters["window"], "window")
        return CatalogNodeExecution(
            implementation_key,
            {"factor_value": rsi_wilder(input_ports["close_adj"], window=window)},
        )
    if implementation_key == PPO_HISTOGRAM_IMPLEMENTATION:
        if set(input_ports) != {"close_adj"}:
            raise ValueError("PPO histogram requires only the close_adj input port")
        if set(parameters) != {"fast_window", "slow_window", "signal_window"}:
            raise ValueError(
                "PPO parameters must contain fast_window, slow_window, and signal_window"
            )
        fast_window = _positive_int(parameters["fast_window"], "fast_window")
        slow_window = _positive_int(parameters["slow_window"], "slow_window")
        signal_window = _positive_int(parameters["signal_window"], "signal_window")
        if fast_window >= slow_window:
            raise ValueError("PPO fast_window must be smaller than slow_window")
        return CatalogNodeExecution(
            implementation_key,
            {
                "factor_value": ppo_histogram(
                    input_ports["close_adj"],
                    fast_window=fast_window,
                    slow_window=slow_window,
                    signal_window=signal_window,
                )
            },
        )
    if implementation_key == COMPAT_CONTINUOUS_IMPLEMENTATION:
        if set(input_ports) != {"feature"}:
            raise ValueError("Continuous signal requires only the feature input port")
        direction = _signal_parameters(parameters, require_rule=False)
        return CatalogNodeExecution(
            implementation_key,
            {
                "signal_score": cross_sectional_centered_rank(
                    input_ports["feature"], direction=direction
                )
            },
        )
    if implementation_key == THRESHOLD_STATE_IMPLEMENTATION:
        if set(input_ports) != {"feature"}:
            raise ValueError("Threshold state requires only the feature input port")
        _signal_parameters(parameters, require_rule=True)
        return CatalogNodeExecution(
            implementation_key,
            {
                "state_score": threshold_state(
                    input_ports["feature"], rule=parameters["rule"]
                )
            },
        )
    if implementation_key == CROSSOVER_EVENT_IMPLEMENTATION:
        if set(input_ports) != {"feature"}:
            raise ValueError("Crossover event requires only the feature input port")
        _signal_parameters(parameters, require_rule=True)
        return CatalogNodeExecution(
            implementation_key,
            {
                "event_score": crossover_event(
                    input_ports["feature"], rule=parameters["rule"]
                )
            },
        )
    raise ValueError(f"Unsupported v0.22 Processing implementation: {implementation_key}")


def total_return(close: Mapping[PanelKey, Decimal | None], *, window: int) -> Panel:
    result: Panel = {}
    for rows in _by_asset(close).values():
        for index, (key, current) in enumerate(rows):
            previous = rows[index - window][1] if index >= window else None
            result[key] = _ratio_minus_one(current, previous)
    return result


def moving_average_ratio(
    close: Mapping[PanelKey, Decimal | None], *, short_window: int, long_window: int
) -> Panel:
    result: Panel = {}
    for rows in _by_asset(close).values():
        values = [value for _, value in rows]
        for index, (key, _) in enumerate(rows):
            short = _complete_mean(values, index, short_window)
            long = _complete_mean(values, index, long_window)
            result[key] = _ratio_minus_one(short, long)
    return result


def amihud_daily_primitives(
    adjusted_close: Mapping[PanelKey, Decimal | None],
    close_raw: Mapping[PanelKey, Decimal | None],
    volume_raw: Mapping[PanelKey, Decimal | None],
) -> tuple[Panel, Panel, Panel]:
    simple_return = total_return(adjusted_close, window=1)
    dollar_volume: Panel = {}
    daily_price_impact: Panel = {}
    for key in sorted(set(adjusted_close) | set(close_raw) | set(volume_raw)):
        close = close_raw.get(key)
        volume = volume_raw.get(key)
        traded = close * volume if close is not None and volume is not None else None
        dollar_volume[key] = traded
        daily_return = simple_return.get(key)
        daily_price_impact[key] = (
            abs(daily_return) / traded
            if daily_return is not None and traded is not None and traded != 0
            else None
        )
    return simple_return, dollar_volume, daily_price_impact


def price_cross_above_ma(ratio: Mapping[PanelKey, Decimal | None]) -> Panel:
    result: Panel = {}
    for rows in _by_asset(ratio).values():
        for index, (key, current) in enumerate(rows):
            previous = rows[index - 1][1] if index else None
            result[key] = (
                Decimal(1)
                if previous is not None and current is not None and previous <= 0 < current
                else Decimal(0)
                if previous is not None and current is not None
                else None
            )
    return result


def rolling_mean(values: Mapping[PanelKey, Decimal | None], *, window: int) -> Panel:
    result: Panel = {}
    for rows in _by_asset(values).values():
        ordered = [value for _, value in rows]
        for index, (key, _) in enumerate(rows):
            result[key] = _complete_mean(ordered, index, window)
    return result


def lagged_return(
    close: Mapping[PanelKey, Decimal | None],
    *,
    long_window: int,
    skip_window: int,
) -> Panel:
    if long_window <= 0 or skip_window <= 0:
        raise ValueError("long_window and skip_window must be positive")
    if skip_window >= long_window:
        raise ValueError("skip_window must be smaller than long_window")
    result: Panel = {}
    for rows in _by_asset(close).values():
        for index, (key, _) in enumerate(rows):
            numerator = rows[index - skip_window][1] if index >= long_window else None
            denominator = rows[index - long_window][1] if index >= long_window else None
            result[key] = _ratio_minus_one(numerator, denominator)
    return result


def maximum_drawdown(
    close: Mapping[PanelKey, Decimal | None], *, window: int
) -> Panel:
    if window <= 0:
        raise ValueError("window must be positive")
    result: Panel = {}
    for rows in _by_asset(close).values():
        ordered = [value for _, value in rows]
        for index, (key, _) in enumerate(rows):
            if index + 1 < window:
                result[key] = None
                continue
            sample = ordered[index - window + 1 : index + 1]
            if any(value is None for value in sample):
                result[key] = None
                continue
            peak: Decimal | None = None
            minimum = Decimal(0)
            for value in sample:
                assert value is not None
                peak = value if peak is None else max(peak, value)
                if peak == 0:
                    result[key] = None
                    break
                minimum = min(minimum, value / peak - 1)
            else:
                result[key] = abs(minimum)
    return result


def realized_volatility(
    close: Mapping[PanelKey, Decimal | None], *, window: int
) -> Panel:
    if window < 2:
        raise ValueError("window must contain at least two returns")
    return _rolling_log_return_statistic(
        close,
        window=window,
        statistic=lambda values: statistics.stdev(values) * math.sqrt(252),
    )


def downside_deviation(
    close: Mapping[PanelKey, Decimal | None], *, window: int
) -> Panel:
    if window <= 0:
        raise ValueError("window must be positive")
    return _rolling_log_return_statistic(
        close,
        window=window,
        statistic=lambda values: math.sqrt(
            252 * statistics.fmean(min(value, 0.0) ** 2 for value in values)
        ),
    )


def _rolling_log_return_statistic(
    close: Mapping[PanelKey, Decimal | None],
    *,
    window: int,
    statistic: Callable[[list[float]], float | None],
) -> Panel:
    result: Panel = {}
    for rows in _by_asset(close).values():
        returns: list[float | None] = []
        for index in range(1, len(rows)):
            current = rows[index][1]
            previous = rows[index - 1][1]
            returns.append(
                math.log(float(current / previous))
                if current is not None
                and previous is not None
                and current > 0
                and previous > 0
                else None
            )
        for index, (key, _) in enumerate(rows):
            if index < window:
                result[key] = None
                continue
            sample = returns[index - window : index]
            if any(value is None for value in sample):
                result[key] = None
                continue
            value = statistic([item for item in sample if item is not None])
            result[key] = (
                Decimal(str(value))
                if value is not None and math.isfinite(value)
                else None
            )
    return result


def relative_dollar_volume(
    close_raw: Mapping[PanelKey, Decimal | None],
    volume_raw: Mapping[PanelKey, Decimal | None],
    *,
    window: int,
) -> Panel:
    if window <= 0:
        raise ValueError("window must be positive")
    traded: Panel = {}
    for key in set(close_raw) | set(volume_raw):
        close = close_raw.get(key)
        volume = volume_raw.get(key)
        traded[key] = close * volume if close is not None and volume is not None else None
    result: Panel = {}
    for rows in _by_asset(traded).values():
        ordered = [value for _, value in rows]
        for index, (key, current) in enumerate(rows):
            average = _complete_mean(ordered, index, window)
            result[key] = _ratio_minus_one(current, average)
    return result


def return_skewness(
    close: Mapping[PanelKey, Decimal | None], *, window: int
) -> Panel:
    if window < 3:
        raise ValueError("window must contain at least three returns")

    def adjusted(values: list[float]) -> float | None:
        count = len(values)
        mean = statistics.fmean(values)
        sample_stdev = statistics.stdev(values)
        if sample_stdev == 0:
            return None
        standardized_cube_sum = sum(
            ((value - mean) / sample_stdev) ** 3 for value in values
        )
        return count * standardized_cube_sum / ((count - 1) * (count - 2))

    return _rolling_log_return_statistic(close, window=window, statistic=adjusted)


def return_excess_kurtosis(
    close: Mapping[PanelKey, Decimal | None], *, window: int
) -> Panel:
    if window < 4:
        raise ValueError("window must contain at least four returns")

    def adjusted(values: list[float]) -> float | None:
        count = len(values)
        mean = statistics.fmean(values)
        deviations = [value - mean for value in values]
        second = statistics.fmean(value**2 for value in deviations)
        if second == 0:
            return None
        biased_excess = (
            statistics.fmean(value**4 for value in deviations) / second**2 - 3
        )
        return (count - 1) * ((count + 1) * biased_excess + 6) / (
            (count - 2) * (count - 3)
        )

    return _rolling_log_return_statistic(close, window=window, statistic=adjusted)


def rsi_wilder(
    close: Mapping[PanelKey, Decimal | None], *, window: int
) -> Panel:
    if window <= 0:
        raise ValueError("window must be positive")
    result: Panel = {}
    for rows in _by_asset(close).values():
        for key, _ in rows:
            result[key] = None
        if len(rows) <= window or any(value is None for _, value in rows):
            continue
        values = [value for _, value in rows if value is not None]
        changes = [values[index] - values[index - 1] for index in range(1, len(values))]
        gains = [max(change, Decimal(0)) for change in changes]
        losses = [max(-change, Decimal(0)) for change in changes]
        average_gain = sum(gains[:window], Decimal(0)) / Decimal(window)
        average_loss = sum(losses[:window], Decimal(0)) / Decimal(window)
        result[rows[window][0]] = _rsi_value(average_gain, average_loss)
        for index in range(window + 1, len(rows)):
            average_gain = (
                average_gain * Decimal(window - 1) + gains[index - 1]
            ) / Decimal(window)
            average_loss = (
                average_loss * Decimal(window - 1) + losses[index - 1]
            ) / Decimal(window)
            result[rows[index][0]] = _rsi_value(average_gain, average_loss)
    return result


def _rsi_value(average_gain: Decimal, average_loss: Decimal) -> Decimal:
    if average_loss == 0:
        return Decimal(100) if average_gain > 0 else Decimal(50)
    return Decimal(100) - Decimal(100) / (
        Decimal(1) + average_gain / average_loss
    )


def ppo_histogram(
    close: Mapping[PanelKey, Decimal | None],
    *,
    fast_window: int,
    slow_window: int,
    signal_window: int,
) -> Panel:
    if fast_window <= 0 or slow_window <= 0 or signal_window <= 0:
        raise ValueError("PPO windows must be positive")
    if fast_window >= slow_window:
        raise ValueError("PPO fast_window must be smaller than slow_window")
    result: Panel = {}
    for rows in _by_asset(close).values():
        for key, _ in rows:
            result[key] = None
        if any(value is None for _, value in rows):
            continue
        values = [float(value) for _, value in rows if value is not None]
        fast = _ema_sma_seed(values, fast_window)
        slow = _ema_sma_seed(values, slow_window)
        ppo: list[float | None] = [None] * len(values)
        for index in range(slow_window - 1, len(values)):
            fast_value = fast[index]
            slow_value = slow[index]
            if (
                fast_value is not None
                and slow_value is not None
                and slow_value != 0.0
            ):
                ppo[index] = 100 * (fast_value - slow_value) / slow_value
        start = slow_window - 1
        available = [value for value in ppo[start:] if value is not None]
        signals = _ema_sma_seed(available, signal_window)
        for relative_index, signal in enumerate(signals):
            absolute_index = start + relative_index
            ppo_value = ppo[absolute_index]
            if signal is not None and ppo_value is not None:
                result[rows[absolute_index][0]] = Decimal(str(ppo_value - signal))
    return result


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


def threshold_state(
    values: Mapping[PanelKey, Decimal | None], *, rule: object
) -> Panel:
    parsed = _threshold_rule(rule)
    operator, threshold, true_score, false_score = parsed
    result: Panel = {}
    for key, value in values.items():
        result[key] = (
            true_score if _compare(value, operator, threshold) else false_score
        ) if value is not None else None
    return result


def crossover_event(
    values: Mapping[PanelKey, Decimal | None], *, rule: object
) -> Panel:
    previous_operator, previous_threshold, current_operator, current_threshold, score = (
        _crossover_rule(rule)
    )
    result: Panel = {key: None for key in values}
    dates = sorted({key[1] for key in values if values[key] is not None})
    for index in range(1, len(dates)):
        previous_date = dates[index - 1]
        current_date = dates[index]
        assets = sorted(
            key[0]
            for key, value in values.items()
            if key[1] == current_date and value is not None
        )
        for asset in assets:
            previous = values.get((asset, previous_date))
            current = values.get((asset, current_date))
            if previous is None or current is None:
                continue
            occurred = _compare(
                previous, previous_operator, previous_threshold
            ) and _compare(current, current_operator, current_threshold)
            result[(asset, current_date)] = score if occurred else Decimal(0)
    return result


def _signal_parameters(
    parameters: Mapping[str, object], *, require_rule: bool
) -> int:
    expected_rule = "rule" in parameters
    if expected_rule != require_rule or "direction" not in parameters:
        raise ValueError("Signal parameters have an invalid rule/direction shape")
    direction = parameters["direction"]
    if type(direction) is not int or direction not in {-1, 1}:
        raise ValueError("direction must be -1 or 1")
    for key, value in parameters.items():
        if key in {"direction", "rule"}:
            continue
        _positive_int(value, key)
    return direction


def _threshold_rule(
    rule: object,
) -> tuple[str, Decimal, Decimal, Decimal]:
    if not isinstance(rule, dict) or set(rule) != {
        "operator",
        "threshold",
        "true_score",
        "false_score",
    }:
        raise ValueError("Threshold state requires its exact frozen rule")
    operator = rule["operator"]
    if operator not in {">", ">=", "<", "<="}:
        raise ValueError("Unsupported threshold operator")
    return (
        str(operator),
        Decimal(str(rule["threshold"])),
        Decimal(str(rule["true_score"])),
        Decimal(str(rule["false_score"])),
    )


def _crossover_rule(
    rule: object,
) -> tuple[str, Decimal, str, Decimal, Decimal]:
    if not isinstance(rule, dict) or set(rule) != {
        "previous",
        "current",
        "event_score",
        "otherwise",
    }:
        raise ValueError("Crossover event requires its exact frozen rule")
    if rule["otherwise"] != "neutral":
        raise ValueError("Crossover non-events must be neutral")
    previous_operator, previous_threshold = _condition(str(rule["previous"]))
    current_operator, current_threshold = _condition(str(rule["current"]))
    return (
        previous_operator,
        previous_threshold,
        current_operator,
        current_threshold,
        Decimal(str(rule["event_score"])),
    )


def _condition(expression: str) -> tuple[str, Decimal]:
    for operator in (">=", "<=", ">", "<"):
        if expression.startswith(operator):
            try:
                return operator, Decimal(expression[len(operator) :])
            except Exception as error:
                raise ValueError("Invalid crossover threshold") from error
    raise ValueError("Unsupported crossover condition")


def _compare(value: Decimal, operator: str, threshold: Decimal) -> bool:
    if operator == ">":
        return value > threshold
    if operator == ">=":
        return value >= threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    raise ValueError("Unsupported comparison operator")


def cross_sectional_centered_rank(
    values: Mapping[PanelKey, Decimal | None], *, direction: int
) -> Panel:
    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or 1")
    grouped: dict[date, list[tuple[PanelKey, Decimal]]] = defaultdict(list)
    result: Panel = {key: None for key in values}
    for key, value in values.items():
        if value is not None:
            grouped[key[1]].append((key, value))
    for rows in grouped.values():
        ordered = sorted(rows, key=lambda item: (item[1], item[0][0]))
        count = len(ordered)
        if count == 1:
            result[ordered[0][0]] = Decimal(0).quantize(QUANTUM)
            continue
        for start, end in _tie_groups(ordered):
            average_rank = (Decimal(start + 1) + Decimal(end)) / Decimal(2)
            centered = Decimal(2) * (average_rank - 1) / Decimal(count - 1) - 1
            score = (centered * direction).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
            for index in range(start, end):
                result[ordered[index][0]] = score
    return result


def execute_representative_graph(
    *,
    adjusted_close: Mapping[PanelKey, Decimal | None],
    close_raw: Mapping[PanelKey, Decimal | None],
    volume_raw: Mapping[PanelKey, Decimal | None],
) -> RepresentativeExecution:
    return_120 = execute_catalog_node(
        TOTAL_RETURN_IMPLEMENTATION,
        parameters={"window": 120},
        input_ports={"close_adj": adjusted_close},
    ).output_ports["total_return"]
    ma_ratio = execute_catalog_node(
        MOVING_AVERAGE_RATIO_IMPLEMENTATION,
        parameters={"short_window": 1, "long_window": 200},
        input_ports={"close_adj": adjusted_close},
    ).output_ports["ma_ratio"]
    simple_return, dollar_volume, daily_impact = amihud_daily_primitives(
        adjusted_close, close_raw, volume_raw
    )
    crossover = price_cross_above_ma(ma_ratio)
    illiquidity = rolling_mean(daily_impact, window=20)
    continuation = cross_sectional_centered_rank(return_120, direction=1)
    liquidity_quality = cross_sectional_centered_rank(illiquidity, direction=-1)
    final_inputs = (continuation, crossover, liquidity_quality)
    aggregate: Panel = {
        key: flat_equal_weight_mean(tuple(panel.get(key) for panel in final_inputs))
        for key in sorted(set().union(*(panel.keys() for panel in final_inputs)))
    }
    features = {
        "total_return__w120": return_120,
        "moving_average_ratio__s1_l200": ma_ratio,
        "simple_return__amihud_daily": simple_return,
        "dollar_volume__close_times_volume": dollar_volume,
        "daily_price_impact__amihud": daily_impact,
        "price_cross_above_ma__s1_l200": crossover,
        "amihud_illiquidity__w20": illiquidity,
        "return_continuation__w120": continuation,
        "low_illiquidity_quality__w20": liquidity_quality,
    }
    return RepresentativeExecution(features, aggregate)


def _by_asset(
    values: Mapping[PanelKey, Decimal | None],
) -> dict[str, list[tuple[PanelKey, Decimal | None]]]:
    result: dict[str, list[tuple[PanelKey, Decimal | None]]] = defaultdict(list)
    for key, value in values.items():
        result[key[0]].append((key, value))
    for rows in result.values():
        rows.sort(key=lambda item: item[0][1])
    return result


def _complete_mean(
    values: list[Decimal | None], index: int, window: int
) -> Decimal | None:
    if window <= 0:
        raise ValueError("window must be positive")
    if index + 1 < window:
        return None
    sample = values[index - window + 1 : index + 1]
    if any(value is None for value in sample):
        return None
    return sum((value for value in sample if value is not None), Decimal(0)) / Decimal(window)


def _ratio_minus_one(
    numerator: Decimal | None, denominator: Decimal | None
) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator - 1


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _tie_groups(rows: list[tuple[PanelKey, Decimal]]) -> Iterable[tuple[int, int]]:
    start = 0
    while start < len(rows):
        end = start + 1
        while end < len(rows) and rows[end][1] == rows[start][1]:
            end += 1
        yield start, end
        start = end
