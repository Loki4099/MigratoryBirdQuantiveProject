from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict
from typing import Any, cast

from sqlalchemy import Engine, create_engine, text

from style_rotation.data.bundle import publish_data_bundle
from style_rotation.v022.green_baseline_registry import (
    GREEN_BASELINE_BENCHMARK_DATASET_KEY,
    GREEN_BASELINE_BENCHMARK_DATASET_VERSION,
)


def _published_artifact(
    engine: Engine,
    *,
    table: str,
    key_column: str,
    key: str,
    version_column: str,
    version: int,
) -> uuid.UUID:
    allowed = {
        ("data.dataset_publication", "dataset_key", "version_number"),
    }
    if (table, key_column, version_column) not in allowed:
        raise ValueError("Unsupported green Bundle identity lookup")
    with engine.connect() as connection:
        values = tuple(
            connection.scalars(
                text(
                    f"SELECT source.artifact_id FROM {table} source "
                    "JOIN lineage.artifact artifact "
                    "ON artifact.artifact_id=source.artifact_id "
                    f"WHERE source.{key_column}=:key "
                    f"AND source.{version_column}=:version "
                    "AND artifact.status='published'"
                ),
                {"key": key, "version": version},
            ).all()
        )
    if len(values) != 1:
        raise LookupError(f"Expected one published {key}@{version}, found {len(values)}")
    return cast(uuid.UUID, values[0])


def _reserve_model_artifact(engine: Engine) -> uuid.UUID:
    with engine.connect() as connection:
        values = tuple(
            connection.scalars(
                text(
                    "SELECT version.artifact_id "
                    "FROM experiment.reserve_return_model_version version "
                    "JOIN experiment.reserve_return_model_definition definition "
                    "ON definition.reserve_return_model_definition_id="
                    "version.reserve_return_model_definition_id "
                    "JOIN lineage.artifact artifact "
                    "ON artifact.artifact_id=version.artifact_id "
                    "WHERE definition.model_key='dgs3mo_cash_accrual_proxy' "
                    "AND version.version_number=1 AND artifact.status='published'"
                )
            ).all()
        )
    if len(values) != 1:
        raise LookupError(f"Expected one published reserve model, found {len(values)}")
    return cast(uuid.UUID, values[0])


def _calendar_artifact(engine: Engine) -> uuid.UUID:
    with engine.connect() as connection:
        values = tuple(
            connection.scalars(
                text(
                    "SELECT version.artifact_id FROM catalog.calendar_version version "
                    "JOIN catalog.calendar_definition definition "
                    "ON definition.calendar_definition_id=version.calendar_definition_id "
                    "JOIN lineage.artifact artifact "
                    "ON artifact.artifact_id=version.artifact_id "
                    "WHERE definition.calendar_key='XNYS' "
                    "AND version.version_number=1 AND artifact.status='published'"
                )
            ).all()
        )
    if len(values) != 1:
        raise LookupError(f"Expected one published XNYS calendar, found {len(values)}")
    return cast(uuid.UUID, values[0])


def publish_green_research_bundle(engine: Engine) -> list[dict[str, Any]]:
    benchmark = _published_artifact(
        engine,
        table="data.dataset_publication",
        key_column="dataset_key",
        key=GREEN_BASELINE_BENCHMARK_DATASET_KEY,
        version_column="version_number",
        version=GREEN_BASELINE_BENCHMARK_DATASET_VERSION,
    )
    rate = _published_artifact(
        engine,
        table="data.dataset_publication",
        key_column="dataset_key",
        key="dgs3mo_canonical",
        version_column="version_number",
        version=1,
    )
    reserve = _published_artifact(
        engine,
        table="data.dataset_publication",
        key_column="dataset_key",
        key="dgs3mo_reserve_return",
        version_column="version_number",
        version=1,
    )
    calendar = _calendar_artifact(engine)
    _reserve_model_artifact(engine)
    publications = publish_data_bundle(
        engine,
        benchmark,
        rate,
        reserve,
        calendar,
        version_number=1,
        market_dataset_key=GREEN_BASELINE_BENCHMARK_DATASET_KEY,
    )
    return [
        {
            **asdict(item),
            "artifact_id": str(item.artifact_id),
        }
        for item in publications
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish the clean-green research Bundle")
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    engine = create_engine(args.database_url)
    try:
        result = publish_green_research_bundle(engine)
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
