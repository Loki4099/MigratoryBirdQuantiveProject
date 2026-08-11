from datetime import date
from decimal import Decimal

import pytest

from style_rotation.experiment.contracts import GrossDailyNav, PortfolioExecution
from style_rotation.experiment.cost_accounting import (
    NetCostAccountingError,
    calculate_net_cost_path,
)

D = Decimal


def _gross() -> tuple[GrossDailyNav, ...]:
    return (
        GrossDailyNav(date(2024, 1, 3), D("0.10"), D("1.10"), D("1"), D("1.10")),
        GrossDailyNav(date(2024, 1, 4), D("0.10"), D("1.21"), D("1"), D("1.10")),
        GrossDailyNav(date(2024, 1, 5), D("0"), D("1.21"), D("1"), D("1")),
    )


def _executions() -> tuple[PortfolioExecution, ...]:
    return (
        PortfolioExecution(
            date(2024, 1, 2),
            date(2024, 1, 3),
            D("1"),
            D("1"),
            D("1"),
            D("1"),
            D("0"),
        ),
        PortfolioExecution(
            date(2024, 1, 3),
            date(2024, 1, 4),
            D("1.10"),
            D("0.25"),
            D("0.5"),
            D("0"),
            D("0"),
        ),
    )


def test_net_cost_path_charges_each_asset_trade_side_and_compounds_after_cost() -> None:
    result = calculate_net_cost_path(
        gross_daily_nav=_gross(), executions=_executions(), cost_bps_per_side=D("10")
    )
    assert result.execution_costs[0].gross_traded_notional == D("1")
    assert result.execution_costs[0].cost_amount == D("0.001")
    assert result.daily_nav[0].net_nav == D("1.09890")
    assert result.execution_costs[1].net_pretrade_nav == D("1.09890")
    assert result.execution_costs[1].gross_traded_notional == D("0.549450")
    assert result.execution_costs[1].cost_amount == D("0.000549450")
    assert result.daily_nav[1].net_nav == D("1.2081856050")
    assert result.daily_nav[2].daily_cost_amount == D("0")
    assert result.daily_nav[2].net_nav == result.daily_nav[1].net_nav
    assert result.daily_nav[-1].net_nav < result.daily_nav[-1].gross_nav


def test_higher_positive_cost_never_improves_net_nav() -> None:
    paths = [
        calculate_net_cost_path(
            gross_daily_nav=_gross(), executions=_executions(), cost_bps_per_side=D(bps)
        )
        for bps in ("2", "5", "10")
    ]
    assert paths[0].daily_nav[-1].net_nav > paths[1].daily_nav[-1].net_nav
    assert paths[1].daily_nav[-1].net_nav > paths[2].daily_nav[-1].net_nav


def test_net_cost_path_rejects_missing_execution_date() -> None:
    invalid = (
        PortfolioExecution(
            date(2024, 1, 5),
            date(2024, 1, 8),
            D("1"),
            D("0"),
            D("0"),
            D("1"),
            D("1"),
        ),
    )
    with pytest.raises(NetCostAccountingError, match="Gross Path NAV date"):
        calculate_net_cost_path(
            gross_daily_nav=_gross(), executions=invalid, cost_bps_per_side=D("5")
        )


def test_net_cost_path_rejects_zero_cost_scenario() -> None:
    with pytest.raises(ValueError, match="positive bps"):
        calculate_net_cost_path(
            gross_daily_nav=_gross(), executions=_executions(), cost_bps_per_side=D("0")
        )
