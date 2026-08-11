from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal

ZERO = Decimal(0)
ONE = Decimal(1)
WEIGHT_QUANTUM = Decimal("0.000000000000000001")


@dataclass(frozen=True, slots=True)
class StrategyVariantInput:
    variant_key: str
    target_k: int
    selection_order: str
    trend_filter: str


@dataclass(frozen=True, slots=True)
class CandidateModelInput:
    asset_id: uuid.UUID
    asset_key: str
    decision_date: date
    score: Decimal
    direction: str
    confidence: Decimal


@dataclass(frozen=True, slots=True)
class TargetAssetDecision:
    asset_id: uuid.UUID
    asset_key: str
    model_score: Decimal
    model_rank: Decimal
    selection_rank: Decimal | None
    trend_state: str | None
    strategy_eligible: bool
    selected: bool
    target_weight: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class PortfolioTargetDecision:
    variant_key: str
    decision_date: date
    target_k: int
    actual_holding_count: int
    boundary_tie_count: int
    reserve_target_weight: Decimal
    positions: tuple[TargetAssetDecision, ...]


class StrategyCalculationError(RuntimeError):
    """Raised when a formal target cannot be calculated without ambiguity."""


def calculate_target(
    variant: StrategyVariantInput,
    model_points: tuple[CandidateModelInput, ...],
    trend_states: dict[uuid.UUID, str] | None = None,
) -> PortfolioTargetDecision:
    _validate(variant, model_points, trend_states)
    decision_date = model_points[0].decision_date
    model_ranks = _average_ranks(model_points)
    states = trend_states or {}

    if variant.selection_order == "filter_then_rank_select":
        selection_pool = tuple(item for item in model_points if states[item.asset_id] == "positive")
    else:
        selection_pool = model_points
    selection_ranks = _average_ranks(selection_pool)
    selected_weights, boundary_tie_count = _slot_weights(selection_pool, variant.target_k)

    positions: list[TargetAssetDecision] = []
    for point in sorted(model_points, key=lambda item: item.asset_key):
        trend_state = states.get(point.asset_id)
        preselected_weight = selected_weights.get(point.asset_id, ZERO)
        strategy_eligible = trend_state == "positive" if states else True
        if variant.selection_order == "rank_select_then_filter":
            weight = preselected_weight if strategy_eligible else ZERO
        else:
            weight = preselected_weight
        reason = _reason(
            variant.selection_order,
            preselected_weight,
            weight,
            strategy_eligible,
            point.asset_id in selection_ranks,
            boundary_tie_count,
            selection_ranks.get(point.asset_id),
            variant.target_k,
        )
        positions.append(
            TargetAssetDecision(
                point.asset_id,
                point.asset_key,
                point.score,
                model_ranks[point.asset_id],
                selection_ranks.get(point.asset_id),
                trend_state,
                strategy_eligible,
                weight > ZERO,
                weight,
                reason,
            )
        )
    invested = sum((item.target_weight for item in positions), ZERO)
    reserve = ONE - invested
    if invested < ZERO or invested > ONE:
        raise StrategyCalculationError("Strategy target weights violate the portfolio budget")
    if variant.trend_filter == "none" and reserve != ZERO:
        raise StrategyCalculationError("Unfiltered Top-K must invest the complete target budget")
    return PortfolioTargetDecision(
        variant.variant_key,
        decision_date,
        variant.target_k,
        sum(item.selected for item in positions),
        boundary_tie_count,
        reserve,
        tuple(positions),
    )


def _validate(
    variant: StrategyVariantInput,
    points: tuple[CandidateModelInput, ...],
    states: dict[uuid.UUID, str] | None,
) -> None:
    if variant.target_k not in {1, 2, 3}:
        raise StrategyCalculationError("Strategy K must be one of 1, 2, or 3")
    if len(points) < variant.target_k:
        raise StrategyCalculationError("Candidate universe contains fewer assets than K")
    if not points:
        raise StrategyCalculationError("Strategy requires candidate Model points")
    if len({item.asset_id for item in points}) != len(points):
        raise StrategyCalculationError("Strategy received duplicate candidate assets")
    if len({item.decision_date for item in points}) != 1:
        raise StrategyCalculationError("Strategy points must share one decision date")
    if any(not item.score.is_finite() for item in points):
        raise StrategyCalculationError("Strategy received a non-finite Model score")
    expected = {
        "rank_then_select": "none",
        "rank_select_then_filter": "published_threshold_state",
        "filter_then_rank_select": "published_threshold_state",
    }
    if expected.get(variant.selection_order) != variant.trend_filter:
        raise StrategyCalculationError("Strategy filter order and trend contract are inconsistent")
    if variant.trend_filter == "none":
        if states is not None:
            raise StrategyCalculationError("Unfiltered strategy must not receive trend state input")
    else:
        if states is None or set(states) != {item.asset_id for item in points}:
            raise StrategyCalculationError("Trend strategy requires one state for every candidate")
        if any(state not in {"positive", "negative", "neutral"} for state in states.values()):
            raise StrategyCalculationError("Trend strategy received an unsupported state")


def _average_ranks(points: tuple[CandidateModelInput, ...]) -> dict[uuid.UUID, Decimal]:
    groups: dict[Decimal, list[CandidateModelInput]] = defaultdict(list)
    for point in points:
        groups[point.score].append(point)
    result: dict[uuid.UUID, Decimal] = {}
    position = 1
    for score in sorted(groups, reverse=True):
        group = groups[score]
        average = Decimal(position + position + len(group) - 1) / Decimal(2)
        result.update({item.asset_id: average for item in group})
        position += len(group)
    return result


def _slot_weights(
    points: tuple[CandidateModelInput, ...], target_k: int
) -> tuple[dict[uuid.UUID, Decimal], int]:
    groups: dict[Decimal, list[CandidateModelInput]] = defaultdict(list)
    for point in points:
        groups[point.score].append(point)
    weights: dict[uuid.UUID, Decimal] = {}
    remaining_slots = target_k
    boundary_tie_count = 0
    for score in sorted(groups, reverse=True):
        group = groups[score]
        if remaining_slots <= 0:
            break
        consumed = min(remaining_slots, len(group))
        group_weight = Decimal(consumed) / Decimal(target_k) / Decimal(len(group))
        weights.update({item.asset_id: group_weight for item in group})
        if len(group) > remaining_slots:
            boundary_tie_count = len(group)
        remaining_slots -= consumed
    if weights:
        # NUMERIC(24,18) cannot represent thirds exactly.  Round every slot down at the
        # storage scale.  When all slots are filled, assign only the microscopic arithmetic
        # residual (<= n e-18) to a deterministic selected row.  Otherwise the unfilled
        # budget becomes an exactly derived reserve weight in calculate_target().
        weights = {
            asset_id: weight.quantize(WEIGHT_QUANTUM, rounding=ROUND_DOWN)
            for asset_id, weight in weights.items()
        }
        if remaining_slots == 0:
            residual = ONE - sum(weights.values(), ZERO)
            residual_asset = min(weights, key=str)
            weights[residual_asset] += residual
    return weights, boundary_tie_count


def _reason(
    selection_order: str,
    preselected_weight: Decimal,
    final_weight: Decimal,
    eligible: bool,
    in_selection_pool: bool,
    boundary_tie_count: int,
    selection_rank: Decimal | None,
    target_k: int,
) -> str:
    if not in_selection_pool:
        return "trend_ineligible"
    if preselected_weight > ZERO and final_weight == ZERO:
        return "selected_then_trend_ineligible"
    if final_weight > ZERO:
        if boundary_tie_count and selection_rank is not None and selection_rank >= target_k:
            return "boundary_tie_selected"
        return "selected"
    if selection_order == "rank_select_then_filter" and not eligible:
        return "trend_ineligible_below_cutoff"
    return "below_cutoff"
