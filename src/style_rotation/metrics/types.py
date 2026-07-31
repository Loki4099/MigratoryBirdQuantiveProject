from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MetricValue:
    value: Decimal | None
    reason_code: str | None
    observation_count: int

    def __post_init__(self) -> None:
        if self.observation_count < 0:
            raise ValueError("Observation count cannot be negative")
        if (self.value is None) == (self.reason_code is None):
            raise ValueError("Exactly one of metric value and reason code must be present")
        if self.value is not None and not self.value.is_finite():
            raise ValueError("Metric values must be finite")


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    nav_date: date
    daily_return: Decimal
    nav: Decimal


@dataclass(frozen=True, slots=True)
class DiagnosticEventInput:
    factor_variant_id: uuid.UUID
    variant_key: str
    rebalance_frequency: str
    signal_date: date
    execution_date: date
    oriented_values: dict[str, Decimal]
    deterministic_ranks: dict[str, int]


@dataclass(frozen=True, slots=True)
class OpenPrice:
    symbol: str
    trade_date: date
    open_adj: Decimal


@dataclass(frozen=True, slots=True)
class FactorDiagnosticPeriod:
    factor_variant_id: uuid.UUID
    variant_key: str
    rebalance_frequency: str
    signal_date: date
    execution_date: date
    next_execution_date: date
    rank_ic: Decimal | None
    rank_ic_reason_code: str | None
    top_bottom_return_spread: Decimal


@dataclass(frozen=True, slots=True)
class FactorDiagnosticSummary:
    factor_variant_id: uuid.UUID
    variant_key: str
    rebalance_frequency: str
    period_count: int
    valid_ic_count: int
    undefined_ic_count: int
    mean_rank_ic: Decimal | None
    positive_ic_ratio: Decimal | None
    mean_top_bottom_return_spread: Decimal
    ic_summary_reason_code: str | None


@dataclass(frozen=True, slots=True)
class PerformanceMetricResult:
    series_type: str
    return_basis: str
    metric_key: str
    value: Decimal | None
    value_status: str
    reason_code: str | None
    observation_count: int
    unit: str

    def __post_init__(self) -> None:
        if self.value_status == "defined":
            if self.value is None or self.reason_code is not None:
                raise ValueError("Defined metric must have a finite value and no reason")
            if not self.value.is_finite():
                raise ValueError("Defined metric value must be finite")
        elif self.value_status in {"undefined", "not_applicable"}:
            if self.value is not None or self.reason_code is None:
                raise ValueError("Undefined metric must have no value and a reason")
        else:
            raise ValueError("Unsupported metric value status")
        if self.observation_count < 0:
            raise ValueError("Observation count cannot be negative")


@dataclass(frozen=True, slots=True)
class RunMetricInput:
    run_id: uuid.UUID
    factor_variant_id: uuid.UUID
    factor_variant_key: str
    rebalance_frequency: str
    strategy_template: str
    transaction_cost_bps: Decimal
    first_execution_date: date
    official_end_date: date
    strategy_gross: tuple[SeriesPoint, ...]
    strategy_net: tuple[SeriesPoint, ...]
    equal_weight_gross: tuple[SeriesPoint, ...]
    equal_weight_net: tuple[SeriesPoint, ...]
    spy_gross: tuple[SeriesPoint, ...]
    spy_net: tuple[SeriesPoint, ...]
    risk_free_returns: tuple[Decimal, ...]
    daily_turnover: tuple[Decimal, ...]
    transaction_cost_amounts: tuple[Decimal, ...]
    reserve_close_weights: tuple[Decimal, ...]
    run_fingerprint: str
    input_manifest_hash: str


@dataclass(frozen=True, slots=True)
class SourceRunDescriptor:
    run_id: uuid.UUID
    experiment_id: uuid.UUID
    data_version_id: uuid.UUID
    cleaning_version_id: uuid.UUID
    factor_version_id: uuid.UUID
    strategy_version_id: uuid.UUID
    source_engine_version_id: uuid.UUID
    factor_variant_id: uuid.UUID
    factor_variant_key: str
    rebalance_frequency: str
    strategy_template: str
    transaction_cost_bps: Decimal
    official_signal_start_date: date
    first_execution_date: date
    official_end_date: date
    configuration: dict[str, object]
    run_fingerprint: str


@dataclass(frozen=True, slots=True)
class SourceRunSet:
    experiment_id: uuid.UUID
    data_version_id: uuid.UUID
    cleaning_version_id: uuid.UUID
    factor_version_id: uuid.UUID
    strategy_version_id: uuid.UUID
    source_engine_version_id: uuid.UUID
    runs: tuple[SourceRunDescriptor, ...]


@dataclass(frozen=True, slots=True)
class RunMetricResult:
    run_id: uuid.UUID
    metrics: tuple[PerformanceMetricResult, ...]
    metric_fingerprint: str
    input_manifest_hash: str


@dataclass(frozen=True, slots=True)
class MetricBatchOutcome:
    metric_version_id: str
    diagnostic_sets_completed: int
    diagnostic_sets_reused: int
    publications_completed: int
    publications_reused: int
