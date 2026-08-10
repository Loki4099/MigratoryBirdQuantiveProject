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
from style_rotation.factor.calculator import (
    FactorBar,
    FactorVariantInput,
    VariantCalculation,
    calculate_variant,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput


@dataclass(frozen=True, slots=True)
class FactorDatasetPublication:
    variant_key: str
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
class _PublicationContext:
    variants: tuple[FactorVariantInput, ...]
    bars_by_asset: dict[uuid.UUID, tuple[FactorBar, ...]]
    universe_version_id: uuid.UUID
    universe_artifact_id: uuid.UUID
    bundle_version_id: uuid.UUID
    bundle_artifact_id: uuid.UUID
    eligibility_snapshot_id: uuid.UUID
    eligibility_artifact_id: uuid.UUID
    engine_version_id: uuid.UUID
    engine_artifact_id: uuid.UUID
    coverage_start: date
    coverage_end: date


class FactorDatasetPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        factor_catalog_artifact_id: uuid.UUID,
        bundle_artifact_id: uuid.UUID,
        eligibility_artifact_id: uuid.UUID,
        engine_artifact_id: uuid.UUID,
    ) -> tuple[FactorDatasetPublication, ...]:
        context = self._load_context(
            factor_catalog_artifact_id,
            bundle_artifact_id,
            eligibility_artifact_id,
            engine_artifact_id,
        )
        calculations = tuple(
            calculate_variant(
                context.bars_by_asset,
                variant,
                coverage_start=context.coverage_start,
                coverage_end=context.coverage_end,
            )
            for variant in context.variants
        )
        outcomes: list[FactorDatasetPublication] = []
        with self._engine.begin() as connection:
            artifacts = ArtifactService(cast(Engine, _BoundConnection(connection)))
            for calculation in calculations:
                semantic = _dataset_semantic(context, calculation)
                context_key = sha256_hexdigest(semantic)[:16]
                result = artifacts.publish(
                    artifact_type="factor_dataset",
                    artifact_key=f"{calculation.variant.variant_key}:{context_key}",
                    version_number=1,
                    semantic_payload=semantic,
                    content_payload={
                        **semantic,
                        "points": [asdict(point) for point in calculation.points],
                    },
                    dependencies=(
                        DependencyInput(calculation.variant.artifact_id, "factor_variant", 0),
                        DependencyInput(context.universe_artifact_id, "universe_version", 1),
                        DependencyInput(context.bundle_artifact_id, "data_bundle", 2),
                        DependencyInput(context.eligibility_artifact_id, "eligibility", 3),
                        DependencyInput(context.engine_artifact_id, "engine_version", 4),
                    ),
                    reason=f"publish factor dataset {calculation.variant.variant_key}",
                    draft_writer=partial(
                        _write_dataset,
                        context=context,
                        calculation=calculation,
                    ),
                )
                outcomes.append(
                    FactorDatasetPublication(
                        calculation.variant.variant_key,
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
        factor_catalog_artifact_id: uuid.UUID,
        bundle_artifact_id: uuid.UUID,
        eligibility_artifact_id: uuid.UUID,
        engine_artifact_id: uuid.UUID,
    ) -> _PublicationContext:
        with self._engine.connect() as connection:
            _published_artifact(
                connection, factor_catalog_artifact_id, "factor_catalog_materialization"
            )
            variants = _catalog_variants(connection, factor_catalog_artifact_id)
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
            engine = _published_business(
                connection,
                "ops.engine_version",
                "engine_version_id",
                engine_artifact_id,
            )
            if eligibility["data_bundle_version_id"] != bundle["data_bundle_version_id"]:
                raise ValueError("Eligibility snapshot does not bind the supplied data bundle")
            if eligibility["eligible_count"] != eligibility["member_count"]:
                raise ValueError("Formal factor publication requires all universe members eligible")
            if eligibility["warmup_observations"] < max(
                item.required_price_observations for item in variants
            ):
                raise ValueError("Eligibility warmup is shorter than the selected factor catalog")
            universe = (
                connection.execute(
                    text(
                        "SELECT version.universe_version_id, version.artifact_id "
                        "FROM catalog.universe_version version "
                        "JOIN lineage.artifact artifact "
                        "ON artifact.artifact_id = version.artifact_id "
                        "WHERE version.universe_version_id = :id AND artifact.status = 'published'"
                    ),
                    {"id": eligibility["universe_version_id"]},
                )
                .mappings()
                .one()
            )
            engine_key = connection.execute(
                text(
                    "SELECT definition.engine_key FROM ops.engine_definition definition "
                    "JOIN ops.engine_version version ON version.engine_definition_id = "
                    "definition.engine_definition_id WHERE version.engine_version_id = :id"
                ),
                {"id": engine["engine_version_id"]},
            ).scalar_one()
            if engine_key != "factor_engine":
                raise ValueError("Supplied engine artifact is not a factor engine")
            market_dataset_id = connection.execute(
                text(
                    "SELECT dataset_publication_id FROM data.data_bundle_member "
                    "WHERE data_bundle_version_id = :bundle_id AND role = 'canonical_market'"
                ),
                {"bundle_id": bundle["data_bundle_version_id"]},
            ).scalar_one()
            eligible_assets = (
                connection.execute(
                    text(
                        "SELECT item.asset_id, asset.asset_key FROM catalog.eligibility_item item "
                        "JOIN catalog.asset asset ON asset.asset_id = item.asset_id "
                        "WHERE item.eligibility_snapshot_id = :snapshot_id AND item.is_eligible "
                        "ORDER BY asset.asset_key"
                    ),
                    {"snapshot_id": eligibility["eligibility_snapshot_id"]},
                )
                .mappings()
                .all()
            )
            asset_keys = {row["asset_id"]: str(row["asset_key"]) for row in eligible_assets}
            bar_rows = (
                connection.execute(
                    text(
                        "SELECT asset_id, session_date, close_adj, close_raw, volume_raw, "
                        "open_raw, high_raw, low_raw, open_adj, high_adj, low_adj "
                        "FROM data.daily_bar WHERE dataset_publication_id = :dataset_id "
                        "AND asset_id IN :asset_ids AND session_date <= :coverage_end "
                        "ORDER BY asset_id, session_date"
                    ).bindparams(bindparam("asset_ids", expanding=True)),
                    {
                        "dataset_id": market_dataset_id,
                        "asset_ids": tuple(asset_keys),
                        "coverage_end": eligibility["requested_end"],
                    },
                )
                .mappings()
                .all()
            )
        bars_by_asset: dict[uuid.UUID, list[FactorBar]] = {asset_id: [] for asset_id in asset_keys}
        for row in bar_rows:
            bars_by_asset[row["asset_id"]].append(
                FactorBar(
                    row["asset_id"],
                    asset_keys[row["asset_id"]],
                    row["session_date"],
                    row["close_adj"],
                    row["close_raw"],
                    row["volume_raw"],
                    row["open_raw"],
                    row["high_raw"],
                    row["low_raw"],
                    row["open_adj"],
                    row["high_adj"],
                    row["low_adj"],
                )
            )
        return _PublicationContext(
            variants,
            {key: tuple(value) for key, value in bars_by_asset.items()},
            universe["universe_version_id"],
            universe["artifact_id"],
            bundle["data_bundle_version_id"],
            bundle_artifact_id,
            eligibility["eligibility_snapshot_id"],
            eligibility_artifact_id,
            engine["engine_version_id"],
            engine_artifact_id,
            eligibility["requested_start"],
            eligibility["requested_end"],
        )


def _catalog_variants(
    connection: Connection, factor_catalog_artifact_id: uuid.UUID
) -> tuple[FactorVariantInput, ...]:
    rows = (
        connection.execute(
            text(
                "SELECT variant.factor_variant_id, variant.artifact_id, variant.variant_key, "
                "version.implementation_key, variant.parameters, "
                "variant.required_price_observations FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact member "
                "ON member.artifact_id = dependency.depends_on_artifact_id "
                "JOIN factor.factor_variant variant ON variant.artifact_id = member.artifact_id "
                "JOIN factor.factor_definition_version version ON "
                "version.factor_definition_version_id = variant.factor_definition_version_id "
                "WHERE dependency.artifact_id = :catalog_id "
                "AND dependency.role = 'materialized_member' "
                "AND member.artifact_type = 'factor_variant' AND member.status = 'published' "
                "ORDER BY variant.variant_key"
            ),
            {"catalog_id": factor_catalog_artifact_id},
        )
        .mappings()
        .all()
    )
    if not rows:
        raise ValueError("Factor catalog materialization contains no published variants")
    return tuple(FactorVariantInput(**dict(row)) for row in rows)


def _dataset_semantic(
    context: _PublicationContext, calculation: VariantCalculation
) -> dict[str, Any]:
    return {
        "factor_variant_artifact_id": calculation.variant.artifact_id,
        "universe_artifact_id": context.universe_artifact_id,
        "data_bundle_artifact_id": context.bundle_artifact_id,
        "eligibility_artifact_id": context.eligibility_artifact_id,
        "engine_artifact_id": context.engine_artifact_id,
        "coverage_start": calculation.coverage_start,
        "coverage_end": calculation.coverage_end,
        "value_encoding": "ieee754-binary64",
    }


def _write_dataset(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _PublicationContext,
    calculation: VariantCalculation,
) -> None:
    dataset_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO factor.factor_dataset (factor_dataset_id, artifact_id, "
            "factor_variant_id, universe_version_id, data_bundle_version_id, "
            "eligibility_snapshot_id, engine_version_id, coverage_start, coverage_end, "
            "row_count) VALUES (:id, :artifact_id, :variant_id, :universe_id, :bundle_id, "
            ":eligibility_id, :engine_id, :start, :end, :row_count)"
        ),
        {
            "id": dataset_id,
            "artifact_id": artifact_id,
            "variant_id": calculation.variant.factor_variant_id,
            "universe_id": context.universe_version_id,
            "bundle_id": context.bundle_version_id,
            "eligibility_id": context.eligibility_snapshot_id,
            "engine_id": context.engine_version_id,
            "start": calculation.coverage_start,
            "end": calculation.coverage_end,
            "row_count": len(calculation.points),
        },
    )
    connection.execute(
        text(
            "INSERT INTO factor.factor_value "
            "(factor_dataset_id, asset_id, observation_date, value) "
            "VALUES (:dataset_id, :asset_id, :date, :value)"
        ),
        [
            {
                "dataset_id": dataset_id,
                "asset_id": point.asset_id,
                "date": point.observation_date,
                "value": point.value,
            }
            for point in calculation.points
        ],
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
