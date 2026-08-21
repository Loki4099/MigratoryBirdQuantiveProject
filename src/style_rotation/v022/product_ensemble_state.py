from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput


@dataclass(frozen=True, slots=True)
class ProductEnsembleStateMember:
    ordinal: int
    target_version_id: uuid.UUID
    training_preset_version_id: uuid.UUID
    fitted_model_state_id: uuid.UUID
    fitted_model_state_artifact_id: uuid.UUID
    target_key: str
    training_preset_key: str
    adapter_key: str
    adapter_version: str
    feature_schema_fingerprint: str
    state_fingerprint: str
    model_fingerprint: str


@dataclass(frozen=True, slots=True)
class ProductEnsembleStatePublication:
    product_ensemble_state_id: uuid.UUID
    artifact_id: uuid.UUID
    execution_version_id: uuid.UUID
    activated_decision_session_id: uuid.UUID
    state_fingerprint: str
    members: tuple[ProductEnsembleStateMember, ...]
    reused: bool


class ProductEnsembleStateService:
    """Freeze one complete experiment-trained member batch for Product inference."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish_initial(
        self,
        *,
        execution_version_id: uuid.UUID,
        result_evidence_snapshot_id: uuid.UUID,
        activated_decision_session_id: uuid.UUID,
    ) -> ProductEnsembleStatePublication | None:
        with self._engine.connect() as connection:
            source = _source(
                connection,
                execution_version_id=execution_version_id,
                result_evidence_snapshot_id=result_evidence_snapshot_id,
                activated_decision_session_id=activated_decision_session_id,
            )
            if source["execution_mode"] == "deterministic":
                return None
            members = _source_members(connection, source)
        if not members:
            raise ValueError("Supervised Product source has no complete fitted member states")
        expected_count = int(source["diagnostic_member_count"])
        if len(members) != expected_count or tuple(item.ordinal for item in members) != tuple(
            range(expected_count)
        ):
            raise ValueError("Supervised Product source member closure is incomplete")
        document: dict[str, object] = {
            "contract_version": "v0.22.product_ensemble_state.v1",
            "execution_version_id": str(execution_version_id),
            "configuration_snapshot_id": str(source["configuration_snapshot_id"]),
            "source_result_evidence_snapshot_id": str(result_evidence_snapshot_id),
            "source_aggregation_run_id": str(source["aggregation_run_id"]),
            "ensemble_spec_id": (
                None if source["ensemble_spec_id"] is None else str(source["ensemble_spec_id"])
            ),
            "activated_decision_session_id": str(activated_decision_session_id),
            "state_version_number": 1,
            "member_policy": "complete_atomic_member_set_v1",
            "failure_policy": "retain_previous_complete_state",
            "member_count": len(members),
            "members": [
                {
                    "ordinal": item.ordinal,
                    "target_version_id": str(item.target_version_id),
                    "training_preset_version_id": str(item.training_preset_version_id),
                    "fitted_model_state_id": str(item.fitted_model_state_id),
                    "fitted_model_state_artifact_id": str(
                        item.fitted_model_state_artifact_id
                    ),
                    "target_key": item.target_key,
                    "training_preset_key": item.training_preset_key,
                    "adapter_key": item.adapter_key,
                    "adapter_version": item.adapter_version,
                    "feature_schema_fingerprint": item.feature_schema_fingerprint,
                    "state_fingerprint": item.state_fingerprint,
                    "model_fingerprint": item.model_fingerprint,
                }
                for item in members
            ],
        }
        fingerprint = sha256_hexdigest(document)
        state_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:product-ensemble-state:{fingerprint}"
        )
        dependencies = [
            DependencyInput(source["execution_artifact_id"], "execution_version", 0),
            DependencyInput(source["evidence_artifact_id"], "result_evidence", 1),
            DependencyInput(source["diagnostic_artifact_id"], "trainable_diagnostic", 2),
        ]
        if source["ensemble_spec_artifact_id"] is not None:
            dependencies.append(
                DependencyInput(
                    source["ensemble_spec_artifact_id"],
                    "trainable_ensemble_spec",
                    len(dependencies),
                )
            )
        dependencies.extend(
            DependencyInput(
                item.fitted_model_state_artifact_id,
                "fitted_model_state",
                len(dependencies) + ordinal,
            )
            for ordinal, item in enumerate(members)
        )

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO product.v022_product_ensemble_state (
                      product_ensemble_state_id,artifact_id,execution_version_id,
                      configuration_snapshot_id,source_result_evidence_snapshot_id,
                      source_aggregation_run_id,ensemble_spec_id,
                      activated_decision_session_id,state_version_number,member_count,
                      state_document,state_fingerprint
                    ) VALUES (
                      :state,:artifact,:execution,:configuration,:evidence,:run,:spec,
                      :session,1,:count,CAST(:document AS jsonb),:fingerprint
                    )
                    """
                ),
                {
                    "state": state_id,
                    "artifact": artifact_id,
                    "execution": execution_version_id,
                    "configuration": source["configuration_snapshot_id"],
                    "evidence": result_evidence_snapshot_id,
                    "run": source["aggregation_run_id"],
                    "spec": source["ensemble_spec_id"],
                    "session": activated_decision_session_id,
                    "count": len(members),
                    "document": json.dumps(document, sort_keys=True),
                    "fingerprint": fingerprint,
                },
            )
            for item in members:
                connection.execute(
                    text(
                        """
                        INSERT INTO product.v022_product_ensemble_state_member (
                          product_ensemble_state_id,ordinal,target_version_id,
                          training_preset_version_id,fitted_model_state_id,
                          fitted_model_state_artifact_id
                        ) VALUES (
                          :state,:ordinal,:target,:preset,:model_state,:model_artifact
                        )
                        """
                    ),
                    {
                        "state": state_id,
                        "ordinal": item.ordinal,
                        "target": item.target_version_id,
                        "preset": item.training_preset_version_id,
                        "model_state": item.fitted_model_state_id,
                        "model_artifact": item.fitted_model_state_artifact_id,
                    },
                )

        publication = self._artifacts.publish(
            artifact_type="v022_product_ensemble_state",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=document,
            content_payload=document,
            dependencies=tuple(dependencies),
            reason="freeze complete trainable Product Ensemble State",
            draft_writer=writer,
        )
        return ProductEnsembleStatePublication(
            state_id,
            publication.artifact_id,
            execution_version_id,
            activated_decision_session_id,
            fingerprint,
            members,
            publication.reused,
        )


def _source(
    connection: Connection,
    *,
    execution_version_id: uuid.UUID,
    result_evidence_snapshot_id: uuid.UUID,
    activated_decision_session_id: uuid.UUID,
) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
                SELECT execution.configuration_snapshot_id,
                       execution.artifact_id AS execution_artifact_id,
                       evidence.artifact_id AS evidence_artifact_id,
                       aggregation_run.aggregation_run_id,
                       aggregation_run.ensemble_spec_id,
                       version.execution_mode,
                       diagnostic.artifact_id AS diagnostic_artifact_id,
                       diagnostic.member_count AS diagnostic_member_count,
                       ensemble.artifact_id AS ensemble_spec_artifact_id,
                       execution_artifact.status AS execution_status,
                       evidence_artifact.status AS evidence_status,
                       diagnostic_artifact.status AS diagnostic_status,
                       session.decision_session_id
                  FROM product.v022_execution_version execution
                  JOIN lineage.artifact execution_artifact
                    ON execution_artifact.artifact_id=execution.artifact_id
                  JOIN experiment.v022_result_evidence_snapshot evidence
                    ON evidence.result_evidence_snapshot_id=:evidence
                   AND evidence.configuration_snapshot_id=
                       execution.configuration_snapshot_id
                  JOIN lineage.artifact evidence_artifact
                    ON evidence_artifact.artifact_id=evidence.artifact_id
                  JOIN experiment.v022_portfolio_cell_runtime_result result
                    ON result.artifact_id=evidence.result_artifact_id
                  JOIN experiment.v022_portfolio_cell_work_spec cell_spec
                    ON cell_spec.graph_work_item_id=result.graph_work_item_id
                  JOIN experiment.v022_suite_runtime_plan plan
                    ON plan.suite_runtime_plan_id=cell_spec.suite_runtime_plan_id
                  JOIN strategy.v022_compiled_strategy_branch branch
                    ON branch.compiled_strategy_branch_id=
                       cell_spec.compiled_strategy_branch_id
                  JOIN aggregation.graph_run_aggregation_binding run_binding
                    ON run_binding.graph_run_id=plan.graph_run_id
                   AND run_binding.compiled_aggregation_instance_id=
                       branch.compiled_aggregation_instance_id
                  JOIN aggregation.aggregation_run aggregation_run
                    ON aggregation_run.aggregation_run_id=
                       run_binding.aggregation_run_id
                  JOIN aggregation.aggregation_version version
                    ON version.aggregation_version_id=
                       aggregation_run.aggregation_version_id
                  LEFT JOIN aggregation.v022_trainable_aggregation_diagnostic diagnostic
                    ON diagnostic.aggregation_run_id=
                       aggregation_run.aggregation_run_id
                  LEFT JOIN lineage.artifact diagnostic_artifact
                    ON diagnostic_artifact.artifact_id=diagnostic.artifact_id
                  LEFT JOIN aggregation.v022_trainable_ensemble_spec ensemble
                    ON ensemble.ensemble_spec_id=aggregation_run.ensemble_spec_id
                  JOIN product.v022_decision_schedule_session session
                    ON session.decision_session_id=:session
                  JOIN product.v022_product_enrollment enrollment
                    ON enrollment.decision_schedule_version_id=
                       session.decision_schedule_version_id
                   AND enrollment.execution_version_id=
                       execution.execution_version_id
                 WHERE execution.execution_version_id=:execution
                """
            ),
            {
                "execution": execution_version_id,
                "evidence": result_evidence_snapshot_id,
                "session": activated_decision_session_id,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Exact Product execution, evidence, or source Run was not found")
    if row["execution_status"] != "published" or row["evidence_status"] != "published":
        raise ValueError("Product Ensemble source identities must be published")
    if row["execution_mode"] == "supervised" and (
        row["diagnostic_artifact_id"] is None
        or row["diagnostic_status"] != "published"
        or row["diagnostic_member_count"] is None
    ):
        raise ValueError("Supervised Product requires a published trainable diagnostic")
    return row


def _source_members(
    connection: Connection, source: RowMapping
) -> tuple[ProductEnsembleStateMember, ...]:
    rows = tuple(
        connection.execute(
            text(
                """
                SELECT dependency.ordinal AS dependency_ordinal,
                       spec.target_version_id,spec.training_preset_version_id,
                       target_definition.target_key,
                       preset_definition.training_preset_key,
                       spec.adapter_key,spec.adapter_version,
                       feature_schema.feature_schema_fingerprint,
                       fitted.fitted_model_state_id,
                       fitted.artifact_id AS fitted_model_state_artifact_id,
                       fitted.state_fingerprint,
                       partition.statistics->>'model_fingerprint' AS model_fingerprint,
                       ensemble_member.ordinal AS ensemble_ordinal
                  FROM lineage.artifact_dependency dependency
                  JOIN aggregation.v022_oof_prediction prediction
                    ON prediction.artifact_id=dependency.depends_on_artifact_id
                  JOIN aggregation.v022_base_learner_spec spec
                    ON spec.base_learner_spec_id=prediction.base_learner_spec_id
                  JOIN aggregation.v022_feature_schema_version feature_schema
                    ON feature_schema.feature_schema_version_id=
                       spec.feature_schema_version_id
                  JOIN aggregation.target_version target
                    ON target.target_version_id=spec.target_version_id
                  JOIN aggregation.target_definition target_definition
                    ON target_definition.target_definition_id=
                       target.target_definition_id
                  JOIN aggregation.training_preset_version preset
                    ON preset.training_preset_version_id=
                       spec.training_preset_version_id
                  JOIN aggregation.training_preset_definition preset_definition
                    ON preset_definition.training_preset_definition_id=
                       preset.training_preset_definition_id
                  JOIN LATERAL (
                    SELECT link.fitted_model_state_id
                      FROM aggregation.v022_oof_prediction_fold link
                     WHERE link.oof_prediction_id=prediction.oof_prediction_id
                     ORDER BY link.ordinal DESC LIMIT 1
                  ) final_fold ON true
                  JOIN aggregation.v022_fitted_model_state fitted
                    ON fitted.fitted_model_state_id=
                       final_fold.fitted_model_state_id
                  JOIN data.payload_manifest manifest
                    ON manifest.payload_manifest_id=fitted.model_payload_manifest_id
                  JOIN data.payload_manifest_partition manifest_partition
                    ON manifest_partition.payload_manifest_id=
                       manifest.payload_manifest_id
                   AND manifest_partition.ordinal=0
                  JOIN data.payload_partition partition
                    ON partition.payload_partition_id=
                       manifest_partition.payload_partition_id
                  LEFT JOIN aggregation.v022_trainable_ensemble_member ensemble_member
                    ON ensemble_member.ensemble_spec_id=:ensemble_spec
                   AND ensemble_member.target_version_id=spec.target_version_id
                   AND ensemble_member.training_preset_version_id=
                       spec.training_preset_version_id
                 WHERE dependency.artifact_id=:diagnostic_artifact
                   AND dependency.role='oof_prediction'
                 ORDER BY coalesce(ensemble_member.ordinal,dependency.ordinal)
                """
            ),
            {
                "diagnostic_artifact": source["diagnostic_artifact_id"],
                "ensemble_spec": source["ensemble_spec_id"],
            },
        ).mappings()
    )
    return tuple(
        ProductEnsembleStateMember(
            ordinal=(
                int(row["ensemble_ordinal"])
                if row["ensemble_ordinal"] is not None
                else ordinal
            ),
            target_version_id=cast(uuid.UUID, row["target_version_id"]),
            training_preset_version_id=cast(
                uuid.UUID, row["training_preset_version_id"]
            ),
            fitted_model_state_id=cast(uuid.UUID, row["fitted_model_state_id"]),
            fitted_model_state_artifact_id=cast(
                uuid.UUID, row["fitted_model_state_artifact_id"]
            ),
            target_key=cast(str, row["target_key"]),
            training_preset_key=cast(str, row["training_preset_key"]),
            adapter_key=cast(str, row["adapter_key"]),
            adapter_version=cast(str, row["adapter_version"]),
            feature_schema_fingerprint=cast(
                str, row["feature_schema_fingerprint"]
            ),
            state_fingerprint=cast(str, row["state_fingerprint"]),
            model_fingerprint=cast(str, row["model_fingerprint"]),
        )
        for ordinal, row in enumerate(rows)
    )
