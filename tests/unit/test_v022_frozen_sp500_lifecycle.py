from __future__ import annotations

import uuid

from style_rotation.v022.frozen_sp500_lifecycle import (
    frozen_aet_lifecycle_event_spec,
    frozen_esrx_lifecycle_event_spec,
    frozen_l3_lifecycle_event_spec,
    frozen_lifecycle_event_specs,
    frozen_twx_lifecycle_event_spec,
    official_aet_lifecycle_evidence_spec,
    official_esrx_lifecycle_evidence_spec,
    official_l3_lifecycle_evidence_spec,
    official_lifecycle_evidence_spec,
    official_twx_lifecycle_evidence_spec,
)


def test_frozen_lifecycle_evidence_and_settlements_are_exact() -> None:
    evidence = official_lifecycle_evidence_spec(created_by="test")
    identities = {
        key: uuid.uuid5(uuid.NAMESPACE_URL, key)
        for key in ("scg", "d", "tss", "gpn", "abmd")
    }
    artifact_id = uuid.uuid4()

    events = frozen_lifecycle_event_specs(
        identities, evidence_artifact_id=artifact_id, created_by="test"
    )

    assert [item.event_key for item in events] == [
        "scg_dominion_stock_merger_2019",
        "tss_global_payments_stock_merger_2019",
        "abmd_jnj_cash_merger_2022",
    ]
    assert [item.settlement_legs[0].leg_kind for item in events] == [
        "successor_security",
        "successor_security",
        "cash",
    ]
    assert events[0].settlement_legs[0].target_security_id == identities["d"]
    assert events[1].settlement_legs[0].target_security_id == identities["gpn"]
    assert str(events[2].settlement_legs[0].cash_amount_per_source_share) == "380.00"
    assert all(item.evidence[0].artifact_id == artifact_id for item in events)
    assert len(evidence.objects) == 3
    assert all(item.provenance_status == "verified" for item in evidence.objects)


def test_l3harris_stock_merger_has_exact_successor_settlement() -> None:
    identities = {
        key: uuid.uuid5(uuid.NAMESPACE_URL, key) for key in ("lll", "lhx")
    }
    evidence = official_l3_lifecycle_evidence_spec(created_by="test")
    artifact_id = uuid.uuid4()

    event = frozen_l3_lifecycle_event_spec(
        identities, evidence_artifact_id=artifact_id, created_by="test"
    )

    assert event.event_key == "lll_l3harris_stock_merger_2019"
    assert event.last_trading_session.isoformat() == "2019-06-28"
    assert event.settlement_session.isoformat() == "2019-07-01"
    assert event.settlement_legs[0].target_security_id == identities["lhx"]
    assert str(event.settlement_legs[0].quantity_per_source_share) == "1.30"
    assert event.evidence[0].artifact_id == artifact_id
    assert evidence.objects[0].provenance_status == "verified"


def test_aetna_cash_stock_merger_has_exact_sec_backed_settlement() -> None:
    identities = {
        key: uuid.uuid5(uuid.NAMESPACE_URL, key) for key in ("aet", "cvs")
    }
    evidence = official_aet_lifecycle_evidence_spec(created_by="test")
    artifact_id = uuid.uuid4()

    event = frozen_aet_lifecycle_event_spec(
        identities, evidence_artifact_id=artifact_id, created_by="test"
    )

    assert event.event_key == "aet_cvs_cash_stock_merger_2018"
    assert event.last_trading_session.isoformat() == "2018-11-28"
    assert event.settlement_session.isoformat() == "2018-11-28"
    assert [item.leg_kind for item in event.settlement_legs] == [
        "cash",
        "successor_security",
    ]
    assert str(event.settlement_legs[0].cash_amount_per_source_share) == "145.00"
    assert event.settlement_legs[1].target_security_id == identities["cvs"]
    assert str(event.settlement_legs[1].quantity_per_source_share) == "0.8378"
    assert evidence.objects[0].content_sha256 == (
        "7c70f17c710b62228abcb56f51c257dae81bd5a220e9bf7b1cc59a14b2fc03b7"
    )


def test_express_scripts_cash_stock_merger_has_exact_sec_backed_settlement() -> None:
    identities = {
        key: uuid.uuid5(uuid.NAMESPACE_URL, key) for key in ("esrx", "ci")
    }
    evidence = official_esrx_lifecycle_evidence_spec(created_by="test")
    artifact_id = uuid.uuid4()

    event = frozen_esrx_lifecycle_event_spec(
        identities, evidence_artifact_id=artifact_id, created_by="test"
    )

    assert event.event_key == "esrx_cigna_cash_stock_merger_2018"
    assert event.last_trading_session.isoformat() == "2018-12-19"
    assert event.settlement_session.isoformat() == "2018-12-20"
    assert [item.leg_kind for item in event.settlement_legs] == [
        "cash",
        "successor_security",
    ]
    assert str(event.settlement_legs[0].cash_amount_per_source_share) == "48.75"
    assert event.settlement_legs[1].target_security_id == identities["ci"]
    assert str(event.settlement_legs[1].quantity_per_source_share) == "0.2434"
    assert evidence.objects[0].content_sha256 == (
        "2116bc2ee8a5395fc3570923e223180c4042c53101367821c25dde12c0009b26"
    )


def test_time_warner_cash_stock_merger_has_exact_sec_backed_settlement() -> None:
    identities = {
        key: uuid.uuid5(uuid.NAMESPACE_URL, key) for key in ("twx", "t")
    }
    evidence = official_twx_lifecycle_evidence_spec(created_by="test")
    artifact_id = uuid.uuid4()

    event = frozen_twx_lifecycle_event_spec(
        identities, evidence_artifact_id=artifact_id, created_by="test"
    )

    assert event.event_key == "twx_att_cash_stock_merger_2018"
    assert event.announced_at.isoformat() == "2016-10-24T00:00:00+00:00"
    assert event.effective_session.isoformat() == "2018-06-14"
    assert event.last_trading_session.isoformat() == "2018-06-14"
    assert event.settlement_session.isoformat() == "2018-06-14"
    assert [item.leg_kind for item in event.settlement_legs] == [
        "cash",
        "successor_security",
    ]
    assert str(event.settlement_legs[0].cash_amount_per_source_share) == "53.75"
    assert event.settlement_legs[1].target_security_id == identities["t"]
    assert str(event.settlement_legs[1].quantity_per_source_share) == "1.437"
    assert event.details["trading_suspension"] == (
        "prior_to_market_open_2018-06-15"
    )
    assert evidence.objects[0].size_bytes == 53864
    assert evidence.objects[0].content_sha256 == (
        "0a1e0210253d111e84c744449a5fdcb5bc58de7ecef4456fd36b5bcaa58ca28c"
    )
    assert evidence.objects[0].metadata["sec_accession"] == (
        "0000950157-18-000694"
    )
    assert evidence.objects[0].metadata["prior_sec_disclosure_date"] == "2016-10-24"
