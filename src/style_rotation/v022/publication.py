from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult
from style_rotation.v022.catalog import LoadedCatalogRelease, load_catalog_release
from style_rotation.v022.contracts import (
    NodeOutputFeatureSeed,
    PayloadContractSeed,
    PhysicalEncodingSeed,
    ProcessingNodeSeed,
)


@dataclass(frozen=True, slots=True)
class PublishedCatalogComponent:
    component_kind: str
    component_key: str
    component_version: int
    artifact_id: uuid.UUID
    semantic_fingerprint: str
    content_hash: str
    reused: bool


@dataclass(frozen=True, slots=True)
class CatalogPublicationContext:
    actor_key: str
    reviewer_actor: str
    trusted_local_authorization_bootstrap: bool = False


@dataclass(frozen=True, slots=True)
class CatalogPublication:
    catalog_release_id: uuid.UUID
    release_artifact_id: uuid.UUID
    evidence_artifact_id: uuid.UUID
    release_fingerprint: str
    source_manifest_hash: str
    component_count: int
    reused_component_count: int
    release_reused: bool
    evidence_reused: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("catalog_release_id", "release_artifact_id", "evidence_artifact_id"):
            payload[key] = str(payload[key])
        return payload


def publish_catalog_release(
    engine: Engine,
    manifest_path: Path,
    *,
    context: CatalogPublicationContext,
) -> CatalogPublication:
    loaded = load_catalog_release(manifest_path)
    if context.actor_key != loaded.bundle.release.publisher_actor:
        raise ValueError("Authenticated publisher does not match the Release policy")
    if context.reviewer_actor != loaded.bundle.release.reviewer_actor:
        raise ValueError("Authenticated reviewer does not match the Release policy")
    now = datetime.now(UTC)
    with engine.begin() as connection:
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        components, contract_versions = _publish_payload(service, connection, loaded)
        components.extend(_publish_raw_inputs(service, connection, loaded, contract_versions))
        components.extend(_publish_processing(service, connection, loaded, contract_versions))
        components.extend(_publish_aggregation(service, connection, loaded, contract_versions))
        components.extend(_publish_strategies(service, connection, loaded, contract_versions))
        components.extend(_publish_defenses(service, connection, loaded))
        ordered = tuple(
            sorted(
                components,
                key=lambda item: (
                    item.component_kind,
                    item.component_key,
                    item.component_version,
                ),
            )
        )
        authorization_id = _publisher_authorization(
            connection,
            context.actor_key,
            now,
            allow_bootstrap=context.trusted_local_authorization_bootstrap,
        )
        release_fingerprint = _release_fingerprint(loaded, ordered)
        version = semantic_version_number(loaded.bundle.release.catalog_version)
        release_result = service.publish(
            artifact_type="v022_catalog_release",
            artifact_key=loaded.bundle.release.release_key,
            version_number=version,
            semantic_payload={
                "contract_version": loaded.bundle.release.contract_version,
                "processing_stage_count": loaded.bundle.release.processing_stage_count,
                "release_fingerprint": release_fingerprint,
                "source_manifest_hash": loaded.bundle.source_manifest_hash,
            },
            content_payload={
                "components": [
                    {
                        "kind": item.component_kind,
                        "key": item.component_key,
                        "version": item.component_version,
                        "semantic_fingerprint": item.semantic_fingerprint,
                    }
                    for item in ordered
                ]
            },
            dependencies=tuple(
                DependencyInput(item.artifact_id, f"component:{item.component_kind}", ordinal)
                for ordinal, item in enumerate(ordered)
            ),
            reason=f"publish v0.22 Catalog Release {loaded.bundle.release.catalog_version}",
            draft_writer=partial(
                _write_release,
                authorization_id=authorization_id,
                loaded=loaded,
                components=ordered,
                release_fingerprint=release_fingerprint,
                version=version,
                now=now,
            ),
        )
        release_id = cast(
            uuid.UUID,
            connection.execute(
                text(
                    "SELECT catalog_release_id FROM workspace.v022_catalog_release "
                    "WHERE artifact_id = :artifact_id"
                ),
                {"artifact_id": release_result.artifact_id},
            ).scalar_one(),
        )
        checks = _verification_checks(connection, release_id, loaded, ordered)
        if not all(checks.values()):
            failed = sorted(key for key, value in checks.items() if not value)
            raise ValueError(f"Published Catalog verification failed: {', '.join(failed)}")
        evidence_result = service.publish(
            artifact_type="v022_catalog_validation_evidence",
            artifact_key=loaded.bundle.release.release_key,
            version_number=version,
            semantic_payload={"evidence_kind": "rebuild_verify", "checks": checks},
            content_payload={
                "release_fingerprint": release_fingerprint,
                "source_manifest_hash": loaded.bundle.source_manifest_hash,
                "component_count": len(ordered),
            },
            dependencies=(DependencyInput(release_result.artifact_id, "catalog_release", 0),),
            reason=f"verify v0.22 Catalog Release {loaded.bundle.release.catalog_version}",
            draft_writer=partial(
                _write_evidence,
                release_id=release_id,
                loaded=loaded,
                checks=checks,
                now=now,
            ),
        )
    return CatalogPublication(
        catalog_release_id=release_id,
        release_artifact_id=release_result.artifact_id,
        evidence_artifact_id=evidence_result.artifact_id,
        release_fingerprint=release_fingerprint,
        source_manifest_hash=loaded.bundle.source_manifest_hash,
        component_count=len(ordered),
        reused_component_count=sum(item.reused for item in ordered),
        release_reused=release_result.reused,
        evidence_reused=evidence_result.reused,
    )


def verify_published_catalog(engine: Engine, release_artifact_id: uuid.UUID) -> dict[str, Any]:
    with engine.connect() as connection:
        release = (
            connection.execute(
                text(
                    """
                    SELECT r.catalog_release_id, r.release_key, r.version_number,
                           r.contract_version, r.processing_stage_count,
                           r.release_fingerprint, r.source_manifest_hash,
                           a.status artifact_status
                    FROM workspace.v022_catalog_release r
                    JOIN lineage.artifact a ON a.artifact_id = r.artifact_id
                    WHERE r.artifact_id = :artifact_id
                    """
                ),
                {"artifact_id": release_artifact_id},
            )
            .mappings()
            .one_or_none()
        )
        if release is None:
            raise ValueError(f"Unknown v0.22 Catalog Release artifact: {release_artifact_id}")
        members = _release_members(connection, release["catalog_release_id"])
        rebuilt_fingerprint = sha256_hexdigest(
            {
                "contract_version": release["contract_version"],
                "processing_stage_count": release["processing_stage_count"],
                "source_manifest_hash": release["source_manifest_hash"],
                "components": [
                    {
                        "kind": item["component_kind"],
                        "key": item["component_key"],
                        "version": item["component_version"],
                        "semantic_fingerprint": item["component_fingerprint"],
                    }
                    for item in members
                ],
            }
        )
        invalid_members = [item for item in members if item["artifact_status"] != "published"]
        evidence = (
            connection.execute(
                text(
                    "SELECT passed, checks FROM workspace.v022_catalog_validation_evidence "
                    "WHERE catalog_release_id = :release_id AND evidence_kind = 'rebuild_verify'"
                ),
                {"release_id": release["catalog_release_id"]},
            )
            .mappings()
            .one_or_none()
        )
        checks = {
            "release_artifact_published": release["artifact_status"] == "published",
            "component_membership_nonempty": bool(members),
            "all_components_published": not invalid_members,
            "release_fingerprint_rebuilt": rebuilt_fingerprint == release["release_fingerprint"],
            "validation_evidence_passed": evidence is not None and evidence["passed"],
        }
        return {
            "status": "passed" if all(checks.values()) else "failed",
            "catalog_release_id": str(release["catalog_release_id"]),
            "release_artifact_id": str(release_artifact_id),
            "release_key": release["release_key"],
            "release_fingerprint": release["release_fingerprint"],
            "source_manifest_hash": release["source_manifest_hash"],
            "component_count": len(members),
            "checks": checks,
            "components": [dict(item) for item in members],
        }


def _publish_payload(
    service: ArtifactService,
    connection: Connection,
    loaded: LoadedCatalogRelease,
) -> tuple[list[PublishedCatalogComponent], dict[str, tuple[uuid.UUID, uuid.UUID]]]:
    components: list[PublishedCatalogComponent] = []
    versions: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
    for seed in loaded.bundle.payload.contracts:
        family_payload = {
            "contract_key": seed.contract_key,
            "name": seed.name,
            "semantic_role": seed.semantic_role,
            "description": seed.description,
        }
        family = service.publish(
            artifact_type="v022_payload_contract_family",
            artifact_key=seed.contract_key,
            version_number=1,
            semantic_payload=family_payload,
            content_payload=family_payload,
            reason=f"publish Payload Contract Family {seed.contract_key}",
            draft_writer=partial(_write_payload_family, seed=seed),
        )
        components.append(_component("payload_contract_family", seed.contract_key, 1, family))
        family_id = _id_for_artifact(
            connection,
            "data.payload_contract_family",
            "payload_contract_family_id",
            family.artifact_id,
        )
        payload = seed.model_dump(mode="json")
        version = service.publish(
            artifact_type="v022_payload_contract_version",
            artifact_key=seed.contract_key,
            version_number=seed.version_number,
            semantic_payload=payload,
            content_payload=payload,
            dependencies=(DependencyInput(family.artifact_id, "contract_family", 0),),
            reason=f"publish Payload Contract Version {seed.contract_key} v{seed.version_number}",
            draft_writer=partial(_write_payload_version, family_id=family_id, seed=seed),
        )
        components.append(
            _component(
                "payload_contract_version",
                seed.contract_key,
                seed.version_number,
                version,
            )
        )
        version_id = _id_for_artifact(
            connection,
            "data.payload_contract_version",
            "payload_contract_version_id",
            version.artifact_id,
        )
        versions[seed.contract_key] = (version_id, version.artifact_id)
    for encoding in loaded.bundle.payload.encodings:
        payload = encoding.model_dump(mode="json")
        result = service.publish(
            artifact_type="v022_physical_encoding_version",
            artifact_key=encoding.encoding_key,
            version_number=encoding.version_number,
            semantic_payload=payload,
            content_payload=payload,
            reason=(
                f"publish Physical Encoding {encoding.encoding_key} v{encoding.version_number}"
            ),
            draft_writer=partial(_write_encoding, seed=encoding),
        )
        components.append(
            _component(
                "physical_encoding_version",
                encoding.encoding_key,
                encoding.version_number,
                result,
            )
        )
    return components, versions


def _publish_raw_inputs(
    service: ArtifactService,
    connection: Connection,
    loaded: LoadedCatalogRelease,
    contract_versions: dict[str, tuple[uuid.UUID, uuid.UUID]],
) -> list[PublishedCatalogComponent]:
    components: list[PublishedCatalogComponent] = []
    for seed in loaded.bundle.raw_inputs.raw_inputs:
        family_payload = {
            "family_key": seed.family_key,
            "name": seed.name,
            "formula_identity": seed.formula_identity,
            "input_roles": [{"role": "raw_source", "series_key": seed.source_series_key}],
            "output_semantics": {
                "semantic_role": seed.semantic_role,
                "unit": seed.unit,
            },
            "direction": seed.direction,
            "research_hypothesis": seed.research_hypothesis,
        }
        family = service.publish(
            artifact_type="v022_feature_family",
            artifact_key=seed.family_key,
            version_number=1,
            semantic_payload=family_payload,
            content_payload=family_payload,
            reason=f"publish Raw Feature Family {seed.family_key}",
            draft_writer=partial(_write_feature_family, payload=family_payload),
        )
        components.append(_component("feature_family", seed.family_key, 1, family))
        family_id = _id_for_artifact(
            connection,
            "processing.feature_family",
            "feature_family_id",
            family.artifact_id,
        )
        variant_payload = {
            "variant_key": seed.variant_key,
            "parameters": {
                "source_series_key": seed.source_series_key,
                "source_field": seed.source_field,
                "unit": seed.unit,
            },
            "research_tier": "raw",
        }
        variant = service.publish(
            artifact_type="v022_feature_variant",
            artifact_key=seed.variant_key,
            version_number=1,
            semantic_payload=variant_payload,
            content_payload=variant_payload,
            dependencies=(DependencyInput(family.artifact_id, "feature_family", 0),),
            reason=f"publish Raw Feature Variant {seed.variant_key}",
            draft_writer=partial(
                _write_feature_variant,
                family_id=family_id,
                payload=variant_payload,
            ),
        )
        components.append(_component("feature_variant", seed.variant_key, 1, variant))
        variant_id = _id_for_artifact(
            connection,
            "processing.feature_variant",
            "feature_variant_id",
            variant.artifact_id,
        )
        contract_id, contract_artifact_id = contract_versions[seed.payload_contract_key]
        version_payload = seed.model_dump(mode="json")
        version = service.publish(
            artifact_type="v022_feature_version",
            artifact_key=seed.variant_key,
            version_number=1,
            semantic_payload=version_payload,
            content_payload=version_payload,
            dependencies=(
                DependencyInput(variant.artifact_id, "feature_variant", 0),
                DependencyInput(contract_artifact_id, "payload_contract", 1),
            ),
            reason=f"publish Raw Feature Version {seed.variant_key} v1",
            draft_writer=partial(
                _write_raw_feature_version,
                variant_id=variant_id,
                contract_id=contract_id,
                variant_key=seed.variant_key,
                aggregation_readiness=seed.aggregation_readiness,
                payload=version_payload,
            ),
        )
        components.append(_component("feature_version", seed.variant_key, 1, version))
    return components


def _publish_aggregation(
    service: ArtifactService,
    connection: Connection,
    loaded: LoadedCatalogRelease,
    contract_versions: dict[str, tuple[uuid.UUID, uuid.UUID]],
) -> list[PublishedCatalogComponent]:
    components: list[PublishedCatalogComponent] = []
    taxonomy_artifact: uuid.UUID | None = None
    taxonomy = loaded.bundle.aggregation.feature_taxonomy
    if taxonomy is not None:
        taxonomy_payload = taxonomy.model_dump(mode="json")
        taxonomy_publication = service.publish(
            artifact_type="v022_aggregation_feature_taxonomy_version",
            artifact_key=taxonomy.taxonomy_key,
            version_number=taxonomy.version_number,
            semantic_payload=taxonomy_payload,
            content_payload=taxonomy_payload,
            reason=(
                "publish Aggregation Feature Taxonomy "
                f"{taxonomy.taxonomy_key} v{taxonomy.version_number}"
            ),
            draft_writer=partial(
                _write_aggregation_feature_taxonomy,
                taxonomy=taxonomy,
                payload=taxonomy_payload,
            ),
        )
        taxonomy_artifact = taxonomy_publication.artifact_id
        components.append(
            _component(
                "aggregation_feature_taxonomy_version",
                taxonomy.taxonomy_key,
                taxonomy.version_number,
                taxonomy_publication,
            )
        )
    for seed in loaded.bundle.aggregation.families:
        family_payload = {
            "family_key": seed.family_key,
            "name": seed.name,
            "algorithm_identity": seed.algorithm_identity,
            "objective_semantics": seed.objective_semantics,
            "output_semantics": seed.output_semantics,
        }
        family = service.publish(
            artifact_type="v022_aggregation_family",
            artifact_key=seed.family_key,
            version_number=1,
            semantic_payload=family_payload,
            content_payload=family_payload,
            reason=f"publish Aggregation Family {seed.family_key}",
            draft_writer=partial(_write_aggregation_family, payload=family_payload),
        )
        components.append(_component("aggregation_family", seed.family_key, 1, family))
        family_id = _id_for_artifact(
            connection,
            "aggregation.aggregation_family",
            "aggregation_family_id",
            family.artifact_id,
        )
        input_contract_id, input_contract_artifact = contract_versions[
            seed.input_payload_contract_key
        ]
        output_contract_id, output_contract_artifact = contract_versions[
            seed.output_payload_contract_key
        ]
        version_payload = seed.model_dump(mode="json")
        version_dependencies = [
            DependencyInput(family.artifact_id, "aggregation_family", 0),
            DependencyInput(input_contract_artifact, "input_payload_contract", 1),
            DependencyInput(output_contract_artifact, "output_payload_contract", 2),
        ]
        if taxonomy_artifact is not None and seed.input_policy.get(
            "taxonomy_artifact_required"
        ):
            version_dependencies.append(
                DependencyInput(taxonomy_artifact, "feature_taxonomy", 3)
            )
        version = service.publish(
            artifact_type="v022_aggregation_version",
            artifact_key=seed.family_key,
            version_number=seed.version_number,
            semantic_payload=version_payload,
            content_payload=version_payload,
            dependencies=tuple(version_dependencies),
            reason=f"publish Aggregation Version {seed.family_key} v{seed.version_number}",
            draft_writer=partial(
                _write_aggregation_version,
                family_id=family_id,
                input_contract_id=input_contract_id,
                output_contract_id=output_contract_id,
                seed=seed,
                payload=version_payload,
            ),
        )
        components.append(
            _component("aggregation_version", seed.family_key, seed.version_number, version)
        )
        components.extend(
            _publish_definition_versions(
                service,
                connection,
                family_id,
                family.artifact_id,
                seed.family_key,
                "parameter_preset",
                seed.parameter_presets,
            )
        )
        components.extend(
            _publish_definition_versions(
                service,
                connection,
                family_id,
                family.artifact_id,
                seed.family_key,
                "target",
                seed.targets,
            )
        )
        components.extend(
            _publish_definition_versions(
                service,
                connection,
                family_id,
                family.artifact_id,
                seed.family_key,
                "training_preset",
                seed.training_presets,
            )
        )
    return components


def _publish_processing(
    service: ArtifactService,
    connection: Connection,
    loaded: LoadedCatalogRelease,
    contract_versions: dict[str, tuple[uuid.UUID, uuid.UUID]],
) -> list[PublishedCatalogComponent]:
    components: dict[tuple[str, str, int], PublishedCatalogComponent] = {}
    feature_versions = _published_feature_versions(connection)
    family_payloads: dict[str, dict[str, Any]] = {}
    definition_payloads: dict[str, dict[str, Any]] = {}
    for seed in sorted(
        loaded.bundle.processing.nodes,
        key=lambda item: (item.stage_no, item.node_key, item.variant_key),
    ):
        definition_payload = {
            "node_key": seed.node_key,
            "name": seed.name,
            "algorithm_identity": seed.algorithm_identity,
            "description": (f"Published v0.22 Processing Node for stage {seed.stage_no}."),
        }
        previous_definition = definition_payloads.setdefault(seed.node_key, definition_payload)
        if previous_definition != definition_payload:
            raise ValueError(f"Node Definition drift: {seed.node_key}")
        definition = service.publish(
            artifact_type="v022_processing_node_definition",
            artifact_key=seed.node_key,
            version_number=1,
            semantic_payload=definition_payload,
            content_payload=definition_payload,
            reason=f"publish Processing Node Definition {seed.node_key}",
            draft_writer=partial(_write_node_definition, payload=definition_payload),
        )
        _add_component(
            components,
            _component("processing_node_definition", seed.node_key, 1, definition),
        )
        definition_id = _id_for_artifact(
            connection,
            "processing.node_definition",
            "node_definition_id",
            definition.artifact_id,
        )
        variant_payload = {
            "variant_key": seed.variant_key,
            "parameters": seed.parameters,
        }
        variant = service.publish(
            artifact_type="v022_processing_node_variant",
            artifact_key=seed.variant_key,
            version_number=1,
            semantic_payload=variant_payload,
            content_payload=variant_payload,
            dependencies=(DependencyInput(definition.artifact_id, "node_definition", 0),),
            reason=f"publish Processing Node Variant {seed.variant_key}",
            draft_writer=partial(
                _write_node_variant,
                definition_id=definition_id,
                payload=variant_payload,
            ),
        )
        _add_component(
            components,
            _component("processing_node_variant", seed.variant_key, 1, variant),
        )
        variant_id = _id_for_artifact(
            connection,
            "processing.node_variant",
            "node_variant_id",
            variant.artifact_id,
        )
        source_bindings: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
        for binding in seed.input_bindings:
            source = feature_versions.get(binding.source_feature_variant_key)
            if source is None:
                raise ValueError(
                    f"Processing source not yet published: {binding.source_feature_variant_key}"
                )
            source_bindings[binding.input_port_key] = source
        version_payload = seed.model_dump(mode="json", exclude={"output_features"})
        version_dependencies = [DependencyInput(variant.artifact_id, "node_variant", 0)]
        version_dependencies.extend(
            DependencyInput(artifact_id, f"input:{port_key}", ordinal + 1)
            for ordinal, (port_key, (_, artifact_id)) in enumerate(sorted(source_bindings.items()))
        )
        node_version = service.publish(
            artifact_type="v022_processing_node_version",
            artifact_key=seed.variant_key,
            version_number=seed.version_number,
            semantic_payload=version_payload,
            content_payload=version_payload,
            dependencies=tuple(version_dependencies),
            reason=f"publish Processing Node Version {seed.variant_key}",
            draft_writer=partial(
                _write_node_version,
                variant_id=variant_id,
                seed=seed,
                source_bindings=source_bindings,
                contract_versions=contract_versions,
                payload=version_payload,
            ),
        )
        _add_component(
            components,
            _component(
                "processing_node_version",
                seed.variant_key,
                seed.version_number,
                node_version,
            ),
        )
        node_version_id = _id_for_artifact(
            connection,
            "processing.node_version",
            "node_version_id",
            node_version.artifact_id,
        )
        for output in seed.output_features:
            published = _publish_processing_feature(
                service,
                connection,
                seed,
                output,
                node_version,
                node_version_id,
                contract_versions,
                family_payloads,
            )
            for component in published:
                _add_component(components, component)
            feature_versions[output.variant_key] = (
                _id_for_artifact(
                    connection,
                    "processing.feature_version",
                    "feature_version_id",
                    published[-1].artifact_id,
                ),
                published[-1].artifact_id,
            )
    return list(components.values())


def _publish_processing_feature(
    service: ArtifactService,
    connection: Connection,
    node: ProcessingNodeSeed,
    output: NodeOutputFeatureSeed,
    node_version: PublicationResult,
    node_version_id: uuid.UUID,
    contract_versions: dict[str, tuple[uuid.UUID, uuid.UUID]],
    family_payloads: dict[str, dict[str, Any]],
) -> list[PublishedCatalogComponent]:
    family_payload = {
        "family_key": output.family_key,
        "name": output.name,
        "formula_identity": output.formula_identity,
        "input_roles": [item.binding_role for item in node.input_bindings],
        "output_semantics": output.output_semantics,
        "direction": output.direction,
        "research_hypothesis": output.research_hypothesis,
    }
    previous = family_payloads.setdefault(output.family_key, family_payload)
    if previous != family_payload:
        raise ValueError(f"Feature Family identity drift: {output.family_key}")
    family = service.publish(
        artifact_type="v022_feature_family",
        artifact_key=output.family_key,
        version_number=1,
        semantic_payload=family_payload,
        content_payload=family_payload,
        reason=f"publish Feature Family {output.family_key}",
        draft_writer=partial(_write_feature_family, payload=family_payload),
    )
    family_id = _id_for_artifact(
        connection,
        "processing.feature_family",
        "feature_family_id",
        family.artifact_id,
    )
    variant_payload = {
        "variant_key": output.variant_key,
        "parameters": output.parameters,
        "research_tier": output.research_tier,
    }
    variant = service.publish(
        artifact_type="v022_feature_variant",
        artifact_key=output.variant_key,
        version_number=1,
        semantic_payload=variant_payload,
        content_payload=variant_payload,
        dependencies=(DependencyInput(family.artifact_id, "feature_family", 0),),
        reason=f"publish Feature Variant {output.variant_key}",
        draft_writer=partial(
            _write_feature_variant,
            family_id=family_id,
            payload=variant_payload,
        ),
    )
    variant_id = _id_for_artifact(
        connection,
        "processing.feature_variant",
        "feature_variant_id",
        variant.artifact_id,
    )
    contract_id, contract_artifact = contract_versions[output.payload_contract_key]
    version_payload = {
        **output.model_dump(mode="json"),
        "origin_stage": node.stage_no,
        "node_variant_key": node.variant_key,
    }
    version = service.publish(
        artifact_type="v022_feature_version",
        artifact_key=output.variant_key,
        version_number=node.version_number,
        semantic_payload=version_payload,
        content_payload=version_payload,
        dependencies=(
            DependencyInput(variant.artifact_id, "feature_variant", 0),
            DependencyInput(node_version.artifact_id, "producer_node_version", 1),
            DependencyInput(contract_artifact, "payload_contract", 2),
        ),
        reason=f"publish Feature Version {output.variant_key}",
        draft_writer=partial(
            _write_processing_feature_version,
            variant_id=variant_id,
            node_version_id=node_version_id,
            contract_id=contract_id,
            node_stage=node.stage_no,
            output=output,
            payload=version_payload,
        ),
    )
    return [
        _component("feature_family", output.family_key, 1, family),
        _component("feature_variant", output.variant_key, 1, variant),
        _component("feature_version", output.variant_key, node.version_number, version),
    ]


def _published_feature_versions(
    connection: Connection,
) -> dict[str, tuple[uuid.UUID, uuid.UUID]]:
    return {
        row["variant_key"]: (row["feature_version_id"], row["artifact_id"])
        for row in connection.execute(
            text(
                """
                SELECT v.variant_key,f.feature_version_id,f.artifact_id
                FROM processing.feature_version f
                JOIN processing.feature_variant v ON v.feature_variant_id=f.feature_variant_id
                """
            )
        ).mappings()
    }


def _add_component(
    components: dict[tuple[str, str, int], PublishedCatalogComponent],
    component: PublishedCatalogComponent,
) -> None:
    key = (component.component_kind, component.component_key, component.component_version)
    previous = components.setdefault(key, component)
    if previous.semantic_fingerprint != component.semantic_fingerprint:
        raise ValueError(f"Catalog component identity drift: {key}")


def _publish_definition_versions(
    service: ArtifactService,
    connection: Connection,
    family_id: uuid.UUID,
    family_artifact_id: uuid.UUID,
    family_key: str,
    prefix: str,
    seeds: list[Any],
) -> list[PublishedCatalogComponent]:
    components: list[PublishedCatalogComponent] = []
    for seed in seeds:
        local_key = seed.target_key if prefix == "target" else seed.preset_key
        key = f"{family_key}__{local_key}"
        definition_payload = {
            f"{prefix}_key": key,
            "name": seed.name,
            "description": seed.description,
        }
        definition_kind = f"aggregation_{prefix}_definition"
        definition = service.publish(
            artifact_type=f"v022_{definition_kind}",
            artifact_key=key,
            version_number=1,
            semantic_payload=definition_payload,
            content_payload=definition_payload,
            dependencies=(DependencyInput(family_artifact_id, "aggregation_family", 0),),
            reason=f"publish {definition_kind} {key}",
            draft_writer=partial(
                _write_aggregation_axis_definition,
                family_id=family_id,
                prefix=prefix,
                payload=definition_payload,
            ),
        )
        components.append(_component(definition_kind, key, 1, definition))
        definition_id = _id_for_artifact(
            connection,
            f"aggregation.{prefix}_definition",
            f"{prefix}_definition_id",
            definition.artifact_id,
        )
        version_payload = seed.model_dump(mode="json")
        version_kind = f"aggregation_{prefix}_version"
        version = service.publish(
            artifact_type=f"v022_{version_kind}",
            artifact_key=key,
            version_number=seed.version_number,
            semantic_payload=version_payload,
            content_payload=version_payload,
            dependencies=(DependencyInput(definition.artifact_id, f"{prefix}_definition", 0),),
            reason=f"publish {version_kind} {key} v{seed.version_number}",
            draft_writer=partial(
                _write_aggregation_axis_version,
                definition_id=definition_id,
                prefix=prefix,
                version_number=seed.version_number,
                semantics=seed.semantics,
            ),
        )
        components.append(_component(version_kind, key, seed.version_number, version))
    return components


def _publish_strategies(
    service: ArtifactService,
    connection: Connection,
    loaded: LoadedCatalogRelease,
    contract_versions: dict[str, tuple[uuid.UUID, uuid.UUID]],
) -> list[PublishedCatalogComponent]:
    components: list[PublishedCatalogComponent] = []
    published_families: dict[str, tuple[dict[str, Any], PublicationResult]] = {}
    published_variants: dict[str, tuple[uuid.UUID, PublicationResult]] = {}
    for seed in loaded.bundle.strategy.strategies:
        family_payload = {
            "family_key": seed.family_key,
            "name": seed.name,
            "selection_semantics": seed.selection_semantics,
            "research_hypothesis": seed.research_hypothesis,
        }
        existing_family = published_families.get(seed.family_key)
        if existing_family is None:
            family = service.publish(
                artifact_type="v022_strategy_family",
                artifact_key=seed.family_key,
                version_number=1,
                semantic_payload=family_payload,
                content_payload=family_payload,
                reason=f"publish Strategy Family {seed.family_key}",
                draft_writer=partial(_write_strategy_family, payload=family_payload),
            )
            published_families[seed.family_key] = (family_payload, family)
            components.append(_component("strategy_family", seed.family_key, 1, family))
        else:
            expected_payload, family = existing_family
            if expected_payload != family_payload:
                raise ValueError(
                    f"Strategy Family semantics drift across variants: {seed.family_key}"
                )
        family_id = _id_for_artifact(
            connection,
            "strategy.v022_strategy_family",
            "strategy_family_id",
            family.artifact_id,
        )
        variant_payload = {"variant_key": seed.variant_key, "parameters": seed.parameters}
        variant = service.publish(
            artifact_type="v022_strategy_variant",
            artifact_key=seed.variant_key,
            version_number=1,
            semantic_payload=variant_payload,
            content_payload=variant_payload,
            dependencies=(DependencyInput(family.artifact_id, "strategy_family", 0),),
            reason=f"publish Strategy Variant {seed.variant_key}",
            draft_writer=partial(
                _write_strategy_variant,
                family_id=family_id,
                payload=variant_payload,
            ),
        )
        components.append(_component("strategy_variant", seed.variant_key, 1, variant))
        variant_id = _id_for_artifact(
            connection,
            "strategy.v022_strategy_variant",
            "strategy_variant_id",
            variant.artifact_id,
        )
        published_variants[seed.variant_key] = (variant_id, variant)
        contract_id, contract_artifact = contract_versions[seed.input_payload_contract_key]
        payload = seed.model_dump(mode="json")
        version = service.publish(
            artifact_type="v022_strategy_version",
            artifact_key=seed.variant_key,
            version_number=seed.version_number,
            semantic_payload=payload,
            content_payload=payload,
            dependencies=(
                DependencyInput(variant.artifact_id, "strategy_variant", 0),
                DependencyInput(contract_artifact, "input_payload_contract", 1),
            ),
            reason=f"publish Strategy Version {seed.variant_key} v{seed.version_number}",
            draft_writer=partial(
                _write_strategy_version,
                variant_id=variant_id,
                contract_id=contract_id,
                seed=seed,
                payload=payload,
            ),
        )
        components.append(
            _component("strategy_version", seed.variant_key, seed.version_number, version)
        )
    for strategy_preset in loaded.bundle.strategy.parameter_presets:
        variant_id, variant = published_variants[strategy_preset.strategy_variant_key]
        key = f"{strategy_preset.strategy_variant_key}__{strategy_preset.preset_key}"
        definition_payload = {
            "strategy_variant_key": strategy_preset.strategy_variant_key,
            "preset_key": strategy_preset.preset_key,
            "name": strategy_preset.name,
            "description": strategy_preset.description,
        }
        definition = service.publish(
            artifact_type="v022_strategy_parameter_preset_definition",
            artifact_key=key,
            version_number=1,
            semantic_payload=definition_payload,
            content_payload=definition_payload,
            dependencies=(DependencyInput(variant.artifact_id, "strategy_variant", 0),),
            reason=f"publish Strategy parameter preset definition {key}",
            draft_writer=partial(
                _write_strategy_parameter_preset_definition,
                variant_id=variant_id,
                payload=definition_payload,
            ),
        )
        components.append(_component("strategy_parameter_preset_definition", key, 1, definition))
        definition_id = _id_for_artifact(
            connection,
            "strategy.v022_strategy_parameter_preset_definition",
            "strategy_parameter_preset_definition_id",
            definition.artifact_id,
        )
        version_payload = strategy_preset.model_dump(mode="json")
        version = service.publish(
            artifact_type="v022_strategy_parameter_preset_version",
            artifact_key=key,
            version_number=strategy_preset.version_number,
            semantic_payload=version_payload,
            content_payload=version_payload,
            dependencies=(
                DependencyInput(definition.artifact_id, "strategy_parameter_preset_definition", 0),
            ),
            reason=(
                f"publish Strategy parameter preset version {key} v{strategy_preset.version_number}"
            ),
            draft_writer=partial(
                _write_strategy_parameter_preset_version,
                definition_id=definition_id,
                variant_id=variant_id,
                version_number=strategy_preset.version_number,
                parameters=strategy_preset.parameters,
            ),
        )
        components.append(
            _component(
                "strategy_parameter_preset_version",
                key,
                strategy_preset.version_number,
                version,
            )
        )
    return components


def _publish_defenses(
    service: ArtifactService,
    connection: Connection,
    loaded: LoadedCatalogRelease,
) -> list[PublishedCatalogComponent]:
    components, timing_versions, allocation_versions = _publish_defense_policies(
        service,
        connection,
        loaded,
    )
    for seed in loaded.bundle.defense.defenses:
        family_payload = {
            "family_key": seed.family_key,
            "name": seed.name,
            "allocation_semantics": seed.allocation_semantics,
            "research_hypothesis": seed.research_hypothesis,
        }
        family = service.publish(
            artifact_type="v022_defense_family",
            artifact_key=seed.family_key,
            version_number=1,
            semantic_payload=family_payload,
            content_payload=family_payload,
            reason=f"publish Defense Family {seed.family_key}",
            draft_writer=partial(_write_defense_family, payload=family_payload),
        )
        components.append(_component("defense_family", seed.family_key, 1, family))
        family_id = _id_for_artifact(
            connection,
            "defense.defense_family",
            "defense_family_id",
            family.artifact_id,
        )
        variant_payload = {"variant_key": seed.variant_key, "parameters": seed.parameters}
        variant = service.publish(
            artifact_type="v022_defense_variant",
            artifact_key=seed.variant_key,
            version_number=1,
            semantic_payload=variant_payload,
            content_payload=variant_payload,
            dependencies=(DependencyInput(family.artifact_id, "defense_family", 0),),
            reason=f"publish Defense Variant {seed.variant_key}",
            draft_writer=partial(
                _write_defense_variant,
                family_id=family_id,
                payload=variant_payload,
            ),
        )
        components.append(_component("defense_variant", seed.variant_key, 1, variant))
        variant_id = _id_for_artifact(
            connection,
            "defense.defense_variant",
            "defense_variant_id",
            variant.artifact_id,
        )
        payload = seed.model_dump(mode="json", exclude_none=True)
        dependencies = [DependencyInput(variant.artifact_id, "defense_variant", 0)]
        binding: dict[str, Any] | None = None
        if seed.timing_policy_ref is not None:
            timing_key = (
                seed.timing_policy_ref.variant_key,
                seed.timing_policy_ref.version_number,
            )
            allocation_ref = seed.defensive_allocation_policy_ref
            if allocation_ref is None or seed.research_status is None:
                raise ValueError("Composed Defense Package lacks exact policy identity")
            allocation_key = (allocation_ref.variant_key, allocation_ref.version_number)
            try:
                timing = timing_versions[timing_key]
                allocation = allocation_versions[allocation_key]
            except KeyError as error:
                raise ValueError(
                    "Composed Defense Package references unpublished policy"
                ) from error
            supported_sets = _resolve_supported_asset_sets(
                connection,
                seed.supported_asset_context_keys,
                allocation["asset_registry_release_id"],
                allocation["asset_registry_artifact_id"],
            )
            dependencies.extend(
                (
                    DependencyInput(timing["artifact_id"], "defense_timing_policy_version", 1),
                    DependencyInput(
                        allocation["artifact_id"],
                        "defense_allocation_policy_version",
                        2,
                    ),
                    DependencyInput(
                        allocation["asset_registry_artifact_id"],
                        "asset_registry_release",
                        3,
                    ),
                )
            )
            if allocation["reserve_return_model_artifact_id"] is not None:
                dependencies.append(
                    DependencyInput(
                        allocation["reserve_return_model_artifact_id"],
                        "reserve_return_model_version",
                        4,
                    )
                )
            binding = {
                "timing": timing,
                "allocation": allocation,
                "supported_sets": supported_sets,
                "research_status": seed.research_status,
            }
        version = service.publish(
            artifact_type="v022_defense_version",
            artifact_key=seed.variant_key,
            version_number=seed.version_number,
            semantic_payload=payload,
            content_payload=payload,
            dependencies=tuple(dependencies),
            reason=f"publish Defense Version {seed.variant_key} v{seed.version_number}",
            draft_writer=partial(
                _write_defense_version,
                variant_id=variant_id,
                seed=seed,
                payload=payload,
                package_binding=binding,
            ),
        )
        components.append(
            _component("defense_version", seed.variant_key, seed.version_number, version)
        )
    return components


def _publish_defense_policies(
    service: ArtifactService,
    connection: Connection,
    loaded: LoadedCatalogRelease,
) -> tuple[
    list[PublishedCatalogComponent],
    dict[tuple[str, int], dict[str, Any]],
    dict[tuple[str, int], dict[str, Any]],
]:
    components: list[PublishedCatalogComponent] = []
    timing_versions: dict[tuple[str, int], dict[str, Any]] = {}
    allocation_versions: dict[tuple[str, int], dict[str, Any]] = {}
    seed: Any
    for seed in loaded.bundle.defense.timing_policies:
        payload = seed.model_dump(mode="json")
        family_payload = {
            key: payload[key]
            for key in ("family_key", "name", "formula_identity", "research_hypothesis")
        }
        family = service.publish(
            artifact_type="v022_defense_timing_family",
            artifact_key=seed.family_key,
            version_number=1,
            semantic_payload=family_payload,
            content_payload=family_payload,
            reason=f"publish Defense Timing Family {seed.family_key}",
            draft_writer=partial(
                _write_defense_timing_family,
                payload=family_payload,
            ),
        )
        components.append(_component("defense_timing_family", seed.family_key, 1, family))
        family_id = _id_for_artifact(
            connection,
            "defense.v022_timing_policy_family",
            "timing_policy_family_id",
            family.artifact_id,
        )
        variant_payload = {"variant_key": seed.variant_key, "rule": payload["rule"]}
        variant = service.publish(
            artifact_type="v022_defense_timing_variant",
            artifact_key=seed.variant_key,
            version_number=1,
            semantic_payload=variant_payload,
            content_payload=variant_payload,
            dependencies=(DependencyInput(family.artifact_id, "defense_timing_family", 0),),
            reason=f"publish Defense Timing Variant {seed.variant_key}",
            draft_writer=partial(
                _write_defense_timing_variant,
                family_id=family_id,
                payload=variant_payload,
            ),
        )
        components.append(_component("defense_timing_variant", seed.variant_key, 1, variant))
        variant_id = _id_for_artifact(
            connection,
            "defense.v022_timing_policy_variant",
            "timing_policy_variant_id",
            variant.artifact_id,
        )
        version = service.publish(
            artifact_type="v022_defense_timing_version",
            artifact_key=seed.variant_key,
            version_number=seed.version_number,
            semantic_payload=payload,
            content_payload=payload,
            dependencies=(DependencyInput(variant.artifact_id, "defense_timing_variant", 0),),
            reason=f"publish Defense Timing Version {seed.variant_key} v{seed.version_number}",
            draft_writer=partial(
                _write_defense_timing_version,
                variant_id=variant_id,
                seed=seed,
                payload=payload,
            ),
        )
        components.append(
            _component("defense_timing_version", seed.variant_key, seed.version_number, version)
        )
        timing_versions[(seed.variant_key, seed.version_number)] = {
            "version_id": _id_for_artifact(
                connection,
                "defense.v022_timing_policy_version",
                "timing_policy_version_id",
                version.artifact_id,
            ),
            "artifact_id": version.artifact_id,
        }

    for seed in loaded.bundle.defense.allocation_policies:
        payload = seed.model_dump(mode="json")
        resolved = _resolve_allocation_identities(connection, seed)
        family_payload = {
            key: payload[key]
            for key in ("family_key", "name", "formula_identity", "research_hypothesis")
        }
        family = service.publish(
            artifact_type="v022_defense_allocation_family",
            artifact_key=seed.family_key,
            version_number=1,
            semantic_payload=family_payload,
            content_payload=family_payload,
            reason=f"publish Defense Allocation Family {seed.family_key}",
            draft_writer=partial(
                _write_defense_allocation_family,
                payload=family_payload,
            ),
        )
        components.append(_component("defense_allocation_family", seed.family_key, 1, family))
        family_id = _id_for_artifact(
            connection,
            "defense.v022_allocation_policy_family",
            "allocation_policy_family_id",
            family.artifact_id,
        )
        variant_payload = {
            key: payload[key]
            for key in (
                "variant_key",
                "asset_registry_catalog_version",
                "asset_set_key",
                "reserve_return_model_ref",
                "members",
            )
        }
        variant = service.publish(
            artifact_type="v022_defense_allocation_variant",
            artifact_key=seed.variant_key,
            version_number=1,
            semantic_payload=variant_payload,
            content_payload=variant_payload,
            dependencies=(DependencyInput(family.artifact_id, "defense_allocation_family", 0),),
            reason=f"publish Defense Allocation Variant {seed.variant_key}",
            draft_writer=partial(
                _write_defense_allocation_variant,
                family_id=family_id,
                payload=variant_payload,
            ),
        )
        components.append(_component("defense_allocation_variant", seed.variant_key, 1, variant))
        variant_id = _id_for_artifact(
            connection,
            "defense.v022_allocation_policy_variant",
            "allocation_policy_variant_id",
            variant.artifact_id,
        )
        dependencies = [
            DependencyInput(variant.artifact_id, "defense_allocation_variant", 0),
            DependencyInput(resolved["asset_registry_artifact_id"], "asset_registry_release", 1),
        ]
        if resolved["reserve_return_model_artifact_id"] is not None:
            dependencies.append(
                DependencyInput(
                    resolved["reserve_return_model_artifact_id"],
                    "reserve_return_model_version",
                    2,
                )
            )
        version = service.publish(
            artifact_type="v022_defense_allocation_version",
            artifact_key=seed.variant_key,
            version_number=seed.version_number,
            semantic_payload=payload,
            content_payload=payload,
            dependencies=tuple(dependencies),
            reason=(
                f"publish Defense Allocation Version {seed.variant_key} v{seed.version_number}"
            ),
            draft_writer=partial(
                _write_defense_allocation_version,
                variant_id=variant_id,
                seed=seed,
                payload=payload,
                resolved=resolved,
            ),
        )
        components.append(
            _component(
                "defense_allocation_version",
                seed.variant_key,
                seed.version_number,
                version,
            )
        )
        allocation_versions[(seed.variant_key, seed.version_number)] = {
            **resolved,
            "version_id": _id_for_artifact(
                connection,
                "defense.v022_allocation_policy_version",
                "allocation_policy_version_id",
                version.artifact_id,
            ),
            "artifact_id": version.artifact_id,
        }
    return components, timing_versions, allocation_versions


def _resolve_allocation_identities(connection: Connection, seed: Any) -> dict[str, Any]:
    rows = (
        connection.execute(
            text(
                """
            SELECT release.asset_registry_release_id,
                   release.artifact_id AS asset_registry_artifact_id,
                   definition.asset_set_definition_id,definition.set_type,
                   member.ordinal,security.security_id,security.security_key,
                   artifact.artifact_type,artifact.status
              FROM catalog.asset_registry_release release
              JOIN lineage.artifact artifact ON artifact.artifact_id=release.artifact_id
              JOIN catalog.asset_set_definition definition
                ON definition.asset_registry_release_id=release.asset_registry_release_id
              JOIN catalog.asset_set_member member
                ON member.asset_set_definition_id=definition.asset_set_definition_id
              JOIN catalog.security security ON security.security_id=member.security_id
             WHERE release.catalog_version=:catalog_version
               AND definition.set_key=:asset_set_key
             ORDER BY member.ordinal
            """
            ),
            {
                "catalog_version": seed.asset_registry_catalog_version,
                "asset_set_key": seed.asset_set_key,
            },
        )
        .mappings()
        .all()
    )
    expected_members = [(item.ordinal, item.asset_key) for item in seed.members]
    actual_members = [(row["ordinal"], row["security_key"]) for row in rows]
    if (
        not rows
        or rows[0]["artifact_type"] != "asset_registry_release"
        or rows[0]["status"] != "published"
        or rows[0]["set_type"] != "defensive_basket"
        or actual_members != expected_members
        or len({row["asset_registry_release_id"] for row in rows}) != 1
    ):
        raise ValueError(
            "defense_allocation_asset_set_unpublished: exact ordered Registry basket required"
        )
    reserve_model_version_id: uuid.UUID | None = None
    reserve_model_artifact_id: uuid.UUID | None = None
    if seed.reserve_return_model_ref is not None:
        model = (
            connection.execute(
                text(
                    """
                SELECT version.reserve_return_model_version_id,version.artifact_id,
                       artifact.artifact_type,artifact.status
                  FROM experiment.reserve_return_model_version version
                  JOIN experiment.reserve_return_model_definition definition
                    ON definition.reserve_return_model_definition_id=
                       version.reserve_return_model_definition_id
                  JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
                 WHERE definition.model_key=:model_key
                   AND version.version_number=:version_number
                """
                ),
                {
                    "model_key": seed.reserve_return_model_ref.model_key,
                    "version_number": seed.reserve_return_model_ref.version_number,
                },
            )
            .mappings()
            .one_or_none()
        )
        if (
            model is None
            or model["artifact_type"] != "reserve_return_model_version"
            or model["status"] != "published"
        ):
            raise ValueError(
                "defense_allocation_reserve_model_unpublished: exact published Model required"
            )
        reserve_model_version_id = model["reserve_return_model_version_id"]
        reserve_model_artifact_id = model["artifact_id"]
    return {
        "asset_registry_release_id": rows[0]["asset_registry_release_id"],
        "asset_registry_artifact_id": rows[0]["asset_registry_artifact_id"],
        "asset_set_definition_id": rows[0]["asset_set_definition_id"],
        "members": tuple(
            {
                "ordinal": row["ordinal"],
                "security_id": row["security_id"],
                "asset_key": row["security_key"],
            }
            for row in rows
        ),
        "reserve_return_model_version_id": reserve_model_version_id,
        "reserve_return_model_artifact_id": reserve_model_artifact_id,
    }


def _resolve_supported_asset_sets(
    connection: Connection,
    asset_context_keys: list[str],
    registry_release_id: uuid.UUID,
    registry_artifact_id: uuid.UUID,
) -> tuple[dict[str, Any], ...]:
    rows = (
        connection.execute(
            text(
                """
            SELECT definition.set_key,definition.asset_set_definition_id,
                   release.asset_registry_release_id,release.artifact_id,
                   artifact.artifact_type,artifact.status
              FROM catalog.asset_set_definition definition
              JOIN catalog.asset_registry_release release
                ON release.asset_registry_release_id=definition.asset_registry_release_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=release.artifact_id
             WHERE definition.asset_registry_release_id=:release
               AND definition.set_key=ANY(:keys)
            """
            ),
            {"release": registry_release_id, "keys": asset_context_keys},
        )
        .mappings()
        .all()
    )
    by_key = {row["set_key"]: row for row in rows}
    if set(by_key) != set(asset_context_keys) or any(
        row["artifact_id"] != registry_artifact_id
        or row["artifact_type"] != "asset_registry_release"
        or row["status"] != "published"
        for row in rows
    ):
        raise ValueError(
            "defense_package_asset_context_unpublished: exact supported Asset Sets required"
        )
    return tuple(
        {
            "ordinal": ordinal,
            "asset_context_key": key,
            "asset_registry_release_id": registry_release_id,
            "asset_registry_artifact_id": registry_artifact_id,
            "asset_set_definition_id": by_key[key]["asset_set_definition_id"],
        }
        for ordinal, key in enumerate(asset_context_keys)
    )


def _component(
    kind: str, key: str, version: int, result: PublicationResult
) -> PublishedCatalogComponent:
    return PublishedCatalogComponent(
        kind,
        key,
        version,
        result.artifact_id,
        result.semantic_fingerprint,
        result.content_hash,
        result.reused,
    )


def _publisher_authorization(
    connection: Connection,
    actor: str,
    now: datetime,
    *,
    allow_bootstrap: bool,
) -> uuid.UUID:
    existing = connection.execute(
        text(
            """
            SELECT catalog_publisher_authorization_id
            FROM workspace.catalog_publisher_authorization
            WHERE actor_key = :actor AND valid_from <= :now
              AND (valid_until IS NULL OR valid_until > :now)
            ORDER BY valid_from DESC LIMIT 1
            """
        ),
        {"actor": actor, "now": now},
    ).scalar_one_or_none()
    if existing is not None:
        return cast(uuid.UUID, existing)
    if not allow_bootstrap:
        raise ValueError("Catalog publisher has no active database authorization")
    authorization_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO workspace.catalog_publisher_authorization (
                catalog_publisher_authorization_id, actor_key, authorization_scope,
                authorization_source, valid_from
            ) VALUES (
                :id, :actor, CAST(:scope AS jsonb), :source, :valid_from
            )
            """
        ),
        {
            "id": authorization_id,
            "actor": actor,
            "scope": _json({"contract_versions": ["v0.22.0"], "release_keys": ["*"]}),
            "source": "trusted_single_user_local_research_environment",
            "valid_from": now,
        },
    )
    return authorization_id


def _release_fingerprint(
    loaded: LoadedCatalogRelease, components: tuple[PublishedCatalogComponent, ...]
) -> str:
    return sha256_hexdigest(
        {
            "contract_version": loaded.bundle.release.contract_version,
            "processing_stage_count": loaded.bundle.release.processing_stage_count,
            "source_manifest_hash": loaded.bundle.source_manifest_hash,
            "components": [
                {
                    "kind": item.component_kind,
                    "key": item.component_key,
                    "version": item.component_version,
                    "semantic_fingerprint": item.semantic_fingerprint,
                }
                for item in components
            ],
        }
    )


def _write_release(
    connection: Connection,
    artifact_id: uuid.UUID,
    authorization_id: uuid.UUID,
    loaded: LoadedCatalogRelease,
    components: tuple[PublishedCatalogComponent, ...],
    release_fingerprint: str,
    version: int,
    now: datetime,
) -> None:
    release_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO workspace.v022_catalog_release (
                catalog_release_id, artifact_id, publisher_authorization_id,
                release_key, version_number, contract_version, processing_stage_count,
                release_fingerprint, source_manifest_hash, publisher_actor, published_at
            ) VALUES (
                :id, :artifact_id, :authorization_id, :release_key, :version_number,
                :contract_version, :stage_count, :release_fingerprint,
                :source_manifest_hash, :publisher_actor, :published_at
            )
            """
        ),
        {
            "id": release_id,
            "artifact_id": artifact_id,
            "authorization_id": authorization_id,
            "release_key": loaded.bundle.release.release_key,
            "version_number": version,
            "contract_version": loaded.bundle.release.contract_version,
            "stage_count": loaded.bundle.release.processing_stage_count,
            "release_fingerprint": release_fingerprint,
            "source_manifest_hash": loaded.bundle.source_manifest_hash,
            "publisher_actor": loaded.bundle.release.publisher_actor,
            "published_at": now,
        },
    )
    for ordinal, component in enumerate(components):
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_catalog_release_component (
                    catalog_release_id, component_artifact_id, component_kind,
                    component_key, component_version, ordinal, component_fingerprint
                ) VALUES (
                    :release_id, :artifact_id, :kind, :key, :version, :ordinal, :fingerprint
                )
                """
            ),
            {
                "release_id": release_id,
                "artifact_id": component.artifact_id,
                "kind": component.component_kind,
                "key": component.component_key,
                "version": component.component_version,
                "ordinal": ordinal,
                "fingerprint": component.semantic_fingerprint,
            },
        )


def _write_evidence(
    connection: Connection,
    artifact_id: uuid.UUID,
    release_id: uuid.UUID,
    loaded: LoadedCatalogRelease,
    checks: dict[str, bool],
    now: datetime,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO workspace.v022_catalog_validation_evidence (
                catalog_validation_evidence_id, artifact_id, catalog_release_id,
                evidence_kind, validator_version, checks, passed, publisher_actor,
                reviewer_actor, reviewed_at
            ) VALUES (
                :id, :artifact_id, :release_id, 'rebuild_verify', 'v022-catalog-validator-v1',
                CAST(:checks AS jsonb), true, :publisher, :reviewer, :reviewed_at
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact_id": artifact_id,
            "release_id": release_id,
            "checks": _json(checks),
            "publisher": loaded.bundle.release.publisher_actor,
            "reviewer": loaded.bundle.release.reviewer_actor,
            "reviewed_at": now,
        },
    )


def _verification_checks(
    connection: Connection,
    release_id: uuid.UUID,
    loaded: LoadedCatalogRelease,
    components: tuple[PublishedCatalogComponent, ...],
) -> dict[str, bool]:
    members = _release_members(connection, release_id)
    expected = [
        (
            item.component_kind,
            item.component_key,
            item.component_version,
            item.semantic_fingerprint,
        )
        for item in components
    ]
    actual = [
        (
            item["component_kind"],
            item["component_key"],
            item["component_version"],
            item["component_fingerprint"],
        )
        for item in members
    ]
    return {
        "source_manifest_hash_matches": connection.execute(
            text(
                "SELECT source_manifest_hash = :hash FROM workspace.v022_catalog_release "
                "WHERE catalog_release_id = :release_id"
            ),
            {"hash": loaded.bundle.source_manifest_hash, "release_id": release_id},
        ).scalar_one(),
        "component_membership_exact": actual == expected,
        "all_components_published": all(item["artifact_status"] == "published" for item in members),
        "processing_stage_count_is_three": loaded.bundle.release.processing_stage_count == 3,
        "raw_input_count_is_nine": len(loaded.bundle.raw_inputs.raw_inputs) == 9,
        "deterministic_family_count_is_four": len(
            [
                item
                for item in loaded.bundle.aggregation.families
                if item.execution_mode == "deterministic"
            ]
        )
        == 4,
    }


def _release_members(connection: Connection, release_id: uuid.UUID) -> list[Any]:
    return list(
        connection.execute(
            text(
                """
                SELECT c.component_kind, c.component_key, c.component_version,
                       c.component_artifact_id::text component_artifact_id,
                       c.component_fingerprint, c.ordinal, a.status artifact_status,
                       a.content_hash
                FROM workspace.v022_catalog_release_component c
                JOIN lineage.artifact a ON a.artifact_id = c.component_artifact_id
                WHERE c.catalog_release_id = :release_id
                ORDER BY c.ordinal
                """
            ),
            {"release_id": release_id},
        ).mappings()
    )


def _write_payload_family(
    connection: Connection, artifact_id: uuid.UUID, seed: PayloadContractSeed
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO data.payload_contract_family (
                payload_contract_family_id, artifact_id, contract_key, name,
                semantic_role, description
            ) VALUES (:id, :artifact_id, :key, :name, :role, :description)
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact_id": artifact_id,
            "key": seed.contract_key,
            "name": seed.name,
            "role": seed.semantic_role,
            "description": seed.description,
        },
    )


def _write_payload_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    family_id: uuid.UUID,
    seed: PayloadContractSeed,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO data.payload_contract_version (
                payload_contract_version_id, payload_contract_family_id, artifact_id,
                version_number, payload_kind, schema_document, entity_axis, time_axis,
                observation_grain, primary_key_fields, ordering_contract,
                missingness_contract, pit_contract, quality_contract, aggregation_role,
                export_policy, schema_fingerprint, compatibility_class
            ) VALUES (
                :id, :family_id, :artifact_id, :version, :kind,
                CAST(:schema AS jsonb), CAST(:entity AS jsonb), CAST(:time AS jsonb),
                CAST(:grain AS jsonb), CAST(:primary_key AS jsonb), CAST(:ordering AS jsonb),
                CAST(:missing AS jsonb), CAST(:pit AS jsonb), CAST(:quality AS jsonb),
                :aggregation_role, CAST(:export AS jsonb), :schema_fingerprint,
                :compatibility_class
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "family_id": family_id,
            "artifact_id": artifact_id,
            "version": seed.version_number,
            "kind": seed.payload_kind,
            "schema": _json(seed.schema_document),
            "entity": _json(seed.entity_axis),
            "time": _json(seed.time_axis),
            "grain": _json(seed.observation_grain),
            "primary_key": _json(seed.primary_key_fields),
            "ordering": _json(seed.ordering_contract),
            "missing": _json(seed.missingness_contract),
            "pit": _json(seed.pit_contract),
            "quality": _json(seed.quality_contract),
            "aggregation_role": seed.aggregation_role,
            "export": _json(seed.export_policy),
            "schema_fingerprint": sha256_hexdigest(seed.schema_document),
            "compatibility_class": seed.compatibility_class,
        },
    )


def _write_encoding(
    connection: Connection, artifact_id: uuid.UUID, seed: PhysicalEncodingSeed
) -> None:
    payload = seed.model_dump(mode="json")
    connection.execute(
        text(
            """
            INSERT INTO data.physical_encoding_version (
                physical_encoding_version_id, artifact_id, encoding_key, version_number,
                media_type, file_extension, compression, writer_version,
                reader_min_version, reader_max_version, canonicalization_policy,
                partition_policy, encryption_policy, verification_implementation
            ) VALUES (
                :id, :artifact_id, :encoding_key, :version_number, :media_type,
                :file_extension, :compression, :writer_version, :reader_min_version,
                :reader_max_version, CAST(:canonicalization AS jsonb),
                CAST(:partition AS jsonb), CAST(:encryption AS jsonb), :verification
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact_id": artifact_id,
            **{
                key: payload[key]
                for key in (
                    "encoding_key",
                    "version_number",
                    "media_type",
                    "file_extension",
                    "compression",
                    "writer_version",
                    "reader_min_version",
                    "reader_max_version",
                )
            },
            "canonicalization": _json(seed.canonicalization_policy),
            "partition": _json(seed.partition_policy),
            "encryption": _json(seed.encryption_policy),
            "verification": seed.verification_implementation,
        },
    )


def _write_feature_family(
    connection: Connection, artifact_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO processing.feature_family (
                feature_family_id, artifact_id, family_key, name, formula_identity,
                input_roles, output_semantics, direction, research_hypothesis
            ) VALUES (
                :id, :artifact_id, :family_key, :name, :formula_identity,
                CAST(:input_roles AS jsonb), CAST(:output_semantics AS jsonb),
                :direction, :research_hypothesis
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact_id": artifact_id,
            **payload,
            "input_roles": _json(payload["input_roles"]),
            "output_semantics": _json(payload["output_semantics"]),
        },
    )


def _write_feature_variant(
    connection: Connection,
    artifact_id: uuid.UUID,
    family_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO processing.feature_variant (
                feature_variant_id, feature_family_id, artifact_id, variant_key,
                parameters, research_tier
            ) VALUES (
                :id, :family_id, :artifact_id, :variant_key,
                CAST(:parameters AS jsonb), :research_tier
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "family_id": family_id,
            "artifact_id": artifact_id,
            "variant_key": payload["variant_key"],
            "parameters": _json(payload["parameters"]),
            "research_tier": payload["research_tier"],
        },
    )


def _write_raw_feature_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    variant_id: uuid.UUID,
    contract_id: uuid.UUID,
    variant_key: str,
    aggregation_readiness: str,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO processing.feature_version (
                feature_version_id, feature_variant_id, artifact_id,
                payload_contract_version_id, version_number, origin_stage,
                output_port_key, aggregation_readiness, execution_semantics,
                version_fingerprint
            ) VALUES (
                :id, :variant_id, :artifact_id, :contract_id, 1, 0,
                :output_port_key, :aggregation_readiness, CAST(:semantics AS jsonb),
                :fingerprint
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "variant_id": variant_id,
            "artifact_id": artifact_id,
            "contract_id": contract_id,
            "output_port_key": f"{variant_key}_value",
            "aggregation_readiness": aggregation_readiness,
            "semantics": _json(payload),
            "fingerprint": sha256_hexdigest(payload),
        },
    )


def _write_node_definition(
    connection: Connection, artifact_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO processing.node_definition (
              node_definition_id,artifact_id,node_key,name,algorithm_identity,description
            ) VALUES (:id,:artifact,:node_key,:name,:algorithm_identity,:description)
            """
        ),
        {"id": uuid.uuid4(), "artifact": artifact_id, **payload},
    )


def _write_node_variant(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO processing.node_variant (
              node_variant_id,node_definition_id,artifact_id,variant_key,parameters
            ) VALUES (:id,:definition,:artifact,:key,CAST(:parameters AS jsonb))
            """
        ),
        {
            "id": uuid.uuid4(),
            "definition": definition_id,
            "artifact": artifact_id,
            "key": payload["variant_key"],
            "parameters": _json(payload["parameters"]),
        },
    )


def _write_node_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    variant_id: uuid.UUID,
    seed: ProcessingNodeSeed,
    source_bindings: dict[str, tuple[uuid.UUID, uuid.UUID]],
    contract_versions: dict[str, tuple[uuid.UUID, uuid.UUID]],
    payload: dict[str, Any],
) -> None:
    node_version_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO processing.node_version (
              node_version_id,node_variant_id,artifact_id,version_number,stage_no,
              implementation_key,implementation_version,determinism_policy,cache_policy,
              execution_contract,version_fingerprint
            ) VALUES (
              :id,:variant,:artifact,:version,:stage,:implementation,
              :implementation_version,:determinism,:cache,
              CAST(:execution AS jsonb),:fingerprint
            )
            """
        ),
        {
            "id": node_version_id,
            "variant": variant_id,
            "artifact": artifact_id,
            "version": seed.version_number,
            "stage": seed.stage_no,
            "implementation": seed.implementation_key,
            "implementation_version": seed.implementation_version,
            "determinism": seed.determinism_policy,
            "cache": seed.cache_policy,
            "execution": _json(seed.execution_contract),
            "fingerprint": sha256_hexdigest(payload),
        },
    )
    port_ids: dict[str, uuid.UUID] = {}
    for port in seed.ports:
        port_id = uuid.uuid4()
        port_ids[port.port_key] = port_id
        contract_id = contract_versions[port.payload_contract_key][0]
        connection.execute(
            text(
                """
                INSERT INTO processing.node_port (
                  node_port_id,node_version_id,payload_contract_version_id,port_key,
                  direction,ordinal,binding_cardinality,port_semantics
                ) VALUES (
                  :id,:node,:contract,:key,:direction,:ordinal,'required',
                  CAST(:semantics AS jsonb)
                )
                """
            ),
            {
                "id": port_id,
                "node": node_version_id,
                "contract": contract_id,
                "key": port.port_key,
                "direction": port.direction,
                "ordinal": port.ordinal,
                "semantics": _json(port.semantics),
            },
        )
    for binding in seed.input_bindings:
        source_feature_id = source_bindings[binding.input_port_key][0]
        connection.execute(
            text(
                """
                INSERT INTO processing.node_input_binding (
                  node_input_binding_id,node_version_id,input_port_id,source_feature_version_id,
                  binding_role,ordinal
                ) VALUES (:id,:node,:port,:source,:role,:ordinal)
                """
            ),
            {
                "id": uuid.uuid4(),
                "node": node_version_id,
                "port": port_ids[binding.input_port_key],
                "source": source_feature_id,
                "role": binding.binding_role,
                "ordinal": binding.ordinal,
            },
        )


def _write_processing_feature_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    variant_id: uuid.UUID,
    node_version_id: uuid.UUID,
    contract_id: uuid.UUID,
    node_stage: int,
    output: NodeOutputFeatureSeed,
    payload: dict[str, Any],
) -> None:
    feature_version_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO processing.feature_version (
              feature_version_id,feature_variant_id,artifact_id,payload_contract_version_id,
              version_number,origin_stage,output_port_key,aggregation_readiness,
              execution_semantics,version_fingerprint
            ) VALUES (:id,:variant,:artifact,:contract,1,:stage,:port,:readiness,
                      CAST(:semantics AS jsonb),:fingerprint)
            """
        ),
        {
            "id": feature_version_id,
            "variant": variant_id,
            "artifact": artifact_id,
            "contract": contract_id,
            "stage": node_stage,
            "port": output.output_port_key,
            "readiness": output.aggregation_readiness,
            "semantics": _json(payload),
            "fingerprint": sha256_hexdigest(payload),
        },
    )
    output_port_id = connection.scalar(
        text(
            "SELECT node_port_id FROM processing.node_port "
            "WHERE node_version_id=:node AND port_key=:port AND direction='output'"
        ),
        {"node": node_version_id, "port": output.output_port_key},
    )
    connection.execute(
        text(
            """
            INSERT INTO processing.feature_producer (
              feature_producer_id,feature_version_id,node_version_id,output_port_id
            ) VALUES (:id,:feature,:node,:port)
            """
        ),
        {
            "id": uuid.uuid4(),
            "feature": feature_version_id,
            "node": node_version_id,
            "port": output_port_id,
        },
    )


def _write_aggregation_family(
    connection: Connection, artifact_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO aggregation.aggregation_family (
                aggregation_family_id, artifact_id, family_key, name,
                algorithm_identity, objective_semantics, output_semantics
            ) VALUES (
                :id, :artifact_id, :family_key, :name, :algorithm_identity,
                CAST(:objective AS jsonb), CAST(:output AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact_id": artifact_id,
            "family_key": payload["family_key"],
            "name": payload["name"],
            "algorithm_identity": payload["algorithm_identity"],
            "objective": _json(payload["objective_semantics"]),
            "output": _json(payload["output_semantics"]),
        },
    )


def _write_aggregation_feature_taxonomy(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    taxonomy: Any,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO aggregation.v022_feature_taxonomy_version (
              feature_taxonomy_version_id,artifact_id,taxonomy_key,version_number,
              taxonomy_fingerprint,taxonomy_document
            ) VALUES (
              :id,:artifact,:key,:version,:fingerprint,CAST(:document AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact": artifact_id,
            "key": taxonomy.taxonomy_key,
            "version": taxonomy.version_number,
            "fingerprint": sha256_hexdigest(payload),
            "document": _json(payload),
        },
    )


def _write_aggregation_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    family_id: uuid.UUID,
    input_contract_id: uuid.UUID,
    output_contract_id: uuid.UUID,
    seed: Any,
    payload: dict[str, Any],
) -> None:
    version_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO aggregation.aggregation_version (
                aggregation_version_id, aggregation_family_id, artifact_id,
                output_payload_contract_version_id, version_number, execution_mode,
                implementation_key, input_policy, missing_policy, tie_policy,
                version_fingerprint
            ) VALUES (
                :id, :family_id, :artifact_id, :output_contract_id, :version,
                :execution_mode, :implementation_key, CAST(:input_policy AS jsonb),
                CAST(:missing_policy AS jsonb), CAST(:tie_policy AS jsonb), :fingerprint
            )
            """
        ),
        {
            "id": version_id,
            "family_id": family_id,
            "artifact_id": artifact_id,
            "output_contract_id": output_contract_id,
            "version": seed.version_number,
            "execution_mode": seed.execution_mode,
            "implementation_key": seed.implementation_key,
            "input_policy": _json(seed.input_policy),
            "missing_policy": _json(seed.missing_policy),
            "tie_policy": _json(seed.tie_policy),
            "fingerprint": sha256_hexdigest(payload),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO aggregation.aggregation_input_port (
                aggregation_input_port_id, aggregation_version_id,
                payload_contract_version_id, port_key, minimum_count, maximum_count,
                ordering_policy, compatibility_policy
            ) VALUES (
                :id, :version_id, :contract_id, 'stage3_inputs', :minimum_count,
                :maximum_count, :ordering_policy, CAST(:compatibility_policy AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "version_id": version_id,
            "contract_id": input_contract_id,
            "minimum_count": seed.minimum_inputs,
            "maximum_count": seed.maximum_inputs,
            "ordering_policy": seed.ordering_policy,
            "compatibility_policy": _json(seed.compatibility_policy),
        },
    )


def _write_aggregation_axis_definition(
    connection: Connection,
    artifact_id: uuid.UUID,
    family_id: uuid.UUID,
    prefix: str,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            f"""
            INSERT INTO aggregation.{prefix}_definition (
                {prefix}_definition_id, aggregation_family_id, artifact_id,
                {prefix}_key, name, description
            ) VALUES (:id, :family_id, :artifact_id, :key, :name, :description)
            """
        ),
        {
            "id": uuid.uuid4(),
            "family_id": family_id,
            "artifact_id": artifact_id,
            "key": payload[f"{prefix}_key"],
            "name": payload["name"],
            "description": payload["description"],
        },
    )


def _write_aggregation_axis_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    prefix: str,
    version_number: int,
    semantics: dict[str, Any],
) -> None:
    connection.execute(
        text(
            f"""
            INSERT INTO aggregation.{prefix}_version (
                {prefix}_version_id, {prefix}_definition_id, artifact_id,
                version_number, semantics, version_fingerprint
            ) VALUES (
                :id, :definition_id, :artifact_id, :version,
                CAST(:semantics AS jsonb), :fingerprint
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "definition_id": definition_id,
            "artifact_id": artifact_id,
            "version": version_number,
            "semantics": _json(semantics),
            "fingerprint": sha256_hexdigest(semantics),
        },
    )


def _write_strategy_family(
    connection: Connection, artifact_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO strategy.v022_strategy_family (
                strategy_family_id, artifact_id, family_key, name,
                selection_semantics, research_hypothesis
            ) VALUES (
                :id, :artifact_id, :family_key, :name,
                CAST(:selection AS jsonb), :hypothesis
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact_id": artifact_id,
            "family_key": payload["family_key"],
            "name": payload["name"],
            "selection": _json(payload["selection_semantics"]),
            "hypothesis": payload["research_hypothesis"],
        },
    )


def _write_strategy_variant(
    connection: Connection,
    artifact_id: uuid.UUID,
    family_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO strategy.v022_strategy_variant (
                strategy_variant_id, strategy_family_id, artifact_id,
                variant_key, parameters
            ) VALUES (
                :id, :family_id, :artifact_id, :variant_key, CAST(:parameters AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "family_id": family_id,
            "artifact_id": artifact_id,
            "variant_key": payload["variant_key"],
            "parameters": _json(payload["parameters"]),
        },
    )


def _write_strategy_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    variant_id: uuid.UUID,
    contract_id: uuid.UUID,
    seed: Any,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO strategy.v022_strategy_version (
                strategy_version_id, strategy_variant_id, artifact_id,
                input_payload_contract_version_id, version_number, implementation_key,
                schedule_policy, execution_policy, version_fingerprint
            ) VALUES (
                :id, :variant_id, :artifact_id, :contract_id, :version,
                :implementation_key, CAST(:schedule AS jsonb), CAST(:execution AS jsonb),
                :fingerprint
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "variant_id": variant_id,
            "artifact_id": artifact_id,
            "contract_id": contract_id,
            "version": seed.version_number,
            "implementation_key": seed.implementation_key,
            "schedule": _json(seed.schedule_policy),
            "execution": _json(seed.execution_policy),
            "fingerprint": sha256_hexdigest(payload),
        },
    )


def _write_strategy_parameter_preset_definition(
    connection: Connection,
    artifact_id: uuid.UUID,
    variant_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO strategy.v022_strategy_parameter_preset_definition (
                strategy_parameter_preset_definition_id, strategy_variant_id,
                artifact_id, preset_key, name, description
            ) VALUES (
                :id, :variant_id, :artifact_id, :preset_key, :name, :description
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "variant_id": variant_id,
            "artifact_id": artifact_id,
            "preset_key": payload["preset_key"],
            "name": payload["name"],
            "description": payload["description"],
        },
    )


def _write_strategy_parameter_preset_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    variant_id: uuid.UUID,
    version_number: int,
    parameters: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO strategy.v022_strategy_parameter_preset_version (
                strategy_parameter_preset_version_id,
                strategy_parameter_preset_definition_id, strategy_variant_id, artifact_id,
                version_number, resolved_parameters, parameter_fingerprint
            ) VALUES (
                :id, :definition_id, :variant_id, :artifact_id, :version,
                CAST(:parameters AS jsonb), :fingerprint
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "definition_id": definition_id,
            "variant_id": variant_id,
            "artifact_id": artifact_id,
            "version": version_number,
            "parameters": _json(parameters),
            "fingerprint": sha256_hexdigest(parameters),
        },
    )


def _write_defense_family(
    connection: Connection, artifact_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO defense.defense_family (
                defense_family_id, artifact_id, family_key, name,
                allocation_semantics, research_hypothesis
            ) VALUES (
                :id, :artifact_id, :family_key, :name,
                CAST(:allocation AS jsonb), :hypothesis
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact_id": artifact_id,
            "family_key": payload["family_key"],
            "name": payload["name"],
            "allocation": _json(payload["allocation_semantics"]),
            "hypothesis": payload["research_hypothesis"],
        },
    )


def _write_defense_timing_family(
    connection: Connection, artifact_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO defense.v022_timing_policy_family (
              timing_policy_family_id,artifact_id,family_key,name,
              formula_identity,research_hypothesis
            ) VALUES (:id,:artifact,:family_key,:name,:formula,:hypothesis)
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact": artifact_id,
            "family_key": payload["family_key"],
            "name": payload["name"],
            "formula": payload["formula_identity"],
            "hypothesis": payload["research_hypothesis"],
        },
    )


def _write_defense_timing_variant(
    connection: Connection,
    artifact_id: uuid.UUID,
    family_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO defense.v022_timing_policy_variant (
              timing_policy_variant_id,timing_policy_family_id,artifact_id,
              variant_key,rule
            ) VALUES (:id,:family,:artifact,:variant_key,CAST(:rule AS jsonb))
            """
        ),
        {
            "id": uuid.uuid4(),
            "family": family_id,
            "artifact": artifact_id,
            "variant_key": payload["variant_key"],
            "rule": _json(payload["rule"]),
        },
    )


def _write_defense_timing_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    variant_id: uuid.UUID,
    seed: Any,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO defense.v022_timing_policy_version (
              timing_policy_version_id,timing_policy_variant_id,artifact_id,
              version_number,implementation_key,research_status,
              supported_frequencies,input_policy,rule,version_fingerprint
            ) VALUES (
              :id,:variant,:artifact,:version,:implementation,:status,
              CAST(:frequencies AS jsonb),CAST(:input_policy AS jsonb),
              CAST(:rule AS jsonb),:fingerprint
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "variant": variant_id,
            "artifact": artifact_id,
            "version": seed.version_number,
            "implementation": seed.implementation_key,
            "status": seed.research_status,
            "frequencies": _json(seed.supported_frequencies),
            "input_policy": _json(seed.input_policy.model_dump(mode="json")),
            "rule": _json(seed.rule.model_dump(mode="json")),
            "fingerprint": sha256_hexdigest(payload),
        },
    )


def _write_defense_allocation_family(
    connection: Connection, artifact_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO defense.v022_allocation_policy_family (
              allocation_policy_family_id,artifact_id,family_key,name,
              formula_identity,research_hypothesis
            ) VALUES (:id,:artifact,:family_key,:name,:formula,:hypothesis)
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact": artifact_id,
            "family_key": payload["family_key"],
            "name": payload["name"],
            "formula": payload["formula_identity"],
            "hypothesis": payload["research_hypothesis"],
        },
    )


def _write_defense_allocation_variant(
    connection: Connection,
    artifact_id: uuid.UUID,
    family_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO defense.v022_allocation_policy_variant (
              allocation_policy_variant_id,allocation_policy_family_id,artifact_id,
              variant_key,asset_registry_catalog_version,asset_set_key,members_document
            ) VALUES (
              :id,:family,:artifact,:variant_key,:catalog_version,:asset_set,
              CAST(:members AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "family": family_id,
            "artifact": artifact_id,
            "variant_key": payload["variant_key"],
            "catalog_version": payload["asset_registry_catalog_version"],
            "asset_set": payload["asset_set_key"],
            "members": _json(payload["members"]),
        },
    )


def _write_defense_allocation_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    variant_id: uuid.UUID,
    seed: Any,
    payload: dict[str, Any],
    resolved: dict[str, Any],
) -> None:
    version_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO defense.v022_allocation_policy_version (
              allocation_policy_version_id,allocation_policy_variant_id,artifact_id,
              version_number,implementation_key,research_status,formal_eligible,
              missing_member_policy,reserve_fallback_policy,rebalance_policy,
              asset_registry_release_id,asset_registry_artifact_id,
              asset_set_definition_id,reserve_return_model_version_id,
              reserve_return_model_artifact_id,member_count,version_fingerprint
            ) VALUES (
              :id,:variant,:artifact,:version,:implementation,:status,:formal,
              :missing,:fallback,:rebalance,:registry_release,:registry_artifact,
              :asset_set,:reserve_model_version,:reserve_model_artifact,
              :member_count,:fingerprint
            )
            """
        ),
        {
            "id": version_id,
            "variant": variant_id,
            "artifact": artifact_id,
            "version": seed.version_number,
            "implementation": seed.implementation_key,
            "status": seed.research_status,
            "formal": seed.formal_eligible,
            "missing": seed.missing_member_policy,
            "fallback": seed.reserve_fallback_policy,
            "rebalance": seed.rebalance_policy,
            "registry_release": resolved["asset_registry_release_id"],
            "registry_artifact": resolved["asset_registry_artifact_id"],
            "asset_set": resolved["asset_set_definition_id"],
            "reserve_model_version": resolved["reserve_return_model_version_id"],
            "reserve_model_artifact": resolved["reserve_return_model_artifact_id"],
            "member_count": len(seed.members),
            "fingerprint": sha256_hexdigest(payload),
        },
    )
    security_by_key = {item["asset_key"]: item["security_id"] for item in resolved["members"]}
    connection.execute(
        text(
            """
            INSERT INTO defense.v022_allocation_policy_member (
              allocation_policy_version_id,ordinal,security_id,asset_key,
              component_role,sleeve_weight
            ) VALUES (:version,:ordinal,:security,:asset_key,:role,:weight)
            """
        ),
        [
            {
                "version": version_id,
                "ordinal": item.ordinal,
                "security": security_by_key[item.asset_key],
                "asset_key": item.asset_key,
                "role": item.component_role,
                "weight": item.sleeve_weight,
            }
            for item in seed.members
        ],
    )


def _write_defense_variant(
    connection: Connection,
    artifact_id: uuid.UUID,
    family_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO defense.defense_variant (
                defense_variant_id, defense_family_id, artifact_id,
                variant_key, parameters
            ) VALUES (
                :id, :family_id, :artifact_id, :variant_key, CAST(:parameters AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "family_id": family_id,
            "artifact_id": artifact_id,
            "variant_key": payload["variant_key"],
            "parameters": _json(payload["parameters"]),
        },
    )


def _write_defense_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    variant_id: uuid.UUID,
    seed: Any,
    payload: dict[str, Any],
    package_binding: dict[str, Any] | None = None,
) -> None:
    version_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO defense.defense_version (
                defense_version_id, defense_variant_id, artifact_id, version_number,
                implementation_key, input_policy, allocation_policy,
                supported_asset_context_keys, version_fingerprint
            ) VALUES (
                :id, :variant_id, :artifact_id, :version, :implementation_key,
                CAST(:input AS jsonb), CAST(:allocation AS jsonb),
                CAST(:contexts AS jsonb), :fingerprint
            )
            """
        ),
        {
            "id": version_id,
            "variant_id": variant_id,
            "artifact_id": artifact_id,
            "version": seed.version_number,
            "implementation_key": seed.implementation_key,
            "input": _json(seed.input_policy),
            "allocation": _json(seed.allocation_policy),
            "contexts": _json(seed.supported_asset_context_keys),
            "fingerprint": sha256_hexdigest(payload),
        },
    )
    if package_binding is None:
        return
    timing = package_binding["timing"]
    allocation = package_binding["allocation"]
    supported_sets = package_binding["supported_sets"]
    connection.execute(
        text(
            """
            INSERT INTO defense.v022_defense_package_policy_binding (
              defense_version_id,timing_policy_version_id,timing_policy_artifact_id,
              allocation_policy_version_id,allocation_policy_artifact_id,
              asset_registry_release_id,asset_registry_artifact_id,
              allocation_asset_set_definition_id,reserve_return_model_version_id,
              reserve_return_model_artifact_id,research_status,supported_asset_set_count
            ) VALUES (
              :defense,:timing_version,:timing_artifact,:allocation_version,
              :allocation_artifact,:registry_release,:registry_artifact,:allocation_set,
              :reserve_model_version,:reserve_model_artifact,:status,:set_count
            )
            """
        ),
        {
            "defense": version_id,
            "timing_version": timing["version_id"],
            "timing_artifact": timing["artifact_id"],
            "allocation_version": allocation["version_id"],
            "allocation_artifact": allocation["artifact_id"],
            "registry_release": allocation["asset_registry_release_id"],
            "registry_artifact": allocation["asset_registry_artifact_id"],
            "allocation_set": allocation["asset_set_definition_id"],
            "reserve_model_version": allocation["reserve_return_model_version_id"],
            "reserve_model_artifact": allocation["reserve_return_model_artifact_id"],
            "status": package_binding["research_status"],
            "set_count": len(supported_sets),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO defense.v022_defense_package_supported_asset_set (
              defense_version_id,ordinal,asset_context_key,asset_registry_release_id,
              asset_registry_artifact_id,asset_set_definition_id
            ) VALUES (
              :defense,:ordinal,:key,:registry_release,:registry_artifact,:asset_set
            )
            """
        ),
        [
            {
                "defense": version_id,
                "ordinal": item["ordinal"],
                "key": item["asset_context_key"],
                "registry_release": item["asset_registry_release_id"],
                "registry_artifact": item["asset_registry_artifact_id"],
                "asset_set": item["asset_set_definition_id"],
            }
            for item in supported_sets
        ],
    )


def _id_for_artifact(
    connection: Connection, table: str, id_column: str, artifact_id: uuid.UUID
) -> uuid.UUID:
    if not all(part.replace("_", "").isalnum() for part in (*table.split("."), id_column)):
        raise ValueError("Unsafe SQL identifier")
    return cast(
        uuid.UUID,
        connection.execute(
            text(f"SELECT {id_column} FROM {table} WHERE artifact_id = :artifact_id"),
            {"artifact_id": artifact_id},
        ).scalar_one(),
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
