from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from style_rotation.core.canonical import sha256_hexdigest

EXPECTED_DISTRIBUTION = {
    "single_signal": 51,
    "dimension_subset_equal_weight": 31,
    "fixed_weight": 2,
    "directional_vote": 2,
}
LEGACY_DIMENSION_WEIGHT_TOLERANCE = Decimal("1e-16")


def load_model_migration_registry(
    path: Path,
    *,
    oracle_manifest_path: Path = Path(
        "v0.22/m0/v021-baseline-manifest.v0.22.0.json"
    ),
    aggregation_catalog_path: Path = Path(
        "v0.22/catalogs/aggregation/deterministic.v0.22.0.json"
    ),
    signal_registry_path: Path = Path(
        "v0.22/m4/migration-registry.v0.22.3.json"
    ),
) -> dict[str, Any]:
    registry = _read_json(path)
    fingerprint = registry.pop("registry_fingerprint", None)
    validate_model_migration_registry(
        registry,
        oracle_manifest=_read_json(oracle_manifest_path),
        aggregation_catalog=_read_json(aggregation_catalog_path),
        signal_registry=_read_json(signal_registry_path),
    )
    if fingerprint != sha256_hexdigest(registry):
        raise ValueError("Model Registry fingerprint drift")
    registry["registry_fingerprint"] = fingerprint
    return registry


def extract_model_migration_registry(
    engine: Engine,
    *,
    oracle_manifest_path: Path,
    aggregation_catalog_path: Path,
    signal_registry_path: Path,
) -> dict[str, Any]:
    """Read the frozen v0.21 Oracle and build all 86 exact Model mappings."""

    oracle = _read_json(oracle_manifest_path)
    aggregation = _read_json(aggregation_catalog_path)
    signal_registry = _read_json(signal_registry_path)
    signal_mapping = {
        record["legacy_key"]: record["mapping"]["variant_key"]
        for record in signal_registry["records"]
        if record["component_kind"] == "signal_version"
    }
    oracle_outputs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for output in oracle["oracle_outputs"]["model_datasets"]:
        oracle_outputs[output["specification_key"]].append(_oracle_output(output))
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            database_name = connection.execute(
                text("SELECT current_database()")
            ).scalar_one()
            specifications = connection.execute(
                text(
                    """
                    SELECT specification.model_specification_id,
                           specification.specification_key,
                           specification.specification_type,
                           overall_method.method_key AS method,
                           specification.tie_output,specification.output_type,
                           specification.active_dimension_count,
                           specification.component_count,specification.research_tier,
                           artifact.artifact_id,artifact.status AS artifact_status,
                           artifact.semantic_fingerprint,artifact.content_hash
                    FROM model.model_specification specification
                    JOIN model.model_method_version overall_method_version
                      ON overall_method_version.model_method_version_id=
                         specification.overall_method_version_id
                    JOIN model.model_method_definition overall_method
                      ON overall_method.model_method_definition_id=
                         overall_method_version.model_method_definition_id
                    JOIN lineage.artifact artifact
                      ON artifact.artifact_id=specification.artifact_id
                    ORDER BY specification.specification_key
                    """
                )
            ).mappings().all()
            dimensions = connection.execute(
                text(
                    """
                    SELECT dimension.model_specification_id,dimension.dimension_key,
                           dimension.ordinal,method.method_key AS method,
                           dimension.input_transform,dimension.weight
                    FROM model.model_dimension dimension
                    JOIN model.model_method_version method_version
                      ON method_version.model_method_version_id=dimension.method_version_id
                    JOIN model.model_method_definition method
                      ON method.model_method_definition_id=
                         method_version.model_method_definition_id
                    ORDER BY dimension.model_specification_id,dimension.ordinal
                    """
                )
            ).mappings().all()
            components = connection.execute(
                text(
                    """
                    SELECT component.model_specification_id,dimension.dimension_key,
                           component.ordinal,component.input_transform,component.weight,
                           definition.signal_key
                    FROM model.model_component component
                    JOIN model.model_dimension dimension
                      ON dimension.model_dimension_id=component.model_dimension_id
                    JOIN signal.signal_version version
                      ON version.signal_version_id=component.signal_version_id
                    JOIN signal.signal_definition definition
                      ON definition.signal_definition_id=version.signal_definition_id
                    ORDER BY component.model_specification_id,dimension.ordinal,
                             component.ordinal
                    """
                )
            ).mappings().all()
        finally:
            transaction.rollback()
    dimensions_by_spec: dict[object, list[dict[str, Any]]] = defaultdict(list)
    for row in dimensions:
        dimensions_by_spec[row["model_specification_id"]].append(
            {
                "dimension_key": row["dimension_key"],
                "ordinal": row["ordinal"],
                "method": row["method"],
                "input_transform": row["input_transform"],
                "weight": _decimal(row["weight"]),
                "components": [],
            }
        )
    component_by_spec_dimension: dict[tuple[object, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for row in components:
        legacy_signal_key = row["signal_key"]
        try:
            mapped_signal_key = signal_mapping[legacy_signal_key]
        except KeyError as error:
            raise ValueError(
                f"Model component references unmapped Signal {legacy_signal_key}"
            ) from error
        component_by_spec_dimension[
            (row["model_specification_id"], row["dimension_key"])
        ].append(
            {
                "ordinal": row["ordinal"],
                "legacy_signal_key": legacy_signal_key,
                "mapped_signal_variant_key": mapped_signal_key,
                "input_transform": row["input_transform"],
                "weight": _decimal(row["weight"]),
            }
        )
    records: list[dict[str, Any]] = []
    for row in specifications:
        specification_id = row["model_specification_id"]
        recipe_dimensions = dimensions_by_spec[specification_id]
        for dimension in recipe_dimensions:
            dimension["components"] = component_by_spec_dimension[
                (specification_id, dimension["dimension_key"])
            ]
        family_key, preset_key = _mapping_for(
            row["specification_type"], row["specification_key"]
        )
        records.append(
            {
                "legacy_key": row["specification_key"],
                "legacy_artifact": {
                    "artifact_id": str(row["artifact_id"]),
                    "status": row["artifact_status"],
                    "semantic_fingerprint": row["semantic_fingerprint"],
                    "content_hash": row["content_hash"],
                },
                "legacy_recipe": {
                    "specification_type": row["specification_type"],
                    "method": row["method"],
                    "tie_output": row["tie_output"],
                    "output_type": row["output_type"],
                    "research_tier": row["research_tier"],
                    "active_dimension_count": row["active_dimension_count"],
                    "component_count": row["component_count"],
                    "dimensions": recipe_dimensions,
                },
                "mapping": {
                    "family_key": family_key,
                    "parameter_preset_key": preset_key,
                    "execution_mode": "deterministic",
                    "input_signal_variant_keys": [
                        component["mapped_signal_variant_key"]
                        for dimension in recipe_dimensions
                        for component in dimension["components"]
                    ],
                },
                "oracle_outputs": sorted(
                    oracle_outputs[row["specification_key"]],
                    key=lambda item: item["bundle_version"],
                ),
                "status": "oracle_bound",
            }
        )
    registry = {
        "catalog_type": "v022_model_migration_registry",
        "registry_version": "0.22.0",
        "contract_version": "v0.22.0",
        "oracle_baseline_id": oracle["baseline_id"],
        "oracle_manifest_fingerprint": sha256_hexdigest(oracle),
        "aggregation_catalog_fingerprint": sha256_hexdigest(aggregation),
        "signal_registry_fingerprint": sha256_hexdigest(signal_registry),
        "source_database": {
            "database_name": database_name,
            "alembic_revision": revision,
            "transaction_mode": "read_only",
        },
        "records": records,
    }
    validate_model_migration_registry(
        registry,
        oracle_manifest=oracle,
        aggregation_catalog=aggregation,
        signal_registry=signal_registry,
    )
    registry["registry_fingerprint"] = sha256_hexdigest(registry)
    return registry


def validate_model_migration_registry(
    registry: Mapping[str, Any],
    *,
    oracle_manifest: Mapping[str, Any],
    aggregation_catalog: Mapping[str, Any],
    signal_registry: Mapping[str, Any],
) -> None:
    if registry.get("catalog_type") != "v022_model_migration_registry":
        raise ValueError("invalid Model Migration Registry type")
    if registry.get("oracle_baseline_id") != oracle_manifest["baseline_id"]:
        raise ValueError("Model Registry references the wrong Oracle baseline")
    if registry.get("oracle_manifest_fingerprint") != sha256_hexdigest(oracle_manifest):
        raise ValueError("Model Registry Oracle fingerprint drift")
    if registry.get("aggregation_catalog_fingerprint") != sha256_hexdigest(
        aggregation_catalog
    ):
        raise ValueError("Model Registry Aggregation Catalog fingerprint drift")
    if registry.get("signal_registry_fingerprint") != sha256_hexdigest(signal_registry):
        raise ValueError("Model Registry Signal Registry fingerprint drift")
    records = registry.get("records")
    if not isinstance(records, list) or len(records) != 86:
        raise ValueError("Model Registry must contain exactly 86 records")
    keys = [record["legacy_key"] for record in records]
    if len(set(keys)) != len(keys):
        raise ValueError("Model Registry legacy keys must be unique")
    expected_keys = {
        output["specification_key"]
        for output in oracle_manifest["oracle_outputs"]["model_datasets"]
    }
    if set(keys) != expected_keys:
        raise ValueError("Model Registry coverage differs from the M0 Oracle")
    distribution = Counter(
        record["legacy_recipe"]["specification_type"] for record in records
    )
    if dict(distribution) != EXPECTED_DISTRIBUTION:
        raise ValueError(f"Model Registry distribution mismatch: {distribution}")
    aggregation_families = {
        item["family_key"]: item for item in aggregation_catalog["families"]
    }
    signal_mappings = {
        record["mapping"]["variant_key"]
        for record in signal_registry["records"]
        if record["component_kind"] == "signal_version"
    }
    expected_outputs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for output in oracle_manifest["oracle_outputs"]["model_datasets"]:
        expected_outputs[output["specification_key"]].append(_oracle_output(output))
    for record in records:
        _validate_model_record(
            record,
            aggregation_families,
            signal_mappings,
            sorted(
                expected_outputs[record["legacy_key"]],
                key=lambda item: item["bundle_version"],
            ),
        )


def model_registry_summary(registry: Mapping[str, Any]) -> dict[str, Any]:
    records = registry["records"]
    return {
        "record_count": len(records),
        "distribution": dict(
            sorted(
                Counter(
                    record["legacy_recipe"]["specification_type"]
                    for record in records
                ).items()
            )
        ),
        "family_mapping": dict(
            sorted(Counter(record["mapping"]["family_key"] for record in records).items())
        ),
        "oracle_binding_count": sum(len(record["oracle_outputs"]) for record in records),
        "registry_fingerprint": registry.get("registry_fingerprint"),
    }


def _validate_model_record(
    record: Mapping[str, Any],
    aggregation_families: Mapping[str, Mapping[str, Any]],
    signal_mappings: set[str],
    expected_oracle_outputs: list[dict[str, Any]],
) -> None:
    artifact = record["legacy_artifact"]
    if artifact["status"] != "published":
        raise ValueError(f"Legacy Model is not published: {record['legacy_key']}")
    recipe = record["legacy_recipe"]
    dimensions = recipe["dimensions"]
    components = [component for item in dimensions for component in item["components"]]
    if len(dimensions) != recipe["active_dimension_count"]:
        raise ValueError(f"Model dimension count mismatch: {record['legacy_key']}")
    if len(components) != recipe["component_count"]:
        raise ValueError(f"Model component count mismatch: {record['legacy_key']}")
    dimension_weight_sum = sum(
        (Decimal(item["weight"]) for item in dimensions), Decimal()
    )
    if abs(dimension_weight_sum - Decimal(1)) > LEGACY_DIMENSION_WEIGHT_TOLERANCE:
        raise ValueError(f"Model dimension weights do not sum to one: {record['legacy_key']}")
    for dimension in dimensions:
        if dimension["method"] != "weighted_mean":
            raise ValueError(
                f"Unsupported legacy Model dimension method: {record['legacy_key']}"
            )
        if sum(
            (Decimal(item["weight"]) for item in dimension["components"]), Decimal()
        ) != Decimal(1):
            raise ValueError(
                f"Model component weights do not sum to one: {record['legacy_key']}"
            )
    mapping = record["mapping"]
    try:
        family = aggregation_families[mapping["family_key"]]
    except KeyError as error:
        raise ValueError(f"Unknown Aggregation Family: {mapping['family_key']}") from error
    presets = {item["preset_key"] for item in family["parameter_presets"]}
    preset = mapping["parameter_preset_key"]
    if preset is not None and preset not in presets:
        raise ValueError(f"Unknown Aggregation preset: {preset}")
    expected_method = {
        "single_signal_identity": "weighted_mean",
        "hierarchical_weighted_mean": "weighted_mean",
        "directional_weighted_vote": (
            "weighted_vote"
            if preset == "legacy_weighted_vote_v1"
            else "majority_vote"
        ),
    }[mapping["family_key"]]
    if recipe["method"] != expected_method:
        raise ValueError(f"Model method disagrees with Aggregation mapping: {record['legacy_key']}")
    if mapping["family_key"] == "single_signal_identity":
        if len(dimensions) != 1 or len(components) != 1:
            raise ValueError(
                f"Single Signal identity recipe is not structurally exact: {record['legacy_key']}"
            )
        dimension = dimensions[0]
        component = components[0]
        if (
            Decimal(dimension["weight"]) != Decimal(1)
            or Decimal(component["weight"]) != Decimal(1)
            or dimension["input_transform"] != "identity"
            or component["input_transform"] != "identity"
        ):
            raise ValueError(
                f"Single Signal identity recipe is not structurally exact: {record['legacy_key']}"
            )
    inputs = mapping["input_signal_variant_keys"]
    if len(inputs) != len(components) or len(inputs) != len(set(inputs)):
        raise ValueError(f"Model inputs are incomplete or duplicated: {record['legacy_key']}")
    if not set(inputs) <= signal_mappings:
        raise ValueError(f"Model references unknown v0.22 Signal: {record['legacy_key']}")
    if not family["minimum_inputs"] <= len(inputs) <= family["maximum_inputs"]:
        raise ValueError(f"Model violates Aggregation input cardinality: {record['legacy_key']}")
    if len(record["oracle_outputs"]) != 2:
        raise ValueError(f"Model must bind two frozen Oracle outputs: {record['legacy_key']}")
    if record["oracle_outputs"] != expected_oracle_outputs:
        raise ValueError(f"Model Oracle binding drift: {record['legacy_key']}")


def _mapping_for(specification_type: str, specification_key: str) -> tuple[str, str | None]:
    if specification_type == "single_signal":
        return "single_signal_identity", None
    if specification_type == "dimension_subset_equal_weight":
        return "hierarchical_weighted_mean", "legacy_dimension_equal_v1"
    if specification_type == "fixed_weight":
        presets = {
            "trend_tilt_v1": "legacy_trend_tilt_v1",
            "defensive_tilt_v1": "legacy_defensive_tilt_v1",
        }
        return "hierarchical_weighted_mean", presets[specification_key]
    if specification_type == "directional_vote":
        preset = (
            "legacy_weighted_vote_v1"
            if specification_key == "five_dimension_weighted_vote_v1"
            else "legacy_equal_vote_v1"
        )
        return "directional_weighted_vote", preset
    raise ValueError(f"Unsupported legacy Model specification type: {specification_type}")


def _oracle_output(output: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "artifact_id",
        "semantic_fingerprint",
        "content_hash",
        "bundle_key",
        "bundle_version",
        "universe_key",
        "universe_version",
        "engine_key",
        "engine_version",
        "coverage_start",
        "coverage_end",
        "row_count",
        "input_set_hash",
    )
    return {key: output[key] for key in keys}


def _decimal(value: object) -> str:
    if not isinstance(value, Decimal):
        raise ValueError("legacy Model weight must be Decimal")
    return format(value, "f")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value
