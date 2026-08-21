from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from style_rotation.v022.object_backup import StrongObjectBackupService


def _inventory(content: bytes) -> tuple[dict[str, object], ...]:
    content_hash = hashlib.sha256(content).hexdigest()
    object_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
    return (
        {
            "payload_manifest_id": uuid.UUID("20000000-0000-4000-8000-000000000001"),
            "manifest_artifact_id": uuid.UUID("30000000-0000-4000-8000-000000000001"),
            "payload_object_id": object_id,
            "object_content_hash": content_hash,
            "storage_uri": f"payload-object://sha256/{content_hash}.parquet",
            "byte_size": len(content),
            "object_state": "published",
            "verification_status": "verified",
        },
        {
            "payload_manifest_id": uuid.UUID("20000000-0000-4000-8000-000000000002"),
            "manifest_artifact_id": uuid.UUID("30000000-0000-4000-8000-000000000002"),
            "payload_object_id": object_id,
            "object_content_hash": content_hash,
            "storage_uri": f"payload-object://sha256/{content_hash}.parquet",
            "byte_size": len(content),
            "object_state": "published",
            "verification_status": "verified",
        },
    )


def _service(tmp_path: Path, content: bytes) -> tuple[StrongObjectBackupService, Path]:
    source_root = tmp_path / "source"
    content_hash = hashlib.sha256(content).hexdigest()
    object_path = source_root / "sha256" / f"{content_hash}.parquet"
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(content)
    service = StrongObjectBackupService(MagicMock(), source_root)
    return service, source_root


def _backup(tmp_path: Path) -> dict[str, object]:
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"PGDMPportable-database")
    return {
        "schema_revision": "head",
        "git_commit": "abcdef0",
        "dump_sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
        "storage_reference": str(dump),
        "byte_count": dump.stat().st_size,
        "status": "verified",
    }


def test_strong_root_bundle_round_trip_is_deterministic_and_reusable(tmp_path: Path) -> None:
    content = b"canonical-payload-object"
    service, _source_root = _service(tmp_path, content)
    backup_id = uuid.UUID("40000000-0000-4000-8000-000000000001")
    backup = _backup(tmp_path)
    with (
        patch.object(service, "_backup_record", return_value=backup),
        patch(
            "style_rotation.v022.object_backup.strong_object_inventory",
            return_value=_inventory(content),
        ),
    ):
        first = service.create(tmp_path / "bundle", backup_record_id=backup_id)
        second = service.create(tmp_path / "bundle", backup_record_id=backup_id)

    restored = service.restore(tmp_path / "bundle", tmp_path / "restored")
    reused = service.restore(tmp_path / "bundle", tmp_path / "restored")

    assert first.object_count == 1
    assert first.byte_count == len(content)
    assert first.reused is False
    assert second.bundle_fingerprint == first.bundle_fingerprint
    assert second.reused is True
    assert restored.bundle_fingerprint == first.bundle_fingerprint
    assert restored.reused is False
    assert reused.reused is True


def test_strong_root_bundle_rejects_corrupt_object_bytes(tmp_path: Path) -> None:
    content = b"canonical-payload-object"
    service, source_root = _service(tmp_path, content)
    content_hash = hashlib.sha256(content).hexdigest()
    (source_root / "sha256" / f"{content_hash}.parquet").write_bytes(b"corrupt")
    backup = _backup(tmp_path)
    with (
        patch.object(service, "_backup_record", return_value=backup),
        patch(
            "style_rotation.v022.object_backup.strong_object_inventory",
            return_value=_inventory(content),
        ),
        pytest.raises(ValueError, match="hash or size mismatch"),
    ):
        service.create(
            tmp_path / "bundle",
            backup_record_id=uuid.UUID("40000000-0000-4000-8000-000000000001"),
        )

    assert not (tmp_path / "bundle").exists()


def test_joint_verifier_rejects_database_object_inventory_drift(tmp_path: Path) -> None:
    content = b"canonical-payload-object"
    service, _source_root = _service(tmp_path, content)
    backup = _backup(tmp_path)
    with (
        patch.object(service, "_backup_record", return_value=backup),
        patch(
            "style_rotation.v022.object_backup.strong_object_inventory",
            return_value=_inventory(content),
        ),
    ):
        service.create(
            tmp_path / "bundle",
            backup_record_id=uuid.UUID("40000000-0000-4000-8000-000000000001"),
        )
    service.restore(tmp_path / "bundle", tmp_path / "restored")

    restored_engine = MagicMock()
    with (
        patch(
            "style_rotation.v022.object_backup.create_postgres_engine",
            return_value=restored_engine,
        ),
        patch(
            "style_rotation.v022.object_backup.strong_object_inventory",
            return_value=(),
        ),
        pytest.raises(RuntimeError, match="does not match"),
    ):
        service.verify_against_database(
            bundle_root=tmp_path / "bundle",
            restored_database_url="postgresql+psycopg://restored",
            restored_object_root=tmp_path / "restored",
        )

    restored_engine.dispose.assert_called_once_with()
