from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from style_rotation.contracts.spec import DataContractSpec
from style_rotation.core.canonical import canonicalize
from style_rotation.data.types import (
    CleanResult,
    MarketPriceRecord,
    QualityIssue,
    RateObservation,
)
from style_rotation.persistence.models import (
    Asset,
    CleanDataset,
    CleaningVersion,
    CleanMarketPrice,
    DataContract,
    DataQualityEvent,
    DataVersion,
    RawMarketPrice,
    RawRateObservation,
    ReserveDailyReturn,
)


class DataRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def publish_contracts(self, contracts: Iterable[DataContractSpec]) -> None:
        with self._session_factory.begin() as session:
            for contract in contracts:
                existing = session.scalar(
                    select(DataContract).where(DataContract.contract_hash == contract.contract_hash)
                )
                if existing is None:
                    session.add(
                        DataContract(
                            layer=contract.layer.value,
                            name=contract.name,
                            schema_version=contract.schema_version,
                            contract_hash=contract.contract_hash,
                            contract_body=canonicalize(contract),
                        )
                    )

    def ensure_cleaning_version(
        self,
        *,
        version_key: str,
        rules_hash: str,
        code_hash: str,
        configuration: dict[str, Any],
    ) -> uuid.UUID:
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(CleaningVersion).where(CleaningVersion.version_key == version_key)
            )
            if existing is not None:
                if existing.rules_hash != rules_hash or existing.code_hash != code_hash:
                    raise ValueError("Cleaning version key already exists with different hashes")
                return existing.cleaning_version_id
            version = CleaningVersion(
                version_key=version_key,
                rules_hash=rules_hash,
                code_hash=code_hash,
                configuration=configuration,
            )
            session.add(version)
            session.flush()
            return version.cleaning_version_id

    def find_data_version(self, content_hash: str) -> tuple[uuid.UUID, str] | None:
        with self._session_factory() as session:
            version = session.scalar(
                select(DataVersion).where(DataVersion.content_hash == content_hash)
            )
            if version is None:
                return None
            return version.data_version_id, version.status

    def clean_dataset_exists(
        self, data_version_id: uuid.UUID, cleaning_version_id: uuid.UUID
    ) -> bool:
        with self._session_factory() as session:
            return (
                session.get(
                    CleanDataset,
                    {
                        "data_version_id": data_version_id,
                        "cleaning_version_id": cleaning_version_id,
                    },
                )
                is not None
            )

    def create_raw_version(
        self,
        *,
        version_key: str,
        content_hash: str,
        requested_at: datetime,
        coverage_start: date,
        coverage_end: date,
        request_parameters: dict[str, Any],
        source_metadata: dict[str, Any],
        market_records: tuple[MarketPriceRecord, ...],
        rate_observations: tuple[RateObservation, ...],
    ) -> uuid.UUID:
        with self._session_factory.begin() as session:
            assets = {asset.symbol: asset.asset_id for asset in session.scalars(select(Asset))}
            missing = sorted({item.symbol for item in market_records}.difference(assets))
            if missing:
                raise ValueError(f"Unknown assets in provider data: {missing}")
            version = DataVersion(
                version_key=version_key,
                provider="yfinance+fred_csv",
                content_hash=content_hash,
                requested_at=requested_at.astimezone(UTC),
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                request_parameters=canonicalize(request_parameters),
                source_metadata=canonicalize(source_metadata),
                status="pending",
            )
            session.add(version)
            session.flush()
            session.add_all(
                RawMarketPrice(
                    data_version_id=version.data_version_id,
                    asset_id=assets[item.symbol],
                    trade_date=item.trade_date,
                    open_raw=item.open_raw,
                    high_raw=item.high_raw,
                    low_raw=item.low_raw,
                    close_raw=item.close_raw,
                    adj_close=item.adj_close,
                    volume_raw=item.volume_raw,
                    dividends=item.dividends,
                    stock_splits=item.stock_splits,
                    source_row_hash=item.source_row_hash,
                )
                for item in market_records
            )
            session.add_all(
                RawRateObservation(
                    data_version_id=version.data_version_id,
                    series_id=item.series_id,
                    observation_date=item.observation_date,
                    available_date=item.available_date,
                    annual_rate_percent=item.annual_rate_percent,
                    source_row_hash=item.source_row_hash,
                )
                for item in rate_observations
            )
            return version.data_version_id

    def record_failure(
        self,
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        issues: tuple[QualityIssue, ...],
    ) -> None:
        with self._session_factory.begin() as session:
            version = session.get(DataVersion, data_version_id)
            if version is None:
                raise LookupError("Data version not found")
            version.status = "failed"
            version.failure_message = f"Data quality gate rejected {len(issues)} issue(s)"
            self._add_issues(session, data_version_id, cleaning_version_id, issues)

    def publish_clean_dataset(
        self,
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        result: CleanResult,
    ) -> None:
        if result.has_errors or result.common_market_start is None or result.content_hash is None:
            raise ValueError("Cannot publish a failed or incomplete clean result")
        with self._session_factory.begin() as session:
            assets = {asset.symbol: asset.asset_id for asset in session.scalars(select(Asset))}
            session.add_all(
                CleanMarketPrice(
                    data_version_id=data_version_id,
                    cleaning_version_id=cleaning_version_id,
                    asset_id=assets[item.symbol],
                    trade_date=item.trade_date,
                    open_adj=item.open_adj,
                    high_adj=item.high_adj,
                    low_adj=item.low_adj,
                    close_adj=item.close_adj,
                    adj_factor=item.adj_factor,
                    volume_raw=item.volume_raw,
                    dividends=item.dividends,
                    stock_splits=item.stock_splits,
                )
                for item in result.prices
            )
            session.add_all(
                ReserveDailyReturn(
                    data_version_id=data_version_id,
                    cleaning_version_id=cleaning_version_id,
                    nav_date=item.nav_date,
                    series_id=item.series_id,
                    source_observation_date=item.source_observation_date,
                    source_available_date=item.source_available_date,
                    annual_rate_percent=item.annual_rate_percent,
                    calendar_daily_factor=item.calendar_daily_factor,
                )
                for item in result.reserve_returns
            )
            coverage_dates = [item.trade_date for item in result.prices]
            session.add(
                CleanDataset(
                    data_version_id=data_version_id,
                    cleaning_version_id=cleaning_version_id,
                    content_hash=result.content_hash,
                    coverage_start=min(coverage_dates),
                    coverage_end=max(coverage_dates),
                    common_market_start=result.common_market_start,
                    status="published",
                )
            )
            self._add_issues(session, data_version_id, cleaning_version_id, result.issues)
            version = session.get(DataVersion, data_version_id)
            if version is None:
                raise LookupError("Data version not found")
            version.status = "published"
            version.published_at = datetime.now(UTC)
            version.failure_message = None

    @staticmethod
    def _add_issues(
        session: Session,
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        issues: tuple[QualityIssue, ...],
    ) -> None:
        assets = {asset.symbol: asset.asset_id for asset in session.scalars(select(Asset))}
        session.add_all(
            DataQualityEvent(
                data_version_id=data_version_id,
                cleaning_version_id=cleaning_version_id,
                severity=issue.severity,
                rule_code=issue.rule_code,
                asset_id=assets.get(issue.symbol) if issue.symbol else None,
                series_id=issue.series_id,
                event_date=issue.event_date,
                message=issue.message,
                details=issue.details or {},
            )
            for issue in issues
        )
