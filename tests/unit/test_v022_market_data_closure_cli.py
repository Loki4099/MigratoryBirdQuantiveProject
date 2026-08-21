from __future__ import annotations

import uuid

import pytest

from style_rotation.cli.v022_market_data_closure import build_parser


def test_closure_cli_has_separate_exact_and_candidate_reference_modes() -> None:
    dataset_id = uuid.uuid4()
    cohort_id = uuid.uuid4()
    exact = build_parser().parse_args(
        ["exact", str(dataset_id), "--cohort", str(cohort_id)]
    )
    candidate = build_parser().parse_args(
        [
            "candidate-against-reference",
            str(dataset_id),
            "--reference-runtime-contract",
            str(uuid.uuid4()),
        ]
    )

    assert exact.dataset_publication_id == dataset_id
    assert exact.cohort == cohort_id
    assert candidate.candidate_dataset_publication_id == dataset_id
    assert candidate.reference_runtime_contract is not None

    with pytest.raises(SystemExit):
        build_parser().parse_args(["exact", str(dataset_id)])
