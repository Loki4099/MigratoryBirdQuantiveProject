from __future__ import annotations

import math
import statistics
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from style_rotation.factor.calculator import (
    FactorBar,
    FactorCalculationError,
    FactorVariantInput,
    calculate_variant,
)

ASSET_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _bars(count: int = 300) -> tuple[FactorBar, ...]:
    start = date(2025, 1, 1)
    result = []
    for index in range(count):
        close = Decimal("100") + Decimal(index) / 10 + Decimal((index % 11) ** 2) / 100
        raw = close * Decimal("1.01")
        result.append(
            FactorBar(
                ASSET_ID,
                "iwf",
                start + timedelta(days=index),
                close,
                raw,
                1_000_000 + index * 137,
            )
        )
    return tuple(result)


def _variant(key: str, implementation: str, parameters: dict[str, int]) -> FactorVariantInput:
    return FactorVariantInput(
        uuid.uuid5(uuid.NAMESPACE_URL, key),
        uuid.uuid5(uuid.NAMESPACE_DNS, key),
        key,
        implementation,
        parameters,
        1,
    )


def _last_value(implementation: str, parameters: dict[str, int]) -> float:
    bars = _bars()
    result = calculate_variant(
        {ASSET_ID: bars},
        _variant(implementation, implementation, parameters),
        coverage_start=bars[-1].session_date,
        coverage_end=bars[-1].session_date,
    )
    return result.points[0].value


def test_return_trend_risk_and_liquidity_formulas_match_golden_values() -> None:
    bars = _bars()
    closes = [item.close_adj for item in bars]
    index = len(bars) - 1
    assert _last_value("total_return_v1", {"window": 20}) == pytest.approx(
        float(closes[index] / closes[index - 20] - 1)
    )
    assert _last_value(
        "lagged_return_v1", {"long_window": 120, "skip_window": 20}
    ) == pytest.approx(float(closes[index - 20] / closes[index - 120] - 1))
    short = sum(closes[-20:]) / 20
    long = sum(closes[-60:]) / 60
    assert _last_value(
        "moving_average_ratio_v1", {"short_window": 20, "long_window": 60}
    ) == pytest.approx(float(short / long - 1))

    returns = [
        math.log(float(closes[position] / closes[position - 1]))
        for position in range(index - 19, index + 1)
    ]
    assert _last_value("realized_volatility_v1", {"window": 20}) == pytest.approx(
        statistics.stdev(returns) * math.sqrt(252)
    )
    assert _last_value("downside_deviation_v1", {"window": 20}) == pytest.approx(
        math.sqrt(252 * statistics.fmean(min(item, 0.0) ** 2 for item in returns))
    )
    sample = closes[-60:]
    peak = Decimal(0)
    drawdown = Decimal(0)
    for close in sample:
        peak = max(peak, close)
        drawdown = min(drawdown, close / peak - 1)
    assert _last_value("maximum_drawdown_v1", {"window": 60}) == pytest.approx(float(abs(drawdown)))

    dollar_volume = [item.close_raw * item.volume_raw for item in bars]
    assert _last_value("relative_dollar_volume_v1", {"window": 20}) == pytest.approx(
        float(dollar_volume[-1] / (sum(dollar_volume[-20:]) / 20) - 1)
    )
    amihud = [
        abs(closes[position] / closes[position - 1] - 1) / dollar_volume[position]
        for position in range(index - 19, index + 1)
    ]
    assert _last_value("amihud_illiquidity_v1", {"window": 20}) == pytest.approx(
        float(sum(amihud) / 20)
    )


def test_rsi_skewness_kurtosis_and_ppo_match_independent_golden_calculations() -> None:
    bars = _bars()
    closes = [float(item.close_adj) for item in bars]
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(item, 0.0) for item in changes]
    losses = [max(-item, 0.0) for item in changes]
    average_gain = statistics.fmean(gains[:14])
    average_loss = statistics.fmean(losses[:14])
    for index in range(14, len(changes)):
        average_gain = (average_gain * 13 + gains[index]) / 14
        average_loss = (average_loss * 13 + losses[index]) / 14
    expected_rsi = 100.0 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)
    assert _last_value("rsi_wilder_v1", {"window": 14}) == pytest.approx(expected_rsi)

    log_returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
    sample = log_returns[-60:]
    mean = statistics.fmean(sample)
    stdev = statistics.stdev(sample)
    expected_skew = 60 * sum(((item - mean) / stdev) ** 3 for item in sample) / (59 * 58)
    assert _last_value("return_skewness_fisher_pearson_v1", {"window": 60}) == pytest.approx(
        expected_skew
    )
    sample = log_returns[-120:]
    mean = statistics.fmean(sample)
    deviations = [item - mean for item in sample]
    m2 = statistics.fmean(item**2 for item in deviations)
    biased_excess = statistics.fmean(item**4 for item in deviations) / m2**2 - 3
    expected_kurtosis = 119 * (121 * biased_excess + 6) / (118 * 117)
    assert _last_value("return_excess_kurtosis_fisher_v1", {"window": 120}) == pytest.approx(
        expected_kurtosis
    )

    fast = _ema(closes, 12)
    slow = _ema(closes, 26)
    ppo = [100 * (fast[index] - slow[index]) / slow[index] for index in range(25, len(closes))]
    signal = _ema(ppo, 9)
    expected_histogram = ppo[-1] - signal[-1]
    assert _last_value(
        "ppo_histogram_sma_seed_v1",
        {"fast_window": 12, "slow_window": 26, "signal_window": 9},
    ) == pytest.approx(expected_histogram)


def _ema(values: list[float], window: int) -> list[float]:
    result = [math.nan] * (window - 1)
    current = statistics.fmean(values[:window])
    result.append(current)
    alpha = 2 / (window + 1)
    for value in values[window:]:
        current = alpha * value + (1 - alpha) * current
        result.append(current)
    return result


def test_formal_coverage_rejects_undefined_values_and_misaligned_assets() -> None:
    bars = _bars(80)
    flat = tuple(
        FactorBar(item.asset_id, item.asset_key, item.session_date, Decimal(100), Decimal(100), 1)
        for item in bars
    )
    with pytest.raises(FactorCalculationError, match="Undefined factor value"):
        calculate_variant(
            {ASSET_ID: flat},
            _variant("skew", "return_skewness_fisher_pearson_v1", {"window": 60}),
            coverage_start=flat[-1].session_date,
            coverage_end=flat[-1].session_date,
        )

    second_id = uuid.uuid4()
    shifted = tuple(
        FactorBar(
            second_id,
            "iwd",
            item.session_date,
            item.close_adj,
            item.close_raw,
            item.volume_raw,
        )
        for item in bars
        if item.session_date != bars[-2].session_date
    )
    with pytest.raises(FactorCalculationError, match="not aligned"):
        calculate_variant(
            {ASSET_ID: bars, second_id: shifted},
            _variant("return", "total_return_v1", {"window": 20}),
            coverage_start=bars[-2].session_date,
            coverage_end=bars[-1].session_date,
        )
