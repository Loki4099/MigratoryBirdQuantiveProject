from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult
from style_rotation.model.contracts import (
    ExpandedModelSpecification,
    ModelCatalog,
    ModelMethodSeed,
    expand_model_specifications,
)

MODEL_KEY = "classic_market_model"
MODEL_FAMILY = "classic_market_composite"


@dataclass(frozen=True, slots=True)
class ModelCatalogPublication:
    release_artifact_id: uuid.UUID
    method_count: int
    definition_count: int
    definition_version_count: int
    specification_count: int
    dimension_count: int
    component_count: int
    reused_count: int
    artifact_ids: tuple[uuid.UUID, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["release_artifact_id"] = str(self.release_artifact_id)
        payload["artifact_ids"] = [str(item) for item in self.artifact_ids]
        return payload


def publish_model_catalog(engine: Engine, catalog_path: Path) -> ModelCatalogPublication:
    raw_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog = ModelCatalog.model_validate(raw_catalog)
    with engine.begin() as connection:
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        catalog_artifact_id = _catalog_artifact(connection, catalog.catalog_version, raw_catalog)
        signals = _signal_versions(connection, catalog_artifact_id)
        specifications = expand_model_specifications(catalog, tuple(signals))

        results: list[PublicationResult] = []
        methods: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
        for method in catalog.methods:
            published, method_version = _publish_method(service, connection, method)
            results.extend(published)
            methods[method.key] = method_version

        definition_results, definition_version_id, definition_version_artifact_id = (
            _publish_model_definition(service, connection)
        )
        results.extend(definition_results)
        specification_results: list[PublicationResult] = []
        for specification in specifications:
            specification_results.append(
                _publish_specification(
                    service,
                    connection,
                    specification,
                    definition_version_id,
                    definition_version_artifact_id,
                    methods,
                    signals,
                )
            )
        results.extend(specification_results)
        release = service.publish(
            artifact_type="model_catalog_materialization",
            artifact_key="model_catalog",
            version_number=semantic_version_number(catalog.catalog_version),
            semantic_payload={
                "catalog_version": catalog.catalog_version,
                "method_count": len(catalog.methods),
                "definition_count": 1,
                "definition_version_count": 1,
                "specification_count": len(specifications),
                "dimension_count": sum(len(item.dimensions) for item in specifications),
                "component_count": sum(
                    len(dimension.components)
                    for item in specifications
                    for dimension in item.dimensions
                ),
            },
            content_payload={"member_artifact_ids": [str(item.artifact_id) for item in results]},
            dependencies=(
                DependencyInput(catalog_artifact_id, "source_catalog", 0),
                *tuple(
                    DependencyInput(item.artifact_id, "materialized_member", ordinal)
                    for ordinal, item in enumerate(results)
                ),
            ),
            reason=f"materialize model catalog {catalog.catalog_version}",
        )
    return ModelCatalogPublication(
        release_artifact_id=release.artifact_id,
        method_count=len(catalog.methods),
        definition_count=1,
        definition_version_count=1,
        specification_count=len(specifications),
        dimension_count=sum(len(item.dimensions) for item in specifications),
        component_count=sum(
            len(dimension.components) for item in specifications for dimension in item.dimensions
        ),
        reused_count=sum(item.reused for item in results) + int(release.reused),
        artifact_ids=tuple(item.artifact_id for item in results),
    )


def _publish_method(
    service: ArtifactService, connection: Connection, seed: ModelMethodSeed
) -> tuple[list[PublicationResult], tuple[uuid.UUID, uuid.UUID]]:
    definition_payload = {"method_key": seed.key}
    definition = service.publish(
        artifact_type="model_method_definition",
        artifact_key=seed.key,
        version_number=1,
        semantic_payload=definition_payload,
        content_payload=definition_payload,
        reason=f"publish model method definition {seed.key}",
        draft_writer=lambda conn, artifact_id: _write_method_definition(
            conn, artifact_id, seed.key
        ),
    )
    definition_id = connection.execute(
        text(
            "SELECT model_method_definition_id FROM model.model_method_definition "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": definition.artifact_id},
    ).scalar_one()
    version_payload = {
        "supported_input_transforms": seed.input_transforms,
        "missing_policy": "require_complete_inputs",
        "neutral_policy": "preserve_explicit_neutral",
        "tie_policy": "neutral" if seed.key != "weighted_mean" else "not_applicable",
        "output_scaling": "none",
    }
    version = service.publish(
        artifact_type="model_method_version",
        artifact_key=seed.key,
        version_number=1,
        semantic_payload=version_payload,
        content_payload=version_payload,
        dependencies=(DependencyInput(definition.artifact_id, "method_definition", 0),),
        reason=f"publish model method version {seed.key} v1",
        draft_writer=lambda conn, artifact_id: _write_method_version(
            conn, artifact_id, definition_id, version_payload
        ),
    )
    version_id = connection.execute(
        text(
            "SELECT model_method_version_id FROM model.model_method_version "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": version.artifact_id},
    ).scalar_one()
    return [definition, version], (version_id, version.artifact_id)


def _publish_model_definition(
    service: ArtifactService, connection: Connection
) -> tuple[list[PublicationResult], uuid.UUID, uuid.UUID]:
    definition_payload = {
        "model_key": MODEL_KEY,
        "model_family": MODEL_FAMILY,
        "hypothesis": (
            "Economically directed market Signals may be combined transparently without "
            "embedding asset-selection or trading rules."
        ),
    }
    definition = service.publish(
        artifact_type="model_definition",
        artifact_key=MODEL_KEY,
        version_number=1,
        semantic_payload=definition_payload,
        content_payload=definition_payload,
        reason=f"publish model definition {MODEL_KEY}",
        draft_writer=lambda conn, artifact_id: _write_model_definition(
            conn, artifact_id, definition_payload
        ),
    )
    definition_id = connection.execute(
        text(
            "SELECT model_definition_id FROM model.model_definition "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": definition.artifact_id},
    ).scalar_one()
    version_payload = {
        "architecture": "two_level_dimension_signal",
        "missing_policy": "require_complete_inputs",
        "neutral_policy": "preserve_explicit_neutral",
    }
    version = service.publish(
        artifact_type="model_definition_version",
        artifact_key=MODEL_KEY,
        version_number=1,
        semantic_payload=version_payload,
        content_payload=version_payload,
        dependencies=(DependencyInput(definition.artifact_id, "model_definition", 0),),
        reason=f"publish model definition version {MODEL_KEY} v1",
        draft_writer=lambda conn, artifact_id: _write_model_definition_version(
            conn, artifact_id, definition_id, version_payload
        ),
    )
    version_id = connection.execute(
        text(
            "SELECT model_definition_version_id FROM model.model_definition_version "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": version.artifact_id},
    ).scalar_one()
    return [definition, version], version_id, version.artifact_id


def _publish_specification(
    service: ArtifactService,
    connection: Connection,
    specification: ExpandedModelSpecification,
    definition_version_id: uuid.UUID,
    definition_version_artifact_id: uuid.UUID,
    methods: dict[str, tuple[uuid.UUID, uuid.UUID]],
    signals: dict[str, tuple[uuid.UUID, uuid.UUID]],
) -> PublicationResult:
    payload = specification.model_dump(mode="json")
    signal_keys = [
        component.signal_key
        for dimension in specification.dimensions
        for component in dimension.components
    ]
    dependencies = (
        DependencyInput(definition_version_artifact_id, "model_definition_version", 0),
        DependencyInput(methods[specification.method][1], "overall_method_version", 1),
        *tuple(
            DependencyInput(signals[key][1], "signal_version", ordinal + 2)
            for ordinal, key in enumerate(signal_keys)
        ),
    )
    return service.publish(
        artifact_type="model_specification",
        artifact_key=specification.key,
        version_number=1,
        semantic_payload=payload,
        content_payload=payload,
        dependencies=dependencies,
        reason=f"publish model specification {specification.key}",
        draft_writer=lambda conn, artifact_id: _write_specification(
            conn,
            artifact_id,
            definition_version_id,
            specification,
            methods,
            signals,
        ),
    )


def _catalog_artifact(connection: Connection, catalog_version: str, raw_catalog: Any) -> uuid.UUID:
    row = (
        connection.execute(
            text(
                "SELECT artifact_id, content_hash FROM lineage.artifact "
                "WHERE artifact_type = 'research_catalog' AND artifact_key = 'model_catalog' "
                "AND version_number = :version AND status = 'published'"
            ),
            {"version": semantic_version_number(catalog_version)},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Publish the matching M0 model research catalog before materialization")
    dependency_rows = (
        connection.execute(
            text(
                "SELECT dependency.role, dependency.ordinal, upstream.semantic_fingerprint, "
                "upstream.content_hash FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact upstream ON upstream.artifact_id = "
                "dependency.depends_on_artifact_id WHERE dependency.artifact_id = :artifact_id "
                "ORDER BY dependency.ordinal NULLS FIRST, dependency.role"
            ),
            {"artifact_id": row["artifact_id"]},
        )
        .mappings()
        .all()
    )
    version_number = semantic_version_number(catalog_version)
    semantic_fingerprint = sha256_hexdigest(
        {
            "artifact_identity": {
                "artifact_type": "research_catalog",
                "artifact_key": "model_catalog",
                "version_number": version_number,
            },
            "semantic_payload": raw_catalog,
            "dependencies": [
                {
                    "role": item["role"],
                    "ordinal": item["ordinal"],
                    "semantic_fingerprint": item["semantic_fingerprint"],
                }
                for item in dependency_rows
            ],
        }
    )
    expected_content_hash = sha256_hexdigest(
        {
            "semantic_fingerprint": semantic_fingerprint,
            "content_payload": raw_catalog,
            "dependencies": [
                {
                    "role": item["role"],
                    "ordinal": item["ordinal"],
                    "content_hash": item["content_hash"],
                }
                for item in dependency_rows
            ],
        }
    )
    if row["content_hash"] != expected_content_hash:
        raise ValueError("Local model catalog does not match the published M0 artifact")
    artifact_id = row["artifact_id"]
    if not isinstance(artifact_id, uuid.UUID):
        raise RuntimeError("Model catalog artifact id must be a UUID")
    return artifact_id


def _signal_versions(
    connection: Connection, model_catalog_artifact_id: uuid.UUID
) -> dict[str, tuple[uuid.UUID, uuid.UUID]]:
    rows = (
        connection.execute(
            text(
                "SELECT definition.signal_key, version.signal_version_id, version.artifact_id "
                "FROM lineage.artifact_dependency model_dependency "
                "JOIN lineage.artifact signal_catalog ON signal_catalog.artifact_id = "
                "model_dependency.depends_on_artifact_id "
                "JOIN lineage.artifact_dependency release_source ON "
                "release_source.depends_on_artifact_id = signal_catalog.artifact_id "
                "AND release_source.role = 'source_catalog' "
                "JOIN lineage.artifact release ON release.artifact_id = release_source.artifact_id "
                "AND release.artifact_type = 'signal_catalog_materialization' "
                "AND release.status = 'published' "
                "JOIN lineage.artifact_dependency member ON "
                "member.artifact_id = release.artifact_id "
                "AND member.role = 'materialized_member' "
                "JOIN lineage.artifact signal_artifact ON signal_artifact.artifact_id = "
                "member.depends_on_artifact_id AND "
                "signal_artifact.artifact_type = 'signal_version' "
                "AND signal_artifact.status = 'published' "
                "JOIN signal.signal_version version ON "
                "version.artifact_id = signal_artifact.artifact_id "
                "JOIN signal.signal_definition definition ON definition.signal_definition_id = "
                "version.signal_definition_id "
                "WHERE model_dependency.artifact_id = :model_catalog "
                "AND model_dependency.role = 'signal_catalog' ORDER BY definition.signal_key"
            ),
            {"model_catalog": model_catalog_artifact_id},
        )
        .mappings()
        .all()
    )
    result = {row["signal_key"]: (row["signal_version_id"], row["artifact_id"]) for row in rows}
    if not result:
        raise ValueError("Materialize the exact upstream Signal catalog before the Model catalog")
    if len(result) != len(rows):
        raise ValueError("Exact upstream Signal catalog contains duplicate Signal keys")
    return result


def _write_method_definition(
    connection: Connection, artifact_id: uuid.UUID, method_key: str
) -> None:
    connection.execute(
        text(
            "INSERT INTO model.model_method_definition "
            "(model_method_definition_id, artifact_id, method_key) "
            "VALUES (:id, :artifact, :method_key)"
        ),
        {"id": uuid.uuid4(), "artifact": artifact_id, "method_key": method_key},
    )


def _write_method_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            "INSERT INTO model.model_method_version "
            "(model_method_version_id, model_method_definition_id, artifact_id, version_number, "
            "supported_input_transforms, missing_policy, neutral_policy, tie_policy, "
            "output_scaling) VALUES (:id, :definition, :artifact, 1, CAST(:transforms AS jsonb), "
            ":missing_policy, :neutral_policy, :tie_policy, :output_scaling)"
        ),
        {
            "id": uuid.uuid4(),
            "definition": definition_id,
            "artifact": artifact_id,
            "transforms": json.dumps(payload["supported_input_transforms"]),
            **{
                key: payload[key]
                for key in ("missing_policy", "neutral_policy", "tie_policy", "output_scaling")
            },
        },
    )


def _write_model_definition(
    connection: Connection, artifact_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    connection.execute(
        text(
            "INSERT INTO model.model_definition "
            "(model_definition_id, artifact_id, model_key, model_family, hypothesis) "
            "VALUES (:id, :artifact, :model_key, :model_family, :hypothesis)"
        ),
        {"id": uuid.uuid4(), "artifact": artifact_id, **payload},
    )


def _write_model_definition_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            "INSERT INTO model.model_definition_version "
            "(model_definition_version_id, model_definition_id, artifact_id, version_number, "
            "architecture, missing_policy, neutral_policy) VALUES "
            "(:id, :definition, :artifact, 1, :architecture, :missing_policy, :neutral_policy)"
        ),
        {"id": uuid.uuid4(), "definition": definition_id, "artifact": artifact_id, **payload},
    )


def _write_specification(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_version_id: uuid.UUID,
    specification: ExpandedModelSpecification,
    methods: dict[str, tuple[uuid.UUID, uuid.UUID]],
    signals: dict[str, tuple[uuid.UUID, uuid.UUID]],
) -> None:
    specification_id = uuid.uuid4()
    component_count = sum(len(item.components) for item in specification.dimensions)
    connection.execute(
        text(
            "INSERT INTO model.model_specification "
            "(model_specification_id, model_definition_version_id, overall_method_version_id, "
            "artifact_id, specification_key, specification_type, tie_output, output_type, "
            "active_dimension_count, component_count, research_tier) VALUES "
            "(:id, :definition_version, :method_version, :artifact, :key, :type, :tie_output, "
            ":output_type, :dimension_count, :component_count, 'canonical')"
        ),
        {
            "id": specification_id,
            "definition_version": definition_version_id,
            "method_version": methods[specification.method][0],
            "artifact": artifact_id,
            "key": specification.key,
            "type": specification.specification_type,
            "tie_output": specification.tie_output,
            "output_type": specification.output_type,
            "dimension_count": len(specification.dimensions),
            "component_count": component_count,
        },
    )
    for dimension_ordinal, dimension in enumerate(specification.dimensions):
        dimension_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO model.model_dimension "
                "(model_dimension_id, model_specification_id, method_version_id, dimension_key, "
                "ordinal, input_transform, weight) VALUES "
                "(:id, :specification, :method, :key, :ordinal, :transform, :weight)"
            ),
            {
                "id": dimension_id,
                "specification": specification_id,
                "method": methods[dimension.method][0],
                "key": dimension.key,
                "ordinal": dimension_ordinal,
                "transform": dimension.input_transform,
                "weight": Decimal(str(dimension.weight)),
            },
        )
        for component_ordinal, component in enumerate(dimension.components):
            connection.execute(
                text(
                    "INSERT INTO model.model_component "
                    "(model_component_id, model_specification_id, model_dimension_id, "
                    "signal_version_id, ordinal, input_transform, weight) VALUES "
                    "(:id, :specification, :dimension, :signal, :ordinal, :transform, :weight)"
                ),
                {
                    "id": uuid.uuid4(),
                    "specification": specification_id,
                    "dimension": dimension_id,
                    "signal": signals[component.signal_key][0],
                    "ordinal": component_ordinal,
                    "transform": component.input_transform,
                    "weight": Decimal(str(component.weight)),
                },
            )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
