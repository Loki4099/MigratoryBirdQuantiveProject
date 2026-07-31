from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from psycopg import Connection as PsycopgConnection
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from style_rotation.contracts.spec import DataContractSpec
from style_rotation.core.canonical import canonicalize
from style_rotation.data.types import CleanMarketPriceRecord
from style_rotation.domain.enums import FactorDirection
from style_rotation.persistence.models import (
    Asset,
    CleanMarketPrice,
    DataContract,
    FactorDataset,
    FactorDefinition,
    FactorValue,
    FactorVariant,
    SignalDataset,
    StrategyVersion,
)
from style_rotation.signals.types import (
    FactorSignalPoint,
    SignalComputationResult,
)


class SignalRepository:
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

    def latest_factor_dataset_ids(self) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        with self._session_factory() as session:
            dataset = session.scalar(
                select(FactorDataset)
                .where(FactorDataset.status == "published")
                .order_by(FactorDataset.created_at.desc())
                .limit(1)
            )
            if dataset is None:
                raise LookupError("No published factor dataset is available")
            return (
                dataset.data_version_id,
                dataset.cleaning_version_id,
                dataset.factor_version_id,
            )

    def load_inputs(
        self,
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        factor_version_id: uuid.UUID,
    ) -> tuple[
        tuple[CleanMarketPriceRecord, ...],
        tuple[FactorSignalPoint, ...],
        Any,
    ]:
        with self._session_factory() as session:
            dataset = session.get(
                FactorDataset,
                {
                    "data_version_id": data_version_id,
                    "cleaning_version_id": cleaning_version_id,
                    "factor_version_id": factor_version_id,
                },
            )
            if dataset is None or dataset.status != "published":
                raise LookupError("Requested factor dataset is not published")
            price_rows = session.execute(
                select(CleanMarketPrice, Asset.symbol)
                .join(Asset, Asset.asset_id == CleanMarketPrice.asset_id)
                .where(
                    CleanMarketPrice.data_version_id == data_version_id,
                    CleanMarketPrice.cleaning_version_id == cleaning_version_id,
                    Asset.symbol.in_(("IWF", "IWD", "IWO", "IWN")),
                )
                .order_by(Asset.symbol, CleanMarketPrice.trade_date)
            ).all()
            prices = tuple(
                CleanMarketPriceRecord(
                    symbol=symbol,
                    trade_date=price.trade_date,
                    open_adj=price.open_adj,
                    high_adj=price.high_adj,
                    low_adj=price.low_adj,
                    close_adj=price.close_adj,
                    adj_factor=price.adj_factor,
                    volume_raw=price.volume_raw,
                    dividends=price.dividends,
                    stock_splits=price.stock_splits,
                )
                for price, symbol in price_rows
            )
            factor_rows = session.execute(
                select(
                    FactorVariant.variant_key,
                    Asset.symbol,
                    FactorValue.trade_date,
                    FactorValue.raw_value,
                    FactorDefinition.direction,
                )
                .join(
                    FactorVariant,
                    FactorVariant.factor_variant_id == FactorValue.factor_variant_id,
                )
                .join(
                    FactorDefinition,
                    FactorDefinition.factor_definition_id == FactorVariant.factor_definition_id,
                )
                .join(Asset, Asset.asset_id == FactorValue.asset_id)
                .where(
                    FactorValue.data_version_id == data_version_id,
                    FactorValue.cleaning_version_id == cleaning_version_id,
                    FactorValue.factor_version_id == factor_version_id,
                    FactorValue.trade_date >= dataset.common_valid_start,
                )
                .order_by(
                    FactorVariant.variant_key,
                    FactorValue.trade_date,
                    Asset.symbol,
                )
            ).all()
            factor_points = tuple(
                FactorSignalPoint(
                    variant_key,
                    symbol,
                    trade_date,
                    raw_value,
                    FactorDirection(direction),
                )
                for variant_key, symbol, trade_date, raw_value, direction in factor_rows
            )
            return prices, factor_points, dataset.common_valid_start

    def ensure_strategy_version(
        self,
        *,
        version_key: str,
        configuration_hash: str,
        configuration: dict[str, Any],
    ) -> uuid.UUID:
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(StrategyVersion).where(StrategyVersion.version_key == version_key)
            )
            if existing is not None:
                if existing.configuration_hash != configuration_hash:
                    raise ValueError("Strategy version key already exists with a different hash")
                return existing.strategy_version_id
            version = StrategyVersion(
                version_key=version_key,
                configuration_hash=configuration_hash,
                configuration=canonicalize(configuration),
            )
            session.add(version)
            session.flush()
            return version.strategy_version_id

    def signal_dataset_summary(
        self,
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        factor_version_id: uuid.UUID,
        strategy_version_id: uuid.UUID,
    ) -> tuple[int, int, Any] | None:
        with self._session_factory() as session:
            dataset = session.get(
                SignalDataset,
                {
                    "data_version_id": data_version_id,
                    "cleaning_version_id": cleaning_version_id,
                    "factor_version_id": factor_version_id,
                    "strategy_version_id": strategy_version_id,
                },
            )
            if dataset is None:
                return None
            return dataset.event_count, dataset.position_count, dataset.first_signal_date

    def publish_signal_result(
        self,
        *,
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        factor_version_id: uuid.UUID,
        strategy_version_id: uuid.UUID,
        result: SignalComputationResult,
    ) -> None:
        with self._session_factory.begin() as session:
            assets = {
                asset.symbol: asset.asset_id
                for asset in session.scalars(
                    select(Asset).where(Asset.symbol.in_(("IWF", "IWD", "IWO", "IWN")))
                )
            }
            variants = {
                variant.variant_key: variant.factor_variant_id
                for variant in session.scalars(
                    select(FactorVariant).where(
                        FactorVariant.factor_version_id == factor_version_id
                    )
                )
            }
            session.add(
                SignalDataset(
                    data_version_id=data_version_id,
                    cleaning_version_id=cleaning_version_id,
                    factor_version_id=factor_version_id,
                    strategy_version_id=strategy_version_id,
                    content_hash=result.content_hash,
                    first_signal_date=result.first_signal_date,
                    first_execution_date=result.first_execution_date,
                    coverage_end=result.coverage_end,
                    event_count=len(result.events),
                    position_count=result.position_count,
                    status="published",
                )
            )
            session.flush()
            driver_connection = session.connection().connection.driver_connection
            if not isinstance(driver_connection, PsycopgConnection):
                raise TypeError("Signal publication requires the psycopg PostgreSQL driver")
            event_copy_sql = """
                COPY rebalance_events (
                    rebalance_event_id, data_version_id, cleaning_version_id,
                    factor_version_id, strategy_version_id, factor_variant_id,
                    rebalance_frequency, strategy_template, signal_date,
                    execution_date, eligible_count, tie_flag, reserve_target_weight
                ) FROM STDIN
            """
            with driver_connection.cursor().copy(event_copy_sql) as copy:
                for event in result.events:
                    event_id = self._event_id(
                        data_version_id,
                        cleaning_version_id,
                        factor_version_id,
                        strategy_version_id,
                        event.variant_key,
                        event.frequency.value,
                        event.strategy_template.value,
                        event.signal_date,
                    )
                    copy.write_row(
                        (
                            event_id,
                            data_version_id,
                            cleaning_version_id,
                            factor_version_id,
                            strategy_version_id,
                            variants[event.variant_key],
                            event.frequency.value,
                            event.strategy_template.value,
                            event.signal_date,
                            event.execution_date,
                            event.eligible_count,
                            event.tie_flag,
                            event.reserve_target_weight,
                        )
                    )
            position_copy_sql = """
                COPY target_positions (
                    rebalance_event_id, asset_id, raw_factor_value,
                    oriented_factor_value, rank, trend_eligible, tie_flag,
                    selected, target_weight
                ) FROM STDIN
            """
            with driver_connection.cursor().copy(position_copy_sql) as copy:
                for event in result.events:
                    event_id = self._event_id(
                        data_version_id,
                        cleaning_version_id,
                        factor_version_id,
                        strategy_version_id,
                        event.variant_key,
                        event.frequency.value,
                        event.strategy_template.value,
                        event.signal_date,
                    )
                    for position in event.positions:
                        copy.write_row(
                            (
                                event_id,
                                assets[position.symbol],
                                position.raw_factor_value,
                                position.oriented_factor_value,
                                position.rank,
                                position.trend_eligible,
                                position.tie_flag,
                                position.selected,
                                position.target_weight,
                            )
                        )

    @staticmethod
    def _event_id(
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        factor_version_id: uuid.UUID,
        strategy_version_id: uuid.UUID,
        variant_key: str,
        frequency: str,
        strategy_template: str,
        signal_date: Any,
    ) -> uuid.UUID:
        identity = (
            f"{data_version_id}|{cleaning_version_id}|{factor_version_id}|"
            f"{strategy_version_id}|{variant_key}|{frequency}|"
            f"{strategy_template}|{signal_date}"
        )
        return uuid.uuid5(uuid.NAMESPACE_URL, identity)
