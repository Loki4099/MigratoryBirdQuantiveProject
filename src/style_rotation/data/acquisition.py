from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Protocol

from sqlalchemy import Engine

from style_rotation.data.providers.snapshots import RawFetch, snapshot_key
from style_rotation.data.service import SourceSnapshotService


class MarketSnapshotAdapter(Protocol):
    def fetch(self, symbol: str, start: date, end_exclusive: date) -> RawFetch: ...


class RateSnapshotAdapter(Protocol):
    def fetch(self, series_id: str, start: date, end_inclusive: date) -> RawFetch: ...


@dataclass(frozen=True, slots=True)
class AcquiredSnapshot:
    subject: str
    artifact_id: str
    semantic_fingerprint: str
    content_hash: str
    reused: bool
    raw_size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SourceAcquisitionService:
    def __init__(
        self,
        engine: Engine,
        market_adapter: MarketSnapshotAdapter,
        rate_adapter: RateSnapshotAdapter,
    ) -> None:
        self._snapshots = SourceSnapshotService(engine)
        self._market = market_adapter
        self._rate = rate_adapter

    def acquire(
        self,
        *,
        symbols: tuple[str, ...],
        start: date,
        end_inclusive: date,
        include_market: bool = True,
        include_rate: bool = True,
    ) -> tuple[AcquiredSnapshot, ...]:
        if start > end_inclusive:
            raise ValueError("Acquisition start must not be after end")
        if include_market and not symbols:
            raise ValueError("At least one market symbol is required")
        if len(symbols) != len(set(symbols)):
            raise ValueError("Market symbols must be unique")
        results: list[AcquiredSnapshot] = []
        if include_market:
            fetch_many = getattr(self._market, "fetch_many", None)
            fetched_items = (
                fetch_many(symbols, start, end_inclusive + timedelta(days=1))
                if callable(fetch_many) and len(symbols) > 1
                else tuple(
                    (symbol, self._market.fetch(symbol, start, end_inclusive + timedelta(days=1)))
                    for symbol in symbols
                )
            )
            for symbol, fetched in fetched_items:
                results.append(
                    self._publish(
                        symbol,
                        fetched,
                        series_key="us_etf_daily_market",
                    )
                )
        if include_rate:
            fetched = self._rate.fetch("DGS3MO", start, end_inclusive)
            results.append(self._publish("DGS3MO", fetched, series_key="dgs3mo_daily"))
        return tuple(results)

    def _publish(self, subject: str, fetched: RawFetch, *, series_key: str) -> AcquiredSnapshot:
        key = snapshot_key(subject, fetched.fetched_at)
        published = self._snapshots.publish(
            fetched.snapshot_input(
                series_key=series_key,
                series_version=1,
                snapshot_key=key,
            )
        )
        return AcquiredSnapshot(
            subject=subject,
            artifact_id=str(published.artifact_id),
            semantic_fingerprint=published.semantic_fingerprint,
            content_hash=published.content_hash,
            reused=published.reused,
            raw_size_bytes=len(fetched.payload),
        )
