from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.contracts import (
    AggregationCatalog,
    CatalogBundle,
    CatalogReleaseManifest,
    DefenseCatalog,
    PayloadCatalog,
    ProcessingCatalog,
    RawInputCatalog,
    StrategyCatalog,
)

CatalogModel = TypeVar("CatalogModel", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class LoadedCatalogRelease:
    manifest_path: Path
    catalog_root: Path
    bundle: CatalogBundle
    source_documents: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CatalogComponentPlan:
    component_kind: str
    component_key: str
    component_version: int
    source_fingerprint: str


@dataclass(frozen=True, slots=True)
class CatalogDiff:
    added: tuple[CatalogComponentPlan, ...]
    removed: tuple[CatalogComponentPlan, ...]
    changed: tuple[tuple[CatalogComponentPlan, CatalogComponentPlan], ...]
    unchanged: tuple[CatalogComponentPlan, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": [asdict(item) for item in self.added],
            "removed": [asdict(item) for item in self.removed],
            "changed": [
                {"before": asdict(before), "after": asdict(after)}
                for before, after in self.changed
            ],
            "unchanged": [asdict(item) for item in self.unchanged],
        }


def load_catalog_release(manifest_path: Path) -> LoadedCatalogRelease:
    resolved_manifest = manifest_path.resolve()
    raw_manifest = _read_json(resolved_manifest)
    manifest = CatalogReleaseManifest.model_validate(raw_manifest)
    catalog_root = resolved_manifest.parent.parent
    models: dict[str, BaseModel] = {}
    source_documents: dict[str, Any] = {
        resolved_manifest.relative_to(catalog_root).as_posix(): raw_manifest
    }
    validators: dict[str, type[BaseModel]] = {
        "payload": PayloadCatalog,
        "raw_inputs": RawInputCatalog,
        "processing": ProcessingCatalog,
        "aggregation": AggregationCatalog,
        "strategy": StrategyCatalog,
        "defense": DefenseCatalog,
    }
    for reference in manifest.files:
        path = (catalog_root / Path(reference.path)).resolve()
        if not path.is_relative_to(catalog_root):
            raise ValueError(f"Catalog file escapes catalog root: {reference.path}")
        raw_document = _read_json(path)
        if reference.component_group == "payload":
            models[reference.component_group] = _resolve_payload_catalog(
                raw_document, catalog_root, source_documents
            )
        elif reference.component_group == "aggregation":
            models[reference.component_group] = _resolve_aggregation_catalog(
                raw_document, catalog_root, source_documents
            )
        else:
            models[reference.component_group] = validators[
                reference.component_group
            ].model_validate(raw_document)
        source_documents[reference.path] = raw_document
    source_manifest_hash = sha256_hexdigest(
        [
            {"path": path, "document": source_documents[path]}
            for path in sorted(source_documents)
        ]
    )
    bundle = CatalogBundle(
        release=manifest,
        payload=cast(PayloadCatalog, models["payload"]),
        raw_inputs=cast(RawInputCatalog, models["raw_inputs"]),
        processing=cast(ProcessingCatalog, models["processing"]),
        aggregation=cast(AggregationCatalog, models["aggregation"]),
        strategy=cast(StrategyCatalog, models["strategy"]),
        defense=cast(DefenseCatalog, models["defense"]),
        source_manifest_hash=source_manifest_hash,
    )
    return LoadedCatalogRelease(resolved_manifest, catalog_root, bundle, source_documents)


def _resolve_payload_catalog(
    document: Any,
    catalog_root: Path,
    source_documents: dict[str, Any],
) -> PayloadCatalog:
    catalog = PayloadCatalog.model_validate(document)
    contracts = list(catalog.contracts)
    encodings = list(catalog.encodings)
    visited: set[str] = set()
    pending = list(catalog.extends)
    while pending:
        reference = pending.pop(0)
        if reference in visited:
            raise ValueError(f"Payload Catalog inheritance cycle or duplicate: {reference}")
        visited.add(reference)
        path = (catalog_root / Path(reference)).resolve()
        if not path.is_relative_to(catalog_root):
            raise ValueError(f"Payload Catalog extends escapes root: {reference}")
        inherited_document = _read_json(path)
        inherited = PayloadCatalog.model_validate(inherited_document)
        source_documents[reference] = inherited_document
        contracts = list(inherited.contracts) + contracts
        encodings = list(inherited.encodings) + encodings
        pending = list(inherited.extends) + pending
    return PayloadCatalog(
        catalog_type="v022_payload",
        catalog_version=catalog.catalog_version,
        contracts=contracts,
        encodings=encodings,
    )


def _resolve_aggregation_catalog(
    document: Any,
    catalog_root: Path,
    source_documents: dict[str, Any],
) -> AggregationCatalog:
    catalog = AggregationCatalog.model_validate(document)
    families = list(catalog.families)
    taxonomy = catalog.feature_taxonomy
    visited: set[str] = set()
    pending = list(catalog.extends)
    while pending:
        reference = pending.pop(0)
        if reference in visited:
            raise ValueError(f"Aggregation Catalog inheritance cycle or duplicate: {reference}")
        visited.add(reference)
        path = (catalog_root / Path(reference)).resolve()
        if not path.is_relative_to(catalog_root):
            raise ValueError(f"Aggregation Catalog extends escapes root: {reference}")
        inherited_document = _read_json(path)
        inherited = AggregationCatalog.model_validate(inherited_document)
        source_documents[reference] = inherited_document
        families = list(inherited.families) + families
        if taxonomy is None:
            taxonomy = inherited.feature_taxonomy
        elif (
            inherited.feature_taxonomy is not None
            and inherited.feature_taxonomy != taxonomy
        ):
            raise ValueError("Aggregation Catalog inheritance changes Feature Taxonomy")
        pending = list(inherited.extends) + pending
    return AggregationCatalog(
        catalog_type="v022_aggregation",
        catalog_version=catalog.catalog_version,
        feature_taxonomy=taxonomy,
        families=families,
    )


def lint_catalog_release(manifest_path: Path) -> dict[str, Any]:
    loaded = load_catalog_release(manifest_path)
    plan = catalog_component_plan(loaded.bundle)
    return {
        "status": "passed",
        "release_key": loaded.bundle.release.release_key,
        "catalog_version": loaded.bundle.release.catalog_version,
        "source_manifest_hash": loaded.bundle.source_manifest_hash,
        "component_count": len(plan),
        "checks": {
            "strict_contracts": True,
            "source_paths_confined": True,
            "references_resolved": True,
            "processing_edges_adjacent": True,
            "fixed_required_bindings": True,
            "deterministic_axes_are_conditional": True,
            "raw_input_count_is_nine": True,
            "deterministic_family_set_is_exact": True,
        },
    }


def catalog_component_plan(bundle: CatalogBundle) -> tuple[CatalogComponentPlan, ...]:
    components: dict[tuple[str, str, int], CatalogComponentPlan] = {}

    def add(kind: str, key: str, version: int, payload: Any) -> None:
        identity = (kind, key, version)
        component = CatalogComponentPlan(kind, key, version, sha256_hexdigest(payload))
        previous = components.setdefault(identity, component)
        if previous.source_fingerprint != component.source_fingerprint:
            raise ValueError(f"Catalog component identity drift: {identity}")

    for contract in bundle.payload.contracts:
        payload = contract.model_dump(mode="json")
        add("payload_contract_family", contract.contract_key, 1, _family_payload(payload))
        add("payload_contract_version", contract.contract_key, contract.version_number, payload)
    for encoding in bundle.payload.encodings:
        add(
            "physical_encoding_version",
            encoding.encoding_key,
            encoding.version_number,
            encoding.model_dump(mode="json"),
        )
    for raw in bundle.raw_inputs.raw_inputs:
        payload = raw.model_dump(mode="json")
        add("feature_family", raw.family_key, 1, _raw_family_payload(payload))
        add("feature_variant", raw.variant_key, 1, _raw_variant_payload(payload))
        add("feature_version", raw.variant_key, 1, payload)
    for node in bundle.processing.nodes:
        payload = node.model_dump(mode="json")
        add("processing_node_definition", node.node_key, 1, _node_family_payload(payload))
        add("processing_node_variant", node.variant_key, 1, _node_variant_payload(payload))
        add(
            "processing_node_version",
            node.variant_key,
            node.version_number,
            node.model_dump(mode="json", exclude={"output_features"}),
        )
        for output in node.output_features:
            family_payload = {
                "family_key": output.family_key,
                "name": output.name,
                "formula_identity": output.formula_identity,
                "input_roles": [item.binding_role for item in node.input_bindings],
                "output_semantics": output.output_semantics,
                "direction": output.direction,
                "research_hypothesis": output.research_hypothesis,
            }
            add("feature_family", output.family_key, 1, family_payload)
            add(
                "feature_variant",
                output.variant_key,
                1,
                {
                    "variant_key": output.variant_key,
                    "parameters": output.parameters,
                    "research_tier": output.research_tier,
                },
            )
            add(
                "feature_version",
                output.variant_key,
                node.version_number,
                {
                    **output.model_dump(mode="json"),
                    "origin_stage": node.stage_no,
                    "node_variant_key": node.variant_key,
                },
            )
    for family in bundle.aggregation.families:
        payload = family.model_dump(mode="json")
        add("aggregation_family", family.family_key, 1, _aggregation_family_payload(payload))
        add("aggregation_version", family.family_key, family.version_number, payload)
        for preset in family.parameter_presets:
            add(
                "aggregation_parameter_preset_definition",
                f"{family.family_key}__{preset.preset_key}",
                1,
                {
                    "preset_key": preset.preset_key,
                    "name": preset.name,
                    "description": preset.description,
                },
            )
            add(
                "aggregation_parameter_preset_version",
                f"{family.family_key}__{preset.preset_key}",
                preset.version_number,
                preset.model_dump(mode="json"),
            )
        for target in family.targets:
            add(
                "aggregation_target_definition",
                f"{family.family_key}__{target.target_key}",
                1,
                {
                    "target_key": target.target_key,
                    "name": target.name,
                    "description": target.description,
                },
            )
            add(
                "aggregation_target_version",
                f"{family.family_key}__{target.target_key}",
                target.version_number,
                target.model_dump(mode="json"),
            )
        for preset in family.training_presets:
            add(
                "aggregation_training_preset_definition",
                f"{family.family_key}__{preset.preset_key}",
                1,
                {
                    "preset_key": preset.preset_key,
                    "name": preset.name,
                    "description": preset.description,
                },
            )
            add(
                "aggregation_training_preset_version",
                f"{family.family_key}__{preset.preset_key}",
                preset.version_number,
                preset.model_dump(mode="json"),
            )
    if bundle.aggregation.feature_taxonomy is not None:
        taxonomy = bundle.aggregation.feature_taxonomy
        add(
            "aggregation_feature_taxonomy_version",
            taxonomy.taxonomy_key,
            taxonomy.version_number,
            taxonomy.model_dump(mode="json"),
        )
    for strategy in bundle.strategy.strategies:
        payload = strategy.model_dump(mode="json")
        add("strategy_family", strategy.family_key, 1, _strategy_family_payload(payload))
        add("strategy_variant", strategy.variant_key, 1, _strategy_variant_payload(payload))
        add("strategy_version", strategy.variant_key, strategy.version_number, payload)
    for strategy_preset in bundle.strategy.parameter_presets:
        key = (
            f"{strategy_preset.strategy_variant_key}__{strategy_preset.preset_key}"
        )
        add(
            "strategy_parameter_preset_definition",
            key,
            1,
            {
                "strategy_variant_key": strategy_preset.strategy_variant_key,
                "preset_key": strategy_preset.preset_key,
                "name": strategy_preset.name,
                "description": strategy_preset.description,
            },
        )
        add(
            "strategy_parameter_preset_version",
            key,
            strategy_preset.version_number,
            strategy_preset.model_dump(mode="json"),
        )
    for timing in bundle.defense.timing_policies:
        payload = timing.model_dump(mode="json")
        add(
            "defense_timing_family",
            timing.family_key,
            1,
            _defense_policy_family_payload(payload),
        )
        add(
            "defense_timing_variant",
            timing.variant_key,
            1,
            _defense_timing_variant_payload(payload),
        )
        add(
            "defense_timing_version",
            timing.variant_key,
            timing.version_number,
            payload,
        )
    for allocation in bundle.defense.allocation_policies:
        payload = allocation.model_dump(mode="json")
        add(
            "defense_allocation_family",
            allocation.family_key,
            1,
            _defense_policy_family_payload(payload),
        )
        add(
            "defense_allocation_variant",
            allocation.variant_key,
            1,
            _defense_allocation_variant_payload(payload),
        )
        add(
            "defense_allocation_version",
            allocation.variant_key,
            allocation.version_number,
            payload,
        )
    for defense in bundle.defense.defenses:
        payload = defense.model_dump(mode="json", exclude_none=True)
        add("defense_family", defense.family_key, 1, _defense_family_payload(payload))
        add("defense_variant", defense.variant_key, 1, _defense_variant_payload(payload))
        add("defense_version", defense.variant_key, defense.version_number, payload)
    return tuple(
        sorted(
            components.values(),
            key=lambda item: (
                item.component_kind,
                item.component_key,
                item.component_version,
            ),
        )
    )


def diff_catalog_releases(before_path: Path, after_path: Path) -> CatalogDiff:
    before = _plan_by_identity(catalog_component_plan(load_catalog_release(before_path).bundle))
    after = _plan_by_identity(catalog_component_plan(load_catalog_release(after_path).bundle))
    before_keys = set(before)
    after_keys = set(after)
    added = tuple(after[key] for key in sorted(after_keys - before_keys))
    removed = tuple(before[key] for key in sorted(before_keys - after_keys))
    changed: list[tuple[CatalogComponentPlan, CatalogComponentPlan]] = []
    unchanged: list[CatalogComponentPlan] = []
    for key in sorted(before_keys & after_keys):
        if before[key].source_fingerprint == after[key].source_fingerprint:
            unchanged.append(after[key])
        else:
            changed.append((before[key], after[key]))
    return CatalogDiff(added, removed, tuple(changed), tuple(unchanged))


def _plan_by_identity(
    plan: tuple[CatalogComponentPlan, ...],
) -> dict[tuple[str, str, int], CatalogComponentPlan]:
    return {
        (item.component_kind, item.component_key, item.component_version): item
        for item in plan
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Catalog file does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON Catalog file {path}: {error}") from error


def _family_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in ("contract_key", "name", "semantic_role", "description")}


def _raw_family_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "family_key",
            "name",
            "formula_identity",
            "semantic_role",
            "direction",
            "research_hypothesis",
        )
    }


def _raw_variant_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "variant_key": payload["variant_key"],
        "source_series_key": payload["source_series_key"],
        "source_field": payload["source_field"],
        "unit": payload["unit"],
    }


def _node_family_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_key": payload["node_key"],
        "name": payload["name"],
        "algorithm_identity": payload["algorithm_identity"],
        "description": f"Published v0.22 Processing Node for stage {payload['stage_no']}.",
    }


def _node_variant_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {"variant_key": payload["variant_key"], "parameters": payload["parameters"]}


def _aggregation_family_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "family_key",
            "name",
            "algorithm_identity",
            "objective_semantics",
            "output_semantics",
        )
    }


def _strategy_family_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "family_key",
            "name",
            "selection_semantics",
            "research_hypothesis",
        )
    }


def _strategy_variant_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {"variant_key": payload["variant_key"], "parameters": payload["parameters"]}


def _defense_policy_family_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "family_key",
            "name",
            "formula_identity",
            "research_hypothesis",
        )
    }


def _defense_timing_variant_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {"variant_key": payload["variant_key"], "rule": payload["rule"]}


def _defense_allocation_variant_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "variant_key",
            "asset_registry_catalog_version",
            "asset_set_key",
            "reserve_return_model_ref",
            "members",
        )
    }


def _defense_family_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "family_key",
            "name",
            "allocation_semantics",
            "research_hypothesis",
        )
    }


def _defense_variant_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {"variant_key": payload["variant_key"], "parameters": payload["parameters"]}
