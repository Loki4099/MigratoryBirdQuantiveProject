from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.green_baseline_foundation import _BoundConnection
from style_rotation.v022.green_baseline_import import (
    DatasetImportPlan,
    GreenBaselineImportPlan,
    build_green_baseline_import_plan,
)

_CONTRACT = "migratory_bird_v022_green_baseline_dataset_publication_v1"
_BATCH_ROWS = 50_000


@dataclass(frozen=True, slots=True)
class GreenBaselineDatasetImportSpec:
    transfer_root: Path
    plan: GreenBaselineImportPlan
    dataset_key: str


@dataclass(frozen=True, slots=True)
class GreenBaselineDatasetPublication:
    contract: str
    dataset_publication_id: str
    artifact_id: str
    dataset_key: str
    version_number: int
    daily_bar_rows: int
    corporate_action_rows: int
    asset_count: int
    coverage_start: str
    coverage_end: str
    partition_manifest_hash: str
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _manifest_records(root: Path) -> tuple[dict[str, Any], ...]:
    return tuple(
        json.loads(line)
        for line in (root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    )


def _dataset(plan: GreenBaselineImportPlan, dataset_key: str) -> DatasetImportPlan:
    matches = [item for item in plan.datasets if item.target_dataset_key == dataset_key]
    if len(matches) != 1:
        raise ValueError(f"green baseline plan must contain one Dataset {dataset_key}")
    return matches[0]


def _records(
    root: Path, dataset: DatasetImportPlan
) -> tuple[dict[str, Any], ...]:
    selected = tuple(
        record
        for record in _manifest_records(root)
        if record.get("dataset_publication_id")
        == dataset.source_dataset_publication_id
        and record.get("kind") in {"daily_bar", "corporate_action"}
    )
    if sum(record["kind"] == "daily_bar" for record in selected) != dataset.daily_bar_files:
        raise ValueError("daily-bar partition count differs from the frozen import plan")
    if sum(record["kind"] == "corporate_action" for record in selected) != (
        dataset.corporate_action_files
    ):
        raise ValueError("corporate-action partition count differs from the frozen import plan")
    return tuple(sorted(selected, key=lambda item: str(item["path"])))


def _partition_manifest_hash(records: Sequence[Mapping[str, Any]]) -> str:
    payload = [
        {
            "path": item["path"],
            "kind": item["kind"],
            "security_id": item["security_id"],
            "year": item["year"],
            "row_count": item["row_count"],
            "min_date": item["min_date"],
            "max_date": item["max_date"],
            "byte_size": item["byte_size"],
            "sha256": item["sha256"],
        }
        for item in records
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_batches(
    root: Path, records: Sequence[Mapping[str, Any]]
) -> Iterator[tuple[str, pa.RecordBatch]]:
    for record in records:
        path = root / str(record["path"])
        if path.stat().st_size != int(record["byte_size"]) or _sha256(path) != record["sha256"]:
            raise ValueError(f"transfer partition fails content verification: {record['path']}")
        parquet = pq.ParquetFile(path)
        observed = 0
        for batch in parquet.iter_batches(batch_size=_BATCH_ROWS):
            observed += batch.num_rows
            yield str(record["kind"]), batch
        if observed != int(record["row_count"]):
            raise ValueError(f"transfer partition row count changed: {record['path']}")


_BAR_INSERT = text(
    """
    INSERT INTO data.daily_bar (
        dataset_publication_id, asset_id, session_date, open_raw, high_raw, low_raw,
        close_raw, adj_close, open_adj, high_adj, low_adj, close_adj,
        adjustment_factor, volume_raw
    ) VALUES (
        :dataset, :asset_id, :session_date, :open_raw, :high_raw, :low_raw,
        :close_raw, :adj_close, :open_adj, :high_adj, :low_adj, :close_adj,
        :adjustment_factor, :volume_raw
    )
    """
)
_ACTION_INSERT = text(
    """
    INSERT INTO data.corporate_action (
        dataset_publication_id, asset_id, effective_date, cash_dividend, split_ratio
    ) VALUES (:dataset, :asset_id, :effective_date, :cash_dividend, :split_ratio)
    """
)


def _rows(
    batch: pa.RecordBatch, columns: Sequence[str], dataset_id: uuid.UUID
) -> list[dict[str, Any]]:
    table = pa.Table.from_batches([batch]).select(columns)
    return [dict(item, dataset=dataset_id) for item in table.to_pylist()]


def _write_partitions(
    connection: Connection,
    root: Path,
    records: Sequence[Mapping[str, Any]],
    dataset_id: uuid.UUID,
) -> tuple[int, int]:
    bars = 0
    actions = 0
    for kind, batch in _verified_batches(root, records):
        if kind == "daily_bar":
            values = _rows(
                batch,
                (
                    "asset_id",
                    "session_date",
                    "open_raw",
                    "high_raw",
                    "low_raw",
                    "close_raw",
                    "adj_close",
                    "open_adj",
                    "high_adj",
                    "low_adj",
                    "close_adj",
                    "adjustment_factor",
                    "volume_raw",
                ),
                dataset_id,
            )
            connection.execute(_BAR_INSERT, values)
            bars += len(values)
        else:
            values = _rows(
                batch,
                ("asset_id", "effective_date", "cash_dividend", "split_ratio"),
                dataset_id,
            )
            connection.execute(_ACTION_INSERT, values)
            actions += len(values)
    return bars, actions


def _dependency(connection: Connection, artifact_type: str, artifact_key: str) -> uuid.UUID:
    return cast(
        uuid.UUID,
        connection.execute(
            text(
                "SELECT artifact_id FROM lineage.artifact WHERE artifact_type=:type "
                "AND artifact_key=:key AND status='published' "
                "ORDER BY version_number DESC LIMIT 1"
            ),
            {"type": artifact_type, "key": artifact_key},
        ).scalar_one(),
    )


def publish_green_baseline_dataset(
    engine: Engine, spec: GreenBaselineDatasetImportSpec
) -> GreenBaselineDatasetPublication:
    expected = build_green_baseline_import_plan(spec.transfer_root)
    if expected.to_dict() != spec.plan.to_dict():
        raise ValueError("green baseline import plan is stale or does not match the transfer")
    dataset = _dataset(spec.plan, spec.dataset_key)
    records = _records(spec.transfer_root, dataset)
    partition_hash = _partition_manifest_hash(records)
    dataset_id = uuid.UUID(dataset.dataset_publication_id)
    semantic = {
        "contract": _CONTRACT,
        "plan_fingerprint": spec.plan.plan_fingerprint,
        "dataset": asdict(dataset),
        "partition_manifest_hash": partition_hash,
        "price_semantics": (
            "historical_constituent_pit__frozen_reconciled_retrospective_"
            "split_normalized_total_return_prices"
        ),
    }
    content = {
        **semantic,
        "transfer_manifest_sha256": spec.plan.transfer_manifest_sha256,
        "partition_count": len(records),
        "partition_bytes": sum(int(item["byte_size"]) for item in records),
    }

    with engine.begin() as connection:
        import_artifact = _dependency(
            connection,
            "v022_external_import_manifest",
            "v022_external_import_manifest__v022_green_transfer_baseline",
        )
        master_artifact = _dependency(connection, "catalog_master_data_release", "research_scope")
        calendar_artifact = _dependency(connection, "calendar_version", "XNYS")
        cleaning_artifact = _dependency(connection, "cleaning_version", "adjusted_ohlc")
        calendar_id = connection.execute(
            text("SELECT calendar_version_id FROM catalog.calendar_version WHERE artifact_id=:id"),
            {"id": calendar_artifact},
        ).scalar_one()
        cleaning_id = connection.execute(
            text("SELECT cleaning_version_id FROM data.cleaning_version WHERE artifact_id=:id"),
            {"id": cleaning_artifact},
        ).scalar_one()

        def writer(draft: Connection, artifact_id: uuid.UUID) -> None:
            draft.execute(
                text(
                    "INSERT INTO data.dataset_publication "
                    "(dataset_publication_id,artifact_id,cleaning_version_id,calendar_version_id,"
                    "dataset_key,version_number,dataset_kind,value_kind,coverage_start,"
                    "coverage_end,row_count) VALUES (:id,:artifact,:cleaning,:calendar,:key,"
                    ":version,'canonical','daily_bar',"
                    ":start,:end,:rows)"
                ),
                {
                    "id": dataset_id,
                    "artifact": artifact_id,
                    "cleaning": cleaning_id,
                    "calendar": calendar_id,
                    "key": dataset.target_dataset_key,
                    "version": dataset.target_dataset_version,
                    "start": dataset.coverage_start,
                    "end": dataset.coverage_end,
                    "rows": dataset.daily_bar_rows,
                },
            )
            bar_count, action_count = _write_partitions(
                draft, spec.transfer_root, records, dataset_id
            )
            if (bar_count, action_count) != (
                dataset.daily_bar_rows,
                dataset.corporate_action_rows,
            ):
                raise ValueError("imported market row counts differ from the frozen plan")
            draft.execute(
                text(
                    """
                    INSERT INTO data.dataset_coverage (
                        dataset_coverage_id,dataset_publication_id,asset_id,subject_key,
                        coverage_start,coverage_end,observation_count,missing_count
                    )
                    SELECT gen_random_uuid(), :dataset, bars.asset_id, asset.asset_key,
                           min(bars.session_date), max(bars.session_date), count(*),
                           (SELECT count(*) FROM catalog.calendar_session sessions
                            WHERE sessions.calendar_version_id=:calendar
                              AND sessions.session_date BETWEEN min(bars.session_date)
                                                            AND max(bars.session_date)) - count(*)
                    FROM data.daily_bar bars
                    JOIN catalog.asset asset ON asset.asset_id=bars.asset_id
                    WHERE bars.dataset_publication_id=:dataset
                    GROUP BY bars.asset_id, asset.asset_key
                    """
                ),
                {"dataset": dataset_id, "calendar": calendar_id},
            )
            coverage_count = draft.execute(
                text("SELECT count(*) FROM data.dataset_coverage WHERE dataset_publication_id=:id"),
                {"id": dataset_id},
            ).scalar_one()
            if coverage_count != dataset.asset_count:
                raise ValueError("Dataset coverage count differs from the frozen plan")

        publication = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
            artifact_type=dataset.artifact_type,
            artifact_key=dataset.artifact_key,
            version_number=dataset.target_dataset_version,
            semantic_payload=semantic,
            content_payload=content,
            dependencies=(
                DependencyInput(import_artifact, "external_import_manifest", 0),
                DependencyInput(master_artifact, "master_data_release", 1),
                DependencyInput(calendar_artifact, "calendar_version", 2),
                DependencyInput(cleaning_artifact, "cleaning_version", 3),
            ),
            reason=f"publish clean-green baseline Dataset {dataset.target_dataset_key}",
            draft_writer=writer,
        )
    return GreenBaselineDatasetPublication(
        contract=_CONTRACT,
        dataset_publication_id=str(dataset_id),
        artifact_id=str(publication.artifact_id),
        dataset_key=dataset.target_dataset_key,
        version_number=dataset.target_dataset_version,
        daily_bar_rows=dataset.daily_bar_rows,
        corporate_action_rows=dataset.corporate_action_rows,
        asset_count=dataset.asset_count,
        coverage_start=dataset.coverage_start,
        coverage_end=dataset.coverage_end,
        partition_manifest_hash=partition_hash,
        reused=publication.reused,
    )
