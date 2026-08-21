from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from style_rotation.api.actor_context import TrustedLocalActorContext
from style_rotation.api.app import create_app
from style_rotation.v022.draft_service import GraphDraftCompileResult
from style_rotation.v022.mutation_admission import (
    MutationAdmissionDecision,
    MutationAdmissionDenied,
    MutationScope,
    decide_mutation_admission,
)
from style_rotation.v022.suite_runtime_commands import GraphSuiteResultsNotReady


def test_mutation_admission_matrix_matches_cutover_contract() -> None:
    assert decide_mutation_admission("hidden", "v021_research").allowed is True
    assert decide_mutation_admission("shadow", "v021_research").allowed is True
    assert decide_mutation_admission("explicit_eligible", "v022_research").allowed is True
    assert decide_mutation_admission("default", "v022_research").allowed is True
    retired = decide_mutation_admission("default", "v021_research")
    assert retired.allowed is False
    assert retired.reason_code == "v021_research_creation_retired"
    hidden = decide_mutation_admission("hidden", "v022_research")
    assert hidden.allowed is False
    assert hidden.reason_code == "v022_explicit_creation_not_enabled"
    for scope in (
        "v021_research",
        "v022_research",
        "product_operations",
        "suite_cancellation",
    ):
        blocked = decide_mutation_admission("maintenance_read_only", scope)
        assert blocked.allowed is False
        assert blocked.reason_code == "release_maintenance_read_only"
    assert (
        decide_mutation_admission("maintenance_read_only", "historical_export").allowed
        is True
    )


class _Reader:
    def database_revision(self) -> str:
        return "20260821_142_asset_export"


class _Admission:
    def __init__(self, state: str) -> None:
        self.state = state
        self.calls: list[MutationScope] = []

    def require(self, scope: MutationScope) -> MutationAdmissionDecision:
        self.calls.append(scope)
        decision = decide_mutation_admission(self.state, scope)  # type: ignore[arg-type]
        if not decision.allowed:
            raise MutationAdmissionDenied(decision)
        return decision


class _Commands:
    def __init__(self, *, replay: bool) -> None:
        self.replay = replay
        self.save_calls = 0
        self.cached = {
            "research_draft_id": uuid.uuid4(),
            "researcher_id": "local",
            "draft_key": "default",
            "name": "Frozen v0.21 draft",
            "revision": 1,
            "selection": _selection(),
            "last_compiled_artifact_id": None,
        }

    def idempotent(
        self,
        *,
        command_name: str,
        idempotency_key: uuid.UUID,
        request: dict[str, Any],
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        del command_name, idempotency_key, request
        return self.cached if self.replay else operation()

    def save_workspace_draft(self, **_kwargs: object) -> dict[str, Any]:
        self.save_calls += 1
        return self.cached


class _GraphSuites:
    def __init__(self, *, replay: bool) -> None:
        self.should_replay = replay
        self.submit_calls = 0
        self.graph_id = uuid.uuid4()
        self.suite_id = uuid.uuid4()
        self.cached = {
            "contract_version": "v0.22.0",
            "research_suite_id": self.suite_id,
            "suite_artifact_id": uuid.uuid4(),
            "compiled_research_graph_id": self.graph_id,
            "graph_fingerprint": "a" * 64,
            "suite_fingerprint": "b" * 64,
            "strategy_branch_count": 2,
            "backtest_cell_count": 2,
            "status": "materializing",
            "reused": replay,
            "suite_mode": "exploratory",
        }

    def replay(self, **kwargs: object) -> dict[str, Any] | None:
        assert kwargs["compiled_research_graph_id"] == self.graph_id
        return self.cached if self.should_replay else None

    def submit(self, **kwargs: object) -> dict[str, Any]:
        assert kwargs["compiled_research_graph_id"] == self.graph_id
        self.submit_calls += 1
        return self.cached

    def status(self, research_suite_id: uuid.UUID) -> dict[str, Any]:
        assert research_suite_id == self.suite_id
        return {
            "contract_version": "v0.22.0",
            "research_suite_id": self.suite_id,
            "compiled_research_graph_id": self.graph_id,
            "status": "materializing",
            "total": 9,
            "terminal": 0,
            "complete": False,
            "status_counts": {"queued": 9},
            "suite_mode": "exploratory",
        }

    def results(self, research_suite_id: uuid.UUID) -> dict[str, Any]:
        assert research_suite_id == self.suite_id
        return {
            "research_suite_id": self.suite_id,
            "compiled_research_graph_id": self.graph_id,
            "status": "completed",
            "complete": True,
            "expected_result_count": 1,
            "result_count": 1,
            "results": [
                {
                    "research_cell_id": uuid.uuid4(),
                    "research_suite_branch_id": uuid.uuid4(),
                    "compiled_strategy_branch_id": uuid.uuid4(),
                    "configuration_snapshot_id": uuid.uuid4(),
                    "portfolio_evaluation_data_context_id": uuid.uuid4(),
                    "result_artifact_id": uuid.uuid4(),
                    "payload_manifest_id": uuid.uuid4(),
                    "payload_manifest_artifact_id": uuid.uuid4(),
                    "result_fingerprint": "c" * 64,
                    "logical_payload_fingerprint": "d" * 64,
                    "manifest_hash": "e" * 64,
                    "outcome": "accepted",
                    "quality_status": "passed",
                    "effective_start": "2024-01-02",
                    "effective_end": "2024-12-31",
                    "metric_document": {"total_return": 0.1},
                    "result_document": {"quality": {"outcome": "accepted"}},
                    "diagnostic": {
                        "metrics": [
                            {
                                "metric_group": "absolute",
                                "metric_key": "total_return",
                                "value": "0.1",
                                "value_status": "defined",
                                "reason_code": None,
                                "observation_count": 252,
                            }
                        ],
                        "quality": {
                            "outcome": "accepted",
                            "status": "passed",
                            "reason_code": None,
                            "details": {},
                            "path_session_count": 252,
                        },
                        "execution": {
                            "benchmark_asset_id": None,
                            "benchmark_asset_key": "spy",
                            "cost_policy_key": "linear_bps",
                            "basis_points_per_side": "5",
                            "execution_delay_sessions": 1,
                            "evaluation_input_cutoff_at": None,
                            "work_execution_fingerprint": "f" * 64,
                            "evaluation_data_context_fingerprint": "a" * 64,
                        },
                        "evidence": {
                            "publication_status": "not_published",
                            "result_evidence_snapshot_id": None,
                            "result_evidence_artifact_id": None,
                            "evidence_fingerprint": None,
                            "evidence_class": None,
                            "common_evaluation_panel_id": None,
                            "common_evaluation_panel_fingerprint": None,
                        },
                        "elements": [],
                    },
                }
            ],
        }

    def list_suites(self, *, limit: int, offset: int) -> dict[str, Any]:
        assert limit == 25
        assert offset == 0
        return {
            "items": [
                {
                    "research_suite_id": self.suite_id,
                    "compiled_research_graph_id": self.graph_id,
                    "graph_fingerprint": "a" * 64,
                    "suite_fingerprint": "b" * 64,
                    "status": "materializing",
                    "total": 9,
                    "terminal": 0,
                    "complete": False,
                    "status_counts": {"queued": 9},
                    "strategy_branch_count": 2,
                    "backtest_cell_count": 2,
                    "suite_mode": "exploratory",
                    "created_at": "2026-08-13T12:00:00Z",
                }
            ],
            "total_count": 1,
            "limit": limit,
            "offset": offset,
        }


class _PendingGraphSuites(_GraphSuites):
    def results(self, research_suite_id: uuid.UUID) -> dict[str, Any]:
        assert research_suite_id == self.suite_id
        raise GraphSuiteResultsNotReady(
            {
                "status": "not_started",
                "total": 0,
                "terminal": 0,
            }
        )


class _GraphDrafts:
    def __init__(self, *, replay: bool) -> None:
        self.should_replay = replay
        self.compile_calls = 0
        self.lock_calls = 0
        self.draft_id = uuid.uuid4()
        self.result = GraphDraftCompileResult(
            graph_draft_id=self.draft_id,
            graph_draft_revision=3,
            draft_intent_id=uuid.uuid4(),
            compile_attempt_id=uuid.uuid4(),
            compiled_research_graph_id=uuid.uuid4(),
            graph_artifact_id=uuid.uuid4(),
            graph_fingerprint="a" * 64,
            reused=replay,
            compiled_execution_data_context_id=uuid.uuid4(),
            execution_data_context_artifact_id=uuid.uuid4(),
            execution_data_context_fingerprint="b" * 64,
            execution_data_context_reused=replay,
        )

    def replay_compile(
        self, graph_draft_id: uuid.UUID, **kwargs: object
    ) -> GraphDraftCompileResult | None:
        assert graph_draft_id == self.draft_id
        assert kwargs["expected_revision"] == 3
        return self.result if self.should_replay else None

    def compile(
        self, graph_draft_id: uuid.UUID, **kwargs: object
    ) -> GraphDraftCompileResult:
        assert graph_draft_id == self.draft_id
        self.compile_calls += 1
        return self.result

    def current_compile(
        self, graph_draft_id: uuid.UUID, **kwargs: object
    ) -> GraphDraftCompileResult | None:
        assert graph_draft_id == self.draft_id
        assert kwargs["actor_key"] == "local"
        return self.result if self.should_replay else None

    def lock_for_experiment(
        self, graph_draft_id: uuid.UUID, **kwargs: object
    ) -> object:
        assert graph_draft_id == self.draft_id
        assert kwargs["expected_revision"] == 3
        assert kwargs["compiled_research_graph_id"] == self.result.compiled_research_graph_id
        self.lock_calls += 1
        return object()


def _selection() -> dict[str, object]:
    return {
        "frequency": "weekly",
        "asset_security_ids": [],
        "asset_data_inputs": {},
        "factor_variant_keys": ["total_return__w120"],
        "signal_version_keys": ["return_continuation__total_return__w120"],
        "model_preset_keys": ["single_signal__identity_v1"],
        "model_target_keys": ["cross_sectional_relative_return__h5"],
        "strategy_preset_keys": ["multi_etf_top_k__k2__none__none__none"],
    }


def _save_request() -> dict[str, object]:
    return {
        "idempotency_key": str(uuid.uuid4()),
        "researcher_id": "local",
        "draft_key": "default",
        "name": "Frozen v0.21 draft",
        "expected_revision": None,
        "selection": _selection(),
    }


def test_api_guard_blocks_new_write_but_preserves_exact_idempotency_replay() -> None:
    admission = _Admission("default")
    commands = _Commands(replay=False)
    client = TestClient(
        create_app(
            _Reader(),
            commands=commands,  # type: ignore[arg-type]
            mutation_admission=admission,  # type: ignore[arg-type]
        )
    )
    blocked = client.put(
        "/api/v2/workspace/drafts/local/default", json=_save_request()
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "mutation_admission_blocked"
    assert blocked.json()["details"] == {
        "scope": "v021_research",
        "release_state": "default",
        "reason_code": "v021_research_creation_retired",
    }
    assert commands.save_calls == 0

    replay_admission = _Admission("default")
    replay_commands = _Commands(replay=True)
    replay_client = TestClient(
        create_app(
            _Reader(),
            commands=replay_commands,  # type: ignore[arg-type]
            mutation_admission=replay_admission,  # type: ignore[arg-type]
        )
    )
    replay = replay_client.put(
        "/api/v2/workspace/drafts/local/default", json=_save_request()
    )
    assert replay.status_code == 200
    assert replay.json()["revision"] == 1
    assert replay_admission.calls == []
    assert replay_commands.save_calls == 0


def test_graph_suite_results_not_ready_is_a_structured_conflict() -> None:
    suites = _PendingGraphSuites(replay=False)
    client = TestClient(create_app(_Reader(), graph_suites=suites))  # type: ignore[arg-type]

    response = client.get(
        f"/api/v2/workspace/graph-suites/{suites.suite_id}/results"
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "graph_suite_results_not_ready",
        "message": (
            "v0.22 Graph Suite results are not ready: "
            "status=not_started, terminal=0/0"
        ),
        "details": {"status": "not_started", "total": 0, "terminal": 0},
    }


def test_graph_suite_uses_v022_admission_and_replays_before_current_release_gate() -> None:
    admission = _Admission("explicit_eligible")
    suites = _GraphSuites(replay=False)
    drafts = _GraphDrafts(replay=False)
    drafts.result = replace(
        drafts.result, compiled_research_graph_id=suites.graph_id
    )
    client = TestClient(
        create_app(
            _Reader(),
            graph_suites=suites,
            graph_drafts=drafts,  # type: ignore[arg-type]
            mutation_admission=admission,  # type: ignore[arg-type]
        )
    )
    request = {
        "compiled_research_graph_id": str(suites.graph_id),
        "graph_draft_id": str(drafts.draft_id),
        "graph_draft_revision": 3,
        "actor_key": "local",
        "idempotency_key": str(uuid.uuid4()),
        "suite_mode": "exploratory",
    }
    submitted = client.post("/api/v2/workspace/graph-suites", json=request)
    assert submitted.status_code == 200
    assert submitted.json()["compiled_research_graph_id"] == str(suites.graph_id)
    assert admission.calls == ["v022_research"]
    assert suites.submit_calls == 1
    assert drafts.lock_calls == 1
    status = client.get(f"/api/v2/workspace/graph-suites/{suites.suite_id}")
    assert status.status_code == 200
    assert status.json()["research_suite_id"] == str(suites.suite_id)
    assert status.json()["status_counts"] == {"queued": 9}
    results = client.get(
        f"/api/v2/workspace/graph-suites/{suites.suite_id}/results"
    )
    assert results.status_code == 200
    assert results.json()["result_count"] == 1
    assert results.json()["results"][0]["quality_status"] == "passed"
    history = client.get("/api/v2/workspace/graph-suites?limit=25&offset=0")
    assert history.status_code == 200
    assert history.json()["total_count"] == 1
    assert history.json()["items"][0]["research_suite_id"] == str(suites.suite_id)

    maintenance = _Admission("maintenance_read_only")
    replay_suites = _GraphSuites(replay=True)
    replay_drafts = _GraphDrafts(replay=True)
    replay_drafts.result = replace(
        replay_drafts.result, compiled_research_graph_id=replay_suites.graph_id
    )
    replay_request = {
        **request,
        "compiled_research_graph_id": str(replay_suites.graph_id),
        "graph_draft_id": str(replay_drafts.draft_id),
    }
    replay_client = TestClient(
        create_app(
            _Reader(),
            graph_suites=replay_suites,
            graph_drafts=replay_drafts,  # type: ignore[arg-type]
            mutation_admission=maintenance,  # type: ignore[arg-type]
        )
    )
    replayed = replay_client.post("/api/v2/workspace/graph-suites", json=replay_request)
    assert replayed.status_code == 200
    assert replayed.json()["reused"] is True
    assert maintenance.calls == []
    assert replay_suites.submit_calls == 0
    assert replay_drafts.lock_calls == 1


def test_graph_compile_exact_replay_precedes_maintenance_admission() -> None:
    request_key = uuid.uuid4()
    blocked_admission = _Admission("maintenance_read_only")
    blocked_drafts = _GraphDrafts(replay=False)
    blocked_client = TestClient(
        create_app(
            _Reader(),
            graph_drafts=blocked_drafts,  # type: ignore[arg-type]
            mutation_admission=blocked_admission,  # type: ignore[arg-type]
        )
    )
    request = {
        "expected_revision": 3,
        "actor_key": "local",
        "idempotency_key": str(request_key),
    }
    blocked = blocked_client.post(
        f"/api/v2/workspace/graph-drafts/{blocked_drafts.draft_id}/compile",
        json=request,
    )
    assert blocked.status_code == 423
    assert blocked_drafts.compile_calls == 0
    assert blocked_admission.calls == ["v022_research"]

    replay_admission = _Admission("maintenance_read_only")
    replay_drafts = _GraphDrafts(replay=True)
    replay_client = TestClient(
        create_app(
            _Reader(),
            graph_drafts=replay_drafts,  # type: ignore[arg-type]
            mutation_admission=replay_admission,  # type: ignore[arg-type]
        )
    )
    replayed = replay_client.post(
        f"/api/v2/workspace/graph-drafts/{replay_drafts.draft_id}/compile",
        json=request,
    )
    assert replayed.status_code == 200
    assert replayed.json()["compiled_research_graph_id"] == str(
        replay_drafts.result.compiled_research_graph_id
    )
    assert replay_admission.calls == []
    assert replay_drafts.compile_calls == 0


def test_current_graph_compile_is_read_only_and_actor_scoped() -> None:
    drafts = _GraphDrafts(replay=True)
    admission = _Admission("maintenance_read_only")
    client = TestClient(
        create_app(
            _Reader(),
            graph_drafts=drafts,  # type: ignore[arg-type]
            mutation_admission=admission,  # type: ignore[arg-type]
            actor_context=TrustedLocalActorContext(
                actor_key="local", operator_enabled=False
            ),
        )
    )

    response = client.get(
        f"/api/v2/workspace/graph-drafts/{drafts.draft_id}/current-compile"
    )

    assert response.status_code == 200
    assert response.json()["compile_attempt_id"] == str(drafts.result.compile_attempt_id)
    assert admission.calls == []


def test_failed_suite_submission_does_not_lock_the_current_research() -> None:
    drafts = _GraphDrafts(replay=False)
    suites = _GraphSuites(replay=False)
    suites.graph_id = drafts.result.compiled_research_graph_id

    def fail_submit(**_kwargs: object) -> dict[str, Any]:
        raise RuntimeError("suite identity publication failed")

    suites.submit = fail_submit  # type: ignore[method-assign]
    client = TestClient(
        create_app(
            _Reader(),
            graph_drafts=drafts,  # type: ignore[arg-type]
            graph_suites=suites,  # type: ignore[arg-type]
            mutation_admission=_Admission("explicit_eligible"),  # type: ignore[arg-type]
        ),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/api/v2/workspace/graph-suites",
        json={
            "compiled_research_graph_id": str(drafts.result.compiled_research_graph_id),
            "graph_draft_id": str(drafts.draft_id),
            "graph_draft_revision": 3,
            "actor_key": "local",
            "idempotency_key": str(uuid.uuid4()),
            "suite_mode": "exploratory",
        },
    )

    assert response.status_code == 500
    assert drafts.lock_calls == 0


def test_graph_suite_runtime_is_fail_closed_and_never_falls_back_to_v021() -> None:
    admission = _Admission("explicit_eligible")
    client = TestClient(
        create_app(_Reader(), mutation_admission=admission)  # type: ignore[arg-type]
    )
    response = client.post(
        "/api/v2/workspace/graph-suites",
        json={
            "compiled_research_graph_id": str(uuid.uuid4()),
            "graph_draft_id": str(uuid.uuid4()),
            "graph_draft_revision": 1,
            "actor_key": "local",
            "idempotency_key": str(uuid.uuid4()),
            "suite_mode": "exploratory",
        },
    )
    assert response.status_code == 503
    assert "runtime is not enabled" in response.json()["message"]
    assert admission.calls == []
    status = client.get(f"/api/v2/workspace/graph-suites/{uuid.uuid4()}")
    assert status.status_code == 503
    assert "runtime is not enabled" in status.json()["message"]


def test_maintenance_blocks_persistent_graph_preview_but_allows_pure_preview() -> None:
    admission = _Admission("maintenance_read_only")
    client = TestClient(
        create_app(
            _Reader(),
            graph_drafts=object(),  # type: ignore[arg-type]
            mutation_admission=admission,  # type: ignore[arg-type]
        )
    )
    blocked = client.post(
        f"/api/v2/workspace/graph-drafts/{uuid.uuid4()}/change-previews",
        json={
            "expected_revision": 1,
            "actor_key": "local",
            "feature_key": "return_continuation__w120",
            "stage_no": 3,
        },
    )
    assert blocked.status_code == 423
    assert blocked.json()["details"]["reason_code"] == "release_maintenance_read_only"

    pure = client.post("/api/v2/workspace/graph-preview", json={})
    assert pure.status_code == 200


def test_api_actor_comes_from_server_context_and_rejects_spoofed_claim() -> None:
    admission = _Admission("explicit_eligible")
    actor_context = TrustedLocalActorContext(actor_key="local", operator_enabled=False)
    client = TestClient(
        create_app(
            _Reader(),
            graph_drafts=object(),  # type: ignore[arg-type]
            mutation_admission=admission,  # type: ignore[arg-type]
            actor_context=actor_context,
        )
    )
    session = client.get("/api/v2/session")
    assert session.status_code == 200
    assert session.json()["actor_key"] == "local"
    assert session.json()["roles"] == ["researcher"]
    assert session.json()["authentication_source"] == (
        "trusted_local_server_configuration"
    )

    spoofed = client.post(
        "/api/v2/workspace/graph-drafts",
        json={
            "researcher_key": "another-user",
            "draft_key": "spoofed",
            "name": "must not reach service",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert spoofed.status_code == 403
    assert spoofed.json()["code"] == "actor_claim_mismatch"
    assert spoofed.json()["details"] == {"authenticated_actor": "local"}
    assert admission.calls == []
