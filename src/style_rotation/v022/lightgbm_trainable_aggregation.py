from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from math import isfinite

import lightgbm as lgb
import numpy as np

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.trainable_aggregation import (
    VALUE_QUANTUM,
    FeatureSchema,
    FittedRegressionModel,
    TrainableAggregationError,
    TrainingMatrixRow,
)


@dataclass(frozen=True, slots=True)
class _LightGbmParameters:
    n_estimators: int
    learning_rate: float
    num_leaves: int
    max_depth: int
    min_child_samples: int
    subsample: float
    colsample_bytree: float
    reg_alpha: float
    reg_lambda: float


class LightGbmRegressionAdapter:
    """Deterministic CPU LightGBM regression with text model state."""

    adapter_key = "lightgbm_cross_sectional_regression"
    adapter_version = "lightgbm_regressor_cpu_v1"

    def fit(
        self,
        rows: Sequence[TrainingMatrixRow],
        *,
        feature_schema: FeatureSchema,
        seed: int,
        hyperparameters: Mapping[str, object],
    ) -> FittedRegressionModel:
        features, target = _features_and_target(rows, feature_schema)
        return self.fit_dense(
            features,
            target,
            feature_schema=feature_schema,
            seed=seed,
            hyperparameters=hyperparameters,
        )

    def fit_dense(
        self,
        features: np.ndarray,
        target: np.ndarray,
        *,
        feature_schema: FeatureSchema,
        seed: int,
        hyperparameters: Mapping[str, object],
    ) -> FittedRegressionModel:
        """Fit an already validated dense panel without rebuilding it per Fold."""

        parameters = _parameters(hyperparameters)
        if (
            features.ndim != 2
            or features.shape[1] != len(feature_schema.ordered_feature_keys)
            or target.shape != (features.shape[0],)
            or not np.isfinite(features).all()
            or not np.isfinite(target).all()
        ):
            raise TrainableAggregationError("LightGBM dense inputs are malformed")
        estimator = lgb.LGBMRegressor(
            boosting_type="gbdt",
            objective="regression_l2",
            n_estimators=parameters.n_estimators,
            learning_rate=parameters.learning_rate,
            num_leaves=parameters.num_leaves,
            max_depth=parameters.max_depth,
            min_child_samples=parameters.min_child_samples,
            subsample=parameters.subsample,
            subsample_freq=1,
            colsample_bytree=parameters.colsample_bytree,
            reg_alpha=parameters.reg_alpha,
            reg_lambda=parameters.reg_lambda,
            random_state=seed,
            n_jobs=1,
            deterministic=True,
            force_col_wise=True,
            verbosity=-1,
        )
        estimator.fit(features, target)
        booster = estimator.booster_
        model_string = booster.model_to_string(num_iteration=parameters.n_estimators)
        if not model_string.strip():
            raise TrainableAggregationError("LightGBM fitted an empty model")
        document: dict[str, object] = {
            "adapter_key": self.adapter_key,
            "adapter_version": self.adapter_version,
            "lightgbm_version": lgb.__version__,
            "numpy_version": np.__version__,
            "feature_schema_fingerprint": feature_schema.fingerprint,
            "ordered_feature_keys": feature_schema.ordered_feature_keys,
            "hyperparameters": {
                "boosting_type": "gbdt",
                "objective": "regression_l2",
                "n_estimators": parameters.n_estimators,
                "learning_rate": _float_hex(parameters.learning_rate),
                "num_leaves": parameters.num_leaves,
                "max_depth": parameters.max_depth,
                "min_child_samples": parameters.min_child_samples,
                "subsample": _float_hex(parameters.subsample),
                "subsample_freq": 1,
                "colsample_bytree": _float_hex(parameters.colsample_bytree),
                "reg_alpha": _float_hex(parameters.reg_alpha),
                "reg_lambda": _float_hex(parameters.reg_lambda),
                "n_jobs": 1,
                "deterministic": True,
                "force_col_wise": True,
            },
            "seed": seed,
            "feature_importances": [int(value) for value in estimator.feature_importances_],
            "model_string": model_string,
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
        if (
            model.adapter_key != self.adapter_key
            or model.adapter_version != self.adapter_version
        ):
            raise TrainableAggregationError("Fitted LightGBM identity mismatch")
        ordered_keys = model.model_document.get("ordered_feature_keys")
        model_string = model.model_document.get("model_string")
        if not isinstance(ordered_keys, (list, tuple)) or not isinstance(model_string, str):
            raise TrainableAggregationError("Fitted LightGBM document is malformed")
        if any(len(row.feature_values) != len(ordered_keys) for row in rows):
            raise TrainableAggregationError(
                "Prediction row does not match the LightGBM Feature Schema"
            )
        features = np.asarray(
            [[float(value) for value in row.feature_values] for row in rows],
            dtype=np.float64,
        )
        if not np.isfinite(features).all():
            raise TrainableAggregationError("LightGBM prediction inputs must be finite")
        return self.predict_dense(model, features)

    def predict_dense(
        self,
        model: FittedRegressionModel,
        features: np.ndarray,
    ) -> tuple[Decimal, ...]:
        """Predict from an already validated dense panel."""

        if (
            model.adapter_key != self.adapter_key
            or model.adapter_version != self.adapter_version
        ):
            raise TrainableAggregationError("Fitted LightGBM identity mismatch")
        ordered_keys = model.model_document.get("ordered_feature_keys")
        model_string = model.model_document.get("model_string")
        if not isinstance(ordered_keys, (list, tuple)) or not isinstance(model_string, str):
            raise TrainableAggregationError("Fitted LightGBM document is malformed")
        if (
            features.ndim != 2
            or features.shape[1] != len(ordered_keys)
            or not np.isfinite(features).all()
        ):
            raise TrainableAggregationError("LightGBM dense prediction inputs are malformed")
        booster = lgb.Booster(model_str=model_string)
        raw_predictions = booster.predict(features, num_threads=1)
        result = tuple(
            Decimal.from_float(float(value)).quantize(
                VALUE_QUANTUM, rounding=ROUND_HALF_EVEN
            )
            for value in raw_predictions
        )
        if len(result) != features.shape[0] or not all(item.is_finite() for item in result):
            raise TrainableAggregationError("LightGBM prediction output is malformed")
        return result


def _parameters(hyperparameters: Mapping[str, object]) -> _LightGbmParameters:
    required = {
        "n_estimators",
        "learning_rate",
        "num_leaves",
        "max_depth",
        "min_child_samples",
        "subsample",
        "colsample_bytree",
        "reg_alpha",
        "reg_lambda",
    }
    if set(hyperparameters) != required:
        raise TrainableAggregationError(
            "LightGBM requires its exact frozen hyperparameter set"
        )
    n_estimators = _bounded_integer(
        hyperparameters["n_estimators"], "n_estimators", 1, 256
    )
    num_leaves = _bounded_integer(hyperparameters["num_leaves"], "num_leaves", 2, 63)
    max_depth = _bounded_integer(hyperparameters["max_depth"], "max_depth", 2, 12)
    min_child_samples = _bounded_integer(
        hyperparameters["min_child_samples"], "min_child_samples", 5, 2000
    )
    if num_leaves > 2**max_depth:
        raise TrainableAggregationError("LightGBM num_leaves exceeds max_depth capacity")
    return _LightGbmParameters(
        n_estimators=n_estimators,
        learning_rate=_bounded_float(
            hyperparameters["learning_rate"], "learning_rate", 0.001, 0.2
        ),
        num_leaves=num_leaves,
        max_depth=max_depth,
        min_child_samples=min_child_samples,
        subsample=_bounded_float(hyperparameters["subsample"], "subsample", 0.5, 1.0),
        colsample_bytree=_bounded_float(
            hyperparameters["colsample_bytree"], "colsample_bytree", 0.5, 1.0
        ),
        reg_alpha=_bounded_float(hyperparameters["reg_alpha"], "reg_alpha", 0.0, 100.0),
        reg_lambda=_bounded_float(
            hyperparameters["reg_lambda"], "reg_lambda", 0.0, 100.0
        ),
    )


def _bounded_integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise TrainableAggregationError(f"LightGBM {name} is invalid")
    return value


def _bounded_float(value: object, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TrainableAggregationError(f"LightGBM {name} is invalid")
    try:
        scalar = float(value)
    except ValueError as error:
        raise TrainableAggregationError(f"LightGBM {name} is invalid") from error
    if not isfinite(scalar) or not minimum <= scalar <= maximum:
        raise TrainableAggregationError(f"LightGBM {name} is invalid")
    return scalar


def _features_and_target(
    rows: Sequence[TrainingMatrixRow], feature_schema: FeatureSchema
) -> tuple[np.ndarray, np.ndarray]:
    if not rows:
        raise TrainableAggregationError("LightGBM fit requires training rows")
    feature_count = len(feature_schema.ordered_feature_keys)
    if any(len(row.feature_values) != feature_count for row in rows):
        raise TrainableAggregationError("Training row does not match the Feature Schema")
    features = np.asarray(
        [[float(value) for value in row.feature_values] for row in rows], dtype=np.float64
    )
    target = np.asarray([float(row.target_value) for row in rows], dtype=np.float64)
    if not np.isfinite(features).all() or not np.isfinite(target).all():
        raise TrainableAggregationError("LightGBM inputs must be finite")
    return features, target


def _float_hex(value: float) -> str:
    if not isfinite(value):
        raise TrainableAggregationError("LightGBM state contains non-finite values")
    return value.hex()
