from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from style_rotation.v022.security_lifecycle import (
    LifecycleEvidenceRef,
    SecurityLifecycleEventSpec,
    SecuritySettlementLegSpec,
)


def _evidence() -> tuple[LifecycleEvidenceRef, ...]:
    return (
        LifecycleEvidenceRef(uuid.UUID("20000000-0000-4000-8000-000000000002"), "primary_notice"),
        LifecycleEvidenceRef(
            uuid.UUID("20000000-0000-4000-8000-000000000001"),
            "corporate_action_terms",
        ),
    )


def test_cash_merger_freezes_exact_position_settlement() -> None:
    spec = SecurityLifecycleEventSpec(
        security_id=uuid.UUID("10000000-0000-4000-8000-000000000001"),
        event_key="cash_merger_2020_01_03",
        version_number=1,
        event_type="cash_merger",
        event_status="confirmed",
        announced_at=datetime(2019, 12, 1, tzinfo=UTC),
        effective_session=date(2020, 1, 3),
        last_trading_session=date(2020, 1, 2),
        settlement_session=date(2020, 1, 3),
        selectable_after=False,
        tradable_after=False,
        valuation_state_after="terminal",
        evidence=_evidence(),
        settlement_legs=(
            SecuritySettlementLegSpec(
                leg_kind="cash",
                cash_amount_per_source_share=Decimal("42.50"),
                currency="USD",
                valuation_policy="fixed_cash",
            ),
        ),
        created_by="reviewer",
    )

    document = spec.document()
    assert document["event_type"] == "cash_merger"
    assert document["evidence_artifact_ids"] == [
        "20000000-0000-4000-8000-000000000001",
        "20000000-0000-4000-8000-000000000002",
    ]
    assert document["evidence"] == [
        {
            "artifact_id": "20000000-0000-4000-8000-000000000001",
            "role": "corporate_action_terms",
        },
        {
            "artifact_id": "20000000-0000-4000-8000-000000000002",
            "role": "primary_notice",
        },
    ]
    assert document["settlement_legs"] == [
        {
            "ordinal": 0,
            "leg_kind": "cash",
            "target_security_id": None,
            "quantity_per_source_share": None,
            "cash_amount_per_source_share": "42.50",
            "currency": "USD",
            "valuation_policy": "fixed_cash",
        }
    ]


def test_stock_merger_supports_cash_and_successor_legs() -> None:
    successor = uuid.uuid4()
    spec = SecurityLifecycleEventSpec(
        security_id=uuid.uuid4(),
        event_key="stock_merger_2021_06_01",
        version_number=1,
        event_type="stock_merger",
        event_status="confirmed",
        announced_at=datetime(2021, 5, 1, tzinfo=UTC),
        effective_session=date(2021, 6, 1),
        settlement_session=date(2021, 6, 1),
        selectable_after=False,
        tradable_after=False,
        valuation_state_after="terminal",
        evidence=_evidence(),
        settlement_legs=(
            SecuritySettlementLegSpec(
                leg_kind="successor_security",
                target_security_id=successor,
                quantity_per_source_share=Decimal("0.5"),
                valuation_policy="successor_market_value",
            ),
            SecuritySettlementLegSpec(
                leg_kind="cash",
                cash_amount_per_source_share=Decimal("5"),
                currency="USD",
                valuation_policy="fixed_cash",
            ),
        ),
        created_by="reviewer",
    )

    assert spec.document()["settlement_leg_count"] == 2


def test_halt_and_terminal_events_reject_unsafe_state_or_missing_settlement() -> None:
    common = {
        "security_id": uuid.uuid4(),
        "event_key": "halt_2022_02_02",
        "version_number": 1,
        "event_status": "confirmed",
        "announced_at": datetime(2022, 2, 1, tzinfo=UTC),
        "effective_session": date(2022, 2, 2),
        "evidence": _evidence(),
        "created_by": "reviewer",
    }
    with pytest.raises(ValueError, match="state transition"):
        SecurityLifecycleEventSpec(
            **common,
            event_type="trading_halt",
            selectable_after=True,
            tradable_after=False,
            valuation_state_after="stale_confirmed",
        )
    with pytest.raises(ValueError, match="requires settlement legs"):
        SecurityLifecycleEventSpec(
            **{**common, "event_key": "delisting_2022_02_02"},
            event_type="delisting",
            selectable_after=False,
            tradable_after=False,
            valuation_state_after="terminal",
        )


def test_unresolved_terminal_event_is_recordable_but_has_no_fake_settlement() -> None:
    spec = SecurityLifecycleEventSpec(
        security_id=uuid.uuid4(),
        event_key="delisting_unresolved_2022_02_02",
        version_number=1,
        event_type="delisting",
        event_status="unresolved",
        announced_at=datetime(2022, 2, 1, tzinfo=UTC),
        effective_session=date(2022, 2, 2),
        selectable_after=False,
        tradable_after=False,
        valuation_state_after="terminal",
        evidence=_evidence(),
        created_by="reviewer",
    )

    assert spec.document()["settlement_leg_count"] == 0
    assert spec.document()["settlement_session"] is None


def test_spinoff_distributes_new_security_without_terminating_parent() -> None:
    child_security_id = uuid.uuid4()
    spec = SecurityLifecycleEventSpec(
        security_id=uuid.uuid4(),
        event_key="spinoff_2023_05_01",
        version_number=1,
        event_type="spinoff",
        event_status="confirmed",
        announced_at=datetime(2023, 4, 1, tzinfo=UTC),
        effective_session=date(2023, 5, 1),
        settlement_session=date(2023, 5, 1),
        selectable_after=True,
        tradable_after=True,
        valuation_state_after="live",
        evidence=_evidence(),
        settlement_legs=(
            SecuritySettlementLegSpec(
                leg_kind="distributed_security",
                target_security_id=child_security_id,
                quantity_per_source_share=Decimal("0.25"),
                valuation_policy="distribution_market_value",
            ),
        ),
        created_by="reviewer",
    )

    assert spec.document()["valuation_state_after"] == "live"
    assert spec.document()["settlement_legs"][0]["target_security_id"] == str(
        child_security_id
    )


def test_reorganization_requires_the_correct_settlement_leg_kind() -> None:
    with pytest.raises(ValueError, match="cash Settlement Leg"):
        SecurityLifecycleEventSpec(
            security_id=uuid.uuid4(),
            event_key="cash_merger_wrong_leg_2023_05_01",
            version_number=1,
            event_type="cash_merger",
            event_status="confirmed",
            announced_at=datetime(2023, 4, 1, tzinfo=UTC),
            effective_session=date(2023, 5, 1),
            settlement_session=date(2023, 5, 1),
            selectable_after=False,
            tradable_after=False,
            valuation_state_after="terminal",
            evidence=_evidence(),
            settlement_legs=(
                SecuritySettlementLegSpec(
                    leg_kind="writeoff", valuation_policy="zero_recovery"
                ),
            ),
            created_by="reviewer",
        )
