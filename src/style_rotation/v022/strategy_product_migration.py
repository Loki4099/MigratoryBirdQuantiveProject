from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from style_rotation.core.canonical import sha256_hexdigest


def extract_strategy_product_registry(
    engine: Engine,
    *,
    oracle_path: Path,
    strategy_catalog_path: Path,
    defense_catalog_path: Path,
    aggregation_catalog_path: Path,
    signal_registry_path: Path,
) -> dict[str, Any]:
    oracle = _read(oracle_path)
    strategy_catalog = _read(strategy_catalog_path)
    defense_catalog = _read(defense_catalog_path)
    aggregation_catalog = _read(aggregation_catalog_path)
    signal_registry = _read(signal_registry_path)
    research = oracle["research_and_product_evidence"]
    frozen_strategies = {
        row["compiled_strategy_version_id"]: row
        for row in research["compiled_strategy_versions"]
    }
    frozen_models = {
        row["compiled_model_instance_id"]: row
        for row in research["compiled_model_instances"]
    }
    signal_mapping = {
        row["legacy_key"]: row["mapping"]["variant_key"]
        for row in signal_registry["records"]
        if row["component_kind"] == "signal_version"
    }
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            strategies = connection.execute(
                text(
                    "SELECT strategy.compiled_strategy_version_id::text,"
                    "strategy.compiled_model_instance_id::text,strategy.artifact_id::text,"
                    "strategy.branch_key,strategy.strategy_family_key,"
                    "strategy.strategy_preset_key,strategy.schedule_key,"
                    "strategy.strategy_fingerprint,strategy.rule_graph,"
                    "model.instance_fingerprint FROM strategy.compiled_strategy_version "
                    "strategy JOIN workspace.compiled_model_instance model ON "
                    "model.compiled_model_instance_id=strategy.compiled_model_instance_id "
                    "ORDER BY strategy.created_at,strategy.compiled_strategy_version_id"
                )
            ).mappings().all()
            products = connection.execute(
                text(
                    "SELECT product.product_version_id::text,product.product_key,"
                    "product.version_number,product.compiled_strategy_version_id::text,"
                    "product.artifact_id::text,enrollment.product_enrollment_id::text,"
                    "enrollment.lifecycle,enrollment.health FROM product.product_version product "
                    "LEFT JOIN product.product_enrollment enrollment ON "
                    "enrollment.product_version_id=product.product_version_id "
                    "ORDER BY product.product_key,product.version_number,enrollment.created_at"
                )
            ).mappings().all()
            database_name = connection.execute(
                text("SELECT current_database()")
            ).scalar_one()
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        finally:
            transaction.rollback()
    records: list[dict[str, Any]] = []
    for row in strategies:
        strategy_id = row["compiled_strategy_version_id"]
        frozen = frozen_strategies[strategy_id]
        model = frozen_models[row["compiled_model_instance_id"]]
        records.append(
            {
                "legacy_identity": {
                    **frozen,
                    "compiled_model_instance_id": row["compiled_model_instance_id"],
                    "instance_fingerprint": row["instance_fingerprint"],
                },
                "model_mapping": _model_mapping(model, signal_mapping),
                "strategy_mapping": _strategy_mapping(frozen),
                "defense_mapping_key": _defense_key(
                    frozen["rule_graph"]["parameters"]["defense"]
                ),
                "status": "oracle_bound",
            }
        )
    strategy_by_id = {
        record["legacy_identity"]["compiled_strategy_version_id"]: record
        for record in records
    }
    active_by_artifact = {
        row["artifact_id"]: row for row in research["active_products"]
    }
    product_records: list[dict[str, Any]] = []
    for row in products:
        frozen = active_by_artifact.get(row["artifact_id"])
        if frozen is None:
            raise ValueError(f"Product missing from the M0 Oracle: {row['product_key']}")
        strategy = strategy_by_id[row["compiled_strategy_version_id"]]
        product_records.append(
            {
                "legacy_identity": {**frozen, **dict(row)},
                "aggregation_mapping": strategy["model_mapping"],
                "strategy_mapping": strategy["strategy_mapping"],
                "defense_mapping_key": strategy["defense_mapping_key"],
                "lineage_closure": {
                    "artifact_count": len(research["active_product_artifact_closure"]),
                    "artifact_closure_fingerprint": sha256_hexdigest(
                        research["active_product_artifact_closure"]
                    ),
                    "dependency_edge_count": len(
                        research["active_product_dependency_edges"]
                    ),
                    "dependency_edges_fingerprint": sha256_hexdigest(
                        research["active_product_dependency_edges"]
                    ),
                },
                "status": "oracle_bound",
            }
        )
    registry: dict[str, Any] = {
        "catalog_type": "v022_strategy_product_migration_registry",
        "registry_version": "0.22.0",
        "contract_version": "v0.22.0",
        "oracle_baseline_id": oracle["baseline_id"],
        "oracle_manifest_fingerprint": sha256_hexdigest(oracle),
        "strategy_catalog_fingerprint": sha256_hexdigest(strategy_catalog),
        "defense_catalog_fingerprint": sha256_hexdigest(defense_catalog),
        "aggregation_catalog_fingerprint": sha256_hexdigest(aggregation_catalog),
        "signal_registry_fingerprint": sha256_hexdigest(signal_registry),
        "source_database": {
            "database_name": database_name,
            "alembic_revision": revision,
            "transaction_mode": "read_only",
        },
        "defense_baselines": [
            {
                "legacy_key": "none",
                "mapping": None,
                "historical_strategy_reference_count": 8,
                "status": "catalog_validated",
            },
            {
                "legacy_key": "fixed_20",
                "mapping": {"variant_key": "fixed20_defense", "version_number": 2},
                "historical_strategy_reference_count": 6,
                "status": "catalog_validated",
            },
            {
                "legacy_key": "internal_timing_v1",
                "mapping": {
                    "variant_key": "ma200_tiered_defense",
                    "version_number": 1,
                },
                "historical_strategy_reference_count": 0,
                "status": "catalog_validated",
            },
        ],
        "strategy_records": records,
        "product_records": product_records,
    }
    validate_strategy_product_registry(
        registry,
        oracle=oracle,
        strategy_catalog=strategy_catalog,
        defense_catalog=defense_catalog,
        aggregation_catalog=aggregation_catalog,
        signal_registry=signal_registry,
    )
    registry["registry_fingerprint"] = sha256_hexdigest(registry)
    return registry


def load_strategy_product_registry(
    path: Path,
    *,
    oracle_path: Path = Path("v0.22/m0/v021-baseline-manifest.v0.22.0.json"),
    strategy_catalog_path: Path = Path(
        "v0.22/catalogs/strategies/cross_section.v0.22.1.json"
    ),
    defense_catalog_path: Path = Path(
        "v0.22/catalogs/defense/parity.v0.22.1.json"
    ),
    aggregation_catalog_path: Path = Path(
        "v0.22/catalogs/aggregation/deterministic.v0.22.0.json"
    ),
    signal_registry_path: Path = Path(
        "v0.22/m4/migration-registry.v0.22.3.json"
    ),
) -> dict[str, Any]:
    registry = _read(path)
    fingerprint = registry.pop("registry_fingerprint", None)
    validate_strategy_product_registry(
        registry,
        oracle=_read(oracle_path),
        strategy_catalog=_read(strategy_catalog_path),
        defense_catalog=_read(defense_catalog_path),
        aggregation_catalog=_read(aggregation_catalog_path),
        signal_registry=_read(signal_registry_path),
    )
    if fingerprint != sha256_hexdigest(registry):
        raise ValueError("Strategy/Product Registry fingerprint drift")
    registry["registry_fingerprint"] = fingerprint
    return registry


def validate_strategy_product_registry(
    registry: Mapping[str, Any],
    *,
    oracle: Mapping[str, Any],
    strategy_catalog: Mapping[str, Any],
    defense_catalog: Mapping[str, Any],
    aggregation_catalog: Mapping[str, Any],
    signal_registry: Mapping[str, Any],
) -> None:
    if registry.get("catalog_type") != "v022_strategy_product_migration_registry":
        raise ValueError("invalid Strategy/Product Registry type")
    sources = (
        ("oracle_manifest_fingerprint", oracle),
        ("strategy_catalog_fingerprint", strategy_catalog),
        ("defense_catalog_fingerprint", defense_catalog),
        ("aggregation_catalog_fingerprint", aggregation_catalog),
        ("signal_registry_fingerprint", signal_registry),
    )
    for field, document in sources:
        if registry.get(field) != sha256_hexdigest(document):
            raise ValueError(f"Strategy/Product Registry source drift: {field}")
    research = oracle["research_and_product_evidence"]
    records = registry.get("strategy_records")
    if not isinstance(records, list) or len(records) != 14:
        raise ValueError("Strategy Registry must contain 14 historical versions")
    frozen = {
        row["compiled_strategy_version_id"]: row
        for row in research["compiled_strategy_versions"]
    }
    if {row["legacy_identity"]["compiled_strategy_version_id"] for row in records} != set(
        frozen
    ):
        raise ValueError("Strategy Registry historical coverage drift")
    if Counter(
        row["legacy_identity"]["strategy_family_key"] for row in records
    ) != {"multi_etf_top_k": 4, "us_large_cap_top_k": 10}:
        raise ValueError("Strategy Registry family distribution drift")
    variants = {
        row["variant_key"]: row for row in strategy_catalog["strategies"]
    }
    defenses = {
        row["variant_key"]: row for row in defense_catalog["defenses"]
    }
    aggregations = {row["family_key"] for row in aggregation_catalog["families"]}
    mapped_signals = {
        row["mapping"]["variant_key"]
        for row in signal_registry["records"]
        if row["component_kind"] == "signal_version"
    }
    for record in records:
        legacy = record["legacy_identity"]
        expected = frozen[legacy["compiled_strategy_version_id"]]
        for field in (
            "artifact_id",
            "semantic_fingerprint",
            "content_hash",
            "strategy_fingerprint",
            "rule_graph",
        ):
            if legacy[field] != expected[field]:
                raise ValueError(f"Strategy Oracle drift: {legacy['branch_key']}")
        mapping = record["strategy_mapping"]
        if mapping["variant_key"] not in variants:
            raise ValueError(f"Unknown Strategy Variant: {mapping['variant_key']}")
        variant = variants[mapping["variant_key"]]
        parameters = variant["parameters"]
        if (
            mapping["target_k"] not in parameters["allowed_k"]
            or mapping["frequency"] not in parameters["allowed_frequency"]
        ):
            raise ValueError("Strategy mapping violates Variant parameters")
        expected_variant = {
            "multi_etf_top_k": "cross_section_rank_top_k_parity",
            "us_large_cap_top_k": "cross_section_rank_top_k_large_cap_parity",
        }[legacy["strategy_family_key"]]
        if mapping["variant_key"] != expected_variant:
            raise ValueError("Strategy legacy family mapped to the wrong Variant")
        if record["model_mapping"]["family_key"] not in aggregations:
            raise ValueError("Strategy references unknown Aggregation Family")
        if not set(record["model_mapping"]["input_signal_variant_keys"]) <= mapped_signals:
            raise ValueError("Strategy references unknown Signal Variant")
    baselines = {row["legacy_key"]: row for row in registry["defense_baselines"]}
    if set(baselines) != {"none", "fixed_20", "internal_timing_v1"}:
        raise ValueError("Defense baseline coverage drift")
    if baselines["none"]["mapping"] is not None:
        raise ValueError("Defense none must remain nullable")
    for key in ("fixed_20", "internal_timing_v1"):
        mapping = baselines[key]["mapping"]
        variant = defenses.get(mapping["variant_key"])
        if variant is None:
            raise ValueError(f"Unknown Defense Variant: {mapping['variant_key']}")
        if mapping["version_number"] != variant["version_number"]:
            raise ValueError(f"Defense Version drift: {mapping['variant_key']}")
    if Counter(row["defense_mapping_key"] for row in records) != {
        "none": 8,
        "fixed_20": 6,
    }:
        raise ValueError("Historical Defense distribution drift")
    products = registry.get("product_records")
    if not isinstance(products, list) or len(products) != 1:
        raise ValueError("Product Registry must contain the one frozen Product")
    product = products[0]
    frozen_product = research["active_products"][0]
    for field in (
        "artifact_id",
        "semantic_fingerprint",
        "content_hash",
        "product_key",
        "version_number",
        "product_fingerprint",
        "strategy_fingerprint",
        "compiled_model_instance_id",
        "slot_assignments",
        "specification_fingerprint",
        "lifecycle",
        "health",
    ):
        if product["legacy_identity"][field] != frozen_product[field]:
            raise ValueError(f"Active Product identity drift: {field}")
    referenced_strategy = next(
        row
        for row in records
        if row["legacy_identity"]["compiled_strategy_version_id"]
        == product["legacy_identity"]["compiled_strategy_version_id"]
    )
    if (
        product["aggregation_mapping"] != referenced_strategy["model_mapping"]
        or product["strategy_mapping"] != referenced_strategy["strategy_mapping"]
        or product["defense_mapping_key"]
        != referenced_strategy["defense_mapping_key"]
    ):
        raise ValueError("Active Product mapped reference chain drift")
    closure = product["lineage_closure"]
    if closure != {
        "artifact_count": len(research["active_product_artifact_closure"]),
        "artifact_closure_fingerprint": sha256_hexdigest(
            research["active_product_artifact_closure"]
        ),
        "dependency_edge_count": len(research["active_product_dependency_edges"]),
        "dependency_edges_fingerprint": sha256_hexdigest(
            research["active_product_dependency_edges"]
        ),
    }:
        raise ValueError("Active Product lineage closure drift")


def strategy_product_summary(registry: Mapping[str, Any]) -> dict[str, Any]:
    records = registry["strategy_records"]
    return {
        "strategy_version_count": len(records),
        "legacy_strategy_family_distribution": dict(
            sorted(
                Counter(
                    row["legacy_identity"]["strategy_family_key"]
                    for row in records
                ).items()
            )
        ),
        "defense_distribution": dict(
            sorted(Counter(row["defense_mapping_key"] for row in records).items())
        ),
        "product_version_count": len(registry["product_records"]),
        "active_product_count": sum(
            row["legacy_identity"]["lifecycle"] == "active"
            for row in registry["product_records"]
        ),
        "registry_fingerprint": registry.get("registry_fingerprint"),
    }


def _strategy_mapping(frozen: Mapping[str, Any]) -> dict[str, Any]:
    legacy_family = frozen["strategy_family_key"]
    variant = {
        "multi_etf_top_k": "cross_section_rank_top_k_parity",
        "us_large_cap_top_k": "cross_section_rank_top_k_large_cap_parity",
    }[legacy_family]
    parameters = frozen["rule_graph"]["parameters"]
    return {
        "family_key": "cross_section_rank_top_k",
        "variant_key": variant,
        "version_number": 1,
        "frequency": frozen["rule_graph"]["frequency"],
        "target_k": parameters["target_k"],
        "selection_buffer": parameters["selection_buffer"],
        "sector_cap": parameters["sector_cap"],
    }


def _model_mapping(
    model: Mapping[str, Any], signal_mapping: Mapping[str, str]
) -> dict[str, Any]:
    family, preset = {
        "single_signal__identity_v1": ("single_signal_identity", None),
        "linear_weighted__signal_equal_v1": (
            "flat_equal_weight_mean",
            "signal_equal_v1",
        ),
        "linear_weighted__dimension_equal_v1": (
            "hierarchical_weighted_mean",
            "legacy_dimension_equal_v1",
        ),
    }[model["preset_key"]]
    legacy_inputs = [
        key
        for slot in model["slot_assignments"]
        for key in slot["signal_version_keys"]
    ]
    return {
        "legacy_compiled_model_instance_id": model["compiled_model_instance_id"],
        "legacy_instance_fingerprint": model["instance_fingerprint"],
        "family_key": family,
        "parameter_preset_key": preset,
        "legacy_input_signal_keys": legacy_inputs,
        "input_signal_variant_keys": [signal_mapping[key] for key in legacy_inputs],
        "legacy_target_key": model["target_key"],
    }


def _defense_key(key: str) -> str:
    return {"none": "none", "fixed_20": "fixed_20"}[key]


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value
