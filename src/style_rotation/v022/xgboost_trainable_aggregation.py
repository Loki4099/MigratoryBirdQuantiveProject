from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from math import isfinite

import numpy as np
import xgboost as xgb

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.trainable_aggregation import (
    VALUE_QUANTUM,
    FeatureSchema,
    FittedRegressionModel,
    TrainableAggregationError,
    TrainingMatrixRow,
)


@dataclass(frozen=True, slots=True)
class _XgBoostParameters:
    n_estimators: int
    learning_rate: float
    max_depth: int
    min_child_weight: float
    subsample: float
    colsample_bytree: float
    reg_alpha: float
    reg_lambda: float
    gamma: float


class XgBoostRegressionAdapter:
    """Deterministic single-threaded CPU XGBoost regression."""

    adapter_key = "xgboost_cross_sectional_regression"
    adapter_version = "xgboost_regressor_cpu_hist_v1"

    def fit(
        self,
        rows: Sequence[TrainingMatrixRow],
        *,
        feature_schema: FeatureSchema,
        seed: int,
        hyperparameters: Mapping[str, object],
    ) -> FittedRegressionModel:
        parameters = _parameters(hyperparameters)
        features, target = _features_and_target(rows, feature_schema)
        estimator = xgb.XGBRegressor(
            booster="gbtree",
            objective="reg:squarederror",
            tree_method="hist",
            device="cpu",
            n_estimators=parameters.n_estimators,
            learning_rate=parameters.learning_rate,
            max_depth=parameters.max_depth,
            min_child_weight=parameters.min_child_weight,
            subsample=parameters.subsample,
            sampling_method="uniform",
            colsample_bytree=parameters.colsample_bytree,
            reg_alpha=parameters.reg_alpha,
            reg_lambda=parameters.reg_lambda,
            gamma=parameters.gamma,
            random_state=seed,
            n_jobs=1,
            validate_parameters=True,
            eval_metric="rmse",
            verbosity=0,
        )
        estimator.fit(features, target, verbose=False)
        booster = estimator.get_booster()
        model_json = bytes(booster.save_raw(raw_format="json")).decode("utf-8")
        if not model_json.strip():
            raise TrainableAggregationError("XGBoost fitted an empty model")
        document: dict[str, object] = {
            "adapter_key": self.adapter_key,
            "adapter_version": self.adapter_version,
            "xgboost_version": xgb.__version__,
            "numpy_version": np.__version__,
            "feature_schema_fingerprint": feature_schema.fingerprint,
            "ordered_feature_keys": feature_schema.ordered_feature_keys,
            "hyperparameters": {
                "booster": "gbtree",
                "objective": "reg:squarederror",
                "tree_method": "hist",
                "device": "cpu",
                "n_estimators": parameters.n_estimators,
                "learning_rate": _float_hex(parameters.learning_rate),
                "max_depth": parameters.max_depth,
                "min_child_weight": _float_hex(parameters.min_child_weight),
                "subsample": _float_hex(parameters.subsample),
                "sampling_method": "uniform",
                "colsample_bytree": _float_hex(parameters.colsample_bytree),
                "reg_alpha": _float_hex(parameters.reg_alpha),
                "reg_lambda": _float_hex(parameters.reg_lambda),
                "gamma": _float_hex(parameters.gamma),
                "n_jobs": 1,
            },
            "seed": seed,
            "feature_importance_gain": {
                key: _importance_hex(value)
                for key, value in sorted(booster.get_score(importance_type="gain").items())
            },
            "model_json": model_json,
        }
        return FittedRegressionModel(
            adapter_key=self.adapter_key,
            adapter_version=self.adapter_version,
            feature_schema_fingerprint=feature_schema.fingerprint,
            model_document=document,
            model_fingerprint=sha256_hexdigest(document),
        )

    def predict(
        self,
        model: FittedRegressionModel,
        rows: Sequence[TrainingMatrixRow],
    ) -> tuple[Decimal, ...]:
        if model.adapter_key != self.adapter_key or model.adapter_version != self.adapter_version:
            raise TrainableAggregationError("Fitted XGBoost identity mismatch")
        ordered_keys = model.model_document.get("ordered_feature_keys")
        model_json = model.model_document.get("model_json")
        if not isinstance(ordered_keys, (list, tuple)) or not isinstance(model_json, str):
            raise TrainableAggregationError("Fitted XGBoost document is malformed")
        if any(len(row.feature_values) != len(ordered_keys) for row in rows):
            raise TrainableAggregationError(
                "Prediction row does not match the XGBoost Feature Schema"
            )
        features = np.asarray(
            [[float(value) for value in row.feature_values] for row in rows],
            dtype=np.float64,
        )
        if not np.isfinite(features).all():
            raise TrainableAggregationError("XGBoost prediction inputs must be finite")
        booster = xgb.Booster(params={"device": "cpu", "nthread": 1})
        booster.load_model(bytearray(model_json, "utf-8"))
        raw_predictions = booster.predict(xgb.DMatrix(features, nthread=1))
        result = tuple(
            Decimal.from_float(float(value)).quantize(VALUE_QUANTUM, rounding=ROUND_HALF_EVEN)
            for value in raw_predictions
        )
        if len(result) != len(rows) or not all(item.is_finite() for item in result):
            raise TrainableAggregationError("XGBoost prediction output is malformed")
        return result


def _parameters(hyperparameters: Mapping[str, object]) -> _XgBoostParameters:
    required = {
        "n_estimators",
        "learning_rate",
        "max_depth",
        "min_child_weight",
        "subsample",
        "colsample_bytree",
        "reg_alpha",
        "reg_lambda",
        "gamma",
    }
    if set(hyperparameters) != required:
        raise TrainableAggregationError("XGBoost requires its exact frozen hyperparameter set")
    return _XgBoostParameters(
        n_estimators=_bounded_integer(hyperparameters["n_estimators"], "n_estimators", 1, 256),
        learning_rate=_bounded_float(hyperparameters["learning_rate"], "learning_rate", 0.001, 0.2),
        max_depth=_bounded_integer(hyperparameters["max_depth"], "max_depth", 2, 12),
        min_child_weight=_bounded_float(
            hyperparameters["min_child_weight"], "min_child_weight", 0.0, 10_000.0
        ),
        subsample=_bounded_float(hyperparameters["subsample"], "subsample", 0.5, 1.0),
        colsample_bytree=_bounded_float(
            hyperparameters["colsample_bytree"], "colsample_bytree", 0.5, 1.0
        ),
        reg_alpha=_bounded_float(hyperparameters["reg_alpha"], "reg_alpha", 0.0, 100.0),
        reg_lambda=_bounded_float(hyperparameters["reg_lambda"], "reg_lambda", 0.0, 100.0),
        gamma=_bounded_float(hyperparameters["gamma"], "gamma", 0.0, 100.0),
    )


def _bounded_integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise TrainableAggregationError(f"XGBoost {name} is invalid")
    return value


def _bounded_float(value: object, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TrainableAggregationError(f"XGBoost {name} is invalid")
    try:
        scalar = float(value)
    except ValueError as error:
        raise TrainableAggregationError(f"XGBoost {name} is invalid") from error
    if not isfinite(scalar) or not minimum <= scalar <= maximum:
        raise TrainableAggregationError(f"XGBoost {name} is invalid")
    return scalar


def _features_and_target(
    rows: Sequence[TrainingMatrixRow], feature_schema: FeatureSchema
) -> tuple[np.ndarray, np.ndarray]:
    if not rows:
        raise TrainableAggregationError("XGBoost fit requires training rows")
    feature_count = len(feature_schema.ordered_feature_keys)
    if any(len(row.feature_values) != feature_count for row in rows):
        raise TrainableAggregationError("Training row does not match the Feature Schema")
    features = np.asarray(
        [[float(value) for value in row.feature_values] for row in rows],
        dtype=np.float64,
    )
    target = np.asarray([float(row.target_value) for row in rows], dtype=np.float64)
    if not np.isfinite(features).all() or not np.isfinite(target).all():
        raise TrainableAggregationError("XGBoost inputs must be finite")
    return features, target


def _float_hex(value: float) -> str:
    if not isfinite(value):
        raise TrainableAggregationError("XGBoost state contains non-finite values")
    return value.hex()


def _importance_hex(value: float | list[float]) -> str:
    if isinstance(value, list):
        raise TrainableAggregationError("XGBoost regression feature importance is not scalar")
    return _float_hex(value)
