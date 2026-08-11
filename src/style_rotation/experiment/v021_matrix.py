from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.workspace.contracts import CompiledStrategyBranch

WindowKey = Literal["full_common_history", "trailing_3_years", "trailing_1_year"]
CostKey = Literal["base_5bps_plus_impact", "base_10bps_plus_impact"]


class PortfolioMatrixPolicy(BaseModel):
    """Frozen v0.21 matrix. Changing a value requires a new policy version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_key: Literal["v021_fixed_portfolio_matrix_v1"] = "v021_fixed_portfolio_matrix_v1"
    windows: tuple[WindowKey, ...] = (
        "full_common_history",
        "trailing_3_years",
        "trailing_1_year",
    )
    costs: tuple[CostKey, ...] = (
        "base_5bps_plus_impact",
        "base_10bps_plus_impact",
    )
    initial_capital_usd: Decimal = Decimal("100000000")
    initialization_policy: Literal["fresh_start"] = "fresh_start"
    capacity_adv_limit: Decimal = Decimal("0.05")

    @model_validator(mode="after")
    def validate_frozen_matrix(self) -> PortfolioMatrixPolicy:
        if self.windows != (
            "full_common_history",
            "trailing_3_years",
            "trailing_1_year",
        ):
            raise ValueError("v0.21 permits only Full/3Y/1Y Portfolio windows")
        if self.costs != ("base_5bps_plus_impact", "base_10bps_plus_impact"):
            raise ValueError("v0.21 permits only 5/10bps plus impact")
        if self.initial_capital_usd != Decimal("100000000"):
            raise ValueError("v0.21 Portfolio capital is fixed at USD 100M")
        if self.capacity_adv_limit != Decimal("0.05"):
            raise ValueError("v0.21 capacity hard limit is 5% of trailing ADV")
        return self


@dataclass(frozen=True, slots=True)
class PortfolioCellSpec:
    cell_key: str
    branch_key: str
    window_key: WindowKey
    cost_key: CostKey
    cost_bps_per_side: int
    initial_capital_usd: Decimal
    initialization_policy: Literal["fresh_start"]
    state_reset: bool
    cell_fingerprint: str


def build_fixed_portfolio_matrix(
    branches: tuple[CompiledStrategyBranch, ...],
    *,
    comparison_context_fingerprint: str,
    policy: PortfolioMatrixPolicy | None = None,
) -> tuple[PortfolioCellSpec, ...]:
    effective = policy or PortfolioMatrixPolicy()
    cells: list[PortfolioCellSpec] = []
    for branch in sorted(branches, key=lambda item: item.branch_key):
        for window in effective.windows:
            for cost in effective.costs:
                cost_bps = 5 if cost == "base_5bps_plus_impact" else 10
                identity = {
                    "branch_key": branch.branch_key,
                    "window_key": window,
                    "cost_key": cost,
                    "initial_capital_usd": str(effective.initial_capital_usd),
                    "initialization_policy": effective.initialization_policy,
                    "comparison_context_fingerprint": comparison_context_fingerprint,
                    "policy_key": effective.policy_key,
                }
                cells.append(
                    PortfolioCellSpec(
                        cell_key=f"{branch.branch_key}__{window}__{cost}",
                        branch_key=branch.branch_key,
                        window_key=window,
                        cost_key=cost,
                        cost_bps_per_side=cost_bps,
                        initial_capital_usd=effective.initial_capital_usd,
                        initialization_policy=effective.initialization_policy,
                        state_reset=True,
                        cell_fingerprint=sha256_hexdigest(identity),
                    )
                )
    if len(cells) != len(branches) * 6:
        raise AssertionError("Each compiled Strategy branch must produce exactly six cells")
    return tuple(cells)


@dataclass(frozen=True, slots=True)
class CapacityDecision:
    participation_rate: Decimal | None
    status: Literal["accepted", "capacity_rejected", "data_quality_failed"]
    reason_code: str | None


def evaluate_capacity(
    *, order_notional: Decimal, trailing_median_dollar_volume_20: Decimal | None
) -> CapacityDecision:
    if order_notional < 0:
        raise ValueError("Order notional cannot be negative")
    if trailing_median_dollar_volume_20 is None or trailing_median_dollar_volume_20 <= 0:
        return CapacityDecision(None, "data_quality_failed", "adv20_unavailable")
    participation = order_notional / trailing_median_dollar_volume_20
    if participation > Decimal("0.05"):
        return CapacityDecision(participation, "capacity_rejected", "adv_5_percent_exceeded")
    return CapacityDecision(participation, "accepted", None)


class ImpactPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_key: str = Field(min_length=1)
    coefficient: Decimal = Field(gt=0)
    maximum_bps: Decimal = Field(gt=0)
    p0_finalized: bool


def square_root_impact_bps(
    *, participation_rate: Decimal, daily_volatility: Decimal, policy: ImpactPolicy
) -> Decimal:
    if not policy.p0_finalized:
        raise RuntimeError("Liquidity impact policy is not P0-finalized")
    if participation_rate < 0 or daily_volatility < 0:
        raise ValueError("Participation and volatility cannot be negative")
    raw = (
        policy.coefficient
        * daily_volatility
        * Decimal(str(math.sqrt(float(participation_rate))))
        * Decimal("10000")
    )
    return min(raw, policy.maximum_bps)
