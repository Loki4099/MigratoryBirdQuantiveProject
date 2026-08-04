from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext

from style_rotation.experiment.contracts import (
    AccountingMarketBar,
    AccountingReserveInterval,
    DailyAssetPosition,
    DailyReservePosition,
    ExecutableTarget,
    GrossAccountingResult,
    GrossDailyNav,
    PortfolioExecution,
    PortfolioTrade,
    TargetDecision,
)

ZERO = Decimal(0)
ONE = Decimal(1)


class PortfolioAccountingError(RuntimeError):
    """Raised when formal v0.2 portfolio accounting inputs are incomplete or inconsistent."""


def map_execution_dates(
    decisions: tuple[TargetDecision, ...],
    common_sessions: tuple[date, ...],
    *,
    delay_common_sessions: int = 1,
    simulation_end: date,
) -> tuple[ExecutableTarget, ...]:
    if delay_common_sessions < 1:
        raise ValueError("Execution delay must be at least one common session")
    sessions = tuple(sorted(set(common_sessions)))
    if not sessions:
        raise PortfolioAccountingError("Common trading sessions are required")
    session_index = {session: index for index, session in enumerate(sessions)}
    if len({item.decision_date for item in decisions}) != len(decisions):
        raise PortfolioAccountingError("Only one target decision is allowed per date")
    if tuple(item.decision_date for item in decisions) != tuple(
        sorted(item.decision_date for item in decisions)
    ):
        raise PortfolioAccountingError("Target decisions must be sorted")
    mapped: list[ExecutableTarget] = []
    for decision in decisions:
        index = session_index.get(decision.decision_date)
        if index is None:
            raise PortfolioAccountingError("Every decision date must be a common trading session")
        execution_index = index + delay_common_sessions
        if execution_index >= len(sessions):
            raise PortfolioAccountingError("A decision has no required future execution session")
        execution_date = sessions[execution_index]
        if execution_date > simulation_end:
            continue
        mapped.append(
            ExecutableTarget(
                decision.decision_date,
                execution_date,
                decision.asset_weights,
                decision.reserve_target_weight,
            )
        )
    if not mapped:
        raise PortfolioAccountingError("No target can execute inside the simulation interval")
    return tuple(mapped)


def calculate_gross_portfolio_path(
    *,
    bars: tuple[AccountingMarketBar, ...],
    reserve_intervals: tuple[AccountingReserveInterval, ...],
    targets: tuple[ExecutableTarget, ...],
    common_sessions: tuple[date, ...],
    simulation_end: date,
    delay_common_sessions: int = 1,
) -> GrossAccountingResult:
    if not targets:
        raise PortfolioAccountingError("At least one executable target is required")
    if delay_common_sessions < 1:
        raise ValueError("Execution delay must be at least one common session")
    assets, bars_by_asset = _validate_bars(bars)
    all_sessions = tuple(sorted(set(common_sessions)))
    ordered_sessions = tuple(
        session
        for session in all_sessions
        if targets[0].execution_date <= session <= simulation_end
    )
    _validate_targets(
        targets,
        assets,
        all_sessions,
        ordered_sessions,
        delay_common_sessions=delay_common_sessions,
    )
    _validate_bar_coverage(bars_by_asset, assets, ordered_sessions)
    reserve_by_interval = _validate_reserve(reserve_intervals, ordered_sessions)
    target_by_date = {item.execution_date: item for item in targets}

    with localcontext() as context:
        context.prec = 40
        gross_nav = ONE
        prior_close_weights = {asset_id: ZERO for asset_id in assets}
        reserve_close_weight = ONE
        previous_session: date | None = None
        daily_nav: list[GrossDailyNav] = []
        asset_positions: list[DailyAssetPosition] = []
        reserve_positions: list[DailyReservePosition] = []
        executions: list[PortfolioExecution] = []
        trades: list[PortfolioTrade] = []

        for session in ordered_sessions:
            prior_nav = gross_nav
            reserve_interval: AccountingReserveInterval | None = None
            if previous_session is None:
                open_notionals = dict(prior_close_weights)
                reserve_open_notional = reserve_close_weight
            else:
                open_notionals = {
                    asset_id: prior_close_weights[asset_id]
                    * bars_by_asset[asset_id][session].adjusted_open
                    / bars_by_asset[asset_id][previous_session].adjusted_close
                    for asset_id in assets
                }
                reserve_interval = reserve_by_interval[(previous_session, session)]
                reserve_open_notional = reserve_close_weight * reserve_interval.accrual_factor
            overnight_factor = sum(open_notionals.values(), reserve_open_notional)
            if overnight_factor <= 0:
                raise PortfolioAccountingError("Gross portfolio value must remain positive")
            gross_pretrade_nav = gross_nav * overnight_factor
            pretrade_asset_weights = {
                asset_id: notional / overnight_factor
                for asset_id, notional in open_notionals.items()
            }
            pretrade_reserve_weight = reserve_open_notional / overnight_factor
            target = target_by_date.get(session)
            if target is None:
                posttrade_asset_weights = pretrade_asset_weights
                posttrade_reserve_weight = pretrade_reserve_weight
            else:
                target_weights = {
                    item.asset_id: item.target_weight for item in target.asset_weights
                }
                asset_changes = {
                    asset_id: target_weights[asset_id] - pretrade_asset_weights[asset_id]
                    for asset_id in assets
                }
                reserve_change = target.reserve_target_weight - pretrade_reserve_weight
                gross_traded_fraction = sum(
                    (abs(change) for change in asset_changes.values()), ZERO
                )
                one_way_turnover = (gross_traded_fraction + abs(reserve_change)) / Decimal(2)
                executions.append(
                    PortfolioExecution(
                        target.decision_date,
                        target.execution_date,
                        gross_pretrade_nav,
                        one_way_turnover,
                        gross_traded_fraction,
                        pretrade_reserve_weight,
                        target.reserve_target_weight,
                    )
                )
                for asset_id, asset_key in assets.items():
                    change = asset_changes[asset_id]
                    trades.append(
                        PortfolioTrade(
                            target.decision_date,
                            target.execution_date,
                            asset_id,
                            asset_key,
                            "buy" if change > 0 else "sell" if change < 0 else "none",
                            bars_by_asset[asset_id][session].adjusted_open,
                            pretrade_asset_weights[asset_id],
                            target_weights[asset_id],
                            change,
                            abs(change),
                        )
                    )
                posttrade_asset_weights = target_weights
                posttrade_reserve_weight = target.reserve_target_weight

            close_notionals = {
                asset_id: posttrade_asset_weights[asset_id]
                * bars_by_asset[asset_id][session].adjusted_close
                / bars_by_asset[asset_id][session].adjusted_open
                for asset_id in assets
            }
            reserve_close_notional = posttrade_reserve_weight
            intraday_factor = sum(close_notionals.values(), reserve_close_notional)
            if intraday_factor <= 0:
                raise PortfolioAccountingError("Gross portfolio value must remain positive")
            gross_nav = gross_pretrade_nav * intraday_factor
            prior_close_weights = {
                asset_id: notional / intraday_factor
                for asset_id, notional in close_notionals.items()
            }
            reserve_close_weight = reserve_close_notional / intraday_factor
            _assert_budget(prior_close_weights, reserve_close_weight)
            daily_nav.append(
                GrossDailyNav(
                    session,
                    gross_nav / prior_nav - ONE,
                    gross_nav,
                    overnight_factor,
                    intraday_factor,
                )
            )
            asset_positions.extend(
                DailyAssetPosition(session, asset_id, assets[asset_id], weight)
                for asset_id, weight in prior_close_weights.items()
            )
            reserve_positions.append(
                DailyReservePosition(
                    session,
                    reserve_close_weight,
                    (
                        reserve_interval.source_observation_date
                        if reserve_interval is not None
                        else None
                    ),
                    (
                        reserve_interval.source_available_date
                        if reserve_interval is not None
                        else None
                    ),
                    reserve_interval.quality_status if reserve_interval else "not_applicable",
                )
            )
            previous_session = session

    return GrossAccountingResult(
        targets[0].decision_date,
        targets[0].execution_date,
        ordered_sessions[0],
        ordered_sessions[-1],
        tuple(daily_nav),
        tuple(asset_positions),
        tuple(reserve_positions),
        tuple(executions),
        tuple(trades),
    )


def _validate_bars(
    bars: tuple[AccountingMarketBar, ...],
) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, dict[date, AccountingMarketBar]]]:
    if not bars:
        raise PortfolioAccountingError("Adjusted market bars are required")
    assets: dict[uuid.UUID, str] = {}
    grouped: dict[uuid.UUID, dict[date, AccountingMarketBar]] = defaultdict(dict)
    for bar in bars:
        if bar.adjusted_open <= 0 or bar.adjusted_close <= 0:
            raise PortfolioAccountingError("Adjusted open and close prices must be positive")
        existing_key = assets.setdefault(bar.asset_id, bar.asset_key)
        if existing_key != bar.asset_key:
            raise PortfolioAccountingError("Asset identity and key mapping must be stable")
        if bar.session_date in grouped[bar.asset_id]:
            raise PortfolioAccountingError("Only one market bar is allowed per asset-session")
        grouped[bar.asset_id][bar.session_date] = bar
    return assets, dict(grouped)


def _validate_targets(
    targets: tuple[ExecutableTarget, ...],
    assets: dict[uuid.UUID, str],
    all_sessions: tuple[date, ...],
    active_sessions: tuple[date, ...],
    *,
    delay_common_sessions: int,
) -> None:
    if not active_sessions or active_sessions[0] != targets[0].execution_date:
        raise PortfolioAccountingError("Simulation must start on the first execution date")
    session_index = {session: index for index, session in enumerate(all_sessions)}
    execution_dates = tuple(item.execution_date for item in targets)
    if execution_dates != tuple(sorted(execution_dates)) or len(set(execution_dates)) != len(
        execution_dates
    ):
        raise PortfolioAccountingError("Executable targets must have unique sorted dates")
    for target in targets:
        decision_index = session_index.get(target.decision_date)
        execution_index = session_index.get(target.execution_date)
        if decision_index is None or execution_index is None:
            raise PortfolioAccountingError("Decision and execution must be common sessions")
        if execution_index != decision_index + delay_common_sessions:
            raise PortfolioAccountingError("Execution date does not match the frozen delay policy")
        if target.execution_date not in active_sessions:
            raise PortfolioAccountingError("Every execution must be inside the simulation sessions")
        weights = {item.asset_id: item for item in target.asset_weights}
        if len(weights) != len(target.asset_weights) or set(weights) != set(assets):
            raise PortfolioAccountingError("Every target must contain each configured asset once")
        if any(item.asset_key != assets[item.asset_id] for item in target.asset_weights):
            raise PortfolioAccountingError("Target asset keys do not match market data identities")
        all_weights = [item.target_weight for item in target.asset_weights]
        all_weights.append(target.reserve_target_weight)
        if any(weight < 0 or weight > 1 for weight in all_weights):
            raise PortfolioAccountingError("Target weights must be between zero and one")
        if sum(all_weights, ZERO) != ONE:
            raise PortfolioAccountingError("Target asset and reserve weights must sum to one")


def _validate_bar_coverage(
    bars: dict[uuid.UUID, dict[date, AccountingMarketBar]],
    assets: dict[uuid.UUID, str],
    sessions: tuple[date, ...],
) -> None:
    expected = set(sessions)
    if any(set(bars[asset_id]).intersection(expected) != expected for asset_id in assets):
        raise PortfolioAccountingError("Every asset requires an adjusted bar on each NAV session")


def _validate_reserve(
    intervals: tuple[AccountingReserveInterval, ...], sessions: tuple[date, ...]
) -> dict[tuple[date, date], AccountingReserveInterval]:
    by_interval: dict[tuple[date, date], AccountingReserveInterval] = {}
    for interval in intervals:
        key = (interval.interval_start, interval.interval_end)
        if key in by_interval:
            raise PortfolioAccountingError("Only one reserve factor is allowed per interval")
        if interval.accrual_factor <= 0:
            raise PortfolioAccountingError("Reserve accrual factors must be positive")
        if interval.source_available_date > interval.interval_start:
            raise PortfolioAccountingError("Reserve rates must be available by interval start")
        by_interval[key] = interval
    required = set(zip(sessions, sessions[1:], strict=False))
    if not required.issubset(by_interval):
        raise PortfolioAccountingError("A reserve factor is required between every NAV session")
    return by_interval


def _assert_budget(asset_weights: dict[uuid.UUID, Decimal], reserve_weight: Decimal) -> None:
    total = sum(asset_weights.values(), reserve_weight)
    if abs(total - ONE) > Decimal("1e-30"):
        raise PortfolioAccountingError("Closing asset and reserve weights must sum to one")
