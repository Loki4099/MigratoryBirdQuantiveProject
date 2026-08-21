from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sklearn.ensemble import RandomForestRegressor

from style_rotation.v022.trainable_aggregation import (
    FeatureSchema,
    TrainableAggregationError,
    TrainingMatrixRow,
)
from style_rotation.v022.tree_trainable_aggregation import (
    RandomForestRegressionAdapter,
)


def test_random_forest_model_state_is_deterministic_json_and_predictable() -> None:
    schema = FeatureSchema(("momentum", "quality"))
    rows = _rows()
    adapter = RandomForestRegressionAdapter()
    parameters = _parameters()

    first = adapter.fit(rows, feature_schema=schema, seed=1729, hyperparameters=parameters)
    second = adapter.fit(rows, feature_schema=schema, seed=1729, hyperparameters=parameters)
    predictions = adapter.predict(first, rows)

    assert first.model_fingerprint == second.model_fingerprint
    assert predictions == adapter.predict(second, rows)
    assert len(predictions) == len(rows)
    assert all(prediction.is_finite() for prediction in predictions)
    assert first.model_document["seed"] == 1729
    assert first.model_document["sklearn_version"]
    trees = first.model_document["trees"]
    assert isinstance(trees, (list, tuple))
    assert len(trees) == parameters["n_estimators"]
    json.dumps(first.model_document)

    reference = RandomForestRegressor(
        n_estimators=8,
        max_depth=4,
        min_samples_leaf=2,
        max_features="sqrt",
        bootstrap=True,
        max_samples=0.75,
        criterion="squared_error",
        random_state=1729,
        n_jobs=1,
    )
    reference.fit(
        [[float(value) for value in row.feature_values] for row in rows],
        [float(row.target_value) for row in rows],
    )
    reference_predictions = tuple(
        Decimal.from_float(float(value)).quantize(Decimal("1e-18"))
        for value in reference.predict(
            [[float(item) for item in row.feature_values] for row in rows]
        )
    )
    assert all(
        abs(actual - expected) <= Decimal("1e-15")
        for actual, expected in zip(predictions, reference_predictions, strict=True)
    )


def test_random_forest_seed_is_part_of_fitted_identity() -> None:
    schema = FeatureSchema(("momentum", "quality"))
    rows = _rows()
    adapter = RandomForestRegressionAdapter()

    first = adapter.fit(rows, feature_schema=schema, seed=7, hyperparameters=_parameters())
    second = adapter.fit(rows, feature_schema=schema, seed=8, hyperparameters=_parameters())

    assert first.model_fingerprint != second.model_fingerprint


def test_random_forest_rejects_unpublished_hyperparameters() -> None:
    parameters = _parameters()
    parameters["unknown"] = 1

    with pytest.raises(TrainableAggregationError, match="exact frozen"):
        RandomForestRegressionAdapter().fit(
            _rows(),
            feature_schema=FeatureSchema(("momentum", "quality")),
            seed=0,
            hyperparameters=parameters,
        )


def _parameters() -> dict[str, object]:
    return {
        "n_estimators": 8,
        "max_depth": 4,
        "min_samples_leaf": 2,
        "max_features": "sqrt",
        "max_samples": "0.75",
    }


def _rows() -> tuple[TrainingMatrixRow, ...]:
    start = date(2020, 1, 2)
    return tuple(
        TrainingMatrixRow(
            security_id=uuid.UUID(int=security),
            security_key=f"s{security}",
            decision_date=day,
            decision_cutoff_at=datetime.combine(day, time(21), tzinfo=UTC),
            feature_values=(
                Decimal(day_ordinal + security),
                Decimal((day_ordinal % 3) - security),
            ),
            target_value=Decimal((day_ordinal + security) % 5 - 2),
            target_known_at=datetime.combine(day, time(22), tzinfo=UTC),
            target_entry_date=day,
            target_exit_date=day + timedelta(days=5),
        )
        for day_ordinal in range(8)
        for security in range(1, 5)
        for day in (start + timedelta(days=day_ordinal),)
    )
