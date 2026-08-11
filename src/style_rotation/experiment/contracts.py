from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True, slots=True)
class AccountingMarketBar:
    asset_id: uuid.UUID
    asset_key: str
    session_date: date
    adjusted_open: Decimal
    adjusted_close: Decimal


@dataclass(frozen=True, slots=True)
class AccountingReserveInterval:
    interval_start: date
    interval_end: date
    accrual_factor: Decimal
    source_observation_date: date
    source_available_date: date
    quality_status: Literal["normal", "warning"]


@dataclass(frozen=True, slots=True)
class TargetAssetWeight:
    asset_id: uuid.UUID
    asset_key: str
    target_weight: Decimal


@dataclass(frozen=True, slots=True)
class TargetDecision:
    decision_date: date
    asset_weights: tuple[TargetAssetWeight, ...]
    reserve_target_weight: Decimal


@dataclass(frozen=True, slots=True)
class ExecutableTarget:
    decision_date: date
    execution_date: date
    asset_weights: tuple[TargetAssetWeight, ...]
    reserve_target_weight: Decimal


@dataclass(frozen=True, slots=True)
class GrossDailyNav:
    nav_date: date
    daily_return: Decimal
    gross_nav: Decimal
    overnight_factor: Decimal
    intraday_factor: Decimal


@dataclass(frozen=True, slots=True)
class DailyAssetPosition:
    nav_date: date
    asset_id: uuid.UUID
    asset_key: str
    close_weight: Decimal


@dataclass(frozen=True, slots=True)
class DailyReservePosition:
    nav_date: date
    close_weight: Decimal
    interval_source_observation_date: date | None
    interval_source_available_date: date | None
    quality_status: Literal["not_applicable", "normal", "warning"]


@dataclass(frozen=True, slots=True)
class PortfolioExecution:
    decision_date: date
    execution_date: date
    gross_pretrade_nav: Decimal
    one_way_turnover: Decimal
    gross_traded_fraction: Decimal
    pretrade_reserve_weight: Decimal
    posttrade_reserve_weight: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioTrade:
    decision_date: date
    execution_date: date
    asset_id: uuid.UUID
    asset_key: str
    side: Literal["buy", "sell", "none"]
    adjusted_execution_price: Decimal
    pretrade_weight: Decimal
    target_weight: Decimal
    signed_weight_change: Decimal
    absolute_weight_change: Decimal


@dataclass(frozen=True, slots=True)
class GrossAccountingResult:
    first_decision_date: date
    first_execution_date: date
    effective_nav_start: date
    effective_nav_end: date
    daily_nav: tuple[GrossDailyNav, ...]
    daily_asset_positions: tuple[DailyAssetPosition, ...]
    daily_reserve_positions: tuple[DailyReservePosition, ...]
    executions: tuple[PortfolioExecution, ...]
    trades: tuple[PortfolioTrade, ...]


@dataclass(frozen=True, slots=True)
class NetDailyNav:
    nav_date: date
    net_daily_return: Decimal
    net_nav: Decimal
    gross_nav: Decimal
    daily_cost_amount: Decimal


@dataclass(frozen=True, slots=True)
class ExecutionCost:
    decision_date: date
    execution_date: date
    net_pretrade_nav: Decimal
    gross_traded_notional: Decimal
    cost_fraction: Decimal
    cost_amount: Decimal


@dataclass(frozen=True, slots=True)
class NetCostResult:
    effective_nav_start: date
    effective_nav_end: date
    cost_bps_per_side: Decimal
    cumulative_cost_amount: Decimal
    daily_nav: tuple[NetDailyNav, ...]
    execution_costs: tuple[ExecutionCost, ...]
