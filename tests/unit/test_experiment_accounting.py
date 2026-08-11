from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from style_rotation.experiment.accounting import (
    PortfolioAccountingError,
    calculate_gross_portfolio_path,
    map_execution_dates,
)
from style_rotation.experiment.contracts import (
    AccountingMarketBar,
    AccountingReserveInterval,
    ExecutableTarget,
    GrossAccountingResult,
    TargetAssetWeight,
    TargetDecision,
)

D = Decimal
SESSIONS = (
    date(2025, 1, 2),
    date(2025, 1, 3),
    date(2025, 1, 6),
    date(2025, 1, 7),
)
ASSET_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ASSET_B = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _decisions() -> tuple[TargetDecision, ...]:
    return (
        TargetDecision(
            SESSIONS[0],
            (
                TargetAssetWeight(ASSET_A, "asset_a", D("0.5")),
                TargetAssetWeight(ASSET_B, "asset_b", D("0.5")),
            ),
            D("0"),
        ),
        TargetDecision(
            SESSIONS[1],
            (
                TargetAssetWeight(ASSET_A, "asset_a", D("0")),
                TargetAssetWeight(ASSET_B, "asset_b", D("0.5")),
            ),
            D("0.5"),
        ),
    )


def _bars(scale_a: Decimal | None = None) -> tuple[AccountingMarketBar, ...]:
    scale = D("1") if scale_a is None else scale_a
    values = {
        SESSIONS[1]: ((D("100"), D("110")), (D("100"), D("100"))),
        SESSIONS[2]: ((D("110"), D("110")), (D("100"), D("102"))),
        SESSIONS[3]: ((D("110"), D("110")), (D("102"), D("102"))),
    }
    rows: list[AccountingMarketBar] = []
    for session, (a_prices, b_prices) in values.items():
        rows.extend(
            (
                AccountingMarketBar(
                    ASSET_A,
                    "asset_a",
                    session,
                    a_prices[0] * scale,
                    a_prices[1] * scale,
                ),
                AccountingMarketBar(
                    ASSET_B,
                    "asset_b",
                    session,
                    b_prices[0],
                    b_prices[1],
                ),
            )
        )
    return tuple(rows)


def _reserve() -> tuple[AccountingReserveInterval, ...]:
    return (
        AccountingReserveInterval(
            SESSIONS[1],
            SESSIONS[2],
            D("1.001"),
            date(2025, 1, 2),
            date(2025, 1, 2),
            "normal",
        ),
        AccountingReserveInterval(
            SESSIONS[2],
            SESSIONS[3],
            D("1.001"),
            date(2025, 1, 3),
            date(2025, 1, 3),
            "warning",
        ),
    )


def _targets() -> tuple[ExecutableTarget, ...]:
    return map_execution_dates(_decisions(), SESSIONS, simulation_end=SESSIONS[-1])


def _calculate(
    *,
    bars: tuple[AccountingMarketBar, ...] | None = None,
    reserve: tuple[AccountingReserveInterval, ...] | None = None,
    targets: tuple[ExecutableTarget, ...] | None = None,
) -> GrossAccountingResult:
    return calculate_gross_portfolio_path(
        bars=_bars() if bars is None else bars,
        reserve_intervals=_reserve() if reserve is None else reserve,
        targets=_targets() if targets is None else targets,
        common_sessions=SESSIONS,
        simulation_end=SESSIONS[-1],
    )


def test_execution_mapping_uses_the_next_common_session_and_excludes_late_targets() -> None:
    decisions = _decisions() + (
        TargetDecision(
            SESSIONS[2],
            (
                TargetAssetWeight(ASSET_A, "asset_a", D("1")),
                TargetAssetWeight(ASSET_B, "asset_b", D("0")),
            ),
            D("0"),
        ),
    )
    mapped = map_execution_dates(decisions, SESSIONS, simulation_end=SESSIONS[2])
    assert [(item.decision_date, item.execution_date) for item in mapped] == [
        (SESSIONS[0], SESSIONS[1]),
        (SESSIONS[1], SESSIONS[2]),
    ]
    assert all(item.execution_date > item.decision_date for item in mapped)


def test_gross_path_follows_overnight_trade_intraday_order_and_preserves_budget() -> None:
    result = _calculate()
    assert result.first_decision_date == SESSIONS[0]
    assert result.first_execution_date == SESSIONS[1]
    assert result.effective_nav_start == SESSIONS[1]
    assert result.effective_nav_end == SESSIONS[3]
    assert len(result.daily_nav) == 3
    assert result.daily_nav[0].gross_nav == D("1.05")
    assert result.daily_nav[1].gross_nav == D("1.0605")
    assert result.daily_nav[2].gross_nav > result.daily_nav[1].gross_nav
    assert result.daily_nav[2].overnight_factor > D("1")
    assert result.daily_reserve_positions[-1].quality_status == "warning"
    by_date: dict[date, Decimal] = {}
    for item in result.daily_asset_positions:
        by_date[item.nav_date] = by_date.get(item.nav_date, D("0")) + item.close_weight
    for reserve in result.daily_reserve_positions:
        assert abs(by_date[reserve.nav_date] + reserve.close_weight - D("1")) < D("1e-30")


def test_first_build_and_rebalance_keep_one_way_and_gross_traded_fraction_distinct() -> None:
    result = _calculate()
    first, second = result.executions
    assert first.one_way_turnover == D("1")
    assert first.gross_traded_fraction == D("1.0")
    assert second.gross_traded_fraction > second.one_way_turnover
    second_trades = [item for item in result.trades if item.execution_date == SESSIONS[2]]
    assert {item.asset_key: item.side for item in second_trades} == {
        "asset_a": "sell",
        "asset_b": "buy",
    }
    trade_total = sum((item.absolute_weight_change for item in second_trades), D("0"))
    assert abs(trade_total - second.gross_traded_fraction) < D("1e-27")


def test_adjusted_price_scale_does_not_change_returns_or_weights() -> None:
    baseline = _calculate()
    scaled = _calculate(bars=_bars(D("10")))
    assert baseline.daily_nav == scaled.daily_nav
    assert baseline.daily_asset_positions == scaled.daily_asset_positions
    assert [item.signed_weight_change for item in baseline.trades] == [
        item.signed_weight_change for item in scaled.trades
    ]
    assert [item.adjusted_execution_price for item in baseline.trades] != [
        item.adjusted_execution_price for item in scaled.trades
    ]


def test_calculation_is_deterministic() -> None:
    assert _calculate() == _calculate()


@pytest.mark.parametrize(
    ("bars", "reserve", "targets", "message"),
    [
        (_bars()[:-1], _reserve(), _targets(), "adjusted bar"),
        (_bars(), _reserve()[:-1], _targets(), "reserve factor"),
        (
            _bars(),
            (replace(_reserve()[0], source_available_date=date(2025, 1, 4)), _reserve()[1]),
            _targets(),
            "available by interval start",
        ),
        (
            _bars(),
            _reserve(),
            (
                replace(
                    _targets()[0],
                    asset_weights=(
                        TargetAssetWeight(ASSET_A, "asset_a", D("0.6")),
                        TargetAssetWeight(ASSET_B, "asset_b", D("0.5")),
                    ),
                ),
                _targets()[1],
            ),
            "sum to one",
        ),
    ],
)
def test_formal_accounting_rejects_incomplete_or_invalid_inputs(
    bars: Any, reserve: Any, targets: Any, message: str
) -> None:
    with pytest.raises(PortfolioAccountingError, match=message):
        _calculate(bars=bars, reserve=reserve, targets=targets)


def test_execution_mapping_rejects_missing_future_session() -> None:
    with pytest.raises(PortfolioAccountingError, match="future execution"):
        map_execution_dates(
            (_decisions()[0],),
            SESSIONS[:1],
            simulation_end=SESSIONS[0],
        )


def test_gross_accounting_rejects_execution_that_bypasses_the_frozen_delay() -> None:
    delayed = replace(_targets()[0], execution_date=SESSIONS[2])
    with pytest.raises(PortfolioAccountingError, match="frozen delay"):
        _calculate(targets=(delayed,))


def test_gross_accounting_rejects_an_empty_target_path() -> None:
    with pytest.raises(PortfolioAccountingError, match="executable target"):
        _calculate(targets=())
