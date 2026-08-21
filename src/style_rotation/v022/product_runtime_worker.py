from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.aggregation_work_runtime import (
    AggregationCalculation,
    AggregationOutputPoint,
    FrozenAggregationRecipeResolver,
    VerifiedAggregationInput,
    VerifiedSignalManifestReader,
    execute_verified_aggregation,
)
from style_rotation.v022.defense_runtime import (
    DefenseAllocationMember,
    DefensePriceObservation,
)
from style_rotation.v022.lightgbm_trainable_aggregation import LightGbmRegressionAdapter
from style_rotation.v022.linear_trainable_aggregation import (
    OofPredictionPoint,
    OrdinaryLeastSquaresAdapter,
    RidgeRegressionAdapter,
)
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore
from style_rotation.v022.product_input_snapshot import ProductInputSnapshotPublication
from style_rotation.v022.product_runtime import (
    ProductRuntimeBindingIdentity,
    RuntimeArtifactSet,
)
from style_rotation.v022.product_runtime_execution import (
    ProductRuntimeExecutionPublication,
    ProductRuntimeExecutionService,
    ProductRuntimeStagePublication,
    ProductRuntimeTargetStages,
)
from style_rotation.v022.product_runtime_pipeline import (
    ProductDefenseContract,
    ProductMemberState,
    ProductStrategyContract,
    ProductTargetCalculation,
    calculate_product_target,
)
from style_rotation.v022.representative_pipeline_runtime import (
    RepresentativeProcessingMaterialization,
    materialize_product_representative_processing,
)
from style_rotation.v022.runtime_contract import (
    V022RuntimeContractError,
    V022RuntimeDataError,
)
from style_rotation.v022.trainable_aggregation import (
    FeatureSchema,
    FittedRegressionModel,
    TrainingMatrixRow,
    _average_rank_center,
)
from style_rotation.v022.trainable_ensemble import (
    EnsembleMemberPrediction,
    combine_trainable_oof_members,
)
from style_rotation.v022.tree_trainable_aggregation import RandomForestRegressionAdapter
from style_rotation.v022.xgboost_trainable_aggregation import XgBoostRegressionAdapter


@dataclass(frozen=True, slots=True)
class ProductRuntimePublication:
    input_snapshot: ProductInputSnapshotPublication
    processing: RepresentativeProcessingMaterialization
    execution: ProductRuntimeExecutionPublication
    aggregation: ProductRuntimeStagePublication
    targets: ProductRuntimeTargetStages
    calculation: ProductTargetCalculation
    runtime_artifacts: RuntimeArtifactSet
    runtime_binding: ProductRuntimeBindingIdentity
    decision_document: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _AggregationInputIdentity:
    compiled_feature_occurrence_id: uuid.UUID
    feature_variant_key: str
    slot_key: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class _RuntimeConfiguration:
    compiled_strategy_branch_id: uuid.UUID
    catalog_release_id: uuid.UUID
    aggregation_family_key: str
    aggregation_parameter_preset_key: str | None
    aggregation_inputs: tuple[_AggregationInputIdentity, ...]
    aggregation_recipe: Mapping[str, object] | None
    strategy: ProductStrategyContract
    defense_version_id: uuid.UUID | None
    timing_policy_version_id: uuid.UUID | None
    allocation_policy_version_id: uuid.UUID | None
    timing_variant_key: str | None
    execution_mode: str


@dataclass(frozen=True, slots=True)
class _ProductModelMember:
    ordinal: int
    target_key: str
    training_preset_key: str
    adapter_key: str
    adapter_version: str
    feature_schema_fingerprint: str
    state_fingerprint: str
    model: FittedRegressionModel


@dataclass(frozen=True, slots=True)
class _ProductEnsembleState:
    artifact_id: uuid.UUID
    state_fingerprint: str
    members: tuple[_ProductModelMember, ...]


class ProductRuntimeWorker:
    """Execute one exact Product configuration without creating a Graph Suite."""

    def __init__(
        self,
        engine: Engine,
        *,
        object_store: LocalPayloadObjectStore,
        object_root: Path,
        model_registry_path: Path,
    ) -> None:
        self._engine = engine
        self._object_store = object_store
        self._reader = VerifiedSignalManifestReader(engine, object_root)
        self._recipe_resolver = FrozenAggregationRecipeResolver.from_path(
            model_registry_path
        )
        self._runtime = ProductRuntimeExecutionService(engine)

    def execute(
        self,
        *,
        product_input_snapshot_id: uuid.UUID,
        product_enrollment_id: uuid.UUID,
        configuration_snapshot_id: uuid.UUID,
        compiled_research_graph_id: uuid.UUID,
        decision_session_id: uuid.UUID,
        decision_date: date,
        decision_cutoff_at: datetime,
        actor_key: str,
        runtime_version: str,
        environment_fingerprint: str,
    ) -> ProductRuntimePublication:
        if decision_cutoff_at.utcoffset() is None:
            raise ValueError("Product runtime decision cutoff must be timezone-aware")
        configuration = _load_runtime_configuration(
            self._engine,
            configuration_snapshot_id=configuration_snapshot_id,
            compiled_research_graph_id=compiled_research_graph_id,
        )
        snapshot = _load_product_input_snapshot(
            self._engine,
            product_input_snapshot_id=product_input_snapshot_id,
            product_enrollment_id=product_enrollment_id,
            decision_session_id=decision_session_id,
        )
        processing = materialize_product_representative_processing(
            self._engine,
            object_store=self._object_store,
            product_input_snapshot_id=snapshot.product_input_snapshot_id,
            compiled_research_graph_id=compiled_research_graph_id,
            requested_by=actor_key,
            executor_version=runtime_version,
            environment_fingerprint=environment_fingerprint,
        )
        aggregation_calculation, manifest_artifact_ids, active_state = self._aggregate(
            configuration,
            processing,
            snapshot_id=snapshot.product_input_snapshot_id,
            product_enrollment_id=product_enrollment_id,
            decision_session_id=decision_session_id,
            decision_date=decision_date,
            decision_cutoff_at=decision_cutoff_at.astimezone(UTC),
        )
        execution = self._runtime.publish_execution(
            product_input_snapshot_id=snapshot.product_input_snapshot_id,
            runtime_version=runtime_version,
        )
        aggregation = self._runtime.publish_aggregation(
            product_runtime_execution_id=execution.product_runtime_execution_id,
            calculation=aggregation_calculation,
            processing_manifest_artifact_ids=manifest_artifact_ids,
            active_model_state_artifact_id=(
                None if active_state is None else active_state.artifact_id
            ),
        )
        members = _load_product_members(
            self._engine,
            snapshot.product_input_snapshot_id,
            decision_session_id,
        )
        defense = _load_product_defense(
            self._engine,
            configuration,
            snapshot.product_input_snapshot_id,
            decision_date,
        )
        calculation = calculate_product_target(
            aggregation_calculation,
            decision_date=decision_date,
            decision_cutoff_at=decision_cutoff_at.astimezone(UTC),
            members=members,
            strategy=configuration.strategy,
            defense=defense,
        )
        targets = self._runtime.publish_targets(
            product_runtime_execution_id=execution.product_runtime_execution_id,
            aggregation_stage=aggregation,
            calculation=calculation,
            compiled_strategy_branch_id=configuration.compiled_strategy_branch_id,
            defense_version_id=configuration.defense_version_id,
            timing_policy_version_id=configuration.timing_policy_version_id,
            allocation_policy_version_id=configuration.allocation_policy_version_id,
        )
        artifacts = RuntimeArtifactSet(
            input_manifest_artifact_id=manifest_artifact_ids[0],
            active_model_state_artifact_id=(
                None if active_state is None else active_state.artifact_id
            ),
            aggregation_run_artifact_id=aggregation.artifact_id,
            strategy_target_artifact_id=targets.strategy.artifact_id,
            defense_decision_artifact_id=(
                None if targets.defense is None else targets.defense.artifact_id
            ),
            merged_target_artifact_id=targets.merge.artifact_id,
        )
        binding = ProductRuntimeBindingIdentity(
            product_input_snapshot_id=snapshot.product_input_snapshot_id,
            product_runtime_execution_id=execution.product_runtime_execution_id,
            aggregation_stage_id=aggregation.product_runtime_stage_id,
            strategy_stage_id=targets.strategy.product_runtime_stage_id,
            defense_stage_id=(
                None
                if targets.defense is None
                else targets.defense.product_runtime_stage_id
            ),
            merge_stage_id=targets.merge.product_runtime_stage_id,
        )
        return ProductRuntimePublication(
            snapshot,
            processing,
            execution,
            aggregation,
            targets,
            calculation,
            artifacts,
            binding,
            _decision_document(
                calculation,
                compiled_strategy_branch_id=configuration.compiled_strategy_branch_id,
                decision_cutoff_at=decision_cutoff_at.astimezone(UTC),
            ),
        )

    def _aggregate(
        self,
        configuration: _RuntimeConfiguration,
        processing: RepresentativeProcessingMaterialization,
        *,
        snapshot_id: uuid.UUID,
        product_enrollment_id: uuid.UUID,
        decision_session_id: uuid.UUID,
        decision_date: date,
        decision_cutoff_at: datetime,
    ) -> tuple[
        AggregationCalculation,
        tuple[uuid.UUID, ...],
        _ProductEnsembleState | None,
    ]:
        asset_keys = _load_product_executable_asset_keys(self._engine, snapshot_id)
        with self._engine.connect() as connection:
            manifests = {
                key: _manifest_identity(connection, output.payload_manifest_id)
                for key, output in processing.stage3_outputs.items()
            }
        inputs: list[VerifiedAggregationInput] = []
        for item in configuration.aggregation_inputs:
            output = processing.stage3_outputs.get(item.feature_variant_key)
            manifest = manifests.get(item.feature_variant_key)
            if output is None or manifest is None:
                raise V022RuntimeDataError(
                    "product_processing_manifest_missing",
                    "Product Processing did not publish every compiled Aggregation input",
                    details={"feature_variant_key": item.feature_variant_key},
                )
            inputs.append(
                VerifiedAggregationInput(
                    item.compiled_feature_occurrence_id,
                    item.feature_variant_key,
                    item.slot_key,
                    item.ordinal,
                    output.payload_manifest_id,
                    output.manifest_artifact_id,
                    cast(str, manifest["manifest_hash"]),
                    self._reader.read(
                        payload_manifest_id=output.payload_manifest_id,
                        expected_manifest_hash=cast(str, manifest["manifest_hash"]),
                        expected_artifact_id=output.manifest_artifact_id,
                        catalog_release_id=configuration.catalog_release_id,
                        allowed_asset_keys=asset_keys,
                        decision_dates=frozenset((decision_date,)),
                    ),
                )
            )
        if configuration.execution_mode == "deterministic":
            active_state = None
            calculation = execute_verified_aggregation(
                family_key=configuration.aggregation_family_key,
                parameter_preset_key=configuration.aggregation_parameter_preset_key,
                inputs=tuple(inputs),
                recipe_resolver=self._recipe_resolver,
                compiled_recipe=configuration.aggregation_recipe,
            )
        elif configuration.execution_mode == "supervised":
            active_state = _load_active_product_ensemble_state(
                self._engine,
                self._object_store,
                product_enrollment_id=product_enrollment_id,
                decision_session_id=decision_session_id,
                execution_mode=configuration.execution_mode,
            )
            if active_state is None:
                raise V022RuntimeDataError(
                    "product_ensemble_state_missing",
                    "Supervised Product lacks an active complete Ensemble State",
                )
            calculation = _predict_product_ensemble(
                configuration,
                tuple(inputs),
                active_state,
                decision_date=decision_date,
                decision_cutoff_at=decision_cutoff_at,
            )
        else:
            raise V022RuntimeContractError(
                "product_aggregation_execution_mode_invalid",
                "Product Aggregation execution mode is not supported",
            )
        return (
            calculation,
            tuple(item.manifest_artifact_id for item in inputs),
            active_state,
        )


def _load_active_product_ensemble_state(
    engine: Engine,
    object_store: LocalPayloadObjectStore,
    *,
    product_enrollment_id: uuid.UUID,
    decision_session_id: uuid.UUID,
    execution_mode: str,
) -> _ProductEnsembleState | None:
    with engine.connect() as connection:
        state = (
            connection.execute(
                text(
                    """
                    SELECT state.product_ensemble_state_id,state.artifact_id,
                           state.state_fingerprint,state.member_count,
                           artifact.status
                      FROM product.v022_product_enrollment enrollment
                      JOIN product.v022_decision_schedule_session current_session
                        ON current_session.decision_session_id=:session
                       AND current_session.decision_schedule_version_id=
                           enrollment.decision_schedule_version_id
                      JOIN product.v022_product_ensemble_state state
                        ON state.execution_version_id=enrollment.execution_version_id
                      JOIN product.v022_decision_schedule_session activation
                        ON activation.decision_session_id=
                           state.activated_decision_session_id
                       AND activation.decision_schedule_version_id=
                           enrollment.decision_schedule_version_id
                       AND activation.ordinal<=current_session.ordinal
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=state.artifact_id
                     WHERE enrollment.product_enrollment_id=:enrollment
                     ORDER BY state.state_version_number DESC
                     LIMIT 1
                    """
                ),
                {
                    "enrollment": product_enrollment_id,
                    "session": decision_session_id,
                },
            )
            .mappings()
            .one_or_none()
        )
        if execution_mode == "deterministic":
            if state is not None:
                raise V022RuntimeContractError(
                    "deterministic_product_model_state_forbidden",
                    "Deterministic Product must not bind an Ensemble State",
                )
            return None
        if execution_mode != "supervised":
            raise V022RuntimeContractError(
                "product_aggregation_execution_mode_invalid",
                "Product Aggregation execution mode is not supported",
            )
        if state is None or state["status"] != "published":
            raise V022RuntimeDataError(
                "product_ensemble_state_missing",
                "Supervised Product lacks an active published Ensemble State",
            )
        rows = tuple(
            connection.execute(
                text(
                    """
                    SELECT member.ordinal,target_definition.target_key,
                           preset_definition.training_preset_key,
                           spec.adapter_key,spec.adapter_version,
                           feature_schema.feature_schema_fingerprint,
                           fitted.state_fingerprint,
                           partition.statistics->>'model_fingerprint'
                             AS model_fingerprint,
                           object.storage_uri
                      FROM product.v022_product_ensemble_state_member member
                      JOIN aggregation.v022_fitted_model_state fitted
                        ON fitted.fitted_model_state_id=
                           member.fitted_model_state_id
                       AND fitted.artifact_id=
                           member.fitted_model_state_artifact_id
                      JOIN aggregation.v022_base_learner_spec spec
                        ON spec.base_learner_spec_id=fitted.base_learner_spec_id
                      JOIN aggregation.v022_feature_schema_version feature_schema
                        ON feature_schema.feature_schema_version_id=
                           spec.feature_schema_version_id
                      JOIN aggregation.target_version target
                        ON target.target_version_id=member.target_version_id
                      JOIN aggregation.target_definition target_definition
                        ON target_definition.target_definition_id=
                           target.target_definition_id
                      JOIN aggregation.training_preset_version preset
                        ON preset.training_preset_version_id=
                           member.training_preset_version_id
                      JOIN aggregation.training_preset_definition preset_definition
                        ON preset_definition.training_preset_definition_id=
                           preset.training_preset_definition_id
                      JOIN data.payload_manifest manifest
                        ON manifest.payload_manifest_id=
                           fitted.model_payload_manifest_id
                       AND manifest.materialization_state='materialized'
                      JOIN data.payload_manifest_partition manifest_partition
                        ON manifest_partition.payload_manifest_id=
                           manifest.payload_manifest_id
                       AND manifest_partition.ordinal=0
                      JOIN data.payload_partition partition
                        ON partition.payload_partition_id=
                           manifest_partition.payload_partition_id
                      JOIN data.payload_object object
                        ON object.payload_object_id=partition.payload_object_id
                       AND object.object_state='published'
                       AND object.verification_status='verified'
                     WHERE member.product_ensemble_state_id=:state
                     ORDER BY member.ordinal
                    """
                ),
                {"state": state["product_ensemble_state_id"]},
            ).mappings()
        )
    expected_count = int(state["member_count"])
    if len(rows) != expected_count or tuple(row["ordinal"] for row in rows) != tuple(
        range(expected_count)
    ):
        raise V022RuntimeDataError(
            "product_ensemble_state_incomplete",
            "Product Ensemble State member closure is incomplete",
        )
    members: list[_ProductModelMember] = []
    for row in rows:
        content = object_store.read(cast(str, row["storage_uri"]))
        table = pq.read_table(pa.BufferReader(content))
        if table.num_rows != 1 or "model_document" not in table.column_names:
            raise V022RuntimeDataError(
                "product_model_state_payload_invalid",
                "Product fitted Model State payload is invalid",
            )
        document = json.loads(cast(str, table["model_document"][0].as_py()))
        model_fingerprint = cast(str, row["model_fingerprint"])
        model = FittedRegressionModel(
            adapter_key=cast(str, row["adapter_key"]),
            adapter_version=cast(str, row["adapter_version"]),
            feature_schema_fingerprint=cast(str, row["feature_schema_fingerprint"]),
            model_document=cast(Mapping[str, object], document),
            model_fingerprint=model_fingerprint,
        )
        members.append(
            _ProductModelMember(
                ordinal=int(row["ordinal"]),
                target_key=cast(str, row["target_key"]),
                training_preset_key=cast(str, row["training_preset_key"]),
                adapter_key=model.adapter_key,
                adapter_version=model.adapter_version,
                feature_schema_fingerprint=model.feature_schema_fingerprint,
                state_fingerprint=cast(str, row["state_fingerprint"]),
                model=model,
            )
        )
    return _ProductEnsembleState(
        artifact_id=cast(uuid.UUID, state["artifact_id"]),
        state_fingerprint=cast(str, state["state_fingerprint"]),
        members=tuple(members),
    )


def _predict_product_ensemble(
    configuration: _RuntimeConfiguration,
    inputs: tuple[VerifiedAggregationInput, ...],
    state: _ProductEnsembleState,
    *,
    decision_date: date,
    decision_cutoff_at: datetime,
) -> AggregationCalculation:
    feature_schema = FeatureSchema(tuple(item.feature_variant_key for item in inputs))
    indexed_inputs: list[dict[uuid.UUID, Any]] = []
    expected_assets: frozenset[uuid.UUID] | None = None
    for item in inputs:
        indexed: dict[uuid.UUID, Any] = {}
        for point in item.points:
            if point.decision_date != decision_date or point.signal_value is None:
                raise V022RuntimeDataError(
                    "product_trainable_feature_panel_invalid",
                    "Product trainable Feature panel is missing a current value",
                )
            if point.asset_id in indexed:
                raise V022RuntimeDataError(
                    "product_trainable_feature_panel_duplicate",
                    "Product trainable Feature panel contains duplicate Assets",
                )
            indexed[point.asset_id] = point
        panel = frozenset(indexed)
        if expected_assets is None:
            expected_assets = panel
        elif panel != expected_assets:
            raise V022RuntimeDataError(
                "product_trainable_feature_panel_mismatch",
                "Product trainable Features do not share one exact current panel",
            )
        indexed_inputs.append(indexed)
    if expected_assets is None or len(expected_assets) < 2:
        raise V022RuntimeDataError(
            "product_trainable_feature_panel_too_small",
            "Product trainable inference requires at least two Assets",
        )
    rows = tuple(
        TrainingMatrixRow(
            security_id=asset_id,
            security_key=indexed_inputs[0][asset_id].asset_key,
            decision_date=decision_date,
            decision_cutoff_at=decision_cutoff_at,
            feature_values=tuple(
                cast(Decimal, indexed[asset_id].signal_value)
                for indexed in indexed_inputs
            ),
            target_value=Decimal(),
            target_known_at=decision_cutoff_at,
            target_entry_date=decision_date,
            target_exit_date=decision_date,
            target_available=False,
        )
        for asset_id in sorted(expected_assets, key=str)
    )
    member_predictions: list[EnsembleMemberPrediction] = []
    for member in state.members:
        if member.feature_schema_fingerprint != feature_schema.fingerprint:
            raise V022RuntimeContractError(
                "product_model_feature_schema_drift",
                "Product Model State Feature Schema differs from the compiled Graph",
            )
        adapter = _product_model_adapter(member.adapter_key, member.adapter_version)
        raw = adapter.predict(member.model, rows)
        if len(raw) != len(rows):
            raise V022RuntimeDataError(
                "product_model_prediction_count_invalid",
                "Product Model returned the wrong prediction count",
            )
        centered = _average_rank_center(
            tuple(
                (row.security_id, row.security_key, prediction)
                for row, prediction in zip(rows, raw, strict=True)
            )
        )
        predictions = tuple(
            OofPredictionPoint(
                row.security_id,
                row.security_key,
                decision_date,
                prediction,
                centered[row.security_id],
                0,
            )
            for row, prediction in zip(rows, raw, strict=True)
        )
        member_predictions.append(
            EnsembleMemberPrediction(
                member.target_key,
                member.training_preset_key,
                sha256_hexdigest(
                    {
                        "state_fingerprint": member.state_fingerprint,
                        "decision_date": decision_date,
                        "predictions": predictions,
                    }
                ),
                predictions,
            )
        )
    ensemble = combine_trainable_oof_members(
        configuration.aggregation_family_key, member_predictions
    )
    points = tuple(
        AggregationOutputPoint(
            prediction.security_id,
            prediction.security_key,
            prediction.decision_date,
            prediction.centered_rank,
            decision_cutoff_at,
            state.state_fingerprint,
            None,
        )
        for prediction in ensemble.predictions
    )
    return AggregationCalculation(
        configuration.aggregation_family_key,
        None,
        points,
        sha256_hexdigest(
            {
                "family_key": configuration.aggregation_family_key,
                "product_ensemble_state_fingerprint": state.state_fingerprint,
                "ensemble_prediction_fingerprint": ensemble.fingerprint,
                "points": points,
            }
        ),
    )


def _product_model_adapter(adapter_key: str, adapter_version: str) -> Any:
    adapters = {
        "ols_cross_sectional_regression": OrdinaryLeastSquaresAdapter,
        "ridge_cross_sectional_regression": RidgeRegressionAdapter,
        "random_forest_cross_sectional_regression": RandomForestRegressionAdapter,
        "lightgbm_cross_sectional_regression": LightGbmRegressionAdapter,
        "xgboost_cross_sectional_regression": XgBoostRegressionAdapter,
    }
    factory = adapters.get(adapter_key)
    if factory is None:
        raise V022RuntimeContractError(
            "product_model_adapter_unsupported",
            "Product Model State uses an unsupported adapter",
        )
    adapter: Any = factory()
    if adapter.adapter_version != adapter_version:
        raise V022RuntimeContractError(
            "product_model_adapter_version_drift",
            "Product Model State adapter version is not executable",
        )
    return adapter


def _load_product_executable_asset_keys(
    engine: Engine, snapshot_id: uuid.UUID
) -> dict[uuid.UUID, str]:
    with engine.connect() as connection:
        rows = tuple(
            connection.execute(
                text(
                    "SELECT security_id,asset_key FROM "
                    "product.v022_product_input_member "
                    "WHERE product_input_snapshot_id=:snapshot "
                    "AND NOT is_uniformly_excluded ORDER BY ordinal"
                ),
                {"snapshot": snapshot_id},
            ).mappings()
        )
    if not rows:
        raise V022RuntimeDataError(
            "product_member_set_empty",
            "Product Input Snapshot has no executable members",
        )
    return {row["security_id"]: row["asset_key"] for row in rows}


def _load_product_input_snapshot(
    engine: Engine,
    *,
    product_input_snapshot_id: uuid.UUID,
    product_enrollment_id: uuid.UUID,
    decision_session_id: uuid.UUID,
) -> ProductInputSnapshotPublication:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    """
                    SELECT snapshot.product_input_snapshot_id,snapshot.artifact_id,
                           snapshot.snapshot_fingerprint,snapshot.dataset_publication_id,
                           snapshot.input_start,snapshot.input_end,
                           snapshot.inputs_available_at,
                           (SELECT count(*)
                              FROM product.v022_product_input_member member
                             WHERE member.product_input_snapshot_id=
                                   snapshot.product_input_snapshot_id) AS member_count,
                           artifact.status
                      FROM product.v022_product_input_snapshot snapshot
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=snapshot.artifact_id
                     WHERE snapshot.product_input_snapshot_id=:snapshot
                       AND snapshot.product_enrollment_id=:enrollment
                       AND snapshot.decision_session_id=:session
                    """
                ),
                {
                    "snapshot": product_input_snapshot_id,
                    "enrollment": product_enrollment_id,
                    "session": decision_session_id,
                },
            )
            .mappings()
            .one_or_none()
        )
    if row is None or row["status"] != "published":
        raise V022RuntimeDataError(
            "product_input_snapshot_not_prepared",
            "Product runtime requires its exact published Product Input Snapshot",
            details={
                "product_input_snapshot_id": str(product_input_snapshot_id),
                "product_enrollment_id": str(product_enrollment_id),
                "decision_session_id": str(decision_session_id),
            },
        )
    return ProductInputSnapshotPublication(
        row["product_input_snapshot_id"],
        row["artifact_id"],
        str(row["snapshot_fingerprint"]),
        row["dataset_publication_id"],
        row["input_start"],
        row["input_end"],
        row["inputs_available_at"],
        int(row["member_count"]),
        True,
    )


def _load_runtime_configuration(
    engine: Engine,
    *,
    configuration_snapshot_id: uuid.UUID,
    compiled_research_graph_id: uuid.UUID,
) -> _RuntimeConfiguration:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    """
                    SELECT snapshot.compiled_strategy_branch_id,
                           snapshot.semantic_identity_document,
                           graph.catalog_release_id,
                           family.family_key AS aggregation_family_key,
                           aggregation_version.execution_mode,
                           preset_definition.parameter_preset_key,
                           compiled_recipe.recipe_document,
                           strategy_variant.variant_key AS strategy_variant_key,
                           strategy_preset.resolved_parameters,
                           branch.defense_version_id,
                           context.timing_policy_version_id,
                           context.allocation_policy_version_id,
                           timing_variant.variant_key AS timing_variant_key
                      FROM experiment.v022_research_configuration_snapshot snapshot
                      JOIN lineage.artifact snapshot_artifact
                        ON snapshot_artifact.artifact_id=snapshot.artifact_id
                       AND snapshot_artifact.status='published'
                      JOIN workspace.compiled_research_graph graph
                        ON graph.compiled_research_graph_id=
                           snapshot.compiled_research_graph_id
                      JOIN strategy.v022_compiled_strategy_branch branch
                        ON branch.compiled_strategy_branch_id=
                           snapshot.compiled_strategy_branch_id
                      JOIN workspace.compiled_aggregation_instance instance
                        ON instance.compiled_aggregation_instance_id=
                           branch.compiled_aggregation_instance_id
                      JOIN aggregation.aggregation_version aggregation_version
                        ON aggregation_version.aggregation_version_id=
                           instance.aggregation_version_id
                      JOIN aggregation.aggregation_family family
                        ON family.aggregation_family_id=
                           aggregation_version.aggregation_family_id
                      LEFT JOIN aggregation.parameter_preset_version preset_version
                        ON preset_version.parameter_preset_version_id=
                           instance.parameter_preset_version_id
                      LEFT JOIN aggregation.parameter_preset_definition preset_definition
                        ON preset_definition.parameter_preset_definition_id=
                           preset_version.parameter_preset_definition_id
                      LEFT JOIN workspace.v022_compiled_aggregation_recipe
                           compiled_recipe
                        ON compiled_recipe.compiled_aggregation_instance_id=
                           instance.compiled_aggregation_instance_id
                      JOIN strategy.v022_strategy_version strategy_version
                        ON strategy_version.strategy_version_id=branch.strategy_version_id
                      JOIN strategy.v022_strategy_variant strategy_variant
                        ON strategy_variant.strategy_variant_id=
                           strategy_version.strategy_variant_id
                      JOIN strategy.v022_compiled_strategy_branch_preset_binding
                           strategy_preset
                        ON strategy_preset.compiled_strategy_branch_id=
                           branch.compiled_strategy_branch_id
                      LEFT JOIN experiment.v022_configuration_execution_context_binding
                           context
                        ON context.configuration_snapshot_id=
                           snapshot.configuration_snapshot_id
                      LEFT JOIN defense.v022_timing_policy_version timing
                        ON timing.timing_policy_version_id=
                           context.timing_policy_version_id
                      LEFT JOIN defense.v022_timing_policy_variant timing_variant
                        ON timing_variant.timing_policy_variant_id=
                           timing.timing_policy_variant_id
                     WHERE snapshot.configuration_snapshot_id=:configuration
                       AND snapshot.compiled_research_graph_id=:graph
                    """
                ),
                {
                    "configuration": configuration_snapshot_id,
                    "graph": compiled_research_graph_id,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError("Published Product configuration was not found")
        input_rows = tuple(
            connection.execute(
                text(
                    """
                    SELECT input.slot_key,input.ordinal,
                           occurrence.compiled_feature_occurrence_id,
                           variant.variant_key
                      FROM experiment.v022_research_configuration_snapshot snapshot
                      JOIN strategy.v022_compiled_strategy_branch branch
                        ON branch.compiled_strategy_branch_id=
                           snapshot.compiled_strategy_branch_id
                      JOIN workspace.compiled_aggregation_input input
                        ON input.compiled_aggregation_instance_id=
                           branch.compiled_aggregation_instance_id
                      JOIN workspace.compiled_feature_occurrence occurrence
                        ON occurrence.compiled_feature_occurrence_id=
                           input.compiled_feature_occurrence_id
                      JOIN processing.feature_version feature
                        ON feature.feature_version_id=occurrence.feature_version_id
                      JOIN processing.feature_variant variant
                        ON variant.feature_variant_id=feature.feature_variant_id
                     WHERE snapshot.configuration_snapshot_id=:configuration
                     ORDER BY input.ordinal
                    """
                ),
                {"configuration": configuration_snapshot_id},
            ).mappings()
        )
    if not input_rows or tuple(item["ordinal"] for item in input_rows) != tuple(
        range(len(input_rows))
    ):
        raise V022RuntimeContractError(
            "product_aggregation_inputs_invalid",
            "Product configuration requires contiguous ordered Aggregation inputs",
        )
    semantic = cast(dict[str, Any], row["semantic_identity_document"])
    mode = cast(dict[str, Any], semantic.get("execution_policy", {})).get(
        "research_mode", "formal"
    )
    resolved = cast(dict[str, Any], row["resolved_parameters"])
    family_key = cast(str, row["aggregation_family_key"])
    stored_preset_key = cast(str | None, row["parameter_preset_key"])
    preset_key = _local_aggregation_preset_key(family_key, stored_preset_key)
    defense_version_id = cast(uuid.UUID | None, row["defense_version_id"])
    if defense_version_id is None:
        if any(
            row[key] is not None
            for key in (
                "timing_policy_version_id",
                "allocation_policy_version_id",
                "timing_variant_key",
            )
        ):
            raise V022RuntimeContractError(
                "product_none_defense_identity_invalid",
                "No-defense Product configuration carries Defense identities",
            )
    elif any(
        row[key] is None
        for key in (
            "timing_policy_version_id",
            "allocation_policy_version_id",
            "timing_variant_key",
        )
    ):
        raise V022RuntimeContractError(
            "product_defense_identity_incomplete",
            "Defended Product configuration lacks exact Defense policy identities",
        )
    return _RuntimeConfiguration(
        compiled_strategy_branch_id=cast(
            uuid.UUID, row["compiled_strategy_branch_id"]
        ),
        catalog_release_id=cast(uuid.UUID, row["catalog_release_id"]),
        aggregation_family_key=family_key,
        aggregation_parameter_preset_key=preset_key,
        aggregation_inputs=tuple(
            _AggregationInputIdentity(
                cast(uuid.UUID, item["compiled_feature_occurrence_id"]),
                cast(str, item["variant_key"]),
                cast(str, item["slot_key"]),
                cast(int, item["ordinal"]),
            )
            for item in input_rows
        ),
        aggregation_recipe=cast(
            Mapping[str, object] | None, row["recipe_document"]
        ),
        strategy=ProductStrategyContract(
            variant_key=cast(Any, row["strategy_variant_key"]),
            target_k=_positive_int(resolved.get("target_k"), "target_k"),
            research_mode=cast(Any, mode),
            selection_buffer=cast(Any, resolved.get("selection_buffer", "none")),
            sector_cap=cast(Any, resolved.get("sector_cap", "none")),
        ),
        defense_version_id=defense_version_id,
        timing_policy_version_id=cast(
            uuid.UUID | None, row["timing_policy_version_id"]
        ),
        allocation_policy_version_id=cast(
            uuid.UUID | None, row["allocation_policy_version_id"]
        ),
        timing_variant_key=cast(str | None, row["timing_variant_key"]),
        execution_mode=cast(str, row["execution_mode"]),
    )


def _load_product_members(
    engine: Engine,
    snapshot_id: uuid.UUID,
    decision_session_id: uuid.UUID,
) -> tuple[ProductMemberState, ...]:
    with engine.connect() as connection:
        rows = tuple(
            connection.execute(
                text(
                    """
                    SELECT member.security_id,member.asset_key,member.is_selectable
                      FROM product.v022_product_input_member member
                     WHERE member.product_input_snapshot_id=:snapshot
                       AND NOT member.is_uniformly_excluded
                     ORDER BY member.asset_key
                    """
                ),
                {"snapshot": snapshot_id},
            ).mappings()
        )
        held = frozenset(
            connection.scalars(
                text(
                    """
                    SELECT (target->>'asset_id')::uuid
                      FROM product.v022_product_input_snapshot snapshot
                      JOIN product.v022_product_enrollment enrollment
                        ON enrollment.product_enrollment_id=
                           snapshot.product_enrollment_id
                      JOIN product.v022_decision_schedule_session current_session
                        ON current_session.decision_session_id=:session
                       AND current_session.decision_schedule_version_id=
                           enrollment.decision_schedule_version_id
                      JOIN product.v022_decision_schedule_session prior_session
                        ON prior_session.decision_schedule_version_id=
                           enrollment.decision_schedule_version_id
                       AND prior_session.ordinal=current_session.ordinal-1
                      JOIN product.v022_product_decision prior
                        ON prior.execution_version_id=enrollment.execution_version_id
                       AND prior.decision_session_id=prior_session.decision_session_id
                       AND prior.decision_status='completed'
                      CROSS JOIN LATERAL jsonb_array_elements(
                        prior.decision_document->'ordered_net_asset_targets'
                      ) target
                     WHERE snapshot.product_input_snapshot_id=:snapshot
                    """
                ),
                {"snapshot": snapshot_id, "session": decision_session_id},
            )
        )
    if not rows:
        raise V022RuntimeDataError(
            "product_member_set_empty", "Product Input Snapshot has no members"
        )
    return tuple(
        ProductMemberState(
            cast(uuid.UUID, row["security_id"]),
            cast(str, row["asset_key"]),
            cast(bool, row["is_selectable"]),
            cast(uuid.UUID, row["security_id"]) in held,
        )
        for row in rows
    )


def _load_product_defense(
    engine: Engine,
    configuration: _RuntimeConfiguration,
    snapshot_id: uuid.UUID,
    decision_date: date,
) -> ProductDefenseContract | None:
    if configuration.defense_version_id is None:
        return None
    allocation_id = cast(
        uuid.UUID, configuration.allocation_policy_version_id
    )
    with engine.connect() as connection:
        members = tuple(
            DefenseAllocationMember(
                None if row["component_role"] == "reserve" else row["security_id"],
                row["asset_key"],
                cast(Any, row["component_role"]),
                row["sleeve_weight"],
                row["ordinal"],
            )
            for row in connection.execute(
                text(
                    """
                    SELECT ordinal,security_id,asset_key,component_role,sleeve_weight
                      FROM defense.v022_allocation_policy_member
                     WHERE allocation_policy_version_id=:allocation
                     ORDER BY ordinal
                    """
                ),
                {"allocation": allocation_id},
            ).mappings()
        )
    if not members:
        raise V022RuntimeDataError(
            "product_defense_allocation_empty",
            "Product Defense has no published Allocation members",
        )
    timing_key = cast(str, configuration.timing_variant_key)
    if timing_key == "fixed20_budget":
        return ProductDefenseContract(cast(Any, timing_key), (), (), members)
    if timing_key != "spy_ma200_tiered_budget":
        raise V022RuntimeContractError(
            "product_defense_variant_unsupported",
            "Product Defense Timing Variant is not executable",
        )
    observations, sessions = _load_spy_timing_window(
        engine, snapshot_id=snapshot_id, decision_date=decision_date
    )
    return ProductDefenseContract(cast(Any, timing_key), observations, sessions, members)


def _load_spy_timing_window(
    engine: Engine, *, snapshot_id: uuid.UUID, decision_date: date
) -> tuple[tuple[DefensePriceObservation, ...], tuple[date, ...]]:
    with engine.connect() as connection:
        rows = tuple(
            connection.execute(
                text(
                    """
                    SELECT session.session_date,session.close_at_utc,bar.adj_close
                      FROM product.v022_product_input_snapshot snapshot
                      JOIN catalog.calendar_session session
                        ON session.calendar_version_id=snapshot.calendar_version_id
                      JOIN catalog.asset spy ON spy.asset_key='spy'
                      LEFT JOIN data.daily_bar bar
                        ON bar.dataset_publication_id=snapshot.dataset_publication_id
                       AND bar.asset_id=spy.asset_id
                       AND bar.session_date=session.session_date
                     WHERE snapshot.product_input_snapshot_id=:snapshot
                       AND session.session_date<=:decision_date
                     ORDER BY session.session_date DESC
                     LIMIT 200
                    """
                ),
                {"snapshot": snapshot_id, "decision_date": decision_date},
            ).mappings()
        )
    ordered = tuple(reversed(rows))
    if len(ordered) != 200 or any(
        row["adj_close"] is None
        or row["close_at_utc"] is None
        or row["close_at_utc"].utcoffset() is None
        for row in ordered
    ):
        raise V022RuntimeDataError(
            "product_ma200_window_incomplete",
            "Product MA200 Defense requires 200 exact SPY observations",
        )
    return (
        tuple(
            DefensePriceObservation(
                cast(date, row["session_date"]),
                cast(datetime, row["close_at_utc"]).astimezone(UTC),
                Decimal(row["adj_close"]),
            )
            for row in ordered
        ),
        tuple(cast(date, row["session_date"]) for row in ordered),
    )


def _manifest_identity(connection: Connection, manifest_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT payload_manifest_id,artifact_id,manifest_hash "
                "FROM data.payload_manifest WHERE payload_manifest_id=:manifest"
            ),
            {"manifest": manifest_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise V022RuntimeDataError(
            "product_processing_manifest_missing",
            "Product Processing Manifest identity is missing",
        )
    return row


def _local_aggregation_preset_key(
    family_key: str, stored_key: str | None
) -> str | None:
    if stored_key is None:
        return None
    prefix = f"{family_key}__"
    if not stored_key.startswith(prefix) or len(stored_key) == len(prefix):
        raise V022RuntimeContractError(
            "product_aggregation_preset_identity_invalid",
            "Product Aggregation Preset is not owned by its exact Family",
        )
    return stored_key[len(prefix) :]


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise V022RuntimeContractError(
            "product_strategy_parameter_invalid",
            f"Product Strategy {field} must be a positive integer",
        )
    return value


def _decision_document(
    calculation: ProductTargetCalculation,
    *,
    compiled_strategy_branch_id: uuid.UUID,
    decision_cutoff_at: datetime,
) -> dict[str, Any]:
    target = calculation.merged_target
    return {
        "decision_session": target.decision_date.isoformat(),
        "decision_cutoff_at": decision_cutoff_at.isoformat(),
        "target_identity": {
            "decision_date": target.decision_date.isoformat(),
            "decision_cutoff_at": target.decision_cutoff_at.isoformat(),
            "input_known_at": target.input_known_at.isoformat(),
            "compiled_strategy_branch_id": str(compiled_strategy_branch_id),
            "risk_budget": str(target.risk_budget),
            "defense_budget": str(target.defense_budget),
            "reserve_target_weight": str(target.reserve_target_weight),
        },
        "ordered_net_asset_targets": [
            {
                "decision_date": target.decision_date.isoformat(),
                "asset_id": str(item.asset_id),
                "asset_key": item.asset_key,
                "target_weight": str(item.target_weight),
            }
            for item in target.net_asset_weights
        ],
        "reserve_target": {
            "decision_date": target.decision_date.isoformat(),
            "reserve_target_weight": str(target.reserve_target_weight),
        },
        "execution_timing": "next_scheduled_open",
        "market_price_basis": "raw_open_at_execution",
        "research_signal_price_basis": "back_adjusted",
    }
