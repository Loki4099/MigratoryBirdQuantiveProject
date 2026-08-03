from __future__ import annotations

import math
import re
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

SCORE_QUANTUM = Decimal("0.000000000000000001")
CONDITION_PATTERN = re.compile(r"^(<=|>=|<|>)(-?(?:\d+(?:\.\d*)?|\.\d+))$")


@dataclass(frozen=True, slots=True)
class SignalVersionInput:
    signal_version_id: uuid.UUID
    artifact_id: uuid.UUID
    signal_key: str
    factor_variant_id: uuid.UUID
    direction: str
    normalization: str
    extreme_policy: str
    missing_policy: str
    tie_policy: str
    output_type: str
    rule: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class FactorValueInput:
    asset_id: uuid.UUID
    asset_key: str
    observation_date: date
    value: float


@dataclass(frozen=True, slots=True)
class SignalPoint:
    asset_id: uuid.UUID
    asset_key: str
    observation_date: date
    score: Decimal
    state: str | None
    event: bool | None


@dataclass(frozen=True, slots=True)
class SignalCalculation:
    version: SignalVersionInput
    coverage_start: date
    coverage_end: date
    points: tuple[SignalPoint, ...]


class SignalCalculationError(RuntimeError):
    """Raised when a formal signal cannot be calculated without ambiguity."""


def calculate_signal(
    version: SignalVersionInput,
    factor_points: tuple[FactorValueInput, ...],
) -> SignalCalculation:
    _validate_policy(version)
    if not factor_points:
        raise SignalCalculationError(f"Signal {version.signal_key} has no factor observations")
    if any(not math.isfinite(item.value) for item in factor_points):
        raise SignalCalculationError(f"Signal {version.signal_key} received a non-finite factor")
    identities = [(item.asset_id, item.observation_date) for item in factor_points]
    if len(identities) != len(set(identities)):
        raise SignalCalculationError(f"Signal {version.signal_key} has duplicate asset dates")
    by_date = _aligned_by_date(factor_points, version.signal_key)
    if version.output_type == "continuous":
        points = _continuous_points(version, by_date)
    elif version.output_type == "threshold_state":
        points = _threshold_points(version, by_date)
    elif version.output_type == "crossover_event":
        points = _crossover_points(version, by_date)
    else:
        raise SignalCalculationError(
            f"Signal form {version.output_type} has no published v1 semantics"
        )
    if not points:
        raise SignalCalculationError(f"Signal {version.signal_key} produced no observations")
    points.sort(key=lambda item: (item.asset_key, item.observation_date, str(item.asset_id)))
    dates = [item.observation_date for item in points]
    return SignalCalculation(version, min(dates), max(dates), tuple(points))


def _validate_policy(version: SignalVersionInput) -> None:
    if version.extreme_policy != "none":
        raise SignalCalculationError(f"Unsupported extreme policy: {version.extreme_policy}")
    if version.missing_policy != "error_after_common_warmup":
        raise SignalCalculationError(f"Unsupported missing policy: {version.missing_policy}")
    if version.direction not in {"higher_is_better", "lower_is_better"}:
        raise SignalCalculationError(f"Unsupported signal direction: {version.direction}")
    if version.output_type == "continuous":
        if version.normalization != "cross_sectional_centered_rank_-1_1":
            raise SignalCalculationError(f"Unsupported normalization: {version.normalization}")
        if version.tie_policy != "average_rank":
            raise SignalCalculationError(f"Unsupported tie policy: {version.tie_policy}")
        if version.rule is not None:
            raise SignalCalculationError("Continuous signals cannot have a discrete rule")
    elif version.normalization != "none" or version.tie_policy != "not_applicable":
        raise SignalCalculationError("Discrete signals must not apply cross-sectional ranking")


def _aligned_by_date(
    factor_points: tuple[FactorValueInput, ...], signal_key: str
) -> dict[date, tuple[FactorValueInput, ...]]:
    grouped: dict[date, list[FactorValueInput]] = defaultdict(list)
    dates_by_asset: dict[uuid.UUID, list[date]] = defaultdict(list)
    asset_keys: dict[uuid.UUID, str] = {}
    for point in factor_points:
        grouped[point.observation_date].append(point)
        dates_by_asset[point.asset_id].append(point.observation_date)
        previous = asset_keys.setdefault(point.asset_id, point.asset_key)
        if previous != point.asset_key:
            raise SignalCalculationError(f"Signal {signal_key} has unstable asset identity")
    reference: tuple[date, ...] | None = None
    for asset_id in sorted(dates_by_asset, key=str):
        dates = tuple(sorted(dates_by_asset[asset_id]))
        if reference is None:
            reference = dates
        elif dates != reference:
            raise SignalCalculationError(f"Signal {signal_key} factor inputs are not aligned")
    if reference is None or len(asset_keys) < 2:
        raise SignalCalculationError(f"Signal {signal_key} requires at least two aligned assets")
    return {
        day: tuple(sorted(items, key=lambda item: (item.asset_key, str(item.asset_id))))
        for day, items in sorted(grouped.items())
    }


def _continuous_points(
    version: SignalVersionInput,
    by_date: dict[date, tuple[FactorValueInput, ...]],
) -> list[SignalPoint]:
    points: list[SignalPoint] = []
    direction = Decimal(1) if version.direction == "higher_is_better" else Decimal(-1)
    for day, inputs in by_date.items():
        ranks = _average_ranks(inputs)
        denominator = Decimal(len(inputs) - 1)
        for item in inputs:
            centered = (Decimal(2) * (ranks[item.asset_id] - 1) / denominator) - 1
            score = (direction * centered).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_EVEN)
            points.append(SignalPoint(item.asset_id, item.asset_key, day, score, None, None))
    return points


def _average_ranks(inputs: tuple[FactorValueInput, ...]) -> dict[uuid.UUID, Decimal]:
    ordered = sorted(inputs, key=lambda item: (item.value, item.asset_key, str(item.asset_id)))
    ranks: dict[uuid.UUID, Decimal] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end].value == ordered[index].value:
            end += 1
        average = (Decimal(index + 1) + Decimal(end)) / 2
        for item in ordered[index:end]:
            ranks[item.asset_id] = average
        index = end
    return ranks


def _threshold_points(
    version: SignalVersionInput,
    by_date: dict[date, tuple[FactorValueInput, ...]],
) -> list[SignalPoint]:
    rule = _required_rule(version)
    operator = str(rule["operator"])
    threshold = float(rule["threshold"])
    true_score = _score(rule["true_score"])
    false_score = _score(rule["false_score"])
    return [
        SignalPoint(
            item.asset_id,
            item.asset_key,
            day,
            true_score if _compare(item.value, operator, threshold) else false_score,
            _state(true_score if _compare(item.value, operator, threshold) else false_score),
            None,
        )
        for day, inputs in by_date.items()
        for item in inputs
    ]


def _crossover_points(
    version: SignalVersionInput,
    by_date: dict[date, tuple[FactorValueInput, ...]],
) -> list[SignalPoint]:
    rule = _required_rule(version)
    previous_condition = _condition(str(rule["previous"]))
    current_condition = _condition(str(rule["current"]))
    event_score = _score(rule["event_score"])
    if rule["otherwise"] != "neutral":
        raise SignalCalculationError("Crossover non-events must be neutral")
    dates = tuple(by_date)
    if len(dates) < 2:
        raise SignalCalculationError("Crossover signals require at least two observations")
    points: list[SignalPoint] = []
    for index in range(1, len(dates)):
        previous = {item.asset_id: item for item in by_date[dates[index - 1]]}
        current = by_date[dates[index]]
        for item in current:
            prior = previous[item.asset_id]
            occurred = previous_condition(prior.value) and current_condition(item.value)
            score = event_score if occurred else Decimal(0).quantize(SCORE_QUANTUM)
            points.append(
                SignalPoint(
                    item.asset_id,
                    item.asset_key,
                    item.observation_date,
                    score,
                    _state(score),
                    occurred,
                )
            )
    return points


def _required_rule(version: SignalVersionInput) -> dict[str, Any]:
    if version.rule is None:
        raise SignalCalculationError(f"Signal {version.signal_key} requires an explicit rule")
    return version.rule


def _score(value: Any) -> Decimal:
    score = Decimal(str(value)).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_EVEN)
    if not score.is_finite():
        raise SignalCalculationError("Signal rule score must be finite")
    return score


def _state(score: Decimal) -> str:
    if score > 0:
        return "positive"
    if score < 0:
        return "negative"
    return "neutral"


def _compare(value: float, operator: str, threshold: float) -> bool:
    if operator == ">":
        return value > threshold
    if operator == ">=":
        return value >= threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    raise SignalCalculationError(f"Unsupported rule operator: {operator}")


def _condition(expression: str) -> Callable[[float], bool]:
    match = CONDITION_PATTERN.fullmatch(expression)
    if match is None:
        raise SignalCalculationError(f"Unsupported crossover condition: {expression}")
    operator, threshold_text = match.groups()
    threshold = float(threshold_text)
    return lambda value: _compare(value, operator, threshold)
