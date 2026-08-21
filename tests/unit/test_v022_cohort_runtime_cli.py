from __future__ import annotations

import uuid

from style_rotation.cli.v022_cohort_runtime import build_parser


def test_cohort_runtime_cli_requires_exact_cohort_and_gate() -> None:
    cohort_id = uuid.uuid4()
    gate_id = uuid.uuid4()

    parsed = build_parser().parse_args(
        [str(cohort_id), str(gate_id), "--created-by", "runtime-reviewer"]
    )

    assert parsed.evaluation_cohort_version_id == cohort_id
    assert parsed.dataset_gate_assessment_id == gate_id
    assert parsed.created_by == "runtime-reviewer"
