from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from style_rotation.v022.runtime_contract import (
    V022RuntimeContractError,
    V022RuntimeDataError,
)

StrategyVariant = Literal[
    "cross_section_rank_top_k_parity",
    "cross_section_rank_top_k_large_cap_parity",
    "cross_section_rank_top_k_large_cap_multi_frequency",
]
DefenseVariant = Literal["none", "fixed20_defense", "ma200_tiered_defense"]


@dataclass(frozen=True, slots=True)
class RankedAsset:
    asset_key: str
    model_score: Decimal | None
    eligible: bool = True
    sector_key: str | None = None
    previously_held: bool = False


@dataclass(frozen=True, slots=True)
class TargetPosition:
    asset_key: str
    model_score: Decimal
    rank: int
    slot_share: Decimal
    target_weight: Decimal
    retained_by_buffer: bool


@dataclass(frozen=True, slots=True)
class TopKDecision:
    status: Literal["accepted", "failed"]
    reason_code: str | None
    eligible_count: int
    rankable_count: int
    coverage_ratio: Decimal
    positions: tuple[TargetPosition, ...]
    risk_budget: Decimal
    defense_budget: Decimal


@dataclass(frozen=True, slots=True)
class StrategyAssetInput:
    asset_id: uuid.UUID
    asset_key: str
    model_score: Decimal | None
    eligible: bool = True
    sector_key: str | None = None
    previously_held: bool = False

    def __post_init__(self) -> None:
        if not self.asset_key.strip():
            raise V022RuntimeContractError(
                "strategy_asset_key_blank", "Strategy Asset key must be nonblank"
            )
        if self.model_score is not None and not self.model_score.is_finite():
            raise V022RuntimeDataError(
                "strategy_score_nonfinite",
                f"Strategy score is non-finite for {self.asset_key}",
            )


@dataclass(frozen=True, slots=True)
class UnitRiskPosition:
    asset_id: uuid.UUID
    asset_key: str
    model_score: Decimal
    rank: int
    slot_share: Decimal
    unit_risk_weight: Decimal
    retained_by_buffer: bool

    def __post_init__(self) -> None:
        if not self.asset_key.strip() or self.rank < 1:
            raise V022RuntimeContractError(
                "strategy_position_identity_invalid",
                "Unit-Risk Position requires a nonblank Asset key and positive rank",
            )
        numeric = (self.model_score, self.slot_share, self.unit_risk_weight)
        if any(not value.is_finite() for value in numeric):
            raise V022RuntimeContractError(
                "strategy_position_nonfinite",
                "Unit-Risk Position values must remain finite",
            )
        if self.slot_share <= 0 or not Decimal(0) < self.unit_risk_weight <= Decimal(1):
            raise V022RuntimeContractError(
                "strategy_position_weight_invalid",
                "Unit-Risk Position shares and weights must be positive",
            )


@dataclass(frozen=True, slots=True)
class StrategyUnitRiskTarget:
    decision_date: date
    decision_cutoff_at: datetime
    input_known_at: datetime
    eligible_count: int
    rankable_count: int
    coverage_ratio: Decimal
    positions: tuple[UnitRiskPosition, ...]

    def __post_init__(self) -> None:
        if self.decision_cutoff_at.utcoffset() is None:
            raise V022RuntimeContractError(
                "strategy_cutoff_naive",
                "Strategy Decision cutoff must be timezone-aware",
            )
        if self.input_known_at.utcoffset() is None:
            raise V022RuntimeContractError(
                "strategy_input_known_at_naive",
                "Strategy input known_at must be timezone-aware",
            )
        if self.decision_cutoff_at.date() != self.decision_date:
            raise V022RuntimeContractError(
                "strategy_decision_cutoff_mismatch",
                "Strategy Decision date must match its exact cutoff date",
            )
        if self.input_known_at > self.decision_cutoff_at:
            raise V022RuntimeContractError(
                "strategy_input_after_cutoff",
                "Strategy input cannot be known after its exact Decision cutoff",
            )
        if (
            self.eligible_count < 1
            or self.rankable_count < len(self.positions)
            or self.rankable_count > self.eligible_count
            or not self.positions
        ):
            raise V022RuntimeContractError(
                "strategy_target_counts_invalid",
                "Unit-Risk Target counts do not reconcile",
            )
        if (
            not self.coverage_ratio.is_finite()
            or not Decimal(0) <= self.coverage_ratio <= Decimal(1)
        ):
            raise V022RuntimeContractError(
                "strategy_target_coverage_invalid",
                "Unit-Risk Target coverage must be finite inside [0, 1]",
            )
        if self.coverage_ratio != Decimal(self.rankable_count) / Decimal(self.eligible_count):
            raise V022RuntimeContractError(
                "strategy_target_coverage_mismatch",
                "Unit-Risk Target coverage must reconcile to its exact counts",
            )
        canonical = tuple(sorted(self.positions, key=lambda item: item.asset_key))
        if canonical != self.positions:
            raise V022RuntimeContractError(
                "strategy_target_order_invalid",
                "Unit-Risk Positions must be ordered by Asset key",
            )
        asset_ids = tuple(item.asset_id for item in self.positions)
        asset_keys = tuple(item.asset_key for item in self.positions)
        if len(asset_ids) != len(set(asset_ids)) or len(asset_keys) != len(set(asset_keys)):
            raise V022RuntimeContractError(
                "strategy_target_asset_duplicate",
                "Unit-Risk Target Asset identities and keys must be unique",
            )
        if sum((item.unit_risk_weight for item in self.positions), Decimal()) != Decimal(1):
            raise V022RuntimeContractError(
                "strategy_unit_risk_not_conserved",
                "Unit-Risk Target weights must sum exactly to one",
            )


def build_unit_risk_topk_target(
    assets: tuple[StrategyAssetInput, ...],
    *,
    decision_date: date,
    decision_cutoff_at: datetime,
    input_known_at: datetime,
    variant_key: StrategyVariant,
    target_k: int,
    research_mode: Literal["formal", "exploratory"],
    selection_buffer: Literal["none", "half_k"],
    sector_cap: Literal["none", "pit_30_percent"],
) -> StrategyUnitRiskTarget:
    """Build the Strategy sleeve independently from any Defense budget.

    Defense is deliberately absent from this boundary: a Strategy always publishes
    a complete unit-risk target, and the composed Defense package scales it only in
    :func:`style_rotation.v022.defense_runtime.merge_sleeves`.
    """

    if not assets:
        raise V022RuntimeContractError(
            "strategy_assets_empty", "Strategy Unit-Risk input cannot be empty"
        )
    asset_ids = tuple(item.asset_id for item in assets)
    asset_keys = tuple(item.asset_key for item in assets)
    if len(asset_ids) != len(set(asset_ids)) or len(asset_keys) != len(set(asset_keys)):
        raise V022RuntimeContractError(
            "strategy_asset_identity_duplicate",
            "Strategy Asset identities and keys must be unique",
        )
    id_by_key = {item.asset_key: item.asset_id for item in assets}
    ranked = tuple(
        RankedAsset(
            asset_key=item.asset_key,
            model_score=item.model_score,
            eligible=item.eligible,
            sector_key=item.sector_key,
            previously_held=item.previously_held,
        )
        for item in assets
    )
    try:
        decision = build_cross_section_topk_decision(
            ranked,
            variant_key=variant_key,
            target_k=target_k,
            research_mode=research_mode,
            selection_buffer=selection_buffer,
            sector_cap=sector_cap,
            defense_budget=Decimal(0),
        )
    except ValueError as error:
        raise V022RuntimeContractError(
            "strategy_parameters_invalid", str(error)
        ) from error
    if decision.status != "accepted":
        reason_code = decision.reason_code or "strategy_target_rejected"
        raise V022RuntimeDataError(
            reason_code,
            "Exact Strategy input cannot produce a published Unit-Risk Target",
            details={
                "eligible_count": decision.eligible_count,
                "rankable_count": decision.rankable_count,
                "coverage_ratio": str(decision.coverage_ratio),
            },
        )
    return StrategyUnitRiskTarget(
        decision_date=decision_date,
        decision_cutoff_at=decision_cutoff_at,
        input_known_at=input_known_at,
        eligible_count=decision.eligible_count,
        rankable_count=decision.rankable_count,
        coverage_ratio=decision.coverage_ratio,
        positions=tuple(
            UnitRiskPosition(
                asset_id=id_by_key[item.asset_key],
                asset_key=item.asset_key,
                model_score=item.model_score,
                rank=item.rank,
                slot_share=item.slot_share,
                unit_risk_weight=item.target_weight,
                retained_by_buffer=item.retained_by_buffer,
            )
            for item in decision.positions
        ),
    )


def build_cross_section_topk_decision(
    assets: tuple[RankedAsset, ...],
    *,
    variant_key: StrategyVariant,
    target_k: int,
    research_mode: Literal["formal", "exploratory"],
    selection_buffer: Literal["none", "half_k"],
    sector_cap: Literal["none", "pit_30_percent"],
    defense_budget: Decimal,
) -> TopKDecision:
    """Execute the published v0.22 cross-sectional Strategy contract.

    This implementation intentionally owns its ranking and allocation code.  The
    v0.21 calculator is an Oracle used while producing migration Evidence, not a
    runtime dependency of the new Strategy layer.
    """

    if research_mode not in {"formal", "exploratory"}:
        raise ValueError(f"Unknown v0.22 research mode: {research_mode}")
    if variant_key == "cross_section_rank_top_k_parity":
        minimum_eligible = 2
        if target_k not in {1, 2, 3} or selection_buffer != "none":
            raise ValueError("ETF Top-K parameters violate the published Strategy Variant")
        etf_half_universe_limit = True
    elif variant_key in {
        "cross_section_rank_top_k_large_cap_parity",
        "cross_section_rank_top_k_large_cap_multi_frequency",
    }:
        minimum_eligible = 100 if research_mode == "formal" else 50
        if target_k not in {10, 20} or selection_buffer != "half_k":
            raise ValueError(
                "Large-cap Top-K parameters violate the published Strategy Variant"
            )
        etf_half_universe_limit = False
    else:
        raise ValueError(f"Unknown v0.22 Strategy Variant: {variant_key}")
    if sector_cap != "none":
        raise ValueError("M5 parity Strategy Variants require sector_cap=none")
    if target_k < 1 or not Decimal(0) <= defense_budget <= Decimal(1):
        raise ValueError("K and defense budget are invalid")

    eligible = tuple(asset for asset in assets if asset.eligible)
    rankable = tuple(
        asset
        for asset in eligible
        if asset.model_score is not None and asset.model_score.is_finite()
    )
    coverage = (
        Decimal(len(rankable)) / Decimal(len(eligible)) if eligible else Decimal(0)
    )
    reason: str | None = None
    if len(eligible) < minimum_eligible:
        reason = "eligible_count_below_minimum"
    elif coverage < Decimal("0.9"):
        reason = "rankable_coverage_below_90_percent"
    elif len(rankable) < target_k:
        reason = "rankable_count_below_k"
    elif etf_half_universe_limit and target_k > len(rankable) // 2:
        reason = "etf_k_exceeds_half_rankable"
    if reason is not None:
        return _failed_decision(reason, eligible, rankable, coverage, defense_budget)

    ranks = _competition_ranks(rankable)
    retained = {
        asset.asset_key
        for asset in rankable
        if selection_buffer == "half_k"
        and asset.previously_held
        and ranks[asset.asset_key] <= target_k + target_k // 2
    }
    priority = sorted(
        rankable,
        key=lambda asset: (
            0 if asset.asset_key in retained else 1,
            # Preserve the v0.21 ordering boundary exactly.  Historical normalized
            # scores were sorted through binary64 before exact Decimal tie groups
            # were formed; changing this during migration can swap a K-boundary
            # asset even though both displayed scores look tied.
            -float(_finite_score(asset)),
            asset.asset_key,
        ),
    )

    allocations: dict[str, Decimal] = {}
    remaining = Decimal(target_k)
    offset = 0
    while offset < len(priority) and remaining > 0:
        first = priority[offset]
        group_key = (first.asset_key in retained, _finite_score(first))
        boundary = offset + 1
        while boundary < len(priority):
            candidate = priority[boundary]
            if (candidate.asset_key in retained, _finite_score(candidate)) != group_key:
                break
            boundary += 1
        group = priority[offset:boundary]
        group_slots = min(remaining, Decimal(len(group)))
        share = group_slots / Decimal(len(group))
        for asset in group:
            allocations[asset.asset_key] = share
        remaining -= group_slots
        offset = boundary

    slot_residual = Decimal(target_k) - sum(allocations.values(), Decimal(0))
    if allocations and abs(slot_residual) <= Decimal("1e-18"):
        allocations[min(allocations)] += slot_residual
    if sum(allocations.values(), Decimal(0)) != Decimal(target_k):
        return _failed_decision(
            "allocation_did_not_fill_k_slots",
            eligible,
            rankable,
            coverage,
            defense_budget,
        )

    risk_budget = Decimal(1) - defense_budget
    selected_assets = tuple(
        asset
        for asset in sorted(rankable, key=lambda item: item.asset_key)
        if asset.asset_key in allocations
    )
    weights = {
        asset_key: risk_budget * slots / Decimal(target_k)
        for asset_key, slots in allocations.items()
    }
    # The output contract sums positions in canonical Asset-key order.  Decimal
    # addition is precision bounded and therefore order dependent for a large
    # boundary tie.  Apply the arithmetic residual to the final canonical
    # position so the published tuple conserves unit risk under the same order
    # used by StrategyUnitRiskTarget and every downstream accounting adapter.
    if selected_assets:
        residual_asset_key = selected_assets[-1].asset_key
        canonical_prefix_total = sum(
            (weights[asset.asset_key] for asset in selected_assets[:-1]), Decimal(0)
        )
        # Assign the final value from the already-rounded canonical prefix.  Adding
        # a tiny residual to an existing Decimal can itself round back to the same
        # value under the process precision (the real K20 boundary-tie path hit
        # this at 4E-28), leaving the exact output contract unconserved.
        weights[residual_asset_key] = risk_budget - canonical_prefix_total
    positions = tuple(
        TargetPosition(
            asset_key=asset.asset_key,
            model_score=_finite_score(asset),
            rank=ranks[asset.asset_key],
            slot_share=allocations[asset.asset_key],
            target_weight=weights[asset.asset_key],
            retained_by_buffer=asset.asset_key in retained,
        )
        for asset in selected_assets
    )
    return TopKDecision(
        status="accepted",
        reason_code=None,
        eligible_count=len(eligible),
        rankable_count=len(rankable),
        coverage_ratio=coverage,
        positions=positions,
        risk_budget=risk_budget,
        defense_budget=defense_budget,
    )


def resolve_defense_budget(
    variant_key: DefenseVariant,
    *,
    spy_close: Decimal | None = None,
    spy_sma200: Decimal | None = None,
) -> Decimal:
    if variant_key == "none":
        return Decimal(0)
    if variant_key == "fixed20_defense":
        return Decimal("0.2")
    if variant_key != "ma200_tiered_defense":
        raise ValueError(f"Unknown v0.22 Defense Variant: {variant_key}")
    if spy_close is None or spy_sma200 is None or spy_sma200 <= 0:
        raise ValueError("ma200_tiered_defense requires SPY and SMA200")
    distance = spy_close / spy_sma200 - Decimal(1)
    if distance > Decimal("0.02"):
        return Decimal(0)
    if distance < Decimal("-0.02"):
        return Decimal("0.4")
    return Decimal("0.2")


def _failed_decision(
    reason: str,
    eligible: tuple[RankedAsset, ...],
    rankable: tuple[RankedAsset, ...],
    coverage: Decimal,
    defense_budget: Decimal,
) -> TopKDecision:
    return TopKDecision(
        status="failed",
        reason_code=reason,
        eligible_count=len(eligible),
        rankable_count=len(rankable),
        coverage_ratio=coverage,
        positions=(),
        risk_budget=Decimal(1) - defense_budget,
        defense_budget=defense_budget,
    )


def _competition_ranks(assets: tuple[RankedAsset, ...]) -> dict[str, int]:
    score_counts: dict[Decimal, int] = {}
    for asset in assets:
        score = _finite_score(asset)
        score_counts[score] = score_counts.get(score, 0) + 1
    rank_by_score: dict[Decimal, int] = {}
    next_rank = 1
    for score in sorted(score_counts, reverse=True):
        rank_by_score[score] = next_rank
        next_rank += score_counts[score]
    return {asset.asset_key: rank_by_score[_finite_score(asset)] for asset in assets}


def _finite_score(asset: RankedAsset) -> Decimal:
    if asset.model_score is None or not asset.model_score.is_finite():
        raise ValueError("Internal ranking requires a finite score")
    return asset.model_score
