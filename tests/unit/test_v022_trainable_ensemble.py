from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from style_rotation.v022.linear_trainable_aggregation import OofPredictionPoint
from style_rotation.v022.trainable_aggregation import TrainableAggregationError
from style_rotation.v022.trainable_ensemble import (
    EnsembleMemberPrediction,
    combine_trainable_oof_members,
    compile_trainable_ensemble_spec,
)

DAY = date(2025, 1, 3)
ASSETS = tuple(uuid.UUID(int=value) for value in (1, 2, 3))


def test_compile_spec_freezes_target_groups_and_internal_members() -> None:
    spec = compile_trainable_ensemble_spec(
        "ridge_cross_sectional_regression",
        ("h21", "h5"),
        ("alpha10", "alpha1"),
    )

    assert [
        (item.ordinal, item.target_key, item.training_preset_key)
        for item in spec.members
    ] == [
        (0, "h21", "alpha1"),
        (1, "h21", "alpha10"),
        (2, "h5", "alpha1"),
        (3, "h5", "alpha10"),
    ]
    assert spec.document["member_count"] == 4
    assert spec.document["target_group_count"] == 2
    target_groups = spec.document["target_groups"]
    assert isinstance(target_groups, list)
    assert target_groups[0]["target_group_weight"] == {"numerator": 1, "denominator": 2}
    assert target_groups[0]["members"][0]["within_target_weight"] == {
        "numerator": 1,
        "denominator": 2,
    }
    assert len(spec.fingerprint) == 64


def test_compile_spec_rejects_more_than_twelve_members() -> None:
    with pytest.raises(ValueError, match="at most 12"):
        compile_trainable_ensemble_spec(
            "ridge_cross_sectional_regression",
            ("h5", "h10", "h21", "h42"),
            ("alpha1", "alpha10", "alpha100", "alpha1000"),
        )


def test_two_level_equal_weight_prevents_preset_count_from_overweighting_target() -> None:
    result = combine_trainable_oof_members(
        "ridge_cross_sectional_regression",
        (
            _member("h5", "alpha1", ("1", "0", "-1"), "a"),
            _member("h5", "alpha10", ("1", "-1", "0"), "b"),
            _member("h21", "alpha1", ("-1", "1", "0"), "c"),
        ),
    )

    assert result.ordered_target_keys == ("h21", "h5")
    assert [(item.security_id, item.centered_rank) for item in result.predictions] == [
        (ASSETS[0], Decimal("0E-18")),
        (ASSETS[1], Decimal("1.000000000000000000")),
        (ASSETS[2], Decimal("-1.000000000000000000")),
    ]
    assert len(result.fingerprint) == 64


def test_ensemble_requires_exact_common_oof_panel() -> None:
    incomplete = _member("h10", "alpha1", ("1", "-1", "0"), "d")
    incomplete = EnsembleMemberPrediction(
        incomplete.target_key,
        incomplete.training_preset_key,
        incomplete.prediction_fingerprint,
        incomplete.predictions[:-1],
    )

    with pytest.raises(TrainableAggregationError, match="exact common OOF panel"):
        combine_trainable_oof_members(
            "ridge_cross_sectional_regression",
            (_member("h5", "alpha1", ("1", "0", "-1"), "e"), incomplete),
        )


def test_ensemble_rejects_duplicate_target_preset_coordinate() -> None:
    with pytest.raises(ValueError, match="coordinates must be unique"):
        combine_trainable_oof_members(
            "ridge_cross_sectional_regression",
            (
                _member("h5", "alpha1", ("1", "0", "-1"), "f"),
                _member("h5", "alpha1", ("-1", "0", "1"), "1"),
            ),
        )


def _member(
    target: str,
    preset: str,
    scores: tuple[str, str, str],
    fingerprint_character: str,
) -> EnsembleMemberPrediction:
    return EnsembleMemberPrediction(
        target_key=target,
        training_preset_key=preset,
        prediction_fingerprint=fingerprint_character * 64,
        predictions=tuple(
            OofPredictionPoint(
                security_id=asset_id,
                security_key=f"asset_{ordinal}",
                decision_date=DAY,
                raw_prediction=Decimal(score),
                centered_rank=Decimal(score),
                fold_ordinal=0,
            )
            for ordinal, (asset_id, score) in enumerate(zip(ASSETS, scores, strict=True))
        ),
    )
