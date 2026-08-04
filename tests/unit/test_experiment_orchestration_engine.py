from pathlib import Path

import pytest

from style_rotation.experiment.orchestration_engine import build_orchestration_engine_spec


def test_orchestration_engine_spec_freezes_run_and_acceptance_semantics(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("package==1\n", encoding="utf-8")
    spec = build_orchestration_engine_spec(
        "abcdef0", lock, "20260804_24_v02_exp_result", version_number=2
    )
    assert spec.version_number == 2
    assert spec.schema_revision == "20260804_24_v02_exp_result"
    assert len(spec.dependency_lock_hash) == 64
    assert len(spec.configuration_hash) == 64


def test_orchestration_engine_spec_rejects_unpinned_commit(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("package==1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hex commit"):
        build_orchestration_engine_spec("main", lock, "head")
