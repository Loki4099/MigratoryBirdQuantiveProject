from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from style_rotation.v022.payload_runtime import LocalPayloadObjectStore


def test_local_object_store_uses_raw_content_hash_and_is_idempotent(tmp_path: Path) -> None:
    content = b"immutable parquet fixture"
    expected_hash = hashlib.sha256(content).hexdigest()
    store = LocalPayloadObjectStore(tmp_path)

    first = store.publish(content, file_extension="parquet")
    second = store.publish(content, file_extension="parquet")

    assert first == second
    assert first.content_hash == expected_hash
    assert first.storage_uri == f"payload-object://sha256/{expected_hash}.parquet"
    assert (tmp_path / "sha256" / f"{expected_hash}.parquet").read_bytes() == content


def test_local_object_store_rejects_corrupt_existing_content(tmp_path: Path) -> None:
    content = b"expected"
    store = LocalPayloadObjectStore(tmp_path)
    result = store.publish(content, file_extension="parquet")
    path = tmp_path / "sha256" / f"{result.content_hash}.parquet"
    path.write_bytes(b"corrupt")

    with pytest.raises(ValueError, match="corrupt"):
        store.publish(content, file_extension="parquet")


def test_local_object_store_observes_actual_restored_bytes(tmp_path: Path) -> None:
    store = LocalPayloadObjectStore(tmp_path)
    published = store.publish(b"restored bytes", file_extension="parquet")

    assert store.observe(published.storage_uri) == (published.content_hash, 14)

    path = tmp_path / "sha256" / f"{published.content_hash}.parquet"
    path.write_bytes(b"corrupt restored bytes")
    actual_hash, actual_size = store.observe(published.storage_uri)
    assert actual_hash == hashlib.sha256(b"corrupt restored bytes").hexdigest()
    assert actual_size == 22


@pytest.mark.parametrize(
    "storage_uri",
    (
        "payload-object://sha256/../../secret.parquet",
        f"payload-object://sha256/{'a' * 64}.PARQUET",
        f"file:///{'a' * 64}.parquet",
    ),
)
def test_local_object_store_rejects_noncanonical_observation_uri(
    tmp_path: Path, storage_uri: str
) -> None:
    with pytest.raises(ValueError, match="canonical"):
        LocalPayloadObjectStore(tmp_path).observe(storage_uri)
