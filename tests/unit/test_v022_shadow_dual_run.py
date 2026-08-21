from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from style_rotation.v022.shadow_dual_run import (
    RuntimeCapability,
    RuntimeContract,
    _v021_reference_document,
    worker_supports,
)
from style_rotation.v022.shadow_v021_replay import V021ShadowReplayDecision


def _capability(
    runtime: RuntimeContract = "v0.22",
    *,
    compiler: str = "compiler-22.0",
    executor: str = "executor-22.0",
    environment: str = "a" * 64,
    key: str = "deterministic-product-decision",
) -> RuntimeCapability:
    return RuntimeCapability(
        runtime,
        compiler,
        executor,
        environment,
        key,
    )


def test_worker_capability_requires_an_exact_frozen_match() -> None:
    required = _capability()

    assert worker_supports(required, (required,)) is True
    assert worker_supports(required, (_capability(executor="executor-22.1"),)) is False
    assert worker_supports(required, (_capability(environment="b" * 64),)) is False
    assert worker_supports(required, (_capability(key="research-graph"),)) is False


def test_one_worker_can_advertise_n_and_n_minus_one_without_cross_claiming() -> None:
    v021 = _capability(
        "v0.21",
        compiler="compiler-21.9",
        executor="executor-21.9",
        key="legacy-product-decision",
    )
    v022 = _capability()
    advertised = (v021, v022)

    assert worker_supports(v021, advertised) is True
    assert worker_supports(v022, advertised) is True
    assert worker_supports(
        _capability("v0.21", compiler="compiler-21.8", executor="executor-21.9"),
        advertised,
    ) is False


@pytest.mark.parametrize(
    "capability",
    (
        _capability(compiler=""),
        _capability(environment="not-a-hash"),
        _capability(key=" "),
    ),
)
def test_invalid_worker_capability_fails_closed(capability: RuntimeCapability) -> None:
    with pytest.raises(ValueError):
        worker_supports(capability, ())


def test_v021_monitoring_snapshot_projects_an_exact_shadow_reference() -> None:
    snapshot_id = uuid.uuid4()
    bundle_id = uuid.uuid4()
    document = _v021_reference_document(
        {
            "artifact_id": snapshot_id,
            "data_bundle_artifact_id": bundle_id,
            "as_of_session": date(2026, 8, 10),
            "known_at": datetime(2026, 8, 10, 21, tzinfo=UTC),
            "recommended_execution_date": date(2026, 8, 11),
            "metrics": {
                "pending_decision_date": "2026-08-10",
                "pending_target_holdings": [
                    {"asset_key": "iwf", "target_weight": "0.5"}
                ],
            },
            "health_components": {"reason_codes": []},
        }
    )

    assert document["decision_status"] == "completed"
    assert document["recommended_execution_date"] == "2026-08-11"
    assert document["source_monitoring_snapshot_artifact_id"] == str(snapshot_id)
    assert document["source_data_bundle_artifact_id"] == str(bundle_id)


def test_v021_monitoring_interruption_projects_missing_without_a_fake_target() -> None:
    document = _v021_reference_document(
        {
            "artifact_id": uuid.uuid4(),
            "data_bundle_artifact_id": uuid.uuid4(),
            "as_of_session": date(2026, 8, 10),
            "known_at": datetime(2026, 8, 10, 21, tzinfo=UTC),
            "recommended_execution_date": date(2026, 8, 11),
            "metrics": {"pending_target_holdings": []},
            "health_components": {"reason_codes": ["data_contract_interrupted"]},
        }
    )

    assert document["decision_status"] == "missing"
    assert document["recommended_execution_date"] is None
    assert document["reason_codes"] == ["data_contract_interrupted"]


def test_shadow_only_replay_decision_keeps_exact_bundle_provenance() -> None:
    bundle_id = uuid.uuid4()
    decision = V021ShadowReplayDecision(
        {
            "decision_status": "completed",
            "decision_session": "2026-08-10",
            "recommended_execution_date": "2026-08-11",
            "positions": [{"asset_key": "iwf", "target_weight": "0.5"}],
            "reason_codes": [],
            "source_data_bundle_artifact_id": str(bundle_id),
        },
        bundle_id,
        datetime(2026, 8, 10, 20, tzinfo=UTC),
    )

    assert decision.source_artifact_id == bundle_id
    assert decision.decision_document["source_data_bundle_artifact_id"] == str(bundle_id)
