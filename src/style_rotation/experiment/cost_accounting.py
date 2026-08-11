from __future__ import annotations

from decimal import Decimal, localcontext

from style_rotation.experiment.contracts import (
    ExecutionCost,
    GrossDailyNav,
    NetCostResult,
    NetDailyNav,
    PortfolioExecution,
)

ZERO = Decimal(0)
ONE = Decimal(1)
BPS_DIVISOR = Decimal(10_000)


class NetCostAccountingError(RuntimeError):
    """Raised when a formal linear-cost path cannot be reconciled to its Gross Path."""


def calculate_net_cost_path(
    *,
    gross_daily_nav: tuple[GrossDailyNav, ...],
    executions: tuple[PortfolioExecution, ...],
    cost_bps_per_side: Decimal,
) -> NetCostResult:
    if cost_bps_per_side <= ZERO:
        raise ValueError("Formal net cost paths require a positive bps scenario")
    if not gross_daily_nav:
        raise NetCostAccountingError("Gross daily NAV is required")
    dates = tuple(item.nav_date for item in gross_daily_nav)
    if dates != tuple(sorted(dates)) or len(set(dates)) != len(dates):
        raise NetCostAccountingError("Gross daily NAV dates must be unique and sorted")
    if any(
        item.gross_nav <= ZERO or item.overnight_factor <= ZERO or item.intraday_factor <= ZERO
        for item in gross_daily_nav
    ):
        raise NetCostAccountingError("Gross NAV and daily factors must remain positive")
    execution_dates = tuple(item.execution_date for item in executions)
    if execution_dates != tuple(sorted(execution_dates)) or len(set(execution_dates)) != len(
        executions
    ):
        raise NetCostAccountingError("Execution dates must be unique and sorted")
    if not set(execution_dates).issubset(dates):
        raise NetCostAccountingError("Every execution must occur on a Gross Path NAV date")
    if any(item.gross_traded_fraction < ZERO for item in executions):
        raise NetCostAccountingError("Gross traded fractions cannot be negative")

    execution_by_date = {item.execution_date: item for item in executions}
    rate = cost_bps_per_side / BPS_DIVISOR
    with localcontext() as context:
        context.prec = 40
        net_nav = ONE
        rows: list[NetDailyNav] = []
        costs: list[ExecutionCost] = []
        cumulative_cost = ZERO
        for gross_row in gross_daily_nav:
            prior_net_nav = net_nav
            net_pretrade_nav = prior_net_nav * gross_row.overnight_factor
            execution = execution_by_date.get(gross_row.nav_date)
            daily_cost = ZERO
            if execution is not None:
                cost_fraction = execution.gross_traded_fraction * rate
                if cost_fraction >= ONE:
                    raise NetCostAccountingError("Execution cost would exhaust portfolio NAV")
                gross_traded_notional = net_pretrade_nav * execution.gross_traded_fraction
                daily_cost = net_pretrade_nav * cost_fraction
                costs.append(
                    ExecutionCost(
                        execution.decision_date,
                        execution.execution_date,
                        net_pretrade_nav,
                        gross_traded_notional,
                        cost_fraction,
                        daily_cost,
                    )
                )
                cumulative_cost += daily_cost
            net_nav = (net_pretrade_nav - daily_cost) * gross_row.intraday_factor
            if net_nav <= ZERO:
                raise NetCostAccountingError("Net portfolio NAV must remain positive")
            rows.append(
                NetDailyNav(
                    gross_row.nav_date,
                    net_nav / prior_net_nav - ONE,
                    net_nav,
                    gross_row.gross_nav,
                    daily_cost,
                )
            )
    return NetCostResult(
        dates[0],
        dates[-1],
        cost_bps_per_side,
        cumulative_cost,
        tuple(rows),
        tuple(costs),
    )
