from __future__ import annotations

import math
import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import combinations


@dataclass(frozen=True, slots=True)
class EvaluationValue:
    asset_id: uuid.UUID
    asset_key: str
    observation_date: date
    score: float
    state: str | None
    event: bool | None


@dataclass(frozen=True, slots=True)
class EvaluationSignal:
    signal_dataset_id: uuid.UUID
    artifact_id: uuid.UUID
    signal_key: str
    output_type: str
    values: tuple[EvaluationValue, ...]


@dataclass(frozen=True, slots=True)
class EvaluationReturn:
    asset_id: uuid.UUID
    asset_key: str
    decision_date: date
    forward_return: float


@dataclass(frozen=True, slots=True)
class SignalEvaluationPeriod:
    signal_dataset_id: uuid.UUID
    decision_date: date
    rank_ic: float | None
    rank_ic_reason: str | None
    top_bottom_spread: float
    active_count: int
    event_count: int | None


@dataclass(frozen=True, slots=True)
class SignalEvaluationMetric:
    signal_dataset_id: uuid.UUID
    window_key: str
    window_start: date
    window_end: date
    period_count: int
    valid_ic_count: int
    undefined_ic_count: int
    mean_rank_ic: float | None
    median_rank_ic: float | None
    positive_ic_ratio: float | None
    information_ratio: float | None
    mean_top_bottom_spread: float
    event_rate: float | None
    event_asset_concentration: float | None
    non_neutral_rate: float
    mean_top2_turnover: float | None


@dataclass(frozen=True, slots=True)
class SignalPairDiagnostic:
    left_signal_dataset_id: uuid.UUID
    right_signal_dataset_id: uuid.UUID
    left_signal_key: str
    right_signal_key: str
    score_observation_count: int
    score_spearman: float | None
    spread_period_count: int
    spread_correlation: float | None
    mean_top2_overlap: float
    high_correlation: bool


@dataclass(frozen=True, slots=True)
class SignalDiagnosticIssue:
    signal_dataset_id: uuid.UUID
    severity: str
    issue_code: str
    message: str
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class SignalDiagnostics:
    periods: tuple[SignalEvaluationPeriod, ...]
    metrics: tuple[SignalEvaluationMetric, ...]
    pairs: tuple[SignalPairDiagnostic, ...]
    issues: tuple[SignalDiagnosticIssue, ...]


def calculate_signal_diagnostics(
    signals: tuple[EvaluationSignal, ...],
    returns: tuple[EvaluationReturn, ...],
    candidate_asset_ids: frozenset[uuid.UUID],
    *,
    frequency: str,
    high_correlation_threshold: float = 0.85,
) -> SignalDiagnostics:
    if not signals or not returns:
        raise ValueError("Signal diagnostics require signals and forward returns")
    if len(candidate_asset_ids) != 4:
        raise ValueError("v0.2 Signal diagnostics require exactly four candidate assets")
    if frequency not in {"weekly", "monthly"}:
        raise ValueError("Signal diagnostic frequency must be weekly or monthly")
    if not 0 < high_correlation_threshold <= 1:
        raise ValueError("Signal correlation threshold must be in (0, 1]")
    ordered_signals = tuple(sorted(signals, key=lambda item: item.signal_key))
    if len({item.signal_dataset_id for item in ordered_signals}) != len(ordered_signals):
        raise ValueError("Signal diagnostics contain duplicate datasets")
    return_by_date = _return_index(returns, candidate_asset_ids)
    periods: list[SignalEvaluationPeriod] = []
    issues: list[SignalDiagnosticIssue] = []
    values_by_signal = {
        signal.signal_dataset_id: _signal_index(signal, candidate_asset_ids)
        for signal in ordered_signals
    }
    common_target_dates = set(return_by_date)
    for signal_dates in values_by_signal.values():
        common_target_dates.intersection_update(signal_dates)
    common_dates = tuple(sorted(common_target_dates))
    if not common_dates:
        raise ValueError("Signal datasets and forward returns share no common cohort dates")
    top2_by_signal: dict[uuid.UUID, dict[date, frozenset[uuid.UUID]]] = {}
    for signal in ordered_signals:
        by_date = values_by_signal[signal.signal_dataset_id]
        top2_by_signal[signal.signal_dataset_id] = {}
        for day in common_dates:
            cross_section = by_date[day]
            future = return_by_date[day]
            scores = [
                cross_section[asset_id].score for asset_id in sorted(candidate_asset_ids, key=str)
            ]
            outcomes = [future[asset_id] for asset_id in sorted(candidate_asset_ids, key=str)]
            rank_ic = _spearman(scores, outcomes)
            reason = None
            if rank_ic is None:
                reason = (
                    "constant_signal_cross_section"
                    if len(set(scores)) == 1
                    else "constant_forward_return_cross_section"
                )
            ranked = sorted(
                candidate_asset_ids,
                key=lambda asset_id: (
                    -cross_section[asset_id].score,
                    cross_section[asset_id].asset_key,
                ),
            )
            top = frozenset(ranked[:2])
            bottom = ranked[2:]
            top2_by_signal[signal.signal_dataset_id][day] = top
            active_count = sum(
                cross_section[asset_id].score != 0 for asset_id in candidate_asset_ids
            )
            event_count = (
                sum(cross_section[asset_id].event is True for asset_id in candidate_asset_ids)
                if signal.output_type == "crossover_event"
                else None
            )
            periods.append(
                SignalEvaluationPeriod(
                    signal.signal_dataset_id,
                    day,
                    rank_ic,
                    reason,
                    statistics.fmean(future[item] for item in top)
                    - statistics.fmean(future[item] for item in bottom),
                    active_count,
                    event_count,
                )
            )
        signal_periods = tuple(
            item for item in periods if item.signal_dataset_id == signal.signal_dataset_id
        )
        if all(item.rank_ic is None for item in signal_periods):
            issues.append(
                SignalDiagnosticIssue(
                    signal.signal_dataset_id,
                    "warning",
                    "all_rank_ic_undefined",
                    "Rank IC is undefined for every common target period",
                    {"signal_key": signal.signal_key},
                )
            )
        if len(signal_periods) < (12 if frequency == "weekly" else 6):
            issues.append(
                SignalDiagnosticIssue(
                    signal.signal_dataset_id,
                    "warning",
                    "short_evaluation_sample",
                    "The evaluation sample is too short for a stable research conclusion",
                    {"period_count": len(signal_periods), "frequency": frequency},
                )
            )
    periods.sort(key=lambda item: (str(item.signal_dataset_id), item.decision_date))
    metrics = _metrics(
        tuple(periods),
        ordered_signals,
        frequency,
        top2_by_signal,
        values_by_signal,
    )
    pairs = _pairs(
        ordered_signals,
        values_by_signal,
        top2_by_signal,
        tuple(periods),
        high_correlation_threshold,
    )
    return SignalDiagnostics(tuple(periods), metrics, pairs, tuple(issues))


def _return_index(
    returns: tuple[EvaluationReturn, ...], candidate_ids: frozenset[uuid.UUID]
) -> dict[date, dict[uuid.UUID, float]]:
    grouped: dict[date, dict[uuid.UUID, float]] = defaultdict(dict)
    for point in returns:
        if point.asset_id not in candidate_ids:
            continue
        if not math.isfinite(point.forward_return):
            raise ValueError("Forward return must be finite")
        if point.asset_id in grouped[point.decision_date]:
            raise ValueError("Duplicate forward return for candidate and decision date")
        grouped[point.decision_date][point.asset_id] = point.forward_return
    if not grouped or any(set(items) != candidate_ids for items in grouped.values()):
        raise ValueError("Forward returns must contain all four candidates on every date")
    return dict(grouped)


def _signal_index(
    signal: EvaluationSignal, candidate_ids: frozenset[uuid.UUID]
) -> dict[date, dict[uuid.UUID, EvaluationValue]]:
    grouped: dict[date, dict[uuid.UUID, EvaluationValue]] = defaultdict(dict)
    for point in signal.values:
        if point.asset_id not in candidate_ids:
            continue
        if not math.isfinite(point.score):
            raise ValueError(f"Non-finite score in {signal.signal_key}")
        if point.asset_id in grouped[point.observation_date]:
            raise ValueError(f"Duplicate score in {signal.signal_key}")
        grouped[point.observation_date][point.asset_id] = point
    if not grouped or any(set(items) != candidate_ids for items in grouped.values()):
        raise ValueError(f"Signal {signal.signal_key} must have four aligned candidates")
    return dict(grouped)


def _metrics(
    periods: tuple[SignalEvaluationPeriod, ...],
    signals: tuple[EvaluationSignal, ...],
    frequency: str,
    top2_by_signal: dict[uuid.UUID, dict[date, frozenset[uuid.UUID]]],
    values_by_signal: dict[uuid.UUID, dict[date, dict[uuid.UUID, EvaluationValue]]],
) -> tuple[SignalEvaluationMetric, ...]:
    by_signal: dict[uuid.UUID, list[SignalEvaluationPeriod]] = defaultdict(list)
    for item in periods:
        by_signal[item.signal_dataset_id].append(item)
    output_types = {item.signal_dataset_id: item.output_type for item in signals}
    result: list[SignalEvaluationMetric] = []
    for signal_id, items in by_signal.items():
        windows: list[tuple[str, list[SignalEvaluationPeriod]]] = [("full", items)]
        by_year: dict[int, list[SignalEvaluationPeriod]] = defaultdict(list)
        for item in items:
            by_year[item.decision_date.year].append(item)
        windows.extend((f"year:{year}", values) for year, values in sorted(by_year.items()))
        for window_key, window in windows:
            valid_ic = [item.rank_ic for item in window if item.rank_ic is not None]
            ic_std = statistics.stdev(valid_ic) if len(valid_ic) > 1 else None
            mean_ic = statistics.fmean(valid_ic) if valid_ic else None
            annual_periods = 52 if frequency == "weekly" else 12
            information_ratio = None
            if mean_ic is not None and ic_std is not None and ic_std != 0:
                information_ratio = mean_ic / ic_std * math.sqrt(annual_periods)
            event_observations = sum(item.event_count or 0 for item in window)
            window_dates = sorted(item.decision_date for item in window)
            event_by_asset: dict[uuid.UUID, int] = defaultdict(int)
            if output_types[signal_id] == "crossover_event":
                for day in window_dates:
                    for asset_id, value in values_by_signal[signal_id][day].items():
                        event_by_asset[asset_id] += int(value.event is True)
            event_concentration = (
                sum((count / event_observations) ** 2 for count in event_by_asset.values())
                if event_observations
                else None
            )
            turnovers = [
                1
                - len(
                    top2_by_signal[signal_id][previous].intersection(
                        top2_by_signal[signal_id][current]
                    )
                )
                / 2
                for previous, current in zip(window_dates, window_dates[1:], strict=False)
            ]
            result.append(
                SignalEvaluationMetric(
                    signal_id,
                    window_key,
                    min(item.decision_date for item in window),
                    max(item.decision_date for item in window),
                    len(window),
                    len(valid_ic),
                    len(window) - len(valid_ic),
                    mean_ic,
                    statistics.median(valid_ic) if valid_ic else None,
                    sum(item > 0 for item in valid_ic) / len(valid_ic) if valid_ic else None,
                    information_ratio,
                    statistics.fmean(item.top_bottom_spread for item in window),
                    event_observations / (len(window) * 4)
                    if output_types[signal_id] == "crossover_event"
                    else None,
                    event_concentration,
                    sum(item.active_count for item in window) / (len(window) * 4),
                    statistics.fmean(turnovers) if turnovers else None,
                )
            )
    return tuple(sorted(result, key=lambda item: (str(item.signal_dataset_id), item.window_key)))


def _pairs(
    signals: tuple[EvaluationSignal, ...],
    values_by_signal: dict[uuid.UUID, dict[date, dict[uuid.UUID, EvaluationValue]]],
    top2_by_signal: dict[uuid.UUID, dict[date, frozenset[uuid.UUID]]],
    periods: tuple[SignalEvaluationPeriod, ...],
    threshold: float,
) -> tuple[SignalPairDiagnostic, ...]:
    spreads = {
        signal.signal_dataset_id: {
            item.decision_date: item.top_bottom_spread
            for item in periods
            if item.signal_dataset_id == signal.signal_dataset_id
        }
        for signal in signals
    }
    result: list[SignalPairDiagnostic] = []
    for left, right in combinations(signals, 2):
        left_values = values_by_signal[left.signal_dataset_id]
        right_values = values_by_signal[right.signal_dataset_id]
        common_dates = sorted(
            set(top2_by_signal[left.signal_dataset_id]).intersection(
                top2_by_signal[right.signal_dataset_id]
            )
        )
        identities = [
            (day, asset_id)
            for day in common_dates
            for asset_id in sorted(set(left_values[day]).intersection(right_values[day]), key=str)
        ]
        score_correlation = _spearman(
            [left_values[day][asset_id].score for day, asset_id in identities],
            [right_values[day][asset_id].score for day, asset_id in identities],
        )
        common_periods = sorted(
            set(spreads[left.signal_dataset_id]).intersection(spreads[right.signal_dataset_id])
        )
        spread_correlation = _pearson(
            [spreads[left.signal_dataset_id][day] for day in common_periods],
            [spreads[right.signal_dataset_id][day] for day in common_periods],
        )
        overlap_dates = sorted(
            set(top2_by_signal[left.signal_dataset_id]).intersection(
                top2_by_signal[right.signal_dataset_id]
            )
        )
        overlap = statistics.fmean(
            len(
                top2_by_signal[left.signal_dataset_id][day].intersection(
                    top2_by_signal[right.signal_dataset_id][day]
                )
            )
            / 2
            for day in overlap_dates
        )
        result.append(
            SignalPairDiagnostic(
                left.signal_dataset_id,
                right.signal_dataset_id,
                left.signal_key,
                right.signal_key,
                len(identities),
                score_correlation,
                len(common_periods),
                spread_correlation,
                overlap,
                score_correlation is not None and abs(score_correlation) >= threshold,
            )
        )
    return tuple(result)


def _spearman(left: list[float], right: list[float]) -> float | None:
    return _pearson(_average_ranks(left), _average_ranks(right))


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean, right_mean = statistics.fmean(left), statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    denominator = math.sqrt(
        sum((item - left_mean) ** 2 for item in left)
        * sum((item - right_mean) ** 2 for item in right)
    )
    return None if denominator == 0 else max(-1.0, min(1.0, numerator / denominator))


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average = (start + 1 + end) / 2
        for position in range(start, end):
            ranks[ordered[position][0]] = average
        start = end
    return ranks
