from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from style_rotation.v022.runtime_contract import (
    V022RuntimeContractError,
    V022RuntimeDataError,
)
from style_rotation.v022.strategy_compat_runtime import (
    RankedAsset,
    StrategyAssetInput,
    build_cross_section_topk_decision,
    build_unit_risk_topk_target,
    resolve_defense_budget,
)

DECISION_DATE = date(2025, 1, 3)
DECISION_CUTOFF = datetime(2025, 1, 3, 21, tzinfo=UTC)
INPUT_KNOWN_AT = datetime(2025, 1, 3, 20, tzinfo=UTC)


def test_etf_and_large_cap_variants_preserve_their_distinct_parameter_contracts() -> None:
    etfs = tuple(
        RankedAsset(f"etf_{index}", Decimal(5 - index)) for index in range(4)
    )
    etf = build_cross_section_topk_decision(
        etfs,
        variant_key="cross_section_rank_top_k_parity",
        target_k=2,
        research_mode="formal",
        selection_buffer="none",
        sector_cap="none",
        defense_budget=Decimal("0.2"),
    )
    assert etf.status == "accepted"
    assert [item.asset_key for item in etf.positions] == ["etf_0", "etf_1"]
    assert [item.target_weight for item in etf.positions] == [
        Decimal("0.4"),
        Decimal("0.4"),
    ]

    equities = tuple(
        RankedAsset(f"stock_{index:03d}", Decimal(100 - index))
        for index in range(100)
    )
    large_cap = build_cross_section_topk_decision(
        equities,
        variant_key="cross_section_rank_top_k_large_cap_parity",
        target_k=10,
        research_mode="formal",
        selection_buffer="half_k",
        sector_cap="none",
        defense_budget=Decimal(),
    )
    assert large_cap.status == "accepted"
    assert len(large_cap.positions) == 10
    assert sum((item.target_weight for item in large_cap.positions), Decimal()) == 1


def test_strategy_variants_fail_closed_on_cross_context_parameters() -> None:
    assets = tuple(
        RankedAsset(f"etf_{index}", Decimal(5 - index)) for index in range(4)
    )
    with pytest.raises(ValueError, match="ETF Top-K parameters"):
        build_cross_section_topk_decision(
            assets,
            variant_key="cross_section_rank_top_k_parity",
            target_k=2,
            research_mode="formal",
            selection_buffer="half_k",
            sector_cap="none",
            defense_budget=Decimal(),
        )
    with pytest.raises(V022RuntimeContractError) as mode_error:
        build_unit_risk_topk_target(
            tuple(
                StrategyAssetInput(
                    uuid.UUID(int=index + 1), f"etf_{index}", Decimal(4 - index)
                )
                for index in range(4)
            ),
            decision_date=DECISION_DATE,
            decision_cutoff_at=DECISION_CUTOFF,
            input_known_at=INPUT_KNOWN_AT,
            variant_key="cross_section_rank_top_k_parity",
            target_k=2,
            research_mode="unknown",  # type: ignore[arg-type]
            selection_buffer="none",
            sector_cap="none",
        )
    assert mode_error.value.reason_code == "strategy_parameters_invalid"


def test_none_fixed20_and_ma200_tiers_are_exact_at_the_boundaries() -> None:
    assert resolve_defense_budget("none") == Decimal(0)
    assert resolve_defense_budget("fixed20_defense") == Decimal("0.2")
    assert resolve_defense_budget(
        "ma200_tiered_defense", spy_close=Decimal("103"), spy_sma200=Decimal("100")
    ) == Decimal(0)
    assert resolve_defense_budget(
        "ma200_tiered_defense", spy_close=Decimal("102"), spy_sma200=Decimal("100")
    ) == Decimal("0.2")
    assert resolve_defense_budget(
        "ma200_tiered_defense", spy_close=Decimal("98"), spy_sma200=Decimal("100")
    ) == Decimal("0.2")
    assert resolve_defense_budget(
        "ma200_tiered_defense", spy_close=Decimal("97"), spy_sma200=Decimal("100")
    ) == Decimal("0.4")
    with pytest.raises(ValueError, match="requires SPY"):
        resolve_defense_budget("ma200_tiered_defense")


def test_binary64_score_collision_uses_asset_key_as_frozen_tie_break() -> None:
    assets = tuple(
        RankedAsset(f"high_{index:03d}", Decimal(100 - index))
        for index in range(9)
    ) + (
        RankedAsset("qcom", Decimal("0.4732510288065843621399176955")),
        RankedAsset("mstr", Decimal("0.4732510288065843621399176953")),
    ) + tuple(
        RankedAsset(f"low_{index:03d}", Decimal(-index - 1))
        for index in range(90)
    )
    decision = build_cross_section_topk_decision(
        assets,
        variant_key="cross_section_rank_top_k_large_cap_parity",
        target_k=10,
        research_mode="formal",
        selection_buffer="half_k",
        sector_cap="none",
        defense_budget=Decimal(0),
    )

    assert decision.status == "accepted"
    assert "mstr" in {item.asset_key for item in decision.positions}
    assert "qcom" not in {item.asset_key for item in decision.positions}


def test_unit_risk_target_is_complete_before_any_defense_scaling() -> None:
    assets = tuple(
        StrategyAssetInput(
            uuid.UUID(int=index + 1),
            f"etf_{index}",
            Decimal(4 - index),
        )
        for index in range(4)
    )

    target = build_unit_risk_topk_target(
        assets,
        decision_date=DECISION_DATE,
        decision_cutoff_at=DECISION_CUTOFF,
        input_known_at=INPUT_KNOWN_AT,
        variant_key="cross_section_rank_top_k_parity",
        target_k=2,
        research_mode="formal",
        selection_buffer="none",
        sector_cap="none",
    )

    assert [item.asset_key for item in target.positions] == ["etf_0", "etf_1"]
    assert [item.unit_risk_weight for item in target.positions] == [
        Decimal("0.5"),
        Decimal("0.5"),
    ]
    assert sum((item.unit_risk_weight for item in target.positions), Decimal()) == 1
    assert target.decision_cutoff_at == DECISION_CUTOFF
    assert target.input_known_at == INPUT_KNOWN_AT


def test_large_boundary_tie_conserves_unit_risk_in_canonical_output_order() -> None:
    assets = (
        StrategyAssetInput(uuid.UUID(int=1), "z_high", Decimal("1")),
        *(
            StrategyAssetInput(
                uuid.UUID(int=index + 2),
                f"a{index:03}",
                Decimal("0.1"),
            )
            for index in range(621)
        ),
    )

    target = build_unit_risk_topk_target(
        assets,
        decision_date=DECISION_DATE,
        decision_cutoff_at=DECISION_CUTOFF,
        input_known_at=INPUT_KNOWN_AT,
        variant_key="cross_section_rank_top_k_large_cap_multi_frequency",
        target_k=10,
        research_mode="exploratory",
        selection_buffer="half_k",
        sector_cap="none",
    )

    assert len(target.positions) == 622
    assert sum((item.unit_risk_weight for item in target.positions), Decimal()) == 1


def test_k20_boundary_thirds_conserve_unit_risk_after_decimal_rounding() -> None:
    assets = (
        *(
            StrategyAssetInput(
                uuid.UUID(int=index + 1),
                f"a{index:02}",
                Decimal(100 - index),
            )
            for index in range(19)
        ),
        *(
            StrategyAssetInput(uuid.UUID(int=100 + index), f"z{index}", Decimal(1))
            for index in range(3)
        ),
        *(
            StrategyAssetInput(
                uuid.UUID(int=200 + index),
                f"y{index:02}",
                Decimal(-index - 1),
            )
            for index in range(28)
        ),
    )

    target = build_unit_risk_topk_target(
        assets,
        decision_date=DECISION_DATE,
        decision_cutoff_at=DECISION_CUTOFF,
        input_known_at=INPUT_KNOWN_AT,
        variant_key="cross_section_rank_top_k_large_cap_multi_frequency",
        target_k=20,
        research_mode="exploratory",
        selection_buffer="half_k",
        sector_cap="none",
    )

    assert len(target.positions) == 22
    assert sum((item.unit_risk_weight for item in target.positions), Decimal()) == 1


@pytest.mark.parametrize(
    ("decision_date", "decision_cutoff_at", "input_known_at", "reason_code"),
    [
        (
            DECISION_DATE,
            DECISION_CUTOFF.replace(tzinfo=None),
            INPUT_KNOWN_AT,
            "strategy_cutoff_naive",
        ),
        (
            DECISION_DATE,
            DECISION_CUTOFF,
            INPUT_KNOWN_AT.replace(tzinfo=None),
            "strategy_input_known_at_naive",
        ),
        (
            DECISION_DATE + timedelta(days=1),
            DECISION_CUTOFF,
            INPUT_KNOWN_AT,
            "strategy_decision_cutoff_mismatch",
        ),
        (
            DECISION_DATE,
            DECISION_CUTOFF,
            DECISION_CUTOFF + timedelta(microseconds=1),
            "strategy_input_after_cutoff",
        ),
    ],
)
def test_unit_risk_target_rejects_invalid_pit_identity(
    decision_date: date,
    decision_cutoff_at: datetime,
    input_known_at: datetime,
    reason_code: str,
) -> None:
    with pytest.raises(V022RuntimeContractError) as error:
        build_unit_risk_topk_target(
            tuple(
                StrategyAssetInput(
                    uuid.UUID(int=index + 1), f"etf_{index}", Decimal(4 - index)
                )
                for index in range(4)
            ),
            decision_date=decision_date,
            decision_cutoff_at=decision_cutoff_at,
            input_known_at=input_known_at,
            variant_key="cross_section_rank_top_k_parity",
            target_k=2,
            research_mode="formal",
            selection_buffer="none",
            sector_cap="none",
        )
    assert error.value.reason_code == reason_code


def test_unit_risk_target_distinguishes_contract_and_exact_data_failures() -> None:
    duplicate = (
        StrategyAssetInput(uuid.UUID(int=1), "etf_a", Decimal(2)),
        StrategyAssetInput(uuid.UUID(int=1), "etf_b", Decimal(1)),
    )
    with pytest.raises(V022RuntimeContractError) as duplicate_error:
        build_unit_risk_topk_target(
            duplicate,
            decision_date=DECISION_DATE,
            decision_cutoff_at=DECISION_CUTOFF,
            input_known_at=INPUT_KNOWN_AT,
            variant_key="cross_section_rank_top_k_parity",
            target_k=1,
            research_mode="formal",
            selection_buffer="none",
            sector_cap="none",
        )
    assert duplicate_error.value.reason_code == "strategy_asset_identity_duplicate"

    insufficient = (StrategyAssetInput(uuid.UUID(int=1), "etf_a", Decimal(2)),)
    with pytest.raises(V022RuntimeDataError) as data_error:
        build_unit_risk_topk_target(
            insufficient,
            decision_date=DECISION_DATE,
            decision_cutoff_at=DECISION_CUTOFF,
            input_known_at=INPUT_KNOWN_AT,
            variant_key="cross_section_rank_top_k_parity",
            target_k=1,
            research_mode="formal",
            selection_buffer="none",
            sector_cap="none",
        )
    assert data_error.value.reason_code == "eligible_count_below_minimum"
