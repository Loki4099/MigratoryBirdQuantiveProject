from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.experiment_identity import (
    ConfigurationSnapshotPublication,
    ConfigurationSnapshotService,
)

CONTRACT_VERSION = "v0.22.0"
EXPLORATORY_POLICY_KEY = "v022_exploratory_baseline"
EXPLORATORY_POLICY_VERSION = 1

EXPLORATORY_EXECUTION_POLICY: dict[str, Any] = {
    "contract_version": CONTRACT_VERSION,
    "policy_key": "v022_exploratory_execution_v1",
    "decision_timing": "after_scheduled_close",
    "execution_timing": "next_scheduled_open",
    "initialization_policy": "fresh_start",
    "initial_capital_usd": "100000000.00",
    "cost_policy": {
        "policy_key": "linear_10bps_per_side_v1",
        "basis_points_per_side": "10",
    },
    "missing_policy": "fail_closed",
}

EXPLORATORY_CONTEXTS: tuple[tuple[str, dict[str, Any], dict[str, Any]], ...] = (
    (
        "full_common_history_spy_v1",
        {
            "contract_version": CONTRACT_VERSION,
            "context_key": "full_common_history_spy_v1",
            "evaluation_window": {"policy": "full_common_history"},
            "benchmark": {"family_key": "spy_buy_and_hold", "version_number": 1},
            "metric_catalog": {"catalog_key": "v022_exploratory_core", "version_number": 1},
            "execution_policy": EXPLORATORY_EXECUTION_POLICY,
        },
        {
            "name": "Full common history / SPY / 10 bps per side",
            "window": "Full common history",
            "benchmark": "SPY buy-and-hold v1",
            "cost": "10 bps per side",
        },
    ),
)


@dataclass(frozen=True, slots=True)
class EvaluationContextIdentity:
    ordinal: int
    context_key: str
    context_fingerprint: str
    semantic_document: dict[str, Any]
    display_document: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EvaluationMatrixPublication:
    evaluation_matrix_policy_id: uuid.UUID
    artifact_id: uuid.UUID
    policy_fingerprint: str
    contexts: tuple[EvaluationContextIdentity, ...]
    reused: bool


@dataclass(frozen=True, slots=True)
class GraphSuiteIdentityPublication:
    research_suite_id: uuid.UUID
    suite_artifact_id: uuid.UUID
    compiled_research_graph_id: uuid.UUID
    suite_fingerprint: str
    graph_fingerprint: str
    evaluation_matrix_policy_id: uuid.UUID
    strategy_branch_count: int
    backtest_cell_count: int
    suite_mode: Literal["exploratory"]
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "research_suite_id": str(self.research_suite_id),
            "suite_artifact_id": str(self.suite_artifact_id),
            "compiled_research_graph_id": str(self.compiled_research_graph_id),
            "suite_fingerprint": self.suite_fingerprint,
            "graph_fingerprint": self.graph_fingerprint,
            "evaluation_matrix_policy_id": str(self.evaluation_matrix_policy_id),
            "strategy_branch_count": self.strategy_branch_count,
            "backtest_cell_count": self.backtest_cell_count,
            "suite_mode": self.suite_mode,
            "reused": self.reused,
        }


class EvaluationMatrixPolicyService:
    """Publish the server-owned first-slice evaluation matrix as immutable identity."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish_exploratory_baseline(self) -> EvaluationMatrixPublication:
        contexts = tuple(
            EvaluationContextIdentity(
                ordinal,
                key,
                sha256_hexdigest(semantic),
                semantic,
                display,
            )
            for ordinal, (key, semantic, display) in enumerate(EXPLORATORY_CONTEXTS)
        )
        semantic = {
            "contract_version": CONTRACT_VERSION,
            "policy_key": EXPLORATORY_POLICY_KEY,
            "version_number": EXPLORATORY_POLICY_VERSION,
            "suite_mode": "exploratory",
            "contexts": [
                {
                    "ordinal": item.ordinal,
                    "context_key": item.context_key,
                    "context_fingerprint": item.context_fingerprint,
                    "semantic_document": item.semantic_document,
                }
                for item in contexts
            ],
        }
        fingerprint = _artifact_semantic_fingerprint(
            artifact_type="v022_evaluation_matrix_policy",
            artifact_key=EXPLORATORY_POLICY_KEY,
            version_number=EXPLORATORY_POLICY_VERSION,
            semantic_payload=semantic,
            dependency_rows=(),
        )
        policy_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:evaluation-matrix:{fingerprint}"
        )

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.v022_evaluation_matrix_policy (
                      evaluation_matrix_policy_id,artifact_id,policy_key,version_number,
                      contract_version,suite_mode,context_count,policy_document,
                      policy_fingerprint
                    ) VALUES (
                      :id,:artifact,:key,:version,:contract,'exploratory',:count,
                      CAST(:document AS jsonb),:fingerprint
                    )
                    """
                ),
                {
                    "id": policy_id,
                    "artifact": artifact_id,
                    "key": EXPLORATORY_POLICY_KEY,
                    "version": EXPLORATORY_POLICY_VERSION,
                    "contract": CONTRACT_VERSION,
                    "count": len(contexts),
                    "document": _json(semantic),
                    "fingerprint": fingerprint,
                },
            )
            for item in contexts:
                connection.execute(
                    text(
                        """
                        INSERT INTO experiment.v022_evaluation_matrix_policy_context (
                          evaluation_matrix_policy_id,ordinal,context_key,
                          context_fingerprint,semantic_context_document,display_document
                        ) VALUES (
                          :policy,:ordinal,:key,:fingerprint,CAST(:semantic AS jsonb),
                          CAST(:display AS jsonb)
                        )
                        """
                    ),
                    {
                        "policy": policy_id,
                        "ordinal": item.ordinal,
                        "key": item.context_key,
                        "fingerprint": item.context_fingerprint,
                        "semantic": _json(item.semantic_document),
                        "display": _json(item.display_document),
                    },
                )

        publication = self._artifacts.publish(
            artifact_type="v022_evaluation_matrix_policy",
            artifact_key=EXPLORATORY_POLICY_KEY,
            version_number=EXPLORATORY_POLICY_VERSION,
            semantic_payload=semantic,
            content_payload={
                "semantic_identity_document": semantic,
                "display_document": {
                    "name": "v0.22 exploratory baseline",
                    "contexts": [item.display_document for item in contexts],
                },
            },
            reason="publish v0.22 exploratory evaluation matrix policy",
            draft_writer=write,
        )
        if publication.semantic_fingerprint != fingerprint:
            raise ValueError("Evaluation Matrix Policy fingerprint calculation drifted")
        with self._engine.connect() as connection:
            stored_id = connection.scalar(
                text(
                    "SELECT evaluation_matrix_policy_id "
                    "FROM experiment.v022_evaluation_matrix_policy WHERE artifact_id=:artifact"
                ),
                {"artifact": publication.artifact_id},
            )
        if stored_id != policy_id:
            raise ValueError("Evaluation Matrix Policy identity collision")
        return EvaluationMatrixPublication(
            policy_id,
            publication.artifact_id,
            fingerprint,
            contexts,
            publication.reused,
        )


class GraphSuiteIdentityService:
    """Atomically publish one full-Graph Suite/Branch/Cell identity expansion.

    This service deliberately does not create a Graph Run. The public Suite command must
    remain disabled until Strategy, Defense, and Portfolio Cell runtime can be enqueued.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)
        self._configurations = ConfigurationSnapshotService(engine)
        self._policies = EvaluationMatrixPolicyService(engine)

    def publish(
        self,
        *,
        compiled_research_graph_id: uuid.UUID,
        submission_key: uuid.UUID,
        actor_key: str,
        suite_mode: Literal["exploratory"] = "exploratory",
    ) -> GraphSuiteIdentityPublication:
        if suite_mode != "exploratory":
            raise ValueError("v0.22.0 first-slice Suite identity supports exploratory mode only")
        if not actor_key.strip():
            raise ValueError("Suite actor key is required")
        command_scope = sha256_hexdigest(
            {"actor_key": actor_key, "submission_key": str(submission_key)}
        )
        suite_key = f"v022_graph_suite__{command_scope}"
        with self._engine.connect() as connection:
            existing = _existing_submission(connection, suite_key)
            if existing is not None:
                if (
                    existing["compiled_research_graph_id"]
                    != compiled_research_graph_id
                    or existing["owner_key"] != actor_key
                    or existing["suite_mode"] != suite_mode
                ):
                    raise ValueError(
                        "Artifact identity already exists with different semantics"
                    )
                if existing["artifact_status"] != "published":
                    raise ValueError("Existing Research Suite Artifact is not published")
                return GraphSuiteIdentityPublication(
                    existing["research_suite_id"],
                    existing["artifact_id"],
                    compiled_research_graph_id,
                    existing["suite_fingerprint"],
                    existing["graph_fingerprint"],
                    existing["evaluation_matrix_policy_id"],
                    existing["branch_count"],
                    existing["cell_count"],
                    "exploratory",
                    True,
                )
            graph = _graph(connection, compiled_research_graph_id)
            branches = _branches(connection, compiled_research_graph_id)
            risk_context_id, defense_context_ids = _suite_execution_contexts(
                connection,
                graph_id=compiled_research_graph_id,
                branches=branches,
            )
        if int(graph["strategy_branch_count"]) != len(branches) or not branches:
            raise ValueError("Compiled Graph branch projection is incomplete")

        policy = self._policies.publish_exploratory_baseline()
        configurations = tuple(
            self._configurations.publish(
                compiled_strategy_branch_id=cast(uuid.UUID, branch["compiled_strategy_branch_id"]),
                execution_policy_document=EXPLORATORY_EXECUTION_POLICY,
                provenance_document={
                    "source": "v022_graph_suite_identity",
                    "compiled_research_graph_id": str(compiled_research_graph_id),
                    "compiled_strategy_branch_id": str(branch["compiled_strategy_branch_id"]),
                },
                compiled_execution_data_context_id=risk_context_id,
                compiled_defense_execution_context_id=defense_context_ids[
                    cast(uuid.UUID, branch["compiled_strategy_branch_id"])
                ],
            )
            for branch in branches
        )
        branch_semantics = [
            {
                "ordinal": ordinal,
                "compiled_branch_fingerprint": branch["branch_fingerprint"],
                "configuration_fingerprint": configuration.configuration_fingerprint,
            }
            for ordinal, (branch, configuration) in enumerate(
                zip(branches, configurations, strict=True)
            )
        ]
        semantic = {
            "contract_version": CONTRACT_VERSION,
            "compiled_graph_fingerprint": graph["graph_fingerprint"],
            "evaluation_matrix_policy_fingerprint": policy.policy_fingerprint,
            "execution_policy": EXPLORATORY_EXECUTION_POLICY,
            "suite_mode": suite_mode,
            "branches": branch_semantics,
            "cell_context_fingerprints": [
                item.context_fingerprint for item in policy.contexts
            ],
        }
        dependencies = (
            DependencyInput(cast(uuid.UUID, graph["artifact_id"]), "compiled_graph", 0),
            DependencyInput(policy.artifact_id, "evaluation_matrix_policy", 0),
            *tuple(
                DependencyInput(item.artifact_id, "configuration_snapshot", ordinal)
                for ordinal, item in enumerate(configurations)
            ),
        )
        with self._engine.connect() as connection:
            dependency_rows = tuple(
                _published_dependency(connection, item.artifact_id) for item in dependencies
            )
        suite_fingerprint = _artifact_semantic_fingerprint(
            artifact_type="v022_research_suite",
            artifact_key=suite_key,
            version_number=1,
            semantic_payload=semantic,
            dependency_rows=tuple(
                (dependency.role, dependency.ordinal, row["semantic_fingerprint"])
                for dependency, row in zip(dependencies, dependency_rows, strict=True)
            ),
        )
        suite_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:research-suite:{suite_fingerprint}"
        )
        cell_count = len(branches) * len(policy.contexts)
        provenance = {
            "submission_key": str(submission_key),
            "submitted_by": actor_key,
            "compiled_research_graph_id": str(compiled_research_graph_id),
        }

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.v022_research_suite (
                      research_suite_id,artifact_id,compiled_research_graph_id,
                      evaluation_matrix_policy_id,contract_version,suite_key,
                      suite_fingerprint,suite_mode,execution_policy_document,
                      provenance_document,branch_count,cell_count,owner_key,created_by
                    ) VALUES (
                      :id,:artifact,:graph,:policy,:contract,:key,:fingerprint,
                      'exploratory',CAST(:execution AS jsonb),CAST(:provenance AS jsonb),
                      :branches,:cells,:owner,:actor
                    )
                    """
                ),
                {
                    "id": suite_id,
                    "artifact": artifact_id,
                    "graph": compiled_research_graph_id,
                    "policy": policy.evaluation_matrix_policy_id,
                    "contract": CONTRACT_VERSION,
                    "key": suite_key,
                    "fingerprint": suite_fingerprint,
                    "execution": _json(EXPLORATORY_EXECUTION_POLICY),
                    "provenance": _json(provenance),
                    "branches": len(branches),
                    "cells": cell_count,
                    "owner": actor_key,
                    "actor": actor_key,
                },
            )
            for branch_ordinal, (branch, configuration) in enumerate(
                zip(branches, configurations, strict=True)
            ):
                branch_fingerprint = sha256_hexdigest(
                    {
                        "suite_fingerprint": suite_fingerprint,
                        "compiled_branch_fingerprint": branch["branch_fingerprint"],
                        "configuration_fingerprint": configuration.configuration_fingerprint,
                    }
                )
                suite_branch_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"bird:v0.22:research-suite-branch:{branch_fingerprint}",
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO experiment.v022_research_suite_branch (
                          research_suite_branch_id,research_suite_id,
                          compiled_research_graph_id,compiled_strategy_branch_id,
                          configuration_snapshot_id,ordinal,branch_key,
                          branch_fingerprint,provenance_document
                        ) VALUES (
                          :id,:suite,:graph,:branch,:snapshot,:ordinal,:key,
                          :fingerprint,CAST(:provenance AS jsonb)
                        )
                        """
                    ),
                    {
                        "id": suite_branch_id,
                        "suite": suite_id,
                        "graph": compiled_research_graph_id,
                        "branch": branch["compiled_strategy_branch_id"],
                        "snapshot": configuration.configuration_snapshot_id,
                        "ordinal": branch_ordinal,
                        "key": branch["branch_key"],
                        "fingerprint": branch_fingerprint,
                        "provenance": _json(
                            {
                                "compiled_strategy_branch_id": str(
                                    branch["compiled_strategy_branch_id"]
                                ),
                                "configuration_snapshot_id": str(
                                    configuration.configuration_snapshot_id
                                ),
                            }
                        ),
                    },
                )
                self._write_cells(
                    connection,
                    suite_id=suite_id,
                    suite_branch_id=suite_branch_id,
                    compiled_research_graph_id=compiled_research_graph_id,
                    branch=branch,
                    configuration=configuration,
                    branch_ordinal=branch_ordinal,
                    branch_fingerprint=branch_fingerprint,
                    suite_fingerprint=suite_fingerprint,
                    policy=policy,
                )

        publication = self._artifacts.publish(
            artifact_type="v022_research_suite",
            artifact_key=suite_key,
            version_number=1,
            semantic_payload=semantic,
            content_payload={
                "semantic_identity_document": semantic,
                "provenance_document": provenance,
                "display_document": {
                    "graph_fingerprint": graph["graph_fingerprint"],
                    "strategy_branch_count": len(branches),
                    "backtest_cell_count": cell_count,
                },
            },
            dependencies=dependencies,
            reason="publish immutable v0.22 Research Suite identity",
            draft_writer=write,
        )
        if publication.semantic_fingerprint != suite_fingerprint:
            raise ValueError("Research Suite fingerprint calculation drifted")
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT research_suite_id,branch_count,cell_count "
                    "FROM experiment.v022_research_suite WHERE artifact_id=:artifact"
                ),
                {"artifact": publication.artifact_id},
            ).one()
        if row.research_suite_id != suite_id:
            raise ValueError("Research Suite identity collision")
        return GraphSuiteIdentityPublication(
            suite_id,
            publication.artifact_id,
            compiled_research_graph_id,
            suite_fingerprint,
            str(graph["graph_fingerprint"]),
            policy.evaluation_matrix_policy_id,
            int(row.branch_count),
            int(row.cell_count),
            "exploratory",
            publication.reused,
        )

    @staticmethod
    def _write_cells(
        connection: Connection,
        *,
        suite_id: uuid.UUID,
        suite_branch_id: uuid.UUID,
        compiled_research_graph_id: uuid.UUID,
        branch: RowMapping,
        configuration: ConfigurationSnapshotPublication,
        branch_ordinal: int,
        branch_fingerprint: str,
        suite_fingerprint: str,
        policy: EvaluationMatrixPublication,
    ) -> None:
        for context in policy.contexts:
            cell_fingerprint = sha256_hexdigest(
                {
                    "suite_fingerprint": suite_fingerprint,
                    "branch_fingerprint": branch_fingerprint,
                    "evaluation_context_fingerprint": context.context_fingerprint,
                }
            )
            cell_id = uuid.uuid5(
                uuid.NAMESPACE_URL, f"bird:v0.22:research-cell:{cell_fingerprint}"
            )
            cell_ordinal = branch_ordinal * len(policy.contexts) + context.ordinal
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.v022_research_cell (
                      research_cell_id,research_suite_id,research_suite_branch_id,
                      compiled_research_graph_id,compiled_strategy_branch_id,
                      configuration_snapshot_id,evaluation_matrix_policy_id,
                      evaluation_context_ordinal,ordinal,cell_key,
                      evaluation_context_fingerprint,cell_fingerprint
                    ) VALUES (
                      :id,:suite,:suite_branch,:graph,:branch,:snapshot,:policy,
                      :context_ordinal,:ordinal,:key,:context_fingerprint,:fingerprint
                    )
                    """
                ),
                {
                    "id": cell_id,
                    "suite": suite_id,
                    "suite_branch": suite_branch_id,
                    "graph": compiled_research_graph_id,
                    "branch": branch["compiled_strategy_branch_id"],
                    "snapshot": configuration.configuration_snapshot_id,
                    "policy": policy.evaluation_matrix_policy_id,
                    "context_ordinal": context.ordinal,
                    "ordinal": cell_ordinal,
                    "key": f"{branch['branch_key']}__{context.context_key}",
                    "context_fingerprint": context.context_fingerprint,
                    "fingerprint": cell_fingerprint,
                },
            )


def _graph(connection: Connection, graph_id: uuid.UUID) -> RowMapping:
    row = connection.execute(
        text(
            """
            SELECT graph.*,artifact.status AS artifact_status
              FROM workspace.compiled_research_graph graph
              JOIN lineage.artifact artifact ON artifact.artifact_id=graph.artifact_id
             WHERE graph.compiled_research_graph_id=:graph
            """
        ),
        {"graph": graph_id},
    ).mappings().one_or_none()
    if row is None:
        raise LookupError(f"Compiled Research Graph not found: {graph_id}")
    if row["contract_version"] != CONTRACT_VERSION or row["artifact_status"] != "published":
        raise ValueError("Research Suite requires an immutable published v0.22.0 Graph")
    return row


def _existing_submission(connection: Connection, suite_key: str) -> RowMapping | None:
    return (
        connection.execute(
            text(
                """
                SELECT suite.research_suite_id,suite.artifact_id,
                       suite.compiled_research_graph_id,suite.suite_fingerprint,
                       suite.evaluation_matrix_policy_id,suite.branch_count,
                       suite.cell_count,suite.suite_mode,suite.owner_key,
                       graph.graph_fingerprint,artifact.status AS artifact_status
                  FROM experiment.v022_research_suite suite
                  JOIN workspace.compiled_research_graph graph
                    ON graph.compiled_research_graph_id=suite.compiled_research_graph_id
                  JOIN lineage.artifact artifact ON artifact.artifact_id=suite.artifact_id
                 WHERE suite.suite_key=:suite_key
                """
            ),
            {"suite_key": suite_key},
        )
        .mappings()
        .one_or_none()
    )


def _branches(connection: Connection, graph_id: uuid.UUID) -> tuple[RowMapping, ...]:
    return tuple(
        connection.execute(
            text(
                """
                SELECT branch.compiled_strategy_branch_id,branch.branch_key,
                       branch.branch_fingerprint,branch.defense_version_id
                  FROM strategy.v022_compiled_strategy_branch branch
                  JOIN strategy.v022_compiled_strategy_branch_preset_binding preset
                    ON preset.compiled_strategy_branch_id=
                       branch.compiled_strategy_branch_id
                 WHERE branch.compiled_research_graph_id=:graph
                 ORDER BY branch.branch_key
                """
            ),
            {"graph": graph_id},
        ).mappings()
    )


def _suite_execution_contexts(
    connection: Connection,
    *,
    graph_id: uuid.UUID,
    branches: tuple[RowMapping, ...],
) -> tuple[uuid.UUID | None, dict[uuid.UUID, uuid.UUID | None]]:
    composed = connection.scalar(
        text("SELECT experiment.v022_graph_uses_composed_defense(:graph)"),
        {"graph": graph_id},
    )
    if not bool(composed):
        return None, {
            cast(uuid.UUID, branch["compiled_strategy_branch_id"]): None
            for branch in branches
        }

    risk_rows = (
        connection.execute(
            text(
                """
                SELECT context.compiled_execution_data_context_id,
                       artifact.artifact_type,artifact.status AS artifact_status
                  FROM workspace.v022_compiled_execution_data_context context
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=context.artifact_id
                 WHERE context.compiled_research_graph_id=:graph
                """
            ),
            {"graph": graph_id},
        )
        .mappings()
        .all()
    )
    if len(risk_rows) != 1:
        raise ValueError(
            "Composed Research Suite requires exactly one Graph Risk Execution Context"
        )
    risk = risk_rows[0]
    if (
        risk["artifact_type"] != "v022_compiled_execution_data_context"
        or risk["artifact_status"] != "published"
    ):
        raise ValueError(
            "Composed Research Suite requires its exact published Risk Execution Context"
        )
    risk_context_id = cast(uuid.UUID, risk["compiled_execution_data_context_id"])

    result: dict[uuid.UUID, uuid.UUID | None] = {}
    for branch in branches:
        branch_id = cast(uuid.UUID, branch["compiled_strategy_branch_id"])
        defense_version_id = cast(uuid.UUID | None, branch["defense_version_id"])
        if defense_version_id is None:
            result[branch_id] = None
            continue
        defense_rows = (
            connection.execute(
                text(
                    """
                    SELECT context.compiled_defense_execution_context_id,
                           artifact.artifact_type,artifact.status AS artifact_status
                      FROM defense.v022_compiled_defense_execution_context context
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=context.artifact_id
                     WHERE context.compiled_execution_data_context_id=:risk_context
                       AND context.defense_version_id=:defense
                    """
                ),
                {"risk_context": risk_context_id, "defense": defense_version_id},
            )
            .mappings()
            .all()
        )
        if len(defense_rows) != 1:
            raise ValueError(
                "Defended Research Suite Branch requires exactly one Defense "
                "Execution Context for its Risk Context and Defense Package"
            )
        defense = defense_rows[0]
        if (
            defense["artifact_type"] != "v022_compiled_defense_execution_context"
            or defense["artifact_status"] != "published"
        ):
            raise ValueError(
                "Defended Research Suite Branch requires its exact published "
                "Defense Execution Context"
            )
        result[branch_id] = cast(
            uuid.UUID, defense["compiled_defense_execution_context_id"]
        )
    return risk_context_id, result


def _published_dependency(connection: Connection, artifact_id: uuid.UUID) -> RowMapping:
    row = connection.execute(
        text(
            "SELECT artifact_id,status,semantic_fingerprint FROM lineage.artifact "
            "WHERE artifact_id=:artifact"
        ),
        {"artifact": artifact_id},
    ).mappings().one_or_none()
    if row is None or row["status"] != "published" or row["semantic_fingerprint"] is None:
        raise ValueError(f"Suite dependency is not exactly published: {artifact_id}")
    return row


def _artifact_semantic_fingerprint(
    *,
    artifact_type: str,
    artifact_key: str,
    version_number: int,
    semantic_payload: object,
    dependency_rows: tuple[tuple[str, int | None, object], ...],
) -> str:
    return sha256_hexdigest(
        {
            "artifact_identity": {
                "artifact_type": artifact_type,
                "artifact_key": artifact_key,
                "version_number": version_number,
            },
            "semantic_payload": semantic_payload,
            "dependencies": [
                {
                    "role": role,
                    "ordinal": ordinal,
                    "semantic_fingerprint": semantic_fingerprint,
                }
                for role, ordinal, semantic_fingerprint in dependency_rows
            ],
        }
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
