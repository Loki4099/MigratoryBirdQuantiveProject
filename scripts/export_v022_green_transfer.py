from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

csv.field_size_limit(2**31 - 1)

RISK_DATASET_ID = "7b8940ed-85ea-5109-81c2-f8e5d7fffc78"
BENCHMARK_DATASET_ID = "528886d4-42ed-4564-a079-80379a82812b"

# These files are source facts for a new green publication.  They are not COPY
# dumps: importers must create fresh Artifact/Dataset/Gate/Cohort identities.
# In particular, old publication and runtime projections are intentionally not
# allowed in the transfer package.
SOURCE_FACT_TABLES = (
    "catalog.issuer",
    "catalog.asset",
    "catalog.asset_identifier",
    "catalog.asset_listing",
    "catalog.asset_classification",
    "catalog.security",
    "catalog.security_identifier",
    "catalog.security_profile",
    "catalog.security_capability",
    "catalog.calendar_version",
    "catalog.calendar_session",
    "catalog.v022_universe_membership_ledger",
    "catalog.v022_universe_change_batch",
    "catalog.v022_universe_membership_event",
    "catalog.v022_security_identity_review_case",
    "catalog.v022_security_lifecycle_event",
    "catalog.v022_security_settlement_leg",
    "catalog.v022_security_identity_evidence",
    "catalog.v022_security_identity_resolution",
    "catalog.v022_security_identity_resolution_evidence",
    "catalog.v022_security_lifecycle_event_evidence",
    "catalog.v022_security_terminal_event_evidence_binding",
    "data.v022_external_import_manifest",
    "data.v022_external_import_object",
)

FORBIDDEN_METADATA_DOMAINS = (
    "data.cleaning_version",
    "data.dataset_publication",
    "data.dataset_coverage",
    "data.quality_issue",
    "data.v022_dataset_gate_",
    "data.v022_reconciled_market_dataset_binding",
    "data.v022_security_market_dataset_binding",
    "data.v022_market_gap_resolution",
    "experiment.",
    "workspace.",
    "product.",
    "lineage.",
)

BAR_SCHEMA = pa.schema(
    [
        ("dataset_publication_id", pa.string()),
        ("dataset_key", pa.string()),
        ("dataset_version", pa.int32()),
        ("security_id", pa.string()),
        ("security_key", pa.string()),
        ("asset_id", pa.string()),
        ("asset_key", pa.string()),
        ("session_date", pa.date32()),
        ("open_raw", pa.decimal128(24, 10)),
        ("high_raw", pa.decimal128(24, 10)),
        ("low_raw", pa.decimal128(24, 10)),
        ("close_raw", pa.decimal128(24, 10)),
        ("adj_close", pa.decimal128(24, 10)),
        ("open_adj", pa.decimal128(24, 10)),
        ("high_adj", pa.decimal128(24, 10)),
        ("low_adj", pa.decimal128(24, 10)),
        ("close_adj", pa.decimal128(24, 10)),
        ("adjustment_factor", pa.decimal128(24, 14)),
        ("volume_raw", pa.int64()),
    ]
)

ACTION_SCHEMA = pa.schema(
    [
        ("dataset_publication_id", pa.string()),
        ("dataset_key", pa.string()),
        ("dataset_version", pa.int32()),
        ("security_id", pa.string()),
        ("security_key", pa.string()),
        ("asset_id", pa.string()),
        ("asset_key", pa.string()),
        ("effective_date", pa.date32()),
        ("cash_dividend", pa.decimal128(24, 10)),
        ("split_ratio", pa.decimal128(24, 10)),
    ]
)


@dataclass(frozen=True)
class FileRecord:
    path: str
    kind: str
    dataset_publication_id: str | None
    security_id: str | None
    security_key: str | None
    year: int | None
    row_count: int
    min_date: str | None
    max_date: str | None
    byte_size: int
    sha256: str


def _safe_segment(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    if not normalized:
        raise ValueError(f"unsafe empty path segment for {value!r}")
    return normalized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    raise TypeError(type(value).__name__)


def _write_parquet_partition(
    root: Path,
    *,
    dataset_id: str,
    dataset_key: str,
    dataset_version: int,
    security_id: str,
    security_key: str,
    year: int,
    kind: str,
    schema: pa.Schema,
    rows: list[tuple[Any, ...]],
    date_index: int,
) -> FileRecord:
    relative = Path(
        f"dataset={_safe_segment(dataset_key)}_v{dataset_version}",
        f"security={_safe_segment(security_key)}",
        f"year={year}",
        f"{kind}.parquet",
    )
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(
        [dict(zip(schema.names, row, strict=True)) for row in rows], schema=schema
    )
    pq.write_table(table, target, compression="zstd", use_dictionary=True)
    dates = [row[date_index] for row in rows]
    return FileRecord(
        path=relative.as_posix(),
        kind=kind,
        dataset_publication_id=dataset_id,
        security_id=security_id,
        security_key=security_key,
        year=year,
        row_count=len(rows),
        min_date=str(min(dates)),
        max_date=str(max(dates)),
        byte_size=target.stat().st_size,
        sha256=_sha256(target),
    )


def _partitioned_query(
    connection: psycopg.Connection[Any],
    root: Path,
    *,
    dataset_id: str,
    kind: str,
) -> list[FileRecord]:
    if kind == "daily_bar":
        table = "data.daily_bar"
        date_column = "session_date"
        value_columns = (
            "b.open_raw,b.high_raw,b.low_raw,b.close_raw,b.adj_close,b.open_adj,"
            "b.high_adj,b.low_adj,b.close_adj,b.adjustment_factor,b.volume_raw"
        )
        schema = BAR_SCHEMA
        date_index = 7
    else:
        table = "data.corporate_action"
        date_column = "effective_date"
        value_columns = "b.cash_dividend,b.split_ratio"
        schema = ACTION_SCHEMA
        date_index = 7
    sql = f"""
        SELECT b.dataset_publication_id::text,d.dataset_key,d.version_number,
               s.security_id::text,s.security_key,b.asset_id::text,a.asset_key,
               b.{date_column},{value_columns}
        FROM {table} b
        JOIN data.dataset_publication d USING (dataset_publication_id)
        JOIN catalog.asset a USING (asset_id)
        JOIN catalog.security s ON s.legacy_asset_id=b.asset_id
        WHERE b.dataset_publication_id=%s
        ORDER BY s.security_key,EXTRACT(YEAR FROM b.{date_column}),b.{date_column}
    """
    records: list[FileRecord] = []
    group_key: tuple[str, str, int] | None = None
    group_rows: list[tuple[Any, ...]] = []
    dataset_key = ""
    dataset_version = 0
    with connection.cursor(name=f"export_{kind}_{dataset_id[:8]}") as cursor:
        cursor.itersize = 20_000
        cursor.execute(sql, (dataset_id,))
        for row in cursor:
            materialized = tuple(row)
            dataset_key = materialized[1]
            dataset_version = materialized[2]
            key = (materialized[3], materialized[4], materialized[7].year)
            if group_key is not None and key != group_key:
                records.append(
                    _write_parquet_partition(
                        root,
                        dataset_id=dataset_id,
                        dataset_key=dataset_key,
                        dataset_version=dataset_version,
                        security_id=group_key[0],
                        security_key=group_key[1],
                        year=group_key[2],
                        kind=kind,
                        schema=schema,
                        rows=group_rows,
                        date_index=date_index,
                    )
                )
                group_rows = []
            group_key = key
            group_rows.append(materialized)
    if group_key is not None:
        records.append(
            _write_parquet_partition(
                root,
                dataset_id=dataset_id,
                dataset_key=dataset_key,
                dataset_version=dataset_version,
                security_id=group_key[0],
                security_key=group_key[1],
                year=group_key[2],
                kind=kind,
                schema=schema,
                rows=group_rows,
                date_index=date_index,
            )
        )
    return records


def _export_metadata(
    connection: psycopg.Connection[Any], root: Path, table_name: str
) -> FileRecord:
    schema_name, relation_name = table_name.split(".", 1)
    relative = Path("metadata", f"{schema_name}.{relation_name}.csv")
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s
            ORDER BY ordinal_position
            """,
            (schema_name, relation_name),
        )
        columns = [row[0] for row in cursor.fetchall()]
        if not columns:
            raise ValueError(f"missing metadata table: {table_name}")
        quoted_columns = ",".join(f'"{column}"' for column in columns)
        copy_sql = (
            f'COPY (SELECT {quoted_columns} FROM "{schema_name}"."{relation_name}") '
            "TO STDOUT WITH (FORMAT CSV, HEADER TRUE, ENCODING 'UTF8')"
        )
        with target.open("wb") as output, cursor.copy(copy_sql) as copy:
            for block in copy:
                output.write(block)
    with target.open("r", encoding="utf-8", newline="") as source:
        row_count = max(sum(1 for _ in csv.reader(source)) - 1, 0)
    return FileRecord(
        path=relative.as_posix(),
        kind="metadata",
        dataset_publication_id=None,
        security_id=None,
        security_key=None,
        year=None,
        row_count=row_count,
        min_date=None,
        max_date=None,
        byte_size=target.stat().st_size,
        sha256=_sha256(target),
    )


def _summary(records: Sequence[FileRecord]) -> dict[str, Any]:
    by_kind: dict[str, dict[str, int]] = {}
    for record in records:
        values = by_kind.setdefault(record.kind, {"files": 0, "rows": 0, "bytes": 0})
        values["files"] += 1
        values["rows"] += record.row_count
        values["bytes"] += record.byte_size
    return {"file_count": len(records), "by_kind": by_kind}


def _validate_transfer_contract(
    package: dict[str, Any], records: Sequence[dict[str, Any]]
) -> None:
    if package.get("contract") != "migratory_bird_v022_green_transfer_v2":
        raise ValueError("unsupported or legacy green transfer contract")
    metadata_policy = package.get("metadata_policy")
    if (
        not isinstance(metadata_policy, dict)
        or metadata_policy.get("direct_copy_allowed") is not False
    ):
        raise ValueError("green transfer metadata must be source facts, not COPY input")
    declared_tables = tuple(metadata_policy.get("tables", ()))
    if declared_tables != SOURCE_FACT_TABLES:
        raise ValueError("green transfer source-fact table allowlist mismatch")
    forbidden_declared = [
        table
        for table in declared_tables
        if any(table.startswith(prefix) for prefix in FORBIDDEN_METADATA_DOMAINS)
    ]
    if forbidden_declared:
        raise ValueError(f"forbidden metadata domains declared: {forbidden_declared}")
    metadata_paths = {
        record["path"]
        for record in records
        if record.get("kind") == "metadata"
    }
    expected_metadata_paths = {
        f"metadata/{table}.csv" for table in SOURCE_FACT_TABLES
    }
    if metadata_paths != expected_metadata_paths:
        raise ValueError("green transfer metadata file allowlist mismatch")


def export(database_url: str, output: Path, *, resume: bool = False) -> None:
    if output.exists() and any(output.iterdir()) and not resume:
        raise ValueError(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    records: list[FileRecord] = []
    with psycopg.connect(database_url, autocommit=False) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for dataset_id in (RISK_DATASET_ID, BENCHMARK_DATASET_ID):
            records.extend(
                _partitioned_query(
                    connection, output, dataset_id=dataset_id, kind="daily_bar"
                )
            )
            records.extend(
                _partitioned_query(
                    connection, output, dataset_id=dataset_id, kind="corporate_action"
                )
            )
        for table_name in SOURCE_FACT_TABLES:
            records.append(_export_metadata(connection, output, table_name))
        connection.rollback()
    ordered = sorted(records, key=lambda record: record.path)
    manifest_lines = [
        json.dumps(asdict(record), sort_keys=True, default=_json_default)
        for record in ordered
    ]
    manifest_payload = ("\n".join(manifest_lines) + "\n").encode("utf-8")
    manifest_path = output / "manifest.jsonl"
    manifest_path.write_bytes(manifest_payload)
    manifest_hash = hashlib.sha256(manifest_payload).hexdigest()
    package = {
        "contract": "migratory_bird_v022_green_transfer_v2",
        "created_at": datetime.now().astimezone().isoformat(),
        "source_datasets": [RISK_DATASET_ID, BENCHMARK_DATASET_ID],
        "excluded_domains": [
            "product",
            "workspace_draft",
            "research_round",
            "suite",
            "result",
            "evidence",
            "ranking",
            "work",
            "cache",
            "payload_manifest",
            "old_dataset_publication",
            "old_gate",
            "old_cohort_runtime",
            "old_artifact_lineage",
        ],
        "metadata_policy": {
            "mode": "source_facts_only",
            "tables": list(SOURCE_FACT_TABLES),
            "forbidden_prefixes": list(FORBIDDEN_METADATA_DOMAINS),
            "direct_copy_allowed": False,
        },
        "manifest_sha256": manifest_hash,
        "summary": _summary(ordered),
        "schemas": {
            "daily_bar": str(BAR_SCHEMA),
            "corporate_action": str(ACTION_SCHEMA),
        },
    }
    package_path = output / "package.json"
    package_path.write_text(
        json.dumps(package, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    (output / "SHA256SUMS").write_text(
        "\n".join(f"{record.sha256}  {record.path}" for record in ordered)
        + f"\n{manifest_hash}  manifest.jsonl\n{_sha256(package_path)}  package.json\n",
        encoding="utf-8",
    )


def verify(output: Path) -> dict[str, Any]:
    package = json.loads((output / "package.json").read_text(encoding="utf-8"))
    manifest_payload = (output / "manifest.jsonl").read_bytes()
    manifest_hash = hashlib.sha256(manifest_payload).hexdigest()
    if manifest_hash != package["manifest_sha256"]:
        raise ValueError("manifest hash mismatch")
    records = [
        json.loads(line)
        for line in manifest_payload.decode("utf-8").splitlines()
        if line
    ]
    _validate_transfer_contract(package, records)
    expected_paths = {record["path"] for record in records}
    bad: list[str] = []
    grouped: dict[str, dict[str, int]] = {}
    for record in records:
        path = output / record["path"]
        if not path.is_file():
            bad.append(f"missing:{record['path']}")
            continue
        if path.stat().st_size != record["byte_size"]:
            bad.append(f"size:{record['path']}")
        if _sha256(path) != record["sha256"]:
            bad.append(f"sha256:{record['path']}")
        if (
            path.suffix == ".parquet"
            and pq.ParquetFile(path).metadata.num_rows != record["row_count"]
        ):
            bad.append(f"rows:{record['path']}")
        dataset_id = record["dataset_publication_id"] or "metadata"
        key = f"{dataset_id}:{record['kind']}"
        values = grouped.setdefault(key, {"files": 0, "rows": 0, "bytes": 0})
        values["files"] += 1
        values["rows"] += record["row_count"]
        values["bytes"] += record["byte_size"]
    actual_paths = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
        and path.name not in {"manifest.jsonl", "package.json", "SHA256SUMS", "verification.json"}
    }
    unexpected = sorted(actual_paths - expected_paths)
    missing_from_tree = sorted(expected_paths - actual_paths)
    if unexpected:
        bad.extend(f"unexpected:{path}" for path in unexpected)
    if missing_from_tree:
        bad.extend(f"tree-missing:{path}" for path in missing_from_tree)
    expected_rows = {
        f"{RISK_DATASET_ID}:daily_bar": 3_034_078,
        f"{RISK_DATASET_ID}:corporate_action": 34_931,
        f"{BENCHMARK_DATASET_ID}:daily_bar": 27_035,
        f"{BENCHMARK_DATASET_ID}:corporate_action": 434,
    }
    for key, count in expected_rows.items():
        if grouped.get(key, {}).get("rows") != count:
            bad.append(f"aggregate:{key}")
    result = {
        "passed": not bad,
        "verified_at": datetime.now().astimezone().isoformat(),
        "manifest_sha256": manifest_hash,
        "records": len(records),
        "grouped": grouped,
        "errors": bad,
    }
    (output / "verification.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if bad:
        raise ValueError(f"transfer verification failed: {bad[:10]}")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if args.verify_only:
        verify(output)
        return 0
    if not args.database_url:
        parser.error("--database-url is required unless --verify-only is used")
    export(args.database_url, output, resume=args.resume)
    verify(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
