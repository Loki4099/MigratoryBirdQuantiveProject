from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import Engine, text

from style_rotation.strategy.product_service import publish_strategy_product
from style_rotation.strategy.target_publication import StrategyTargetPublicationService

FORMAL_PRODUCT_MODEL_TYPES = (
    "dimension_subset_equal_weight",
    "fixed_weight",
    "directional_vote",
)
FORMAL_K_VALUES = (1, 2, 3)
FORMAL_FREQUENCIES = ("weekly", "monthly")


@dataclass(frozen=True, slots=True)
class StrategyGridPublication:
    model_count: int
    target_count: int
    target_artifact_ids: tuple[uuid.UUID, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["target_artifact_ids"] = [str(item) for item in self.target_artifact_ids]
        return payload


def publish_strategy_target_grid(
    engine: Engine,
    *,
    strategy_catalog_artifact_id: uuid.UUID,
    model_catalog_artifact_id: uuid.UUID,
    universe_artifact_id: uuid.UUID,
    data_bundle_artifact_id: uuid.UUID,
    eligibility_artifact_id: uuid.UUID,
    target_engine_artifact_id: uuid.UUID,
    auxiliary_signal_dataset_artifact_id: uuid.UUID,
    model_specification_keys: tuple[str, ...] | None = None,
    k_values: tuple[int, ...] = (2,),
    frequencies: tuple[str, ...] = FORMAL_FREQUENCIES,
) -> StrategyGridPublication:
    if not k_values or set(k_values) - set(FORMAL_K_VALUES):
        raise ValueError("Strategy grid K must use one or more of 1, 2, and 3")
    if not frequencies or set(frequencies) - set(FORMAL_FREQUENCIES):
        raise ValueError("Strategy grid frequency must use weekly and/or monthly")
    with engine.connect() as connection:
        model_rows = connection.execute(
            text(
                "SELECT specification.specification_key, "
                "dataset.artifact_id AS dataset_artifact_id "
                "FROM lineage.artifact_dependency member "
                "JOIN model.model_specification specification ON specification.artifact_id = "
                "member.depends_on_artifact_id JOIN model.model_dataset dataset ON "
                "dataset.model_specification_id = specification.model_specification_id "
                "JOIN data.data_bundle_version bundle ON bundle.data_bundle_version_id = "
                "dataset.data_bundle_version_id JOIN catalog.eligibility_snapshot eligibility ON "
                "eligibility.eligibility_snapshot_id = dataset.eligibility_snapshot_id "
                "JOIN lineage.artifact dataset_artifact ON dataset_artifact.artifact_id = "
                "dataset.artifact_id AND dataset_artifact.status = 'published' "
                "WHERE member.artifact_id = :catalog AND member.role = 'materialized_member' "
                "AND specification.specification_type = ANY(:types) "
                "AND bundle.artifact_id = :bundle AND eligibility.artifact_id = :eligibility "
                "ORDER BY specification.specification_key"
            ),
            {
                "catalog": model_catalog_artifact_id,
                "types": list(FORMAL_PRODUCT_MODEL_TYPES),
                "bundle": data_bundle_artifact_id,
                "eligibility": eligibility_artifact_id,
            },
        ).mappings().all()
        variants = connection.execute(
            text(
                "SELECT variant.variant_key, variant.target_k, variant.trend_filter FROM "
                "lineage.artifact_dependency member JOIN strategy.strategy_variant variant ON "
                "variant.artifact_id = member.depends_on_artifact_id WHERE member.artifact_id = "
                ":catalog AND member.role = 'materialized_member' AND variant.target_k = ANY(:ks) "
                "ORDER BY variant.variant_key"
            ),
            {"catalog": strategy_catalog_artifact_id, "ks": list(k_values)},
        ).mappings().all()
        schedules = connection.execute(
            text(
                "SELECT definition.schedule_key, version.frequency FROM "
                "lineage.artifact_dependency member JOIN ops.rebalance_schedule_version version "
                "ON version.artifact_id = member.depends_on_artifact_id JOIN "
                "ops.rebalance_schedule_definition definition ON "
                "definition.rebalance_schedule_definition_id = "
                "version.rebalance_schedule_definition_id WHERE member.artifact_id = :catalog "
                "AND member.role = 'materialized_member' AND version.frequency = ANY(:frequencies) "
                "ORDER BY definition.schedule_key"
            ),
            {"catalog": strategy_catalog_artifact_id, "frequencies": list(frequencies)},
        ).mappings().all()
    if model_specification_keys is not None:
        requested = set(model_specification_keys)
        model_rows = [row for row in model_rows if row["specification_key"] in requested]
        found = {str(row["specification_key"]) for row in model_rows}
        missing = requested - found
        if missing:
            raise ValueError(f"Strategy grid Model Dataset not found: {sorted(missing)}")
    if not model_rows or not variants or not schedules:
        raise ValueError("Strategy grid inputs resolve to an empty formal matrix")
    target_service = StrategyTargetPublicationService(engine)
    target_ids: list[uuid.UUID] = []
    for model in model_rows:
        for variant in variants:
            for schedule in schedules:
                product = publish_strategy_product(
                    engine,
                    strategy_catalog_artifact_id,
                    model_catalog_artifact_id,
                    universe_artifact_id,
                    str(model["specification_key"]),
                    str(variant["variant_key"]),
                    str(schedule["schedule_key"]),
                )
                target = target_service.publish(
                    product.version_artifact_id,
                    model["dataset_artifact_id"],
                    target_engine_artifact_id,
                    (
                        auxiliary_signal_dataset_artifact_id
                        if variant["trend_filter"] != "none"
                        else None
                    ),
                )
                target_ids.append(target.artifact_id)
    return StrategyGridPublication(len(model_rows), len(target_ids), tuple(target_ids))
