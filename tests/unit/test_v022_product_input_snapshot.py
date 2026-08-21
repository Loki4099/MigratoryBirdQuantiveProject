from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest

from style_rotation.v022.product_input_snapshot import (
    ProductInputSnapshotSpec,
    _load_source,
    _member_document,
    _validate_source,
)


def test_product_input_snapshot_accepts_new_exact_published_dataset_version() -> None:
    source = _source()
    source["dataset_key"] = source["baseline_dataset_key"]

    _validate_source(source)


def test_product_input_snapshot_rejects_dynamic_range_or_methodology_drift() -> None:
    source = _source()
    source["dataset_coverage_start"] = date(2002, 1, 3)
    with pytest.raises(ValueError, match="exact decision range"):
        _validate_source(source)

    source = _source()
    source["universe_methodology_id"] = uuid.uuid4()
    with pytest.raises(ValueError, match="data methodology"):
        _validate_source(source)


def test_product_input_snapshot_rejects_ineligible_gate_and_runtime_network_policy() -> None:
    source = _source()
    source["product_eligibility"] = "ineligible"
    source["blocker_count"] = 1
    with pytest.raises(ValueError, match="ineligible"):
        _validate_source(source)

    source = _source()
    source["disclosure_document"]["future_input_policy"]["runtime_network_access"] = True
    with pytest.raises(ValueError, match="offline future inputs"):
        _validate_source(source)


def test_product_input_snapshot_spec_requires_actor() -> None:
    with pytest.raises(ValueError, match="created_by"):
        ProductInputSnapshotSpec(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), " ")


def test_product_input_source_projects_exact_gate_identity_for_member_loading() -> None:
    class Result:
        def mappings(self) -> Result:
            return self

        def one_or_none(self) -> dict[str, object]:
            return {"dataset_gate_assessment_id": gate_id}

    class Connection:
        statement = ""

        def execute(self, statement: object, parameters: object) -> Result:
            del parameters
            self.statement = str(statement)
            return Result()

    gate_id = uuid.uuid4()
    connection = Connection()
    row = _load_source(
        connection,  # type: ignore[arg-type]
        ProductInputSnapshotSpec(uuid.uuid4(), uuid.uuid4(), gate_id, "tester"),
    )

    assert row["dataset_gate_assessment_id"] == gate_id
    assert "gate.dataset_gate_assessment_id" in connection.statement


def test_product_input_member_freezes_warmup_and_exclusion_state() -> None:
    identity = uuid.uuid4()
    document = _member_document(
        {
            "ordinal": 7,
            "universe_snapshot_id": uuid.uuid4(),
            "security_id": identity,
            "legacy_asset_id": uuid.uuid4(),
            "security_key": "sample_security",
            "observed_session_count": 503,
            "is_uniformly_excluded": False,
            "is_terminal": False,
        }  # type: ignore[arg-type]
    )
    assert document["security_id"] == str(identity)
    assert document["is_warmup_ready"] is False
    assert document["is_selectable"] is False
    assert document["reason_codes"] == ["warmup_504_incomplete"]

    excluded = _member_document(
        {
            **document,
            "security_key": "sample_security",
            "observed_session_count": 504,
            "is_uniformly_excluded": True,
            "is_terminal": False,
        }  # type: ignore[arg-type]
    )
    assert excluded["is_warmup_ready"] is True
    assert excluded["is_selectable"] is False
    assert excluded["reason_codes"] == ["uniform_provider_exclusion"]


def _source() -> dict[str, Any]:
    cutoff = datetime(2026, 8, 21, 20, 0, tzinfo=UTC)
    available = datetime(2026, 8, 21, 20, 5, tzinfo=UTC)
    methodology = uuid.uuid4()
    calendar_definition = uuid.uuid4()
    return {
        "enrollment_status": "published",
        "disclosure_status": "published",
        "gate_status": "published",
        "dataset_status": "published",
        "history_status": "published",
        "calendar_status": "published",
        "disclosure_document": {
            "future_input_policy": {
                "require_published_dataset_universe_manifest": True,
                "require_gate_assessment": True,
                "stop_on_new_product_ineligible_blocker": True,
                "preserve_prior_decisions_and_evidence": True,
                "runtime_network_access": False,
            }
        },
        "ordinal": 5,
        "first_ordinal": 5,
        "lifecycle": "active",
        "product_eligibility": "eligible_with_warnings",
        "blocker_count": 0,
        "dataset_kind": "canonical",
        "value_kind": "daily_bar",
        "dataset_key": "sp500_daily_market",
        "baseline_dataset_key": "sp500_daily_market",
        "universe_methodology_id": methodology,
        "baseline_methodology_id": methodology,
        "calendar_definition_id": calendar_definition,
        "baseline_calendar_definition_id": calendar_definition,
        "warmup_start": date(2001, 1, 3),
        "required_history_sessions": 504,
        "session_date": date(2026, 8, 21),
        "assessed_coverage_start": date(2001, 1, 3),
        "assessed_coverage_end": date(2026, 8, 21),
        "dataset_coverage_start": date(2001, 1, 3),
        "dataset_coverage_end": date(2026, 8, 21),
        "calendar_coverage_start": date(2001, 1, 3),
        "calendar_coverage_end": date(2026, 8, 21),
        "calendar_session_exists": True,
        "decision_cutoff_at": cutoff,
        "inputs_available_at": available,
    }
