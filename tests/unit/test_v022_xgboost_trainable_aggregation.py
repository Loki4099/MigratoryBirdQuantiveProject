from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
import xgboost

from style_rotation.v022.trainable_aggregation import (
    FeatureSchema,
    TrainableAggregationError,
    TrainingMatrixRow,
)
from style_rotation.v022.xgboost_trainable_aggregation import (
    XgBoostRegressionAdapter,
)


def test_xgboost_model_state_is_deterministic_json_and_replayable() -> None:
    rows = _rows()
    schema = FeatureSchema(("momentum", "quality"))
    adapter = XgBoostRegressionAdapter()

    first = adapter.fit(rows, feature_schema=schema, seed=1729, hyperparameters=_parameters())
    second = adapter.fit(rows, feature_schema=schema, seed=1729, hyperparameters=_parameters())

    assert first.model_fingerprint == second.model_fingerprint
    assert adapter.predict(first, rows) == adapter.predict(second, rows)
    assert len(adapter.predict(first, rows)) == len(rows)
    assert first.model_document["xgboost_version"] == xgboost.__version__
    model_json = first.model_document["model_json"]
    assert isinstance(model_json, str)
    assert json.loads(model_json)["learner"]
    json.dumps(first.model_document)


def test_xgboost_seed_is_part_of_fitted_identity() -> None:
    rows = _rows()
    schema = FeatureSchema(("momentum", "quality"))
    adapter = XgBoostRegressionAdapter()

    first = adapter.fit(rows, feature_schema=schema, seed=7, hyperparameters=_parameters())
    second = adapter.fit(rows, feature_schema=schema, seed=8, hyperparameters=_parameters())

    assert first.model_fingerprint != second.model_fingerprint


def test_xgboost_rejects_unpublished_hyperparameters() -> None:
    parameters = _parameters()
    parameters["unknown"] = 1

    with pytest.raises(TrainableAggregationError, match="exact frozen"):
        XgBoostRegressionAdapter().fit(
            _rows(),
            feature_schema=FeatureSchema(("momentum", "quality")),
            seed=0,
            hyperparameters=parameters,
        )


def _parameters() -> dict[str, object]:
    return {
        "n_estimators": 12,
        "learning_rate": "0.05",
        "max_depth": 4,
        "min_child_weight": "5",
        "subsample": "0.8",
        "colsample_bytree": "0.7",
        "reg_alpha": "0",
        "reg_lambda": "1",
        "gamma": "0",
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
