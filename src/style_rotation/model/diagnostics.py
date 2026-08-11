from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from style_rotation.signal.diagnostics import (
    EvaluationReturn,
    EvaluationSignal,
    EvaluationValue,
    calculate_signal_diagnostics,
)


@dataclass(frozen=True, slots=True)
class ModelEvaluationValue:
    asset_id: uuid.UUID
    asset_key: str
    observation_date: date
    score: float
    direction: int
    confidence: float


@dataclass(frozen=True, slots=True)
class ModelEvaluationDataset:
    model_dataset_id: uuid.UUID
    artifact_id: uuid.UUID
    specification_key: str
    specification_type: str
    dimension_keys: tuple[str, ...]
    values: tuple[ModelEvaluationValue, ...]


@dataclass(frozen=True, slots=True)
class ModelEvaluationPeriod:
    model_dataset_id: uuid.UUID
    decision_date: date
    rank_ic: float | None
    rank_ic_reason: str | None
    top_bottom_spread: float
    active_count: int
    score_dispersion: float
    mean_confidence: float


@dataclass(frozen=True, slots=True)
class ModelEvaluationMetric:
    model_dataset_id: uuid.UUID
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
    non_neutral_rate: float
    mean_top2_turnover: float | None
    mean_score_dispersion: float
    mean_confidence: float


@dataclass(frozen=True, slots=True)
class ModelPairDiagnostic:
    left_model_dataset_id: uuid.UUID
    right_model_dataset_id: uuid.UUID
    score_observation_count: int
    score_spearman: float | None
    spread_period_count: int
    spread_correlation: float | None
    mean_top2_overlap: float
    high_correlation: bool


@dataclass(frozen=True, slots=True)
class ModelAblationComparison:
    full_model_dataset_id: uuid.UUID
    ablated_model_dataset_id: uuid.UUID
    removed_dimension_key: str
    window_key: str
    period_count: int
    delta_mean_rank_ic: float | None
    delta_information_ratio: float | None
    delta_mean_top_bottom_spread: float


@dataclass(frozen=True, slots=True)
class ModelDiagnosticIssue:
    model_dataset_id: uuid.UUID
    severity: str
    issue_code: str
    message: str
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class ModelDiagnostics:
    periods: tuple[ModelEvaluationPeriod, ...]
    metrics: tuple[ModelEvaluationMetric, ...]
    pairs: tuple[ModelPairDiagnostic, ...]
    ablations: tuple[ModelAblationComparison, ...]
    issues: tuple[ModelDiagnosticIssue, ...]


def calculate_model_diagnostics(
    datasets: tuple[ModelEvaluationDataset, ...],
    returns: tuple[EvaluationReturn, ...],
    candidate_asset_ids: frozenset[uuid.UUID],
    *,
    frequency: str,
    high_correlation_threshold: float = 0.85,
) -> ModelDiagnostics:
    if not datasets:
        raise ValueError("Model diagnostics require published Model datasets")
    base = calculate_signal_diagnostics(
        tuple(
            EvaluationSignal(
                item.model_dataset_id,
                item.artifact_id,
                item.specification_key,
                "continuous",
                tuple(
                    EvaluationValue(
                        point.asset_id,
                        point.asset_key,
                        point.observation_date,
                        point.score,
                        "neutral" if point.direction == 0 else "active",
                        None,
                    )
                    for point in item.values
                ),
            )
            for item in datasets
        ),
        returns,
        candidate_asset_ids,
        frequency=frequency,
        high_correlation_threshold=high_correlation_threshold,
    )
    value_windows = _value_windows(datasets)
    periods = tuple(
        ModelEvaluationPeriod(
            item.signal_dataset_id,
            item.decision_date,
            item.rank_ic,
            item.rank_ic_reason,
            item.top_bottom_spread,
            item.active_count,
            value_windows[(item.signal_dataset_id, item.decision_date)][0],
            value_windows[(item.signal_dataset_id, item.decision_date)][1],
        )
        for item in base.periods
    )
    period_by_dataset: dict[uuid.UUID, list[ModelEvaluationPeriod]] = defaultdict(list)
    for item in periods:
        period_by_dataset[item.model_dataset_id].append(item)
    metrics = tuple(
        ModelEvaluationMetric(
            item.signal_dataset_id,
            item.window_key,
            item.window_start,
            item.window_end,
            item.period_count,
            item.valid_ic_count,
            item.undefined_ic_count,
            item.mean_rank_ic,
            item.median_rank_ic,
            item.positive_ic_ratio,
            item.information_ratio,
            item.mean_top_bottom_spread,
            item.non_neutral_rate,
            item.mean_top2_turnover,
            statistics.fmean(
                point.score_dispersion
                for point in period_by_dataset[item.signal_dataset_id]
                if item.window_start <= point.decision_date <= item.window_end
            ),
            statistics.fmean(
                point.mean_confidence
                for point in period_by_dataset[item.signal_dataset_id]
                if item.window_start <= point.decision_date <= item.window_end
            ),
        )
        for item in base.metrics
    )
    pairs = tuple(
        ModelPairDiagnostic(
            item.left_signal_dataset_id,
            item.right_signal_dataset_id,
            item.score_observation_count,
            item.score_spearman,
            item.spread_period_count,
            item.spread_correlation,
            item.mean_top2_overlap,
            item.high_correlation,
        )
        for item in base.pairs
    )
    issues = tuple(
        ModelDiagnosticIssue(
            item.signal_dataset_id,
            item.severity,
            item.issue_code,
            item.message.replace("Signal", "Model"),
            item.details,
        )
        for item in base.issues
    )
    return ModelDiagnostics(periods, metrics, pairs, _ablations(datasets, metrics), issues)


def _value_windows(
    datasets: tuple[ModelEvaluationDataset, ...],
) -> dict[tuple[uuid.UUID, date], tuple[float, float]]:
    grouped: dict[tuple[uuid.UUID, date], list[ModelEvaluationValue]] = defaultdict(list)
    for dataset in datasets:
        for point in dataset.values:
            grouped[(dataset.model_dataset_id, point.observation_date)].append(point)
    return {
        key: (
            statistics.pstdev(point.score for point in values),
            statistics.fmean(point.confidence for point in values),
        )
        for key, values in grouped.items()
    }


def _ablations(
    datasets: tuple[ModelEvaluationDataset, ...],
    metrics: tuple[ModelEvaluationMetric, ...],
) -> tuple[ModelAblationComparison, ...]:
    subset_models = {
        frozenset(item.dimension_keys): item
        for item in datasets
        if item.specification_type == "dimension_subset_equal_weight"
    }
    metric_by_key = {(item.model_dataset_id, item.window_key): item for item in metrics}
    result: list[ModelAblationComparison] = []
    for dimensions, full in sorted(
        subset_models.items(), key=lambda item: (len(item[0]), sorted(item[0]))
    ):
        if len(dimensions) < 2:
            continue
        for removed in sorted(dimensions):
            ablated = subset_models.get(dimensions - {removed})
            if ablated is None:
                raise ValueError("Dimension-subset catalog is incomplete for controlled ablation")
            full_windows = sorted(
                item.window_key
                for item in metrics
                if item.model_dataset_id == full.model_dataset_id
            )
            for window_key in full_windows:
                left = metric_by_key[(full.model_dataset_id, window_key)]
                right = metric_by_key[(ablated.model_dataset_id, window_key)]
                result.append(
                    ModelAblationComparison(
                        full.model_dataset_id,
                        ablated.model_dataset_id,
                        removed,
                        window_key,
                        min(left.period_count, right.period_count),
                        _difference(left.mean_rank_ic, right.mean_rank_ic),
                        _difference(left.information_ratio, right.information_ratio),
                        left.mean_top_bottom_spread - right.mean_top_bottom_spread,
                    )
                )
    return tuple(result)


def _difference(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right
