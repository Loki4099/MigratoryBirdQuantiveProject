# ruff: noqa: E501
from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from functools import partial
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshotPublication:
    configuration_snapshot_id: uuid.UUID
    artifact_id: uuid.UUID
    configuration_fingerprint: str
    semantic_identity_document: dict[str, Any]
    provenance_document: dict[str, Any]
    display_document: dict[str, Any]
    reused: bool
    execution_context_binding: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class _ConfigurationExecutionContextBinding:
    compiled_research_graph_id: uuid.UUID
    compiled_strategy_branch_id: uuid.UUID
    compiled_execution_data_context_id: uuid.UUID
    execution_data_context_artifact_id: uuid.UUID
    execution_data_context_fingerprint: str
    defense_version_id: uuid.UUID | None
    defense_package_artifact_id: uuid.UUID | None
    timing_policy_version_id: uuid.UUID | None
    timing_policy_artifact_id: uuid.UUID | None
    allocation_policy_version_id: uuid.UUID | None
    allocation_policy_artifact_id: uuid.UUID | None
    compiled_defense_execution_context_id: uuid.UUID | None
    defense_execution_context_artifact_id: uuid.UUID | None
    defense_execution_context_fingerprint: str | None
    document: dict[str, Any]
    fingerprint: str

    def dependencies(self) -> tuple[DependencyInput, ...]:
        result = [
            DependencyInput(
                self.execution_data_context_artifact_id,
                "compiled_execution_data_context",
                0,
            )
        ]
        if self.defense_version_id is not None:
            required = (
                (self.defense_package_artifact_id, "defense_package"),
                (self.timing_policy_artifact_id, "defense_timing_policy_version"),
                (
                    self.allocation_policy_artifact_id,
                    "defense_allocation_policy_version",
                ),
                (
                    self.defense_execution_context_artifact_id,
                    "compiled_defense_execution_context",
                ),
            )
            if any(artifact_id is None for artifact_id, _ in required):
                raise ValueError("Defense Execution Context binding is incomplete")
            result.extend(
                DependencyInput(cast(uuid.UUID, artifact_id), role, 0)
                for artifact_id, role in required
            )
        return tuple(result)


@dataclass(frozen=True, slots=True)
class _ConfigurationEnsembleBinding:
    artifact_id: uuid.UUID
    semantic_document: dict[str, Any]
    display_document: dict[str, Any]


EvidenceClass = Literal[
    "walk_forward_backtest",
    "locked_historical_test",
    "prospective_oos",
]


@dataclass(frozen=True, slots=True, order=True)
class PanelObservation:
    decision_session: date
    asset_key: str


@dataclass(frozen=True, slots=True)
class CommonPanelPublication:
    common_evaluation_panel_id: uuid.UUID
    artifact_id: uuid.UUID
    panel_fingerprint: str
    evidence_class: EvidenceClass
    observations: tuple[PanelObservation, ...]
    panel_document: dict[str, Any]
    reused: bool


@dataclass(frozen=True, slots=True)
class ResultEvidencePublication:
    result_evidence_snapshot_id: uuid.UUID
    artifact_id: uuid.UUID
    evidence_fingerprint: str
    result_artifact_id: uuid.UUID
    configuration_snapshot_id: uuid.UUID
    common_evaluation_panel_id: uuid.UUID | None
    evidence_class: EvidenceClass
    evidence_document: dict[str, Any]
    quality_document: dict[str, Any]
    reused: bool


class ConfigurationSnapshotService:
    """Publish the immutable UI/research identity for one compiled branch."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        *,
        compiled_strategy_branch_id: uuid.UUID,
        execution_policy_document: dict[str, Any],
        provenance_document: dict[str, Any],
        compiled_execution_data_context_id: uuid.UUID | None = None,
        compiled_defense_execution_context_id: uuid.UUID | None = None,
    ) -> ConfigurationSnapshotPublication:
        with self._engine.connect() as connection:
            branch = _branch(connection, compiled_strategy_branch_id)
            direct_inputs = _direct_inputs(connection, compiled_strategy_branch_id)
            ensemble = _configuration_ensemble_binding(connection, branch)
            composed = _graph_uses_composed_defense(
                connection, cast(uuid.UUID, branch["compiled_research_graph_id"])
            )
            binding = _configuration_execution_context_binding(
                connection,
                branch=branch,
                composed=composed,
                compiled_execution_data_context_id=compiled_execution_data_context_id,
                compiled_defense_execution_context_id=(
                    compiled_defense_execution_context_id
                ),
            )
        semantic = _semantic_document(
            branch,
            direct_inputs,
            execution_policy_document,
            ensemble=ensemble,
        )
        if binding is not None:
            semantic["execution_contexts"] = binding.document
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(fingerprint)
        if existing is not None:
            return existing
        if branch["strategy_parameter_preset_version_id"] is None:
            raise ValueError(
                "New Configuration Snapshots require an exact Strategy "
                "Parameter Preset binding"
            )
        display = _display_document(branch, direct_inputs, ensemble=ensemble)
        snapshot_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bird:v0.22:research-configuration:{fingerprint}",
        )
        dependencies = [
            DependencyInput(cast(uuid.UUID, branch["graph_artifact_id"]), "compiled_graph", 0)
        ]
        if branch["strategy_parameter_preset_artifact_id"] is not None:
            dependencies.append(
                DependencyInput(
                    cast(uuid.UUID, branch["strategy_parameter_preset_artifact_id"]),
                    "strategy_parameter_preset",
                    0,
                )
            )
        if binding is not None:
            dependencies.extend(binding.dependencies())
        # The compiled Graph Artifact already owns the exact Ensemble Spec as
        # a direct dependency.  Keep the Configuration Snapshot's historic
        # dependency closure exact (Graph + Strategy preset + execution
        # contexts); the Snapshot semantic document freezes the Ensemble ID,
        # Artifact ID, fingerprint, and complete member specification.
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_research_configuration_snapshot",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload={
                "semantic_identity_document": semantic,
                "provenance_document": provenance_document,
                "display_document": display,
            },
            dependencies=tuple(dependencies),
            reason="publish immutable v0.22 Research Configuration Snapshot",
            draft_writer=partial(
                self._write,
                snapshot_id=snapshot_id,
                branch=branch,
                direct_inputs=direct_inputs,
                fingerprint=fingerprint,
                semantic=semantic,
                provenance=provenance_document,
                display=display,
                execution_context_binding=binding,
            ),
        )
        if publication.reused:
            frozen = self._existing(fingerprint)
            if frozen is None:
                raise ValueError("Reused Configuration Artifact has no Snapshot row")
            return frozen
        return ConfigurationSnapshotPublication(
            snapshot_id,
            publication.artifact_id,
            fingerprint,
            semantic,
            provenance_document,
            display,
            publication.reused,
            None if binding is None else binding.document,
        )

    def _existing(self, fingerprint: str) -> ConfigurationSnapshotPublication | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT snapshot.*,artifact.status,"
                        "binding.binding_document,binding.binding_fingerprint FROM "
                        "experiment.v022_research_configuration_snapshot snapshot "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id "
                        "LEFT JOIN experiment.v022_configuration_execution_context_binding binding "
                        "ON binding.configuration_snapshot_id=snapshot.configuration_snapshot_id "
                        "WHERE snapshot.configuration_fingerprint=:fingerprint"
                    ),
                    {"fingerprint": fingerprint},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("Research Configuration Snapshot Artifact is not published")
        semantic = cast(dict[str, Any], row["semantic_identity_document"])
        if row["configuration_fingerprint"] != sha256_hexdigest(semantic):
            raise ValueError("Research Configuration Snapshot semantic identity drifted")
        binding_document = cast(dict[str, Any] | None, row["binding_document"])
        if (binding_document is None) != ("execution_contexts" not in semantic):
            raise ValueError("Research Configuration Snapshot Context binding is incomplete")
        if binding_document is not None and (
            semantic["execution_contexts"] != binding_document
            or row["binding_fingerprint"] != sha256_hexdigest(binding_document)
        ):
            raise ValueError("Research Configuration Snapshot Context binding drifted")
        return ConfigurationSnapshotPublication(
            row["configuration_snapshot_id"],
            row["artifact_id"],
            row["configuration_fingerprint"],
            semantic,
            cast(dict[str, Any], row["provenance_document"]),
            cast(dict[str, Any], row["display_document"]),
            True,
            binding_document,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        snapshot_id: uuid.UUID,
        branch: RowMapping,
        direct_inputs: tuple[RowMapping, ...],
        fingerprint: str,
        semantic: dict[str, Any],
        provenance: dict[str, Any],
        display: dict[str, Any],
        execution_context_binding: _ConfigurationExecutionContextBinding | None,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO experiment.v022_research_configuration_snapshot (
                  configuration_snapshot_id,artifact_id,compiled_research_graph_id,
                  compiled_strategy_branch_id,configuration_fingerprint,
                  semantic_identity_document,provenance_document,display_document
                ) VALUES (:id,:artifact,:graph,:branch,:fingerprint,CAST(:semantic AS jsonb),
                          CAST(:provenance AS jsonb),CAST(:display AS jsonb))
                """
            ),
            {
                "id": snapshot_id,
                "artifact": artifact_id,
                "graph": branch["compiled_research_graph_id"],
                "branch": branch["compiled_strategy_branch_id"],
                "fingerprint": fingerprint,
                "semantic": _json(semantic),
                "provenance": _json(provenance),
                "display": _json(display),
            },
        )
        for item in direct_inputs:
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.v022_configuration_direct_input (
                      configuration_snapshot_id,ordinal,compiled_feature_occurrence_id,
                      display_document
                    ) VALUES (:snapshot,:ordinal,:occurrence,CAST(:display AS jsonb))
                    """
                ),
                {
                    "snapshot": snapshot_id,
                    "ordinal": item["ordinal"],
                    "occurrence": item["compiled_feature_occurrence_id"],
                    "display": _json(_input_display(item)),
                },
            )
        if execution_context_binding is not None:
            binding = execution_context_binding
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.v022_configuration_execution_context_binding (
                      configuration_snapshot_id,compiled_research_graph_id,
                      compiled_strategy_branch_id,compiled_execution_data_context_id,
                      execution_data_context_artifact_id,
                      execution_data_context_fingerprint,defense_version_id,
                      defense_package_artifact_id,timing_policy_version_id,
                      timing_policy_artifact_id,allocation_policy_version_id,
                      allocation_policy_artifact_id,
                      compiled_defense_execution_context_id,
                      defense_execution_context_artifact_id,
                      defense_execution_context_fingerprint,binding_document,
                      binding_fingerprint
                    ) VALUES (
                      :snapshot,:graph,:branch,:risk_context,:risk_artifact,
                      :risk_fingerprint,:defense,:package_artifact,:timing_version,
                      :timing_artifact,:allocation_version,:allocation_artifact,
                      :defense_context,:defense_context_artifact,
                      :defense_context_fingerprint,CAST(:document AS jsonb),:fingerprint
                    )
                    """
                ),
                {
                    "snapshot": snapshot_id,
                    "graph": binding.compiled_research_graph_id,
                    "branch": binding.compiled_strategy_branch_id,
                    "risk_context": binding.compiled_execution_data_context_id,
                    "risk_artifact": binding.execution_data_context_artifact_id,
                    "risk_fingerprint": binding.execution_data_context_fingerprint,
                    "defense": binding.defense_version_id,
                    "package_artifact": binding.defense_package_artifact_id,
                    "timing_version": binding.timing_policy_version_id,
                    "timing_artifact": binding.timing_policy_artifact_id,
                    "allocation_version": binding.allocation_policy_version_id,
                    "allocation_artifact": binding.allocation_policy_artifact_id,
                    "defense_context": binding.compiled_defense_execution_context_id,
                    "defense_context_artifact": (
                        binding.defense_execution_context_artifact_id
                    ),
                    "defense_context_fingerprint": (
                        binding.defense_execution_context_fingerprint
                    ),
                    "document": _json(binding.document),
                    "fingerprint": binding.fingerprint,
                },
            )


class CommonEvaluationPanelService:
    """Publish one exact ordered evaluation mask; never infer panel overlap at query time."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        *,
        evidence_class: EvidenceClass,
        observations: Sequence[PanelObservation],
        panel_document: dict[str, Any],
        dependencies: tuple[DependencyInput, ...] = (),
        evaluation_cohort_version_id: uuid.UUID | None = None,
        evaluation_cohort_fingerprint: str | None = None,
    ) -> CommonPanelPublication:
        members = tuple(observations)
        _validate_evidence_class(evidence_class)
        if not members:
            raise ValueError("Common Evaluation Panel cannot be empty")
        if any(not item.asset_key.strip() for item in members):
            raise ValueError("Common Evaluation Panel asset_key cannot be empty")
        if members != tuple(sorted(members)):
            raise ValueError("Common Evaluation Panel observations must use canonical order")
        if len(members) != len(set(members)):
            raise ValueError("Common Evaluation Panel observations must be unique")
        if (evaluation_cohort_version_id is None) != (
            evaluation_cohort_fingerprint is None
        ):
            raise ValueError("Evaluation Cohort identity must be complete")
        semantic = {
            "contract_version": "v0.22.0",
            "evidence_class": evidence_class,
            "panel_document": panel_document,
            "observations": [
                {
                    "decision_session": item.decision_session.isoformat(),
                    "asset_key": item.asset_key,
                }
                for item in members
            ],
        }
        if evaluation_cohort_version_id is not None:
            semantic["evaluation_cohort"] = {
                "evaluation_cohort_version_id": evaluation_cohort_version_id,
                "cohort_fingerprint": evaluation_cohort_fingerprint,
            }
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(fingerprint)
        if existing is not None:
            return existing
        panel_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:common-panel:{fingerprint}")
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_common_evaluation_panel",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=dependencies,
            reason="publish immutable v0.22 Common Evaluation Panel",
            draft_writer=partial(
                self._write,
                panel_id=panel_id,
                fingerprint=fingerprint,
                evidence_class=evidence_class,
                observations=members,
                panel_document=panel_document,
                evaluation_cohort_version_id=evaluation_cohort_version_id,
                evaluation_cohort_fingerprint=evaluation_cohort_fingerprint,
            ),
        )
        if publication.reused:
            frozen = self._existing(fingerprint)
            if frozen is None:
                raise ValueError("Reused Common Panel Artifact has no Panel row")
            return frozen
        return CommonPanelPublication(
            panel_id,
            publication.artifact_id,
            fingerprint,
            evidence_class,
            members,
            panel_document,
            False,
        )

    def existing_for_evaluation_cohort(
        self,
        *,
        evaluation_cohort_version_id: uuid.UUID,
        evaluation_cohort_fingerprint: str,
    ) -> CommonPanelPublication | None:
        """Resolve a published Cohort Panel without scanning its member rows."""

        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT panel.*,artifact.status "
                        "FROM experiment.v022_common_evaluation_panel panel "
                        "JOIN lineage.artifact artifact "
                        "ON artifact.artifact_id=panel.artifact_id "
                        "WHERE panel.evaluation_cohort_version_id=:cohort"
                    ),
                    {"cohort": evaluation_cohort_version_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("Common Evaluation Panel Artifact is not published")
        if row["evaluation_cohort_fingerprint"] != evaluation_cohort_fingerprint:
            raise ValueError("Common Evaluation Panel Cohort fingerprint differs")
        return CommonPanelPublication(
            row["common_evaluation_panel_id"],
            row["artifact_id"],
            row["panel_fingerprint"],
            cast(EvidenceClass, row["evidence_class"]),
            (),
            cast(dict[str, Any], row["panel_document"]),
            True,
        )

    def _existing(self, fingerprint: str) -> CommonPanelPublication | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT panel.*,artifact.status FROM experiment.v022_common_evaluation_panel panel "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id=panel.artifact_id "
                        "WHERE panel.panel_fingerprint=:fingerprint"
                    ),
                    {"fingerprint": fingerprint},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            observations = tuple(
                PanelObservation(item["decision_session"], item["asset_key"])
                for item in connection.execute(
                    text(
                        "SELECT decision_session,asset_key FROM "
                        "experiment.v022_common_evaluation_panel_member "
                        "WHERE common_evaluation_panel_id=:panel ORDER BY ordinal"
                    ),
                    {"panel": row["common_evaluation_panel_id"]},
                ).mappings()
            )
        if row["status"] != "published":
            raise ValueError("Common Evaluation Panel Artifact is not published")
        return CommonPanelPublication(
            row["common_evaluation_panel_id"],
            row["artifact_id"],
            row["panel_fingerprint"],
            cast(EvidenceClass, row["evidence_class"]),
            observations,
            cast(dict[str, Any], row["panel_document"]),
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        panel_id: uuid.UUID,
        fingerprint: str,
        evidence_class: EvidenceClass,
        observations: tuple[PanelObservation, ...],
        panel_document: dict[str, Any],
        evaluation_cohort_version_id: uuid.UUID | None,
        evaluation_cohort_fingerprint: str | None,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO experiment.v022_common_evaluation_panel (
                  common_evaluation_panel_id,artifact_id,panel_fingerprint,evidence_class,
                  observation_count,panel_document,evaluation_cohort_version_id,
                  evaluation_cohort_fingerprint
                ) VALUES (:id,:artifact,:fingerprint,:evidence_class,:count,
                          CAST(:document AS jsonb),:cohort,:cohort_fingerprint)
                """
            ),
            {
                "id": panel_id,
                "artifact": artifact_id,
                "fingerprint": fingerprint,
                "evidence_class": evidence_class,
                "count": len(observations),
                "document": _json(panel_document),
                "cohort": evaluation_cohort_version_id,
                "cohort_fingerprint": evaluation_cohort_fingerprint,
            },
        )
        member_statement = text(
            "INSERT INTO experiment.v022_common_evaluation_panel_member "
            "(common_evaluation_panel_id,ordinal,decision_session,asset_key) "
            "VALUES (:panel,:ordinal,:session,:asset)"
        )
        # A frozen S&P panel contains millions of members.  Preserve the exact
        # canonical ordinals while avoiding one database round-trip per row.
        batch_size = 10_000
        for start in range(0, len(observations), batch_size):
            batch = observations[start : start + batch_size]
            connection.execute(
                member_statement,
                [
                    {
                        "panel": panel_id,
                        "ordinal": start + offset,
                        "session": item.decision_session,
                        "asset": item.asset_key,
                    }
                    for offset, item in enumerate(batch)
                ],
            )


class ResultEvidenceService:
    """Bind selected configuration identity to exact resolved runtime evidence."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        *,
        result_artifact_id: uuid.UUID,
        configuration_snapshot_id: uuid.UUID,
        evidence_class: EvidenceClass,
        evidence_document: dict[str, Any],
        quality_document: dict[str, Any],
        common_evaluation_panel_id: uuid.UUID | None = None,
        runtime_dependencies: tuple[DependencyInput, ...] = (),
        evaluation_cohort_version_id: uuid.UUID | None = None,
        evaluation_cohort_fingerprint: str | None = None,
    ) -> ResultEvidencePublication:
        _validate_evidence_class(evidence_class)
        if (evaluation_cohort_version_id is None) != (
            evaluation_cohort_fingerprint is None
        ):
            raise ValueError("Evaluation Cohort identity must be complete")
        with self._engine.connect() as connection:
            result = _published_artifact(connection, result_artifact_id, "Result")
            configuration = _configuration_identity(connection, configuration_snapshot_id)
            panel = (
                _panel_identity(connection, common_evaluation_panel_id)
                if common_evaluation_panel_id is not None
                else None
            )
            runtime = tuple(
                (
                    item,
                    _published_artifact(connection, item.artifact_id, "Runtime dependency"),
                )
                for item in runtime_dependencies
            )
        if panel is not None and panel["evidence_class"] != evidence_class:
            raise ValueError("Result Evidence and Common Panel evidence classes differ")
        semantic = {
            "contract_version": "v0.22.0",
            "result_semantic_fingerprint": result["semantic_fingerprint"],
            "configuration_fingerprint": configuration["configuration_fingerprint"],
            "common_evaluation_panel_fingerprint": (
                panel["panel_fingerprint"] if panel is not None else None
            ),
            "evidence_class": evidence_class,
            "evidence_document": evidence_document,
            "quality_document": quality_document,
            "runtime_dependencies": [
                {
                    "role": item.role,
                    "ordinal": item.ordinal,
                    "semantic_fingerprint": artifact["semantic_fingerprint"],
                }
                for item, artifact in runtime
            ],
        }
        if evaluation_cohort_version_id is not None:
            semantic["evaluation_cohort"] = {
                "evaluation_cohort_version_id": evaluation_cohort_version_id,
                "cohort_fingerprint": evaluation_cohort_fingerprint,
            }
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(result_artifact_id)
        if existing is not None:
            if existing.evidence_fingerprint != fingerprint:
                raise ValueError("Result Artifact is already bound to different Evidence")
            return existing
        evidence_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:result-evidence:{fingerprint}")
        dependencies = (
            DependencyInput(result_artifact_id, "result", 0),
            DependencyInput(configuration["artifact_id"], "configuration", 0),
            *(
                (DependencyInput(panel["artifact_id"], "common_panel", 0),)
                if panel is not None
                else ()
            ),
            *runtime_dependencies,
        )
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_result_evidence_snapshot",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=dependencies,
            reason="publish immutable v0.22 Result Evidence Snapshot",
            draft_writer=partial(
                self._write,
                evidence_id=evidence_id,
                result_artifact_id=result_artifact_id,
                configuration_snapshot_id=configuration_snapshot_id,
                common_evaluation_panel_id=common_evaluation_panel_id,
                evidence_class=evidence_class,
                fingerprint=fingerprint,
                evidence_document=evidence_document,
                quality_document=quality_document,
                evaluation_cohort_version_id=evaluation_cohort_version_id,
                evaluation_cohort_fingerprint=evaluation_cohort_fingerprint,
            ),
        )
        if publication.reused:
            frozen = self._existing(result_artifact_id)
            if frozen is None:
                raise ValueError("Reused Result Evidence Artifact has no Evidence row")
            return frozen
        return ResultEvidencePublication(
            evidence_id,
            publication.artifact_id,
            fingerprint,
            result_artifact_id,
            configuration_snapshot_id,
            common_evaluation_panel_id,
            evidence_class,
            evidence_document,
            quality_document,
            False,
        )

    def _existing(self, result_artifact_id: uuid.UUID) -> ResultEvidencePublication | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT evidence.*,artifact.status FROM "
                        "experiment.v022_result_evidence_snapshot evidence "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id=evidence.artifact_id "
                        "WHERE evidence.result_artifact_id=:result"
                    ),
                    {"result": result_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("Result Evidence Snapshot Artifact is not published")
        return ResultEvidencePublication(
            row["result_evidence_snapshot_id"],
            row["artifact_id"],
            row["evidence_fingerprint"],
            row["result_artifact_id"],
            row["configuration_snapshot_id"],
            row["common_evaluation_panel_id"],
            cast(EvidenceClass, row["evidence_class"]),
            cast(dict[str, Any], row["evidence_document"]),
            cast(dict[str, Any], row["quality_document"]),
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        evidence_id: uuid.UUID,
        result_artifact_id: uuid.UUID,
        configuration_snapshot_id: uuid.UUID,
        common_evaluation_panel_id: uuid.UUID | None,
        evidence_class: EvidenceClass,
        fingerprint: str,
        evidence_document: dict[str, Any],
        quality_document: dict[str, Any],
        evaluation_cohort_version_id: uuid.UUID | None,
        evaluation_cohort_fingerprint: str | None,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO experiment.v022_result_evidence_snapshot (
                  result_evidence_snapshot_id,artifact_id,result_artifact_id,
                  configuration_snapshot_id,common_evaluation_panel_id,evidence_class,
                  evidence_fingerprint,evidence_document,quality_document,
                  evaluation_cohort_version_id,evaluation_cohort_fingerprint
                ) VALUES (:id,:artifact,:result,:configuration,:panel,:evidence_class,
                          :fingerprint,CAST(:evidence AS jsonb),CAST(:quality AS jsonb),
                          :cohort,:cohort_fingerprint)
                """
            ),
            {
                "id": evidence_id,
                "artifact": artifact_id,
                "result": result_artifact_id,
                "configuration": configuration_snapshot_id,
                "panel": common_evaluation_panel_id,
                "evidence_class": evidence_class,
                "fingerprint": fingerprint,
                "evidence": _json(evidence_document),
                "quality": _json(quality_document),
                "cohort": evaluation_cohort_version_id,
                "cohort_fingerprint": evaluation_cohort_fingerprint,
            },
        )


def _graph_uses_composed_defense(
    connection: Connection, graph_id: uuid.UUID
) -> bool:
    value = connection.scalar(
        text("SELECT experiment.v022_graph_uses_composed_defense(:graph)"),
        {"graph": graph_id},
    )
    if value is None:
        raise LookupError(f"Compiled Research Graph not found: {graph_id}")
    return bool(value)


def _configuration_execution_context_binding(
    connection: Connection,
    *,
    branch: RowMapping,
    composed: bool,
    compiled_execution_data_context_id: uuid.UUID | None,
    compiled_defense_execution_context_id: uuid.UUID | None,
) -> _ConfigurationExecutionContextBinding | None:
    if not composed:
        if (
            compiled_execution_data_context_id is not None
            or compiled_defense_execution_context_id is not None
        ):
            raise ValueError(
                "Legacy Configuration Snapshots must remain Execution Context free"
            )
        return None
    if compiled_execution_data_context_id is None:
        raise ValueError(
            "Composed Configuration Snapshot requires an exact Risk Execution Context"
        )

    graph_id = cast(uuid.UUID, branch["compiled_research_graph_id"])
    branch_id = cast(uuid.UUID, branch["compiled_strategy_branch_id"])
    risk = (
        connection.execute(
            text(
                """
                SELECT context.compiled_research_graph_id,context.artifact_id,
                       context.context_fingerprint,artifact.artifact_type,
                       artifact.status AS artifact_status
                  FROM workspace.v022_compiled_execution_data_context context
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=context.artifact_id
                 WHERE context.compiled_execution_data_context_id=:context
                """
            ),
            {"context": compiled_execution_data_context_id},
        )
        .mappings()
        .one_or_none()
    )
    if risk is None:
        raise LookupError(
            "Risk Execution Context not found: "
            f"{compiled_execution_data_context_id}"
        )
    if (
        risk["compiled_research_graph_id"] != graph_id
        or risk["artifact_type"] != "v022_compiled_execution_data_context"
        or risk["artifact_status"] != "published"
    ):
        raise ValueError(
            "Configuration Snapshot requires its exact published Risk Execution Context"
        )

    defense_version_id = cast(uuid.UUID | None, branch["defense_version_id"])
    defense: RowMapping | None = None
    if defense_version_id is None:
        if compiled_defense_execution_context_id is not None:
            raise ValueError(
                "No-defense Configuration Snapshot forbids a Defense Execution Context"
            )
    else:
        if compiled_defense_execution_context_id is None:
            raise ValueError(
                "Defended Configuration Snapshot requires an exact Defense Execution Context"
            )
        defense = (
            connection.execute(
                text(
                    """
                    SELECT context.compiled_execution_data_context_id,
                           context.defense_version_id,
                           context.defense_package_artifact_id,
                           context.timing_policy_version_id,
                           context.timing_policy_artifact_id,
                           context.allocation_policy_version_id,
                           context.allocation_policy_artifact_id,
                           context.artifact_id AS defense_context_artifact_id,
                           context.context_fingerprint,
                           context_artifact.artifact_type AS context_artifact_type,
                           context_artifact.status AS context_artifact_status,
                           package_artifact.status AS package_artifact_status,
                           timing_artifact.status AS timing_artifact_status,
                           allocation_artifact.status AS allocation_artifact_status
                      FROM defense.v022_compiled_defense_execution_context context
                      JOIN lineage.artifact context_artifact
                        ON context_artifact.artifact_id=context.artifact_id
                      JOIN lineage.artifact package_artifact
                        ON package_artifact.artifact_id=
                           context.defense_package_artifact_id
                      JOIN lineage.artifact timing_artifact
                        ON timing_artifact.artifact_id=context.timing_policy_artifact_id
                      JOIN lineage.artifact allocation_artifact
                        ON allocation_artifact.artifact_id=
                           context.allocation_policy_artifact_id
                     WHERE context.compiled_defense_execution_context_id=:context
                    """
                ),
                {"context": compiled_defense_execution_context_id},
            )
            .mappings()
            .one_or_none()
        )
        if defense is None:
            raise LookupError(
                "Defense Execution Context not found: "
                f"{compiled_defense_execution_context_id}"
            )
        if (
            defense["compiled_execution_data_context_id"]
            != compiled_execution_data_context_id
            or defense["defense_version_id"] != defense_version_id
            or defense["context_artifact_type"]
            != "v022_compiled_defense_execution_context"
            or defense["context_artifact_status"] != "published"
            or defense["package_artifact_status"] != "published"
            or defense["timing_artifact_status"] != "published"
            or defense["allocation_artifact_status"] != "published"
        ):
            raise ValueError(
                "Configuration Snapshot requires its exact published Defense Package "
                "and Execution Context"
            )

    defense_document: dict[str, Any] | None = None
    if defense is not None:
        defense_document = {
            "defense_version_id": str(defense_version_id),
            "package_artifact_id": str(defense["defense_package_artifact_id"]),
            "timing_policy_version_id": str(defense["timing_policy_version_id"]),
            "timing_policy_artifact_id": str(defense["timing_policy_artifact_id"]),
            "allocation_policy_version_id": str(
                defense["allocation_policy_version_id"]
            ),
            "allocation_policy_artifact_id": str(
                defense["allocation_policy_artifact_id"]
            ),
            "execution_context": {
                "compiled_defense_execution_context_id": str(
                    compiled_defense_execution_context_id
                ),
                "artifact_id": str(defense["defense_context_artifact_id"]),
                "context_fingerprint": defense["context_fingerprint"],
            },
        }
    document = {
        "contract_version": "v0.22.0",
        "compiled_research_graph_id": str(graph_id),
        "compiled_strategy_branch_id": str(branch_id),
        "risk_execution_context": {
            "compiled_execution_data_context_id": str(
                compiled_execution_data_context_id
            ),
            "artifact_id": str(risk["artifact_id"]),
            "context_fingerprint": risk["context_fingerprint"],
        },
        "defense": defense_document,
    }
    return _ConfigurationExecutionContextBinding(
        compiled_research_graph_id=graph_id,
        compiled_strategy_branch_id=branch_id,
        compiled_execution_data_context_id=compiled_execution_data_context_id,
        execution_data_context_artifact_id=cast(uuid.UUID, risk["artifact_id"]),
        execution_data_context_fingerprint=str(risk["context_fingerprint"]),
        defense_version_id=defense_version_id,
        defense_package_artifact_id=(
            None
            if defense is None
            else cast(uuid.UUID, defense["defense_package_artifact_id"])
        ),
        timing_policy_version_id=(
            None
            if defense is None
            else cast(uuid.UUID, defense["timing_policy_version_id"])
        ),
        timing_policy_artifact_id=(
            None
            if defense is None
            else cast(uuid.UUID, defense["timing_policy_artifact_id"])
        ),
        allocation_policy_version_id=(
            None
            if defense is None
            else cast(uuid.UUID, defense["allocation_policy_version_id"])
        ),
        allocation_policy_artifact_id=(
            None
            if defense is None
            else cast(uuid.UUID, defense["allocation_policy_artifact_id"])
        ),
        compiled_defense_execution_context_id=compiled_defense_execution_context_id,
        defense_execution_context_artifact_id=(
            None
            if defense is None
            else cast(uuid.UUID, defense["defense_context_artifact_id"])
        ),
        defense_execution_context_fingerprint=(
            None if defense is None else str(defense["context_fingerprint"])
        ),
        document=document,
        fingerprint=sha256_hexdigest(document),
    )


def _branch(connection: Connection, branch_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
                SELECT branch.*,graph.graph_fingerprint,graph.artifact_id AS graph_artifact_id,
                       graph.asset_context_fingerprint,graph.resolved_data_binding_fingerprint,
                       graph.frequency,aggregation.aggregation_version_id,
                       aggregation.parameter_preset_version_id,aggregation.target_version_id,
                       aggregation.training_preset_version_id,aggregation.instance_fingerprint,
                       av.version_number AS aggregation_version_number,av.execution_mode,
                       av.version_fingerprint AS aggregation_version_fingerprint,
                       af.family_key AS aggregation_family_key,af.name AS aggregation_name,
                       sv.version_number AS strategy_version_number,
                       sv.version_fingerprint AS strategy_version_fingerprint,
                       sf.family_key AS strategy_family_key,sf.name AS strategy_name,
                       svar.variant_key AS strategy_variant_key,svar.parameters AS strategy_parameters,
                       sv.schedule_policy,sv.execution_policy,
                       preset_binding.strategy_parameter_preset_version_id,
                       preset_binding.parameter_fingerprint AS strategy_parameter_fingerprint,
                       preset_binding.resolved_parameters AS resolved_strategy_parameters,
                       preset_version.version_number AS strategy_parameter_preset_version_number,
                       preset_version.artifact_id AS strategy_parameter_preset_artifact_id,
                       preset_definition.preset_key AS strategy_parameter_preset_key,
                       preset_definition.name AS strategy_parameter_preset_name,
                       dv.version_number AS defense_version_number,
                       dv.version_fingerprint AS defense_version_fingerprint,
                       df.family_key AS defense_family_key,df.name AS defense_name,
                       dvar.variant_key AS defense_variant_key,dvar.parameters AS defense_parameters,
                       dv.input_policy AS defense_input_policy,
                       dv.allocation_policy AS defense_allocation_policy
                  FROM strategy.v022_compiled_strategy_branch branch
                  JOIN workspace.compiled_research_graph graph
                    ON graph.compiled_research_graph_id=branch.compiled_research_graph_id
                  JOIN workspace.compiled_aggregation_instance aggregation
                    ON aggregation.compiled_aggregation_instance_id=branch.compiled_aggregation_instance_id
                  JOIN aggregation.aggregation_version av
                    ON av.aggregation_version_id=aggregation.aggregation_version_id
                  JOIN aggregation.aggregation_family af
                    ON af.aggregation_family_id=av.aggregation_family_id
                  JOIN strategy.v022_strategy_version sv
                    ON sv.strategy_version_id=branch.strategy_version_id
                  JOIN strategy.v022_strategy_variant svar
                    ON svar.strategy_variant_id=sv.strategy_variant_id
                  JOIN strategy.v022_strategy_family sf
                    ON sf.strategy_family_id=svar.strategy_family_id
                  LEFT JOIN strategy.v022_compiled_strategy_branch_preset_binding preset_binding
                    ON preset_binding.compiled_strategy_branch_id=
                       branch.compiled_strategy_branch_id
                  LEFT JOIN strategy.v022_strategy_parameter_preset_version preset_version
                    ON preset_version.strategy_parameter_preset_version_id=
                       preset_binding.strategy_parameter_preset_version_id
                  LEFT JOIN strategy.v022_strategy_parameter_preset_definition preset_definition
                    ON preset_definition.strategy_parameter_preset_definition_id=
                       preset_version.strategy_parameter_preset_definition_id
                  LEFT JOIN defense.defense_version dv
                    ON dv.defense_version_id=branch.defense_version_id
                  LEFT JOIN defense.defense_variant dvar
                    ON dvar.defense_variant_id=dv.defense_variant_id
                  LEFT JOIN defense.defense_family df
                    ON df.defense_family_id=dvar.defense_family_id
                 WHERE branch.compiled_strategy_branch_id=:branch
                """
            ),
            {"branch": branch_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"Compiled Strategy Branch not found: {branch_id}")
    return row


def _direct_inputs(connection: Connection, branch_id: uuid.UUID) -> tuple[RowMapping, ...]:
    return tuple(
        connection.execute(
            text(
                """
                SELECT input.ordinal,occurrence.compiled_feature_occurrence_id,
                       occurrence.feature_version_id,occurrence.stage_no,occurrence.production_kind,
                       occurrence.output_port_key,occurrence.source_occurrence_id,
                       fv.version_number,fv.origin_stage,fv.version_fingerprint,
                       variant.variant_key,variant.parameters,family.family_key,family.name,
                       contract.payload_contract_version_id,contract.version_number AS contract_version_number,
                       contract.schema_fingerprint,contract_family.contract_key
                  FROM strategy.v022_compiled_strategy_branch branch
                  JOIN workspace.compiled_aggregation_input input
                    ON input.compiled_aggregation_instance_id=branch.compiled_aggregation_instance_id
                  JOIN workspace.compiled_feature_occurrence occurrence
                    ON occurrence.compiled_feature_occurrence_id=input.compiled_feature_occurrence_id
                  JOIN processing.feature_version fv ON fv.feature_version_id=occurrence.feature_version_id
                  JOIN processing.feature_variant variant ON variant.feature_variant_id=fv.feature_variant_id
                  JOIN processing.feature_family family ON family.feature_family_id=variant.feature_family_id
                  JOIN data.payload_contract_version contract
                    ON contract.payload_contract_version_id=fv.payload_contract_version_id
                  JOIN data.payload_contract_family contract_family
                    ON contract_family.payload_contract_family_id=contract.payload_contract_family_id
                 WHERE branch.compiled_strategy_branch_id=:branch
                 ORDER BY input.ordinal
                """
            ),
            {"branch": branch_id},
        ).mappings()
    )


def _configuration_ensemble_binding(
    connection: Connection,
    branch: RowMapping,
) -> _ConfigurationEnsembleBinding | None:
    rows = tuple(
        connection.execute(
            text(
                """
                SELECT spec.ensemble_spec_id,spec.artifact_id,
                       spec.ensemble_fingerprint,spec.artifact_semantic_fingerprint,
                       spec.member_count,spec.target_group_count,spec.ensemble_document,
                       artifact.semantic_fingerprint AS artifact_fingerprint,
                       artifact.status AS artifact_status,
                       member.ordinal,member.target_group_ordinal,
                       member.member_ordinal_within_target,
                       target_definition.target_key,target_definition.name AS target_name,
                       target_version.version_number AS target_version_number,
                       target_version.semantics AS target_semantics,
                       training_definition.training_preset_key,
                       training_definition.name AS training_preset_name,
                       training_version.version_number AS training_preset_version_number,
                       training_version.semantics AS training_preset_semantics
                  FROM workspace.v022_compiled_trainable_ensemble_binding binding
                  JOIN aggregation.v022_trainable_ensemble_spec spec
                    ON spec.ensemble_spec_id=binding.ensemble_spec_id
                  JOIN lineage.artifact artifact ON artifact.artifact_id=spec.artifact_id
                  JOIN aggregation.v022_trainable_ensemble_member member
                    ON member.ensemble_spec_id=spec.ensemble_spec_id
                  JOIN aggregation.target_version target_version
                    ON target_version.target_version_id=member.target_version_id
                  JOIN aggregation.target_definition target_definition
                    ON target_definition.target_definition_id=
                       target_version.target_definition_id
                  JOIN aggregation.training_preset_version training_version
                    ON training_version.training_preset_version_id=
                       member.training_preset_version_id
                  JOIN aggregation.training_preset_definition training_definition
                    ON training_definition.training_preset_definition_id=
                       training_version.training_preset_definition_id
                 WHERE binding.compiled_aggregation_instance_id=:instance
                 ORDER BY member.ordinal
                """
            ),
            {"instance": branch["compiled_aggregation_instance_id"]},
        ).mappings()
    )
    if not rows:
        return None
    first = rows[0]
    if first["artifact_status"] != "published":
        raise ValueError("Trainable Ensemble Spec Artifact must be published")
    document = cast(dict[str, Any], first["ensemble_document"])
    if (
        first["ensemble_fingerprint"] != sha256_hexdigest(document)
        or first["artifact_semantic_fingerprint"] != first["artifact_fingerprint"]
    ):
        raise ValueError("Trainable Ensemble Spec identity drifted")
    if (
        len(rows) != first["member_count"]
        or len({row["target_group_ordinal"] for row in rows})
        != first["target_group_count"]
        or tuple(row["ordinal"] for row in rows) != tuple(range(len(rows)))
    ):
        raise ValueError("Trainable Ensemble Spec member closure is incomplete")
    semantic = {
        "ensemble_spec_id": str(first["ensemble_spec_id"]),
        "artifact_id": str(first["artifact_id"]),
        "ensemble_fingerprint": first["ensemble_fingerprint"],
        "specification": document,
    }
    display_groups: list[dict[str, Any]] = []
    for target_ordinal in range(first["target_group_count"]):
        members = tuple(
            row for row in rows if row["target_group_ordinal"] == target_ordinal
        )
        if tuple(row["member_ordinal_within_target"] for row in members) != tuple(
            range(len(members))
        ):
            raise ValueError("Trainable Ensemble Target member order is incomplete")
        target = members[0]
        display_groups.append(
            {
                "target_key": target["target_key"],
                "target_name": target["target_name"],
                "target_version_number": target["target_version_number"],
                "target_semantics": target["target_semantics"],
                "members": [
                    {
                        "training_preset_key": row["training_preset_key"],
                        "training_preset_name": row["training_preset_name"],
                        "training_preset_version_number": row[
                            "training_preset_version_number"
                        ],
                        "training_preset_semantics": row[
                            "training_preset_semantics"
                        ],
                    }
                    for row in members
                ],
            }
        )
    return _ConfigurationEnsembleBinding(
        artifact_id=cast(uuid.UUID, first["artifact_id"]),
        semantic_document=semantic,
        display_document={
            "ensemble_fingerprint": first["ensemble_fingerprint"],
            "member_count": first["member_count"],
            "target_group_count": first["target_group_count"],
            "combination_policy": document["combination_policy"],
            "target_groups": display_groups,
        },
    )


def _semantic_document(
    branch: RowMapping,
    inputs: tuple[RowMapping, ...],
    execution_policy: dict[str, Any],
    *,
    ensemble: _ConfigurationEnsembleBinding | None = None,
) -> dict[str, Any]:
    strategy = {
        "family_key": branch["strategy_family_key"],
        "variant_key": branch["strategy_variant_key"],
        "version_id": str(branch["strategy_version_id"]),
        "version_fingerprint": branch["strategy_version_fingerprint"],
        "parameters": branch["strategy_parameters"],
        "schedule_policy": branch["schedule_policy"],
        "execution_policy": branch["execution_policy"],
    }
    if branch["strategy_parameter_preset_version_id"] is not None:
        strategy["variant_parameter_domain"] = strategy.pop("parameters")
        strategy["parameter_preset"] = {
            "preset_key": branch["strategy_parameter_preset_key"],
            "version_id": str(branch["strategy_parameter_preset_version_id"]),
            "version_number": branch[
                "strategy_parameter_preset_version_number"
            ],
            "parameter_fingerprint": branch["strategy_parameter_fingerprint"],
            "resolved_parameters": branch["resolved_strategy_parameters"],
        }
    aggregation: dict[str, Any] = {
        "family_key": branch["aggregation_family_key"],
        "version_id": str(branch["aggregation_version_id"]),
        "version_fingerprint": branch["aggregation_version_fingerprint"],
        "instance_fingerprint": branch["instance_fingerprint"],
        "execution_mode": branch["execution_mode"],
        "parameter_preset_version_id": _uuid(branch["parameter_preset_version_id"]),
        "target_version_id": _uuid(branch["target_version_id"]),
        "training_preset_version_id": _uuid(branch["training_preset_version_id"]),
    }
    if ensemble is not None:
        aggregation["trainable_ensemble"] = ensemble.semantic_document
    return {
        "contract_version": "v0.22.0",
        "compiled_graph_fingerprint": branch["graph_fingerprint"],
        "asset_context_fingerprint": branch["asset_context_fingerprint"],
        "resolved_data_binding_fingerprint": branch["resolved_data_binding_fingerprint"],
        "frequency": branch["frequency"],
        "aggregation": aggregation,
        "direct_inputs": [_input_semantic(item) for item in inputs],
        "strategy": strategy,
        "defense": (
            None
            if branch["defense_version_id"] is None
            else {
                "family_key": branch["defense_family_key"],
                "variant_key": branch["defense_variant_key"],
                "version_id": str(branch["defense_version_id"]),
                "version_fingerprint": branch["defense_version_fingerprint"],
                "parameters": branch["defense_parameters"],
                "input_policy": branch["defense_input_policy"],
                "allocation_policy": branch["defense_allocation_policy"],
            }
        ),
        "execution_policy": execution_policy,
    }


def _display_document(
    branch: RowMapping,
    inputs: tuple[RowMapping, ...],
    *,
    ensemble: _ConfigurationEnsembleBinding | None = None,
) -> dict[str, Any]:
    strategy = {
        "name": branch["strategy_name"],
        "family_key": branch["strategy_family_key"],
        "variant_key": branch["strategy_variant_key"],
        "version_number": branch["strategy_version_number"],
    }
    if branch["strategy_parameter_preset_version_id"] is not None:
        strategy["parameter_preset"] = {
            "name": branch["strategy_parameter_preset_name"],
            "preset_key": branch["strategy_parameter_preset_key"],
            "version_number": branch[
                "strategy_parameter_preset_version_number"
            ],
            "parameters": branch["resolved_strategy_parameters"],
        }
    aggregation: dict[str, Any] = {
        "name": branch["aggregation_name"],
        "family_key": branch["aggregation_family_key"],
        "version_number": branch["aggregation_version_number"],
    }
    if ensemble is not None:
        aggregation["trainable_ensemble"] = ensemble.display_document
    return {
        "aggregation": aggregation,
        "direct_inputs": [_input_display(item) for item in inputs],
        "strategy": strategy,
        "defense": (
            {"name": "No defense", "none": True}
            if branch["defense_version_id"] is None
            else {
                "name": branch["defense_name"],
                "family_key": branch["defense_family_key"],
                "variant_key": branch["defense_variant_key"],
                "version_number": branch["defense_version_number"],
                "none": False,
            }
        ),
    }


def _input_semantic(item: RowMapping) -> dict[str, Any]:
    return {
        "ordinal": item["ordinal"],
        "compiled_feature_occurrence_id": str(item["compiled_feature_occurrence_id"]),
        "feature_version_id": str(item["feature_version_id"]),
        "feature_version_fingerprint": item["version_fingerprint"],
        "family_key": item["family_key"],
        "variant_key": item["variant_key"],
        "stage_no": item["stage_no"],
        "origin_stage": item["origin_stage"],
        "production_kind": item["production_kind"],
        "output_port_key": item["output_port_key"],
        "source_occurrence_id": _uuid(item["source_occurrence_id"]),
        "payload_contract_version_id": str(item["payload_contract_version_id"]),
        "payload_contract_fingerprint": item["schema_fingerprint"],
    }


def _input_display(item: RowMapping) -> dict[str, Any]:
    return {
        "name": item["name"],
        "family_key": item["family_key"],
        "variant_key": item["variant_key"],
        "parameters": item["parameters"],
        "feature_version_number": item["version_number"],
        "payload_contract_key": item["contract_key"],
        "payload_contract_version": item["contract_version_number"],
    }


def _published_artifact(
    connection: Connection,
    artifact_id: uuid.UUID,
    label: str,
) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT artifact_id,status,semantic_fingerprint,content_hash "
                "FROM lineage.artifact WHERE artifact_id=:artifact"
            ),
            {"artifact": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"{label} Artifact not found: {artifact_id}")
    if row["status"] != "published":
        raise ValueError(f"{label} Artifact must be published")
    return row


def _configuration_identity(connection: Connection, snapshot_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT snapshot.configuration_snapshot_id,snapshot.artifact_id,"
                "snapshot.configuration_fingerprint,artifact.status "
                "FROM experiment.v022_research_configuration_snapshot snapshot "
                "JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id "
                "WHERE snapshot.configuration_snapshot_id=:snapshot"
            ),
            {"snapshot": snapshot_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"Research Configuration Snapshot not found: {snapshot_id}")
    if row["status"] != "published":
        raise ValueError("Research Configuration Snapshot must be published")
    return row


def _panel_identity(connection: Connection, panel_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT panel.common_evaluation_panel_id,panel.artifact_id,"
                "panel.panel_fingerprint,panel.evidence_class,artifact.status "
                "FROM experiment.v022_common_evaluation_panel panel "
                "JOIN lineage.artifact artifact ON artifact.artifact_id=panel.artifact_id "
                "WHERE panel.common_evaluation_panel_id=:panel"
            ),
            {"panel": panel_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"Common Evaluation Panel not found: {panel_id}")
    if row["status"] != "published":
        raise ValueError("Common Evaluation Panel must be published")
    return row


def _validate_evidence_class(value: str) -> None:
    if value not in {
        "walk_forward_backtest",
        "locked_historical_test",
        "prospective_oos",
    }:
        raise ValueError(f"Unsupported Evidence Class: {value}")


def _uuid(value: object) -> str | None:
    return str(value) if value is not None else None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
