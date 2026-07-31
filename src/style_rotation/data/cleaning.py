from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.types import (
    CleanMarketPriceRecord,
    CleanResult,
    MarketPriceRecord,
    QualityIssue,
    RateObservation,
    ReserveDailyRecord,
)

REQUIRED_SYMBOLS = ("IWF", "IWD", "IWO", "IWN", "SPY")
REFERENCE_SYMBOL = "SPY"
MAX_RATE_STALENESS_DAYS = 10
MAX_ABSOLUTE_DAILY_RETURN = Decimal("0.50")


def _error(
    rule_code: str,
    message: str,
    *,
    symbol: str | None = None,
    series_id: str | None = None,
    event_date: date | None = None,
    details: dict[str, object] | None = None,
) -> QualityIssue:
    return QualityIssue(
        severity="error",
        rule_code=rule_code,
        message=message,
        symbol=symbol,
        series_id=series_id,
        event_date=event_date,
        details=details,
    )


def _calendar_daily_factor(rate_percent: Decimal) -> Decimal:
    base = Decimal(1) + rate_percent / Decimal(100)
    if base <= 0:
        raise ValueError("Annual rate must be greater than -100 percent")
    with localcontext() as context:
        context.prec = 34
        return ((base.ln() / Decimal(365)).exp()).quantize(Decimal("0.0000000000000001"))


def clean_and_validate(
    market_records: tuple[MarketPriceRecord, ...],
    rate_observations: tuple[RateObservation, ...],
    *,
    required_symbols: tuple[str, ...] = REQUIRED_SYMBOLS,
    reference_symbol: str = REFERENCE_SYMBOL,
) -> CleanResult:
    issues: list[QualityIssue] = []
    by_symbol: dict[str, list[MarketPriceRecord]] = defaultdict(list)
    seen_keys: set[tuple[str, date]] = set()
    for record in market_records:
        key = (record.symbol, record.trade_date)
        if key in seen_keys:
            issues.append(
                _error(
                    "duplicate_market_key",
                    "Duplicate symbol and trade-date row",
                    symbol=record.symbol,
                    event_date=record.trade_date,
                )
            )
        seen_keys.add(key)
        by_symbol[record.symbol].append(record)

    missing_symbols = sorted(set(required_symbols).difference(by_symbol))
    for symbol in missing_symbols:
        issues.append(_error("missing_symbol", "Required ETF is absent", symbol=symbol))
    if missing_symbols:
        return CleanResult((), (), tuple(issues), None, None)

    valid_start_by_symbol: dict[str, date] = {}
    for symbol in required_symbols:
        valid_dates = [
            item.trade_date
            for item in by_symbol[symbol]
            if item.open_raw is not None
            and item.high_raw is not None
            and item.low_raw is not None
            and item.close_raw is not None
            and item.adj_close is not None
            and item.volume_raw is not None
        ]
        if not valid_dates:
            issues.append(
                _error(
                    "no_valid_market_history",
                    "Required ETF has no complete OHLCV history",
                    symbol=symbol,
                )
            )
        else:
            valid_start_by_symbol[symbol] = min(valid_dates)
    if len(valid_start_by_symbol) != len(required_symbols):
        return CleanResult((), (), tuple(issues), None, None)
    common_start = max(valid_start_by_symbol.values())
    reference_dates = {
        item.trade_date for item in by_symbol[reference_symbol] if item.trade_date >= common_start
    }
    if not reference_dates:
        issues.append(_error("empty_common_calendar", "No common market dates are available"))
        return CleanResult((), (), tuple(issues), None, None)

    for symbol in required_symbols:
        symbol_dates = {
            item.trade_date for item in by_symbol[symbol] if item.trade_date >= common_start
        }
        missing_dates = sorted(reference_dates.difference(symbol_dates))
        extra_dates = sorted(symbol_dates.difference(reference_dates))
        for missing_date in missing_dates:
            issues.append(
                _error(
                    "missing_market_session",
                    "ETF row is missing on an SPY market date",
                    symbol=symbol,
                    event_date=missing_date,
                )
            )
        for extra_date in extra_dates:
            issues.append(
                _error(
                    "unexpected_market_session",
                    "ETF row exists on a date absent from SPY",
                    symbol=symbol,
                    event_date=extra_date,
                )
            )

    clean_prices: list[CleanMarketPriceRecord] = []
    for symbol in required_symbols:
        prior_close: Decimal | None = None
        for record in sorted(by_symbol[symbol], key=lambda item: item.trade_date):
            if record.trade_date < common_start:
                continue
            values = (
                record.open_raw,
                record.high_raw,
                record.low_raw,
                record.close_raw,
                record.adj_close,
            )
            if any(value is None for value in values) or record.volume_raw is None:
                issues.append(
                    _error(
                        "null_required_market_value",
                        "Required OHLCV or adjusted close value is null",
                        symbol=symbol,
                        event_date=record.trade_date,
                    )
                )
                continue
            open_raw, high_raw, low_raw, close_raw, adj_close = values
            assert open_raw is not None
            assert high_raw is not None
            assert low_raw is not None
            assert close_raw is not None
            assert adj_close is not None
            if min(open_raw, high_raw, low_raw, close_raw, adj_close) <= 0:
                issues.append(
                    _error(
                        "nonpositive_price",
                        "OHLC and adjusted close must be positive",
                        symbol=symbol,
                        event_date=record.trade_date,
                    )
                )
                continue
            if high_raw < max(open_raw, close_raw) or low_raw > min(open_raw, close_raw):
                issues.append(
                    _error(
                        "invalid_ohlc_geometry",
                        "Raw OHLC values violate high/low bounds",
                        symbol=symbol,
                        event_date=record.trade_date,
                    )
                )
                continue
            if record.volume_raw < 0 or record.dividends < 0 or record.stock_splits < 0:
                issues.append(
                    _error(
                        "negative_market_value",
                        "Volume, dividends, and split ratio must be nonnegative",
                        symbol=symbol,
                        event_date=record.trade_date,
                    )
                )
                continue
            factor = adj_close / close_raw
            adjusted = CleanMarketPriceRecord(
                symbol=symbol,
                trade_date=record.trade_date,
                open_adj=open_raw * factor,
                high_adj=high_raw * factor,
                low_adj=low_raw * factor,
                close_adj=adj_close,
                adj_factor=factor,
                volume_raw=record.volume_raw,
                dividends=record.dividends,
                stock_splits=record.stock_splits,
            )
            if prior_close is not None:
                daily_return = adjusted.close_adj / prior_close - Decimal(1)
                if abs(daily_return) > MAX_ABSOLUTE_DAILY_RETURN:
                    issues.append(
                        _error(
                            "extreme_adjusted_return",
                            "Adjusted close-to-close move exceeds 50 percent",
                            symbol=symbol,
                            event_date=record.trade_date,
                            details={"return": str(daily_return)},
                        )
                    )
            prior_close = adjusted.close_adj
            clean_prices.append(adjusted)

    observations = sorted(rate_observations, key=lambda item: item.available_date)
    reserve_returns: list[ReserveDailyRecord] = []
    observation_index = 0
    current_observation: RateObservation | None = None
    for nav_date in sorted(reference_dates):
        while (
            observation_index < len(observations)
            and observations[observation_index].available_date <= nav_date
        ):
            current_observation = observations[observation_index]
            observation_index += 1
        if current_observation is None:
            issues.append(
                _error(
                    "missing_available_reserve_rate",
                    "No DGS3MO observation was available by this market date",
                    series_id="DGS3MO",
                    event_date=nav_date,
                )
            )
            continue
        staleness = (nav_date - current_observation.available_date).days
        if staleness > MAX_RATE_STALENESS_DAYS:
            issues.append(
                _error(
                    "stale_reserve_rate",
                    "Most recent available DGS3MO observation is more than 10 days old",
                    series_id=current_observation.series_id,
                    event_date=nav_date,
                    details={"staleness_days": staleness},
                )
            )
            continue
        try:
            daily_factor = _calendar_daily_factor(current_observation.annual_rate_percent)
        except ValueError as error:
            issues.append(
                _error(
                    "invalid_reserve_rate",
                    str(error),
                    series_id=current_observation.series_id,
                    event_date=nav_date,
                )
            )
            continue
        reserve_returns.append(
            ReserveDailyRecord(
                nav_date=nav_date,
                series_id=current_observation.series_id,
                source_observation_date=current_observation.observation_date,
                source_available_date=current_observation.available_date,
                annual_rate_percent=current_observation.annual_rate_percent,
                calendar_daily_factor=daily_factor,
            )
        )

    if any(issue.severity == "error" for issue in issues):
        return CleanResult(
            tuple(clean_prices), tuple(reserve_returns), tuple(issues), common_start, None
        )
    content_hash = sha256_hexdigest(
        {"prices": clean_prices, "reserve_returns": reserve_returns, "common_start": common_start}
    )
    return CleanResult(
        tuple(clean_prices),
        tuple(reserve_returns),
        tuple(issues),
        common_start,
        content_hash,
    )
