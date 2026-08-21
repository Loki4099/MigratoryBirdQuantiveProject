from __future__ import annotations

import uuid

from style_rotation.v022.green_baseline_gate import _quality_projection


def test_quality_projection_freezes_exclusions_and_large_move_warnings() -> None:
    excluded = uuid.uuid4()
    moved = uuid.uuid4()
    document = {
        "uniformly_excluded_security_count": 1,
        "large_move_security_count": 1,
        "issues": [
            {
                "rule_code": "security_uniformly_excluded_provider_unavailable",
                "subject_key": str(excluded),
                "details": {"policy_reason_code": "zero_volume"},
            },
            {
                "rule_code": "adjusted_return_over_50_percent_reviewed_not_excluded",
                "subject_key": str(moved),
                "details": {"observation_count": 2},
            },
        ],
    }
    evidence = uuid.uuid4()
    monthly = uuid.uuid4()
    findings, exclusions = _quality_projection(
        document,
        evidence_artifact_id=evidence,
        monthly_evidence_artifact_id=monthly,
    )
    assert len(exclusions) == 1
    assert exclusions[0].security_id == excluded
    assert exclusions[0].reason_code == "zero_volume"
    assert any(
        item.security_id == moved
        and item.finding_code == "adjusted_return_over_50_percent_reviewed"
        for item in findings
    )
    assert all(item.ranking_effect == "none" for item in findings)
