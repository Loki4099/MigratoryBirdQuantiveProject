from pathlib import Path

import pytest

from style_rotation.ops.backup import _verify_custom_dump


def test_custom_dump_verification_checks_magic_and_checksum(tmp_path: Path) -> None:
    dump = tmp_path / "release.dump"
    dump.write_bytes(b"PGDMPvalid-test-content")
    _verify_custom_dump(dump)
    with pytest.raises(ValueError, match="checksum"):
        _verify_custom_dump(dump, expected_checksum="0" * 64)


def test_custom_dump_verification_rejects_plain_sql(tmp_path: Path) -> None:
    dump = tmp_path / "release.dump"
    dump.write_text("CREATE TABLE example();", encoding="utf-8")
    with pytest.raises(ValueError, match="custom-format"):
        _verify_custom_dump(dump)
