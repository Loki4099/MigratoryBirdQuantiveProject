from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from math import isfinite
from typing import Protocol, cast

import numpy as np
import sklearn
from sklearn.ensemble import RandomForestRegressor

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.trainable_aggregation import (
    VALUE_QUANTUM,
    FeatureSchema,
    FittedRegressionModel,
    TrainableAggregationError,
    TrainingMatrixRow,
)


@dataclass(frozen=True, slots=True)
class _RandomForestParameters:
    n_estimators: int
    max_depth: int
    min_samples_leaf: int
    max_features: str | float
    max_samples: float


class _TreeState(Protocol):
    children_left: np.ndarray
    children_right: np.ndarray
    feature: np.ndarray
    threshold: np.ndarray
    value: np.ndarray
    node_count: int


class RandomForestRegressionAdapter:
    """Deterministic, single-threaded Random Forest with JSON tree state."""

    adapter_key = "random_forest_cross_sectional_regression"
    adapter_version = "sklearn_random_forest_regressor_v1"

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
        estimator = RandomForestRegressor(
            n_estimators=parameters.n_estimators,
            max_depth=parameters.max_depth,
            min_samples_leaf=parameters.min_samples_leaf,
            max_features=parameters.max_features,
            bootstrap=True,
            max_samples=parameters.max_samples,
            criterion="squared_error",
            random_state=seed,
            n_jobs=1,
        )
        estimator.fit(features, target)
        trees = tuple(_tree_document(tree.tree_) for tree in estimator.estimators_)
        if len(trees) != parameters.n_estimators:
            raise TrainableAggregationError("Random Forest fitted the wrong tree count")
        document: dict[str, object] = {
            "adapter_key": self.adapter_key,
            "adapter_version": self.adapter_version,
            "sklearn_version": sklearn.__version__,
            "numpy_version": np.__version__,
            "feature_schema_fingerprint": feature_schema.fingerprint,
            "ordered_feature_keys": feature_schema.ordered_feature_keys,
            "hyperparameters": {
                "n_estimators": parameters.n_estimators,
                "max_depth": parameters.max_depth,
                "min_samples_leaf": parameters.min_samples_leaf,
                "max_features": parameters.max_features,
                "bootstrap": True,
                "max_samples": _float_hex(parameters.max_samples),
                "criterion": "squared_error",
                "n_jobs": 1,
            },
            "seed": seed,
            "feature_importances": [
                _float_hex(value) for value in estimator.feature_importances_
            ],
            "trees": trees,
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
            raise TrainableAggregationError("Fitted Random Forest identity mismatch")
        ordered_keys = model.model_document.get("ordered_feature_keys")
        trees = model.model_document.get("trees")
        if not isinstance(ordered_keys, (list, tuple)) or not isinstance(trees, (list, tuple)):
            raise TrainableAggregationError("Fitted Random Forest document is malformed")
        if not trees:
            raise TrainableAggregationError("Fitted Random Forest has no trees")
        if any(len(row.feature_values) != len(ordered_keys) for row in rows):
            raise TrainableAggregationError(
                "Prediction row does not match the Random Forest Feature Schema"
            )
        predictions: list[Decimal] = []
        for row in rows:
            # scikit-learn's tree prediction boundary converts input features
            # to float32.  Reproduce that exact traversal contract from the
            # published JSON state rather than using wider Python floats.
            values = tuple(float(np.float32(value)) for value in row.feature_values)
            raw = sum(_predict_tree(cast(Mapping[str, object], tree), values) for tree in trees)
            raw /= len(trees)
            if not isfinite(raw):
                raise TrainableAggregationError("Random Forest prediction is non-finite")
            predictions.append(
                Decimal.from_float(raw).quantize(VALUE_QUANTUM, rounding=ROUND_HALF_EVEN)
            )
        return tuple(predictions)


def _parameters(hyperparameters: Mapping[str, object]) -> _RandomForestParameters:
    required = {
        "n_estimators",
        "max_depth",
        "min_samples_leaf",
        "max_features",
        "max_samples",
    }
    if set(hyperparameters) != required:
        raise TrainableAggregationError(
            "Random Forest requires its exact frozen hyperparameter set"
        )
    n_estimators = hyperparameters["n_estimators"]
    max_depth = hyperparameters["max_depth"]
    min_samples_leaf = hyperparameters["min_samples_leaf"]
    max_features = hyperparameters["max_features"]
    max_samples = hyperparameters["max_samples"]
    if (
        isinstance(n_estimators, bool)
        or not isinstance(n_estimators, int)
        or not 1 <= n_estimators <= 128
        or isinstance(max_depth, bool)
        or not isinstance(max_depth, int)
        or not 2 <= max_depth <= 16
        or isinstance(min_samples_leaf, bool)
        or not isinstance(min_samples_leaf, int)
        or not 1 <= min_samples_leaf <= 1000
    ):
        raise TrainableAggregationError("Random Forest integer parameters are invalid")
    if max_features not in {"sqrt", "log2", "all"}:
        raise TrainableAggregationError("Random Forest max_features is invalid")
    if isinstance(max_samples, bool) or not isinstance(max_samples, (int, float, str)):
        raise TrainableAggregationError("Random Forest max_samples is invalid")
    try:
        sample_fraction = float(max_samples)
    except ValueError as error:
        raise TrainableAggregationError("Random Forest max_samples is invalid") from error
    if not isfinite(sample_fraction) or not 0 < sample_fraction <= 1:
        raise TrainableAggregationError("Random Forest max_samples is invalid")
    return _RandomForestParameters(
        n_estimators=n_estimators,
        max_depth=max_depth,
        min_samples_leaf=min_samples_leaf,
        max_features=1.0 if max_features == "all" else max_features,
        max_samples=sample_fraction,
    )


def _features_and_target(
    rows: Sequence[TrainingMatrixRow], feature_schema: FeatureSchema
) -> tuple[np.ndarray, np.ndarray]:
    if not rows:
        raise TrainableAggregationError("Random Forest fit requires training rows")
    feature_count = len(feature_schema.ordered_feature_keys)
    if any(len(row.feature_values) != feature_count for row in rows):
        raise TrainableAggregationError("Training row does not match the Feature Schema")
    features = np.asarray(
        [[float(value) for value in row.feature_values] for row in rows],
        dtype=np.float64,
    )
    target = np.asarray([float(row.target_value) for row in rows], dtype=np.float64)
    if not np.isfinite(features).all() or not np.isfinite(target).all():
        raise TrainableAggregationError("Random Forest inputs must be finite")
    return features, target


def _tree_document(tree: _TreeState) -> dict[str, object]:
    children_left = tree.children_left
    children_right = tree.children_right
    features = tree.feature
    thresholds = tree.threshold
    values = tree.value
    node_count = tree.node_count
    if any(
        len(item) != node_count
        for item in (children_left, children_right, features, thresholds)
    ):
        raise TrainableAggregationError("Random Forest tree state is malformed")
    flattened = values.reshape(node_count, -1)
    if flattened.shape[1] != 1:
        raise TrainableAggregationError("Random Forest tree output is not scalar")
    return {
        "children_left": [int(value) for value in children_left],
        "children_right": [int(value) for value in children_right],
        "feature": [int(value) for value in features],
        "threshold": [_float_hex(value) for value in thresholds],
        "value": [_float_hex(value) for value in flattened[:, 0]],
    }


def _predict_tree(tree: Mapping[str, object], values: tuple[float, ...]) -> float:
    children_left = _integer_list(tree.get("children_left"))
    children_right = _integer_list(tree.get("children_right"))
    features = _integer_list(tree.get("feature"))
    thresholds = _hex_list(tree.get("threshold"))
    outputs = _hex_list(tree.get("value"))
    node_count = len(children_left)
    if not node_count or any(
        len(item) != node_count
        for item in (children_right, features, thresholds, outputs)
    ):
        raise TrainableAggregationError("Fitted Random Forest tree is malformed")
    node = 0
    visited = 0
    while children_left[node] != children_right[node]:
        feature = features[node]
        if feature < 0 or feature >= len(values):
            raise TrainableAggregationError("Random Forest split feature is invalid")
        node = children_left[node] if values[feature] <= thresholds[node] else children_right[node]
        visited += 1
        if node < 0 or node >= node_count or visited > node_count:
            raise TrainableAggregationError("Random Forest tree traversal is invalid")
    return outputs[node]


def _integer_list(value: object) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        raise TrainableAggregationError("Random Forest integer state is malformed")
    return tuple(cast(int, item) for item in value)


def _hex_list(value: object) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) for item in value
    ):
        raise TrainableAggregationError("Random Forest floating state is malformed")
    result = tuple(float.fromhex(cast(str, item)) for item in value)
    if not all(isfinite(item) for item in result):
        raise TrainableAggregationError("Random Forest floating state is non-finite")
    return result


def _float_hex(value: float | np.float64) -> str:
    scalar = float(value)
    if not isfinite(scalar):
        raise TrainableAggregationError("Random Forest state contains non-finite values")
    return scalar.hex()
