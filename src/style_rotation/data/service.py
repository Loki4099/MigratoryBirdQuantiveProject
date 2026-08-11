from __future__ import annotations

import hashlib
import json
import uuid
import zlib
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.data.contracts import (
    CleaningContract,
    DataContractsCatalog,
    SeriesContract,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult


@dataclass(frozen=True, slots=True)
class SnapshotInput:
    series_key: str
    series_version: int
    snapshot_key: str
    requested_at: datetime
    fetched_at: datetime
    as_of_at: datetime
    media_type: str
    request_parameters: dict[str, Any]
    response_metadata: dict[str, Any]
    raw_payload: bytes


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)


def publish_data_contracts(engine: Engine, catalog_path: Path) -> list[dict[str, Any]]:
    catalog = DataContractsCatalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))
    with engine.begin() as connection:
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        scope_artifact_id = connection.execute(
            text(
                "SELECT artifact_id FROM lineage.artifact "
                "WHERE artifact_type = 'catalog_master_data_release' "
                "AND artifact_key = 'research_scope' AND status = 'published' "
                "ORDER BY version_number DESC LIMIT 1"
            )
        ).scalar_one_or_none()
        if scope_artifact_id is None:
            raise ValueError("Publish the research scope before data contracts")
        master_payload = catalog.model_dump(
            mode="json",
            exclude={
                "series": {"__all__": {"version"}},
                "cleaning": {
                    "__all__": {
                        "version_number",
                        "implementation_key",
                        "implementation_version",
                        "configuration",
                    }
                },
            },
        )
        release = service.publish(
            artifact_type="data_contract_release",
            artifact_key="data_contracts",
            version_number=semantic_version_number(catalog.catalog_version),
            semantic_payload=master_payload,
            content_payload=master_payload,
            dependencies=(DependencyInput(scope_artifact_id, "research_scope", 0),),
            reason=f"bootstrap data contracts {catalog.catalog_version}",
            draft_writer=partial(_write_release, catalog=catalog),
        )
        ids = _contract_ids(connection, release.artifact_id)
        results: list[tuple[str, PublicationResult]] = [("data_contract_release", release)]
        for series_item in catalog.series:
            payload = series_item.model_dump(mode="json")
            result = service.publish(
                artifact_type="data_series_version",
                artifact_key=series_item.key,
                version_number=series_item.version.version_number,
                semantic_payload=payload,
                content_payload=payload,
                dependencies=(DependencyInput(release.artifact_id, "data_contract_release", 0),),
                reason=(
                    f"publish data series {series_item.key} v{series_item.version.version_number}"
                ),
                draft_writer=partial(_write_series_version, item=series_item, ids=ids),
            )
            results.append(("data_series_version", result))
        for cleaning_item in catalog.cleaning:
            payload = cleaning_item.model_dump(mode="json")
            result = service.publish(
                artifact_type="cleaning_version",
                artifact_key=cleaning_item.key,
                version_number=cleaning_item.version_number,
                semantic_payload=payload,
                content_payload=payload,
                dependencies=(DependencyInput(release.artifact_id, "data_contract_release", 0),),
                reason=f"publish cleaning {cleaning_item.key} v{cleaning_item.version_number}",
                draft_writer=partial(_write_cleaning_version, item=cleaning_item, ids=ids),
            )
            results.append(("cleaning_version", result))
    return [_result(kind, result) for kind, result in results]


class SourceSnapshotService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, item: SnapshotInput) -> PublicationResult:
        if item.fetched_at < item.requested_at:
            raise ValueError("Snapshot fetched_at cannot precede requested_at")
        payload_hash = hashlib.sha256(item.raw_payload).hexdigest()
        compressed = zlib.compress(item.raw_payload, level=9)
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT version.data_series_version_id, version.artifact_id,
                           version.parser_version
                    FROM data.data_series_version version
                    JOIN data.data_series_definition definition
                      ON definition.data_series_definition_id = version.data_series_definition_id
                    JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id
                    WHERE definition.series_key = :series_key
                      AND version.version_number = :version_number
                      AND artifact.status = 'published'
                    """
                    ),
                    {"series_key": item.series_key, "version_number": item.series_version},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ValueError("Published data series version not found")
        semantic = {
            "series_key": item.series_key,
            "series_version": item.series_version,
            "snapshot_key": item.snapshot_key,
            "requested_at": item.requested_at,
            "fetched_at": item.fetched_at,
            "as_of_at": item.as_of_at,
            "media_type": item.media_type,
            "request_parameters": item.request_parameters,
            "parser_version": row["parser_version"],
        }
        content = {
            **semantic,
            "response_metadata": item.response_metadata,
            "payload_hash": payload_hash,
            "raw_size_bytes": len(item.raw_payload),
        }
        return self._artifacts.publish(
            artifact_type="source_snapshot",
            artifact_key=f"{item.series_key}:{item.snapshot_key}",
            version_number=1,
            semantic_payload=semantic,
            content_payload=content,
            dependencies=(DependencyInput(row["artifact_id"], "data_series_version", 0),),
            reason=f"publish source snapshot {item.snapshot_key}",
            draft_writer=lambda connection, artifact_id: _write_snapshot(
                connection,
                artifact_id,
                item,
                row["data_series_version_id"],
                str(row["parser_version"]),
                payload_hash,
                compressed,
            ),
        )

    def raw_payload(self, artifact_id: uuid.UUID) -> bytes:
        with self._engine.connect() as connection:
            payload = connection.execute(
                text(
                    "SELECT compressed_payload FROM data.source_snapshot "
                    "WHERE artifact_id = :artifact_id"
                ),
                {"artifact_id": artifact_id},
            ).scalar_one_or_none()
        if payload is None:
            raise LookupError(f"Source snapshot not found: {artifact_id}")
        return zlib.decompress(payload)


def _write_release(
    connection: Connection, artifact_id: uuid.UUID, *, catalog: DataContractsCatalog
) -> None:
    release_id = uuid.uuid4()
    _insert(
        connection,
        "data_contract_release",
        {
            "data_contract_release_id": release_id,
            "artifact_id": artifact_id,
            "release_key": "data_contracts",
            "version_number": semantic_version_number(catalog.catalog_version),
        },
    )
    for provider in catalog.providers:
        _insert(
            connection,
            "source_provider",
            {
                "source_provider_id": uuid.uuid4(),
                "data_contract_release_id": release_id,
                **provider.model_dump(mode="json", by_alias=True),
            }
            | {"provider_key": provider.key},
            remove=("key",),
        )
    for series in catalog.series:
        _insert(
            connection,
            "data_series_definition",
            {
                "data_series_definition_id": uuid.uuid4(),
                "data_contract_release_id": release_id,
                "series_key": series.key,
                "name": series.name,
                "description": series.description,
                "subject_type": series.subject_type,
                "value_kind": series.value_kind,
            },
        )
    for cleaning in catalog.cleaning:
        _insert(
            connection,
            "cleaning_definition",
            {
                "cleaning_definition_id": uuid.uuid4(),
                "data_contract_release_id": release_id,
                "cleaning_key": cleaning.key,
                "name": cleaning.name,
                "description": cleaning.description,
            },
        )


def _contract_ids(connection: Connection, artifact_id: uuid.UUID) -> dict[str, Any]:
    release_id = connection.execute(
        text(
            "SELECT data_contract_release_id FROM data.data_contract_release "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": artifact_id},
    ).scalar_one()
    provider_rows = connection.execute(
        text(
            "SELECT provider_key, source_provider_id FROM data.source_provider "
            "WHERE data_contract_release_id = :release_id"
        ),
        {"release_id": release_id},
    ).mappings()
    providers = {str(row["provider_key"]): row["source_provider_id"] for row in provider_rows}
    series_rows = connection.execute(
        text(
            "SELECT series_key, data_series_definition_id "
            "FROM data.data_series_definition "
            "WHERE data_contract_release_id = :release_id"
        ),
        {"release_id": release_id},
    ).mappings()
    series = {str(row["series_key"]): row["data_series_definition_id"] for row in series_rows}
    cleaning_rows = connection.execute(
        text(
            "SELECT cleaning_key, cleaning_definition_id "
            "FROM data.cleaning_definition "
            "WHERE data_contract_release_id = :release_id"
        ),
        {"release_id": release_id},
    ).mappings()
    cleaning = {str(row["cleaning_key"]): row["cleaning_definition_id"] for row in cleaning_rows}
    return {"providers": providers, "series": series, "cleaning": cleaning}


def _write_series_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    item: SeriesContract,
    ids: dict[str, Any],
) -> None:
    values = item.version.model_dump(mode="json")
    provider = values.pop("provider")
    _insert(
        connection,
        "data_series_version",
        {
            "data_series_version_id": uuid.uuid4(),
            "data_series_definition_id": ids["series"][item.key],
            "source_provider_id": ids["providers"][provider],
            "artifact_id": artifact_id,
            **values,
        },
    )


def _write_cleaning_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    item: CleaningContract,
    ids: dict[str, Any],
) -> None:
    _insert(
        connection,
        "cleaning_version",
        {
            "cleaning_version_id": uuid.uuid4(),
            "cleaning_definition_id": ids["cleaning"][item.key],
            "artifact_id": artifact_id,
            "version_number": item.version_number,
            "implementation_key": item.implementation_key,
            "implementation_version": item.implementation_version,
            "configuration": item.configuration,
        },
    )


def _write_snapshot(
    connection: Connection,
    artifact_id: uuid.UUID,
    item: SnapshotInput,
    series_version_id: uuid.UUID,
    parser_version: str,
    payload_hash: str,
    compressed: bytes,
) -> None:
    _insert(
        connection,
        "source_snapshot",
        {
            "source_snapshot_id": uuid.uuid4(),
            "data_series_version_id": series_version_id,
            "artifact_id": artifact_id,
            "snapshot_key": item.snapshot_key,
            "requested_at": item.requested_at,
            "fetched_at": item.fetched_at,
            "as_of_at": item.as_of_at,
            "media_type": item.media_type,
            "parser_version": parser_version,
            "request_parameters": item.request_parameters,
            "response_metadata": item.response_metadata,
            "payload_hash": payload_hash,
            "payload_compression": "zlib",
            "raw_size_bytes": len(item.raw_payload),
            "compressed_payload": compressed,
        },
    )


def _insert(
    connection: Connection,
    table_name: str,
    values: dict[str, Any],
    *,
    remove: tuple[str, ...] = (),
) -> None:
    payload = {key: value for key, value in values.items() if key not in remove}
    columns = ", ".join(payload)
    placeholders = ", ".join(
        f"CAST(:{key} AS jsonb)" if isinstance(value, dict) else f":{key}"
        for key, value in payload.items()
    )
    parameters = {
        key: json.dumps(value) if isinstance(value, dict) else value
        for key, value in payload.items()
    }
    connection.execute(
        text(f"INSERT INTO data.{table_name} ({columns}) VALUES ({placeholders})"),
        parameters,
    )


def _result(kind: str, result: PublicationResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["artifact_id"] = str(result.artifact_id)
    return {"catalog_type": kind, **payload}
