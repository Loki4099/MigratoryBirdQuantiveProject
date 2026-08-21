from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from functools import partial
from typing import Any, Literal

from pydantic import Field, model_validator
from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.contracts import Key, StrictModel
from style_rotation.v022.graph import AssetContextSnapshot

CONTRACT_VERSION = "v0.22.0"
ARTIFACT_TYPE = "v022_compiled_execution_data_context"
ARTIFACT_VERSION = 1


class ResolvedDataInputBinding(StrictModel):
    input_key: Key
    dataset_publication_id: uuid.UUID
    dataset_artifact_id: uuid.UUID
    dataset_key: Key
    dataset_version_number: int = Field(ge=1)
    coverage_start: date
    coverage_end: date
    calendar_version_id: uuid.UUID | None = None
    calendar_artifact_id: uuid.UUID | None = None
    security_ids: tuple[uuid.UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity(self) -> ResolvedDataInputBinding:
        if self.coverage_start > self.coverage_end:
            raise ValueError("Resolved Data Binding coverage is inverted")
        if (self.calendar_version_id is None) != (self.calendar_artifact_id is None):
            raise ValueError(
                "Resolved Data Binding Calendar version and Artifact must be provided together"
            )
        if len(self.security_ids) != len(set(self.security_ids)):
            raise ValueError("Resolved Data Binding Security identities must be unique")
        return self


class ResolvedDataBindingSnapshot(StrictModel):
    contract_version: Literal["v0.22.0"]
    bindings: tuple[ResolvedDataInputBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bindings(self) -> ResolvedDataBindingSnapshot:
        input_keys = [binding.input_key for binding in self.bindings]
        if len(input_keys) != len(set(input_keys)):
            raise ValueError("Resolved Data Binding input keys must be unique")
        return self


@dataclass(frozen=True, slots=True)
class ExecutionDataContextPublication:
    context_id: uuid.UUID
    artifact_id: uuid.UUID
    compiled_research_graph_id: uuid.UUID
    context_fingerprint: str
    asset_context_fingerprint: str
    resolved_data_binding_fingerprint: str
    input_count: int
    reused: bool


@dataclass(frozen=True, slots=True)
class _PublishedIdentity:
    artifact_id: uuid.UUID
    semantic_fingerprint: str


@dataclass(frozen=True, slots=True)
class _GraphIdentity:
    artifact: _PublishedIdentity
    graph_fingerprint: str
    asset_context_fingerprint: str
    resolved_data_binding_fingerprint: str


@dataclass(frozen=True, slots=True)
class _ValidatedInput:
    ordinal: int
    binding: ResolvedDataInputBinding
    binding_document: dict[str, Any]
    binding_fingerprint: str
    dataset: _PublishedIdentity
    calendar: _PublishedIdentity | None


@dataclass(frozen=True, slots=True)
class _ValidatedAssetContext:
    registry: _PublishedIdentity
    selection: _PublishedIdentity | None = None


class ExecutionDataContextService:
    """Publish exact immutable execution data identities for one compiled v0.22 Graph."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        compiled_research_graph_id: uuid.UUID,
        asset_context_document: Mapping[str, Any],
        resolved_data_binding_document: Mapping[str, Any],
    ) -> ExecutionDataContextPublication:
        asset_context = AssetContextSnapshot.model_validate(asset_context_document)
        resolved_binding = ResolvedDataBindingSnapshot.model_validate(
            resolved_data_binding_document
        )
        canonical_asset_document = asset_context.model_dump(mode="json")
        canonical_binding_document = resolved_binding.model_dump(mode="json")
        asset_fingerprint = sha256_hexdigest(canonical_asset_document)
        binding_fingerprint = sha256_hexdigest(canonical_binding_document)

        with self._engine.connect() as connection:
            graph = self._graph_identity(connection, compiled_research_graph_id)
            if graph.asset_context_fingerprint != asset_fingerprint:
                raise ValueError(
                    "execution_data_context_asset_fingerprint_mismatch: document differs from Graph"
                )
            if graph.resolved_data_binding_fingerprint != binding_fingerprint:
                raise ValueError(
                    "execution_data_context_binding_fingerprint_mismatch: "
                    "document differs from Graph"
                )
            validated_context = self._validate_asset_context(connection, asset_context)
            inputs = tuple(
                self._validate_input(connection, ordinal, item, asset_context)
                for ordinal, item in enumerate(resolved_binding.bindings)
            )

        dependencies = _dependencies(graph.artifact, validated_context, inputs)
        semantic_payload = {
            "contract_version": CONTRACT_VERSION,
            "compiled_research_graph_id": str(compiled_research_graph_id),
            "graph_fingerprint": graph.graph_fingerprint,
            "asset_context_fingerprint": asset_fingerprint,
            "resolved_data_binding_fingerprint": binding_fingerprint,
            "asset_context_document": canonical_asset_document,
            "resolved_data_binding_document": canonical_binding_document,
            "inputs": [
                {
                    "ordinal": item.ordinal,
                    "input_key": item.binding.input_key,
                    "binding_fingerprint": item.binding_fingerprint,
                }
                for item in inputs
            ],
        }
        artifact_key = f"compiled_execution_data_context__{graph.graph_fingerprint}"
        context_fingerprint = _artifact_semantic_fingerprint(
            artifact_key=artifact_key,
            semantic_payload=semantic_payload,
            dependencies=dependencies,
            graph=graph,
            validated_context=validated_context,
            inputs=inputs,
        )
        context_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bird:v0.22:compiled-execution-data-context:{context_fingerprint}",
        )
        publication = self._artifacts.publish(
            artifact_type=ARTIFACT_TYPE,
            artifact_key=artifact_key,
            version_number=ARTIFACT_VERSION,
            semantic_payload=semantic_payload,
            content_payload=semantic_payload,
            dependencies=dependencies,
            reason="publish immutable v0.22 compiled execution data context",
            draft_writer=partial(
                self._write_projection,
                context_id=context_id,
                compiled_research_graph_id=compiled_research_graph_id,
                asset_context=asset_context,
                asset_context_document=canonical_asset_document,
                asset_context_fingerprint=asset_fingerprint,
                resolved_data_binding_document=canonical_binding_document,
                resolved_data_binding_fingerprint=binding_fingerprint,
                inputs=inputs,
                context_fingerprint=context_fingerprint,
            ),
        )
        if publication.semantic_fingerprint != context_fingerprint:
            raise ValueError("Execution Data Context fingerprint calculation drifted")
        self._validate_projection(
            publication.artifact_id,
            context_id,
            compiled_research_graph_id,
            context_fingerprint,
            canonical_asset_document,
            canonical_binding_document,
            inputs,
        )
        return ExecutionDataContextPublication(
            context_id=context_id,
            artifact_id=publication.artifact_id,
            compiled_research_graph_id=compiled_research_graph_id,
            context_fingerprint=context_fingerprint,
            asset_context_fingerprint=asset_fingerprint,
            resolved_data_binding_fingerprint=binding_fingerprint,
            input_count=len(inputs),
            reused=publication.reused,
        )

    @staticmethod
    def _graph_identity(
        connection: Connection, compiled_research_graph_id: uuid.UUID
    ) -> _GraphIdentity:
        row = connection.execute(
            text(
                """
                SELECT graph.artifact_id,graph.graph_fingerprint,graph.contract_version,
                       graph.asset_context_fingerprint,
                       graph.resolved_data_binding_fingerprint,
                       artifact.artifact_type,artifact.status,
                       artifact.semantic_fingerprint
                  FROM workspace.compiled_research_graph graph
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=graph.artifact_id
                 WHERE graph.compiled_research_graph_id=:graph
                """
            ),
            {"graph": compiled_research_graph_id},
        ).mappings().one_or_none()
        if (
            row is None
            or row["contract_version"] != CONTRACT_VERSION
            or row["artifact_type"] != "v022_compiled_research_graph"
            or row["status"] != "published"
            or row["semantic_fingerprint"] is None
        ):
            raise ValueError(
                "execution_data_context_graph_unpublished: exact published v0.22 Graph required"
            )
        return _GraphIdentity(
            _PublishedIdentity(row["artifact_id"], row["semantic_fingerprint"]),
            row["graph_fingerprint"],
            row["asset_context_fingerprint"],
            row["resolved_data_binding_fingerprint"],
        )

    @staticmethod
    def _validate_asset_context(
        connection: Connection, snapshot: AssetContextSnapshot
    ) -> _ValidatedAssetContext:
        if snapshot.selection_kind == "fixed_asset_set":
            rows = connection.execute(
                text(
                    """
                SELECT release.asset_registry_release_id,
                       release.artifact_id AS release_artifact_id,
                       release.catalog_version,definition.asset_set_definition_id,
                       definition.set_key,definition.set_type,member.ordinal,
                       security.security_id,security.security_key,
                       security.legacy_asset_id,profile.instrument_type,
                       profile.tradability,artifact.artifact_type,artifact.status,
                       artifact.semantic_fingerprint
                  FROM catalog.asset_registry_release release
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=release.artifact_id
                  JOIN catalog.asset_set_definition definition
                    ON definition.asset_registry_release_id=release.asset_registry_release_id
                  JOIN catalog.asset_set_member member
                    ON member.asset_set_definition_id=definition.asset_set_definition_id
                  JOIN catalog.security security
                    ON security.security_id=member.security_id
                  JOIN catalog.security_profile profile
                    ON profile.asset_registry_release_id=release.asset_registry_release_id
                   AND profile.security_id=security.security_id
                 WHERE release.asset_registry_release_id=:release
                   AND release.artifact_id=:artifact
                   AND definition.asset_set_definition_id=:definition
                   AND definition.set_key=:context_key
                 ORDER BY member.ordinal
                    """
                ),
                {
                    "release": snapshot.asset_registry_release_id,
                    "artifact": snapshot.asset_registry_artifact_id,
                    "definition": snapshot.asset_set_definition_id,
                    "context_key": snapshot.asset_context_key,
                },
            ).mappings().all()
        elif snapshot.selection_kind == "dynamic_universe_snapshot":
            rows = connection.execute(
                text(
                    """
                    SELECT release.asset_registry_release_id,
                           release.artifact_id AS release_artifact_id,
                           release.catalog_version,
                           definition.asset_set_definition_id,
                           definition.set_key,definition.set_type,member.ordinal,
                           security.security_id,security.security_key,
                           security.legacy_asset_id,profile.instrument_type,
                           profile.tradability,artifact.artifact_type,artifact.status,
                           artifact.semantic_fingerprint,
                           methodology.universe_methodology_id,
                           methodology.artifact_id AS methodology_artifact_id,
                           history.universe_history_id,
                           history.artifact_id AS history_artifact_id,
                           universe.universe_snapshot_id,universe.effective_session
                      FROM catalog.asset_registry_release release
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=release.artifact_id
                      JOIN catalog.asset_set_definition definition
                        ON definition.asset_registry_release_id=
                           release.asset_registry_release_id
                      JOIN catalog.universe_methodology methodology
                        ON methodology.methodology_key=definition.set_key
                      JOIN lineage.artifact methodology_artifact
                        ON methodology_artifact.artifact_id=methodology.artifact_id
                       AND methodology_artifact.status='published'
                      JOIN catalog.universe_history history
                        ON history.universe_methodology_id=
                           methodology.universe_methodology_id
                      JOIN lineage.artifact history_artifact
                        ON history_artifact.artifact_id=history.artifact_id
                       AND history_artifact.status='published'
                      JOIN catalog.universe_snapshot universe
                        ON universe.universe_history_id=history.universe_history_id
                      JOIN catalog.universe_snapshot_member member
                        ON member.universe_snapshot_id=universe.universe_snapshot_id
                      JOIN catalog.security security
                        ON security.security_id=member.security_id
                      JOIN catalog.security_profile profile
                        ON profile.asset_registry_release_id=
                           release.asset_registry_release_id
                       AND profile.security_id=security.security_id
                     WHERE release.asset_registry_release_id=:release
                       AND release.artifact_id=:artifact
                       AND definition.asset_set_definition_id=:definition
                       AND definition.set_key=:context_key
                       AND methodology.universe_methodology_id=:methodology
                       AND methodology.artifact_id=:methodology_artifact
                       AND history.universe_history_id=:history
                       AND history.artifact_id=:history_artifact
                       AND universe.universe_snapshot_id=:snapshot
                     ORDER BY member.ordinal
                    """
                ),
                {
                    "release": snapshot.asset_registry_release_id,
                    "artifact": snapshot.asset_registry_artifact_id,
                    "definition": snapshot.asset_set_definition_id,
                    "context_key": snapshot.asset_context_key,
                    "methodology": snapshot.universe_methodology_id,
                    "methodology_artifact": snapshot.universe_methodology_artifact_id,
                    "history": snapshot.universe_history_id,
                    "history_artifact": snapshot.universe_history_artifact_id,
                    "snapshot": snapshot.universe_snapshot_id,
                },
            ).mappings().all()
        else:
            rows = connection.execute(
                text(
                    """
                    SELECT release.asset_registry_release_id,
                           release.artifact_id AS release_artifact_id,
                           release.catalog_version,
                           selection.explicit_asset_selection_id,
                           selection.artifact_id AS selection_artifact_id,
                           selection.selection_group,selection.selection_document,
                           member.ordinal,
                           member.security_id,member.security_key,
                           security.legacy_asset_id,member.instrument_type,
                           profile.tradability,registry_artifact.artifact_type,
                           registry_artifact.status,
                           registry_artifact.semantic_fingerprint,
                           selection_artifact.status AS selection_status,
                           selection_artifact.semantic_fingerprint AS selection_semantic_fingerprint
                      FROM workspace.v022_explicit_asset_selection selection
                      JOIN lineage.artifact selection_artifact
                        ON selection_artifact.artifact_id=selection.artifact_id
                      JOIN catalog.asset_registry_release release
                        ON release.asset_registry_release_id=selection.asset_registry_release_id
                      JOIN lineage.artifact registry_artifact
                        ON registry_artifact.artifact_id=release.artifact_id
                      JOIN workspace.v022_explicit_asset_selection_member member
                        ON member.explicit_asset_selection_id=
                           selection.explicit_asset_selection_id
                      JOIN catalog.security security ON security.security_id=member.security_id
                      JOIN catalog.security_profile profile
                        ON profile.asset_registry_release_id=release.asset_registry_release_id
                       AND profile.security_id=member.security_id
                     WHERE selection.explicit_asset_selection_id=:selection
                       AND selection.artifact_id=:selection_artifact
                       AND release.asset_registry_release_id=:release
                       AND release.artifact_id=:registry_artifact
                     ORDER BY member.ordinal
                    """
                ),
                {
                    "selection": snapshot.explicit_asset_selection_id,
                    "selection_artifact": snapshot.explicit_asset_selection_artifact_id,
                    "release": snapshot.asset_registry_release_id,
                    "registry_artifact": snapshot.asset_registry_artifact_id,
                },
            ).mappings().all()
        if (
            not rows
            or rows[0]["artifact_type"] != "asset_registry_release"
            or rows[0]["status"] != "published"
            or rows[0]["semantic_fingerprint"] is None
        ):
            raise ValueError(
                "execution_data_context_registry_unpublished: exact published Registry required"
            )
        expected_set_type = {
            "fixed_asset_set": "fixed",
            "dynamic_universe_snapshot": "dynamic_methodology",
        }.get(snapshot.selection_kind)
        if (
            expected_set_type is not None and rows[0]["set_type"] != expected_set_type
        ) or any(
            row["tradability"] == "reference_only" or row["legacy_asset_id"] is None
            for row in rows
        ) or (
            snapshot.selection_kind == "explicit_security_selection"
            and (
                rows[0]["selection_status"] != "published"
                or rows[0]["selection_semantic_fingerprint"] is None
            )
        ):
            raise ValueError(
                "execution_data_context_asset_context_invalid: "
                "fixed canonical tradable set required"
            )
        authoritative = {
            "contract_version": CONTRACT_VERSION,
            "selection_kind": snapshot.selection_kind,
            "asset_context_key": snapshot.asset_context_key,
            "asset_registry_release_id": str(rows[0]["asset_registry_release_id"]),
            "asset_registry_artifact_id": str(rows[0]["release_artifact_id"]),
            "asset_registry_catalog_version": rows[0]["catalog_version"],
            **(
                {"asset_set_definition_id": str(rows[0]["asset_set_definition_id"])}
                if snapshot.selection_kind != "explicit_security_selection"
                else {
                    "explicit_asset_selection_id": str(
                        rows[0]["explicit_asset_selection_id"]
                    ),
                    "explicit_asset_selection_artifact_id": str(
                        rows[0]["selection_artifact_id"]
                    ),
                    "selection_group": rows[0]["selection_group"],
                }
            ),
            **(
                {
                    "universe_methodology_id": str(rows[0]["universe_methodology_id"]),
                    "universe_methodology_artifact_id": str(
                        rows[0]["methodology_artifact_id"]
                    ),
                    "universe_history_id": str(rows[0]["universe_history_id"]),
                    "universe_history_artifact_id": str(rows[0]["history_artifact_id"]),
                    "universe_snapshot_id": str(rows[0]["universe_snapshot_id"]),
                    "universe_effective_session": rows[0]["effective_session"].isoformat(),
                }
                if snapshot.selection_kind == "dynamic_universe_snapshot"
                else {}
            ),
            "members": [
                {
                    "ordinal": row["ordinal"],
                    "security_id": str(row["security_id"]),
                    "security_key": row["security_key"],
                    "instrument_type": row["instrument_type"],
                }
                for row in rows
            ],
        }
        if snapshot.selection_kind == "explicit_security_selection":
            authoritative = dict(rows[0]["selection_document"])
        if snapshot.model_dump(mode="json") != authoritative:
            raise ValueError(
                "execution_data_context_registry_mismatch: document does not reproduce Registry"
            )
        return _ValidatedAssetContext(
            _PublishedIdentity(
                rows[0]["release_artifact_id"], rows[0]["semantic_fingerprint"]
            ),
            (
                _PublishedIdentity(
                    rows[0]["selection_artifact_id"],
                    rows[0]["selection_semantic_fingerprint"],
                )
                if snapshot.selection_kind == "explicit_security_selection"
                else None
            ),
        )

    @staticmethod
    def _validate_input(
        connection: Connection,
        ordinal: int,
        binding: ResolvedDataInputBinding,
        asset_context: AssetContextSnapshot,
    ) -> _ValidatedInput:
        row = connection.execute(
            text(
                """
                SELECT publication.dataset_publication_id,publication.artifact_id,
                       publication.dataset_key,publication.version_number,
                       publication.coverage_start,publication.coverage_end,
                       publication.calendar_version_id,
                       dataset_artifact.artifact_type AS dataset_artifact_type,
                       dataset_artifact.status AS dataset_artifact_status,
                       dataset_artifact.semantic_fingerprint AS dataset_semantic_fingerprint,
                       calendar.artifact_id AS calendar_artifact_id,
                       calendar_artifact.artifact_type AS calendar_artifact_type,
                       calendar_artifact.status AS calendar_artifact_status,
                       calendar_artifact.semantic_fingerprint AS calendar_semantic_fingerprint
                  FROM data.dataset_publication publication
                  JOIN lineage.artifact dataset_artifact
                    ON dataset_artifact.artifact_id=publication.artifact_id
                  LEFT JOIN catalog.calendar_version calendar
                    ON calendar.calendar_version_id=publication.calendar_version_id
                  LEFT JOIN lineage.artifact calendar_artifact
                    ON calendar_artifact.artifact_id=calendar.artifact_id
                 WHERE publication.dataset_publication_id=:publication
                """
            ),
            {"publication": binding.dataset_publication_id},
        ).mappings().one_or_none()
        if (
            row is None
            or row["artifact_id"] != binding.dataset_artifact_id
            or row["dataset_artifact_type"] != "dataset_publication"
            or row["dataset_artifact_status"] != "published"
            or row["dataset_semantic_fingerprint"] is None
        ):
            raise ValueError(
                "execution_data_context_dataset_unpublished: exact Dataset required "
                f"for {binding.input_key}"
            )
        expected_security_ids = tuple(member.security_id for member in asset_context.members)
        if binding.security_ids != expected_security_ids:
            raise ValueError(
                f"execution_data_context_security_mismatch: {binding.input_key} "
                "must cover Asset Context order"
            )
        authoritative = {
            "dataset_key": row["dataset_key"],
            "dataset_version_number": row["version_number"],
            "coverage_start": row["coverage_start"],
            "coverage_end": row["coverage_end"],
            "calendar_version_id": row["calendar_version_id"],
            "calendar_artifact_id": row["calendar_artifact_id"],
        }
        claimed = {
            "dataset_key": binding.dataset_key,
            "dataset_version_number": binding.dataset_version_number,
            "coverage_start": binding.coverage_start,
            "coverage_end": binding.coverage_end,
            "calendar_version_id": binding.calendar_version_id,
            "calendar_artifact_id": binding.calendar_artifact_id,
        }
        if claimed != authoritative:
            raise ValueError(
                f"execution_data_context_dataset_mismatch: {binding.input_key} differs from Dataset"
            )
        calendar: _PublishedIdentity | None = None
        if binding.calendar_version_id is not None:
            if (
                row["calendar_artifact_type"] != "calendar_version"
                or row["calendar_artifact_status"] != "published"
                or row["calendar_semantic_fingerprint"] is None
            ):
                raise ValueError(
                    "execution_data_context_calendar_unpublished: exact Calendar required "
                    f"for {binding.input_key}"
                )
            assert binding.calendar_artifact_id is not None
            calendar = _PublishedIdentity(
                binding.calendar_artifact_id, row["calendar_semantic_fingerprint"]
            )
        covered = set(
            connection.execute(
                text(
                    """
                    SELECT DISTINCT security.security_id
                      FROM catalog.security security
                      JOIN data.dataset_coverage coverage
                        ON coverage.asset_id=security.legacy_asset_id
                     WHERE coverage.dataset_publication_id=:publication
                       AND security.security_id IN :security_ids
                    """
                ).bindparams(bindparam("security_ids", expanding=True)),
                {
                    "publication": binding.dataset_publication_id,
                    "security_ids": binding.security_ids,
                },
            ).scalars()
        )
        if covered != set(binding.security_ids):
            raise ValueError(
                "execution_data_context_dataset_coverage_missing: "
                f"{binding.input_key} lacks Security coverage"
            )
        document = binding.model_dump(mode="json")
        return _ValidatedInput(
            ordinal,
            binding,
            document,
            sha256_hexdigest(document),
            _PublishedIdentity(binding.dataset_artifact_id, row["dataset_semantic_fingerprint"]),
            calendar,
        )

    @staticmethod
    def _write_projection(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        context_id: uuid.UUID,
        compiled_research_graph_id: uuid.UUID,
        asset_context: AssetContextSnapshot,
        asset_context_document: dict[str, Any],
        asset_context_fingerprint: str,
        resolved_data_binding_document: dict[str, Any],
        resolved_data_binding_fingerprint: str,
        inputs: tuple[_ValidatedInput, ...],
        context_fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_compiled_execution_data_context (
                  compiled_execution_data_context_id,artifact_id,
                  compiled_research_graph_id,contract_version,
                  asset_registry_release_id,asset_registry_artifact_id,
                  asset_set_definition_id,explicit_asset_selection_id,
                  explicit_asset_selection_artifact_id,asset_context_fingerprint,
                  resolved_data_binding_fingerprint,asset_context_document,
                  resolved_data_binding_document,input_count,context_fingerprint
                ) VALUES (
                  :id,:artifact,:graph,:contract,:registry_release,
                  :registry_artifact,:asset_set,:explicit_selection,
                  :explicit_selection_artifact,:asset_fingerprint,
                  :binding_fingerprint,CAST(:asset_document AS jsonb),
                  CAST(:binding_document AS jsonb),:input_count,:context_fingerprint
                )
                """
            ),
            {
                "id": context_id,
                "artifact": artifact_id,
                "graph": compiled_research_graph_id,
                "contract": CONTRACT_VERSION,
                "registry_release": asset_context.asset_registry_release_id,
                "registry_artifact": asset_context.asset_registry_artifact_id,
                "asset_set": asset_context.asset_set_definition_id,
                "explicit_selection": asset_context.explicit_asset_selection_id,
                "explicit_selection_artifact": (
                    asset_context.explicit_asset_selection_artifact_id
                ),
                "asset_fingerprint": asset_context_fingerprint,
                "binding_fingerprint": resolved_data_binding_fingerprint,
                "asset_document": _json(asset_context_document),
                "binding_document": _json(resolved_data_binding_document),
                "input_count": len(inputs),
                "context_fingerprint": context_fingerprint,
            },
        )
        for item in inputs:
            binding = item.binding
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_compiled_execution_data_input (
                      compiled_execution_data_context_id,ordinal,input_key,
                      dataset_publication_id,dataset_artifact_id,calendar_version_id,
                      calendar_artifact_id,coverage_start,coverage_end,security_ids,
                      binding_document,binding_fingerprint
                    ) VALUES (
                      :context,:ordinal,:input_key,:dataset_publication,
                      :dataset_artifact,:calendar_version,:calendar_artifact,
                      :coverage_start,:coverage_end,CAST(:security_ids AS jsonb),
                      CAST(:binding_document AS jsonb),:binding_fingerprint
                    )
                    """
                ),
                {
                    "context": context_id,
                    "ordinal": item.ordinal,
                    "input_key": binding.input_key,
                    "dataset_publication": binding.dataset_publication_id,
                    "dataset_artifact": binding.dataset_artifact_id,
                    "calendar_version": binding.calendar_version_id,
                    "calendar_artifact": binding.calendar_artifact_id,
                    "coverage_start": binding.coverage_start,
                    "coverage_end": binding.coverage_end,
                    "security_ids": _json([str(value) for value in binding.security_ids]),
                    "binding_document": _json(item.binding_document),
                    "binding_fingerprint": item.binding_fingerprint,
                },
            )

    def _validate_projection(
        self,
        artifact_id: uuid.UUID,
        context_id: uuid.UUID,
        compiled_research_graph_id: uuid.UUID,
        context_fingerprint: str,
        asset_context_document: dict[str, Any],
        resolved_data_binding_document: dict[str, Any],
        inputs: tuple[_ValidatedInput, ...],
    ) -> None:
        with self._engine.connect() as connection:
            context = connection.execute(
                text(
                    """
                    SELECT compiled_execution_data_context_id,
                           compiled_research_graph_id,context_fingerprint,
                           asset_context_document,resolved_data_binding_document,input_count
                      FROM workspace.v022_compiled_execution_data_context
                     WHERE artifact_id=:artifact
                    """
                ),
                {"artifact": artifact_id},
            ).mappings().one_or_none()
            children = connection.execute(
                text(
                    """
                    SELECT ordinal,binding_document,binding_fingerprint
                      FROM workspace.v022_compiled_execution_data_input
                     WHERE compiled_execution_data_context_id=:context
                     ORDER BY ordinal
                    """
                ),
                {"context": context_id},
            ).mappings().all()
        if (
            context is None
            or context["compiled_execution_data_context_id"] != context_id
            or context["compiled_research_graph_id"] != compiled_research_graph_id
            or context["context_fingerprint"] != context_fingerprint
            or context["asset_context_document"] != asset_context_document
            or context["resolved_data_binding_document"] != resolved_data_binding_document
            or context["input_count"] != len(inputs)
            or len(children) != len(inputs)
            or any(
                child["ordinal"] != item.ordinal
                or child["binding_document"] != item.binding_document
                or child["binding_fingerprint"] != item.binding_fingerprint
                for child, item in zip(children, inputs, strict=True)
            )
        ):
            raise ValueError("Execution Data Context projection identity collision")


def _dependencies(
    graph: _PublishedIdentity,
    validated_context: _ValidatedAssetContext,
    inputs: tuple[_ValidatedInput, ...],
) -> tuple[DependencyInput, ...]:
    result = [
        DependencyInput(graph.artifact_id, "compiled_graph", 0),
        DependencyInput(validated_context.registry.artifact_id, "asset_context", 0),
    ]
    if validated_context.selection is not None:
        result.append(
            DependencyInput(validated_context.selection.artifact_id, "asset_selection", 0)
        )
    seen_datasets: set[uuid.UUID] = set()
    seen_calendars: set[uuid.UUID] = set()
    for item in inputs:
        if item.dataset.artifact_id not in seen_datasets:
            result.append(
                DependencyInput(item.dataset.artifact_id, "data_binding", item.ordinal)
            )
            seen_datasets.add(item.dataset.artifact_id)
        if item.calendar is not None and item.calendar.artifact_id not in seen_calendars:
            result.append(DependencyInput(item.calendar.artifact_id, "calendar", item.ordinal))
            seen_calendars.add(item.calendar.artifact_id)
    return tuple(result)


def _artifact_semantic_fingerprint(
    *,
    artifact_key: str,
    semantic_payload: object,
    dependencies: tuple[DependencyInput, ...],
    graph: _GraphIdentity,
    validated_context: _ValidatedAssetContext,
    inputs: tuple[_ValidatedInput, ...],
) -> str:
    fingerprints: dict[uuid.UUID, str] = {
        graph.artifact.artifact_id: graph.artifact.semantic_fingerprint,
        validated_context.registry.artifact_id:
            validated_context.registry.semantic_fingerprint,
    }
    if validated_context.selection is not None:
        fingerprints[validated_context.selection.artifact_id] = (
            validated_context.selection.semantic_fingerprint
        )
    for item in inputs:
        fingerprints[item.dataset.artifact_id] = item.dataset.semantic_fingerprint
        if item.calendar is not None:
            fingerprints[item.calendar.artifact_id] = item.calendar.semantic_fingerprint
    return sha256_hexdigest(
        {
            "artifact_identity": {
                "artifact_type": ARTIFACT_TYPE,
                "artifact_key": artifact_key,
                "version_number": ARTIFACT_VERSION,
            },
            "semantic_payload": semantic_payload,
            "dependencies": [
                {
                    "role": item.role,
                    "ordinal": item.ordinal,
                    "semantic_fingerprint": fingerprints[item.artifact_id],
                }
                for item in dependencies
            ],
        }
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
