from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.cleaning import REQUIRED_SYMBOLS, clean_and_validate
from style_rotation.data.contracts import PHASE2_CONTRACTS
from style_rotation.data.providers.base import MarketDataProvider, RateDataProvider
from style_rotation.data.repository import DataRepository
from style_rotation.data.types import (
    DataQualityGateError,
    MarketPriceRecord,
    RateObservation,
)


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    data_version_id: uuid.UUID
    cleaning_version_id: uuid.UUID
    reused: bool
    market_rows: int
    rate_rows: int


class DataIngestionService:
    def __init__(
        self,
        repository: DataRepository,
        market_provider: MarketDataProvider,
        rate_provider: RateDataProvider,
    ) -> None:
        self._repository = repository
        self._market_provider = market_provider
        self._rate_provider = rate_provider

    def run(
        self,
        *,
        start: date,
        end_inclusive: date,
        cleaning_version_key: str,
        cleaning_rules_hash: str,
        cleaning_code_hash: str,
    ) -> IngestionOutcome:
        if start > end_inclusive:
            raise ValueError("Data start must not be after end")
        self._repository.publish_contracts(PHASE2_CONTRACTS)
        cleaning_version_id = self._repository.ensure_cleaning_version(
            version_key=cleaning_version_key,
            rules_hash=cleaning_rules_hash,
            code_hash=cleaning_code_hash,
            configuration={
                "adjustment": "adj_close_over_close_raw",
                "reserve_day_count": "ACT/365",
                "reserve_availability_lag": "one_calendar_day",
                "max_rate_staleness_days": 10,
                "missing_market_values": "fail_publication",
            },
        )
        market_batch = self._market_provider.download(
            REQUIRED_SYMBOLS, start, end_inclusive + timedelta(days=1)
        )
        rate_batch = self._rate_provider.download(
            "DGS3MO", start - timedelta(days=15), end_inclusive
        )
        market_records = tuple(
            item for item in market_batch.records if isinstance(item, MarketPriceRecord)
        )
        rate_observations = tuple(
            item for item in rate_batch.records if isinstance(item, RateObservation)
        )
        combined_hash = sha256_hexdigest(
            {
                "market_content_hash": market_batch.content_hash,
                "rate_content_hash": rate_batch.content_hash,
                "start": start,
                "end_inclusive": end_inclusive,
            }
        )
        existing = self._repository.find_data_version(combined_hash)
        if existing is not None:
            data_version_id, _ = existing
            if self._repository.clean_dataset_exists(data_version_id, cleaning_version_id):
                return IngestionOutcome(
                    data_version_id,
                    cleaning_version_id,
                    True,
                    len(market_records),
                    len(rate_observations),
                )
        else:
            data_version_id = self._repository.create_raw_version(
                version_key=f"data-{combined_hash[:16]}",
                content_hash=combined_hash,
                requested_at=max(market_batch.requested_at, rate_batch.requested_at),
                coverage_start=start,
                coverage_end=end_inclusive,
                request_parameters={
                    "market": market_batch.request_parameters,
                    "reserve": rate_batch.request_parameters,
                },
                source_metadata={
                    "market_content_hash": market_batch.content_hash,
                    "rate_content_hash": rate_batch.content_hash,
                },
                market_records=market_records,
                rate_observations=rate_observations,
            )

        result = clean_and_validate(market_records, rate_observations)
        if result.has_errors:
            self._repository.record_failure(data_version_id, cleaning_version_id, result.issues)
            raise DataQualityGateError(
                f"Data version {data_version_id} failed with {len(result.issues)} quality issue(s)"
            )
        self._repository.publish_clean_dataset(data_version_id, cleaning_version_id, result)
        return IngestionOutcome(
            data_version_id,
            cleaning_version_id,
            False,
            len(market_records),
            len(rate_observations),
        )
