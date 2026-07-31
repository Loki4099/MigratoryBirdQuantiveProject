from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext

from style_rotation.backtest.types import (
    BacktestResult,
    DailyNavRecord,
    DailyPositionRecord,
    ExecutionRecord,
    ExecutionTarget,
    TradeRecord,
)
from style_rotation.data.types import CleanMarketPriceRecord, ReserveDailyRecord

RESERVE_SLEEVE = "RESERVE"
ZERO = Decimal(0)
ONE = Decimal(1)


class BacktestQualityError(RuntimeError):
    """Raised when prices, targets, or accounting invariants are incomplete."""


def _normalize(notionals: dict[str, Decimal]) -> dict[str, Decimal]:
    total = sum(notionals.values(), ZERO)
    if total <= 0:
        raise BacktestQualityError("Portfolio value must stay positive")
    return {sleeve: value / total for sleeve, value in notionals.items()}


def _validate_target(target: ExecutionTarget, symbols: tuple[str, ...]) -> None:
    if set(target.asset_weights) != set(symbols):
        raise BacktestQualityError("Every target must contain exactly the configured assets")
    if target.signal_date >= target.execution_date:
        raise BacktestQualityError("Signal date must precede execution date")
    weights = (*target.asset_weights.values(), target.reserve_weight)
    if any(weight < 0 or weight > 1 for weight in weights):
        raise BacktestQualityError("Target weights must be between zero and one")
    if sum(weights, ZERO) != ONE:
        raise BacktestQualityError("Target asset and reserve weights must sum to one")


def run_backtest(
    *,
    prices: tuple[CleanMarketPriceRecord, ...],
    reserve_returns: tuple[ReserveDailyRecord, ...],
    targets: tuple[ExecutionTarget, ...],
    symbols: tuple[str, ...],
    transaction_cost_bps: Decimal,
) -> BacktestResult:
    if transaction_cost_bps < 0:
        raise ValueError("Transaction cost cannot be negative")
    if not targets:
        raise BacktestQualityError("At least one execution target is required")
    for target_item in targets:
        _validate_target(target_item, symbols)
    if tuple(sorted(target.execution_date for target in targets)) != tuple(
        target.execution_date for target in targets
    ):
        raise BacktestQualityError("Execution targets must be sorted")
    target_by_date = {target.execution_date: target for target in targets}
    if len(target_by_date) != len(targets):
        raise BacktestQualityError("Only one target is allowed per execution date")

    prices_by_symbol: dict[str, dict[date, CleanMarketPriceRecord]] = defaultdict(dict)
    for price in prices:
        if price.symbol in symbols:
            prices_by_symbol[price.symbol][price.trade_date] = price
    if set(prices_by_symbol) != set(symbols):
        raise BacktestQualityError("All configured price series are required")
    trading_dates = tuple(sorted(prices_by_symbol[symbols[0]]))
    if any(tuple(sorted(prices_by_symbol[symbol])) != trading_dates for symbol in symbols[1:]):
        raise BacktestQualityError("Asset trading dates must be exactly aligned")
    reserve_by_date = {record.nav_date: record for record in reserve_returns}
    if any(trading_date not in reserve_by_date for trading_date in trading_dates):
        raise BacktestQualityError("Reserve return is missing for a trading date")
    if any(target.execution_date not in prices_by_symbol[symbols[0]] for target in targets):
        raise BacktestQualityError("Every execution date must be an available price date")
    start_date = targets[0].execution_date
    active_dates = tuple(item for item in trading_dates if item >= start_date)

    with localcontext() as context:
        context.prec = 40
        cost_rate = transaction_cost_bps / Decimal(10_000)
        gross_nav = ONE
        net_nav = ONE
        close_weights = {symbol: ZERO for symbol in symbols}
        close_weights[RESERVE_SLEEVE] = ONE
        previous_date: date | None = None
        daily_nav: list[DailyNavRecord] = []
        daily_positions: list[DailyPositionRecord] = []
        executions: list[ExecutionRecord] = []
        trades: list[TradeRecord] = []

        for nav_date in active_dates:
            prior_gross_nav = gross_nav
            prior_net_nav = net_nav
            if previous_date is None:
                open_notionals = dict(close_weights)
            else:
                gap_days = (nav_date - previous_date).days
                open_notionals = {
                    symbol: close_weights[symbol]
                    * prices_by_symbol[symbol][nav_date].open_adj
                    / prices_by_symbol[symbol][previous_date].close_adj
                    for symbol in symbols
                }
                reserve_growth = reserve_by_date[previous_date].calendar_daily_factor ** gap_days
                open_notionals[RESERVE_SLEEVE] = close_weights[RESERVE_SLEEVE] * reserve_growth
            overnight_factor = sum(open_notionals.values(), ZERO)
            gross_pretrade_nav = gross_nav * overnight_factor
            net_pretrade_nav = net_nav * overnight_factor
            pretrade_weights = _normalize(open_notionals)
            execution_target = target_by_date.get(nav_date)
            turnover = ZERO
            cost_fraction = ZERO
            cost_amount = ZERO
            if execution_target is None:
                posttrade_weights = pretrade_weights
            else:
                target_weights = dict(execution_target.asset_weights)
                target_weights[RESERVE_SLEEVE] = execution_target.reserve_weight
                turnover = (
                    sum(
                        (
                            abs(target_weights[sleeve] - pretrade_weights[sleeve])
                            for sleeve in (*symbols, RESERVE_SLEEVE)
                        ),
                        ZERO,
                    )
                    / 2
                )
                cost_fraction = turnover * cost_rate
                cost_amount = net_pretrade_nav * cost_fraction
                posttrade_weights = target_weights
                executions.append(
                    ExecutionRecord(
                        execution_target.signal_date,
                        execution_target.execution_date,
                        turnover,
                        cost_fraction,
                        cost_amount,
                        gross_pretrade_nav,
                        net_pretrade_nav,
                    )
                )
                for symbol in symbols:
                    weight_change = (
                        execution_target.asset_weights[symbol] - pretrade_weights[symbol]
                    )
                    if weight_change == 0:
                        continue
                    trades.append(
                        TradeRecord(
                            nav_date,
                            symbol,
                            "buy" if weight_change > 0 else "sell",
                            prices_by_symbol[symbol][nav_date].open_adj,
                            pretrade_weights[symbol],
                            execution_target.asset_weights[symbol],
                            weight_change,
                        )
                    )

            intraday_notionals = {
                symbol: posttrade_weights[symbol]
                * prices_by_symbol[symbol][nav_date].close_adj
                / prices_by_symbol[symbol][nav_date].open_adj
                for symbol in symbols
            }
            intraday_notionals[RESERVE_SLEEVE] = posttrade_weights[RESERVE_SLEEVE]
            intraday_factor = sum(intraday_notionals.values(), ZERO)
            gross_nav = gross_pretrade_nav * intraday_factor
            net_nav = net_pretrade_nav * (ONE - cost_fraction) * intraday_factor
            close_weights = _normalize(intraday_notionals)
            daily_nav.append(
                DailyNavRecord(
                    nav_date,
                    gross_nav / prior_gross_nav - ONE,
                    net_nav / prior_net_nav - ONE,
                    gross_nav,
                    net_nav,
                    turnover,
                    cost_fraction,
                    cost_amount,
                )
            )
            daily_positions.extend(
                DailyPositionRecord(nav_date, sleeve, close_weights[sleeve])
                for sleeve in (*symbols, RESERVE_SLEEVE)
            )
            previous_date = nav_date

    return BacktestResult(
        tuple(daily_nav),
        tuple(daily_positions),
        tuple(executions),
        tuple(trades),
    )


def equal_weight_targets(
    execution_pairs: tuple[tuple[date, date], ...], symbols: tuple[str, ...]
) -> tuple[ExecutionTarget, ...]:
    weight = ONE / Decimal(len(symbols))
    return tuple(
        ExecutionTarget(signal_date, execution_date, {symbol: weight for symbol in symbols}, ZERO)
        for signal_date, execution_date in execution_pairs
    )


def buy_and_hold_target(
    signal_date: date, execution_date: date, symbol: str
) -> tuple[ExecutionTarget, ...]:
    return (ExecutionTarget(signal_date, execution_date, {symbol: ONE}, ZERO),)
