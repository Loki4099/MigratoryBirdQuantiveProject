from __future__ import annotations

from pathlib import Path

import pytest

from style_rotation.model.engine import build_model_engine_spec


def test_model_engine_spec_is_stable_and_freezes_vote_and_missing_semantics(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("numpy==2.0\n", encoding="utf-8")
    first = build_model_engine_spec("a" * 40, lock, "20260804_15_v02_model_data", version_number=2)
    second = build_model_engine_spec("a" * 40, lock, "20260804_15_v02_model_data", version_number=2)
    assert first == second
    assert first.version_number == 2
    assert first.schema_revision == "20260804_15_v02_model_data"
    assert len(first.dependency_lock_hash) == 64
    assert len(first.configuration_hash) == 64


def test_model_engine_spec_rejects_unknown_commit_or_missing_lock(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("locked", encoding="utf-8")
    with pytest.raises(ValueError, match="hexadecimal commit"):
        build_model_engine_spec("not-a-commit", lock, "head")
    with pytest.raises(ValueError, match="not found"):
        build_model_engine_spec("a" * 40, tmp_path / "missing.lock", "head")
