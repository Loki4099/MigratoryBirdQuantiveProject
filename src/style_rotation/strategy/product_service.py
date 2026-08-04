from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput


@dataclass(frozen=True, slots=True)
class StrategyProductPublication:
    product_key: str
    definition_artifact_id: uuid.UUID
    version_artifact_id: uuid.UUID
    model_specification_key: str
    strategy_variant_key: str
    schedule_key: str
    universe_key: str
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["definition_artifact_id"] = str(self.definition_artifact_id)
        payload["version_artifact_id"] = str(self.version_artifact_id)
        return payload


def publish_strategy_product(
    engine: Engine,
    strategy_catalog_artifact_id: uuid.UUID,
    model_catalog_artifact_id: uuid.UUID,
    universe_artifact_id: uuid.UUID,
    model_specification_key: str,
    strategy_variant_key: str,
    schedule_key: str,
) -> StrategyProductPublication:
    with engine.begin() as connection:
        _published(connection, strategy_catalog_artifact_id, "strategy_catalog_materialization")
        _published(connection, model_catalog_artifact_id, "model_catalog_materialization")
        model = _model_specification(connection, model_catalog_artifact_id, model_specification_key)
        variant = _strategy_member(
            connection,
            strategy_catalog_artifact_id,
            "strategy_variant",
            "strategy.strategy_variant",
            "strategy_variant_id",
            "variant_key",
            strategy_variant_key,
        )
        schedule = _strategy_member(
            connection,
            strategy_catalog_artifact_id,
            "rebalance_schedule_version",
            "ops.rebalance_schedule_version",
            "rebalance_schedule_version_id",
            "schedule_key",
            schedule_key,
            definition_join=(
                "ops.rebalance_schedule_definition",
                "rebalance_schedule_definition_id",
            ),
        )
        execution = _single_strategy_member(
            connection,
            strategy_catalog_artifact_id,
            "execution_policy_version",
            "ops.execution_policy_version",
            "execution_policy_version_id",
        )
        universe = _universe(connection, universe_artifact_id)
        _validate_compatibility(connection, model, variant, universe)

        product_key = "__".join(
            (
                str(model["specification_key"]),
                str(variant["variant_key"]),
                str(schedule["schedule_key"]),
                str(universe["universe_key"]),
            )
        )
        short_key = f"strategy_product:{sha256_hexdigest(product_key)[:24]}"
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        definition_payload = {"product_key": product_key}
        definition = service.publish(
            artifact_type="strategy_product_definition",
            artifact_key=short_key,
            version_number=1,
            semantic_payload=definition_payload,
            content_payload=definition_payload,
            reason=f"publish strategy product definition {short_key}",
            draft_writer=lambda conn, artifact_id: _write_definition(
                conn, artifact_id, product_key
            ),
        )
        definition_id = connection.execute(
            text(
                "SELECT strategy_product_definition_id "
                "FROM strategy.strategy_product_definition WHERE artifact_id = :artifact"
            ),
            {"artifact": definition.artifact_id},
        ).scalar_one()
        version_payload = {
            "product_key": product_key,
            "model_specification_key": model["specification_key"],
            "strategy_variant_key": variant["variant_key"],
            "universe_key": universe["universe_key"],
            "schedule_key": schedule["schedule_key"],
            "execution_policy_key": execution["policy_key"],
        }
        version = service.publish(
            artifact_type="strategy_product_version",
            artifact_key=short_key,
            version_number=1,
            semantic_payload=version_payload,
            content_payload=version_payload,
            dependencies=(
                DependencyInput(definition.artifact_id, "product_definition", 0),
                DependencyInput(model["artifact_id"], "model_specification", 1),
                DependencyInput(variant["artifact_id"], "strategy_variant", 2),
                DependencyInput(universe_artifact_id, "universe_version", 3),
                DependencyInput(schedule["artifact_id"], "rebalance_schedule_version", 4),
                DependencyInput(execution["artifact_id"], "execution_policy_version", 5),
            ),
            reason=f"publish strategy product version {short_key} v1",
            draft_writer=lambda conn, artifact_id: _write_version(
                conn,
                artifact_id,
                definition_id,
                model["model_specification_id"],
                variant["strategy_variant_id"],
                universe["universe_version_id"],
                schedule["rebalance_schedule_version_id"],
                execution["execution_policy_version_id"],
            ),
        )
    return StrategyProductPublication(
        product_key,
        definition.artifact_id,
        version.artifact_id,
        str(model["specification_key"]),
        str(variant["variant_key"]),
        str(schedule["schedule_key"]),
        str(universe["universe_key"]),
        definition.reused and version.reused,
    )


def _model_specification(connection: Connection, catalog_id: uuid.UUID, key: str) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT specification.*, artifact.artifact_id "
                "FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = "
                "dependency.depends_on_artifact_id AND artifact.artifact_type = "
                "'model_specification' AND artifact.status = 'published' "
                "JOIN model.model_specification specification ON specification.artifact_id = "
                "artifact.artifact_id WHERE dependency.artifact_id = :catalog "
                "AND dependency.role = 'materialized_member' "
                "AND specification.specification_key = :key"
            ),
            {"catalog": catalog_id, "key": key},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Model specification is not a member of the supplied Model catalog")
    return row


def _strategy_member(
    connection: Connection,
    catalog_id: uuid.UUID,
    artifact_type: str,
    table: str,
    id_column: str,
    key_column: str,
    key: str,
    definition_join: tuple[str, str] | None = None,
) -> RowMapping:
    if definition_join is None:
        select_key = f"business.{key_column}"
        join = ""
    else:
        definition_table, definition_id = definition_join
        select_key = f"definition.{key_column}"
        join = (
            f" JOIN {definition_table} definition ON definition.{definition_id} = "
            f"business.{definition_id}"
        )
    row = (
        connection.execute(
            text(
                f"SELECT business.{id_column}, business.artifact_id, "
                f"{select_key} AS {key_column} FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = "
                "dependency.depends_on_artifact_id AND artifact.artifact_type = :type "
                f"JOIN {table} business ON business.artifact_id = artifact.artifact_id{join} "
                "WHERE dependency.artifact_id = :catalog AND dependency.role = "
                f"'materialized_member' AND {select_key} = :key"
            ),
            {"catalog": catalog_id, "type": artifact_type, "key": key},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"{artifact_type} is not a member of the supplied Strategy catalog")
    return row


def _single_strategy_member(
    connection: Connection,
    catalog_id: uuid.UUID,
    artifact_type: str,
    table: str,
    id_column: str,
) -> RowMapping:
    row = (
        connection.execute(
            text(
                f"SELECT business.{id_column}, business.artifact_id, definition.policy_key "
                "FROM lineage.artifact_dependency dependency JOIN lineage.artifact artifact ON "
                "artifact.artifact_id = dependency.depends_on_artifact_id "
                f"AND artifact.artifact_type = :type JOIN {table} business ON "
                "business.artifact_id = artifact.artifact_id "
                "JOIN ops.execution_policy_definition definition ON "
                "definition.execution_policy_definition_id = "
                "business.execution_policy_definition_id WHERE dependency.artifact_id = "
                ":catalog AND dependency.role = 'materialized_member'"
            ),
            {"catalog": catalog_id, "type": artifact_type},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Strategy catalog must contain exactly one execution policy")
    return row


def _universe(connection: Connection, artifact_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT version.universe_version_id, version.artifact_id, "
                "definition.universe_key FROM catalog.universe_version version "
                "JOIN catalog.universe_definition definition ON "
                "definition.universe_definition_id = version.universe_definition_id "
                "WHERE version.artifact_id = :artifact"
            ),
            {"artifact": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Supplied artifact is not a Universe version")
    _published(connection, artifact_id, "universe_version")
    return row


def _validate_compatibility(
    connection: Connection, model: RowMapping, variant: RowMapping, universe: RowMapping
) -> None:
    output_types = connection.execute(
        text(
            "SELECT compatible_model_output_types FROM strategy.strategy_input_contract "
            "WHERE strategy_input_contract_id = (SELECT strategy_input_contract_id "
            "FROM strategy.strategy_variant WHERE strategy_variant_id = :variant)"
        ),
        {"variant": variant["strategy_variant_id"]},
    ).scalar_one()
    if model["output_type"] not in output_types:
        raise ValueError("Model output type is incompatible with the Strategy input contract")
    if model["specification_type"] == "single_signal":
        eligible = connection.execute(
            text(
                "SELECT bool_and(definition.product_eligible) FROM model.model_component component "
                "JOIN signal.signal_version version ON version.signal_version_id = "
                "component.signal_version_id JOIN signal.signal_definition definition ON "
                "definition.signal_definition_id = version.signal_definition_id "
                "WHERE component.model_specification_id = :model"
            ),
            {"model": model["model_specification_id"]},
        ).scalar_one()
        if not eligible:
            raise ValueError("Single-Signal model is not eligible for a Strategy Product")
    candidate_count = connection.execute(
        text(
            "SELECT count(*) FROM catalog.universe_member WHERE universe_version_id = :universe "
            "AND role = 'candidate'"
        ),
        {"universe": universe["universe_version_id"]},
    ).scalar_one()
    target_k = connection.execute(
        text("SELECT target_k FROM strategy.strategy_variant WHERE strategy_variant_id = :variant"),
        {"variant": variant["strategy_variant_id"]},
    ).scalar_one()
    if candidate_count < target_k:
        raise ValueError("Universe candidate count is smaller than Strategy K")


def _published(connection: Connection, artifact_id: uuid.UUID, artifact_type: str) -> None:
    found = connection.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM lineage.artifact WHERE artifact_id = :artifact "
            "AND artifact_type = :type AND status = 'published')"
        ),
        {"artifact": artifact_id, "type": artifact_type},
    ).scalar_one()
    if not found:
        raise ValueError(f"Expected published {artifact_type} artifact")


def _write_definition(connection: Connection, artifact_id: uuid.UUID, product_key: str) -> None:
    connection.execute(
        text(
            "INSERT INTO strategy.strategy_product_definition "
            "(strategy_product_definition_id, artifact_id, product_key) "
            "VALUES (:id, :artifact, :key)"
        ),
        {"id": uuid.uuid4(), "artifact": artifact_id, "key": product_key},
    )


def _write_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    model_id: uuid.UUID,
    variant_id: uuid.UUID,
    universe_id: uuid.UUID,
    schedule_id: uuid.UUID,
    execution_id: uuid.UUID,
) -> None:
    connection.execute(
        text(
            "INSERT INTO strategy.strategy_product_version "
            "(strategy_product_version_id, strategy_product_definition_id, artifact_id, "
            "model_specification_id, strategy_variant_id, universe_version_id, "
            "rebalance_schedule_version_id, execution_policy_version_id, version_number) "
            "VALUES (:id, :definition, :artifact, :model, :variant, :universe, :schedule, "
            ":execution, 1)"
        ),
        {
            "id": uuid.uuid4(),
            "definition": definition_id,
            "artifact": artifact_id,
            "model": model_id,
            "variant": variant_id,
            "universe": universe_id,
            "schedule": schedule_id,
            "execution": execution_id,
        },
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
