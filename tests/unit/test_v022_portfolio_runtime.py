from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from style_rotation.experiment.contracts import (
    AccountingMarketBar,
    AccountingReserveInterval,
)
from style_rotation.v022.defense_runtime import MergedPortfolioTarget, merge_sleeves
from style_rotation.v022.portfolio_runtime import PortfolioCellSpec, evaluate_portfolio_cell
from style_rotation.v022.runtime_contract import (
    V022RuntimeContractError,
    V022RuntimeDataError,
)
from style_rotation.v022.strategy_compat_runtime import (
    StrategyUnitRiskTarget,
    UnitRiskPosition,
)

D = Decimal
ASSET_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ASSET_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
SPY = uuid.UUID("00000000-0000-0000-0000-000000000003")
SESSIONS = (
    date(2025, 1, 2),
    date(2025, 1, 3),
    date(2025, 1, 6),
    date(2025, 1, 7),
)


def _unit_target(
    decision_date: date, weights: tuple[tuple[uuid.UUID, str, str], ...]
) -> StrategyUnitRiskTarget:
    cutoff = datetime.combine(decision_date, time(21), tzinfo=UTC)
    return StrategyUnitRiskTarget(
        decision_date=decision_date,
        decision_cutoff_at=cutoff,
        input_known_at=cutoff - timedelta(hours=1),
        eligible_count=max(2, len(weights)),
        rankable_count=max(2, len(weights)),
        coverage_ratio=D(1),
        positions=tuple(
            UnitRiskPosition(
                asset_id,
                asset_key,
                D(len(weights) - ordinal),
                ordinal + 1,
                D(1),
                D(raw_weight),
                False,
            )
            for ordinal, (asset_id, asset_key, raw_weight) in enumerate(weights)
        ),
    )


def _targets() -> tuple[MergedPortfolioTarget, ...]:
    return (
        merge_sleeves(
            _unit_target(
                SESSIONS[0],
                ((ASSET_A, "asset_a", "0.5"), (ASSET_B, "asset_b", "0.5")),
            )
        ),
        merge_sleeves(_unit_target(SESSIONS[1], ((ASSET_A, "asset_a", "1"),))),
    )


def _bars() -> tuple[AccountingMarketBar, ...]:
    prices = {
        SESSIONS[1]: (("100", "105"), ("100", "100"), ("100", "101")),
        SESSIONS[2]: (("105", "106"), ("100", "99"), ("101", "102")),
        SESSIONS[3]: (("106", "107"), ("99", "100"), ("102", "103")),
    }
    rows: list[AccountingMarketBar] = []
    identities = ((ASSET_A, "asset_a"), (ASSET_B, "asset_b"), (SPY, "spy"))
    for session, session_prices in prices.items():
        rows.extend(
            AccountingMarketBar(asset_id, asset_key, session, D(open_), D(close))
            for (asset_id, asset_key), (open_, close) in zip(
                identities, session_prices, strict=True
            )
        )
    return tuple(rows)


def _reserve() -> tuple[AccountingReserveInterval, ...]:
    return (
        AccountingReserveInterval(
            SESSIONS[1], SESSIONS[2], D("1.001"), SESSIONS[0], SESSIONS[0], "normal"
        ),
        AccountingReserveInterval(
            SESSIONS[2], SESSIONS[3], D("1.001"), SESSIONS[1], SESSIONS[1], "normal"
        ),
    )


def _spec() -> PortfolioCellSpec:
    return PortfolioCellSpec(
        context_fingerprint="a" * 64,
        simulation_end=SESSIONS[-1],
        cost_bps_per_side=D(5),
        initial_capital=D("1000000"),
        benchmark_asset_id=SPY,
        benchmark_asset_key="spy",
    )


def test_portfolio_cell_evaluates_net_strategy_and_frozen_benchmark_paths() -> None:
    result = evaluate_portfolio_cell(
        _spec(),
        targets=_targets(),
        bars=_bars(),
        reserve_intervals=_reserve(),
        common_sessions=SESSIONS,
    )

    assert tuple(item.nav_date for item in result.net.daily_nav) == SESSIONS[1:]
    assert result.net.daily_nav[-1].net_nav < result.gross.daily_nav[-1].gross_nav
    assert (
        result.benchmark_net.daily_nav[-1].net_nav
        < result.benchmark_gross.daily_nav[-1].gross_nav
    )
    assert result.gross.executions[0].gross_traded_fraction == D(1)
    assert result.benchmark_gross.executions[0].gross_traded_fraction == D(1)
    assert "cumulative_return" in result.absolute_metrics
    assert "cumulative_relative_return" in result.relative_metrics

    scaled_identity = replace(_spec(), initial_capital=D("2000000"))
    assert evaluate_portfolio_cell(
        scaled_identity,
        targets=_targets(),
        bars=_bars(),
        reserve_intervals=_reserve(),
        common_sessions=SESSIONS,
    ) == result


def test_portfolio_cell_uses_reserve_until_first_signal_from_frozen_start() -> None:
    first_prices = tuple(
        AccountingMarketBar(asset_id, asset_key, SESSIONS[0], D("100"), D("100"))
        for asset_id, asset_key in (
            (ASSET_A, "asset_a"),
            (ASSET_B, "asset_b"),
            (SPY, "spy"),
        )
    )
    reserve = (
        AccountingReserveInterval(
            SESSIONS[0], SESSIONS[1], D("1.001"), SESSIONS[0], SESSIONS[0], "normal"
        ),
    ) + _reserve()

    result = evaluate_portfolio_cell(
        replace(_spec(), simulation_start=SESSIONS[1]),
        targets=(_targets()[1],),
        bars=first_prices + _bars(),
        reserve_intervals=reserve,
        common_sessions=SESSIONS,
    )

    assert result.net.daily_nav[0].nav_date == SESSIONS[1]
    assert result.gross.executions[0].execution_date == SESSIONS[1]
    assert result.gross.executions[0].gross_traded_fraction == D(0)
    assert result.gross.executions[1].execution_date == SESSIONS[2]
    assert result.benchmark_gross.executions[0].execution_date == SESSIONS[1]


@pytest.mark.parametrize(
    ("bars", "reserve"),
    [(_bars()[:-1], _reserve()), (_bars(), _reserve()[:-1])],
)
def test_portfolio_cell_rejects_incomplete_exact_market_inputs(
    bars: tuple[AccountingMarketBar, ...],
    reserve: tuple[AccountingReserveInterval, ...],
) -> None:
    with pytest.raises(V022RuntimeDataError) as error:
        evaluate_portfolio_cell(
            _spec(),
            targets=_targets(),
            bars=bars,
            reserve_intervals=reserve,
            common_sessions=SESSIONS,
        )
    assert error.value.reason_code in {
        "portfolio_cell_input_invalid",
        "portfolio_reserve_interval_missing",
    }


def test_portfolio_cell_rejects_noncanonical_target_path_before_accounting() -> None:
    with pytest.raises(V022RuntimeContractError) as error:
        evaluate_portfolio_cell(
            _spec(),
            targets=tuple(reversed(_targets())),
            bars=_bars(),
            reserve_intervals=_reserve(),
            common_sessions=SESSIONS,
        )
    assert error.value.reason_code == "portfolio_target_order_invalid"


def test_portfolio_cell_requires_exact_canonical_session_end() -> None:
    with pytest.raises(V022RuntimeContractError) as order_error:
        evaluate_portfolio_cell(
            _spec(),
            targets=_targets(),
            bars=_bars(),
            reserve_intervals=_reserve(),
            common_sessions=tuple(reversed(SESSIONS)),
        )
    assert order_error.value.reason_code == "portfolio_common_sessions_invalid"

    with pytest.raises(V022RuntimeDataError) as end_error:
        evaluate_portfolio_cell(
            replace(_spec(), simulation_end=date(2025, 1, 8)),
            targets=_targets(),
            bars=_bars(),
            reserve_intervals=_reserve(),
            common_sessions=SESSIONS,
        )
    assert end_error.value.reason_code == "portfolio_simulation_end_unavailable"


def test_portfolio_cell_classifies_nonfinite_market_and_reserve_input() -> None:
    invalid_bars = (replace(_bars()[0], adjusted_open=D("NaN")),) + _bars()[1:]
    with pytest.raises(V022RuntimeDataError) as market_error:
        evaluate_portfolio_cell(
            _spec(),
            targets=_targets(),
            bars=invalid_bars,
            reserve_intervals=_reserve(),
            common_sessions=SESSIONS,
        )
    assert market_error.value.reason_code == "portfolio_market_price_invalid"

    invalid_reserve = (replace(_reserve()[0], accrual_factor=D("Infinity")),) + _reserve()[1:]
    with pytest.raises(V022RuntimeDataError) as reserve_error:
        evaluate_portfolio_cell(
            _spec(),
            targets=_targets(),
            bars=_bars(),
            reserve_intervals=invalid_reserve,
            common_sessions=SESSIONS,
        )
    assert reserve_error.value.reason_code == "portfolio_reserve_factor_invalid"
