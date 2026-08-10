from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from style_rotation.api.app import create_app
from style_rotation.api.commands import ApplicationCommandService
from style_rotation.api.query import ArtifactQueryService
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_workspace_draft_is_persistent_and_revision_guarded() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    client = TestClient(
        create_app(ArtifactQueryService(engine), commands=ApplicationCommandService(engine))
    )
    selection = {
        "frequency": "weekly",
        "asset_security_ids": [],
        "asset_data_inputs": {},
        "factor_variant_keys": ["total_return__w120"],
        "signal_version_keys": ["return_continuation__total_return__w120"],
        "model_preset_keys": ["single_signal__identity_v1"],
        "model_target_keys": ["cross_sectional_relative_return__h5"],
        "strategy_preset_keys": ["multi_etf_top_k__k2__none__none__none"],
    }
    create_request = {
        "idempotency_key": str(uuid.uuid4()),
        "researcher_id": "local",
        "draft_key": "default",
        "name": "Local research draft",
        "expected_revision": None,
        "selection": selection,
    }
    created = client.put("/api/v2/workspace/drafts/local/default", json=create_request)
    assert created.status_code == 200
    assert created.json()["revision"] == 1
    repeated = client.put("/api/v2/workspace/drafts/local/default", json=create_request)
    assert repeated.status_code == 200
    assert repeated.json()["revision"] == 1
    loaded = client.get("/api/v2/workspace/drafts/local/default")
    assert loaded.status_code == 200
    assert loaded.json()["selection"] == selection
    updated = client.put(
        "/api/v2/workspace/drafts/local/default",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "researcher_id": "local",
            "draft_key": "default",
            "name": "Renamed",
            "expected_revision": 1,
            "selection": selection,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    conflict = client.put(
        "/api/v2/workspace/drafts/local/default",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "researcher_id": "local",
            "draft_key": "default",
            "name": "Stale overwrite",
            "expected_revision": 1,
            "selection": selection,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "revision_conflict"
    gates = client.get("/api/v2/release-gates")
    assert gates.status_code == 200
    assert gates.json()["formal_enabled"] is False
    assert gates.json()["reason_codes"] == [
        "pit_universe_gate_open",
        "terminal_event_gate_open",
        "impact_policy_gate_open",
    ]
    blocked_suite = client.post(
        "/api/v2/workspace/suites",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "researcher_id": "local",
            "draft_key": "default",
            "expected_revision": 2,
            "suite_mode": "formal",
        },
    )
    assert blocked_suite.status_code == 409
    assert blocked_suite.json()["code"] == "formal_submission_blocked"
    assert blocked_suite.json()["details"]["reason_codes"] == gates.json()["reason_codes"]
