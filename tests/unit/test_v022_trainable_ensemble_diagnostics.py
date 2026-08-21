from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from style_rotation.v022.linear_trainable_aggregation import OofPredictionPoint
from style_rotation.v022.trainable_aggregation import TrainingMatrixRow
from style_rotation.v022.trainable_ensemble_diagnostics import (
    EnsembleDiagnosticMemberInput,
    calculate_trainable_ensemble_diagnostic,
)

ASSETS = tuple((uuid.uuid4(), key) for key in ("A", "B", "C"))
DAYS = (date(2020, 1, 2), date(2020, 1, 3))


def _member(
    target_key: str,
    preset_key: str,
    orders: tuple[tuple[int, int, int], tuple[int, int, int]],
) -> EnsembleDiagnosticMemberInput:
    predictions = tuple(
        OofPredictionPoint(
            security_id=ASSETS[position][0],
            security_key=ASSETS[position][1],
            decision_date=day,
            raw_prediction=Decimal(rank),
            centered_rank=Decimal(rank - 1),
            fold_ordinal=day_ordinal,
        )
        for day_ordinal, (day, order) in enumerate(zip(DAYS, orders, strict=True))
        for rank, position in enumerate(order)
    )
    rows = tuple(
        TrainingMatrixRow(
            security_id=ASSETS[position][0],
            security_key=ASSETS[position][1],
            decision_date=day,
            decision_cutoff_at=datetime(2020, 1, 1, tzinfo=UTC),
            feature_values=(Decimal("0"),),
            target_value=Decimal(rank - 1),
            target_known_at=datetime(2020, 2, 1, tzinfo=UTC),
            target_entry_date=day,
            target_exit_date=date(2020, 2, 1),
        )
        for day, order in zip(DAYS, ((0, 1, 2), (2, 1, 0)), strict=True)
        for rank, position in enumerate(order)
    )
    return EnsembleDiagnosticMemberInput(
        target_key=target_key,
        training_preset_key=preset_key,
        prediction_fingerprint=(preset_key[0] * 64),
        fold_count=2,
        predictions=predictions,
        target_rows=rows,
    )


def test_ensemble_diagnostic_reports_members_correlations_and_target_ablations() -> None:
    diagnostic = calculate_trainable_ensemble_diagnostic(
        "ridge_regression",
        (
            _member("forward_rank_h5", "alpha", ((0, 1, 2), (2, 1, 0))),
            _member("forward_rank_h5", "beta", ((0, 2, 1), (2, 0, 1))),
            _member("forward_rank_h21", "gamma", ((2, 1, 0), (0, 1, 2))),
        ),
        ensemble_fingerprint="e" * 64,
    )

    document = diagnostic.diagnostic_document
    assert document["member_count"] == 3
    assert document["target_group_count"] == 2
    assert len(document["member_diagnostics"]) == 3  # type: ignore[arg-type]
    assert len(document["pairwise_prediction_correlations"]) == 3  # type: ignore[arg-type]
    groups = document["target_group_diagnostics"]
    assert isinstance(groups, list)
    h5 = next(item for item in groups if item["target_key"] == "forward_rank_h5")
    assert len(h5["within_target_member_ablations"]) == 2
    assert document["portfolio_ablation_status"] == ("not_computed_requires_separate_frozen_run")
    assert len(diagnostic.fingerprint) == 64


def test_ensemble_diagnostic_rejects_mismatched_oof_panels() -> None:
    first = _member("forward_rank_h5", "alpha", ((0, 1, 2), (2, 1, 0)))
    second = _member("forward_rank_h5", "beta", ((0, 1, 2), (2, 1, 0)))
    shortened = EnsembleDiagnosticMemberInput(
        target_key=second.target_key,
        training_preset_key=second.training_preset_key,
        prediction_fingerprint=second.prediction_fingerprint,
        fold_count=second.fold_count,
        predictions=second.predictions[:-1],
        target_rows=second.target_rows,
    )

    with pytest.raises(ValueError, match="exact OOF panel"):
        calculate_trainable_ensemble_diagnostic(
            "ridge_regression",
            (first, shortened),
            ensemble_fingerprint="e" * 64,
        )
