from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date
from functools import partial
from typing import Any, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.signal.calculator import (
    FactorValueInput,
    SignalCalculation,
    SignalVersionInput,
    calculate_signal,
)


@dataclass(frozen=True, slots=True)
class SignalDatasetPublication:
    signal_key: str
    output_type: str
    artifact_id: uuid.UUID
    row_count: int
    coverage_start: str
    coverage_end: str
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifact_id"] = str(self.artifact_id)
        return payload


@dataclass(frozen=True, slots=True)
class _FactorDatasetInput:
    factor_dataset_id: uuid.UUID
    artifact_id: uuid.UUID
    factor_variant_id: uuid.UUID
    coverage_start: date
    coverage_end: date
    points: tuple[FactorValueInput, ...]


@dataclass(frozen=True, slots=True)
class _PublicationContext:
    versions: tuple[SignalVersionInput, ...]
    factor_datasets: dict[uuid.UUID, _FactorDatasetInput]
    universe_version_id: uuid.UUID
    universe_artifact_id: uuid.UUID
    bundle_version_id: uuid.UUID
    bundle_artifact_id: uuid.UUID
    eligibility_snapshot_id: uuid.UUID
    eligibility_artifact_id: uuid.UUID
    factor_engine_artifact_id: uuid.UUID
    signal_engine_version_id: uuid.UUID
    signal_engine_artifact_id: uuid.UUID


class SignalDatasetPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        signal_catalog_artifact_id: uuid.UUID,
        factor_catalog_artifact_id: uuid.UUID,
        bundle_artifact_id: uuid.UUID,
        eligibility_artifact_id: uuid.UUID,
        factor_engine_artifact_id: uuid.UUID,
        signal_engine_artifact_id: uuid.UUID,
    ) -> tuple[SignalDatasetPublication, ...]:
        context = self._load_context(
            signal_catalog_artifact_id,
            factor_catalog_artifact_id,
            bundle_artifact_id,
            eligibility_artifact_id,
            factor_engine_artifact_id,
            signal_engine_artifact_id,
        )
        calculations = tuple(
            calculate_signal(
                version,
                context.factor_datasets[version.factor_variant_id].points,
            )
            for version in context.versions
        )
        outcomes: list[SignalDatasetPublication] = []
        with self._engine.begin() as connection:
            artifacts = ArtifactService(cast(Engine, _BoundConnection(connection)))
            for calculation in calculations:
                factor_dataset = context.factor_datasets[calculation.version.factor_variant_id]
                semantic = _dataset_semantic(context, calculation, factor_dataset)
                context_key = sha256_hexdigest(semantic)[:16]
                result = artifacts.publish(
                    artifact_type="signal_dataset",
                    artifact_key=f"{calculation.version.signal_key}:{context_key}",
                    version_number=1,
                    semantic_payload=semantic,
                    content_payload={
                        **semantic,
                        "points": [asdict(point) for point in calculation.points],
                    },
                    dependencies=(
                        DependencyInput(calculation.version.artifact_id, "signal_version", 0),
                        DependencyInput(factor_dataset.artifact_id, "factor_dataset", 1),
                        DependencyInput(context.universe_artifact_id, "universe_version", 2),
                        DependencyInput(context.bundle_artifact_id, "data_bundle", 3),
                        DependencyInput(context.eligibility_artifact_id, "eligibility", 4),
                        DependencyInput(context.signal_engine_artifact_id, "engine_version", 5),
                    ),
                    reason=f"publish signal dataset {calculation.version.signal_key}",
                    draft_writer=partial(
                        _write_dataset,
                        context=context,
                        calculation=calculation,
                        factor_dataset=factor_dataset,
                    ),
                )
                outcomes.append(
                    SignalDatasetPublication(
                        calculation.version.signal_key,
                        calculation.version.output_type,
                        result.artifact_id,
                        len(calculation.points),
                        calculation.coverage_start.isoformat(),
                        calculation.coverage_end.isoformat(),
                        result.reused,
                    )
                )
        return tuple(outcomes)

    def _load_context(
        self,
        signal_catalog_artifact_id: uuid.UUID,
        factor_catalog_artifact_id: uuid.UUID,
        bundle_artifact_id: uuid.UUID,
        eligibility_artifact_id: uuid.UUID,
        factor_engine_artifact_id: uuid.UUID,
        signal_engine_artifact_id: uuid.UUID,
    ) -> _PublicationContext:
        with self._engine.connect() as connection:
            _published_artifact(
                connection, signal_catalog_artifact_id, "signal_catalog_materialization"
            )
            _published_artifact(
                connection, factor_catalog_artifact_id, "factor_catalog_materialization"
            )
            versions = _catalog_versions(connection, signal_catalog_artifact_id)
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
            factor_engine = _published_business(
                connection,
                "ops.engine_version",
                "engine_version_id",
                factor_engine_artifact_id,
            )
            signal_engine = _published_business(
                connection,
                "ops.engine_version",
                "engine_version_id",
                signal_engine_artifact_id,
            )
            if _engine_key(connection, factor_engine["engine_version_id"]) != "factor_engine":
                raise ValueError("Supplied factor engine artifact is not a factor engine")
            if _engine_key(connection, signal_engine["engine_version_id"]) != "signal_engine":
                raise ValueError("Supplied signal engine artifact is not a signal engine")
            if eligibility["data_bundle_version_id"] != bundle["data_bundle_version_id"]:
                raise ValueError("Eligibility snapshot does not bind the supplied data bundle")
            if eligibility["eligible_count"] != eligibility["member_count"]:
                raise ValueError("Formal signal publication requires all universe members eligible")
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
            factor_datasets = _factor_datasets(
                connection,
                factor_catalog_artifact_id,
                {version.factor_variant_id for version in versions},
                universe["universe_version_id"],
                bundle["data_bundle_version_id"],
                eligibility["eligibility_snapshot_id"],
                factor_engine["engine_version_id"],
            )
        expected = {version.factor_variant_id for version in versions}
        missing = expected.difference(factor_datasets)
        if missing:
            raise ValueError(f"Missing exact published factor datasets for {len(missing)} variants")
        return _PublicationContext(
            versions,
            factor_datasets,
            universe["universe_version_id"],
            universe["artifact_id"],
            bundle["data_bundle_version_id"],
            bundle_artifact_id,
            eligibility["eligibility_snapshot_id"],
            eligibility_artifact_id,
            factor_engine_artifact_id,
            signal_engine["engine_version_id"],
            signal_engine_artifact_id,
        )


def _catalog_versions(
    connection: Connection, signal_catalog_artifact_id: uuid.UUID
) -> tuple[SignalVersionInput, ...]:
    rows = (
        connection.execute(
            text(
                "SELECT version.signal_version_id, version.artifact_id, "
                "definition.signal_key, version.factor_variant_id, version.direction, "
                "version.normalization, version.extreme_policy, version.missing_policy, "
                "version.tie_policy, version.output_type, version.rule "
                "FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact member ON member.artifact_id = "
                "dependency.depends_on_artifact_id "
                "JOIN signal.signal_version version ON version.artifact_id = member.artifact_id "
                "JOIN signal.signal_definition definition ON definition.signal_definition_id = "
                "version.signal_definition_id WHERE dependency.artifact_id = :catalog_id "
                "AND dependency.role = 'materialized_member' "
                "AND member.artifact_type = 'signal_version' AND member.status = 'published' "
                "ORDER BY definition.signal_key"
            ),
            {"catalog_id": signal_catalog_artifact_id},
        )
        .mappings()
        .all()
    )
    if not rows:
        raise ValueError("Signal catalog materialization contains no published versions")
    versions = tuple(
        SignalVersionInput(
            row["signal_version_id"],
            row["artifact_id"],
            row["signal_key"],
            row["factor_variant_id"],
            row["direction"],
            row["normalization"],
            row["extreme_policy"],
            row["missing_policy"],
            row["tie_policy"],
            row["output_type"],
            row["rule"],
        )
        for row in rows
    )
    return versions


def _factor_datasets(
    connection: Connection,
    factor_catalog_artifact_id: uuid.UUID,
    factor_variant_ids: set[uuid.UUID],
    universe_version_id: uuid.UUID,
    bundle_version_id: uuid.UUID,
    eligibility_snapshot_id: uuid.UUID,
    factor_engine_version_id: uuid.UUID,
) -> dict[uuid.UUID, _FactorDatasetInput]:
    rows = (
        connection.execute(
            text(
                "SELECT dataset.factor_dataset_id, dataset.artifact_id, "
                "dataset.factor_variant_id, dataset.coverage_start, dataset.coverage_end "
                "FROM factor.factor_dataset dataset JOIN lineage.artifact artifact ON "
                "artifact.artifact_id = dataset.artifact_id "
                "JOIN factor.factor_variant variant ON variant.factor_variant_id = "
                "dataset.factor_variant_id JOIN lineage.artifact_dependency dependency ON "
                "dependency.depends_on_artifact_id = variant.artifact_id "
                "WHERE dependency.artifact_id = :factor_catalog_id "
                "AND dependency.role = 'materialized_member' "
                "AND dataset.factor_variant_id IN :variant_ids "
                "AND dataset.universe_version_id = :universe_id "
                "AND dataset.data_bundle_version_id = :bundle_id "
                "AND dataset.eligibility_snapshot_id = :eligibility_id "
                "AND dataset.engine_version_id = :engine_id AND artifact.status = 'published'"
            ).bindparams(bindparam("variant_ids", expanding=True)),
            {
                "factor_catalog_id": factor_catalog_artifact_id,
                "variant_ids": tuple(factor_variant_ids),
                "universe_id": universe_version_id,
                "bundle_id": bundle_version_id,
                "eligibility_id": eligibility_snapshot_id,
                "engine_id": factor_engine_version_id,
            },
        )
        .mappings()
        .all()
    )
    if not rows:
        return {}
    dataset_ids = tuple(row["factor_dataset_id"] for row in rows)
    value_rows = (
        connection.execute(
            text(
                "SELECT value.factor_dataset_id, value.asset_id, asset.asset_key, "
                "value.observation_date, value.value FROM factor.factor_value value "
                "JOIN catalog.asset asset ON asset.asset_id = value.asset_id "
                "JOIN catalog.universe_member member ON member.asset_id = value.asset_id "
                "AND member.universe_version_id = :universe_id AND member.role = 'candidate' "
                "WHERE value.factor_dataset_id IN :dataset_ids "
                "ORDER BY value.factor_dataset_id, asset.asset_key, value.observation_date"
            ).bindparams(bindparam("dataset_ids", expanding=True)),
            {"dataset_ids": dataset_ids, "universe_id": universe_version_id},
        )
        .mappings()
        .all()
    )
    points: dict[uuid.UUID, list[FactorValueInput]] = {row["factor_dataset_id"]: [] for row in rows}
    for row in value_rows:
        points[row["factor_dataset_id"]].append(
            FactorValueInput(
                row["asset_id"], row["asset_key"], row["observation_date"], row["value"]
            )
        )
    return {
        row["factor_variant_id"]: _FactorDatasetInput(
            row["factor_dataset_id"],
            row["artifact_id"],
            row["factor_variant_id"],
            row["coverage_start"],
            row["coverage_end"],
            tuple(points[row["factor_dataset_id"]]),
        )
        for row in rows
    }


def _dataset_semantic(
    context: _PublicationContext,
    calculation: SignalCalculation,
    factor_dataset: _FactorDatasetInput,
) -> dict[str, Any]:
    return {
        "signal_version_artifact_id": calculation.version.artifact_id,
        "factor_dataset_artifact_id": factor_dataset.artifact_id,
        "universe_artifact_id": context.universe_artifact_id,
        "data_bundle_artifact_id": context.bundle_artifact_id,
        "eligibility_artifact_id": context.eligibility_artifact_id,
        "factor_engine_artifact_id": context.factor_engine_artifact_id,
        "signal_engine_artifact_id": context.signal_engine_artifact_id,
        "coverage_start": calculation.coverage_start,
        "coverage_end": calculation.coverage_end,
        "score_encoding": "numeric(24,18)",
    }


def _write_dataset(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _PublicationContext,
    calculation: SignalCalculation,
    factor_dataset: _FactorDatasetInput,
) -> None:
    dataset_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO signal.signal_dataset (signal_dataset_id, artifact_id, "
            "signal_version_id, factor_dataset_id, universe_version_id, data_bundle_version_id, "
            "eligibility_snapshot_id, engine_version_id, coverage_start, coverage_end, row_count) "
            "VALUES (:id, :artifact, :signal_version, :factor_dataset, :universe, :bundle, "
            ":eligibility, :engine, :start, :end, :row_count)"
        ),
        {
            "id": dataset_id,
            "artifact": artifact_id,
            "signal_version": calculation.version.signal_version_id,
            "factor_dataset": factor_dataset.factor_dataset_id,
            "universe": context.universe_version_id,
            "bundle": context.bundle_version_id,
            "eligibility": context.eligibility_snapshot_id,
            "engine": context.signal_engine_version_id,
            "start": calculation.coverage_start,
            "end": calculation.coverage_end,
            "row_count": len(calculation.points),
        },
    )
    connection.execute(
        text(
            "INSERT INTO signal.signal_value "
            "(signal_dataset_id, asset_id, observation_date, score, state, event) "
            "VALUES (:dataset, :asset, :date, :score, :state, :event)"
        ),
        [
            {
                "dataset": dataset_id,
                "asset": point.asset_id,
                "date": point.observation_date,
                "score": point.score,
                "state": point.state,
                "event": point.event,
            }
            for point in calculation.points
        ],
    )


def _engine_key(connection: Connection, engine_version_id: uuid.UUID) -> str:
    value = connection.execute(
        text(
            "SELECT definition.engine_key FROM ops.engine_definition definition "
            "JOIN ops.engine_version version ON version.engine_definition_id = "
            "definition.engine_definition_id WHERE version.engine_version_id = :id"
        ),
        {"id": engine_version_id},
    ).scalar_one()
    return str(value)


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
