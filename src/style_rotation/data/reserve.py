from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FACTOR_QUANTUM = Decimal("0.00000000000001")


class ReserveModelCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_type: Literal["reserve_return_model"]
    catalog_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    model_key: Literal["dgs3mo_cash_accrual_proxy"]
    name: str
    version_number: int = Field(ge=1)
    accrual_method: Literal["simple"]
    day_count_basis: Literal["ACT/365"]
    warning_after_days: int = Field(ge=0)
    error_after_days: int = Field(ge=0)
    rate_selection: Literal["latest_available_at_interval_start"]

    @model_validator(mode="after")
    def validate_staleness(self) -> ReserveModelCatalog:
        if self.error_after_days < self.warning_after_days:
            raise ValueError("Reserve error threshold cannot precede warning threshold")
        return self


@dataclass(frozen=True, slots=True)
class AvailableRate:
    observation_date: date
    available_date: date
    annual_rate_percent: Decimal


@dataclass(frozen=True, slots=True)
class ReserveInterval:
    interval_start: date
    interval_end: date
    source_observation_date: date
    source_available_date: date
    annual_rate_percent: Decimal
    calendar_days: int
    accrual_factor: Decimal
    staleness_days: int
    quality_status: str


@dataclass(frozen=True, slots=True)
class ReserveIssue:
    severity: str
    rule_code: str
    event_date: date
    message: str
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class ReserveResult:
    intervals: tuple[ReserveInterval, ...]
    issues: tuple[ReserveIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.issues)


def calculate_reserve_intervals(
    rates: tuple[AvailableRate, ...],
    sessions: tuple[date, ...],
    *,
    warning_after_days: int = 5,
    error_after_days: int = 10,
) -> ReserveResult:
    if warning_after_days < 0 or error_after_days < warning_after_days:
        raise ValueError("Invalid reserve staleness thresholds")
    ordered_rates = sorted(rates, key=lambda item: (item.available_date, item.observation_date))
    ordered_sessions = tuple(sorted(set(sessions)))
    issues: list[ReserveIssue] = []
    intervals: list[ReserveInterval] = []
    rate_index = 0
    current: AvailableRate | None = None
    for interval_start, interval_end in zip(ordered_sessions, ordered_sessions[1:], strict=False):
        while (
            rate_index < len(ordered_rates)
            and ordered_rates[rate_index].available_date <= interval_start
        ):
            current = ordered_rates[rate_index]
            rate_index += 1
        if current is None:
            issues.append(
                ReserveIssue(
                    "error",
                    "missing_available_reserve_rate",
                    interval_start,
                    "No DGS3MO value was publicly available at interval start",
                    {},
                )
            )
            continue
        staleness = (interval_start - current.available_date).days
        if staleness > error_after_days:
            issues.append(
                ReserveIssue(
                    "error",
                    "stale_reserve_rate",
                    interval_start,
                    "Latest available DGS3MO value exceeds the error staleness limit",
                    {"staleness_days": staleness},
                )
            )
            continue
        status = "warning" if staleness > warning_after_days else "normal"
        if status == "warning":
            issues.append(
                ReserveIssue(
                    "warning",
                    "aging_reserve_rate",
                    interval_start,
                    "Latest available DGS3MO value is 6-10 calendar days old",
                    {"staleness_days": staleness},
                )
            )
        calendar_days = (interval_end - interval_start).days
        factor = (
            Decimal(1)
            + current.annual_rate_percent / Decimal(100) * Decimal(calendar_days) / Decimal(365)
        ).quantize(FACTOR_QUANTUM)
        if factor <= 0:
            issues.append(
                ReserveIssue(
                    "error",
                    "nonpositive_reserve_factor",
                    interval_start,
                    "DGS3MO simple-accrual factor is not positive",
                    {"factor": str(factor)},
                )
            )
            continue
        intervals.append(
            ReserveInterval(
                interval_start,
                interval_end,
                current.observation_date,
                current.available_date,
                current.annual_rate_percent,
                calendar_days,
                factor,
                staleness,
                status,
            )
        )
    return ReserveResult(tuple(intervals), tuple(issues))
