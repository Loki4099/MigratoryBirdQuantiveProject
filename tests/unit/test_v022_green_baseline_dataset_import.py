from __future__ import annotations

import hashlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from style_rotation.v022 import green_baseline_dataset_import as subject


def test_partition_manifest_hash_is_ordered_and_path_sensitive() -> None:
    first = {
        "path": "b.parquet",
        "kind": "daily_bar",
        "security_id": "s1",
        "year": 2020,
        "row_count": 1,
        "min_date": "2020-01-02",
        "max_date": "2020-01-02",
        "byte_size": 4,
        "sha256": "a" * 64,
    }
    second = {**first, "path": "a.parquet", "sha256": "b" * 64}

    assert subject._partition_manifest_hash((first, second)) != subject._partition_manifest_hash(
        (second, first)
    )


def test_verified_batches_rejects_changed_partition(tmp_path: Path) -> None:
    path = tmp_path / "part.parquet"
    pq.write_table(pa.table({"asset_id": ["asset-a"]}), path)
    record = {
        "path": path.name,
        "kind": "daily_bar",
        "row_count": 1,
        "byte_size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    batches = list(subject._verified_batches(tmp_path, (record,)))
    assert batches[0][1].num_rows == 1

    path.write_bytes(path.read_bytes() + b"changed")
    try:
        list(subject._verified_batches(tmp_path, (record,)))
    except ValueError as error:
        assert "content verification" in str(error)
    else:
        raise AssertionError("changed transfer partition was accepted")


def test_rows_projects_only_requested_columns() -> None:
    batch = pa.record_batch(
        [["asset-a"], [123], ["ignored"]], names=["asset_id", "volume_raw", "extra"]
    )
    dataset_id = subject.uuid.UUID(int=1)

    assert subject._rows(batch, ("asset_id", "volume_raw"), dataset_id) == [
        {"asset_id": "asset-a", "volume_raw": 123, "dataset": dataset_id}
    ]
