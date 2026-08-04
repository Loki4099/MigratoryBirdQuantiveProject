from __future__ import annotations

from pathlib import Path

from style_rotation.signal.diagnostic_engine import build_signal_evaluation_engine_spec


def test_signal_evaluation_engine_spec_is_deterministic(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("scipy==1.0\n", encoding="utf-8")
    first = build_signal_evaluation_engine_spec(
        "a" * 40, lock, "20260804_13_v02_signal_eval", version_number=2
    )
    second = build_signal_evaluation_engine_spec(
        "a" * 40, lock, "20260804_13_v02_signal_eval", version_number=2
    )
    assert first == second
    assert first.version_number == 2
    assert first.schema_revision == "20260804_13_v02_signal_eval"
    assert len(first.configuration_hash) == 64
