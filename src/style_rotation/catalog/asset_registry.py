from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.catalog.v021_assets import (
    AssetRegistryCatalog,
    SecuritySeed,
    load_asset_registry,
    searchable_document,
)
from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.lineage.service import ArtifactService, DependencyInput


def publish_asset_registry(engine: Engine, catalog_path: Path) -> dict[str, Any]:
    """Publish a validated registry as one immutable lineage artifact."""
    catalog = load_asset_registry(catalog_path)
    with engine.begin() as transaction:
        service = ArtifactService(cast(Engine, _BoundConnection(transaction)))
        payload = canonical_registry_payload(catalog)
        result = service.publish(
            artifact_type="asset_registry_release",
            artifact_key="v021_asset_registry",
            version_number=semantic_version_number(catalog.catalog_version),
            semantic_payload=payload,
            content_payload=payload,
            reason=f"publish asset registry {catalog.catalog_version}",
            draft_writer=lambda connection, artifact_id: _write_registry(
                connection, artifact_id, catalog
            ),
        )
    output = asdict(result)
    output["artifact_id"] = str(result.artifact_id)
    return {"catalog_type": "asset_registry", **output}


def publish_asset_identities(engine: Engine, catalog_path: Path) -> dict[str, Any]:
    """Bridge tradable v0.21 Securities into the canonical daily-bar Asset identity."""
    catalog = load_asset_registry(catalog_path)
    eligible = tuple(
        item
        for item in catalog.securities
        if item.tradability == "tradable" and item.calendar_key == "XNYS"
    )
    semantic = {
        "catalog_version": catalog.catalog_version,
        "as_of_date": catalog.as_of_date,
        "identity_rule": "tradable_xnys_security_to_canonical_asset_v1",
        "securities": [
            {
                "key": item.key,
                "name": item.name,
                "symbol": item.symbol,
                "instrument_type": item.instrument_type,
                "venue_mic": item.venue_mic,
                "currency": item.currency,
            }
            for item in eligible
        ],
    }
    with engine.begin() as transaction:
        registry_artifact_id = transaction.execute(
            text(
                "SELECT artifact_id FROM catalog.asset_registry_release "
                "WHERE catalog_version = :version ORDER BY version_number DESC LIMIT 1"
            ),
            {"version": catalog.catalog_version},
        ).scalar_one_or_none()
        if registry_artifact_id is None:
            raise ValueError(
                f"Publish Asset Registry {catalog.catalog_version} before its identities"
            )
        service = ArtifactService(cast(Engine, _BoundConnection(transaction)))
        result = service.publish(
            artifact_type="catalog_master_data_release",
            artifact_key="v021_asset_identity",
            version_number=semantic_version_number(catalog.catalog_version),
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=(DependencyInput(registry_artifact_id, "asset_registry_release", 0),),
            reason=f"publish canonical asset identities {catalog.catalog_version}",
            draft_writer=lambda connection, artifact_id: _write_asset_identities(
                connection, artifact_id, catalog, eligible
            ),
        )
    output = asdict(result)
    output["artifact_id"] = str(result.artifact_id)
    output["security_count"] = len(eligible)
    return {"catalog_type": "asset_identity", **output}


def canonical_registry_payload(catalog: AssetRegistryCatalog) -> dict[str, Any]:
    """Return deterministic content even when code-side fields use sets."""
    payload = catalog.model_dump(mode="json")
    securities = cast(list[dict[str, Any]], payload["securities"])
    for security in securities:
        security["tags"] = sorted(cast(list[str], security["tags"]))
    return payload


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)


def _write_registry(
    connection: Connection, artifact_id: uuid.UUID, catalog: AssetRegistryCatalog
) -> None:
    release_id = uuid.uuid4()
    _insert(
        connection,
        "asset_registry_release",
        {
            "asset_registry_release_id": release_id,
            "artifact_id": artifact_id,
            "release_key": "v021_asset_registry",
            "version_number": semantic_version_number(catalog.catalog_version),
            "catalog_version": catalog.catalog_version,
            "as_of_date": catalog.as_of_date,
        },
    )
    category_ids: dict[str, uuid.UUID] = {}
    for ordinal, category in enumerate(catalog.categories):
        category_id = uuid.uuid4()
        category_ids[category.key] = category_id
        _insert(
            connection,
            "asset_category",
            {
                "asset_category_id": category_id,
                "asset_registry_release_id": release_id,
                "category_key": category.key,
                "name": category.name,
                "description": category.description,
                "ordinal": ordinal,
            },
        )

    legacy_assets = _legacy_asset_ids(connection)
    security_ids: dict[str, uuid.UUID] = {}
    for ordinal, security in enumerate(catalog.securities):
        security_id = _security_id(connection, security, legacy_assets.get(security.key))
        security_ids[security.key] = security_id
        _insert(
            connection,
            "security_profile",
            {
                "security_profile_id": uuid.uuid4(),
                "asset_registry_release_id": release_id,
                "security_id": security_id,
                "asset_category_id": category_ids[security.category],
                "symbol": security.symbol,
                "aliases": list(security.aliases),
                "asset_class": security.asset_class,
                "instrument_type": security.instrument_type,
                "tradability": security.tradability,
                "venue_mic": security.venue_mic,
                "calendar_key": security.calendar_key,
                "tags": sorted(security.tags),
                "maturity": security.maturity,
                "target_maturity": security.target_maturity,
                "missing_requirements": list(security.missing_requirements),
                "search_document": searchable_document(security),
                "ordinal": ordinal,
            },
        )

    for asset_set in catalog.asset_sets:
        asset_set_id = uuid.uuid4()
        _insert(
            connection,
            "asset_set_definition",
            {
                "asset_set_definition_id": asset_set_id,
                "asset_registry_release_id": release_id,
                "set_key": asset_set.key,
                "name": asset_set.name,
                "set_type": asset_set.set_type,
                "maturity": asset_set.maturity,
                "formal_eligible": asset_set.formal_eligible,
                "notes": asset_set.notes,
            },
        )
        for ordinal, security_key in enumerate(asset_set.member_keys):
            _insert(
                connection,
                "asset_set_member",
                {
                    "asset_set_definition_id": asset_set_id,
                    "security_id": security_ids[security_key],
                    "ordinal": ordinal,
                },
            )


def _write_asset_identities(
    connection: Connection,
    artifact_id: uuid.UUID,
    catalog: AssetRegistryCatalog,
    securities: tuple[SecuritySeed, ...],
) -> None:
    release_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO catalog.master_data_release "
            "(master_data_release_id, artifact_id, release_key, version_number, as_of_date) "
            "VALUES (:id, :artifact, 'v021_asset_identity', :version, :as_of)"
        ),
        {
            "id": release_id,
            "artifact": artifact_id,
            "version": semantic_version_number(catalog.catalog_version),
            "as_of": catalog.as_of_date,
        },
    )
    calendar_id = connection.execute(
        text(
            "SELECT definition.calendar_definition_id "
            "FROM catalog.calendar_definition definition "
            "JOIN catalog.master_data_release release ON "
            "release.master_data_release_id = definition.master_data_release_id "
            "JOIN lineage.artifact artifact ON artifact.artifact_id = release.artifact_id "
            "WHERE definition.calendar_key = 'XNYS' AND artifact.status = 'published' "
            "ORDER BY release.version_number DESC LIMIT 1"
        )
    ).scalar_one()
    existing_assets = _legacy_asset_ids(connection)
    for security in securities:
        asset_id = existing_assets.get(security.key)
        if asset_id is None:
            asset_id = uuid.uuid4()
            _insert(
                connection,
                "asset",
                {
                    "asset_id": asset_id,
                    "master_data_release_id": release_id,
                    "asset_key": security.key,
                    "name": security.name,
                    "asset_type": _canonical_asset_type(security.instrument_type),
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
                    "identifier_value": security.key,
                },
            )
            listing_id = uuid.uuid4()
            _insert(
                connection,
                "asset_listing",
                {
                    "asset_listing_id": listing_id,
                    "master_data_release_id": release_id,
                    "asset_id": asset_id,
                    "calendar_definition_id": calendar_id,
                    "listing_key": f"{security.key}_{(security.venue_mic or 'XNAS').lower()}",
                    "venue_mic": security.venue_mic or "XNAS",
                    "currency": security.currency,
                    "timezone": "America/New_York",
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
                    "symbol": security.symbol,
                },
            )
        connection.execute(
            text(
                "UPDATE catalog.security SET legacy_asset_id = :asset_id "
                "WHERE security_key = :key AND "
                "(legacy_asset_id IS NULL OR legacy_asset_id = :asset_id)"
            ),
            {"asset_id": asset_id, "key": security.key},
        )


def _canonical_asset_type(instrument_type: str) -> str:
    lowered = instrument_type.lower()
    if "stock" in lowered or instrument_type == "ADR":
        return "equity"
    if "etf" in lowered or "etp" in lowered:
        return "etf"
    return "listed_security"


def _legacy_asset_ids(connection: Connection) -> dict[str, uuid.UUID]:
    rows = connection.execute(
        text("""
        SELECT DISTINCT ON (asset.asset_key) asset.asset_key, asset.asset_id
        FROM catalog.asset asset
        JOIN catalog.master_data_release release
          ON release.master_data_release_id = asset.master_data_release_id
        JOIN lineage.artifact artifact ON artifact.artifact_id = release.artifact_id
        WHERE artifact.status = 'published'
        ORDER BY asset.asset_key, release.version_number DESC
    """)
    ).mappings()
    return {str(row["asset_key"]): row["asset_id"] for row in rows}


def _security_id(
    connection: Connection, seed: SecuritySeed, legacy_asset_id: uuid.UUID | None
) -> uuid.UUID:
    existing = connection.execute(
        text("SELECT security_id FROM catalog.security WHERE security_key = :key"),
        {"key": seed.key},
    ).scalar_one_or_none()
    if existing is not None:
        return cast(uuid.UUID, existing)
    security_id = uuid.uuid4()
    connection.execute(
        text("""
        INSERT INTO catalog.security (
            security_id, legacy_asset_id, security_key, name,
            instrument_type, currency, status
        ) VALUES (
            :id, :legacy_asset_id, :key, :name,
            :instrument_type, :currency, :status
        )
    """),
        {
            "id": security_id,
            "legacy_asset_id": legacy_asset_id,
            "key": seed.key,
            "name": seed.name,
            "instrument_type": seed.instrument_type,
            "currency": seed.currency,
            "status": "reference" if seed.tradability == "reference_only" else "active",
        },
    )
    identifiers = [
        ("internal_key", seed.key),
        ("symbol", seed.symbol),
        *(("alias", alias) for alias in seed.aliases),
    ]
    for identifier_type, identifier_value in identifiers:
        connection.execute(
            text("""
            INSERT INTO catalog.security_identifier (
                security_identifier_id, security_id, provider_scope,
                identifier_type, identifier_value
            ) VALUES (:id, :security_id, 'catalog', :type, :value)
        """),
            {
                "id": uuid.uuid4(),
                "security_id": security_id,
                "type": identifier_type,
                "value": identifier_value,
            },
        )
    return security_id


def _insert(connection: Connection, table_name: str, values: dict[str, Any]) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join(
        f"CAST(:{column} AS jsonb)" if isinstance(value, list) else f":{column}"
        for column, value in values.items()
    )
    parameters = {
        column: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
        for column, value in values.items()
    }
    connection.execute(
        text(f"INSERT INTO catalog.{table_name} ({columns}) VALUES ({placeholders})"),
        parameters,
    )
