from __future__ import annotations

import uuid
from contextlib import nullcontext
from typing import Any, cast

import pytest

from style_rotation.lineage.service import PublicationResult
from style_rotation.v022 import product_runtime_execution as runtime
from style_rotation.v022.product_runtime_execution import (
    ProductRuntimeExecutionService,
    ProductRuntimeStageInput,
)


class _Artifacts:
    def __init__(self, _engine: object) -> None:
        self.calls: list[dict[str, Any]] = []

    def publish(self, **kwargs: Any) -> PublicationResult:
        self.calls.append(kwargs)
        artifact_id = uuid.uuid4()
        writer = kwargs.get("draft_writer")
        if writer is not None:
            writer(_Connection(), artifact_id)
        return PublicationResult(artifact_id, "1" * 64, "2" * 64, "3" * 64, False)


class _Connection:
    def __init__(self) -> None:
        self.writes: list[tuple[object, object]] = []

    def execute(self, statement: object, parameters: object = None) -> None:
        self.writes.append((statement, parameters))


class _Engine:
    def connect(self) -> Any:
        return nullcontext(_Connection())


def test_product_aggregation_stage_publishes_ordered_exact_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts(object())
    monkeypatch.setattr(runtime, "ArtifactService", lambda _engine: artifacts)
    service = ProductRuntimeExecutionService(cast(Any, _Engine()))
    execution_id = uuid.uuid4()
    execution_artifact_id = uuid.uuid4()
    monkeypatch.setattr(
        service,
        "_execution",
        lambda _execution_id: {
            "artifact_id": execution_artifact_id,
            "execution_fingerprint": "a" * 64,
        },
    )
    manifest_ids = (uuid.uuid4(), uuid.uuid4())

    publication = service._publish_stage(
        product_runtime_execution_id=execution_id,
        stage_kind="aggregation",
        payload_document={"calculation_fingerprint": "b" * 64},
        inputs=tuple(
            ProductRuntimeStageInput("processing_manifest", item)
            for item in manifest_ids
        ),
    )

    assert publication.stage_kind == "aggregation"
    dependencies = artifacts.calls[0]["dependencies"]
    assert tuple(
        (item.role, item.ordinal, item.artifact_id) for item in dependencies
    ) == (
        ("runtime_execution", 0, execution_artifact_id),
        ("processing_manifest", 1, manifest_ids[0]),
        ("processing_manifest", 2, manifest_ids[1]),
    )


def test_none_defense_has_no_stage_and_merge_cannot_depend_on_defense_only() -> None:
    with pytest.raises(ValueError, match="input topology"):
        runtime._validate_stage_inputs(
            "merge",
            (ProductRuntimeStageInput("defense_decision", uuid.uuid4()),),
        )
    runtime._validate_stage_inputs("defense", ())
