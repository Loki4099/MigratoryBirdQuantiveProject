from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import cast

import numpy as np
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import DependencyInput
from style_rotation.v022.aggregation_work_runtime import (
    AggregationCalculation,
    AggregationOutputPoint,
    PublishedAggregationOutput,
    SignalManifestPoint,
    VerifiedSignalManifestReader,
    _AggregationWorkContext,
    _load_work_context,
    _prepare_aggregation_output,
    _publish_aggregation_output,
    encode_final_signal_numeric_parquet,
)
from style_rotation.v022.compact_trainable_ensemble import (
    CompactTrainableEnsembleResult,
    CompactTrainableMember,
    combine_compact_member_scores,
    combine_compact_trainable_members,
    compact_member_execution,
    q18_decimal,
    security_uuid,
)
from style_rotation.v022.dag import ClaimedGraphWork, GraphDagService
from style_rotation.v022.lightgbm_trainable_aggregation import (
    LightGbmRegressionAdapter,
)
from style_rotation.v022.linear_trainable_aggregation import (
    OrdinaryLeastSquaresAdapter,
    RidgeRegressionAdapter,
    StrictOofResult,
    run_strict_oof_predictions,
)
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore
from style_rotation.v022.trainable_aggregation import (
    VALUE_QUANTUM,
    AdjustedOpenPoint,
    CrossSectionalTargetPoint,
    FeatureSchema,
    FixedSessionTarget,
    RegressionModelAdapter,
    TrainableAggregationError,
    TrainingMatrix,
    TrainingMatrixRow,
    WalkForwardFold,
    WalkForwardPolicy,
    build_expanding_walk_forward_folds,
    build_fixed_session_target_panel,
)
from style_rotation.v022.trainable_aggregation_publication import (
    OofFoldPublicationLink,
    PublishedTrainablePayload,
    TrainablePayloadBinding,
    publish_base_learner_spec,
    publish_fitted_model_state,
    publish_oof_prediction,
    publish_training_folds,
    publish_training_matrix,
    publish_walk_forward_policy,
)
from style_rotation.v022.trainable_ensemble_diagnostic_publication import (
    publish_trainable_aggregation_diagnostic,
)
from style_rotation.v022.trainable_ensemble_diagnostics import (
    EnsembleDiagnosticMemberInput,
    TrainableEnsembleDiagnostic,
    calculate_trainable_ensemble_diagnostic,
)
from style_rotation.v022.tree_trainable_aggregation import (
    RandomForestRegressionAdapter,
)
from style_rotation.v022.xgboost_trainable_aggregation import (
    XgBoostRegressionAdapter,
)


@dataclass(frozen=True, slots=True)
class TrainableFeatureInput:
    feature_key: str
    manifest_fingerprint: str
    points: tuple[SignalManifestPoint, ...] | list[SignalManifestPoint | None]

    def __post_init__(self) -> None:
        if not self.feature_key.strip():
            raise ValueError("Trainable Feature input key must be nonempty")
        if len(self.manifest_fingerprint) != 64:
            raise ValueError("Trainable Feature Manifest fingerprint must be SHA-256")


@dataclass(frozen=True, slots=True)
class TrainableAggregationExecutionRequest:
    family_key: str
    target: FixedSessionTarget
    training_preset_key: str
    training_preset_semantics: Mapping[str, object]
    feature_inputs: tuple[TrainableFeatureInput, ...]
    security_keys_by_asset_id: Mapping[uuid.UUID, str]
    sessions: tuple[date, ...]
    adjusted_opens: tuple[AdjustedOpenPoint, ...]
    candidate_security_ids_by_date: Mapping[date, frozenset[uuid.UUID]]
    decision_cutoff_at_by_date: Mapping[date, datetime]
    training_start: date
    prediction_start: date
    prediction_end: date
    candidate_mask_is_feature_complete: bool = False
    consume_source_panels: bool = False

    def __post_init__(self) -> None:
        if self.family_key not in {
            "ols_cross_sectional_regression",
            "ridge_cross_sectional_regression",
            "random_forest_cross_sectional_regression",
            "lightgbm_cross_sectional_regression",
            "xgboost_cross_sectional_regression",
        }:
            raise ValueError("Unsupported trainable regression Family")
        if not self.training_preset_key.strip():
            raise ValueError("Training Preset key must be nonempty")
        if not self.feature_inputs:
            raise ValueError("Trainable execution requires at least one Feature input")
        if len(self.feature_inputs) > 32:
            raise ValueError("Trainable execution accepts at most 32 Feature inputs")
        keys = tuple(item.feature_key for item in self.feature_inputs)
        if len(keys) != len(set(keys)):
            raise ValueError("Trainable Feature input keys must be unique")
        if self.training_start >= self.prediction_start:
            raise ValueError("Trainable execution requires history before prediction")
        if self.prediction_start > self.prediction_end:
            raise ValueError("Prediction start must not follow prediction end")


@dataclass(frozen=True, slots=True)
class TrainableAggregationExecution:
    feature_schema: FeatureSchema
    matrix: TrainingMatrix
    policy: WalkForwardPolicy
    folds: tuple[WalkForwardFold, ...]
    oof_result: StrictOofResult
    calculation: AggregationCalculation


@dataclass(frozen=True, slots=True)
class LoadedTrainableAggregationWork:
    context: _AggregationWorkContext
    members: tuple[LoadedTrainableAggregationMember, ...]


@dataclass(frozen=True, slots=True)
class LoadedTrainableAggregationMember:
    ordinal: int
    target_version_id: uuid.UUID
    target_version_artifact_id: uuid.UUID
    training_preset_version_id: uuid.UUID
    training_preset_version_artifact_id: uuid.UUID
    request: TrainableAggregationExecutionRequest


@dataclass(frozen=True, slots=True)
class _TrainablePublicationMember:
    ordinal: int
    target_version_id: uuid.UUID
    target_version_artifact_id: uuid.UUID
    training_preset_version_id: uuid.UUID
    training_preset_version_artifact_id: uuid.UUID
    target_key: str
    training_preset_key: str
    training_preset_semantics: Mapping[str, object]
    decision_cutoff_at_by_date: Mapping[date, datetime]


@dataclass(frozen=True, slots=True)
class _TrainableMemberAxis:
    ordinal: int
    target_version_id: uuid.UUID
    target_version_artifact_id: uuid.UUID
    target_key: str
    target_semantics: Mapping[str, object]
    training_preset_version_id: uuid.UUID
    training_preset_version_artifact_id: uuid.UUID
    training_preset_key: str
    training_preset_semantics: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _ReusableTrainableEnsemble:
    members: tuple[CompactTrainableMember, ...]
    publications: tuple[PublishedTrainablePayload, ...]
    diagnostic: TrainableEnsembleDiagnostic


class TrainableAggregationWorkExecutor:
    """Execute and publish one already-planned trainable Aggregation Work."""

    def __init__(
        self,
        engine: Engine,
        *,
        object_store: LocalPayloadObjectStore,
        object_root: Path,
    ) -> None:
        self._engine = engine
        self._object_store = object_store
        self._object_root = object_root.resolve()

    def execute(
        self,
        *,
        graph_run_id: uuid.UUID,
        claim: ClaimedGraphWork,
        worker_key: str,
    ) -> PublishedAggregationOutput:
        loaded = load_trainable_aggregation_work(
            self._engine,
            object_root=self._object_root,
            graph_run_id=graph_run_id,
            claim=claim,
            worker_key=worker_key,
        )
        context = loaded.context
        publication_members = tuple(
            _TrainablePublicationMember(
                ordinal=member.ordinal,
                target_version_id=member.target_version_id,
                target_version_artifact_id=member.target_version_artifact_id,
                training_preset_version_id=member.training_preset_version_id,
                training_preset_version_artifact_id=member.training_preset_version_artifact_id,
                target_key=member.request.target.target_key,
                training_preset_key=member.request.training_preset_key,
                training_preset_semantics=member.request.training_preset_semantics,
                decision_cutoff_at_by_date=member.request.decision_cutoff_at_by_date,
            )
            for member in loaded.members
        )
        oof_publications: tuple[PublishedTrainablePayload, ...]
        if len(publication_members) == 1:
            request = loaded.members[0].request
            del loaded
            execution = execute_trainable_aggregation(request)
            del request
            oof_publications = (self._publish_member(context, publication_members[0], execution),)
            diagnostic = calculate_trainable_ensemble_diagnostic(
                context.family_key,
                (
                    EnsembleDiagnosticMemberInput(
                        target_key=publication_members[0].target_key,
                        training_preset_key=publication_members[0].training_preset_key,
                        prediction_fingerprint=execution.oof_result.fingerprint,
                        fold_count=len(execution.folds),
                        predictions=execution.oof_result.predictions,
                        target_rows=execution.matrix.rows,
                    ),
                ),
                ensemble_fingerprint=context.ensemble_fingerprint,
            )
            calculation = execution.calculation
            del execution
        else:
            if context.ensemble_fingerprint is None:
                raise TrainableAggregationError(
                    "Multi-member calculation lacks its frozen Ensemble fingerprint"
                )
            reusable = _load_reusable_trainable_ensemble(
                self._engine,
                object_store=self._object_store,
                context=context,
                publication_members=publication_members,
            )
            if reusable is not None:
                oof_publications = reusable.publications
                diagnostic = reusable.diagnostic
                compact_result = combine_compact_member_scores(
                    context.family_key,
                    reusable.members,
                    ensemble_fingerprint=context.ensemble_fingerprint,
                )
                del reusable
            else:
                compact_members: list[CompactTrainableMember] = []
                oof_publication_list: list[PublishedTrainablePayload] = []
                for ordinal, publication_member in enumerate(publication_members):
                    # A real historical member can take many minutes. Renew between
                    # members so a healthy multi-target ensemble never loses its
                    # fence merely because the total group exceeds one base lease.
                    GraphDagService(self._engine).renew(claim, worker_key=worker_key)
                    if ordinal:
                        loaded = load_trainable_aggregation_work(
                            self._engine,
                            object_root=self._object_root,
                            graph_run_id=graph_run_id,
                            claim=claim,
                            worker_key=worker_key,
                        )
                        if loaded.context.execution_fingerprint != context.execution_fingerprint:
                            raise TrainableAggregationError(
                                "Trainable Work identity drifted while loading Ensemble members"
                            )
                    request = loaded.members[ordinal].request
                    del loaded
                    execution = execute_trainable_aggregation(request)
                    del request
                    oof_publication_list.append(
                        self._publish_member(context, publication_member, execution)
                    )
                    compact_members.append(
                        compact_member_execution(
                            target_key=publication_member.target_key,
                            training_preset_key=publication_member.training_preset_key,
                            prediction_fingerprint=execution.oof_result.fingerprint,
                            fold_count=len(execution.folds),
                            predictions=execution.oof_result.predictions,
                            matrix_rows=execution.matrix.rows,
                        )
                    )
                    del execution
                oof_publications = tuple(oof_publication_list)
                del oof_publication_list
                compact_result, diagnostic = combine_compact_trainable_members(
                    context.family_key,
                    tuple(compact_members),
                    ensemble_fingerprint=context.ensemble_fingerprint,
                )
                del compact_members
            calculation = _compact_ensemble_calculation(
                context,
                compact_result,
                publication_members[0].decision_cutoff_at_by_date,
            )
            del compact_result
        diagnostic_dependencies = tuple(
            DependencyInput(publication.artifact_id, "oof_prediction", ordinal)
            for ordinal, publication in enumerate(oof_publications)
        )
        if context.ensemble_spec_artifact_id is not None:
            diagnostic_dependencies = (
                *diagnostic_dependencies,
                DependencyInput(
                    context.ensemble_spec_artifact_id,
                    "trainable_ensemble_spec",
                    len(oof_publications),
                ),
            )
        diagnostic_publication = publish_trainable_aggregation_diagnostic(
            self._engine,
            aggregation_run_id=context.aggregation_run_id,
            ensemble_spec_id=context.ensemble_spec_id,
            diagnostic=diagnostic,
            dependencies=diagnostic_dependencies,
        )
        content = encode_final_signal_numeric_parquet(calculation.points)
        prepared = _prepare_aggregation_output(
            self._object_store,
            context=context,
            calculation=calculation,
            content=content,
        )
        dependencies = [
            DependencyInput(
                publication.artifact_id,
                "oof_prediction",
                len(context.inputs) + 1 + ordinal,
            )
            for ordinal, publication in enumerate(oof_publications)
        ]
        if context.ensemble_spec_artifact_id is not None:
            dependencies.append(
                DependencyInput(
                    context.ensemble_spec_artifact_id,
                    "trainable_ensemble_spec",
                    len(context.inputs) + 1 + len(oof_publications),
                )
            )
        dependencies.append(
            DependencyInput(
                diagnostic_publication.artifact_id,
                "trainable_aggregation_diagnostic",
                len(context.inputs) + 2 + len(oof_publications),
            )
        )
        return _publish_aggregation_output(
            self._engine,
            context=context,
            calculation=calculation,
            prepared=prepared,
            additional_dependencies=tuple(dependencies),
        )

    def _publish_member(
        self,
        context: _AggregationWorkContext,
        member: _TrainablePublicationMember,
        execution: TrainableAggregationExecution,
    ) -> PublishedTrainablePayload:
        if context.feature_schema_version_id is None or context.feature_schema_artifact_id is None:
            raise TrainableAggregationError("Supervised publication identity is incomplete")
        bindings, cohort_artifact_id, cohort_id = _publication_bindings(self._engine, context)
        input_dependencies = tuple(
            DependencyInput(item.manifest_artifact_id, "feature_input", item.ordinal)
            for item in context.inputs
        )
        matrix = publish_training_matrix(
            self._engine,
            object_store=self._object_store,
            matrix=execution.matrix,
            feature_schema_version_id=context.feature_schema_version_id,
            target_version_id=member.target_version_id,
            evaluation_cohort_version_id=cohort_id,
            binding=bindings["training_matrix_numeric"],
            dependencies=(
                DependencyInput(context.feature_schema_artifact_id, "feature_schema", 0),
                DependencyInput(member.target_version_artifact_id, "target_version", 1),
                DependencyInput(cohort_artifact_id, "evaluation_cohort", 2),
                *tuple(
                    DependencyInput(
                        dependency.artifact_id,
                        dependency.role,
                        ordinal + 3,
                    )
                    for ordinal, dependency in enumerate(input_dependencies)
                ),
            ),
        )
        policy = publish_walk_forward_policy(
            self._engine,
            policy=execution.policy,
            # The walk-forward policy is shared by every member that freezes
            # the same train/validation/prediction schedule.  A dependency on
            # one Training Preset would make the same policy identity acquire
            # different semantics as soon as an ensemble selects two presets.
            dependencies=(),
        )
        folds = publish_training_folds(
            self._engine,
            matrix=execution.matrix,
            training_matrix_id=matrix.projection_id,
            training_matrix_artifact_id=matrix.artifact_id,
            policy=execution.policy,
            policy_publication=policy,
            folds=execution.folds,
        )
        semantics = member.training_preset_semantics
        hyperparameters = cast(Mapping[str, object], semantics["hyperparameters"])
        seed = cast(int, semantics["seed"])
        spec = publish_base_learner_spec(
            self._engine,
            aggregation_version_id=context.aggregation_version_id,
            feature_schema_version_id=context.feature_schema_version_id,
            target_version_id=member.target_version_id,
            training_preset_version_id=member.training_preset_version_id,
            fold_policy_version_id=policy.projection_id,
            adapter_key=execution.oof_result.adapter_key,
            adapter_version=execution.oof_result.adapter_version,
            hyperparameters=hyperparameters,
            random_seed=seed,
            dependencies=(
                DependencyInput(context.aggregation_version_artifact_id, "aggregation_version", 0),
                DependencyInput(context.feature_schema_artifact_id, "feature_schema", 1),
                DependencyInput(member.target_version_artifact_id, "target_version", 2),
                DependencyInput(
                    member.training_preset_version_artifact_id,
                    "training_preset",
                    3,
                ),
                DependencyInput(policy.artifact_id, "fold_policy", 4),
            ),
        )
        labels_known_through_by_date: dict[date, datetime] = {}
        for row in execution.matrix.rows:
            if not row.target_available:
                continue
            prior = labels_known_through_by_date.get(row.decision_date)
            if prior is None or row.target_known_at > prior:
                labels_known_through_by_date[row.decision_date] = row.target_known_at
        states = []
        for fitted, fold, fold_publication in zip(
            execution.oof_result.fitted_folds,
            execution.folds,
            folds,
            strict=True,
        ):
            train_label_dates = tuple(
                labels_known_through_by_date.get(day) for day in fold.train_dates
            )
            if not train_label_dates or any(item is None for item in train_label_dates):
                raise TrainableAggregationError("Fitted Fold lacks training rows")
            state = publish_fitted_model_state(
                self._engine,
                object_store=self._object_store,
                fitted=fitted,
                base_learner_spec_id=spec.projection_id,
                training_fold_id=fold_publication.projection_id,
                trained_through=fold.train_dates[-1],
                labels_known_through=max(cast(datetime, item) for item in train_label_dates),
                environment_fingerprint=context.environment_fingerprint,
                binding=bindings["fitted_regression_model"],
                dependencies=(
                    DependencyInput(spec.artifact_id, "base_learner_spec", 0),
                    DependencyInput(fold_publication.artifact_id, "training_fold", 1),
                    DependencyInput(matrix.artifact_id, "training_matrix", 2),
                ),
            )
            states.append(state)
        fold_links = tuple(
            OofFoldPublicationLink(
                training_fold_id=fold.projection_id,
                fitted_model_state_id=state.projection_id,
                ordinal=ordinal,
            )
            for ordinal, (fold, state) in enumerate(zip(folds, states, strict=True))
        )
        oof = publish_oof_prediction(
            self._engine,
            object_store=self._object_store,
            result=execution.oof_result,
            base_learner_spec_id=spec.projection_id,
            decision_known_at=member.decision_cutoff_at_by_date,
            fold_links=fold_links,
            binding=bindings["oof_regression_prediction"],
            dependencies=(
                DependencyInput(spec.artifact_id, "base_learner_spec", 0),
                *tuple(
                    DependencyInput(state.artifact_id, "fitted_model_state", index + 1)
                    for index, state in enumerate(states)
                ),
            ),
        )
        return oof


def load_trainable_aggregation_work(
    engine: Engine,
    *,
    object_root: Path,
    graph_run_id: uuid.UUID,
    claim: ClaimedGraphWork,
    worker_key: str,
) -> LoadedTrainableAggregationWork:
    """Load one exact supervised Work into the pure execution boundary."""

    if claim.work_kind != "aggregation":
        raise TrainableAggregationError("Trainable executor accepts only Aggregation GraphWork")
    context = _load_work_context(
        engine,
        graph_run_id=graph_run_id,
        claim=claim,
        worker_key=worker_key,
        execution_mode="supervised",
    )
    if context.feature_schema_document is None or context.feature_schema_fingerprint is None:
        raise TrainableAggregationError("Supervised Work lacks its compiled Feature Schema")
    if context.ensemble_spec_id is not None:
        axes = tuple(
            _TrainableMemberAxis(
                ordinal=member.ordinal,
                target_version_id=member.target_version_id,
                target_version_artifact_id=member.target_version_artifact_id,
                target_key=member.target_key,
                target_semantics=member.target_semantics,
                training_preset_version_id=member.training_preset_version_id,
                training_preset_version_artifact_id=(member.training_preset_version_artifact_id),
                training_preset_key=member.training_preset_key,
                training_preset_semantics=member.training_preset_semantics,
            )
            for member in context.ensemble_members
        )
    else:
        if (
            context.target_version_id is None
            or context.target_version_artifact_id is None
            or context.target_key is None
            or context.target_semantics is None
            or context.training_preset_version_id is None
            or context.training_preset_version_artifact_id is None
            or context.training_preset_key is None
            or context.training_preset_semantics is None
        ):
            raise TrainableAggregationError(
                "Direct supervised Work lacks its exact Target or Training Preset"
            )
        axes = (
            _TrainableMemberAxis(
                ordinal=0,
                target_version_id=context.target_version_id,
                target_version_artifact_id=context.target_version_artifact_id,
                target_key=context.target_key,
                target_semantics=context.target_semantics,
                training_preset_version_id=context.training_preset_version_id,
                training_preset_version_artifact_id=(context.training_preset_version_artifact_id),
                training_preset_key=context.training_preset_key,
                training_preset_semantics=context.training_preset_semantics,
            ),
        )
    if not axes or tuple(item.ordinal for item in axes) != tuple(range(len(axes))):
        raise TrainableAggregationError(
            "Supervised Work member axes are incomplete or non-contiguous"
        )
    targets = tuple(_fixed_target(axis.target_key, axis.target_semantics) for axis in axes)
    maximum_target_horizon = max(target.horizon_sessions for target in targets)
    ordered_feature_keys = tuple(
        cast(list[str], context.feature_schema_document["ordered_feature_keys"])
    )
    if ordered_feature_keys != tuple(item.feature_variant_key for item in context.inputs):
        raise TrainableAggregationError(
            "Compiled Feature Schema differs from ordered Aggregation inputs"
        )
    if FeatureSchema(ordered_feature_keys).fingerprint != context.feature_schema_fingerprint:
        raise TrainableAggregationError(
            "Compiled Feature Schema differs from the runtime Feature Schema contract"
        )
    with engine.connect() as connection:
        cohort = (
            connection.execute(
                text(
                    """
                SELECT cohort.evaluation_cohort_version_id,
                       cohort.dataset_publication_id,cohort.calendar_version_id,
                       cohort.warmup_start,cohort.evaluation_start,
                       cohort.evaluation_end,
                       contract.evaluation_cohort_runtime_contract_id
                  FROM experiment.v022_research_suite_evaluation_cohort_binding binding
                  JOIN experiment.v022_evaluation_cohort_version cohort
                    ON cohort.evaluation_cohort_version_id=
                       binding.evaluation_cohort_version_id
                  JOIN experiment.v022_evaluation_cohort_runtime_contract contract
                    ON contract.evaluation_cohort_version_id=
                       cohort.evaluation_cohort_version_id
                 WHERE binding.research_suite_id=:suite
                """
                ),
                {"suite": context.research_suite_id},
            )
            .mappings()
            .one_or_none()
        )
        if cohort is None:
            raise TrainableAggregationError(
                "Supervised Work requires one exact Cohort Runtime Contract"
            )
        session_rows = (
            connection.execute(
                text(
                    """
                SELECT session.session_date,calendar.open_at_utc,calendar.close_at_utc
                  FROM experiment.v022_evaluation_cohort_session session
                  JOIN catalog.calendar_session calendar
                    ON calendar.calendar_version_id=:calendar
                   AND calendar.session_date=session.session_date
                 WHERE session.evaluation_cohort_version_id=:cohort
                 ORDER BY session.ordinal
                """
                ),
                {
                    "calendar": cohort["calendar_version_id"],
                    "cohort": cohort["evaluation_cohort_version_id"],
                },
            )
            .mappings()
            .all()
        )
        cohort_sessions = tuple(cast(date, row["session_date"]) for row in session_rows)
        if not cohort_sessions:
            raise TrainableAggregationError("Evaluation Cohort session panel is empty")
        post_rows = (
            connection.execute(
                text(
                    """
                SELECT session_date,open_at_utc,close_at_utc
                  FROM catalog.calendar_session
                 WHERE calendar_version_id=:calendar AND session_date>:end
                 ORDER BY session_date
                 LIMIT :limit
                """
                ),
                {
                    "calendar": cohort["calendar_version_id"],
                    "end": cohort["evaluation_end"],
                    "limit": maximum_target_horizon + 1,
                },
            )
            .mappings()
            .all()
        )
        if len(post_rows) != maximum_target_horizon + 1:
            raise TrainableAggregationError(
                "Frozen Calendar lacks post-evaluation sessions for Target maturity"
            )
        all_session_rows = (*session_rows, *post_rows)
        all_sessions = tuple(cast(date, row["session_date"]) for row in all_session_rows)
        decision_cutoff_at = {
            cast(date, row["session_date"]): cast(datetime, row["close_at_utc"])
            for row in session_rows
        }
        candidate_by_date = _candidate_mask(
            connection,
            runtime_contract_id=cohort["evaluation_cohort_runtime_contract_id"],
            sessions=cohort_sessions,
            evaluation_start=cohort["evaluation_start"],
            allowed_security_ids=frozenset(context.asset_keys),
        )
        adjusted_opens = _adjusted_opens(
            connection,
            dataset_publication_id=cohort["dataset_publication_id"],
            security_keys=context.asset_keys,
            session_rows=all_session_rows,
        )
    reader = VerifiedSignalManifestReader(engine, object_root)
    feature_inputs = tuple(
        TrainableFeatureInput(
            feature_key=item.feature_variant_key,
            manifest_fingerprint=item.manifest_hash,
            points=list(
                reader.read(
                    payload_manifest_id=item.payload_manifest_id,
                    expected_manifest_hash=item.manifest_hash,
                    expected_artifact_id=item.manifest_artifact_id,
                    catalog_release_id=context.catalog_release_id,
                    allowed_asset_keys=context.asset_keys,
                    decision_dates=frozenset(cohort_sessions),
                )
            ),
        )
        for item in context.inputs
    )
    candidate_by_date = _feature_complete_candidate_mask(
        feature_inputs=feature_inputs,
        candidate_security_ids_by_date=candidate_by_date,
        allowed_security_ids=frozenset(context.asset_keys),
    )
    training_start = _first_complete_training_session(
        sessions=cohort_sessions,
        evaluation_start=cohort["evaluation_start"],
        candidate_security_ids_by_date=candidate_by_date,
    )
    members = tuple(
        LoadedTrainableAggregationMember(
            ordinal=axis.ordinal,
            target_version_id=axis.target_version_id,
            target_version_artifact_id=axis.target_version_artifact_id,
            training_preset_version_id=axis.training_preset_version_id,
            training_preset_version_artifact_id=(axis.training_preset_version_artifact_id),
            request=TrainableAggregationExecutionRequest(
                family_key=context.family_key,
                target=target,
                training_preset_key=axis.training_preset_key,
                training_preset_semantics=axis.training_preset_semantics,
                feature_inputs=feature_inputs,
                security_keys_by_asset_id=context.asset_keys,
                sessions=all_sessions,
                adjusted_opens=adjusted_opens,
                candidate_security_ids_by_date=candidate_by_date,
                decision_cutoff_at_by_date=decision_cutoff_at,
                training_start=training_start,
                prediction_start=cohort["evaluation_start"],
                prediction_end=cohort["evaluation_end"],
                candidate_mask_is_feature_complete=True,
                consume_source_panels=True,
            ),
        )
        for axis, target in zip(axes, targets, strict=True)
    )
    return LoadedTrainableAggregationWork(context=context, members=members)


def execute_trainable_aggregation(
    request: TrainableAggregationExecutionRequest,
) -> TrainableAggregationExecution:
    """Execute one strict regression member over an exact frozen daily panel.

    This function deliberately performs no database or clock access.  The caller
    supplies the frozen calendar, asset/security mapping, candidate mask, PIT
    cutoffs, adjusted-open Target evidence, and exact input Manifest points.
    """

    adapter, policy, hyperparameters, seed = _training_configuration(request)
    family_key = request.family_key
    target_fingerprint = request.target.fingerprint
    training_preset_key = request.training_preset_key
    security_keys_by_asset_id = request.security_keys_by_asset_id
    decision_cutoff_at_by_date = request.decision_cutoff_at_by_date
    sessions = request.sessions
    prediction_start = request.prediction_start
    prediction_end = request.prediction_end
    schema = FeatureSchema(tuple(item.feature_key for item in request.feature_inputs))
    model_candidates = (
        request.candidate_security_ids_by_date
        if request.candidate_mask_is_feature_complete
        else _feature_complete_candidate_mask(
            feature_inputs=request.feature_inputs,
            candidate_security_ids_by_date=request.candidate_security_ids_by_date,
            allowed_security_ids=frozenset(request.security_keys_by_asset_id),
        )
    )
    target_panel = build_fixed_session_target_panel(
        request.target,
        request.sessions,
        request.adjusted_opens,
        requested_start=request.training_start,
        requested_end=request.prediction_end,
        candidate_security_ids_by_date=model_candidates,
        label_observation_end=request.prediction_end,
    )
    target = target_panel.target
    target_points: list[CrossSectionalTargetPoint | None] = list(target_panel.points)
    del target_panel
    if request.consume_source_panels:
        # The target panel is now self-contained.  Drop the multi-million-row
        # adjusted-open source before the Matrix starts to grow.
        request = replace(request, adjusted_opens=())
    matrix = _build_training_matrix_from_manifests(
        request,
        schema,
        target,
        target_points,
        model_candidates,
    )
    # Matrix rows are the compact canonical representation used by fitting and
    # publication.  Release the much larger source manifests, adjusted-open
    # panel, transient Feature points, and Target panel before OOF fitting.
    del target_points
    del request
    folds = build_expanding_walk_forward_folds(
        matrix,
        policy,
        prediction_start=prediction_start,
        prediction_end=prediction_end,
    )
    oof = run_strict_oof_predictions(
        adapter,
        matrix,
        folds,
        hyperparameters=hyperparameters,
        seed=seed,
    )
    calculation = _oof_calculation(
        family_key=family_key,
        target_fingerprint=target_fingerprint,
        training_preset_key=training_preset_key,
        security_keys_by_asset_id=security_keys_by_asset_id,
        decision_cutoff_at_by_date=decision_cutoff_at_by_date,
        sessions=sessions,
        prediction_start=prediction_start,
        prediction_end=prediction_end,
        result=oof,
        candidate_security_ids_by_date=model_candidates,
    )
    return TrainableAggregationExecution(schema, matrix, policy, folds, oof, calculation)


def _training_configuration(
    request: TrainableAggregationExecutionRequest,
) -> tuple[
    RegressionModelAdapter,
    WalkForwardPolicy,
    Mapping[str, object],
    int,
]:
    semantics = request.training_preset_semantics
    adapter: RegressionModelAdapter
    if request.family_key == "ols_cross_sectional_regression":
        expected_adapter_key = "ols_cross_sectional_regression"
        expected_adapter_version = "numpy_lstsq_v1"
        adapter = OrdinaryLeastSquaresAdapter()
    elif request.family_key == "ridge_cross_sectional_regression":
        expected_adapter_key = "ridge_cross_sectional_regression"
        expected_adapter_version = "numpy_closed_form_v1"
        adapter = RidgeRegressionAdapter()
    elif request.family_key == "random_forest_cross_sectional_regression":
        expected_adapter_key = "random_forest_cross_sectional_regression"
        expected_adapter_version = "sklearn_random_forest_regressor_v1"
        adapter = RandomForestRegressionAdapter()
    elif request.family_key == "lightgbm_cross_sectional_regression":
        expected_adapter_key = "lightgbm_cross_sectional_regression"
        expected_adapter_version = "lightgbm_regressor_cpu_v1"
        adapter = LightGbmRegressionAdapter()
    elif request.family_key == "xgboost_cross_sectional_regression":
        expected_adapter_key = "xgboost_cross_sectional_regression"
        expected_adapter_version = "xgboost_regressor_cpu_hist_v1"
        adapter = XgBoostRegressionAdapter()
    else:
        raise TrainableAggregationError("Unsupported trainable regression Family")
    if (
        semantics.get("adapter_key") != expected_adapter_key
        or semantics.get("adapter_version") != expected_adapter_version
        or semantics.get("observation_grid") != "xnys_completed_session_daily"
        or semantics.get("fold_mode") != "expanding_walk_forward"
        or semantics.get("random_split") is not False
    ):
        raise TrainableAggregationError(
            "Training Preset does not match the frozen supervised implementation"
        )
    hyperparameters = semantics.get("hyperparameters")
    seed = semantics.get("seed")
    if not isinstance(hyperparameters, dict):
        raise TrainableAggregationError("Training Preset hyperparameters must be an object")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TrainableAggregationError("Training Preset seed must be an integer")
    normalized_hyperparameters: dict[str, object] = dict(hyperparameters)
    if request.family_key == "ridge_cross_sectional_regression":
        alpha = normalized_hyperparameters.get("alpha")
        if isinstance(alpha, str):
            try:
                normalized_hyperparameters["alpha"] = Decimal(alpha)
            except Exception as error:
                raise TrainableAggregationError("Ridge alpha is not a canonical decimal") from error
    policy_key = semantics.get("fold_policy_key")
    minimum_train_groups = semantics.get("minimum_train_groups")
    validation_groups = semantics.get("validation_groups")
    prediction_groups = semantics.get("prediction_groups")
    embargo_groups = semantics.get("embargo_groups")
    integer_policy_values = (
        minimum_train_groups,
        validation_groups,
        prediction_groups,
        embargo_groups,
    )
    if (
        not isinstance(policy_key, str)
        or not policy_key.strip()
        or any(
            isinstance(value, bool) or not isinstance(value, int) for value in integer_policy_values
        )
    ):
        raise TrainableAggregationError("Training Preset is missing its exact walk-forward policy")
    assert isinstance(minimum_train_groups, int)
    assert isinstance(validation_groups, int)
    assert isinstance(prediction_groups, int)
    assert isinstance(embargo_groups, int)
    policy = WalkForwardPolicy(
        policy_key=policy_key,
        minimum_train_groups=minimum_train_groups,
        validation_groups=validation_groups,
        prediction_groups=prediction_groups,
        embargo_groups=embargo_groups,
    )
    return adapter, policy, normalized_hyperparameters, seed


def _build_training_matrix_from_manifests(
    request: TrainableAggregationExecutionRequest,
    schema: FeatureSchema,
    target: FixedSessionTarget,
    target_points: list[CrossSectionalTargetPoint | None],
    candidate_security_ids_by_date: Mapping[date, frozenset[uuid.UUID]],
) -> TrainingMatrix:
    """Build the exact Matrix without duplicating the full Feature panel.

    The verified input Manifests are already canonical ordered streams.  The
    previous implementation expanded every input into another tuple of Python
    Feature objects and then a multi-million-entry dictionary.  That transient
    representation dominated memory on the full S&P history.  This merge keeps
    only one row from each input live while preserving every completeness,
    ordering, PIT, and candidate-mask check.
    """

    def _selected_points(
        input_: TrainableFeatureInput,
    ) -> Iterator[tuple[tuple[date, str], SignalManifestPoint]]:
        prior_key: tuple[date, str] | None = None
        for point_index, maybe_point in enumerate(input_.points):
            if maybe_point is None:
                raise TrainableAggregationError(
                    "Trainable Feature source was consumed before execution"
                )
            point = maybe_point
            if request.consume_source_panels:
                if not isinstance(input_.points, list):
                    raise TrainableAggregationError(
                        "Consumable Feature input requires mutable runtime storage"
                    )
                input_.points[point_index] = None
            security_key = request.security_keys_by_asset_id.get(point.asset_id)
            if security_key is None:
                raise TrainableAggregationError(
                    "Feature input contains an Asset outside the frozen Security mapping"
                )
            if point.asset_key != security_key:
                raise TrainableAggregationError("Feature input Security key drift")
            key = (point.decision_date, str(point.asset_id))
            if prior_key is not None and key <= prior_key:
                raise TrainableAggregationError(
                    "Trainable Feature Manifest is not in canonical unique order"
                )
            prior_key = key
            # Input Manifests cover the whole frozen Cohort, including the
            # feature's own warm-up rows.  Those rows are evidence, not model
            # observations; completeness becomes mandatory from the exact
            # first complete training session onward.
            if not request.training_start <= point.decision_date <= request.prediction_end:
                continue
            candidates = candidate_security_ids_by_date.get(point.decision_date)
            if candidates is None or point.asset_id not in candidates:
                continue
            if point.signal_value is None:
                raise TrainableAggregationError(
                    "A selectable training observation has a missing Feature value"
                )
            if point.missing_reason is not None:
                raise TrainableAggregationError(
                    "A present training Feature cannot carry a missing reason"
                )
            yield key, point

    target_index = 0

    def _next_target() -> CrossSectionalTargetPoint | None:
        nonlocal target_index
        if target_index >= len(target_points):
            return None
        point = target_points[target_index]
        if point is None:
            raise TrainableAggregationError("Target source was consumed before execution")
        if request.consume_source_panels:
            target_points[target_index] = None
        target_index += 1
        return point

    target_point = _next_target()
    target_day: date | None = None
    targets_for_day: dict[uuid.UUID, CrossSectionalTargetPoint] = {}
    rows: list[TrainingMatrixRow] = []
    observed_dates: set[date] = set()
    streams = tuple(_selected_points(input_) for input_ in request.feature_inputs)
    for grouped in zip(*streams, strict=True):
        first_key = grouped[0][0]
        if any(key != first_key for key, _point in grouped[1:]):
            raise TrainableAggregationError(
                "Trainable Feature inputs do not share the exact complete panel"
            )
        decision_date, security_id_text = first_key
        first_point = grouped[0][1]
        if str(first_point.asset_id) != security_id_text:
            raise TrainableAggregationError("Trainable Feature identity drift")
        security_key = first_point.asset_key
        cutoff = request.decision_cutoff_at_by_date.get(decision_date)
        if cutoff is None:
            raise TrainableAggregationError("Training matrix lacks a decision cutoff")
        feature_values: list[Decimal] = []
        for (_key, point), _expected_feature_key in zip(
            grouped,
            schema.ordered_feature_keys,
            strict=True,
        ):
            if point.known_at > cutoff:
                raise TrainableAggregationError("Training feature violates the decision cutoff")
            feature_values.append(
                point.signal_value.quantize(VALUE_QUANTUM, rounding=ROUND_HALF_EVEN)
            )

        if target_day != decision_date:
            if targets_for_day:
                raise TrainableAggregationError(
                    "Target panel contains an observation outside the Feature panel"
                )
            target_day = decision_date
            while target_point is not None and target_point.decision_date < decision_date:
                raise TrainableAggregationError(
                    "Target panel contains an observation outside the Feature panel"
                )
            while target_point is not None and target_point.decision_date == decision_date:
                targets_for_day[target_point.security_id] = target_point
                target_point = _next_target()
        row_target = targets_for_day.pop(first_point.asset_id, None)
        rows.append(
            TrainingMatrixRow(
                security_id=first_point.asset_id,
                security_key=security_key,
                decision_date=decision_date,
                decision_cutoff_at=cutoff,
                feature_values=tuple(feature_values),
                target_value=(row_target.centered_rank if row_target is not None else Decimal()),
                target_known_at=(row_target.label_known_at if row_target is not None else cutoff),
                target_entry_date=(
                    row_target.entry_date if row_target is not None else decision_date
                ),
                target_exit_date=(
                    row_target.exit_date if row_target is not None else decision_date
                ),
                target_available=row_target is not None,
            )
        )
        observed_dates.add(decision_date)
    if targets_for_day or target_point is not None:
        raise TrainableAggregationError(
            "Target panel contains an observation outside the Feature panel"
        )
    expected_row_count = sum(
        len(security_ids)
        for day, security_ids in candidate_security_ids_by_date.items()
        if request.training_start <= day <= request.prediction_end
    )
    if len(rows) != expected_row_count:
        raise TrainableAggregationError(
            "Training matrix does not cover the exact frozen candidate panel"
        )
    expected_prediction_dates = {
        day
        for day in request.decision_cutoff_at_by_date
        if request.prediction_start <= day <= request.prediction_end
    }
    if not expected_prediction_dates.issubset(observed_dates):
        raise TrainableAggregationError(
            "Prediction matrix does not cover the exact frozen evaluation panel"
        )
    return TrainingMatrix(
        schema,
        target,
        tuple(rows),
        tuple(sorted(observed_dates)),
    )


def _oof_calculation(
    *,
    family_key: str,
    target_fingerprint: str,
    training_preset_key: str,
    security_keys_by_asset_id: Mapping[uuid.UUID, str],
    decision_cutoff_at_by_date: Mapping[date, datetime],
    sessions: tuple[date, ...],
    prediction_start: date,
    prediction_end: date,
    result: StrictOofResult,
    candidate_security_ids_by_date: Mapping[date, frozenset[uuid.UUID]],
) -> AggregationCalculation:
    points: list[AggregationOutputPoint] = []
    for prediction in result.predictions:
        security_key = security_keys_by_asset_id.get(prediction.security_id)
        known_at = decision_cutoff_at_by_date.get(prediction.decision_date)
        if security_key is None or known_at is None:
            raise TrainableAggregationError(
                "OOF prediction cannot map to its frozen Asset and cutoff"
            )
        if security_key != prediction.security_key:
            raise TrainableAggregationError("OOF prediction Security key drift")
        points.append(
            AggregationOutputPoint(
                asset_id=prediction.security_id,
                asset_key=security_key,
                decision_date=prediction.decision_date,
                signal_value=prediction.centered_rank,
                known_at=known_at,
                input_revision=result.fingerprint,
                missing_reason=None,
            )
        )
    points.sort(key=lambda item: (item.decision_date, str(item.asset_id)))
    prediction_dates = frozenset(item.decision_date for item in points)
    expected_dates = frozenset(day for day in sessions if prediction_start <= day <= prediction_end)
    if prediction_dates != expected_dates:
        raise TrainableAggregationError(
            "OOF prediction does not cover the exact frozen evaluation session panel"
        )
    for day in expected_dates:
        expected_assets = candidate_security_ids_by_date.get(day)
        actual_assets = {item.asset_id for item in points if item.decision_date == day}
        if expected_assets is None or actual_assets != set(expected_assets):
            raise TrainableAggregationError(
                "OOF prediction does not cover the exact frozen candidate cross-section"
            )
    calculation_fingerprint = sha256_hexdigest(
        {
            "calculation_contract": "strict_oof_centered_rank_v2",
            "family_key": family_key,
            "target_fingerprint": target_fingerprint,
            "training_preset_key": training_preset_key,
            "oof_fingerprint": result.fingerprint,
            "prediction_start": prediction_start,
            "prediction_end": prediction_end,
            "point_count": len(points),
        }
    )
    return AggregationCalculation(
        family_key=family_key,
        parameter_preset_key=None,
        points=tuple(points),
        calculation_fingerprint=calculation_fingerprint,
    )


def _compact_ensemble_calculation(
    context: _AggregationWorkContext,
    result: CompactTrainableEnsembleResult,
    decision_cutoff_at_by_date: Mapping[date, datetime],
) -> AggregationCalculation:
    if context.ensemble_fingerprint is None or context.ensemble_spec_id is None:
        raise TrainableAggregationError("Multi-member calculation lacks its frozen Ensemble Spec")
    if len(result.centered_scores) < 1:
        raise TrainableAggregationError("Ensemble prediction is empty")
    points: list[AggregationOutputPoint] = []
    for index, score in enumerate(result.centered_scores):
        decision_date = date.fromordinal(int(result.session_ordinals[index]))
        security_id = security_uuid(result.security_id_bytes[index])
        security_key = context.asset_keys.get(security_id)
        known_at = decision_cutoff_at_by_date.get(decision_date)
        if security_key is None or known_at is None:
            raise TrainableAggregationError(
                "Ensemble prediction does not map to its frozen Asset and cutoff"
            )
        points.append(
            AggregationOutputPoint(
                asset_id=security_id,
                asset_key=security_key,
                decision_date=decision_date,
                signal_value=q18_decimal(score),
                known_at=known_at,
                input_revision=result.fingerprint,
                missing_reason=None,
            )
        )
    # OOF payloads are canonically ordered by the published Security key for
    # audit readability.  Final signal payloads use the platform-wide UUID
    # order instead, so normalize explicitly at this contract boundary.
    points.sort(key=lambda item: (item.decision_date, str(item.asset_id)))
    ordered_points = tuple(points)
    calculation_fingerprint = sha256_hexdigest(
        {
            "calculation_contract": "compact_trainable_ensemble_v2",
            "family_key": context.family_key,
            "ensemble_spec_id": context.ensemble_spec_id,
            "ensemble_fingerprint": context.ensemble_fingerprint,
            "ensemble_prediction_fingerprint": result.fingerprint,
            "point_count": len(ordered_points),
        }
    )
    return AggregationCalculation(
        family_key=context.family_key,
        parameter_preset_key=None,
        points=ordered_points,
        calculation_fingerprint=calculation_fingerprint,
    )


def _load_reusable_trainable_ensemble(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    context: _AggregationWorkContext,
    publication_members: tuple[_TrainablePublicationMember, ...],
) -> _ReusableTrainableEnsemble | None:
    """Load one exact published OOF checkpoint for a failed final publication.

    Reuse is intentionally all-or-nothing.  The prior diagnostic must bind the
    same Ensemble Spec and its ordered OOF dependencies must reproduce every
    current Target/Training coordinate, Aggregation Version, Feature Schema and
    verified single-partition payload.  Any incomplete closure falls back to a
    fresh training run.
    """

    if (
        context.ensemble_spec_id is None
        or context.ensemble_fingerprint is None
        or context.feature_schema_version_id is None
    ):
        return None
    with engine.connect() as connection:
        diagnostics = tuple(
            connection.execute(
                text(
                    """
                    SELECT diagnostic.artifact_id,diagnostic.diagnostic_document
                      FROM aggregation.v022_trainable_aggregation_diagnostic diagnostic
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=diagnostic.artifact_id
                     WHERE diagnostic.ensemble_spec_id=:ensemble
                       AND artifact.status='published'
                     ORDER BY diagnostic.created_at DESC
                    """
                ),
                {"ensemble": context.ensemble_spec_id},
            ).mappings()
        )
        for diagnostic_row in diagnostics:
            rows = tuple(
                connection.execute(
                    text(
                        """
                        SELECT dependency.ordinal,
                               prediction.oof_prediction_id,
                               prediction.artifact_id,
                               prediction.artifact_semantic_fingerprint,
                               prediction.prediction_payload_manifest_id,
                               prediction.coverage_start,prediction.coverage_end,
                               prediction.row_count,prediction.group_count,
                               prediction.prediction_fingerprint,
                               spec.aggregation_version_id,
                               spec.feature_schema_version_id,
                               spec.target_version_id,
                               spec.training_preset_version_id,
                               prediction_artifact.status AS prediction_status,
                               manifest.artifact_id AS manifest_artifact_id,
                               manifest.manifest_hash,manifest.partition_count,
                               manifest.byte_size AS manifest_byte_size,
                               manifest.row_or_item_count AS manifest_row_count,
                               manifest.materialization_state,
                               manifest_artifact.status AS manifest_status,
                               partition.byte_size AS partition_byte_size,
                               partition.row_or_item_count AS partition_row_count,
                               object.object_content_hash,object.storage_uri,
                               object.byte_size AS object_byte_size,
                               object.object_state,object.verification_status,
                               object.verified_at,
                               (SELECT count(*)
                                  FROM aggregation.v022_oof_prediction_fold fold_link
                                 WHERE fold_link.oof_prediction_id=
                                       prediction.oof_prediction_id) AS fold_count
                          FROM lineage.artifact_dependency dependency
                          JOIN aggregation.v022_oof_prediction prediction
                            ON prediction.artifact_id=
                               dependency.depends_on_artifact_id
                          JOIN lineage.artifact prediction_artifact
                            ON prediction_artifact.artifact_id=prediction.artifact_id
                          JOIN aggregation.v022_base_learner_spec spec
                            ON spec.base_learner_spec_id=
                               prediction.base_learner_spec_id
                          JOIN data.payload_manifest manifest
                            ON manifest.payload_manifest_id=
                               prediction.prediction_payload_manifest_id
                          JOIN lineage.artifact manifest_artifact
                            ON manifest_artifact.artifact_id=manifest.artifact_id
                          JOIN data.payload_manifest_partition manifest_link
                            ON manifest_link.payload_manifest_id=
                               manifest.payload_manifest_id
                           AND manifest_link.ordinal=0
                          JOIN data.payload_partition partition
                            ON partition.payload_partition_id=
                               manifest_link.payload_partition_id
                          JOIN data.payload_object object
                            ON object.payload_object_id=partition.payload_object_id
                         WHERE dependency.artifact_id=:diagnostic
                           AND dependency.role='oof_prediction'
                         ORDER BY dependency.ordinal
                        """
                    ),
                    {"diagnostic": diagnostic_row["artifact_id"]},
                ).mappings()
            )
            if len(rows) != len(publication_members):
                continue
            if any(
                row["ordinal"] != ordinal
                or row["aggregation_version_id"] != context.aggregation_version_id
                or row["feature_schema_version_id"] != context.feature_schema_version_id
                or row["target_version_id"] != member.target_version_id
                or row["training_preset_version_id"] != member.training_preset_version_id
                or row["prediction_status"] != "published"
                or row["manifest_status"] != "published"
                or row["materialization_state"] != "materialized"
                or row["partition_count"] != 1
                or row["manifest_row_count"] != row["row_count"]
                or row["partition_row_count"] != row["row_count"]
                or row["manifest_byte_size"] != row["partition_byte_size"]
                or row["partition_byte_size"] != row["object_byte_size"]
                or row["object_state"] != "published"
                or row["verification_status"] != "verified"
                or row["verified_at"] is None
                or row["fold_count"] < 1
                for ordinal, (row, member) in enumerate(zip(rows, publication_members, strict=True))
            ):
                continue
            document = cast(dict[str, object], diagnostic_row["diagnostic_document"])
            if (
                document.get("family_key") != context.family_key
                or document.get("ensemble_fingerprint") != context.ensemble_fingerprint
                or document.get("member_count") != len(rows)
            ):
                continue
            compact_members: list[CompactTrainableMember] = []
            publications: list[PublishedTrainablePayload] = []
            valid = True
            for row, member in zip(rows, publication_members, strict=True):
                content = object_store.read(cast(str, row["storage_uri"]))
                if (
                    len(content) != row["object_byte_size"]
                    or hashlib.sha256(content).hexdigest() != row["object_content_hash"]
                ):
                    valid = False
                    break
                compact_members.append(
                    _compact_member_from_published_oof(
                        content,
                        target_key=member.target_key,
                        training_preset_key=member.training_preset_key,
                        prediction_fingerprint=cast(str, row["prediction_fingerprint"]),
                        fold_count=cast(int, row["fold_count"]),
                        expected_row_count=cast(int, row["row_count"]),
                        expected_group_count=cast(int, row["group_count"]),
                        expected_start=cast(date, row["coverage_start"]),
                        expected_end=cast(date, row["coverage_end"]),
                    )
                )
                publications.append(
                    PublishedTrainablePayload(
                        projection_id=cast(uuid.UUID, row["oof_prediction_id"]),
                        artifact_id=cast(uuid.UUID, row["artifact_id"]),
                        artifact_semantic_fingerprint=cast(
                            str, row["artifact_semantic_fingerprint"]
                        ),
                        payload_manifest_id=cast(uuid.UUID, row["prediction_payload_manifest_id"]),
                        manifest_artifact_id=cast(uuid.UUID, row["manifest_artifact_id"]),
                        manifest_hash=cast(str, row["manifest_hash"]),
                        reused=True,
                    )
                )
            if valid:
                return _ReusableTrainableEnsemble(
                    members=tuple(compact_members),
                    publications=tuple(publications),
                    diagnostic=TrainableEnsembleDiagnostic(
                        family_key=context.family_key,
                        ensemble_fingerprint=context.ensemble_fingerprint,
                        diagnostic_document=document,
                    ),
                )
    return None


def _compact_member_from_published_oof(
    content: bytes,
    *,
    target_key: str,
    training_preset_key: str,
    prediction_fingerprint: str,
    fold_count: int,
    expected_row_count: int,
    expected_group_count: int,
    expected_start: date,
    expected_end: date,
) -> CompactTrainableMember:
    parquet = pq.ParquetFile(io.BytesIO(content))
    sessions = np.empty(expected_row_count, dtype=np.int32)
    security_ids = np.empty((expected_row_count, 16), dtype=np.uint8)
    scores = np.empty(expected_row_count, dtype=np.int64)
    offset = 0
    observed_dates: set[date] = set()
    for batch in parquet.iter_batches(
        batch_size=100_000,
        columns=("session_date", "asset_id", "feature_value"),
    ):
        values = batch.to_pydict()
        dates = cast(list[date], values["session_date"])
        ids = cast(list[str], values["asset_id"])
        score_values = cast(list[str], values["feature_value"])
        count = len(dates)
        end = offset + count
        if end > expected_row_count or len(ids) != count or len(score_values) != count:
            raise TrainableAggregationError("Cached OOF payload row shape is invalid")
        sessions[offset:end] = np.fromiter(
            (item.toordinal() for item in dates), dtype=np.int32, count=count
        )
        security_ids[offset:end, :] = np.frombuffer(
            b"".join(uuid.UUID(item).bytes for item in ids), dtype=np.uint8
        ).reshape(count, 16)
        scores[offset:end] = np.fromiter(
            (int(Decimal(item).scaleb(18)) for item in score_values),
            dtype=np.int64,
            count=count,
        )
        observed_dates.update(dates)
        offset = end
    if (
        offset != expected_row_count
        or len(observed_dates) != expected_group_count
        or not observed_dates
        or min(observed_dates) != expected_start
        or max(observed_dates) != expected_end
    ):
        raise TrainableAggregationError("Cached OOF payload coverage is invalid")
    return CompactTrainableMember(
        target_key=target_key,
        training_preset_key=training_preset_key,
        prediction_fingerprint=prediction_fingerprint,
        fold_count=fold_count,
        session_ordinals=sessions,
        security_id_bytes=security_ids,
        centered_scores=scores,
        target_values=np.zeros(expected_row_count, dtype=np.int64),
        target_available=np.zeros(expected_row_count, dtype=np.bool_),
    )


def _fixed_target(target_key: str, semantics: Mapping[str, object]) -> FixedSessionTarget:
    horizon = semantics.get("horizon_sessions")
    if isinstance(horizon, bool) or not isinstance(horizon, int):
        raise TrainableAggregationError("Target lacks its fixed-session horizon")
    target = FixedSessionTarget(target_key, horizon)
    expected = {
        "horizon_sessions": horizon,
        "observation_grid": "xnys_completed_session_daily",
        "entry_rule": "next_common_session_open_after_decision_close",
        "exit_rule": "open_after_exact_complete_session_intervals",
        "label_transform": "average_rank_centered_minus_one_to_one",
    }
    if any(semantics.get(key) != value for key, value in expected.items()):
        raise TrainableAggregationError(
            "Target semantics differ from the fixed-session rank contract"
        )
    return target


def _publication_bindings(
    engine: Engine,
    context: _AggregationWorkContext,
) -> tuple[dict[str, TrainablePayloadBinding], uuid.UUID, uuid.UUID]:
    contract_ports = {
        "training_matrix_numeric": "training_matrix",
        "fitted_regression_model": "fitted_model",
        "oof_regression_prediction": "oof_prediction",
    }
    with engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    """
                SELECT component.component_key,
                       contract.payload_contract_version_id
                  FROM workspace.v022_catalog_release_component component
                  JOIN data.payload_contract_version contract
                    ON contract.artifact_id=component.component_artifact_id
                 WHERE component.catalog_release_id=:release
                   AND component.component_kind='payload_contract_version'
                   AND component.component_key IN :keys
                   AND component.component_version=1
                """
                ).bindparams(bindparam("keys", expanding=True)),
                {"release": context.catalog_release_id, "keys": tuple(contract_ports)},
            )
            .mappings()
            .all()
        )
        if {row["component_key"] for row in rows} != set(contract_ports):
            raise TrainableAggregationError(
                "Catalog Release lacks exact trainable Payload Contracts"
            )
        encoding_count = connection.scalar(
            text(
                """
                SELECT count(*)
                  FROM workspace.v022_catalog_release_component component
                  JOIN data.physical_encoding_version encoding
                    ON encoding.artifact_id=component.component_artifact_id
                 WHERE component.catalog_release_id=:release
                   AND component.component_kind='physical_encoding_version'
                   AND component.component_key='canonical_parquet'
                   AND component.component_version=1
                   AND encoding.physical_encoding_version_id=:encoding
                """
            ),
            {
                "release": context.catalog_release_id,
                "encoding": context.physical_encoding_version_id,
            },
        )
        if encoding_count != 1:
            raise TrainableAggregationError(
                "Catalog Release lacks the exact canonical Parquet encoding"
            )
        cohort = (
            connection.execute(
                text(
                    """
                SELECT cohort.evaluation_cohort_version_id,cohort.artifact_id
                  FROM experiment.v022_research_suite_evaluation_cohort_binding binding
                  JOIN experiment.v022_evaluation_cohort_version cohort
                    ON cohort.evaluation_cohort_version_id=
                       binding.evaluation_cohort_version_id
                  JOIN lineage.artifact artifact ON artifact.artifact_id=cohort.artifact_id
                 WHERE binding.research_suite_id=:suite
                   AND artifact.status='published'
                """
                ),
                {"suite": context.research_suite_id},
            )
            .mappings()
            .one_or_none()
        )
        if cohort is None:
            raise TrainableAggregationError(
                "Supervised publication requires its exact published Evaluation Cohort"
            )
    bindings = {
        cast(str, row["component_key"]): TrainablePayloadBinding(
            payload_contract_version_id=row["payload_contract_version_id"],
            physical_encoding_version_id=context.physical_encoding_version_id,
            output_port_key=contract_ports[row["component_key"]],
        )
        for row in rows
    }
    return bindings, cohort["artifact_id"], cohort["evaluation_cohort_version_id"]


def _candidate_mask(
    connection: Connection,
    *,
    runtime_contract_id: uuid.UUID,
    sessions: tuple[date, ...],
    evaluation_start: date,
    allowed_security_ids: frozenset[uuid.UUID],
) -> dict[date, frozenset[uuid.UUID]]:
    rows = (
        connection.execute(
            text(
                """
            SELECT security_id,effective_start,effective_end,
                   is_member,is_selectable,is_tradable,valuation_state
              FROM experiment.v022_cohort_runtime_mask_interval
             WHERE evaluation_cohort_runtime_contract_id=:contract
             ORDER BY security_id,effective_start
            """
            ),
            {"contract": runtime_contract_id},
        )
        .mappings()
        .all()
    )
    result: dict[date, frozenset[uuid.UUID]] = {}
    for session in sessions:
        selected = frozenset(
            cast(uuid.UUID, row["security_id"])
            for row in rows
            if row["security_id"] in allowed_security_ids
            and row["effective_start"] <= session <= row["effective_end"]
            and row["is_member"] is True
            and row["is_tradable"] is True
            and row["valuation_state"] == "live"
            and (session < evaluation_start or row["is_selectable"] is True)
        )
        if selected:
            result[session] = selected
    return result


def _adjusted_opens(
    connection: Connection,
    *,
    dataset_publication_id: uuid.UUID,
    security_keys: Mapping[uuid.UUID, str],
    session_rows: tuple[RowMapping, ...],
) -> tuple[AdjustedOpenPoint, ...]:
    security_ids = tuple(security_keys)
    security_rows = (
        connection.execute(
            text(
                """
            SELECT security_id,security_key,legacy_asset_id
              FROM catalog.security
             WHERE security_id IN :security_ids
             ORDER BY security_id
            """
            ).bindparams(bindparam("security_ids", expanding=True)),
            {"security_ids": security_ids},
        )
        .mappings()
        .all()
    )
    if len(security_rows) != len(security_ids) or any(
        row["legacy_asset_id"] is None for row in security_rows
    ):
        raise TrainableAggregationError(
            "Every trainable Security requires its canonical market Asset identity"
        )
    security_by_canonical_asset = {
        cast(uuid.UUID, row["legacy_asset_id"]): (
            cast(uuid.UUID, row["security_id"]),
            cast(str, row["security_key"]),
        )
        for row in security_rows
    }
    session_dates = tuple(cast(date, row["session_date"]) for row in session_rows)
    market_rows = (
        connection.execute(
            text(
                """
            SELECT asset_id,session_date,open_adj
              FROM data.daily_bar
             WHERE dataset_publication_id=:dataset
               AND asset_id IN :asset_ids
               AND session_date BETWEEN :start AND :end
             ORDER BY session_date,asset_id
            """
            ).bindparams(bindparam("asset_ids", expanding=True)),
            {
                "dataset": dataset_publication_id,
                "asset_ids": tuple(security_by_canonical_asset),
                "start": session_dates[0],
                "end": session_dates[-1],
            },
        )
        .mappings()
        .all()
    )
    open_at_by_date = {
        cast(date, row["session_date"]): cast(datetime, row["open_at_utc"]) for row in session_rows
    }
    points = tuple(
        AdjustedOpenPoint(
            security_id=security_by_canonical_asset[row["asset_id"]][0],
            security_key=security_by_canonical_asset[row["asset_id"]][1],
            session_date=row["session_date"],
            adjusted_open=Decimal(row["open_adj"]),
            known_at=open_at_by_date[row["session_date"]],
        )
        for row in market_rows
    )
    return points


def _first_complete_training_session(
    *,
    sessions: tuple[date, ...],
    evaluation_start: date,
    candidate_security_ids_by_date: Mapping[date, frozenset[uuid.UUID]],
) -> date:
    for session in sessions:
        if session >= evaluation_start:
            break
        candidates = candidate_security_ids_by_date.get(session, frozenset())
        if len(candidates) >= 2:
            return session
    raise TrainableAggregationError(
        "Feature history cannot form a complete pre-evaluation training panel"
    )


def _feature_complete_candidate_mask(
    *,
    feature_inputs: tuple[TrainableFeatureInput, ...],
    candidate_security_ids_by_date: Mapping[date, frozenset[uuid.UUID]],
    allowed_security_ids: frozenset[uuid.UUID],
) -> dict[date, frozenset[uuid.UUID]]:
    """Intersect the frozen eligibility mask with the exact Feature panel."""

    if not feature_inputs:
        raise TrainableAggregationError("Trainable execution requires Feature inputs")
    result = {
        decision_date: frozenset(
            security_id for security_id in candidates if security_id in allowed_security_ids
        )
        for decision_date, candidates in candidate_security_ids_by_date.items()
    }
    result = {day: candidates for day, candidates in result.items() if candidates}
    for input_ in feature_inputs:
        complete_by_date: dict[date, set[uuid.UUID]] = {}
        for point in input_.points:
            if point is None:
                raise TrainableAggregationError(
                    "Trainable Feature source was consumed before mask resolution"
                )
            candidates = result.get(point.decision_date)
            if (
                candidates is not None
                and point.asset_id in candidates
                and point.signal_value is not None
                and point.missing_reason is None
            ):
                complete_by_date.setdefault(point.decision_date, set()).add(point.asset_id)
        result = {
            decision_date: frozenset(
                security_id
                for security_id in candidates
                if security_id in complete_by_date.get(decision_date, set())
            )
            for decision_date, candidates in result.items()
        }
        result = {day: candidates for day, candidates in result.items() if candidates}
    return result
