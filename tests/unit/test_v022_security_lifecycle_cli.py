from __future__ import annotations

import uuid

from style_rotation.cli.v022_security_lifecycle import event_spec


def test_lifecycle_cli_parses_manual_review_document() -> None:
    security_id = uuid.uuid4()
    parsed = event_spec(
        {
            "security_id": str(security_id),
            "event_key": "cash_merger_2020_01_03",
            "version_number": 1,
            "event_type": "cash_merger",
            "event_status": "confirmed",
            "announced_at": "2019-12-01T00:00:00+00:00",
            "effective_session": "2020-01-03",
            "last_trading_session": "2020-01-02",
            "settlement_session": "2020-01-03",
            "selectable_after": False,
            "tradable_after": False,
            "valuation_state_after": "terminal",
            "evidence": [
                {"artifact_id": str(uuid.uuid4()), "role": "primary_notice"}
            ],
            "settlement_legs": [
                {
                    "leg_kind": "cash",
                    "cash_amount_per_source_share": "42.50",
                    "currency": "USD",
                    "valuation_policy": "fixed_cash",
                }
            ],
            "created_by": "reviewer",
            "details": {"source": "public_announcement"},
        }
    )

    assert parsed.security_id == security_id
    assert parsed.document()["settlement_legs"][0]["cash_amount_per_source_share"] == "42.50"
