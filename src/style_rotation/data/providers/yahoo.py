from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import yfinance as yf

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.types import MarketPriceRecord, ProviderBatch

YAHOO_DOWNLOAD_PARAMETERS: dict[str, Any] = {
    "interval": "1d",
    "auto_adjust": False,
    "back_adjust": False,
    "actions": True,
    "repair": False,
    "keepna": True,
    "prepost": False,
    "rounding": False,
    "threads": False,
    "group_by": "ticker",
    "progress": False,
    "ignore_tz": True,
    "multi_level_index": True,
}


def _decimal_or_none(value: Any) -> Decimal | None:
    if pd.isna(value):
        return None
    return Decimal(str(value))


def _integer_or_none(value: Any) -> int | None:
    if pd.isna(value):
        return None
    return int(value)


def _symbol_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        if symbol in frame.columns.get_level_values(0):
            return frame[symbol]
        if symbol in frame.columns.get_level_values(1):
            return frame.xs(symbol, axis=1, level=1)
    if len(frame.columns) and not isinstance(frame.columns, pd.MultiIndex):
        return frame
    raise ValueError(f"Yahoo response does not contain ticker {symbol}")


class YahooFinanceProvider:
    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self._timeout_seconds = timeout_seconds

    def download(self, symbols: tuple[str, ...], start: date, end_exclusive: date) -> ProviderBatch:
        if start >= end_exclusive:
            raise ValueError("Yahoo start must be before exclusive end")
        requested_at = datetime.now(UTC)
        parameters = {
            **YAHOO_DOWNLOAD_PARAMETERS,
            "tickers": list(symbols),
            "start": start.isoformat(),
            "end": end_exclusive.isoformat(),
            "timeout": self._timeout_seconds,
        }
        frame = yf.download(**parameters)
        if frame is None or frame.empty:
            raise RuntimeError("Yahoo returned no market data")

        records: list[MarketPriceRecord] = []
        for symbol in symbols:
            symbol_frame = _symbol_frame(frame, symbol)
            for timestamp, row in symbol_frame.iterrows():
                trade_date = pd.Timestamp(timestamp).date()
                payload = {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "open_raw": _decimal_or_none(row.get("Open")),
                    "high_raw": _decimal_or_none(row.get("High")),
                    "low_raw": _decimal_or_none(row.get("Low")),
                    "close_raw": _decimal_or_none(row.get("Close")),
                    "adj_close": _decimal_or_none(row.get("Adj Close")),
                    "volume_raw": _integer_or_none(row.get("Volume")),
                    "dividends": _decimal_or_none(row.get("Dividends")) or Decimal(0),
                    "stock_splits": _decimal_or_none(row.get("Stock Splits")) or Decimal(0),
                }
                records.append(
                    MarketPriceRecord(**payload, source_row_hash=sha256_hexdigest(payload))
                )

        records.sort(key=lambda item: (item.symbol, item.trade_date))
        return ProviderBatch(
            provider="yfinance",
            requested_at=requested_at,
            request_parameters=parameters,
            content_hash=sha256_hexdigest(records),
            records=tuple(records),
        )
