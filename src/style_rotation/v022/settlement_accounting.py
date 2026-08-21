from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Literal

from style_rotation.experiment.accounting import PortfolioAccountingError
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
)

ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class RuntimeSettlementLeg:
    leg_kind: Literal["cash", "successor_security", "distributed_security", "writeoff"]
    target_asset_id: uuid.UUID | None = None
    target_asset_key: str | None = None
    quantity_per_source_share: Decimal | None = None
    cash_amount_per_source_share: Decimal | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeSettlementInstruction:
    source_asset_id: uuid.UUID
    source_asset_key: str
    event_type: str
    settlement_session: date
    legs: tuple[RuntimeSettlementLeg, ...]


def calculate_settlement_aware_gross_portfolio_path(
    *,
    bars: tuple[AccountingMarketBar, ...],
    reserve_intervals: tuple[AccountingReserveInterval, ...],
    targets: tuple[ExecutableTarget, ...],
    common_sessions: tuple[date, ...],
    simulation_end: date,
    settlements: tuple[RuntimeSettlementInstruction, ...],
    delay_common_sessions: int = 1,
) -> GrossAccountingResult:
    """Run normalized gross accounting with frozen, cost-free corporate settlements."""

    if not targets:
        raise PortfolioAccountingError("At least one executable target is required")
    assets, bars_by_asset = _bars(bars)
    all_sessions = tuple(sorted(set(common_sessions)))
    ordered_sessions = tuple(
        session
        for session in all_sessions
        if targets[0].execution_date <= session <= simulation_end
    )
    _targets(targets, assets, all_sessions, ordered_sessions, delay_common_sessions)
    _coverage(bars_by_asset, assets, ordered_sessions)
    reserve_by_interval = _reserve(reserve_intervals, ordered_sessions)
    settlements_by_date = _settlements(settlements, assets, all_sessions, bars_by_asset)
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
                for instruction in settlements_by_date.get(session, ()):
                    source_shares = (
                        prior_close_weights[instruction.source_asset_id]
                        / bars_by_asset[instruction.source_asset_id][
                            previous_session
                        ].adjusted_close
                    )
                    if instruction.event_type != "spinoff":
                        open_notionals[instruction.source_asset_id] = ZERO
                    for leg in instruction.legs:
                        if leg.leg_kind == "cash":
                            assert leg.cash_amount_per_source_share is not None
                            reserve_open_notional += (
                                source_shares * leg.cash_amount_per_source_share
                            )
                        elif leg.leg_kind in {
                            "successor_security",
                            "distributed_security",
                        }:
                            assert leg.target_asset_id is not None
                            assert leg.quantity_per_source_share is not None
                            open_notionals[leg.target_asset_id] += (
                                source_shares
                                * leg.quantity_per_source_share
                                * bars_by_asset[leg.target_asset_id][session].adjusted_open
                            )

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
                one_way_turnover = (
                    gross_traded_fraction + abs(reserve_change)
                ) / Decimal(2)
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
            _budget(prior_close_weights, reserve_close_weight)
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
                    reserve_interval.source_observation_date if reserve_interval else None,
                    reserve_interval.source_available_date if reserve_interval else None,
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


def _bars(
    bars: tuple[AccountingMarketBar, ...],
) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, dict[date, AccountingMarketBar]]]:
    if not bars:
        raise PortfolioAccountingError("Adjusted market bars are required")
    assets: dict[uuid.UUID, str] = {}
    grouped: dict[uuid.UUID, dict[date, AccountingMarketBar]] = defaultdict(dict)
    for bar in bars:
        if (
            not bar.adjusted_open.is_finite()
            or not bar.adjusted_close.is_finite()
            or bar.adjusted_open <= 0
            or bar.adjusted_close <= 0
        ):
            raise PortfolioAccountingError("Adjusted open and close prices must be positive")
        existing = assets.setdefault(bar.asset_id, bar.asset_key)
        if existing != bar.asset_key or bar.session_date in grouped[bar.asset_id]:
            raise PortfolioAccountingError("Market Asset identity or session is not unique")
        grouped[bar.asset_id][bar.session_date] = bar
    return assets, dict(grouped)


def _targets(
    targets: tuple[ExecutableTarget, ...],
    assets: dict[uuid.UUID, str],
    sessions: tuple[date, ...],
    active: tuple[date, ...],
    delay: int,
) -> None:
    if delay < 1 or not active or active[0] != targets[0].execution_date:
        raise PortfolioAccountingError("Settlement accounting execution range is invalid")
    index = {session: ordinal for ordinal, session in enumerate(sessions)}
    if tuple(item.execution_date for item in targets) != tuple(
        sorted({item.execution_date for item in targets})
    ):
        raise PortfolioAccountingError("Executable targets must have unique sorted dates")
    for target in targets:
        if (
            target.decision_date not in index
            or target.execution_date not in index
            or index[target.execution_date] != index[target.decision_date] + delay
        ):
            raise PortfolioAccountingError("Execution date violates the frozen delay")
        weights = {item.asset_id: item for item in target.asset_weights}
        if set(weights) != set(assets) or len(weights) != len(target.asset_weights):
            raise PortfolioAccountingError("Every target must contain each configured asset")
        if any(item.asset_key != assets[item.asset_id] for item in target.asset_weights):
            raise PortfolioAccountingError("Target Asset identities do not match market data")
        values = [item.target_weight for item in target.asset_weights]
        values.append(target.reserve_target_weight)
        if any(not value.is_finite() or value < 0 or value > 1 for value in values):
            raise PortfolioAccountingError("Target weights must be finite and bounded")
        if sum(values, ZERO) != ONE:
            raise PortfolioAccountingError("Target weights must sum to one")


def _coverage(
    bars: dict[uuid.UUID, dict[date, AccountingMarketBar]],
    assets: dict[uuid.UUID, str],
    sessions: tuple[date, ...],
) -> None:
    expected = set(sessions)
    if any(set(bars[asset_id]).intersection(expected) != expected for asset_id in assets):
        raise PortfolioAccountingError("Every Asset requires a bar on each NAV session")


def _reserve(
    intervals: tuple[AccountingReserveInterval, ...], sessions: tuple[date, ...]
) -> dict[tuple[date, date], AccountingReserveInterval]:
    result: dict[tuple[date, date], AccountingReserveInterval] = {}
    for interval in intervals:
        key = (interval.interval_start, interval.interval_end)
        if key in result or interval.accrual_factor <= 0:
            raise PortfolioAccountingError("Reserve intervals are invalid")
        result[key] = interval
    if not set(zip(sessions, sessions[1:], strict=False)).issubset(result):
        raise PortfolioAccountingError("Reserve intervals do not cover the NAV path")
    return result


def _settlements(
    instructions: tuple[RuntimeSettlementInstruction, ...],
    assets: dict[uuid.UUID, str],
    sessions: tuple[date, ...],
    bars: dict[uuid.UUID, dict[date, AccountingMarketBar]],
) -> dict[date, tuple[RuntimeSettlementInstruction, ...]]:
    grouped: dict[date, list[RuntimeSettlementInstruction]] = defaultdict(list)
    seen: set[tuple[uuid.UUID, date]] = set()
    for instruction in instructions:
        key = (instruction.source_asset_id, instruction.settlement_session)
        if key in seen or instruction.settlement_session not in sessions:
            raise PortfolioAccountingError("Settlement identity or session is invalid")
        seen.add(key)
        if assets.get(instruction.source_asset_id) != instruction.source_asset_key:
            raise PortfolioAccountingError("Settlement source Asset identity is invalid")
        if not instruction.legs:
            raise PortfolioAccountingError("Settlement requires at least one leg")
        kinds = {leg.leg_kind for leg in instruction.legs}
        if instruction.event_type == "spinoff" and "distributed_security" not in kinds:
            raise PortfolioAccountingError("Spinoff requires a distributed Security leg")
        for leg in instruction.legs:
            if leg.leg_kind == "cash":
                if (
                    leg.currency != "USD"
                    or leg.cash_amount_per_source_share is None
                    or not leg.cash_amount_per_source_share.is_finite()
                    or leg.cash_amount_per_source_share < 0
                ):
                    raise PortfolioAccountingError("Cash Settlement Leg is invalid")
            elif leg.leg_kind in {"successor_security", "distributed_security"}:
                if (
                    leg.target_asset_id is None
                    or leg.target_asset_key is None
                    or assets.get(leg.target_asset_id) != leg.target_asset_key
                    or leg.quantity_per_source_share is None
                    or not leg.quantity_per_source_share.is_finite()
                    or leg.quantity_per_source_share <= 0
                    or instruction.settlement_session not in bars[leg.target_asset_id]
                ):
                    raise PortfolioAccountingError("Security Settlement Leg is invalid")
            elif leg.leg_kind != "writeoff":
                raise PortfolioAccountingError("Settlement Leg kind is unsupported")
        grouped[instruction.settlement_session].append(instruction)
    return {
        session: tuple(sorted(items, key=lambda item: str(item.source_asset_id)))
        for session, items in grouped.items()
    }


def _budget(asset_weights: dict[uuid.UUID, Decimal], reserve_weight: Decimal) -> None:
    if abs(sum(asset_weights.values(), reserve_weight) - ONE) > Decimal("1e-30"):
        raise PortfolioAccountingError("Closing weights must sum to one")
