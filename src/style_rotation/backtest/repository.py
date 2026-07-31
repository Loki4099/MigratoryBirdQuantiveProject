from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from psycopg import Connection as PsycopgConnection
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from style_rotation.backtest.types import (
    BacktestResult,
    ExecutionTarget,
    RunInputSpec,
)
from style_rotation.contracts.spec import DataContractSpec
from style_rotation.core.canonical import canonicalize
from style_rotation.data.types import CleanMarketPriceRecord, ReserveDailyRecord
from style_rotation.domain.enums import RebalanceFrequency, StrategyTemplate
from style_rotation.persistence.models import (
    Asset,
    BacktestRun,
    CleanDataset,
    CleanMarketPrice,
    DataContract,
    EngineVersion,
    Experiment,
    FactorVariant,
    RebalanceEvent,
    ReserveDailyReturn,
    RunEvent,
    SignalDataset,
    TargetPosition,
)


class BacktestRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def publish_contracts(self, contracts: Iterable[DataContractSpec]) -> None:
        with self._session_factory.begin() as session:
            for contract in contracts:
                existing = session.scalar(
                    select(DataContract).where(
                        DataContract.layer == contract.layer.value,
                        DataContract.name == contract.name,
                        DataContract.schema_version == contract.schema_version,
                    )
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
                elif existing.contract_hash != contract.contract_hash:
                    raise ValueError(
                        "Contract identity exists with different content; increment schema_version"
                    )

    def latest_signal_dataset_ids(
        self,
    ) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
        with self._session_factory() as session:
            dataset = session.scalar(
                select(SignalDataset)
                .where(SignalDataset.status == "published")
                .order_by(SignalDataset.created_at.desc())
                .limit(1)
            )
            if dataset is None:
                raise LookupError("No published signal dataset is available")
            return (
                dataset.data_version_id,
                dataset.cleaning_version_id,
                dataset.factor_version_id,
                dataset.strategy_version_id,
            )

    def load_inputs(
        self,
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        factor_version_id: uuid.UUID,
        strategy_version_id: uuid.UUID,
    ) -> tuple[
        tuple[CleanMarketPriceRecord, ...],
        tuple[ReserveDailyRecord, ...],
        tuple[RunInputSpec, ...],
        date,
        date,
    ]:
        with self._session_factory() as session:
            signal_dataset = session.get(
                SignalDataset,
                {
                    "data_version_id": data_version_id,
                    "cleaning_version_id": cleaning_version_id,
                    "factor_version_id": factor_version_id,
                    "strategy_version_id": strategy_version_id,
                },
            )
            clean_dataset = session.get(
                CleanDataset,
                {
                    "data_version_id": data_version_id,
                    "cleaning_version_id": cleaning_version_id,
                },
            )
            if signal_dataset is None or clean_dataset is None:
                raise LookupError("Published signal or clean dataset is missing")
            price_rows = session.execute(
                select(CleanMarketPrice, Asset.symbol)
                .join(Asset, Asset.asset_id == CleanMarketPrice.asset_id)
                .where(
                    CleanMarketPrice.data_version_id == data_version_id,
                    CleanMarketPrice.cleaning_version_id == cleaning_version_id,
                    Asset.symbol.in_(("IWF", "IWD", "IWO", "IWN", "SPY")),
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
            reserve_returns = tuple(
                ReserveDailyRecord(
                    nav_date=row.nav_date,
                    series_id=row.series_id,
                    source_observation_date=row.source_observation_date,
                    source_available_date=row.source_available_date,
                    annual_rate_percent=row.annual_rate_percent,
                    calendar_daily_factor=row.calendar_daily_factor,
                )
                for row in session.scalars(
                    select(ReserveDailyReturn)
                    .where(
                        ReserveDailyReturn.data_version_id == data_version_id,
                        ReserveDailyReturn.cleaning_version_id == cleaning_version_id,
                    )
                    .order_by(ReserveDailyReturn.nav_date)
                )
            )
            event_rows = session.execute(
                select(RebalanceEvent, FactorVariant.variant_key)
                .join(
                    FactorVariant,
                    FactorVariant.factor_variant_id == RebalanceEvent.factor_variant_id,
                )
                .where(
                    RebalanceEvent.data_version_id == data_version_id,
                    RebalanceEvent.cleaning_version_id == cleaning_version_id,
                    RebalanceEvent.factor_version_id == factor_version_id,
                    RebalanceEvent.strategy_version_id == strategy_version_id,
                )
                .order_by(
                    FactorVariant.variant_key,
                    RebalanceEvent.rebalance_frequency,
                    RebalanceEvent.strategy_template,
                    RebalanceEvent.execution_date,
                )
            ).all()
            position_rows = session.execute(
                select(TargetPosition, Asset.symbol)
                .join(Asset, Asset.asset_id == TargetPosition.asset_id)
                .join(
                    RebalanceEvent,
                    RebalanceEvent.rebalance_event_id == TargetPosition.rebalance_event_id,
                )
                .where(
                    RebalanceEvent.data_version_id == data_version_id,
                    RebalanceEvent.cleaning_version_id == cleaning_version_id,
                    RebalanceEvent.factor_version_id == factor_version_id,
                    RebalanceEvent.strategy_version_id == strategy_version_id,
                )
            ).all()
            weights_by_event: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(dict)
            for position, symbol in position_rows:
                weights_by_event[position.rebalance_event_id][symbol] = position.target_weight
            grouped: dict[tuple[str, str, str], list[ExecutionTarget]] = defaultdict(list)
            for event, variant_key in event_rows:
                grouped[(variant_key, event.rebalance_frequency, event.strategy_template)].append(
                    ExecutionTarget(
                        event.signal_date,
                        event.execution_date,
                        weights_by_event[event.rebalance_event_id],
                        event.reserve_target_weight,
                    )
                )
            specs = tuple(
                RunInputSpec(
                    variant_key,
                    RebalanceFrequency(frequency),
                    StrategyTemplate(template),
                    tuple(targets),
                )
                for (variant_key, frequency, template), targets in sorted(grouped.items())
            )
            return (
                prices,
                reserve_returns,
                specs,
                clean_dataset.coverage_start,
                clean_dataset.coverage_end,
            )

    def ensure_engine_version(
        self,
        *,
        version_key: str,
        git_commit: str,
        dependency_lock_hash: str,
        code_hash: str,
        python_version: str,
    ) -> uuid.UUID:
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(EngineVersion).where(EngineVersion.version_key == version_key)
            )
            if existing is not None:
                expected = (git_commit, dependency_lock_hash, code_hash, python_version)
                actual = (
                    existing.git_commit,
                    existing.dependency_lock_hash,
                    existing.code_hash,
                    existing.python_version,
                )
                if actual != expected:
                    raise ValueError("Engine version key exists with different metadata")
                return existing.engine_version_id
            version = EngineVersion(
                version_key=version_key,
                git_commit=git_commit,
                dependency_lock_hash=dependency_lock_hash,
                code_hash=code_hash,
                python_version=python_version,
            )
            session.add(version)
            session.flush()
            return version.engine_version_id

    def ensure_experiment(self, name: str, system_version: str) -> uuid.UUID:
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(Experiment).where(
                    Experiment.name == name,
                    Experiment.system_version == system_version,
                )
            )
            if existing is not None:
                existing.status = "running"
                return existing.experiment_id
            experiment = Experiment(
                name=name,
                system_version=system_version,
                status="running",
                description="Formal v0.1 single-factor backtest batch",
            )
            session.add(experiment)
            session.flush()
            return experiment.experiment_id

    def completed_run_id(self, fingerprint: str) -> uuid.UUID | None:
        with self._session_factory() as session:
            run = session.scalar(
                select(BacktestRun).where(
                    BacktestRun.run_fingerprint == fingerprint,
                    BacktestRun.status == "completed",
                )
            )
            return None if run is None else run.run_id

    def publish_run(
        self,
        *,
        run_fields: dict[str, Any],
        result: BacktestResult,
        equal_weight_benchmark: BacktestResult,
        spy_benchmark: BacktestResult,
    ) -> uuid.UUID:
        run_id = uuid.uuid4()
        with self._session_factory.begin() as session:
            run = BacktestRun(
                run_id=run_id, status="running", started_at=datetime.now(UTC), **run_fields
            )
            session.add(run)
            session.flush()
            driver_connection = session.connection().connection.driver_connection
            if not isinstance(driver_connection, PsycopgConnection):
                raise TypeError("Backtest publication requires psycopg")
            assets = {asset.symbol: asset.asset_id for asset in session.scalars(select(Asset))}
            self._copy_result(driver_connection, run_id, result, assets)
            self._copy_benchmark(
                driver_connection, run_id, "four_etf_equal_weight", equal_weight_benchmark
            )
            self._copy_benchmark(driver_connection, run_id, "spy_buy_hold", spy_benchmark)
            session.add(
                RunEvent(
                    run_id=run_id,
                    sequence_no=0,
                    status="completed",
                    message="Backtest and benchmarks published atomically",
                    details={},
                )
            )
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
        return run_id

    @staticmethod
    def _copy_result(
        connection: PsycopgConnection[Any],
        run_id: uuid.UUID,
        result: BacktestResult,
        assets: dict[str, uuid.UUID],
    ) -> None:
        with connection.cursor().copy(
            "COPY daily_nav (run_id,nav_date,gross_daily_return,net_daily_return,"
            "gross_nav,net_nav,turnover,transaction_cost_fraction,transaction_cost_amount) "
            "FROM STDIN"
        ) as copy:
            for nav_row in result.daily_nav:
                copy.write_row(
                    (
                        run_id,
                        nav_row.nav_date,
                        nav_row.gross_daily_return,
                        nav_row.net_daily_return,
                        nav_row.gross_nav,
                        nav_row.net_nav,
                        nav_row.turnover,
                        nav_row.transaction_cost_fraction,
                        nav_row.transaction_cost_amount,
                    )
                )
        with connection.cursor().copy(
            "COPY daily_positions (run_id,nav_date,sleeve,asset_id,close_weight) FROM STDIN"
        ) as copy:
            for position_row in result.daily_positions:
                copy.write_row(
                    (
                        run_id,
                        position_row.nav_date,
                        position_row.sleeve,
                        assets.get(position_row.sleeve),
                        position_row.close_weight,
                    )
                )
        with connection.cursor().copy(
            "COPY rebalance_executions (run_id,execution_date,signal_date,turnover,"
            "transaction_cost_fraction,transaction_cost_amount,gross_pretrade_nav,"
            "net_pretrade_nav) FROM STDIN"
        ) as copy:
            for execution_row in result.executions:
                copy.write_row(
                    (
                        run_id,
                        execution_row.execution_date,
                        execution_row.signal_date,
                        execution_row.turnover,
                        execution_row.transaction_cost_fraction,
                        execution_row.transaction_cost_amount,
                        execution_row.gross_pretrade_nav,
                        execution_row.net_pretrade_nav,
                    )
                )
        with connection.cursor().copy(
            "COPY trades (trade_id,run_id,execution_date,asset_id,side,execution_price,"
            "pretrade_weight,target_weight,weight_change) FROM STDIN"
        ) as copy:
            for trade_row in result.trades:
                trade_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{run_id}|{trade_row.execution_date}|{trade_row.symbol}",
                )
                copy.write_row(
                    (
                        trade_id,
                        run_id,
                        trade_row.execution_date,
                        assets[trade_row.symbol],
                        trade_row.side,
                        trade_row.execution_price,
                        trade_row.pretrade_weight,
                        trade_row.target_weight,
                        trade_row.weight_change,
                    )
                )

    @staticmethod
    def _copy_benchmark(
        connection: PsycopgConnection[Any],
        run_id: uuid.UUID,
        benchmark_type: str,
        result: BacktestResult,
    ) -> None:
        with connection.cursor().copy(
            "COPY benchmark_daily_nav (run_id,nav_date,benchmark_type,gross_daily_return,"
            "net_daily_return,gross_nav,net_nav,turnover,transaction_cost_fraction) FROM STDIN"
        ) as copy:
            for benchmark_row in result.daily_nav:
                copy.write_row(
                    (
                        run_id,
                        benchmark_row.nav_date,
                        benchmark_type,
                        benchmark_row.gross_daily_return,
                        benchmark_row.net_daily_return,
                        benchmark_row.gross_nav,
                        benchmark_row.net_nav,
                        benchmark_row.turnover,
                        benchmark_row.transaction_cost_fraction,
                    )
                )

    def complete_experiment(self, experiment_id: uuid.UUID) -> None:
        with self._session_factory.begin() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment is None:
                raise LookupError("Experiment not found")
            experiment.status = "completed"
