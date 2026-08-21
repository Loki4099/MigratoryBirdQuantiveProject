from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import psycopg

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
verify = cast(
    Callable[[Path], dict[str, Any]],
    importlib.import_module("scripts.export_v022_green_transfer").verify,
)

EXPECTED_ALEMBIC_REVISION = "20260821_142_asset_export"
FORBIDDEN_ID_LOCATIONS = (
    (
        "986351e7-e54f-5981-bf70-a51b9fb5bb1a",
        "data.dataset_publication",
        "dataset_publication_id",
    ),
    (
        "f8f841f0-23a3-5653-857c-00a2c41c297f",
        "product.v022_product_enrollment",
        "product_enrollment_id",
    ),
    (
        "186b031a-ca3b-52aa-812c-5e3970f58cde",
        "product.v022_product_enrollment",
        "product_enrollment_id",
    ),
)
EMPTY_RELATIONS = (
    "data.dataset_publication",
    "workspace.v022_graph_draft",
    "experiment.v022_research_suite",
    "experiment.v022_portfolio_cell_runtime_result",
    "experiment.v022_result_evidence_snapshot",
    "product.v022_product_enrollment",
)


@dataclass(frozen=True, slots=True)
class GreenTargetSnapshot:
    database_name: str
    alembic_revision: str
    relation_counts: Mapping[str, int]
    forbidden_id_hits: Mapping[str, int]


def validate_green_target_snapshot(
    snapshot: GreenTargetSnapshot, *, expected_database: str
) -> None:
    errors: list[str] = []
    if snapshot.database_name != expected_database:
        errors.append(
            f"database_name:{snapshot.database_name!r}!={expected_database!r}"
        )
    if snapshot.alembic_revision != EXPECTED_ALEMBIC_REVISION:
        errors.append(f"alembic_revision:{snapshot.alembic_revision}")
    errors.extend(
        f"not_empty:{relation}={count}"
        for relation, count in snapshot.relation_counts.items()
        if count != 0
    )
    errors.extend(
        f"forbidden_id:{identifier}={count}"
        for identifier, count in snapshot.forbidden_id_hits.items()
        if count != 0
    )
    if errors:
        raise ValueError(f"green database preflight failed: {errors}")


def _relation_count(
    connection: psycopg.Connection[Any], relation: str
) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)::text", (relation,))
        relation_row = cursor.fetchone()
        if relation_row is None or relation_row[0] is None:
            raise ValueError(f"green database is missing migrated relation {relation}")
        cursor.execute(f"SELECT count(*) FROM {relation}")
        count_row = cursor.fetchone()
        if count_row is None:
            raise ValueError(f"green database did not return count for {relation}")
        return int(count_row[0])


def inspect_green_target(database_url: str) -> GreenTargetSnapshot:
    with psycopg.connect(database_url, autocommit=False) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            database_row = cursor.fetchone()
            if database_row is None:
                raise ValueError("green database did not return current_database()")
            database_name = str(database_row[0])
            cursor.execute("SELECT version_num FROM alembic_version")
            revision_rows = cursor.fetchall()
            if len(revision_rows) != 1:
                raise ValueError("green database must have exactly one Alembic head")
            alembic_revision = str(revision_rows[0][0])
            forbidden_hits: dict[str, int] = {}
            for identifier, relation, column in FORBIDDEN_ID_LOCATIONS:
                cursor.execute(
                    f"SELECT count(*) FROM {relation} WHERE {column}=%s",
                    (identifier,),
                )
                hit_row = cursor.fetchone()
                if hit_row is None:
                    raise ValueError(f"green database did not inspect {relation}")
                forbidden_hits[identifier] = int(hit_row[0])
        relation_counts = {
            relation: _relation_count(connection, relation)
            for relation in EMPTY_RELATIONS
        }
        connection.rollback()
    return GreenTargetSnapshot(
        database_name=database_name,
        alembic_revision=alembic_revision,
        relation_counts=relation_counts,
        forbidden_id_hits=forbidden_hits,
    )


def preflight(
    *, database_url: str, expected_database: str, package_root: Path
) -> dict[str, Any]:
    package_report = verify(package_root)
    snapshot = inspect_green_target(database_url)
    validate_green_target_snapshot(snapshot, expected_database=expected_database)
    return {
        "passed": True,
        "verified_at": datetime.now().astimezone().isoformat(),
        "package": package_report,
        "target": {
            "database_name": snapshot.database_name,
            "alembic_revision": snapshot.alembic_revision,
            "relation_counts": dict(snapshot.relation_counts),
            "forbidden_id_hits": dict(snapshot.forbidden_id_hits),
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--expected-database", default="style_rotation_green")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    result = preflight(
        database_url=args.database_url,
        expected_database=args.expected_database,
        package_root=args.package.resolve(),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.report is None:
        print(rendered, end="")
    else:
        args.report.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
