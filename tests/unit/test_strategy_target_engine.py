from __future__ import annotations

from pathlib import Path

import pytest

from style_rotation.strategy.engine import build_strategy_target_engine_spec


def test_target_engine_identity_freezes_decision_semantics() -> None:
    spec = build_strategy_target_engine_spec(
        "a" * 40,
        Path(__file__).parents[2] / "requirements.lock",
        "20260804_18_v02_strategy_target",
    )
    assert spec.version_number == 1
    assert spec.schema_revision == "20260804_18_v02_strategy_target"
    assert len(spec.configuration_hash) == 64


def test_target_engine_rejects_non_commit_identity() -> None:
    with pytest.raises(ValueError, match="hex commit"):
        build_strategy_target_engine_spec(
            "not-a-commit",
            Path(__file__).parents[2] / "requirements.lock",
            "20260804_18_v02_strategy_target",
        )
