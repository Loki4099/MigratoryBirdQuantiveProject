from __future__ import annotations

import hashlib
import math
import statistics
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import cast

import numpy as np
import numpy.typing as npt

from style_rotation.core.canonical import canonical_json
from style_rotation.v022.linear_trainable_aggregation import OofPredictionPoint
from style_rotation.v022.trainable_aggregation import (
    VALUE_QUANTUM,
    TrainableAggregationError,
    TrainingMatrixRow,
)
from style_rotation.v022.trainable_ensemble_diagnostics import (
    TrainableEnsembleDiagnostic,
)

_SCALE = 10**18
_METRIC_QUANTUM = Decimal("0.000000000001")


@dataclass(frozen=True, slots=True)
class CompactTrainableMember:
    target_key: str
    training_preset_key: str
    prediction_fingerprint: str
    fold_count: int
    session_ordinals: npt.NDArray[np.int32]
    security_id_bytes: npt.NDArray[np.uint8]
    centered_scores: npt.NDArray[np.int64]
    target_values: npt.NDArray[np.int64]
    target_available: npt.NDArray[np.bool_]

    def __post_init__(self) -> None:
        count = len(self.centered_scores)
        if (
            not self.target_key.strip()
            or not self.training_preset_key.strip()
            or len(self.prediction_fingerprint) != 64
            or self.fold_count < 1
            or count < 1
            or self.session_ordinals.shape != (count,)
            or self.security_id_bytes.shape != (count, 16)
            or self.target_values.shape != (count,)
            or self.target_available.shape != (count,)
        ):
            raise ValueError("Compact trainable member is malformed")


@dataclass(frozen=True, slots=True)
class CompactTrainableEnsembleResult:
    family_key: str
    ordered_members: tuple[CompactTrainableMember, ...]
    ordered_target_keys: tuple[str, ...]
    session_ordinals: npt.NDArray[np.int32]
    security_id_bytes: npt.NDArray[np.uint8]
    centered_scores: npt.NDArray[np.int64]
    fingerprint: str


def compact_member_execution(
    *,
    target_key: str,
    training_preset_key: str,
    prediction_fingerprint: str,
    fold_count: int,
    predictions: tuple[OofPredictionPoint, ...],
    matrix_rows: tuple[TrainingMatrixRow, ...],
) -> CompactTrainableMember:
    """Retain one OOF member without retaining its multi-million-row Matrix."""

    count = len(predictions)
    if count < 1:
        raise TrainableAggregationError("Compact OOF member cannot be empty")
    session_ordinals = np.empty(count, dtype=np.int32)
    security_ids = np.empty((count, 16), dtype=np.uint8)
    scores = np.empty(count, dtype=np.int64)
    targets = np.zeros(count, dtype=np.int64)
    available = np.zeros(count, dtype=np.bool_)

    matrix_index = 0
    prediction_index = 0
    while prediction_index < count:
        prediction = predictions[prediction_index]
        prediction_date = prediction.decision_date
        date_end = prediction_index + 1
        while date_end < count and predictions[date_end].decision_date == prediction_date:
            date_end += 1
        while (
            matrix_index < len(matrix_rows)
            and matrix_rows[matrix_index].decision_date < prediction_date
        ):
            matrix_index += 1
        matrix_end = matrix_index
        target_by_security: dict[uuid.UUID, TrainingMatrixRow] = {}
        while (
            matrix_end < len(matrix_rows)
            and matrix_rows[matrix_end].decision_date == prediction_date
        ):
            row = matrix_rows[matrix_end]
            if row.security_id in target_by_security:
                raise TrainableAggregationError("Training Matrix contains a duplicate identity")
            target_by_security[row.security_id] = row
            matrix_end += 1
        if not target_by_security:
            raise TrainableAggregationError("OOF prediction is absent from its Training Matrix")
        for offset in range(prediction_index, date_end):
            item = predictions[offset]
            security_id = item.security_id
            matched_row = target_by_security.get(security_id)
            if matched_row is None:
                raise TrainableAggregationError(
                    "OOF prediction is absent from its Training Matrix cross-section"
                )
            session_ordinals[offset] = prediction_date.toordinal()
            security_ids[offset, :] = np.frombuffer(security_id.bytes, dtype=np.uint8)
            scores[offset] = _decimal_q18(item.centered_rank)
            available[offset] = matched_row.target_available
            if matched_row.target_available:
                targets[offset] = _decimal_q18(matched_row.target_value)
        matrix_index = matrix_end
        prediction_index = date_end

    return CompactTrainableMember(
        target_key=target_key,
        training_preset_key=training_preset_key,
        prediction_fingerprint=prediction_fingerprint,
        fold_count=fold_count,
        session_ordinals=session_ordinals,
        security_id_bytes=security_ids,
        centered_scores=scores,
        target_values=targets,
        target_available=available,
    )


def combine_compact_trainable_members(
    family_key: str,
    members: tuple[CompactTrainableMember, ...],
    *,
    ensemble_fingerprint: str,
) -> tuple[CompactTrainableEnsembleResult, TrainableEnsembleDiagnostic]:
    if not family_key.strip() or not 2 <= len(members) <= 12:
        raise ValueError("Compact Ensemble requires a family and 2..12 members")
    ordered = tuple(sorted(members, key=lambda item: (item.target_key, item.training_preset_key)))
    coordinates = tuple((item.target_key, item.training_preset_key) for item in ordered)
    if len(coordinates) != len(set(coordinates)) or len(ensemble_fingerprint) != 64:
        raise ValueError("Compact Ensemble member identity is invalid")
    reference = ordered[0]
    for member in ordered[1:]:
        if not (
            np.array_equal(member.session_ordinals, reference.session_ordinals)
            and np.array_equal(member.security_id_bytes, reference.security_id_bytes)
        ):
            raise TrainableAggregationError(
                "Trainable Ensemble members require one exact common OOF panel"
            )

    target_keys = tuple(sorted({item.target_key for item in ordered}))
    target_scores: dict[str, npt.NDArray[np.int64]] = {}
    target_references: dict[str, CompactTrainableMember] = {}
    target_documents: list[dict[str, object]] = []
    member_documents: list[dict[str, object]] = []
    for member in ordered:
        member_documents.append(
            {
                "target_key": member.target_key,
                "training_preset_key": member.training_preset_key,
                "prediction_fingerprint": member.prediction_fingerprint,
                "fold_count": member.fold_count,
                "predictive": _predictive_summary(
                    member.centered_scores,
                    member.target_values,
                    member.target_available,
                    member.session_ordinals,
                ),
            }
        )
    for target_key in target_keys:
        group = tuple(item for item in ordered if item.target_key == target_key)
        reference_target = group[0]
        if any(
            not np.array_equal(item.target_values, reference_target.target_values)
            or not np.array_equal(item.target_available, reference_target.target_available)
            for item in group[1:]
        ):
            raise TrainableAggregationError(
                "Members in one Target group require exact common labels"
            )
        full_scores = _rank_centered(
            _mean_q18(tuple(item.centered_scores for item in group)),
            reference.session_ordinals,
        )
        target_scores[target_key] = full_scores
        target_references[target_key] = reference_target
        full_summary = _predictive_summary(
            full_scores,
            reference_target.target_values,
            reference_target.target_available,
            reference_target.session_ordinals,
        )
        full_mean = _metric_decimal(full_summary["mean_rank_ic"])
        ablations: list[dict[str, object]] = []
        if len(group) > 1:
            for omitted in group:
                reduced_scores = _rank_centered(
                    _mean_q18(tuple(item.centered_scores for item in group if item is not omitted)),
                    reference.session_ordinals,
                )
                reduced_summary = _predictive_summary(
                    reduced_scores,
                    reference_target.target_values,
                    reference_target.target_available,
                    reference_target.session_ordinals,
                )
                reduced_mean = _metric_decimal(reduced_summary["mean_rank_ic"])
                ablations.append(
                    {
                        "omitted_training_preset_key": omitted.training_preset_key,
                        "reduced_mean_rank_ic": reduced_summary["mean_rank_ic"],
                        "full_minus_reduced_mean_rank_ic": (
                            None
                            if full_mean is None or reduced_mean is None
                            else _metric_string(full_mean - reduced_mean)
                        ),
                    }
                )
        target_documents.append(
            {
                "target_key": target_key,
                "member_count": len(group),
                "predictive": full_summary,
                "within_target_member_ablations": ablations,
            }
        )

    final_scores = _rank_centered(
        _mean_q18(tuple(target_scores[key] for key in target_keys)),
        reference.session_ordinals,
    )
    pairwise = [
        {
            "left_target_key": ordered[left].target_key,
            "left_training_preset_key": ordered[left].training_preset_key,
            "right_target_key": ordered[right].target_key,
            "right_training_preset_key": ordered[right].training_preset_key,
            "mean_cross_sectional_rank_correlation": _mean_daily_correlation(
                ordered[left].centered_scores,
                ordered[right].centered_scores,
                reference.session_ordinals,
            ),
        }
        for left in range(len(ordered))
        for right in range(left + 1, len(ordered))
    ]
    final_by_target = [
        {
            "target_key": key,
            "predictive": _predictive_summary(
                final_scores,
                target_references[key].target_values,
                target_references[key].target_available,
                reference.session_ordinals,
            ),
        }
        for key in target_keys
    ]
    diagnostic_document: dict[str, object] = {
        "contract_version": "v0.22.0",
        "diagnostic_kind": "strict_oof_trainable_ensemble_v1",
        "family_key": family_key,
        "ensemble_fingerprint": ensemble_fingerprint,
        "member_count": len(ordered),
        "target_group_count": len(target_keys),
        "panel_row_count": len(final_scores),
        "member_diagnostics": member_documents,
        "target_group_diagnostics": target_documents,
        "pairwise_prediction_correlations": pairwise,
        "final_ensemble_by_target": final_by_target,
        "portfolio_ablation_status": "not_computed_requires_separate_frozen_run",
    }
    fingerprint_digest = hashlib.sha256()
    fingerprint_digest.update(
        canonical_json(
            {
                "family_key": family_key,
                "member_policy": "equal_within_target_equal_across_targets_v2_compact",
                "ordered_members": [
                    {
                        "target_key": item.target_key,
                        "training_preset_key": item.training_preset_key,
                        "prediction_fingerprint": item.prediction_fingerprint,
                    }
                    for item in ordered
                ],
                "ordered_target_keys": target_keys,
                "panel_row_count": len(final_scores),
            }
        ).encode("utf-8")
    )
    fingerprint_digest.update(reference.session_ordinals.tobytes())
    fingerprint_digest.update(reference.security_id_bytes.tobytes())
    fingerprint_digest.update(final_scores.tobytes())
    result = CompactTrainableEnsembleResult(
        family_key=family_key,
        ordered_members=ordered,
        ordered_target_keys=target_keys,
        session_ordinals=reference.session_ordinals,
        security_id_bytes=reference.security_id_bytes,
        centered_scores=final_scores,
        fingerprint=fingerprint_digest.hexdigest(),
    )
    return result, TrainableEnsembleDiagnostic(
        family_key=family_key,
        ensemble_fingerprint=ensemble_fingerprint,
        diagnostic_document=diagnostic_document,
    )


def combine_compact_member_scores(
    family_key: str,
    members: tuple[CompactTrainableMember, ...],
    *,
    ensemble_fingerprint: str,
) -> CompactTrainableEnsembleResult:
    """Combine exact cached OOF scores without recalculating diagnostics.

    A prior published diagnostic remains the authority when a failed final
    publication resumes from immutable OOF payloads.  This function deliberately
    performs only the same two-level score combination and fingerprinting used by
    :func:`combine_compact_trainable_members`.
    """

    if not family_key.strip() or not 2 <= len(members) <= 12:
        raise ValueError("Compact Ensemble requires a family and 2..12 members")
    ordered = tuple(sorted(members, key=lambda item: (item.target_key, item.training_preset_key)))
    coordinates = tuple((item.target_key, item.training_preset_key) for item in ordered)
    if len(coordinates) != len(set(coordinates)) or len(ensemble_fingerprint) != 64:
        raise ValueError("Compact Ensemble member identity is invalid")
    reference = ordered[0]
    for member in ordered[1:]:
        if not (
            np.array_equal(member.session_ordinals, reference.session_ordinals)
            and np.array_equal(member.security_id_bytes, reference.security_id_bytes)
        ):
            raise TrainableAggregationError(
                "Trainable Ensemble members require one exact common OOF panel"
            )

    target_keys = tuple(sorted({item.target_key for item in ordered}))
    target_scores = {
        target_key: _rank_centered(
            _mean_q18(
                tuple(item.centered_scores for item in ordered if item.target_key == target_key)
            ),
            reference.session_ordinals,
        )
        for target_key in target_keys
    }
    final_scores = _rank_centered(
        _mean_q18(tuple(target_scores[key] for key in target_keys)),
        reference.session_ordinals,
    )
    fingerprint_digest = hashlib.sha256()
    fingerprint_digest.update(
        canonical_json(
            {
                "family_key": family_key,
                "member_policy": "equal_within_target_equal_across_targets_v2_compact",
                "ordered_members": [
                    {
                        "target_key": item.target_key,
                        "training_preset_key": item.training_preset_key,
                        "prediction_fingerprint": item.prediction_fingerprint,
                    }
                    for item in ordered
                ],
                "ordered_target_keys": target_keys,
                "panel_row_count": len(final_scores),
            }
        ).encode("utf-8")
    )
    fingerprint_digest.update(reference.session_ordinals.tobytes())
    fingerprint_digest.update(reference.security_id_bytes.tobytes())
    fingerprint_digest.update(final_scores.tobytes())
    return CompactTrainableEnsembleResult(
        family_key=family_key,
        ordered_members=ordered,
        ordered_target_keys=target_keys,
        session_ordinals=reference.session_ordinals,
        security_id_bytes=reference.security_id_bytes,
        centered_scores=final_scores,
        fingerprint=fingerprint_digest.hexdigest(),
    )


def _decimal_q18(value: Decimal) -> int:
    quantized = value.quantize(VALUE_QUANTUM, rounding=ROUND_HALF_EVEN)
    return int(quantized.scaleb(18))


def q18_decimal(value: np.int64 | int) -> Decimal:
    return Decimal(int(value)).scaleb(-18)


def security_uuid(value: npt.NDArray[np.uint8]) -> uuid.UUID:
    return uuid.UUID(bytes=value.tobytes())


def _mean_q18(values: tuple[npt.NDArray[np.int64], ...]) -> npt.NDArray[np.int64]:
    if not values or any(item.shape != values[0].shape for item in values[1:]):
        raise ValueError("Q18 mean requires identically shaped members")
    count = len(values)
    if count <= 9:
        total = np.zeros(values[0].shape, dtype=np.int64)
        for value in values:
            total += value
        quotient = np.floor_divide(total, count)
        remainder = total - quotient * count
        increment = (remainder * 2 > count) | (
            (remainder * 2 == count) & (np.remainder(quotient, 2) != 0)
        )
        return cast(npt.NDArray[np.int64], quotient + increment.astype(np.int64))
    result = np.empty(values[0].shape, dtype=np.int64)
    for index in range(len(result)):
        result[index] = _round_ratio_half_even(sum(int(item[index]) for item in values), count)
    return result


def _rank_centered(
    values: npt.NDArray[np.int64],
    session_ordinals: npt.NDArray[np.int32],
) -> npt.NDArray[np.int64]:
    result = np.empty(values.shape, dtype=np.int64)
    for start, end in _date_ranges(session_ordinals):
        count = end - start
        if count < 2:
            raise TrainableAggregationError("Rank centering requires two securities")
        order = np.argsort(values[start:end], kind="stable")
        sorted_values = values[start:end][order]
        group_start = 0
        while group_start < count:
            group_end = group_start + 1
            while group_end < count and sorted_values[group_end] == sorted_values[group_start]:
                group_end += 1
            centered = _round_ratio_half_even(
                (group_start + group_end - count) * _SCALE,
                count - 1,
            )
            result[start + order[group_start:group_end]] = centered
            group_start = group_end
    return result


def _round_ratio_half_even(numerator: int, denominator: int) -> int:
    quotient, remainder = divmod(numerator, denominator)
    doubled = remainder * 2
    if doubled > denominator or (doubled == denominator and quotient % 2 != 0):
        quotient += 1
    return quotient


def _date_ranges(values: npt.NDArray[np.int32]) -> tuple[tuple[int, int], ...]:
    if len(values) < 1 or np.any(values[1:] < values[:-1]):
        raise TrainableAggregationError("Compact OOF sessions are not canonical")
    boundaries = np.flatnonzero(values[1:] != values[:-1]) + 1
    starts = np.concatenate((np.asarray([0]), boundaries))
    ends = np.concatenate((boundaries, np.asarray([len(values)])))
    return tuple((int(start), int(end)) for start, end in zip(starts, ends, strict=True))


def _predictive_summary(
    predictions: npt.NDArray[np.int64],
    targets: npt.NDArray[np.int64],
    available: npt.NDArray[np.bool_],
    dates: npt.NDArray[np.int32],
) -> dict[str, object]:
    correlations = _daily_correlations(predictions, targets, dates, available)
    mean = statistics.fmean(correlations) if correlations else None
    median = statistics.median(correlations) if correlations else None
    positive = (
        sum(value > 0 for value in correlations) / len(correlations) if correlations else None
    )
    deviation = statistics.stdev(correlations) if len(correlations) > 1 else None
    return {
        "row_count": int(np.count_nonzero(available)),
        "group_count": len({int(value) for value in dates[available]}),
        "defined_ic_group_count": len(correlations),
        "coverage": "1.000000000000",
        "mean_rank_ic": _optional_metric(mean),
        "median_rank_ic": _optional_metric(median),
        "positive_ic_ratio": _optional_metric(positive),
        "ic_ir": _optional_metric(
            None if deviation is None or deviation == 0.0 or mean is None else mean / deviation
        ),
    }


def _mean_daily_correlation(
    left: npt.NDArray[np.int64],
    right: npt.NDArray[np.int64],
    dates: npt.NDArray[np.int32],
) -> str | None:
    values = _daily_correlations(
        left,
        right,
        dates,
        np.ones(len(left), dtype=np.bool_),
    )
    return _optional_metric(statistics.fmean(values) if values else None)


def _daily_correlations(
    left: npt.NDArray[np.int64],
    right: npt.NDArray[np.int64],
    dates: npt.NDArray[np.int32],
    available: npt.NDArray[np.bool_],
) -> list[float]:
    result: list[float] = []
    for start, end in _date_ranges(dates):
        mask = available[start:end]
        if np.count_nonzero(mask) < 2:
            continue
        left_values = left[start:end][mask].astype(np.float64)
        right_values = right[start:end][mask].astype(np.float64)
        left_values -= left_values.mean()
        right_values -= right_values.mean()
        scale = math.sqrt(
            float(np.dot(left_values, left_values)) * float(np.dot(right_values, right_values))
        )
        if scale != 0.0:
            result.append(max(-1.0, min(1.0, float(np.dot(left_values, right_values)) / scale)))
    return result


def _optional_metric(value: float | None) -> str | None:
    if value is None or not math.isfinite(value):
        return None
    return _metric_string(Decimal(str(value)))


def _metric_string(value: Decimal) -> str:
    return format(value.quantize(_METRIC_QUANTUM, rounding=ROUND_HALF_EVEN), "f")


def _metric_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))
