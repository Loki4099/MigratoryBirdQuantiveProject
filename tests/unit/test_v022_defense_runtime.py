from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from style_rotation.v022.defense_runtime import (
    DefenseAllocationMember,
    DefensePriceObservation,
    evaluate_defense_timing,
    merge_sleeves,
)
from style_rotation.v022.runtime_contract import (
    V022RuntimeContractError,
    V022RuntimeDataError,
)
from style_rotation.v022.strategy_compat_runtime import (
    StrategyAssetInput,
    StrategyUnitRiskTarget,
    UnitRiskPosition,
    build_unit_risk_topk_target,
)

D = Decimal
ASSET_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ASSET_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
DECISION_DATE = date(2025, 8, 8)
CUTOFF = datetime(2025, 8, 8, 21, tzinfo=UTC)
STRATEGY_INPUT_KNOWN_AT = CUTOFF - timedelta(hours=2)


def _observations(first: str, latest: str) -> tuple[DefensePriceObservation, ...]:
    reversed_sessions: list[date] = []
    cursor = DECISION_DATE
    while len(reversed_sessions) < 200:
        if cursor.weekday() < 5:
            reversed_sessions.append(cursor)
        cursor -= timedelta(days=1)
    sessions = tuple(reversed(reversed_sessions))
    prices = (D(first),) + (D("100"),) * 198 + (D(latest),)
    return tuple(
        DefensePriceObservation(
            session_date=session,
            known_at=datetime.combine(
                session, datetime.min.time(), tzinfo=UTC
            ),
            adjusted_close=price,
        )
        for session, price in zip(sessions, prices, strict=True)
    )


def _sessions(
    observations: tuple[DefensePriceObservation, ...],
) -> tuple[date, ...]:
    return tuple(item.session_date for item in observations[-200:])


def _strategy_target() -> StrategyUnitRiskTarget:
    return StrategyUnitRiskTarget(
        decision_date=DECISION_DATE,
        decision_cutoff_at=CUTOFF,
        input_known_at=STRATEGY_INPUT_KNOWN_AT,
        eligible_count=2,
        rankable_count=2,
        coverage_ratio=D(1),
        positions=(
            UnitRiskPosition(ASSET_A, "asset_a", D(2), 1, D(1), D("0.5"), False),
            UnitRiskPosition(ASSET_B, "asset_b", D(1), 2, D(1), D("0.5"), False),
        ),
    )


def _allocation(*, asset_a_eligible: bool = True) -> tuple[DefenseAllocationMember, ...]:
    return (
        DefenseAllocationMember(None, "synthetic_reserve", "reserve", D("0.4"), 0),
        DefenseAllocationMember(
            ASSET_A,
            "asset_a",
            "defensive_asset",
            D("0.6"),
            1,
            eligible=asset_a_eligible,
        ),
    )


def test_fixed20_has_no_market_input_dependency() -> None:
    decision = evaluate_defense_timing(
        "fixed20_budget", decision_date=DECISION_DATE, decision_cutoff_at=CUTOFF
    )
    assert decision.risk_budget == D("0.8")
    assert decision.defense_budget == D("0.2")
    assert decision.indicator_value is None
    assert decision.input_known_at is None

    with pytest.raises(V022RuntimeContractError) as error:
        evaluate_defense_timing(
            "fixed20_budget",
            decision_date=DECISION_DATE,
            decision_cutoff_at=CUTOFF,
            observations=_observations("98", "102"),
        )
    assert error.value.reason_code == "fixed20_input_forbidden"


@pytest.mark.parametrize(
    ("first", "latest", "regime", "defense_budget", "indicator"),
    [
        ("97", "103", "above_upper", "0", "0.030000000000000000"),
        ("98", "102", "middle", "0.2", "0.020000000000000000"),
        ("102", "98", "middle", "0.2", "-0.020000000000000000"),
        ("103", "97", "below_lower", "0.4", "-0.030000000000000000"),
    ],
)
def test_ma200_tiers_keep_strict_outer_and_inclusive_middle_boundaries(
    first: str,
    latest: str,
    regime: str,
    defense_budget: str,
    indicator: str,
) -> None:
    observations = _observations(first, latest)
    decision = evaluate_defense_timing(
        "spy_ma200_tiered_budget",
        decision_date=DECISION_DATE,
        decision_cutoff_at=CUTOFF,
        observations=observations,
        expected_window_sessions=_sessions(observations),
    )
    assert decision.regime_key == regime
    assert decision.defense_budget == D(defense_budget)
    assert decision.indicator_value == D(indicator)
    assert decision.input_known_at is not None
    assert decision.input_known_at <= CUTOFF


def test_ma200_fails_closed_on_short_stale_or_late_exact_input() -> None:
    complete = _observations("98", "102")
    with pytest.raises(V022RuntimeDataError) as short_error:
        evaluate_defense_timing(
            "spy_ma200_tiered_budget",
            decision_date=DECISION_DATE,
            decision_cutoff_at=CUTOFF,
            observations=complete[:-1],
            expected_window_sessions=_sessions(complete),
        )
    assert short_error.value.reason_code == "spy_history_below_required"

    with pytest.raises(V022RuntimeDataError) as stale_error:
        evaluate_defense_timing(
            "spy_ma200_tiered_budget",
            decision_date=DECISION_DATE + timedelta(days=1),
            decision_cutoff_at=CUTOFF + timedelta(days=1),
            observations=complete,
            expected_window_sessions=_sessions(complete)[:-1]
            + (DECISION_DATE + timedelta(days=1),),
        )
    assert stale_error.value.reason_code == "timing_input_stale"

    late = complete[:-1] + (replace(complete[-1], known_at=CUTOFF + timedelta(seconds=1)),)
    with pytest.raises(V022RuntimeDataError) as late_error:
        evaluate_defense_timing(
            "spy_ma200_tiered_budget",
            decision_date=DECISION_DATE,
            decision_cutoff_at=CUTOFF,
            observations=late,
            expected_window_sessions=_sessions(complete),
        )
    assert late_error.value.reason_code == "defense_signal_available_after_cutoff"


def test_ma200_binds_exact_common_session_window_and_ignores_unused_history_cutoff() -> None:
    complete = _observations("98", "102")
    mismatched = list(_sessions(complete))
    gap_index = next(
        index
        for index, (left, right) in enumerate(
            zip(mismatched, mismatched[1:], strict=False)
        )
        if (right - left).days > 1
    )
    mismatched[gap_index] += timedelta(days=1)
    mismatched_sessions = tuple(mismatched)
    with pytest.raises(V022RuntimeDataError) as mismatch_error:
        evaluate_defense_timing(
            "spy_ma200_tiered_budget",
            decision_date=DECISION_DATE,
            decision_cutoff_at=CUTOFF,
            observations=complete,
            expected_window_sessions=mismatched_sessions,
        )
    assert mismatch_error.value.reason_code == "defense_session_window_mismatch"

    unused = DefensePriceObservation(
        session_date=complete[0].session_date - timedelta(days=1),
        known_at=CUTOFF + timedelta(days=1),
        adjusted_close=D("50"),
    )
    decision = evaluate_defense_timing(
        "spy_ma200_tiered_budget",
        decision_date=DECISION_DATE,
        decision_cutoff_at=CUTOFF,
        observations=(unused,) + complete,
        expected_window_sessions=_sessions(complete),
    )
    assert decision.regime_key == "middle"


def test_merge_preserves_sleeve_attribution_and_nets_overlapping_asset() -> None:
    decision = evaluate_defense_timing(
        "fixed20_budget", decision_date=DECISION_DATE, decision_cutoff_at=CUTOFF
    )
    merged = merge_sleeves(
        _strategy_target(), defense_decision=decision, allocation_members=_allocation()
    )

    assert [(item.sleeve, item.asset_key) for item in merged.contributions] == [
        ("risk", "asset_a"),
        ("risk", "asset_b"),
        ("defense", "asset_a"),
        ("reserve", "synthetic_reserve"),
    ]
    assert {item.asset_key: item.target_weight for item in merged.net_asset_weights} == {
        "asset_a": D("0.52"),
        "asset_b": D("0.40"),
    }
    assert merged.reserve_target_weight == D("0.08")
    assert merged.decision_cutoff_at == CUTOFF
    assert merged.input_known_at == STRATEGY_INPUT_KNOWN_AT
    assert sum((item.target_weight for item in merged.net_asset_weights), D(0)) + D(
        "0.08"
    ) == D(1)


def test_null_defense_has_no_fake_artifact_or_sleeve_and_ineligible_member_fails() -> None:
    unscaled = merge_sleeves(_strategy_target())
    assert unscaled.risk_budget == D(1)
    assert unscaled.defense_budget == D(0)
    assert {item.sleeve for item in unscaled.contributions} == {"risk"}
    assert unscaled.reserve_target_weight == D(0)

    decision = evaluate_defense_timing(
        "fixed20_budget", decision_date=DECISION_DATE, decision_cutoff_at=CUTOFF
    )
    with pytest.raises(V022RuntimeDataError) as error:
        merge_sleeves(
            _strategy_target(),
            defense_decision=decision,
            allocation_members=_allocation(asset_a_eligible=False),
        )
    assert error.value.reason_code == "defense_allocation_member_ineligible"


@pytest.mark.parametrize("asset_count", [51, 101, 300])
def test_large_tie_and_allocation_scaling_absorb_only_tiny_canonical_residual(
    asset_count: int,
) -> None:
    raw_assets = tuple(
        StrategyAssetInput(uuid.UUID(int=index + 1), f"stock_{index:03d}", D(1))
        for index in range(asset_count)
    )
    unit_target = build_unit_risk_topk_target(
        raw_assets,
        decision_date=DECISION_DATE,
        decision_cutoff_at=CUTOFF,
        input_known_at=STRATEGY_INPUT_KNOWN_AT,
        variant_key="cross_section_rank_top_k_large_cap_parity",
        target_k=10,
        research_mode="exploratory",
        selection_buffer="half_k",
        sector_cap="none",
    )
    equal_share = D(1) / D(asset_count)
    member_weights = [equal_share for _ in range(asset_count)]
    member_weights[0] += D(1) - sum(member_weights, D(0))
    members = tuple(
        DefenseAllocationMember(
            uuid.UUID(int=10_000 + index),
            f"defense_{index:03d}",
            "defensive_asset",
            member_weights[index],
            index,
        )
        for index in range(asset_count)
    )
    decision = evaluate_defense_timing(
        "fixed20_budget", decision_date=DECISION_DATE, decision_cutoff_at=CUTOFF
    )

    merged = merge_sleeves(
        unit_target, defense_decision=decision, allocation_members=members
    )

    risk_contributions = tuple(
        item for item in merged.contributions if item.sleeve == "risk"
    )
    raw_legacy_risk = tuple(item.unit_risk_weight * D("0.8") for item in unit_target.positions)
    legacy_residual = D("0.8") - sum(raw_legacy_risk, D(0))
    expected_risk = list(raw_legacy_risk)
    expected_risk[0] += legacy_residual
    expected_risk[0] += D("0.8") - sum(expected_risk, D(0))
    assert tuple(item.portfolio_weight for item in risk_contributions) == tuple(expected_risk)
    assert sum(
        (item.portfolio_weight for item in risk_contributions),
        D(0),
    ) == D("0.8")
    assert sum(
        (item.portfolio_weight for item in merged.contributions if item.sleeve == "defense"),
        D(0),
    ) == D("0.2")


def test_merge_preserves_latest_exact_input_and_rejects_cutoff_drift() -> None:
    decision = evaluate_defense_timing(
        "spy_ma200_tiered_budget",
        decision_date=DECISION_DATE,
        decision_cutoff_at=CUTOFF,
        observations=_observations("98", "102"),
        expected_window_sessions=_sessions(_observations("98", "102")),
    )
    later_known_at = CUTOFF - timedelta(minutes=1)
    decision = replace(decision, input_known_at=later_known_at)
    merged = merge_sleeves(
        _strategy_target(),
        defense_decision=decision,
        allocation_members=_allocation(),
    )
    assert merged.decision_cutoff_at == CUTOFF
    assert merged.input_known_at == later_known_at

    with pytest.raises(V022RuntimeContractError) as cutoff_error:
        merge_sleeves(
            _strategy_target(),
            defense_decision=replace(
                decision, decision_cutoff_at=CUTOFF + timedelta(minutes=1)
            ),
            allocation_members=_allocation(),
        )
    assert cutoff_error.value.reason_code == "sleeve_decision_cutoff_mismatch"
