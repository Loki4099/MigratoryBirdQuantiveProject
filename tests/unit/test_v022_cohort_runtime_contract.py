from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from style_rotation.experiment.contracts import AccountingMarketBar
from style_rotation.v022.aggregation_work_runtime import SignalManifestPoint
from style_rotation.v022.cohort_runtime_contract import _derive_runtime_mask, _Inputs
from style_rotation.v022.runtime_contract import V022RuntimeDataError
from style_rotation.v022.suite_typed_work_runtime import (
    _masked_accounting_bars,
    build_strategy_target_payload,
)

SECURITY = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER = uuid.UUID("00000000-0000-0000-0000-000000000002")
COHORT_ARTIFACT = uuid.UUID("00000000-0000-0000-0000-000000000010")
GATE_ARTIFACT = uuid.UUID("00000000-0000-0000-0000-000000000011")
EVENT_ARTIFACT = uuid.UUID("00000000-0000-0000-0000-000000000012")
SESSIONS = (date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6))


def test_runtime_mask_keeps_removed_security_tradable_for_close_only() -> None:
    inputs = _Inputs(
        cohort={
            "artifact_id": COHORT_ARTIFACT,
            "evaluation_cohort_version_id": uuid.uuid4(),
        },
        gate={"artifact_id": GATE_ARTIFACT},
        session_dates=SESSIONS,
        evaluation_dates=frozenset(SESSIONS),
        base_intervals={
            SECURITY: (
                {
                    "effective_start": SESSIONS[0],
                    "effective_end": SESSIONS[0],
                    "is_member": True,
                    "is_warmup_ready": True,
                    "valuation_state": "live",
                    "reason_codes": [],
                    "evidence_artifact_ids": [str(COHORT_ARTIFACT)],
                },
                {
                    "effective_start": SESSIONS[1],
                    "effective_end": SESSIONS[2],
                    "is_member": False,
                    "is_warmup_ready": True,
                    "valuation_state": "live",
                    "reason_codes": ["not_sp500_member"],
                    "evidence_artifact_ids": [str(COHORT_ARTIFACT)],
                },
            )
        },
        exclusions={},
        lifecycle_events={},
        lifecycle_artifacts=(),
        settlement_instructions=(),
    )

    intervals = _derive_runtime_mask(inputs)

    assert intervals[0].is_selectable is True
    assert intervals[1].is_member is False
    assert intervals[1].is_selectable is False
    assert intervals[1].is_tradable is True
    assert "removed_member_close_only" in intervals[1].reason_codes


def test_runtime_mask_applies_confirmed_halt_and_gate_exclusion() -> None:
    inputs = _Inputs(
        cohort={"artifact_id": COHORT_ARTIFACT},
        gate={"artifact_id": GATE_ARTIFACT},
        session_dates=SESSIONS,
        evaluation_dates=frozenset(SESSIONS),
        base_intervals={
            SECURITY: (
                {
                    "effective_start": SESSIONS[0],
                    "effective_end": SESSIONS[2],
                    "is_member": True,
                    "is_warmup_ready": True,
                    "valuation_state": "live",
                    "reason_codes": [],
                    "evidence_artifact_ids": [str(COHORT_ARTIFACT)],
                },
            )
        },
        exclusions={
            SECURITY: {
                "exclusion_start": SESSIONS[2],
                "exclusion_end": SESSIONS[2],
                "reason_code": "uniform_provider_gap",
                "evidence_artifact_id": GATE_ARTIFACT,
            }
        },
        lifecycle_events={
            SECURITY: (
                {
                    "effective_session": SESSIONS[1],
                    "selectable_after": False,
                    "tradable_after": False,
                    "valuation_state_after": "stale_confirmed",
                    "event_type": "trading_halt",
                    "artifact_id": EVENT_ARTIFACT,
                },
            )
        },
        lifecycle_artifacts=(EVENT_ARTIFACT,),
        settlement_instructions=(),
    )

    intervals = _derive_runtime_mask(inputs)

    assert intervals[0].is_selectable is True
    assert intervals[1].valuation_state == "stale_confirmed"
    assert intervals[1].is_tradable is False
    assert intervals[2].valuation_state == "unavailable"
    assert "uniform_provider_gap" in intervals[2].reason_codes


def test_strategy_uses_exact_decision_selection_mask() -> None:
    points = tuple(
        SignalManifestPoint(
            asset_id,
            f"asset_{ordinal}",
            SESSIONS[0],
            Decimal(10 - ordinal),
            datetime.combine(SESSIONS[0], time(21), tzinfo=UTC),
            f"revision-{ordinal}",
            None,
        )
        for ordinal, asset_id in enumerate((SECURITY, OTHER, uuid.uuid4(), uuid.uuid4()))
    )
    with pytest.raises(V022RuntimeDataError, match="first frozen Cohort decision"):
        build_strategy_target_payload(
            points,
            variant_key="cross_section_rank_top_k_parity",
            resolved_parameters={
                "target_k": 2,
                "selection_buffer": "none",
                "sector_cap": "none",
            },
            research_mode="exploratory",
            work_execution_fingerprint="a" * 64,
            selectable_asset_ids_by_date={SESSIONS[0]: frozenset({SECURITY, OTHER})},
        )


def test_zero_weight_prelisting_sessions_do_not_move_frozen_start() -> None:
    real = AccountingMarketBar(
        SECURITY,
        "asset_0",
        SESSIONS[1],
        Decimal("10"),
        Decimal("11"),
    )

    filled = _masked_accounting_bars(
        {SECURITY: {SESSIONS[1]: real}},
        common_sessions=SESSIONS,
        required_by_asset={SECURITY: (SESSIONS[1],)},
    )

    assert tuple(item.session_date for item in filled) == SESSIONS
    assert filled[0].adjusted_open == Decimal("10")
    assert filled[0].adjusted_close == Decimal("10")
    assert filled[1] == real
    assert filled[2].adjusted_close == Decimal("11")


def test_missing_held_asset_bar_remains_fatal() -> None:
    real = AccountingMarketBar(
        SECURITY,
        "asset_0",
        SESSIONS[1],
        Decimal("10"),
        Decimal("11"),
    )

    with pytest.raises(V022RuntimeDataError, match="held or traded"):
        _masked_accounting_bars(
            {SECURITY: {SESSIONS[1]: real}},
            common_sessions=SESSIONS,
            required_by_asset={SECURITY: (SESSIONS[2],)},
        )


def test_frozen_nontradable_holding_valuation_carries_prior_close() -> None:
    first = AccountingMarketBar(
        SECURITY,
        "asset_0",
        SESSIONS[0],
        Decimal("10"),
        Decimal("11"),
    )
    last = AccountingMarketBar(
        SECURITY,
        "asset_0",
        SESSIONS[2],
        Decimal("12"),
        Decimal("13"),
    )

    filled = _masked_accounting_bars(
        {SECURITY: {SESSIONS[0]: first, SESSIONS[2]: last}},
        common_sessions=SESSIONS,
        required_by_asset={SECURITY: SESSIONS},
        carry_forward_by_asset={SECURITY: (SESSIONS[1],)},
    )

    assert filled[1].adjusted_open == Decimal("11")
    assert filled[1].adjusted_close == Decimal("11")
