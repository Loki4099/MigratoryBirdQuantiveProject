from __future__ import annotations

import subprocess
import sys

import pytest

from scripts.preflight_v022_green_database import (
    EXPECTED_ALEMBIC_REVISION,
    GreenTargetSnapshot,
    validate_green_target_snapshot,
)


def test_green_preflight_tracks_the_release_migration_head() -> None:
    assert EXPECTED_ALEMBIC_REVISION == "20260821_142_asset_export"


def _snapshot() -> GreenTargetSnapshot:
    return GreenTargetSnapshot(
        database_name="style_rotation_green",
        alembic_revision=EXPECTED_ALEMBIC_REVISION,
        relation_counts={"data.dataset_publication": 0},
        forbidden_id_hits={"retired-id": 0},
    )


def test_green_preflight_accepts_exact_empty_target() -> None:
    validate_green_target_snapshot(_snapshot(), expected_database="style_rotation_green")


def test_green_preflight_script_entrypoint_resolves_sibling_import() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/preflight_v022_green_database.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--expected-database" in completed.stdout


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("database_name", "style_rotation", "database_name"),
        ("alembic_revision", "old", "alembic_revision"),
        ("relation_counts", {"data.dataset_publication": 1}, "not_empty"),
        ("forbidden_id_hits", {"retired-id": 1}, "forbidden_id"),
    ],
)
def test_green_preflight_rejects_unsafe_target(
    field: str, value: object, message: str
) -> None:
    values = {
        "database_name": "style_rotation_green",
        "alembic_revision": EXPECTED_ALEMBIC_REVISION,
        "relation_counts": {"data.dataset_publication": 0},
        "forbidden_id_hits": {"retired-id": 0},
    }
    values[field] = value
    snapshot = GreenTargetSnapshot(**values)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=message):
        validate_green_target_snapshot(snapshot, expected_database="style_rotation_green")
