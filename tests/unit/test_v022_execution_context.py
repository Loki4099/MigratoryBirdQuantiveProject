from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from style_rotation.api.schemas import GraphDraftCompileResponse
from style_rotation.v022.draft_service import (
    GraphDraftCompileResult,
    GraphDraftDefenseExecutionContext,
    GraphDraftService,
)
from style_rotation.v022.execution_context import ResolvedDataBindingSnapshot


def _binding_document() -> dict[str, Any]:
    return {
        "contract_version": "v0.22.0",
        "bindings": [
            {
                "input_key": "canonical_market_bars",
                "dataset_publication_id": "10000000-0000-0000-0000-000000000001",
                "dataset_artifact_id": "10000000-0000-0000-0000-000000000002",
                "dataset_key": "canonical_daily_bars",
                "dataset_version_number": 1,
                "coverage_start": "2020-01-02",
                "coverage_end": "2026-08-11",
                "calendar_version_id": "10000000-0000-0000-0000-000000000003",
                "calendar_artifact_id": "10000000-0000-0000-0000-000000000004",
                "security_ids": [
                    "20000000-0000-0000-0000-000000000001",
                    "20000000-0000-0000-0000-000000000002",
                ],
            }
        ],
    }


def test_resolved_data_binding_snapshot_canonicalizes_exact_identity() -> None:
    snapshot = ResolvedDataBindingSnapshot.model_validate(_binding_document())

    assert snapshot.model_dump(mode="json") == _binding_document()
    assert snapshot.bindings[0].input_key == "canonical_market_bars"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda document: document["bindings"][0].update(
                coverage_start="2027-01-01"
            ),
            "coverage is inverted",
        ),
        (
            lambda document: document["bindings"][0].update(
                calendar_artifact_id=None
            ),
            "must be provided together",
        ),
        (
            lambda document: document["bindings"][0].update(
                security_ids=[
                    "20000000-0000-0000-0000-000000000001",
                    "20000000-0000-0000-0000-000000000001",
                ]
            ),
            "must be unique",
        ),
    ],
)
def test_resolved_data_binding_rejects_incoherent_identity(
    mutate: Any, message: str
) -> None:
    document = _binding_document()
    mutate(document)

    with pytest.raises(ValidationError, match=message):
        ResolvedDataBindingSnapshot.model_validate(document)


def test_resolved_data_binding_rejects_duplicate_inputs_and_unknown_fields() -> None:
    duplicate = _binding_document()
    duplicate["bindings"].append(deepcopy(duplicate["bindings"][0]))
    with pytest.raises(ValidationError, match="input keys must be unique"):
        ResolvedDataBindingSnapshot.model_validate(duplicate)

    unknown = _binding_document()
    unknown["bindings"][0]["display_name"] = "mutable presentation"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ResolvedDataBindingSnapshot.model_validate(unknown)


def test_compile_context_identity_is_all_present_or_all_absent() -> None:
    common = {
        "graph_draft_id": "10000000-0000-4000-8000-000000000001",
        "graph_draft_revision": 1,
        "draft_intent_id": "10000000-0000-4000-8000-000000000002",
        "compile_attempt_id": "10000000-0000-4000-8000-000000000003",
        "compiled_research_graph_id": "10000000-0000-4000-8000-000000000004",
        "graph_artifact_id": "10000000-0000-4000-8000-000000000005",
        "graph_fingerprint": "a" * 64,
        "reused": False,
    }
    legacy = GraphDraftCompileResult(**common)  # type: ignore[arg-type]
    assert legacy.compiled_execution_data_context_id is None
    assert legacy.defense_execution_contexts == ()
    assert legacy.to_dict()["defense_execution_contexts"] == []

    with pytest.raises(ValueError, match="wholly present or absent"):
        GraphDraftCompileResult(  # type: ignore[arg-type]
            **common,
            compiled_execution_data_context_id=(
                "10000000-0000-4000-8000-000000000006"
            ),
        )

    response = {
        "context": {
            "api_version": "v2",
            "system_version": "0.22.0",
            "read_only": False,
        },
        "quality": {"state": "ok", "codes": []},
        **common,
        "compiled_execution_data_context_id": (
            "10000000-0000-4000-8000-000000000006"
        ),
    }
    with pytest.raises(ValidationError, match="wholly present or absent"):
        GraphDraftCompileResponse.model_validate(response)


def test_compile_response_freezes_ordered_defense_contexts_and_legacy_replay() -> None:
    risk_context_id = "10000000-0000-4000-8000-000000000010"
    common = {
        "graph_draft_id": uuid.UUID("10000000-0000-4000-8000-000000000001"),
        "graph_draft_revision": 1,
        "draft_intent_id": uuid.UUID("10000000-0000-4000-8000-000000000002"),
        "compile_attempt_id": uuid.UUID("10000000-0000-4000-8000-000000000003"),
        "compiled_research_graph_id": uuid.UUID(
            "10000000-0000-4000-8000-000000000004"
        ),
        "graph_artifact_id": uuid.UUID("10000000-0000-4000-8000-000000000005"),
        "graph_fingerprint": "a" * 64,
        "reused": False,
        "compiled_execution_data_context_id": uuid.UUID(risk_context_id),
        "execution_data_context_artifact_id": uuid.UUID(
            "10000000-0000-4000-8000-000000000011"
        ),
        "execution_data_context_fingerprint": "b" * 64,
        "execution_data_context_reused": False,
    }

    def context(
        ordinal: int,
        defense_version_id: str,
    ) -> GraphDraftDefenseExecutionContext:
        return GraphDraftDefenseExecutionContext(
            compiled_defense_execution_context_id=uuid.UUID(
                f"20000000-0000-4000-8000-{ordinal:012d}"
            ),
            defense_execution_context_artifact_id=uuid.UUID(
                f"30000000-0000-4000-8000-{ordinal:012d}"
            ),
            compiled_execution_data_context_id=uuid.UUID(risk_context_id),
            defense_version_id=uuid.UUID(defense_version_id),
            defense_execution_context_fingerprint=str(ordinal) * 64,
            resolved_input_binding_fingerprint=str(ordinal + 2) * 64,
            input_count=5,
            reused=False,
        )

    lower = "10000000-0000-4000-8000-000000000020"
    upper = "f0000000-0000-4000-8000-000000000021"
    ordered = (context(1, lower), context(2, upper))
    result = GraphDraftCompileResult(  # type: ignore[arg-type]
        **common,
        defense_execution_contexts=ordered,
        selection_fingerprint="c" * 64,
    )
    response_document = {
        "context": {
            "api_version": "v2",
            "system_version": "0.22.0",
            "read_only": False,
        },
        "quality": {"state": "ok", "codes": []},
        **result.to_dict(),
    }
    response = GraphDraftCompileResponse.model_validate(response_document)
    assert [str(item.defense_version_id) for item in response.defense_execution_contexts] == [
        lower,
        upper,
    ]
    replayed = GraphDraftService._compile_result(result.to_dict())
    assert replayed.defense_execution_contexts == ordered
    assert replayed.selection_fingerprint == "c" * 64

    legacy_document = dict(response_document)
    legacy_document.pop("defense_execution_contexts")
    legacy_document.pop("selection_fingerprint")
    assert GraphDraftCompileResponse.model_validate(
        legacy_document
    ).defense_execution_contexts == []

    with pytest.raises(ValueError, match="canonical Defense order"):
        GraphDraftCompileResult(  # type: ignore[arg-type]
            **common,
            defense_execution_contexts=tuple(reversed(ordered)),
        )
    with pytest.raises(ValueError, match="unique Defense versions"):
        GraphDraftCompileResult(  # type: ignore[arg-type]
            **common,
            defense_execution_contexts=(ordered[0], ordered[0]),
        )


def test_compile_command_result_legacy_document_defaults_defense_contexts_empty() -> None:
    document = {
        "graph_draft_id": "10000000-0000-4000-8000-000000000001",
        "graph_draft_revision": 1,
        "draft_intent_id": "10000000-0000-4000-8000-000000000002",
        "compile_attempt_id": "10000000-0000-4000-8000-000000000003",
        "compiled_research_graph_id": "10000000-0000-4000-8000-000000000004",
        "graph_artifact_id": "10000000-0000-4000-8000-000000000005",
        "graph_fingerprint": "a" * 64,
        "reused": True,
    }

    replayed = GraphDraftService._compile_result(document)

    assert replayed.defense_execution_contexts == ()
