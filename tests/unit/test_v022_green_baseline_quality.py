from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from style_rotation.v022.green_baseline_quality import _policy, _report_document


def test_policy_rejects_duplicate_security(tmp_path: Path) -> None:
    security_id = str(uuid.uuid4())
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "exclusion_policy": {
                    "decisions": [
                        {"security_id": security_id},
                        {"security_id": security_id},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        _policy(path)


def test_report_excludes_policy_and_zero_volume_but_retains_large_moves() -> None:
    policy_security = uuid.uuid4()
    zero_security = uuid.uuid4()
    large_move_security = uuid.uuid4()
    dataset_id = uuid.uuid4()
    report = _report_document(
        dataset={
            "dataset_publication_id": dataset_id,
            "dataset_artifact_id": uuid.uuid4(),
            "dataset_key": "risk_v5",
            "version_number": 1,
            "external_import_manifest_id": uuid.uuid4(),
            "calendar_version_id": uuid.uuid4(),
        },
        policy_sha256="a" * 64,
        policy_decisions={
            policy_security: {
                "reason_code": "reviewed_exclusion",
                "reviewer_note": "reviewed",
            }
        },
        facts={
            "scalar": {
                "bar_count": 10,
                "asset_count": 3,
                "coverage_start": "2004-12-31",
                "coverage_end": "2026-06-30",
                "non_positive_count": 0,
                "raw_envelope_error_count": 0,
                "zero_volume_count": 2,
            },
            "zero_rows": (
                {
                    "security_id": zero_security,
                    "observation_count": 2,
                    "first_session": "2020-01-01",
                    "last_session": "2020-01-02",
                },
            ),
            "move_rows": (
                {
                    "security_id": large_move_security,
                    "observation_count": 1,
                    "first_session": "2020-01-03",
                    "last_session": "2020-01-03",
                },
            ),
        },
    )
    assert report["uniformly_excluded_security_count"] == 2
    issues = report["issues"]
    excluded = {
        item["subject_key"]
        for item in issues
        if item["rule_code"] == "security_uniformly_excluded_provider_unavailable"
    }
    assert excluded == {str(policy_security), str(zero_security)}
    large = [
        item
        for item in issues
        if item["rule_code"]
        == "adjusted_return_over_50_percent_reviewed_not_excluded"
    ]
    assert [item["subject_key"] for item in large] == [str(large_move_security)]
