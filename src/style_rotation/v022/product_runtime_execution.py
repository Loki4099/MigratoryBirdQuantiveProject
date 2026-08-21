from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.aggregation_work_runtime import AggregationCalculation
from style_rotation.v022.product_runtime_pipeline import ProductTargetCalculation
from style_rotation.v022.runtime_output_payloads import (
    CanonicalRuntimePayload,
    adapt_defense_budget_decisions,
    adapt_merged_portfolio_targets,
    adapt_strategy_unit_risk_targets,
)

ProductRuntimeStageKind = Literal["aggregation", "strategy", "defense", "merge"]


@dataclass(frozen=True, slots=True)
class ProductRuntimeExecutionPublication:
    product_runtime_execution_id: uuid.UUID
    artifact_id: uuid.UUID
    product_input_snapshot_id: uuid.UUID
    configuration_snapshot_id: uuid.UUID
    decision_session_id: uuid.UUID
    runtime_version: str
    execution_fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class ProductRuntimeStageInput:
    role: str
    artifact_id: uuid.UUID

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("Product Runtime Stage input role must be nonblank")


@dataclass(frozen=True, slots=True)
class ProductRuntimeStagePublication:
    product_runtime_stage_id: uuid.UUID
    product_runtime_execution_id: uuid.UUID
    stage_kind: ProductRuntimeStageKind
    artifact_id: uuid.UUID
    stage_fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class ProductRuntimeTargetStages:
    strategy: ProductRuntimeStagePublication
    defense: ProductRuntimeStagePublication | None
    merge: ProductRuntimeStagePublication


class ProductRuntimeExecutionService:
    """Publish one exact Product-session runtime chain without Suite identities."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish_execution(
        self,
        *,
        product_input_snapshot_id: uuid.UUID,
        runtime_version: str,
    ) -> ProductRuntimeExecutionPublication:
        runtime_version = runtime_version.strip()
        if not runtime_version:
            raise ValueError("Product runtime version must be nonblank")
        with self._engine.connect() as connection:
            source = _execution_source(connection, product_input_snapshot_id)
        document = {
            "contract_version": "v0.22.product_runtime_execution.v1",
            "product_input_snapshot_id": str(product_input_snapshot_id),
            "configuration_snapshot_id": str(source["configuration_snapshot_id"]),
            "decision_session_id": str(source["decision_session_id"]),
            "runtime_version": runtime_version,
            "runtime_network_access": False,
        }
        fingerprint = sha256_hexdigest(document)
        execution_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:product-runtime-execution:{fingerprint}"
        )

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO product.v022_product_runtime_execution (
                      product_runtime_execution_id,artifact_id,product_input_snapshot_id,
                      configuration_snapshot_id,decision_session_id,runtime_version,
                      execution_document,execution_fingerprint
                    ) VALUES (
                      :execution,:artifact,:snapshot,:configuration,:session,:runtime_version,
                      CAST(:document AS jsonb),:fingerprint
                    )
                    """
                ),
                {
                    "execution": execution_id,
                    "artifact": artifact_id,
                    "snapshot": product_input_snapshot_id,
                    "configuration": source["configuration_snapshot_id"],
                    "session": source["decision_session_id"],
                    "runtime_version": runtime_version,
                    "document": _json(document),
                    "fingerprint": fingerprint,
                },
            )

        publication = self._artifacts.publish(
            artifact_type="v022_product_runtime_execution",
            artifact_key=(
                f"v022_product_runtime_execution__{product_input_snapshot_id}__"
                f"{runtime_version}"
            ),
            version_number=1,
            semantic_payload=document,
            content_payload=document,
            dependencies=(
                DependencyInput(
                    cast(uuid.UUID, source["product_input_snapshot_artifact_id"]),
                    "product_input_snapshot",
                    0,
                ),
                DependencyInput(
                    cast(uuid.UUID, source["configuration_snapshot_artifact_id"]),
                    "configuration_snapshot",
                    1,
                ),
            ),
            reason="publish immutable v0.22 Product Runtime Execution",
            draft_writer=writer,
        )
        return ProductRuntimeExecutionPublication(
            execution_id,
            publication.artifact_id,
            product_input_snapshot_id,
            cast(uuid.UUID, source["configuration_snapshot_id"]),
            cast(uuid.UUID, source["decision_session_id"]),
            runtime_version,
            fingerprint,
            publication.reused,
        )

    def publish_aggregation(
        self,
        *,
        product_runtime_execution_id: uuid.UUID,
        calculation: AggregationCalculation,
        processing_manifest_artifact_ids: tuple[uuid.UUID, ...],
        active_model_state_artifact_id: uuid.UUID | None = None,
    ) -> ProductRuntimeStagePublication:
        if not processing_manifest_artifact_ids:
            raise ValueError("Product aggregation requires Processing Manifest inputs")
        if len(processing_manifest_artifact_ids) != len(
            set(processing_manifest_artifact_ids)
        ):
            raise ValueError("Product aggregation Processing Manifest inputs must be unique")
        payload = {
            "family_key": calculation.family_key,
            "parameter_preset_key": calculation.parameter_preset_key,
            "calculation_fingerprint": calculation.calculation_fingerprint,
            "points": [
                {
                    "decision_date": item.decision_date.isoformat(),
                    "asset_id": str(item.asset_id),
                    "asset_key": item.asset_key,
                    "signal_value": (
                        str(item.signal_value) if item.signal_value is not None else None
                    ),
                    "known_at": item.known_at.astimezone(UTC).isoformat(),
                    "input_revision": item.input_revision,
                    "missing_reason": item.missing_reason,
                }
                for item in calculation.points
            ],
        }
        stage_inputs = [
            ProductRuntimeStageInput("processing_manifest", artifact_id)
            for artifact_id in processing_manifest_artifact_ids
        ]
        if active_model_state_artifact_id is not None:
            stage_inputs.append(
                ProductRuntimeStageInput(
                    "active_model_state", active_model_state_artifact_id
                )
            )
        return self._publish_stage(
            product_runtime_execution_id=product_runtime_execution_id,
            stage_kind="aggregation",
            payload_document=payload,
            inputs=tuple(stage_inputs),
        )

    def publish_targets(
        self,
        *,
        product_runtime_execution_id: uuid.UUID,
        aggregation_stage: ProductRuntimeStagePublication,
        calculation: ProductTargetCalculation,
        compiled_strategy_branch_id: uuid.UUID,
        defense_version_id: uuid.UUID | None = None,
        timing_policy_version_id: uuid.UUID | None = None,
        allocation_policy_version_id: uuid.UUID | None = None,
    ) -> ProductRuntimeTargetStages:
        execution = self._execution(product_runtime_execution_id)
        if aggregation_stage.product_runtime_execution_id != product_runtime_execution_id:
            raise ValueError("Aggregation Stage belongs to another Product Runtime Execution")
        if aggregation_stage.stage_kind != "aggregation":
            raise ValueError("Product target publication requires an Aggregation Stage")
        strategy_payload = adapt_strategy_unit_risk_targets(
            (calculation.strategy_target,),
            work_execution_fingerprint=_stage_work_fingerprint(
                str(execution["execution_fingerprint"]), "strategy"
            ),
        )
        strategy = self._publish_stage(
            product_runtime_execution_id=product_runtime_execution_id,
            stage_kind="strategy",
            payload_document=_canonical_payload_document(strategy_payload),
            inputs=(
                ProductRuntimeStageInput(
                    "aggregation_output", aggregation_stage.artifact_id
                ),
            ),
        )
        defense: ProductRuntimeStagePublication | None = None
        if calculation.defense_decision is not None:
            if None in {
                defense_version_id,
                timing_policy_version_id,
                allocation_policy_version_id,
            }:
                raise ValueError(
                    "Defense runtime identity IDs are required for a Defense decision"
                )
            defense_payload = adapt_defense_budget_decisions(
                (calculation.defense_decision,),
                work_execution_fingerprint=_stage_work_fingerprint(
                    str(execution["execution_fingerprint"]), "defense"
                ),
                defense_version_id=cast(uuid.UUID, defense_version_id),
                timing_policy_version_id=cast(uuid.UUID, timing_policy_version_id),
                allocation_policy_version_id=cast(
                    uuid.UUID, allocation_policy_version_id
                ),
            )
            defense = self._publish_stage(
                product_runtime_execution_id=product_runtime_execution_id,
                stage_kind="defense",
                payload_document=_canonical_payload_document(defense_payload),
                inputs=(),
            )
        elif any(
            item is not None
            for item in (
                defense_version_id,
                timing_policy_version_id,
                allocation_policy_version_id,
            )
        ):
            raise ValueError("None Defense must not carry Defense runtime identity IDs")
        merged_payload = adapt_merged_portfolio_targets(
            (calculation.merged_target,),
            work_execution_fingerprint=_stage_work_fingerprint(
                str(execution["execution_fingerprint"]), "merge"
            ),
            compiled_strategy_branch_id=compiled_strategy_branch_id,
        )
        merge_inputs = [ProductRuntimeStageInput("strategy_target", strategy.artifact_id)]
        if defense is not None:
            merge_inputs.append(
                ProductRuntimeStageInput("defense_decision", defense.artifact_id)
            )
        merge = self._publish_stage(
            product_runtime_execution_id=product_runtime_execution_id,
            stage_kind="merge",
            payload_document=_canonical_payload_document(merged_payload),
            inputs=tuple(merge_inputs),
        )
        return ProductRuntimeTargetStages(strategy, defense, merge)

    def _publish_stage(
        self,
        *,
        product_runtime_execution_id: uuid.UUID,
        stage_kind: ProductRuntimeStageKind,
        payload_document: Mapping[str, Any],
        inputs: tuple[ProductRuntimeStageInput, ...],
    ) -> ProductRuntimeStagePublication:
        execution = self._execution(product_runtime_execution_id)
        _validate_stage_inputs(stage_kind, inputs)
        ordered_inputs = (
            ProductRuntimeStageInput(
                "runtime_execution", cast(uuid.UUID, execution["artifact_id"])
            ),
            *inputs,
        )
        document: dict[str, Any] = {
            "contract_version": "v0.22.product_runtime_stage.v1",
            "product_runtime_execution_id": str(product_runtime_execution_id),
            "stage_kind": stage_kind,
            "input_count": len(ordered_inputs),
            "payload_document": dict(payload_document),
        }
        fingerprint = sha256_hexdigest(document)
        stage_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bird:v0.22:product-runtime-stage:{fingerprint}",
        )
        artifact_type = {
            "aggregation": "v022_product_aggregation_output",
            "strategy": "v022_product_strategy_target",
            "defense": "v022_product_defense_decision",
            "merge": "v022_product_merged_target",
        }[stage_kind]

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO product.v022_product_runtime_stage (
                      product_runtime_stage_id,product_runtime_execution_id,stage_kind,
                      artifact_id,input_count,stage_document,stage_fingerprint
                    ) VALUES (
                      :stage,:execution,:kind,:artifact,:input_count,
                      CAST(:document AS jsonb),:fingerprint
                    )
                    """
                ),
                {
                    "stage": stage_id,
                    "execution": product_runtime_execution_id,
                    "kind": stage_kind,
                    "artifact": artifact_id,
                    "input_count": len(ordered_inputs),
                    "document": _json(document),
                    "fingerprint": fingerprint,
                },
            )
            for ordinal, item in enumerate(ordered_inputs):
                connection.execute(
                    text(
                        """
                        INSERT INTO product.v022_product_runtime_stage_input (
                          product_runtime_stage_id,ordinal,role,input_artifact_id
                        ) VALUES (:stage,:ordinal,:role,:artifact)
                        """
                    ),
                    {
                        "stage": stage_id,
                        "ordinal": ordinal,
                        "role": item.role,
                        "artifact": item.artifact_id,
                    },
                )

        publication = self._artifacts.publish(
            artifact_type=artifact_type,
            artifact_key=(
                f"{artifact_type}__{execution['execution_fingerprint']}"
            ),
            version_number=1,
            semantic_payload=document,
            content_payload=document,
            dependencies=tuple(
                DependencyInput(item.artifact_id, item.role, ordinal)
                for ordinal, item in enumerate(ordered_inputs)
            ),
            reason=f"publish immutable v0.22 Product Runtime {stage_kind} Stage",
            draft_writer=writer,
        )
        return ProductRuntimeStagePublication(
            stage_id,
            product_runtime_execution_id,
            stage_kind,
            publication.artifact_id,
            fingerprint,
            publication.reused,
        )

    def _execution(self, execution_id: uuid.UUID) -> RowMapping:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT execution.*,artifact.status
                          FROM product.v022_product_runtime_execution execution
                          JOIN lineage.artifact artifact
                            ON artifact.artifact_id=execution.artifact_id
                         WHERE execution.product_runtime_execution_id=:execution
                        """
                    ),
                    {"execution": execution_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None or row["status"] != "published":
            raise ValueError("Product Runtime Execution is not published")
        return row


def _execution_source(
    connection: Connection, product_input_snapshot_id: uuid.UUID
) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
                SELECT snapshot.decision_session_id,snapshot.artifact_id AS
                         product_input_snapshot_artifact_id,
                       snapshot_artifact.status AS snapshot_status,
                       execution.configuration_snapshot_id,
                       configuration.artifact_id AS configuration_snapshot_artifact_id,
                       configuration_artifact.status AS configuration_status
                  FROM product.v022_product_input_snapshot snapshot
                  JOIN lineage.artifact snapshot_artifact
                    ON snapshot_artifact.artifact_id=snapshot.artifact_id
                  JOIN product.v022_execution_version execution
                    ON execution.execution_version_id=snapshot.execution_version_id
                  JOIN experiment.v022_research_configuration_snapshot configuration
                    ON configuration.configuration_snapshot_id=
                       execution.configuration_snapshot_id
                  JOIN lineage.artifact configuration_artifact
                    ON configuration_artifact.artifact_id=configuration.artifact_id
                 WHERE snapshot.product_input_snapshot_id=:snapshot
                """
            ),
            {"snapshot": product_input_snapshot_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Product Input Snapshot was not found")
    if row["snapshot_status"] != "published" or row["configuration_status"] != "published":
        raise ValueError("Product Runtime source identities must be published")
    return row


def _validate_stage_inputs(
    stage_kind: ProductRuntimeStageKind,
    inputs: tuple[ProductRuntimeStageInput, ...],
) -> None:
    if len({item.artifact_id for item in inputs}) != len(inputs):
        raise ValueError("Product Runtime Stage inputs must be unique")
    expected_roles: tuple[str, ...]
    if stage_kind == "aggregation":
        if not inputs:
            raise ValueError("Aggregation Stage requires Processing Manifest inputs")
        roles = tuple(item.role for item in inputs)
        if any(role not in {"processing_manifest", "active_model_state"} for role in roles):
            raise ValueError(
                "Aggregation Stage accepts only Processing Manifests and Model State"
            )
        if roles.count("active_model_state") > 1 or "active_model_state" in roles[:-1]:
            raise ValueError("Aggregation Stage Model State input topology is invalid")
        return
    if stage_kind == "strategy":
        expected_roles = ("aggregation_output",)
    elif stage_kind == "defense":
        expected_roles = ()
    else:
        expected_roles = (
            ("strategy_target",)
            if len(inputs) == 1
            else ("strategy_target", "defense_decision")
        )
    if tuple(item.role for item in inputs) != expected_roles:
        raise ValueError(f"Product Runtime {stage_kind} Stage input topology is invalid")


def _stage_work_fingerprint(execution_fingerprint: str, stage_kind: str) -> str:
    return sha256_hexdigest(
        {"execution_fingerprint": execution_fingerprint, "stage_kind": stage_kind}
    )


def _canonical_payload_document(payload: CanonicalRuntimePayload) -> dict[str, Any]:
    return {
        "contract_key": payload.contract_key,
        "contract_version": payload.contract_version,
        "output_port_key": payload.output_port_key,
        "work_execution_fingerprint": payload.work_execution_fingerprint,
        "canonical_document_fingerprint": payload.canonical_document_fingerprint,
        "row_or_item_count": payload.row_or_item_count,
        "document": payload.document,
    }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
