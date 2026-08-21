from __future__ import annotations

import base64
import binascii
import json
import uuid
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.compiler_service import GraphCompilerService
from style_rotation.v022.dag import finalize_released_graph_run
from style_rotation.v022.defense_context import DefenseExecutionContextPublication
from style_rotation.v022.execution_context import ResolvedDataBindingSnapshot
from style_rotation.v022.graph import (
    AggregationSelection,
    AssetContextSnapshot,
    DraftIntent,
    FeatureSelection,
)
from style_rotation.v022.workspace_context import (
    CANONICAL_MARKET_INPUT,
    UNCONFIGURED_ASSET_CONTEXT_KEY,
    GraphWorkspaceContextResolver,
    require_active_v022_workspace_identity,
    unconfigured_workspace_context,
)
from style_rotation.v022.workspace_view import (
    ExplicitFeature,
    GraphWorkspacePreviewService,
    WorkspacePreviewIntent,
)


class GraphDraftRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__(f"Graph Draft revision is {current_revision}; reload before retrying")


class GraphDraftIdempotencyConflict(RuntimeError):
    pass


class CascadeConfirmationRequired(RuntimeError):
    def __init__(self, locked_by: tuple[str, ...]) -> None:
        self.locked_by = locked_by
        super().__init__("Locked ancestor requires a cascade change preview")


class ChangePreviewExpired(RuntimeError):
    pass


class GraphDraftCompilationBlocked(RuntimeError):
    def __init__(self, blockers: tuple[dict[str, Any], ...]) -> None:
        self.blockers = blockers
        super().__init__("Graph Draft cannot be compiled until all blockers are resolved")


class GraphViewTokenConflict(RuntimeError):
    def __init__(self, current_revision: int, current_view_token: str) -> None:
        self.current_revision = current_revision
        self.current_view_token = current_view_token
        super().__init__("Workspace view changed while paging; reload the current query")


class GraphWorkspaceViewIncompatible(RuntimeError):
    def __init__(self, revision: int, reason_code: str) -> None:
        self.revision = revision
        self.reason_code = reason_code
        super().__init__("The saved Workspace view cannot be rendered safely")


class GraphDraftLocked(RuntimeError):
    def __init__(self, revision: int) -> None:
        self.revision = revision
        super().__init__("The current research is locked by an experiment; reset it to edit")


class GraphCatalogRebaseRequired(RuntimeError):
    def __init__(self, current_catalog_release_id: uuid.UUID) -> None:
        self.current_catalog_release_id = current_catalog_release_id
        super().__init__("Graph Draft Catalog is stale; preview and confirm a rebase")


@dataclass(frozen=True, slots=True)
class GraphDraftSnapshot:
    graph_draft_id: uuid.UUID
    catalog_release_id: uuid.UUID
    draft_key: str
    name: str
    revision: int
    status: str
    asset_context: dict[str, Any]
    resolved_data_binding: dict[str, Any]
    intent: dict[str, Any]
    derived_view: dict[str, Any]
    cloned_from_graph_draft_id: uuid.UUID | None = None
    cloned_from_revision: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_draft_id": str(self.graph_draft_id),
            "catalog_release_id": str(self.catalog_release_id),
            "draft_key": self.draft_key,
            "name": self.name,
            "revision": self.revision,
            "status": self.status,
            "asset_context": self.asset_context,
            "resolved_data_binding": self.resolved_data_binding,
            "intent": self.intent,
            "derived_view": self.derived_view,
            "cloned_from_graph_draft_id": (
                str(self.cloned_from_graph_draft_id)
                if self.cloned_from_graph_draft_id is not None
                else None
            ),
            "cloned_from_revision": self.cloned_from_revision,
        }


@dataclass(frozen=True, slots=True)
class GraphDraftEventResult:
    snapshot: GraphDraftSnapshot
    applied: bool


@dataclass(frozen=True, slots=True)
class GraphDraftResetResult:
    snapshot: GraphDraftSnapshot
    closed_research_round_id: uuid.UUID
    opened_research_round_id: uuid.UUID
    cancelled_graph_run_count: int

    def __post_init__(self) -> None:
        if self.cancelled_graph_run_count < 0:
            raise ValueError("cancelled_graph_run_count cannot be negative")


@dataclass(frozen=True, slots=True)
class GraphChangePreview:
    impact_token: str
    graph_draft_id: uuid.UUID
    base_revision: int
    expires_at: datetime
    impact: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "impact_token": self.impact_token,
            "graph_draft_id": str(self.graph_draft_id),
            "base_revision": self.base_revision,
            "expires_at": self.expires_at.isoformat(),
            "impact": self.impact,
        }


@dataclass(frozen=True, slots=True)
class GraphDraftDefenseExecutionContext:
    compiled_defense_execution_context_id: uuid.UUID
    defense_execution_context_artifact_id: uuid.UUID
    compiled_execution_data_context_id: uuid.UUID
    defense_version_id: uuid.UUID
    defense_execution_context_fingerprint: str
    resolved_input_binding_fingerprint: str
    input_count: int
    reused: bool

    def __post_init__(self) -> None:
        if self.input_count < 1:
            raise ValueError("Defense Execution Context input_count must be positive")
        for label, fingerprint in (
            ("Defense Execution Context", self.defense_execution_context_fingerprint),
            ("Defense Resolved Input Binding", self.resolved_input_binding_fingerprint),
        ):
            if len(fingerprint) != 64 or any(
                character not in "0123456789abcdef" for character in fingerprint
            ):
                raise ValueError(f"{label} fingerprint must be lowercase sha256")

    @classmethod
    def from_publication(
        cls,
        publication: DefenseExecutionContextPublication,
    ) -> GraphDraftDefenseExecutionContext:
        return cls(
            publication.context_id,
            publication.artifact_id,
            publication.compiled_execution_data_context_id,
            publication.defense_version_id,
            publication.context_fingerprint,
            publication.resolved_input_binding_fingerprint,
            publication.input_count,
            publication.reused,
        )

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> GraphDraftDefenseExecutionContext:
        reused = document["reused"]
        input_count = document["input_count"]
        if not isinstance(reused, bool):
            raise ValueError("Defense Execution Context reused must be boolean")
        if isinstance(input_count, bool) or not isinstance(input_count, int):
            raise ValueError("Defense Execution Context input_count must be an integer")
        return cls(
            uuid.UUID(document["compiled_defense_execution_context_id"]),
            uuid.UUID(document["defense_execution_context_artifact_id"]),
            uuid.UUID(document["compiled_execution_data_context_id"]),
            uuid.UUID(document["defense_version_id"]),
            document["defense_execution_context_fingerprint"],
            document["resolved_input_binding_fingerprint"],
            input_count,
            reused,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "compiled_defense_execution_context_id": str(
                self.compiled_defense_execution_context_id
            ),
            "defense_execution_context_artifact_id": str(
                self.defense_execution_context_artifact_id
            ),
            "compiled_execution_data_context_id": str(
                self.compiled_execution_data_context_id
            ),
            "defense_version_id": str(self.defense_version_id),
            "defense_execution_context_fingerprint": (
                self.defense_execution_context_fingerprint
            ),
            "resolved_input_binding_fingerprint": (
                self.resolved_input_binding_fingerprint
            ),
            "input_count": self.input_count,
            "reused": self.reused,
        }


@dataclass(frozen=True, slots=True)
class GraphDraftCompileResult:
    graph_draft_id: uuid.UUID
    graph_draft_revision: int
    draft_intent_id: uuid.UUID
    compile_attempt_id: uuid.UUID
    compiled_research_graph_id: uuid.UUID
    graph_artifact_id: uuid.UUID
    graph_fingerprint: str
    reused: bool
    compiled_execution_data_context_id: uuid.UUID | None = None
    execution_data_context_artifact_id: uuid.UUID | None = None
    execution_data_context_fingerprint: str | None = None
    execution_data_context_reused: bool | None = None
    defense_execution_contexts: tuple[GraphDraftDefenseExecutionContext, ...] = ()
    selection_fingerprint: str | None = None

    def __post_init__(self) -> None:
        context_identity = (
            self.compiled_execution_data_context_id,
            self.execution_data_context_artifact_id,
            self.execution_data_context_fingerprint,
            self.execution_data_context_reused,
        )
        present = tuple(value is not None for value in context_identity)
        if any(present) and not all(present):
            raise ValueError(
                "Compiled Execution Data Context identity must be wholly present or absent"
            )
        if self.execution_data_context_fingerprint is not None and (
            len(self.execution_data_context_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.execution_data_context_fingerprint
            )
        ):
            raise ValueError(
                "Compiled Execution Data Context fingerprint must be lowercase sha256"
            )
        if self.selection_fingerprint is not None and (
            len(self.selection_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.selection_fingerprint
            )
        ):
            raise ValueError("Graph selection fingerprint must be lowercase sha256")
        defense_version_ids = tuple(
            item.defense_version_id for item in self.defense_execution_contexts
        )
        if len(defense_version_ids) != len(set(defense_version_ids)):
            raise ValueError("Defense Execution Contexts require unique Defense versions")
        if defense_version_ids != tuple(sorted(defense_version_ids, key=str)):
            raise ValueError("Defense Execution Contexts require canonical Defense order")
        if self.defense_execution_contexts and self.compiled_execution_data_context_id is None:
            raise ValueError(
                "Defense Execution Contexts require the Compiled Execution Data Context"
            )
        if any(
            item.compiled_execution_data_context_id
            != self.compiled_execution_data_context_id
            for item in self.defense_execution_contexts
        ):
            raise ValueError(
                "Defense Execution Contexts must bind the response Execution Data Context"
            )
        for label, identities in (
            (
                "Defense Execution Context",
                tuple(
                    item.compiled_defense_execution_context_id
                    for item in self.defense_execution_contexts
                ),
            ),
            (
                "Defense Execution Context Artifact",
                tuple(
                    item.defense_execution_context_artifact_id
                    for item in self.defense_execution_contexts
                ),
            ),
        ):
            if len(identities) != len(set(identities)):
                raise ValueError(f"{label} identities must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_draft_id": str(self.graph_draft_id),
            "graph_draft_revision": self.graph_draft_revision,
            "draft_intent_id": str(self.draft_intent_id),
            "compile_attempt_id": str(self.compile_attempt_id),
            "compiled_research_graph_id": str(self.compiled_research_graph_id),
            "graph_artifact_id": str(self.graph_artifact_id),
            "graph_fingerprint": self.graph_fingerprint,
            "reused": self.reused,
            "compiled_execution_data_context_id": (
                str(self.compiled_execution_data_context_id)
                if self.compiled_execution_data_context_id is not None
                else None
            ),
            "execution_data_context_artifact_id": (
                str(self.execution_data_context_artifact_id)
                if self.execution_data_context_artifact_id is not None
                else None
            ),
            "execution_data_context_fingerprint": (
                self.execution_data_context_fingerprint
            ),
            "execution_data_context_reused": self.execution_data_context_reused,
            "defense_execution_contexts": [
                item.to_dict() for item in self.defense_execution_contexts
            ],
            "selection_fingerprint": self.selection_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class GraphStageFamilyPage:
    graph_draft_id: uuid.UUID
    revision: int
    stage_no: Literal[0, 1, 2, 3]
    view_token: str
    pinned_families: tuple[dict[str, Any], ...]
    catalog_families: tuple[dict[str, Any], ...]
    next_cursor: str | None
    total_catalog_family_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_draft_id": str(self.graph_draft_id),
            "revision": self.revision,
            "stage_no": self.stage_no,
            "view_token": self.view_token,
            "pinned_families": list(self.pinned_families),
            "catalog_families": list(self.catalog_families),
            "next_cursor": self.next_cursor,
            "total_catalog_family_count": self.total_catalog_family_count,
        }


class GraphDraftService:
    """Transactional Graph Draft state machine backed by immutable revisions."""

    def __init__(
        self,
        engine: Engine,
        workspace: GraphWorkspacePreviewService,
        compiler: GraphCompilerService | None = None,
        context_resolver: GraphWorkspaceContextResolver | None = None,
    ) -> None:
        self._engine = engine
        self._workspace = workspace
        self._compiler = compiler or GraphCompilerService(
            engine, compiler_version="v022-compiler-native-recipe-v1"
        )
        self._context_resolver = context_resolver or GraphWorkspaceContextResolver()

    def create(
        self,
        *,
        researcher_key: str,
        draft_key: str,
        name: str,
        idempotency_key: uuid.UUID,
        frequency: Literal["weekly", "monthly"] = "weekly",
        asset_context_key: str | None = None,
        data_input_keys: tuple[str, ...] = (),
    ) -> GraphDraftSnapshot:
        request = {
            "researcher_key": researcher_key,
            "draft_key": draft_key,
            "name": name,
            "asset_context_key": asset_context_key,
            "data_input_keys": data_input_keys,
            "frequency": frequency,
        }
        request_fingerprint = sha256_hexdigest(request)
        with self._engine.begin() as connection:
            self._lock_command_key(
                connection, researcher_key, "create_graph_draft", idempotency_key
            )
            replay = self._command_replay(
                connection,
                researcher_key,
                "create_graph_draft",
                idempotency_key,
                request_fingerprint,
            )
            if replay is not None:
                return self._get(connection, uuid.UUID(replay["graph_draft_id"]), lock=False)

            existing_id = connection.scalar(
                text(
                    """
                    SELECT graph_draft_id FROM workspace.v022_graph_draft
                    WHERE researcher_key=:researcher AND draft_key=:key FOR UPDATE
                    """
                ),
                {"researcher": researcher_key, "key": draft_key},
            )
            if existing_id is not None:
                existing = self._get(connection, existing_id, lock=False)
                created = self._get(connection, existing_id, revision=1, lock=False)
                existing_inputs = tuple(
                    item["input_key"] for item in created.resolved_data_binding.get("bindings", [])
                )
                same_identity = (
                    existing.name == name
                    and created.intent["frequency"] == frequency
                    and created.asset_context.get("asset_context_key")
                    == (asset_context_key or UNCONFIGURED_ASSET_CONTEXT_KEY)
                    and existing_inputs == data_input_keys
                )
                if not same_identity:
                    raise GraphDraftIdempotencyConflict(
                        "Graph Draft key already exists with different creation semantics"
                    )
                response = {
                    "graph_draft_id": str(existing.graph_draft_id),
                    "revision": existing.revision,
                }
                self._insert_command_result(
                    connection,
                    researcher_key,
                    "create_graph_draft",
                    idempotency_key,
                    request_fingerprint,
                    response,
                )
                return existing

            release_id = self._catalog_release_id(connection)
            resolved_context = (
                self._context_resolver.resolve(
                    connection,
                    asset_context_key=asset_context_key,
                    data_input_keys=data_input_keys,
                )
                if asset_context_key is not None
                else unconfigured_workspace_context()
            )
            graph_draft_id = uuid.uuid4()
            intent = self._initial_intent(frequency)
            derived = self._derive(intent, resolved_context.asset_context_document)
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_graph_draft (
                      graph_draft_id,catalog_release_id,researcher_key,draft_key,name,
                      current_revision,status,asset_context_fingerprint,
                      resolved_data_binding_fingerprint,asset_context_document,
                      resolved_data_binding_document
                    ) VALUES (:id,:release,:researcher,:key,:name,1,'draft',:asset,:binding,
                              CAST(:asset_document AS jsonb),CAST(:binding_document AS jsonb))
                    """
                ),
                {
                    "id": graph_draft_id,
                    "release": release_id,
                    "researcher": researcher_key,
                    "key": draft_key,
                    "name": name,
                    "asset": resolved_context.asset_context_fingerprint,
                    "binding": resolved_context.resolved_data_binding_fingerprint,
                    "asset_document": json.dumps(
                        resolved_context.asset_context_document, sort_keys=True
                    ),
                    "binding_document": json.dumps(
                        resolved_context.resolved_data_binding_document, sort_keys=True
                    ),
                },
            )
            research_round_id = self._create_research_round(
                connection,
                root_graph_draft_id=graph_draft_id,
                ordinal=1,
                actor_key=researcher_key,
            )
            self._insert_revision(
                connection,
                graph_draft_id,
                1,
                release_id,
                intent,
                derived,
                resolved_context.asset_context_document,
                resolved_context.resolved_data_binding_document,
                researcher_key,
                research_round_id=research_round_id,
            )
            response = {"graph_draft_id": str(graph_draft_id), "revision": 1}
            self._insert_command_result(
                connection,
                researcher_key,
                "create_graph_draft",
                idempotency_key,
                request_fingerprint,
                response,
            )
            return self._get(connection, graph_draft_id, lock=False)

    def get(self, graph_draft_id: uuid.UUID) -> GraphDraftSnapshot:
        with self._engine.connect() as connection:
            return self._presentation_snapshot(
                connection, self._get(connection, graph_draft_id, lock=False)
            )

    def get_by_key(self, *, researcher_key: str, draft_key: str) -> GraphDraftSnapshot:
        """Restore the actor-owned Draft without relying on browser-local identity."""

        with self._engine.connect() as connection:
            draft_ids = connection.scalars(
                text(
                    "SELECT graph_draft_id FROM workspace.v022_graph_draft "
                    "WHERE researcher_key=:researcher AND draft_key=:key"
                ),
                {"researcher": researcher_key, "key": draft_key},
            ).all()
            if not draft_ids:
                raise LookupError("Graph Draft not found")
            if len(draft_ids) != 1:
                raise GraphDraftIdempotencyConflict(
                    "Graph Draft actor/key identity is not unique"
                )
            return self._presentation_snapshot(
                connection, self._get(connection, draft_ids[0], lock=False)
            )

    def current_compile(
        self,
        graph_draft_id: uuid.UUID,
        *,
        actor_key: str,
    ) -> GraphDraftCompileResult | None:
        """Return the exact compile bound to the current immutable revision."""

        with self._engine.connect() as connection:
            self._require_owner(connection, graph_draft_id, actor_key)
            root = (
                connection.execute(
                    text(
                        """
                        SELECT current_revision,last_compiled_research_graph_id,
                               last_compile_command_result_id
                          FROM workspace.v022_graph_draft
                         WHERE graph_draft_id=:draft
                        """
                    ),
                    {"draft": graph_draft_id},
                )
                .mappings()
                .one_or_none()
            )
            if root is None:
                raise LookupError("Graph Draft not found")
            command_result_id = root["last_compile_command_result_id"]
            compiled_graph_id = root["last_compiled_research_graph_id"]
            if command_result_id is None or compiled_graph_id is None:
                return None
            document = connection.scalar(
                text(
                    """
                    SELECT response_document
                      FROM workspace.v022_command_result
                     WHERE command_result_id=:command
                       AND actor_key=:actor
                       AND command_kind='compile_graph_draft'
                    """
                ),
                {"command": command_result_id, "actor": actor_key},
            )
            if document is None:
                raise GraphDraftIdempotencyConflict(
                    "Current Graph Draft compile binding is incomplete"
                )
            result = self._compile_result(cast(dict[str, Any], document))
            if (
                result.graph_draft_id != graph_draft_id
                or result.graph_draft_revision != int(root["current_revision"])
                or result.compiled_research_graph_id != compiled_graph_id
            ):
                raise GraphDraftIdempotencyConflict(
                    "Current Graph Draft compile binding does not reproduce"
                )
            return result

    def lock_for_experiment(
        self,
        graph_draft_id: uuid.UUID,
        *,
        expected_revision: int,
        actor_key: str,
        compiled_research_graph_id: uuid.UUID,
    ) -> GraphDraftSnapshot:
        with self._engine.begin() as connection:
            snapshot = self._get(connection, graph_draft_id, lock=True)
            self._require_owner(connection, graph_draft_id, actor_key)
            if snapshot.revision != expected_revision:
                raise GraphDraftRevisionConflict(snapshot.revision)
            compiled_graph_id = connection.scalar(
                text(
                    "SELECT last_compiled_research_graph_id "
                    "FROM workspace.v022_graph_draft WHERE graph_draft_id=:draft"
                ),
                {"draft": graph_draft_id},
            )
            if compiled_graph_id != compiled_research_graph_id:
                raise GraphDraftCompilationBlocked(
                    (
                        {
                            "layer": "experiment",
                            "object_key": "compiled_research_graph",
                            "reason_codes": ["current_revision_compile_required"],
                        },
                    )
                )
            if snapshot.status == "archived":
                return snapshot
            connection.execute(
                text(
                    "UPDATE workspace.v022_graph_draft "
                    "SET status='archived',updated_at=now() WHERE graph_draft_id=:draft"
                ),
                {"draft": graph_draft_id},
            )
            return self._get(connection, graph_draft_id, lock=False)

    def reset_current_research(
        self,
        graph_draft_id: uuid.UUID,
        *,
        expected_revision: int,
        actor_key: str,
        idempotency_key: uuid.UUID,
    ) -> GraphDraftResetResult:
        request = {
            "graph_draft_id": graph_draft_id,
            "expected_revision": expected_revision,
        }
        request_fingerprint = sha256_hexdigest(request)
        event_type = "reset_current_research"
        with self._engine.begin() as connection:
            current = self._get(connection, graph_draft_id, lock=True)
            replay = (
                connection.execute(
                    text(
                        "SELECT request_fingerprint,response_document "
                        "FROM workspace.v022_graph_draft_event "
                        "WHERE graph_draft_id=:draft AND actor_key=:actor "
                        "AND idempotency_key=:key"
                    ),
                    {"draft": graph_draft_id, "actor": actor_key, "key": idempotency_key},
                )
                .mappings()
                .one_or_none()
            )
            if replay is not None:
                if replay["request_fingerprint"] != request_fingerprint:
                    raise GraphDraftIdempotencyConflict(
                        "Idempotency key was already used for another research reset"
                    )
                response_document = cast(dict[str, Any], replay["response_document"])
                return GraphDraftResetResult(
                    snapshot=self._get(
                        connection,
                        graph_draft_id,
                        revision=int(response_document["revision"]),
                        lock=False,
                    ),
                    closed_research_round_id=uuid.UUID(
                        str(response_document["closed_research_round_id"])
                    ),
                    opened_research_round_id=uuid.UUID(
                        str(response_document["opened_research_round_id"])
                    ),
                    cancelled_graph_run_count=int(
                        response_document["cancelled_graph_run_count"]
                    ),
                )
            self._require_owner(connection, graph_draft_id, actor_key)
            if current.revision != expected_revision:
                raise GraphDraftRevisionConflict(current.revision)

            round_row = connection.execute(
                text(
                    "SELECT round.research_round_id,round.ordinal,round.status "
                    "FROM workspace.v022_graph_draft_revision_round binding "
                    "JOIN workspace.v022_research_round round "
                    "ON round.research_round_id=binding.research_round_id "
                    "WHERE binding.graph_draft_id=:draft "
                    "AND binding.revision=:revision FOR UPDATE OF round"
                ),
                {"draft": graph_draft_id, "revision": current.revision},
            ).mappings().one_or_none()
            if round_row is None or round_row["status"] != "active":
                raise RuntimeError("Current Graph Draft revision has no active Research Round")
            current_round_id = cast(uuid.UUID, round_row["research_round_id"])

            run_ids = connection.scalars(
                text(
                    """
                    WITH round_suites AS (
                      SELECT child.research_suite_id
                        FROM experiment.v022_suite_launch_batch_round batch_round
                        JOIN experiment.v022_suite_launch_batch_child child
                          ON child.suite_launch_batch_id=
                             batch_round.suite_launch_batch_id
                       WHERE batch_round.research_round_id=:round
                         AND child.research_suite_id IS NOT NULL
                      UNION
                      SELECT suite.research_suite_id
                        FROM workspace.v022_graph_draft_revision_round revision_round
                        JOIN workspace.v022_graph_draft_compile_binding bridge
                          ON bridge.graph_draft_id=revision_round.graph_draft_id
                         AND bridge.graph_draft_revision=revision_round.revision
                        JOIN workspace.v022_compile_attempt attempt
                          ON attempt.draft_intent_id=bridge.draft_intent_id
                         AND attempt.draft_revision=bridge.graph_draft_revision
                         AND attempt.status='succeeded'
                        JOIN experiment.v022_research_suite suite
                          ON suite.compiled_research_graph_id=
                             attempt.compiled_research_graph_id
                       WHERE revision_round.research_round_id=:round
                    )
                    SELECT DISTINCT binding.graph_run_id
                      FROM round_suites suite
                      JOIN experiment.v022_research_suite_graph_run_binding binding
                        ON binding.research_suite_id=suite.research_suite_id
                      JOIN workspace.v022_graph_run run
                        ON run.graph_run_id=binding.graph_run_id
                     WHERE run.status NOT IN ('completed','failed','cancelled')
                    """
                ),
                {"round": current_round_id},
            ).all()
            for graph_run_id in run_ids:
                connection.execute(
                    text("SELECT workspace.v022_release_graph_run(:run)"),
                    {"run": graph_run_id},
                )
                finalize_released_graph_run(connection, graph_run_id)

            release_id = self._catalog_release_id(connection)
            connection.execute(
                text(
                    "UPDATE workspace.v022_research_round "
                    "SET status='gc_pending',closed_at=now(),"
                    "close_reason='user_reset',reset_idempotency_key=:reset_key "
                    "WHERE research_round_id=:round AND status='active'"
                ),
                {"round": current_round_id, "reset_key": idempotency_key},
            )
            resolved_context = unconfigured_workspace_context()
            intent = self._initial_intent("weekly")
            derived = self._derive(intent, resolved_context.asset_context_document)
            next_revision = current.revision + 1
            next_round_id = self._create_research_round(
                connection,
                root_graph_draft_id=graph_draft_id,
                ordinal=int(round_row["ordinal"]) + 1,
                actor_key=actor_key,
                reset_idempotency_key=idempotency_key,
            )
            self._insert_revision(
                connection,
                graph_draft_id,
                next_revision,
                release_id,
                intent,
                derived,
                resolved_context.asset_context_document,
                resolved_context.resolved_data_binding_document,
                actor_key,
                research_round_id=next_round_id,
            )
            connection.execute(
                text(
                    """
                    UPDATE workspace.v022_graph_draft
                       SET catalog_release_id=:release,current_revision=:revision,
                           status='draft',asset_context_fingerprint=:asset,
                           resolved_data_binding_fingerprint=:binding,
                           asset_context_document=CAST(:asset_document AS jsonb),
                           resolved_data_binding_document=CAST(:binding_document AS jsonb),
                           last_compiled_research_graph_id=NULL,
                           last_compile_command_result_id=NULL,updated_at=now()
                     WHERE graph_draft_id=:draft
                    """
                ),
                {
                    "release": release_id,
                    "revision": next_revision,
                    "asset": resolved_context.asset_context_fingerprint,
                    "binding": resolved_context.resolved_data_binding_fingerprint,
                    "asset_document": json.dumps(
                        resolved_context.asset_context_document, sort_keys=True
                    ),
                    "binding_document": json.dumps(
                        resolved_context.resolved_data_binding_document, sort_keys=True
                    ),
                    "draft": graph_draft_id,
                },
            )
            self._insert_event(
                connection,
                graph_draft_id,
                current.revision,
                next_revision,
                actor_key,
                idempotency_key,
                request_fingerprint,
                event_type,
                {
                    "closed_research_round_id": str(current_round_id),
                    "opened_research_round_id": str(next_round_id),
                    "cancelled_graph_run_count": len(run_ids),
                },
                applied=True,
                response_extra={
                    "closed_research_round_id": str(current_round_id),
                    "opened_research_round_id": str(next_round_id),
                    "cancelled_graph_run_count": len(run_ids),
                },
            )
            return GraphDraftResetResult(
                snapshot=self._get(connection, graph_draft_id, lock=False),
                closed_research_round_id=current_round_id,
                opened_research_round_id=next_round_id,
                cancelled_graph_run_count=len(run_ids),
            )

    def clone_revision(
        self,
        source_graph_draft_id: uuid.UUID,
        *,
        source_revision: int,
        researcher_key: str,
        draft_key: str,
        name: str,
        idempotency_key: uuid.UUID,
    ) -> GraphDraftSnapshot:
        request = {
            "source_graph_draft_id": source_graph_draft_id,
            "source_revision": source_revision,
            "researcher_key": researcher_key,
            "draft_key": draft_key,
            "name": name,
        }
        fingerprint = sha256_hexdigest(request)
        with self._engine.begin() as connection:
            self._lock_command_key(
                connection, researcher_key, "clone_graph_draft_revision", idempotency_key
            )
            replay = self._command_replay(
                connection,
                researcher_key,
                "clone_graph_draft_revision",
                idempotency_key,
                fingerprint,
            )
            if replay is not None:
                return self._get(connection, uuid.UUID(replay["graph_draft_id"]), lock=False)
            source = self._get(
                connection,
                source_graph_draft_id,
                revision=source_revision,
                lock=False,
            )
            source_round_id = connection.scalar(
                text(
                    "SELECT research_round_id "
                    "FROM workspace.v022_graph_draft_revision_round "
                    "WHERE graph_draft_id=:draft AND revision=:revision"
                ),
                {"draft": source_graph_draft_id, "revision": source_revision},
            )
            if source_round_id is None:
                raise RuntimeError("Clone source revision has no Research Round")
            existing_id = connection.scalar(
                text(
                    "SELECT graph_draft_id FROM workspace.v022_graph_draft "
                    "WHERE researcher_key=:researcher AND draft_key=:key FOR UPDATE"
                ),
                {"researcher": researcher_key, "key": draft_key},
            )
            if existing_id is not None:
                existing = self._get(connection, existing_id, lock=False)
                if (
                    existing.name != name
                    or existing.cloned_from_graph_draft_id != source_graph_draft_id
                    or existing.cloned_from_revision != source_revision
                ):
                    raise GraphDraftIdempotencyConflict(
                        "Graph Draft key already exists with another clone source"
                    )
                result = existing
            else:
                graph_draft_id = uuid.uuid4()
                connection.execute(
                    text(
                        "INSERT INTO workspace.v022_graph_draft ("
                        "graph_draft_id,catalog_release_id,researcher_key,draft_key,name,"
                        "current_revision,status,asset_context_fingerprint,"
                        "resolved_data_binding_fingerprint,asset_context_document,"
                        "resolved_data_binding_document,cloned_from_graph_draft_id,"
                        "cloned_from_revision) VALUES ("
                        ":id,:release,:researcher,:key,:name,1,'draft',:asset,:binding,"
                        "CAST(:asset_document AS jsonb),CAST(:binding_document AS jsonb),"
                        ":source,:source_revision)"
                    ),
                    {
                        "id": graph_draft_id,
                        "release": source.catalog_release_id,
                        "researcher": researcher_key,
                        "key": draft_key,
                        "name": name,
                        "asset": sha256_hexdigest(source.asset_context),
                        "binding": sha256_hexdigest(source.resolved_data_binding),
                        "asset_document": json.dumps(
                            source.asset_context, sort_keys=True
                        ),
                        "binding_document": json.dumps(
                            source.resolved_data_binding, sort_keys=True
                        ),
                        "source": source_graph_draft_id,
                        "source_revision": source_revision,
                    },
                )
                self._insert_revision(
                    connection,
                    graph_draft_id,
                    1,
                    source.catalog_release_id,
                    source.intent,
                    source.derived_view,
                    source.asset_context,
                    source.resolved_data_binding,
                    researcher_key,
                    research_round_id=cast(uuid.UUID, source_round_id),
                )
                result = self._get(connection, graph_draft_id, lock=False)
            self._insert_command_result(
                connection,
                researcher_key,
                "clone_graph_draft_revision",
                idempotency_key,
                fingerprint,
                {"graph_draft_id": str(result.graph_draft_id), "revision": 1},
            )
            return result

    def stage_families(
        self,
        graph_draft_id: uuid.UUID,
        *,
        stage_no: Literal[0, 1, 2, 3],
        search: str = "",
        selection_filter: Literal["all", "selected", "locked"] = "all",
        availability_filter: Literal[
            "all", "ready", "requires_ancestors", "hard_incompatible"
        ] = "all",
        cursor: str | None = None,
        limit: int = 12,
        actor_key: str | None = None,
    ) -> GraphStageFamilyPage:
        if not 1 <= limit <= 50:
            raise ValueError("Family page limit must be between 1 and 50")
        with self._engine.connect() as connection:
            snapshot = self._get(connection, graph_draft_id, lock=False)
            if actor_key is not None:
                self._require_owner(connection, graph_draft_id, actor_key)
            derived_view = snapshot.derived_view
            if not _has_stage_presentation_contract(derived_view):
                derived_view = self._stage_derived_view(
                    snapshot,
                    current_catalog_release_id=self._catalog_release_id(connection),
                )
        token = sha256_hexdigest(
            {
                "draft_revision": snapshot.revision,
                "catalog_release_id": snapshot.catalog_release_id,
                "data_availability_revision": sha256_hexdigest(
                    snapshot.resolved_data_binding
                ),
                "derived_state_fingerprint": derived_view["derived_state_fingerprint"],
            }
        )
        query = search.strip().casefold()
        query_fingerprint = sha256_hexdigest(
            {
                "stage_no": stage_no,
                "search": query,
                "selection_filter": selection_filter,
                "availability_filter": availability_filter,
                "limit": limit,
            }
        )
        offset = 0
        if cursor is not None:
            cursor_document = _decode_family_cursor(cursor)
            if cursor_document.get("view_token") != token:
                raise GraphViewTokenConflict(snapshot.revision, token)
            if cursor_document.get("query_fingerprint") != query_fingerprint:
                raise ValueError("Family page cursor belongs to another query")
            cursor_offset = cursor_document.get("offset")
            if not isinstance(cursor_offset, int) or cursor_offset < 0:
                raise ValueError("Invalid Family page cursor offset")
            offset = cursor_offset

        stage = derived_view["stages"][stage_no]
        families = tuple(cast(dict[str, Any], item) for item in stage["families"])
        pinned = tuple(item for item in families if item["pinned"])
        catalog = tuple(
            item
            for item in families
            if not item["pinned"]
            and _family_matches_search(item, query)
            and _family_matches_selection(item, selection_filter)
            and _family_matches_availability(item, availability_filter)
        )
        page = catalog[offset : offset + limit]
        next_offset = offset + len(page)
        next_cursor = (
            _encode_family_cursor(token, query_fingerprint, next_offset)
            if next_offset < len(catalog)
            else None
        )
        return GraphStageFamilyPage(
            graph_draft_id,
            snapshot.revision,
            stage_no,
            token,
            pinned,
            page,
            next_cursor,
            len(catalog),
        )

    def _stage_derived_view(
        self,
        snapshot: GraphDraftSnapshot,
        *,
        current_catalog_release_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Enrich legacy presentation fields without mutating frozen Draft identity."""

        if snapshot.catalog_release_id != current_catalog_release_id:
            if _has_stage_presentation_contract(snapshot.derived_view):
                return snapshot.derived_view
            raise GraphCatalogRebaseRequired(current_catalog_release_id)

        rebuilt = self._derive(snapshot.intent, snapshot.asset_context)
        for fingerprint_key in ("selection_fingerprint", "derived_state_fingerprint"):
            if rebuilt.get(fingerprint_key) != snapshot.derived_view.get(fingerprint_key):
                raise GraphWorkspaceViewIncompatible(
                    snapshot.revision,
                    "workspace_view_identity_mismatch",
                )
        if not _has_stage_presentation_contract(rebuilt):
            raise GraphWorkspaceViewIncompatible(
                snapshot.revision,
                "workspace_view_contract_unavailable",
            )
        return rebuilt

    def _presentation_snapshot(
        self, connection: Connection, snapshot: GraphDraftSnapshot
    ) -> GraphDraftSnapshot:
        """Refresh derivable UI facts without mutating the frozen revision record."""

        if snapshot.catalog_release_id != self._catalog_release_id(connection):
            return replace(
                snapshot,
                derived_view=_with_legacy_aggregation_presentation(
                    snapshot.derived_view
                ),
            )
        rebuilt = self._derive(snapshot.intent, snapshot.asset_context)
        for fingerprint_key in ("selection_fingerprint", "derived_state_fingerprint"):
            if rebuilt.get(fingerprint_key) != snapshot.derived_view.get(fingerprint_key):
                raise GraphWorkspaceViewIncompatible(
                    snapshot.revision,
                    "workspace_view_identity_mismatch",
                )
        return replace(snapshot, derived_view=rebuilt)

    def apply_event(
        self,
        graph_draft_id: uuid.UUID,
        *,
        expected_revision: int,
        actor_key: str,
        idempotency_key: uuid.UUID,
        event_type: str,
        event: dict[str, Any],
    ) -> GraphDraftEventResult:
        request = {
            "expected_revision": expected_revision,
            "event_type": event_type,
            "event": event,
        }
        request_fingerprint = sha256_hexdigest(request)
        with self._engine.begin() as connection:
            current = self._get(connection, graph_draft_id, lock=True)
            self._require_owner(connection, graph_draft_id, actor_key)
            replay = (
                connection.execute(
                    text(
                        """
                    SELECT request_fingerprint,response_document,applied
                    FROM workspace.v022_graph_draft_event
                    WHERE graph_draft_id=:draft AND actor_key=:actor AND idempotency_key=:key
                    """
                    ),
                    {"draft": graph_draft_id, "actor": actor_key, "key": idempotency_key},
                )
                .mappings()
                .one_or_none()
            )
            if replay is not None:
                if replay["request_fingerprint"] != request_fingerprint:
                    raise GraphDraftIdempotencyConflict(
                        "Idempotency key was already used for another Graph Draft event"
                    )
                snapshot = self._get(
                    connection,
                    graph_draft_id,
                    revision=int(replay["response_document"]["revision"]),
                    lock=False,
                )
                return GraphDraftEventResult(snapshot, bool(replay["applied"]))

            if current.revision != expected_revision:
                raise GraphDraftRevisionConflict(current.revision)
            self._require_editable(current)
            self._require_current_catalog(connection, current)
            next_intent = self._mutate(current, event_type, event)
            next_context = None
            if event_type == "set_asset_selection":
                raw_ids = event.get("security_ids")
                if not isinstance(raw_ids, list):
                    raise ValueError("Asset Selection requires a Security id list")
                asset_registry_release_id = current.asset_context.get(
                    "asset_registry_release_id"
                )
                if asset_registry_release_id is None:
                    asset_registry_release_id = self._active_asset_registry_release_id(
                        connection
                    )
                next_context = self._context_resolver.resolve_explicit_selection(
                    connection,
                    asset_registry_release_id=uuid.UUID(str(asset_registry_release_id)),
                    security_ids=tuple(raw_ids),
                    data_input_keys=(CANONICAL_MARKET_INPUT,),
                    created_by=actor_key,
                )
            next_asset_context = (
                next_context.asset_context_document
                if next_context is not None
                else current.asset_context
            )
            next_binding = (
                next_context.resolved_data_binding_document
                if next_context is not None
                else current.resolved_data_binding
            )
            if next_intent == current.intent and next_asset_context == current.asset_context:
                self._insert_event(
                    connection,
                    graph_draft_id,
                    current.revision,
                    current.revision,
                    actor_key,
                    idempotency_key,
                    request_fingerprint,
                    event_type,
                    event,
                    applied=False,
                )
                return GraphDraftEventResult(current, False)

            derived = self._derive(next_intent, next_asset_context)
            self._reject_explicit_hard_incompatibility(derived)
            next_revision = current.revision + 1
            self._insert_revision(
                connection,
                graph_draft_id,
                next_revision,
                current.catalog_release_id,
                next_intent,
                derived,
                next_asset_context,
                next_binding,
                actor_key,
            )
            connection.execute(
                text(
                    "UPDATE workspace.v022_graph_draft SET current_revision=:revision,"
                    "asset_context_fingerprint=:asset_fingerprint,"
                    "resolved_data_binding_fingerprint=:binding_fingerprint,"
                    "asset_context_document=CAST(:asset_document AS jsonb),"
                    "resolved_data_binding_document=CAST(:binding_document AS jsonb),"
                    "last_compiled_research_graph_id=NULL,"
                    "last_compile_command_result_id=NULL,updated_at=now() "
                    "WHERE graph_draft_id=:draft"
                ),
                {
                    "revision": next_revision,
                    "asset_fingerprint": sha256_hexdigest(next_asset_context),
                    "binding_fingerprint": sha256_hexdigest(next_binding),
                    "asset_document": json.dumps(next_asset_context, sort_keys=True),
                    "binding_document": json.dumps(next_binding, sort_keys=True),
                    "draft": graph_draft_id,
                },
            )
            self._insert_event(
                connection,
                graph_draft_id,
                current.revision,
                next_revision,
                actor_key,
                idempotency_key,
                request_fingerprint,
                event_type,
                event,
                applied=True,
            )
            return GraphDraftEventResult(self._get(connection, graph_draft_id, lock=False), True)

    def preview_cascade_deselect(
        self,
        graph_draft_id: uuid.UUID,
        *,
        expected_revision: int,
        actor_key: str,
        feature_key: str,
        stage_no: int,
        ttl: timedelta = timedelta(minutes=15),
    ) -> GraphChangePreview:
        with self._engine.begin() as connection:
            current = self._get(connection, graph_draft_id, lock=True)
            self._require_owner(connection, graph_draft_id, actor_key)
            if current.revision != expected_revision:
                raise GraphDraftRevisionConflict(current.revision)
            self._require_editable(current)
            self._require_current_catalog(connection, current)
            occurrence = self._occurrence(current.derived_view, feature_key, stage_no)
            locked_by = tuple(cast(list[str], occurrence["locked_by"]))
            if not locked_by:
                raise ValueError("Occurrence is not locked; use a normal deselect event")
            removals = set(locked_by) | {f"{feature_key}@{stage_no}"}
            next_intent = deepcopy(current.intent)
            next_intent["explicit_features"] = [
                item
                for item in next_intent["explicit_features"]
                if f"{item['feature_key']}@{item['stage_no']}" not in removals
            ]
            next_derived = self._derive(next_intent, current.asset_context)
            before = self._present_labels(current.derived_view)
            after = self._present_labels(next_derived)
            impact = {
                "requested_occurrence": f"{feature_key}@{stage_no}",
                "removed_explicit_occurrences": sorted(
                    removals & self._explicit_labels(current.intent)
                ),
                "removed_derived_occurrences": sorted(before - after),
                "remaining_blockers": next_derived["blockers"],
            }
            token = sha256_hexdigest(
                {"preview_id": uuid.uuid4(), "draft": graph_draft_id, "impact": impact}
            )
            preview_id = uuid.uuid4()
            expires_at = datetime.now(UTC) + ttl
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_graph_change_preview (
                      graph_change_preview_id,graph_draft_id,base_revision,impact_token,
                      request_document,next_intent_document,impact_document,created_by,expires_at
                    ) VALUES (:id,:draft,:revision,:token,CAST(:request AS jsonb),
                              CAST(:intent AS jsonb),CAST(:impact AS jsonb),:actor,:expires)
                    """
                ),
                {
                    "id": preview_id,
                    "draft": graph_draft_id,
                    "revision": current.revision,
                    "token": token,
                    "request": json.dumps(
                        {"feature_key": feature_key, "stage_no": stage_no}, sort_keys=True
                    ),
                    "intent": json.dumps(next_intent, sort_keys=True),
                    "impact": json.dumps(impact, sort_keys=True),
                    "actor": actor_key,
                    "expires": expires_at,
                },
            )
            return GraphChangePreview(token, graph_draft_id, current.revision, expires_at, impact)

    def preview_catalog_rebase(
        self,
        graph_draft_id: uuid.UUID,
        *,
        expected_revision: int,
        actor_key: str,
        ttl: timedelta = timedelta(minutes=15),
    ) -> GraphChangePreview:
        """Preview the exact, non-guessing projection onto the running Catalog."""

        with self._engine.begin() as connection:
            current = self._get(connection, graph_draft_id, lock=True)
            self._require_owner(connection, graph_draft_id, actor_key)
            if current.revision != expected_revision:
                raise GraphDraftRevisionConflict(current.revision)
            self._require_editable(current)
            target_release_id = self._catalog_release_id(connection)
            if current.catalog_release_id == target_release_id:
                raise ValueError("Graph Draft already uses the current Catalog Release")

            next_intent, removals = self._workspace.rebase_intent(current.intent)
            next_derived = self._derive(next_intent, current.asset_context)
            self._reject_explicit_hard_incompatibility(next_derived)
            before = self._present_labels(current.derived_view)
            after = self._present_labels(next_derived)
            impact = {
                "change_type": "rebase_catalog",
                "from_catalog_release_id": str(current.catalog_release_id),
                "to_catalog_release_id": str(target_release_id),
                **removals,
                "removed_derived_occurrences": sorted(before - after),
                "added_derived_occurrences": sorted(after - before),
                "previous_blockers": current.derived_view["blockers"],
                "remaining_blockers": next_derived["blockers"],
            }
            preview_id = uuid.uuid4()
            token = sha256_hexdigest(
                {"preview_id": preview_id, "draft": graph_draft_id, "impact": impact}
            )
            expires_at = datetime.now(UTC) + ttl
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_graph_change_preview (
                      graph_change_preview_id,graph_draft_id,base_revision,impact_token,
                      request_document,next_intent_document,impact_document,created_by,expires_at
                    ) VALUES (:id,:draft,:revision,:token,CAST(:request AS jsonb),
                              CAST(:intent AS jsonb),CAST(:impact AS jsonb),:actor,:expires)
                    """
                ),
                {
                    "id": preview_id,
                    "draft": graph_draft_id,
                    "revision": current.revision,
                    "token": token,
                    "request": json.dumps(
                        {
                            "change_type": "rebase_catalog",
                            "target_catalog_release_id": str(target_release_id),
                            "target_catalog_identity": self._workspace.catalog_identity(),
                        },
                        sort_keys=True,
                    ),
                    "intent": json.dumps(next_intent, sort_keys=True),
                    "impact": json.dumps(impact, sort_keys=True),
                    "actor": actor_key,
                    "expires": expires_at,
                },
            )
            return GraphChangePreview(token, graph_draft_id, current.revision, expires_at, impact)

    def confirm_change_preview(
        self,
        graph_draft_id: uuid.UUID,
        impact_token: str,
        *,
        expected_revision: int,
        actor_key: str,
        idempotency_key: uuid.UUID,
    ) -> GraphDraftSnapshot:
        request = {
            "graph_draft_id": graph_draft_id,
            "impact_token": impact_token,
            "expected_revision": expected_revision,
        }
        fingerprint = sha256_hexdigest(request)
        with self._engine.begin() as connection:
            self._require_owner(connection, graph_draft_id, actor_key)
            self._lock_command_key(
                connection, actor_key, "confirm_graph_change_preview", idempotency_key
            )
            replay = self._command_replay(
                connection,
                actor_key,
                "confirm_graph_change_preview",
                idempotency_key,
                fingerprint,
            )
            if replay is not None:
                return self._get(
                    connection,
                    graph_draft_id,
                    revision=int(replay["revision"]),
                    lock=False,
                )
            preview = (
                connection.execute(
                    text(
                        "SELECT * FROM workspace.v022_graph_change_preview "
                        "WHERE impact_token=:token AND graph_draft_id=:draft FOR UPDATE"
                    ),
                    {"token": impact_token, "draft": graph_draft_id},
                )
                .mappings()
                .one_or_none()
            )
            if preview is None:
                raise LookupError("Graph change preview not found")
            if preview["consumed_at"] is not None or preview["expires_at"] <= datetime.now(UTC):
                raise ChangePreviewExpired("Graph change preview is expired or already consumed")
            current = self._get(connection, graph_draft_id, lock=True)
            preview_revision_matches = current.revision == preview["base_revision"]
            if current.revision != expected_revision or not preview_revision_matches:
                raise GraphDraftRevisionConflict(current.revision)
            self._require_editable(current)
            preview_request = cast(dict[str, Any], preview["request_document"])
            change_type = preview_request.get("change_type", "cascade_deselect")
            revision_catalog_release_id = current.catalog_release_id
            event_type = "confirm_cascade_deselect"
            if change_type == "rebase_catalog":
                target_release_id = self._catalog_release_id(connection)
                if str(target_release_id) != preview_request.get("target_catalog_release_id"):
                    raise ChangePreviewExpired(
                        "Catalog changed after the rebase preview; create a new preview"
                    )
                revision_catalog_release_id = target_release_id
                event_type = "confirm_rebase_catalog"
            else:
                self._require_current_catalog(connection, current)
            next_intent = cast(dict[str, Any], preview["next_intent_document"])
            derived = self._derive(next_intent, current.asset_context)
            self._reject_explicit_hard_incompatibility(derived)
            next_revision = current.revision + 1
            self._insert_revision(
                connection,
                graph_draft_id,
                next_revision,
                revision_catalog_release_id,
                next_intent,
                derived,
                current.asset_context,
                current.resolved_data_binding,
                actor_key,
            )
            connection.execute(
                text(
                    "UPDATE workspace.v022_graph_draft SET current_revision=:revision,"
                    "catalog_release_id=:release,"
                    "last_compiled_research_graph_id=NULL,"
                    "last_compile_command_result_id=NULL,updated_at=now() "
                    "WHERE graph_draft_id=:draft"
                ),
                {
                    "revision": next_revision,
                    "release": revision_catalog_release_id,
                    "draft": graph_draft_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE workspace.v022_graph_change_preview SET consumed_at=now() "
                    "WHERE graph_change_preview_id=:id"
                ),
                {"id": preview["graph_change_preview_id"]},
            )
            self._insert_event(
                connection,
                graph_draft_id,
                current.revision,
                next_revision,
                actor_key,
                idempotency_key,
                fingerprint,
                event_type,
                {"impact_token": impact_token},
                applied=True,
            )
            response = {"graph_draft_id": str(graph_draft_id), "revision": next_revision}
            self._insert_command_result(
                connection,
                actor_key,
                "confirm_graph_change_preview",
                idempotency_key,
                fingerprint,
                response,
            )
            return self._get(connection, graph_draft_id, lock=False)

    def replay_compile(
        self,
        graph_draft_id: uuid.UUID,
        *,
        expected_revision: int,
        actor_key: str,
        idempotency_key: uuid.UUID,
    ) -> GraphDraftCompileResult | None:
        fingerprint = sha256_hexdigest(
            {
                "graph_draft_id": graph_draft_id,
                "expected_revision": expected_revision,
            }
        )
        with self._engine.connect() as connection:
            self._require_owner(connection, graph_draft_id, actor_key)
            replay = self._command_replay(
                connection,
                actor_key,
                "compile_graph_draft",
                idempotency_key,
                fingerprint,
            )
        return self._compile_result(replay) if replay is not None else None

    def compile(
        self,
        graph_draft_id: uuid.UUID,
        *,
        expected_revision: int,
        actor_key: str,
        idempotency_key: uuid.UUID,
    ) -> GraphDraftCompileResult:
        request = {
            "graph_draft_id": graph_draft_id,
            "expected_revision": expected_revision,
        }
        fingerprint = sha256_hexdigest(request)
        with self._engine.begin() as connection:
            self._require_owner(connection, graph_draft_id, actor_key)
            self._lock_command_key(
                connection, actor_key, "compile_graph_draft", idempotency_key
            )
            replay = self._command_replay(
                connection,
                actor_key,
                "compile_graph_draft",
                idempotency_key,
                fingerprint,
            )
            if replay is not None:
                return self._compile_result(replay)
            snapshot = self._get(connection, graph_draft_id, lock=True)
            if snapshot.revision != expected_revision:
                raise GraphDraftRevisionConflict(snapshot.revision)
            self._require_editable(snapshot)
            self._require_current_catalog(connection, snapshot)

            derived = self._derive(snapshot.intent, snapshot.asset_context)
            if (
                derived["derived_state_fingerprint"]
                != snapshot.derived_view["derived_state_fingerprint"]
            ):
                raise ValueError("Stored Graph Draft Derived View does not reproduce")
            blockers = tuple(cast(list[dict[str, Any]], derived["blockers"]))
            if blockers:
                raise GraphDraftCompilationBlocked(blockers)
            release_fingerprint = connection.scalar(
                text(
                    "SELECT release_fingerprint FROM workspace.v022_catalog_release "
                    "WHERE catalog_release_id=:release"
                ),
                {"release": snapshot.catalog_release_id},
            )
            intent = self._compiler_intent(
                snapshot,
                derived,
                cast(str, release_fingerprint),
                sha256_hexdigest(snapshot.asset_context),
                sha256_hexdigest(snapshot.resolved_data_binding),
            )
            bridge = self._compiler.ensure_bridge_draft(
                graph_draft_id=graph_draft_id,
                graph_draft_revision=snapshot.revision,
                catalog_release_id=snapshot.catalog_release_id,
                intent=intent,
                actor_key=actor_key,
            )
            asset_context_snapshot = AssetContextSnapshot.model_validate(
                snapshot.asset_context
            )
            resolved_data_binding_snapshot = ResolvedDataBindingSnapshot.model_validate(
                snapshot.resolved_data_binding
            )
            outcome = self._compiler.compile(
                bridge.draft_intent_id,
                asset_context_snapshot=asset_context_snapshot,
                resolved_data_binding_snapshot=resolved_data_binding_snapshot,
            )
            result = GraphDraftCompileResult(
                graph_draft_id,
                snapshot.revision,
                bridge.draft_intent_id,
                outcome.compile_attempt_id,
                outcome.compiled_research_graph_id,
                outcome.graph_artifact_id,
                outcome.graph_fingerprint,
                outcome.reused,
                outcome.compiled_execution_data_context_id,
                outcome.execution_data_context_artifact_id,
                outcome.execution_data_context_fingerprint,
                outcome.execution_data_context_reused,
                tuple(
                    GraphDraftDefenseExecutionContext.from_publication(item)
                    for item in outcome.defense_execution_contexts
                ),
                cast(str, snapshot.derived_view["selection_fingerprint"]),
            )
            command_result_id = self._insert_command_result(
                connection,
                actor_key,
                "compile_graph_draft",
                idempotency_key,
                fingerprint,
                result.to_dict(),
            )
            connection.execute(
                text(
                    "UPDATE workspace.v022_graph_draft "
                    "SET last_compiled_research_graph_id=:graph,"
                    "last_compile_command_result_id=:command,updated_at=now() "
                    "WHERE graph_draft_id=:draft"
                ),
                {
                    "graph": outcome.compiled_research_graph_id,
                    "command": command_result_id,
                    "draft": graph_draft_id,
                },
            )
            return result

    @staticmethod
    def _compiler_intent(
        snapshot: GraphDraftSnapshot,
        derived: dict[str, Any],
        release_fingerprint: str,
        asset_context_fingerprint: str,
        resolved_data_binding_fingerprint: str,
    ) -> DraftIntent:
        preset_selections = cast(
            dict[str, list[str]],
            snapshot.intent.get("aggregation_parameter_preset_keys", {}),
        )
        target_selections = cast(
            dict[str, list[str]],
            snapshot.intent.get("aggregation_target_keys", {}),
        )
        training_selections = cast(
            dict[str, list[str]],
            snapshot.intent.get("aggregation_training_preset_keys", {}),
        )
        aggregations: list[AggregationSelection] = []
        for option in cast(list[dict[str, Any]], derived["aggregations"]):
            if not option["selected"]:
                continue
            available = tuple(cast(list[str], option["parameter_presets"]))
            selected = tuple(preset_selections.get(option["family_key"], []))
            if not selected and available:
                raise GraphDraftCompilationBlocked(
                    (
                        {
                            "layer": "aggregation",
                            "object_key": option["family_key"],
                            "reason_codes": ["aggregation_parameter_preset_required"],
                        },
                    )
                )
            aggregations.append(
                AggregationSelection(
                    family_key=option["family_key"],
                    parameter_preset_keys=selected,
                    target_keys=tuple(target_selections.get(option["family_key"], [])),
                    training_preset_keys=tuple(
                        training_selections.get(option["family_key"], [])
                    ),
                )
            )
        return DraftIntent(
            catalog_release_fingerprint=release_fingerprint,
            asset_context_fingerprint=asset_context_fingerprint,
            resolved_data_binding_fingerprint=resolved_data_binding_fingerprint,
            frequency=snapshot.intent["frequency"],
            aggregation_inputs=tuple(derived["aggregation_inputs"]),
            explicit_features=tuple(
                FeatureSelection(feature_key=item["feature_key"], visible_stage=item["stage_no"])
                for item in snapshot.intent["explicit_features"]
            ),
            aggregations=tuple(aggregations),
            strategy_keys=tuple(snapshot.intent["strategy_keys"]),
            strategy_parameter_preset_keys=tuple(
                (strategy_key, tuple(preset_keys))
                for strategy_key, preset_keys in sorted(
                    cast(
                        dict[str, list[str]],
                        snapshot.intent.get("strategy_parameter_preset_keys", {}),
                    ).items()
                )
            ),
            defense_keys=tuple(snapshot.intent["defense_keys"]),
        )

    @staticmethod
    def _compile_result(document: dict[str, Any]) -> GraphDraftCompileResult:
        return GraphDraftCompileResult(
            uuid.UUID(document["graph_draft_id"]),
            int(document["graph_draft_revision"]),
            uuid.UUID(document["draft_intent_id"]),
            uuid.UUID(document["compile_attempt_id"]),
            uuid.UUID(document["compiled_research_graph_id"]),
            uuid.UUID(document["graph_artifact_id"]),
            document["graph_fingerprint"],
            bool(document["reused"]),
            (
                uuid.UUID(document["compiled_execution_data_context_id"])
                if document.get("compiled_execution_data_context_id") is not None
                else None
            ),
            (
                uuid.UUID(document["execution_data_context_artifact_id"])
                if document.get("execution_data_context_artifact_id") is not None
                else None
            ),
            document.get("execution_data_context_fingerprint"),
            document.get("execution_data_context_reused"),
            tuple(
                GraphDraftDefenseExecutionContext.from_dict(item)
                for item in document.get("defense_execution_contexts", [])
            ),
            document.get("selection_fingerprint"),
        )

    def _mutate(
        self, current: GraphDraftSnapshot, event_type: str, event: dict[str, Any]
    ) -> dict[str, Any]:
        intent = deepcopy(current.intent)
        feature_event_types = {
            "select_feature_occurrence",
            "deselect_feature_occurrence",
            "batch_select_feature_occurrences",
            "batch_deselect_feature_occurrences",
        }
        if event_type in {
            "select_all_legal_feature_occurrences",
            "clear_stage_feature_occurrences",
        }:
            stage_no = int(event["stage_no"])
            if stage_no not in {1, 2, 3}:
                raise ValueError("Processing stage must be 1, 2, or 3")
            if event_type == "clear_stage_feature_occurrences":
                intent["explicit_features"] = [
                    item
                    for item in intent["explicit_features"]
                    if int(item["stage_no"]) != stage_no
                ]
            else:
                stage = next(
                    item
                    for item in current.derived_view["stages"]
                    if int(item["stage_no"]) == stage_no
                )
                selected = intent["explicit_features"]
                for family in stage["families"]:
                    for occurrence in family["variants"]:
                        candidate = {
                            "feature_key": str(occurrence["feature_key"]),
                            "stage_no": stage_no,
                        }
                        if (
                            occurrence["availability"] != "hard_incompatible"
                            and candidate not in selected
                        ):
                            selected.append(candidate)
                selected.sort(key=lambda item: (item["stage_no"], item["feature_key"]))
        elif event_type in feature_event_types:
            batch = event_type.startswith("batch_")
            raw_occurrences = event.get("occurrences") if batch else [event]
            if not isinstance(raw_occurrences, list) or not raw_occurrences:
                raise ValueError("Feature batch must contain at least one occurrence")
            if len(raw_occurrences) > 500:
                raise ValueError("Feature batch cannot exceed 500 occurrences")
            occurrences = [
                (str(item["feature_key"]), int(item["stage_no"]))
                for item in raw_occurrences
                if isinstance(item, dict)
            ]
            if len(occurrences) != len(raw_occurrences):
                raise ValueError("Every Feature batch item must be an occurrence object")
            if len(set(occurrences)) != len(occurrences):
                raise ValueError("Feature batch contains duplicate occurrences")
            candidates = [
                {"feature_key": feature_key, "stage_no": stage_no}
                for feature_key, stage_no in occurrences
            ]
            selected = intent["explicit_features"]
            selecting = event_type in {
                "select_feature_occurrence",
                "batch_select_feature_occurrences",
            }
            if selecting:
                for feature_key, stage_no in occurrences:
                    self._occurrence(current.derived_view, feature_key, stage_no)
                selected.extend(candidate for candidate in candidates if candidate not in selected)
                selected.sort(key=lambda item: (item["stage_no"], item["feature_key"]))
            else:
                removal_labels = {
                    f"{feature_key}@{stage_no}" for feature_key, stage_no in occurrences
                }
                unresolved_locks: set[str] = set()
                for feature_key, stage_no in occurrences:
                    occurrence = self._occurrence(current.derived_view, feature_key, stage_no)
                    unresolved_locks.update(
                        set(cast(list[str], occurrence["locked_by"])) - removal_labels
                    )
                if unresolved_locks:
                    raise CascadeConfirmationRequired(tuple(sorted(unresolved_locks)))
                intent["explicit_features"] = [item for item in selected if item not in candidates]
        elif event_type in {"select_aggregation_family", "deselect_aggregation_family"}:
            family_key = str(event["family_key"])
            selected_families = intent["aggregation_family_keys"]
            if event_type == "select_aggregation_family" and family_key not in selected_families:
                selected_families.append(family_key)
                selected_families.sort()
            elif event_type == "deselect_aggregation_family":
                intent["aggregation_family_keys"] = [
                    item for item in selected_families if item != family_key
                ]
                intent.setdefault("aggregation_parameter_preset_keys", {}).pop(family_key, None)
                intent.setdefault("aggregation_target_keys", {}).pop(family_key, None)
                intent.setdefault("aggregation_training_preset_keys", {}).pop(
                    family_key, None
                )
        elif event_type == "set_aggregation_parameter_presets":
            family_key = str(event["family_key"])
            if family_key not in intent["aggregation_family_keys"]:
                raise ValueError("Cannot configure an unselected Aggregation Family")
            preset_keys = [str(item) for item in event["preset_keys"]]
            if len(set(preset_keys)) != len(preset_keys):
                raise ValueError("Duplicate Aggregation parameter preset")
            intent.setdefault("aggregation_parameter_preset_keys", {})[family_key] = sorted(
                preset_keys
            )
        elif event_type in {
            "set_aggregation_targets",
            "set_aggregation_training_presets",
        }:
            family_key = str(event["family_key"])
            if family_key not in intent["aggregation_family_keys"]:
                raise ValueError("Cannot configure an unselected Aggregation Family")
            value_key = "target_keys" if event_type == "set_aggregation_targets" else "preset_keys"
            values = [str(item) for item in event[value_key]]
            if len(set(values)) != len(values):
                raise ValueError("Duplicate Aggregation supervised axis value")
            intent_key = (
                "aggregation_target_keys"
                if event_type == "set_aggregation_targets"
                else "aggregation_training_preset_keys"
            )
            intent.setdefault(intent_key, {})[family_key] = sorted(values)
        elif event_type in {"select_strategy", "deselect_strategy"}:
            strategy_key = str(event["strategy_key"])
            selected_strategies = intent["strategy_keys"]
            if event_type == "select_strategy" and strategy_key not in selected_strategies:
                selected_strategies.append(strategy_key)
                selected_strategies.sort()
            elif event_type == "deselect_strategy":
                intent["strategy_keys"] = [
                    item for item in selected_strategies if item != strategy_key
                ]
                intent.setdefault("strategy_parameter_preset_keys", {}).pop(
                    strategy_key, None
                )
        elif event_type == "set_strategy_parameter_presets":
            strategy_key = str(event["strategy_key"])
            preset_keys = [str(item) for item in event["preset_keys"]]
            if len(set(preset_keys)) != len(preset_keys):
                raise ValueError("Duplicate Strategy parameter preset")
            selected_strategies = intent["strategy_keys"]
            selections = intent.setdefault("strategy_parameter_preset_keys", {})
            if preset_keys:
                if strategy_key not in selected_strategies:
                    selected_strategies.append(strategy_key)
                    selected_strategies.sort()
                selections[strategy_key] = sorted(preset_keys)
            else:
                intent["strategy_keys"] = [
                    item for item in selected_strategies if item != strategy_key
                ]
                selections.pop(strategy_key, None)
        elif event_type == "select_all_compatible_strategy_presets":
            compatible_strategies: list[str] = []
            compatible_selections: dict[str, list[str]] = {}
            for strategy in current.derived_view["strategies"]:
                selectable_presets = sorted(
                    str(preset["preset_key"])
                    for preset in strategy.get("parameter_presets", [])
                    if preset.get("selectable")
                )
                if not selectable_presets:
                    continue
                strategy_key = str(strategy["variant_key"])
                if strategy_key not in compatible_strategies:
                    compatible_strategies.append(strategy_key)
                compatible_selections[strategy_key] = selectable_presets
            compatible_strategies.sort()
            intent["strategy_keys"] = compatible_strategies
            intent["strategy_parameter_preset_keys"] = compatible_selections
        elif event_type == "clear_strategy_presets":
            intent["strategy_keys"] = []
            intent["strategy_parameter_preset_keys"] = {}
        elif event_type in {"select_defense", "deselect_defense"}:
            defense_key = str(event["defense_key"])
            if defense_key != "none":
                raise ValueError(
                    "defense_retired: v0.22 currently supports only the no-defense branch"
                )
            selected_defenses = intent["defense_keys"]
            if event_type == "select_defense" and defense_key not in selected_defenses:
                selected_defenses.append(defense_key)
                selected_defenses.sort()
            elif event_type == "deselect_defense":
                intent["defense_keys"] = [item for item in selected_defenses if item != defense_key]
        elif event_type in {"select_all_compatible_defenses", "clear_defenses"}:
            intent["defense_keys"] = ["none"]
        elif event_type == "set_frequency":
            frequency = str(event["frequency"])
            if frequency not in {"weekly", "monthly"}:
                raise ValueError("Frequency must be weekly or monthly")
            intent["frequency"] = frequency
        elif event_type == "set_asset_selection":
            # Asset identity is revision-scoped and resolved transactionally by apply_event.
            pass
        else:
            raise ValueError(f"Unsupported Graph Draft event: {event_type}")
        return intent

    def _derive(
        self,
        intent: dict[str, Any],
        asset_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        preset_selections = cast(
            dict[str, list[str]],
            intent.get("aggregation_parameter_preset_keys", {}),
        )
        strategy_preset_selections = cast(
            dict[str, list[str]],
            intent.get("strategy_parameter_preset_keys", {}),
        )
        target_selections = cast(
            dict[str, list[str]], intent.get("aggregation_target_keys", {})
        )
        training_selections = cast(
            dict[str, list[str]],
            intent.get("aggregation_training_preset_keys", {}),
        )
        return self._workspace.preview(
            WorkspacePreviewIntent(
                tuple(
                    ExplicitFeature(item["feature_key"], item["stage_no"])
                    for item in intent["explicit_features"]
                ),
                tuple(intent["aggregation_family_keys"]),
                intent["frequency"],
                tuple((key, tuple(values)) for key, values in sorted(preset_selections.items())),
                tuple(intent["strategy_keys"]),
                tuple(intent["defense_keys"]),
                tuple(
                    (key, tuple(values))
                    for key, values in sorted(strategy_preset_selections.items())
                ),
                tuple((key, tuple(values)) for key, values in sorted(target_selections.items())),
                tuple(
                    (key, tuple(values))
                    for key, values in sorted(training_selections.items())
                ),
            ),
            asset_context=asset_context,
        )

    def _initial_intent(
        self, frequency: Literal["weekly", "monthly"]
    ) -> dict[str, Any]:
        return {
            "explicit_features": [],
            "aggregation_family_keys": [],
            "aggregation_parameter_preset_keys": {},
            "aggregation_target_keys": {},
            "aggregation_training_preset_keys": {},
            "frequency": frequency,
            "strategy_keys": [],
            "strategy_parameter_preset_keys": {},
            "defense_keys": [],
        }

    @staticmethod
    def _require_editable(snapshot: GraphDraftSnapshot) -> None:
        if snapshot.status != "draft":
            raise GraphDraftLocked(snapshot.revision)

    @staticmethod
    def _require_owner(
        connection: Connection, graph_draft_id: uuid.UUID, actor_key: str
    ) -> None:
        owner = connection.scalar(
            text(
                "SELECT researcher_key FROM workspace.v022_graph_draft "
                "WHERE graph_draft_id=:draft"
            ),
            {"draft": graph_draft_id},
        )
        if owner != actor_key:
            raise ValueError("Graph Draft actor does not own the current research")

    def _catalog_release_id(self, connection: Connection) -> uuid.UUID:
        identity = self._workspace.catalog_identity()
        release_id = connection.scalar(
            text(
                """
                SELECT catalog_release_id FROM workspace.v022_catalog_release
                WHERE release_key=:key AND source_manifest_hash=:source
                """
            ),
            {
                "key": identity["release_key"],
                "source": identity["source_manifest_hash"],
            },
        )
        if release_id is None:
            raise LookupError("Workspace Catalog Release has not been published")
        return cast(uuid.UUID, release_id)

    def _require_current_catalog(
        self, connection: Connection, snapshot: GraphDraftSnapshot
    ) -> None:
        current_release_id = self._catalog_release_id(connection)
        if snapshot.catalog_release_id != current_release_id:
            raise GraphCatalogRebaseRequired(current_release_id)

    def _get(
        self,
        connection: Connection,
        graph_draft_id: uuid.UUID,
        *,
        revision: int | None = None,
        lock: bool,
    ) -> GraphDraftSnapshot:
        root_sql = "SELECT * FROM workspace.v022_graph_draft WHERE graph_draft_id=:draft"
        if lock:
            root_sql += " FOR UPDATE"
        root = (
            connection.execute(text(root_sql), {"draft": graph_draft_id}).mappings().one_or_none()
        )
        if root is None:
            raise LookupError("Graph Draft not found")
        selected_revision = int(root["current_revision"] if revision is None else revision)
        row = (
            connection.execute(
                text(
                    """
                SELECT * FROM workspace.v022_graph_draft_revision
                WHERE graph_draft_id=:draft AND revision=:revision
                """
                ),
                {"draft": graph_draft_id, "revision": selected_revision},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError("Graph Draft revision not found")
        return self._snapshot(root, row)

    @staticmethod
    def _snapshot(root: RowMapping, revision: RowMapping) -> GraphDraftSnapshot:
        return GraphDraftSnapshot(
            root["graph_draft_id"],
            revision["catalog_release_id"],
            root["draft_key"],
            root["name"],
            int(revision["revision"]),
            root["status"],
            cast(dict[str, Any], revision["asset_context_document"]),
            cast(dict[str, Any], revision["resolved_data_binding_document"]),
            cast(dict[str, Any], revision["intent_document"]),
            cast(dict[str, Any], revision["derived_view_document"]),
            root["cloned_from_graph_draft_id"],
            root["cloned_from_revision"],
        )

    @staticmethod
    def _insert_revision(
        connection: Connection,
        graph_draft_id: uuid.UUID,
        revision: int,
        catalog_release_id: uuid.UUID,
        intent: dict[str, Any],
        derived: dict[str, Any],
        asset_context: dict[str, Any],
        resolved_data_binding: dict[str, Any],
        actor_key: str,
        *,
        research_round_id: uuid.UUID | None = None,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_graph_draft_revision (
                  graph_draft_id,revision,catalog_release_id,intent_document,selection_fingerprint,
                  derived_state_fingerprint,derived_view_document,
                  asset_context_fingerprint,resolved_data_binding_fingerprint,
                  asset_context_document,resolved_data_binding_document,created_by
                ) VALUES (:draft,:revision,:release,CAST(:intent AS jsonb),:selection,
                          :derived_fingerprint,CAST(:derived AS jsonb),
                          :asset_fingerprint,:binding_fingerprint,
                          CAST(:asset_document AS jsonb),CAST(:binding_document AS jsonb),:actor)
                """
            ),
            {
                "draft": graph_draft_id,
                "revision": revision,
                "release": catalog_release_id,
                "intent": json.dumps(intent, sort_keys=True),
                "selection": derived["selection_fingerprint"],
                "derived_fingerprint": derived["derived_state_fingerprint"],
                "derived": json.dumps(derived, sort_keys=True),
                "asset_fingerprint": sha256_hexdigest(asset_context),
                "binding_fingerprint": sha256_hexdigest(resolved_data_binding),
                "asset_document": json.dumps(asset_context, sort_keys=True),
                "binding_document": json.dumps(resolved_data_binding, sort_keys=True),
                "actor": actor_key,
            },
        )
        if research_round_id is None:
            research_round_id = connection.scalar(
                text(
                    "SELECT research_round_id "
                    "FROM workspace.v022_graph_draft_revision_round "
                    "WHERE graph_draft_id=:draft AND revision<:revision "
                    "ORDER BY revision DESC LIMIT 1"
                ),
                {"draft": graph_draft_id, "revision": revision},
            )
        if research_round_id is None:
            raise RuntimeError("Graph Draft revision has no Research Round")
        connection.execute(
            text(
                "INSERT INTO workspace.v022_graph_draft_revision_round ("
                "graph_draft_id,revision,research_round_id) "
                "VALUES (:draft,:revision,:round)"
            ),
            {
                "draft": graph_draft_id,
                "revision": revision,
                "round": research_round_id,
            },
        )

    @staticmethod
    def _create_research_round(
        connection: Connection,
        *,
        root_graph_draft_id: uuid.UUID,
        ordinal: int,
        actor_key: str,
        reset_idempotency_key: uuid.UUID | None = None,
    ) -> uuid.UUID:
        research_round_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO workspace.v022_research_round ("
                "research_round_id,root_graph_draft_id,ordinal,status,created_by,"
                "reset_idempotency_key) VALUES (:round,:draft,:ordinal,'active',"
                ":actor,:reset_key)"
            ),
            {
                "round": research_round_id,
                "draft": root_graph_draft_id,
                "ordinal": ordinal,
                "actor": actor_key,
                "reset_key": reset_idempotency_key,
            },
        )
        return research_round_id

    @staticmethod
    def _active_asset_registry_release_id(connection: Connection) -> uuid.UUID:
        return require_active_v022_workspace_identity(
            connection
        ).asset_registry_release_id

    @staticmethod
    def _insert_event(
        connection: Connection,
        graph_draft_id: uuid.UUID,
        base_revision: int,
        resulting_revision: int,
        actor_key: str,
        idempotency_key: uuid.UUID,
        request_fingerprint: str,
        event_type: str,
        event: dict[str, Any],
        *,
        applied: bool,
        response_extra: dict[str, Any] | None = None,
    ) -> None:
        response = {"graph_draft_id": str(graph_draft_id), "revision": resulting_revision}
        if response_extra:
            response.update(response_extra)
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_graph_draft_event (
                  graph_draft_event_id,graph_draft_id,base_revision,resulting_revision,
                  event_type,event_document,actor_key,idempotency_key,request_fingerprint,
                  response_document,applied
                ) VALUES (:id,:draft,:base,:resulting,:type,CAST(:event AS jsonb),:actor,
                          :key,:fingerprint,CAST(:response AS jsonb),:applied)
                """
            ),
            {
                "id": uuid.uuid4(),
                "draft": graph_draft_id,
                "base": base_revision,
                "resulting": resulting_revision,
                "type": event_type,
                "event": json.dumps(event, sort_keys=True),
                "actor": actor_key,
                "key": idempotency_key,
                "fingerprint": request_fingerprint,
                "response": json.dumps(response, sort_keys=True),
                "applied": applied,
            },
        )

    @staticmethod
    def _lock_command_key(
        connection: Connection,
        actor_key: str,
        command_kind: str,
        idempotency_key: uuid.UUID,
    ) -> None:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{actor_key}:{command_kind}:{idempotency_key}"},
        )

    @staticmethod
    def _command_replay(
        connection: Connection,
        actor_key: str,
        command_kind: str,
        idempotency_key: uuid.UUID,
        request_fingerprint: str,
    ) -> dict[str, Any] | None:
        row = (
            connection.execute(
                text(
                    """
                SELECT request_fingerprint,response_document FROM workspace.v022_command_result
                WHERE actor_key=:actor AND command_kind=:kind AND idempotency_key=:key
                """
                ),
                {"actor": actor_key, "kind": command_kind, "key": idempotency_key},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if row["request_fingerprint"] != request_fingerprint:
            raise GraphDraftIdempotencyConflict(
                "Idempotency key was already used for another Graph Draft command"
            )
        return cast(dict[str, Any], row["response_document"])

    @staticmethod
    def _insert_command_result(
        connection: Connection,
        actor_key: str,
        command_kind: str,
        idempotency_key: uuid.UUID,
        request_fingerprint: str,
        response: dict[str, Any],
    ) -> uuid.UUID:
        command_result_id = uuid.uuid4()
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_command_result (
                  command_result_id,actor_key,command_kind,idempotency_key,
                  request_fingerprint,response_document
                ) VALUES (:id,:actor,:kind,:key,:fingerprint,CAST(:response AS jsonb))
                """
            ),
            {
                "id": command_result_id,
                "actor": actor_key,
                "kind": command_kind,
                "key": idempotency_key,
                "fingerprint": request_fingerprint,
                "response": json.dumps(response, sort_keys=True),
            },
        )
        return command_result_id

    @staticmethod
    def _occurrence(derived: dict[str, Any], feature_key: str, stage_no: int) -> dict[str, Any]:
        for stage in derived["stages"]:
            if stage["stage_no"] != stage_no:
                continue
            for family in stage["families"]:
                for variant in family["variants"]:
                    if variant["feature_key"] == feature_key:
                        return cast(dict[str, Any], variant)
        raise ValueError(f"Unknown Feature occurrence: {feature_key}@{stage_no}")

    @staticmethod
    def _reject_explicit_hard_incompatibility(derived: dict[str, Any]) -> None:
        for stage in derived["stages"]:
            for family in stage["families"]:
                for variant in family["variants"]:
                    if variant["is_explicit"] and variant["availability"] == "hard_incompatible":
                        raise ValueError(
                            f"Feature occurrence is incompatible: "
                            f"{variant['feature_key']}@{variant['stage_no']}"
                        )

    @staticmethod
    def _explicit_labels(intent: dict[str, Any]) -> set[str]:
        return {f"{item['feature_key']}@{item['stage_no']}" for item in intent["explicit_features"]}

    @staticmethod
    def _present_labels(derived: dict[str, Any]) -> set[str]:
        labels: set[str] = set()
        for stage in derived["stages"]:
            for family in stage["families"]:
                for variant in family["variants"]:
                    if variant["is_present"]:
                        labels.add(f"{variant['feature_key']}@{variant['stage_no']}")
        return labels


def _family_matches_search(family: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    values = [family["family_key"], family["name"]]
    for variant in family["variants"]:
        values.extend(
            (
                variant["feature_key"],
                variant["name"],
                variant.get("research_hypothesis", ""),
                variant.get("payload_contract_key", ""),
            )
        )
    return any(query in str(value).casefold() for value in values)


_STAGE_PRESENTATION_FIELDS = frozenset(
    {
        "formula_identity",
        "semantic_role",
        "unit",
        "input_feature_keys",
        "output_semantics",
    }
)


def _has_stage_presentation_contract(derived_view: dict[str, Any]) -> bool:
    stages = derived_view.get("stages")
    if not isinstance(stages, list) or len(stages) != 4:
        return False
    for stage in stages:
        if not isinstance(stage, dict):
            return False
        families = stage.get("families")
        if not isinstance(families, list):
            return False
        for family in families:
            if not isinstance(family, dict):
                return False
            variants = family.get("variants")
            if not isinstance(variants, list):
                return False
            if any(
                not isinstance(variant, dict)
                or not _STAGE_PRESENTATION_FIELDS.issubset(variant)
                for variant in variants
            ):
                return False
    return True


def _with_legacy_aggregation_presentation(
    derived_view: dict[str, Any],
) -> dict[str, Any]:
    """Project pre-ensemble deterministic views into the current API shape."""

    aggregations = derived_view.get("aggregations")
    if not isinstance(aggregations, list):
        return derived_view
    if all(
        isinstance(option, dict)
        and "internal_member_count" in option
        and all(
            isinstance(preset, dict)
            and "selectable" in preset
            and "reason_codes" in preset
            for preset in option.get("parameter_preset_definitions", [])
        )
        for option in aggregations
    ):
        return derived_view

    enriched = deepcopy(derived_view)
    for option in enriched["aggregations"]:
        if not isinstance(option, dict):
            continue
        option.setdefault("internal_member_count", 0)
        for preset in option.get("parameter_preset_definitions", []):
            if not isinstance(preset, dict):
                continue
            preset.setdefault("selectable", True)
            preset.setdefault("reason_codes", [])
    return enriched


def _family_matches_selection(family: dict[str, Any], selection_filter: str) -> bool:
    if selection_filter == "all":
        return True
    if selection_filter == "selected":
        return int(family["explicit_count"]) > 0
    if selection_filter == "locked":
        return int(family["required_count"]) > 0
    raise ValueError(f"Unknown Family selection filter: {selection_filter}")


def _family_matches_availability(family: dict[str, Any], availability_filter: str) -> bool:
    if availability_filter == "all":
        return True
    if availability_filter not in {
        "ready",
        "requires_ancestors",
        "hard_incompatible",
    }:
        raise ValueError(f"Unknown Family availability filter: {availability_filter}")
    return any(variant["availability"] == availability_filter for variant in family["variants"])


def _encode_family_cursor(view_token: str, query_fingerprint: str, offset: int) -> str:
    payload = json.dumps(
        {
            "view_token": view_token,
            "query_fingerprint": query_fingerprint,
            "offset": offset,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_family_cursor(cursor: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid Family page cursor") from exc
    if not isinstance(value, dict):
        raise ValueError("Invalid Family page cursor")
    return cast(dict[str, Any], value)
