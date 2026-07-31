from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.domain.enums import RebalanceFrequency, StrategyTemplate


@dataclass(frozen=True, slots=True)
class RunFingerprintInput:
    data_version: str
    cleaning_version: str
    factor_version: str
    strategy_version: str
    engine_version: str
    factor_variant_key: str
    official_signal_start_date: date
    official_end_date: date
    rebalance_frequency: RebalanceFrequency
    strategy_template: StrategyTemplate
    transaction_cost_bps: Decimal
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.official_signal_start_date > self.official_end_date:
            raise ValueError("Official signal start date must not be after end date")
        if self.transaction_cost_bps < 0:
            raise ValueError("Transaction cost cannot be negative")

    @property
    def fingerprint(self) -> str:
        return sha256_hexdigest(self)
