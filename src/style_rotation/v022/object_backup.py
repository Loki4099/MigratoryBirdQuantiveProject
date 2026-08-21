from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.recovery_drill import strong_object_inventory

_BUNDLE_MANIFEST = "strong-root-bundle.json"
_RESTORE_MARKER = "strong-root-restore.json"
_URI_PATTERN = re.compile(r"payload-object://sha256/([0-9a-f]{64})\.([a-z0-9][a-z0-9._-]{0,19})")


@dataclass(frozen=True, slots=True)
class StrongObjectBundlePublication:
    bundle_root: str
    manifest_path: str
    bundle_fingerprint: str
    object_count: int
    byte_count: int
    reused: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StrongObjectRestore:
    restored_object_root: str
    bundle_fingerprint: str
    object_count: int
    byte_count: int
    reused: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class StrongObjectBackupService:
    """Create and restore a portable, exact strong-root Payload Object bundle."""

    def __init__(self, engine: Engine, source_object_root: Path) -> None:
        self._engine = engine
        self._source_object_root = source_object_root.resolve()

    def create(
        self,
        bundle_root: Path,
        *,
        backup_record_id: uuid.UUID,
    ) -> StrongObjectBundlePublication:
        bundle_root = bundle_root.resolve()
        if _paths_overlap(bundle_root, self._source_object_root):
            raise ValueError("Object backup bundle must not overwrite the source Object Store")
        backup = self._backup_record(backup_record_id)
        objects = _normalize_inventory(strong_object_inventory(self._engine))
        if not objects:
            raise ValueError("Object backup requires a nonempty published strong-root closure")
        _verify_database_backup(backup)
        body: dict[str, object] = {
            "contract_version": "v0.22.0",
            "bundle_kind": "strong_root_object_backup",
            "backup_record_id": str(backup_record_id),
            "schema_revision": str(backup["schema_revision"]),
            "git_commit": str(backup["git_commit"]),
            "dump_sha256": str(backup["dump_sha256"]),
            "dump_byte_count": _as_int(backup["byte_count"], "dump_byte_count"),
            "object_count": len(objects),
            "byte_count": sum(_as_int(item["byte_size"], "byte_size") for item in objects),
            "objects": objects,
        }
        fingerprint = sha256_hexdigest(body)
        manifest = {**body, "bundle_fingerprint": fingerprint}
        if bundle_root.exists():
            observed = _load_bundle(bundle_root)
            if observed != manifest:
                raise ValueError(
                    "Object backup bundle target already exists with different content"
                )
            _verify_bundle_files(bundle_root, observed)
            return _bundle_publication(bundle_root, manifest, reused=True)

        staging = bundle_root.with_name(f".{bundle_root.name}.{uuid.uuid4().hex}.tmp")
        try:
            (staging / "sha256").mkdir(parents=True)
            copied_paths: set[str] = set()
            for item in objects:
                relative = _relative_object_path(str(item["storage_uri"]))
                relative_key = relative.as_posix()
                if relative_key in copied_paths:
                    continue
                copied_paths.add(relative_key)
                source = self._source_object_root / relative
                target = staging / relative
                _copy_verified(
                    source,
                    target,
                    expected_hash=str(item["content_hash"]),
                    expected_size=_as_int(item["byte_size"], "byte_size"),
                )
            _write_json(staging / _BUNDLE_MANIFEST, manifest)
            _verify_bundle_files(staging, manifest)
            bundle_root.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, bundle_root)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return _bundle_publication(bundle_root, manifest, reused=False)

    def restore(self, bundle_root: Path, output_root: Path) -> StrongObjectRestore:
        bundle_root = bundle_root.resolve()
        output_root = output_root.resolve()
        if _paths_overlap(output_root, bundle_root) or _paths_overlap(
            output_root, self._source_object_root
        ):
            raise ValueError("Restored Object Store must use an independent target directory")
        manifest = _load_bundle(bundle_root)
        _verify_bundle_files(bundle_root, manifest)
        marker = _restore_marker(manifest)
        if output_root.exists():
            marker_path = output_root / _RESTORE_MARKER
            if not marker_path.is_file() or _read_json(marker_path) != marker:
                raise ValueError(
                    "Restored Object Store target already exists with different content"
                )
            _verify_restored_files(output_root, manifest)
            return _restore_publication(output_root, manifest, reused=True)

        staging = output_root.with_name(f".{output_root.name}.{uuid.uuid4().hex}.tmp")
        try:
            (staging / "sha256").mkdir(parents=True)
            copied_paths: set[str] = set()
            for item in _objects(manifest):
                relative = _relative_object_path(str(item["storage_uri"]))
                relative_key = relative.as_posix()
                if relative_key in copied_paths:
                    continue
                copied_paths.add(relative_key)
                _copy_verified(
                    bundle_root / relative,
                    staging / relative,
                    expected_hash=str(item["content_hash"]),
                    expected_size=_as_int(item["byte_size"], "byte_size"),
                )
            _write_json(staging / _RESTORE_MARKER, marker)
            _verify_restored_files(staging, manifest)
            output_root.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, output_root)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return _restore_publication(output_root, manifest, reused=False)

    def verify_against_database(
        self,
        *,
        bundle_root: Path,
        restored_database_url: str,
        restored_object_root: Path,
    ) -> None:
        manifest = _load_bundle(bundle_root.resolve())
        _verify_restored_files(restored_object_root.resolve(), manifest)
        restored_engine = create_postgres_engine(restored_database_url)
        try:
            observed = _normalize_inventory(strong_object_inventory(restored_engine))
        finally:
            restored_engine.dispose()
        if observed != _objects(manifest):
            raise RuntimeError(
                "Restored database strong-root inventory does not match the Object backup bundle"
            )

    def _backup_record(self, backup_record_id: uuid.UUID) -> Any:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT backup_record_id,schema_revision,git_commit,dump_sha256,"
                        "storage_reference,byte_count,status "
                        "FROM ops.backup_record WHERE backup_record_id=:id"
                    ),
                    {"id": backup_record_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ValueError(f"Backup record not found: {backup_record_id}")
        if row["status"] not in {"verified", "restore_tested"}:
            raise ValueError("Object backup requires a verified database backup record")
        return row


def _normalize_inventory(rows: tuple[Any, ...]) -> list[dict[str, object]]:
    by_object: dict[str, dict[str, object]] = {}
    for row in rows:
        object_id = str(row["payload_object_id"])
        entry = {
            "payload_object_id": object_id,
            "storage_uri": str(row["storage_uri"]),
            "content_hash": str(row["object_content_hash"]),
            "byte_size": int(row["byte_size"]),
            "object_state": str(row["object_state"]),
            "verification_status": str(row["verification_status"]),
        }
        if entry["object_state"] != "published" or entry["verification_status"] != "verified":
            raise ValueError("Strong-root inventory contains an unpublished or unverified object")
        if (
            _relative_object_path(str(entry["storage_uri"])).name.split(".", 1)[0]
            != entry["content_hash"]
        ):
            raise ValueError("Payload Object URI does not match its recorded content hash")
        manifest_ref = {
            "payload_manifest_id": str(row["payload_manifest_id"]),
            "manifest_artifact_id": str(row["manifest_artifact_id"]),
        }
        existing = by_object.get(object_id)
        if existing is None:
            by_object[object_id] = {**entry, "manifests": [manifest_ref]}
        else:
            comparable = {key: existing[key] for key in entry}
            if comparable != entry:
                raise ValueError("Payload Object identity has conflicting strong-root metadata")
            manifests = existing["manifests"]
            assert isinstance(manifests, list)
            if manifest_ref not in manifests:
                manifests.append(manifest_ref)
    objects = list(by_object.values())
    for item in objects:
        manifests = item["manifests"]
        assert isinstance(manifests, list)
        manifests.sort(
            key=lambda value: (value["payload_manifest_id"], value["manifest_artifact_id"])
        )
    objects.sort(key=lambda value: str(value["payload_object_id"]))
    return objects


def _relative_object_path(storage_uri: str) -> Path:
    match = _URI_PATTERN.fullmatch(storage_uri)
    if match is None:
        raise ValueError("storage_uri is not a canonical Payload Object URI")
    return Path("sha256") / f"{match.group(1)}.{match.group(2)}"


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _copy_verified(
    source: Path,
    target: Path,
    *,
    expected_hash: str,
    expected_size: int,
) -> None:
    if not source.is_file():
        raise ValueError(f"Payload Object is missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    byte_count = 0
    with source.open("rb") as source_handle, target.open("xb") as target_handle:
        for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
            digest.update(chunk)
            byte_count += len(chunk)
            target_handle.write(chunk)
        target_handle.flush()
        os.fsync(target_handle.fileno())
    if digest.hexdigest() != expected_hash or byte_count != expected_size:
        target.unlink(missing_ok=True)
        raise ValueError(f"Payload Object hash or size mismatch: {source}")


def _verify_database_backup(backup: Any) -> None:
    path = Path(str(backup["storage_reference"])).resolve()
    expected_size = _as_int(backup["byte_count"], "dump_byte_count")
    if not path.is_file() or path.stat().st_size != expected_size:
        raise ValueError("Paired PostgreSQL backup dump is missing or has the wrong size")
    with path.open("rb") as handle:
        if handle.read(5) != b"PGDMP":
            raise ValueError("Paired PostgreSQL backup is not a custom-format dump")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != str(backup["dump_sha256"]):
        raise ValueError("Paired PostgreSQL backup checksum verification failed")


def _verify_file(path: Path, *, expected_hash: str, expected_size: int) -> None:
    if not path.is_file():
        raise ValueError(f"Payload Object is missing: {path}")
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            byte_count += len(chunk)
    if digest.hexdigest() != expected_hash or byte_count != expected_size:
        raise ValueError(f"Payload Object hash or size mismatch: {path}")


def _verify_bundle_files(bundle_root: Path, manifest: dict[str, object]) -> None:
    _validate_manifest(manifest)
    for item in _objects(manifest):
        _verify_file(
            bundle_root / _relative_object_path(str(item["storage_uri"])),
            expected_hash=str(item["content_hash"]),
            expected_size=_as_int(item["byte_size"], "byte_size"),
        )


def _verify_restored_files(output_root: Path, manifest: dict[str, object]) -> None:
    _validate_manifest(manifest)
    for item in _objects(manifest):
        _verify_file(
            output_root / _relative_object_path(str(item["storage_uri"])),
            expected_hash=str(item["content_hash"]),
            expected_size=_as_int(item["byte_size"], "byte_size"),
        )


def _load_bundle(bundle_root: Path) -> dict[str, object]:
    manifest_path = bundle_root / _BUNDLE_MANIFEST
    if not manifest_path.is_file():
        raise ValueError(f"Object backup bundle manifest is missing: {manifest_path}")
    manifest = _read_json(manifest_path)
    _validate_manifest(manifest)
    return manifest


def _validate_manifest(manifest: dict[str, object]) -> None:
    if manifest.get("contract_version") != "v0.22.0" or manifest.get("bundle_kind") != (
        "strong_root_object_backup"
    ):
        raise ValueError("Object backup bundle contract is unsupported")
    uuid.UUID(str(manifest.get("backup_record_id")))
    if not str(manifest.get("schema_revision", "")):
        raise ValueError("Object backup bundle schema revision is missing")
    if not re.fullmatch(r"[0-9a-f]{7,64}", str(manifest.get("git_commit"))):
        raise ValueError("Object backup bundle Git commit is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("dump_sha256"))):
        raise ValueError("Object backup bundle database checksum is invalid")
    if _as_int(manifest.get("dump_byte_count"), "dump_byte_count") <= 5:
        raise ValueError("Object backup bundle database byte count is invalid")
    objects = _objects(manifest)
    if not objects:
        raise ValueError("Object backup bundle has no objects")
    if manifest.get("object_count") != len(objects):
        raise ValueError("Object backup bundle object count is inconsistent")
    byte_count = sum(_as_int(item["byte_size"], "byte_size") for item in objects)
    if manifest.get("byte_count") != byte_count:
        raise ValueError("Object backup bundle byte count is inconsistent")
    fingerprint = manifest.get("bundle_fingerprint")
    body = {key: value for key, value in manifest.items() if key != "bundle_fingerprint"}
    if not isinstance(fingerprint, str) or fingerprint != sha256_hexdigest(body):
        raise ValueError("Object backup bundle fingerprint is invalid")
    normalized = _normalize_manifest_objects(objects)
    if normalized != objects:
        raise ValueError("Object backup bundle inventory is not canonical")


def _normalize_manifest_objects(objects: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in objects:
        manifests = item.get("manifests")
        if not isinstance(manifests, list) or not manifests:
            raise ValueError("Object backup bundle object has no Manifest roots")
        _relative_object_path(str(item.get("storage_uri")))
        content_hash = str(item.get("content_hash"))
        if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
            raise ValueError("Object backup bundle content hash is invalid")
        byte_size = _as_int(item.get("byte_size"), "byte_size")
        if byte_size < 0:
            raise ValueError("Object backup bundle object size is invalid")
        storage_uri = str(item.get("storage_uri"))
        if _relative_object_path(storage_uri).name.split(".", 1)[0] != content_hash:
            raise ValueError("Object backup bundle URI does not match its content hash")
        if item.get("object_state") != "published" or item.get("verification_status") != "verified":
            raise ValueError("Object backup bundle contains an unpublished object")
        rows.append(
            {
                "payload_object_id": str(uuid.UUID(str(item.get("payload_object_id")))),
                "storage_uri": storage_uri,
                "content_hash": content_hash,
                "byte_size": byte_size,
                "object_state": str(item.get("object_state")),
                "verification_status": str(item.get("verification_status")),
                "manifests": sorted(
                    [
                        {
                            "payload_manifest_id": str(
                                uuid.UUID(str(value["payload_manifest_id"]))
                            ),
                            "manifest_artifact_id": str(
                                uuid.UUID(str(value["manifest_artifact_id"]))
                            ),
                        }
                        for value in manifests
                    ],
                    key=lambda value: (
                        value["payload_manifest_id"],
                        value["manifest_artifact_id"],
                    ),
                ),
            }
        )
    rows.sort(key=lambda value: str(value["payload_object_id"]))
    return rows


def _objects(manifest: dict[str, object]) -> list[dict[str, object]]:
    objects = manifest.get("objects")
    if not isinstance(objects, list) or any(not isinstance(item, dict) for item in objects):
        raise ValueError("Object backup bundle object inventory is invalid")
    return cast(list[dict[str, object]], objects)


def _as_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Object backup bundle {label} must be an integer")
    return value


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, object]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Object backup JSON is unreadable: {path}") from error
    if not isinstance(loaded, dict):
        raise ValueError("Object backup JSON must be an object")
    return loaded


def _restore_marker(manifest: dict[str, object]) -> dict[str, object]:
    return {
        "contract_version": "v0.22.0",
        "bundle_fingerprint": manifest["bundle_fingerprint"],
        "backup_record_id": manifest["backup_record_id"],
        "object_count": manifest["object_count"],
        "byte_count": manifest["byte_count"],
    }


def _bundle_publication(
    root: Path, manifest: dict[str, object], *, reused: bool
) -> StrongObjectBundlePublication:
    return StrongObjectBundlePublication(
        bundle_root=str(root),
        manifest_path=str(root / _BUNDLE_MANIFEST),
        bundle_fingerprint=str(manifest["bundle_fingerprint"]),
        object_count=_as_int(manifest["object_count"], "object_count"),
        byte_count=_as_int(manifest["byte_count"], "byte_count"),
        reused=reused,
    )


def _restore_publication(
    root: Path, manifest: dict[str, object], *, reused: bool
) -> StrongObjectRestore:
    return StrongObjectRestore(
        restored_object_root=str(root),
        bundle_fingerprint=str(manifest["bundle_fingerprint"]),
        object_count=_as_int(manifest["object_count"], "object_count"),
        byte_count=_as_int(manifest["byte_count"], "byte_count"),
        reused=reused,
    )
