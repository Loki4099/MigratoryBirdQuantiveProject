from __future__ import annotations

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.domain.enums import FactorDirection
from style_rotation.factors.types import FactorDefinitionSpec, FactorVariantSpec

DEFINITIONS = (
    FactorDefinitionSpec(
        "momentum",
        "trend_return",
        "Momentum",
        "Adjusted close total return over N trading days.",
        "close_t / close_(t-N) - 1",
        ("close_adj",),
        FactorDirection.HIGHER_IS_BETTER,
        "momentum",
    ),
    FactorDefinitionSpec(
        "skip_momentum",
        "trend_return",
        "Skip-recent momentum",
        "Long-horizon return excluding the most recent S trading days.",
        "close_(t-S) / close_(t-L) - 1",
        ("close_adj",),
        FactorDirection.HIGHER_IS_BETTER,
        "skip_momentum",
    ),
    FactorDefinitionSpec(
        "short_term_reversal",
        "trend_return",
        "Short-term reversal",
        "Negative adjusted close return over N trading days.",
        "-(close_t / close_(t-N) - 1)",
        ("close_adj",),
        FactorDirection.HIGHER_IS_BETTER,
        "short_term_reversal",
    ),
    FactorDefinitionSpec(
        "moving_average_trend",
        "trend_return",
        "Moving-average trend",
        "Ratio of short and long adjusted-close simple moving averages.",
        "SMA_S(close_adj) / SMA_L(close_adj) - 1",
        ("close_adj",),
        FactorDirection.HIGHER_IS_BETTER,
        "moving_average_trend",
    ),
    FactorDefinitionSpec(
        "distance_to_high",
        "trend_return",
        "Distance to recent high",
        "Adjusted close relative to the rolling high, including the current date.",
        "close_t / rolling_max(close_adj, N) - 1",
        ("close_adj",),
        FactorDirection.HIGHER_IS_BETTER,
        "distance_to_high",
    ),
    FactorDefinitionSpec(
        "historical_volatility",
        "risk",
        "Historical volatility",
        "Annualized sample standard deviation of N adjusted-close log returns.",
        "stdev(log_return, N, ddof=1) * sqrt(252)",
        ("close_adj",),
        FactorDirection.LOWER_IS_BETTER,
        "historical_volatility",
    ),
    FactorDefinitionSpec(
        "downside_volatility",
        "risk",
        "Downside volatility",
        "Annualized lower partial deviation of N adjusted-close log returns.",
        "sqrt(252 * mean(min(log_return, 0)^2))",
        ("close_adj",),
        FactorDirection.LOWER_IS_BETTER,
        "downside_volatility",
    ),
    FactorDefinitionSpec(
        "maximum_drawdown",
        "risk",
        "Maximum drawdown",
        "Absolute maximum drawdown within an N-price adjusted-close window.",
        "abs(min(close_adj / running_max(close_adj) - 1))",
        ("close_adj",),
        FactorDirection.LOWER_IS_BETTER,
        "maximum_drawdown",
    ),
    FactorDefinitionSpec(
        "risk_adjusted_momentum",
        "risk",
        "Risk-adjusted momentum",
        "N-day momentum divided by annualized N-return historical volatility.",
        "momentum_N / historical_volatility_N",
        ("close_adj",),
        FactorDirection.HIGHER_IS_BETTER,
        "risk_adjusted_momentum",
    ),
    FactorDefinitionSpec(
        "atr_ratio",
        "risk",
        "ATR ratio",
        "Wilder ATR divided by adjusted close.",
        "Wilder_ATR_N(high_adj, low_adj, close_adj) / close_adj",
        ("high_adj", "low_adj", "close_adj"),
        FactorDirection.LOWER_IS_BETTER,
        "atr_ratio",
    ),
    FactorDefinitionSpec(
        "volume_trend",
        "volume",
        "Volume trend",
        "Ratio of short and long raw-volume simple moving averages.",
        "SMA_S(volume_raw) / SMA_L(volume_raw) - 1",
        ("volume_raw",),
        FactorDirection.HIGHER_IS_BETTER,
        "volume_trend",
    ),
)


def _variants(
    definition_key: str,
    parameter_sets: tuple[dict[str, int], ...],
    minimum_observations: tuple[int, ...],
) -> tuple[FactorVariantSpec, ...]:
    items: list[FactorVariantSpec] = []
    for parameters, observations in zip(parameter_sets, minimum_observations, strict=True):
        suffix = "_".join(str(value) for value in parameters.values())
        items.append(
            FactorVariantSpec(
                f"{definition_key}_{suffix}",
                definition_key,
                parameters,
                observations,
            )
        )
    return tuple(items)


VARIANTS = (
    *_variants("momentum", tuple({"window": n} for n in (20, 60, 120, 252)), (21, 61, 121, 253)),
    *_variants(
        "skip_momentum",
        ({"long_window": 120, "skip_window": 20}, {"long_window": 252, "skip_window": 20}),
        (121, 253),
    ),
    *_variants("short_term_reversal", ({"window": 5}, {"window": 10}), (6, 11)),
    *_variants(
        "moving_average_trend",
        ({"short_window": 20, "long_window": 60}, {"short_window": 60, "long_window": 120}),
        (60, 120),
    ),
    *_variants("distance_to_high", ({"window": 60}, {"window": 120}), (60, 120)),
    *_variants("historical_volatility", ({"window": 20}, {"window": 60}), (21, 61)),
    *_variants("downside_volatility", ({"window": 20}, {"window": 60}), (21, 61)),
    *_variants(
        "maximum_drawdown",
        ({"window": 20}, {"window": 60}, {"window": 120}),
        (20, 60, 120),
    ),
    *_variants("risk_adjusted_momentum", ({"window": 60}, {"window": 120}), (61, 121)),
    *_variants("atr_ratio", ({"window": 14}, {"window": 20}), (15, 21)),
    *_variants(
        "volume_trend",
        ({"short_window": 20, "long_window": 60},),
        (60,),
    ),
)

DEFINITION_BY_KEY = {item.key: item for item in DEFINITIONS}
VARIANT_BY_KEY = {item.key: item for item in VARIANTS}

if len(DEFINITION_BY_KEY) != len(DEFINITIONS) or len(VARIANT_BY_KEY) != len(VARIANTS):
    raise RuntimeError("Factor registry keys must be unique")

REGISTRY_HASH = sha256_hexdigest({"definitions": DEFINITIONS, "variants": VARIANTS})
