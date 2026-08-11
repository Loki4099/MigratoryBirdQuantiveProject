from __future__ import annotations

import math
import statistics
import uuid
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal, cast

from sqlalchemy import Engine, bindparam, text

from style_rotation.domain.enums import WorkFailureClass
from style_rotation.experiment.accounting import (
    calculate_gross_portfolio_path,
    map_execution_dates,
)
from style_rotation.experiment.contracts import (
    AccountingMarketBar,
    AccountingReserveInterval,
    TargetAssetWeight,
    TargetDecision,
)
from style_rotation.experiment.intervals import IntervalSeries, ResolvedInterval
from style_rotation.experiment.performance import (
    calculate_absolute_performance,
    calculate_relative_performance,
)
from style_rotation.experiment.result_payload import hydrate_cell_result_row
from style_rotation.experiment.v021_matrix import (
    ImpactPolicy,
    evaluate_capacity,
    square_root_impact_bps,
)
from style_rotation.factor.diagnostics import _spearman
from style_rotation.metrics.types import SeriesPoint
from style_rotation.ops.worker import (
    CellExecutionOutput,
    CellExecutionRequest,
    ClassifiedWorkFailure,
    ResultType,
    WorkItemWorker,
)
from style_rotation.strategy.v021_topk import (
    RankedAsset,
    build_topk_decision,
    internal_timing_defense_budget,
)
from style_rotation.workspace.materialization import WorkspaceSignalMaterializer

ZERO = Decimal(0)
ONE = Decimal(1)


class V021DatabaseExecutor:
    """Production v0.21 executor over exact published Signal/Data/Context artifacts."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._signal_materializer = WorkspaceSignalMaterializer(engine)

    def handlers(self) -> dict[str, Any]:
        return {"predictive": self.execute_predictive, "portfolio": self.execute_portfolio}

    def latest_product_scores(
        self,
        *,
        enrollment_id: uuid.UUID,
        data_bundle_artifact_id: uuid.UUID,
        as_of_session: date,
        cached_signals_only: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        with self._engine.connect() as connection:
            context = (
                connection.execute(
                    text("""
                SELECT model.slot_assignments, model.parameters,
                       spec.normalized_selection, spec.frequency,
                       strategy.strategy_family_key,
                       strategy.rule_graph -> 'parameters' AS strategy_parameters,
                       bundle.data_bundle_version_id,
                       policy.document ->> 'defensive_basket_version'
                           AS defensive_basket_version,
                       policy.document #>> '{release_gate_artifact_ids,pit_universe}'
                           AS pit_gate_artifact_id,
                       policy.document #>> '{release_gate_artifact_ids,terminal_event}'
                           AS terminal_gate_artifact_id
                FROM product.product_enrollment enrollment
                JOIN product.product_version version
                  ON version.product_version_id = enrollment.product_version_id
                JOIN strategy.compiled_strategy_version strategy
                  ON strategy.compiled_strategy_version_id = version.compiled_strategy_version_id
                JOIN workspace.compiled_model_instance model
                  ON model.compiled_model_instance_id = strategy.compiled_model_instance_id
                JOIN workspace.compiled_research_spec spec
                  ON spec.compiled_research_spec_id = strategy.compiled_research_spec_id
                JOIN experiment.execution_policy_catalog policy
                  ON policy.artifact_id = version.capital_policy_artifact_id
                JOIN data.data_bundle_version bundle ON bundle.artifact_id = :bundle_id
                WHERE enrollment.product_enrollment_id = :enrollment_id
            """),
                    {
                        "bundle_id": data_bundle_artifact_id,
                        "enrollment_id": enrollment_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
        if context is None:
            raise _contract("Product Enrollment or Data Bundle is unavailable")
        keys = tuple(
            key for slot in context["slot_assignments"] for key in slot["signal_version_keys"]
        )
        assets = self._selected_assets(context["normalized_selection"])
        signals, dimensions, artifacts, signal_metadata = self._signal_points(
            keys,
            context["data_bundle_version_id"],
            assets,
            frequency=context["frequency"],
            observation_start=as_of_session - timedelta(days=14),
            observation_end=as_of_session,
            allow_calculation=not cached_signals_only,
        )
        if set(signals) != set(keys):
            raise _data("OOS Data Bundle lacks the exact compiled Signal set")
        common = set.intersection(*(set(points) for points in signals.values()))
        valid = [identity for identity in common if identity[1] <= as_of_session]
        if not valid:
            raise _data("OOS Data Bundle has no valid compiled Model observations")
        decision_date = max(identity[1] for identity in valid)
        identities = [identity for identity in valid if identity[1] == decision_date]
        scores, _audit = _aggregate_scores(
            identities,
            keys=keys,
            signals=signals,
            dimensions=dimensions,
            weighting=context["parameters"].get("weighting", "equal_by_signal"),
            signal_metadata=signal_metadata,
        )
        return scores, {
            **dict(context),
            "decision_date": decision_date,
            "signal_dataset_artifact_ids": artifacts,
        }

    def market_data_for_bundle(
        self, artifact_id: uuid.UUID, terminal_gate_artifact_id: uuid.UUID | None
    ) -> tuple[tuple[AccountingMarketBar, ...], list[dict[str, Any]]]:
        with self._engine.connect() as connection:
            bundle_id = connection.execute(
                text(
                    "SELECT data_bundle_version_id FROM data.data_bundle_version "
                    "WHERE artifact_id = :artifact_id"
                ),
                {"artifact_id": artifact_id},
            ).scalar_one_or_none()
        if bundle_id is None:
            raise _data("Published Data Bundle version is unavailable")
        return self._market_bars(bundle_id, terminal_gate_artifact_id)

    def execute_predictive(self, request: CellExecutionRequest) -> CellExecutionOutput:
        context = self._predictive_context(request.cell_artifact_id)
        keys = tuple(
            key for slot in context["slot_assignments"] for key in slot["signal_version_keys"]
        )
        if not keys:
            raise _contract("Compiled Model has no Signal inputs")
        selected_assets = self._selected_assets(context["normalized_selection"])
        asset_family = self._asset_family(selected_assets)
        signals, dimensions, signal_artifacts, signal_metadata = self._signal_points(
            keys,
            context["data_bundle_version_id"],
            selected_assets,
            frequency=context["frequency"],
        )
        if set(signals) != set(keys):
            missing = sorted(set(keys).difference(signals))
            raise _data("Exact published Signal datasets are unavailable", missing=missing)
        common = set.intersection(*(set(points) for points in signals.values()))
        if not common:
            raise _data("Selected Signals have no common valid asset-date observations")
        target_values, target_artifact_id = self._forward_return_points(
            context["evaluation_target_key"],
            context["data_bundle_version_id"],
            selected_assets,
            frequency=context["frequency"],
        )
        target_period_dates = tuple(sorted(target_values))
        target_date_set = set(target_period_dates)
        evaluation_identities = [
            identity for identity in common if identity[1] in target_date_set
        ]
        if not evaluation_identities:
            raise _data("Selected Signals have no observations on frozen target dates")
        scores, input_audit = _aggregate_scores(
            evaluation_identities,
            keys=keys,
            signals=signals,
            dimensions=dimensions,
            weighting=context["parameters"].get("weighting", "equal_by_signal"),
            signal_metadata=signal_metadata,
        )
        scores_by_date: dict[date, dict[uuid.UUID, float]] = defaultdict(dict)
        for point in scores:
            scores_by_date[date.fromisoformat(point["observation_date"])][
                uuid.UUID(point["asset_id"])
            ] = float(point["score"])
        period_rank_ic: list[dict[str, Any]] = []
        defined_rank_ic: list[float] = []
        defined_rank_ic_dates: list[date] = []
        score_dispersions: list[float] = []
        cross_section_diagnostics: list[dict[str, Any]] = []
        previous_selected: set[uuid.UUID] | None = None
        aligned_periods = 0
        # Forward-return datasets contain only the decision dates prescribed by the
        # frozen target frequency (weekly/monthly), while published Signal datasets
        # may contain daily observations.  Coverage must therefore be measured over
        # target decision dates, not over every date on which a Model score exists.
        for day in target_period_dates:
            day_scores = scores_by_date.get(day, {})
            common_assets = sorted(
                set(day_scores).intersection(target_values.get(day, {})), key=str
            )
            if len(common_assets) < 2:
                continue
            aligned_periods += 1
            rank_ic = _spearman(
                [day_scores[asset_id] for asset_id in common_assets],
                [float(target_values[day][asset_id]) for asset_id in common_assets],
            )
            if rank_ic is not None:
                defined_rank_ic.append(rank_ic)
                defined_rank_ic_dates.append(day)
            score_dispersions.append(
                statistics.pstdev(float(day_scores[asset_id]) for asset_id in common_assets)
            )
            score_order = sorted(
                common_assets, key=lambda asset_id: (-day_scores[asset_id], str(asset_id))
            )
            target_order = sorted(
                common_assets,
                key=lambda asset_id: (-target_values[day][asset_id], str(asset_id)),
            )
            if asset_family == "etf":
                selected_set = {score_order[0]}
                realized_set = {target_order[0]}
                remaining = [target_values[day][asset_id] for asset_id in score_order[1:]]
                spread = float(
                    target_values[day][score_order[0]]
                    - (sum(remaining, ZERO) / Decimal(len(remaining)))
                )
                domain: dict[str, float | None] = {
                    "top1_hit": float(selected_set == realized_set),
                    "top1_spread": spread,
                    "random_baseline": 1.0 / len(common_assets),
                }
            else:
                decile_count = max(1, math.ceil(len(common_assets) * 0.1))
                top_count = min(20, len(common_assets))
                selected_set = set(score_order[:top_count])
                realized_set = set(target_order[:top_count])
                top_decile = [
                    target_values[day][asset_id] for asset_id in score_order[:decile_count]
                ]
                bottom_decile = [
                    target_values[day][asset_id] for asset_id in score_order[-decile_count:]
                ]
                domain = {
                    "top_decile_spread": float(
                        sum(top_decile, ZERO) / Decimal(decile_count)
                        - sum(bottom_decile, ZERO) / Decimal(decile_count)
                    ),
                    "precision_at_20": len(selected_set.intersection(realized_set)) / top_count,
                    "random_baseline": top_count / len(common_assets),
                }
            turnover = (
                None
                if previous_selected is None
                else 1.0
                - len(selected_set.intersection(previous_selected)) / max(1, len(selected_set))
            )
            domain["selection_turnover"] = turnover
            previous_selected = selected_set
            cross_section_diagnostics.append(
                {"decision_date": day.isoformat(), "asset_count": len(common_assets), **domain}
            )
            period_rank_ic.append(
                {
                    "decision_date": day.isoformat(),
                    "asset_count": len(common_assets),
                    "rank_ic": rank_ic,
                    "status": "defined" if rank_ic is not None else "target_degenerate",
                }
            )
        period_count = len(target_period_dates)
        target_coverage = aligned_periods / period_count if period_count else 0.0
        nondegenerate_ratio = len(defined_rank_ic) / aligned_periods if aligned_periods else 0.0
        minimum_periods = 26 if context["frequency"] == "weekly" else 12
        if aligned_periods < minimum_periods or target_coverage < 0.9 or nondegenerate_ratio < 0.8:
            raise _data(
                "Predictive target evaluation lacks sufficient valid periods",
                aligned_periods=aligned_periods,
                minimum_periods=minimum_periods,
                target_coverage=target_coverage,
                nondegenerate_ratio=nondegenerate_ratio,
            )
        mean_rank_ic = statistics.fmean(defined_rank_ic)
        rank_ic_stdev = statistics.stdev(defined_rank_ic) if len(defined_rank_ic) > 1 else 0.0
        rolling_rank_ic = [
            {
                "decision_date": defined_rank_ic_dates[index].isoformat(),
                "window": 13,
                "rolling_mean_rank_ic": statistics.fmean(defined_rank_ic[index - 12 : index + 1]),
            }
            for index in range(12, len(defined_rank_ic))
        ]
        domain_metrics = _mean_domain_metrics(cross_section_diagnostics)
        return CellExecutionOutput(
            availability_status="accepted",
            quality_status="passed",
            metrics={
                "signal_count": len(keys),
                "model_point_count": len(scores),
                "common_asset_date_coverage": len(evaluation_identities),
                "target_key": context["evaluation_target_key"],
                "target_period_count": period_count,
                "aligned_target_period_count": aligned_periods,
                "target_period_coverage": target_coverage,
                "nondegenerate_target_ratio": nondegenerate_ratio,
                "mean_rank_ic": mean_rank_ic,
                "median_rank_ic": statistics.median(defined_rank_ic),
                "positive_rank_ic_ratio": sum(value > 0 for value in defined_rank_ic)
                / len(defined_rank_ic),
                "rank_ic_information_ratio": (
                    mean_rank_ic / rank_ic_stdev if rank_ic_stdev > 0 else None
                ),
                "mean_score_dispersion": statistics.fmean(score_dispersions),
                "rolling_rank_ic_stability": (
                    statistics.pstdev(
                        cast(float, item["rolling_mean_rank_ic"]) for item in rolling_rank_ic
                    )
                    if len(rolling_rank_ic) > 1
                    else 0.0
                ),
                "asset_family": asset_family,
                **domain_metrics,
            },
            series={
                "model_scores": scores,
                "model_input_audit": input_audit,
                "period_rank_ic": period_rank_ic,
                "rolling_rank_ic": rolling_rank_ic,
                "cross_section_diagnostics": cross_section_diagnostics,
            },
            diagnostics={
                "executor": "v021_database_executor_v1",
                "data_bundle_artifact_id": str(context["data_bundle_artifact_id"]),
                "signal_dataset_artifact_ids": signal_artifacts,
                "forward_return_dataset_artifact_id": str(target_artifact_id),
                "forward_return_target_role": (
                    "computed_target_data_bundle"
                    if context["evaluation_target_key"].startswith(
                        ("future_return__h", "cross_sectional_relative_return__h")
                    )
                    else "forward_return_target_dataset"
                ),
                "normalization_policy": "branch_local_centered_percentile_rank_-1_1",
                "tie_policy": "average_rank",
                "missing_policy": "complete_compiled_input_set",
                "quality_checks": [
                    _check("exact_signal_input_set", "passed"),
                    _check("branch_local_normalization", "passed"),
                    _check("target_period_coverage", "passed"),
                    _check("target_nondegeneracy", "passed"),
                ],
            },
        )

    def execute_portfolio(self, request: CellExecutionRequest) -> CellExecutionOutput:
        context = self._portfolio_context(request.cell_artifact_id)
        model_scores = context["model_scores"]
        if not model_scores:
            raise _data("Predictive Result has no model scores")
        exploratory = context["suite_mode"] == "exploratory"
        terminal_gate_id = (
            None if exploratory else uuid.UUID(context["terminal_gate_artifact_id"])
        )
        bars, raw = self._market_bars(context["data_bundle_version_id"], terminal_gate_id)
        reserve = self._reserve_intervals(context["data_bundle_version_id"])
        sessions = tuple(sorted({item.session_date for item in bars}))
        if not sessions or not reserve:
            raise _data("Formal market bars and reserve intervals are required")
        reserve_start = min(item.interval_start for item in reserve)
        reserve_end = max(item.interval_end for item in reserve)
        common_start = max(context["resolved_start"], sessions[0], reserve_start)
        end = min(context["resolved_end"], sessions[-1], reserve_end)
        if common_start >= end:
            raise _data(
                "Market bars and reserve returns have no common execution interval",
                market_start=sessions[0].isoformat(),
                market_end=sessions[-1].isoformat(),
                reserve_start=reserve_start.isoformat(),
                reserve_end=reserve_end.isoformat(),
            )
        start = _window_start(context["window_key"], common_start, end)
        score_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for item in model_scores:
            day = date.fromisoformat(item["observation_date"])
            if start <= day <= end:
                score_by_date[day].append(item)
        asset_lookup = {item.asset_key: item.asset_id for item in bars}
        selected = self._selected_asset_records(context["normalized_selection"])
        parameters = context["parameters"]
        previous: set[str] = set()
        provisional: list[tuple[date, dict[str, Decimal], Decimal]] = []
        decision_audit: list[dict[str, Any]] = []
        spy = _spy_history(raw)
        decision_dates = _scheduled_decision_dates(
            tuple(score_by_date), sessions, context["frequency"], end
        )
        for day in decision_dates:
            score_lookup = {
                item["asset_key"]: Decimal(item["score"]) for item in score_by_date[day]
            }
            eligibility = (
                {
                    item["asset_id"]: item["asset_key"] in asset_lookup
                    and item["asset_key"] in score_lookup
                    for item in selected
                }
                if exploratory
                else self._pit_eligibility(
                    selected,
                    day,
                    uuid.UUID(context["pit_gate_artifact_id"]),
                    uuid.UUID(context["terminal_gate_artifact_id"]),
                )
            )
            sectors = (
                self._sector_keys(
                    tuple(item["asset_id"] for item in selected),
                    day,
                    uuid.UUID(context["pit_gate_artifact_id"]),
                )
                if not exploratory and parameters.get("sector_cap", "none") == "pit_30_percent"
                else {}
            )
            candidates = tuple(
                RankedAsset(
                    asset_key=item["asset_key"],
                    model_score=score_lookup.get(item["asset_key"]),
                    eligible=eligibility.get(item["asset_id"], False),
                    sector_key=sectors.get(item["asset_id"]),
                    previously_held=item["asset_key"] in previous,
                )
                for item in selected
            )
            defense = _defense_budget(parameters.get("defense", "none"), day, spy)
            decision = build_topk_decision(
                candidates,
                family=context["strategy_family_key"],
                target_k=int(parameters["target_k"]),
                research_mode="exploratory" if exploratory else "formal",
                selection_buffer=parameters.get("selection_buffer", "none"),
                sector_cap=parameters.get("sector_cap", "none"),
                defense_budget=defense,
            )
            if decision.status != "accepted":
                raise _data("Top-K Decision failed", reason_code=decision.reason_code)
            risk_weights = {item.asset_key: item.target_weight for item in decision.positions}
            defense_weights, reserve_weight = _defensive_allocations(
                decision.defense_budget,
                context["defensive_basket_version"],
                available_assets=set(asset_lookup),
                allow_reserve_fallback=exploratory,
            )
            risk_weights.update(defense_weights)
            provisional.append((day, risk_weights, reserve_weight))
            previous = {item.asset_key for item in decision.positions}
            decision_audit.append(
                {
                    "decision_date": day.isoformat(),
                    "eligible_count": decision.eligible_count,
                    "rankable_count": decision.rankable_count,
                    "coverage_ratio": str(decision.coverage_ratio),
                    "positions": [
                        {
                            "asset_key": item.asset_key,
                            "rank": item.rank,
                            "target_weight": str(item.target_weight),
                        }
                        for item in decision.positions
                    ],
                    "defense_budget": str(decision.defense_budget),
                    "reserve_target_weight": str(reserve_weight),
                    "defensive_basket_version": context["defensive_basket_version"],
                }
            )
        if not provisional:
            raise _data("No scheduled Decision can execute inside the requested window")
        configured_keys = sorted({key for _, weights, _ in provisional for key in weights})
        missing_bars = sorted(set(configured_keys).difference(asset_lookup))
        if missing_bars:
            raise _data("Configured risk/defensive assets lack market bars", missing=missing_bars)
        decisions = tuple(
            TargetDecision(
                day,
                tuple(
                    TargetAssetWeight(asset_lookup[key], key, weights.get(key, ZERO))
                    for key in configured_keys
                ),
                reserve_weight,
            )
            for day, weights, reserve_weight in provisional
        )
        targets = map_execution_dates(
            decisions, sessions, simulation_end=end, delay_common_sessions=1
        )
        configured_ids = {asset_lookup[key] for key in configured_keys}
        gross = calculate_gross_portfolio_path(
            bars=tuple(
                item
                for item in bars
                if item.asset_id in configured_ids and start <= item.session_date <= end
            ),
            reserve_intervals=reserve,
            targets=targets,
            common_sessions=tuple(day for day in sessions if start <= day <= end),
            simulation_end=end,
        )
        capacity, net_points, trade_audit = self._net_path_with_costs(
            context, gross.daily_nav, gross.executions, gross.trades, raw
        )
        capacity_warning = capacity != "accepted"
        gross_points = [
            (item.nav_date, item.daily_return, item.gross_nav) for item in gross.daily_nav
        ]
        if capacity_warning and not exploratory:
            return CellExecutionOutput(
                availability_status=cast(
                    Literal["capacity_rejected", "data_quality_failed"], capacity
                ),
                quality_status="warning",
                metrics={},
                series={
                    "gross_nav_series": _single_nav_series(gross_points),
                    "decisions": decision_audit,
                    "trade_capacity": trade_audit,
                },
                diagnostics={
                    "executor": "v021_database_executor_v1",
                    "data_bundle_artifact_id": str(context["data_bundle_artifact_id"]),
                    "predictive_result_artifact_id": str(context["predictive_result_artifact_id"]),
                    "pit_gate_artifact_id": context["pit_gate_artifact_id"],
                    "terminal_gate_artifact_id": context["terminal_gate_artifact_id"],
                    "impact_gate_artifact_id": context["impact_gate_artifact_id"],
                    "quality_checks": [_check("capacity_adv_5_percent", "warning")],
                    "capacity_status": capacity,
                },
            )
        benchmark, benchmark_audit = self._benchmark_path(
            context=context,
            raw=raw,
            bars=bars,
            reserve=reserve,
            sessions=sessions,
            first_decision=decisions[0].decision_date,
            start=start,
            end=end,
        )
        research_benchmark, research_benchmark_audit = self._research_benchmark_path(
            context=context,
            selected=selected,
            asset_lookup=asset_lookup,
            decision_dates=decision_dates,
            raw=raw,
            bars=bars,
            reserve=reserve,
            sessions=sessions,
            start=start,
            end=end,
        )
        risk_free = _risk_free_returns(reserve, tuple(item[0] for item in net_points))
        strategy_series = _interval_series(net_points)
        benchmark_series = _interval_series(benchmark)
        research_benchmark_series = _interval_series(research_benchmark)
        absolute = calculate_absolute_performance(strategy_series, risk_free)
        relative = calculate_relative_performance(strategy_series, benchmark_series, risk_free)
        research_relative = calculate_relative_performance(
            strategy_series, research_benchmark_series, risk_free
        )
        metrics = {
            "strategy": {key: _metric_value(value) for key, value in absolute.metrics.items()},
            "relative": {key: _metric_value(value) for key, value in relative.metrics.items()},
            "relative_research": {
                key: _metric_value(value) for key, value in research_relative.metrics.items()
            },
            "benchmark": {
                key: _metric_value(value)
                for key, value in calculate_absolute_performance(
                    benchmark_series, risk_free
                ).metrics.items()
            },
            "research_benchmark": {
                key: _metric_value(value)
                for key, value in calculate_absolute_performance(
                    research_benchmark_series, risk_free
                ).metrics.items()
            },
        }
        nav_series = _nav_series(net_points, benchmark)
        return CellExecutionOutput(
            availability_status="accepted",
            quality_status=(
                "passed"
                if absolute.quality_status == "passed" and not capacity_warning
                else "warning"
            ),
            metrics=metrics,
            series={
                "nav_series": nav_series,
                "gross_nav_series": _single_nav_series(gross_points),
                "decisions": decision_audit,
                "trade_capacity": trade_audit,
                "benchmark_execution": benchmark_audit,
                "research_benchmark_nav_series": _single_nav_series(research_benchmark),
                "research_benchmark_execution": research_benchmark_audit,
            },
            diagnostics={
                "executor": "v021_database_executor_v1",
                "data_bundle_artifact_id": str(context["data_bundle_artifact_id"]),
                "predictive_result_artifact_id": str(context["predictive_result_artifact_id"]),
                "pit_gate_artifact_id": context["pit_gate_artifact_id"],
                "terminal_gate_artifact_id": context["terminal_gate_artifact_id"],
                "impact_gate_artifact_id": context["impact_gate_artifact_id"],
                "suite_mode": context["suite_mode"],
                "requested_start": start.isoformat(),
                "requested_end": end.isoformat(),
                "resolved_start": start.isoformat(),
                "resolved_end": end.isoformat(),
                "normalization_nav_date": nav_series[0]["nav_date"],
                "observation_count": len(nav_series),
                "quality_checks": [
                    _check(
                        "capacity_adv_5_percent",
                        "warning" if capacity_warning else "passed",
                    ),
                    _check("pit_and_terminal_evidence", "passed"),
                    _check("accounting_reconciliation", "passed"),
                    _check("benchmark_same_execution_contract", "passed"),
                    _check("research_benchmark_same_execution_contract", "passed"),
                ],
            },
        )

    def _predictive_context(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text("""
                SELECT model.slot_assignments, model.parameters, spec.normalized_selection,
                       cell.frequency, cell.evaluation_target_key,
                       context.data_bundle_artifact_id, bundle.data_bundle_version_id
                FROM experiment.predictive_cell_specification cell
                JOIN workspace.compiled_model_instance model
                  ON model.compiled_model_instance_id = cell.compiled_model_instance_id
                JOIN workspace.compiled_research_spec spec
                  ON spec.compiled_research_spec_id = model.compiled_research_spec_id
                JOIN experiment.research_suite suite
                  ON suite.research_suite_id = cell.research_suite_id
                JOIN experiment.execution_policy_catalog policy
                  ON policy.execution_policy_catalog_id = suite.execution_policy_catalog_id
                JOIN experiment.comparison_context context
                  ON context.artifact_id = CAST(
                     policy.document ->> 'comparison_context_artifact_id' AS uuid)
                JOIN data.data_bundle_version bundle
                  ON bundle.artifact_id = context.data_bundle_artifact_id
                WHERE cell.artifact_id = :artifact_id
            """),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise _contract("Predictive Cell has no exact published execution context")
        return dict(row)

    def _forward_return_points(
        self,
        target_key: str,
        bundle_id: uuid.UUID,
        assets: tuple[uuid.UUID, ...],
        *,
        frequency: str = "weekly",
    ) -> tuple[dict[date, dict[uuid.UUID, Decimal]], uuid.UUID]:
        if target_key.startswith(("future_return__h", "cross_sectional_relative_return__h")):
            return self._session_horizon_return_points(
                target_key, bundle_id, assets, frequency=frequency
            )
        with self._engine.connect() as connection:
            datasets = (
                connection.execute(
                    text("""
                        SELECT dataset.forward_return_dataset_id, dataset.artifact_id
                        FROM data.forward_return_dataset dataset
                        JOIN data.forward_return_version version
                          ON version.forward_return_version_id = dataset.forward_return_version_id
                        JOIN data.forward_return_definition definition
                          ON definition.forward_return_definition_id =
                             version.forward_return_definition_id
                        JOIN lineage.artifact artifact ON artifact.artifact_id = dataset.artifact_id
                        WHERE dataset.data_bundle_version_id = :bundle_id
                          AND definition.target_key = :target_key
                          AND artifact.status = 'published'
                    """),
                    {"bundle_id": bundle_id, "target_key": target_key},
                )
                .mappings()
                .all()
            )
            if len(datasets) != 1:
                raise _contract(
                    "Predictive target is absent or ambiguous in the frozen Data Bundle: "
                    f"{target_key} ({len(datasets)} datasets)"
                )
            dataset = datasets[0]
            rows = (
                connection.execute(
                    text("""
                        SELECT asset_id, decision_date, forward_return
                        FROM data.forward_return_value
                        WHERE forward_return_dataset_id = :dataset_id
                          AND asset_id IN :assets
                    """).bindparams(bindparam("assets", expanding=True)),
                    {
                        "dataset_id": dataset["forward_return_dataset_id"],
                        "assets": assets,
                    },
                )
                .mappings()
                .all()
            )
        values: dict[date, dict[uuid.UUID, Decimal]] = defaultdict(dict)
        for row in rows:
            values[row["decision_date"]][row["asset_id"]] = row["forward_return"]
        return dict(values), dataset["artifact_id"]

    def _session_horizon_return_points(
        self,
        target_key: str,
        bundle_id: uuid.UUID,
        assets: tuple[uuid.UUID, ...],
        *,
        frequency: str,
    ) -> tuple[dict[date, dict[uuid.UUID, Decimal]], uuid.UUID]:
        kind, horizon_text = target_key.rsplit("__h", 1)
        horizon = int(horizon_text)
        if kind not in {"future_return", "cross_sectional_relative_return"}:
            raise _contract(f"Unsupported Model target kind: {target_key}")
        if horizon not in {5, 21, 63}:
            raise _contract(f"Unsupported Model target horizon: {target_key}")
        if frequency not in {"weekly", "monthly"}:
            raise _contract(f"Unsupported Model target frequency: {frequency}")
        with self._engine.connect() as connection:
            bundle = (
                connection.execute(
                    text(
                        "SELECT artifact_id FROM data.data_bundle_version "
                        "WHERE data_bundle_version_id = :bundle_id"
                    ),
                    {"bundle_id": bundle_id},
                ).scalar_one()
            )
            members = {
                row["role"]: row
                for row in connection.execute(
                    text(
                        "SELECT role, dataset_publication_id, calendar_version_id "
                        "FROM data.data_bundle_member WHERE data_bundle_version_id = :bundle_id"
                    ),
                    {"bundle_id": bundle_id},
                ).mappings()
            }
            market_id = members["canonical_market"]["dataset_publication_id"]
            calendar_id = members["trading_calendar"]["calendar_version_id"]
            sessions = tuple(
                connection.execute(
                    text(
                        "SELECT session_date FROM catalog.calendar_session "
                        "WHERE calendar_version_id = :calendar_id ORDER BY session_date"
                    ),
                    {"calendar_id": calendar_id},
                ).scalars()
            )
            rows = connection.execute(
                text(
                    "SELECT asset_id, session_date, open_adj FROM data.daily_bar "
                    "WHERE dataset_publication_id = :market_id AND asset_id IN :assets"
                ).bindparams(bindparam("assets", expanding=True)),
                {"market_id": market_id, "assets": assets},
            ).mappings()
            opens = {
                (row["asset_id"], row["session_date"]): Decimal(row["open_adj"])
                for row in rows
            }
        grouped: dict[tuple[int, int], date] = {}
        for session in sessions:
            if frequency == "weekly":
                iso = session.isocalendar()
                group = (iso.year, iso.week)
            else:
                group = (session.year, session.month)
            grouped[group] = session
        decisions = tuple(grouped[key] for key in sorted(grouped))
        session_index = {session: index for index, session in enumerate(sessions)}
        values: dict[date, dict[uuid.UUID, Decimal]] = {}
        for decision in decisions:
            start_index = session_index[decision] + 1
            end_index = start_index + horizon
            if end_index >= len(sessions):
                continue
            start, end = sessions[start_index], sessions[end_index]
            absolute: dict[uuid.UUID, Decimal] = {}
            for asset_id in assets:
                start_open = opens.get((asset_id, start))
                end_open = opens.get((asset_id, end))
                if start_open is None or end_open is None or start_open <= 0 or end_open <= 0:
                    continue
                absolute[asset_id] = end_open / start_open - ONE
            if not absolute:
                continue
            if kind == "future_return":
                values[decision] = absolute
            else:
                mean = sum(absolute.values(), ZERO) / Decimal(len(absolute))
                values[decision] = {
                    asset_id: value - mean for asset_id, value in absolute.items()
                }
        if not values:
            raise _data(
                "Selected target has no complete horizon observations", target_key=target_key
            )
        return values, bundle

    def _selected_assets(self, selection: dict[str, Any]) -> tuple[uuid.UUID, ...]:
        security_ids = tuple(uuid.UUID(value) for value in selection["asset_security_ids"])
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT legacy_asset_id FROM catalog.security "
                        "WHERE security_id IN :ids AND legacy_asset_id IS NOT NULL"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": security_ids},
                )
                .scalars()
                .all()
            )
        if len(rows) != len(security_ids):
            raise _data("Selected assets are not mapped to canonical market identities")
        return tuple(rows)

    def _asset_family(self, asset_ids: tuple[uuid.UUID, ...]) -> Literal["etf", "stock"]:
        with self._engine.connect() as connection:
            types = set(
                connection.execute(
                    text(
                        "SELECT DISTINCT asset_type FROM catalog.asset WHERE asset_id IN :ids"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": asset_ids},
                )
                .scalars()
                .all()
            )
        if types and types <= {"etf"}:
            return "etf"
        if types and types <= {"equity"}:
            return "stock"
        raise _contract("Predictive Cell assets must be a single ETF or stock family")

    def _selected_asset_records(self, selection: dict[str, Any]) -> list[dict[str, Any]]:
        security_ids = tuple(uuid.UUID(value) for value in selection["asset_security_ids"])
        with self._engine.connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT security.security_id, asset.asset_id, asset.asset_key
                        FROM catalog.security security
                        JOIN catalog.asset asset ON asset.asset_id = security.legacy_asset_id
                        WHERE security.security_id IN :ids
                        ORDER BY asset.asset_key
                        """
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": security_ids},
                )
                .mappings()
                .all()
            ]
        if len(rows) != len(security_ids):
            raise _data("Selected assets are not mapped to canonical market identities")
        return rows

    def _sector_keys(
        self,
        asset_ids: tuple[uuid.UUID, ...],
        day: date,
        pit_gate_artifact_id: uuid.UUID,
    ) -> dict[uuid.UUID, str]:
        document = self._frozen_gate_document(pit_gate_artifact_id, "pit_universe")
        rows = document.get("sector_classifications")
        if not isinstance(rows, list):
            raise _contract("Frozen PIT Gate lacks sector-classification intervals")
        wanted = set(asset_ids)
        output: dict[uuid.UUID, str] = {}
        for row in rows:
            asset_id = uuid.UUID(str(row["asset_id"]))
            valid_from = (
                date.fromisoformat(str(row["valid_from"])) if row.get("valid_from") else None
            )
            valid_to = date.fromisoformat(str(row["valid_to"])) if row.get("valid_to") else None
            if (
                asset_id in wanted
                and (valid_from is None or valid_from <= day)
                and (valid_to is None or day < valid_to)
            ):
                output[asset_id] = str(row["value_key"])
        return output

    def _pit_eligibility(
        self,
        assets: list[dict[str, Any]],
        day: date,
        pit_gate_artifact_id: uuid.UUID,
        terminal_gate_artifact_id: uuid.UUID,
    ) -> dict[uuid.UUID, bool]:
        asset_ids = tuple(item["asset_id"] for item in assets)
        with self._engine.connect() as connection:
            gate = (
                connection.execute(
                    text(
                        """
                        SELECT pit.document AS pit_document,
                               terminal.document AS terminal_document
                        FROM workspace.release_gate_evidence pit
                        JOIN workspace.release_gate_evidence terminal ON true
                        JOIN lineage.artifact pit_artifact
                          ON pit_artifact.artifact_id = pit.artifact_id
                         AND pit_artifact.status = 'published'
                        JOIN lineage.artifact terminal_artifact
                          ON terminal_artifact.artifact_id = terminal.artifact_id
                         AND terminal_artifact.status = 'published'
                        WHERE pit.artifact_id = :pit_id AND pit.gate_key = 'pit_universe'
                          AND terminal.artifact_id = :terminal_id
                          AND terminal.gate_key = 'terminal_event'
                        """
                    ),
                    {"pit_id": pit_gate_artifact_id, "terminal_id": terminal_gate_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if gate is None:
                raise _contract("Frozen PIT/Terminal Gate evidence is unavailable")
            snapshot_artifact_id = uuid.UUID(
                str(gate["pit_document"]["eligibility_snapshot_artifact_id"])
            )
            terminal_ids = tuple(
                uuid.UUID(str(value))
                for value in gate["terminal_document"].get("terminal_event_artifact_ids", [])
            )
            rows = (
                connection.execute(
                    text(
                        """
                        SELECT item.asset_id, item.is_eligible,
                               item.available_start, item.available_end, item.data_ready_date,
                               EXISTS (
                                   SELECT 1 FROM catalog.security security
                                   JOIN catalog.security_terminal_event event
                                     ON event.security_id = security.security_id
                                   WHERE security.legacy_asset_id = item.asset_id
                                     AND event.artifact_id IN :terminal_ids
                                     AND event.effective_session <= :day
                                     AND event.known_at::date <= :day
                               ) AS terminal
                        FROM catalog.eligibility_snapshot snapshot
                        JOIN catalog.eligibility_item item
                          ON item.eligibility_snapshot_id = snapshot.eligibility_snapshot_id
                        WHERE snapshot.artifact_id = :snapshot_id AND item.asset_id IN :asset_ids
                        """
                    ).bindparams(
                        bindparam("asset_ids", expanding=True),
                        bindparam("terminal_ids", expanding=True),
                    ),
                    {
                        "snapshot_id": snapshot_artifact_id,
                        "asset_ids": asset_ids,
                        "terminal_ids": terminal_ids,
                        "day": day,
                    },
                )
                .mappings()
                .all()
            )
        return {
            row["asset_id"]: bool(
                row["is_eligible"]
                and not row["terminal"]
                and (row["available_start"] is None or row["available_start"] <= day)
                and (row["available_end"] is None or day <= row["available_end"])
                and (row["data_ready_date"] is None or row["data_ready_date"] <= day)
            )
            for row in rows
        }

    def _signal_points(
        self,
        keys: tuple[str, ...],
        bundle_id: uuid.UUID,
        assets: tuple[uuid.UUID, ...],
        *,
        frequency: str,
        observation_start: date | None = None,
        observation_end: date | None = None,
        allow_calculation: bool = True,
    ) -> tuple[
        dict[str, dict[tuple[uuid.UUID, date], tuple[str, Decimal]]],
        dict[str, str],
        list[str],
        dict[str, dict[str, Any]],
    ]:
        materialized = self._signal_materializer.materialize(
            signal_version_keys=keys,
            asset_ids=assets,
            frequency=cast(Literal["weekly", "monthly"], frequency),
            bundle_version_id=bundle_id,
            observation_start=observation_start,
            observation_end=observation_end,
            allow_calculation=allow_calculation,
        )
        return (
            materialized.signals,
            materialized.dimensions,
            materialized.source_ids,
            materialized.metadata,
        )

    def _portfolio_context(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text("""
                SELECT cell.window_key, cell.cost_bps_per_side AS bps,
                       strategy.strategy_family_key,
                       strategy.rule_graph -> 'parameters' AS parameters,
                       strategy.compiled_model_instance_id, suite.research_suite_id,
                       spec.frequency, spec.normalized_selection,
                       context.resolved_start, context.resolved_end,
                       context.data_bundle_artifact_id, bundle.data_bundle_version_id,
                       policy.document -> 'impact_policy' AS impact_policy,
                       policy.document ->> 'defensive_basket_version'
                           AS defensive_basket_version,
                       policy.document #>> '{release_gate_artifact_ids,pit_universe}'
                           AS pit_gate_artifact_id,
                       policy.document #>> '{release_gate_artifact_ids,terminal_event}'
                           AS terminal_gate_artifact_id,
                       policy.document #>> '{release_gate_artifact_ids,impact_policy}'
                           AS impact_gate_artifact_id,
                       suite.suite_mode
                FROM experiment.portfolio_cell_specification cell
                JOIN strategy.compiled_strategy_version strategy
                  ON strategy.compiled_strategy_version_id = cell.compiled_strategy_version_id
                JOIN workspace.compiled_research_spec spec
                  ON spec.compiled_research_spec_id = strategy.compiled_research_spec_id
                JOIN experiment.research_suite suite
                  ON suite.research_suite_id = cell.research_suite_id
                JOIN experiment.execution_policy_catalog policy
                  ON policy.execution_policy_catalog_id = suite.execution_policy_catalog_id
                JOIN experiment.comparison_context context
                  ON context.artifact_id = CAST(
                     policy.document ->> 'comparison_context_artifact_id' AS uuid)
                JOIN data.data_bundle_version bundle
                  ON bundle.artifact_id = context.data_bundle_artifact_id
                WHERE cell.artifact_id = :artifact_id
            """),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise _contract("Portfolio Cell has no exact published execution context")
            predictive = (
                connection.execute(
                    text("""
                SELECT result.series, result.diagnostics,
                       result.payload_storage_uri, result.payload_content_hash,
                       result.payload_storage_format, result.payload_schema_version,
                       result.payload_byte_size, result.artifact_id
                FROM experiment.predictive_cell_specification cell
                JOIN experiment.cell_result result ON result.cell_artifact_id = cell.artifact_id
                WHERE cell.research_suite_id = :suite_id
                  AND cell.compiled_model_instance_id = :model_id
                  AND result.availability_status = 'accepted'
            """),
                    {
                        "suite_id": row["research_suite_id"],
                        "model_id": row["compiled_model_instance_id"],
                    },
                )
                .mappings()
                .one_or_none()
            )
        if predictive is None:
            raise ClassifiedWorkFailure(
                WorkFailureClass.INTERRUPTED,
                "Portfolio Cell is waiting for its Predictive Result",
            )
        predictive_result = hydrate_cell_result_row(predictive)
        return {
            **dict(row),
            "model_scores": predictive_result["series"].get("model_scores", []),
            "predictive_result_artifact_id": predictive_result["artifact_id"],
        }

    def _market_bars(
        self, bundle_id: uuid.UUID, terminal_gate_artifact_id: uuid.UUID | None
    ) -> tuple[tuple[AccountingMarketBar, ...], list[dict[str, Any]]]:
        terminal_document = (
            self._frozen_gate_document(terminal_gate_artifact_id, "terminal_event")
            if terminal_gate_artifact_id is not None
            else {}
        )
        terminal_ids = tuple(
            uuid.UUID(str(value))
            for value in terminal_document.get("terminal_event_artifact_ids", [])
        )
        with self._engine.connect() as connection:
            publication_id = connection.execute(
                text("""
                SELECT member.dataset_publication_id
                FROM data.data_bundle_member member
                WHERE member.data_bundle_version_id = :bundle_id
                  AND member.role = 'canonical_market'
                  AND member.dataset_publication_id IS NOT NULL
                  AND EXISTS (SELECT 1 FROM data.daily_bar bar
                              WHERE bar.dataset_publication_id = member.dataset_publication_id)
                LIMIT 1
            """),
                {"bundle_id": bundle_id},
            ).scalar_one_or_none()
            if publication_id is None:
                raise _data("Data Bundle has no canonical market-bar member")
            rows = [
                dict(row)
                for row in connection.execute(
                    text("""
                SELECT bar.asset_id, asset.asset_key, bar.session_date,
                       bar.open_raw, bar.open_adj, bar.close_adj,
                       bar.close_raw, bar.volume_raw
                FROM data.daily_bar bar JOIN catalog.asset asset ON asset.asset_id = bar.asset_id
                WHERE bar.dataset_publication_id = :publication_id
                ORDER BY bar.session_date, asset.asset_key
            """),
                    {"publication_id": publication_id},
                )
                .mappings()
                .all()
            ]
            terminal_events = (
                connection.execute(
                    text(
                        """
                        SELECT security.legacy_asset_id AS asset_id, event.artifact_id,
                               event.effective_session, event.terminal_total_return,
                               event.status
                        FROM catalog.security_terminal_event event
                        JOIN catalog.security security ON security.security_id = event.security_id
                        WHERE security.legacy_asset_id IS NOT NULL
                          AND event.artifact_id IN :terminal_ids
                          AND event.status IN ('confirmed','estimated')
                        ORDER BY event.effective_session
                        """
                    ).bindparams(bindparam("terminal_ids", expanding=True)),
                    {"terminal_ids": terminal_ids},
                )
                .mappings()
                .all()
                if terminal_ids
                else []
            )
        rows = _complete_accounting_bar_grid(
            rows,
            terminal_events,
            allow_unverified_carry=terminal_gate_artifact_id is None,
        )
        bars = tuple(
            AccountingMarketBar(
                row["asset_id"],
                row["asset_key"],
                row["session_date"],
                row["open_adj"],
                row["close_adj"],
            )
            for row in rows
        )
        return bars, rows

    def _frozen_gate_document(self, artifact_id: uuid.UUID, gate_key: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
            document = connection.execute(
                text(
                    """
                    SELECT gate.document
                    FROM workspace.release_gate_evidence gate
                    JOIN lineage.artifact artifact
                      ON artifact.artifact_id = gate.artifact_id
                     AND artifact.status = 'published'
                    WHERE gate.artifact_id = :artifact_id AND gate.gate_key = :gate_key
                    """
                ),
                {"artifact_id": artifact_id, "gate_key": gate_key},
            ).scalar_one_or_none()
        if document is None:
            raise _contract(f"Frozen {gate_key} Gate evidence is unavailable")
        return dict(document)

    def _reserve_intervals(self, bundle_id: uuid.UUID) -> tuple[AccountingReserveInterval, ...]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text("""
                SELECT reserve.* FROM data.reserve_return reserve
                JOIN data.data_bundle_member member
                  ON member.dataset_publication_id = reserve.dataset_publication_id
                WHERE member.data_bundle_version_id = :bundle_id
                ORDER BY reserve.interval_start
            """),
                    {"bundle_id": bundle_id},
                )
                .mappings()
                .all()
            )
        return tuple(
            AccountingReserveInterval(
                row["interval_start"],
                row["interval_end"],
                row["accrual_factor"],
                row["source_observation_date"],
                row["source_available_date"],
                row["quality_status"],
            )
            for row in rows
        )

    def _net_path_with_costs(
        self,
        context: dict[str, Any],
        gross_rows: tuple[Any, ...],
        executions: tuple[Any, ...],
        trades: tuple[Any, ...],
        raw: list[dict[str, Any]],
    ) -> tuple[str, list[tuple[date, Decimal, Decimal]], list[dict[str, Any]]]:
        history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in raw:
            history[row["asset_key"]].append(row)
        history_dates = {
            key: [item["session_date"] for item in items] for key, items in history.items()
        }
        history_by_date = {
            key: {item["session_date"]: item for item in items} for key, items in history.items()
        }
        impact_enabled = bool(context["impact_policy"].get("enabled", True))
        policy = ImpactPolicy.model_validate(
            {key: value for key, value in context["impact_policy"].items() if key != "enabled"}
        )
        by_execution = {item.execution_date: item for item in executions}
        trades_by_date: dict[date, list[Any]] = defaultdict(list)
        for trade in trades:
            trades_by_date[trade.execution_date].append(trade)
        initial_capital = Decimal("100000000")
        exploratory = context["suite_mode"] == "exploratory"
        overall_status = "accepted"
        nav = ONE
        points: list[tuple[date, Decimal, Decimal]] = []
        audit: list[dict[str, Any]] = []
        for row in gross_rows:
            prior_nav = nav
            nav *= row.overnight_factor
            execution = by_execution.get(row.nav_date)
            if execution is not None:
                pretrade_currency_nav = nav * initial_capital
                impact_fraction = ZERO
                for trade in trades_by_date[row.nav_date]:
                    if trade.absolute_weight_change == ZERO:
                        continue
                    asset_history = history[trade.asset_key]
                    prior_index = bisect_left(
                        history_dates[trade.asset_key], trade.execution_date
                    )
                    window = asset_history[max(0, prior_index - 20) : prior_index]
                    if len(window) < 20:
                        audit.append(
                            {
                                "execution_date": row.nav_date.isoformat(),
                                "asset_key": trade.asset_key,
                                "status": "data_quality_failed",
                                "reason": "adv20_history_incomplete",
                            }
                        )
                        if not exploratory:
                            return "data_quality_failed", points, audit
                        overall_status = "data_quality_failed"
                        continue
                    dollar = [item["close_raw"] * Decimal(item["volume_raw"]) for item in window]
                    adv20 = Decimal(statistics.median(dollar))
                    order_notional = trade.absolute_weight_change * pretrade_currency_nav
                    capacity = evaluate_capacity(
                        order_notional=order_notional,
                        trailing_median_dollar_volume_20=adv20,
                    )
                    raw_row = history_by_date[trade.asset_key][row.nav_date]
                    record: dict[str, Any] = {
                        "decision_date": trade.decision_date.isoformat(),
                        "execution_date": row.nav_date.isoformat(),
                        "asset_key": trade.asset_key,
                        "side": trade.side,
                        "raw_open": str(raw_row["open_raw"]),
                        "pretrade_currency_nav": str(pretrade_currency_nav),
                        "absolute_weight_change": str(trade.absolute_weight_change),
                        "order_notional": str(order_notional),
                        "trailing_median_dollar_volume_20": str(adv20),
                        "participation_rate": (
                            str(capacity.participation_rate)
                            if capacity.participation_rate is not None
                            else None
                        ),
                        "status": capacity.status,
                    }
                    audit.append(record)
                    if capacity.status != "accepted" or capacity.participation_rate is None:
                        if not exploratory:
                            return capacity.status, points, audit
                        if overall_status == "accepted":
                            overall_status = capacity.status
                        record["advisory_only"] = True
                        record["impact_bps"] = None
                        continue
                    returns = [
                        window[index]["close_adj"] / window[index - 1]["close_adj"] - ONE
                        for index in range(1, len(window))
                    ]
                    volatility = Decimal(str(statistics.stdev(float(value) for value in returns)))
                    impact_bps = (
                        square_root_impact_bps(
                            participation_rate=capacity.participation_rate,
                            daily_volatility=volatility,
                            policy=policy,
                        )
                        if impact_enabled
                        else ZERO
                    )
                    record["daily_volatility"] = str(volatility)
                    record["impact_bps"] = str(impact_bps)
                    impact_fraction += trade.absolute_weight_change * impact_bps / Decimal(10000)
                base_cost = (
                    execution.gross_traded_fraction * Decimal(context["bps"]) / Decimal(10000)
                )
                total_cost = base_cost + impact_fraction
                if total_cost >= ONE:
                    raise _data("Transaction cost exhausts NAV")
                nav *= ONE - total_cost
            nav *= row.intraday_factor
            points.append((row.nav_date, nav / prior_nav - ONE, nav))
        return overall_status, points, audit

    def _benchmark_path(
        self,
        *,
        context: dict[str, Any],
        raw: list[dict[str, Any]],
        bars: tuple[AccountingMarketBar, ...],
        reserve: tuple[AccountingReserveInterval, ...],
        sessions: tuple[date, ...],
        first_decision: date,
        start: date,
        end: date,
    ) -> tuple[list[tuple[date, Decimal, Decimal]], list[dict[str, Any]]]:
        filtered_sessions = tuple(day for day in sessions if start <= day <= end)
        spy_bars = tuple(
            item
            for item in bars
            if item.asset_key.casefold() == "spy" and start <= item.session_date <= end
        )
        if not spy_bars:
            raise _data("SPY Research Benchmark is unavailable")
        spy_id = spy_bars[0].asset_id
        target = TargetDecision(
            first_decision,
            (TargetAssetWeight(spy_id, spy_bars[0].asset_key, ONE),),
            ZERO,
        )
        targets = map_execution_dates((target,), filtered_sessions, simulation_end=end)
        gross = calculate_gross_portfolio_path(
            bars=spy_bars,
            reserve_intervals=reserve,
            targets=targets,
            common_sessions=filtered_sessions,
            simulation_end=end,
        )
        status, points, audit = self._net_path_with_costs(
            context, gross.daily_nav, gross.executions, gross.trades, raw
        )
        if status != "accepted" and context["suite_mode"] != "exploratory":
            raise _data("SPY Research Benchmark failed capacity/accounting", status=status)
        return points, audit

    def _research_benchmark_path(
        self,
        *,
        context: dict[str, Any],
        selected: list[dict[str, Any]],
        asset_lookup: dict[str, uuid.UUID],
        decision_dates: tuple[date, ...],
        raw: list[dict[str, Any]],
        bars: tuple[AccountingMarketBar, ...],
        reserve: tuple[AccountingReserveInterval, ...],
        sessions: tuple[date, ...],
        start: date,
        end: date,
    ) -> tuple[list[tuple[date, Decimal, Decimal]], list[dict[str, Any]]]:
        """PIT equal-weight Research Benchmark on the strategy decision schedule."""
        selected_keys = tuple(item["asset_key"] for item in selected)
        decisions: list[TargetDecision] = []
        for day in decision_dates:
            eligibility = (
                {item["asset_id"]: item["asset_key"] in asset_lookup for item in selected}
                if context["suite_mode"] == "exploratory"
                else self._pit_eligibility(
                    selected,
                    day,
                    uuid.UUID(context["pit_gate_artifact_id"]),
                    uuid.UUID(context["terminal_gate_artifact_id"]),
                )
            )
            eligible_keys = tuple(
                item["asset_key"]
                for item in selected
                if eligibility.get(item["asset_id"], False) and item["asset_key"] in asset_lookup
            )
            if not eligible_keys:
                raise _data("Research Benchmark has no PIT-eligible assets", decision_date=day)
            weight = ONE / Decimal(len(eligible_keys))
            exact_weights = {key: weight for key in eligible_keys}
            exact_weights[min(eligible_keys)] += ONE - sum(exact_weights.values(), ZERO)
            decisions.append(
                TargetDecision(
                    day,
                    tuple(
                        TargetAssetWeight(
                            asset_lookup[key], key, exact_weights.get(key, ZERO)
                        )
                        for key in selected_keys
                    ),
                    ZERO,
                )
            )
        filtered_sessions = tuple(day for day in sessions if start <= day <= end)
        targets = map_execution_dates(tuple(decisions), filtered_sessions, simulation_end=end)
        selected_ids = {asset_lookup[key] for key in selected_keys}
        gross = calculate_gross_portfolio_path(
            bars=tuple(
                item
                for item in bars
                if item.asset_id in selected_ids and start <= item.session_date <= end
            ),
            reserve_intervals=reserve,
            targets=targets,
            common_sessions=filtered_sessions,
            simulation_end=end,
        )
        status, points, audit = self._net_path_with_costs(
            context, gross.daily_nav, gross.executions, gross.trades, raw
        )
        if status != "accepted" and context["suite_mode"] != "exploratory":
            raise _data("Research Benchmark failed capacity/accounting", status=status)
        return points, audit


def build_v021_worker(engine: Engine, *, worker_id: str) -> WorkItemWorker:
    executor = V021DatabaseExecutor(engine)
    handlers: dict[ResultType, Callable[[CellExecutionRequest], CellExecutionOutput]] = {
        "predictive": executor.execute_predictive,
        "portfolio": executor.execute_portfolio,
    }
    return WorkItemWorker(engine, worker_id=worker_id, handlers=handlers)


def _contract(message: str) -> ClassifiedWorkFailure:
    return ClassifiedWorkFailure(WorkFailureClass.CONTRACT, message)


def _data(message: str, **details: Any) -> ClassifiedWorkFailure:
    return ClassifiedWorkFailure(WorkFailureClass.DATA_QUALITY, message, details=details)


def _aggregate_scores(
    identities: list[tuple[uuid.UUID, date]],
    *,
    keys: tuple[str, ...],
    signals: dict[str, dict[tuple[uuid.UUID, date], tuple[str, Decimal]]],
    dimensions: dict[str, str],
    weighting: str,
    signal_metadata: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    by_date: dict[date, list[uuid.UUID]] = defaultdict(list)
    for asset_id, observation_date in identities:
        by_date[observation_date].append(asset_id)
    for observation_date in sorted(by_date):
        assets = sorted(set(by_date[observation_date]), key=str)
        normalized = {
            key: _centered_percentile_ranks(
                {asset_id: signals[key][(asset_id, observation_date)][1] for asset_id in assets}
            )
            for key in keys
        }
        dimension_counts = {
            dimension: sum(dimensions[key] == dimension for key in keys)
            for dimension in set(dimensions.values())
        }
        for asset_id in assets:
            if weighting == "equal_by_active_dimension_then_signal":
                contributions = {
                    key: normalized[key][asset_id]
                    / Decimal(dimension_counts[dimensions[key]])
                    / Decimal(len(dimension_counts))
                    for key in keys
                }
            else:
                contributions = {
                    key: normalized[key][asset_id] / Decimal(len(keys)) for key in keys
                }
            score = sum(contributions.values(), ZERO)
            output.append(
                {
                    "asset_id": str(asset_id),
                    "asset_key": signals[keys[0]][(asset_id, observation_date)][0],
                    "observation_date": observation_date.isoformat(),
                    "score": str(score),
                }
            )
            audit.append(
                {
                    "asset_id": str(asset_id),
                    "asset_key": signals[keys[0]][(asset_id, observation_date)][0],
                    "observation_date": observation_date.isoformat(),
                    "common_asset_count": len(assets),
                    "normalization_policy": "branch_local_centered_percentile_rank_-1_1",
                    "tie_policy": "average_rank",
                    "inputs": [
                        {
                            "signal_version_key": key,
                            "dimension_key": dimensions[key],
                            "signal_version_artifact_id": signal_metadata[key][
                                "signal_version_artifact_id"
                            ],
                            "signal_dataset_artifact_id": signal_metadata[key].get(
                                "materialization_artifact_id",
                                signal_metadata[key]["signal_version_artifact_id"],
                            ),
                            "materialization_cache_key": signal_metadata[key].get(
                                "materialization_cache_key"
                            ),
                            "factor_variant_artifact_id": signal_metadata[key][
                                "factor_variant_artifact_id"
                            ],
                            "raw_signal_value": str(signals[key][(asset_id, observation_date)][1]),
                            "normalized_input_value": str(normalized[key][asset_id]),
                            "contribution": str(contributions[key]),
                        }
                        for key in keys
                    ],
                    "model_score": str(score),
                }
            )
    return output, audit


def _centered_percentile_ranks(values: dict[uuid.UUID, Decimal]) -> dict[uuid.UUID, Decimal]:
    if len(values) == 1:
        return {next(iter(values)): ZERO}
    ordered = sorted(values.values())
    ranks: dict[Decimal, Decimal] = {}
    for value in set(ordered):
        positions = [index + 1 for index, item in enumerate(ordered) if item == value]
        ranks[value] = Decimal(sum(positions)) / Decimal(len(positions))
    denominator = Decimal(len(values) - 1)
    return {
        asset_id: Decimal(2) * (ranks[value] - ONE) / denominator - ONE
        for asset_id, value in values.items()
    }


def _check(key: str, status: str) -> dict[str, Any]:
    return {
        "check_key": key,
        "scope_key": "v021_cell",
        "status": status,
        "severity": (
            "info" if status in {"passed", "accepted"}
            else "warning" if status == "warning"
            else "error"
        ),
        "message": key,
    }


def _mean_domain_metrics(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    keys = sorted(
        set().union(*(set(row) for row in rows)).difference({"decision_date", "asset_count"})
    )
    output: dict[str, float | None] = {}
    for key in keys:
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        output[f"mean_{key}"] = statistics.fmean(values) if values else None
    return output


def _window_start(window: str, start: date, end: date) -> date:
    years = 3 if window == "trailing_3_years" else 1 if window == "trailing_1_year" else None
    if years is None:
        return start
    try:
        return max(start, end.replace(year=end.year - years))
    except ValueError:
        return max(start, end.replace(year=end.year - years, day=28))


def _defense_budget(mode: str, day: date, spy: list[dict[str, Any]]) -> Decimal:
    if mode == "none":
        return ZERO
    if mode == "fixed_20":
        return Decimal("0.2")
    prior = [row["close_adj"] for row in spy if row["session_date"] <= day]
    if len(prior) < 200:
        raise _data("internal_timing_v1 requires 200 SPY sessions")
    return internal_timing_defense_budget(
        spy_close=prior[-1], spy_sma200=sum(prior[-200:], ZERO) / Decimal(200)
    )


def _spy_history(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in raw if row["asset_key"].casefold() == "spy"]


def _scheduled_decision_dates(
    score_dates: tuple[date, ...], sessions: tuple[date, ...], frequency: str, end: date
) -> tuple[date, ...]:
    session_index = {day: index for index, day in enumerate(sessions)}
    legal: list[date] = []
    for day in sorted(set(score_dates)):
        index = session_index.get(day)
        if index is None or index + 1 >= len(sessions) or sessions[index + 1] > end:
            continue
        next_day = sessions[index + 1]
        if frequency == "weekly":
            is_boundary = day.isocalendar()[:2] != next_day.isocalendar()[:2]
        elif frequency == "monthly":
            is_boundary = (day.year, day.month) != (next_day.year, next_day.month)
        else:
            raise _contract(f"Unsupported rebalance frequency: {frequency}")
        if is_boundary:
            legal.append(day)
    return tuple(legal)


def _defensive_allocations(
    budget: Decimal,
    version: str | None,
    *,
    available_assets: set[str],
    allow_reserve_fallback: bool = False,
) -> tuple[dict[str, Decimal], Decimal]:
    if budget == ZERO:
        return {}, ZERO
    if version == "standard_defensive_basket_long_history_v1":
        component_shares = {"IEF": "0.25", "TLT": "0.10", "TIP": "0.15", "IAU": "0.10"}
        reserve_share = Decimal("0.40")
    elif version == "standard_defensive_basket_tradable_v1":
        component_shares = {
            "SGOV": "0.40",
            "IEF": "0.25",
            "TLT": "0.10",
            "TIP": "0.15",
            "IAU": "0.10",
        }
        reserve_share = ZERO
    else:
        raise _contract("Execution Policy has no recognized frozen Defensive Basket version")
    missing = sorted(set(component_shares).difference(available_assets))
    if missing:
        if allow_reserve_fallback:
            return {}, budget
        raise _data("Frozen Defensive Basket is incomplete in the Data Bundle", missing=missing)
    return (
        {key: budget * Decimal(share) for key, share in component_shares.items()},
        budget * reserve_share,
    )


def _risk_free_returns(
    intervals: tuple[AccountingReserveInterval, ...], dates: tuple[date, ...]
) -> tuple[Decimal, ...]:
    return tuple(
        next(
            (
                item.accrual_factor - ONE
                for item in intervals
                if item.interval_start <= day < item.interval_end
            ),
            ZERO,
        )
        for day in dates
    )


def _interval_series(points: list[tuple[date, Decimal, Decimal]]) -> IntervalSeries:
    values = tuple(SeriesPoint(day, daily_return, nav) for day, daily_return, nav in points)
    interval = ResolvedInterval(
        "custom",
        values[-1].nav_date,
        values[0].nav_date,
        values[-1].nav_date,
        values[0].nav_date,
        values[-1].nav_date,
        "fresh_start",
        values[0].nav_date,
        values[0].nav_date,
        "eligible",
        None,
    )
    return IntervalSeries(interval, values[0].nav_date, values)


def _metric_value(value: Any) -> float | None:
    return float(value.value) if value.value is not None else None


def _nav_series(
    strategy: list[tuple[date, Decimal, Decimal]],
    benchmark: list[tuple[date, Decimal, Decimal]],
) -> list[dict[str, Any]]:
    peak = ZERO
    output = []
    for left, right in zip(strategy, benchmark, strict=True):
        peak = max(peak, left[2])
        output.append(
            {
                "nav_date": left[0].isoformat(),
                "strategy_wealth": float(left[2]),
                "strategy_currency_nav": float(left[2] * Decimal("100000000")),
                "benchmark_wealth": float(right[2]),
                "benchmark_currency_nav": float(right[2] * Decimal("100000000")),
                "excess_wealth": float(left[2] / right[2]),
                "drawdown": float(left[2] / peak - ONE),
            }
        )
    return output


def _single_nav_series(points: list[tuple[date, Decimal, Decimal]]) -> list[dict[str, Any]]:
    return [
        {
            "nav_date": day.isoformat(),
            "wealth": float(wealth),
            "currency_nav": float(wealth * Decimal("100000000")),
            "daily_return": float(daily_return),
        }
        for day, daily_return, wealth in points
    ]


def _complete_accounting_bar_grid(
    rows: Sequence[Mapping[str, Any]],
    terminal_events: Sequence[Any],
    *,
    allow_unverified_carry: bool = False,
) -> list[dict[str, Any]]:
    """Provide zero-weight pre-listing placeholders and explicit terminal-value bars.

    Pre-listing rows never make an asset eligible; they only let the accounting
    engine carry an exact zero position. Post-terminal rows freeze the realized
    terminal value, so a held security records its terminal return once and can
    be removed at the next legal execution.
    """
    sessions = sorted({row["session_date"] for row in rows})
    by_asset: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_asset[row["asset_id"]].append(dict(row))
    events = {row["asset_id"]: row for row in terminal_events}
    completed: list[dict[str, Any]] = []
    for asset_id, asset_rows in by_asset.items():
        ordered = sorted(asset_rows, key=lambda item: item["session_date"])
        present = {item["session_date"]: item for item in ordered}
        first = ordered[0]
        prior: dict[str, Any] | None = None
        for session in sessions:
            if session in present:
                prior = present[session]
                completed.append(prior)
                continue
            if session < first["session_date"]:
                completed.append(
                    {
                        **first,
                        "session_date": session,
                        "volume_raw": ZERO,
                        "synthetic_reason": "pre_listing_zero_weight_placeholder",
                    }
                )
                continue
            event = events.get(asset_id)
            if event is not None and session >= event["effective_session"]:
                if prior is None:
                    continue
                terminal_return = Decimal(event["terminal_total_return"] or ZERO)
                applies = session == event["effective_session"]
                close_adj = (
                    prior["close_adj"] * (ONE + terminal_return)
                    if applies
                    else prior["close_adj"]
                )
                close_raw = (
                    prior["close_raw"] * (ONE + terminal_return)
                    if applies
                    else prior["close_raw"]
                )
                prior = {
                    **prior,
                    "session_date": session,
                    "open_raw": prior["close_raw"],
                    "open_adj": prior["close_adj"],
                    "close_raw": close_raw,
                    "close_adj": close_adj,
                    "volume_raw": ZERO,
                    "synthetic_reason": "terminal_value_carry",
                    "terminal_event_artifact_id": str(event["artifact_id"]),
                }
                completed.append(prior)
                continue
            if allow_unverified_carry and prior is not None:
                prior = {
                    **prior,
                    "session_date": session,
                    "open_raw": prior["close_raw"],
                    "open_adj": prior["close_adj"],
                    "close_raw": prior["close_raw"],
                    "close_adj": prior["close_adj"],
                    "volume_raw": ZERO,
                    "synthetic_reason": "exploratory_missing_bar_carry",
                }
                completed.append(prior)
    return sorted(completed, key=lambda item: (item["session_date"], item["asset_key"]))
