from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from style_rotation.v022.aggregation_work_runtime import (
    AggregationCalculation,
    AggregationOutputPoint,
)
from style_rotation.v022.defense_runtime import DefenseAllocationMember
from style_rotation.v022.product_runtime_pipeline import (
    ProductDefenseContract,
    ProductMemberState,
    ProductStrategyContract,
    calculate_product_target,
)
from style_rotation.v022.runtime_contract import V022RuntimeDataError


def test_product_target_uses_exact_member_eligibility_and_none_defense() -> None:
    calculation, members, decision_date, cutoff = _inputs()

    result = calculate_product_target(
        calculation,
        decision_date=decision_date,
        decision_cutoff_at=cutoff,
        members=members,
        strategy=_strategy(),
        defense=None,
    )

    assert len(result.strategy_target.positions) == 2
    assert all(item.asset_key != "asset_0" for item in result.strategy_target.positions)
    assert result.defense_decision is None
    assert result.merged_target.risk_budget == Decimal(1)
    assert result.merged_target.defense_budget == Decimal(0)


def test_product_target_reuses_fixed_defense_and_merge_contract() -> None:
    calculation, members, decision_date, cutoff = _inputs()
    defense = ProductDefenseContract(
        timing_variant_key="fixed20_budget",
        observations=(),
        expected_window_sessions=(),
        allocation_members=(
            DefenseAllocationMember(
                asset_id=None,
                asset_key="synthetic_reserve",
                component_role="reserve",
                sleeve_weight=Decimal(1),
                ordinal=0,
            ),
        ),
    )

    result = calculate_product_target(
        calculation,
        decision_date=decision_date,
        decision_cutoff_at=cutoff,
        members=members,
        strategy=_strategy(),
        defense=defense,
    )

    assert result.defense_decision is not None
    assert result.merged_target.risk_budget == Decimal("0.8")
    assert result.merged_target.defense_budget == Decimal("0.2")
    assert result.merged_target.reserve_target_weight == Decimal("0.2")


def test_product_target_rejects_signal_panel_missing_a_frozen_member() -> None:
    calculation, members, decision_date, cutoff = _inputs()
    incomplete = AggregationCalculation(
        calculation.family_key,
        calculation.parameter_preset_key,
        calculation.points[:-1],
        calculation.calculation_fingerprint,
    )

    with pytest.raises(V022RuntimeDataError) as error:
        calculate_product_target(
            incomplete,
            decision_date=decision_date,
            decision_cutoff_at=cutoff,
            members=members,
            strategy=_strategy(),
            defense=None,
        )

    assert error.value.reason_code == "product_signal_member_panel_mismatch"


def _strategy() -> ProductStrategyContract:
    return ProductStrategyContract(
        variant_key="cross_section_rank_top_k_parity",
        target_k=2,
        research_mode="formal",
        selection_buffer="none",
        sector_cap="none",
    )


def _inputs() -> tuple[
    AggregationCalculation,
    tuple[ProductMemberState, ...],
    date,
    datetime,
]:
    decision_date = date(2026, 8, 14)
    cutoff = datetime(2026, 8, 14, 20, tzinfo=UTC)
    member_rows = tuple(
        ProductMemberState(
            asset_id=uuid.uuid5(uuid.NAMESPACE_URL, f"product-member:{ordinal}"),
            asset_key=f"asset_{ordinal}",
            is_selectable=ordinal != 0,
        )
        for ordinal in range(5)
    )
    points = tuple(
        AggregationOutputPoint(
            asset_id=item.asset_id,
            asset_key=item.asset_key,
            decision_date=decision_date,
            signal_value=Decimal(10 - ordinal),
            known_at=cutoff,
            input_revision="a" * 64,
            missing_reason=None,
        )
        for ordinal, item in enumerate(member_rows)
    )
    return (
        AggregationCalculation(
            "flat_equal_weight_mean",
            "equal_v1",
            points,
            "b" * 64,
        ),
        member_rows,
        decision_date,
        cutoff,
    )
