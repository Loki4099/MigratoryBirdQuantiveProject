from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any

from psycopg import Connection as PsycopgConnection
from sqlalchemy import and_, distinct, func, select
from sqlalchemy.orm import Session, sessionmaker

from style_rotation.contracts.spec import DataContractSpec
from style_rotation.core.canonical import canonicalize, sha256_hexdigest
from style_rotation.metrics.calculator import build_risk_free_returns
from style_rotation.metrics.types import (
    DiagnosticEventInput,
    FactorDiagnosticPeriod,
    FactorDiagnosticSummary,
    OpenPrice,
    PerformanceMetricResult,
    RunMetricInput,
    SeriesPoint,
    SourceRunDescriptor,
    SourceRunSet,
)
from style_rotation.persistence.models import (
    Asset,
    BacktestRun,
    BenchmarkDailyNav,
    CleanDataset,
    CleanMarketPrice,
    DailyNav,
    DailyPosition,
    DataContract,
    EngineVersion,
    FactorDiagnosticSet,
    FactorVariant,
    MetricPublication,
    MetricVersion,
    PerformanceMetric,
    RebalanceEvent,
    ReserveDailyReturn,
    SignalDataset,
    TargetPosition,
)

FORMAL_RUN_COUNT = 288
FORMAL_VARIANT_COUNT = 24
FORMAL_FREQUENCIES = frozenset({"weekly", "monthly"})
FORMAL_TEMPLATES = frozenset({"cross_sectional", "trend_filtered"})
FORMAL_COSTS = frozenset({Decimal(2), Decimal(5), Decimal(10)})
FORMAL_RUN_CONFIGURATION: dict[str, object] = {
    "cost_model": "single_sided_turnover",
    "initial_build_charged": True,
    "terminal_liquidation": False,
    "reserve_accrual": "prior_known_rate_calendar_days",
    "benchmarks": ["four_etf_equal_weight", "spy_buy_hold"],
}


def validate_formal_run_matrix(runs: tuple[SourceRunDescriptor, ...]) -> None:
    """Reject a source group unless it is the exact frozen v0.1 run matrix."""
    if len(runs) != FORMAL_RUN_COUNT:
        raise LookupError(
            f"Formal source matrix requires {FORMAL_RUN_COUNT} runs, got {len(runs)}"
        )

    variant_keys = {run.factor_variant_key for run in runs}
    if len(variant_keys) != FORMAL_VARIANT_COUNT:
        raise LookupError(
            f"Formal source matrix requires {FORMAL_VARIANT_COUNT} variants, "
            f"got {len(variant_keys)}"
        )
    expected_cells = {
        (variant_key, frequency, template, cost)
        for variant_key in variant_keys
        for frequency in FORMAL_FREQUENCIES
        for template in FORMAL_TEMPLATES
        for cost in FORMAL_COSTS
    }
    actual_cells = [
        (
            run.factor_variant_key,
            run.rebalance_frequency,
            run.strategy_template,
            run.transaction_cost_bps,
        )
        for run in runs
    ]
    actual_cell_set = set(actual_cells)
    if len(actual_cells) != len(actual_cell_set):
        raise LookupError("Formal source matrix contains duplicate parameter cells")
    if actual_cell_set != expected_cells:
        missing_count = len(expected_cells - actual_cell_set)
        unexpected_count = len(actual_cell_set - expected_cells)
        raise LookupError(
            "Formal source matrix is not the exact 24 x 2 x 2 x 3 Cartesian product: "
            f"missing={missing_count}, unexpected={unexpected_count}"
        )

    if len({run.run_fingerprint for run in runs}) != FORMAL_RUN_COUNT:
        raise LookupError("Formal source matrix contains duplicate run fingerprints")
    if len({run.official_end_date for run in runs}) != 1:
        raise LookupError("Formal source runs do not share one official end date")
    for frequency in FORMAL_FREQUENCIES:
        signal_dates = {
            run.official_signal_start_date
            for run in runs
            if run.rebalance_frequency == frequency
        }
        if len(signal_dates) != 1:
            raise LookupError(
                f"Formal {frequency} runs do not share one official signal start date"
            )
        execution_dates = {
            run.first_execution_date
            for run in runs
            if run.rebalance_frequency == frequency
        }
        if len(execution_dates) != 1:
            raise LookupError(
                f"Formal {frequency} runs do not share one first execution date"
            )
    for run in runs:
        for key, expected_value in FORMAL_RUN_CONFIGURATION.items():
            if run.configuration.get(key) != expected_value:
                raise LookupError(
                    f"Run {run.run_id} has an invalid frozen configuration value for {key}"
                )


class MetricsRepository:
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

    def ensure_metric_version(
        self,
        *,
        version_key: str,
        methodology_hash: str,
        code_hash: str,
        dependency_lock_hash: str,
        git_commit: str,
        python_version: str,
        configuration: dict[str, Any],
    ) -> uuid.UUID:
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(MetricVersion).where(MetricVersion.version_key == version_key)
            )
            expected = (
                methodology_hash,
                code_hash,
                dependency_lock_hash,
                git_commit,
                python_version,
                canonicalize(configuration),
            )
            if existing is not None:
                actual = (
                    existing.methodology_hash,
                    existing.code_hash,
                    existing.dependency_lock_hash,
                    existing.git_commit,
                    existing.python_version,
                    existing.configuration,
                )
                if actual != expected:
                    raise ValueError("Metric version key exists with different metadata")
                return existing.metric_version_id
            version = MetricVersion(
                version_key=version_key,
                methodology_hash=methodology_hash,
                code_hash=code_hash,
                dependency_lock_hash=dependency_lock_hash,
                git_commit=git_commit,
                python_version=python_version,
                configuration=canonicalize(configuration),
            )
            session.add(version)
            session.flush()
            return version.metric_version_id

    def select_source_run_set(
        self, source_engine_version_id: uuid.UUID | None = None
    ) -> SourceRunSet:
        with self._session_factory() as session:
            group_statement = (
                select(
                    BacktestRun.experiment_id,
                    BacktestRun.data_version_id,
                    BacktestRun.cleaning_version_id,
                    BacktestRun.factor_version_id,
                    BacktestRun.strategy_version_id,
                    BacktestRun.engine_version_id,
                    func.max(BacktestRun.completed_at).label("latest_completion"),
                )
                .join(
                    EngineVersion,
                    EngineVersion.engine_version_id == BacktestRun.engine_version_id,
                )
                .where(BacktestRun.status == "completed")
                .group_by(
                    BacktestRun.experiment_id,
                    BacktestRun.data_version_id,
                    BacktestRun.cleaning_version_id,
                    BacktestRun.factor_version_id,
                    BacktestRun.strategy_version_id,
                    BacktestRun.engine_version_id,
                )
                .having(
                    func.count(BacktestRun.run_id) == FORMAL_RUN_COUNT,
                    func.count(distinct(BacktestRun.factor_variant_key)) == 24,
                    func.count(distinct(BacktestRun.rebalance_frequency)) == 2,
                    func.count(distinct(BacktestRun.strategy_template)) == 2,
                    func.count(distinct(BacktestRun.transaction_cost_bps)) == 3,
                )
            )
            if source_engine_version_id is None:
                group_statement = group_statement.where(
                    EngineVersion.git_commit.not_like("%-dirty")
                )
            else:
                group_statement = group_statement.where(
                    BacktestRun.engine_version_id == source_engine_version_id
                )
            group = session.execute(
                group_statement.order_by(func.max(BacktestRun.completed_at).desc()).limit(1)
            ).first()
            if group is None:
                raise LookupError("No complete formal 288-run source matrix is available")
            (
                experiment_id,
                data_version_id,
                cleaning_version_id,
                factor_version_id,
                strategy_version_id,
                selected_engine_version_id,
                _latest_completion,
            ) = group
            run_rows = session.execute(
                select(BacktestRun, FactorVariant.factor_variant_id)
                .join(
                    FactorVariant,
                    and_(
                        FactorVariant.factor_version_id == BacktestRun.factor_version_id,
                        FactorVariant.variant_key == BacktestRun.factor_variant_key,
                    ),
                )
                .where(
                    BacktestRun.experiment_id == experiment_id,
                    BacktestRun.data_version_id == data_version_id,
                    BacktestRun.cleaning_version_id == cleaning_version_id,
                    BacktestRun.factor_version_id == factor_version_id,
                    BacktestRun.strategy_version_id == strategy_version_id,
                    BacktestRun.engine_version_id == selected_engine_version_id,
                    BacktestRun.status == "completed",
                )
                .order_by(
                    BacktestRun.factor_variant_key,
                    BacktestRun.rebalance_frequency,
                    BacktestRun.strategy_template,
                    BacktestRun.transaction_cost_bps,
                )
            ).all()
            runs = tuple(
                SourceRunDescriptor(
                    run_id=run.run_id,
                    experiment_id=run.experiment_id,
                    data_version_id=run.data_version_id,
                    cleaning_version_id=run.cleaning_version_id,
                    factor_version_id=run.factor_version_id,
                    strategy_version_id=run.strategy_version_id,
                    source_engine_version_id=run.engine_version_id,
                    factor_variant_id=factor_variant_id,
                    factor_variant_key=run.factor_variant_key,
                    rebalance_frequency=run.rebalance_frequency,
                    strategy_template=run.strategy_template,
                    transaction_cost_bps=run.transaction_cost_bps,
                    official_signal_start_date=run.official_signal_start_date,
                    first_execution_date=run.first_execution_date,
                    official_end_date=run.official_end_date,
                    configuration=run.configuration,
                    run_fingerprint=run.run_fingerprint,
                )
                for run, factor_variant_id in run_rows
            )
            validate_formal_run_matrix(runs)
            return SourceRunSet(
                experiment_id=experiment_id,
                data_version_id=data_version_id,
                cleaning_version_id=cleaning_version_id,
                factor_version_id=factor_version_id,
                strategy_version_id=strategy_version_id,
                source_engine_version_id=selected_engine_version_id,
                runs=runs,
            )

    def load_diagnostic_inputs(
        self, source: SourceRunSet
    ) -> tuple[tuple[DiagnosticEventInput, ...], tuple[OpenPrice, ...], str, str]:
        with self._session_factory() as session:
            signal_dataset = session.get(
                SignalDataset,
                {
                    "data_version_id": source.data_version_id,
                    "cleaning_version_id": source.cleaning_version_id,
                    "factor_version_id": source.factor_version_id,
                    "strategy_version_id": source.strategy_version_id,
                },
            )
            clean_dataset = session.get(
                CleanDataset,
                {
                    "data_version_id": source.data_version_id,
                    "cleaning_version_id": source.cleaning_version_id,
                },
            )
            if signal_dataset is None or clean_dataset is None:
                raise LookupError("Published signal or clean dataset is missing")
            rows = session.execute(
                select(
                    RebalanceEvent,
                    FactorVariant.variant_key,
                    Asset.symbol,
                    TargetPosition.oriented_factor_value,
                    TargetPosition.rank,
                )
                .join(
                    FactorVariant,
                    FactorVariant.factor_variant_id == RebalanceEvent.factor_variant_id,
                )
                .join(
                    TargetPosition,
                    TargetPosition.rebalance_event_id == RebalanceEvent.rebalance_event_id,
                )
                .join(Asset, Asset.asset_id == TargetPosition.asset_id)
                .where(
                    RebalanceEvent.data_version_id == source.data_version_id,
                    RebalanceEvent.cleaning_version_id == source.cleaning_version_id,
                    RebalanceEvent.factor_version_id == source.factor_version_id,
                    RebalanceEvent.strategy_version_id == source.strategy_version_id,
                    RebalanceEvent.strategy_template == "cross_sectional",
                )
                .order_by(
                    FactorVariant.variant_key,
                    RebalanceEvent.rebalance_frequency,
                    RebalanceEvent.execution_date,
                    TargetPosition.rank,
                )
            ).all()
            event_metadata: dict[
                uuid.UUID, tuple[RebalanceEvent, uuid.UUID, str]
            ] = {}
            values: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(dict)
            ranks: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
            for event, variant_key, symbol, oriented_value, rank in rows:
                if rank is None:
                    raise LookupError("Cross-sectional diagnostic rank cannot be null")
                event_metadata[event.rebalance_event_id] = (
                    event,
                    event.factor_variant_id,
                    variant_key,
                )
                values[event.rebalance_event_id][symbol] = oriented_value
                ranks[event.rebalance_event_id][symbol] = rank
            events = tuple(
                DiagnosticEventInput(
                    factor_variant_id=variant_id,
                    variant_key=variant_key,
                    rebalance_frequency=event.rebalance_frequency,
                    signal_date=event.signal_date,
                    execution_date=event.execution_date,
                    oriented_values=values[event_id],
                    deterministic_ranks=ranks[event_id],
                )
                for event_id, (event, variant_id, variant_key) in event_metadata.items()
            )
            price_rows = session.execute(
                select(CleanMarketPrice, Asset.symbol)
                .join(Asset, Asset.asset_id == CleanMarketPrice.asset_id)
                .where(
                    CleanMarketPrice.data_version_id == source.data_version_id,
                    CleanMarketPrice.cleaning_version_id == source.cleaning_version_id,
                    Asset.symbol.in_(("IWF", "IWD", "IWO", "IWN")),
                )
                .order_by(CleanMarketPrice.trade_date, Asset.symbol)
            ).all()
            prices = tuple(
                OpenPrice(symbol, price.trade_date, price.open_adj)
                for price, symbol in price_rows
            )
            return events, prices, signal_dataset.content_hash, clean_dataset.content_hash

    def diagnostic_source_hashes(self, source: SourceRunSet) -> tuple[str, str]:
        with self._session_factory() as session:
            signal_dataset = session.get(
                SignalDataset,
                {
                    "data_version_id": source.data_version_id,
                    "cleaning_version_id": source.cleaning_version_id,
                    "factor_version_id": source.factor_version_id,
                    "strategy_version_id": source.strategy_version_id,
                },
            )
            clean_dataset = session.get(
                CleanDataset,
                {
                    "data_version_id": source.data_version_id,
                    "cleaning_version_id": source.cleaning_version_id,
                },
            )
            if signal_dataset is None or clean_dataset is None:
                raise LookupError("Published signal or clean dataset is missing")
            return signal_dataset.content_hash, clean_dataset.content_hash

    def diagnostic_set_ids(self, fingerprints: Iterable[str]) -> dict[str, uuid.UUID]:
        fingerprint_values = tuple(fingerprints)
        if not fingerprint_values:
            return {}
        with self._session_factory() as session:
            return {
                fingerprint: diagnostic_set_id
                for fingerprint, diagnostic_set_id in session.execute(
                    select(
                        FactorDiagnosticSet.diagnostic_fingerprint,
                        FactorDiagnosticSet.diagnostic_set_id,
                    ).where(
                        FactorDiagnosticSet.diagnostic_fingerprint.in_(fingerprint_values),
                        FactorDiagnosticSet.status == "published",
                    )
                )
            }

    def publish_diagnostic_set(
        self,
        *,
        source: SourceRunSet,
        metric_version_id: uuid.UUID,
        fingerprint: str,
        summary: FactorDiagnosticSummary,
        periods: tuple[FactorDiagnosticPeriod, ...],
    ) -> uuid.UUID:
        diagnostic_set_id = uuid.uuid5(uuid.NAMESPACE_URL, fingerprint)
        content_hash = sha256_hexdigest({"summary": summary, "periods": periods})
        with self._session_factory.begin() as session:
            diagnostic_set = FactorDiagnosticSet(
                diagnostic_set_id=diagnostic_set_id,
                metric_version_id=metric_version_id,
                data_version_id=source.data_version_id,
                cleaning_version_id=source.cleaning_version_id,
                factor_version_id=source.factor_version_id,
                strategy_version_id=source.strategy_version_id,
                factor_variant_id=summary.factor_variant_id,
                rebalance_frequency=summary.rebalance_frequency,
                diagnostic_fingerprint=fingerprint,
                period_count=summary.period_count,
                valid_ic_count=summary.valid_ic_count,
                undefined_ic_count=summary.undefined_ic_count,
                mean_rank_ic=summary.mean_rank_ic,
                positive_ic_ratio=summary.positive_ic_ratio,
                mean_top_bottom_return_spread=summary.mean_top_bottom_return_spread,
                ic_summary_reason_code=summary.ic_summary_reason_code,
                content_hash=content_hash,
                status="publishing",
            )
            session.add(diagnostic_set)
            session.flush()
            driver_connection = session.connection().connection.driver_connection
            if not isinstance(driver_connection, PsycopgConnection):
                raise TypeError("Diagnostic publication requires psycopg")
            with driver_connection.cursor().copy(
                "COPY factor_diagnostic_periods "
                "(diagnostic_set_id,signal_date,execution_date,next_execution_date,rank_ic,"
                "rank_ic_reason_code,top_bottom_return_spread) FROM STDIN"
            ) as copy:
                for period in periods:
                    copy.write_row(
                        (
                            diagnostic_set_id,
                            period.signal_date,
                            period.execution_date,
                            period.next_execution_date,
                            period.rank_ic,
                            period.rank_ic_reason_code,
                            period.top_bottom_return_spread,
                        )
                    )
            diagnostic_set.status = "published"
        return diagnostic_set_id

    def reserve_factors(self, source: SourceRunSet) -> dict[date, Decimal]:
        with self._session_factory() as session:
            return {
                row.nav_date: row.calendar_daily_factor
                for row in session.scalars(
                    select(ReserveDailyReturn)
                    .where(
                        ReserveDailyReturn.data_version_id == source.data_version_id,
                        ReserveDailyReturn.cleaning_version_id == source.cleaning_version_id,
                    )
                    .order_by(ReserveDailyReturn.nav_date)
                )
            }

    def publication_exists(self, run_id: uuid.UUID, metric_version_id: uuid.UUID) -> bool:
        with self._session_factory() as session:
            return (
                session.scalar(
                    select(MetricPublication.metric_publication_id).where(
                        MetricPublication.run_id == run_id,
                        MetricPublication.metric_version_id == metric_version_id,
                        MetricPublication.status == "published",
                    )
                )
                is not None
            )

    def published_run_ids(
        self, run_ids: Iterable[uuid.UUID], metric_version_id: uuid.UUID
    ) -> set[uuid.UUID]:
        run_id_values = tuple(run_ids)
        if not run_id_values:
            return set()
        with self._session_factory() as session:
            return set(
                session.scalars(
                    select(MetricPublication.run_id).where(
                        MetricPublication.run_id.in_(run_id_values),
                        MetricPublication.metric_version_id == metric_version_id,
                        MetricPublication.status == "published",
                    )
                )
            )

    def load_run_input(
        self, descriptor: SourceRunDescriptor, reserve_factors: dict[date, Decimal]
    ) -> RunMetricInput:
        with self._session_factory() as session:
            nav_rows = tuple(
                session.scalars(
                    select(DailyNav)
                    .where(DailyNav.run_id == descriptor.run_id)
                    .order_by(DailyNav.nav_date)
                )
            )
            benchmark_rows = tuple(
                session.scalars(
                    select(BenchmarkDailyNav)
                    .where(BenchmarkDailyNav.run_id == descriptor.run_id)
                    .order_by(BenchmarkDailyNav.benchmark_type, BenchmarkDailyNav.nav_date)
                )
            )
            reserve_rows = tuple(
                session.scalars(
                    select(DailyPosition)
                    .where(
                        DailyPosition.run_id == descriptor.run_id,
                        DailyPosition.sleeve == "RESERVE",
                    )
                    .order_by(DailyPosition.nav_date)
                )
            )
        if not nav_rows:
            raise LookupError("Completed run has no daily NAV rows")
        dates = tuple(row.nav_date for row in nav_rows)
        if dates[0] != descriptor.first_execution_date:
            raise LookupError("Strategy NAV does not start on the declared first execution date")
        if dates[-1] != descriptor.official_end_date:
            raise LookupError("Strategy NAV does not end on the declared official end date")
        benchmark_by_type: dict[str, list[BenchmarkDailyNav]] = defaultdict(list)
        for row in benchmark_rows:
            benchmark_by_type[row.benchmark_type].append(row)
        if set(benchmark_by_type) != {"four_etf_equal_weight", "spy_buy_hold"}:
            raise LookupError("Completed run must contain both formal benchmarks")
        if any(
            tuple(row.nav_date for row in rows) != dates
            for rows in benchmark_by_type.values()
        ):
            raise LookupError("Benchmark dates do not align with strategy NAV")
        if tuple(row.nav_date for row in reserve_rows) != dates:
            raise LookupError("Reserve position dates do not align with strategy NAV")

        strategy_gross = tuple(
            SeriesPoint(row.nav_date, row.gross_daily_return, row.gross_nav) for row in nav_rows
        )
        strategy_net = tuple(
            SeriesPoint(row.nav_date, row.net_daily_return, row.net_nav) for row in nav_rows
        )

        def benchmark_series(
            benchmark_type: str, basis: str
        ) -> tuple[SeriesPoint, ...]:
            rows = benchmark_by_type[benchmark_type]
            if basis == "gross":
                return tuple(
                    SeriesPoint(row.nav_date, row.gross_daily_return, row.gross_nav)
                    for row in rows
                )
            return tuple(
                SeriesPoint(row.nav_date, row.net_daily_return, row.net_nav) for row in rows
            )

        risk_free_returns = build_risk_free_returns(dates, reserve_factors)
        daily_turnover = tuple(row.turnover for row in nav_rows)
        costs = tuple(row.transaction_cost_amount for row in nav_rows)
        reserve_weights = tuple(row.close_weight for row in reserve_rows)
        equal_gross = benchmark_series("four_etf_equal_weight", "gross")
        equal_net = benchmark_series("four_etf_equal_weight", "net")
        spy_gross = benchmark_series("spy_buy_hold", "gross")
        spy_net = benchmark_series("spy_buy_hold", "net")
        input_manifest_hash = sha256_hexdigest(
            {
                "strategy_gross": strategy_gross,
                "strategy_net": strategy_net,
                "equal_weight_gross": equal_gross,
                "equal_weight_net": equal_net,
                "spy_gross": spy_gross,
                "spy_net": spy_net,
                "risk_free_returns": risk_free_returns,
                "daily_turnover": daily_turnover,
                "transaction_cost_amounts": costs,
                "reserve_close_weights": reserve_weights,
            }
        )
        return RunMetricInput(
            run_id=descriptor.run_id,
            factor_variant_id=descriptor.factor_variant_id,
            factor_variant_key=descriptor.factor_variant_key,
            rebalance_frequency=descriptor.rebalance_frequency,
            strategy_template=descriptor.strategy_template,
            transaction_cost_bps=descriptor.transaction_cost_bps,
            first_execution_date=descriptor.first_execution_date,
            official_end_date=descriptor.official_end_date,
            strategy_gross=strategy_gross,
            strategy_net=strategy_net,
            equal_weight_gross=equal_gross,
            equal_weight_net=equal_net,
            spy_gross=spy_gross,
            spy_net=spy_net,
            risk_free_returns=risk_free_returns,
            daily_turnover=daily_turnover,
            transaction_cost_amounts=costs,
            reserve_close_weights=reserve_weights,
            run_fingerprint=descriptor.run_fingerprint,
            input_manifest_hash=input_manifest_hash,
        )

    def publish_run_metrics(
        self,
        *,
        run_id: uuid.UUID,
        metric_version_id: uuid.UUID,
        diagnostic_set_id: uuid.UUID,
        metric_fingerprint: str,
        input_manifest_hash: str,
        metrics: tuple[PerformanceMetricResult, ...],
    ) -> uuid.UUID:
        publication_id = uuid.uuid5(uuid.NAMESPACE_URL, metric_fingerprint)
        content_hash = sha256_hexdigest(metrics)
        with self._session_factory.begin() as session:
            publication = MetricPublication(
                metric_publication_id=publication_id,
                run_id=run_id,
                metric_version_id=metric_version_id,
                diagnostic_set_id=diagnostic_set_id,
                metric_fingerprint=metric_fingerprint,
                input_manifest_hash=input_manifest_hash,
                content_hash=content_hash,
                metric_count=len(metrics),
                status="publishing",
            )
            session.add(publication)
            session.flush()
            session.add_all(
                PerformanceMetric(
                    metric_publication_id=publication_id,
                    series_type=metric.series_type,
                    return_basis=metric.return_basis,
                    metric_key=metric.metric_key,
                    metric_value=metric.value,
                    value_status=metric.value_status,
                    reason_code=metric.reason_code,
                    observation_count=metric.observation_count,
                    unit=metric.unit,
                )
                for metric in metrics
            )
            session.flush()
            publication.status = "published"
        return publication_id
