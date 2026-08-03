from __future__ import annotations

from pathlib import Path

import pytest

from style_rotation.factor.calculator import IMPLEMENTATIONS
from style_rotation.factor.engine import FactorEngineSpec, build_factor_engine_spec


def test_factor_engine_spec_fingerprints_code_configuration_and_lock(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_bytes(b"numpy==1.0\n")

    first = build_factor_engine_spec("a" * 40, lock, "revision-1")
    second = build_factor_engine_spec("a" * 40, lock, "revision-1")

    assert first == second
    assert first.version_number == 1
    assert len(first.dependency_lock_hash) == 64
    assert len(first.configuration_hash) == 64
    assert set(first.numerical_environment["packages"]) == {"numpy", "pandas", "scipy"}
    assert len(IMPLEMENTATIONS) == 12

    lock.write_bytes(b"numpy==2.0\n")
    changed = build_factor_engine_spec("a" * 40, lock, "revision-1")
    assert changed.dependency_lock_hash != first.dependency_lock_hash


def test_factor_engine_spec_rejects_untraceable_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        build_factor_engine_spec("a" * 40, tmp_path / "missing.lock", "revision-1")
    with pytest.raises(ValueError, match="hexadecimal"):
        FactorEngineSpec(1, "0.2.0", "working-tree", "a" * 64, "revision", "b" * 64, {})
