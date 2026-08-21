from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import Engine

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.linear_trainable_aggregation import (
    OrdinaryLeastSquaresAdapter,
    run_strict_oof_predictions,
)
from style_rotation.v022.trainable_aggregation import (
    FeatureSchema,
    FixedSessionTarget,
    TrainingMatrix,
    TrainingMatrixRow,
    WalkForwardFold,
    WalkForwardPolicy,
)
from style_rotation.v022.trainable_aggregation_publication import (
    PublishedTrainableIdentity,
    TrainablePublicationError,
    _encode_model_state,
    _encode_oof_prediction,
    _encode_training_matrix,
    _policy_document,
    publish_base_learner_spec,
    publish_training_folds,
)


def test_training_matrix_parquet_is_canonical_and_retains_label_maturity() -> None:
    matrix, _folds = _execution_fixture()

    first = _encode_training_matrix(matrix)
    second = _encode_training_matrix(matrix)
    table = pq.read_table(io.BytesIO(first.content))

    assert first.content == second.content
    assert first.row_count == len(matrix.rows)
    assert first.group_count == len(matrix.decision_dates)
    assert first.coverage_start == matrix.decision_dates[0]
    assert first.coverage_end == matrix.decision_dates[-1]
    assert table.column_names == [
        "decision_date",
        "asset_id",
        "security_key",
        "decision_cutoff_at",
        "feature_values",
        "target_value",
        "target_known_at",
        "target_entry_date",
        "target_exit_date",
        "target_available",
    ]
    assert table["target_known_at"][0].as_py() > table["decision_cutoff_at"][0].as_py()


def test_fitted_state_and_oof_payloads_freeze_exact_fold_and_daily_rank() -> None:
    matrix, folds = _execution_fixture()
    result = run_strict_oof_predictions(
        OrdinaryLeastSquaresAdapter(), matrix, folds, hyperparameters={}, seed=0
    )
    known_at = {
        day: datetime.combine(day, time(21), tzinfo=UTC)
        for day in {point.decision_date for point in result.predictions}
    }

    state = _encode_model_state(
        result.fitted_folds[0], folds[0].train_dates[-1], known_at[folds[0].prediction_dates[0]]
    )
    prediction = _encode_oof_prediction(result, known_at)
    state_table = pq.read_table(io.BytesIO(state.content))
    prediction_table = pq.read_table(io.BytesIO(prediction.content))

    assert state.row_count == 1
    assert state.statistics["fold_fingerprint"] == folds[0].fold_fingerprint
    assert state_table["model_document"][0].as_py().startswith("{")
    assert prediction.group_count == 3
    assert prediction_table.column_names == [
        "session_date",
        "asset_id",
        "security_key",
        "known_at",
        "feature_value",
        "raw_prediction",
        "fold_ordinal",
    ]
    assert set(prediction_table["feature_value"].to_pylist()) == {
        "-1.000000000000000000",
        "0E-18",
        "1.000000000000000000",
    }


def test_fold_policy_document_has_the_builder_intrinsic_fingerprint() -> None:
    policy = WalkForwardPolicy("daily_expanding_v1", 24, 6, 3, embargo_groups=1)

    assert sha256_hexdigest(_policy_document(policy)) == policy.fingerprint


def test_training_fold_publication_rejects_a_matrix_identity_mismatch() -> None:
    matrix, _folds = _execution_fixture()
    policy = WalkForwardPolicy("daily_expanding_v1", 2, 1, 1)
    invalid = WalkForwardFold(
        ordinal=0,
        train_dates=matrix.decision_dates[:3],
        validation_dates=matrix.decision_dates[3:4],
        prediction_dates=matrix.decision_dates[4:5],
        fold_fingerprint="a" * 64,
    )
    policy_publication = PublishedTrainableIdentity(
        projection_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        intrinsic_fingerprint=policy.fingerprint,
        artifact_semantic_fingerprint="b" * 64,
        reused=False,
    )

    try:
        publish_training_folds(
            cast(Engine, object()),
            matrix=matrix,
            training_matrix_id=uuid.uuid4(),
            training_matrix_artifact_id=uuid.uuid4(),
            policy=policy,
            policy_publication=policy_publication,
            folds=(invalid,),
        )
    except TrainablePublicationError as error:
        assert "does not match the Matrix and Policy" in str(error)
    else:
        raise AssertionError("Matrix/Fold identity mismatch was accepted")


def test_base_learner_seed_must_fit_persisted_bigint() -> None:
    try:
        publish_base_learner_spec(
            cast(Engine, object()),
            aggregation_version_id=uuid.uuid4(),
            feature_schema_version_id=uuid.uuid4(),
            target_version_id=uuid.uuid4(),
            training_preset_version_id=uuid.uuid4(),
            fold_policy_version_id=uuid.uuid4(),
            adapter_key="ridge_regression",
            adapter_version="1",
            hyperparameters={"alpha": "1"},
            random_seed=2**63,
            dependencies=(),
        )
    except ValueError as error:
        assert "PostgreSQL bigint" in str(error)
    else:
        raise AssertionError("Out-of-range seed was accepted")


def _execution_fixture() -> tuple[TrainingMatrix, tuple[WalkForwardFold, ...]]:
    start = date(2020, 1, 1)
    dates = tuple(start + timedelta(days=index) for index in range(7))
    rows = tuple(
        _row(day, security)
        for day in dates
        for security in (1, 2, 3)
    )
    matrix = TrainingMatrix(
        FeatureSchema(("x",)),
        FixedSessionTarget("forward_rank_h5", 5),
        rows,
        dates,
    )
    folds = (
        WalkForwardFold(0, dates[:3], dates[3:4], dates[4:5], "a" * 64),
        WalkForwardFold(1, dates[:4], dates[4:5], dates[5:7], "b" * 64),
    )
    return matrix, folds


def _row(day: date, security: int) -> TrainingMatrixRow:
    cutoff = datetime.combine(day, time(21), tzinfo=UTC)
    return TrainingMatrixRow(
        security_id=uuid.UUID(int=security),
        security_key=f"s{security}",
        decision_date=day,
        decision_cutoff_at=cutoff,
        feature_values=(Decimal(security),),
        target_value=Decimal(security),
        target_known_at=cutoff + timedelta(hours=1),
        target_entry_date=day,
        target_exit_date=day + timedelta(days=1),
    )
