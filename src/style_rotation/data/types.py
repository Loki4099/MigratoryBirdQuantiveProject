from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class MarketPriceRecord:
    symbol: str
    trade_date: date
    open_raw: Decimal | None
    high_raw: Decimal | None
    low_raw: Decimal | None
    close_raw: Decimal | None
    adj_close: Decimal | None
    volume_raw: int | None
    dividends: Decimal
    stock_splits: Decimal
    source_row_hash: str


@dataclass(frozen=True, slots=True)
class RateObservation:
    series_id: str
    observation_date: date
    available_date: date
    annual_rate_percent: Decimal
    source_row_hash: str


@dataclass(frozen=True, slots=True)
class ProviderBatch:
    provider: str
    requested_at: datetime
    request_parameters: dict[str, Any]
    content_hash: str
    records: tuple[MarketPriceRecord | RateObservation, ...]


@dataclass(frozen=True, slots=True)
class CleanMarketPriceRecord:
    symbol: str
    trade_date: date
    open_adj: Decimal
    high_adj: Decimal
    low_adj: Decimal
    close_adj: Decimal
    adj_factor: Decimal
    volume_raw: int
    dividends: Decimal
    stock_splits: Decimal


@dataclass(frozen=True, slots=True)
class ReserveDailyRecord:
    nav_date: date
    series_id: str
    source_observation_date: date
    source_available_date: date
    annual_rate_percent: Decimal
    calendar_daily_factor: Decimal


@dataclass(frozen=True, slots=True)
class QualityIssue:
    severity: str
    rule_code: str
    message: str
    symbol: str | None = None
    series_id: str | None = None
    event_date: date | None = None
    details: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CleanResult:
    prices: tuple[CleanMarketPriceRecord, ...]
    reserve_returns: tuple[ReserveDailyRecord, ...]
    issues: tuple[QualityIssue, ...]
    common_market_start: date | None
    content_hash: str | None

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)


class DataQualityGateError(RuntimeError):
    """Raised when a data version cannot be published for formal research."""
