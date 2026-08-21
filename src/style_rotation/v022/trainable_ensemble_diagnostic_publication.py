from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from functools import partial
from typing import cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.trainable_ensemble_diagnostics import (
    TrainableEnsembleDiagnostic,
)


@dataclass(frozen=True, slots=True)
class PublishedTrainableAggregationDiagnostic:
    trainable_aggregation_diagnostic_id: uuid.UUID
    artifact_id: uuid.UUID
    aggregation_run_id: uuid.UUID
    diagnostic_fingerprint: str
    diagnostic_document: dict[str, object]
    reused: bool


def publish_trainable_aggregation_diagnostic(
    engine: Engine,
    *,
    aggregation_run_id: uuid.UUID,
    ensemble_spec_id: uuid.UUID | None,
    diagnostic: TrainableEnsembleDiagnostic,
    dependencies: tuple[DependencyInput, ...],
) -> PublishedTrainableAggregationDiagnostic:
    """Publish the exact strict-OOF diagnostic used by one supervised Run."""

    document_fingerprint = diagnostic.fingerprint
    fingerprint = sha256_hexdigest(
        {
            "aggregation_run_id": aggregation_run_id,
            "diagnostic_document_fingerprint": document_fingerprint,
        }
    )
    existing = _existing(engine, aggregation_run_id)
    if existing is not None:
        if existing.diagnostic_fingerprint != fingerprint:
            raise ValueError("Aggregation Run is already bound to a different trainable diagnostic")
        return existing
    diagnostic_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"bird:v0.22:trainable-aggregation-diagnostic:{fingerprint}",
    )
    semantic = {
        "contract_version": "v0.22.0",
        "aggregation_run_id": str(aggregation_run_id),
        "ensemble_spec_id": str(ensemble_spec_id) if ensemble_spec_id else None,
        "diagnostic_fingerprint": fingerprint,
        "diagnostic_document_fingerprint": document_fingerprint,
        "diagnostic_document": diagnostic.diagnostic_document,
    }
    publication = ArtifactService(engine).publish(
        artifact_type="v022_trainable_aggregation_diagnostic",
        artifact_key=fingerprint,
        version_number=1,
        semantic_payload=semantic,
        content_payload=semantic,
        dependencies=dependencies,
        reason=f"publish strict OOF diagnostic for Aggregation Run {aggregation_run_id}",
        draft_writer=partial(
            _write,
            diagnostic_id=diagnostic_id,
            aggregation_run_id=aggregation_run_id,
            ensemble_spec_id=ensemble_spec_id,
            fingerprint=fingerprint,
            document=diagnostic.diagnostic_document,
        ),
    )
    frozen = _existing(engine, aggregation_run_id)
    if frozen is None:
        raise ValueError("Published Trainable Diagnostic has no immutable projection")
    return PublishedTrainableAggregationDiagnostic(
        frozen.trainable_aggregation_diagnostic_id,
        frozen.artifact_id,
        frozen.aggregation_run_id,
        frozen.diagnostic_fingerprint,
        frozen.diagnostic_document,
        publication.reused,
    )


def _existing(
    engine: Engine, aggregation_run_id: uuid.UUID
) -> PublishedTrainableAggregationDiagnostic | None:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT diagnostic.*,artifact.status FROM "
                    "aggregation.v022_trainable_aggregation_diagnostic diagnostic "
                    "JOIN lineage.artifact artifact ON "
                    "artifact.artifact_id=diagnostic.artifact_id "
                    "WHERE diagnostic.aggregation_run_id=:run"
                ),
                {"run": aggregation_run_id},
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        return None
    if row["status"] != "published":
        raise ValueError("Trainable Diagnostic Artifact is not published")
    return PublishedTrainableAggregationDiagnostic(
        row["trainable_aggregation_diagnostic_id"],
        row["artifact_id"],
        row["aggregation_run_id"],
        row["diagnostic_fingerprint"],
        cast(dict[str, object], row["diagnostic_document"]),
        True,
    )


def _write(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    diagnostic_id: uuid.UUID,
    aggregation_run_id: uuid.UUID,
    ensemble_spec_id: uuid.UUID | None,
    fingerprint: str,
    document: dict[str, object],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO aggregation.v022_trainable_aggregation_diagnostic (
              trainable_aggregation_diagnostic_id,artifact_id,
              aggregation_run_id,ensemble_spec_id,diagnostic_fingerprint,
              member_count,target_group_count,diagnostic_document
            ) VALUES (
              :id,:artifact,:run,:ensemble,:fingerprint,:members,:groups,
              CAST(:document AS jsonb)
            )
            """
        ),
        {
            "id": diagnostic_id,
            "artifact": artifact_id,
            "run": aggregation_run_id,
            "ensemble": ensemble_spec_id,
            "fingerprint": fingerprint,
            "members": cast(int, document["member_count"]),
            "groups": cast(int, document["target_group_count"]),
            "document": json.dumps(document, ensure_ascii=False),
        },
    )
