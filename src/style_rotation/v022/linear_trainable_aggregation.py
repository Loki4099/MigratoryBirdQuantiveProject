from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from math import isfinite
from typing import Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

from style_rotation.core.canonical import canonical_json, sha256_hexdigest
from style_rotation.v022.trainable_aggregation import (
    VALUE_QUANTUM,
    FeatureSchema,
    FittedRegressionModel,
    RegressionModelAdapter,
    TrainableAggregationError,
    TrainingMatrix,
    TrainingMatrixRow,
    WalkForwardFold,
    _average_rank_center,
)


@dataclass(frozen=True, slots=True)
class OofPredictionPoint:
    security_id: uuid.UUID
    security_key: str
    decision_date: date
    raw_prediction: Decimal
    centered_rank: Decimal
    fold_ordinal: int


@dataclass(frozen=True, slots=True)
class FittedFoldState:
    fold_ordinal: int
    fold_fingerprint: str
    model: FittedRegressionModel
    train_row_count: int
    validation_row_count: int
    prediction_row_count: int


@runtime_checkable
class _DenseRegressionAdapter(Protocol):
    adapter_key: str
    adapter_version: str

    def fit_dense(
        self,
        features: npt.NDArray[np.float64],
        target: npt.NDArray[np.float64],
        *,
        feature_schema: FeatureSchema,
        seed: int,
        hyperparameters: Mapping[str, object],
    ) -> FittedRegressionModel: ...

    def predict_dense(
        self,
        model: FittedRegressionModel,
        features: npt.NDArray[np.float64],
    ) -> tuple[Decimal, ...]: ...


@dataclass(frozen=True, slots=True)
class StrictOofResult:
    adapter_key: str
    adapter_version: str
    matrix_fingerprint: str
    fitted_folds: tuple[FittedFoldState, ...]
    predictions: tuple[OofPredictionPoint, ...]
    _fingerprint: str = field(default="", init=False, repr=False, compare=False)

    @property
    def fingerprint(self) -> str:
        if not self._fingerprint:
            digest = hashlib.sha256()
            digest.update(
                canonical_json(
                    {
                        "fingerprint_contract": "canonical_sequence_sha256_v1",
                        "kind": "strict_oof_prediction_v2",
                        "adapter_key": self.adapter_key,
                        "adapter_version": self.adapter_version,
                        "matrix_fingerprint": self.matrix_fingerprint,
                        "fitted_folds": self.fitted_folds,
                        "prediction_count": len(self.predictions),
                    }
                ).encode("utf-8")
            )
            for prediction in self.predictions:
                digest.update(b"\x00")
                digest.update(
                    hashlib.sha256(canonical_json(prediction).encode("utf-8")).digest()
                )
            object.__setattr__(self, "_fingerprint", digest.hexdigest())
        return self._fingerprint


class OrdinaryLeastSquaresAdapter:
    adapter_key = "ols_cross_sectional_regression"
    adapter_version = "numpy_lstsq_v1"

    def fit(
        self,
        rows: Sequence[TrainingMatrixRow],
        *,
        feature_schema: FeatureSchema,
        seed: int,
        hyperparameters: Mapping[str, object],
    ) -> FittedRegressionModel:
        if hyperparameters:
            raise TrainableAggregationError("OLS does not accept hyperparameters")
        design, target = _design_and_target(rows, feature_schema)
        coefficients, _residuals, rank, singular_values = np.linalg.lstsq(
            design, target, rcond=None
        )
        return _fitted_model(
            self,
            feature_schema,
            coefficients,
            seed=seed,
            hyperparameters={},
            fit_diagnostics={
                "design_rank": int(rank),
                "singular_values": [_float_hex(value) for value in singular_values],
            },
        )

    def predict(
        self,
        model: FittedRegressionModel,
        rows: Sequence[TrainingMatrixRow],
    ) -> tuple[Decimal, ...]:
        return _predict_linear(self, model, rows)


class RidgeRegressionAdapter:
    adapter_key = "ridge_cross_sectional_regression"
    adapter_version = "numpy_closed_form_v1"

    def fit(
        self,
        rows: Sequence[TrainingMatrixRow],
        *,
        feature_schema: FeatureSchema,
        seed: int,
        hyperparameters: Mapping[str, object],
    ) -> FittedRegressionModel:
        if set(hyperparameters) != {"alpha"}:
            raise TrainableAggregationError("Ridge requires exactly one alpha hyperparameter")
        alpha_value = hyperparameters["alpha"]
        if isinstance(alpha_value, bool) or not isinstance(alpha_value, (int, float, Decimal)):
            raise TrainableAggregationError("Ridge alpha must be numeric")
        alpha = float(alpha_value)
        if not isfinite(alpha) or alpha <= 0:
            raise TrainableAggregationError("Ridge alpha must be finite and positive")
        design, target = _design_and_target(rows, feature_schema)
        penalty = np.eye(design.shape[1], dtype=np.float64)
        penalty[0, 0] = 0.0  # The intercept is intentionally not regularized.
        normal = design.T @ design + alpha * penalty
        coefficients = np.linalg.solve(normal, design.T @ target)
        return _fitted_model(
            self,
            feature_schema,
            coefficients,
            seed=seed,
            hyperparameters={"alpha": _float_hex(alpha)},
            fit_diagnostics={"solver": "closed_form_solve", "intercept_penalized": False},
        )

    def predict(
        self,
        model: FittedRegressionModel,
        rows: Sequence[TrainingMatrixRow],
    ) -> tuple[Decimal, ...]:
        return _predict_linear(self, model, rows)


def run_strict_oof_predictions(
    adapter: RegressionModelAdapter,
    matrix: TrainingMatrix,
    folds: Sequence[WalkForwardFold],
    *,
    hyperparameters: Mapping[str, object],
    seed: int,
) -> StrictOofResult:
    if not folds:
        raise TrainableAggregationError("Strict OOF execution requires at least one fold")
    if isinstance(adapter, _DenseRegressionAdapter):
        return _run_dense_oof_predictions(
            adapter,
            matrix,
            folds,
            hyperparameters=hyperparameters,
            seed=seed,
        )
    rows_by_date: dict[date, list[TrainingMatrixRow]] = {}
    for row in matrix.rows:
        rows_by_date.setdefault(row.decision_date, []).append(row)

    seen_prediction_dates: set[date] = set()
    fitted_states: list[FittedFoldState] = []
    predictions: list[OofPredictionPoint] = []
    for expected_ordinal, fold in enumerate(folds):
        if fold.ordinal != expected_ordinal:
            raise TrainableAggregationError("Walk-forward fold ordinals must be contiguous")
        train_dates = set(fold.train_dates)
        validation_dates = set(fold.validation_dates)
        prediction_dates = set(fold.prediction_dates)
        if (
            train_dates & validation_dates
            or train_dates & prediction_dates
            or validation_dates & prediction_dates
        ):
            raise TrainableAggregationError("Walk-forward phases must be disjoint")
        if seen_prediction_dates & prediction_dates:
            raise TrainableAggregationError("OOF prediction dates cannot overlap across folds")
        seen_prediction_dates.update(prediction_dates)
        train_rows = _rows_for_dates(
            rows_by_date, fold.train_dates, "training", require_target=True
        )
        validation_rows = _rows_for_dates(
            rows_by_date, fold.validation_dates, "validation", require_target=True
        )
        prediction_rows = _rows_for_dates(rows_by_date, fold.prediction_dates, "prediction")
        validation_cutoff = min(row.decision_cutoff_at for row in validation_rows)
        prediction_cutoff = min(row.decision_cutoff_at for row in prediction_rows)
        if any(row.target_known_at > validation_cutoff for row in train_rows):
            raise TrainableAggregationError(
                "OOF training fold contains a label unavailable at validation cutoff"
            )
        if any(row.target_known_at > prediction_cutoff for row in validation_rows):
            raise TrainableAggregationError(
                "OOF validation fold contains a label unavailable at prediction cutoff"
            )
        model = adapter.fit(
            train_rows,
            feature_schema=matrix.feature_schema,
            seed=seed,
            hyperparameters=hyperparameters,
        )
        raw_predictions = adapter.predict(model, prediction_rows)
        if len(raw_predictions) != len(prediction_rows):
            raise TrainableAggregationError("Regression adapter returned the wrong row count")
        raw_by_date: dict[date, list[tuple[TrainingMatrixRow, Decimal]]] = {}
        for row, prediction in zip(prediction_rows, raw_predictions, strict=True):
            raw_by_date.setdefault(row.decision_date, []).append((row, prediction))
        for decision_date in fold.prediction_dates:
            dated = raw_by_date.get(decision_date)
            if not dated:
                raise TrainableAggregationError("OOF fold omitted a prediction date")
            centered = _average_rank_center(
                tuple(
                    (row.security_id, row.security_key, prediction)
                    for row, prediction in dated
                )
            )
            for row, prediction in dated:
                predictions.append(
                    OofPredictionPoint(
                        security_id=row.security_id,
                        security_key=row.security_key,
                        decision_date=decision_date,
                        raw_prediction=prediction,
                        centered_rank=centered[row.security_id],
                        fold_ordinal=fold.ordinal,
                    )
                )
        fitted_states.append(
            FittedFoldState(
                fold_ordinal=fold.ordinal,
                fold_fingerprint=fold.fold_fingerprint,
                model=model,
                train_row_count=len(train_rows),
                validation_row_count=len(validation_rows),
                prediction_row_count=len(prediction_rows),
            )
        )
    predictions.sort(
        key=lambda item: (item.decision_date, item.security_key, str(item.security_id))
    )
    return StrictOofResult(
        adapter_key=adapter.adapter_key,
        adapter_version=adapter.adapter_version,
        matrix_fingerprint=matrix.fingerprint,
        fitted_folds=tuple(fitted_states),
        predictions=tuple(predictions),
    )


def _run_dense_oof_predictions(
    adapter: _DenseRegressionAdapter,
    matrix: TrainingMatrix,
    folds: Sequence[WalkForwardFold],
    *,
    hyperparameters: Mapping[str, object],
    seed: int,
) -> StrictOofResult:
    """Run exact OOF folds while converting the immutable Matrix only once."""

    feature_count = len(matrix.feature_schema.ordered_feature_keys)
    row_count = len(matrix.rows)
    features = np.empty((row_count, feature_count), dtype=np.float64)
    targets = np.empty(row_count, dtype=np.float64)
    target_available = np.empty(row_count, dtype=np.bool_)
    ranges: dict[date, tuple[int, int]] = {}
    maximum_target_known_at: dict[date, datetime] = {}
    for index, row in enumerate(matrix.rows):
        if len(row.feature_values) != feature_count:
            raise TrainableAggregationError("Training row does not match the Feature Schema")
        features[index, :] = tuple(float(value) for value in row.feature_values)
        targets[index] = float(row.target_value)
        target_available[index] = row.target_available
        prior = ranges.get(row.decision_date)
        ranges[row.decision_date] = (index if prior is None else prior[0], index + 1)
        if row.target_available:
            known = maximum_target_known_at.get(row.decision_date)
            if known is None or row.target_known_at > known:
                maximum_target_known_at[row.decision_date] = row.target_known_at
    if not np.isfinite(features).all() or not np.isfinite(targets[target_available]).all():
        raise TrainableAggregationError("Regression inputs must be finite")

    seen_prediction_dates: set[date] = set()
    fitted_states: list[FittedFoldState] = []
    predictions: list[OofPredictionPoint] = []
    for expected_ordinal, fold in enumerate(folds):
        if fold.ordinal != expected_ordinal:
            raise TrainableAggregationError("Walk-forward fold ordinals must be contiguous")
        train_dates = set(fold.train_dates)
        validation_dates = set(fold.validation_dates)
        prediction_dates = set(fold.prediction_dates)
        if (
            train_dates & validation_dates
            or train_dates & prediction_dates
            or validation_dates & prediction_dates
        ):
            raise TrainableAggregationError("Walk-forward phases must be disjoint")
        if seen_prediction_dates & prediction_dates:
            raise TrainableAggregationError("OOF prediction dates cannot overlap across folds")
        seen_prediction_dates.update(prediction_dates)

        validation_rows = _rows_from_ranges(
            matrix.rows,
            ranges,
            fold.validation_dates,
            "validation",
            require_target=True,
        )
        prediction_rows = _rows_from_ranges(
            matrix.rows,
            ranges,
            fold.prediction_dates,
            "prediction",
        )
        validation_cutoff = min(row.decision_cutoff_at for row in validation_rows)
        prediction_cutoff = min(row.decision_cutoff_at for row in prediction_rows)
        if any(
            maximum_target_known_at.get(day) is None
            or maximum_target_known_at[day] > validation_cutoff
            for day in fold.train_dates
        ):
            raise TrainableAggregationError(
                "OOF training fold contains a label unavailable at validation cutoff"
            )
        if any(row.target_known_at > prediction_cutoff for row in validation_rows):
            raise TrainableAggregationError(
                "OOF validation fold contains a label unavailable at prediction cutoff"
            )

        train_feature_chunks: list[npt.NDArray[np.float64]] = []
        train_target_chunks: list[npt.NDArray[np.float64]] = []
        for day in fold.train_dates:
            start, end = _required_range(ranges, day, "training")
            available = target_available[start:end]
            if not available.any():
                raise TrainableAggregationError(
                    "Walk-forward training date has no mature Target rows"
                )
            train_feature_chunks.append(features[start:end][available])
            train_target_chunks.append(targets[start:end][available])
        train_features = np.concatenate(train_feature_chunks, axis=0)
        train_targets = np.concatenate(train_target_chunks, axis=0)
        prediction_features = np.concatenate(
            [
                features[slice(*_required_range(ranges, day, "prediction"))]
                for day in fold.prediction_dates
            ],
            axis=0,
        )
        model = adapter.fit_dense(
            train_features,
            train_targets,
            feature_schema=matrix.feature_schema,
            seed=seed,
            hyperparameters=hyperparameters,
        )
        raw_predictions = adapter.predict_dense(model, prediction_features)
        if len(raw_predictions) != len(prediction_rows):
            raise TrainableAggregationError("Regression adapter returned the wrong row count")
        raw_by_date: dict[date, list[tuple[TrainingMatrixRow, Decimal]]] = {}
        for row, prediction in zip(prediction_rows, raw_predictions, strict=True):
            raw_by_date.setdefault(row.decision_date, []).append((row, prediction))
        for decision_date in fold.prediction_dates:
            dated = raw_by_date.get(decision_date)
            if not dated:
                raise TrainableAggregationError("OOF fold omitted a prediction date")
            centered = _average_rank_center(
                tuple(
                    (row.security_id, row.security_key, prediction)
                    for row, prediction in dated
                )
            )
            for row, prediction in dated:
                predictions.append(
                    OofPredictionPoint(
                        security_id=row.security_id,
                        security_key=row.security_key,
                        decision_date=decision_date,
                        raw_prediction=prediction,
                        centered_rank=centered[row.security_id],
                        fold_ordinal=fold.ordinal,
                    )
                )
        fitted_states.append(
            FittedFoldState(
                fold_ordinal=fold.ordinal,
                fold_fingerprint=fold.fold_fingerprint,
                model=model,
                train_row_count=len(train_features),
                validation_row_count=len(validation_rows),
                prediction_row_count=len(prediction_rows),
            )
        )
    predictions.sort(
        key=lambda item: (item.decision_date, item.security_key, str(item.security_id))
    )
    return StrictOofResult(
        adapter_key=adapter.adapter_key,
        adapter_version=adapter.adapter_version,
        matrix_fingerprint=matrix.fingerprint,
        fitted_folds=tuple(fitted_states),
        predictions=tuple(predictions),
    )


def _required_range(
    ranges: Mapping[date, tuple[int, int]],
    day: date,
    phase: str,
) -> tuple[int, int]:
    result = ranges.get(day)
    if result is None:
        raise TrainableAggregationError(f"Walk-forward {phase} date has no matrix rows")
    return result


def _rows_from_ranges(
    rows: Sequence[TrainingMatrixRow],
    ranges: Mapping[date, tuple[int, int]],
    dates: Sequence[date],
    phase: str,
    *,
    require_target: bool = False,
) -> tuple[TrainingMatrixRow, ...]:
    result: list[TrainingMatrixRow] = []
    for day in dates:
        start, end = _required_range(ranges, day, phase)
        dated = rows[start:end]
        result.extend(row for row in dated if row.target_available or not require_target)
        if require_target and not any(row.target_available for row in dated):
            raise TrainableAggregationError(
                f"Walk-forward {phase} date has no mature Target rows"
            )
    return tuple(result)


def _design_and_target(
    rows: Sequence[TrainingMatrixRow], feature_schema: FeatureSchema
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    if not rows:
        raise TrainableAggregationError("Regression fit requires training rows")
    if any(not row.target_available for row in rows):
        raise TrainableAggregationError("Regression fit received a prediction-only row")
    feature_count = len(feature_schema.ordered_feature_keys)
    if any(len(row.feature_values) != feature_count for row in rows):
        raise TrainableAggregationError("Training row does not match the Feature Schema")
    matrix = np.asarray(
        [[float(value) for value in row.feature_values] for row in rows],
        dtype=np.float64,
    )
    target = np.asarray([float(row.target_value) for row in rows], dtype=np.float64)
    if not np.isfinite(matrix).all() or not np.isfinite(target).all():
        raise TrainableAggregationError("Regression inputs must be finite")
    design = np.column_stack((np.ones(len(rows), dtype=np.float64), matrix))
    return design, target


def _fitted_model(
    adapter: RegressionModelAdapter,
    feature_schema: FeatureSchema,
    coefficients: npt.NDArray[np.float64],
    *,
    seed: int,
    hyperparameters: Mapping[str, object],
    fit_diagnostics: Mapping[str, object],
) -> FittedRegressionModel:
    if coefficients.shape != (len(feature_schema.ordered_feature_keys) + 1,):
        raise TrainableAggregationError("Regression solver returned an invalid coefficient shape")
    if not np.isfinite(coefficients).all():
        raise TrainableAggregationError("Regression solver returned non-finite coefficients")
    document: dict[str, object] = {
        "adapter_key": adapter.adapter_key,
        "adapter_version": adapter.adapter_version,
        "numpy_version": np.__version__,
        "feature_schema_fingerprint": feature_schema.fingerprint,
        "ordered_feature_keys": feature_schema.ordered_feature_keys,
        "intercept": _float_hex(coefficients[0]),
        "coefficients": [_float_hex(value) for value in coefficients[1:]],
        "hyperparameters": dict(hyperparameters),
        "seed": seed,
        "fit_diagnostics": dict(fit_diagnostics),
    }
    return FittedRegressionModel(
        adapter_key=adapter.adapter_key,
        adapter_version=adapter.adapter_version,
        feature_schema_fingerprint=feature_schema.fingerprint,
        model_document=document,
        model_fingerprint=sha256_hexdigest(document),
    )


def _predict_linear(
    adapter: RegressionModelAdapter,
    model: FittedRegressionModel,
    rows: Sequence[TrainingMatrixRow],
) -> tuple[Decimal, ...]:
    if model.adapter_key != adapter.adapter_key or model.adapter_version != adapter.adapter_version:
        raise TrainableAggregationError("Fitted model adapter identity mismatch")
    ordered_keys = model.model_document.get("ordered_feature_keys")
    coefficients_document = model.model_document.get("coefficients")
    intercept_document = model.model_document.get("intercept")
    if (
        not isinstance(ordered_keys, (list, tuple))
        or not isinstance(coefficients_document, list)
        or not isinstance(intercept_document, str)
    ):
        raise TrainableAggregationError("Fitted linear model document is malformed")
    coefficients = np.asarray(
        [float.fromhex(value) for value in coefficients_document if isinstance(value, str)],
        dtype=np.float64,
    )
    if len(coefficients) != len(coefficients_document) or len(coefficients) != len(ordered_keys):
        raise TrainableAggregationError("Fitted linear model coefficients are malformed")
    if any(len(row.feature_values) != len(coefficients) for row in rows):
        raise TrainableAggregationError("Prediction row does not match the fitted Feature Schema")
    feature_matrix = np.asarray(
        [[float(value) for value in row.feature_values] for row in rows], dtype=np.float64
    )
    raw = float.fromhex(intercept_document) + feature_matrix @ coefficients
    if not np.isfinite(raw).all():
        raise TrainableAggregationError("Regression prediction is non-finite")
    return tuple(
        Decimal.from_float(float(value)).quantize(VALUE_QUANTUM, rounding=ROUND_HALF_EVEN)
        for value in raw
    )


def _rows_for_dates(
    rows_by_date: Mapping[date, Sequence[TrainingMatrixRow]],
    dates: Sequence[date],
    phase: str,
    *,
    require_target: bool = False,
) -> tuple[TrainingMatrixRow, ...]:
    rows: list[TrainingMatrixRow] = []
    for day in dates:
        dated = rows_by_date.get(day)
        if not dated:
            raise TrainableAggregationError(f"Walk-forward {phase} date has no matrix rows")
        rows.extend(
            row for row in dated if row.target_available or not require_target
        )
        if require_target and not any(row.target_available for row in dated):
            raise TrainableAggregationError(
                f"Walk-forward {phase} date has no mature Target rows"
            )
    rows.sort(key=lambda item: (item.decision_date, item.security_key, str(item.security_id)))
    return tuple(rows)


def _float_hex(value: float | np.float64) -> str:
    scalar = float(value)
    if not isfinite(scalar):
        raise TrainableAggregationError("Regression state contains a non-finite value")
    return scalar.hex()
