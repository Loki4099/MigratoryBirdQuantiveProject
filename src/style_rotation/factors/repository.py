from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import insert, select
from sqlalchemy.orm import Session, sessionmaker

from style_rotation.contracts.spec import DataContractSpec
from style_rotation.core.canonical import canonicalize
from style_rotation.data.types import CleanMarketPriceRecord
from style_rotation.factors.types import (
    FactorComputationResult,
    FactorDefinitionSpec,
    FactorVariantSpec,
)
from style_rotation.persistence.models import (
    Asset,
    CleanDataset,
    CleanMarketPrice,
    DataContract,
    FactorDataset,
    FactorDefinition,
    FactorValue,
    FactorVariant,
    FactorVersion,
)


class FactorRepository:
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

    def latest_clean_dataset_ids(self) -> tuple[uuid.UUID, uuid.UUID]:
        with self._session_factory() as session:
            dataset = session.scalar(
                select(CleanDataset)
                .where(CleanDataset.status == "published")
                .order_by(CleanDataset.published_at.desc())
                .limit(1)
            )
            if dataset is None:
                raise LookupError("No published clean dataset is available")
            return dataset.data_version_id, dataset.cleaning_version_id

    def load_clean_prices(
        self, data_version_id: uuid.UUID, cleaning_version_id: uuid.UUID
    ) -> tuple[CleanMarketPriceRecord, ...]:
        with self._session_factory() as session:
            dataset = session.get(
                CleanDataset,
                {
                    "data_version_id": data_version_id,
                    "cleaning_version_id": cleaning_version_id,
                },
            )
            if dataset is None or dataset.status != "published":
                raise LookupError("Requested clean dataset is not published")
            rows = session.execute(
                select(CleanMarketPrice, Asset.symbol)
                .join(Asset, Asset.asset_id == CleanMarketPrice.asset_id)
                .where(
                    CleanMarketPrice.data_version_id == data_version_id,
                    CleanMarketPrice.cleaning_version_id == cleaning_version_id,
                    Asset.symbol.in_(("IWF", "IWD", "IWO", "IWN")),
                )
                .order_by(Asset.symbol, CleanMarketPrice.trade_date)
            ).all()
            return tuple(
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
                for price, symbol in rows
            )

    def ensure_factor_version(
        self,
        *,
        version_key: str,
        registry_hash: str,
        code_hash: str,
        definitions: tuple[FactorDefinitionSpec, ...],
        variants: tuple[FactorVariantSpec, ...],
    ) -> uuid.UUID:
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(FactorVersion).where(FactorVersion.version_key == version_key)
            )
            if existing is not None:
                if existing.registry_hash != registry_hash or existing.code_hash != code_hash:
                    raise ValueError("Factor version key already exists with different hashes")
                return existing.factor_version_id
            version = FactorVersion(
                version_key=version_key,
                registry_hash=registry_hash,
                code_hash=code_hash,
            )
            session.add(version)
            session.flush()
            definition_ids: dict[str, uuid.UUID] = {}
            for spec in definitions:
                definition = FactorDefinition(
                    factor_version_id=version.factor_version_id,
                    definition_key=spec.key,
                    family=spec.family,
                    name=spec.name,
                    description=spec.description,
                    formula=spec.formula,
                    required_fields=list(spec.required_fields),
                    direction=spec.direction.value,
                    implementation_key=spec.implementation_key,
                )
                session.add(definition)
                session.flush()
                definition_ids[spec.key] = definition.factor_definition_id
            session.add_all(
                FactorVariant(
                    factor_version_id=version.factor_version_id,
                    factor_definition_id=definition_ids[spec.definition_key],
                    variant_key=spec.key,
                    parameters=spec.parameters,
                    minimum_observations=spec.minimum_observations,
                )
                for spec in variants
            )
            return version.factor_version_id

    def factor_dataset_exists(
        self,
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        factor_version_id: uuid.UUID,
    ) -> FactorDataset | None:
        with self._session_factory() as session:
            return session.get(
                FactorDataset,
                {
                    "data_version_id": data_version_id,
                    "cleaning_version_id": cleaning_version_id,
                    "factor_version_id": factor_version_id,
                },
            )

    def publish_factor_result(
        self,
        *,
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        factor_version_id: uuid.UUID,
        result: FactorComputationResult,
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
            if len(assets) != 4 or len(variants) == 0:
                raise LookupError("Factor publication metadata is incomplete")
            rows = [
                {
                    "data_version_id": data_version_id,
                    "cleaning_version_id": cleaning_version_id,
                    "factor_version_id": factor_version_id,
                    "factor_variant_id": variants[point.variant_key],
                    "asset_id": assets[point.symbol],
                    "trade_date": point.trade_date,
                    "raw_value": point.raw_value,
                }
                for point in result.points
            ]
            for offset in range(0, len(rows), 10_000):
                session.execute(insert(FactorValue), rows[offset : offset + 10_000])
            session.add(
                FactorDataset(
                    data_version_id=data_version_id,
                    cleaning_version_id=cleaning_version_id,
                    factor_version_id=factor_version_id,
                    content_hash=result.content_hash,
                    common_valid_start=result.common_valid_start,
                    coverage_end=result.coverage_end,
                    row_count=len(rows),
                    status="published",
                )
            )
