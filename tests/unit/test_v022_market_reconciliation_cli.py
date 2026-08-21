from __future__ import annotations

import uuid

from style_rotation.cli.v022_market_reconciliation import (
    observation_spec,
    reconciliation_spec,
    resolution_spec,
)
from style_rotation.v022.market_reconciliation import (
    V1_RECONSTRUCTION_POLICY,
    V2_RECONSTRUCTION_POLICY,
)


def test_manual_reconciliation_documents_parse_into_typed_specs() -> None:
    subject_id = uuid.uuid4()
    observation = observation_spec(
        {
            "source_snapshot_security_subject_id": str(subject_id),
            "observation_key": "stooq_aaa_2020_gap",
            "version_number": 1,
            "created_by": "reviewer",
        }
    )
    assert observation.source_snapshot_security_subject_id == subject_id

    dataset_id = uuid.uuid4()
    alternate_id = uuid.uuid4()
    resolution = resolution_spec(
        {
            "primary_dataset_publication_id": str(dataset_id),
            "security_id": str(uuid.uuid4()),
            "gap_key": "aaa_2020_gap",
            "version_number": 1,
            "gap_type": "missing_bar",
            "gap_start": "2020-01-03",
            "gap_end": "2020-01-03",
            "resolution_kind": "replace_with_alternate",
            "alternate_observation_set_id": str(alternate_id),
            "evidence": [
                {"artifact_id": str(uuid.uuid4()), "role": "provider_comparison"}
            ],
            "created_by": "reviewer",
            "details": {"decision": "alternate raw row accepted"},
        }
    )
    assert resolution.alternate_observation_set_id == alternate_id

    plan = reconciliation_spec(
        {
            "primary_dataset_publication_id": str(dataset_id),
            "resolution_ids": [str(uuid.uuid4())],
            "cleaning_version_id": str(uuid.uuid4()),
            "calendar_version_id": str(uuid.uuid4()),
            "output_dataset_key": "sp500_reconciled_v1",
            "output_version_number": 2,
            "created_by": "reviewer",
        }
    )
    assert plan.output_version_number == 2
    assert plan.reconstruction_policy == V2_RECONSTRUCTION_POLICY

    legacy_plan = reconciliation_spec(
        {
            "primary_dataset_publication_id": str(dataset_id),
            "resolution_ids": [str(uuid.uuid4())],
            "cleaning_version_id": str(uuid.uuid4()),
            "calendar_version_id": str(uuid.uuid4()),
            "output_dataset_key": "sp500_reconciled_legacy_replay",
            "output_version_number": 1,
            "created_by": "reviewer",
            "reconstruction_policy": V1_RECONSTRUCTION_POLICY,
        }
    )
    assert legacy_plan.reconstruction_policy == V1_RECONSTRUCTION_POLICY
