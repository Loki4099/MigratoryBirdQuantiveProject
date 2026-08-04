from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from style_rotation.strategy.calculator import (
    CandidateModelInput,
    StrategyCalculationError,
    StrategyVariantInput,
    calculate_target,
)


def _points(scores: tuple[str, ...]) -> tuple[CandidateModelInput, ...]:
    return tuple(
        CandidateModelInput(
            uuid.UUID(int=index + 1),
            f"asset_{index + 1}",
            date(2026, 7, 31),
            Decimal(score),
            "positive" if Decimal(score) > 0 else "negative",
            abs(Decimal(score)),
        )
        for index, score in enumerate(scores)
    )


def _variant(order: str, trend: bool = False, k: int = 2) -> StrategyVariantInput:
    return StrategyVariantInput(
        f"test__k{k}",
        k,
        order,
        "published_threshold_state" if trend else "none",
    )


def test_plain_top_k_allocates_equal_slots_and_preserves_all_candidate_rows() -> None:
    result = calculate_target(_variant("rank_then_select"), _points(("1", ".5", "0", "-.5")))
    assert [item.target_weight for item in result.positions] == [
        Decimal("0.5"),
        Decimal("0.5"),
        Decimal(0),
        Decimal(0),
    ]
    assert result.reserve_target_weight == 0
    assert result.actual_holding_count == 2
    assert [item.reason for item in result.positions] == [
        "selected",
        "selected",
        "below_cutoff",
        "below_cutoff",
    ]


def test_boundary_tie_shares_remaining_slot_without_ticker_tiebreak() -> None:
    result = calculate_target(_variant("rank_then_select"), _points(("1", ".5", ".5", "0")))
    assert [item.target_weight for item in result.positions] == [
        Decimal("0.5"),
        Decimal("0.25"),
        Decimal("0.25"),
        Decimal(0),
    ]
    assert result.actual_holding_count == 3
    assert result.boundary_tie_count == 2
    assert [item.model_rank for item in result.positions] == [
        Decimal(1),
        Decimal("2.5"),
        Decimal("2.5"),
        Decimal(4),
    ]


def test_post_selection_filter_does_not_backfill_failed_selected_slot() -> None:
    points = _points(("1", ".5", "0", "-.5"))
    states = {item.asset_id: "positive" for item in points}
    states[points[1].asset_id] = "negative"
    result = calculate_target(_variant("rank_select_then_filter", trend=True), points, states)
    assert [item.target_weight for item in result.positions] == [
        Decimal("0.5"),
        Decimal(0),
        Decimal(0),
        Decimal(0),
    ]
    assert result.reserve_target_weight == Decimal("0.5")
    assert result.positions[1].reason == "selected_then_trend_ineligible"
    assert result.positions[2].reason == "below_cutoff"


def test_pre_selection_filter_backfills_with_lower_ranked_eligible_asset() -> None:
    points = _points(("1", ".5", "0", "-.5"))
    states = {item.asset_id: "positive" for item in points}
    states[points[1].asset_id] = "neutral"
    result = calculate_target(_variant("filter_then_rank_select", trend=True), points, states)
    assert [item.target_weight for item in result.positions] == [
        Decimal("0.5"),
        Decimal(0),
        Decimal("0.5"),
        Decimal(0),
    ]
    assert result.reserve_target_weight == 0
    assert result.positions[1].reason == "trend_ineligible"


def test_missing_trend_state_is_error_not_reserve() -> None:
    points = _points(("1", ".5", "0", "-.5"))
    with pytest.raises(StrategyCalculationError, match="one state for every candidate"):
        calculate_target(
            _variant("filter_then_rank_select", trend=True),
            points,
            {item.asset_id: "positive" for item in points[:-1]},
        )
