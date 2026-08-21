from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from style_rotation.v022.linear_trainable_aggregation import (
    OrdinaryLeastSquaresAdapter,
    RidgeRegressionAdapter,
    run_strict_oof_predictions,
)
from style_rotation.v022.trainable_aggregation import (
    FeatureSchema,
    FixedSessionTarget,
    TrainableAggregationError,
    TrainingMatrix,
    TrainingMatrixRow,
    WalkForwardFold,
)


def test_ols_recovers_linear_relation_and_is_reproducible() -> None:
    schema = FeatureSchema(("x", "z"))
    rows = tuple(
        _row(index, Decimal(index), Decimal(index % 3), Decimal(2 * index - (index % 3) + 1))
        for index in range(1, 9)
    )
    adapter = OrdinaryLeastSquaresAdapter()

    first = adapter.fit(rows, feature_schema=schema, seed=7, hyperparameters={})
    second = adapter.fit(rows, feature_schema=schema, seed=7, hyperparameters={})
    predictions = adapter.predict(first, rows)

    assert first.model_fingerprint == second.model_fingerprint
    assert all(
        abs(prediction - row.target_value) < Decimal("1e-12")
        for prediction, row in zip(predictions, rows, strict=True)
    )
    assert abs(float.fromhex(first.model_document["intercept"]) - 1.0) < 1e-12


def test_ridge_shrinks_coefficients_without_penalizing_intercept() -> None:
    schema = FeatureSchema(("x",))
    rows = tuple(
        _row(index, Decimal(index), Decimal(0), Decimal(10 + 3 * index), one_feature=True)
        for index in range(-4, 5)
    )
    ols = OrdinaryLeastSquaresAdapter().fit(
        rows, feature_schema=schema, seed=0, hyperparameters={}
    )
    ridge = RidgeRegressionAdapter().fit(
        rows, feature_schema=schema, seed=0, hyperparameters={"alpha": Decimal("100")}
    )

    ols_coefficient = float.fromhex(ols.model_document["coefficients"][0])
    ridge_coefficient = float.fromhex(ridge.model_document["coefficients"][0])
    assert abs(ridge_coefficient) < abs(ols_coefficient)
    assert float.fromhex(ridge.model_document["intercept"]) == 10.0


def test_strict_oof_fits_only_train_rows_and_rank_centers_each_prediction_date() -> None:
    start = date(2020, 1, 1)
    dates = tuple(start + timedelta(days=index) for index in range(8))
    schema = FeatureSchema(("x",))
    rows = tuple(
        _dated_row(day, security, Decimal(security), Decimal(security))
        for day in dates
        for security in (1, 2, 3)
    )
    matrix = TrainingMatrix(
        schema,
        FixedSessionTarget("forward_rank_h5", 5),
        rows,
        dates,
    )
    folds = (
        WalkForwardFold(0, dates[:4], dates[4:5], dates[5:7], "a" * 64),
        WalkForwardFold(1, dates[:6], dates[6:7], dates[7:8], "b" * 64),
    )

    result = run_strict_oof_predictions(
        OrdinaryLeastSquaresAdapter(), matrix, folds, hyperparameters={}, seed=11
    )

    assert {point.decision_date for point in result.predictions} == set(dates[5:])
    assert result.fitted_folds[0].train_row_count == 12
    assert result.fitted_folds[0].prediction_row_count == 6
    for day in dates[5:]:
        assert {
            point.security_key: point.centered_rank
            for point in result.predictions
            if point.decision_date == day
        } == {
            "s1": Decimal("-1.000000000000000000"),
            "s2": Decimal("0E-18"),
            "s3": Decimal("1.000000000000000000"),
        }
    assert len(result.fingerprint) == 64


def test_strict_oof_rejects_a_training_label_not_known_before_validation() -> None:
    start = date(2020, 1, 1)
    dates = tuple(start + timedelta(days=index) for index in range(4))
    schema = FeatureSchema(("x",))
    rows = list(
        _dated_row(day, security, Decimal(security), Decimal(security))
        for day in dates
        for security in (1, 2)
    )
    rows[0] = replace(
        rows[0],
        target_known_at=datetime.combine(dates[3], time(22), tzinfo=UTC),
    )
    matrix = TrainingMatrix(
        schema,
        FixedSessionTarget("forward_rank_h5", 5),
        tuple(rows),
        dates,
    )
    fold = WalkForwardFold(0, dates[:2], dates[2:3], dates[3:], "c" * 64)

    with pytest.raises(TrainableAggregationError, match="unavailable at validation"):
        run_strict_oof_predictions(
            OrdinaryLeastSquaresAdapter(), matrix, (fold,), hyperparameters={}, seed=0
        )


def _row(
    index: int,
    x: Decimal,
    z: Decimal,
    target: Decimal,
    *,
    one_feature: bool = False,
) -> TrainingMatrixRow:
    return _dated_row(
        date(2020, 1, 1) + timedelta(days=index + 5),
        index + 20,
        x,
        target,
        z=z,
        one_feature=one_feature,
    )


def _dated_row(
    day: date,
    security: int,
    x: Decimal,
    target: Decimal,
    *,
    z: Decimal = Decimal(0),
    one_feature: bool = True,
) -> TrainingMatrixRow:
    cutoff = datetime.combine(day, time(21), tzinfo=UTC)
    return TrainingMatrixRow(
        security_id=uuid.UUID(int=security),
        security_key=f"s{security}",
        decision_date=day,
        decision_cutoff_at=cutoff,
        feature_values=(x,) if one_feature else (x, z),
        target_value=target,
        target_known_at=cutoff + timedelta(hours=1),
        target_entry_date=day,
        target_exit_date=day + timedelta(days=1),
    )
