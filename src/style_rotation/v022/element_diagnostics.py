from __future__ import annotations

import math
import statistics
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.forward_return_calculator import ForwardReturnPoint
from style_rotation.v022.aggregation_work_runtime import SignalManifestPoint


@dataclass(frozen=True, slots=True)
class ElementMetric:
    metric_key: str
    value: str | None
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class ElementDiagnostic:
    compiled_feature_occurrence_id: uuid.UUID
    feature_variant_key: str
    stage_no: int
    payload_manifest_id: uuid.UUID
    manifest_artifact_id: uuid.UUID
    manifest_hash: str
    research_direction: Literal["positive", "negative", "unsigned"]
    target_key: str
    target_version_id: uuid.UUID
    target_version_artifact_id: uuid.UUID
    frequency: Literal["weekly", "monthly"]
    coverage_start: date
    coverage_end: date
    expected_observation_count: int
    observed_value_count: int
    missing_value_count: int
    evaluation_period_count: int
    valid_ic_count: int
    metrics: tuple[ElementMetric, ...]

    @property
    def diagnostic_fingerprint(self) -> str:
        return sha256_hexdigest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            **asdict(self),
            "compiled_feature_occurrence_id": str(
                self.compiled_feature_occurrence_id
            ),
            "payload_manifest_id": str(self.payload_manifest_id),
            "manifest_artifact_id": str(self.manifest_artifact_id),
            "target_version_id": str(self.target_version_id),
            "target_version_artifact_id": str(self.target_version_artifact_id),
            "coverage_start": self.coverage_start.isoformat(),
            "coverage_end": self.coverage_end.isoformat(),
            "metrics": [asdict(item) for item in self.metrics],
        }


def calculate_element_diagnostic(
    *,
    compiled_feature_occurrence_id: uuid.UUID,
    feature_variant_key: str,
    stage_no: int,
    payload_manifest_id: uuid.UUID,
    manifest_artifact_id: uuid.UUID,
    manifest_hash: str,
    research_direction: Literal["positive", "negative", "unsigned"],
    target_key: str,
    target_version_id: uuid.UUID,
    target_version_artifact_id: uuid.UUID,
    frequency: Literal["weekly", "monthly"],
    signal_points: tuple[SignalManifestPoint, ...],
    forward_returns: tuple[ForwardReturnPoint, ...],
    candidate_asset_ids: frozenset[uuid.UUID],
    candidate_asset_ids_by_date: Mapping[date, frozenset[uuid.UUID]] | None = None,
    allow_missing_forward_returns: bool = False,
) -> ElementDiagnostic:
    if not feature_variant_key.strip() or not target_key.strip():
        raise ValueError("Element diagnostic identities must be nonblank")
    if stage_no not in {1, 2, 3} or not candidate_asset_ids:
        raise ValueError("Element diagnostic requires a processing stage and candidates")
    if frequency not in {"weekly", "monthly"}:
        raise ValueError("Element diagnostic frequency must be weekly or monthly")
    if research_direction not in {"positive", "negative", "unsigned"}:
        raise ValueError("Element diagnostic research direction is invalid")
    returns = _return_index(
        forward_returns,
        candidate_asset_ids,
        candidate_asset_ids_by_date=candidate_asset_ids_by_date,
        allow_missing=allow_missing_forward_returns,
    )
    values = _signal_index(
        signal_points,
        candidate_asset_ids,
        candidate_asset_ids_by_date=candidate_asset_ids_by_date,
    )
    common_dates = tuple(
        sorted(
            set(values).intersection(
                returns
                if candidate_asset_ids_by_date is None
                else candidate_asset_ids_by_date
            )
        )
    )
    if not common_dates:
        raise ValueError("Element and Evaluation Target have no common decision dates")

    observed: list[float] = []
    rank_ics: list[float] = []
    expected = 0
    observed_targets = 0
    for decision_date in common_dates:
        eligible_asset_ids = (
            candidate_asset_ids
            if candidate_asset_ids_by_date is None
            else candidate_asset_ids_by_date.get(decision_date)
        )
        if not eligible_asset_ids:
            raise ValueError(
                f"Element eligibility mask is empty for {decision_date.isoformat()}"
            )
        if not eligible_asset_ids.issubset(candidate_asset_ids):
            raise ValueError("Element eligibility mask contains an unfrozen candidate")
        expected += len(eligible_asset_ids)
        score_rows = [
            values[decision_date][asset_id]
            for asset_id in sorted(eligible_asset_ids, key=str)
        ]
        present = [
            (
                item.asset_id,
                float(item.signal_value)
                * (-1.0 if research_direction == "negative" else 1.0),
            )
            for item in score_rows
            if item.signal_value is not None
        ]
        observed.extend(value for _, value in present)
        target_rows = returns.get(decision_date, {})
        present_with_targets = [
            (asset_id, value)
            for asset_id, value in present
            if asset_id in target_rows
        ]
        observed_targets += len(target_rows)
        if research_direction == "unsigned" or len(present_with_targets) < 3:
            continue
        scores = [value for _, value in present_with_targets]
        outcomes = [target_rows[asset_id] for asset_id, _ in present_with_targets]
        rank_ic = _spearman(scores, outcomes)
        if rank_ic is not None:
            rank_ics.append(rank_ic)

    metrics = (
        ElementMetric("coverage_ratio", _ratio(len(observed), expected), None),
        ElementMetric("target_coverage_ratio", _ratio(observed_targets, expected), None),
        ElementMetric(
            "target_missing_observation_count",
            str(expected - observed_targets),
            None,
        ),
        *_distribution_metrics(observed),
        *_predictive_metrics(rank_ics, frequency),
    )
    return ElementDiagnostic(
        compiled_feature_occurrence_id,
        feature_variant_key,
        stage_no,
        payload_manifest_id,
        manifest_artifact_id,
        manifest_hash,
        research_direction,
        target_key,
        target_version_id,
        target_version_artifact_id,
        frequency,
        common_dates[0],
        common_dates[-1],
        expected,
        len(observed),
        expected - len(observed),
        len(common_dates),
        len(rank_ics),
        tuple(metrics),
    )


def _signal_index(
    points: tuple[SignalManifestPoint, ...],
    candidate_ids: frozenset[uuid.UUID],
    *,
    candidate_asset_ids_by_date: Mapping[date, frozenset[uuid.UUID]] | None,
) -> dict[date, dict[uuid.UUID, SignalManifestPoint]]:
    result: dict[date, dict[uuid.UUID, SignalManifestPoint]] = defaultdict(dict)
    for point in points:
        if point.asset_id not in candidate_ids:
            raise ValueError("Element Manifest contains an unfrozen candidate")
        if point.asset_id in result[point.decision_date]:
            raise ValueError("Element Manifest contains a duplicate observation")
        if point.signal_value is not None and not point.signal_value.is_finite():
            raise ValueError("Element Manifest contains a non-finite value")
        result[point.decision_date][point.asset_id] = point
    if not result:
        raise ValueError("Element Manifest has no frozen candidate observations")
    if candidate_asset_ids_by_date is None and any(
        set(items) != candidate_ids for items in result.values()
    ):
        raise ValueError("Element Manifest must cover every frozen candidate identity")
    if candidate_asset_ids_by_date is not None:
        for decision_date, expected_ids in candidate_asset_ids_by_date.items():
            if not expected_ids or not expected_ids.issubset(candidate_ids):
                raise ValueError("Element eligibility mask contains an invalid candidate set")
            actual_ids = set(result.get(decision_date, {}))
            if not expected_ids.issubset(actual_ids):
                raise ValueError(
                    "Element Manifest must cover every eligible candidate identity"
                )
    return dict(result)


def _return_index(
    points: tuple[ForwardReturnPoint, ...],
    candidate_ids: frozenset[uuid.UUID],
    *,
    candidate_asset_ids_by_date: Mapping[date, frozenset[uuid.UUID]] | None,
    allow_missing: bool,
) -> dict[date, dict[uuid.UUID, float]]:
    result: dict[date, dict[uuid.UUID, float]] = defaultdict(dict)
    for point in points:
        if point.asset_id not in candidate_ids:
            continue
        if point.asset_id in result[point.decision_date]:
            raise ValueError("Evaluation Target contains a duplicate return")
        value = float(point.forward_return)
        if not math.isfinite(value):
            raise ValueError("Evaluation Target contains a non-finite return")
        result[point.decision_date][point.asset_id] = value
    if not result:
        raise ValueError("Evaluation Target has no candidate returns")
    for decision_date, items in result.items():
        expected_ids = (
            candidate_ids
            if candidate_asset_ids_by_date is None
            else candidate_asset_ids_by_date.get(decision_date)
        )
        if not expected_ids:
            raise ValueError(
                f"Evaluation Target eligibility mask is empty for {decision_date.isoformat()}"
            )
        if not expected_ids.issubset(candidate_ids):
            raise ValueError("Evaluation Target eligibility mask contains an unfrozen candidate")
        item_ids = set(items)
        if not item_ids.issubset(expected_ids):
            raise ValueError("Evaluation Target contains an ineligible candidate identity")
        if not allow_missing and item_ids != expected_ids:
            raise ValueError("Evaluation Target must cover every eligible candidate identity")
    return dict(result)


def _distribution_metrics(values: list[float]) -> tuple[ElementMetric, ...]:
    if not values:
        return tuple(
            ElementMetric(key, None, "no_observed_values")
            for key in ("value_mean", "value_volatility", "value_skewness", "value_excess_kurtosis")
        )
    mean = statistics.fmean(values)
    volatility = statistics.stdev(values) if len(values) > 1 else None
    skewness = _sample_skewness(values)
    kurtosis = _sample_excess_kurtosis(values)
    return (
        ElementMetric("value_mean", _number(mean), None),
        _optional_metric("value_volatility", volatility, "insufficient_observations"),
        _optional_metric("value_skewness", skewness, "insufficient_or_constant_values"),
        _optional_metric(
            "value_excess_kurtosis", kurtosis, "insufficient_or_constant_values"
        ),
    )


def _predictive_metrics(
    rank_ics: list[float], frequency: Literal["weekly", "monthly"]
) -> tuple[ElementMetric, ...]:
    mean = statistics.fmean(rank_ics) if rank_ics else None
    median = statistics.median(rank_ics) if rank_ics else None
    positive = sum(item > 0 for item in rank_ics) / len(rank_ics) if rank_ics else None
    deviation = statistics.stdev(rank_ics) if len(rank_ics) > 1 else None
    information_ratio = (
        mean / deviation * math.sqrt(52 if frequency == "weekly" else 12)
        if mean is not None and deviation is not None and deviation != 0
        else None
    )
    return (
        _optional_metric("mean_rank_ic", mean, "rank_ic_unavailable"),
        _optional_metric("median_rank_ic", median, "rank_ic_unavailable"),
        _optional_metric("positive_ic_ratio", positive, "rank_ic_unavailable"),
        _optional_metric("ic_information_ratio", information_ratio, "ic_variance_unavailable"),
    )


def _sample_skewness(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values)
    if deviation == 0:
        return None
    count = len(values)
    return count / ((count - 1) * (count - 2)) * sum(
        ((item - mean) / deviation) ** 3 for item in values
    )


def _sample_excess_kurtosis(values: list[float]) -> float | None:
    if len(values) < 4:
        return None
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values)
    if deviation == 0:
        return None
    count = len(values)
    standardized_fourth = sum(((item - mean) / deviation) ** 4 for item in values)
    return (
        count * (count + 1) / ((count - 1) * (count - 2) * (count - 3))
        * standardized_fourth
        - 3 * (count - 1) ** 2 / ((count - 2) * (count - 3))
    )


def _spearman(left: list[float], right: list[float]) -> float | None:
    left_rank = _average_ranks(left)
    right_rank = _average_ranks(right)
    left_deviation = statistics.pstdev(left_rank)
    right_deviation = statistics.pstdev(right_rank)
    if left_deviation == 0 or right_deviation == 0:
        return None
    left_mean = statistics.fmean(left_rank)
    right_mean = statistics.fmean(right_rank)
    correlation = statistics.fmean(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left_rank, right_rank, strict=True)
    ) / (left_deviation * right_deviation)
    if math.isclose(abs(correlation), 1, rel_tol=0, abs_tol=1e-15):
        return math.copysign(1, correlation)
    return correlation


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + 1 + end) / 2
        for original, _ in ordered[index:end]:
            ranks[original] = rank
        index = end
    return ranks


def _optional_metric(key: str, value: float | None, reason: str) -> ElementMetric:
    return ElementMetric(
        key,
        _number(value) if value is not None else None,
        None if value is not None else reason,
    )


def _ratio(numerator: int, denominator: int) -> str:
    return _number(numerator / denominator)


def _number(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("Diagnostic metric must be finite")
    return format(Decimal(str(value)), ".18f").rstrip("0").rstrip(".") or "0"
