from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

REQUIRED_SYMBOLS = ("IWF", "IWD", "IWO", "IWN", "SPY")
EXTREME_RETURN_THRESHOLD = Decimal("0.50")
PRICE_QUANTUM = Decimal("0.0000000001")
FACTOR_QUANTUM = Decimal("0.00000000000001")
RATE_QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class SnapshotDocument:
    subject_key: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class CanonicalBar:
    symbol: str
    session_date: date
    open_raw: Decimal
    high_raw: Decimal
    low_raw: Decimal
    close_raw: Decimal
    adj_close: Decimal
    open_adj: Decimal
    high_adj: Decimal
    low_adj: Decimal
    close_adj: Decimal
    adjustment_factor: Decimal
    volume_raw: int


@dataclass(frozen=True, slots=True)
class CanonicalAction:
    symbol: str
    effective_date: date
    cash_dividend: Decimal
    split_ratio: Decimal


@dataclass(frozen=True, slots=True)
class CanonicalRate:
    series_key: str
    observation_date: date
    available_date: date
    annual_rate_percent: Decimal


@dataclass(frozen=True, slots=True)
class Coverage:
    subject_key: str
    coverage_start: date
    coverage_end: date
    observation_count: int
    missing_count: int


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: str
    rule_code: str
    message: str
    subject_key: str | None = None
    event_date: date | None = None
    details: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class MarketCanonicalResult:
    bars: tuple[CanonicalBar, ...]
    actions: tuple[CanonicalAction, ...]
    coverage: tuple[Coverage, ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.issues)


@dataclass(frozen=True, slots=True)
class RateCanonicalResult:
    observations: tuple[CanonicalRate, ...]
    coverage: Coverage | None
    issues: tuple[ValidationIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.issues)


class CanonicalQualityError(RuntimeError):
    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        self.issues = issues
        error_count = sum(item.severity == "error" for item in issues)
        super().__init__(f"Canonical quality gate rejected {error_count} error(s)")


def parse_market_snapshots(
    documents: tuple[SnapshotDocument, ...],
    calendar_sessions: frozenset[date],
    *,
    required_symbols: tuple[str, ...] = REQUIRED_SYMBOLS,
) -> MarketCanonicalResult:
    issues: list[ValidationIssue] = []
    bars: list[CanonicalBar] = []
    actions: list[CanonicalAction] = []
    seen_symbols: set[str] = set()
    seen_keys: set[tuple[str, date]] = set()
    for document in documents:
        symbol = document.subject_key.upper()
        if symbol in seen_symbols:
            issues.append(_issue("error", "duplicate_symbol_snapshot", symbol, None))
            continue
        seen_symbols.add(symbol)
        try:
            rows = _csv_rows(document.payload)
        except (UnicodeDecodeError, csv.Error) as error:
            issues.append(_issue("error", "invalid_market_csv", symbol, None, str(error)))
            continue
        for row_number, row in enumerate(rows, start=2):
            try:
                session_date = date.fromisoformat(_required(row, "session_date"))
                key = (symbol, session_date)
                if key in seen_keys:
                    raise ValueError("duplicate symbol/session row")
                seen_keys.add(key)
                values = {
                    field: _decimal(row, field).quantize(PRICE_QUANTUM)
                    for field in ("Open", "High", "Low", "Close", "Adj Close")
                }
                volume_value = _decimal(row, "Volume")
                if volume_value != volume_value.to_integral_value():
                    raise ValueError("volume must be an integer")
                volume = int(volume_value)
                dividend = _decimal(row, "Dividends", default=Decimal(0)).quantize(PRICE_QUANTUM)
                split = _decimal(row, "Stock Splits", default=Decimal(0)).quantize(PRICE_QUANTUM)
                open_raw = values["Open"]
                high_raw = values["High"]
                low_raw = values["Low"]
                close_raw = values["Close"]
                adj_close = values["Adj Close"]
                if min(values.values()) <= 0:
                    raise ValueError("prices must be positive")
                if high_raw < max(open_raw, close_raw) or low_raw > min(open_raw, close_raw):
                    raise ValueError("OHLC high/low geometry is invalid")
                if volume < 0 or dividend < 0 or split < 0:
                    raise ValueError("volume and corporate actions must be nonnegative")
                factor = (adj_close / close_raw).quantize(FACTOR_QUANTUM)
                bars.append(
                    CanonicalBar(
                        symbol,
                        session_date,
                        open_raw,
                        high_raw,
                        low_raw,
                        close_raw,
                        adj_close,
                        (open_raw * factor).quantize(PRICE_QUANTUM),
                        (high_raw * factor).quantize(PRICE_QUANTUM),
                        (low_raw * factor).quantize(PRICE_QUANTUM),
                        adj_close,
                        factor,
                        volume,
                    )
                )
                if dividend > 0 or split > 0:
                    actions.append(CanonicalAction(symbol, session_date, dividend, split))
            except (InvalidOperation, ValueError, KeyError) as error:
                issues.append(
                    _issue(
                        "error",
                        "invalid_market_row",
                        symbol,
                        None,
                        str(error),
                        {"row_number": row_number},
                    )
                )
    for symbol in sorted(set(required_symbols).difference(seen_symbols)):
        issues.append(_issue("error", "missing_required_symbol", symbol, None))
    grouped: dict[str, list[CanonicalBar]] = defaultdict(list)
    for bar in bars:
        grouped[bar.symbol].append(bar)
    coverage: list[Coverage] = []
    for symbol, symbol_bars in sorted(grouped.items()):
        symbol_bars.sort(key=lambda item: item.session_date)
        dates = {item.session_date for item in symbol_bars}
        invalid_dates = dates.difference(calendar_sessions)
        for invalid_date in sorted(invalid_dates):
            issues.append(_issue("error", "non_session_market_row", symbol, invalid_date))
        first, last = symbol_bars[0].session_date, symbol_bars[-1].session_date
        expected = {item for item in calendar_sessions if first <= item <= last}
        missing = sorted(expected.difference(dates))
        for missing_date in missing:
            issues.append(_issue("error", "missing_market_session", symbol, missing_date))
        coverage.append(Coverage(symbol, first, last, len(symbol_bars), len(missing)))
        prior_close: Decimal | None = None
        for bar in symbol_bars:
            if prior_close is not None:
                daily_return = bar.close_adj / prior_close - 1
                if abs(daily_return) > EXTREME_RETURN_THRESHOLD:
                    issues.append(
                        _issue(
                            "warning",
                            "extreme_adjusted_return",
                            symbol,
                            bar.session_date,
                            details={"return": str(daily_return)},
                        )
                    )
            prior_close = bar.close_adj
    return MarketCanonicalResult(
        tuple(sorted(bars, key=lambda item: (item.symbol, item.session_date))),
        tuple(sorted(actions, key=lambda item: (item.symbol, item.effective_date))),
        tuple(coverage),
        tuple(issues),
    )


def parse_fred_snapshot(document: SnapshotDocument) -> RateCanonicalResult:
    issues: list[ValidationIssue] = []
    observations: list[CanonicalRate] = []
    seen: set[date] = set()
    try:
        rows = _csv_rows(document.payload)
    except (UnicodeDecodeError, csv.Error) as error:
        return RateCanonicalResult(
            (), None, (_issue("error", "invalid_rate_csv", document.subject_key, None, str(error)),)
        )
    for row_number, row in enumerate(rows, start=2):
        try:
            raw_date = row.get("DATE") or row.get("observation_date")
            if not raw_date:
                raise ValueError("missing observation date")
            observation_date = date.fromisoformat(raw_date)
            if observation_date in seen:
                raise ValueError("duplicate observation date")
            seen.add(observation_date)
            raw_value = (row.get(document.subject_key) or row.get("VALUE") or "").strip()
            if raw_value in {"", "."}:
                issues.append(
                    _issue(
                        "info", "missing_rate_observation", document.subject_key, observation_date
                    )
                )
                continue
            value = Decimal(raw_value).quantize(RATE_QUANTUM)
            if value <= Decimal("-100"):
                raise ValueError("annual rate must be greater than -100 percent")
            observations.append(
                CanonicalRate(
                    document.subject_key,
                    observation_date,
                    observation_date + timedelta(days=1),
                    value,
                )
            )
        except (InvalidOperation, ValueError) as error:
            issues.append(
                _issue(
                    "error",
                    "invalid_rate_row",
                    document.subject_key,
                    None,
                    str(error),
                    {"row_number": row_number},
                )
            )
    observations.sort(key=lambda item: item.observation_date)
    coverage = None
    if observations:
        coverage = Coverage(
            document.subject_key,
            observations[0].observation_date,
            observations[-1].observation_date,
            len(observations),
            sum(item.rule_code == "missing_rate_observation" for item in issues),
        )
    else:
        issues.append(_issue("error", "no_rate_observations", document.subject_key, None))
    return RateCanonicalResult(tuple(observations), coverage, tuple(issues))


def _csv_rows(payload: bytes) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    if not reader.fieldnames:
        raise csv.Error("CSV header is absent")
    return list(reader)


def _required(row: dict[str, str], field: str) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise ValueError(f"missing {field}")
    return value


def _decimal(row: dict[str, str], field: str, *, default: Decimal | None = None) -> Decimal:
    value = (row.get(field) or "").strip()
    if not value and default is not None:
        return default
    if not value:
        raise ValueError(f"missing {field}")
    return Decimal(value)


def _issue(
    severity: str,
    rule_code: str,
    subject_key: str | None,
    event_date: date | None,
    message: str | None = None,
    details: dict[str, object] | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        severity,
        rule_code,
        message or rule_code.replace("_", " "),
        subject_key,
        event_date,
        details,
    )
