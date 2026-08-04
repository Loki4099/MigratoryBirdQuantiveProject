from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult
from style_rotation.strategy.contracts import (
    ExecutionPolicySeed,
    ExpandedStrategyVariant,
    ScheduleSeed,
    StrategyCatalog,
    StrategyInputContractSeed,
    expand_strategy_variants,
)


@dataclass(frozen=True, slots=True)
class StrategyCatalogPublication:
    release_artifact_id: uuid.UUID
    definition_count: int
    definition_version_count: int
    input_contract_count: int
    variant_count: int
    schedule_count: int
    execution_policy_count: int
    reused_count: int
    artifact_ids: tuple[uuid.UUID, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["release_artifact_id"] = str(self.release_artifact_id)
        payload["artifact_ids"] = [str(item) for item in self.artifact_ids]
        return payload


def publish_strategy_catalog(engine: Engine, catalog_path: Path) -> StrategyCatalogPublication:
    raw_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog = StrategyCatalog.model_validate(raw_catalog)
    variants = expand_strategy_variants(catalog)
    with engine.begin() as connection:
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        source_catalog_id = _catalog_artifact(connection, catalog.catalog_version, raw_catalog)
        trend_signal = _signal_version(connection, source_catalog_id, catalog.trend_signal)
        results: list[PublicationResult] = []

        for schedule in catalog.schedules:
            results.extend(_publish_schedule(service, connection, schedule))
        results.extend(_publish_execution_policy(service, connection, catalog.execution_policy))
        definition_results, definition_version = _publish_definition(service, connection, catalog)
        results.extend(definition_results)

        contracts: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
        for contract in catalog.input_contracts:
            published, contract_identity = _publish_input_contract(
                service, connection, definition_version, contract
            )
            results.append(published)
            contracts[contract.key] = contract_identity

        for variant in variants:
            results.append(
                _publish_variant(
                    service,
                    connection,
                    definition_version,
                    contracts[variant.input_contract_key],
                    trend_signal,
                    variant,
                )
            )
        release = service.publish(
            artifact_type="strategy_catalog_materialization",
            artifact_key="strategy_catalog",
            version_number=semantic_version_number(catalog.catalog_version),
            semantic_payload={
                "catalog_version": catalog.catalog_version,
                "definition_count": 1,
                "definition_version_count": 1,
                "input_contract_count": len(catalog.input_contracts),
                "variant_count": len(variants),
                "schedule_count": len(catalog.schedules),
                "execution_policy_count": 1,
                "product_count": 0,
                "product_materialization_milestone": "M6B",
            },
            content_payload={"member_artifact_ids": [str(item.artifact_id) for item in results]},
            dependencies=(
                DependencyInput(source_catalog_id, "source_catalog", 0),
                *tuple(
                    DependencyInput(item.artifact_id, "materialized_member", ordinal + 1)
                    for ordinal, item in enumerate(results)
                ),
            ),
            reason=f"materialize strategy catalog {catalog.catalog_version}",
        )
    return StrategyCatalogPublication(
        release_artifact_id=release.artifact_id,
        definition_count=1,
        definition_version_count=1,
        input_contract_count=len(catalog.input_contracts),
        variant_count=len(variants),
        schedule_count=len(catalog.schedules),
        execution_policy_count=1,
        reused_count=sum(item.reused for item in results) + int(release.reused),
        artifact_ids=tuple(item.artifact_id for item in results),
    )


def _publish_schedule(
    service: ArtifactService, connection: Connection, seed: ScheduleSeed
) -> list[PublicationResult]:
    definition_payload = {"schedule_key": seed.key}
    definition = service.publish(
        artifact_type="rebalance_schedule_definition",
        artifact_key=seed.key,
        version_number=1,
        semantic_payload=definition_payload,
        content_payload=definition_payload,
        reason=f"publish rebalance schedule definition {seed.key}",
        draft_writer=lambda conn, artifact_id: _write_schedule_definition(
            conn, artifact_id, seed.key
        ),
    )
    definition_id = connection.execute(
        text(
            "SELECT rebalance_schedule_definition_id "
            "FROM ops.rebalance_schedule_definition WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": definition.artifact_id},
    ).scalar_one()
    payload = seed.model_dump(mode="json", exclude={"key"})
    version = service.publish(
        artifact_type="rebalance_schedule_version",
        artifact_key=seed.key,
        version_number=1,
        semantic_payload=payload,
        content_payload=payload,
        dependencies=(DependencyInput(definition.artifact_id, "schedule_definition", 0),),
        reason=f"publish rebalance schedule version {seed.key} v1",
        draft_writer=lambda conn, artifact_id: _write_schedule_version(
            conn, artifact_id, definition_id, payload
        ),
    )
    return [definition, version]


def _publish_execution_policy(
    service: ArtifactService, connection: Connection, seed: ExecutionPolicySeed
) -> list[PublicationResult]:
    definition_payload = {"policy_key": seed.key}
    definition = service.publish(
        artifact_type="execution_policy_definition",
        artifact_key=seed.key,
        version_number=1,
        semantic_payload=definition_payload,
        content_payload=definition_payload,
        reason=f"publish execution policy definition {seed.key}",
        draft_writer=lambda conn, artifact_id: _write_execution_definition(
            conn, artifact_id, seed.key
        ),
    )
    definition_id = connection.execute(
        text(
            "SELECT execution_policy_definition_id FROM ops.execution_policy_definition "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": definition.artifact_id},
    ).scalar_one()
    payload = seed.model_dump(mode="json", exclude={"key"})
    version = service.publish(
        artifact_type="execution_policy_version",
        artifact_key=seed.key,
        version_number=1,
        semantic_payload=payload,
        content_payload=payload,
        dependencies=(DependencyInput(definition.artifact_id, "execution_policy_definition", 0),),
        reason=f"publish execution policy version {seed.key} v1",
        draft_writer=lambda conn, artifact_id: _write_execution_version(
            conn, artifact_id, definition_id, payload
        ),
    )
    return [definition, version]


def _publish_definition(
    service: ArtifactService, connection: Connection, catalog: StrategyCatalog
) -> tuple[list[PublicationResult], tuple[uuid.UUID, uuid.UUID]]:
    payload = catalog.definition.model_dump(mode="json")
    definition = service.publish(
        artifact_type="strategy_definition",
        artifact_key=catalog.definition.key,
        version_number=1,
        semantic_payload=payload,
        content_payload=payload,
        reason=f"publish strategy definition {catalog.definition.key}",
        draft_writer=lambda conn, artifact_id: _write_strategy_definition(
            conn, artifact_id, payload
        ),
    )
    definition_id = connection.execute(
        text(
            "SELECT strategy_definition_id FROM strategy.strategy_definition "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": definition.artifact_id},
    ).scalar_one()
    version_payload = {
        "selection_contract": "cross_sectional_ranked_fixed_slots",
        "allocation_contract": catalog.slot_weight_rule,
        "reserve_contract": catalog.reserve_rule,
    }
    version = service.publish(
        artifact_type="strategy_definition_version",
        artifact_key=catalog.definition.key,
        version_number=1,
        semantic_payload=version_payload,
        content_payload=version_payload,
        dependencies=(DependencyInput(definition.artifact_id, "strategy_definition", 0),),
        reason=f"publish strategy definition version {catalog.definition.key} v1",
        draft_writer=lambda conn, artifact_id: _write_strategy_definition_version(
            conn, artifact_id, definition_id, version_payload
        ),
    )
    version_id = connection.execute(
        text(
            "SELECT strategy_definition_version_id FROM strategy.strategy_definition_version "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": version.artifact_id},
    ).scalar_one()
    return [definition, version], (version_id, version.artifact_id)


def _publish_input_contract(
    service: ArtifactService,
    connection: Connection,
    definition_version: tuple[uuid.UUID, uuid.UUID],
    seed: StrategyInputContractSeed,
) -> tuple[PublicationResult, tuple[uuid.UUID, uuid.UUID]]:
    payload = seed.model_dump(mode="json")
    result = service.publish(
        artifact_type="strategy_input_contract",
        artifact_key=seed.key,
        version_number=1,
        semantic_payload=payload,
        content_payload=payload,
        dependencies=(DependencyInput(definition_version[1], "strategy_definition_version", 0),),
        reason=f"publish strategy input contract {seed.key}",
        draft_writer=lambda conn, artifact_id: _write_input_contract(
            conn, artifact_id, definition_version[0], payload
        ),
    )
    contract_id = connection.execute(
        text(
            "SELECT strategy_input_contract_id FROM strategy.strategy_input_contract "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": result.artifact_id},
    ).scalar_one()
    return result, (contract_id, result.artifact_id)


def _publish_variant(
    service: ArtifactService,
    connection: Connection,
    definition_version: tuple[uuid.UUID, uuid.UUID],
    contract: tuple[uuid.UUID, uuid.UUID],
    trend_signal: tuple[uuid.UUID, uuid.UUID],
    variant: ExpandedStrategyVariant,
) -> PublicationResult:
    payload = variant.model_dump(mode="json")
    dependencies = [
        DependencyInput(definition_version[1], "strategy_definition_version", 0),
        DependencyInput(contract[1], "strategy_input_contract", 1),
    ]
    if variant.auxiliary_signal_key is not None:
        dependencies.append(DependencyInput(trend_signal[1], "auxiliary_signal_version", 2))
    return service.publish(
        artifact_type="strategy_variant",
        artifact_key=variant.key,
        version_number=1,
        semantic_payload=payload,
        content_payload=payload,
        dependencies=tuple(dependencies),
        reason=f"publish strategy variant {variant.key}",
        draft_writer=lambda conn, artifact_id: _write_variant(
            conn,
            artifact_id,
            definition_version[0],
            contract[0],
            trend_signal[0] if variant.auxiliary_signal_key is not None else None,
            variant,
        ),
    )


def _catalog_artifact(connection: Connection, catalog_version: str, raw: Any) -> uuid.UUID:
    version_number = semantic_version_number(catalog_version)
    row = (
        connection.execute(
            text(
                "SELECT artifact_id, content_hash FROM lineage.artifact "
                "WHERE artifact_type = 'research_catalog' "
                "AND artifact_key = 'strategy_catalog' AND version_number = :version "
                "AND status = 'published'"
            ),
            {"version": version_number},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Publish the matching M0 strategy research catalog before materialization")
    dependencies = (
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
    semantic_fingerprint = sha256_hexdigest(
        {
            "artifact_identity": {
                "artifact_type": "research_catalog",
                "artifact_key": "strategy_catalog",
                "version_number": version_number,
            },
            "semantic_payload": raw,
            "dependencies": [
                {
                    "role": item["role"],
                    "ordinal": item["ordinal"],
                    "semantic_fingerprint": item["semantic_fingerprint"],
                }
                for item in dependencies
            ],
        }
    )
    expected_hash = sha256_hexdigest(
        {
            "semantic_fingerprint": semantic_fingerprint,
            "content_payload": raw,
            "dependencies": [
                {
                    "role": item["role"],
                    "ordinal": item["ordinal"],
                    "content_hash": item["content_hash"],
                }
                for item in dependencies
            ],
        }
    )
    if row["content_hash"] != expected_hash:
        raise ValueError("Local strategy catalog does not match the published M0 artifact")
    artifact_id = row["artifact_id"]
    if not isinstance(artifact_id, uuid.UUID):
        raise RuntimeError("Strategy catalog artifact id must be a UUID")
    return artifact_id


def _signal_version(
    connection: Connection, strategy_catalog_id: uuid.UUID, signal_key: str
) -> tuple[uuid.UUID, uuid.UUID]:
    rows = (
        connection.execute(
            text(
                "SELECT version.signal_version_id, version.artifact_id "
                "FROM lineage.artifact_dependency source_dependency "
                "JOIN lineage.artifact signal_catalog ON signal_catalog.artifact_id = "
                "source_dependency.depends_on_artifact_id "
                "JOIN lineage.artifact_dependency release_source ON "
                "release_source.depends_on_artifact_id = signal_catalog.artifact_id "
                "AND release_source.role = 'source_catalog' "
                "JOIN lineage.artifact release ON release.artifact_id = release_source.artifact_id "
                "AND release.artifact_type = 'signal_catalog_materialization' "
                "AND release.status = 'published' "
                "JOIN lineage.artifact_dependency member ON member.artifact_id = "
                "release.artifact_id AND member.role = 'materialized_member' "
                "JOIN signal.signal_version version ON version.artifact_id = "
                "member.depends_on_artifact_id JOIN signal.signal_definition definition ON "
                "definition.signal_definition_id = version.signal_definition_id "
                "WHERE source_dependency.artifact_id = :strategy_catalog "
                "AND source_dependency.role = 'signal_catalog' "
                "AND definition.signal_key = :signal_key"
            ),
            {"strategy_catalog": strategy_catalog_id, "signal_key": signal_key},
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise ValueError(
            "Materialize the exact upstream Signal catalog containing the trend state Signal"
        )
    return rows[0]["signal_version_id"], rows[0]["artifact_id"]


def _write_schedule_definition(connection: Connection, artifact_id: uuid.UUID, key: str) -> None:
    connection.execute(
        text(
            "INSERT INTO ops.rebalance_schedule_definition "
            "(rebalance_schedule_definition_id, artifact_id, schedule_key) "
            "VALUES (:id, :artifact, :key)"
        ),
        {"id": uuid.uuid4(), "artifact": artifact_id, "key": key},
    )


def _write_schedule_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            "INSERT INTO ops.rebalance_schedule_version "
            "(rebalance_schedule_version_id, rebalance_schedule_definition_id, artifact_id, "
            "version_number, frequency, decision_timing, decision_data_policy) VALUES "
            "(:id, :definition, :artifact, 1, :frequency, :decision_timing, "
            ":decision_data_policy)"
        ),
        {"id": uuid.uuid4(), "definition": definition_id, "artifact": artifact_id, **payload},
    )


def _write_execution_definition(connection: Connection, artifact_id: uuid.UUID, key: str) -> None:
    connection.execute(
        text(
            "INSERT INTO ops.execution_policy_definition "
            "(execution_policy_definition_id, artifact_id, policy_key) "
            "VALUES (:id, :artifact, :key)"
        ),
        {"id": uuid.uuid4(), "artifact": artifact_id, "key": key},
    )


def _write_execution_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            "INSERT INTO ops.execution_policy_version "
            "(execution_policy_version_id, execution_policy_definition_id, artifact_id, "
            "version_number, delay_common_sessions, execution_price, missing_execution_policy) "
            "VALUES (:id, :definition, :artifact, 1, :delay_common_sessions, "
            ":execution_price, :missing_execution_policy)"
        ),
        {"id": uuid.uuid4(), "definition": definition_id, "artifact": artifact_id, **payload},
    )


def _write_strategy_definition(
    connection: Connection, artifact_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    connection.execute(
        text(
            "INSERT INTO strategy.strategy_definition "
            "(strategy_definition_id, artifact_id, strategy_key, strategy_family, hypothesis) "
            "VALUES (:id, :artifact, :key, :family, :hypothesis)"
        ),
        {"id": uuid.uuid4(), "artifact": artifact_id, **payload},
    )


def _write_strategy_definition_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            "INSERT INTO strategy.strategy_definition_version "
            "(strategy_definition_version_id, strategy_definition_id, artifact_id, "
            "version_number, selection_contract, allocation_contract, reserve_contract) VALUES "
            "(:id, :definition, :artifact, 1, :selection_contract, :allocation_contract, "
            ":reserve_contract)"
        ),
        {"id": uuid.uuid4(), "definition": definition_id, "artifact": artifact_id, **payload},
    )


def _write_input_contract(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_version_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            "INSERT INTO strategy.strategy_input_contract "
            "(strategy_input_contract_id, strategy_definition_version_id, artifact_id, "
            "contract_key, requires_model_score, compatible_model_output_types, "
            "candidate_input_policy, missing_input_policy) VALUES "
            "(:id, :definition, :artifact, :key, :requires_model_score, CAST(:outputs AS jsonb), "
            ":candidate_input_policy, :missing_input_policy)"
        ),
        {
            "id": uuid.uuid4(),
            "definition": definition_version_id,
            "artifact": artifact_id,
            "key": payload["key"],
            "requires_model_score": payload["requires_model_score"],
            "outputs": json.dumps(payload["compatible_model_output_types"]),
            "candidate_input_policy": payload["candidate_input_policy"],
            "missing_input_policy": payload["missing_input_policy"],
        },
    )


def _write_variant(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_version_id: uuid.UUID,
    contract_id: uuid.UUID,
    signal_version_id: uuid.UUID | None,
    variant: ExpandedStrategyVariant,
) -> None:
    connection.execute(
        text(
            "INSERT INTO strategy.strategy_variant "
            "(strategy_variant_id, strategy_definition_version_id, "
            "strategy_input_contract_id, artifact_id, auxiliary_signal_version_id, "
            "variant_key, template_key, target_k, research_tier, selection_order, trend_filter, "
            "auxiliary_eligible_state, empty_slot_policy, tie_policy, slot_weight_rule, "
            "reserve_rule) VALUES (:id, :definition, :contract, :artifact, :signal, :key, "
            ":template_key, :k, :preset_type, :selection_order, :trend_filter, "
            ":auxiliary_eligible_state, :empty_slot_policy, :tie_policy, :slot_weight_rule, "
            ":reserve_rule)"
        ),
        {
            "id": uuid.uuid4(),
            "definition": definition_version_id,
            "contract": contract_id,
            "artifact": artifact_id,
            "signal": signal_version_id,
            **variant.model_dump(mode="python"),
        },
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
