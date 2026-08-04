from __future__ import annotations

from pathlib import Path

from style_rotation.data.forward_return_engine import build_forward_return_engine_spec


def test_forward_return_engine_spec_is_stable_and_freezes_schema(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("numpy==2.0\n", encoding="utf-8")
    first = build_forward_return_engine_spec(
        "a" * 40, lock, "20260804_12_v02_forward_ret", version_number=2
    )
    second = build_forward_return_engine_spec(
        "a" * 40, lock, "20260804_12_v02_forward_ret", version_number=2
    )
    assert first == second
    assert first.version_number == 2
    assert first.schema_revision == "20260804_12_v02_forward_ret"
    assert len(first.configuration_hash) == 64
