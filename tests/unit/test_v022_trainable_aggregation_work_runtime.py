from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from style_rotation.v022.aggregation_work_runtime import SignalManifestPoint
from style_rotation.v022.trainable_aggregation import (
    AdjustedOpenPoint,
    FixedSessionTarget,
)
from style_rotation.v022.trainable_aggregation_work_runtime import (
    TrainableAggregationExecutionRequest,
    TrainableFeatureInput,
    execute_trainable_aggregation,
)


def test_ols_work_runtime_consumes_all_inputs_and_emits_exact_oof_panel() -> None:
    request = _request("ols_cross_sectional_regression")

    execution = execute_trainable_aggregation(request)

    assert execution.feature_schema.ordered_feature_keys == ("momentum", "quality")
    assert execution.oof_result.adapter_key == "ols_cross_sectional_regression"
    assert {point.decision_date for point in execution.calculation.points} == set(
        request.sessions[15:18]
    )
    assert len(execution.calculation.points) == 9
    assert all(point.missing_reason is None for point in execution.calculation.points)
    assert all(
        point.input_revision == execution.oof_result.fingerprint
        for point in execution.calculation.points
    )


def test_ridge_work_runtime_accepts_canonical_decimal_alpha() -> None:
    execution = execute_trainable_aggregation(_request("ridge_cross_sectional_regression"))

    assert execution.oof_result.adapter_key == "ridge_cross_sectional_regression"
    assert len(execution.oof_result.fitted_folds) == 2
    assert len(execution.calculation.calculation_fingerprint) == 64


def test_random_forest_work_runtime_emits_strict_oof_predictions() -> None:
    execution = execute_trainable_aggregation(
        _request("random_forest_cross_sectional_regression")
    )

    assert execution.oof_result.adapter_key == "random_forest_cross_sectional_regression"
    assert execution.oof_result.adapter_version == "sklearn_random_forest_regressor_v1"
    assert len(execution.oof_result.fitted_folds) == 2
    assert len(execution.calculation.points) == 9


def test_lightgbm_work_runtime_emits_strict_oof_predictions() -> None:
    execution = execute_trainable_aggregation(
        _request("lightgbm_cross_sectional_regression")
    )

    assert execution.oof_result.adapter_key == "lightgbm_cross_sectional_regression"
    assert execution.oof_result.adapter_version == "lightgbm_regressor_cpu_v1"
    assert len(execution.oof_result.fitted_folds) == 2
    assert len(execution.calculation.points) == 9


def test_xgboost_work_runtime_emits_strict_oof_predictions() -> None:
    execution = execute_trainable_aggregation(
        _request("xgboost_cross_sectional_regression")
    )

    assert execution.oof_result.adapter_key == "xgboost_cross_sectional_regression"
    assert execution.oof_result.adapter_version == "xgboost_regressor_cpu_hist_v1"
    assert len(execution.oof_result.fitted_folds) == 2
    assert len(execution.calculation.points) == 9


def test_production_request_releases_consumed_feature_rows() -> None:
    source = _request("ols_cross_sectional_regression")
    consumable_inputs = tuple(
        TrainableFeatureInput(
            item.feature_key,
            item.manifest_fingerprint,
            list(item.points),
        )
        for item in source.feature_inputs
    )
    request = TrainableAggregationExecutionRequest(
        **{
            field: getattr(source, field)
            for field in source.__dataclass_fields__
            if field not in {"feature_inputs", "consume_source_panels"}
        },
        feature_inputs=consumable_inputs,
        consume_source_panels=True,
    )

    execution = execute_trainable_aggregation(request)

    assert len(execution.calculation.points) == 9
    assert all(
        isinstance(item.points, list) and all(point is None for point in item.points)
        for item in consumable_inputs
    )


def test_work_runtime_uses_the_complete_feature_cross_section() -> None:
    request = _request("ols_cross_sectional_regression")
    first = request.feature_inputs[0]
    changed = list(first.points)
    missing_index = next(
        index
        for index, point in enumerate(changed)
        if point.decision_date == request.prediction_start
    )
    changed[missing_index] = SignalManifestPoint(
        asset_id=changed[missing_index].asset_id,
        asset_key=changed[missing_index].asset_key,
        decision_date=changed[missing_index].decision_date,
        signal_value=None,
        known_at=changed[missing_index].known_at,
        input_revision=changed[missing_index].input_revision,
        missing_reason="provider_gap",
    )
    bad = TrainableAggregationExecutionRequest(
        **{
            field: getattr(request, field)
            for field in request.__dataclass_fields__
            if field != "feature_inputs"
        },
        feature_inputs=(
            TrainableFeatureInput(first.feature_key, first.manifest_fingerprint, tuple(changed)),
            request.feature_inputs[1],
        ),
    )

    execution = execute_trainable_aggregation(bad)

    first_date_points = [
        point
        for point in execution.calculation.points
        if point.decision_date == request.prediction_start
    ]
    assert len(first_date_points) == 2
    assert changed[missing_index].asset_id not in {
        point.asset_id for point in first_date_points
    }


def _request(family_key: str) -> TrainableAggregationExecutionRequest:
    start = date(2020, 1, 2)
    sessions = tuple(start + timedelta(days=index) for index in range(25))
    securities = tuple(uuid.UUID(int=index) for index in (1, 2, 3))
    mapping = {
        security_id: f"s{ordinal}"
        for ordinal, security_id in enumerate(securities, start=1)
    }
    cutoffs = {day: datetime.combine(day, time(21), tzinfo=UTC) for day in sessions}
    candidates = {day: frozenset(securities) for day in sessions}
    inputs = []
    for feature_ordinal, feature_key in enumerate(("momentum", "quality"), start=1):
        points = tuple(
            SignalManifestPoint(
                asset_id=asset_id,
                asset_key=f"s{security_ordinal}",
                decision_date=day,
                signal_value=Decimal(feature_ordinal * 10 + security_ordinal + day_ordinal),
                known_at=cutoffs[day],
                input_revision=f"revision-{feature_key}",
                missing_reason=None,
            )
            for day_ordinal, day in enumerate(sessions)
            for security_ordinal, asset_id in enumerate(securities, start=1)
        )
        inputs.append(TrainableFeatureInput(feature_key, "a" * 64, points))
    opens = tuple(
        AdjustedOpenPoint(
            security_id=security_id,
            security_key=f"s{security_ordinal}",
            session_date=day,
            adjusted_open=Decimal(100 + security_ordinal * 5 + day_ordinal),
            known_at=datetime.combine(day, time(14), tzinfo=UTC),
        )
        for day_ordinal, day in enumerate(sessions)
        for security_ordinal, security_id in enumerate(securities, start=1)
    )
    if family_key == "ols_cross_sectional_regression":
        adapter_version = "numpy_lstsq_v1"
        hyperparameters: dict[str, object] = {}
        seed = 0
    elif family_key == "ridge_cross_sectional_regression":
        adapter_version = "numpy_closed_form_v1"
        hyperparameters = {"alpha": "1"}
        seed = 0
    elif family_key == "random_forest_cross_sectional_regression":
        adapter_version = "sklearn_random_forest_regressor_v1"
        hyperparameters = {
            "n_estimators": 8,
            "max_depth": 4,
            "min_samples_leaf": 2,
            "max_features": "sqrt",
            "max_samples": "1.0",
        }
        seed = 1729
    elif family_key == "lightgbm_cross_sectional_regression":
        adapter_version = "lightgbm_regressor_cpu_v1"
        hyperparameters = {
            "n_estimators": 8,
            "learning_rate": "0.05",
            "num_leaves": 7,
            "max_depth": 4,
            "min_child_samples": 5,
            "subsample": "1.0",
            "colsample_bytree": "1.0",
            "reg_alpha": "0",
            "reg_lambda": "1",
        }
        seed = 1729
    else:
        adapter_version = "xgboost_regressor_cpu_hist_v1"
        hyperparameters = {
            "n_estimators": 8,
            "learning_rate": "0.05",
            "max_depth": 4,
            "min_child_weight": "2",
            "subsample": "1.0",
            "colsample_bytree": "1.0",
            "reg_alpha": "0",
            "reg_lambda": "1",
            "gamma": "0",
        }
        seed = 1729
    semantics: dict[str, object] = {
        "adapter_key": family_key,
        "adapter_version": adapter_version,
        "observation_grid": "xnys_completed_session_daily",
        "fold_mode": "expanding_walk_forward",
        "random_split": False,
        "hyperparameters": hyperparameters,
        "seed": seed,
        "fold_policy_key": "unit_expanding_v1",
        "minimum_train_groups": 2,
        "validation_groups": 1,
        "prediction_groups": 2,
        "embargo_groups": 0,
    }
    return TrainableAggregationExecutionRequest(
        family_key=family_key,
        target=FixedSessionTarget("forward_rank_h5", 5),
        training_preset_key=f"{family_key}_preset",
        training_preset_semantics=semantics,
        feature_inputs=tuple(inputs),
        security_keys_by_asset_id=mapping,
        sessions=sessions,
        adjusted_opens=opens,
        candidate_security_ids_by_date=candidates,
        decision_cutoff_at_by_date=cutoffs,
        training_start=sessions[0],
        prediction_start=sessions[15],
        prediction_end=sessions[17],
    )
