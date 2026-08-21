from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from style_rotation.api.actor_context import TrustedLocalActorContext
from style_rotation.api.app import create_app
from style_rotation.lineage.service import ArtifactService
from style_rotation.persistence.database import create_postgres_engine, reset_database
from style_rotation.v022.mutation_admission import MutationAdmissionService
from style_rotation.v022.release_control import ReleaseControlService

DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


class _ApiReader:
    def database_revision(self) -> str:
        return "20260821_142_asset_export"


def _evidence(engine: object, key: str) -> uuid.UUID:
    service = ArtifactService(engine)  # type: ignore[arg-type]
    artifact_type = (
        "v022_shadow_plan"
        if key == "shadow_plan_artifact_id"
        else "v022_shadow_coverage_evidence"
        if key == "shadow_coverage_artifact_id"
        else "v022_operations_readiness_evidence"
        if key == "operations_readiness_artifact_id"
        else "v022_release_gate_test_evidence"
    )
    return service.publish(
        artifact_type=artifact_type,
        artifact_key=key,
        version_number=1,
        semantic_payload={"key": key},
        content_payload={"key": key},
    ).artifact_id


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_release_control_requires_exact_evidence_and_recovery_drills() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    service = ReleaseControlService(engine)
    initial = service.current()
    assert initial.state == "hidden"
    assert initial.default_contract == "v0.21"
    assert initial.v021_research_creation_allowed is True
    assert initial.v022_explicit_creation_allowed is False

    initial_preflight = service.preflight(target="default")
    assert initial_preflight.ready is False
    assert initial_preflight.blocker_codes == (
        "illegal_release_transition",
        "release_evidence_incomplete",
    )
    assert service.current() == initial

    with pytest.raises(ValueError, match="Illegal v0.22 release transition"):
        service.transition(
            target="default",
            reason_code="skip_shadow",
            reason="must fail",
            requested_by="release_operator",
        )

    evidence = {
        key: _evidence(engine, key)
        for key in (
            "shadow_plan_artifact_id",
            "parity_gate_artifact_id",
            "shadow_coverage_artifact_id",
            "operations_readiness_artifact_id",
            "restore_drill_artifact_id",
            "rollback_drill_artifact_id",
            "incident_impact_analysis_artifact_id",
        )
    }
    shadow = service.transition(
        target="shadow",
        reason_code="begin_shadow",
        reason="start representative dual run",
        requested_by="release_operator",
        gate_evidence={"shadow_plan_artifact_id": evidence["shadow_plan_artifact_id"]},
        requested_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
    )
    assert shadow.sequence_number == 1
    assert service.current().shadow_runtime_allowed is True

    explicit = service.transition(
        target="explicit_eligible",
        reason_code="open_explicit",
        reason="parity gate passed",
        requested_by="release_operator",
        gate_evidence={"parity_gate_artifact_id": evidence["parity_gate_artifact_id"]},
    )
    assert explicit.from_state == "shadow"
    with pytest.raises(ValueError, match="evidence is incomplete"):
        service.transition(
            target="default",
            reason_code="premature_default",
            reason="coverage is not frozen",
            requested_by="release_operator",
            gate_evidence={"parity_gate_artifact_id": evidence["parity_gate_artifact_id"]},
        )

    default_evidence = {
        key: evidence[key]
        for key in (
            "parity_gate_artifact_id",
            "shadow_coverage_artifact_id",
            "operations_readiness_artifact_id",
            "restore_drill_artifact_id",
            "rollback_drill_artifact_id",
        )
    }
    default_preflight = service.preflight(
        target="default", gate_evidence=default_evidence
    )
    assert default_preflight.ready is False
    assert default_preflight.blocker_codes == ("release_evidence_invalid",)
    assert service.current().state == "explicit_eligible"
    with pytest.raises(ValueError, match="not ready for default"):
        service.transition(
            target="default",
            reason_code="cutover",
            reason="a typed but non-ready coverage artifact is insufficient",
            requested_by="release_operator",
            gate_evidence=default_evidence,
        )

    rollback = service.transition(
        target="maintenance_read_only",
        reason_code="runtime_incident",
        reason="protect new submissions while impact is assessed",
        requested_by="incident_commander",
        incident={"incident_id": "INC-022-1", "impact_summary": "decision freshness"},
    )
    assert rollback.to_state == "maintenance_read_only"
    assert service.current().maintenance_read_only is True
    with pytest.raises(ValueError, match="evidence is incomplete"):
        service.transition(
            target="explicit_eligible",
            reason_code="unsafe_recovery",
            reason="missing drill evidence",
            requested_by="incident_commander",
            gate_evidence={"parity_gate_artifact_id": evidence["parity_gate_artifact_id"]},
        )

    with pytest.raises(ValueError, match="wrong Artifact type"):
        service.transition(
            target="explicit_eligible",
            reason_code="controlled_recovery",
            reason="generic labels cannot impersonate formal recovery drills",
            requested_by="incident_commander",
            gate_evidence={
                key: evidence[key]
                for key in (
                    "incident_impact_analysis_artifact_id",
                    "parity_gate_artifact_id",
                    "restore_drill_artifact_id",
                    "rollback_drill_artifact_id",
                )
            },
        )
    assert service.current().state == "maintenance_read_only"

    with engine.begin() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text(
                "UPDATE workspace.v022_release_transition SET reason='rewrite' "
                "WHERE release_transition_id=:transition"
            ),
            {"transition": rollback.release_transition_id},
        )
    engine.dispose()


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_api_mutation_guard_and_release_status_follow_database_state() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    release = ReleaseControlService(engine)
    client = TestClient(
        create_app(
            _ApiReader(),
            graph_drafts=object(),  # type: ignore[arg-type]
            mutation_admission=MutationAdmissionService(engine),
            release_control=release,
            actor_context=TrustedLocalActorContext(
                actor_key="local", operator_enabled=True
            ),
        )
    )
    initial = client.get("/api/v2/release-control")
    assert initial.status_code == 200
    assert initial.json()["state"] == "hidden"
    assert initial.json()["v021_research_creation_allowed"] is True
    assert initial.json()["v022_explicit_creation_allowed"] is False
    hidden_write = client.post(
        "/api/v2/workspace/graph-drafts",
        json={
            "researcher_key": "local",
            "draft_key": "blocked-hidden",
            "name": "blocked",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert hidden_write.status_code == 409
    assert hidden_write.json()["details"]["release_state"] == "hidden"
    assert client.post("/api/v2/workspace/graph-preview", json={}).status_code == 200

    shadow_plan = ArtifactService(engine).publish(
        artifact_type="v022_shadow_plan",
        artifact_key="api_guard_shadow_plan",
        version_number=1,
        semantic_payload={"scope": "api guard integration"},
        content_payload={"scope": "api guard integration"},
    )
    release.transition(
        target="shadow",
        reason_code="api_guard_shadow",
        reason="exercise the database-authoritative API guard",
        requested_by="integration_test",
        gate_evidence={"shadow_plan_artifact_id": shadow_plan.artifact_id},
    )
    release.transition(
        target="maintenance_read_only",
        reason_code="api_guard_maintenance",
        reason="verify maintenance mode is visible and write-locked",
        requested_by="integration_test",
        incident={"incident_id": "DRILL-API-022", "impact_summary": "controlled"},
    )
    maintenance = client.get("/api/v2/release-control")
    assert maintenance.status_code == 200
    assert maintenance.json()["state"] == "maintenance_read_only"
    assert maintenance.json()["context"]["read_only"] is True
    assert client.get("/api/v2/health").json()["context"]["read_only"] is True
    locked = client.post(
        "/api/v2/workspace/graph-drafts",
        json={
            "researcher_key": "local",
            "draft_key": "blocked-maintenance",
            "name": "blocked",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert locked.status_code == 423
    assert locked.json()["details"]["reason_code"] == "release_maintenance_read_only"
    engine.dispose()
