from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from style_rotation.v022.compact_trainable_ensemble import (
    CompactTrainableEnsembleResult,
    combine_compact_member_scores,
    combine_compact_trainable_members,
    compact_member_execution,
    q18_decimal,
    security_uuid,
)
from style_rotation.v022.linear_trainable_aggregation import OofPredictionPoint
from style_rotation.v022.trainable_aggregation import TrainingMatrixRow
from style_rotation.v022.trainable_aggregation_work_runtime import (
    _compact_ensemble_calculation,
    _compact_member_from_published_oof,
)
from style_rotation.v022.trainable_ensemble import (
    EnsembleMemberPrediction,
    combine_trainable_oof_members,
)
from style_rotation.v022.trainable_ensemble_diagnostics import (
    EnsembleDiagnosticMemberInput,
    calculate_trainable_ensemble_diagnostic,
)

ASSETS = tuple((uuid.UUID(int=index), f"asset_{index}") for index in (1, 2, 3))
DAYS = (date(2020, 1, 2), date(2020, 1, 3))


def test_compact_multi_target_matches_canonical_small_panel() -> None:
    members = (
        _member("h5", "alpha", (("-1", "0", "1"), ("1", "0", "-1")), "a"),
        _member("h5", "beta", (("-1", "1", "0"), ("1", "-1", "0")), "b"),
        _member("h21", "alpha", (("1", "0", "-1"), ("-1", "0", "1")), "c"),
    )
    canonical = combine_trainable_oof_members(
        "ridge_cross_sectional_regression",
        tuple(
            EnsembleMemberPrediction(
                item.target_key,
                item.training_preset_key,
                item.prediction_fingerprint,
                item.predictions,
            )
            for item in members
        ),
    )
    canonical_diagnostic = calculate_trainable_ensemble_diagnostic(
        "ridge_cross_sectional_regression",
        members,
        ensemble_fingerprint="e" * 64,
    )
    compact = tuple(
        compact_member_execution(
            target_key=item.target_key,
            training_preset_key=item.training_preset_key,
            prediction_fingerprint=item.prediction_fingerprint,
            fold_count=item.fold_count,
            predictions=item.predictions,
            matrix_rows=item.target_rows,
        )
        for item in members
    )

    result, diagnostic = combine_compact_trainable_members(
        "ridge_cross_sectional_regression",
        compact,
        ensemble_fingerprint="e" * 64,
    )

    observed = tuple(
        (
            date.fromordinal(int(result.session_ordinals[index])),
            security_uuid(result.security_id_bytes[index]),
            q18_decimal(score),
        )
        for index, score in enumerate(result.centered_scores)
    )
    expected = tuple(
        (item.decision_date, item.security_id, item.centered_rank) for item in canonical.predictions
    )
    assert observed == expected
    assert diagnostic.diagnostic_document == canonical_diagnostic.diagnostic_document


def test_compact_output_normalizes_security_key_order_to_canonical_uuid_order() -> None:
    first = uuid.UUID(int=1)
    last = uuid.UUID(int=2**128 - 1)
    result = CompactTrainableEnsembleResult(
        family_key="lightgbm_cross_sectional_regression",
        ordered_members=(),
        ordered_target_keys=("forward_rank_h5",),
        session_ordinals=np.asarray([DAYS[0].toordinal()] * 2, dtype=np.int32),
        # Deliberately mirror an OOF payload whose security-key order differs
        # from the final payload's canonical UUID-string order.
        security_id_bytes=np.asarray(
            [np.frombuffer(last.bytes, dtype=np.uint8), np.frombuffer(first.bytes, dtype=np.uint8)]
        ),
        centered_scores=np.asarray([10**18, -(10**18)], dtype=np.int64),
        fingerprint="f" * 64,
    )
    context = cast(
        Any,
        SimpleNamespace(
            ensemble_fingerprint="e" * 64,
            ensemble_spec_id=uuid.UUID(int=3),
            family_key="lightgbm_cross_sectional_regression",
            asset_keys={first: "zzz", last: "aaa"},
        ),
    )

    calculation = _compact_ensemble_calculation(
        context,
        result,
        {DAYS[0]: datetime(2020, 1, 2, 21, tzinfo=UTC)},
    )

    assert tuple(point.asset_id for point in calculation.points) == (first, last)


def test_cached_oof_scores_reproduce_fresh_ensemble_result() -> None:
    source = (
        _member("h5", "alpha", (("-1", "0", "1"), ("1", "0", "-1")), "a"),
        _member("h21", "alpha", (("1", "0", "-1"), ("-1", "0", "1")), "b"),
    )
    compact = tuple(
        compact_member_execution(
            target_key=item.target_key,
            training_preset_key=item.training_preset_key,
            prediction_fingerprint=item.prediction_fingerprint,
            fold_count=item.fold_count,
            predictions=item.predictions,
            matrix_rows=item.target_rows,
        )
        for item in source
    )
    fresh, _ = combine_compact_trainable_members(
        "lightgbm_cross_sectional_regression",
        compact,
        ensemble_fingerprint="e" * 64,
    )

    cached = combine_compact_member_scores(
        "lightgbm_cross_sectional_regression",
        compact,
        ensemble_fingerprint="e" * 64,
    )

    assert cached.fingerprint == fresh.fingerprint
    assert np.array_equal(cached.centered_scores, fresh.centered_scores)
    assert np.array_equal(cached.session_ordinals, fresh.session_ordinals)
    assert np.array_equal(cached.security_id_bytes, fresh.security_id_bytes)


def test_published_oof_decodes_to_exact_compact_checkpoint() -> None:
    sink = io.BytesIO()
    rows = [
        (day, str(security_id), score)
        for day in DAYS
        for (security_id, _), score in zip(
            ASSETS, ("-1.000000000000000000", "0", "1.000000000000000000"), strict=True
        )
    ]
    pq.write_table(
        pa.table(
            {
                "session_date": [item[0] for item in rows],
                "asset_id": [item[1] for item in rows],
                "feature_value": [item[2] for item in rows],
            }
        ),
        sink,
    )

    member = _compact_member_from_published_oof(
        sink.getvalue(),
        target_key="h5",
        training_preset_key="balanced",
        prediction_fingerprint="f" * 64,
        fold_count=2,
        expected_row_count=len(rows),
        expected_group_count=len(DAYS),
        expected_start=DAYS[0],
        expected_end=DAYS[-1],
    )

    assert tuple(date.fromordinal(int(item)) for item in member.session_ordinals) == tuple(
        day for day in DAYS for _ in ASSETS
    )
    assert tuple(security_uuid(item) for item in member.security_id_bytes) == tuple(
        security_id for _ in DAYS for security_id, _ in ASSETS
    )
    assert tuple(q18_decimal(item) for item in member.centered_scores[:3]) == (
        Decimal("-1"),
        Decimal("0"),
        Decimal("1"),
    )


def _member(
    target_key: str,
    preset_key: str,
    scores: tuple[tuple[str, str, str], tuple[str, str, str]],
    fingerprint_character: str,
) -> EnsembleDiagnosticMemberInput:
    predictions = tuple(
        OofPredictionPoint(
            security_id=security_id,
            security_key=security_key,
            decision_date=day,
            raw_prediction=Decimal(score),
            centered_rank=Decimal(score),
            fold_ordinal=day_ordinal,
        )
        for day_ordinal, (day, day_scores) in enumerate(zip(DAYS, scores, strict=True))
        for (security_id, security_key), score in zip(ASSETS, day_scores, strict=True)
    )
    rows = tuple(
        TrainingMatrixRow(
            security_id=security_id,
            security_key=security_key,
            decision_date=day,
            decision_cutoff_at=datetime(2020, 1, 1, tzinfo=UTC),
            feature_values=(Decimal("0"),),
            target_value=Decimal(target_score),
            target_known_at=datetime(2020, 2, 1, tzinfo=UTC),
            target_entry_date=day,
            target_exit_date=date(2020, 2, 1),
        )
        for day, day_scores in zip(
            DAYS,
            (("-1", "0", "1"), ("1", "0", "-1")),
            strict=True,
        )
        for (security_id, security_key), target_score in zip(ASSETS, day_scores, strict=True)
    )
    return EnsembleDiagnosticMemberInput(
        target_key=target_key,
        training_preset_key=preset_key,
        prediction_fingerprint=fingerprint_character * 64,
        fold_count=2,
        predictions=predictions,
        target_rows=rows,
    )
