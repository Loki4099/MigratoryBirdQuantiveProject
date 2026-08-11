from __future__ import annotations

import uuid
from datetime import date

import pytest

from style_rotation.signal.diagnostics import (
    EvaluationReturn,
    EvaluationSignal,
    EvaluationValue,
    calculate_signal_diagnostics,
)


def _fixture() -> tuple[
    tuple[EvaluationSignal, ...], tuple[EvaluationReturn, ...], frozenset[uuid.UUID]
]:
    assets = tuple((uuid.uuid4(), key) for key in ("iwf", "iwd", "iwo", "iwn"))
    days = (date(2025, 1, 3), date(2025, 1, 10))
    returns = tuple(
        EvaluationReturn(asset_id, key, day, float(index + day.day / 100))
        for day in days
        for index, (asset_id, key) in enumerate(assets)
    )
    first = EvaluationSignal(
        uuid.uuid4(),
        uuid.uuid4(),
        "ordered",
        "continuous",
        tuple(
            EvaluationValue(asset_id, key, day, float(index), None, None)
            for day in days
            for index, (asset_id, key) in enumerate(assets)
        ),
    )
    second = EvaluationSignal(
        uuid.uuid4(),
        uuid.uuid4(),
        "constant_event",
        "crossover_event",
        tuple(
            EvaluationValue(asset_id, key, day, 0.0, "neutral", False)
            for day in days
            for asset_id, key in assets
        ),
    )
    return (first, second), returns, frozenset(asset_id for asset_id, _key in assets)


def test_diagnostics_calculate_directional_metrics_events_stability_and_redundancy() -> None:
    signals, returns, candidates = _fixture()
    result = calculate_signal_diagnostics(signals, returns, candidates, frequency="weekly")
    assert len(result.periods) == 4
    ordered = [
        item for item in result.periods if item.signal_dataset_id == signals[0].signal_dataset_id
    ]
    assert [item.rank_ic for item in ordered] == pytest.approx([1.0, 1.0])
    assert all(item.top_bottom_spread > 0 for item in ordered)
    full = next(
        item
        for item in result.metrics
        if item.signal_dataset_id == signals[0].signal_dataset_id and item.window_key == "full"
    )
    assert full.mean_rank_ic == pytest.approx(1.0)
    assert full.positive_ic_ratio == pytest.approx(1.0)
    assert full.information_ratio is None
    event = next(
        item
        for item in result.metrics
        if item.signal_dataset_id == signals[1].signal_dataset_id and item.window_key == "full"
    )
    assert event.event_rate == 0
    assert event.event_asset_concentration is None
    assert event.non_neutral_rate == 0
    assert full.mean_top2_turnover == 0
    assert {item.window_key for item in result.metrics} == {"full", "year:2025"}
    assert len(result.pairs) == 1
    assert result.pairs[0].mean_top2_overlap == pytest.approx(0.0)
    assert {item.issue_code for item in result.issues} == {
        "all_rank_ic_undefined",
        "short_evaluation_sample",
    }


def test_diagnostics_reject_incomplete_candidate_cross_section() -> None:
    signals, returns, candidates = _fixture()
    with pytest.raises(ValueError, match="all four candidates"):
        calculate_signal_diagnostics(signals, returns[:-1], candidates, frequency="weekly")
