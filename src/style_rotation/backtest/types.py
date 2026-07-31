from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from style_rotation.domain.enums import RebalanceFrequency, StrategyTemplate


@dataclass(frozen=True, slots=True)
class ExecutionTarget:
    signal_date: date
    execution_date: date
    asset_weights: dict[str, Decimal]
    reserve_weight: Decimal


@dataclass(frozen=True, slots=True)
class DailyNavRecord:
    nav_date: date
    gross_daily_return: Decimal
    net_daily_return: Decimal
    gross_nav: Decimal
    net_nav: Decimal
    turnover: Decimal
    transaction_cost_fraction: Decimal
    transaction_cost_amount: Decimal


@dataclass(frozen=True, slots=True)
class DailyPositionRecord:
    nav_date: date
    sleeve: str
    close_weight: Decimal


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    signal_date: date
    execution_date: date
    turnover: Decimal
    transaction_cost_fraction: Decimal
    transaction_cost_amount: Decimal
    gross_pretrade_nav: Decimal
    net_pretrade_nav: Decimal


@dataclass(frozen=True, slots=True)
class TradeRecord:
    execution_date: date
    symbol: str
    side: str
    execution_price: Decimal
    pretrade_weight: Decimal
    target_weight: Decimal
    weight_change: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    daily_nav: tuple[DailyNavRecord, ...]
    daily_positions: tuple[DailyPositionRecord, ...]
    executions: tuple[ExecutionRecord, ...]
    trades: tuple[TradeRecord, ...]


@dataclass(frozen=True, slots=True)
class RunInputSpec:
    factor_variant_key: str
    frequency: RebalanceFrequency
    strategy_template: StrategyTemplate
    targets: tuple[ExecutionTarget, ...]


@dataclass(frozen=True, slots=True)
class BacktestBatchOutcome:
    experiment_id: str
    completed_runs: int
    reused_runs: int
