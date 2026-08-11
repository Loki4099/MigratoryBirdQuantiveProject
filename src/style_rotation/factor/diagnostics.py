from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import dataclass
from datetime import date
from itertools import combinations


@dataclass(frozen=True, slots=True)
class DiagnosticValue:
    asset_id: uuid.UUID
    observation_date: date
    value: float


@dataclass(frozen=True, slots=True)
class DiagnosticDataset:
    factor_dataset_id: uuid.UUID
    artifact_id: uuid.UUID
    factor_definition_version_id: uuid.UUID
    variant_key: str
    values: tuple[DiagnosticValue, ...]


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    factor_dataset_id: uuid.UUID
    observation_count: int
    asset_count: int
    missing_count: int
    mean: float
    standard_deviation: float
    minimum: float
    p05: float
    p25: float
    median: float
    p75: float
    p95: float
    maximum: float
    zero_variance: bool


@dataclass(frozen=True, slots=True)
class PairCorrelation:
    left_factor_dataset_id: uuid.UUID
    right_factor_dataset_id: uuid.UUID
    left_variant_key: str
    right_variant_key: str
    observation_count: int
    spearman_correlation: float | None
    same_definition: bool
    high_correlation: bool


@dataclass(frozen=True, slots=True)
class DiagnosticIssue:
    factor_dataset_id: uuid.UUID
    severity: str
    issue_code: str
    message: str
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class FactorDiagnostics:
    summaries: tuple[DatasetSummary, ...]
    correlations: tuple[PairCorrelation, ...]
    issues: tuple[DiagnosticIssue, ...]


def calculate_factor_diagnostics(
    datasets: tuple[DiagnosticDataset, ...],
    *,
    high_correlation_threshold: float = 0.85,
) -> FactorDiagnostics:
    if not datasets:
        raise ValueError("Factor diagnostics require at least one dataset")
    if not 0 < high_correlation_threshold <= 1 or not math.isfinite(high_correlation_threshold):
        raise ValueError("High-correlation threshold must be finite and in (0, 1]")
    ordered = tuple(sorted(datasets, key=lambda item: item.variant_key))
    if len({item.factor_dataset_id for item in ordered}) != len(ordered):
        raise ValueError("Factor diagnostics contain duplicate datasets")
    summaries: list[DatasetSummary] = []
    issues: list[DiagnosticIssue] = []
    indexed: dict[uuid.UUID, dict[tuple[uuid.UUID, date], float]] = {}
    expected_keys: set[tuple[uuid.UUID, date]] | None = None
    for dataset in ordered:
        values_by_key = _indexed_values(dataset)
        keys = set(values_by_key)
        if expected_keys is None:
            expected_keys = keys
        elif keys != expected_keys:
            raise ValueError("Factor datasets do not share exact asset-date coverage")
        indexed[dataset.factor_dataset_id] = values_by_key
        summary = _summary(
            dataset.factor_dataset_id,
            tuple(values_by_key.values()),
            len({asset_id for asset_id, _day in values_by_key}),
        )
        summaries.append(summary)
        if summary.zero_variance:
            issues.append(
                DiagnosticIssue(
                    dataset.factor_dataset_id,
                    "warning",
                    "zero_variance",
                    "Factor values have zero variance over the diagnostic context",
                    {"variant_key": dataset.variant_key},
                )
            )
    correlations: list[PairCorrelation] = []
    for left, right in combinations(ordered, 2):
        pair_keys = sorted(
            indexed[left.factor_dataset_id], key=lambda item: (str(item[0]), item[1])
        )
        left_values = [indexed[left.factor_dataset_id][key] for key in pair_keys]
        right_values = [indexed[right.factor_dataset_id][key] for key in pair_keys]
        correlation = _spearman(left_values, right_values)
        same_definition = left.factor_definition_version_id == right.factor_definition_version_id
        high = correlation is not None and abs(correlation) >= high_correlation_threshold
        correlations.append(
            PairCorrelation(
                left.factor_dataset_id,
                right.factor_dataset_id,
                left.variant_key,
                right.variant_key,
                len(pair_keys),
                correlation,
                same_definition,
                high,
            )
        )
        if correlation is None:
            issues.append(
                DiagnosticIssue(
                    left.factor_dataset_id,
                    "warning",
                    "undefined_pair_correlation",
                    "Spearman correlation is undefined because one series has zero rank variance",
                    {
                        "left_variant_key": left.variant_key,
                        "right_variant_key": right.variant_key,
                    },
                )
            )
    return FactorDiagnostics(tuple(summaries), tuple(correlations), tuple(issues))


def _indexed_values(dataset: DiagnosticDataset) -> dict[tuple[uuid.UUID, date], float]:
    result: dict[tuple[uuid.UUID, date], float] = {}
    for point in dataset.values:
        if not math.isfinite(point.value):
            raise ValueError(f"Non-finite factor value in {dataset.variant_key}")
        key = (point.asset_id, point.observation_date)
        if key in result:
            raise ValueError(f"Duplicate factor value in {dataset.variant_key}: {key}")
        result[key] = point.value
    if len(result) < 2:
        raise ValueError("Factor diagnostics require at least two aligned observations")
    return result


def _summary(
    factor_dataset_id: uuid.UUID, values: tuple[float, ...], asset_count: int
) -> DatasetSummary:
    ordered = sorted(values)
    deviation = statistics.stdev(ordered) if len(ordered) > 1 else 0.0
    return DatasetSummary(
        factor_dataset_id,
        len(ordered),
        asset_count,
        0,
        statistics.fmean(ordered),
        deviation,
        ordered[0],
        _percentile(ordered, 0.05),
        _percentile(ordered, 0.25),
        _percentile(ordered, 0.50),
        _percentile(ordered, 0.75),
        _percentile(ordered, 0.95),
        ordered[-1],
        deviation == 0,
    )


def _percentile(ordered: list[float], probability: float) -> float:
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * weight
    return min(ordered[upper], max(ordered[lower], interpolated))


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("Spearman inputs must be aligned and contain at least two values")
    left_ranks = _average_ranks(left)
    right_ranks = _average_ranks(right)
    left_mean = statistics.fmean(left_ranks)
    right_mean = statistics.fmean(right_ranks)
    numerator = sum(
        (left_rank - left_mean) * (right_rank - right_mean)
        for left_rank, right_rank in zip(left_ranks, right_ranks, strict=True)
    )
    left_sum = sum((item - left_mean) ** 2 for item in left_ranks)
    right_sum = sum((item - right_mean) ** 2 for item in right_ranks)
    if left_sum == 0 or right_sum == 0:
        return None
    result = numerator / math.sqrt(left_sum * right_sum)
    return max(-1.0, min(1.0, result))


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average_rank = (start + 1 + end) / 2
        for position in range(start, end):
            ranks[ordered[position][0]] = average_rank
        start = end
    return ranks
