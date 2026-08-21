from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from style_rotation.experiment.accounting import (
    PortfolioAccountingError,
    calculate_gross_portfolio_path,
    map_execution_dates,
)
from style_rotation.experiment.contracts import (
    AccountingMarketBar,
    AccountingReserveInterval,
    GrossAccountingResult,
    NetCostResult,
    TargetAssetWeight,
    TargetDecision,
)
from style_rotation.experiment.cost_accounting import (
    NetCostAccountingError,
    calculate_net_cost_path,
)
from style_rotation.experiment.intervals import IntervalSeries, ResolvedInterval
from style_rotation.experiment.performance import (
    PerformanceCalculationError,
    calculate_absolute_performance,
    calculate_relative_performance,
)
from style_rotation.metrics.types import MetricValue, SeriesPoint
from style_rotation.v022.defense_runtime import MergedPortfolioTarget
from style_rotation.v022.runtime_contract import (
    V022RuntimeContractError,
    V022RuntimeDataError,
)
from style_rotation.v022.settlement_accounting import (
    RuntimeSettlementInstruction,
    calculate_settlement_aware_gross_portfolio_path,
)

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class PortfolioCellSpec:
    """Frozen Cell identity for normalized-return, linear-bps evaluation.

    ``initial_capital`` is preserved now so a future absolute-capacity evaluator can
    bind the same explicit input.  This first slice intentionally publishes normalized
    wealth and proportional costs only; it does not claim currency P&L or capacity.
    """

    context_fingerprint: str
    simulation_end: date
    cost_bps_per_side: Decimal
    initial_capital: Decimal
    benchmark_asset_id: uuid.UUID
    benchmark_asset_key: str
    simulation_start: date | None = None
    execution_delay_sessions: int = 1

    def __post_init__(self) -> None:
        if len(self.context_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in self.context_fingerprint
        ):
            raise V022RuntimeContractError(
                "portfolio_context_fingerprint_invalid",
                "Portfolio Cell Context fingerprint must be lowercase SHA-256",
            )
        if (
            not self.cost_bps_per_side.is_finite()
            or self.cost_bps_per_side <= ZERO
            or not self.initial_capital.is_finite()
            or self.initial_capital <= ZERO
        ):
            raise V022RuntimeContractError(
                "portfolio_cell_numeric_policy_invalid",
                "Portfolio Cell cost and initial capital must be finite and positive",
            )
        if not self.benchmark_asset_key.strip():
            raise V022RuntimeContractError(
                "portfolio_benchmark_key_blank", "Benchmark Asset key must be nonblank"
            )
        if self.execution_delay_sessions < 1:
            raise V022RuntimeContractError(
                "portfolio_execution_delay_invalid",
                "Portfolio execution delay must be at least one common session",
            )
        if self.simulation_start is not None and self.simulation_start > self.simulation_end:
            raise V022RuntimeContractError(
                "portfolio_simulation_range_invalid",
                "Portfolio simulation start must not follow its end",
            )


@dataclass(frozen=True, slots=True)
class PortfolioCellEvaluation:
    gross: GrossAccountingResult
    net: NetCostResult
    benchmark_gross: GrossAccountingResult
    benchmark_net: NetCostResult
    absolute_metrics: dict[str, MetricValue]
    relative_metrics: dict[str, MetricValue]

    def __post_init__(self) -> None:
        gross_dates = tuple(item.nav_date for item in self.gross.daily_nav)
        net_dates = tuple(item.nav_date for item in self.net.daily_nav)
        benchmark_gross_dates = tuple(item.nav_date for item in self.benchmark_gross.daily_nav)
        benchmark_net_dates = tuple(item.nav_date for item in self.benchmark_net.daily_nav)
        if not gross_dates or not (
            gross_dates == net_dates == benchmark_gross_dates == benchmark_net_dates
        ):
            raise V022RuntimeContractError(
                "portfolio_cell_path_alignment_invalid",
                "Strategy and benchmark Gross/Net paths must align exactly",
            )
        if not self.absolute_metrics or not self.relative_metrics:
            raise V022RuntimeContractError(
                "portfolio_cell_metrics_empty",
                "Portfolio Cell evaluation must publish absolute and relative metrics",
            )


def evaluate_portfolio_cell(
    spec: PortfolioCellSpec,
    *,
    targets: tuple[MergedPortfolioTarget, ...],
    bars: tuple[AccountingMarketBar, ...],
    reserve_intervals: tuple[AccountingReserveInterval, ...],
    common_sessions: tuple[date, ...],
    settlements: tuple[RuntimeSettlementInstruction, ...] = (),
) -> PortfolioCellEvaluation:
    """Evaluate one normalized-return Cell without persistence or hidden fallback."""

    _validate_target_path(targets, simulation_end=spec.simulation_end)
    _validate_common_sessions(common_sessions)
    if common_sessions[-1] != spec.simulation_end:
        raise V022RuntimeDataError(
            "portfolio_simulation_end_unavailable",
            "Exact common-session input must end on the frozen simulation end",
            details={
                "latest_common_session": common_sessions[-1].isoformat(),
                "simulation_end": spec.simulation_end.isoformat(),
            },
        )
    _validate_reserve_inputs(reserve_intervals)
    assets = _asset_identities(bars)
    benchmark_key = assets.get(spec.benchmark_asset_id)
    if benchmark_key != spec.benchmark_asset_key:
        raise V022RuntimeDataError(
            "portfolio_benchmark_missing",
            "Exact market input does not contain the frozen benchmark identity",
        )
    final_executable_index = len(common_sessions) - 1 - spec.execution_delay_sessions
    if final_executable_index < 0:
        raise V022RuntimeDataError(
            "portfolio_execution_window_empty",
            "Common-session input has no executable decision window",
        )
    final_executable_decision = common_sessions[final_executable_index]
    decisions = tuple(
        _target_decision(item, assets)
        for item in targets
        if item.decision_date <= final_executable_decision
    )
    if not decisions:
        raise V022RuntimeDataError(
            "portfolio_execution_window_empty",
            "Target path has no decision with an execution session inside the frozen range",
        )
    annualization_start = spec.simulation_start or targets[0].decision_date
    if spec.simulation_start is not None:
        try:
            start_index = common_sessions.index(spec.simulation_start)
        except ValueError as error:
            raise V022RuntimeDataError(
                "portfolio_simulation_start_unavailable",
                "Frozen simulation start is not a common session",
            ) from error
        if start_index < spec.execution_delay_sessions:
            raise V022RuntimeDataError(
                "portfolio_simulation_start_unexecutable",
                "Frozen simulation start has no prior decision session",
            )
        initial_decision_date = common_sessions[
            start_index - spec.execution_delay_sessions
        ]
        if all(item.decision_date != initial_decision_date for item in decisions):
            decisions = (_reserve_decision(initial_decision_date, assets),) + decisions
    else:
        initial_decision_date = targets[0].decision_date
    benchmark_decision = _benchmark_decision(
        initial_decision_date,
        assets,
        benchmark_asset_id=spec.benchmark_asset_id,
    )
    try:
        executable = map_execution_dates(
            decisions,
            common_sessions,
            delay_common_sessions=spec.execution_delay_sessions,
            simulation_end=spec.simulation_end,
        )
        benchmark_executable = map_execution_dates(
            (benchmark_decision,),
            common_sessions,
            delay_common_sessions=spec.execution_delay_sessions,
            simulation_end=spec.simulation_end,
        )
        gross = (
            calculate_settlement_aware_gross_portfolio_path(
                bars=bars,
                reserve_intervals=reserve_intervals,
                targets=executable,
                common_sessions=common_sessions,
                simulation_end=spec.simulation_end,
                settlements=settlements,
                delay_common_sessions=spec.execution_delay_sessions,
            )
            if settlements
            else calculate_gross_portfolio_path(
                bars=bars,
                reserve_intervals=reserve_intervals,
                targets=executable,
                common_sessions=common_sessions,
                simulation_end=spec.simulation_end,
                delay_common_sessions=spec.execution_delay_sessions,
            )
        )
        benchmark_gross = calculate_gross_portfolio_path(
            bars=bars,
            reserve_intervals=reserve_intervals,
            targets=benchmark_executable,
            common_sessions=common_sessions,
            simulation_end=spec.simulation_end,
            delay_common_sessions=spec.execution_delay_sessions,
        )
        net = calculate_net_cost_path(
            gross_daily_nav=gross.daily_nav,
            executions=gross.executions,
            cost_bps_per_side=spec.cost_bps_per_side,
        )
        benchmark_net = calculate_net_cost_path(
            gross_daily_nav=benchmark_gross.daily_nav,
            executions=benchmark_gross.executions,
            cost_bps_per_side=spec.cost_bps_per_side,
        )
        strategy_series = _net_interval_series(net, annualization_start=annualization_start)
        benchmark_series = _net_interval_series(
            benchmark_net, annualization_start=annualization_start
        )
        risk_free = _risk_free_returns(reserve_intervals, net)
        absolute = calculate_absolute_performance(strategy_series, risk_free)
        relative = calculate_relative_performance(strategy_series, benchmark_series, risk_free)
    except (
        PortfolioAccountingError,
        NetCostAccountingError,
        PerformanceCalculationError,
    ) as error:
        raise V022RuntimeDataError(
            "portfolio_cell_input_invalid",
            str(error),
            details={"context_fingerprint": spec.context_fingerprint},
        ) from error
    return PortfolioCellEvaluation(
        gross=gross,
        net=net,
        benchmark_gross=benchmark_gross,
        benchmark_net=benchmark_net,
        absolute_metrics=absolute.metrics,
        relative_metrics=relative.metrics,
    )


def _validate_target_path(
    targets: tuple[MergedPortfolioTarget, ...], *, simulation_end: date
) -> None:
    if not targets:
        raise V022RuntimeContractError(
            "portfolio_target_path_empty", "Portfolio Cell requires a Target path"
        )
    decision_dates = tuple(item.decision_date for item in targets)
    if decision_dates != tuple(sorted(set(decision_dates))):
        raise V022RuntimeContractError(
            "portfolio_target_order_invalid",
            "Portfolio Targets must have unique sorted Decision dates",
        )
    if any(item.decision_date > simulation_end for item in targets):
        raise V022RuntimeContractError(
            "portfolio_simulation_interval_invalid",
            "Portfolio simulation must end on or after every included Decision date",
        )


def _validate_common_sessions(common_sessions: tuple[date, ...]) -> None:
    if not common_sessions or common_sessions != tuple(sorted(set(common_sessions))):
        raise V022RuntimeContractError(
            "portfolio_common_sessions_invalid",
            "Portfolio common sessions must be nonempty, unique, and sorted",
        )


def _validate_reserve_inputs(
    intervals: tuple[AccountingReserveInterval, ...],
) -> None:
    seen: set[tuple[date, date]] = set()
    for item in intervals:
        key = (item.interval_start, item.interval_end)
        if key in seen:
            raise V022RuntimeDataError(
                "portfolio_reserve_interval_duplicate",
                "Only one reserve input is allowed per exact NAV interval",
            )
        seen.add(key)
        if not item.accrual_factor.is_finite() or item.accrual_factor <= ZERO:
            raise V022RuntimeDataError(
                "portfolio_reserve_factor_invalid",
                "Reserve accrual factors must be finite and positive",
            )


def _asset_identities(bars: tuple[AccountingMarketBar, ...]) -> dict[uuid.UUID, str]:
    if not bars:
        raise V022RuntimeDataError(
            "portfolio_market_bars_empty", "Portfolio Cell requires exact adjusted market bars"
        )
    assets: dict[uuid.UUID, str] = {}
    keys: dict[str, uuid.UUID] = {}
    seen: set[tuple[uuid.UUID, date]] = set()
    for item in bars:
        if not item.asset_key.strip():
            raise V022RuntimeDataError(
                "portfolio_market_asset_key_blank", "Market Asset key must be nonblank"
            )
        if (
            not item.adjusted_open.is_finite()
            or not item.adjusted_close.is_finite()
            or item.adjusted_open <= ZERO
            or item.adjusted_close <= ZERO
        ):
            raise V022RuntimeDataError(
                "portfolio_market_price_invalid",
                "Adjusted market prices must be finite and positive",
                details={
                    "asset_key": item.asset_key,
                    "session_date": item.session_date.isoformat(),
                },
            )
        asset_session = (item.asset_id, item.session_date)
        if asset_session in seen:
            raise V022RuntimeDataError(
                "portfolio_market_bar_duplicate",
                "Only one adjusted market bar is allowed per Asset-session",
            )
        seen.add(asset_session)
        prior_key = assets.setdefault(item.asset_id, item.asset_key)
        prior_id = keys.setdefault(item.asset_key, item.asset_id)
        if prior_key != item.asset_key or prior_id != item.asset_id:
            raise V022RuntimeDataError(
                "portfolio_market_asset_identity_conflict",
                "Market bars contain conflicting Asset id/key mappings",
            )
    return assets


def _target_decision(
    target: MergedPortfolioTarget, assets: dict[uuid.UUID, str]
) -> TargetDecision:
    by_asset = {item.asset_id: item for item in target.net_asset_weights}
    missing = tuple(
        item.asset_key for item in target.net_asset_weights if item.asset_id not in assets
    )
    mismatched = tuple(
        item.asset_key
        for item in target.net_asset_weights
        if item.asset_id in assets and assets[item.asset_id] != item.asset_key
    )
    if missing or mismatched:
        raise V022RuntimeDataError(
            "portfolio_target_asset_missing",
            "Target Asset identity is absent or inconsistent in exact market input",
            details={"missing_asset_keys": missing, "mismatched_asset_keys": mismatched},
        )
    return TargetDecision(
        decision_date=target.decision_date,
        asset_weights=tuple(
            TargetAssetWeight(
                asset_id=asset_id,
                asset_key=asset_key,
                target_weight=(
                    by_asset[asset_id].target_weight if asset_id in by_asset else ZERO
                ),
            )
            for asset_id, asset_key in sorted(
                assets.items(), key=lambda item: (item[1], str(item[0]))
            )
        ),
        reserve_target_weight=target.reserve_target_weight,
    )


def _benchmark_decision(
    decision_date: date,
    assets: dict[uuid.UUID, str],
    *,
    benchmark_asset_id: uuid.UUID,
) -> TargetDecision:
    return TargetDecision(
        decision_date=decision_date,
        asset_weights=tuple(
            TargetAssetWeight(
                asset_id=asset_id,
                asset_key=asset_key,
                target_weight=Decimal(1) if asset_id == benchmark_asset_id else ZERO,
            )
            for asset_id, asset_key in sorted(
                assets.items(), key=lambda item: (item[1], str(item[0]))
            )
        ),
        reserve_target_weight=ZERO,
    )


def _reserve_decision(
    decision_date: date, assets: dict[uuid.UUID, str]
) -> TargetDecision:
    return TargetDecision(
        decision_date=decision_date,
        asset_weights=tuple(
            TargetAssetWeight(asset_id, asset_key, ZERO)
            for asset_id, asset_key in sorted(
                assets.items(), key=lambda item: (item[1], str(item[0]))
            )
        ),
        reserve_target_weight=Decimal(1),
    )


def _net_interval_series(
    net: NetCostResult, *, annualization_start: date
) -> IntervalSeries:
    points = tuple(
        SeriesPoint(item.nav_date, item.net_daily_return, item.net_nav) for item in net.daily_nav
    )
    interval = ResolvedInterval(
        template_key="custom",
        as_of_date=points[-1].nav_date,
        requested_start=points[0].nav_date,
        requested_end=points[-1].nav_date,
        resolved_start=points[0].nav_date,
        resolved_end=points[-1].nav_date,
        initialization_policy="fresh_start",
        underlying_simulation_start=points[0].nav_date,
        normalization_nav_date=None,
        availability_status="eligible",
        exclusion_reason=None,
    )
    return IntervalSeries(interval, annualization_start, points)


def _risk_free_returns(
    intervals: tuple[AccountingReserveInterval, ...], net: NetCostResult
) -> tuple[Decimal, ...]:
    by_interval: dict[tuple[date, date], AccountingReserveInterval] = {}
    for item in intervals:
        key = (item.interval_start, item.interval_end)
        if key in by_interval:
            raise V022RuntimeDataError(
                "portfolio_reserve_interval_duplicate",
                "Only one reserve input is allowed per exact NAV interval",
            )
        by_interval[key] = item
    dates = tuple(item.nav_date for item in net.daily_nav)
    values = [ZERO]
    for previous, current in zip(dates, dates[1:], strict=False):
        interval = by_interval.get((previous, current))
        if interval is None:
            raise V022RuntimeDataError(
                "portfolio_reserve_interval_missing",
                "Risk-free performance input requires every exact NAV interval",
                details={
                    "interval_start": previous.isoformat(),
                    "interval_end": current.isoformat(),
                },
            )
        values.append(interval.accrual_factor - Decimal(1))
    return tuple(values)
