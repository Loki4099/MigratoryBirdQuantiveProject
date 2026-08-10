from decimal import Decimal

import pytest

from style_rotation.strategy.v021_topk import (
    RankedAsset,
    build_topk_decision,
    internal_timing_defense_budget,
)


def _assets(count: int) -> tuple[RankedAsset, ...]:
    return tuple(
        RankedAsset(f"asset-{index}", Decimal(count - index), sector_key=f"s{index % 5}")
        for index in range(count)
    )


def test_four_etf_default_k2_and_two_etf_k2_gate() -> None:
    assert (
        build_topk_decision(
            _assets(4), family="multi_etf_top_k", target_k=2, research_mode="formal"
        ).status
        == "accepted"
    )
    blocked = build_topk_decision(
        _assets(2), family="multi_etf_top_k", target_k=2, research_mode="formal"
    )
    assert blocked.reason_code == "etf_k_exceeds_half_rankable"


def test_missing_score_is_excluded_and_coverage_is_a_hard_gate() -> None:
    assets = tuple(
        RankedAsset(f"asset-{index}", None if index == 0 else Decimal(index)) for index in range(10)
    )
    accepted = build_topk_decision(
        assets, family="multi_etf_top_k", target_k=1, research_mode="formal"
    )
    assert accepted.status == "accepted"
    failed = build_topk_decision(
        assets + (RankedAsset("missing-2", None),),
        family="multi_etf_top_k",
        target_k=1,
        research_mode="formal",
    )
    assert failed.reason_code == "rankable_coverage_below_90_percent"


def test_boundary_ties_share_slots_without_ticker_tiebreak() -> None:
    assets = (
        RankedAsset("a", Decimal("3")),
        RankedAsset("b", Decimal("2")),
        RankedAsset("c", Decimal("2")),
        RankedAsset("d", Decimal("1")),
    )
    decision = build_topk_decision(
        assets, family="multi_etf_top_k", target_k=2, research_mode="formal"
    )
    weights = {item.asset_key: item.target_weight for item in decision.positions}
    assert weights == {"a": Decimal("0.5"), "b": Decimal("0.25"), "c": Decimal("0.25")}


def test_large_boundary_tie_does_not_fail_on_decimal_rounding_dust() -> None:
    assets = tuple(
        RankedAsset(f"asset-{index:03d}", Decimal("1"), sector_key="shared")
        for index in range(81)
    )
    decision = build_topk_decision(
        assets,
        family="us_large_cap_top_k",
        target_k=10,
        research_mode="exploratory",
    )

    assert decision.status == "accepted"
    assert sum((item.slot_share for item in decision.positions), Decimal("0")) == Decimal("10")
    assert sum((item.target_weight for item in decision.positions), Decimal("0")) == Decimal("1")


def test_fixed_defense_boundary_tie_preserves_exact_risk_and_reserve_budget() -> None:
    # Mirrors the real K=10 / fixed-20 / half-K failure: eight full slots and
    # three alphabetically interleaved assets sharing the remaining two slots.
    full_slot_assets = ("alny", "bkng", "csco", "ma", "mstr", "nvda", "orcl", "ttwo")
    boundary_tie_assets = ("axon", "lrcx", "mpwr")
    assets = tuple(
        [
            RankedAsset(asset_key, Decimal(100 - index))
            for index, asset_key in enumerate(full_slot_assets)
        ]
        + [RankedAsset(asset_key, Decimal("50")) for asset_key in boundary_tie_assets]
        + [RankedAsset(f"unselected-{index:02d}", Decimal(-index - 1)) for index in range(39)]
    )

    decision = build_topk_decision(
        assets,
        family="us_large_cap_top_k",
        target_k=10,
        research_mode="exploratory",
        selection_buffer="half_k",
        defense_budget=Decimal("0.2"),
    )

    invested = sum((item.target_weight for item in decision.positions), Decimal("0"))
    assert decision.status == "accepted"
    assert len(decision.positions) == 11
    assert invested == Decimal("0.8")
    assert invested + decision.defense_budget == Decimal("1")


def test_stock_formal_threshold_sector_data_and_internal_defense() -> None:
    assert (
        build_topk_decision(
            _assets(99),
            family="us_large_cap_top_k",
            target_k=20,
            research_mode="formal",
        ).reason_code
        == "eligible_count_below_minimum"
    )
    assert internal_timing_defense_budget(
        spy_close=Decimal("103"), spy_sma200=Decimal("100")
    ) == Decimal("0")
    assert internal_timing_defense_budget(
        spy_close=Decimal("100"), spy_sma200=Decimal("100")
    ) == Decimal("0.2")
    with pytest.raises(ValueError, match="requires SPY"):
        internal_timing_defense_budget(spy_close=None, spy_sma200=Decimal("100"))
