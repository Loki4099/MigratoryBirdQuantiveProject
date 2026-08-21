from __future__ import annotations

import math
import statistics
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.linear_trainable_aggregation import OofPredictionPoint
from style_rotation.v022.trainable_aggregation import (
    VALUE_QUANTUM,
    TrainingMatrixRow,
    _average_rank_center,
)
from style_rotation.v022.trainable_ensemble import (
    EnsembleMemberPrediction,
    combine_trainable_oof_members,
)

_METRIC_QUANTUM = Decimal("0.000000000001")


@dataclass(frozen=True, slots=True)
class EnsembleDiagnosticMemberInput:
    target_key: str
    training_preset_key: str
    prediction_fingerprint: str
    fold_count: int
    predictions: tuple[OofPredictionPoint, ...]
    target_rows: tuple[TrainingMatrixRow, ...]


@dataclass(frozen=True, slots=True)
class TrainableEnsembleDiagnostic:
    family_key: str
    ensemble_fingerprint: str | None
    diagnostic_document: dict[str, object]

    @property
    def fingerprint(self) -> str:
        return sha256_hexdigest(self.diagnostic_document)


def calculate_trainable_ensemble_diagnostic(
    family_key: str,
    members: Sequence[EnsembleDiagnosticMemberInput],
    *,
    ensemble_fingerprint: str | None,
) -> TrainableEnsembleDiagnostic:
    """Calculate frozen OOF diagnostics without reading Portfolio outcomes."""

    if not family_key.strip() or not members:
        raise ValueError("Trainable diagnostics require a family and members")
    ordered = tuple(sorted(members, key=lambda item: (item.target_key, item.training_preset_key)))
    coordinates = tuple((item.target_key, item.training_preset_key) for item in ordered)
    if len(coordinates) != len(set(coordinates)):
        raise ValueError("Trainable diagnostic member coordinates must be unique")
    if len(ordered) > 12:
        raise ValueError("Trainable diagnostics support at most 12 members")
    if len(ordered) > 1 and (ensemble_fingerprint is None or len(ensemble_fingerprint) != 64):
        raise ValueError("Multi-member diagnostics require an Ensemble fingerprint")

    prediction_maps = tuple(_prediction_map(item) for item in ordered)
    panels = tuple(frozenset(item) for item in prediction_maps)
    if any(panel != panels[0] for panel in panels[1:]):
        raise ValueError("Trainable diagnostic members require one exact OOF panel")

    target_maps = tuple(_target_map(item, panels[0]) for item in ordered)
    member_documents = [
        {
            "target_key": member.target_key,
            "training_preset_key": member.training_preset_key,
            "prediction_fingerprint": member.prediction_fingerprint,
            "fold_count": member.fold_count,
            "predictive": _predictive_summary(
                _prediction_subset(prediction_maps[index], target_maps[index]),
                target_maps[index],
            ),
        }
        for index, member in enumerate(ordered)
    ]

    target_groups: list[dict[str, object]] = []
    target_keys = tuple(sorted({item.target_key for item in ordered}))
    for target_key in target_keys:
        ordinals = tuple(
            index for index, item in enumerate(ordered) if item.target_key == target_key
        )
        reference_target = target_maps[ordinals[0]]
        if any(target_maps[index] != reference_target for index in ordinals[1:]):
            raise ValueError("Members in one Target group require exact common labels")
        group_scores = _rank_centered_mean(
            tuple(
                _prediction_subset(prediction_maps[index], reference_target)
                for index in ordinals
            )
        )
        full_summary = _predictive_summary(group_scores, reference_target)
        ablations: list[dict[str, object]] = []
        if len(ordinals) > 1:
            full_mean = _metric_decimal(full_summary["mean_rank_ic"])
            for omitted in ordinals:
                reduced = _rank_centered_mean(
                    tuple(
                        _prediction_subset(prediction_maps[index], reference_target)
                        for index in ordinals
                        if index != omitted
                    )
                )
                reduced_summary = _predictive_summary(reduced, reference_target)
                reduced_mean = _metric_decimal(reduced_summary["mean_rank_ic"])
                delta = (
                    None
                    if full_mean is None or reduced_mean is None
                    else _metric_string(full_mean - reduced_mean)
                )
                ablations.append(
                    {
                        "omitted_training_preset_key": ordered[omitted].training_preset_key,
                        "reduced_mean_rank_ic": reduced_summary["mean_rank_ic"],
                        "full_minus_reduced_mean_rank_ic": delta,
                    }
                )
        target_groups.append(
            {
                "target_key": target_key,
                "member_count": len(ordinals),
                "predictive": full_summary,
                "within_target_member_ablations": ablations,
            }
        )

    prediction_members = tuple(
        EnsembleMemberPrediction(
            target_key=item.target_key,
            training_preset_key=item.training_preset_key,
            prediction_fingerprint=item.prediction_fingerprint,
            predictions=item.predictions,
        )
        for item in ordered
    )
    final = combine_trainable_oof_members(family_key, prediction_members)
    final_map = {
        (item.decision_date, item.security_id): item.centered_rank for item in final.predictions
    }
    final_by_target = [
        {
            "target_key": target_key,
            "predictive": _predictive_summary(
                _prediction_subset(
                    final_map,
                    target_maps[
                        next(
                            index
                            for index, item in enumerate(ordered)
                            if item.target_key == target_key
                        )
                    ],
                ),
                target_maps[
                    next(
                        index for index, item in enumerate(ordered) if item.target_key == target_key
                    )
                ],
            ),
        }
        for target_key in target_keys
    ]
    pairwise = [
        {
            "left_target_key": ordered[left].target_key,
            "left_training_preset_key": ordered[left].training_preset_key,
            "right_target_key": ordered[right].target_key,
            "right_training_preset_key": ordered[right].training_preset_key,
            "mean_cross_sectional_rank_correlation": _mean_daily_correlation(
                prediction_maps[left], prediction_maps[right]
            ),
        }
        for left in range(len(ordered))
        for right in range(left + 1, len(ordered))
    ]
    document: dict[str, object] = {
        "contract_version": "v0.22.0",
        "diagnostic_kind": "strict_oof_trainable_ensemble_v1",
        "family_key": family_key,
        "ensemble_fingerprint": ensemble_fingerprint,
        "member_count": len(ordered),
        "target_group_count": len(target_keys),
        "panel_row_count": len(panels[0]),
        "member_diagnostics": member_documents,
        "target_group_diagnostics": target_groups,
        "pairwise_prediction_correlations": pairwise,
        "final_ensemble_by_target": final_by_target,
        "portfolio_ablation_status": "not_computed_requires_separate_frozen_run",
    }
    return TrainableEnsembleDiagnostic(
        family_key=family_key,
        ensemble_fingerprint=ensemble_fingerprint,
        diagnostic_document=document,
    )


Identity = tuple[date, uuid.UUID]


def _prediction_map(
    member: EnsembleDiagnosticMemberInput,
) -> dict[Identity, Decimal]:
    if (
        not member.target_key.strip()
        or not member.training_preset_key.strip()
        or len(member.prediction_fingerprint) != 64
        or member.fold_count < 1
        or not member.predictions
    ):
        raise ValueError("Trainable diagnostic member identity is incomplete")
    result: dict[Identity, Decimal] = {}
    for item in member.predictions:
        identity = (item.decision_date, item.security_id)
        if identity in result:
            raise ValueError("OOF diagnostic predictions must be unique")
        result[identity] = item.centered_rank
    return result


def _target_map(
    member: EnsembleDiagnosticMemberInput,
    panel: frozenset[Identity],
) -> dict[Identity, Decimal]:
    result: dict[Identity, Decimal] = {}
    for row in member.target_rows:
        if not row.target_available:
            continue
        identity = (row.decision_date, row.security_id)
        if identity not in panel:
            continue
        if identity in result:
            raise ValueError("OOF diagnostic Target rows must be unique")
        result[identity] = row.target_value
    if not result or not frozenset(result).issubset(panel):
        raise ValueError("OOF diagnostic Target lacks a valid mature-label panel")
    return result


def _prediction_subset(
    predictions: Mapping[Identity, Decimal],
    targets: Mapping[Identity, Decimal],
) -> dict[Identity, Decimal]:
    result = {identity: predictions[identity] for identity in targets if identity in predictions}
    if frozenset(result) != frozenset(targets):
        raise ValueError("OOF prediction does not cover the mature Target panel")
    return result


def _predictive_summary(
    predictions: Mapping[Identity, Decimal],
    targets: Mapping[Identity, Decimal],
) -> dict[str, object]:
    if frozenset(predictions) != frozenset(targets):
        raise ValueError("Predictive diagnostic panels must match exactly")
    correlations = _daily_correlations(predictions, targets)
    mean = statistics.fmean(correlations) if correlations else None
    median = statistics.median(correlations) if correlations else None
    positive = (
        sum(value > 0 for value in correlations) / len(correlations) if correlations else None
    )
    deviation = statistics.stdev(correlations) if len(correlations) > 1 else None
    ic_ir = None
    if deviation is not None and deviation != 0.0 and mean is not None:
        ic_ir = mean / deviation
    return {
        "row_count": len(predictions),
        "group_count": len({identity[0] for identity in predictions}),
        "defined_ic_group_count": len(correlations),
        "coverage": "1.000000000000",
        "mean_rank_ic": _optional_metric(mean),
        "median_rank_ic": _optional_metric(median),
        "positive_ic_ratio": _optional_metric(positive),
        "ic_ir": _optional_metric(ic_ir),
    }


def _mean_daily_correlation(
    left: Mapping[Identity, Decimal],
    right: Mapping[Identity, Decimal],
) -> str | None:
    if frozenset(left) != frozenset(right):
        raise ValueError("Prediction correlation panels must match exactly")
    values = _daily_correlations(left, right)
    return _optional_metric(statistics.fmean(values) if values else None)


def _daily_correlations(
    left: Mapping[Identity, Decimal],
    right: Mapping[Identity, Decimal],
) -> list[float]:
    identities_by_date: dict[date, list[Identity]] = {}
    for identity in left:
        identities_by_date.setdefault(identity[0], []).append(identity)
    result: list[float] = []
    for decision_date in sorted(identities_by_date):
        identities = sorted(identities_by_date[decision_date], key=lambda item: str(item[1]))
        correlation = _pearson(
            tuple(float(left[item]) for item in identities),
            tuple(float(right[item]) for item in identities),
        )
        if correlation is not None:
            result.append(correlation)
    return result


def _rank_centered_mean(
    members: tuple[Mapping[Identity, Decimal], ...],
) -> dict[Identity, Decimal]:
    if not members:
        raise ValueError("Rank-centered mean requires members")
    panel = frozenset(members[0])
    if any(frozenset(item) != panel for item in members[1:]):
        raise ValueError("Rank-centered mean requires one exact panel")
    identities_by_date: dict[date, list[Identity]] = {}
    for identity in panel:
        identities_by_date.setdefault(identity[0], []).append(identity)
    result: dict[Identity, Decimal] = {}
    for decision_date in sorted(identities_by_date):
        identities = sorted(identities_by_date[decision_date], key=lambda item: str(item[1]))
        ranked = _average_rank_center(
            tuple(
                (
                    identity[1],
                    str(identity[1]),
                    (
                        sum((member[identity] for member in members), Decimal("0")) / len(members)
                    ).quantize(VALUE_QUANTUM, rounding=ROUND_HALF_EVEN),
                )
                for identity in identities
            )
        )
        for identity in identities:
            result[identity] = ranked[identity[1]]
    return result


def _pearson(left: tuple[float, ...], right: tuple[float, ...]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    left_scale = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_scale = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return max(-1.0, min(1.0, numerator / (left_scale * right_scale)))


def _optional_metric(value: float | None) -> str | None:
    if value is None or not math.isfinite(value):
        return None
    return _metric_string(Decimal(str(value)))


def _metric_string(value: Decimal) -> str:
    return format(value.quantize(_METRIC_QUANTUM, rounding=ROUND_HALF_EVEN), "f")


def _metric_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))
