from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Family = Literal["multi_etf_top_k", "us_large_cap_top_k"]


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


def build_topk_decision(
    assets: tuple[RankedAsset, ...],
    *,
    family: Family,
    target_k: int,
    research_mode: Literal["formal", "exploratory"],
    selection_buffer: Literal["none", "half_k"] = "none",
    sector_cap: Literal["none", "pit_30_percent"] = "none",
    defense_budget: Decimal = Decimal("0"),
) -> TopKDecision:
    if target_k < 1 or not Decimal("0") <= defense_budget <= Decimal("1"):
        raise ValueError("K and defense budget are invalid")
    eligible = tuple(item for item in assets if item.eligible)
    rankable = tuple(
        item for item in eligible if item.model_score is not None and item.model_score.is_finite()
    )
    coverage = Decimal(len(rankable)) / Decimal(len(eligible)) if eligible else Decimal("0")
    minimum = 2 if family == "multi_etf_top_k" else (100 if research_mode == "formal" else 50)
    reason = None
    if len(eligible) < minimum:
        reason = "eligible_count_below_minimum"
    elif coverage < Decimal("0.9"):
        reason = "rankable_coverage_below_90_percent"
    elif len(rankable) < target_k:
        reason = "rankable_count_below_k"
    elif family == "multi_etf_top_k" and target_k > len(rankable) // 2:
        reason = "etf_k_exceeds_half_rankable"
    elif sector_cap == "pit_30_percent" and any(item.sector_key is None for item in rankable):
        reason = "pit_sector_data_unavailable"
    if reason:
        return TopKDecision(
            "failed",
            reason,
            len(eligible),
            len(rankable),
            coverage,
            (),
            Decimal("1") - defense_budget,
            defense_budget,
        )

    ordered_groups = _selection_groups(
        rankable, target_k=target_k, use_buffer=selection_buffer == "half_k"
    )
    allocations = _allocate_slots(
        ordered_groups,
        target_k=target_k,
        sector_cap=Decimal("0.3") if sector_cap == "pit_30_percent" else None,
    )
    allocated = sum(allocations.values(), Decimal("0"))
    target_slots = Decimal(target_k)
    residual = target_slots - allocated
    # A boundary tie can divide a slot into a recurring Decimal (for example
    # 10 / 81).  Correct only the arithmetic dust deterministically; a real
    # sector-cap shortfall remains a hard failure below.
    if allocations and abs(residual) <= Decimal("1e-18"):
        residual_recipient = min(allocations)
        allocations[residual_recipient] += residual
        allocated = sum(allocations.values(), Decimal("0"))
    if allocated != target_slots:
        return TopKDecision(
            "failed",
            "sector_cap_prevents_k_slots",
            len(eligible),
            len(rankable),
            coverage,
            (),
            Decimal("1") - defense_budget,
            defense_budget,
        )
    score_ranks = _competition_ranks(rankable)
    risk_budget = Decimal("1") - defense_budget
    target_weights = {
        item.asset_key: risk_budget * allocations[item.asset_key] / Decimal(target_k)
        for item in rankable
        if allocations.get(item.asset_key, Decimal("0")) > 0
    }
    weight_residual = risk_budget - sum(target_weights.values(), Decimal("0"))
    # Scaling a fractional boundary-tie slot by a non-unit risk budget can add a
    # second Decimal rounding residue after slot allocation was already balanced.
    # Preserve the frozen risk/defense split exactly without introducing a
    # ticker tie-break: the deterministic key receives arithmetic dust only.
    if target_weights and abs(weight_residual) <= Decimal("1e-18"):
        target_weights[min(target_weights)] += weight_residual
    positions = tuple(
        TargetPosition(
            asset_key=item.asset_key,
            model_score=_score(item),
            rank=score_ranks[item.asset_key],
            slot_share=allocations[item.asset_key],
            target_weight=target_weights[item.asset_key],
            retained_by_buffer=(
                selection_buffer == "half_k"
                and item.previously_held
                and score_ranks[item.asset_key] <= target_k + target_k // 2
            ),
        )
        for item in sorted(rankable, key=lambda candidate: candidate.asset_key)
        if item.asset_key in target_weights
    )
    return TopKDecision(
        "accepted",
        None,
        len(eligible),
        len(rankable),
        coverage,
        positions,
        risk_budget,
        defense_budget,
    )


def internal_timing_defense_budget(
    *, spy_close: Decimal | None, spy_sma200: Decimal | None
) -> Decimal:
    if spy_close is None or spy_sma200 is None or spy_sma200 <= 0:
        raise ValueError("internal_timing_v1 requires SPY and SMA200")
    ratio = spy_close / spy_sma200 - Decimal("1")
    if ratio > Decimal("0.02"):
        return Decimal("0")
    if ratio < Decimal("-0.02"):
        return Decimal("0.4")
    return Decimal("0.2")


def _selection_groups(
    assets: tuple[RankedAsset, ...], *, target_k: int, use_buffer: bool
) -> tuple[tuple[RankedAsset, ...], ...]:
    ranks = _competition_ranks(assets)
    retained = {
        item.asset_key
        for item in assets
        if use_buffer and item.previously_held and ranks[item.asset_key] <= target_k + target_k // 2
    }
    priority = sorted(
        assets,
        key=lambda item: (
            0 if item.asset_key in retained else 1,
            -float(_score(item)),
        ),
    )
    groups: list[list[RankedAsset]] = []
    group_keys: list[tuple[int, Decimal]] = []
    for item in priority:
        key = (0 if item.asset_key in retained else 1, _score(item))
        if not group_keys or key != group_keys[-1]:
            group_keys.append(key)
            groups.append([])
        groups[-1].append(item)
    return tuple(tuple(group) for group in groups)


def _allocate_slots(
    groups: tuple[tuple[RankedAsset, ...], ...],
    *,
    target_k: int,
    sector_cap: Decimal | None,
) -> dict[str, Decimal]:
    allocations: dict[str, Decimal] = {}
    sector_usage: dict[str, Decimal] = {}
    remaining = Decimal(target_k)
    sector_limit = Decimal(target_k) * sector_cap if sector_cap is not None else None
    for group in groups:
        if remaining <= 0:
            break
        candidates = list(group)
        group_budget = min(remaining, Decimal(len(candidates)))
        pending = group_budget
        active = candidates
        while active and pending > 0:
            share = pending / Decimal(len(active))
            next_active: list[RankedAsset] = []
            distributed = Decimal("0")
            for item in active:
                sector = item.sector_key or "__none__"
                capacity = (
                    sector_limit - sector_usage.get(sector, Decimal("0"))
                    if sector_limit is not None
                    else share
                )
                amount = min(share, max(capacity, Decimal("0")))
                allocations[item.asset_key] = allocations.get(item.asset_key, Decimal("0")) + amount
                sector_usage[sector] = sector_usage.get(sector, Decimal("0")) + amount
                distributed += amount
                if amount == share:
                    next_active.append(item)
            if distributed == 0:
                break
            pending -= distributed
            active = next_active if pending > 0 else []
        remaining -= group_budget - pending
    return allocations


def _competition_ranks(assets: tuple[RankedAsset, ...]) -> dict[str, int]:
    scores = sorted({_score(item) for item in assets}, reverse=True)
    counts = {score: sum(_score(item) == score for item in assets) for score in scores}
    rank = 1
    ranks: dict[Decimal, int] = {}
    for score in scores:
        ranks[score] = rank
        rank += counts[score]
    return {item.asset_key: ranks[_score(item)] for item in assets}


def _score(item: RankedAsset) -> Decimal:
    if item.model_score is None or not item.model_score.is_finite():
        raise ValueError("Internal ranking functions require a finite score")
    return item.model_score
