from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from style_rotation.v022.aggregation_work_runtime import AggregationCalculation
from style_rotation.v022.defense_runtime import (
    DefenseAllocationMember,
    DefenseDecision,
    DefensePriceObservation,
    MergedPortfolioTarget,
    TimingVariant,
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
    StrategyVariant,
    build_unit_risk_topk_target,
)


@dataclass(frozen=True, slots=True)
class ProductMemberState:
    asset_id: uuid.UUID
    asset_key: str
    is_selectable: bool
    previously_held: bool = False
    sector_key: str | None = None

    def __post_init__(self) -> None:
        if not self.asset_key.strip():
            raise V022RuntimeContractError(
                "product_member_key_blank",
                "Product member Asset key must be nonblank",
            )


@dataclass(frozen=True, slots=True)
class ProductStrategyContract:
    variant_key: StrategyVariant
    target_k: int
    research_mode: Literal["formal", "exploratory"]
    selection_buffer: Literal["none", "half_k"]
    sector_cap: Literal["none", "pit_30_percent"]


@dataclass(frozen=True, slots=True)
class ProductDefenseContract:
    timing_variant_key: TimingVariant
    observations: tuple[DefensePriceObservation, ...]
    expected_window_sessions: tuple[date, ...]
    allocation_members: tuple[DefenseAllocationMember, ...]


@dataclass(frozen=True, slots=True)
class ProductTargetCalculation:
    aggregation_calculation_fingerprint: str
    strategy_target: StrategyUnitRiskTarget
    defense_decision: DefenseDecision | None
    merged_target: MergedPortfolioTarget


def calculate_product_target(
    aggregation: AggregationCalculation,
    *,
    decision_date: date,
    decision_cutoff_at: datetime,
    members: tuple[ProductMemberState, ...],
    strategy: ProductStrategyContract,
    defense: ProductDefenseContract | None,
) -> ProductTargetCalculation:
    """Calculate one Product target from exact frozen members and signal evidence."""

    if decision_cutoff_at.utcoffset() is None:
        raise V022RuntimeContractError(
            "product_decision_cutoff_naive",
            "Product decision cutoff must be timezone-aware",
        )
    if decision_cutoff_at.date() != decision_date:
        raise V022RuntimeContractError(
            "product_decision_cutoff_mismatch",
            "Product decision date must match its exact cutoff",
        )
    canonical_members = tuple(sorted(members, key=lambda item: item.asset_key))
    if not canonical_members or canonical_members != members:
        raise V022RuntimeContractError(
            "product_member_order_invalid",
            "Product members must be nonempty and ordered by Asset key",
        )
    member_ids = tuple(item.asset_id for item in members)
    member_keys = tuple(item.asset_key for item in members)
    if len(member_ids) != len(set(member_ids)) or len(member_keys) != len(set(member_keys)):
        raise V022RuntimeContractError(
            "product_member_identity_duplicate",
            "Product member Asset identities and keys must be unique",
        )
    signal_points = tuple(
        item for item in aggregation.points if item.decision_date == decision_date
    )
    points_by_id = {item.asset_id: item for item in signal_points}
    if (
        len(points_by_id) != len(signal_points)
        or set(points_by_id) != set(member_ids)
        or any(
            points_by_id[item.asset_id].asset_key != item.asset_key
            for item in members
        )
    ):
        raise V022RuntimeDataError(
            "product_signal_member_panel_mismatch",
            "Product signal does not reproduce the exact frozen decision-session members",
        )
    input_known_at = max(item.known_at for item in signal_points)
    strategy_target = build_unit_risk_topk_target(
        tuple(
            StrategyAssetInput(
                asset_id=item.asset_id,
                asset_key=item.asset_key,
                model_score=points_by_id[item.asset_id].signal_value,
                eligible=item.is_selectable,
                sector_key=item.sector_key,
                previously_held=item.previously_held,
            )
            for item in members
        ),
        decision_date=decision_date,
        decision_cutoff_at=decision_cutoff_at,
        input_known_at=input_known_at,
        variant_key=strategy.variant_key,
        target_k=strategy.target_k,
        research_mode=strategy.research_mode,
        selection_buffer=strategy.selection_buffer,
        sector_cap=strategy.sector_cap,
    )
    defense_decision = (
        None
        if defense is None
        else evaluate_defense_timing(
            timing_variant_key=defense.timing_variant_key,
            decision_date=decision_date,
            decision_cutoff_at=decision_cutoff_at,
            observations=defense.observations,
            expected_window_sessions=defense.expected_window_sessions,
        )
    )
    merged = merge_sleeves(
        strategy_target,
        defense_decision=defense_decision,
        allocation_members=(
            () if defense is None else defense.allocation_members
        ),
    )
    return ProductTargetCalculation(
        aggregation.calculation_fingerprint,
        strategy_target,
        defense_decision,
        merged,
    )
