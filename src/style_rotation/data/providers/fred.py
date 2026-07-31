from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.types import ProviderBatch, RateObservation


class FredCsvProvider:
    """Download current FRED history and conservatively lag availability by one day."""

    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds

    def download(self, series_id: str, start: date, end_inclusive: date) -> ProviderBatch:
        if start > end_inclusive:
            raise ValueError("FRED start must not be after end")
        requested_at = datetime.now(UTC)
        parameters = {"id": series_id}
        response = httpx.get(self._base_url, params=parameters, timeout=self._timeout_seconds)
        response.raise_for_status()

        records: list[RateObservation] = []
        reader = csv.DictReader(io.StringIO(response.text))
        fieldnames = reader.fieldnames or []
        date_column = "DATE" if "DATE" in fieldnames else "observation_date"
        value_column = series_id if series_id in fieldnames else "VALUE"
        for row in reader:
            observation_date = date.fromisoformat(row[date_column])
            if observation_date < start or observation_date > end_inclusive:
                continue
            raw_value = row.get(value_column, "").strip()
            if raw_value in {"", "."}:
                continue
            available_date = observation_date + timedelta(days=1)
            annual_rate_percent = Decimal(raw_value)
            payload = {
                "series_id": series_id,
                "observation_date": observation_date,
                "available_date": available_date,
                "annual_rate_percent": annual_rate_percent,
            }
            records.append(
                RateObservation(
                    series_id=series_id,
                    observation_date=observation_date,
                    available_date=available_date,
                    annual_rate_percent=annual_rate_percent,
                    source_row_hash=sha256_hexdigest(payload),
                )
            )

        if not records:
            raise RuntimeError(f"FRED returned no usable observations for {series_id}")
        records.sort(key=lambda item: item.observation_date)
        request_parameters = {
            **parameters,
            "start": start.isoformat(),
            "end": end_inclusive.isoformat(),
            "availability_lag": "one_calendar_day",
        }
        return ProviderBatch(
            provider="fred_csv",
            requested_at=requested_at,
            request_parameters=request_parameters,
            content_hash=sha256_hexdigest(records),
            records=tuple(records),
        )
