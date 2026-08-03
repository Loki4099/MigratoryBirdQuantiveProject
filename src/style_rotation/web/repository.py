from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from style_rotation.persistence.models import (
    BacktestRun,
    BenchmarkDailyNav,
    DailyNav,
    FactorDefinition,
    FactorDiagnosticPeriod,
    FactorDiagnosticSet,
    FactorVariant,
    MetricPublication,
    MetricVersion,
    PerformanceMetric,
)

EXPECTED_PUBLICATIONS = 288
EXPECTED_DIAGNOSTIC_SETS = 48
EXPECTED_DIAGNOSTIC_PERIODS = 38_448
EXPECTED_METRICS = 14_688

CORE_METRICS = {
    "cagr",
    "annualized_volatility",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
    "calmar_ratio",
    "tracking_error",
    "information_ratio",
    "annualized_turnover",
    "cumulative_transaction_cost",
    "average_reserve_weight",
}


class ResearchRepository:
    """Query adapter for the latest complete formal metric publication."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @contextmanager
    def _read_session(self) -> Iterator[Session]:
        with self._session_factory() as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            yield session

    def _latest_metric_version(self, session: Session) -> MetricVersion | None:
        statement = (
            select(MetricVersion)
            .join(MetricPublication)
            .where(MetricPublication.status == "published")
            .group_by(MetricVersion.metric_version_id)
            .having(func.count(MetricPublication.metric_publication_id) == EXPECTED_PUBLICATIONS)
            .order_by(MetricVersion.created_at.desc())
            .limit(1)
        )
        return session.scalar(statement)

    @staticmethod
    def _metric_dict(metric: PerformanceMetric) -> dict[str, Any]:
        return {
            "value": metric.metric_value,
            "status": metric.value_status,
            "reason": metric.reason_code,
            "observations": metric.observation_count,
            "unit": metric.unit,
        }

    @staticmethod
    def _metric_label(metric: PerformanceMetric) -> str:
        metric_key = (
            "maximum_drawdown" if metric.metric_key == "max_drawdown" else metric.metric_key
        )
        return f"{metric.series_type}.{metric.return_basis}.{metric_key}"

    def _context(self, session: Session, version: MetricVersion) -> dict[str, Any]:
        rows = (
            session.execute(
                select(BacktestRun)
                .join(MetricPublication, MetricPublication.run_id == BacktestRun.run_id)
                .where(
                    MetricPublication.metric_version_id == version.metric_version_id,
                    MetricPublication.status == "published",
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            raise RuntimeError("Formal metric version has no published runs")
        first = rows[0]
        return {
            "metric_version_id": version.metric_version_id,
            "metric_version": version.version_key,
            "git_commit": version.git_commit,
            "experiment_id": first.experiment_id,
            "data_version_id": first.data_version_id,
            "cleaning_version_id": first.cleaning_version_id,
            "factor_version_id": first.factor_version_id,
            "strategy_version_id": first.strategy_version_id,
            "engine_version_id": first.engine_version_id,
            "period_start": min(run.first_execution_date for run in rows),
            "period_end": max(run.official_end_date for run in rows),
            "publication_count": len(rows),
        }

    def _version_or_raise(self, session: Session) -> MetricVersion:
        version = self._latest_metric_version(session)
        if version is None:
            raise LookupError("No complete formal metric publication is available")
        return version

    def options(self) -> dict[str, Any]:
        with self._read_session() as session:
            version = self._version_or_raise(session)
            context = self._context(session, version)
            factors = session.execute(
                select(FactorVariant.factor_variant_id, FactorVariant.variant_key)
                .where(FactorVariant.factor_version_id == context["factor_version_id"])
                .order_by(FactorVariant.variant_key)
            ).all()
            return {
                "context": context,
                "frequencies": ["weekly", "monthly"],
                "templates": ["cross_sectional", "trend_filtered"],
                "cost_bps": [Decimal("0"), Decimal("5"), Decimal("10")],
                "sort_metrics": [
                    "strategy.net.sharpe_ratio",
                    "strategy.net.cagr",
                    "strategy.net.maximum_drawdown",
                    "diagnostic.mean_rank_ic",
                    "diagnostic.mean_top_bottom_return_spread",
                ],
                "factors": [
                    {"factor_variant_id": factor_id, "variant_key": key}
                    for factor_id, key in factors
                ],
            }

    def status(self) -> dict[str, Any]:
        with self._read_session() as session:
            version = self._version_or_raise(session)
            metric_version_id = version.metric_version_id
            publication_count = (
                session.scalar(
                    select(func.count())
                    .select_from(MetricPublication)
                    .where(
                        MetricPublication.metric_version_id == metric_version_id,
                        MetricPublication.status == "published",
                    )
                )
                or 0
            )
            publishing_count = (
                session.scalar(
                    select(func.count())
                    .select_from(MetricPublication)
                    .where(
                        MetricPublication.metric_version_id == metric_version_id,
                        MetricPublication.status != "published",
                    )
                )
                or 0
            )
            diagnostic_count = (
                session.scalar(
                    select(func.count())
                    .select_from(FactorDiagnosticSet)
                    .where(
                        FactorDiagnosticSet.metric_version_id == metric_version_id,
                        FactorDiagnosticSet.status == "published",
                    )
                )
                or 0
            )
            period_count = (
                session.scalar(
                    select(func.count())
                    .select_from(FactorDiagnosticPeriod)
                    .join(FactorDiagnosticSet)
                    .where(FactorDiagnosticSet.metric_version_id == metric_version_id)
                )
                or 0
            )
            metric_count = (
                session.scalar(
                    select(func.count())
                    .select_from(PerformanceMetric)
                    .join(MetricPublication)
                    .where(MetricPublication.metric_version_id == metric_version_id)
                )
                or 0
            )
            completed_runs = (
                session.scalar(
                    select(func.count())
                    .select_from(BacktestRun)
                    .join(MetricPublication)
                    .where(
                        MetricPublication.metric_version_id == metric_version_id,
                        BacktestRun.status == "completed",
                    )
                )
                or 0
            )
            checks = {
                "publications": publication_count == EXPECTED_PUBLICATIONS,
                "diagnostic_sets": diagnostic_count == EXPECTED_DIAGNOSTIC_SETS,
                "diagnostic_periods": period_count == EXPECTED_DIAGNOSTIC_PERIODS,
                "metrics": metric_count == EXPECTED_METRICS,
                "no_incomplete_publication": publishing_count == 0,
                "completed_runs": completed_runs == EXPECTED_PUBLICATIONS,
            }
            return {
                "state": "healthy" if all(checks.values()) else "attention",
                "checks": checks,
                "counts": {
                    "publications": publication_count,
                    "diagnostic_sets": diagnostic_count,
                    "diagnostic_periods": period_count,
                    "metrics": metric_count,
                    "completed_runs": completed_runs,
                },
                "context": self._context(session, version),
            }

    def leaderboard(
        self,
        *,
        frequency: str = "weekly",
        strategy_template: str = "cross_sectional",
        cost_bps: Decimal = Decimal("5"),
        sort_metric: str = "strategy.net.sharpe_ratio",
        descending: bool = True,
    ) -> dict[str, Any]:
        with self._read_session() as session:
            version = self._version_or_raise(session)
            run_rows = session.execute(
                select(BacktestRun, FactorVariant, FactorDefinition, MetricPublication)
                .join(MetricPublication, MetricPublication.run_id == BacktestRun.run_id)
                .join(
                    FactorVariant,
                    (FactorVariant.factor_version_id == BacktestRun.factor_version_id)
                    & (FactorVariant.variant_key == BacktestRun.factor_variant_key),
                )
                .join(
                    FactorDefinition,
                    FactorDefinition.factor_definition_id == FactorVariant.factor_definition_id,
                )
                .where(
                    MetricPublication.metric_version_id == version.metric_version_id,
                    MetricPublication.status == "published",
                    BacktestRun.rebalance_frequency == frequency,
                    BacktestRun.strategy_template == strategy_template,
                    BacktestRun.transaction_cost_bps == cost_bps,
                )
            ).all()
            publication_ids = [
                publication.metric_publication_id for _, _, _, publication in run_rows
            ]
            metrics_by_publication: dict[uuid.UUID, dict[str, Any]] = defaultdict(dict)
            if publication_ids:
                metrics = session.scalars(
                    select(PerformanceMetric).where(
                        PerformanceMetric.metric_publication_id.in_(publication_ids),
                        PerformanceMetric.metric_key.in_(CORE_METRICS),
                    )
                ).all()
                for metric in metrics:
                    metrics_by_publication[metric.metric_publication_id][
                        self._metric_label(metric)
                    ] = self._metric_dict(metric)
            diagnostic_ids = {publication.diagnostic_set_id for _, _, _, publication in run_rows}
            diagnostics = (
                {
                    item.diagnostic_set_id: item
                    for item in session.scalars(
                        select(FactorDiagnosticSet).where(
                            FactorDiagnosticSet.diagnostic_set_id.in_(diagnostic_ids)
                        )
                    ).all()
                }
                if diagnostic_ids
                else {}
            )
            items: list[dict[str, Any]] = []
            for run, variant, definition, publication in run_rows:
                diagnostic = diagnostics[publication.diagnostic_set_id]
                item = {
                    "run_id": run.run_id,
                    "factor_variant_id": variant.factor_variant_id,
                    "variant_key": variant.variant_key,
                    "factor_name": definition.name,
                    "family": definition.family,
                    "direction": definition.direction,
                    "parameters": variant.parameters,
                    "frequency": run.rebalance_frequency,
                    "strategy_template": run.strategy_template,
                    "transaction_cost_bps": run.transaction_cost_bps,
                    "first_execution_date": run.first_execution_date,
                    "official_end_date": run.official_end_date,
                    "metrics": metrics_by_publication[publication.metric_publication_id],
                    "diagnostic": {
                        "mean_rank_ic": diagnostic.mean_rank_ic,
                        "positive_ic_ratio": diagnostic.positive_ic_ratio,
                        "mean_top_bottom_return_spread": diagnostic.mean_top_bottom_return_spread,
                        "period_count": diagnostic.period_count,
                        "undefined_ic_count": diagnostic.undefined_ic_count,
                    },
                }
                items.append(item)

            def sort_value(item: dict[str, Any]) -> tuple[bool, Decimal]:
                if sort_metric.startswith("diagnostic."):
                    value = item["diagnostic"].get(sort_metric.removeprefix("diagnostic."))
                else:
                    value = item["metrics"].get(sort_metric, {}).get("value")
                return value is not None, value if value is not None else Decimal("0")

            items.sort(key=sort_value, reverse=descending)
            return {"context": self._context(session, version), "items": items}

    def factor_detail(self, factor_variant_id: uuid.UUID) -> dict[str, Any]:
        with self._read_session() as session:
            version = self._version_or_raise(session)
            metadata = session.execute(
                select(FactorVariant, FactorDefinition)
                .join(
                    FactorDefinition,
                    FactorDefinition.factor_definition_id == FactorVariant.factor_definition_id,
                )
                .where(FactorVariant.factor_variant_id == factor_variant_id)
            ).one_or_none()
            if metadata is None:
                raise LookupError("Factor variant not found")
            variant, definition = metadata
            diagnostics = session.scalars(
                select(FactorDiagnosticSet)
                .where(
                    FactorDiagnosticSet.metric_version_id == version.metric_version_id,
                    FactorDiagnosticSet.factor_variant_id == factor_variant_id,
                    FactorDiagnosticSet.status == "published",
                )
                .order_by(FactorDiagnosticSet.rebalance_frequency)
            ).all()
            diagnostic_items: list[dict[str, Any]] = []
            for diagnostic in diagnostics:
                periods = session.scalars(
                    select(FactorDiagnosticPeriod)
                    .where(FactorDiagnosticPeriod.diagnostic_set_id == diagnostic.diagnostic_set_id)
                    .order_by(FactorDiagnosticPeriod.signal_date)
                ).all()
                diagnostic_items.append(
                    {
                        "frequency": diagnostic.rebalance_frequency,
                        "period_count": diagnostic.period_count,
                        "valid_ic_count": diagnostic.valid_ic_count,
                        "undefined_ic_count": diagnostic.undefined_ic_count,
                        "mean_rank_ic": diagnostic.mean_rank_ic,
                        "positive_ic_ratio": diagnostic.positive_ic_ratio,
                        "mean_top_bottom_return_spread": diagnostic.mean_top_bottom_return_spread,
                        "periods": [
                            {
                                "signal_date": period.signal_date,
                                "execution_date": period.execution_date,
                                "next_execution_date": period.next_execution_date,
                                "rank_ic": period.rank_ic,
                                "rank_ic_reason": period.rank_ic_reason_code,
                                "top_bottom_return_spread": period.top_bottom_return_spread,
                            }
                            for period in periods
                        ],
                    }
                )
            run_rows = session.execute(
                select(BacktestRun, MetricPublication)
                .join(MetricPublication, MetricPublication.run_id == BacktestRun.run_id)
                .where(
                    MetricPublication.metric_version_id == version.metric_version_id,
                    BacktestRun.factor_version_id == variant.factor_version_id,
                    BacktestRun.factor_variant_key == variant.variant_key,
                    MetricPublication.status == "published",
                )
                .order_by(
                    BacktestRun.rebalance_frequency,
                    BacktestRun.strategy_template,
                    BacktestRun.transaction_cost_bps,
                )
            ).all()
            publication_ids = [publication.metric_publication_id for _, publication in run_rows]
            metric_map: dict[uuid.UUID, dict[str, Any]] = defaultdict(dict)
            for metric in session.scalars(
                select(PerformanceMetric).where(
                    PerformanceMetric.metric_publication_id.in_(publication_ids),
                    PerformanceMetric.metric_key.in_(CORE_METRICS),
                )
            ).all():
                metric_map[metric.metric_publication_id][self._metric_label(metric)] = (
                    self._metric_dict(metric)
                )
            return {
                "context": self._context(session, version),
                "factor": {
                    "factor_variant_id": variant.factor_variant_id,
                    "variant_key": variant.variant_key,
                    "factor_name": definition.name,
                    "family": definition.family,
                    "description": definition.description,
                    "formula": definition.formula,
                    "direction": definition.direction,
                    "required_fields": definition.required_fields,
                    "parameters": variant.parameters,
                    "minimum_observations": variant.minimum_observations,
                },
                "diagnostics": diagnostic_items,
                "runs": [
                    {
                        "run_id": run.run_id,
                        "frequency": run.rebalance_frequency,
                        "strategy_template": run.strategy_template,
                        "transaction_cost_bps": run.transaction_cost_bps,
                        "metrics": metric_map[publication.metric_publication_id],
                    }
                    for run, publication in run_rows
                ],
            }

    @staticmethod
    def _downsample(points: Sequence[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
        if len(points) <= max_points:
            return list(points)
        indices = {
            round(index * (len(points) - 1) / (max_points - 1)) for index in range(max_points)
        }
        return [points[index] for index in sorted(indices)]

    def compare(self, run_ids: Sequence[uuid.UUID], *, max_points: int = 600) -> dict[str, Any]:
        if not 1 <= len(run_ids) <= 4:
            raise ValueError("Select between one and four runs")
        with self._read_session() as session:
            version = self._version_or_raise(session)
            rows = session.execute(
                select(BacktestRun, FactorVariant, FactorDefinition, MetricPublication)
                .join(MetricPublication, MetricPublication.run_id == BacktestRun.run_id)
                .join(
                    FactorVariant,
                    (FactorVariant.factor_version_id == BacktestRun.factor_version_id)
                    & (FactorVariant.variant_key == BacktestRun.factor_variant_key),
                )
                .join(
                    FactorDefinition,
                    FactorDefinition.factor_definition_id == FactorVariant.factor_definition_id,
                )
                .where(
                    BacktestRun.run_id.in_(run_ids),
                    MetricPublication.metric_version_id == version.metric_version_id,
                    MetricPublication.status == "published",
                )
            ).all()
            if len(rows) != len(set(run_ids)):
                raise LookupError("One or more runs are not in the latest formal publication")
            publication_ids = [publication.metric_publication_id for _, _, _, publication in rows]
            metric_map: dict[uuid.UUID, dict[str, Any]] = defaultdict(dict)
            for metric in session.scalars(
                select(PerformanceMetric).where(
                    PerformanceMetric.metric_publication_id.in_(publication_ids),
                    PerformanceMetric.metric_key.in_(CORE_METRICS),
                )
            ).all():
                metric_map[metric.metric_publication_id][self._metric_label(metric)] = (
                    self._metric_dict(metric)
                )
            items: list[dict[str, Any]] = []
            for run, variant, definition, publication in rows:
                nav = session.scalars(
                    select(DailyNav)
                    .where(DailyNav.run_id == run.run_id)
                    .order_by(DailyNav.nav_date)
                ).all()
                benchmark = session.scalars(
                    select(BenchmarkDailyNav)
                    .where(
                        BenchmarkDailyNav.run_id == run.run_id,
                        BenchmarkDailyNav.benchmark_type == "spy_buy_hold",
                    )
                    .order_by(BenchmarkDailyNav.nav_date)
                ).all()
                points = [
                    {
                        "date": item.nav_date,
                        "net_nav": item.net_nav,
                        "gross_nav": item.gross_nav,
                    }
                    for item in nav
                ]
                benchmark_points = [
                    {"date": item.nav_date, "net_nav": item.net_nav} for item in benchmark
                ]
                items.append(
                    {
                        "run_id": run.run_id,
                        "factor_variant_id": variant.factor_variant_id,
                        "variant_key": variant.variant_key,
                        "factor_name": definition.name,
                        "frequency": run.rebalance_frequency,
                        "strategy_template": run.strategy_template,
                        "transaction_cost_bps": run.transaction_cost_bps,
                        "first_execution_date": run.first_execution_date,
                        "official_end_date": run.official_end_date,
                        "metrics": metric_map[publication.metric_publication_id],
                        "nav": self._downsample(points, max_points),
                        "spy": self._downsample(benchmark_points, max_points),
                    }
                )
            items.sort(key=lambda item: run_ids.index(item["run_id"]))
            return {"context": self._context(session, version), "items": items}
