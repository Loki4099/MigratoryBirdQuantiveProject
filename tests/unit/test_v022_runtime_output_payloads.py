from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from style_rotation.experiment.contracts import (
    GrossAccountingResult,
    GrossDailyNav,
    NetCostResult,
    NetDailyNav,
)
from style_rotation.metrics.types import MetricValue
from style_rotation.v022.defense_runtime import (
    DefenseAllocationMember,
    DefenseDecision,
    evaluate_defense_timing,
    merge_sleeves,
)
from style_rotation.v022.portfolio_runtime import PortfolioCellEvaluation
from style_rotation.v022.runtime_contract import V022RuntimeContractError
from style_rotation.v022.runtime_output_payloads import (
    CanonicalRuntimePayload,
    PortfolioEvaluationPitEvidence,
    PortfolioExecutionIdentity,
    PortfolioRuntimeResultEnvelope,
    PortfolioSessionPitEvidence,
    adapt_defense_budget_decisions,
    adapt_merged_portfolio_targets,
    adapt_portfolio_cell_result,
    adapt_strategy_unit_risk_targets,
    validate_canonical_runtime_payload,
)
from style_rotation.v022.strategy_compat_runtime import (
    StrategyUnitRiskTarget,
    UnitRiskPosition,
)

D = Decimal
DECISION_DATE = date(2025, 1, 2)
CUTOFF = datetime(2025, 1, 2, 21, tzinfo=UTC)
ASSET_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ASSET_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
SPY = uuid.UUID("00000000-0000-0000-0000-000000000003")
BRANCH = uuid.UUID("00000000-0000-0000-0000-000000000004")
SNAPSHOT = uuid.UUID("00000000-0000-0000-0000-000000000005")
DEFENSE = uuid.UUID("00000000-0000-0000-0000-000000000006")
TIMING = uuid.UUID("00000000-0000-0000-0000-000000000007")
ALLOCATION = uuid.UUID("00000000-0000-0000-0000-000000000008")
WORK_E = "a" * 64
SESSIONS = (date(2025, 1, 3), date(2025, 1, 6))


def _strategy_target() -> StrategyUnitRiskTarget:
    return StrategyUnitRiskTarget(
        decision_date=DECISION_DATE,
        decision_cutoff_at=CUTOFF,
        input_known_at=CUTOFF - timedelta(hours=1),
        eligible_count=2,
        rankable_count=2,
        coverage_ratio=D(1),
        positions=(
            UnitRiskPosition(ASSET_A, "asset_a", D(2), 1, D(1), D("0.5"), False),
            UnitRiskPosition(ASSET_B, "asset_b", D(1), 2, D(1), D("0.5"), True),
        ),
    )


def _portfolio_evaluation() -> PortfolioCellEvaluation:
    gross_rows = (
        GrossDailyNav(SESSIONS[0], D("0.02"), D("1.02"), D(1), D("1.02")),
        GrossDailyNav(SESSIONS[1], D("0.01"), D("1.0302"), D(1), D("1.01")),
    )
    benchmark_gross_rows = (
        GrossDailyNav(SESSIONS[0], D("0.01"), D("1.01"), D(1), D("1.01")),
        GrossDailyNav(SESSIONS[1], D("0.01"), D("1.0201"), D(1), D("1.01")),
    )
    gross = GrossAccountingResult(
        DECISION_DATE,
        SESSIONS[0],
        SESSIONS[0],
        SESSIONS[1],
        gross_rows,
        (),
        (),
        (),
        (),
    )
    benchmark_gross = replace(gross, daily_nav=benchmark_gross_rows)
    net = NetCostResult(
        SESSIONS[0],
        SESSIONS[1],
        D(5),
        D("0.0005"),
        (
            NetDailyNav(SESSIONS[0], D("0.019"), D("1.019"), D("1.02"), D("0.001")),
            NetDailyNav(
                SESSIONS[1], D("0.01"), D("1.02919"), D("1.0302"), D(0)
            ),
        ),
        (),
    )
    benchmark_net = replace(
        net,
        daily_nav=(
            NetDailyNav(SESSIONS[0], D("0.009"), D("1.009"), D("1.01"), D("0.001")),
            NetDailyNav(
                SESSIONS[1], D("0.01"), D("1.01909"), D("1.0201"), D(0)
            ),
        ),
    )
    return PortfolioCellEvaluation(
        gross=gross,
        net=net,
        benchmark_gross=benchmark_gross,
        benchmark_net=benchmark_net,
        absolute_metrics={"cumulative_return": MetricValue(D("0.02919"), None, 2)},
        relative_metrics={"excess_return": MetricValue(None, "sample_too_short", 2)},
    )


def _identity() -> PortfolioExecutionIdentity:
    return PortfolioExecutionIdentity(
        compiled_strategy_branch_id=BRANCH,
        configuration_snapshot_id=SNAPSHOT,
        evaluation_data_context_fingerprint="b" * 64,
        effective_start=SESSIONS[0],
        effective_end=SESSIONS[-1],
        benchmark_asset_id=SPY,
        benchmark_asset_key="spy",
        cost_policy_key="linear_5bps_per_side_v1",
        cost_bps_per_side=D(5),
        execution_delay_sessions=1,
        initial_capital_identity_only=D("100000000"),
    )


def _pit() -> PortfolioEvaluationPitEvidence:
    frozen_cutoff = datetime(2025, 1, 7, 12, tzinfo=UTC)
    return PortfolioEvaluationPitEvidence(
        evaluation_input_cutoff_at=frozen_cutoff,
        sessions=tuple(
            PortfolioSessionPitEvidence(
                session,
                datetime.combine(session, datetime.min.time(), tzinfo=UTC)
                + timedelta(hours=23),
            )
            for session in SESSIONS
        ),
    )


def test_strategy_adapter_publishes_exact_canonical_rows_and_validates() -> None:
    payload = adapt_strategy_unit_risk_targets(
        (_strategy_target(),), work_execution_fingerprint=WORK_E
    )

    assert payload.contract_key == payload.output_port_key == "strategy_unit_risk_target"
    assert payload.contract_version == 1
    assert payload.physical_encoding_key == "canonical_parquet"
    assert payload.row_or_item_count == 2
    assert [row["asset_key"] for row in payload.document["rows"]] == [
        "asset_a",
        "asset_b",
    ]
    assert "defense_budget" not in payload.document["rows"][0]
    validate_canonical_runtime_payload(payload)


def test_fixed_defense_keeps_nullable_market_pit_but_always_carries_cutoff() -> None:
    fixed = evaluate_defense_timing(
        "fixed20_budget", decision_date=DECISION_DATE, decision_cutoff_at=CUTOFF
    )
    payload = adapt_defense_budget_decisions(
        (fixed,),
        work_execution_fingerprint=WORK_E,
        defense_version_id=DEFENSE,
        timing_policy_version_id=TIMING,
        allocation_policy_version_id=ALLOCATION,
    )
    row = payload.document["rows"][0]

    assert row["decision_cutoff_at"] == "2025-01-02T21:00:00.000000Z"
    assert row["input_known_at"] is None
    assert row["indicator_value"] is None
    assert row["defense_version_id"] == str(DEFENSE)


def test_merged_adapter_preserves_sleeve_attribution_and_nets_once() -> None:
    fixed = evaluate_defense_timing(
        "fixed20_budget", decision_date=DECISION_DATE, decision_cutoff_at=CUTOFF
    )
    merged = merge_sleeves(
        _strategy_target(),
        defense_decision=fixed,
        allocation_members=(
            DefenseAllocationMember(None, "reserve", "reserve", D("0.4"), 0),
            DefenseAllocationMember(
                ASSET_A, "asset_a", "defensive_asset", D("0.6"), 1
            ),
        ),
    )
    payload = adapt_merged_portfolio_targets(
        (merged,),
        work_execution_fingerprint=WORK_E,
        compiled_strategy_branch_id=BRANCH,
    )

    assert payload.row_or_item_count == 1
    assert [
        row["sleeve_role"]
        for row in payload.document["ordered_sleeve_contributions"]
    ] == ["risk", "risk", "defense", "reserve"]
    assert {
        row["asset_key"]: row["target_weight"]
        for row in payload.document["ordered_net_asset_targets"]
    } == {"asset_a": "0.52", "asset_b": "0.4"}
    assert payload.document["reserve_target"] == [
        {"decision_date": "2025-01-02", "reserve_target_weight": "0.08"}
    ]


def test_merged_adapter_accepts_sub_quantum_decimal_capital_residual() -> None:
    repeating_weight = D("0.06666666666666666666666666667")
    positions = tuple(
        UnitRiskPosition(
            uuid.UUID(int=index + 10),
            f"asset_{index:02d}",
            D(11 - index),
            index + 1,
            D(1),
            repeating_weight if index < 3 else D("0.1"),
            False,
        )
        for index in range(11)
    )
    strategy = StrategyUnitRiskTarget(
        decision_date=DECISION_DATE,
        decision_cutoff_at=CUTOFF,
        input_known_at=CUTOFF - timedelta(hours=1),
        eligible_count=11,
        rankable_count=11,
        coverage_ratio=D(1),
        positions=positions,
    )
    fixed = evaluate_defense_timing(
        "fixed20_budget", decision_date=DECISION_DATE, decision_cutoff_at=CUTOFF
    )
    merged = merge_sleeves(
        strategy,
        defense_decision=fixed,
        allocation_members=(
            DefenseAllocationMember(None, "reserve", "reserve", D("0.4"), 0),
            DefenseAllocationMember(
                uuid.UUID(int=30), "defense_a", "defensive_asset", D("0.25"), 1
            ),
            DefenseAllocationMember(
                uuid.UUID(int=31), "defense_b", "defensive_asset", D("0.1"), 2
            ),
            DefenseAllocationMember(
                uuid.UUID(int=32), "defense_c", "defensive_asset", D("0.15"), 3
            ),
            DefenseAllocationMember(
                uuid.UUID(int=33), "defense_d", "defensive_asset", D("0.1"), 4
            ),
        ),
    )
    serialized_capital = sum(
        (D(str(item.target_weight.normalize())) for item in merged.net_asset_weights),
        D(str(merged.reserve_target_weight.normalize())),
    )
    assert serialized_capital == D("0.9999999999999999999999999999")

    payload = adapt_merged_portfolio_targets(
        (merged,),
        work_execution_fingerprint=WORK_E,
        compiled_strategy_branch_id=BRANCH,
    )

    validate_canonical_runtime_payload(payload)


def test_merged_adapter_keeps_zero_budget_defense_attribution_out_of_net_targets() -> None:
    ma200_risk_on = DefenseDecision(
        decision_date=DECISION_DATE,
        decision_cutoff_at=CUTOFF,
        timing_variant_key="spy_ma200_tiered_budget",
        regime_key="above_upper",
        reason_code="above_strict_upper_threshold",
        risk_budget=D("1.000000000000000000"),
        defense_budget=D("0.000000000000000000"),
        indicator_value=D("1.03"),
        input_known_at=CUTOFF - timedelta(minutes=1),
    )
    merged = merge_sleeves(
        _strategy_target(),
        defense_decision=ma200_risk_on,
        allocation_members=(
            DefenseAllocationMember(None, "reserve", "reserve", D("0.4"), 0),
            DefenseAllocationMember(
                SPY, "spy", "defensive_asset", D("0.6"), 1
            ),
        ),
    )

    payload = adapt_merged_portfolio_targets(
        (merged,),
        work_execution_fingerprint=WORK_E,
        compiled_strategy_branch_id=BRANCH,
    )

    assert [
        row["portfolio_weight"]
        for row in payload.document["ordered_sleeve_contributions"]
        if row["sleeve_role"] in {"defense", "reserve"}
    ] == ["0", "0"]
    assert {
        row["asset_key"]
        for row in payload.document["ordered_net_asset_targets"]
    } == {"asset_a", "asset_b"}


def test_portfolio_adapter_uses_only_global_identity_and_caller_frozen_pit() -> None:
    payload = adapt_portfolio_cell_result(
        PortfolioRuntimeResultEnvelope.accepted(_portfolio_evaluation()),
        identity=_identity(),
        pit_evidence=_pit(),
        work_execution_fingerprint=WORK_E,
    )

    assert payload.document["execution_identity"] == {
        "work_execution_fingerprint": WORK_E,
        "compiled_strategy_branch_id": str(BRANCH),
        "configuration_snapshot_id": str(SNAPSHOT),
        "evaluation_data_context_fingerprint": "b" * 64,
    }
    assert payload.document["evaluation_context"]["evaluation_input_cutoff_at"] == (
        "2025-01-07T12:00:00.000000Z"
    )
    assert payload.document["evaluation_context"]["effective_start"] == "2025-01-03"
    assert payload.document["evaluation_context"]["effective_end"] == "2025-01-06"
    serialized = repr(payload.document)
    assert "research_cell_id" not in serialized
    assert "research_suite_id" not in serialized
    assert "suite_runtime_plan_id" not in serialized
    for section in (
        "gross_path",
        "net_path",
        "benchmark_gross_path",
        "benchmark_net_path",
    ):
        rows = payload.document[section]
        assert [row["normalized_value"] for row in rows][0] == "1"
        assert [row["input_known_at"] for row in rows] == [
            "2025-01-03T23:00:00.000000Z",
            "2025-01-06T23:00:00.000000Z",
        ]
        assert {row["work_execution_fingerprint"] for row in rows} == {WORK_E}
    assert payload.document["quality"]["outcome"] == "accepted"
    assert payload.document["quality"]["effective_end"] == "2025-01-06"


def test_portfolio_adapter_rejects_missing_late_or_naive_caller_pit() -> None:
    envelope = PortfolioRuntimeResultEnvelope.accepted(_portfolio_evaluation())
    incomplete = replace(_pit(), sessions=_pit().sessions[:1])
    with pytest.raises(V022RuntimeContractError) as coverage_error:
        adapt_portfolio_cell_result(
            envelope,
            identity=_identity(),
            pit_evidence=incomplete,
            work_execution_fingerprint=WORK_E,
        )
    assert coverage_error.value.reason_code == "portfolio_path_pit_coverage_mismatch"

    with pytest.raises(V022RuntimeContractError) as late_error:
        PortfolioEvaluationPitEvidence(
            evaluation_input_cutoff_at=CUTOFF,
            sessions=(PortfolioSessionPitEvidence(SESSIONS[0], CUTOFF + timedelta(seconds=1)),),
        )
    assert late_error.value.reason_code == "portfolio_path_input_after_cutoff"

    with pytest.raises(V022RuntimeContractError) as naive_error:
        PortfolioSessionPitEvidence(SESSIONS[0], datetime(2025, 1, 3, 23))
    assert naive_error.value.reason_code == "portfolio_path_input_known_at_naive"


@pytest.mark.parametrize("outcome", ["data_quality_failed", "capacity_rejected"])
def test_portfolio_terminal_quality_envelope_has_no_fabricated_path(
    outcome: str,
) -> None:
    terminal_outcome = (
        "data_quality_failed" if outcome == "data_quality_failed" else "capacity_rejected"
    )
    envelope = PortfolioRuntimeResultEnvelope.terminal(
        terminal_outcome,
        reason_code="exact_input_unavailable",
        details={"missing_sessions": 2},
    )
    payload = adapt_portfolio_cell_result(
        envelope,
        identity=_identity(),
        pit_evidence=PortfolioEvaluationPitEvidence(
            evaluation_input_cutoff_at=CUTOFF, sessions=()
        ),
        work_execution_fingerprint=WORK_E,
    )

    assert payload.document["quality"]["outcome"] == outcome
    assert payload.document["quality"]["quality_status"] == "failed"
    assert payload.document["gross_path"] == []
    assert payload.document["absolute_metrics"] == []
    assert payload.document["evaluation_context"]["effective_start"] == "2025-01-03"
    assert payload.document["evaluation_context"]["effective_end"] == "2025-01-06"
    assert payload.document["quality"]["effective_start"] == "2025-01-03"
    assert payload.document["quality"]["effective_end"] == "2025-01-06"
    assert payload.document["evaluation_context"]["evaluation_input_cutoff_at"] == (
        "2025-01-02T21:00:00.000000Z"
    )


def test_validator_detects_tampering_and_forbids_plan_specific_identity() -> None:
    payload = adapt_strategy_unit_risk_targets(
        (_strategy_target(),), work_execution_fingerprint=WORK_E
    )
    broken = CanonicalRuntimePayload(
        contract_key=payload.contract_key,
        output_port_key=payload.output_port_key,
        work_execution_fingerprint=payload.work_execution_fingerprint,
        canonical_document_fingerprint=payload.canonical_document_fingerprint,
        row_or_item_count=payload.row_or_item_count,
        document={"rows": []},
    )
    with pytest.raises(V022RuntimeContractError) as mismatch:
        validate_canonical_runtime_payload(broken)
    assert mismatch.value.reason_code == "runtime_output_document_fingerprint_mismatch"

    with pytest.raises(V022RuntimeContractError) as forbidden:
        adapt_portfolio_cell_result(
            PortfolioRuntimeResultEnvelope.terminal(
                "data_quality_failed",
                reason_code="missing_data",
                details={"research_cell_id": str(uuid.uuid4())},
            ),
            identity=_identity(),
            pit_evidence=PortfolioEvaluationPitEvidence(
                evaluation_input_cutoff_at=CUTOFF, sessions=()
            ),
            work_execution_fingerprint=WORK_E,
        )
    assert forbidden.value.reason_code == "portfolio_plan_specific_identity_forbidden"
