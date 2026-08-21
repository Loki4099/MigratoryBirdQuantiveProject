from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date
from functools import partial

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

CONTRACT_VERSION = "v0.22.processing_calculation_context.v1"
BINDING_CONTRACT_VERSION = "v0.22.compiled_context_calculation_binding.v1"


@dataclass(frozen=True, slots=True)
class ProcessingCalculationContextSpec:
    compiled_execution_data_context_id: uuid.UUID
    dataset_publication_id: uuid.UUID
    dataset_artifact_id: uuid.UUID
    calendar_version_id: uuid.UUID
    calendar_artifact_id: uuid.UUID
    coverage_start: date
    coverage_end: date
    security_ids: tuple[uuid.UUID, ...]
    raw_feature_versions: tuple[tuple[uuid.UUID, uuid.UUID], ...]
    source_snapshot_artifact_ids: tuple[uuid.UUID, ...]

    def __post_init__(self) -> None:
        if self.coverage_start > self.coverage_end:
            raise ValueError("coverage_start must not follow coverage_end")
        _require_sorted_unique(self.security_ids, "security_ids")
        _require_sorted_unique(
            tuple(item[0] for item in self.raw_feature_versions),
            "raw feature version ids",
        )
        if any(not isinstance(item[1], uuid.UUID) for item in self.raw_feature_versions):
            raise ValueError("raw feature artifacts must be UUIDs")
        _require_sorted_unique(
            self.source_snapshot_artifact_ids, "source snapshot artifact ids"
        )

    @property
    def context_document(self) -> dict[str, object]:
        return {
            "contract_version": CONTRACT_VERSION,
            "dataset_publication_id": str(self.dataset_publication_id),
            "calendar_version_id": str(self.calendar_version_id),
            "coverage_start": self.coverage_start.isoformat(),
            "coverage_end": self.coverage_end.isoformat(),
            "security_ids": [str(item) for item in self.security_ids],
            "raw_feature_version_ids": [
                str(item[0]) for item in self.raw_feature_versions
            ],
            "source_snapshot_artifact_ids": [
                str(item) for item in self.source_snapshot_artifact_ids
            ],
        }

    @property
    def context_fingerprint(self) -> str:
        return sha256_hexdigest(self.context_document)


@dataclass(frozen=True, slots=True)
class ProcessingCalculationContextIdentity:
    calculation_context_id: uuid.UUID
    artifact_id: uuid.UUID
    context_fingerprint: str
    reused: bool


class ProcessingCalculationContextService:
    """Publish and bind one frequency-neutral daily Processing identity."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self, spec: ProcessingCalculationContextSpec
    ) -> ProcessingCalculationContextIdentity:
        document = spec.context_document
        fingerprint = spec.context_fingerprint
        calculation_context_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird-v022:processing-calculation-context:{fingerprint}"
        )
        dependencies = (
            DependencyInput(spec.dataset_artifact_id, "dataset_publication", 0),
            DependencyInput(spec.calendar_artifact_id, "calendar_version", 1),
            *tuple(
                DependencyInput(artifact_id, "raw_feature_version", ordinal)
                for ordinal, (_feature_id, artifact_id) in enumerate(
                    spec.raw_feature_versions, 10
                )
            ),
            *tuple(
                DependencyInput(artifact_id, "source_snapshot", ordinal)
                for ordinal, artifact_id in enumerate(
                    spec.source_snapshot_artifact_ids, 1000
                )
            ),
        )
        result = ArtifactService(self._engine).publish(
            artifact_type="v022_processing_calculation_context",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=document,
            content_payload=document,
            dependencies=dependencies,
            reason=f"publish Processing Calculation Context {fingerprint}",
            draft_writer=partial(
                _write_context,
                calculation_context_id=calculation_context_id,
                spec=spec,
                document=document,
                fingerprint=fingerprint,
            ),
        )
        _bind_compiled_context(
            self._engine,
            compiled_execution_data_context_id=(
                spec.compiled_execution_data_context_id
            ),
            calculation_context_id=calculation_context_id,
        )
        return ProcessingCalculationContextIdentity(
            calculation_context_id=calculation_context_id,
            artifact_id=result.artifact_id,
            context_fingerprint=fingerprint,
            reused=result.reused,
        )


def _write_context(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    calculation_context_id: uuid.UUID,
    spec: ProcessingCalculationContextSpec,
    document: dict[str, object],
    fingerprint: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO processing.v022_calculation_context (
              calculation_context_id,artifact_id,context_fingerprint,
              dataset_publication_id,dataset_artifact_id,calendar_version_id,
              calendar_artifact_id,coverage_start,coverage_end,security_ids,
              raw_feature_version_ids,source_snapshot_artifact_ids,context_document
            ) VALUES (
              :id,:artifact,:fingerprint,:dataset,:dataset_artifact,:calendar,
              :calendar_artifact,:start,:end,CAST(:securities AS jsonb),
              CAST(:features AS jsonb),CAST(:snapshots AS jsonb),
              CAST(:document AS jsonb)
            )
            """
        ),
        {
            "id": calculation_context_id,
            "artifact": artifact_id,
            "fingerprint": fingerprint,
            "dataset": spec.dataset_publication_id,
            "dataset_artifact": spec.dataset_artifact_id,
            "calendar": spec.calendar_version_id,
            "calendar_artifact": spec.calendar_artifact_id,
            "start": spec.coverage_start,
            "end": spec.coverage_end,
            "securities": _json([str(item) for item in spec.security_ids]),
            "features": _json([str(item[0]) for item in spec.raw_feature_versions]),
            "snapshots": _json(
                [str(item) for item in spec.source_snapshot_artifact_ids]
            ),
            "document": _json(document),
        },
    )


def _bind_compiled_context(
    engine: Engine,
    *,
    compiled_execution_data_context_id: uuid.UUID,
    calculation_context_id: uuid.UUID,
) -> None:
    document = {
        "contract_version": BINDING_CONTRACT_VERSION,
        "compiled_execution_data_context_id": str(
            compiled_execution_data_context_id
        ),
        "calculation_context_id": str(calculation_context_id),
    }
    fingerprint = sha256_hexdigest(document)
    with engine.begin() as connection:
        existing = (
            connection.execute(
                text(
                    """
                    SELECT calculation_context_id,binding_fingerprint,binding_document
                      FROM processing.v022_compiled_context_calculation_binding
                     WHERE compiled_execution_data_context_id=:context
                     FOR UPDATE
                    """
                ),
                {"context": compiled_execution_data_context_id},
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if (
                existing["calculation_context_id"] != calculation_context_id
                or existing["binding_fingerprint"] != fingerprint
                or existing["binding_document"] != document
            ):
                raise ValueError(
                    "Compiled Execution Context already binds another calculation identity"
                )
            return
        connection.execute(
            text(
                """
                INSERT INTO processing.v022_compiled_context_calculation_binding (
                  compiled_execution_data_context_id,calculation_context_id,
                  binding_fingerprint,binding_document
                ) VALUES (:context,:calculation,:fingerprint,CAST(:document AS jsonb))
                """
            ),
            {
                "context": compiled_execution_data_context_id,
                "calculation": calculation_context_id,
                "fingerprint": fingerprint,
                "document": _json(document),
            },
        )


def _require_sorted_unique(values: tuple[uuid.UUID, ...], label: str) -> None:
    if not values or any(not isinstance(item, uuid.UUID) for item in values):
        raise ValueError(f"{label} must contain UUIDs")
    if tuple(sorted(set(values), key=str)) != values:
        raise ValueError(f"{label} must be sorted and unique")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
