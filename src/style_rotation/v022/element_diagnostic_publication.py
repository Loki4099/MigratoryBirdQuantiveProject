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
from style_rotation.v022.element_diagnostics import ElementDiagnostic


@dataclass(frozen=True, slots=True)
class ElementDiagnosticPublication:
    result_element_diagnostic_id: uuid.UUID
    artifact_id: uuid.UUID
    diagnostic_fingerprint: str
    result_artifact_id: uuid.UUID
    compiled_feature_occurrence_id: uuid.UUID
    diagnostic_document: dict[str, object]
    reused: bool


class ElementDiagnosticPublicationService:
    """Publish one direct Aggregation input diagnostic as immutable evidence."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        diagnostic: ElementDiagnostic,
        *,
        result_artifact_id: uuid.UUID,
        configuration_snapshot_id: uuid.UUID,
        market_dataset_publication_id: uuid.UUID,
        market_dataset_artifact_id: uuid.UUID,
        calendar_version_id: uuid.UUID,
        calendar_artifact_id: uuid.UUID,
    ) -> ElementDiagnosticPublication:
        document = diagnostic.to_document()
        semantic = {
            "contract_version": "v0.22.0",
            "result_artifact_id": str(result_artifact_id),
            "configuration_snapshot_id": str(configuration_snapshot_id),
            "market_dataset_publication_id": str(market_dataset_publication_id),
            "market_dataset_artifact_id": str(market_dataset_artifact_id),
            "calendar_version_id": str(calendar_version_id),
            "calendar_artifact_id": str(calendar_artifact_id),
            "element_diagnostic": document,
        }
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(
            result_artifact_id, diagnostic.compiled_feature_occurrence_id
        )
        if existing is not None:
            if existing.diagnostic_fingerprint != fingerprint:
                raise ValueError("Result element is already bound to a different diagnostic")
            return existing
        diagnostic_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:result-element-diagnostic:{fingerprint}"
        )
        with self._engine.connect() as connection:
            configuration_artifact_id = connection.scalar(
                text(
                    "SELECT artifact_id FROM "
                    "experiment.v022_research_configuration_snapshot "
                    "WHERE configuration_snapshot_id=:configuration"
                ),
                {"configuration": configuration_snapshot_id},
            )
        if not isinstance(configuration_artifact_id, uuid.UUID):
            raise LookupError("Element Diagnostic Configuration Snapshot was not found")
        dependencies = (
            DependencyInput(result_artifact_id, "result", 0),
            DependencyInput(configuration_artifact_id, "configuration", 0),
            DependencyInput(diagnostic.manifest_artifact_id, "input_manifest", 0),
            DependencyInput(diagnostic.target_version_artifact_id, "evaluation_target", 0),
            DependencyInput(market_dataset_artifact_id, "canonical_market", 0),
            DependencyInput(calendar_artifact_id, "calendar", 0),
        )
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_result_element_diagnostic",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=dependencies,
            reason="publish immutable v0.22 direct-element diagnostic",
            draft_writer=partial(
                self._write,
                diagnostic_id=diagnostic_id,
                diagnostic=diagnostic,
                result_artifact_id=result_artifact_id,
                configuration_snapshot_id=configuration_snapshot_id,
                market_dataset_publication_id=market_dataset_publication_id,
                market_dataset_artifact_id=market_dataset_artifact_id,
                calendar_version_id=calendar_version_id,
                calendar_artifact_id=calendar_artifact_id,
                fingerprint=fingerprint,
                document=document,
            ),
        )
        if publication.reused:
            frozen = self._existing(
                result_artifact_id, diagnostic.compiled_feature_occurrence_id
            )
            if frozen is None:
                raise ValueError("Reused Element Diagnostic Artifact has no projection")
            return frozen
        return ElementDiagnosticPublication(
            diagnostic_id,
            publication.artifact_id,
            fingerprint,
            result_artifact_id,
            diagnostic.compiled_feature_occurrence_id,
            document,
            False,
        )

    def _existing(
        self,
        result_artifact_id: uuid.UUID,
        compiled_feature_occurrence_id: uuid.UUID,
    ) -> ElementDiagnosticPublication | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT diagnostic.*,artifact.status FROM "
                        "experiment.v022_result_element_diagnostic diagnostic "
                        "JOIN lineage.artifact artifact ON "
                        "artifact.artifact_id=diagnostic.artifact_id "
                        "WHERE diagnostic.result_artifact_id=:result AND "
                        "diagnostic.compiled_feature_occurrence_id=:occurrence"
                    ),
                    {
                        "result": result_artifact_id,
                        "occurrence": compiled_feature_occurrence_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("Element Diagnostic Artifact is not published")
        return ElementDiagnosticPublication(
            row["result_element_diagnostic_id"],
            row["artifact_id"],
            row["diagnostic_fingerprint"],
            row["result_artifact_id"],
            row["compiled_feature_occurrence_id"],
            cast(dict[str, object], row["diagnostic_document"]),
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        diagnostic_id: uuid.UUID,
        diagnostic: ElementDiagnostic,
        result_artifact_id: uuid.UUID,
        configuration_snapshot_id: uuid.UUID,
        market_dataset_publication_id: uuid.UUID,
        market_dataset_artifact_id: uuid.UUID,
        calendar_version_id: uuid.UUID,
        calendar_artifact_id: uuid.UUID,
        fingerprint: str,
        document: dict[str, object],
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO experiment.v022_result_element_diagnostic (
                  result_element_diagnostic_id,artifact_id,result_artifact_id,
                  configuration_snapshot_id,compiled_feature_occurrence_id,
                  payload_manifest_id,payload_manifest_artifact_id,
                  target_version_id,target_version_artifact_id,
                  market_dataset_publication_id,market_dataset_artifact_id,
                  calendar_version_id,calendar_artifact_id,
                  diagnostic_fingerprint,diagnostic_document
                ) VALUES (
                  :id,:artifact,:result,:configuration,:occurrence,:manifest,
                  :manifest_artifact,:target,:target_artifact,:market,
                  :market_artifact,:calendar,:calendar_artifact,:fingerprint,
                  CAST(:document AS jsonb)
                )
                """
            ),
            {
                "id": diagnostic_id,
                "artifact": artifact_id,
                "result": result_artifact_id,
                "configuration": configuration_snapshot_id,
                "occurrence": diagnostic.compiled_feature_occurrence_id,
                "manifest": diagnostic.payload_manifest_id,
                "manifest_artifact": diagnostic.manifest_artifact_id,
                "target": diagnostic.target_version_id,
                "target_artifact": diagnostic.target_version_artifact_id,
                "market": market_dataset_publication_id,
                "market_artifact": market_dataset_artifact_id,
                "calendar": calendar_version_id,
                "calendar_artifact": calendar_artifact_id,
                "fingerprint": fingerprint,
                "document": json.dumps(document, ensure_ascii=False),
            },
        )
