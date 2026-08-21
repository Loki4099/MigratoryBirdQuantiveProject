from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.linear_trainable_aggregation import OofPredictionPoint
from style_rotation.v022.trainable_aggregation import (
    VALUE_QUANTUM,
    TrainableAggregationError,
    _average_rank_center,
)


@dataclass(frozen=True, slots=True)
class TrainableEnsembleMemberSpec:
    ordinal: int
    target_key: str
    training_preset_key: str
    target_group_ordinal: int
    member_ordinal_within_target: int


@dataclass(frozen=True, slots=True)
class TrainableEnsembleSpec:
    family_key: str
    members: tuple[TrainableEnsembleMemberSpec, ...]
    target_keys: tuple[str, ...]
    training_preset_keys: tuple[str, ...]

    @property
    def document(self) -> dict[str, object]:
        target_member_count = len(self.training_preset_keys)
        return {
            "contract_version": "v0.22.0",
            "family_key": self.family_key,
            "member_policy": "explicit_target_training_cartesian_v1",
            "combination_policy": "equal_within_target_equal_across_targets_v1",
            "missing_member_policy": "fail_closed",
            "member_count": len(self.members),
            "target_group_count": len(self.target_keys),
            "target_groups": [
                {
                    "target_key": target_key,
                    "target_group_ordinal": target_ordinal,
                    "target_group_weight": {
                        "numerator": 1,
                        "denominator": len(self.target_keys),
                    },
                    "members": [
                        {
                            "ordinal": member.ordinal,
                            "member_ordinal_within_target": (
                                member.member_ordinal_within_target
                            ),
                            "training_preset_key": member.training_preset_key,
                            "within_target_weight": {
                                "numerator": 1,
                                "denominator": target_member_count,
                            },
                        }
                        for member in self.members
                        if member.target_key == target_key
                    ],
                }
                for target_ordinal, target_key in enumerate(self.target_keys)
            ],
            "final_transform": "decision_date_cross_section_average_rank_centered",
        }

    @property
    def fingerprint(self) -> str:
        return sha256_hexdigest(self.document)


def compile_trainable_ensemble_spec(
    family_key: str,
    target_keys: Sequence[str],
    training_preset_keys: Sequence[str],
) -> TrainableEnsembleSpec:
    """Freeze the explicitly selected Target x Training-Preset member coordinates."""

    normalized_family = family_key.strip()
    targets = tuple(sorted(target_keys))
    presets = tuple(sorted(training_preset_keys))
    if not normalized_family:
        raise ValueError("Trainable Ensemble family key must be nonempty")
    if not targets or not presets:
        raise ValueError("Trainable Ensemble requires Target and Training Preset axes")
    if len(targets) != len(set(targets)) or len(presets) != len(set(presets)):
        raise ValueError("Trainable Ensemble axes must be unique")
    if any(not item.strip() for item in (*targets, *presets)):
        raise ValueError("Trainable Ensemble axis keys must be nonempty")
    member_count = len(targets) * len(presets)
    if member_count > 12:
        raise ValueError("Trainable Ensemble supports at most 12 internal members")
    members = tuple(
        TrainableEnsembleMemberSpec(
            ordinal=target_ordinal * len(presets) + preset_ordinal,
            target_key=target_key,
            training_preset_key=preset_key,
            target_group_ordinal=target_ordinal,
            member_ordinal_within_target=preset_ordinal,
        )
        for target_ordinal, target_key in enumerate(targets)
        for preset_ordinal, preset_key in enumerate(presets)
    )
    return TrainableEnsembleSpec(
        family_key=normalized_family,
        members=members,
        target_keys=targets,
        training_preset_keys=presets,
    )


@dataclass(frozen=True, slots=True)
class EnsembleMemberPrediction:
    target_key: str
    training_preset_key: str
    prediction_fingerprint: str
    predictions: tuple[OofPredictionPoint, ...]

    def __post_init__(self) -> None:
        if not self.target_key.strip() or not self.training_preset_key.strip():
            raise ValueError("Ensemble member Target and Training Preset must be nonempty")
        if len(self.prediction_fingerprint) != 64:
            raise ValueError("Ensemble member prediction fingerprint must be SHA-256")
        if not self.predictions:
            raise ValueError("Ensemble member predictions cannot be empty")


@dataclass(frozen=True, slots=True)
class EnsemblePredictionPoint:
    security_id: uuid.UUID
    security_key: str
    decision_date: date
    centered_rank: Decimal


@dataclass(frozen=True, slots=True)
class TrainableEnsembleResult:
    family_key: str
    ordered_members: tuple[EnsembleMemberPrediction, ...]
    ordered_target_keys: tuple[str, ...]
    predictions: tuple[EnsemblePredictionPoint, ...]

    @property
    def fingerprint(self) -> str:
        return sha256_hexdigest(
            {
                "family_key": self.family_key,
                "member_policy": "equal_within_target_equal_across_targets_v1",
                "ordered_members": [
                    {
                        "target_key": item.target_key,
                        "training_preset_key": item.training_preset_key,
                        "prediction_fingerprint": item.prediction_fingerprint,
                    }
                    for item in self.ordered_members
                ],
                "ordered_target_keys": self.ordered_target_keys,
                "predictions": self.predictions,
            }
        )


def combine_trainable_oof_members(
    family_key: str,
    members: Sequence[EnsembleMemberPrediction],
) -> TrainableEnsembleResult:
    """Combine exact OOF members using the frozen two-level equal-weight policy."""

    if not family_key.strip():
        raise ValueError("Trainable Ensemble family key must be nonempty")
    ordered_members = tuple(
        sorted(members, key=lambda item: (item.target_key, item.training_preset_key))
    )
    if not 1 <= len(ordered_members) <= 12:
        raise ValueError("Trainable Ensemble requires between 1 and 12 members")
    coordinates = tuple(
        (item.target_key, item.training_preset_key) for item in ordered_members
    )
    if len(coordinates) != len(set(coordinates)):
        raise ValueError("Trainable Ensemble member coordinates must be unique")

    indexed = tuple(_index_member(item) for item in ordered_members)
    expected_panel = frozenset(indexed[0])
    if any(frozenset(item) != expected_panel for item in indexed[1:]):
        raise TrainableAggregationError(
            "Trainable Ensemble members require one exact common OOF panel"
        )
    target_groups: dict[str, list[int]] = {}
    for ordinal, member in enumerate(ordered_members):
        target_groups.setdefault(member.target_key, []).append(ordinal)

    target_scores: dict[str, dict[tuple[date, uuid.UUID], Decimal]] = {}
    for target_key, ordinals in target_groups.items():
        target_scores[target_key] = {
            identity: _equal_mean(
                tuple(indexed[ordinal][identity].centered_rank for ordinal in ordinals)
            )
            for identity in expected_panel
        }

    combined = {
        identity: _equal_mean(
            tuple(target_scores[target_key][identity] for target_key in sorted(target_scores))
        )
        for identity in expected_panel
    }
    identities_by_date: dict[date, list[tuple[date, uuid.UUID]]] = {}
    for identity in expected_panel:
        identities_by_date.setdefault(identity[0], []).append(identity)
    final_scores: dict[tuple[date, uuid.UUID], Decimal] = {}
    for decision_date, identities in identities_by_date.items():
        ranked = _average_rank_center(
            tuple(
                (
                    identity[1],
                    indexed[0][identity].security_key,
                    combined[identity],
                )
                for identity in identities
            )
        )
        for identity in identities:
            final_scores[(decision_date, identity[1])] = ranked[identity[1]]

    predictions = tuple(
        EnsemblePredictionPoint(
            security_id=identity[1],
            security_key=indexed[0][identity].security_key,
            decision_date=identity[0],
            centered_rank=final_scores[identity],
        )
        for identity in sorted(expected_panel, key=lambda item: (item[0], str(item[1])))
    )
    return TrainableEnsembleResult(
        family_key=family_key,
        ordered_members=ordered_members,
        ordered_target_keys=tuple(sorted(target_groups)),
        predictions=predictions,
    )


def _index_member(
    member: EnsembleMemberPrediction,
) -> dict[tuple[date, uuid.UUID], OofPredictionPoint]:
    indexed: dict[tuple[date, uuid.UUID], OofPredictionPoint] = {}
    keys_by_security: dict[uuid.UUID, str] = {}
    for point in member.predictions:
        identity = (point.decision_date, point.security_id)
        if identity in indexed:
            raise TrainableAggregationError(
                "Trainable Ensemble member contains a duplicate OOF observation"
            )
        previous_key = keys_by_security.setdefault(point.security_id, point.security_key)
        if previous_key != point.security_key:
            raise TrainableAggregationError(
                "Trainable Ensemble member changes a frozen Security key"
            )
        if not -Decimal(1) <= point.centered_rank <= Decimal(1):
            raise TrainableAggregationError(
                "Trainable Ensemble member rank is outside [-1,1]"
            )
        indexed[identity] = point
    return indexed


def _equal_mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise AssertionError("Equal-weight group cannot be empty")
    return (sum(values, Decimal()) / Decimal(len(values))).quantize(
        VALUE_QUANTUM, rounding=ROUND_HALF_EVEN
    )
