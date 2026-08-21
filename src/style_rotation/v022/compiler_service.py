# ruff: noqa: E501
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from functools import partial
from typing import Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import (
    ArtifactService,
    DependencyInput,
    PublicationResult,
)
from style_rotation.v022.defense_context import (
    DefenseExecutionContextPublication,
    DefenseExecutionContextService,
)
from style_rotation.v022.execution_context import (
    ExecutionDataContextPublication,
    ExecutionDataContextService,
    ResolvedDataBindingSnapshot,
)
from style_rotation.v022.graph import (
    AggregationFeatureTaxonomyEntrySpec,
    AggregationFeatureTaxonomySpec,
    AggregationSpec,
    AssetContextSnapshot,
    CompilationResult,
    DefenseAssetContextSpec,
    DefenseSpec,
    DraftIntent,
    FeatureSpec,
    GraphCatalog,
    NodeInputSpec,
    NodeSpec,
    StrategyParameterPresetSpec,
    StrategySpec,
    compile_intent,
    strategy_branch_identity_document,
    strategy_required_instrument_types,
)


@dataclass(frozen=True, slots=True)
class DraftIdentity:
    draft_intent_id: uuid.UUID
    draft_key: str
    revision: int
    intent_fingerprint: str


@dataclass(frozen=True, slots=True)
class CompileOutcome:
    compile_attempt_id: uuid.UUID
    compiled_research_graph_id: uuid.UUID
    graph_artifact_id: uuid.UUID
    graph_fingerprint: str
    reused: bool
    compiled_execution_data_context_id: uuid.UUID | None = None
    execution_data_context_artifact_id: uuid.UUID | None = None
    execution_data_context_fingerprint: str | None = None
    execution_data_context_reused: bool | None = None
    defense_execution_contexts: tuple[DefenseExecutionContextPublication, ...] = ()


class GraphCompilerService:
    def __init__(self, engine: Engine, *, compiler_version: str = "v022-compiler-m2-v1") -> None:
        self._engine = engine
        self._compiler_version = compiler_version

    def create_draft(
        self,
        *,
        catalog_release_id: uuid.UUID,
        draft_key: str,
        intent: DraftIntent,
        actor_key: str,
    ) -> DraftIdentity:
        fingerprint = sha256_hexdigest(intent.model_dump(mode="json"))
        draft_id = uuid.uuid4()
        with self._engine.begin() as connection:
            release_fingerprint = connection.scalar(
                text(
                    "SELECT release_fingerprint FROM workspace.v022_catalog_release "
                    "WHERE catalog_release_id=:release"
                ),
                {"release": catalog_release_id},
            )
            if release_fingerprint != intent.catalog_release_fingerprint:
                raise ValueError("Draft Intent Catalog Release fingerprint mismatch")
            revision = int(
                connection.scalar(
                    text(
                        "SELECT coalesce(max(revision),0)+1 FROM workspace.v022_draft_intent "
                        "WHERE draft_key=:key"
                    ),
                    {"key": draft_key},
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_draft_intent (
                      draft_intent_id,catalog_release_id,draft_key,revision,status,
                      intent_document,intent_fingerprint,created_by
                    ) VALUES (:id,:release,:key,:revision,'draft',CAST(:intent AS jsonb),:fingerprint,:actor)
                    """
                ),
                {
                    "id": draft_id,
                    "release": catalog_release_id,
                    "key": draft_key,
                    "revision": revision,
                    "intent": json.dumps(intent.model_dump(mode="json"), sort_keys=True),
                    "fingerprint": fingerprint,
                    "actor": actor_key,
                },
            )
        return DraftIdentity(draft_id, draft_key, revision, fingerprint)

    def ensure_bridge_draft(
        self,
        *,
        graph_draft_id: uuid.UUID,
        graph_draft_revision: int,
        catalog_release_id: uuid.UUID,
        intent: DraftIntent,
        actor_key: str,
    ) -> DraftIdentity:
        """Create or verify the immutable compiler input for one exact Graph revision."""
        document = intent.model_dump(mode="json")
        fingerprint = sha256_hexdigest(document)
        draft_key = f"graph_draft:{graph_draft_id}"
        draft_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bird:v0.22:graph-draft:{graph_draft_id}:revision:{graph_draft_revision}",
        )
        with self._engine.begin() as connection:
            release_fingerprint = connection.scalar(
                text(
                    "SELECT release_fingerprint FROM workspace.v022_catalog_release "
                    "WHERE catalog_release_id=:release"
                ),
                {"release": catalog_release_id},
            )
            if release_fingerprint != intent.catalog_release_fingerprint:
                raise ValueError("Graph Draft Catalog Release fingerprint mismatch")
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_draft_intent (
                      draft_intent_id,catalog_release_id,draft_key,revision,status,
                      intent_document,intent_fingerprint,created_by
                    ) VALUES (:id,:release,:key,:revision,'draft',CAST(:intent AS jsonb),
                              :fingerprint,:actor)
                    ON CONFLICT (draft_intent_id) DO NOTHING
                    """
                ),
                {
                    "id": draft_id,
                    "release": catalog_release_id,
                    "key": draft_key,
                    "revision": graph_draft_revision,
                    "intent": json.dumps(document, sort_keys=True),
                    "fingerprint": fingerprint,
                    "actor": actor_key,
                },
            )
            row = connection.execute(
                text(
                    "SELECT draft_key,revision,intent_fingerprint,catalog_release_id "
                    "FROM workspace.v022_draft_intent WHERE draft_intent_id=:id"
                ),
                {"id": draft_id},
            ).mappings().one()
            if (
                row["draft_key"] != draft_key
                or row["revision"] != graph_draft_revision
                or row["intent_fingerprint"] != fingerprint
                or row["catalog_release_id"] != catalog_release_id
            ):
                raise ValueError("Graph Draft compiler bridge identity collision")
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_graph_draft_compile_binding (
                      graph_draft_id,graph_draft_revision,draft_intent_id,
                      bridge_contract_version
                    ) VALUES (:draft,:revision,:intent,'v0.22.0')
                    ON CONFLICT (graph_draft_id,graph_draft_revision) DO NOTHING
                    """
                ),
                {
                    "draft": graph_draft_id,
                    "revision": graph_draft_revision,
                    "intent": draft_id,
                },
            )
            bound_id = connection.scalar(
                text(
                    "SELECT draft_intent_id "
                    "FROM workspace.v022_graph_draft_compile_binding "
                    "WHERE graph_draft_id=:draft AND graph_draft_revision=:revision"
                ),
                {"draft": graph_draft_id, "revision": graph_draft_revision},
            )
            if bound_id != draft_id:
                raise ValueError("Graph Draft revision is bound to another compiler input")
        return DraftIdentity(draft_id, draft_key, graph_draft_revision, fingerprint)

    def compile(
        self,
        draft_intent_id: uuid.UUID,
        *,
        asset_context_snapshot: AssetContextSnapshot | None = None,
        resolved_data_binding_snapshot: ResolvedDataBindingSnapshot | None = None,
    ) -> CompileOutcome:
        context_preflight_error: ValueError | None = None
        asset_registry_artifact_id: uuid.UUID | None = None
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM workspace.v022_draft_intent WHERE draft_intent_id=:id"),
                    {"id": draft_intent_id},
                )
                .mappings()
                .one()
            )
            intent = DraftIntent.model_validate(row["intent_document"])
            retired_defenses = sorted(
                defense_key for defense_key in intent.defense_keys if defense_key != "none"
            )
            if retired_defenses:
                context_preflight_error = ValueError(
                    "defense_retired: v0.22 currently supports only the no-defense "
                    f"branch; remove {retired_defenses[0]} before compiling"
                )
            catalog = load_graph_catalog(connection, row["catalog_release_id"])
            requires_asset_context = _requires_asset_context_snapshot(intent, catalog)
            requires_execution_context = bool(
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 "
                        "FROM workspace.v022_catalog_release_component "
                        "WHERE catalog_release_id=:release "
                        "AND component_kind='strategy_parameter_preset_version')"
                    ),
                    {"release": row["catalog_release_id"]},
                )
            ) or any(
                defense_key != "none"
                and isinstance(catalog.defense_versions.get(defense_key), DefenseSpec)
                for defense_key in intent.defense_keys
            )
            execution_context_requested = (
                asset_context_snapshot is not None
                or resolved_data_binding_snapshot is not None
            )
            release_artifact_id = connection.scalar(
                text(
                    "SELECT artifact_id FROM workspace.v022_catalog_release "
                    "WHERE catalog_release_id=:id"
                ),
                {"id": row["catalog_release_id"]},
            )
            try:
                if execution_context_requested and (
                    asset_context_snapshot is None
                    or resolved_data_binding_snapshot is None
                ):
                    raise ValueError(
                        "exact_execution_data_context_incomplete: Asset Context and "
                        "Resolved Data Binding must be provided together"
                    )
                if requires_execution_context and not execution_context_requested:
                    raise ValueError(
                        "exact_execution_data_context_required: this Catalog Release "
                        "requires complete immutable execution data identities"
                    )
                if resolved_data_binding_snapshot is not None and (
                    sha256_hexdigest(
                        resolved_data_binding_snapshot.model_dump(mode="json")
                    )
                    != intent.resolved_data_binding_fingerprint
                ):
                    raise ValueError(
                        "resolved_data_binding_snapshot_mismatch: frozen Data Binding "
                        "fingerprint does not match the Draft Intent"
                    )
                asset_registry_artifact_id = _validated_asset_context_artifact(
                    connection,
                    intent,
                    asset_context_snapshot if execution_context_requested else None,
                )
            except ValueError as error:
                context_preflight_error = error
        try:
            if context_preflight_error is not None:
                raise context_preflight_error
            compiled = compile_intent(
                intent,
                catalog,
                asset_context_snapshot=(
                    asset_context_snapshot if requires_asset_context else None
                ),
            )
        except ValueError as error:
            request_fingerprint = sha256_hexdigest(
                {
                    "draft_intent_id": draft_intent_id,
                    "draft_revision": row["revision"],
                    "compiler_version": self._compiler_version,
                    "intent_fingerprint": row["intent_fingerprint"],
                }
            )
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO workspace.v022_compile_attempt (
                          compile_attempt_id,draft_intent_id,draft_revision,catalog_release_id,
                          compiler_version,context_document,request_fingerprint,status,
                          diagnostics,compiled_research_graph_id
                        ) VALUES (:id,:draft,:revision,:release,:compiler,
                                  CAST(:context AS jsonb),
                                  :request,'rejected',CAST(:diagnostics AS jsonb),NULL)
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "draft": draft_intent_id,
                        "revision": row["revision"],
                        "release": row["catalog_release_id"],
                        "compiler": self._compiler_version,
                        "context": json.dumps(
                            _compile_context_document(
                                intent,
                                asset_context_snapshot,
                                resolved_data_binding_snapshot,
                                include_fingerprints=requires_asset_context,
                            ),
                            sort_keys=True,
                        ),
                        "request": request_fingerprint,
                        "diagnostics": json.dumps(
                            [{"reason_code": "contract_rejected", "message": str(error)}]
                        ),
                    },
                )
            raise
        service = ArtifactService(self._engine)
        recipe_artifact_ids: dict[str, uuid.UUID] = {}
        feature_schema_publications: dict[str, PublicationResult] = {}
        ensemble_spec_publications: dict[str, PublicationResult] = {}
        for instance in compiled.aggregation_instances:
            if instance.recipe_document is None:
                continue
            if (
                instance.recipe_fingerprint is None
                or catalog.feature_taxonomy is None
            ):
                raise AssertionError("Native Recipe identity is incomplete")
            recipe_publication = service.publish(
                artifact_type="v022_compiled_aggregation_recipe",
                artifact_key=instance.recipe_fingerprint,
                version_number=1,
                semantic_payload=instance.recipe_document,
                content_payload=instance.recipe_document,
                dependencies=(
                    DependencyInput(
                        uuid.UUID(catalog.feature_taxonomy.artifact_id),
                        "feature_taxonomy",
                        0,
                    ),
                ),
                reason=(
                    "publish immutable native hierarchical Aggregation Recipe"
                ),
            )
            recipe_artifact_ids[instance.instance_key] = (
                recipe_publication.artifact_id
            )
        for instance in compiled.aggregation_instances:
            if instance.feature_schema_document is None:
                continue
            if instance.feature_schema_fingerprint is None:
                raise AssertionError("Supervised Feature Schema identity is incomplete")
            schema_publication = service.publish(
                artifact_type="v022_feature_schema_version",
                artifact_key=instance.feature_schema_fingerprint,
                version_number=1,
                semantic_payload=instance.feature_schema_document,
                content_payload=instance.feature_schema_document,
                dependencies=(
                    DependencyInput(cast(uuid.UUID, release_artifact_id), "catalog_release", 0),
                ),
                reason="publish immutable supervised Aggregation Feature Schema",
            )
            feature_schema_publications[instance.instance_key] = schema_publication
        for instance in compiled.aggregation_instances:
            if instance.ensemble_spec_document is None:
                continue
            if (
                instance.ensemble_spec_fingerprint is None
                or instance.instance_key not in feature_schema_publications
                or not instance.ensemble_members
            ):
                raise AssertionError("Trainable Ensemble Spec identity is incomplete")
            with self._engine.connect() as connection:
                axis_artifacts = tuple(
                    (
                        _axis_artifact_id(
                            connection,
                            instance.family_key,
                            "target",
                            member.target_key,
                        ),
                        _axis_artifact_id(
                            connection,
                            instance.family_key,
                            "training_preset",
                            member.training_preset_key,
                        ),
                    )
                    for member in instance.ensemble_members
                )
            dependencies_for_ensemble = [
                DependencyInput(
                    feature_schema_publications[instance.instance_key].artifact_id,
                    "feature_schema",
                    0,
                ),
            ]
            seen_axis_artifacts: set[tuple[str, uuid.UUID]] = set()
            axis_role_ordinals = {"target_version": 0, "training_preset": 0}
            for target_artifact_id, training_artifact_id in axis_artifacts:
                for role, artifact_id in (
                    ("target_version", target_artifact_id),
                    ("training_preset", training_artifact_id),
                ):
                    identity = (role, artifact_id)
                    if identity in seen_axis_artifacts:
                        continue
                    seen_axis_artifacts.add(identity)
                    dependencies_for_ensemble.append(
                        DependencyInput(
                            artifact_id,
                            role,
                            axis_role_ordinals[role],
                        )
                    )
                    axis_role_ordinals[role] += 1
            ensemble_spec_publications[instance.instance_key] = service.publish(
                artifact_type="v022_trainable_ensemble_spec",
                artifact_key=instance.ensemble_spec_fingerprint,
                version_number=1,
                semantic_payload=instance.ensemble_spec_document,
                content_payload=instance.ensemble_spec_document,
                dependencies=tuple(dependencies_for_ensemble),
                reason="publish immutable Trainable Ensemble Spec",
            )
        dependencies = [
            DependencyInput(cast(uuid.UUID, release_artifact_id), "catalog_release", 0)
        ]
        dependencies.extend(
            DependencyInput(artifact_id, "aggregation_recipe", ordinal)
            for ordinal, artifact_id in enumerate(
                recipe_artifact_ids[key] for key in sorted(recipe_artifact_ids)
            )
        )
        dependencies.extend(
            DependencyInput(artifact_id, "trainable_ensemble_spec", ordinal)
            for ordinal, artifact_id in enumerate(
                ensemble_spec_publications[key].artifact_id
                for key in sorted(ensemble_spec_publications)
            )
        )
        dependencies.extend(
            DependencyInput(artifact_id, "feature_schema", ordinal)
            for ordinal, artifact_id in enumerate(
                feature_schema_publications[key].artifact_id
                for key in sorted(feature_schema_publications)
            )
        )
        # Keep pre-preset Graph Artifact lineage byte-for-byte compatible.  The
        # downstream Execution Data Context owns the complete Registry/Dataset/
        # Calendar closure; only parameterized Graphs already required the
        # Registry directly at the compiler boundary.
        if asset_registry_artifact_id is not None and requires_asset_context:
            dependencies.append(
                DependencyInput(
                    asset_registry_artifact_id,
                    "asset_context_snapshot",
                    0,
                )
            )
            if asset_context_snapshot is not None and (
                asset_context_snapshot.universe_history_artifact_id is not None
            ):
                dependencies.append(
                    DependencyInput(
                        asset_context_snapshot.universe_history_artifact_id,
                        "dynamic_universe_history",
                        0,
                    )
                )
        publication = service.publish(
            artifact_type="v022_compiled_research_graph",
            artifact_key=compiled.graph_fingerprint,
            version_number=1,
            semantic_payload=compiled.normalized_graph,
            content_payload=compiled.normalized_graph,
            dependencies=tuple(dependencies),
            reason="publish immutable v0.22 compiled graph",
            draft_writer=partial(
                self._write_graph,
                catalog_release_id=row["catalog_release_id"],
                intent=intent,
                compiled=compiled,
                recipe_artifact_ids=recipe_artifact_ids,
                feature_schema_publications=feature_schema_publications,
                ensemble_spec_publications=ensemble_spec_publications,
            ),
        )
        with self._engine.connect() as connection:
            graph_id = connection.scalar(
                text(
                    "SELECT compiled_research_graph_id FROM workspace.compiled_research_graph "
                    "WHERE artifact_id=:artifact"
                ),
                {"artifact": publication.artifact_id},
            )
        execution_context: ExecutionDataContextPublication | None = None
        defense_contexts: list[DefenseExecutionContextPublication] = []
        composed_defense_version_ids = _composed_defense_version_ids(compiled)
        try:
            if execution_context_requested:
                if asset_context_snapshot is None or resolved_data_binding_snapshot is None:
                    raise AssertionError(
                        "Execution context preflight did not reject partial identity"
                    )
                execution_context = ExecutionDataContextService(self._engine).publish(
                    cast(uuid.UUID, graph_id),
                    asset_context_snapshot.model_dump(mode="json"),
                    resolved_data_binding_snapshot.model_dump(mode="json"),
                )
            if composed_defense_version_ids and execution_context is None:
                raise ValueError(
                    "exact_execution_data_context_required: composed Defense requires "
                    "the immutable risk execution context"
                )
            if execution_context is not None:
                defense_context_service = DefenseExecutionContextService(self._engine)
                for defense_version_id in composed_defense_version_ids:
                    defense_context = defense_context_service.publish(
                        execution_context.context_id,
                        defense_version_id,
                    )
                    if defense_context is None:
                        raise AssertionError(
                            "A non-null Defense Package did not publish its Context"
                        )
                    defense_contexts.append(defense_context)
            if tuple(item.defense_version_id for item in defense_contexts) != (
                composed_defense_version_ids
            ):
                raise AssertionError(
                    "Compiled Defense Contexts do not reproduce the exact Defense set"
                )
        except Exception as error:
            request_fingerprint = sha256_hexdigest(
                {
                    "draft_intent_id": draft_intent_id,
                    "draft_revision": row["revision"],
                    "compiler_version": self._compiler_version,
                    "graph_fingerprint": compiled.graph_fingerprint,
                    "execution_context_state": "rejected",
                }
            )
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO workspace.v022_compile_attempt (
                          compile_attempt_id,draft_intent_id,draft_revision,
                          catalog_release_id,compiler_version,context_document,
                          request_fingerprint,status,diagnostics,
                          compiled_research_graph_id
                        ) VALUES (
                          :id,:draft,:revision,:release,:compiler,
                          CAST(:context AS jsonb),:request,'rejected',
                          CAST(:diagnostics AS jsonb),NULL
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "draft": draft_intent_id,
                        "revision": row["revision"],
                        "release": row["catalog_release_id"],
                        "compiler": self._compiler_version,
                        "context": json.dumps(
                            _compile_context_document(
                                intent,
                                asset_context_snapshot,
                                resolved_data_binding_snapshot,
                                include_fingerprints=True,
                                execution_context=execution_context,
                                defense_contexts=tuple(defense_contexts),
                            ),
                            sort_keys=True,
                        ),
                        "request": request_fingerprint,
                        "diagnostics": json.dumps(
                            [
                                {
                                    "reason_code": "contract_rejected",
                                    "message": str(error),
                                }
                            ]
                        ),
                    },
                )
            raise
        with self._engine.begin() as connection:
            attempt_id = uuid.uuid4()
            request_identity = {
                "draft_intent_id": draft_intent_id,
                "draft_revision": row["revision"],
                "compiler_version": self._compiler_version,
                "graph_fingerprint": compiled.graph_fingerprint,
            }
            # Preserve the exact pre-execution-context request identity for old
            # Catalog releases.  Only a compile that actually owns a Context
            # extends the fingerprint document.
            if execution_context is not None:
                request_identity["execution_data_context_fingerprint"] = (
                    execution_context.context_fingerprint
                )
            if defense_contexts:
                request_identity["defense_execution_context_fingerprints"] = [
                    item.context_fingerprint for item in defense_contexts
                ]
            request_fingerprint = sha256_hexdigest(request_identity)
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_compile_attempt (
                      compile_attempt_id,draft_intent_id,draft_revision,catalog_release_id,
                      compiler_version,context_document,request_fingerprint,status,
                      diagnostics,compiled_research_graph_id
                    ) VALUES (:id,:draft,:revision,:release,:compiler,CAST(:context AS jsonb),
                              :request,'succeeded','[]'::jsonb,:graph)
                    """
                ),
                {
                    "id": attempt_id,
                    "draft": draft_intent_id,
                    "revision": row["revision"],
                    "release": row["catalog_release_id"],
                    "compiler": self._compiler_version,
                    "context": json.dumps(
                        _compile_context_document(
                            intent,
                            asset_context_snapshot,
                            resolved_data_binding_snapshot,
                            include_fingerprints=True,
                            execution_context=execution_context,
                            defense_contexts=tuple(defense_contexts),
                        ),
                        sort_keys=True,
                    ),
                    "request": request_fingerprint,
                    "graph": graph_id,
                },
            )
        return CompileOutcome(
            attempt_id,
            graph_id,
            publication.artifact_id,
            compiled.graph_fingerprint,
            publication.reused,
            (
                execution_context.context_id
                if execution_context is not None
                else None
            ),
            execution_context.artifact_id if execution_context is not None else None,
            (
                execution_context.context_fingerprint
                if execution_context is not None
                else None
            ),
            execution_context.reused if execution_context is not None else None,
            tuple(defense_contexts),
        )

    def _write_graph(
        self,
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        catalog_release_id: uuid.UUID,
        intent: DraftIntent,
        compiled: CompilationResult,
        recipe_artifact_ids: dict[str, uuid.UUID],
        feature_schema_publications: dict[str, PublicationResult],
        ensemble_spec_publications: dict[str, PublicationResult],
    ) -> None:
        graph_id = uuid.uuid4()
        connection.execute(
            text(
                """
                INSERT INTO workspace.compiled_research_graph (
                  compiled_research_graph_id,artifact_id,graph_fingerprint,contract_version,
                  compiler_version,catalog_release_id,asset_context_fingerprint,
                  resolved_data_binding_fingerprint,frequency,normalized_graph,node_count,
                  occurrence_count,edge_count,projection_count,aggregation_instance_count,
                  strategy_branch_count
                ) VALUES (:id,:artifact,:fingerprint,'v0.22.0',:compiler,:release,:asset,:binding,
                  :frequency,CAST(:graph AS jsonb),:nodes,:occurrences,:edges,:projections,:aggregations,:branches)
                """
            ),
            {
                "id": graph_id,
                "artifact": artifact_id,
                "fingerprint": compiled.graph_fingerprint,
                "compiler": self._compiler_version,
                "release": catalog_release_id,
                "asset": intent.asset_context_fingerprint,
                "binding": intent.resolved_data_binding_fingerprint,
                "frequency": intent.frequency,
                "graph": json.dumps(compiled.normalized_graph, sort_keys=True),
                "nodes": len(compiled.nodes),
                "occurrences": len(compiled.occurrences),
                "edges": sum(len(item.inputs) for item in compiled.nodes),
                "projections": sum(
                    item.production_kind == "layer_projection" for item in compiled.occurrences
                ),
                "aggregations": len(compiled.aggregation_instances),
                "branches": len(compiled.branches),
            },
        )
        node_ids: dict[str, uuid.UUID] = {}
        for node in compiled.nodes:
            node_id = uuid.uuid4()
            node_ids[node.node_key] = node_id
            connection.execute(
                text(
                    "INSERT INTO workspace.compiled_graph_node VALUES "
                    "(:id,:graph,:version,:stage,:fingerprint)"
                ),
                {
                    "id": node_id,
                    "graph": graph_id,
                    "version": uuid.UUID(node.node_version_id),
                    "stage": node.stage_no,
                    "fingerprint": sha256_hexdigest(node),
                },
            )
        occurrence_ids: dict[tuple[str, int], uuid.UUID] = {}
        for occurrence in compiled.occurrences:
            occurrence_id = uuid.uuid4()
            occurrence_ids[(occurrence.feature_key, occurrence.stage_no)] = occurrence_id
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.compiled_feature_occurrence (
                      compiled_feature_occurrence_id,compiled_research_graph_id,feature_version_id,
                      stage_no,is_explicit,is_required,is_aggregation_input,production_kind,
                      source_occurrence_id,compiled_graph_node_id,output_port_key,occurrence_fingerprint
                    ) VALUES (:id,:graph,:feature,:stage,:explicit,true,:aggregation_input,:kind,
                              :source,:node,:port,:fingerprint)
                    """
                ),
                {
                    "id": occurrence_id,
                    "graph": graph_id,
                    "feature": uuid.UUID(occurrence.feature_version_id),
                    "stage": occurrence.stage_no,
                    "explicit": occurrence.is_explicit,
                    "aggregation_input": occurrence.is_aggregation_input,
                    "kind": occurrence.production_kind,
                    "source": (
                        occurrence_ids.get(occurrence.source_key)
                        if occurrence.source_key is not None
                        else None
                    ),
                    "node": node_ids.get(occurrence.node_key or ""),
                    "port": occurrence.output_port_key,
                    "fingerprint": sha256_hexdigest(occurrence),
                },
            )
        for node in compiled.nodes:
            for port, source, ordinal in node.inputs:
                connection.execute(
                    text("INSERT INTO workspace.compiled_node_input VALUES (:node,:port,:source,:ordinal)"),
                    {
                        "node": node_ids[node.node_key],
                        "port": port,
                        "source": occurrence_ids[source],
                        "ordinal": ordinal,
                    },
                )
        instance_ids: dict[str, uuid.UUID] = {}
        feature_schema_version_ids: dict[str, uuid.UUID] = {}
        for instance in compiled.aggregation_instances:
            instance_id = uuid.uuid4()
            instance_ids[instance.instance_key] = instance_id
            instance_fingerprint = sha256_hexdigest(
                {
                    "compiled_graph_fingerprint": compiled.graph_fingerprint,
                    "aggregation_instance": instance,
                }
            )
            axis_ids = _axis_ids(
                connection,
                instance.family_key,
                instance.parameter_preset_key,
                instance.target_key,
                instance.training_preset_key,
            )
            output_contract = connection.scalar(
                text(
                    "SELECT output_payload_contract_version_id FROM aggregation.aggregation_version "
                    "WHERE aggregation_version_id=:id"
                ),
                {"id": uuid.UUID(instance.aggregation_version_id)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.compiled_aggregation_instance (
                      compiled_aggregation_instance_id,compiled_research_graph_id,aggregation_version_id,
                      parameter_preset_version_id,target_version_id,training_preset_version_id,
                      instance_key,instance_fingerprint,output_payload_contract_version_id
                    ) VALUES (:id,:graph,:version,:parameter,:target,:training,:key,:fingerprint,:output)
                    """
                ),
                {
                    "id": instance_id,
                    "graph": graph_id,
                    "version": uuid.UUID(instance.aggregation_version_id),
                    "parameter": axis_ids[0],
                    "target": axis_ids[1],
                    "training": axis_ids[2],
                    "key": instance.instance_key,
                    "fingerprint": instance_fingerprint,
                    "output": output_contract,
                },
            )
            for ordinal, occurrence_key in enumerate(instance.ordered_inputs):
                connection.execute(
                    text("INSERT INTO workspace.compiled_aggregation_input VALUES (:instance,'stage3_inputs',:ordinal,:occurrence)"),
                    {
                        "instance": instance_id,
                        "ordinal": ordinal,
                        "occurrence": occurrence_ids[occurrence_key],
                    },
                )
            if instance.recipe_document is not None:
                if (
                    instance.recipe_fingerprint is None
                    or instance.instance_key not in recipe_artifact_ids
                ):
                    raise AssertionError("Native Recipe publication is incomplete")
                taxonomy = _required_compiled_taxonomy(
                    connection, catalog_release_id
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO workspace.v022_compiled_aggregation_recipe (
                          compiled_aggregation_instance_id,artifact_id,
                          feature_taxonomy_version_id,recipe_fingerprint,
                          recipe_document
                        ) VALUES (
                          :instance,:artifact,:taxonomy,:fingerprint,
                          CAST(:document AS jsonb)
                        )
                        """
                    ),
                    {
                        "instance": instance_id,
                        "artifact": recipe_artifact_ids[instance.instance_key],
                        "taxonomy": taxonomy,
                        "fingerprint": instance.recipe_fingerprint,
                        "document": json.dumps(
                            instance.recipe_document, sort_keys=True
                        ),
                    },
                )
            if instance.feature_schema_document is not None:
                if (
                    instance.feature_schema_fingerprint is None
                    or instance.instance_key not in feature_schema_publications
                ):
                    raise AssertionError("Supervised Feature Schema publication is incomplete")
                schema_publication = feature_schema_publications[
                    instance.instance_key
                ]
                proposed_schema_version_id = uuid.uuid4()
                connection.execute(
                    text(
                        """
                        INSERT INTO aggregation.v022_feature_schema_version (
                          feature_schema_version_id,artifact_id,version_number,
                          ordered_feature_document,input_count,
                          feature_schema_fingerprint,artifact_semantic_fingerprint
                        ) VALUES (
                          :id,:artifact,1,CAST(:document AS jsonb),:input_count,
                          :fingerprint,:artifact_semantic_fingerprint
                        )
                        ON CONFLICT (artifact_id) DO NOTHING
                        """
                    ),
                    {
                        "id": proposed_schema_version_id,
                        "artifact": schema_publication.artifact_id,
                        "document": json.dumps(
                            instance.feature_schema_document, sort_keys=True
                        ),
                        "input_count": len(instance.ordered_inputs),
                        "fingerprint": instance.feature_schema_fingerprint,
                        "artifact_semantic_fingerprint": (
                            schema_publication.semantic_fingerprint
                        ),
                    },
                )
                schema_row = connection.execute(
                    text(
                        """
                        SELECT feature_schema_version_id,
                               ordered_feature_document,input_count,
                               feature_schema_fingerprint,
                               artifact_semantic_fingerprint
                          FROM aggregation.v022_feature_schema_version
                         WHERE artifact_id=:artifact
                        """
                    ),
                    {"artifact": schema_publication.artifact_id},
                ).mappings().one()
                if (
                    schema_row["ordered_feature_document"]
                    != instance.feature_schema_document
                    or schema_row["input_count"] != len(instance.ordered_inputs)
                    or schema_row["feature_schema_fingerprint"]
                    != instance.feature_schema_fingerprint
                    or schema_row["artifact_semantic_fingerprint"]
                    != schema_publication.semantic_fingerprint
                ):
                    raise AssertionError(
                        "Reused supervised Feature Schema identity drifted"
                    )
                schema_version_id = schema_row["feature_schema_version_id"]
                feature_schema_version_ids[instance.instance_key] = schema_version_id
                connection.execute(
                    text(
                        """
                        INSERT INTO workspace.v022_compiled_feature_schema_binding (
                          compiled_aggregation_instance_id,
                          feature_schema_version_id
                        ) VALUES (:instance,:schema)
                        """
                    ),
                    {"instance": instance_id, "schema": schema_version_id},
                )
            if instance.ensemble_spec_document is not None:
                if (
                    instance.ensemble_spec_fingerprint is None
                    or instance.instance_key not in ensemble_spec_publications
                    or not instance.ensemble_members
                ):
                    raise AssertionError("Trainable Ensemble Spec publication is incomplete")
                schema_version_id = feature_schema_version_ids.get(
                    instance.instance_key
                )
                if schema_version_id is None:
                    raise AssertionError("Trainable Ensemble Feature Schema is missing")
                ensemble_artifact_id = ensemble_spec_publications[
                    instance.instance_key
                ].artifact_id
                ensemble_spec_id = connection.scalar(
                    text(
                        "SELECT ensemble_spec_id FROM "
                        "aggregation.v022_trainable_ensemble_spec "
                        "WHERE artifact_id=:artifact"
                    ),
                    {"artifact": ensemble_artifact_id},
                )
                if ensemble_spec_id is None:
                    ensemble_spec_id = uuid.uuid4()
                    connection.execute(
                        text(
                            """
                            INSERT INTO aggregation.v022_trainable_ensemble_spec (
                              ensemble_spec_id,artifact_id,aggregation_version_id,
                              feature_schema_version_id,ensemble_fingerprint,
                              artifact_semantic_fingerprint,member_count,
                              target_group_count,ensemble_document
                            ) VALUES (
                              :id,:artifact,:version,:schema,:fingerprint,
                              :artifact_semantic_fingerprint,:members,:targets,
                              CAST(:document AS jsonb)
                            )
                            """
                        ),
                        {
                            "id": ensemble_spec_id,
                            "artifact": ensemble_artifact_id,
                            "version": uuid.UUID(instance.aggregation_version_id),
                            "schema": schema_version_id,
                            "fingerprint": instance.ensemble_spec_fingerprint,
                            "artifact_semantic_fingerprint": (
                                ensemble_spec_publications[
                                    instance.instance_key
                                ].semantic_fingerprint
                            ),
                            "members": len(instance.ensemble_members),
                            "targets": len(
                                {member.target_key for member in instance.ensemble_members}
                            ),
                            "document": json.dumps(
                                instance.ensemble_spec_document, sort_keys=True
                            ),
                        },
                    )
                    for member in instance.ensemble_members:
                        member_axis_ids = _axis_ids(
                            connection,
                            instance.family_key,
                            None,
                            member.target_key,
                            member.training_preset_key,
                        )
                        connection.execute(
                            text(
                                """
                                INSERT INTO aggregation.v022_trainable_ensemble_member (
                                  ensemble_spec_id,ordinal,target_group_ordinal,
                                  member_ordinal_within_target,target_version_id,
                                  training_preset_version_id
                                ) VALUES (
                                  :ensemble,:ordinal,:target_ordinal,:member_ordinal,
                                  :target,:training
                                )
                                """
                            ),
                            {
                                "ensemble": ensemble_spec_id,
                                "ordinal": member.ordinal,
                                "target_ordinal": member.target_group_ordinal,
                                "member_ordinal": member.member_ordinal_within_target,
                                "target": member_axis_ids[1],
                                "training": member_axis_ids[2],
                            },
                        )
                connection.execute(
                    text(
                        """
                        INSERT INTO workspace.v022_compiled_trainable_ensemble_binding (
                          compiled_aggregation_instance_id,ensemble_spec_id
                        ) VALUES (:instance,:ensemble)
                        """
                    ),
                    {"instance": instance_id, "ensemble": ensemble_spec_id},
                )
        for branch in compiled.branches:
            branch_id = uuid.uuid4()
            branch_fingerprint = sha256_hexdigest(
                {
                    "compiled_graph_fingerprint": compiled.graph_fingerprint,
                    "strategy_branch": strategy_branch_identity_document(branch),
                }
            )
            connection.execute(
                text(
                    """
                    INSERT INTO strategy.v022_compiled_strategy_branch (
                      compiled_strategy_branch_id,compiled_research_graph_id,
                      compiled_aggregation_instance_id,strategy_version_id,defense_version_id,
                      branch_key,branch_fingerprint
                    ) VALUES (:id,:graph,:aggregation,:strategy,:defense,:key,:fingerprint)
                    """
                ),
                {
                    "id": branch_id,
                    "graph": graph_id,
                    "aggregation": instance_ids[branch.aggregation_instance_key],
                    "strategy": uuid.UUID(branch.strategy_version_id),
                    "defense": (
                        uuid.UUID(branch.defense_version_id) if branch.defense_version_id else None
                    ),
                    "key": branch.branch_key,
                    "fingerprint": branch_fingerprint,
                },
            )
            if branch.strategy_parameter_preset_version_id is not None:
                connection.execute(
                    text(
                        """
                        INSERT INTO strategy.v022_compiled_strategy_branch_preset_binding (
                          compiled_strategy_branch_id,strategy_parameter_preset_version_id,
                          parameter_fingerprint,resolved_parameters
                        ) VALUES (:branch,:preset,:fingerprint,CAST(:parameters AS jsonb))
                        """
                    ),
                    {
                        "branch": branch_id,
                        "preset": uuid.UUID(branch.strategy_parameter_preset_version_id),
                        "fingerprint": branch.strategy_parameter_fingerprint,
                        "parameters": json.dumps(
                            branch.resolved_strategy_parameters, sort_keys=True
                        ),
                    },
                )


def _required_compiled_taxonomy(
    connection: Connection,
    catalog_release_id: uuid.UUID,
) -> uuid.UUID:
    taxonomy_ids = tuple(
        connection.scalars(
            text(
                """
                SELECT taxonomy.feature_taxonomy_version_id
                  FROM aggregation.v022_feature_taxonomy_version taxonomy
                  JOIN workspace.v022_catalog_release_component component
                    ON component.component_artifact_id=taxonomy.artifact_id
                 WHERE component.catalog_release_id=:release
                   AND component.component_kind=
                       'aggregation_feature_taxonomy_version'
                """
            ),
            {"release": catalog_release_id},
        )
    )
    if len(taxonomy_ids) != 1:
        raise ValueError(
            "aggregation_feature_taxonomy_not_unique: compiled native Recipe "
            "requires one exact Catalog taxonomy"
        )
    return cast(uuid.UUID, taxonomy_ids[0])


def load_graph_catalog(connection: Connection, release_id: uuid.UUID) -> GraphCatalog:
    feature_rows = list(
        connection.execute(
            text(
            """
            SELECT fv.feature_version_id,v.variant_key,family.family_key,
                   fv.origin_stage,
                   fv.aggregation_readiness,c.contract_key,
                   fv.execution_semantics->>'unit' AS unit,
                   fv.execution_semantics->>'direction' AS direction,
                   nvar.variant_key AS producer_node_key,nv.node_version_id,nv.stage_no,
                   output_port.port_key AS output_port_key
            FROM processing.feature_version fv
            JOIN processing.feature_variant v ON v.feature_variant_id=fv.feature_variant_id
            JOIN processing.feature_family family
              ON family.feature_family_id=v.feature_family_id
            JOIN data.payload_contract_version cv ON cv.payload_contract_version_id=fv.payload_contract_version_id
            JOIN data.payload_contract_family c ON c.payload_contract_family_id=cv.payload_contract_family_id
            LEFT JOIN processing.feature_producer fp ON fp.feature_version_id=fv.feature_version_id
            LEFT JOIN processing.node_version nv ON nv.node_version_id=fp.node_version_id
            LEFT JOIN processing.node_variant nvar ON nvar.node_variant_id=nv.node_variant_id
            LEFT JOIN processing.node_port output_port ON output_port.node_port_id=fp.output_port_id
            WHERE fv.artifact_id IN (SELECT component_artifact_id FROM workspace.v022_catalog_release_component WHERE catalog_release_id=:release)
            """
            ),
            {"release": release_id},
        ).mappings()
    )
    producer_rows = [row for row in feature_rows if row["producer_node_key"] is not None]
    node_outputs: dict[str, list[str]] = {}
    node_metadata: dict[str, tuple[str, int]] = {}
    for row in producer_rows:
        key = row["producer_node_key"]
        node_outputs.setdefault(key, []).append(row["variant_key"])
        node_metadata[key] = (str(row["node_version_id"]), row["stage_no"])
    binding_rows = list(
        connection.execute(
            text(
                """
                SELECT nvar.variant_key AS producer_node_key,nv.stage_no,
                       port.port_key,source_v.variant_key AS source_key,b.ordinal
                FROM processing.node_version nv
                JOIN processing.node_variant nvar ON nvar.node_variant_id=nv.node_variant_id
                JOIN processing.node_input_binding b ON b.node_version_id=nv.node_version_id
                JOIN processing.node_port port ON port.node_port_id=b.input_port_id
                JOIN processing.feature_version source_f ON source_f.feature_version_id=b.source_feature_version_id
                JOIN processing.feature_variant source_v ON source_v.feature_variant_id=source_f.feature_variant_id
                WHERE nv.artifact_id IN (SELECT component_artifact_id FROM workspace.v022_catalog_release_component WHERE catalog_release_id=:release)
                ORDER BY nvar.variant_key,b.ordinal,port.port_key
                """
            ),
            {"release": release_id},
        ).mappings()
    )
    node_inputs: dict[str, tuple[NodeInputSpec, ...]] = {}
    projection_ceiling = {
        row["variant_key"]: max(row["origin_stage"], 1 if row["origin_stage"] == 0 else 0)
        for row in feature_rows
    }
    for row in binding_rows:
        key = row["producer_node_key"]
        projection_ceiling[row["source_key"]] = max(
            projection_ceiling[row["source_key"]], row["stage_no"] - 1
        )
        node_inputs[key] = node_inputs.get(key, ()) + (
            NodeInputSpec(row["port_key"], row["source_key"], row["ordinal"]),
        )
    features = {
        row["variant_key"]: FeatureSpec(
            feature_key=row["variant_key"],
            version_id=str(row["feature_version_id"]),
            origin_stage=row["origin_stage"],
            payload_contract_key=row["contract_key"],
            producer_node_key=row["producer_node_key"],
            output_port_key=row["output_port_key"],
            maximum_projection_stage=(
                3
                if row["aggregation_readiness"] == "aggregation_ready"
                else projection_ceiling[row["variant_key"]]
            ),
            feature_family_key=row["family_key"],
            unit=row["unit"],
            direction=row["direction"],
        )
        for row in feature_rows
    }
    nodes: dict[str, NodeSpec] = {}
    for key, outputs in node_outputs.items():
        version_id, stage_no = node_metadata[key]
        nodes[key] = NodeSpec(
            key,
            version_id,
            _node_stage(stage_no),
            tuple(sorted(outputs)),
            node_inputs.get(key, ()),
        )
    aggregations = _load_aggregations(connection, release_id)
    strategy_rows = list(connection.execute(
            text(
                "SELECT v.variant_key,v.parameters,ver.strategy_version_id,"
                "ver.schedule_policy "
                "FROM strategy.v022_strategy_version ver "
                "JOIN strategy.v022_strategy_variant v ON v.strategy_variant_id=ver.strategy_variant_id "
                "WHERE ver.artifact_id IN (SELECT component_artifact_id FROM workspace.v022_catalog_release_component WHERE catalog_release_id=:release)"
            ),
            {"release": release_id},
        ).mappings())
    strategy_preset_rows = connection.execute(
        text(
            """
            SELECT variant.variant_key,definition.preset_key,
                   version.strategy_parameter_preset_version_id,
                   version.parameter_fingerprint,version.resolved_parameters
              FROM strategy.v022_strategy_parameter_preset_version version
              JOIN strategy.v022_strategy_parameter_preset_definition definition
                ON definition.strategy_parameter_preset_definition_id=
                   version.strategy_parameter_preset_definition_id
              JOIN strategy.v022_strategy_variant variant
                ON variant.strategy_variant_id=definition.strategy_variant_id
             WHERE version.artifact_id IN (
               SELECT component_artifact_id
                 FROM workspace.v022_catalog_release_component
                WHERE catalog_release_id=:release
             )
             ORDER BY variant.variant_key,definition.preset_key
            """
        ),
        {"release": release_id},
    ).mappings()
    strategy_presets: dict[str, dict[str, StrategyParameterPresetSpec]] = {}
    for row in strategy_preset_rows:
        strategy_presets.setdefault(row["variant_key"], {})[row["preset_key"]] = (
            StrategyParameterPresetSpec(
                row["preset_key"],
                str(row["strategy_parameter_preset_version_id"]),
                row["parameter_fingerprint"],
                row["resolved_parameters"],
            )
        )
    strategies = {
        row["variant_key"]: StrategySpec(
            str(row["strategy_version_id"]),
            tuple(row["schedule_policy"]["frequencies"]),
            strategy_presets.get(row["variant_key"], {}),
            strategy_required_instrument_types(
                row["parameters"].get("required_instrument_type")
            ),
        )
        for row in strategy_rows
    }
    defenses = _load_defenses(connection, release_id)
    feature_taxonomy = _load_feature_taxonomy(connection, release_id)
    return GraphCatalog(
        features, nodes, aggregations,
        strategies,
        defenses,
        feature_taxonomy,
    )


def _load_feature_taxonomy(
    connection: Connection,
    release_id: uuid.UUID,
) -> AggregationFeatureTaxonomySpec | None:
    row = connection.execute(
        text(
            """
            SELECT taxonomy.feature_taxonomy_version_id,taxonomy.artifact_id,
                   taxonomy.taxonomy_fingerprint,taxonomy.taxonomy_document
              FROM aggregation.v022_feature_taxonomy_version taxonomy
              JOIN workspace.v022_catalog_release_component component
                ON component.component_artifact_id=taxonomy.artifact_id
             WHERE component.catalog_release_id=:release
               AND component.component_kind=
                   'aggregation_feature_taxonomy_version'
            """
        ),
        {"release": release_id},
    ).mappings().one_or_none()
    if row is None:
        return None
    entries = {
        item["feature_family_key"]: AggregationFeatureTaxonomyEntrySpec(
            feature_family_key=item["feature_family_key"],
            research_dimension_key=item["research_dimension_key"],
            accepted_units=tuple(item["accepted_units"]),
            accepted_directions=tuple(item["accepted_directions"]),
            native_hierarchical_eligible=item["native_hierarchical_eligible"],
        )
        for item in row["taxonomy_document"]["entries"]
    }
    return AggregationFeatureTaxonomySpec(
        version_id=str(row["feature_taxonomy_version_id"]),
        artifact_id=str(row["artifact_id"]),
        taxonomy_fingerprint=row["taxonomy_fingerprint"],
        entries=entries,
    )


def _load_defenses(
    connection: Connection,
    release_id: uuid.UUID,
) -> dict[str, str | DefenseSpec]:
    base_rows = connection.execute(
        text(
            """
            SELECT variant.variant_key,version.defense_version_id,
                   version.version_fingerprint
              FROM defense.defense_version version
              JOIN defense.defense_variant variant
                ON variant.defense_variant_id=version.defense_variant_id
             WHERE version.artifact_id IN (
               SELECT component_artifact_id
                 FROM workspace.v022_catalog_release_component
                WHERE catalog_release_id=:release
                  AND component_kind='defense_version'
             )
             ORDER BY variant.variant_key
            """
        ),
        {"release": release_id},
    ).mappings().all()
    composed = bool(
        connection.scalar(
            text(
                """
                SELECT EXISTS (
                  SELECT 1
                    FROM workspace.v022_catalog_release_component
                   WHERE catalog_release_id=:release
                     AND component_kind IN (
                       'defense_timing_family','defense_timing_variant',
                       'defense_timing_version','defense_allocation_family',
                       'defense_allocation_variant','defense_allocation_version'
                     )
                )
                """
            ),
            {"release": release_id},
        )
    )
    if not composed:
        return {
            row["variant_key"]: str(row["defense_version_id"])
            for row in base_rows
        }

    rows = connection.execute(
        text(
            """
            SELECT variant.variant_key,version.defense_version_id,
                   version.version_fingerprint,
                   package.supported_asset_set_count,
                   timing.timing_policy_version_id,
                   timing.version_fingerprint AS timing_version_fingerprint,
                   timing.supported_frequencies,
                   allocation.allocation_policy_version_id,
                   allocation.version_fingerprint AS allocation_version_fingerprint,
                   supported.ordinal AS supported_ordinal,
                   supported.asset_context_key,
                   supported.asset_registry_release_id,
                   supported.asset_registry_artifact_id,
                   supported.asset_set_definition_id,
                   EXISTS (
                     SELECT 1 FROM workspace.v022_catalog_release_component component
                      WHERE component.catalog_release_id=:release
                        AND component.component_kind='defense_timing_family'
                        AND component.component_artifact_id=timing_family.artifact_id
                   ) AS timing_family_pinned,
                   EXISTS (
                     SELECT 1 FROM workspace.v022_catalog_release_component component
                      WHERE component.catalog_release_id=:release
                        AND component.component_kind='defense_timing_variant'
                        AND component.component_artifact_id=timing_variant.artifact_id
                   ) AS timing_variant_pinned,
                   EXISTS (
                     SELECT 1 FROM workspace.v022_catalog_release_component component
                      WHERE component.catalog_release_id=:release
                        AND component.component_kind='defense_timing_version'
                        AND component.component_artifact_id=timing.artifact_id
                   ) AS timing_version_pinned,
                   EXISTS (
                     SELECT 1 FROM workspace.v022_catalog_release_component component
                      WHERE component.catalog_release_id=:release
                        AND component.component_kind='defense_allocation_family'
                        AND component.component_artifact_id=allocation_family.artifact_id
                   ) AS allocation_family_pinned,
                   EXISTS (
                     SELECT 1 FROM workspace.v022_catalog_release_component component
                      WHERE component.catalog_release_id=:release
                        AND component.component_kind='defense_allocation_variant'
                        AND component.component_artifact_id=allocation_variant.artifact_id
                   ) AS allocation_variant_pinned,
                   EXISTS (
                     SELECT 1 FROM workspace.v022_catalog_release_component component
                      WHERE component.catalog_release_id=:release
                        AND component.component_kind='defense_allocation_version'
                        AND component.component_artifact_id=allocation.artifact_id
                   ) AS allocation_version_pinned
              FROM defense.defense_version version
              JOIN defense.defense_variant variant
                ON variant.defense_variant_id=version.defense_variant_id
              LEFT JOIN defense.v022_defense_package_policy_binding package
                ON package.defense_version_id=version.defense_version_id
              LEFT JOIN defense.v022_timing_policy_version timing
                ON timing.timing_policy_version_id=package.timing_policy_version_id
               AND timing.artifact_id=package.timing_policy_artifact_id
              LEFT JOIN defense.v022_timing_policy_variant timing_variant
                ON timing_variant.timing_policy_variant_id=
                   timing.timing_policy_variant_id
              LEFT JOIN defense.v022_timing_policy_family timing_family
                ON timing_family.timing_policy_family_id=
                   timing_variant.timing_policy_family_id
              LEFT JOIN defense.v022_allocation_policy_version allocation
                ON allocation.allocation_policy_version_id=
                   package.allocation_policy_version_id
               AND allocation.artifact_id=package.allocation_policy_artifact_id
              LEFT JOIN defense.v022_allocation_policy_variant allocation_variant
                ON allocation_variant.allocation_policy_variant_id=
                   allocation.allocation_policy_variant_id
              LEFT JOIN defense.v022_allocation_policy_family allocation_family
                ON allocation_family.allocation_policy_family_id=
                   allocation_variant.allocation_policy_family_id
              LEFT JOIN defense.v022_defense_package_supported_asset_set supported
                ON supported.defense_version_id=version.defense_version_id
             WHERE version.artifact_id IN (
               SELECT component_artifact_id
                 FROM workspace.v022_catalog_release_component
                WHERE catalog_release_id=:release
                  AND component_kind='defense_version'
             )
             ORDER BY variant.variant_key,supported.ordinal
            """
        ),
        {"release": release_id},
    ).mappings().all()
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(cast(str, row["variant_key"]), []).append(dict(row))
    if set(grouped) != {cast(str, row["variant_key"]) for row in base_rows}:
        raise ValueError(
            "defense_package_incomplete: every Defense in a composed Catalog "
            "Release requires a Package binding"
        )
    result: dict[str, str | DefenseSpec] = {}
    pinned_columns = (
        "timing_family_pinned",
        "timing_variant_pinned",
        "timing_version_pinned",
        "allocation_family_pinned",
        "allocation_variant_pinned",
        "allocation_version_pinned",
    )
    for key, package_rows in grouped.items():
        first = package_rows[0]
        required_values = (
            first["defense_version_id"],
            first["version_fingerprint"],
            first["timing_policy_version_id"],
            first["timing_version_fingerprint"],
            first["allocation_policy_version_id"],
            first["allocation_version_fingerprint"],
            first["supported_asset_set_count"],
        )
        if any(value is None for value in required_values) or not all(
            bool(first[column]) for column in pinned_columns
        ):
            raise ValueError(
                f"defense_package_incomplete: {key} requires exact Package, Timing "
                "and Allocation components from the pinned Catalog Release"
            )
        supported_contexts: list[DefenseAssetContextSpec] = []
        for ordinal, package_row in enumerate(package_rows):
            if package_row["supported_ordinal"] != ordinal or any(
                package_row[column] != first[column]
                for column in (
                    "defense_version_id",
                    "version_fingerprint",
                    "timing_policy_version_id",
                    "timing_version_fingerprint",
                    "supported_frequencies",
                    "allocation_policy_version_id",
                    "allocation_version_fingerprint",
                    "supported_asset_set_count",
                )
            ):
                raise ValueError(
                    f"defense_package_incomplete: {key} has a non-canonical Package "
                    "projection"
                )
            supported_values = (
                package_row["asset_context_key"],
                package_row["asset_registry_release_id"],
                package_row["asset_registry_artifact_id"],
                package_row["asset_set_definition_id"],
            )
            if any(value is None for value in supported_values):
                raise ValueError(
                    f"defense_package_incomplete: {key} has no exact supported "
                    "Asset Context"
                )
            supported_contexts.append(
                DefenseAssetContextSpec(
                    cast(str, package_row["asset_context_key"]),
                    str(package_row["asset_registry_release_id"]),
                    str(package_row["asset_registry_artifact_id"]),
                    str(package_row["asset_set_definition_id"]),
                )
            )
        if len(supported_contexts) != first["supported_asset_set_count"]:
            raise ValueError(
                f"defense_package_incomplete: {key} supported Asset Context count drifted"
            )
        frequencies = tuple(cast(list[str], first["supported_frequencies"]))
        if not frequencies or len(frequencies) != len(set(frequencies)):
            raise ValueError(
                f"defense_package_incomplete: {key} has invalid supported frequencies"
            )
        result[key] = DefenseSpec(
            str(first["defense_version_id"]),
            cast(str, first["version_fingerprint"]),
            str(first["timing_policy_version_id"]),
            cast(str, first["timing_version_fingerprint"]),
            str(first["allocation_policy_version_id"]),
            cast(str, first["allocation_version_fingerprint"]),
            frequencies,
            tuple(supported_contexts),
        )
    return result


def _validated_asset_context_artifact(
    connection: Connection,
    intent: DraftIntent,
    snapshot: AssetContextSnapshot | None,
) -> uuid.UUID | None:
    if snapshot is None:
        return None
    if sha256_hexdigest(snapshot.model_dump(mode="json")) != intent.asset_context_fingerprint:
        raise ValueError(
            "asset_context_snapshot_mismatch: frozen Asset Context fingerprint "
            "does not match the Draft Intent"
        )
    if snapshot.selection_kind == "fixed_asset_set":
        rows = connection.execute(
            text(
                """
            SELECT release.asset_registry_release_id,release.artifact_id,
                   release.catalog_version,definition.asset_set_definition_id,
                   definition.set_key,definition.set_type,member.ordinal,
                   security.security_id,security.security_key,
                   profile.instrument_type,profile.tradability
              FROM catalog.asset_registry_release release
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=release.artifact_id
               AND artifact.artifact_type='asset_registry_release'
               AND artifact.status='published'
              JOIN catalog.asset_set_definition definition
                ON definition.asset_registry_release_id=
                   release.asset_registry_release_id
              JOIN catalog.asset_set_member member
                ON member.asset_set_definition_id=
                   definition.asset_set_definition_id
              JOIN catalog.security security
                ON security.security_id=member.security_id
              JOIN catalog.security_profile profile
                ON profile.asset_registry_release_id=
                   release.asset_registry_release_id
               AND profile.security_id=security.security_id
             WHERE release.artifact_id=:artifact
               AND definition.asset_set_definition_id=:definition
               AND definition.set_key=:context_key
             ORDER BY member.ordinal
                """
            ),
            {
                "artifact": snapshot.asset_registry_artifact_id,
                "definition": snapshot.asset_set_definition_id,
                "context_key": snapshot.asset_context_key,
            },
        ).mappings().all()
    elif snapshot.selection_kind == "dynamic_universe_snapshot":
        rows = connection.execute(
            text(
                """
                SELECT release.asset_registry_release_id,release.artifact_id,
                       release.catalog_version,definition.asset_set_definition_id,
                       definition.set_key,definition.set_type,member.ordinal,
                       security.security_id,security.security_key,
                       profile.instrument_type,profile.tradability,
                       methodology.universe_methodology_id,
                       methodology.artifact_id AS methodology_artifact_id,
                       history.universe_history_id,
                       history.artifact_id AS history_artifact_id,
                       universe.universe_snapshot_id,universe.effective_session
                  FROM catalog.asset_registry_release release
                  JOIN lineage.artifact registry_artifact
                    ON registry_artifact.artifact_id=release.artifact_id
                   AND registry_artifact.artifact_type='asset_registry_release'
                   AND registry_artifact.status='published'
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
                 WHERE release.artifact_id=:artifact
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
                SELECT release.asset_registry_release_id,release.artifact_id,
                       release.catalog_version,selection.explicit_asset_selection_id,
                       selection.artifact_id AS selection_artifact_id,
                       selection.selection_group,selection.selection_document,
                       member.ordinal,member.security_id,
                       member.security_key,member.instrument_type,
                       profile.tradability,selection_artifact.status AS selection_status
                  FROM workspace.v022_explicit_asset_selection selection
                  JOIN lineage.artifact selection_artifact
                    ON selection_artifact.artifact_id=selection.artifact_id
                  JOIN catalog.asset_registry_release release
                    ON release.asset_registry_release_id=selection.asset_registry_release_id
                  JOIN workspace.v022_explicit_asset_selection_member member
                    ON member.explicit_asset_selection_id=
                       selection.explicit_asset_selection_id
                  JOIN catalog.security_profile profile
                    ON profile.asset_registry_release_id=release.asset_registry_release_id
                   AND profile.security_id=member.security_id
                 WHERE selection.explicit_asset_selection_id=:selection
                   AND selection.artifact_id=:selection_artifact
                   AND release.artifact_id=:registry_artifact
                 ORDER BY member.ordinal
                """
            ),
            {
                "selection": snapshot.explicit_asset_selection_id,
                "selection_artifact": snapshot.explicit_asset_selection_artifact_id,
                "registry_artifact": snapshot.asset_registry_artifact_id,
            },
        ).mappings().all()
    if not rows:
        raise ValueError(
            "asset_context_snapshot_unpublished: frozen Asset Context Registry "
            "identity is not published"
        )
    expected_set_type = {
        "fixed_asset_set": "fixed",
        "dynamic_universe_snapshot": "dynamic_methodology",
    }.get(snapshot.selection_kind)
    if (
        expected_set_type is not None and rows[0]["set_type"] != expected_set_type
    ) or any(row["tradability"] == "reference_only" for row in rows) or (
        snapshot.selection_kind == "explicit_security_selection"
        and rows[0]["selection_status"] != "published"
    ):
        raise ValueError(
            "asset_context_snapshot_invalid: compiler requires a tradable fixed Asset Context"
        )
    authoritative = {
        "contract_version": "v0.22.0",
        "selection_kind": snapshot.selection_kind,
        "asset_context_key": (
            snapshot.asset_context_key
            if snapshot.selection_kind == "explicit_security_selection"
            else rows[0]["set_key"]
        ),
        "asset_registry_release_id": str(rows[0]["asset_registry_release_id"]),
        "asset_registry_artifact_id": str(rows[0]["artifact_id"]),
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
            "asset_context_snapshot_mismatch: frozen Asset Context does not "
            "reproduce the published Registry"
        )
    return (
        snapshot.explicit_asset_selection_artifact_id
        if snapshot.selection_kind == "explicit_security_selection"
        else snapshot.asset_registry_artifact_id
    )


def _requires_asset_context_snapshot(
    intent: DraftIntent,
    catalog: GraphCatalog,
) -> bool:
    selections = dict(intent.strategy_parameter_preset_keys)
    strategy_requires_context = any(
        bool(selections.get(strategy_key))
        and bool(catalog.strategy_versions[strategy_key].parameter_presets)
        for strategy_key in intent.strategy_keys
    )
    defense_requires_context = any(
        defense_key != "none"
        and isinstance(catalog.defense_versions.get(defense_key), DefenseSpec)
        for defense_key in intent.defense_keys
    )
    return strategy_requires_context or defense_requires_context


def _composed_defense_version_ids(
    compiled: CompilationResult,
) -> tuple[uuid.UUID, ...]:
    return tuple(
        sorted(
            {
                uuid.UUID(branch.defense_version_id)
                for branch in compiled.branches
                if branch.defense_version_id is not None
                and branch.defense_timing_policy_version_id is not None
            },
            key=str,
        )
    )


def _compile_context_document(
    intent: DraftIntent,
    snapshot: AssetContextSnapshot | None,
    resolved_data_binding: ResolvedDataBindingSnapshot | None,
    *,
    include_fingerprints: bool,
    execution_context: ExecutionDataContextPublication | None = None,
    defense_contexts: tuple[DefenseExecutionContextPublication, ...] = (),
) -> dict[str, object]:
    document: dict[str, object] = {}
    if include_fingerprints:
        document.update(
            {
                "asset_context_fingerprint": intent.asset_context_fingerprint,
                "resolved_data_binding_fingerprint": (
                    intent.resolved_data_binding_fingerprint
                ),
            }
        )
    if snapshot is not None:
        document["asset_context_snapshot"] = snapshot.model_dump(mode="json")
    if resolved_data_binding is not None:
        document["resolved_data_binding_snapshot"] = resolved_data_binding.model_dump(
            mode="json"
        )
    if execution_context is not None:
        document["compiled_execution_data_context_id"] = str(execution_context.context_id)
        document["execution_data_context_fingerprint"] = (
            execution_context.context_fingerprint
        )
    if defense_contexts:
        document["defense_execution_contexts"] = [
            {
                "compiled_defense_execution_context_id": str(item.context_id),
                "defense_version_id": str(item.defense_version_id),
                "defense_execution_context_fingerprint": item.context_fingerprint,
            }
            for item in defense_contexts
        ]
    return document


def _load_aggregations(
    connection: Connection, release_id: uuid.UUID
) -> dict[str, AggregationSpec]:
    rows = connection.execute(
        text(
            """
            SELECT f.family_key,v.aggregation_version_id,v.execution_mode,c.contract_key,
                   p.minimum_count,p.maximum_count
            FROM aggregation.aggregation_version v
            JOIN aggregation.aggregation_family f ON f.aggregation_family_id=v.aggregation_family_id
            JOIN aggregation.aggregation_input_port p ON p.aggregation_version_id=v.aggregation_version_id
            JOIN data.payload_contract_version cv ON cv.payload_contract_version_id=p.payload_contract_version_id
            JOIN data.payload_contract_family c ON c.payload_contract_family_id=cv.payload_contract_family_id
            WHERE v.artifact_id IN (SELECT component_artifact_id FROM workspace.v022_catalog_release_component WHERE catalog_release_id=:release)
            """
        ),
        {"release": release_id},
    ).mappings()
    result: dict[str, AggregationSpec] = {}
    for row in rows:
        axes: list[tuple[str, ...]] = []
        for prefix in ("parameter_preset", "target", "training_preset"):
            axes.append(
                tuple(
                    _local_axis_key(row["family_key"], value)
                    for value in connection.scalars(
                        text(
                            f"SELECT d.{prefix}_key FROM aggregation.{prefix}_definition d "
                            "JOIN aggregation.aggregation_family f "
                            "ON f.aggregation_family_id=d.aggregation_family_id "
                            "WHERE f.family_key=:family ORDER BY 1"
                        ),
                        {"family": row["family_key"]},
                    )
                )
            )
        result[row["family_key"]] = AggregationSpec(
            row["family_key"], str(row["aggregation_version_id"]), row["execution_mode"],
            row["contract_key"], row["minimum_count"], row["maximum_count"],
            axes[0], axes[1], axes[2],
        )
    return result


def _axis_ids(
    connection: Connection,
    family_key: str,
    parameter_key: str | None,
    target_key: str | None,
    training_key: str | None,
) -> tuple[uuid.UUID | None, uuid.UUID | None, uuid.UUID | None]:
    return (
        _axis_id(connection, family_key, "parameter_preset", parameter_key),
        _axis_id(connection, family_key, "target", target_key),
        _axis_id(connection, family_key, "training_preset", training_key),
    )


def _axis_id(
    connection: Connection,
    family_key: str,
    prefix: str,
    axis_key: str | None,
) -> uuid.UUID | None:
    if axis_key is None:
        return None
    value = connection.scalar(
        text(
            f"SELECT v.{prefix}_version_id FROM aggregation.{prefix}_version v "
            f"JOIN aggregation.{prefix}_definition d "
            f"ON d.{prefix}_definition_id=v.{prefix}_definition_id "
            "JOIN aggregation.aggregation_family f "
            "ON f.aggregation_family_id=d.aggregation_family_id "
            f"WHERE f.family_key=:family AND d.{prefix}_key=:key"
        ),
        {"family": family_key, "key": f"{family_key}__{axis_key}"},
    )
    if value is None:
        raise ValueError(f"Published {prefix} not found: {family_key}/{axis_key}")
    return cast(uuid.UUID, value)


def _axis_artifact_id(
    connection: Connection,
    family_key: str,
    prefix: str,
    axis_key: str,
) -> uuid.UUID:
    value = connection.scalar(
        text(
            f"SELECT v.artifact_id FROM aggregation.{prefix}_version v "
            f"JOIN aggregation.{prefix}_definition d "
            f"ON d.{prefix}_definition_id=v.{prefix}_definition_id "
            "JOIN aggregation.aggregation_family f "
            "ON f.aggregation_family_id=d.aggregation_family_id "
            f"WHERE f.family_key=:family AND d.{prefix}_key=:key"
        ),
        {"family": family_key, "key": f"{family_key}__{axis_key}"},
    )
    if value is None:
        raise ValueError(f"Published {prefix} Artifact not found: {family_key}/{axis_key}")
    return cast(uuid.UUID, value)


def _local_axis_key(family_key: str, stored_key: str) -> str:
    prefix = f"{family_key}__"
    if not stored_key.startswith(prefix):
        raise ValueError(f"Aggregation axis key is outside family namespace: {stored_key}")
    return stored_key.removeprefix(prefix)


def _node_stage(value: int) -> Literal[1, 2, 3]:
    if value not in {1, 2, 3}:
        raise ValueError(f"Invalid Processing Node stage: {value}")
    return cast(Literal[1, 2, 3], value)
