from __future__ import annotations

import uuid
from typing import Any, cast

from style_rotation.v022.product_runtime import (
    ProductDecisionService,
    ProductRuntimeBindingIdentity,
    RuntimeArtifactSet,
)


class _RecordingConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement: object, parameters: object = None) -> None:
        self.executed.append((str(statement), cast(dict[str, object], parameters)))


def test_product_decision_writer_persists_exact_runtime_binding() -> None:
    connection = _RecordingConnection()
    decision_id = uuid.uuid4()
    binding = ProductRuntimeBindingIdentity(
        product_input_snapshot_id=uuid.uuid4(),
        product_runtime_execution_id=uuid.uuid4(),
        aggregation_stage_id=uuid.uuid4(),
        strategy_stage_id=uuid.uuid4(),
        defense_stage_id=None,
        merge_stage_id=uuid.uuid4(),
    )
    artifacts = RuntimeArtifactSet(
        input_manifest_artifact_id=uuid.uuid4(),
        aggregation_run_artifact_id=uuid.uuid4(),
        strategy_target_artifact_id=uuid.uuid4(),
        merged_target_artifact_id=uuid.uuid4(),
    )

    ProductDecisionService._write(
        cast(Any, connection),
        uuid.uuid4(),
        decision_id=decision_id,
        enrollment_id=uuid.uuid4(),
        execution_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        decision_status="completed",
        evidence_class="prospective_oos",
        oos_eligible=True,
        runtime_artifacts=artifacts,
        product_runtime_binding=binding,
        decision_document={"target": "published"},
        quality_document={"status": "warning"},
        reason_codes=(),
        fingerprint="a" * 64,
    )

    assert len(connection.executed) == 2
    binding_sql, values = connection.executed[1]
    assert "product.v022_product_decision_runtime_binding" in binding_sql
    assert values["decision"] == decision_id
    assert values["snapshot"] == binding.product_input_snapshot_id
    assert values["execution"] == binding.product_runtime_execution_id
    assert values["defense"] is None
    assert len(cast(str, values["fingerprint"])) == 64
