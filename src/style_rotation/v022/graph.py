from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.contracts import Key, SemVer, StrictModel
from style_rotation.v022.trainable_ensemble import (
    TrainableEnsembleMemberSpec,
    compile_trainable_ensemble_spec,
)

Stage = Literal[0, 1, 2, 3]


class FeatureSelection(StrictModel):
    feature_key: Key
    visible_stage: Stage


class AggregationSelection(StrictModel):
    family_key: Key
    parameter_preset_keys: tuple[Key, ...] = ()
    target_keys: tuple[Key, ...] = ()
    training_preset_keys: tuple[Key, ...] = ()

    @model_validator(mode="after")
    def validate_axes(self) -> AggregationSelection:
        _unique("parameter preset", self.parameter_preset_keys)
        _unique("target", self.target_keys)
        _unique("training preset", self.training_preset_keys)
        return self


class AssetContextMember(StrictModel):
    ordinal: int = Field(ge=0)
    security_id: UUID
    security_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,239}$")
    instrument_type: str = Field(min_length=1, max_length=160)


class AssetContextSnapshot(StrictModel):
    contract_version: Literal["v0.22.0"]
    selection_kind: Literal[
        "fixed_asset_set", "dynamic_universe_snapshot", "explicit_security_selection"
    ]
    asset_context_key: Key
    asset_registry_release_id: UUID
    asset_registry_artifact_id: UUID
    asset_registry_catalog_version: SemVer
    asset_set_definition_id: UUID | None = None
    explicit_asset_selection_id: UUID | None = None
    explicit_asset_selection_artifact_id: UUID | None = None
    selection_group: Literal["stock", "fund"] | None = None
    universe_methodology_id: UUID | None = None
    universe_methodology_artifact_id: UUID | None = None
    universe_history_id: UUID | None = None
    universe_history_artifact_id: UUID | None = None
    universe_snapshot_id: UUID | None = None
    universe_effective_session: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"
    )
    members: tuple[AssetContextMember, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_members(self) -> AssetContextSnapshot:
        _unique("Asset Context member ordinal", [item.ordinal for item in self.members])
        _unique("Asset Context member Security", [item.security_id for item in self.members])
        if tuple(item.ordinal for item in self.members) != tuple(range(len(self.members))):
            raise ValueError("Asset Context members require canonical contiguous ordinals")
        dynamic_identity = (
            self.universe_methodology_id,
            self.universe_methodology_artifact_id,
            self.universe_history_id,
            self.universe_history_artifact_id,
            self.universe_snapshot_id,
            self.universe_effective_session,
        )
        if self.selection_kind == "fixed_asset_set" and any(
            item is not None for item in dynamic_identity
        ):
            raise ValueError("Fixed Asset Context cannot carry Dynamic Universe identity")
        if self.selection_kind == "dynamic_universe_snapshot" and any(
            item is None for item in dynamic_identity
        ):
            raise ValueError("Dynamic Asset Context requires its complete Universe Snapshot")
        explicit_identity = (
            self.explicit_asset_selection_id,
            self.explicit_asset_selection_artifact_id,
            self.selection_group,
        )
        if self.selection_kind == "explicit_security_selection":
            if self.asset_set_definition_id is not None or any(
                item is not None for item in dynamic_identity
            ) or any(item is None for item in explicit_identity):
                raise ValueError(
                    "Explicit Asset Context requires only its complete Selection identity"
                )
        elif self.asset_set_definition_id is None or any(
            item is not None for item in explicit_identity
        ):
            raise ValueError(
                "Published Asset Set contexts require one Asset Set and no explicit Selection"
            )
        return self

    @model_serializer(mode="wrap")
    def serialize_without_inapplicable_identity(
        self, handler: SerializerFunctionWrapHandler
    ) -> object:
        document = handler(self)
        if isinstance(document, dict):
            return {key: value for key, value in document.items() if value is not None}
        return document


class DraftIntent(StrictModel):
    catalog_release_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_data_binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    frequency: Key
    aggregation_inputs: tuple[Key, ...] = Field(min_length=1)
    explicit_features: tuple[FeatureSelection, ...] = ()
    aggregations: tuple[AggregationSelection, ...] = Field(min_length=1)
    strategy_keys: tuple[Key, ...] = Field(min_length=1)
    strategy_parameter_preset_keys: tuple[tuple[Key, tuple[Key, ...]], ...] = ()
    defense_keys: tuple[Key | Literal["none"], ...] = ("none",)

    @model_validator(mode="after")
    def validate_identity(self) -> DraftIntent:
        _unique("aggregation input", self.aggregation_inputs)
        _unique(
            "explicit feature",
            [(item.feature_key, item.visible_stage) for item in self.explicit_features],
        )
        _unique("aggregation selection", [item.family_key for item in self.aggregations])
        _unique("strategy", self.strategy_keys)
        _unique(
            "strategy parameter preset owner",
            [strategy_key for strategy_key, _ in self.strategy_parameter_preset_keys],
        )
        for strategy_key, preset_keys in self.strategy_parameter_preset_keys:
            _unique(f"strategy parameter preset for {strategy_key}", preset_keys)
        _unique("defense", self.defense_keys)
        return self


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    feature_key: str
    version_id: str
    origin_stage: Stage
    payload_contract_key: str
    producer_node_key: str | None = None
    output_port_key: str | None = None
    maximum_projection_stage: Stage = 3
    feature_family_key: str | None = None
    unit: str | None = None
    direction: str | None = None


@dataclass(frozen=True, slots=True)
class NodeInputSpec:
    port_key: str
    source_feature_key: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class NodeSpec:
    node_key: str
    version_id: str
    stage_no: Literal[1, 2, 3]
    output_feature_keys: tuple[str, ...]
    inputs: tuple[NodeInputSpec, ...]


@dataclass(frozen=True, slots=True)
class AggregationSpec:
    family_key: str
    version_id: str
    execution_mode: Literal["deterministic", "supervised"]
    input_payload_contract_key: str
    minimum_inputs: int
    maximum_inputs: int
    parameter_preset_keys: tuple[str, ...] = ()
    target_keys: tuple[str, ...] = ()
    training_preset_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AggregationFeatureTaxonomyEntrySpec:
    feature_family_key: str
    research_dimension_key: str
    accepted_units: tuple[str, ...]
    accepted_directions: tuple[str, ...]
    native_hierarchical_eligible: bool


@dataclass(frozen=True, slots=True)
class AggregationFeatureTaxonomySpec:
    version_id: str
    artifact_id: str
    taxonomy_fingerprint: str
    entries: Mapping[str, AggregationFeatureTaxonomyEntrySpec]


@dataclass(frozen=True, slots=True)
class StrategySpec:
    version_id: str
    supported_frequencies: tuple[str, ...]
    parameter_presets: Mapping[str, StrategyParameterPresetSpec] = field(default_factory=dict)
    required_instrument_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyParameterPresetSpec:
    preset_key: str
    version_id: str
    parameter_fingerprint: str
    resolved_parameters: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class DefenseAssetContextSpec:
    asset_context_key: str
    asset_registry_release_id: str
    asset_registry_artifact_id: str
    asset_set_definition_id: str


@dataclass(frozen=True, slots=True)
class DefenseSpec:
    version_id: str
    version_fingerprint: str
    timing_policy_version_id: str
    timing_policy_version_fingerprint: str
    allocation_policy_version_id: str
    allocation_policy_version_fingerprint: str
    supported_frequencies: tuple[str, ...]
    supported_asset_contexts: tuple[DefenseAssetContextSpec, ...]


@dataclass(frozen=True, slots=True)
class GraphCatalog:
    features: Mapping[str, FeatureSpec]
    nodes: Mapping[str, NodeSpec]
    aggregations: Mapping[str, AggregationSpec]
    strategy_versions: Mapping[str, StrategySpec]
    # A plain version id is the frozen v0.22.0-v0.22.4 compatibility shape.
    # Composed Defense Packages are intentionally opt-in so legacy normalized
    # Graph documents and fingerprints never acquire synthetic null fields.
    defense_versions: Mapping[str, str | DefenseSpec]
    feature_taxonomy: AggregationFeatureTaxonomySpec | None = None


@dataclass(frozen=True, slots=True)
class FeatureOccurrence:
    feature_key: str
    feature_version_id: str
    stage_no: Stage
    production_kind: Literal["raw_input", "node_output", "layer_projection"]
    source_key: tuple[str, int] | None
    node_key: str | None
    output_port_key: str | None
    is_explicit: bool
    is_aggregation_input: bool


@dataclass(frozen=True, slots=True)
class CompiledNode:
    node_key: str
    node_version_id: str
    stage_no: int
    output_occurrence_keys: tuple[tuple[str, int], ...]
    inputs: tuple[tuple[str, tuple[str, int], int], ...]


@dataclass(frozen=True, slots=True)
class AggregationInstance:
    instance_key: str
    family_key: str
    aggregation_version_id: str
    parameter_preset_key: str | None
    target_key: str | None
    training_preset_key: str | None
    ordered_inputs: tuple[tuple[str, int], ...]
    recipe_document: Mapping[str, object] | None = None
    recipe_fingerprint: str | None = None
    feature_schema_document: Mapping[str, object] | None = None
    feature_schema_fingerprint: str | None = None
    ensemble_spec_document: Mapping[str, object] | None = None
    ensemble_spec_fingerprint: str | None = None
    ensemble_members: tuple[TrainableEnsembleMemberSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyBranch:
    branch_key: str
    aggregation_instance_key: str
    strategy_key: str
    strategy_version_id: str
    strategy_parameter_preset_key: str | None
    strategy_parameter_preset_version_id: str | None
    strategy_parameter_fingerprint: str | None
    resolved_strategy_parameters: Mapping[str, object] | None
    defense_key: str | None
    defense_version_id: str | None
    defense_version_fingerprint: str | None = None
    defense_timing_policy_version_id: str | None = None
    defense_timing_policy_version_fingerprint: str | None = None
    defense_allocation_policy_version_id: str | None = None
    defense_allocation_policy_version_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class CompilationResult:
    graph_fingerprint: str
    normalized_graph: dict[str, object]
    occurrences: tuple[FeatureOccurrence, ...]
    nodes: tuple[CompiledNode, ...]
    aggregation_instances: tuple[AggregationInstance, ...]
    branches: tuple[StrategyBranch, ...]


def compile_intent(
    intent: DraftIntent,
    catalog: GraphCatalog,
    *,
    asset_context_snapshot: AssetContextSnapshot | None = None,
) -> CompilationResult:
    """Pure v0.22 compiler; every external input is frozen in ``intent`` or ``catalog``."""

    if (
        asset_context_snapshot is not None
        and sha256_hexdigest(asset_context_snapshot.model_dump(mode="json"))
        != intent.asset_context_fingerprint
    ):
        raise ValueError("asset_context_snapshot_mismatch: fingerprint differs from Intent")

    occurrence_flags: dict[tuple[str, int], bool] = {
        (item.feature_key, item.visible_stage): True for item in intent.explicit_features
    }
    for key in intent.aggregation_inputs:
        occurrence_flags[(key, 3)] = True

    occurrences: dict[tuple[str, int], FeatureOccurrence] = {}
    nodes: dict[str, CompiledNode] = {}
    visiting: set[tuple[str, int]] = set()

    def expand(feature_key: str, stage: int) -> tuple[str, int]:
        occurrence_key = (feature_key, stage)
        if occurrence_key in occurrences:
            return occurrence_key
        if occurrence_key in visiting:
            raise ValueError(f"Cycle detected at {feature_key}@{stage}")
        feature = _required(catalog.features, feature_key, "feature")
        if stage < feature.origin_stage:
            raise ValueError(f"Feature {feature_key} does not exist at stage {stage}")
        if stage > feature.maximum_projection_stage:
            raise ValueError(
                f"Feature {feature_key} is not published for projection to stage {stage}"
            )
        visiting.add(occurrence_key)
        if stage > feature.origin_stage:
            source = expand(feature_key, stage - 1)
            occurrence = FeatureOccurrence(
                feature_key,
                feature.version_id,
                _stage(stage),
                "layer_projection",
                source,
                None,
                None,
                occurrence_flags.get(occurrence_key, False),
                stage == 3 and feature_key in intent.aggregation_inputs,
            )
        elif feature.origin_stage == 0:
            occurrence = FeatureOccurrence(
                feature_key,
                feature.version_id,
                0,
                "raw_input",
                None,
                None,
                None,
                occurrence_flags.get(occurrence_key, False), False,
            )
        else:
            if feature.producer_node_key is None:
                raise ValueError(f"Feature {feature_key} has no producer")
            node = _required(catalog.nodes, feature.producer_node_key, "node")
            if node.stage_no != stage or feature_key not in node.output_feature_keys:
                raise ValueError(f"Producer mismatch for {feature_key}")
            if node.node_key not in nodes:
                compiled_inputs: list[tuple[str, tuple[str, int], int]] = []
                for binding in sorted(
                    node.inputs, key=lambda item: (item.ordinal, item.port_key)
                ):
                    source = expand(binding.source_feature_key, stage - 1)
                    compiled_inputs.append((binding.port_key, source, binding.ordinal))
                output_keys = tuple((key, stage) for key in node.output_feature_keys)
                nodes[node.node_key] = CompiledNode(
                    node.node_key,
                    node.version_id,
                    stage,
                    output_keys,
                    tuple(compiled_inputs),
                )
                for output_key in node.output_feature_keys:
                    output_feature = _required(catalog.features, output_key, "feature")
                    output_occurrence_key = (output_key, stage)
                    occurrences[output_occurrence_key] = FeatureOccurrence(
                        output_key,
                        output_feature.version_id,
                        _stage(stage),
                        "node_output",
                        None,
                        node.node_key,
                        output_feature.output_port_key,
                        occurrence_flags.get(output_occurrence_key, False),
                        stage == 3 and output_key in intent.aggregation_inputs,
                    )
            occurrence = occurrences[occurrence_key]
        visiting.remove(occurrence_key)
        occurrences[occurrence_key] = occurrence
        return occurrence_key

    for item in sorted(
        intent.explicit_features,
        key=lambda value: (value.visible_stage, value.feature_key),
    ):
        expand(item.feature_key, item.visible_stage)
    ordered_aggregation_inputs = tuple(expand(key, 3) for key in intent.aggregation_inputs)

    instances: list[AggregationInstance] = []
    for selection in sorted(intent.aggregations, key=lambda item: item.family_key):
        specification = _required(catalog.aggregations, selection.family_key, "aggregation")
        input_count = len(ordered_aggregation_inputs)
        if not specification.minimum_inputs <= input_count <= specification.maximum_inputs:
            raise ValueError(f"Aggregation {selection.family_key} rejects {input_count} inputs")
        for occurrence_key in ordered_aggregation_inputs:
            feature = catalog.features[occurrence_key[0]]
            if feature.payload_contract_key != specification.input_payload_contract_key:
                raise ValueError(
                    f"Aggregation {selection.family_key} rejects payload "
                    f"{feature.payload_contract_key}"
                )
        axes = _conditional_axes(selection, specification)
        ensemble_spec = (
            compile_trainable_ensemble_spec(
                selection.family_key,
                selection.target_keys,
                selection.training_preset_keys,
            )
            if specification.execution_mode == "supervised"
            else None
        )
        if ensemble_spec is not None and len(ensemble_spec.members) > 1:
            axes = tuple((parameter, None, None) for parameter, _target, _training in axes[:1])
        for parameter, target, training in axes:
            key = "__".join(
                value for value in (selection.family_key, parameter, target, training) if value
            )
            recipe_document: Mapping[str, object] | None = None
            recipe_fingerprint: str | None = None
            feature_schema_document: Mapping[str, object] | None = None
            feature_schema_fingerprint: str | None = None
            if (
                selection.family_key == "hierarchical_weighted_mean"
                and parameter == "active_dimension_equal_component_equal_v1"
            ):
                recipe_document = _native_hierarchical_recipe(
                    ordered_aggregation_inputs, catalog
                )
                recipe_fingerprint = sha256_hexdigest(recipe_document)
            if specification.execution_mode == "supervised":
                feature_schema_document = {
                    "version_number": 1,
                    "ordered_feature_keys": [
                        feature_key for feature_key, _stage_no in ordered_aggregation_inputs
                    ],
                    "missing_policy": "complete_case_fail_closed",
                    "known_at_policy": "feature_known_at_not_after_decision_cutoff",
                }
                feature_schema_fingerprint = sha256_hexdigest(feature_schema_document)
            ensemble_document: dict[str, object] | None = None
            ensemble_fingerprint: str | None = None
            if ensemble_spec is not None and len(ensemble_spec.members) > 1:
                if feature_schema_fingerprint is None:
                    raise AssertionError("Trainable Ensemble requires a Feature Schema")
                ensemble_document = {
                    **ensemble_spec.document,
                    "aggregation_version_id": specification.version_id,
                    "feature_schema_fingerprint": feature_schema_fingerprint,
                }
                ensemble_fingerprint = sha256_hexdigest(ensemble_document)
            instances.append(
                AggregationInstance(
                    key,
                    selection.family_key,
                    specification.version_id,
                    parameter,
                    target,
                    training,
                    ordered_aggregation_inputs,
                    recipe_document,
                    recipe_fingerprint,
                    feature_schema_document,
                    feature_schema_fingerprint,
                    ensemble_document,
                    ensemble_fingerprint,
                    (
                        ensemble_spec.members
                        if ensemble_spec is not None and len(ensemble_spec.members) > 1
                        else ()
                    ),
                )
            )

    branches: list[StrategyBranch] = []
    strategy_preset_selections = dict(intent.strategy_parameter_preset_keys)
    if set(strategy_preset_selections) - set(intent.strategy_keys):
        raise ValueError("Strategy preset selected for an unselected Strategy")
    for instance in instances:
        for strategy_key in sorted(intent.strategy_keys):
            strategy = _required(catalog.strategy_versions, strategy_key, "strategy")
            if intent.frequency not in strategy.supported_frequencies:
                raise ValueError(
                    f"Strategy {strategy_key} does not support frequency {intent.frequency}"
                )
            selected_presets = tuple(sorted(strategy_preset_selections.get(strategy_key, ())))
            available_presets = strategy.parameter_presets
            if available_presets and not selected_presets:
                raise ValueError(f"Strategy {strategy_key} requires a parameter preset")
            if not set(selected_presets) <= set(available_presets):
                raise ValueError(f"Unknown Strategy parameter preset for {strategy_key}")
            preset_axis: tuple[StrategyParameterPresetSpec | None, ...] = (
                tuple(available_presets[key] for key in selected_presets)
                if selected_presets
                else (None,)
            )
            for preset in preset_axis:
                if preset is not None:
                    if asset_context_snapshot is None:
                        raise ValueError(
                            "asset_context_required: Strategy parameter presets require "
                            "a frozen Asset Context snapshot"
                        )
                    target_k = preset.resolved_parameters.get("target_k")
                    if isinstance(target_k, bool) or not isinstance(target_k, int):
                        raise ValueError(
                            f"Strategy parameter preset {strategy_key}:{preset.preset_key} "
                            "has an invalid target_k"
                        )
                    context_reasons = strategy_asset_context_reason_codes(
                        strategy.required_instrument_types,
                        target_k,
                        (
                            item.instrument_type
                            for item in asset_context_snapshot.members
                        ),
                    )
                    if context_reasons:
                        raise ValueError(
                            f"Strategy parameter preset {strategy_key}:{preset.preset_key} "
                            "rejects the frozen Asset Context: "
                            + ",".join(context_reasons)
                        )
                for defense_selection in sorted(intent.defense_keys):
                    defense_key = None if defense_selection == "none" else defense_selection
                    defense = (
                        None
                        if defense_key is None
                        else _required(catalog.defense_versions, defense_key, "defense")
                    )
                    defense_version = (
                        defense.version_id if isinstance(defense, DefenseSpec) else defense
                    )
                    if isinstance(defense, DefenseSpec):
                        if intent.frequency not in defense.supported_frequencies:
                            raise ValueError(
                                f"Defense {defense_key} does not support frequency "
                                f"{intent.frequency}"
                            )
                        if asset_context_snapshot is None:
                            raise ValueError(
                                "asset_context_required: composed Defense requires "
                                "a frozen Asset Context snapshot"
                            )
                        if not any(
                            _defense_supports_asset_context(item, asset_context_snapshot)
                            for item in defense.supported_asset_contexts
                        ):
                            raise ValueError(
                                f"Defense {defense_key} rejects the exact frozen "
                                "Asset Context"
                            )
                    branch_key = "__".join(
                        value
                        for value in (
                            instance.instance_key,
                            strategy_key,
                            preset.preset_key if preset is not None else None,
                            defense_key or "none",
                        )
                        if value
                    )
                    if len(branch_key) > 500:
                        raise ValueError(
                            "Strategy Branch key exceeds the physical identity contract"
                        )
                    branches.append(
                        StrategyBranch(
                            branch_key,
                            instance.instance_key,
                            strategy_key,
                            strategy.version_id,
                            preset.preset_key if preset is not None else None,
                            preset.version_id if preset is not None else None,
                            preset.parameter_fingerprint if preset is not None else None,
                            preset.resolved_parameters if preset is not None else None,
                            defense_key,
                            defense_version,
                            (
                                defense.version_fingerprint
                                if isinstance(defense, DefenseSpec)
                                else None
                            ),
                            (
                                defense.timing_policy_version_id
                                if isinstance(defense, DefenseSpec)
                                else None
                            ),
                            (
                                defense.timing_policy_version_fingerprint
                                if isinstance(defense, DefenseSpec)
                                else None
                            ),
                            (
                                defense.allocation_policy_version_id
                                if isinstance(defense, DefenseSpec)
                                else None
                            ),
                            (
                                defense.allocation_policy_version_fingerprint
                                if isinstance(defense, DefenseSpec)
                                else None
                            ),
                        )
                    )

    normalized = _normalized_graph(
        intent, occurrences.values(), nodes.values(), instances, branches
    )
    return CompilationResult(
        graph_fingerprint=sha256_hexdigest(normalized),
        normalized_graph=normalized,
        occurrences=tuple(
            sorted(occurrences.values(), key=lambda item: (item.stage_no, item.feature_key))
        ),
        nodes=tuple(sorted(nodes.values(), key=lambda item: (item.stage_no, item.node_key))),
        aggregation_instances=tuple(instances),
        branches=tuple(branches),
    )


def _conditional_axes(
    selection: AggregationSelection, specification: AggregationSpec
) -> tuple[tuple[str | None, str | None, str | None], ...]:
    parameters: tuple[str | None, ...] = (
        tuple(sorted(selection.parameter_preset_keys)) or (None,)
    )
    if any(
        item is not None and item not in specification.parameter_preset_keys
        for item in parameters
    ):
        raise ValueError(f"Unknown preset for {selection.family_key}")
    if specification.execution_mode == "deterministic":
        if selection.target_keys or selection.training_preset_keys:
            raise ValueError("Deterministic aggregation cannot expand Target/Training axes")
        return tuple((item, None, None) for item in parameters)
    if not selection.target_keys or not selection.training_preset_keys:
        raise ValueError("Supervised aggregation requires Target and Training axes")
    if not set(selection.target_keys) <= set(specification.target_keys):
        raise ValueError(f"Unknown target for {selection.family_key}")
    if not set(selection.training_preset_keys) <= set(specification.training_preset_keys):
        raise ValueError(f"Unknown training preset for {selection.family_key}")
    return tuple(
        (parameter, target, training)
        for parameter in parameters
        for target in sorted(selection.target_keys)
        for training in sorted(selection.training_preset_keys)
    )


def _normalized_graph(
    intent: DraftIntent,
    occurrences: Iterable[FeatureOccurrence],
    nodes: Iterable[CompiledNode],
    instances: Iterable[AggregationInstance],
    branches: Iterable[StrategyBranch],
) -> dict[str, object]:
    return {
        "compiler_contract_version": "v0.22.0",
        "catalog_release_fingerprint": intent.catalog_release_fingerprint,
        "asset_context_fingerprint": intent.asset_context_fingerprint,
        "resolved_data_binding_fingerprint": intent.resolved_data_binding_fingerprint,
        "frequency": intent.frequency,
        "occurrences": [item.__dict__ if hasattr(item, "__dict__") else {
            "feature_key": item.feature_key,
            "feature_version_id": item.feature_version_id,
            "stage_no": item.stage_no,
            "production_kind": item.production_kind,
            "source_key": item.source_key,
            "node_key": item.node_key,
            "output_port_key": item.output_port_key,
            "is_explicit": item.is_explicit,
            "is_aggregation_input": item.is_aggregation_input,
        } for item in sorted(occurrences, key=lambda value: (value.stage_no, value.feature_key))],
        "nodes": [{
            "node_key": item.node_key,
            "node_version_id": item.node_version_id,
            "stage_no": item.stage_no,
            "output_occurrence_keys": item.output_occurrence_keys,
            "inputs": item.inputs,
        } for item in sorted(nodes, key=lambda value: (value.stage_no, value.node_key))],
        "aggregation_instances": [{
            "instance_key": item.instance_key,
            "family_key": item.family_key,
            "aggregation_version_id": item.aggregation_version_id,
            "parameter_preset_key": item.parameter_preset_key,
            "target_key": item.target_key,
            "training_preset_key": item.training_preset_key,
            "ordered_inputs": item.ordered_inputs,
            **(
                {
                    "recipe_fingerprint": item.recipe_fingerprint,
                    "recipe_document": item.recipe_document,
                }
                if item.recipe_document is not None
                else {}
            ),
            **(
                {
                    "feature_schema_fingerprint": item.feature_schema_fingerprint,
                    "feature_schema_document": item.feature_schema_document,
                }
                if item.feature_schema_document is not None
                else {}
            ),
            **(
                {
                    "ensemble_spec_fingerprint": item.ensemble_spec_fingerprint,
                    "ensemble_spec_document": item.ensemble_spec_document,
                }
                if item.ensemble_spec_document is not None
                else {}
            ),
        } for item in instances],
        "branches": [strategy_branch_identity_document(item) for item in branches],
    }


def _native_hierarchical_recipe(
    ordered_inputs: tuple[tuple[str, int], ...],
    catalog: GraphCatalog,
) -> dict[str, object]:
    if len(ordered_inputs) < 2:
        raise ValueError(
            "native_hierarchical_requires_multiple_inputs: select at least two signals"
        )
    taxonomy = catalog.feature_taxonomy
    if taxonomy is None:
        raise ValueError(
            "aggregation_feature_taxonomy_required: native hierarchical recipe "
            "requires a published taxonomy"
        )
    grouped: dict[str, list[str]] = {}
    for feature_key, _stage_no in ordered_inputs:
        feature = catalog.features[feature_key]
        if feature.feature_family_key is None:
            raise ValueError(
                f"aggregation_taxonomy_feature_family_missing: {feature_key}"
            )
        entry = taxonomy.entries.get(feature.feature_family_key)
        if entry is None:
            raise ValueError(f"aggregation_taxonomy_entry_missing: {feature_key}")
        if not entry.native_hierarchical_eligible:
            raise ValueError(
                f"aggregation_native_calibration_required: {feature_key}"
            )
        if feature.unit not in entry.accepted_units:
            raise ValueError(f"aggregation_native_scale_incompatible: {feature_key}")
        if feature.direction not in entry.accepted_directions:
            raise ValueError(
                f"aggregation_native_direction_incompatible: {feature_key}"
            )
        grouped.setdefault(entry.research_dimension_key, []).append(feature_key)
    dimension_weight = _q18_equal_weight(len(grouped))
    dimensions: list[dict[str, object]] = []
    for dimension_key in sorted(grouped):
        features = grouped[dimension_key]
        component_weight = _q18_equal_weight(len(features))
        dimensions.append(
            {
                "dimension_key": dimension_key,
                "dimension_weight": dimension_weight,
                "components": [
                    {
                        "feature_key": feature_key,
                        "component_weight": component_weight,
                    }
                    for feature_key in features
                ],
            }
        )
    return {
        "contract_version": "v0.22.0",
        "recipe_kind": "native_hierarchical_equal_v2",
        "family_key": "hierarchical_weighted_mean",
        "parameter_preset_key": "active_dimension_equal_component_equal_v1",
        "taxonomy_fingerprint": taxonomy.taxonomy_fingerprint,
        "input_scale": "centered_rank",
        "direction": "higher_is_better",
        "missing_policy": "fail_complete_case",
        "quantum": "1e-18",
        "rounding": "half_even",
        "ordered_inputs": [feature_key for feature_key, _ in ordered_inputs],
        "dimensions": dimensions,
    }


def _q18_equal_weight(count: int) -> str:
    if count < 1:
        raise ValueError("Aggregation weight groups cannot be empty")
    return format(
        (Decimal(1) / Decimal(count)).quantize(
            Decimal("1e-18"), rounding=ROUND_HALF_EVEN
        ),
        "f",
    )


def _required[T](values: Mapping[str, T], key: str, label: str) -> T:
    try:
        return values[key]
    except KeyError as error:
        raise ValueError(f"Unknown {label}: {key}") from error


def strategy_required_instrument_types(policy: object) -> tuple[str, ...]:
    if policy is None:
        return ()
    if policy == "common_stock_or_adr":
        return ("adr", "common_stock")
    raise ValueError(f"Unsupported Strategy instrument policy: {policy}")


def strategy_branch_identity_document(branch: StrategyBranch) -> dict[str, object]:
    identity: dict[str, object] = {
        "branch_key": branch.branch_key,
        "aggregation_instance_key": branch.aggregation_instance_key,
        "strategy_key": branch.strategy_key,
        "strategy_version_id": branch.strategy_version_id,
    }
    if branch.strategy_parameter_preset_version_id is not None:
        identity.update(
            {
                "strategy_parameter_preset_key": branch.strategy_parameter_preset_key,
                "strategy_parameter_preset_version_id": (
                    branch.strategy_parameter_preset_version_id
                ),
                "strategy_parameter_fingerprint": branch.strategy_parameter_fingerprint,
                "resolved_strategy_parameters": branch.resolved_strategy_parameters,
            }
        )
    identity.update(
        {
            "defense_key": branch.defense_key,
            "defense_version_id": branch.defense_version_id,
        }
    )
    if branch.defense_version_fingerprint is not None:
        identity.update(
            {
                "defense_version_fingerprint": branch.defense_version_fingerprint,
                "defense_timing_policy_version_id": (
                    branch.defense_timing_policy_version_id
                ),
                "defense_timing_policy_version_fingerprint": (
                    branch.defense_timing_policy_version_fingerprint
                ),
                "defense_allocation_policy_version_id": (
                    branch.defense_allocation_policy_version_id
                ),
                "defense_allocation_policy_version_fingerprint": (
                    branch.defense_allocation_policy_version_fingerprint
                ),
            }
        )
    return identity


def _defense_supports_asset_context(
    supported: DefenseAssetContextSpec,
    snapshot: AssetContextSnapshot,
) -> bool:
    if snapshot.selection_kind == "explicit_security_selection":
        return (
            snapshot.selection_group in {"stock", "fund"}
            and supported.asset_registry_release_id
            == str(snapshot.asset_registry_release_id)
            and supported.asset_registry_artifact_id
            == str(snapshot.asset_registry_artifact_id)
        )
    return (
        supported.asset_context_key == snapshot.asset_context_key
        and supported.asset_registry_release_id == str(snapshot.asset_registry_release_id)
        and supported.asset_registry_artifact_id == str(snapshot.asset_registry_artifact_id)
        and supported.asset_set_definition_id == str(snapshot.asset_set_definition_id)
    )


def strategy_asset_context_reason_codes(
    required_instrument_types: tuple[str, ...],
    target_k: int,
    instrument_types: Iterable[str],
) -> tuple[str, ...]:
    normalized_types = tuple(_normalize_instrument_type(value) for value in instrument_types)
    reasons: list[str] = []
    if required_instrument_types and any(
        value not in required_instrument_types for value in normalized_types
    ):
        reasons.append("asset_context_instrument_type_unsupported")
    if target_k > len(normalized_types):
        reasons.append("insufficient_eligible_assets")
    return tuple(reasons)


def _normalize_instrument_type(value: str) -> str:
    return value.casefold().replace(" ", "_").replace("-", "_")


def _stage(value: int) -> Stage:
    if value not in {0, 1, 2, 3}:
        raise ValueError(f"Invalid stage: {value}")
    return value  # type: ignore[return-value]


def _unique(label: str, values: Iterable[object]) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"Duplicate {label}")
