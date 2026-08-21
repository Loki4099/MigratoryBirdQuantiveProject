from __future__ import annotations

import hashlib
import io
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import (
    ArtifactService,
    DependencyInput,
    PublicationResult,
)
from style_rotation.v022.linear_trainable_aggregation import (
    FittedFoldState,
    StrictOofResult,
)
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore
from style_rotation.v022.trainable_aggregation import (
    TrainingMatrix,
    WalkForwardFold,
    WalkForwardPolicy,
)


class TrainablePublicationError(RuntimeError):
    """Raised when immutable trainable runtime evidence cannot be published."""


@dataclass(frozen=True, slots=True)
class TrainablePayloadBinding:
    payload_contract_version_id: uuid.UUID
    physical_encoding_version_id: uuid.UUID
    output_port_key: str

    def __post_init__(self) -> None:
        if not self.output_port_key.strip():
            raise ValueError("Trainable output port key must be nonempty")


@dataclass(frozen=True, slots=True)
class PublishedTrainablePayload:
    projection_id: uuid.UUID
    artifact_id: uuid.UUID
    artifact_semantic_fingerprint: str
    payload_manifest_id: uuid.UUID
    manifest_artifact_id: uuid.UUID
    manifest_hash: str
    reused: bool


@dataclass(frozen=True, slots=True)
class PublishedTrainableIdentity:
    projection_id: uuid.UUID
    artifact_id: uuid.UUID
    intrinsic_fingerprint: str
    artifact_semantic_fingerprint: str
    reused: bool


def publish_walk_forward_policy(
    engine: Engine,
    *,
    policy: WalkForwardPolicy,
    dependencies: tuple[DependencyInput, ...] = (),
) -> PublishedTrainableIdentity:
    document = _policy_document(policy)
    projection_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"bird-v022:fold-policy:{policy.fingerprint}"
    )

    def insert(
        connection: Connection, publication: PublicationResult
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO aggregation.v022_fold_policy_version (
                  fold_policy_version_id,artifact_id,policy_key,version_number,
                  policy_document,policy_fingerprint,artifact_semantic_fingerprint
                ) VALUES (
                  :id,:artifact,:key,:version,CAST(:document AS jsonb),
                  :fingerprint,:semantic
                ) ON CONFLICT (policy_fingerprint) DO NOTHING
                """
            ),
            {
                "id": projection_id,
                "artifact": publication.artifact_id,
                "key": policy.policy_key,
                "version": policy.version_number,
                "document": _json(document),
                "fingerprint": policy.fingerprint,
                "semantic": publication.semantic_fingerprint,
            },
        )

    return _publish_nonpayload_identity(
        engine,
        artifact_type="v022_fold_policy_version",
        intrinsic_fingerprint=policy.fingerprint,
        projection_id=projection_id,
        semantic_payload=document,
        dependencies=dependencies,
        insert_projection=insert,
    )


def publish_training_folds(
    engine: Engine,
    *,
    matrix: TrainingMatrix,
    training_matrix_id: uuid.UUID,
    training_matrix_artifact_id: uuid.UUID,
    policy: WalkForwardPolicy,
    policy_publication: PublishedTrainableIdentity,
    folds: Sequence[WalkForwardFold],
) -> tuple[PublishedTrainableIdentity, ...]:
    if policy_publication.intrinsic_fingerprint != policy.fingerprint:
        raise TrainablePublicationError("Fold Policy publication does not match the policy")
    ordered = tuple(sorted(folds, key=lambda item: item.ordinal))
    if tuple(item.ordinal for item in ordered) != tuple(range(len(ordered))):
        raise TrainablePublicationError("Training Fold ordinals must be contiguous")
    if not ordered:
        raise TrainablePublicationError("Training Fold publication cannot be empty")
    publications: list[PublishedTrainableIdentity] = []
    for fold in ordered:
        expected_fingerprint = sha256_hexdigest(
            {
                "matrix_fingerprint": matrix.fingerprint,
                "policy_fingerprint": policy.fingerprint,
                "ordinal": fold.ordinal,
                "train_dates": fold.train_dates,
                "validation_dates": fold.validation_dates,
                "prediction_dates": fold.prediction_dates,
            }
        )
        if fold.fold_fingerprint != expected_fingerprint:
            raise TrainablePublicationError(
                "Training Fold fingerprint does not match the Matrix and Policy"
            )
        document = _fold_document(
            fold,
            training_matrix_id=training_matrix_id,
            matrix_fingerprint=matrix.fingerprint,
            policy_fingerprint=policy.fingerprint,
        )
        projection_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird-v022:training-fold:{fold.fold_fingerprint}"
        )

        def insert(
            connection: Connection,
            publication: PublicationResult,
            *,
            current: WalkForwardFold = fold,
            current_document: Mapping[str, object] = document,
            current_projection_id: uuid.UUID = projection_id,
        ) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO aggregation.v022_training_fold (
                      training_fold_id,artifact_id,training_matrix_id,
                      fold_policy_version_id,ordinal,train_range,validation_range,
                      prediction_range,train_group_count,validation_group_count,
                      prediction_group_count,fold_document,fold_fingerprint,
                      artifact_semantic_fingerprint
                    ) VALUES (
                      :id,:artifact,:matrix,:policy,:ordinal,
                      daterange(:train_start,:train_end,'[)'),
                      daterange(:validation_start,:validation_end,'[)'),
                      daterange(:prediction_start,:prediction_end,'[)'),
                      :train_count,:validation_count,:prediction_count,
                      CAST(:document AS jsonb),:fingerprint,:semantic
                    ) ON CONFLICT (fold_fingerprint) DO NOTHING
                    """
                ),
                {
                    "id": current_projection_id,
                    "artifact": publication.artifact_id,
                    "matrix": training_matrix_id,
                    "policy": policy_publication.projection_id,
                    "ordinal": current.ordinal,
                    "train_start": current.train_dates[0],
                    "train_end": current.train_dates[-1] + timedelta(days=1),
                    "validation_start": current.validation_dates[0],
                    "validation_end": current.validation_dates[-1] + timedelta(days=1),
                    "prediction_start": current.prediction_dates[0],
                    "prediction_end": current.prediction_dates[-1] + timedelta(days=1),
                    "train_count": len(current.train_dates),
                    "validation_count": len(current.validation_dates),
                    "prediction_count": len(current.prediction_dates),
                    "document": _json(current_document),
                    "fingerprint": current.fold_fingerprint,
                    "semantic": publication.semantic_fingerprint,
                },
            )

        publications.append(
            _publish_nonpayload_identity(
                engine,
                artifact_type="v022_training_fold",
                intrinsic_fingerprint=fold.fold_fingerprint,
                projection_id=projection_id,
                semantic_payload=document,
                dependencies=(
                    DependencyInput(training_matrix_artifact_id, "training_matrix", 0),
                    DependencyInput(policy_publication.artifact_id, "fold_policy", 1),
                ),
                insert_projection=insert,
            )
        )
    return tuple(publications)


def publish_base_learner_spec(
    engine: Engine,
    *,
    aggregation_version_id: uuid.UUID,
    feature_schema_version_id: uuid.UUID,
    target_version_id: uuid.UUID,
    training_preset_version_id: uuid.UUID,
    fold_policy_version_id: uuid.UUID,
    adapter_key: str,
    adapter_version: str,
    hyperparameters: Mapping[str, object],
    random_seed: int,
    dependencies: tuple[DependencyInput, ...],
) -> PublishedTrainableIdentity:
    if not adapter_key.strip() or not adapter_version.strip():
        raise ValueError("Base Learner adapter identity must be nonempty")
    if not -(2**63) <= random_seed < 2**63:
        raise ValueError("Base Learner random seed must fit PostgreSQL bigint")
    document = {
        "aggregation_version_id": aggregation_version_id,
        "feature_schema_version_id": feature_schema_version_id,
        "target_version_id": target_version_id,
        "training_preset_version_id": training_preset_version_id,
        "fold_policy_version_id": fold_policy_version_id,
        "adapter_key": adapter_key,
        "adapter_version": adapter_version,
        "hyperparameters": dict(hyperparameters),
        "random_seed": random_seed,
    }
    fingerprint = sha256_hexdigest(document)
    projection_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"bird-v022:base-learner-spec:{fingerprint}"
    )

    def insert(
        connection: Connection, publication: PublicationResult
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO aggregation.v022_base_learner_spec (
                  base_learner_spec_id,artifact_id,aggregation_version_id,
                  feature_schema_version_id,target_version_id,
                  training_preset_version_id,fold_policy_version_id,adapter_key,
                  adapter_version,hyperparameter_document,random_seed,
                  spec_fingerprint,artifact_semantic_fingerprint
                ) VALUES (
                  :id,:artifact,:aggregation,:schema,:target,:preset,:policy,
                  :adapter,:adapter_version,CAST(:hyperparameters AS jsonb),:seed,
                  :fingerprint,:semantic
                ) ON CONFLICT (spec_fingerprint) DO NOTHING
                """
            ),
            {
                "id": projection_id,
                "artifact": publication.artifact_id,
                "aggregation": aggregation_version_id,
                "schema": feature_schema_version_id,
                "target": target_version_id,
                "preset": training_preset_version_id,
                "policy": fold_policy_version_id,
                "adapter": adapter_key,
                "adapter_version": adapter_version,
                "hyperparameters": _json(hyperparameters),
                "seed": random_seed,
                "fingerprint": fingerprint,
                "semantic": publication.semantic_fingerprint,
            },
        )

    return _publish_nonpayload_identity(
        engine,
        artifact_type="v022_base_learner_spec",
        intrinsic_fingerprint=fingerprint,
        projection_id=projection_id,
        semantic_payload=document,
        dependencies=dependencies,
        insert_projection=insert,
    )


@dataclass(frozen=True, slots=True)
class OofFoldPublicationLink:
    training_fold_id: uuid.UUID
    fitted_model_state_id: uuid.UUID
    ordinal: int


@dataclass(frozen=True, slots=True)
class _EncodedPayload:
    content: bytes
    row_count: int
    group_count: int
    coverage_start: date
    coverage_end: date
    statistics: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _PreparedPayload:
    content_hash: str
    storage_uri: str
    byte_size: int
    payload_object_id: uuid.UUID
    payload_partition_id: uuid.UUID
    partition_descriptor_hash: str
    payload_manifest_id: uuid.UUID
    logical_payload_fingerprint: str
    manifest_hash: str
    coverage_document: Mapping[str, object]


ProjectionWriter = Callable[
    [Connection, uuid.UUID, PublicationResult, _PreparedPayload], None
]
IdentityProjectionWriter = Callable[[Connection, PublicationResult], None]


def _publish_nonpayload_identity(
    engine: Engine,
    *,
    artifact_type: str,
    intrinsic_fingerprint: str,
    projection_id: uuid.UUID,
    semantic_payload: Mapping[str, object],
    dependencies: tuple[DependencyInput, ...],
    insert_projection: IdentityProjectionWriter,
) -> PublishedTrainableIdentity:
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"{artifact_type}:{intrinsic_fingerprint}"},
        )
        publication = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
            artifact_type=artifact_type,
            artifact_key=intrinsic_fingerprint,
            version_number=1,
            semantic_payload=dict(semantic_payload),
            content_payload=dict(semantic_payload),
            dependencies=dependencies,
            reason=f"publish immutable {artifact_type}",
        )
        insert_projection(connection, publication)
        _verify_nonpayload_projection(
            connection,
            artifact_type=artifact_type,
            intrinsic_fingerprint=intrinsic_fingerprint,
            projection_id=projection_id,
            artifact_id=publication.artifact_id,
            artifact_semantic_fingerprint=publication.semantic_fingerprint,
        )
    return PublishedTrainableIdentity(
        projection_id=projection_id,
        artifact_id=publication.artifact_id,
        intrinsic_fingerprint=intrinsic_fingerprint,
        artifact_semantic_fingerprint=publication.semantic_fingerprint,
        reused=publication.reused,
    )


def _verify_nonpayload_projection(
    connection: Connection,
    *,
    artifact_type: str,
    intrinsic_fingerprint: str,
    projection_id: uuid.UUID,
    artifact_id: uuid.UUID,
    artifact_semantic_fingerprint: str,
) -> None:
    identity = {
        "v022_fold_policy_version": (
            "aggregation.v022_fold_policy_version",
            "fold_policy_version_id",
            "policy_fingerprint",
        ),
        "v022_training_fold": (
            "aggregation.v022_training_fold",
            "training_fold_id",
            "fold_fingerprint",
        ),
        "v022_base_learner_spec": (
            "aggregation.v022_base_learner_spec",
            "base_learner_spec_id",
            "spec_fingerprint",
        ),
    }.get(artifact_type)
    if identity is None:
        raise AssertionError("Trainable non-Payload projection type is not allowlisted")
    table, id_column, fingerprint_column = identity
    row = (
        connection.execute(
            text(
                f"SELECT {id_column} AS projection_id,artifact_id,"
                f"artifact_semantic_fingerprint FROM {table} "
                f"WHERE {fingerprint_column}=:fingerprint"
            ),
            {"fingerprint": intrinsic_fingerprint},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or not (
        row["projection_id"] == projection_id
        and row["artifact_id"] == artifact_id
        and row["artifact_semantic_fingerprint"] == artifact_semantic_fingerprint
    ):
        raise TrainablePublicationError("Trainable identity projection conflicts")


def _policy_document(policy: WalkForwardPolicy) -> dict[str, object]:
    return {
        "policy_key": policy.policy_key,
        "version_number": policy.version_number,
        "mode": "expanding_walk_forward",
        "minimum_train_groups": policy.minimum_train_groups,
        "validation_groups": policy.validation_groups,
        "prediction_groups": policy.prediction_groups,
        "embargo_groups": policy.embargo_groups,
        "purge_policy": "target_known_at_before_next_phase_cutoff",
        "random_split": False,
    }


def _fold_document(
    fold: WalkForwardFold,
    *,
    training_matrix_id: uuid.UUID,
    matrix_fingerprint: str,
    policy_fingerprint: str,
) -> dict[str, object]:
    if not fold.train_dates or not fold.validation_dates or not fold.prediction_dates:
        raise TrainablePublicationError("Training Fold phases cannot be empty")
    if not (
        max(fold.train_dates) < min(fold.validation_dates)
        and max(fold.validation_dates) < min(fold.prediction_dates)
    ):
        raise TrainablePublicationError("Training Fold phases must be strictly ordered")
    return {
        "training_matrix_id": training_matrix_id,
        "matrix_fingerprint": matrix_fingerprint,
        "policy_fingerprint": policy_fingerprint,
        "ordinal": fold.ordinal,
        "train_dates": [item.isoformat() for item in fold.train_dates],
        "validation_dates": [item.isoformat() for item in fold.validation_dates],
        "prediction_dates": [item.isoformat() for item in fold.prediction_dates],
        "fold_fingerprint": fold.fold_fingerprint,
    }


def publish_training_matrix(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    matrix: TrainingMatrix,
    feature_schema_version_id: uuid.UUID,
    target_version_id: uuid.UUID,
    evaluation_cohort_version_id: uuid.UUID,
    binding: TrainablePayloadBinding,
    dependencies: tuple[DependencyInput, ...],
) -> PublishedTrainablePayload:
    encoded = _encode_training_matrix(matrix)
    projection_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"bird-v022:training-matrix:{matrix.fingerprint}"
    )
    semantic = {
        "matrix_fingerprint": matrix.fingerprint,
        "feature_schema_fingerprint": matrix.feature_schema.fingerprint,
        "target_fingerprint": matrix.target.fingerprint,
        "feature_schema_version_id": feature_schema_version_id,
        "target_version_id": target_version_id,
        "evaluation_cohort_version_id": evaluation_cohort_version_id,
        "observation_grid": "xnys_completed_session_daily",
        "coverage_start": encoded.coverage_start,
        "coverage_end": encoded.coverage_end,
    }

    def write_projection(
        connection: Connection,
        manifest_artifact_id: uuid.UUID,
        publication: PublicationResult,
        prepared: _PreparedPayload,
    ) -> None:
        del manifest_artifact_id
        connection.execute(
            text(
                """
                INSERT INTO aggregation.v022_training_matrix (
                  training_matrix_id,artifact_id,feature_schema_version_id,
                  target_version_id,evaluation_cohort_version_id,payload_manifest_id,
                  observation_grid,coverage_start,coverage_end,row_count,group_count,
                  matrix_fingerprint,artifact_semantic_fingerprint
                ) VALUES (
                  :id,:artifact,:schema,:target,:cohort,:manifest,
                  'xnys_completed_session_daily',:start,:end,:rows,:groups,
                  :fingerprint,:semantic
                ) ON CONFLICT (matrix_fingerprint) DO NOTHING
                """
            ),
            {
                "id": projection_id,
                "artifact": publication.artifact_id,
                "schema": feature_schema_version_id,
                "target": target_version_id,
                "cohort": evaluation_cohort_version_id,
                "manifest": prepared.payload_manifest_id,
                "start": encoded.coverage_start,
                "end": encoded.coverage_end,
                "rows": encoded.row_count,
                "groups": encoded.group_count,
                "fingerprint": matrix.fingerprint,
                "semantic": publication.semantic_fingerprint,
            },
        )

    return _publish_payload_projection(
        engine,
        object_store=object_store,
        artifact_type="v022_training_matrix",
        intrinsic_fingerprint=matrix.fingerprint,
        projection_id=projection_id,
        binding=binding,
        encoded=encoded,
        semantic_payload=semantic,
        dependencies=dependencies,
        projection_writer=write_projection,
    )


def publish_fitted_model_state(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    fitted: FittedFoldState,
    base_learner_spec_id: uuid.UUID,
    training_fold_id: uuid.UUID,
    trained_through: date,
    labels_known_through: datetime,
    environment_fingerprint: str,
    binding: TrainablePayloadBinding,
    dependencies: tuple[DependencyInput, ...],
) -> PublishedTrainablePayload:
    if labels_known_through.tzinfo is None or labels_known_through.utcoffset() is None:
        raise ValueError("labels_known_through must be timezone-aware")
    if len(environment_fingerprint) != 64:
        raise ValueError("environment_fingerprint must be a SHA-256 hex digest")
    encoded = _encode_model_state(fitted, trained_through, labels_known_through)
    intrinsic = sha256_hexdigest(
        {
            "base_learner_spec_id": base_learner_spec_id,
            "training_fold_id": training_fold_id,
            "fold_fingerprint": fitted.fold_fingerprint,
            "model_fingerprint": fitted.model.model_fingerprint,
            "trained_through": trained_through,
            "labels_known_through": labels_known_through,
            "environment_fingerprint": environment_fingerprint,
        }
    )
    projection_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"bird-v022:fitted-model-state:{intrinsic}"
    )

    def write_projection(
        connection: Connection,
        manifest_artifact_id: uuid.UUID,
        publication: PublicationResult,
        prepared: _PreparedPayload,
    ) -> None:
        del manifest_artifact_id
        connection.execute(
            text(
                """
                INSERT INTO aggregation.v022_fitted_model_state (
                  fitted_model_state_id,artifact_id,base_learner_spec_id,
                  training_fold_id,model_payload_manifest_id,trained_through,
                  labels_known_through,environment_fingerprint,state_fingerprint,
                  artifact_semantic_fingerprint
                ) VALUES (
                  :id,:artifact,:spec,:fold,:manifest,:trained,:known,:environment,
                  :fingerprint,:semantic
                ) ON CONFLICT (state_fingerprint) DO NOTHING
                """
            ),
            {
                "id": projection_id,
                "artifact": publication.artifact_id,
                "spec": base_learner_spec_id,
                "fold": training_fold_id,
                "manifest": prepared.payload_manifest_id,
                "trained": trained_through,
                "known": labels_known_through,
                "environment": environment_fingerprint,
                "fingerprint": intrinsic,
                "semantic": publication.semantic_fingerprint,
            },
        )

    return _publish_payload_projection(
        engine,
        object_store=object_store,
        artifact_type="v022_fitted_model_state",
        intrinsic_fingerprint=intrinsic,
        projection_id=projection_id,
        binding=binding,
        encoded=encoded,
        semantic_payload={
            "state_fingerprint": intrinsic,
            "base_learner_spec_id": base_learner_spec_id,
            "training_fold_id": training_fold_id,
            "model_fingerprint": fitted.model.model_fingerprint,
            "trained_through": trained_through,
            "labels_known_through": labels_known_through,
            "environment_fingerprint": environment_fingerprint,
        },
        dependencies=dependencies,
        projection_writer=write_projection,
    )


def publish_oof_prediction(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    result: StrictOofResult,
    base_learner_spec_id: uuid.UUID,
    decision_known_at: Mapping[date, datetime],
    fold_links: Sequence[OofFoldPublicationLink],
    binding: TrainablePayloadBinding,
    dependencies: tuple[DependencyInput, ...],
) -> PublishedTrainablePayload:
    encoded = _encode_oof_prediction(result, decision_known_at)
    intrinsic = result.fingerprint
    projection_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"bird-v022:oof-prediction:{intrinsic}"
    )
    ordered_links = tuple(sorted(fold_links, key=lambda item: item.ordinal))
    if tuple(item.ordinal for item in ordered_links) != tuple(range(len(ordered_links))):
        raise TrainablePublicationError("OOF publication Fold ordinals must be contiguous")
    if len(ordered_links) != len(result.fitted_folds):
        raise TrainablePublicationError("OOF publication requires one link per fitted Fold")

    def write_projection(
        connection: Connection,
        manifest_artifact_id: uuid.UUID,
        publication: PublicationResult,
        prepared: _PreparedPayload,
    ) -> None:
        del manifest_artifact_id
        connection.execute(
            text(
                """
                INSERT INTO aggregation.v022_oof_prediction (
                  oof_prediction_id,artifact_id,base_learner_spec_id,
                  prediction_payload_manifest_id,coverage_start,coverage_end,
                  row_count,group_count,prediction_fingerprint,
                  artifact_semantic_fingerprint
                ) VALUES (
                  :id,:artifact,:spec,:manifest,:start,:end,:rows,:groups,
                  :fingerprint,:semantic
                ) ON CONFLICT (prediction_fingerprint) DO NOTHING
                """
            ),
            {
                "id": projection_id,
                "artifact": publication.artifact_id,
                "spec": base_learner_spec_id,
                "manifest": prepared.payload_manifest_id,
                "start": encoded.coverage_start,
                "end": encoded.coverage_end,
                "rows": encoded.row_count,
                "groups": encoded.group_count,
                "fingerprint": intrinsic,
                "semantic": publication.semantic_fingerprint,
            },
        )
        for link in ordered_links:
            connection.execute(
                text(
                    """
                    INSERT INTO aggregation.v022_oof_prediction_fold (
                      oof_prediction_id,training_fold_id,fitted_model_state_id,ordinal
                    ) VALUES (:prediction,:fold,:state,:ordinal)
                    ON CONFLICT (oof_prediction_id,training_fold_id) DO NOTHING
                    """
                ),
                {
                    "prediction": projection_id,
                    "fold": link.training_fold_id,
                    "state": link.fitted_model_state_id,
                    "ordinal": link.ordinal,
                },
            )
        observed_links = tuple(
            connection.execute(
                text(
                    "SELECT training_fold_id,fitted_model_state_id,ordinal "
                    "FROM aggregation.v022_oof_prediction_fold "
                    "WHERE oof_prediction_id=:prediction ORDER BY ordinal"
                ),
                {"prediction": projection_id},
            ).all()
        )
        expected_links = tuple(
            (link.training_fold_id, link.fitted_model_state_id, link.ordinal)
            for link in ordered_links
        )
        if observed_links != expected_links:
            raise TrainablePublicationError("OOF Fold publication links conflict")

    return _publish_payload_projection(
        engine,
        object_store=object_store,
        artifact_type="v022_oof_prediction",
        intrinsic_fingerprint=intrinsic,
        projection_id=projection_id,
        binding=binding,
        encoded=encoded,
        semantic_payload={
            "prediction_fingerprint": intrinsic,
            "base_learner_spec_id": base_learner_spec_id,
            "adapter_key": result.adapter_key,
            "adapter_version": result.adapter_version,
            "matrix_fingerprint": result.matrix_fingerprint,
            "ordered_fold_fingerprints": tuple(
                item.fold_fingerprint for item in result.fitted_folds
            ),
        },
        dependencies=dependencies,
        projection_writer=write_projection,
    )


def _publish_payload_projection(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    artifact_type: str,
    intrinsic_fingerprint: str,
    projection_id: uuid.UUID,
    binding: TrainablePayloadBinding,
    encoded: _EncodedPayload,
    semantic_payload: Mapping[str, object],
    dependencies: tuple[DependencyInput, ...],
    projection_writer: ProjectionWriter,
) -> PublishedTrainablePayload:
    prepared = _prepare_payload(
        object_store,
        artifact_type=artifact_type,
        intrinsic_fingerprint=intrinsic_fingerprint,
        binding=binding,
        encoded=encoded,
    )
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"{artifact_type}:{intrinsic_fingerprint}"},
        )
        bound = _BoundConnection(connection)
        publication = ArtifactService(cast(Engine, bound)).publish(
            artifact_type=artifact_type,
            artifact_key=intrinsic_fingerprint,
            version_number=1,
            semantic_payload=dict(semantic_payload),
            content_payload={
                **dict(semantic_payload),
                "object_content_hash": prepared.content_hash,
                "manifest_hash": prepared.manifest_hash,
            },
            dependencies=dependencies,
            reason=f"publish immutable {artifact_type}",
        )
        manifest_publication = ArtifactService(cast(Engine, bound)).publish(
            artifact_type="v022_payload_manifest",
            artifact_key=f"v022_payload_manifest__{prepared.manifest_hash}",
            version_number=1,
            semantic_payload={
                "manifest_hash": prepared.manifest_hash,
                "logical_payload_fingerprint": prepared.logical_payload_fingerprint,
                "producer_artifact_id": publication.artifact_id,
                "output_port_key": binding.output_port_key,
            },
            content_payload={
                "partition_descriptor_hash": prepared.partition_descriptor_hash,
                "object_content_hash": prepared.content_hash,
                "coverage_document": prepared.coverage_document,
                "row_count": encoded.row_count,
            },
            dependencies=(DependencyInput(publication.artifact_id, "producer", 0),),
            reason=f"publish {artifact_type} Payload Manifest",
            draft_writer=lambda draft_connection, manifest_artifact_id: _write_manifest(
                draft_connection,
                manifest_artifact_id,
                producer_artifact_id=publication.artifact_id,
                binding=binding,
                encoded=encoded,
                prepared=prepared,
            ),
        )
        projection_writer(
            connection, manifest_publication.artifact_id, publication, prepared
        )
        _verify_projection(
            connection,
            artifact_type=artifact_type,
            intrinsic_fingerprint=intrinsic_fingerprint,
            projection_id=projection_id,
            artifact_id=publication.artifact_id,
            artifact_semantic_fingerprint=publication.semantic_fingerprint,
            payload_manifest_id=prepared.payload_manifest_id,
        )
    return PublishedTrainablePayload(
        projection_id=projection_id,
        artifact_id=publication.artifact_id,
        artifact_semantic_fingerprint=publication.semantic_fingerprint,
        payload_manifest_id=prepared.payload_manifest_id,
        manifest_artifact_id=manifest_publication.artifact_id,
        manifest_hash=prepared.manifest_hash,
        reused=publication.reused or manifest_publication.reused,
    )


def _verify_projection(
    connection: Connection,
    *,
    artifact_type: str,
    intrinsic_fingerprint: str,
    projection_id: uuid.UUID,
    artifact_id: uuid.UUID,
    artifact_semantic_fingerprint: str,
    payload_manifest_id: uuid.UUID,
) -> None:
    identity = {
        "v022_training_matrix": (
            "aggregation.v022_training_matrix",
            "training_matrix_id",
            "matrix_fingerprint",
            "payload_manifest_id",
        ),
        "v022_fitted_model_state": (
            "aggregation.v022_fitted_model_state",
            "fitted_model_state_id",
            "state_fingerprint",
            "model_payload_manifest_id",
        ),
        "v022_oof_prediction": (
            "aggregation.v022_oof_prediction",
            "oof_prediction_id",
            "prediction_fingerprint",
            "prediction_payload_manifest_id",
        ),
    }.get(artifact_type)
    if identity is None:
        raise AssertionError("Trainable projection type is not allowlisted")
    table, id_column, fingerprint_column, manifest_column = identity
    row = (
        connection.execute(
            text(
                f"SELECT {id_column} AS projection_id,artifact_id,"
                f"artifact_semantic_fingerprint,{manifest_column} AS manifest_id "
                f"FROM {table} WHERE {fingerprint_column}=:fingerprint"
            ),
            {"fingerprint": intrinsic_fingerprint},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or not (
        row["projection_id"] == projection_id
        and row["artifact_id"] == artifact_id
        and row["artifact_semantic_fingerprint"] == artifact_semantic_fingerprint
        and row["manifest_id"] == payload_manifest_id
    ):
        raise TrainablePublicationError("Trainable projection identity conflicts")


def _prepare_payload(
    object_store: LocalPayloadObjectStore,
    *,
    artifact_type: str,
    intrinsic_fingerprint: str,
    binding: TrainablePayloadBinding,
    encoded: _EncodedPayload,
) -> _PreparedPayload:
    stored = object_store.publish(encoded.content, file_extension="parquet")
    actual_hash = hashlib.sha256(encoded.content).hexdigest()
    if stored.content_hash != actual_hash:
        raise TrainablePublicationError("Content-addressed Object hash mismatch")
    coverage = {
        "start": encoded.coverage_start.isoformat(),
        "end": encoded.coverage_end.isoformat(),
    }
    partition_key = {
        "artifact_type": artifact_type,
        "intrinsic_fingerprint": intrinsic_fingerprint,
        "output_port_key": binding.output_port_key,
    }
    descriptor = sha256_hexdigest(
        {
            "object_content_hash": stored.content_hash,
            "payload_contract_version_id": binding.payload_contract_version_id,
            "physical_encoding_version_id": binding.physical_encoding_version_id,
            "partition_key": partition_key,
            "coverage": coverage,
            "row_count": encoded.row_count,
        }
    )
    logical = sha256_hexdigest(
        {
            "intrinsic_fingerprint": intrinsic_fingerprint,
            "payload_contract_version_id": binding.payload_contract_version_id,
            "partition_descriptor_hash": descriptor,
        }
    )
    manifest_hash = sha256_hexdigest(
        {
            "artifact_type": artifact_type,
            "intrinsic_fingerprint": intrinsic_fingerprint,
            "physical_encoding_version_id": binding.physical_encoding_version_id,
            "logical_payload_fingerprint": logical,
            "partition_descriptor_hash": descriptor,
        }
    )
    return _PreparedPayload(
        content_hash=stored.content_hash,
        storage_uri=stored.storage_uri,
        byte_size=stored.byte_size,
        payload_object_id=uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird-v022:payload-object:{stored.content_hash}"
        ),
        payload_partition_id=uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird-v022:payload-partition:{descriptor}"
        ),
        partition_descriptor_hash=descriptor,
        payload_manifest_id=uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird-v022:payload-manifest:{manifest_hash}"
        ),
        logical_payload_fingerprint=logical,
        manifest_hash=manifest_hash,
        coverage_document=coverage,
    )


def _write_manifest(
    connection: Connection,
    manifest_artifact_id: uuid.UUID,
    *,
    producer_artifact_id: uuid.UUID,
    binding: TrainablePayloadBinding,
    encoded: _EncodedPayload,
    prepared: _PreparedPayload,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO data.payload_object (
              payload_object_id,object_content_hash,storage_uri,byte_size,
              object_state,verification_status,verified_at
            ) VALUES (:id,:hash,:uri,:bytes,'published','verified',:verified)
            ON CONFLICT (object_content_hash) DO NOTHING
            """
        ),
        {
            "id": prepared.payload_object_id,
            "hash": prepared.content_hash,
            "uri": prepared.storage_uri,
            "bytes": prepared.byte_size,
            "verified": datetime.now(UTC),
        },
    )
    observed = (
        connection.execute(
            text(
                "SELECT payload_object_id,storage_uri,byte_size,object_state,"
                "verification_status,verified_at FROM data.payload_object "
                "WHERE object_content_hash=:hash"
            ),
            {"hash": prepared.content_hash},
        )
        .mappings()
        .one()
    )
    if not (
        observed["payload_object_id"] == prepared.payload_object_id
        and observed["storage_uri"] == prepared.storage_uri
        and observed["byte_size"] == prepared.byte_size
        and observed["object_state"] == "published"
        and observed["verification_status"] == "verified"
        and observed["verified_at"] is not None
    ):
        raise TrainablePublicationError("Trainable Payload Object identity conflicts")
    partition_key = {
        "fields": {
            "producer_artifact_id": str(producer_artifact_id),
            "output_port_key": binding.output_port_key,
        },
        "partition_key_hash": sha256_hexdigest(
            {
                "producer_artifact_id": producer_artifact_id,
                "output_port_key": binding.output_port_key,
            }
        ),
    }
    connection.execute(
        text(
            """
            INSERT INTO data.payload_partition (
              payload_partition_id,payload_object_id,partition_descriptor_hash,
              byte_size,row_or_item_count,partition_key,coverage_document,statistics
            ) VALUES (
              :id,:object,:descriptor,:bytes,:rows,CAST(:key AS jsonb),
              CAST(:coverage AS jsonb),CAST(:statistics AS jsonb)
            ) ON CONFLICT (partition_descriptor_hash) DO NOTHING
            """
        ),
        {
            "id": prepared.payload_partition_id,
            "object": prepared.payload_object_id,
            "descriptor": prepared.partition_descriptor_hash,
            "bytes": prepared.byte_size,
            "rows": encoded.row_count,
            "key": _json(partition_key),
            "coverage": _json(prepared.coverage_document),
            "statistics": _json(encoded.statistics),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO data.payload_manifest (
              payload_manifest_id,artifact_id,payload_contract_version_id,
              physical_encoding_version_id,producer_artifact_id,
              producer_output_port_key,logical_payload_fingerprint,manifest_hash,
              partition_count,byte_size,row_or_item_count,coverage_document,
              retention_class,materialization_state
            ) VALUES (
              :id,:artifact,:contract,:encoding,:producer,:port,:logical,:hash,
              1,:bytes,:rows,CAST(:coverage AS jsonb),'research','materialized'
            )
            """
        ),
        {
            "id": prepared.payload_manifest_id,
            "artifact": manifest_artifact_id,
            "contract": binding.payload_contract_version_id,
            "encoding": binding.physical_encoding_version_id,
            "producer": producer_artifact_id,
            "port": binding.output_port_key,
            "logical": prepared.logical_payload_fingerprint,
            "hash": prepared.manifest_hash,
            "bytes": prepared.byte_size,
            "rows": encoded.row_count,
            "coverage": _json(prepared.coverage_document),
        },
    )
    connection.execute(
        text(
            "INSERT INTO data.payload_manifest_partition "
            "(payload_manifest_id,payload_partition_id,ordinal) "
            "VALUES (:manifest,:partition,0)"
        ),
        {
            "manifest": prepared.payload_manifest_id,
            "partition": prepared.payload_partition_id,
        },
    )


def _encode_training_matrix(matrix: TrainingMatrix) -> _EncodedPayload:
    if not matrix.rows or not matrix.decision_dates:
        raise TrainablePublicationError("Training Matrix payload cannot be empty")
    rows = sorted(
        matrix.rows,
        key=lambda item: (item.decision_date, item.security_key, str(item.security_id)),
    )
    table_bytes = _write_chunked_parquet(
        rows,
        lambda chunk: pa.table(
            {
                "decision_date": [item.decision_date for item in chunk],
                "asset_id": [str(item.security_id) for item in chunk],
                "security_key": [item.security_key for item in chunk],
                "decision_cutoff_at": [item.decision_cutoff_at for item in chunk],
                "feature_values": [
                    [str(value) for value in item.feature_values] for item in chunk
                ],
                "target_value": [str(item.target_value) for item in chunk],
                "target_known_at": [item.target_known_at for item in chunk],
                "target_entry_date": [item.target_entry_date for item in chunk],
                "target_exit_date": [item.target_exit_date for item in chunk],
                "target_available": [item.target_available for item in chunk],
            }
        ),
    )
    return _encoded_bytes(
        table_bytes,
        row_count=len(rows),
        group_dates=matrix.decision_dates,
        statistics={
            "matrix_fingerprint": matrix.fingerprint,
            "feature_schema_fingerprint": matrix.feature_schema.fingerprint,
            "target_fingerprint": matrix.target.fingerprint,
        },
    )


def _encode_model_state(
    fitted: FittedFoldState,
    trained_through: date,
    labels_known_through: datetime,
) -> _EncodedPayload:
    table = pa.table(
        {
            "state_key": [fitted.model.model_fingerprint],
            "trained_through": [trained_through],
            "labels_known_through": [labels_known_through],
            "model_document": [_json(fitted.model.model_document)],
        }
    )
    return _encoded_table(
        table,
        group_dates=(trained_through,),
        statistics={
            "model_fingerprint": fitted.model.model_fingerprint,
            "fold_fingerprint": fitted.fold_fingerprint,
        },
    )


def _encode_oof_prediction(
    result: StrictOofResult,
    decision_known_at: Mapping[date, datetime],
) -> _EncodedPayload:
    if not result.predictions:
        raise TrainablePublicationError("OOF Prediction payload cannot be empty")
    rows = sorted(
        result.predictions,
        key=lambda item: (item.decision_date, item.security_key, str(item.security_id)),
    )
    dates = tuple(sorted({item.decision_date for item in rows}))
    missing = set(dates) - set(decision_known_at)
    if missing:
        raise TrainablePublicationError("OOF Prediction is missing decision known-at evidence")
    for value in decision_known_at.values():
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("OOF decision known-at timestamps must be timezone-aware")
    table_bytes = _write_chunked_parquet(
        rows,
        lambda chunk: pa.table(
            {
                "session_date": [item.decision_date for item in chunk],
                "asset_id": [str(item.security_id) for item in chunk],
                "security_key": [item.security_key for item in chunk],
                "known_at": [decision_known_at[item.decision_date] for item in chunk],
                "feature_value": [str(item.centered_rank) for item in chunk],
                "raw_prediction": [str(item.raw_prediction) for item in chunk],
                "fold_ordinal": [item.fold_ordinal for item in chunk],
            }
        ),
    )
    return _encoded_bytes(
        table_bytes,
        row_count=len(rows),
        group_dates=dates,
        statistics={
            "prediction_fingerprint": result.fingerprint,
            "matrix_fingerprint": result.matrix_fingerprint,
            "adapter_key": result.adapter_key,
            "adapter_version": result.adapter_version,
        },
    )


def _encoded_table(
    table: pa.Table,
    *,
    group_dates: Sequence[date],
    statistics: Mapping[str, object],
) -> _EncodedPayload:
    dates = tuple(sorted(set(group_dates)))
    if not dates or table.num_rows <= 0:
        raise TrainablePublicationError("Trainable payload must contain rows and groups")
    sink = io.BytesIO()
    pq.write_table(
        table,
        sink,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
    )
    return _EncodedPayload(
        content=sink.getvalue(),
        row_count=table.num_rows,
        group_count=len(dates),
        coverage_start=dates[0],
        coverage_end=dates[-1],
        statistics=dict(statistics),
    )


def _write_chunked_parquet[RowT](
    rows: Sequence[RowT],
    table_factory: Callable[[Sequence[RowT]], pa.Table],
    *,
    chunk_size: int = 50_000,
) -> bytes:
    """Encode large immutable row sequences without building all Arrow columns at once."""

    if not rows:
        raise TrainablePublicationError("Trainable payload cannot be empty")
    sink = io.BytesIO()
    writer: pq.ParquetWriter | None = None
    try:
        for offset in range(0, len(rows), chunk_size):
            table = table_factory(rows[offset : offset + chunk_size])
            if writer is None:
                writer = pq.ParquetWriter(
                    sink,
                    table.schema,
                    compression="zstd",
                    use_dictionary=False,
                    write_statistics=True,
                )
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()
    return sink.getvalue()


def _encoded_bytes(
    content: bytes,
    *,
    row_count: int,
    group_dates: Sequence[date],
    statistics: Mapping[str, object],
) -> _EncodedPayload:
    dates = tuple(sorted(set(group_dates)))
    if not dates or row_count <= 0:
        raise TrainablePublicationError("Trainable payload must contain rows and groups")
    return _EncodedPayload(
        content=content,
        row_count=row_count,
        group_count=len(dates),
        coverage_start=dates[0],
        coverage_end=dates[-1],
        statistics=dict(statistics),
    )


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: object) -> object:
    if isinstance(value, (date, datetime, uuid.UUID)):
        return str(value)
    raise TypeError(f"Unsupported trainable publication value: {type(value).__name__}")


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
