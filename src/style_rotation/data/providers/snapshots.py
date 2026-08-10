from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pandas as pd
import yfinance as yf

from style_rotation.data.providers.yahoo import YAHOO_DOWNLOAD_PARAMETERS, _symbol_frame
from style_rotation.data.service import SnapshotInput

Clock = Callable[[], datetime]
HttpGet = Callable[..., httpx.Response]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RawFetch:
    requested_at: datetime
    fetched_at: datetime
    as_of_at: datetime
    media_type: str
    request_parameters: dict[str, Any]
    response_metadata: dict[str, Any]
    payload: bytes

    def snapshot_input(
        self,
        *,
        series_key: str,
        series_version: int,
        snapshot_key: str,
    ) -> SnapshotInput:
        return SnapshotInput(
            series_key=series_key,
            series_version=series_version,
            snapshot_key=snapshot_key,
            requested_at=self.requested_at,
            fetched_at=self.fetched_at,
            as_of_at=self.as_of_at,
            media_type=self.media_type,
            request_parameters=self.request_parameters,
            response_metadata=self.response_metadata,
            raw_payload=self.payload,
        )


class YahooYFinanceSnapshotAdapter:
    """Capture the uncleaned table returned by the declared yfinance wrapper."""

    _fields = (
        "Open",
        "High",
        "Low",
        "Close",
        "Adj Close",
        "Volume",
        "Dividends",
        "Stock Splits",
    )

    def __init__(self, timeout_seconds: float = 30.0, *, clock: Clock = _utc_now) -> None:
        self._timeout_seconds = timeout_seconds
        self._clock = clock

    def fetch(self, symbol: str, start: date, end_exclusive: date) -> RawFetch:
        if not symbol or symbol.strip() != symbol:
            raise ValueError("Yahoo symbol must be a non-empty normalized value")
        if start >= end_exclusive:
            raise ValueError("Yahoo start must be before exclusive end")
        requested_at = self._clock()
        provider_symbol = _yahoo_symbol(symbol)
        parameters = {
            **YAHOO_DOWNLOAD_PARAMETERS,
            "tickers": provider_symbol,
            "start": start.isoformat(),
            "end": end_exclusive.isoformat(),
            "timeout": self._timeout_seconds,
        }
        frame = yf.download(**parameters)
        fetched_at = self._clock()
        if frame is None or frame.empty:
            raise RuntimeError(f"Yahoo returned no market data for {symbol}")
        return self._snapshot_from_frame(
            frame,
            provider_symbol,
            {**parameters, "tickers": symbol, "provider_ticker": provider_symbol},
            requested_at=requested_at,
            fetched_at=fetched_at,
        )

    def fetch_many(
        self, symbols: tuple[str, ...], start: date, end_exclusive: date
    ) -> tuple[tuple[str, RawFetch], ...]:
        """Download symbols in bounded batches while preserving one immutable snapshot each."""
        if not symbols or len(symbols) != len(set(symbols)):
            raise ValueError("Yahoo batch symbols must be non-empty and unique")
        results: list[tuple[str, RawFetch]] = []
        failures: list[str] = []
        for offset in range(0, len(symbols), 40):
            batch = symbols[offset : offset + 40]
            provider_batch = tuple(_yahoo_symbol(symbol) for symbol in batch)
            requested_at = self._clock()
            parameters = {
                **YAHOO_DOWNLOAD_PARAMETERS,
                "tickers": list(provider_batch),
                "start": start.isoformat(),
                "end": end_exclusive.isoformat(),
                "timeout": self._timeout_seconds,
                "threads": True,
            }
            frame = yf.download(**parameters)
            fetched_at = self._clock()
            if frame is None or frame.empty:
                failures.extend(batch)
                continue
            for symbol, provider_symbol in zip(batch, provider_batch, strict=True):
                try:
                    per_symbol = {
                        **parameters,
                        "tickers": symbol,
                        "provider_ticker": provider_symbol,
                        "batch_tickers": list(batch),
                    }
                    results.append(
                        (
                            symbol,
                            self._snapshot_from_frame(
                                frame,
                                provider_symbol,
                                per_symbol,
                                requested_at=requested_at,
                                fetched_at=fetched_at,
                            ),
                        )
                    )
                except (KeyError, ValueError, RuntimeError):
                    failures.append(symbol)
        if failures:
            raise RuntimeError(f"Yahoo returned no usable market data for: {', '.join(failures)}")
        return tuple(results)

    def _snapshot_from_frame(
        self,
        frame: pd.DataFrame,
        symbol: str,
        parameters: dict[str, Any],
        *,
        requested_at: datetime,
        fetched_at: datetime,
    ) -> RawFetch:
        symbol_frame = _symbol_frame(frame, symbol).copy()
        for field in self._fields:
            if field not in symbol_frame:
                symbol_frame[field] = pd.NA
        symbol_frame = symbol_frame.loc[:, list(self._fields)].sort_index()
        symbol_frame = symbol_frame.dropna(
            how="all", subset=["Open", "High", "Low", "Close", "Adj Close", "Volume"]
        )
        if symbol_frame.empty:
            raise RuntimeError(f"Yahoo returned no market data for {symbol}")
        symbol_frame.index = pd.to_datetime(symbol_frame.index).strftime("%Y-%m-%d")
        symbol_frame.index.name = "session_date"
        payload = symbol_frame.to_csv(
            date_format="%Y-%m-%d", lineterminator="\n", na_rep=""
        ).encode("utf-8")
        return RawFetch(
            requested_at=requested_at,
            fetched_at=fetched_at,
            as_of_at=fetched_at,
            media_type="text/csv; charset=utf-8",
            request_parameters=parameters,
            response_metadata={
                "adapter": "yfinance.download",
                "wrapper_version": importlib.metadata.version("yfinance"),
                "row_count": len(symbol_frame),
                "fields": list(self._fields),
                "raw_semantics": "uncleaned wrapper table; not a historical vendor vintage",
            },
            payload=payload,
        )


def _yahoo_symbol(symbol: str) -> str:
    """Translate exchange class-share notation to Yahoo's ticker convention."""
    return symbol.replace(".", "-")


class FredCsvSnapshotAdapter:
    """Capture exact FRED CSV response bytes without interpreting observations."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        *,
        get: HttpGet = httpx.get,
        clock: Clock = _utc_now,
    ) -> None:
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._get = get
        self._clock = clock

    def fetch(self, series_id: str, start: date, end_inclusive: date) -> RawFetch:
        if not series_id or series_id.strip() != series_id:
            raise ValueError("FRED series id must be a non-empty normalized value")
        if start > end_inclusive:
            raise ValueError("FRED start must not be after end")
        parameters = {
            "id": series_id,
            "cosd": start.isoformat(),
            "coed": end_inclusive.isoformat(),
        }
        requested_at = self._clock()
        response = self._get(
            self._base_url,
            params=parameters,
            timeout=self._timeout_seconds,
            follow_redirects=True,
        )
        fetched_at = self._clock()
        response.raise_for_status()
        if not response.content:
            raise RuntimeError(f"FRED returned an empty response for {series_id}")
        selected_headers = {
            key.lower(): value
            for key, value in response.headers.items()
            if key.lower() in {"content-type", "etag", "last-modified"}
        }
        return RawFetch(
            requested_at=requested_at,
            fetched_at=fetched_at,
            as_of_at=fetched_at,
            media_type=response.headers.get("content-type", "text/csv"),
            request_parameters=parameters,
            response_metadata={
                "adapter": "httpx.get",
                "status_code": response.status_code,
                "headers": selected_headers,
                "final_url": str(response.url),
            },
            payload=response.content,
        )


def snapshot_key(subject: str, fetched_at: datetime) -> str:
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("Snapshot timestamps must be timezone-aware")
    timestamp = fetched_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{subject.lower()}-{timestamp}"
