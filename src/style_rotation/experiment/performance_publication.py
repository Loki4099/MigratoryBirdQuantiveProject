# ruff: noqa: E501
from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from functools import partial
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.experiment.intervals import (
    IntervalTemplateKey,
    resolve_interval,
    slice_carry_in_series,
)
from style_rotation.experiment.performance import (
    IntervalPerformance,
    calculate_absolute_performance,
    calculate_relative_performance,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.metrics.types import MetricValue, SeriesPoint

CATALOG_KEY = "classic_interval_performance"
ABSOLUTE_METRICS = (
    ("cumulative_return", "Cumulative Return", "ratio"),
    ("cagr", "CAGR", "annual_ratio"),
    ("annualized_volatility", "Annualized Volatility", "annual_ratio"),
    ("sharpe_ratio", "Sharpe Ratio", "ratio"),
    ("sortino_ratio", "Sortino Ratio", "ratio"),
    ("maximum_drawdown", "Maximum Drawdown", "ratio"),
    ("maximum_drawdown_duration_days", "Maximum Drawdown Duration", "calendar_days"),
    ("calmar_ratio", "Calmar Ratio", "ratio"),
    ("positive_daily_return_ratio", "Positive Daily Return Ratio", "ratio"),
    ("best_daily_return", "Best Daily Return", "ratio"),
    ("worst_daily_return", "Worst Daily Return", "ratio"),
    ("positive_monthly_return_ratio", "Positive Monthly Return Ratio", "ratio"),
    ("best_monthly_return", "Best Monthly Return", "ratio"),
    ("worst_monthly_return", "Worst Monthly Return", "ratio"),
)
RELATIVE_METRICS = (
    ("cumulative_relative_return", "Cumulative Relative Return", "ratio"),
    ("annualized_relative_wealth_growth", "Annualized Relative Wealth Growth", "annual_ratio"),
    ("cagr_spread", "CAGR Spread", "annual_ratio"),
    ("tracking_error", "Tracking Error", "annual_ratio"),
    ("information_ratio", "Information Ratio", "ratio"),
    ("return_correlation", "Return Correlation", "ratio"),
    ("beta", "Beta", "ratio"),
    ("annualized_alpha", "Annualized Alpha", "annual_ratio"),
)


@dataclass(frozen=True, slots=True)
class MetricCatalogPublication:
    artifact_id: uuid.UUID
    metric_count: int
    reused: bool


@dataclass(frozen=True, slots=True)
class IntervalResultPublication:
    artifact_id: uuid.UUID
    availability_status: str
    exclusion_reason: str | None
    quality_status: str
    observation_count: int
    metric_value_count: int
    reused: bool


@dataclass(frozen=True, slots=True)
class _Context:
    strategy: RowMapping
    benchmark: RowMapping
    catalog: RowMapping
    engine: RowMapping
    strategy_points: tuple[SeriesPoint, ...]
    benchmark_points: tuple[SeriesPoint, ...]
    reserve_returns: dict[date, Decimal]
    reserve_artifact_id: uuid.UUID


def publish_performance_metric_catalog(
    engine: Engine, *, version_number: int = 1
) -> MetricCatalogPublication:
    definitions = tuple(("absolute", *item) for item in ABSOLUTE_METRICS) + tuple(
        ("relative", *item) for item in RELATIVE_METRICS
    )
    semantic = {
        "catalog_key": CATALOG_KEY,
        "version_number": version_number,
        "methodology": "interval-performance-v1",
        "definitions": definitions,
    }
    with engine.begin() as connection:
        result = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
            artifact_type="performance_metric_catalog",
            artifact_key=CATALOG_KEY,
            version_number=version_number,
            semantic_payload=semantic,
            content_payload=semantic,
            reason=f"publish performance metric catalog v{version_number}",
            draft_writer=partial(
                _write_catalog, definitions=definitions, version_number=version_number
            ),
        )
    return MetricCatalogPublication(result.artifact_id, len(definitions), result.reused)


class IntervalPerformancePublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        strategy_net_artifact_id: uuid.UUID,
        benchmark_net_artifact_id: uuid.UUID,
        metric_catalog_artifact_id: uuid.UUID,
        performance_engine_artifact_id: uuid.UUID,
        *,
        template_key: IntervalTemplateKey,
        as_of_date: date,
        custom_start: date | None = None,
        custom_end: date | None = None,
    ) -> IntervalResultPublication:
        context = self._load_context(
            strategy_net_artifact_id,
            benchmark_net_artifact_id,
            metric_catalog_artifact_id,
            performance_engine_artifact_id,
        )
        path_dates = tuple(point.nav_date for point in context.strategy_points)
        interval = resolve_interval(
            template_key=template_key,
            path_dates=path_dates,
            as_of_date=as_of_date,
            custom_start=custom_start,
            custom_end=custom_end,
        )
        results: dict[str, IntervalPerformance] = {}
        quality_status = "not_applicable"
        if interval.availability_status == "eligible":
            strategy_series = slice_carry_in_series(context.strategy_points, interval)
            benchmark_series = slice_carry_in_series(context.benchmark_points, interval)
            risk_free = tuple(
                context.reserve_returns[point.nav_date] for point in strategy_series.points
            )
            strategy_result = calculate_absolute_performance(strategy_series, risk_free)
            benchmark_result = calculate_absolute_performance(benchmark_series, risk_free)
            relative_result = calculate_relative_performance(
                strategy_series, benchmark_series, risk_free
            )
            results = {
                "strategy": strategy_result,
                "benchmark": benchmark_result,
                "relative": relative_result,
            }
            quality_status = strategy_result.quality_status
        semantic = {
            "strategy_net_artifact_id": str(strategy_net_artifact_id),
            "benchmark_net_artifact_id": str(benchmark_net_artifact_id),
            "metric_catalog_artifact_id": str(metric_catalog_artifact_id),
            "performance_engine_artifact_id": str(performance_engine_artifact_id),
            "template_key": template_key,
            "as_of_date": as_of_date,
            "custom_start": custom_start,
            "custom_end": custom_end,
            "initialization_policy": "carry_in",
        }
        key = sha256_hexdigest(semantic)[:20]
        dependencies = (
            DependencyInput(strategy_net_artifact_id, "strategy_net_path", 0),
            DependencyInput(benchmark_net_artifact_id, "benchmark_net_path", 1),
            DependencyInput(metric_catalog_artifact_id, "metric_catalog", 2),
            DependencyInput(performance_engine_artifact_id, "performance_engine", 3),
            DependencyInput(context.reserve_artifact_id, "risk_free_dataset", 4),
        )
        with self._engine.begin() as connection:
            publication = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
                artifact_type="interval_performance_result",
                artifact_key=f"performance:{key}",
                version_number=1,
                semantic_payload=semantic,
                content_payload={
                    **semantic,
                    "interval": asdict(interval),
                    "results": {
                        role: {key: asdict(value) for key, value in result.metrics.items()}
                        for role, result in results.items()
                    },
                },
                dependencies=dependencies,
                reason=f"publish interval performance {key}",
                draft_writer=partial(
                    _write_result,
                    context=context,
                    interval=interval,
                    results=results,
                    quality_status=quality_status,
                ),
            )
        metric_count = sum(len(item.metrics) for item in results.values())
        observation_count = next(iter(results.values())).observation_count if results else 0
        return IntervalResultPublication(
            publication.artifact_id,
            interval.availability_status,
            interval.exclusion_reason,
            quality_status,
            observation_count,
            metric_count,
            publication.reused,
        )

    def _load_context(
        self,
        strategy_artifact: uuid.UUID,
        benchmark_artifact: uuid.UUID,
        catalog_artifact: uuid.UUID,
        engine_artifact: uuid.UUID,
    ) -> _Context:
        with self._engine.connect() as connection:
            strategy = _net_context(connection, strategy_artifact, "model_strategy")
            benchmark = _net_context(connection, benchmark_artifact, "benchmark")
            if (
                strategy["cost_scenario_id"] != benchmark["cost_scenario_id"]
                or strategy["data_bundle_version_id"] != benchmark["data_bundle_version_id"]
                or strategy["effective_nav_start"] != benchmark["effective_nav_start"]
                or strategy["effective_nav_end"] != benchmark["effective_nav_end"]
                or strategy["portfolio_target_path_id"]
                != benchmark["reference_portfolio_target_path_id"]
            ):
                raise ValueError(
                    "Strategy and benchmark Net Paths must share context and per-side cost"
                )
            catalog = (
                connection.execute(
                    text(
                        "SELECT catalog.* FROM experiment.performance_metric_catalog catalog JOIN lineage.artifact artifact ON artifact.artifact_id = catalog.artifact_id AND artifact.status = 'published' WHERE catalog.artifact_id = :artifact"
                    ),
                    {"artifact": catalog_artifact},
                )
                .mappings()
                .one_or_none()
            )
            if catalog is None:
                raise ValueError("Published Performance Metric Catalog not found")
            perf_engine = (
                connection.execute(
                    text(
                        "SELECT version.* FROM ops.engine_version version JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id AND artifact.status = 'published' WHERE version.artifact_id = :artifact AND definition.engine_key = 'performance_engine'"
                    ),
                    {"artifact": engine_artifact},
                )
                .mappings()
                .one_or_none()
            )
            if perf_engine is None:
                raise ValueError("Published Performance engine not found")
            strategy_points = _net_points(connection, strategy["net_cost_path_id"])
            benchmark_points = _net_points(connection, benchmark["net_cost_path_id"])
            if tuple(point.nav_date for point in strategy_points) != tuple(
                point.nav_date for point in benchmark_points
            ):
                raise ValueError("Strategy and benchmark Net Paths must have identical dates")
            reserve = (
                connection.execute(
                    text(
                        "SELECT dataset.dataset_publication_id, dataset.artifact_id FROM data.data_bundle_member member JOIN data.dataset_publication dataset ON dataset.dataset_publication_id = member.dataset_publication_id JOIN lineage.artifact artifact ON artifact.artifact_id = dataset.artifact_id AND artifact.status = 'published' WHERE member.data_bundle_version_id = :bundle AND member.role = 'reserve_return'"
                    ),
                    {"bundle": strategy["data_bundle_version_id"]},
                )
                .mappings()
                .one()
            )
            reserve_by_end = {
                row["interval_end"]: Decimal(row["accrual_factor"]) - Decimal(1)
                for row in connection.execute(
                    text(
                        "SELECT interval_end, accrual_factor FROM data.reserve_return WHERE dataset_publication_id = :dataset"
                    ),
                    {"dataset": reserve["dataset_publication_id"]},
                ).mappings()
            }
            reserve_by_end[strategy_points[0].nav_date] = Decimal(0)
            missing = set(point.nav_date for point in strategy_points) - reserve_by_end.keys()
            if missing:
                raise ValueError("Reserve return dataset does not cover every performance date")
        return _Context(
            strategy,
            benchmark,
            catalog,
            perf_engine,
            strategy_points,
            benchmark_points,
            reserve_by_end,
            reserve["artifact_id"],
        )


def _net_context(connection: Connection, artifact_id: uuid.UUID, target_type: str) -> RowMapping:
    reference = (
        "benchmark.reference_portfolio_target_path_id"
        if target_type == "benchmark"
        else "NULL::uuid AS reference_portfolio_target_path_id"
    )
    benchmark_join = (
        "JOIN strategy.benchmark_target_path benchmark ON benchmark.portfolio_target_path_id = target.portfolio_target_path_id"
        if target_type == "benchmark"
        else ""
    )
    row = (
        connection.execute(
            text(
                f"SELECT net.*, gross.data_bundle_version_id, gross.portfolio_target_path_id, target.target_type, {reference} FROM experiment.net_cost_path net JOIN lineage.artifact artifact ON artifact.artifact_id = net.artifact_id AND artifact.status = 'published' JOIN experiment.gross_portfolio_path gross ON gross.gross_portfolio_path_id = net.gross_portfolio_path_id JOIN strategy.portfolio_target_path target ON target.portfolio_target_path_id = gross.portfolio_target_path_id {benchmark_join} WHERE net.artifact_id = :artifact AND target.target_type = :type"
            ),
            {"artifact": artifact_id, "type": target_type},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Published {target_type} Net Cost Path not found")
    return row


def _net_points(connection: Connection, path_id: uuid.UUID) -> tuple[SeriesPoint, ...]:
    return tuple(
        SeriesPoint(row["nav_date"], Decimal(row["net_daily_return"]), Decimal(row["net_nav"]))
        for row in connection.execute(
            text(
                "SELECT nav_date, net_daily_return, net_nav FROM experiment.net_daily_nav WHERE net_cost_path_id = :path ORDER BY nav_date"
            ),
            {"path": path_id},
        ).mappings()
    )


def _write_catalog(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    definitions: tuple[tuple[str, str, str, str], ...],
    version_number: int,
) -> None:
    catalog_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO experiment.performance_metric_catalog (performance_metric_catalog_id, artifact_id, catalog_key, version_number, methodology, metric_count) VALUES (:id, :artifact, :key, :version, 'interval-performance-v1', :count)"
        ),
        {
            "id": catalog_id,
            "artifact": artifact_id,
            "key": CATALOG_KEY,
            "version": version_number,
            "count": len(definitions),
        },
    )
    connection.execute(
        text(
            "INSERT INTO experiment.performance_metric_definition (performance_metric_definition_id, performance_metric_catalog_id, metric_scope, metric_key, name, unit, ordinal) VALUES (:id, :catalog, :scope, :key, :name, :unit, :ordinal)"
        ),
        [
            {
                "id": uuid.uuid4(),
                "catalog": catalog_id,
                "scope": scope,
                "key": key,
                "name": name,
                "unit": unit,
                "ordinal": ordinal,
            }
            for ordinal, (scope, key, name, unit) in enumerate(definitions)
        ],
    )


def _write_result(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _Context,
    interval: Any,
    results: dict[str, IntervalPerformance],
    quality_status: str,
) -> None:
    result_id = uuid.uuid4()
    metric_count = sum(len(item.metrics) for item in results.values())
    observation_count = next(iter(results.values())).observation_count if results else 0
    connection.execute(
        text(
            "INSERT INTO experiment.interval_performance_result (interval_performance_result_id, artifact_id, strategy_net_cost_path_id, benchmark_net_cost_path_id, performance_metric_catalog_id, engine_version_id, template_key, initialization_policy, as_of_date, requested_start, requested_end, resolved_start, resolved_end, normalization_nav_date, availability_status, exclusion_reason, quality_status, observation_count, metric_value_count) VALUES (:id, :artifact, :strategy, :benchmark, :catalog, :engine, :template, :policy, :as_of, :requested_start, :requested_end, :resolved_start, :resolved_end, :normalization, :availability, :exclusion, :quality, :observations, :metric_count)"
        ),
        {
            "id": result_id,
            "artifact": artifact_id,
            "strategy": context.strategy["net_cost_path_id"],
            "benchmark": context.benchmark["net_cost_path_id"],
            "catalog": context.catalog["performance_metric_catalog_id"],
            "engine": context.engine["engine_version_id"],
            "template": interval.template_key,
            "policy": interval.initialization_policy,
            "as_of": interval.as_of_date,
            "requested_start": interval.requested_start,
            "requested_end": interval.requested_end,
            "resolved_start": interval.resolved_start,
            "resolved_end": interval.resolved_end,
            "normalization": interval.normalization_nav_date,
            "availability": interval.availability_status,
            "exclusion": interval.exclusion_reason,
            "quality": quality_status,
            "observations": observation_count,
            "metric_count": metric_count,
        },
    )
    if not results:
        return
    definitions = {
        (row["metric_scope"], row["metric_key"]): row["performance_metric_definition_id"]
        for row in connection.execute(
            text(
                "SELECT performance_metric_definition_id, metric_scope, metric_key FROM experiment.performance_metric_definition WHERE performance_metric_catalog_id = :catalog"
            ),
            {"catalog": context.catalog["performance_metric_catalog_id"]},
        ).mappings()
    }
    rows: list[dict[str, Any]] = []
    for role, result in results.items():
        scope = "relative" if role == "relative" else "absolute"
        for key, value in result.metrics.items():
            rows.append(_metric_row(result_id, definitions[(scope, key)], role, value))
    connection.execute(
        text(
            "INSERT INTO experiment.performance_metric_value (interval_performance_result_id, performance_metric_definition_id, series_role, metric_value, value_status, reason_code, observation_count) VALUES (:result, :definition, :role, :value, :status, :reason, :count)"
        ),
        rows,
    )


def _metric_row(
    result_id: uuid.UUID, definition_id: uuid.UUID, role: str, metric: MetricValue
) -> dict[str, Any]:
    return {
        "result": result_id,
        "definition": definition_id,
        "role": role,
        "value": metric.value,
        "status": "defined" if metric.value is not None else "undefined",
        "reason": metric.reason_code,
        "count": metric.observation_count,
    }


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
