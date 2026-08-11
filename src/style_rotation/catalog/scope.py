from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.catalog.contracts import ResearchScopeCatalog
from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult


def publish_research_scope(engine: Engine, catalog_path: Path) -> list[dict[str, Any]]:
    catalog = ResearchScopeCatalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))
    with engine.begin() as transaction:
        service = ArtifactService(cast(Engine, _BoundConnection(transaction)))
        master, universe, requirements = _publish_scope(service, transaction, catalog)
    return [
        _result("master_data_release", master),
        _result("universe_version", universe),
        _result("data_requirement_version", requirements),
    ]


def _publish_scope(
    service: ArtifactService, connection: Connection, catalog: ResearchScopeCatalog
) -> tuple[PublicationResult, PublicationResult, PublicationResult]:
    master_payload = catalog.model_dump(mode="json", exclude={"universe", "data_requirement_set"})
    master = service.publish(
        artifact_type="catalog_master_data_release",
        artifact_key="research_scope",
        version_number=semantic_version_number(catalog.catalog_version),
        semantic_payload=master_payload,
        content_payload=master_payload,
        reason=f"bootstrap research scope master data {catalog.catalog_version}",
        draft_writer=lambda connection, artifact_id: _write_master(
            connection, artifact_id, catalog
        ),
    )
    ids = _master_ids(connection, master.artifact_id)
    universe_payload = catalog.universe.model_dump(mode="json")
    universe = service.publish(
        artifact_type="universe_version",
        artifact_key=catalog.universe.key,
        version_number=catalog.universe.version_number,
        semantic_payload=universe_payload,
        content_payload=universe_payload,
        dependencies=(DependencyInput(master.artifact_id, "master_data_release", 0),),
        reason=f"bootstrap universe {catalog.universe.key} v{catalog.universe.version_number}",
        draft_writer=lambda connection, artifact_id: _write_universe(
            connection, artifact_id, catalog, ids
        ),
    )
    requirement_payload = catalog.data_requirement_set.model_dump(mode="json")
    requirements = service.publish(
        artifact_type="data_requirement_version",
        artifact_key=catalog.data_requirement_set.key,
        version_number=catalog.data_requirement_set.version_number,
        semantic_payload=requirement_payload,
        content_payload=requirement_payload,
        dependencies=(DependencyInput(master.artifact_id, "master_data_release", 0),),
        reason=(
            "bootstrap data requirements "
            f"{catalog.data_requirement_set.key} v{catalog.data_requirement_set.version_number}"
        ),
        draft_writer=lambda connection, artifact_id: _write_requirements(
            connection, artifact_id, catalog, ids
        ),
    )
    return master, universe, requirements


class _BoundConnection:
    """Let nested publications share one outer commit boundary."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)


def _write_master(
    connection: Connection, artifact_id: uuid.UUID, catalog: ResearchScopeCatalog
) -> None:
    release_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO catalog.master_data_release "
            "(master_data_release_id, artifact_id, release_key, version_number, as_of_date) "
            "VALUES (:id, :artifact, 'research_scope', :version, :as_of)"
        ),
        {
            "id": release_id,
            "artifact": artifact_id,
            "version": semantic_version_number(catalog.catalog_version),
            "as_of": catalog.as_of_date,
        },
    )
    calendars: dict[str, uuid.UUID] = {}
    for item in catalog.calendar_definitions:
        item_id = uuid.uuid4()
        calendars[item.key] = item_id
        _insert(
            connection,
            "calendar_definition",
            {
                "calendar_definition_id": item_id,
                "master_data_release_id": release_id,
                "calendar_key": item.key,
                "name": item.name,
                "timezone": item.timezone,
                "venue_mic": item.venue_mic,
            },
        )
    values: dict[tuple[str, str], uuid.UUID] = {}
    for scheme in catalog.classification_schemes:
        scheme_id = uuid.uuid4()
        _insert(
            connection,
            "classification_scheme",
            {
                "classification_scheme_id": scheme_id,
                "master_data_release_id": release_id,
                "scheme_key": scheme.key,
                "name": scheme.name,
            },
        )
        for value in scheme.values:
            value_id = uuid.uuid4()
            values[(scheme.key, value.key)] = value_id
            _insert(
                connection,
                "classification_value",
                {
                    "classification_value_id": value_id,
                    "master_data_release_id": release_id,
                    "classification_scheme_id": scheme_id,
                    "value_key": value.key,
                    "label_key": value.label_key,
                },
            )
    for asset in catalog.assets:
        asset_id = uuid.uuid4()
        listing_id = uuid.uuid4()
        _insert(
            connection,
            "asset",
            {
                "asset_id": asset_id,
                "master_data_release_id": release_id,
                "asset_key": asset.key,
                "name": asset.name,
                "asset_type": asset.asset_type,
                "status": "active",
            },
        )
        _insert(
            connection,
            "asset_identifier",
            {
                "asset_identifier_id": uuid.uuid4(),
                "master_data_release_id": release_id,
                "asset_id": asset_id,
                "identifier_type": "internal_key",
                "identifier_value": asset.key,
            },
        )
        _insert(
            connection,
            "asset_listing",
            {
                "asset_listing_id": listing_id,
                "master_data_release_id": release_id,
                "asset_id": asset_id,
                "calendar_definition_id": calendars[asset.listing.calendar],
                "listing_key": asset.listing.key,
                "venue_mic": asset.listing.venue_mic,
                "currency": asset.listing.currency,
                "timezone": asset.listing.timezone,
            },
        )
        _insert(
            connection,
            "listing_symbol",
            {
                "listing_symbol_id": uuid.uuid4(),
                "master_data_release_id": release_id,
                "asset_listing_id": listing_id,
                "symbol_type": "ticker",
                "symbol": asset.listing.symbol,
            },
        )
        for scheme_key, value_key in asset.classifications.items():
            _insert(
                connection,
                "asset_classification",
                {
                    "asset_classification_id": uuid.uuid4(),
                    "master_data_release_id": release_id,
                    "asset_id": asset_id,
                    "classification_value_id": values[(scheme_key, value_key)],
                },
            )
    _insert(
        connection,
        "universe_definition",
        {
            "universe_definition_id": uuid.uuid4(),
            "master_data_release_id": release_id,
            "universe_key": catalog.universe.key,
            "name": catalog.universe.name,
            "description": catalog.universe.description,
        },
    )
    _insert(
        connection,
        "data_requirement_definition",
        {
            "data_requirement_definition_id": uuid.uuid4(),
            "master_data_release_id": release_id,
            "requirement_set_key": catalog.data_requirement_set.key,
            "name": catalog.data_requirement_set.name,
            "description": catalog.data_requirement_set.description,
        },
    )


def _master_ids(connection: Connection, artifact_id: uuid.UUID) -> dict[str, Any]:
    release_id = connection.execute(
        text(
            "SELECT master_data_release_id FROM catalog.master_data_release "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": artifact_id},
    ).scalar_one()
    asset_rows = connection.execute(
        text(
            "SELECT asset_key, asset_id FROM catalog.asset "
            "WHERE master_data_release_id = :release_id"
        ),
        {"release_id": release_id},
    ).mappings()
    assets: dict[str, uuid.UUID] = {str(row["asset_key"]): row["asset_id"] for row in asset_rows}
    universe_definition_id = connection.execute(
        text(
            "SELECT universe_definition_id FROM catalog.universe_definition "
            "WHERE master_data_release_id = :release_id"
        ),
        {"release_id": release_id},
    ).scalar_one()
    requirement_definition_id = connection.execute(
        text(
            "SELECT data_requirement_definition_id "
            "FROM catalog.data_requirement_definition "
            "WHERE master_data_release_id = :release_id"
        ),
        {"release_id": release_id},
    ).scalar_one()
    return {
        "assets": assets,
        "universe_definition_id": universe_definition_id,
        "requirement_definition_id": requirement_definition_id,
    }


def _write_universe(
    connection: Connection,
    artifact_id: uuid.UUID,
    catalog: ResearchScopeCatalog,
    ids: dict[str, Any],
) -> None:
    version_id = uuid.uuid4()
    _insert(
        connection,
        "universe_version",
        {
            "universe_version_id": version_id,
            "universe_definition_id": ids["universe_definition_id"],
            "artifact_id": artifact_id,
            "version_number": catalog.universe.version_number,
            "member_count": len(catalog.universe.members),
        },
    )
    for member in catalog.universe.members:
        _insert(
            connection,
            "universe_member",
            {
                "universe_member_id": uuid.uuid4(),
                "universe_version_id": version_id,
                "asset_id": ids["assets"][member.asset],
                "role": member.role,
                "ordinal": member.ordinal,
            },
        )


def _write_requirements(
    connection: Connection,
    artifact_id: uuid.UUID,
    catalog: ResearchScopeCatalog,
    ids: dict[str, Any],
) -> None:
    version_id = uuid.uuid4()
    requirement_set = catalog.data_requirement_set
    _insert(
        connection,
        "data_requirement_version",
        {
            "data_requirement_version_id": version_id,
            "data_requirement_definition_id": ids["requirement_definition_id"],
            "artifact_id": artifact_id,
            "version_number": requirement_set.version_number,
            "requirement_count": len(requirement_set.requirements),
        },
    )
    for requirement in requirement_set.requirements:
        values = requirement.model_dump(mode="json")
        values["requirement_key"] = values.pop("key")
        values.update(
            {
                "data_requirement_member_id": uuid.uuid4(),
                "data_requirement_version_id": version_id,
            }
        )
        _insert(connection, "data_requirement_member", values)


def _insert(connection: Connection, table_name: str, values: dict[str, Any]) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join(
        f"CAST(:{column} AS jsonb)" if isinstance(value, list) else f":{column}"
        for column, value in values.items()
    )
    parameters = {
        column: json.dumps(value) if isinstance(value, list) else value
        for column, value in values.items()
    }
    connection.execute(
        text(f"INSERT INTO catalog.{table_name} ({columns}) VALUES ({placeholders})"),
        parameters,
    )


def _result(kind: str, result: PublicationResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["artifact_id"] = str(result.artifact_id)
    return {"catalog_type": kind, **payload}
