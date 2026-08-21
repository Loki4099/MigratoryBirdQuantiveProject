from __future__ import annotations

import uuid

from style_rotation.cli.v022_dataset_gate import assessment_spec


def test_dataset_gate_cli_parses_independent_eligibility() -> None:
    evidence_id = uuid.uuid4()
    parsed = assessment_spec(
        {
            "dataset_publication_id": str(uuid.uuid4()),
            "universe_membership_ledger_id": str(uuid.uuid4()),
            "gate_key": "sp500_free_data_gate_v1",
            "version_number": 1,
            "assessed_coverage_start": "2007-01-03",
            "assessed_coverage_end": "2026-06-30",
            "ranking_eligibility": "rankable_research",
            "product_eligibility": "eligible_with_warnings",
            "evidence": [
                {"artifact_id": str(evidence_id), "role": "supporting_evidence"}
            ],
            "findings": [
                {
                    "finding_code": "historical_membership_retrospective",
                    "finding_category": "data_provenance",
                    "severity": "warning",
                    "ranking_effect": "none",
                    "product_effect": "warning",
                },
                {
                    "finding_code": "retrospective_price_snapshot",
                    "finding_category": "data_provenance",
                    "severity": "warning",
                    "ranking_effect": "none",
                    "product_effect": "warning",
                },
            ],
            "uniform_exclusions": [],
            "created_by": "reviewer",
        }
    )

    assert parsed.ranking_eligibility == "rankable_research"
    assert parsed.product_eligibility == "eligible_with_warnings"
    assert parsed.evidence[0].artifact_id == evidence_id
