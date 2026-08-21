from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from style_rotation.compatibility.v021_baseline import (
    V021BaselineError,
    _normalize_text_bytes,
    verify_baseline,
    write_baseline,
)
from style_rotation.core.canonical import sha256_hexdigest

BASELINE_PATH = Path("v0.22/m0/v021-baseline-manifest.v0.22.0.json")
POLICY_PATH = Path("v0.22/m0/m0-policy.v0.22.0.json")
EVIDENCE_PATH = Path("v0.22/m0/m0-evidence-manifest.v0.22.0.json")


def test_m0_baseline_is_complete_and_self_authenticating() -> None:
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    payload_hash = payload.pop("payload_sha256")

    assert payload["status"] == "complete"
    assert sha256_hexdigest(payload) == payload_hash
    assert payload["source"]["v021_source_commit"] == (
        "85a600811b2f58a7bb4be13b2a9c707035891d98"
    )
    assert payload["database_snapshot"]["alembic_revision"] == (
        "20260809_47_signal_export_job"
    )
    assert "database_url" not in json.dumps(payload)
    assert all(payload["checks"].values())


def test_m0_inventory_and_oracle_counts_are_exact() -> None:
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    inventory = payload["inventory"]
    outputs = payload["oracle_outputs"]
    evidence = payload["research_and_product_evidence"]

    assert inventory["factor_family_count"] == 12
    assert inventory["factor_variant_count"] == 28
    assert inventory["signal_family_count"] == 27
    assert inventory["signal_version_count"] == 51
    assert inventory["model_specification_count"] == 86
    assert len(outputs["factor_datasets"]) == 56
    assert len(outputs["signal_datasets"]) == 102
    assert len(outputs["model_datasets"]) == 172
    assert len(evidence["frozen_cell_results"]) == 26
    assert len(evidence["active_products"]) == 1
    assert len(evidence["active_product_artifact_closure"]) == 218
    assert evidence["active_products"][0]["preset_key"] == (
        "linear_weighted__signal_equal_v1"
    )


def test_m0_policy_keeps_future_capabilities_disabled() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    assert policy["status"] == "frozen"
    assert policy["feature_gates"]["v022_m0_oracle"] == "enabled"
    assert policy["feature_gates"]["v022_workspace"] == "hidden"
    assert policy["feature_gates"]["v022_default_route"] == "disabled"
    assert policy["feature_gates"]["v022_lightgbm"] == "planned_disabled"
    assert policy["test_strategy"]["production_like_database"]["reset_allowed"] is False
    assert policy["admission_limits"]["max_cells"] == 2048


def test_m0_evidence_opens_only_the_m1_entry_gate() -> None:
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["status"] == "passed"
    assert evidence["milestone"] == "M0"
    assert evidence["m1_entry_allowed"] is True
    assert all(evidence["gate_conditions"].values())
    assert evidence["legacy_baseline"]["production_database_writes"] == 0
    assert evidence["legacy_baseline"]["file_sha256"] == hashlib.sha256(
        BASELINE_PATH.read_bytes()
    ).hexdigest()
    assert evidence["policy"]["file_sha256"] == hashlib.sha256(
        POLICY_PATH.read_bytes()
    ).hexdigest()
    assert evidence["verification"]["postgresql_integration"][
        "destructive_database_name_guard"
    ] is True


def test_baseline_writer_refuses_overwrite_and_verifier_detects_drift(
    tmp_path: Path,
) -> None:
    path = tmp_path / "baseline.json"
    expected = {"status": "complete", "count": 86}
    write_baseline(path, expected)
    verify_baseline(path, expected)

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        write_baseline(path, expected)
    with pytest.raises(V021BaselineError, match="differs"):
        verify_baseline(path, {"status": "complete", "count": 87})


def test_text_hash_normalization_is_independent_of_checkout_line_endings() -> None:
    assert _normalize_text_bytes(b"a\r\nb\rc\n") == b"a\nb\nc\n"


def test_legacy_source_inventory_is_commit_scoped_not_worktree_scoped() -> None:
    from style_rotation.compatibility import v021_baseline

    calls: list[tuple[str, ...]] = []

    def fake_git_text(_root: Path, *arguments: str) -> str:
        calls.append(arguments)
        return "migrations/versions/legacy.py\nv0.21/catalogs/legacy.json\n"

    def fake_git_bytes(root: Path, _commit: str, relative: str) -> bytes:
        return (root / relative).read_bytes()

    with (
        patch.object(v021_baseline, "_git_text", side_effect=fake_git_text),
        patch.object(v021_baseline, "_git_bytes", side_effect=fake_git_bytes),
        pytest.raises(FileNotFoundError),
    ):
        v021_baseline._source_files(Path.cwd(), "frozen-commit")

    assert calls == [
        (
            "ls-tree",
            "-r",
            "--name-only",
            "frozen-commit",
            "--",
            "migrations/versions",
            "v0.2/catalogs",
            "v0.21/catalogs",
        )
    ]
