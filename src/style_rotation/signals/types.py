from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from style_rotation.domain.enums import FactorDirection, RebalanceFrequency, StrategyTemplate


@dataclass(frozen=True, slots=True)
class FactorSignalPoint:
    variant_key: str
    symbol: str
    trade_date: date
    raw_value: Decimal
    direction: FactorDirection


@dataclass(frozen=True, slots=True)
class RebalancePair:
    signal_date: date
    execution_date: date


@dataclass(frozen=True, slots=True)
class TargetPosition:
    symbol: str
    raw_factor_value: Decimal
    oriented_factor_value: Decimal
    rank: int | None
    trend_eligible: bool
    tie_flag: bool
    selected: bool
    target_weight: Decimal


@dataclass(frozen=True, slots=True)
class RebalanceTarget:
    variant_key: str
    frequency: RebalanceFrequency
    strategy_template: StrategyTemplate
    signal_date: date
    execution_date: date
    eligible_count: int
    tie_flag: bool
    reserve_target_weight: Decimal
    positions: tuple[TargetPosition, ...]


@dataclass(frozen=True, slots=True)
class SignalComputationResult:
    events: tuple[RebalanceTarget, ...]
    first_signal_date: date
    first_execution_date: date
    coverage_end: date
    content_hash: str

    @property
    def position_count(self) -> int:
        return sum(len(event.positions) for event in self.events)


@dataclass(frozen=True, slots=True)
class SignalPublicationOutcome:
    strategy_version_id: uuid.UUID
    reused: bool
    event_count: int
    position_count: int
    first_signal_date: date
