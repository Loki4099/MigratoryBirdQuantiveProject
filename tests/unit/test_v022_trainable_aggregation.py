from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from style_rotation.v022.trainable_aggregation import (
    AdjustedOpenPoint,
    CrossSectionalTargetPoint,
    FeatureSchema,
    FixedSessionTarget,
    FixedSessionTargetPanel,
    TrainableAggregationError,
    TrainingFeaturePoint,
    TrainingMatrix,
    TrainingMatrixRow,
    WalkForwardPolicy,
    append_prediction_only_rows,
    build_expanding_walk_forward_folds,
    build_fixed_session_target_panel,
    build_training_matrix,
)


def _at(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour=hour), tzinfo=UTC)


def test_fixed_h5_target_uses_next_open_exact_horizon_and_average_rank() -> None:
    securities = (
        (uuid.UUID(int=1), "a", Decimal("10"), Decimal("11")),
        (uuid.UUID(int=2), "b", Decimal("10"), Decimal("12")),
        (uuid.UUID(int=3), "c", Decimal("20"), Decimal("24")),
    )
    sessions = tuple(date(2020, 1, 2) + timedelta(days=index) for index in range(8))
    opens = tuple(
        AdjustedOpenPoint(
            security_id=security_id,
            security_key=security_key,
            session_date=session,
            adjusted_open=(
                exit_value
                if session == sessions[6]
                else entry_value
            ),
            known_at=_at(session, 14),
        )
        for security_id, security_key, entry_value, exit_value in securities
        for session in sessions
    )

    panel = build_fixed_session_target_panel(
        FixedSessionTarget("forward_rank_h5", 5),
        sessions,
        opens,
        requested_start=sessions[0],
        requested_end=sessions[0],
        candidate_security_ids_by_date={
            sessions[0]: frozenset(item[0] for item in securities)
        },
    )

    assert panel.decision_dates == (sessions[0],)
    assert {item.entry_date for item in panel.points} == {sessions[1]}
    assert {item.exit_date for item in panel.points} == {sessions[6]}
    assert {item.label_known_at for item in panel.points} == {_at(sessions[6], 14)}
    assert {item.security_key: item.raw_forward_return for item in panel.points} == {
        "a": Decimal("0.100000000000000000"),
        "b": Decimal("0.200000000000000000"),
        "c": Decimal("0.200000000000000000"),
    }
    assert {item.security_key: item.centered_rank for item in panel.points} == {
        "a": Decimal("-1.000000000000000000"),
        "b": Decimal("0.500000000000000000"),
        "c": Decimal("0.500000000000000000"),
    }


def test_fixed_target_omits_one_unobservable_label_without_losing_the_group() -> None:
    securities = (
        (uuid.UUID(int=1), "a", Decimal("10"), Decimal("11")),
        (uuid.UUID(int=2), "b", Decimal("10"), Decimal("12")),
        (uuid.UUID(int=3), "provider_gap", Decimal("10"), Decimal("13")),
    )
    sessions = tuple(date(2020, 1, 2) + timedelta(days=index) for index in range(8))
    opens = tuple(
        AdjustedOpenPoint(
            security_id=security_id,
            security_key=security_key,
            session_date=session,
            adjusted_open=exit_value if session == sessions[6] else entry_value,
            known_at=_at(session, 14),
        )
        for security_id, security_key, entry_value, exit_value in securities
        for session in sessions
        if not (security_key == "provider_gap" and session == sessions[6])
    )

    panel = build_fixed_session_target_panel(
        FixedSessionTarget("forward_rank_h5", 5),
        sessions,
        opens,
        requested_start=sessions[0],
        requested_end=sessions[0],
        candidate_security_ids_by_date={
            sessions[0]: frozenset(item[0] for item in securities)
        },
    )

    assert panel.decision_dates == (sessions[0],)
    assert {point.security_key for point in panel.points} == {"a", "b"}
    assert {point.centered_rank for point in panel.points} == {
        Decimal("-1.000000000000000000"),
        Decimal("1.000000000000000000"),
    }


def test_fixed_target_rejects_a_group_with_fewer_than_two_observable_labels() -> None:
    securities = ((uuid.UUID(int=1), "a"), (uuid.UUID(int=2), "missing"))
    sessions = tuple(date(2020, 1, 2) + timedelta(days=index) for index in range(8))
    opens = tuple(
        AdjustedOpenPoint(
            security_id=security_id,
            security_key=security_key,
            session_date=session,
            adjusted_open=Decimal("10"),
            known_at=_at(session, 14),
        )
        for security_id, security_key in securities
        for session in sessions
        if not (security_key == "missing" and session == sessions[6])
    )

    with pytest.raises(
        TrainableAggregationError,
        match="fewer than two complete adjusted-open returns",
    ):
        build_fixed_session_target_panel(
            FixedSessionTarget("forward_rank_h5", 5),
            sessions,
            opens,
            requested_start=sessions[0],
            requested_end=sessions[0],
            candidate_security_ids_by_date={
                sessions[0]: frozenset(item[0] for item in securities)
            },
        )


def test_terminal_unmatured_targets_become_prediction_only_rows() -> None:
    securities = ((uuid.UUID(int=1), "a"), (uuid.UUID(int=2), "b"))
    sessions = tuple(date(2020, 1, 2) + timedelta(days=index) for index in range(9))
    opens = tuple(
        AdjustedOpenPoint(
            security_id=security_id,
            security_key=security_key,
            session_date=session,
            adjusted_open=Decimal("10") + Decimal(index),
            known_at=_at(session, 14),
        )
        for security_id, security_key in securities
        for index, session in enumerate(sessions)
        if session <= sessions[7]
    )
    candidates = {
        session: frozenset(item[0] for item in securities) for session in sessions[:3]
    }
    panel = build_fixed_session_target_panel(
        FixedSessionTarget("forward_rank_h5", 5),
        sessions,
        opens,
        requested_start=sessions[0],
        requested_end=sessions[2],
        candidate_security_ids_by_date=candidates,
        label_observation_end=sessions[7],
    )
    schema = FeatureSchema(("signal",))
    features = tuple(
        TrainingFeaturePoint(
            security_id,
            session,
            "signal",
            Decimal(index + 1),
            _at(session, 13),
        )
        for security_id, _security_key in securities
        for index, session in enumerate(sessions[:3])
    )
    matrix = build_training_matrix(
        schema,
        panel,
        features,
        decision_cutoff_at_by_date={session: _at(session, 21) for session in sessions[:3]},
    )

    completed = append_prediction_only_rows(
        matrix,
        features,
        prediction_start=sessions[0],
        prediction_end=sessions[2],
        candidate_security_ids_by_date=candidates,
        security_keys_by_id=dict(securities),
        decision_cutoff_at_by_date={session: _at(session, 21) for session in sessions[:3]},
    )

    assert completed.decision_dates == sessions[:3]
    assert all(
        row.target_available
        for row in completed.rows
        if row.decision_date in sessions[:2]
    )
    assert all(
        not row.target_available
        for row in completed.rows
        if row.decision_date == sessions[2]
    )


def test_training_matrix_is_schema_ordered_complete_and_pit_safe() -> None:
    decision = date(2020, 1, 2)
    cutoff = _at(decision, 21)
    target = FixedSessionTarget("forward_rank_h5", 5)
    panel = FixedSessionTargetPanel(
        target=target,
        decision_dates=(decision,),
        points=(
            _target_point(uuid.UUID(int=1), "a", decision, Decimal("-1")),
            _target_point(uuid.UUID(int=2), "b", decision, Decimal("1")),
        ),
    )
    schema = FeatureSchema(("momentum", "quality"))
    features = tuple(
        TrainingFeaturePoint(security_id, decision, key, value, _at(decision, 20))
        for security_id, values in (
            (uuid.UUID(int=1), (Decimal("0.1"), Decimal("0.2"))),
            (uuid.UUID(int=2), (Decimal("0.3"), Decimal("0.4"))),
        )
        for key, value in zip(schema.ordered_feature_keys, values, strict=True)
    )

    matrix = build_training_matrix(
        schema,
        panel,
        features,
        decision_cutoff_at_by_date={decision: cutoff},
    )

    assert matrix.rows[0].feature_values == (
        Decimal("0.100000000000000000"),
        Decimal("0.200000000000000000"),
    )
    assert matrix.rows[1].target_value == Decimal("1")
    assert len(matrix.fingerprint) == 64

    late_features = list(features)
    late_features[0] = TrainingFeaturePoint(
        late_features[0].security_id,
        decision,
        late_features[0].feature_key,
        late_features[0].value,
        _at(decision + timedelta(days=1), 1),
    )
    with pytest.raises(TrainableAggregationError, match="decision cutoff"):
        build_training_matrix(
            schema,
            panel,
            late_features,
            decision_cutoff_at_by_date={decision: cutoff},
        )


def test_expanding_walk_forward_purges_unmatured_labels_and_applies_embargo() -> None:
    start = date(2020, 1, 1)
    dates = tuple(start + timedelta(days=index) for index in range(14))
    schema = FeatureSchema(("momentum",))
    target = FixedSessionTarget("forward_rank_h5", 5)
    rows = tuple(
        TrainingMatrixRow(
            security_id=uuid.UUID(int=security),
            security_key=f"s{security}",
            decision_date=day,
            decision_cutoff_at=_at(day, 21),
            feature_values=(Decimal(security),),
            target_value=Decimal("-1") if security == 1 else Decimal("1"),
            target_known_at=_at(day + timedelta(days=2), 14),
            target_entry_date=day + timedelta(days=1),
            target_exit_date=day + timedelta(days=2),
        )
        for day in dates
        for security in (1, 2)
    )
    matrix = TrainingMatrix(schema, target, rows, dates)
    policy = WalkForwardPolicy(
        policy_key="expanding_daily_v1",
        minimum_train_groups=3,
        validation_groups=2,
        prediction_groups=2,
        embargo_groups=1,
    )

    folds = build_expanding_walk_forward_folds(
        matrix,
        policy,
        prediction_start=dates[10],
        prediction_end=dates[13],
    )

    assert len(folds) == 2
    assert folds[0].prediction_dates == dates[10:12]
    assert folds[0].validation_dates == dates[6:8]
    assert folds[0].train_dates == dates[0:5]
    assert dates[8] not in folds[0].validation_dates  # embargoed before prediction
    assert len({fold.fold_fingerprint for fold in folds}) == 2
    rows_by_date = {
        day: [row for row in matrix.rows if row.decision_date == day]
        for day in matrix.decision_dates
    }
    for fold in folds:
        validation_cutoff = min(
            row.decision_cutoff_at for row in rows_by_date[fold.validation_dates[0]]
        )
        prediction_cutoff = min(
            row.decision_cutoff_at for row in rows_by_date[fold.prediction_dates[0]]
        )
        assert all(
            row.target_known_at <= validation_cutoff
            for day in fold.train_dates
            for row in rows_by_date[day]
        )
        assert all(
            row.target_known_at <= prediction_cutoff
            for day in fold.validation_dates
            for row in rows_by_date[day]
        )


def _target_point(
    security_id: uuid.UUID,
    security_key: str,
    decision: date,
    centered_rank: Decimal,
) -> CrossSectionalTargetPoint:
    return CrossSectionalTargetPoint(
        security_id=security_id,
        security_key=security_key,
        decision_date=decision,
        entry_date=decision + timedelta(days=1),
        exit_date=decision + timedelta(days=6),
        label_known_at=_at(decision + timedelta(days=6), 14),
        raw_forward_return=centered_rank,
        centered_rank=centered_rank,
    )
