from __future__ import annotations

from decimal import Decimal

from style_rotation.v022 import green_baseline_lifecycle as subject


def test_leg_rebuilds_exact_successor_terms() -> None:
    leg = subject._leg(
        {
            "leg_kind": "successor_security",
            "target_security_id": "00000000-0000-0000-0000-000000000002",
            "quantity_per_source_share": "1.437000000000000000",
            "cash_amount_per_source_share": "",
            "currency": "",
            "valuation_policy": "successor_market_value",
        }
    )

    assert leg.quantity_per_source_share == Decimal("1.437")
    assert str(leg.target_security_id) == "00000000-0000-0000-0000-000000000002"


def test_leg_rebuilds_exact_cash_terms() -> None:
    leg = subject._leg(
        {
            "leg_kind": "cash",
            "target_security_id": "",
            "quantity_per_source_share": "",
            "cash_amount_per_source_share": "53.750000000000000000",
            "currency": "USD",
            "valuation_policy": "fixed_cash",
        }
    )

    assert leg.cash_amount_per_source_share == Decimal("53.75")
    assert leg.currency == "USD"
