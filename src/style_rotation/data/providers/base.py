from __future__ import annotations

from datetime import date
from typing import Protocol

from style_rotation.data.types import ProviderBatch


class MarketDataProvider(Protocol):
    def download(
        self, symbols: tuple[str, ...], start: date, end_exclusive: date
    ) -> ProviderBatch: ...


class RateDataProvider(Protocol):
    def download(self, series_id: str, start: date, end_inclusive: date) -> ProviderBatch: ...
