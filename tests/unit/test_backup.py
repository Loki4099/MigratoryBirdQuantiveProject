import hashlib
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from style_rotation.ops.backup import (
    BackupService,
    _run,
    _validate_docker_service,
    _verify_custom_dump,
)


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


def test_subprocess_runner_streams_stdout_and_stdin(tmp_path: Path) -> None:
    source = tmp_path / "source.dump"
    target = tmp_path / "target.dump"
    source.write_bytes(b"PGDMP" + b"x" * 1024 * 1024)

    with source.open("rb") as input_stream, target.open("wb") as output_stream:
        _run(
            [
                sys.executable,
                "-c",
                "import shutil,sys; shutil.copyfileobj(sys.stdin.buffer,sys.stdout.buffer)",
            ],
            environment=dict(os.environ),
            input_stream=input_stream,
            output_stream=output_stream,
        )

    assert target.read_bytes() == source.read_bytes()


def test_backup_create_writes_dump_directly_to_temporary_file(tmp_path: Path) -> None:
    engine = MagicMock()
    output = tmp_path / "portable.dump"
    service = BackupService(engine, "postgresql+psycopg://u:p@localhost:5432/style_rotation")

    def write_dump(*_args: object, **kwargs: object) -> None:
        stream = kwargs["output_stream"]
        assert hasattr(stream, "write")
        stream.write(b"PGDMPstreamed-content")

    with (
        patch(
            "style_rotation.ops.backup.database_status",
            return_value=SimpleNamespace(current_revision="head"),
        ),
        patch.object(service, "_dump_command", return_value=(["pg_dump"], {})),
        patch("style_rotation.ops.backup._run", side_effect=write_dump) as runner,
    ):
        publication = service.create(output, git_commit="abcdef0")

    assert output.read_bytes() == b"PGDMPstreamed-content"
    assert publication.byte_count == output.stat().st_size
    assert runner.call_args.kwargs["output_stream"].closed is True


def test_restore_test_runs_joint_verifier_before_accepting_backup(tmp_path: Path) -> None:
    dump = tmp_path / "portable.dump"
    dump.write_bytes(b"PGDMPstreamed-content")
    backup_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    row = {
        "backup_record_id": backup_id,
        "storage_reference": str(dump),
        "dump_sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
        "schema_revision": "head",
        "byte_count": dump.stat().st_size,
    }
    engine = MagicMock()
    query = engine.connect.return_value.__enter__.return_value.execute.return_value
    query.mappings.return_value.one_or_none.return_value = row
    verifier = MagicMock()
    service = BackupService(engine, "postgresql+psycopg://u:p@localhost:5432/style_rotation")

    with (
        patch("style_rotation.ops.backup._run"),
        patch("style_rotation.ops.backup._verify_restored_database"),
        patch("style_rotation.ops.backup.subprocess.run"),
    ):
        restored = service.restore_test(
            backup_id,
            docker_service="postgres",
            restored_database_verifier=verifier,
        )

    verifier.assert_called_once_with(
        "postgresql+psycopg://u:p@localhost:5432/style_rotation_restore_000000000000"
    )
    assert restored.status == "restore_tested"


@pytest.mark.parametrize("service", ("../postgres", "--help", "postgres test", ""))
def test_docker_service_name_is_restricted(service: str) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _validate_docker_service(service)
