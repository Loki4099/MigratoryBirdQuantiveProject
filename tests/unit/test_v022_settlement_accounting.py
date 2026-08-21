from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from style_rotation.experiment.contracts import (
    AccountingMarketBar,
    AccountingReserveInterval,
    ExecutableTarget,
    TargetAssetWeight,
)
from style_rotation.v022.settlement_accounting import (
    RuntimeSettlementInstruction,
    RuntimeSettlementLeg,
    calculate_settlement_aware_gross_portfolio_path,
)

D = Decimal
SOURCE = uuid.UUID("00000000-0000-0000-0000-000000000001")
TARGET = uuid.UUID("00000000-0000-0000-0000-000000000002")
SESSIONS = (
    date(2025, 1, 2),
    date(2025, 1, 3),
    date(2025, 1, 6),
    date(2025, 1, 7),
)


def _bars(include_target: bool) -> tuple[AccountingMarketBar, ...]:
    rows = [
        AccountingMarketBar(SOURCE, "source", session, D("10"), D("10"))
        for session in SESSIONS[1:]
    ]
    if include_target:
        rows.extend(
            AccountingMarketBar(TARGET, "target", session, D("6"), D("6"))
            for session in SESSIONS[1:]
        )
    return tuple(rows)


def _reserve() -> tuple[AccountingReserveInterval, ...]:
    return tuple(
        AccountingReserveInterval(start, end, D(1), start, start, "normal")
        for start, end in zip(SESSIONS[1:], SESSIONS[2:], strict=False)
    )


def _target(include_target: bool) -> tuple[ExecutableTarget, ...]:
    weights = [TargetAssetWeight(SOURCE, "source", D(1))]
    if include_target:
        weights.append(TargetAssetWeight(TARGET, "target", D(0)))
    return (
        ExecutableTarget(SESSIONS[0], SESSIONS[1], tuple(weights), D(0)),
    )


@pytest.mark.parametrize(
    (
        "instruction",
        "include_target",
        "expected_nav",
        "expected_source",
        "expected_target",
        "expected_reserve",
    ),
    [
        (
            RuntimeSettlementInstruction(
                SOURCE,
                "source",
                "cash_merger",
                SESSIONS[2],
                (
                    RuntimeSettlementLeg(
                        "cash", cash_amount_per_source_share=D("12"), currency="USD"
                    ),
                ),
            ),
            False,
            D("1.2"),
            D(0),
            None,
            D(1),
        ),
        (
            RuntimeSettlementInstruction(
                SOURCE,
                "source",
                "stock_merger",
                SESSIONS[2],
                (
                    RuntimeSettlementLeg(
                        "successor_security",
                        target_asset_id=TARGET,
                        target_asset_key="target",
                        quantity_per_source_share=D(2),
                    ),
                ),
            ),
            True,
            D("1.2"),
            D(0),
            D(1),
            D(0),
        ),
        (
            RuntimeSettlementInstruction(
                SOURCE,
                "source",
                "spinoff",
                SESSIONS[2],
                (
                    RuntimeSettlementLeg(
                        "distributed_security",
                        target_asset_id=TARGET,
                        target_asset_key="target",
                        quantity_per_source_share=D("0.5"),
                    ),
                ),
            ),
            True,
            D("1.3"),
            D("0.7692307692307692307692307692307692307692"),
            D("0.2307692307692307692307692307692307692308"),
            D(0),
        ),
    ],
)
def test_frozen_settlement_legs_transform_existing_position_without_trade_cost(
    instruction: RuntimeSettlementInstruction,
    include_target: bool,
    expected_nav: Decimal,
    expected_source: Decimal,
    expected_target: Decimal | None,
    expected_reserve: Decimal,
) -> None:
    result = calculate_settlement_aware_gross_portfolio_path(
        bars=_bars(include_target),
        reserve_intervals=_reserve(),
        targets=_target(include_target),
        common_sessions=SESSIONS,
        simulation_end=SESSIONS[-1],
        settlements=(instruction,),
    )

    assert result.daily_nav[1].gross_nav == expected_nav
    positions = {
        item.asset_id: item.close_weight
        for item in result.daily_asset_positions
        if item.nav_date == SESSIONS[2]
    }
    assert positions[SOURCE] == expected_source
    if expected_target is not None:
        assert positions[TARGET] == expected_target
    reserve = next(
        item.close_weight
        for item in result.daily_reserve_positions
        if item.nav_date == SESSIONS[2]
    )
    assert reserve == expected_reserve
    assert len(result.executions) == 1
