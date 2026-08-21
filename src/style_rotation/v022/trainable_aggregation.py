from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Protocol

from style_rotation.core.canonical import canonical_json, sha256_hexdigest

VALUE_QUANTUM = Decimal("0.000000000000000001")
SUPPORTED_FIXED_HORIZONS = frozenset({5, 10, 21})


class TrainableAggregationError(RuntimeError):
    """Raised when a trainable Aggregation identity cannot be built without leakage."""


@dataclass(frozen=True, slots=True)
class FixedSessionTarget:
    target_key: str
    horizon_sessions: int
    version_number: int = 1

    def __post_init__(self) -> None:
        expected_key = f"forward_rank_h{self.horizon_sessions}"
        if self.horizon_sessions not in SUPPORTED_FIXED_HORIZONS:
            raise ValueError("Fixed-session Target horizon must be H5, H10, or H21")
        if self.target_key != expected_key:
            raise ValueError(f"Fixed-session Target key must be {expected_key}")
        if self.version_number < 1:
            raise ValueError("Target version must be positive")

    @property
    def semantic_document(self) -> dict[str, object]:
        return {
            "target_key": self.target_key,
            "version_number": self.version_number,
            "observation_grid": "xnys_completed_session_daily",
            "entry_rule": "next_common_session_open_after_decision_close",
            "exit_rule": "open_after_exact_complete_session_intervals",
            "horizon_sessions": self.horizon_sessions,
            "raw_label": "adjusted_open_total_return",
            "cross_section_transform": "average_rank_centered_minus_one_to_one",
            "direction": "higher_is_better",
            "quantum": "0.000000000000000001",
        }

    @property
    def fingerprint(self) -> str:
        return sha256_hexdigest(self.semantic_document)


@dataclass(frozen=True, slots=True)
class AdjustedOpenPoint:
    security_id: uuid.UUID
    security_key: str
    session_date: date
    adjusted_open: Decimal
    known_at: datetime


@dataclass(frozen=True, slots=True)
class CrossSectionalTargetPoint:
    security_id: uuid.UUID
    security_key: str
    decision_date: date
    entry_date: date
    exit_date: date
    label_known_at: datetime
    raw_forward_return: Decimal
    centered_rank: Decimal


@dataclass(frozen=True, slots=True)
class FixedSessionTargetPanel:
    target: FixedSessionTarget
    decision_dates: tuple[date, ...]
    points: tuple[CrossSectionalTargetPoint, ...]
    _fingerprint: str = field(default="", init=False, repr=False, compare=False)

    @property
    def fingerprint(self) -> str:
        if not self._fingerprint:
            object.__setattr__(
                self,
                "_fingerprint",
                _streaming_sequence_fingerprint(
                    kind="fixed_session_target_panel_v2",
                    header={
                        "target_fingerprint": self.target.fingerprint,
                        "decision_dates": self.decision_dates,
                    },
                    values=self.points,
                ),
            )
        return self._fingerprint


@dataclass(frozen=True, slots=True)
class FeatureSchema:
    ordered_feature_keys: tuple[str, ...]
    version_number: int = 1

    def __post_init__(self) -> None:
        if not 1 <= len(self.ordered_feature_keys) <= 32:
            raise ValueError("Trainable Aggregation requires between 1 and 32 inputs")
        if len(self.ordered_feature_keys) != len(set(self.ordered_feature_keys)):
            raise ValueError("Feature Schema keys must be unique")
        if any(not key.strip() for key in self.ordered_feature_keys):
            raise ValueError("Feature Schema keys must be nonempty")
        if self.version_number < 1:
            raise ValueError("Feature Schema version must be positive")

    @property
    def fingerprint(self) -> str:
        return sha256_hexdigest(
            {
                "version_number": self.version_number,
                "ordered_feature_keys": self.ordered_feature_keys,
                "missing_policy": "complete_case_fail_closed",
                "known_at_policy": "feature_known_at_not_after_decision_cutoff",
            }
        )


@dataclass(frozen=True, slots=True)
class TrainingFeaturePoint:
    security_id: uuid.UUID
    decision_date: date
    feature_key: str
    value: Decimal
    known_at: datetime


@dataclass(frozen=True, slots=True)
class TrainingMatrixRow:
    security_id: uuid.UUID
    security_key: str
    decision_date: date
    decision_cutoff_at: datetime
    feature_values: tuple[Decimal, ...]
    target_value: Decimal
    target_known_at: datetime
    target_entry_date: date
    target_exit_date: date
    target_available: bool = True


@dataclass(frozen=True, slots=True)
class TrainingMatrix:
    feature_schema: FeatureSchema
    target: FixedSessionTarget
    rows: tuple[TrainingMatrixRow, ...]
    decision_dates: tuple[date, ...]
    _fingerprint: str = field(default="", init=False, repr=False, compare=False)

    @property
    def fingerprint(self) -> str:
        if not self._fingerprint:
            object.__setattr__(
                self,
                "_fingerprint",
                _streaming_sequence_fingerprint(
                    kind="training_matrix_v2",
                    header={
                        "feature_schema_fingerprint": self.feature_schema.fingerprint,
                        "target_fingerprint": self.target.fingerprint,
                        "decision_dates": self.decision_dates,
                    },
                    values=self.rows,
                ),
            )
        return self._fingerprint


def _streaming_sequence_fingerprint(
    *,
    kind: str,
    header: Mapping[str, object],
    values: Sequence[object],
) -> str:
    """Hash large canonical sequences without constructing one giant JSON tree."""

    digest = hashlib.sha256()
    digest.update(
        canonical_json(
            {
                "fingerprint_contract": "canonical_sequence_sha256_v1",
                "kind": kind,
                "header": header,
                "value_count": len(values),
            }
        ).encode("utf-8")
    )
    for value in values:
        digest.update(b"\x00")
        digest.update(hashlib.sha256(canonical_json(value).encode("utf-8")).digest())
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class WalkForwardPolicy:
    policy_key: str
    minimum_train_groups: int
    validation_groups: int
    prediction_groups: int
    embargo_groups: int = 0
    version_number: int = 1

    def __post_init__(self) -> None:
        if not self.policy_key.strip():
            raise ValueError("Fold policy key must be nonempty")
        if self.minimum_train_groups < 2:
            raise ValueError("Fold policy requires at least two training groups")
        if self.validation_groups < 1 or self.prediction_groups < 1:
            raise ValueError("Validation and prediction group counts must be positive")
        if self.embargo_groups < 0:
            raise ValueError("Embargo group count cannot be negative")
        if self.version_number < 1:
            raise ValueError("Fold policy version must be positive")

    @property
    def fingerprint(self) -> str:
        return sha256_hexdigest(
            {
                "policy_key": self.policy_key,
                "version_number": self.version_number,
                "mode": "expanding_walk_forward",
                "minimum_train_groups": self.minimum_train_groups,
                "validation_groups": self.validation_groups,
                "prediction_groups": self.prediction_groups,
                "embargo_groups": self.embargo_groups,
                "purge_policy": "target_known_at_before_next_phase_cutoff",
                "random_split": False,
            }
        )


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    ordinal: int
    train_dates: tuple[date, ...]
    validation_dates: tuple[date, ...]
    prediction_dates: tuple[date, ...]
    fold_fingerprint: str


@dataclass(frozen=True, slots=True)
class FittedRegressionModel:
    adapter_key: str
    adapter_version: str
    feature_schema_fingerprint: str
    model_document: Mapping[str, object]
    model_fingerprint: str


class RegressionModelAdapter(Protocol):
    adapter_key: str
    adapter_version: str

    def fit(
        self,
        rows: Sequence[TrainingMatrixRow],
        *,
        feature_schema: FeatureSchema,
        seed: int,
        hyperparameters: Mapping[str, object],
    ) -> FittedRegressionModel: ...

    def predict(
        self,
        model: FittedRegressionModel,
        rows: Sequence[TrainingMatrixRow],
    ) -> tuple[Decimal, ...]: ...


def build_fixed_session_target_panel(
    target: FixedSessionTarget,
    sessions: Sequence[date],
    opens: Sequence[AdjustedOpenPoint],
    *,
    requested_start: date,
    requested_end: date,
    candidate_security_ids_by_date: Mapping[date, frozenset[uuid.UUID]],
    label_observation_end: date | None = None,
) -> FixedSessionTargetPanel:
    if requested_start > requested_end:
        raise ValueError("Target requested start must not follow end")
    ordered_sessions = tuple(sorted(sessions))
    if len(ordered_sessions) != len(set(ordered_sessions)):
        raise TrainableAggregationError("Target calendar contains duplicate sessions")
    if len(ordered_sessions) < target.horizon_sessions + 2:
        raise TrainableAggregationError("Target calendar is too short for the selected horizon")

    security_keys: dict[uuid.UUID, str] = {}
    open_by_key: dict[tuple[uuid.UUID, date], AdjustedOpenPoint] = {}
    session_open_at: dict[date, datetime] = {}
    for point in opens:
        _require_aware(point.known_at, "Adjusted-open known_at")
        if point.adjusted_open <= 0:
            raise TrainableAggregationError("Adjusted open must be positive")
        key = (point.security_id, point.session_date)
        if key in open_by_key:
            raise TrainableAggregationError("Duplicate adjusted-open observation")
        open_by_key[key] = point
        prior_key = security_keys.setdefault(point.security_id, point.security_key)
        if prior_key != point.security_key:
            raise TrainableAggregationError("Security key drift in adjusted opens")
        prior_open_at = session_open_at.setdefault(point.session_date, point.known_at)
        if prior_open_at != point.known_at:
            raise TrainableAggregationError("Session open timestamp differs across securities")

    points: list[CrossSectionalTargetPoint] = []
    emitted_dates: list[date] = []
    for index, decision_date in enumerate(ordered_sessions):
        if decision_date < requested_start or decision_date > requested_end:
            continue
        exit_index = index + 1 + target.horizon_sessions
        if exit_index >= len(ordered_sessions):
            raise TrainableAggregationError(
                f"Target H{target.horizon_sessions} is not mature for "
                f"{decision_date.isoformat()}"
            )
        entry_date = ordered_sessions[index + 1]
        exit_date = ordered_sessions[exit_index]
        # The terminal evaluation sessions still require model predictions,
        # but their forward labels mature after the frozen evaluation end.
        # They are prediction-only rows and must never block or enter fitting.
        if label_observation_end is not None and exit_date > label_observation_end:
            continue
        eligible = candidate_security_ids_by_date.get(decision_date)
        if eligible is None or len(eligible) < 2:
            raise TrainableAggregationError(
                f"Target cross-section requires at least two securities on "
                f"{decision_date.isoformat()}"
            )
        raw_values: list[tuple[uuid.UUID, str, Decimal]] = []
        for security_id in sorted(
            eligible,
            key=lambda item: (security_keys.get(item, ""), str(item)),
        ):
            security_key = security_keys.get(security_id)
            if security_key is None:
                raise TrainableAggregationError("Target mask contains an unknown security")
            entry = open_by_key.get((security_id, entry_date))
            exit_point = open_by_key.get((security_id, exit_date))
            if entry is None or exit_point is None:
                # A provider gap or a frozen unavailable session makes this
                # security's realised label unobservable; it must not destroy
                # the otherwise valid cross-sectional training group.  The
                # omission is decided only after the target exit is known and
                # therefore cannot leak information into the prediction made
                # on ``decision_date``.
                continue
            raw_return = (exit_point.adjusted_open / entry.adjusted_open - 1).quantize(
                VALUE_QUANTUM, rounding=ROUND_HALF_EVEN
            )
            raw_values.append((security_id, security_key, raw_return))
        if len(raw_values) < 2:
            raise TrainableAggregationError(
                "Target cross-section has fewer than two complete adjusted-open "
                f"returns on {decision_date.isoformat()}"
            )
        centered = _average_rank_center(raw_values)
        label_known_at = session_open_at.get(exit_date)
        if label_known_at is None:
            raise TrainableAggregationError("Target exit session lacks a common open timestamp")
        for security_id, security_key, raw_return in raw_values:
            points.append(
                CrossSectionalTargetPoint(
                    security_id=security_id,
                    security_key=security_key,
                    decision_date=decision_date,
                    entry_date=entry_date,
                    exit_date=exit_date,
                    label_known_at=label_known_at,
                    raw_forward_return=raw_return,
                    centered_rank=centered[security_id],
                )
            )
        emitted_dates.append(decision_date)
    if not points:
        raise TrainableAggregationError("Target range produced no observations")
    points.sort(key=lambda item: (item.decision_date, item.security_key, str(item.security_id)))
    return FixedSessionTargetPanel(target, tuple(emitted_dates), tuple(points))


def build_training_matrix(
    feature_schema: FeatureSchema,
    target_panel: FixedSessionTargetPanel,
    features: Sequence[TrainingFeaturePoint],
    *,
    decision_cutoff_at_by_date: Mapping[date, datetime],
) -> TrainingMatrix:
    feature_by_key: dict[tuple[uuid.UUID, date, str], TrainingFeaturePoint] = {}
    allowed_keys = set(feature_schema.ordered_feature_keys)
    for point in features:
        _require_aware(point.known_at, "Feature known_at")
        if point.feature_key not in allowed_keys:
            raise TrainableAggregationError(
                f"Feature {point.feature_key} is not in the frozen Feature Schema"
            )
        key = (point.security_id, point.decision_date, point.feature_key)
        if key in feature_by_key:
            raise TrainableAggregationError("Duplicate training feature observation")
        feature_by_key[key] = point

    rows: list[TrainingMatrixRow] = []
    for target_point in target_panel.points:
        cutoff = decision_cutoff_at_by_date.get(target_point.decision_date)
        if cutoff is None:
            raise TrainableAggregationError("Training matrix lacks a decision cutoff")
        _require_aware(cutoff, "Decision cutoff")
        values: list[Decimal] = []
        for feature_key in feature_schema.ordered_feature_keys:
            feature = feature_by_key.get(
                (target_point.security_id, target_point.decision_date, feature_key)
            )
            if feature is None:
                raise TrainableAggregationError(
                    f"Training matrix is incomplete for {target_point.security_key} "
                    f"on {target_point.decision_date.isoformat()}"
                )
            if feature.known_at > cutoff:
                raise TrainableAggregationError("Training feature violates the decision cutoff")
            values.append(feature.value.quantize(VALUE_QUANTUM, rounding=ROUND_HALF_EVEN))
        rows.append(
            TrainingMatrixRow(
                security_id=target_point.security_id,
                security_key=target_point.security_key,
                decision_date=target_point.decision_date,
                decision_cutoff_at=cutoff,
                feature_values=tuple(values),
                target_value=target_point.centered_rank,
                target_known_at=target_point.label_known_at,
                target_entry_date=target_point.entry_date,
                target_exit_date=target_point.exit_date,
            )
        )
    rows.sort(key=lambda item: (item.decision_date, item.security_key, str(item.security_id)))
    dates = tuple(sorted({row.decision_date for row in rows}))
    return TrainingMatrix(feature_schema, target_panel.target, tuple(rows), dates)


def append_prediction_only_rows(
    matrix: TrainingMatrix,
    features: Sequence[TrainingFeaturePoint],
    *,
    prediction_start: date,
    prediction_end: date,
    candidate_security_ids_by_date: Mapping[date, frozenset[uuid.UUID]],
    security_keys_by_id: Mapping[uuid.UUID, str],
    decision_cutoff_at_by_date: Mapping[date, datetime],
) -> TrainingMatrix:
    """Complete the OOS prediction panel without inventing future labels."""

    feature_by_key = {
        (point.security_id, point.decision_date, point.feature_key): point
        for point in features
    }
    if len(feature_by_key) != len(features):
        raise TrainableAggregationError("Duplicate prediction feature observation")
    rows = list(matrix.rows)
    existing = {(row.security_id, row.decision_date) for row in rows}
    for decision_date in sorted(decision_cutoff_at_by_date):
        if not prediction_start <= decision_date <= prediction_end:
            continue
        cutoff = decision_cutoff_at_by_date[decision_date]
        _require_aware(cutoff, "Decision cutoff")
        candidates = candidate_security_ids_by_date.get(decision_date)
        if candidates is None or len(candidates) < 2:
            raise TrainableAggregationError(
                "Prediction cross-section requires at least two securities on "
                f"{decision_date.isoformat()}"
            )
        for security_id in sorted(
            candidates,
            key=lambda item: (security_keys_by_id.get(item, ""), str(item)),
        ):
            if (security_id, decision_date) in existing:
                continue
            security_key = security_keys_by_id.get(security_id)
            if security_key is None:
                raise TrainableAggregationError(
                    "Prediction mask contains an unknown security"
                )
            values: list[Decimal] = []
            for feature_key in matrix.feature_schema.ordered_feature_keys:
                feature = feature_by_key.get(
                    (security_id, decision_date, feature_key)
                )
                if feature is None:
                    raise TrainableAggregationError(
                        f"Prediction matrix is incomplete for {security_key} "
                        f"on {decision_date.isoformat()}"
                    )
                if feature.known_at > cutoff:
                    raise TrainableAggregationError(
                        "Prediction feature violates the decision cutoff"
                    )
                values.append(
                    feature.value.quantize(VALUE_QUANTUM, rounding=ROUND_HALF_EVEN)
                )
            rows.append(
                TrainingMatrixRow(
                    security_id=security_id,
                    security_key=security_key,
                    decision_date=decision_date,
                    decision_cutoff_at=cutoff,
                    feature_values=tuple(values),
                    target_value=Decimal(),
                    target_known_at=cutoff,
                    target_entry_date=decision_date,
                    target_exit_date=decision_date,
                    target_available=False,
                )
            )
    rows.sort(key=lambda item: (item.decision_date, item.security_key, str(item.security_id)))
    dates = tuple(sorted({row.decision_date for row in rows}))
    prediction_dates = {
        row.decision_date
        for row in rows
        if prediction_start <= row.decision_date <= prediction_end
    }
    expected_dates = {
        day
        for day in decision_cutoff_at_by_date
        if prediction_start <= day <= prediction_end
    }
    if prediction_dates != expected_dates:
        raise TrainableAggregationError(
            "Prediction matrix does not cover the exact frozen evaluation panel"
        )
    return TrainingMatrix(matrix.feature_schema, matrix.target, tuple(rows), dates)


def build_expanding_walk_forward_folds(
    matrix: TrainingMatrix,
    policy: WalkForwardPolicy,
    *,
    prediction_start: date,
    prediction_end: date,
) -> tuple[WalkForwardFold, ...]:
    if prediction_start > prediction_end:
        raise ValueError("Prediction start must not follow end")
    rows_by_date: dict[date, list[TrainingMatrixRow]] = {}
    for row in matrix.rows:
        rows_by_date.setdefault(row.decision_date, []).append(row)
    prediction_dates = tuple(
        day for day in matrix.decision_dates if prediction_start <= day <= prediction_end
    )
    if not prediction_dates:
        raise TrainableAggregationError("Walk-forward range has no prediction groups")

    folds: list[WalkForwardFold] = []
    for block_start in range(0, len(prediction_dates), policy.prediction_groups):
        prediction = prediction_dates[block_start : block_start + policy.prediction_groups]
        first_prediction = prediction[0]
        prediction_cutoff = min(row.decision_cutoff_at for row in rows_by_date[first_prediction])
        prior_dates = [day for day in matrix.decision_dates if day < first_prediction]
        mature_dates = []
        for day in prior_dates:
            labelled = [row for row in rows_by_date[day] if row.target_available]
            if (
                len(labelled) >= 2
                and max(row.target_known_at for row in labelled) <= prediction_cutoff
            ):
                mature_dates.append(day)
        if policy.embargo_groups:
            if len(mature_dates) <= policy.embargo_groups:
                raise TrainableAggregationError("Walk-forward fold has no data before embargo")
            mature_dates = mature_dates[: -policy.embargo_groups]
        if len(mature_dates) <= policy.validation_groups:
            raise TrainableAggregationError("Walk-forward fold lacks validation history")
        validation = tuple(mature_dates[-policy.validation_groups :])
        validation_cutoff = min(row.decision_cutoff_at for row in rows_by_date[validation[0]])
        train = tuple(
            day
            for day in mature_dates[: -policy.validation_groups]
            if max(
                row.target_known_at
                for row in rows_by_date[day]
                if row.target_available
            )
            <= validation_cutoff
        )
        if len(train) < policy.minimum_train_groups:
            raise TrainableAggregationError(
                "Walk-forward fold does not meet minimum mature training groups"
            )
        ordinal = len(folds)
        document = {
            "matrix_fingerprint": matrix.fingerprint,
            "policy_fingerprint": policy.fingerprint,
            "ordinal": ordinal,
            "train_dates": train,
            "validation_dates": validation,
            "prediction_dates": prediction,
        }
        folds.append(
            WalkForwardFold(
                ordinal=ordinal,
                train_dates=train,
                validation_dates=validation,
                prediction_dates=prediction,
                fold_fingerprint=sha256_hexdigest(document),
            )
        )
    return tuple(folds)


def _average_rank_center(
    values: Sequence[tuple[uuid.UUID, str, Decimal]],
) -> dict[uuid.UUID, Decimal]:
    ordered = sorted(values, key=lambda item: (item[2], item[1], str(item[0])))
    count = len(ordered)
    if count < 2:
        raise TrainableAggregationError("Rank centering requires at least two securities")
    result: dict[uuid.UUID, Decimal] = {}
    index = 0
    denominator = Decimal(count - 1)
    while index < count:
        end = index + 1
        while end < count and ordered[end][2] == ordered[index][2]:
            end += 1
        average_rank = (Decimal(index + 1) + Decimal(end)) / Decimal(2)
        centered = (
            Decimal(2) * (average_rank - Decimal(1)) / denominator - Decimal(1)
        ).quantize(VALUE_QUANTUM, rounding=ROUND_HALF_EVEN)
        for security_id, _, _ in ordered[index:end]:
            result[security_id] = centered
        index = end
    return result


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TrainableAggregationError(f"{label} must be timezone-aware")
