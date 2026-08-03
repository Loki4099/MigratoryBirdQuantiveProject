from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.factor.contracts import FactorCatalog, FactorDefinitionSeed, FactorVariantSeed
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult


@dataclass(frozen=True, slots=True)
class FactorCatalogPublication:
    release_artifact_id: uuid.UUID
    definition_count: int
    definition_version_count: int
    variant_count: int
    reused_count: int
    artifact_ids: tuple[uuid.UUID, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["release_artifact_id"] = str(self.release_artifact_id)
        payload["artifact_ids"] = [str(item) for item in self.artifact_ids]
        return payload


def publish_factor_catalog(engine: Engine, catalog_path: Path) -> FactorCatalogPublication:
    catalog_text = catalog_path.read_text(encoding="utf-8")
    raw_catalog = json.loads(catalog_text)
    catalog = FactorCatalog.model_validate(raw_catalog)
    with engine.begin() as connection:
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        catalog_artifact_id = _catalog_artifact(connection, catalog.catalog_version, raw_catalog)
        results: list[PublicationResult] = []
        for definition in catalog.definitions:
            results.extend(_publish_definition(service, connection, definition))
        release = service.publish(
            artifact_type="factor_catalog_materialization",
            artifact_key="factor_catalog",
            version_number=semantic_version_number(catalog.catalog_version),
            semantic_payload={
                "catalog_version": catalog.catalog_version,
                "definition_count": len(catalog.definitions),
                "variant_count": sum(len(item.variants) for item in catalog.definitions),
            },
            content_payload={"member_artifact_ids": [str(item.artifact_id) for item in results]},
            dependencies=(
                DependencyInput(catalog_artifact_id, "source_catalog", 0),
                *tuple(
                    DependencyInput(item.artifact_id, "materialized_member", ordinal)
                    for ordinal, item in enumerate(results)
                ),
            ),
            reason=f"materialize factor catalog {catalog.catalog_version}",
        )
    return FactorCatalogPublication(
        release_artifact_id=release.artifact_id,
        definition_count=len(catalog.definitions),
        definition_version_count=len(catalog.definitions),
        variant_count=sum(len(item.variants) for item in catalog.definitions),
        reused_count=sum(item.reused for item in results) + int(release.reused),
        artifact_ids=tuple(item.artifact_id for item in results),
    )


def _publish_definition(
    service: ArtifactService,
    connection: Connection,
    seed: FactorDefinitionSeed,
) -> list[PublicationResult]:
    definition_payload = {"factor_key": seed.key, "measurement_family": seed.family}
    definition = service.publish(
        artifact_type="factor_definition",
        artifact_key=seed.key,
        version_number=1,
        semantic_payload=definition_payload,
        content_payload=definition_payload,
        reason=f"publish factor definition {seed.key}",
        draft_writer=lambda conn, artifact_id: _write_definition(conn, artifact_id, seed),
    )
    definition_id = connection.execute(
        text(
            "SELECT factor_definition_id FROM factor.factor_definition "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": definition.artifact_id},
    ).scalar_one()
    version_payload = seed.model_dump(mode="json", exclude={"variants", "family", "key"})
    version = service.publish(
        artifact_type="factor_definition_version",
        artifact_key=seed.key,
        version_number=seed.definition_version,
        semantic_payload=version_payload,
        content_payload=version_payload,
        dependencies=(DependencyInput(definition.artifact_id, "factor_definition", 0),),
        reason=f"publish factor definition version {seed.key} v{seed.definition_version}",
        draft_writer=lambda conn, artifact_id: _write_definition_version(
            conn, artifact_id, definition_id, seed
        ),
    )
    definition_version_id = connection.execute(
        text(
            "SELECT factor_definition_version_id FROM factor.factor_definition_version "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": version.artifact_id},
    ).scalar_one()
    results = [definition, version]
    for variant_seed in seed.variants:
        parameter_hash = sha256_hexdigest(variant_seed.parameters)
        payload = {
            **variant_seed.model_dump(mode="json"),
            "parameter_hash": parameter_hash,
        }
        variant = service.publish(
            artifact_type="factor_variant",
            artifact_key=variant_seed.key,
            version_number=seed.definition_version,
            semantic_payload=payload,
            content_payload=payload,
            dependencies=(DependencyInput(version.artifact_id, "factor_definition_version", 0),),
            reason=f"publish factor variant {variant_seed.key}",
            draft_writer=partial(
                _write_variant,
                definition_version_id=definition_version_id,
                seed=variant_seed,
                parameter_hash=parameter_hash,
            ),
        )
        results.append(variant)
    return results


def _catalog_artifact(connection: Connection, catalog_version: str, raw_catalog: Any) -> uuid.UUID:
    row = (
        connection.execute(
            text(
                "SELECT artifact_id, content_hash FROM lineage.artifact "
                "WHERE artifact_type = 'research_catalog' AND artifact_key = 'factor_catalog' "
                "AND version_number = :version AND status = 'published'"
            ),
            {"version": semantic_version_number(catalog_version)},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Publish the matching M0 factor research catalog before materialization")
    version_number = semantic_version_number(catalog_version)
    semantic_fingerprint = sha256_hexdigest(
        {
            "artifact_identity": {
                "artifact_type": "research_catalog",
                "artifact_key": "factor_catalog",
                "version_number": version_number,
            },
            "semantic_payload": raw_catalog,
            "dependencies": [],
        }
    )
    expected_content_hash = sha256_hexdigest(
        {
            "semantic_fingerprint": semantic_fingerprint,
            "content_payload": raw_catalog,
            "dependencies": [],
        }
    )
    if row["content_hash"] != expected_content_hash:
        raise ValueError("Local factor catalog does not match the published M0 artifact")
    artifact_id = row["artifact_id"]
    if not isinstance(artifact_id, uuid.UUID):
        raise RuntimeError("Factor catalog artifact id must be a UUID")
    return artifact_id


def _write_definition(
    connection: Connection, artifact_id: uuid.UUID, seed: FactorDefinitionSeed
) -> None:
    connection.execute(
        text(
            "INSERT INTO factor.factor_definition "
            "(factor_definition_id, artifact_id, factor_key, measurement_family) "
            "VALUES (:id, :artifact, :key, :family)"
        ),
        {"id": uuid.uuid4(), "artifact": artifact_id, "key": seed.key, "family": seed.family},
    )


def _write_definition_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    seed: FactorDefinitionSeed,
) -> None:
    connection.execute(
        text(
            "INSERT INTO factor.factor_definition_version "
            "(factor_definition_version_id, factor_definition_id, artifact_id, version_number, "
            "formula, inputs, output_unit, time_semantics, implementation_key) VALUES "
            "(:id, :definition, :artifact, :version, :formula, CAST(:inputs AS jsonb), "
            ":unit, :time_semantics, :implementation)"
        ),
        {
            "id": uuid.uuid4(),
            "definition": definition_id,
            "artifact": artifact_id,
            "version": seed.definition_version,
            "formula": seed.formula,
            "inputs": json.dumps(seed.inputs),
            "unit": seed.output_unit,
            "time_semantics": seed.time_semantics,
            "implementation": seed.implementation_key,
        },
    )


def _write_variant(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    definition_version_id: uuid.UUID,
    seed: FactorVariantSeed,
    parameter_hash: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO factor.factor_variant "
            "(factor_variant_id, factor_definition_version_id, artifact_id, variant_key, "
            "parameters, parameter_hash, required_price_observations, preset_type) VALUES "
            "(:id, :definition_version, :artifact, :key, CAST(:parameters AS jsonb), "
            ":parameter_hash, :required, :preset)"
        ),
        {
            "id": uuid.uuid4(),
            "definition_version": definition_version_id,
            "artifact": artifact_id,
            "key": seed.key,
            "parameters": json.dumps(seed.parameters, sort_keys=True),
            "parameter_hash": parameter_hash,
            "required": seed.required_price_observations,
            "preset": seed.preset_type,
        },
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
