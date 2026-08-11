from decimal import Decimal

import pytest

from style_rotation.experiment.v021_matrix import (
    ImpactPolicy,
    PortfolioMatrixPolicy,
    build_fixed_portfolio_matrix,
    evaluate_capacity,
    square_root_impact_bps,
)
from style_rotation.workspace.contracts import CompiledStrategyBranch


def _branch(key: str = "branch") -> CompiledStrategyBranch:
    return CompiledStrategyBranch(
        branch_key=key,
        model_instance_key="model__weekly",
        strategy_preset_key="multi_etf_top_k__k1__none__none__none",
        strategy_family_key="multi_etf_top_k",
        frequency="weekly",
        target_k=1,
    )


def test_every_strategy_branch_expands_to_the_frozen_six_cells() -> None:
    cells = build_fixed_portfolio_matrix(
        (_branch("a"), _branch("b")), comparison_context_fingerprint="c" * 64
    )
    assert len(cells) == 12
    assert {item.cost_bps_per_side for item in cells} == {5, 10}
    assert {item.window_key for item in cells} == {
        "full_common_history",
        "trailing_3_years",
        "trailing_1_year",
    }
    assert all(item.initial_capital_usd == Decimal("100000000") for item in cells)
    assert all(item.state_reset for item in cells)
    assert len({item.cell_fingerprint for item in cells}) == 12


def test_fixed_matrix_rejects_parameter_drift() -> None:
    with pytest.raises(ValueError, match="USD 100M"):
        PortfolioMatrixPolicy(initial_capital_usd=Decimal("10000000"))


def test_capacity_is_a_hard_gate_not_an_execution_assumption() -> None:
    assert (
        evaluate_capacity(
            order_notional=Decimal("5000000"),
            trailing_median_dollar_volume_20=Decimal("100000000"),
        ).status
        == "accepted"
    )
    rejected = evaluate_capacity(
        order_notional=Decimal("5000000.01"),
        trailing_median_dollar_volume_20=Decimal("100000000"),
    )
    assert rejected.status == "capacity_rejected"
    assert rejected.reason_code == "adv_5_percent_exceeded"


def test_impact_refuses_to_run_before_p0_coefficients_are_frozen() -> None:
    policy = ImpactPolicy(
        policy_key="impact_pending",
        coefficient=Decimal("0.1"),
        maximum_bps=Decimal("100"),
        p0_finalized=False,
    )
    with pytest.raises(RuntimeError, match="P0-finalized"):
        square_root_impact_bps(
            participation_rate=Decimal("0.01"),
            daily_volatility=Decimal("0.02"),
            policy=policy,
        )
