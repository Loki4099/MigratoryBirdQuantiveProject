from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from style_rotation.v022.lightgbm_trainable_aggregation import (
    LightGbmRegressionAdapter,
)
from style_rotation.v022.trainable_aggregation import (
    FeatureSchema,
    TrainableAggregationError,
    TrainingMatrixRow,
)


def test_lightgbm_model_state_is_deterministic_text_and_replayable() -> None:
    rows = _rows()
    schema = FeatureSchema(("momentum", "quality"))
    adapter = LightGbmRegressionAdapter()

    first = adapter.fit(rows, feature_schema=schema, seed=1729, hyperparameters=_parameters())
    second = adapter.fit(rows, feature_schema=schema, seed=1729, hyperparameters=_parameters())

    assert first.model_fingerprint == second.model_fingerprint
    assert adapter.predict(first, rows) == adapter.predict(second, rows)
    assert len(adapter.predict(first, rows)) == len(rows)
    assert first.model_document["lightgbm_version"] == "4.7.0"
    model_string = first.model_document["model_string"]
    assert isinstance(model_string, str)
    assert "Tree=" in model_string
    json.dumps(first.model_document)


def test_lightgbm_seed_is_part_of_fitted_identity() -> None:
    rows = _rows()
    schema = FeatureSchema(("momentum", "quality"))
    adapter = LightGbmRegressionAdapter()

    first = adapter.fit(rows, feature_schema=schema, seed=7, hyperparameters=_parameters())
    second = adapter.fit(rows, feature_schema=schema, seed=8, hyperparameters=_parameters())

    assert first.model_fingerprint != second.model_fingerprint


def test_lightgbm_rejects_unpublished_hyperparameters() -> None:
    parameters = _parameters()
    parameters["unknown"] = 1

    with pytest.raises(TrainableAggregationError, match="exact frozen"):
        LightGbmRegressionAdapter().fit(
            _rows(),
            feature_schema=FeatureSchema(("momentum", "quality")),
            seed=0,
            hyperparameters=parameters,
        )


def _parameters() -> dict[str, object]:
    return {
        "n_estimators": 12,
        "learning_rate": "0.05",
        "num_leaves": 7,
        "max_depth": 4,
        "min_child_samples": 5,
        "subsample": "0.8",
        "colsample_bytree": "0.7",
        "reg_alpha": "0",
        "reg_lambda": "1",
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
                Decimal((day_ordinal % 5) - security),
            ),
            target_value=Decimal(((day_ordinal * security) % 9) - 4),
            target_known_at=datetime.combine(day, time(22), tzinfo=UTC),
            target_entry_date=day,
            target_exit_date=day + timedelta(days=5),
        )
        for day_ordinal in range(12)
        for security in range(1, 7)
        for day in (start + timedelta(days=day_ordinal),)
    )
