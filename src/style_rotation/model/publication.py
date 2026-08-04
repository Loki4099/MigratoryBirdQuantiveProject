from __future__ import annotations

import uuid
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from decimal import Decimal
from functools import partial
from typing import Any, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.model.calculator import (
    ModelCalculation,
    ModelComponentInput,
    ModelDimensionInput,
    ModelSpecificationInput,
    SignalScoreInput,
    calculate_model,
)


@dataclass(frozen=True, slots=True)
class ModelDatasetPublication:
    specification_key: str
    specification_type: str
    artifact_id: uuid.UUID
    input_count: int
    row_count: int
    coverage_start: str
    coverage_end: str
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifact_id"] = str(self.artifact_id)
        return payload


@dataclass(frozen=True, slots=True)
class _SignalDatasetInput:
    signal_dataset_id: uuid.UUID
    artifact_id: uuid.UUID
    signal_version_id: uuid.UUID
    points: tuple[SignalScoreInput, ...]


@dataclass(frozen=True, slots=True)
class _PublicationContext:
    specifications: tuple[ModelSpecificationInput, ...]
    specification_types: dict[uuid.UUID, str]
    signal_datasets: dict[uuid.UUID, _SignalDatasetInput]
    universe_version_id: uuid.UUID
    universe_artifact_id: uuid.UUID
    bundle_version_id: uuid.UUID
    bundle_artifact_id: uuid.UUID
    eligibility_snapshot_id: uuid.UUID
    eligibility_artifact_id: uuid.UUID
    signal_engine_artifact_id: uuid.UUID
    model_engine_version_id: uuid.UUID
    model_engine_artifact_id: uuid.UUID


class ModelDatasetPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        model_catalog_artifact_id: uuid.UUID,
        signal_catalog_artifact_id: uuid.UUID,
        bundle_artifact_id: uuid.UUID,
        eligibility_artifact_id: uuid.UUID,
        signal_engine_artifact_id: uuid.UUID,
        model_engine_artifact_id: uuid.UUID,
    ) -> tuple[ModelDatasetPublication, ...]:
        context = self._load_context(
            model_catalog_artifact_id,
            signal_catalog_artifact_id,
            bundle_artifact_id,
            eligibility_artifact_id,
            signal_engine_artifact_id,
            model_engine_artifact_id,
        )
        calculations = tuple(
            calculate_model(
                specification,
                {
                    component.signal_version_id: context.signal_datasets[
                        component.signal_version_id
                    ].points
                    for dimension in specification.dimensions
                    for component in dimension.components
                },
            )
            for specification in context.specifications
        )
        outcomes: list[ModelDatasetPublication] = []
        with self._engine.begin() as connection:
            artifacts = ArtifactService(cast(Engine, _BoundConnection(connection)))
            for calculation in calculations:
                inputs = _calculation_inputs(context, calculation)
                input_set_hash = sha256_hexdigest(
                    [
                        {
                            "component_id": str(component.model_component_id),
                            "signal_dataset_artifact_id": str(dataset.artifact_id),
                        }
                        for component, dataset in inputs
                    ]
                )
                semantic = _dataset_semantic(context, calculation, input_set_hash)
                context_key = sha256_hexdigest(semantic)[:16]
                result = artifacts.publish(
                    artifact_type="model_dataset",
                    artifact_key=f"{calculation.specification.specification_key}:{context_key}",
                    version_number=1,
                    semantic_payload=semantic,
                    content_payload={
                        **semantic,
                        "points": [asdict(point) for point in calculation.points],
                    },
                    dependencies=(
                        DependencyInput(
                            calculation.specification.artifact_id, "model_specification", 0
                        ),
                        *tuple(
                            DependencyInput(dataset.artifact_id, "signal_dataset", ordinal + 1)
                            for ordinal, (_component, dataset) in enumerate(inputs)
                        ),
                        DependencyInput(
                            context.universe_artifact_id, "universe_version", len(inputs) + 1
                        ),
                        DependencyInput(context.bundle_artifact_id, "data_bundle", len(inputs) + 2),
                        DependencyInput(
                            context.eligibility_artifact_id, "eligibility", len(inputs) + 3
                        ),
                        DependencyInput(
                            context.model_engine_artifact_id, "engine_version", len(inputs) + 4
                        ),
                    ),
                    reason=(f"publish model dataset {calculation.specification.specification_key}"),
                    draft_writer=partial(
                        _write_dataset,
                        context=context,
                        calculation=calculation,
                        inputs=inputs,
                        input_set_hash=input_set_hash,
                    ),
                )
                outcomes.append(
                    ModelDatasetPublication(
                        calculation.specification.specification_key,
                        context.specification_types[
                            calculation.specification.model_specification_id
                        ],
                        result.artifact_id,
                        len(inputs),
                        len(calculation.points),
                        calculation.coverage_start.isoformat(),
                        calculation.coverage_end.isoformat(),
                        result.reused,
                    )
                )
        return tuple(outcomes)

    def _load_context(
        self,
        model_catalog_artifact_id: uuid.UUID,
        signal_catalog_artifact_id: uuid.UUID,
        bundle_artifact_id: uuid.UUID,
        eligibility_artifact_id: uuid.UUID,
        signal_engine_artifact_id: uuid.UUID,
        model_engine_artifact_id: uuid.UUID,
    ) -> _PublicationContext:
        with self._engine.connect() as connection:
            _published_artifact(
                connection, model_catalog_artifact_id, "model_catalog_materialization"
            )
            _published_artifact(
                connection, signal_catalog_artifact_id, "signal_catalog_materialization"
            )
            specifications, specification_types = _catalog_specifications(
                connection, model_catalog_artifact_id
            )
            bundle = _published_business(
                connection,
                "data.data_bundle_version",
                "data_bundle_version_id",
                bundle_artifact_id,
            )
            eligibility = _published_business(
                connection,
                "catalog.eligibility_snapshot",
                "eligibility_snapshot_id",
                eligibility_artifact_id,
            )
            signal_engine = _published_business(
                connection,
                "ops.engine_version",
                "engine_version_id",
                signal_engine_artifact_id,
            )
            model_engine = _published_business(
                connection,
                "ops.engine_version",
                "engine_version_id",
                model_engine_artifact_id,
            )
            if _engine_key(connection, signal_engine["engine_version_id"]) != "signal_engine":
                raise ValueError("Supplied Signal engine artifact is not a Signal engine")
            if _engine_key(connection, model_engine["engine_version_id"]) != "model_engine":
                raise ValueError("Supplied Model engine artifact is not a Model engine")
            if eligibility["data_bundle_version_id"] != bundle["data_bundle_version_id"]:
                raise ValueError("Eligibility snapshot does not bind the supplied data bundle")
            if eligibility["eligible_count"] != eligibility["member_count"]:
                raise ValueError("Formal Model publication requires all universe members eligible")
            universe = (
                connection.execute(
                    text(
                        "SELECT universe_version_id, artifact_id FROM catalog.universe_version "
                        "WHERE universe_version_id = :id"
                    ),
                    {"id": eligibility["universe_version_id"]},
                )
                .mappings()
                .one()
            )
            required_versions = {
                component.signal_version_id
                for specification in specifications
                for dimension in specification.dimensions
                for component in dimension.components
            }
            signal_datasets = _signal_datasets(
                connection,
                signal_catalog_artifact_id,
                required_versions,
                universe["universe_version_id"],
                bundle["data_bundle_version_id"],
                eligibility["eligibility_snapshot_id"],
                signal_engine["engine_version_id"],
            )
        missing = required_versions.difference(signal_datasets)
        if missing:
            raise ValueError(f"Missing exact published Signal datasets for {len(missing)} inputs")
        return _PublicationContext(
            specifications,
            specification_types,
            signal_datasets,
            universe["universe_version_id"],
            universe["artifact_id"],
            bundle["data_bundle_version_id"],
            bundle_artifact_id,
            eligibility["eligibility_snapshot_id"],
            eligibility_artifact_id,
            signal_engine_artifact_id,
            model_engine["engine_version_id"],
            model_engine_artifact_id,
        )


def _catalog_specifications(
    connection: Connection, model_catalog_artifact_id: uuid.UUID
) -> tuple[tuple[ModelSpecificationInput, ...], dict[uuid.UUID, str]]:
    specification_rows = (
        connection.execute(
            text(
                "SELECT specification.model_specification_id, specification.artifact_id, "
                "specification.specification_key, specification.specification_type, "
                "specification.tie_output, specification.output_type, method.method_key "
                "FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact member ON member.artifact_id = "
                "dependency.depends_on_artifact_id JOIN model.model_specification specification "
                "ON specification.artifact_id = member.artifact_id "
                "JOIN model.model_method_version method_version ON "
                "method_version.model_method_version_id = "
                "specification.overall_method_version_id "
                "JOIN model.model_method_definition method ON "
                "method.model_method_definition_id = method_version.model_method_definition_id "
                "WHERE dependency.artifact_id = :catalog AND dependency.role = "
                "'materialized_member' AND member.artifact_type = 'model_specification' "
                "AND member.status = 'published' ORDER BY specification.specification_key"
            ),
            {"catalog": model_catalog_artifact_id},
        )
        .mappings()
        .all()
    )
    if not specification_rows:
        raise ValueError("Model catalog materialization contains no published specifications")
    specification_ids = tuple(row["model_specification_id"] for row in specification_rows)
    dimension_rows = (
        connection.execute(
            text(
                "SELECT dimension.model_dimension_id, dimension.model_specification_id, "
                "dimension.dimension_key, dimension.input_transform, dimension.weight, "
                "method.method_key FROM model.model_dimension dimension "
                "JOIN model.model_method_version method_version ON "
                "method_version.model_method_version_id = dimension.method_version_id "
                "JOIN model.model_method_definition method ON "
                "method.model_method_definition_id = method_version.model_method_definition_id "
                "WHERE dimension.model_specification_id IN :ids "
                "ORDER BY dimension.model_specification_id, dimension.ordinal"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": specification_ids},
        )
        .mappings()
        .all()
    )
    component_rows = (
        connection.execute(
            text(
                "SELECT component.model_component_id, component.model_specification_id, "
                "component.model_dimension_id, component.signal_version_id, "
                "definition.signal_key, component.input_transform, component.weight "
                "FROM model.model_component component JOIN signal.signal_version version ON "
                "version.signal_version_id = component.signal_version_id "
                "JOIN signal.signal_definition definition ON "
                "definition.signal_definition_id = version.signal_definition_id "
                "WHERE component.model_specification_id IN :ids "
                "ORDER BY component.model_specification_id, component.model_dimension_id, "
                "component.ordinal"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": specification_ids},
        )
        .mappings()
        .all()
    )
    components_by_dimension: dict[uuid.UUID, list[ModelComponentInput]] = defaultdict(list)
    for row in component_rows:
        components_by_dimension[row["model_dimension_id"]].append(
            ModelComponentInput(
                row["model_component_id"],
                row["signal_version_id"],
                row["signal_key"],
                row["input_transform"],
                Decimal(row["weight"]),
            )
        )
    dimensions_by_specification: dict[uuid.UUID, list[ModelDimensionInput]] = defaultdict(list)
    for row in dimension_rows:
        components = tuple(components_by_dimension[row["model_dimension_id"]])
        if not components:
            raise ValueError("Published Model dimension contains no components")
        dimensions_by_specification[row["model_specification_id"]].append(
            ModelDimensionInput(
                row["model_dimension_id"],
                row["dimension_key"],
                row["method_key"],
                row["input_transform"],
                Decimal(row["weight"]),
                components,
            )
        )
    specifications = tuple(
        ModelSpecificationInput(
            row["model_specification_id"],
            row["artifact_id"],
            row["specification_key"],
            row["method_key"],
            row["tie_output"],
            row["output_type"],
            tuple(dimensions_by_specification[row["model_specification_id"]]),
        )
        for row in specification_rows
    )
    if any(not item.dimensions for item in specifications):
        raise ValueError("Published Model specification contains no dimensions")
    return specifications, {
        row["model_specification_id"]: row["specification_type"] for row in specification_rows
    }


def _signal_datasets(
    connection: Connection,
    signal_catalog_artifact_id: uuid.UUID,
    signal_version_ids: set[uuid.UUID],
    universe_version_id: uuid.UUID,
    bundle_version_id: uuid.UUID,
    eligibility_snapshot_id: uuid.UUID,
    signal_engine_version_id: uuid.UUID,
) -> dict[uuid.UUID, _SignalDatasetInput]:
    rows = (
        connection.execute(
            text(
                "SELECT dataset.signal_dataset_id, dataset.artifact_id, "
                "dataset.signal_version_id FROM signal.signal_dataset dataset "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = dataset.artifact_id "
                "JOIN signal.signal_version version ON version.signal_version_id = "
                "dataset.signal_version_id JOIN lineage.artifact_dependency membership ON "
                "membership.depends_on_artifact_id = version.artifact_id "
                "WHERE membership.artifact_id = :catalog AND membership.role = "
                "'materialized_member' AND dataset.signal_version_id IN :versions "
                "AND dataset.universe_version_id = :universe "
                "AND dataset.data_bundle_version_id = :bundle "
                "AND dataset.eligibility_snapshot_id = :eligibility "
                "AND dataset.engine_version_id = :engine AND artifact.status = 'published' "
                "ORDER BY dataset.signal_version_id"
            ).bindparams(bindparam("versions", expanding=True)),
            {
                "catalog": signal_catalog_artifact_id,
                "versions": tuple(signal_version_ids),
                "universe": universe_version_id,
                "bundle": bundle_version_id,
                "eligibility": eligibility_snapshot_id,
                "engine": signal_engine_version_id,
            },
        )
        .mappings()
        .all()
    )
    versions = [row["signal_version_id"] for row in rows]
    if len(versions) != len(set(versions)):
        raise ValueError("Multiple Signal datasets match one formal Model input")
    if not rows:
        return {}
    dataset_ids = tuple(row["signal_dataset_id"] for row in rows)
    value_rows = (
        connection.execute(
            text(
                "SELECT value.signal_dataset_id, dataset.signal_version_id, value.asset_id, "
                "asset.asset_key, value.observation_date, value.score "
                "FROM signal.signal_value value JOIN signal.signal_dataset dataset ON "
                "dataset.signal_dataset_id = value.signal_dataset_id "
                "JOIN catalog.asset asset ON asset.asset_id = value.asset_id "
                "WHERE value.signal_dataset_id IN :datasets "
                "ORDER BY value.signal_dataset_id, asset.asset_key, value.observation_date"
            ).bindparams(bindparam("datasets", expanding=True)),
            {"datasets": dataset_ids},
        )
        .mappings()
        .all()
    )
    points: dict[uuid.UUID, list[SignalScoreInput]] = {row["signal_dataset_id"]: [] for row in rows}
    for row in value_rows:
        points[row["signal_dataset_id"]].append(
            SignalScoreInput(
                row["signal_version_id"],
                row["asset_id"],
                row["asset_key"],
                row["observation_date"],
                Decimal(row["score"]),
            )
        )
    return {
        row["signal_version_id"]: _SignalDatasetInput(
            row["signal_dataset_id"],
            row["artifact_id"],
            row["signal_version_id"],
            tuple(points[row["signal_dataset_id"]]),
        )
        for row in rows
    }


def _calculation_inputs(
    context: _PublicationContext, calculation: ModelCalculation
) -> tuple[tuple[ModelComponentInput, _SignalDatasetInput], ...]:
    return tuple(
        (component, context.signal_datasets[component.signal_version_id])
        for dimension in calculation.specification.dimensions
        for component in dimension.components
    )


def _dataset_semantic(
    context: _PublicationContext,
    calculation: ModelCalculation,
    input_set_hash: str,
) -> dict[str, Any]:
    return {
        "model_specification_artifact_id": str(calculation.specification.artifact_id),
        "universe_artifact_id": str(context.universe_artifact_id),
        "data_bundle_artifact_id": str(context.bundle_artifact_id),
        "eligibility_artifact_id": str(context.eligibility_artifact_id),
        "signal_engine_artifact_id": str(context.signal_engine_artifact_id),
        "model_engine_artifact_id": str(context.model_engine_artifact_id),
        "input_set_hash": input_set_hash,
        "coverage_start": calculation.coverage_start,
        "coverage_end": calculation.coverage_end,
        "row_count": len(calculation.points),
    }


def _write_dataset(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _PublicationContext,
    calculation: ModelCalculation,
    inputs: tuple[tuple[ModelComponentInput, _SignalDatasetInput], ...],
    input_set_hash: str,
) -> None:
    dataset_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO model.model_dataset "
            "(model_dataset_id, artifact_id, model_specification_id, universe_version_id, "
            "data_bundle_version_id, eligibility_snapshot_id, engine_version_id, "
            "input_set_hash, coverage_start, coverage_end, row_count) VALUES "
            "(:id, :artifact, :specification, :universe, :bundle, :eligibility, :engine, "
            ":input_hash, :start, :end, :row_count)"
        ),
        {
            "id": dataset_id,
            "artifact": artifact_id,
            "specification": calculation.specification.model_specification_id,
            "universe": context.universe_version_id,
            "bundle": context.bundle_version_id,
            "eligibility": context.eligibility_snapshot_id,
            "engine": context.model_engine_version_id,
            "input_hash": input_set_hash,
            "start": calculation.coverage_start,
            "end": calculation.coverage_end,
            "row_count": len(calculation.points),
        },
    )
    connection.execute(
        text(
            "INSERT INTO model.model_dataset_input "
            "(model_dataset_id, model_component_id, signal_dataset_id) "
            "VALUES (:dataset, :component, :signal_dataset)"
        ),
        [
            {
                "dataset": dataset_id,
                "component": component.model_component_id,
                "signal_dataset": dataset.signal_dataset_id,
            }
            for component, dataset in inputs
        ],
    )
    connection.execute(
        text(
            "INSERT INTO model.model_value "
            "(model_dataset_id, asset_id, observation_date, score, direction, confidence) "
            "VALUES (:dataset, :asset, :date, :score, :direction, :confidence)"
        ),
        [
            {
                "dataset": dataset_id,
                "asset": point.asset_id,
                "date": point.observation_date,
                "score": point.score,
                "direction": point.direction,
                "confidence": point.confidence,
            }
            for point in calculation.points
        ],
    )


def _engine_key(connection: Connection, engine_version_id: uuid.UUID) -> str:
    return str(
        connection.execute(
            text(
                "SELECT definition.engine_key FROM ops.engine_definition definition "
                "JOIN ops.engine_version version ON version.engine_definition_id = "
                "definition.engine_definition_id WHERE version.engine_version_id = :id"
            ),
            {"id": engine_version_id},
        ).scalar_one()
    )


def _published_artifact(
    connection: Connection, artifact_id: uuid.UUID, artifact_type: str
) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT * FROM lineage.artifact WHERE artifact_id = :id "
                "AND artifact_type = :type AND status = 'published'"
            ),
            {"id": artifact_id, "type": artifact_type},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Published {artifact_type} artifact not found: {artifact_id}")
    return row


def _published_business(
    connection: Connection, table: str, id_column: str, artifact_id: uuid.UUID
) -> RowMapping:
    row = (
        connection.execute(
            text(
                f"SELECT business.* FROM {table} business JOIN lineage.artifact artifact "
                "ON artifact.artifact_id = business.artifact_id WHERE business.artifact_id = :id "
                "AND artifact.status = 'published'"
            ),
            {"id": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row[id_column] is None:
        raise ValueError(f"Published dependency not found: {table}")
    return row


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
