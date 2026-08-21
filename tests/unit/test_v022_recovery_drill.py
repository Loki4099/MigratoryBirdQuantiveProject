from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy.engine import RowMapping

from style_rotation.v022.recovery_drill import (
    RestoredObjectObservation,
    evaluate_restored_objects,
)


def _row(*, suffix: int = 1) -> RowMapping:
    return cast(
        RowMapping,
        cast(
            Any,
            {
                "payload_manifest_id": uuid.UUID(int=100 + suffix),
                "manifest_artifact_id": uuid.UUID(int=200 + suffix),
                "payload_object_id": uuid.UUID(int=300 + suffix),
                "object_content_hash": f"{suffix:064x}",
                "byte_size": 1000 + suffix,
                "object_state": "published",
                "verification_status": "verified",
            },
        ),
    )


def test_restore_object_evaluation_requires_exact_exhaustive_hashes() -> None:
    inventory = (_row(suffix=1), _row(suffix=2))
    observations = tuple(
        RestoredObjectObservation(
            row["payload_object_id"], row["object_content_hash"], row["byte_size"]
        )
        for row in inventory
    )

    passing, blockers = evaluate_restored_objects(inventory, observations)
    assert all(item.passed for item in passing)
    assert blockers == ()

    corrupted = (
        observations[0],
        RestoredObjectObservation(observations[1].payload_object_id, "f" * 64, 1002),
    )
    failing, blockers = evaluate_restored_objects(inventory, corrupted)
    assert failing[1].passed is False
    assert blockers == (f"content_hash_mismatch:{observations[1].payload_object_id}",)


def test_restore_object_evaluation_fails_closed_on_empty_or_unexpected_inventory() -> None:
    results, blockers = evaluate_restored_objects((), ())
    assert results == ()
    assert blockers == ("no_materialized_strong_root_objects",)

    with pytest.raises(ValueError, match="unexpected Payload Objects"):
        evaluate_restored_objects(
            (_row(),),
            (RestoredObjectObservation(uuid.UUID(int=999), "0" * 64, 1),),
        )
