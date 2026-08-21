from __future__ import annotations

import inspect

from style_rotation.v022.suite_result_evidence import SuiteResultEvidenceService


def test_result_evidence_uses_plan_risk_context_for_simple_and_composed_graphs() -> None:
    source = inspect.getsource(SuiteResultEvidenceService.publish)

    assert "plan.compiled_execution_data_context_id" in source
    assert "v022_configuration_execution_context_binding" not in source
    assert "result.configuration_snapshot_id" in source
