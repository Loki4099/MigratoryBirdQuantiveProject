from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.admission import StructuralEstimate, structural_admission
from style_rotation.v022.catalog import LoadedCatalogRelease, load_catalog_release
from style_rotation.v022.graph import (
    strategy_asset_context_reason_codes,
    strategy_required_instrument_types,
)
from style_rotation.v022.model_migration import load_model_migration_registry

Stage = Literal[0, 1, 2, 3]
OccurrenceKey = tuple[str, int]
_FROZEN_RECIPE_FAMILIES = frozenset(
    {"hierarchical_weighted_mean", "directional_weighted_vote"}
)


@dataclass(frozen=True, slots=True)
class ExplicitFeature:
    feature_key: str
    stage_no: Stage


@dataclass(frozen=True, slots=True)
class WorkspacePreviewIntent:
    explicit_features: tuple[ExplicitFeature, ...]
    aggregation_family_keys: tuple[str, ...]
    frequency: Literal["weekly", "monthly"]
    aggregation_parameter_preset_keys: tuple[tuple[str, tuple[str, ...]], ...] = ()
    strategy_keys: tuple[str, ...] = ("cross_section_rank_top_k_parity",)
    defense_keys: tuple[str, ...] = ("none",)
    strategy_parameter_preset_keys: tuple[tuple[str, tuple[str, ...]], ...] = ()
    aggregation_target_keys: tuple[tuple[str, tuple[str, ...]], ...] = ()
    aggregation_training_preset_keys: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class _Feature:
    family_key: str
    variant_key: str
    name: str
    origin_stage: Stage
    formula_identity: str
    semantic_role: str
    unit: str
    payload_contract_key: str
    direction: str
    aggregation_readiness: str
    research_hypothesis: str
    parameters: dict[str, object]
    input_feature_keys: tuple[str, ...]
    output_semantics: dict[str, object]
    producer_node_key: str | None
    output_port_key: str | None


@dataclass(frozen=True, slots=True)
class _Node:
    variant_key: str
    stage_no: Stage
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Aggregation:
    family_key: str
    name: str
    algorithm_identity: str
    objective_semantics: dict[str, object]
    output_semantics: dict[str, object]
    execution_mode: str
    input_payload_contract_key: str
    output_payload_contract_key: str
    minimum_inputs: int
    maximum_inputs: int
    ordering_policy: str
    input_policy: dict[str, object]
    compatibility_policy: dict[str, object]
    missing_policy: dict[str, object]
    tie_policy: dict[str, object]
    parameter_presets: tuple[_AggregationPreset, ...]
    targets: tuple[_AggregationPreset, ...]
    training_presets: tuple[_AggregationPreset, ...]


@dataclass(frozen=True, slots=True)
class _AggregationPreset:
    preset_key: str
    name: str
    description: str
    version_number: int
    semantics: dict[str, object]


@dataclass(frozen=True, slots=True)
class _Strategy:
    family_key: str
    variant_key: str
    name: str
    selection_semantics: dict[str, object]
    research_hypothesis: str
    parameters: dict[str, object]
    input_payload_contract_key: str
    schedule_policy: dict[str, object]
    execution_policy: dict[str, object]
    supported_frequencies: tuple[str, ...]
    parameter_presets: tuple[_StrategyParameterPreset, ...]


@dataclass(frozen=True, slots=True)
class _StrategyParameterPreset:
    preset_key: str
    name: str
    description: str
    version_number: int
    parameters: dict[str, object]


@dataclass(frozen=True, slots=True)
class _DefenseTimingPolicy:
    family_key: str
    variant_key: str
    name: str
    formula_identity: str
    research_hypothesis: str
    version_number: int
    research_status: str
    supported_frequencies: tuple[str, ...]
    input_policy: dict[str, object]
    rule: dict[str, object]


@dataclass(frozen=True, slots=True)
class _DefenseAllocationPolicy:
    family_key: str
    variant_key: str
    name: str
    formula_identity: str
    research_hypothesis: str
    version_number: int
    asset_registry_catalog_version: str
    asset_set_key: str
    research_status: str
    formal_eligible: bool
    missing_member_policy: str
    reserve_fallback_policy: str
    rebalance_policy: str
    reserve_return_model: dict[str, object] | None
    members: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class _Defense:
    family_key: str
    variant_key: str
    name: str
    allocation_semantics: dict[str, object]
    research_hypothesis: str
    parameters: dict[str, object]
    input_policy: dict[str, object]
    allocation_policy_document: dict[str, object]
    supported_asset_context_keys: tuple[str, ...]
    version_number: int
    research_status: str | None
    timing_policy: _DefenseTimingPolicy | None
    allocation_policy: _DefenseAllocationPolicy | None

    @property
    def composed(self) -> bool:
        return self.timing_policy is not None and self.allocation_policy is not None


class GraphWorkspacePreviewService:
    """M3 graph-aware derived view over one immutable source Catalog Release."""

    def __init__(
        self,
        loaded: LoadedCatalogRelease,
        *,
        model_migration_registry: dict[str, Any] | None = None,
    ) -> None:
        self._release = loaded.bundle.release
        self._source_manifest_hash = loaded.bundle.source_manifest_hash
        self._frozen_aggregation_recipes = _frozen_recipe_index(
            model_migration_registry
        )
        taxonomy = loaded.bundle.aggregation.feature_taxonomy
        self._aggregation_feature_taxonomy = (
            {
                item.feature_family_key: item
                for item in taxonomy.entries
            }
            if taxonomy is not None
            else {}
        )
        features: dict[str, _Feature] = {}
        for raw in loaded.bundle.raw_inputs.raw_inputs:
            features[raw.variant_key] = _Feature(
                family_key=raw.family_key,
                variant_key=raw.variant_key,
                name=raw.name,
                origin_stage=0,
                formula_identity=raw.formula_identity,
                semantic_role=raw.semantic_role,
                unit=raw.unit,
                payload_contract_key=raw.payload_contract_key,
                direction=raw.direction,
                aggregation_readiness=raw.aggregation_readiness,
                research_hypothesis=raw.research_hypothesis,
                parameters={},
                input_feature_keys=(),
                output_semantics={
                    "source_series_key": raw.source_series_key,
                    "source_field": raw.source_field,
                },
                producer_node_key=None,
                output_port_key=None,
            )
        nodes: dict[str, _Node] = {}
        for node in loaded.bundle.processing.nodes:
            outputs = tuple(item.variant_key for item in node.output_features)
            nodes[node.variant_key] = _Node(
                node.variant_key,
                _stage(node.stage_no),
                tuple(item.source_feature_variant_key for item in node.input_bindings),
                outputs,
            )
            for output in node.output_features:
                features[output.variant_key] = _Feature(
                    family_key=output.family_key,
                    variant_key=output.variant_key,
                    name=output.name,
                    origin_stage=_stage(node.stage_no),
                    formula_identity=output.formula_identity,
                    semantic_role=output.semantic_role,
                    unit=output.unit,
                    payload_contract_key=output.payload_contract_key,
                    direction=output.direction,
                    aggregation_readiness=output.aggregation_readiness,
                    research_hypothesis=output.research_hypothesis,
                    parameters=output.parameters,
                    input_feature_keys=tuple(
                        item.source_feature_variant_key for item in node.input_bindings
                    ),
                    output_semantics=output.output_semantics,
                    producer_node_key=node.variant_key,
                    output_port_key=output.output_port_key,
                )
        self._features = features
        self._nodes = nodes
        # Projection is a published Catalog capability, not an automatic property of
        # every upstream value. Raw inputs remain visible in Processing 1, while a
        # later stage is reachable only when a published node consumes the feature
        # there or the feature is explicitly aggregation-ready.
        projection_ceiling: dict[str, Stage] = {
            key: _stage(max(feature.origin_stage, 1 if feature.origin_stage == 0 else 0))
            for key, feature in features.items()
        }
        for published_node in nodes.values():
            source_stage = _stage(published_node.stage_no - 1)
            for source_key in published_node.inputs:
                projection_ceiling[source_key] = _stage(
                    max(projection_ceiling[source_key], source_stage)
                )
        for key, feature in features.items():
            if feature.aggregation_readiness == "aggregation_ready":
                projection_ceiling[key] = 3
        self._projection_ceiling = projection_ceiling
        self._aggregations = {
            item.family_key: _Aggregation(
                item.family_key,
                item.name,
                item.algorithm_identity,
                item.objective_semantics,
                item.output_semantics,
                item.execution_mode,
                item.input_payload_contract_key,
                item.output_payload_contract_key,
                item.minimum_inputs,
                item.maximum_inputs,
                item.ordering_policy,
                item.input_policy,
                item.compatibility_policy,
                item.missing_policy,
                item.tie_policy,
                tuple(
                    _AggregationPreset(
                        preset.preset_key,
                        preset.name,
                        preset.description,
                        preset.version_number,
                        preset.semantics,
                    )
                    for preset in item.parameter_presets
                ),
                tuple(
                    _AggregationPreset(
                        target.target_key,
                        target.name,
                        target.description,
                        target.version_number,
                        target.semantics,
                    )
                    for target in item.targets
                ),
                tuple(
                    _AggregationPreset(
                        preset.preset_key,
                        preset.name,
                        preset.description,
                        preset.version_number,
                        preset.semantics,
                    )
                    for preset in item.training_presets
                ),
            )
            for item in loaded.bundle.aggregation.families
        }
        strategy_presets: dict[str, list[_StrategyParameterPreset]] = defaultdict(list)
        for preset in loaded.bundle.strategy.parameter_presets:
            strategy_presets[preset.strategy_variant_key].append(
                _StrategyParameterPreset(
                    preset.preset_key,
                    preset.name,
                    preset.description,
                    preset.version_number,
                    preset.parameters,
                )
            )
        self._strategies = {
            item.variant_key: _Strategy(
                item.family_key,
                item.variant_key,
                item.name,
                item.selection_semantics,
                item.research_hypothesis,
                item.parameters,
                item.input_payload_contract_key,
                item.schedule_policy,
                item.execution_policy,
                _string_tuple(item.schedule_policy.get("frequencies")),
                tuple(strategy_presets.get(item.variant_key, ())),
            )
            for item in loaded.bundle.strategy.strategies
        }
        timing_policies = {
            (item.variant_key, item.version_number): _DefenseTimingPolicy(
                item.family_key,
                item.variant_key,
                item.name,
                item.formula_identity,
                item.research_hypothesis,
                item.version_number,
                item.research_status,
                tuple(item.supported_frequencies),
                item.input_policy.model_dump(mode="json"),
                item.rule.model_dump(mode="json"),
            )
            for item in loaded.bundle.defense.timing_policies
        }
        allocation_policies = {
            (item.variant_key, item.version_number): _DefenseAllocationPolicy(
                item.family_key,
                item.variant_key,
                item.name,
                item.formula_identity,
                item.research_hypothesis,
                item.version_number,
                item.asset_registry_catalog_version,
                item.asset_set_key,
                item.research_status,
                item.formal_eligible,
                item.missing_member_policy,
                item.reserve_fallback_policy,
                item.rebalance_policy,
                (
                    item.reserve_return_model_ref.model_dump(mode="json")
                    if item.reserve_return_model_ref is not None
                    else None
                ),
                tuple(member.model_dump(mode="json") for member in item.members),
            )
            for item in loaded.bundle.defense.allocation_policies
        }
        defenses: dict[str, _Defense] = {}
        for item in loaded.bundle.defense.defenses:
            timing = (
                timing_policies[
                    (item.timing_policy_ref.variant_key, item.timing_policy_ref.version_number)
                ]
                if item.timing_policy_ref is not None
                else None
            )
            allocation = (
                allocation_policies[
                    (
                        item.defensive_allocation_policy_ref.variant_key,
                        item.defensive_allocation_policy_ref.version_number,
                    )
                ]
                if item.defensive_allocation_policy_ref is not None
                else None
            )
            defenses[item.variant_key] = _Defense(
                item.family_key,
                item.variant_key,
                item.name,
                item.allocation_semantics,
                item.research_hypothesis,
                item.parameters,
                item.input_policy,
                item.allocation_policy,
                tuple(item.supported_asset_context_keys),
                item.version_number,
                item.research_status,
                timing,
                allocation,
            )
        # v0.22 freezes the research mainline without a defensive sleeve.  The
        # historical Catalog identities remain readable for immutable replay,
        # but they are deliberately not projected into an editable Workspace.
        # A future defense-specific release may opt back in with a new contract.
        self._defenses: dict[str, _Defense] = {}
        self._strategy_keys = set(self._strategies)
        self._defense_keys = {"none"}

    @classmethod
    def from_manifest(cls, manifest: Path) -> GraphWorkspacePreviewService:
        root = Path(__file__).parents[3]
        registry = load_model_migration_registry(
            root / "v0.22" / "m5" / "model-migration-registry.v0.22.0.json",
            oracle_manifest_path=(
                root / "v0.22" / "m0" / "v021-baseline-manifest.v0.22.0.json"
            ),
            aggregation_catalog_path=(
                root
                / "v0.22"
                / "catalogs"
                / "aggregation"
                / "deterministic.v0.22.0.json"
            ),
            signal_registry_path=(
                root / "v0.22" / "m4" / "migration-registry.v0.22.3.json"
            ),
        )
        return cls(load_catalog_release(manifest), model_migration_registry=registry)

    def catalog_identity(self) -> dict[str, str]:
        """Return the immutable source identity used by every derived preview."""
        return {
            "release_key": self._release.release_key,
            "catalog_version": self._release.catalog_version,
            "contract_version": self._release.contract_version,
            "source_manifest_hash": self._source_manifest_hash,
        }

    def initial_strategy_parameter_preset_keys(self, strategy_key: str) -> tuple[str, ...]:
        """Return the curated initial choice that will be persisted in a new Draft."""

        strategy = self._strategies[strategy_key]
        available = tuple(item.preset_key for item in strategy.parameter_presets)
        if "k2" in available:
            return ("k2",)
        return available[:1]

    def rebase_intent(self, intent: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[str]]]:
        """Project a frozen Draft Intent onto this Catalog without guessing replacements."""

        next_intent = dict(intent)
        retained_features: list[dict[str, Any]] = []
        removed_features: list[str] = []
        for item in intent.get("explicit_features", []):
            occurrence = (str(item["feature_key"]), int(item["stage_no"]))
            try:
                self._validate_occurrence(occurrence)
            except ValueError:
                removed_features.append(_occurrence_label(occurrence))
            else:
                retained_features.append({"feature_key": occurrence[0], "stage_no": occurrence[1]})
        aggregation_keys = [str(key) for key in intent.get("aggregation_family_keys", [])]
        retained_aggregations = [key for key in aggregation_keys if key in self._aggregations]
        strategy_keys = [str(key) for key in intent.get("strategy_keys", [])]
        defense_keys = [str(key) for key in intent.get("defense_keys", [])]
        retained_strategies = [key for key in strategy_keys if key in self._strategy_keys]
        retained_defenses = [key for key in defense_keys if key in self._defense_keys]
        preset_selections = {
            str(family_key): [
                str(preset)
                for preset in presets
                if str(preset)
                in {
                    item.preset_key
                    for item in self._aggregations[str(family_key)].parameter_presets
                }
            ]
            for family_key, presets in intent.get("aggregation_parameter_preset_keys", {}).items()
            if str(family_key) in retained_aggregations
        }
        target_selections = _retained_aggregation_axis(
            intent.get("aggregation_target_keys", {}),
            retained_aggregations,
            self._aggregations,
            "targets",
        )
        training_selections = _retained_aggregation_axis(
            intent.get("aggregation_training_preset_keys", {}),
            retained_aggregations,
            self._aggregations,
            "training_presets",
        )
        strategy_preset_selections = {
            str(strategy_key): [
                str(preset)
                for preset in presets
                if str(preset)
                in {
                    item.preset_key
                    for item in self._strategies[str(strategy_key)].parameter_presets
                }
            ]
            for strategy_key, presets in intent.get("strategy_parameter_preset_keys", {}).items()
            if str(strategy_key) in retained_strategies
        }
        next_intent.update(
            {
                "explicit_features": retained_features,
                "aggregation_family_keys": retained_aggregations,
                "strategy_keys": retained_strategies,
                "defense_keys": retained_defenses,
            }
        )
        if "aggregation_parameter_preset_keys" in next_intent:
            next_intent["aggregation_parameter_preset_keys"] = preset_selections
        if "aggregation_target_keys" in next_intent:
            next_intent["aggregation_target_keys"] = target_selections
        if "aggregation_training_preset_keys" in next_intent:
            next_intent["aggregation_training_preset_keys"] = training_selections
        if "strategy_parameter_preset_keys" in next_intent:
            next_intent["strategy_parameter_preset_keys"] = strategy_preset_selections
        return next_intent, {
            "removed_explicit_occurrences": sorted(removed_features),
            "removed_aggregation_families": sorted(
                set(aggregation_keys) - set(retained_aggregations)
            ),
            "removed_strategies": sorted(set(strategy_keys) - set(retained_strategies)),
            "removed_strategy_parameter_presets": sorted(
                f"{strategy_key}:{preset_key}"
                for strategy_key, preset_keys in intent.get(
                    "strategy_parameter_preset_keys", {}
                ).items()
                for preset_key in preset_keys
                if str(preset_key) not in set(strategy_preset_selections.get(str(strategy_key), ()))
            ),
            "removed_defenses": sorted(set(defense_keys) - set(retained_defenses)),
        }

    def preview(
        self,
        intent: WorkspacePreviewIntent,
        *,
        asset_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        explicit: set[OccurrenceKey] = {
            (item.feature_key, item.stage_no) for item in intent.explicit_features
        }
        if len(explicit) != len(intent.explicit_features):
            raise ValueError("Duplicate explicit Feature occurrence")
        for key in explicit:
            self._validate_occurrence(key)
        for family_key in intent.aggregation_family_keys:
            if family_key not in self._aggregations:
                raise ValueError(f"Unknown Aggregation Family: {family_key}")
        if len(set(intent.aggregation_family_keys)) != len(intent.aggregation_family_keys):
            raise ValueError("Duplicate Aggregation Family")
        if len(set(intent.strategy_keys)) != len(intent.strategy_keys):
            raise ValueError("Duplicate Strategy")
        if len(set(intent.defense_keys)) != len(intent.defense_keys):
            raise ValueError("Duplicate Defense")
        unknown_strategies = set(intent.strategy_keys) - self._strategy_keys
        unknown_defenses = set(intent.defense_keys) - self._defense_keys
        if unknown_strategies:
            raise ValueError(f"Unknown Strategy: {sorted(unknown_strategies)[0]}")
        if unknown_defenses:
            raise ValueError(f"Unknown Defense: {sorted(unknown_defenses)[0]}")
        preset_selections = dict(intent.aggregation_parameter_preset_keys)
        target_selections = dict(intent.aggregation_target_keys)
        training_selections = dict(intent.aggregation_training_preset_keys)
        if set(preset_selections) - set(intent.aggregation_family_keys):
            raise ValueError("Aggregation preset selected for an unselected Family")
        if set(target_selections) - set(intent.aggregation_family_keys):
            raise ValueError("Aggregation Target selected for an unselected Family")
        if set(training_selections) - set(intent.aggregation_family_keys):
            raise ValueError("Aggregation Training Preset selected for an unselected Family")
        strategy_preset_selections = dict(intent.strategy_parameter_preset_keys)
        if len(strategy_preset_selections) != len(intent.strategy_parameter_preset_keys):
            raise ValueError("Duplicate Strategy parameter preset owner")
        if set(strategy_preset_selections) - set(intent.strategy_keys):
            raise ValueError("Strategy preset selected for an unselected Strategy")

        present: set[OccurrenceKey] = set()
        required_by: dict[OccurrenceKey, set[str]] = defaultdict(set)
        reason_codes: dict[OccurrenceKey, set[str]] = defaultdict(set)
        for occurrence in sorted(explicit, key=lambda item: (item[1], item[0])):
            self._expand(
                occurrence,
                root=_occurrence_label(occurrence),
                present=present,
                required_by=required_by,
                reason_codes=reason_codes,
            )

        aggregation_inputs = tuple(sorted(key[0] for key in explicit if key[1] == 3))
        blockers = self._aggregation_blockers(aggregation_inputs, intent.aggregation_family_keys)
        aggregation_instances = 0
        trainable_member_counts: dict[str, int] = {}
        for family_key in intent.aggregation_family_keys:
            available = tuple(
                item.preset_key for item in self._aggregations[family_key].parameter_presets
            )
            selected = preset_selections.get(family_key, ())
            if len(set(selected)) != len(selected) or not set(selected) <= set(available):
                raise ValueError(f"Invalid Aggregation preset selection: {family_key}")
            if not selected and available:
                blockers.append(
                    {
                        "layer": "aggregation",
                        "object_key": family_key,
                        "reason_codes": ["aggregation_parameter_preset_required"],
                    }
                )
            for preset_key in selected:
                preset_reasons = self._aggregation_preset_reason_codes(
                    family_key,
                    preset_key,
                    aggregation_inputs,
                )
                if preset_reasons:
                    blockers.append(
                        {
                            "layer": "aggregation",
                            "object_key": f"{family_key}:{preset_key}",
                            "reason_codes": preset_reasons,
                            "feature_keys": list(aggregation_inputs),
                        }
                    )
            aggregation = self._aggregations[family_key]
            selected_targets = target_selections.get(family_key, ())
            selected_training = training_selections.get(family_key, ())
            available_targets = {item.preset_key for item in aggregation.targets}
            available_training = {item.preset_key for item in aggregation.training_presets}
            if (
                len(set(selected_targets)) != len(selected_targets)
                or not set(selected_targets) <= available_targets
            ):
                raise ValueError(f"Invalid Aggregation Target selection: {family_key}")
            if (
                len(set(selected_training)) != len(selected_training)
                or not set(selected_training) <= available_training
            ):
                raise ValueError(f"Invalid Aggregation Training selection: {family_key}")
            if aggregation.execution_mode == "supervised":
                if not selected_targets:
                    blockers.append(
                        {
                            "layer": "aggregation",
                            "object_key": family_key,
                            "reason_codes": ["aggregation_target_required"],
                        }
                    )
                if not selected_training:
                    blockers.append(
                        {
                            "layer": "aggregation",
                            "object_key": family_key,
                            "reason_codes": ["aggregation_training_preset_required"],
                        }
                    )
                # Target x Training Preset coordinates are internal OOF members
                # of one Model Family branch, not separate Aggregation branches.
                trainable_member_counts[family_key] = (
                    len(selected_targets) * len(selected_training)
                )
                aggregation_instances += len(selected) if selected else 1
            else:
                if selected_targets or selected_training:
                    raise ValueError(
                        f"Deterministic Aggregation cannot select supervised axes: {family_key}"
                    )
                aggregation_instances += len(selected) if selected else 1
        if not intent.strategy_keys:
            blockers.append(
                {
                    "layer": "strategy",
                    "object_key": "strategy_variant",
                    "reason_codes": ["strategy_required"],
                }
            )
        if not intent.defense_keys:
            blockers.append(
                {
                    "layer": "defense",
                    "object_key": "defense_variant",
                    "reason_codes": ["defense_selection_required"],
                }
            )
        strategy_configuration_count = 0
        for strategy_key in intent.strategy_keys:
            strategy = self._strategies[strategy_key]
            selected_strategy_presets = strategy_preset_selections.get(strategy_key, ())
            available_strategy_presets = {item.preset_key for item in strategy.parameter_presets}
            if (
                len(set(selected_strategy_presets)) != len(selected_strategy_presets)
                or not set(selected_strategy_presets) <= available_strategy_presets
            ):
                raise ValueError(f"Invalid Strategy parameter preset selection: {strategy_key}")
            if strategy.parameter_presets and not selected_strategy_presets:
                blockers.append(
                    {
                        "layer": "strategy",
                        "object_key": strategy_key,
                        "reason_codes": ["strategy_parameter_preset_required"],
                    }
                )
            if intent.frequency not in strategy.supported_frequencies:
                blockers.append(
                    {
                        "layer": "strategy",
                        "object_key": strategy_key,
                        "reason_codes": ["frequency_unsupported"],
                    }
                )
            for preset in strategy.parameter_presets:
                if preset.preset_key not in selected_strategy_presets:
                    continue
                preset_reasons = self._strategy_preset_reason_codes(
                    strategy,
                    preset,
                    intent.frequency,
                    asset_context,
                )
                if preset_reasons:
                    blockers.append(
                        {
                            "layer": "strategy",
                            "object_key": f"{strategy_key}:{preset.preset_key}",
                            "reason_codes": preset_reasons,
                        }
                    )
            strategy_configuration_count += (
                len(selected_strategy_presets) if selected_strategy_presets else 1
            )
        for defense_key in intent.defense_keys:
            if defense_key == "none":
                continue
            defense_reasons = self._defense_reason_codes(
                self._defenses[defense_key],
                intent.frequency,
                asset_context,
            )
            if defense_reasons:
                blockers.append(
                    {
                        "layer": "defense",
                        "object_key": defense_key,
                        "reason_codes": defense_reasons,
                    }
                )
        strategy_branches = (
            aggregation_instances * strategy_configuration_count * len(intent.defense_keys)
        )
        projection_count = sum(stage > self._features[key].origin_stage for key, stage in present)
        active_nodes = {
            self._features[key].producer_node_key
            for key, stage in present
            if stage == self._features[key].origin_stage
            and self._features[key].producer_node_key is not None
        }
        graph_edges = projection_count + sum(
            len(self._nodes[node_key].inputs) for node_key in active_nodes if node_key is not None
        )
        resources = structural_admission(
            StructuralEstimate(
                explicit_stage3_inputs=len(aggregation_inputs),
                feature_occurrences=len(present),
                ancestor_occurrences=len(present - explicit),
                graph_edges=graph_edges,
                aggregation_candidates=len(intent.aggregation_family_keys),
                aggregation_instances=aggregation_instances,
                strategy_candidates=len(intent.strategy_keys),
                defense_candidates=len(intent.defense_keys),
                strategy_branches=strategy_branches,
                backtest_cells=aggregation_instances + strategy_branches * 6,
                work_items=aggregation_instances + strategy_branches * 6,
            )
        )
        resource_estimates = resources["estimates"]
        if not isinstance(resource_estimates, dict):
            raise TypeError("Structural admission estimates must be a mapping")
        if resources["state"] == "rejected":
            blockers.append(
                {
                    "layer": "admission",
                    "object_key": resources["policy_id"],
                    "reason_codes": resources["reason_codes"],
                }
            )
        stages = [
            self._stage_view(
                _stage(stage),
                explicit,
                present,
                required_by,
                reason_codes,
                intent.aggregation_family_keys,
            )
            for stage in range(4)
        ]
        selection_document = {
            "explicit_features": [
                {"feature_key": key, "stage_no": stage}
                for key, stage in sorted(explicit, key=lambda item: (item[1], item[0]))
            ],
            "aggregation_family_keys": list(intent.aggregation_family_keys),
            "aggregation_parameter_preset_keys": {
                key: list(values) for key, values in intent.aggregation_parameter_preset_keys
            },
            "aggregation_target_keys": {
                key: list(values) for key, values in intent.aggregation_target_keys
            },
            "aggregation_training_preset_keys": {
                key: list(values)
                for key, values in intent.aggregation_training_preset_keys
            },
            "strategy_keys": list(intent.strategy_keys),
            "strategy_parameter_preset_keys": {
                key: list(values) for key, values in intent.strategy_parameter_preset_keys
            },
            "defense_keys": list(intent.defense_keys),
            "frequency": intent.frequency,
        }
        derived_document = {
            "selection": selection_document,
            "present": sorted(_occurrence_label(key) for key in present),
            "required_by": {
                _occurrence_label(key): sorted(values)
                for key, values in sorted(required_by.items())
            },
            "blockers": blockers,
        }
        return {
            "catalog_release": {
                "release_key": self._release.release_key,
                "catalog_version": self._release.catalog_version,
                "contract_version": self._release.contract_version,
                "source_manifest_hash": self._source_manifest_hash,
            },
            "selection_fingerprint": sha256_hexdigest(selection_document),
            "derived_state_fingerprint": sha256_hexdigest(derived_document),
            "frequency": intent.frequency,
            "summary": {
                "explicit_count": len(explicit),
                "required_count": len(present - explicit),
                "stage3_input_count": len(aggregation_inputs),
                "aggregation_instance_count": aggregation_instances,
                "strategy_branch_count": strategy_branches,
                "backtest_cell_count": resource_estimates["backtest_cells"],
            },
            "aggregation_inputs": list(aggregation_inputs),
            "aggregations": [
                {
                    "family_key": item.family_key,
                    "name": item.name,
                    "algorithm_identity": item.algorithm_identity,
                    "objective_semantics": item.objective_semantics,
                    "output_semantics": item.output_semantics,
                    "execution_mode": item.execution_mode,
                    "input_payload_contract_key": item.input_payload_contract_key,
                    "output_payload_contract_key": item.output_payload_contract_key,
                    "ordering_policy": item.ordering_policy,
                    "input_policy": item.input_policy,
                    "compatibility_policy": item.compatibility_policy,
                    "missing_policy": item.missing_policy,
                    "tie_policy": item.tie_policy,
                    "selected": item.family_key in intent.aggregation_family_keys,
                    "minimum_inputs": item.minimum_inputs,
                    "maximum_inputs": item.maximum_inputs,
                    "parameter_presets": [
                        preset.preset_key for preset in item.parameter_presets
                    ],
                    "parameter_preset_definitions": [
                        {
                            "preset_key": preset.preset_key,
                            "name": preset.name,
                            "description": preset.description,
                            "version_number": preset.version_number,
                            "semantics": preset.semantics,
                            "selected": preset.preset_key
                            in preset_selections.get(item.family_key, ()),
                            "selectable": not self._aggregation_preset_reason_codes(
                                item.family_key,
                                preset.preset_key,
                                aggregation_inputs,
                            ),
                            "reason_codes": self._aggregation_preset_reason_codes(
                                item.family_key,
                                preset.preset_key,
                                aggregation_inputs,
                            ),
                        }
                        for preset in item.parameter_presets
                    ],
                    "selected_parameter_presets": list(preset_selections.get(item.family_key, ())),
                    "targets": [
                        {
                            "key": target.preset_key,
                            "name": target.name,
                            "description": target.description,
                            "version_number": target.version_number,
                            "semantics": target.semantics,
                            "selected": target.preset_key
                            in target_selections.get(item.family_key, ()),
                        }
                        for target in item.targets
                    ],
                    "selected_targets": list(target_selections.get(item.family_key, ())),
                    "training_presets": [
                        {
                            "key": preset.preset_key,
                            "name": preset.name,
                            "description": preset.description,
                            "version_number": preset.version_number,
                            "semantics": preset.semantics,
                            "selected": preset.preset_key
                            in training_selections.get(item.family_key, ()),
                        }
                        for preset in item.training_presets
                    ],
                    "selected_training_presets": list(
                        training_selections.get(item.family_key, ())
                    ),
                    "internal_member_count": trainable_member_counts.get(
                        item.family_key, 0
                    ),
                    "accepted_input_count": sum(
                        self._features[key].payload_contract_key == item.input_payload_contract_key
                        for key in aggregation_inputs
                    ),
                }
                for item in self._aggregations.values()
            ],
            "strategies": [
                {
                    "family_key": item.family_key,
                    "variant_key": item.variant_key,
                    "name": item.name,
                    "selection_semantics": item.selection_semantics,
                    "research_hypothesis": item.research_hypothesis,
                    "parameters": item.parameters,
                    "input_payload_contract_key": item.input_payload_contract_key,
                    "schedule_policy": item.schedule_policy,
                    "execution_policy": item.execution_policy,
                    "parameter_presets": [
                        {
                            "preset_key": preset.preset_key,
                            "name": preset.name,
                            "description": preset.description,
                            "version_number": preset.version_number,
                            "parameters": preset.parameters,
                            "selected": preset.preset_key
                            in strategy_preset_selections.get(item.variant_key, ()),
                            "selectable": not self._strategy_preset_reason_codes(
                                item,
                                preset,
                                intent.frequency,
                                asset_context,
                            ),
                            "reason_codes": self._strategy_preset_reason_codes(
                                item,
                                preset,
                                intent.frequency,
                                asset_context,
                            ),
                        }
                        for preset in sorted(
                            item.parameter_presets,
                            key=lambda value: (
                                value.preset_key
                                not in strategy_preset_selections.get(item.variant_key, ()),
                                (
                                    value.parameters["target_k"]
                                    if isinstance(value.parameters.get("target_k"), int)
                                    else 0
                                ),
                                value.preset_key,
                            ),
                        )
                    ],
                    "supported_frequencies": list(item.supported_frequencies),
                    "selected": item.variant_key in intent.strategy_keys,
                    "compatible": intent.frequency in item.supported_frequencies,
                    "reason_codes": (
                        []
                        if intent.frequency in item.supported_frequencies
                        else ["frequency_unsupported"]
                    ),
                }
                for item in sorted(
                    self._strategies.values(),
                    key=lambda value: (
                        value.variant_key not in intent.strategy_keys,
                        value.family_key,
                        value.variant_key,
                    ),
                )
            ],
            "defenses": self._defense_options(intent, asset_context),
            "stages": stages,
            "blockers": blockers,
            "warnings": [],
            "resources": resources,
        }

    @staticmethod
    def _strategy_preset_reason_codes(
        strategy: _Strategy,
        preset: _StrategyParameterPreset,
        frequency: str,
        asset_context: dict[str, Any] | None,
    ) -> list[str]:
        reasons: list[str] = []
        if frequency not in strategy.supported_frequencies:
            reasons.append("frequency_unsupported")
        target_k = preset.parameters.get("target_k")
        required_types = strategy_required_instrument_types(
            strategy.parameters.get("required_instrument_type")
        )
        if asset_context is None:
            if required_types or isinstance(target_k, int):
                reasons.append("asset_context_required")
            return reasons
        members = asset_context.get("members", [])
        if isinstance(target_k, int):
            reasons.extend(
                strategy_asset_context_reason_codes(
                    required_types,
                    target_k,
                    (str(item.get("instrument_type", "")) for item in members),
                )
            )
        return sorted(set(reasons))

    @staticmethod
    def _defense_reason_codes(
        defense: _Defense,
        frequency: str,
        asset_context: dict[str, Any] | None,
    ) -> list[str]:
        if not defense.composed:
            return []
        assert defense.timing_policy is not None
        assert defense.allocation_policy is not None
        reasons: list[str] = []
        if frequency not in defense.timing_policy.supported_frequencies:
            reasons.append("frequency_unsupported")
        if asset_context is None or not asset_context.get("asset_context_key"):
            reasons.append("asset_context_required")
            return reasons
        explicit_selection = (
            asset_context.get("selection_kind") == "explicit_security_selection"
            and asset_context.get("selection_group") in {"stock", "fund"}
        )
        if (
            not explicit_selection
            and asset_context["asset_context_key"] not in defense.supported_asset_context_keys
        ):
            reasons.append("asset_context_unsupported")
        registry_version = asset_context.get("asset_registry_catalog_version")
        if registry_version != defense.allocation_policy.asset_registry_catalog_version:
            reasons.append("asset_registry_version_mismatch")
        return sorted(set(reasons))

    @staticmethod
    def _defense_timing_document(defense: _Defense) -> dict[str, Any] | None:
        policy = defense.timing_policy
        if policy is None:
            return None
        return {
            "family_key": policy.family_key,
            "variant_key": policy.variant_key,
            "name": policy.name,
            "formula_identity": policy.formula_identity,
            "research_hypothesis": policy.research_hypothesis,
            "version_number": policy.version_number,
            "research_status": policy.research_status,
            "supported_frequencies": list(policy.supported_frequencies),
            "input_policy": policy.input_policy,
            "rule": policy.rule,
        }

    @staticmethod
    def _defense_allocation_document(defense: _Defense) -> dict[str, Any] | None:
        policy = defense.allocation_policy
        if policy is None:
            return None
        return {
            "family_key": policy.family_key,
            "variant_key": policy.variant_key,
            "name": policy.name,
            "formula_identity": policy.formula_identity,
            "research_hypothesis": policy.research_hypothesis,
            "version_number": policy.version_number,
            "asset_registry_catalog_version": policy.asset_registry_catalog_version,
            "asset_set_key": policy.asset_set_key,
            "research_status": policy.research_status,
            "formal_eligible": policy.formal_eligible,
            "missing_member_policy": policy.missing_member_policy,
            "reserve_fallback_policy": policy.reserve_fallback_policy,
            "rebalance_policy": policy.rebalance_policy,
            "reserve_return_model": policy.reserve_return_model,
            "members": list(policy.members),
        }

    def _defense_options(
        self,
        intent: WorkspacePreviewIntent,
        asset_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = [
            {
                "family_key": "none",
                "variant_key": "none",
                "name": "No defense",
                "allocation_semantics": {"risk_budget": "1", "defense_budget": "0"},
                "research_hypothesis": "No defensive sleeve is applied.",
                "parameters": {},
                "input_policy": {},
                "allocation_policy_document": {},
                "supported_asset_context_keys": [],
                "version_number": None,
                "composed": False,
                "research_status": None,
                "timing_policy": None,
                "allocation_policy": None,
                "selected": "none" in intent.defense_keys,
                "compatible": True,
                "reason_codes": [],
            }
        ]
        for item in self._defenses.values():
            reasons = self._defense_reason_codes(item, intent.frequency, asset_context)
            options.append(
                {
                    "family_key": item.family_key,
                    "variant_key": item.variant_key,
                    "name": item.name,
                    "allocation_semantics": item.allocation_semantics,
                    "research_hypothesis": item.research_hypothesis,
                    "parameters": item.parameters,
                    "input_policy": item.input_policy,
                    "allocation_policy_document": item.allocation_policy_document,
                    "supported_asset_context_keys": list(item.supported_asset_context_keys),
                    "version_number": item.version_number,
                    "composed": item.composed,
                    "research_status": item.research_status,
                    "timing_policy": self._defense_timing_document(item),
                    "allocation_policy": self._defense_allocation_document(item),
                    "selected": item.variant_key in intent.defense_keys,
                    "compatible": not reasons,
                    "reason_codes": reasons,
                }
            )
        return sorted(
            options,
            key=lambda value: (
                not bool(value["selected"]),
                str(value["family_key"]),
                str(value["variant_key"]),
            ),
        )

    def _stage_view(
        self,
        stage: Stage,
        explicit: set[OccurrenceKey],
        present: set[OccurrenceKey],
        required_by: dict[OccurrenceKey, set[str]],
        occurrence_reasons: dict[OccurrenceKey, set[str]],
        aggregation_family_keys: tuple[str, ...],
    ) -> dict[str, Any]:
        variants: list[dict[str, Any]] = []
        for feature in self._features.values():
            if (
                feature.origin_stage > stage
                or stage > self._projection_ceiling[feature.variant_key]
            ):
                continue
            key = (feature.variant_key, stage)
            candidate_present = set(present)
            candidate_required: dict[OccurrenceKey, set[str]] = defaultdict(set)
            self._expand(
                key,
                root=_occurrence_label(key),
                present=candidate_present,
                required_by=candidate_required,
                reason_codes=defaultdict(set),
            )
            new_occurrences = candidate_present - present - {key}
            incompatible = self._incompatible_aggregations(feature, stage, aggregation_family_keys)
            availability = (
                "hard_incompatible"
                if incompatible
                else "requires_ancestors"
                if new_occurrences
                else "ready"
            )
            dependencies = sorted(required_by.get(key, set()))
            is_explicit = key in explicit
            is_present = key in present
            variants.append(
                {
                    "family_key": feature.family_key,
                    "feature_key": feature.variant_key,
                    "name": feature.name,
                    "stage_no": stage,
                    "origin_stage": feature.origin_stage,
                    "formula_identity": feature.formula_identity,
                    "semantic_role": feature.semantic_role,
                    "unit": feature.unit,
                    "parameters": feature.parameters,
                    "input_feature_keys": list(feature.input_feature_keys),
                    "output_semantics": feature.output_semantics,
                    "payload_contract_key": feature.payload_contract_key,
                    "direction": feature.direction,
                    "aggregation_readiness": feature.aggregation_readiness,
                    "research_hypothesis": feature.research_hypothesis,
                    "is_explicit": is_explicit,
                    "is_required": bool(dependencies),
                    "is_present": is_present,
                    "required_by": dependencies,
                    "availability": availability,
                    "lock_state": "locked" if dependencies else "unlocked",
                    "locked_by": dependencies,
                    "pinned": is_present,
                    "producer": self._producer(feature, stage),
                    "select_effect": {
                        "ancestor_count": len(new_occurrences),
                        "projection_count": sum(
                            occurrence[1] > self._features[occurrence[0]].origin_stage
                            for occurrence in new_occurrences
                        ),
                    },
                    "reason_codes": sorted(
                        occurrence_reasons.get(key, set())
                        | {f"aggregation_rejected:{item}" for item in incompatible}
                    ),
                }
            )
        variants.sort(key=_variant_sort_key)
        families: list[dict[str, Any]] = []
        for family_key in sorted({item["family_key"] for item in variants}):
            family_variants = [item for item in variants if item["family_key"] == family_key]
            families.append(
                {
                    "family_key": family_key,
                    "name": family_variants[0]["name"],
                    "pinned": any(item["pinned"] for item in family_variants),
                    "explicit_count": sum(item["is_explicit"] for item in family_variants),
                    "required_count": sum(
                        item["is_required"] and not item["is_explicit"] for item in family_variants
                    ),
                    "available_count": sum(
                        item["availability"] != "hard_incompatible" for item in family_variants
                    ),
                    "variants": family_variants,
                }
            )
        families.sort(key=lambda item: (not item["pinned"], item["family_key"]))
        return {
            "stage_no": stage,
            "explicit_count": sum(key[1] == stage for key in explicit),
            "required_count": sum(key[1] == stage for key in present - explicit),
            "families": families,
        }

    def _expand(
        self,
        occurrence: OccurrenceKey,
        *,
        root: str,
        present: set[OccurrenceKey],
        required_by: dict[OccurrenceKey, set[str]],
        reason_codes: dict[OccurrenceKey, set[str]],
    ) -> None:
        feature = self._features[occurrence[0]]
        stage = occurrence[1]
        if stage > self._projection_ceiling[feature.variant_key]:
            raise ValueError(
                f"Feature {feature.variant_key} is not published for projection to stage {stage}"
            )
        present.add(occurrence)
        if stage > feature.origin_stage:
            source = (feature.variant_key, stage - 1)
            required_by[source].add(root)
            self._expand(
                source,
                root=root,
                present=present,
                required_by=required_by,
                reason_codes=reason_codes,
            )
            return
        if feature.producer_node_key is None:
            return
        node = self._nodes[feature.producer_node_key]
        for output_key in node.outputs:
            output_occurrence = (output_key, stage)
            present.add(output_occurrence)
            if output_occurrence != occurrence:
                required_by[output_occurrence].add(root)
                reason_codes[output_occurrence].add("co_produced_output")
        for source_key in node.inputs:
            source = (source_key, stage - 1)
            required_by[source].add(root)
            self._expand(
                source,
                root=root,
                present=present,
                required_by=required_by,
                reason_codes=reason_codes,
            )

    def _producer(self, feature: _Feature, stage: Stage) -> dict[str, object]:
        if stage > feature.origin_stage:
            return {
                "kind": "layer_projection",
                "source_feature_key": feature.variant_key,
                "source_stage_no": stage - 1,
            }
        if feature.producer_node_key is None:
            return {"kind": "raw_input"}
        return {
            "kind": "node_output",
            "node_variant_key": feature.producer_node_key,
            "output_port_key": feature.output_port_key,
        }

    def _aggregation_blockers(
        self,
        aggregation_inputs: tuple[str, ...],
        aggregation_family_keys: tuple[str, ...],
    ) -> list[dict[str, object]]:
        blockers: list[dict[str, object]] = []
        if not aggregation_inputs:
            blockers.append(
                {
                    "layer": "stage3",
                    "object_key": "aggregation_inputs",
                    "reason_codes": ["stage3_input_required"],
                }
            )
        if not aggregation_family_keys:
            blockers.append(
                {
                    "layer": "aggregation",
                    "object_key": "aggregation_family",
                    "reason_codes": ["aggregation_required"],
                }
            )
        for family_key in aggregation_family_keys:
            aggregation = self._aggregations[family_key]
            if not (
                aggregation.minimum_inputs <= len(aggregation_inputs) <= aggregation.maximum_inputs
            ):
                blockers.append(
                    {
                        "layer": "aggregation",
                        "object_key": family_key,
                        "reason_codes": ["input_count_rejected"],
                    }
                )
            rejected = [
                key
                for key in aggregation_inputs
                if self._features[key].payload_contract_key
                != aggregation.input_payload_contract_key
            ]
            if rejected:
                blockers.append(
                    {
                        "layer": "aggregation",
                        "object_key": family_key,
                        "reason_codes": ["payload_contract_incompatible"],
                        "feature_keys": rejected,
                    }
                )
        return blockers

    def _aggregation_preset_reason_codes(
        self,
        family_key: str,
        preset_key: str,
        aggregation_inputs: tuple[str, ...],
    ) -> list[str]:
        if (
            family_key == "hierarchical_weighted_mean"
            and preset_key == "active_dimension_equal_component_equal_v1"
        ):
            reasons: list[str] = []
            if len(aggregation_inputs) < 2:
                reasons.append("aggregation_native_requires_multiple_inputs")
            for feature_key in aggregation_inputs:
                feature = self._features[feature_key]
                entry = self._aggregation_feature_taxonomy.get(
                    feature.family_key
                )
                if entry is None:
                    reasons.append("aggregation_taxonomy_entry_missing")
                elif not entry.native_hierarchical_eligible:
                    reasons.append("aggregation_native_calibration_required")
                elif feature.unit not in entry.accepted_units:
                    reasons.append("aggregation_native_scale_incompatible")
                elif feature.direction not in entry.accepted_directions:
                    reasons.append("aggregation_native_direction_incompatible")
            return list(dict.fromkeys(reasons))
        if family_key not in _FROZEN_RECIPE_FAMILIES:
            return []
        recipes = self._frozen_aggregation_recipes.get((family_key, preset_key), ())
        selected = frozenset(aggregation_inputs)
        match_count = sum(
            recipe == selected and len(recipe) == len(aggregation_inputs)
            for recipe in recipes
        )
        if match_count == 1:
            return []
        if match_count > 1:
            return ["aggregation_recipe_ambiguous"]
        return ["aggregation_recipe_unavailable"]

    def _incompatible_aggregations(
        self,
        feature: _Feature,
        stage: Stage,
        aggregation_family_keys: tuple[str, ...],
    ) -> tuple[str, ...]:
        if stage != 3:
            return ()
        return tuple(
            family_key
            for family_key in aggregation_family_keys
            if feature.payload_contract_key
            != self._aggregations[family_key].input_payload_contract_key
        )

    def _validate_occurrence(self, occurrence: OccurrenceKey) -> None:
        feature = self._features.get(occurrence[0])
        if feature is None:
            raise ValueError(f"Unknown Feature Variant: {occurrence[0]}")
        if (
            occurrence[1] < feature.origin_stage
            or occurrence[1] > self._projection_ceiling[feature.variant_key]
            or occurrence[1] not in {0, 1, 2, 3}
        ):
            raise ValueError(f"Feature {occurrence[0]} does not exist at stage {occurrence[1]}")


def _variant_sort_key(item: dict[str, Any]) -> tuple[object, ...]:
    return (
        not item["pinned"],
        not (item["is_explicit"] and item["is_required"]),
        not item["is_explicit"],
        not item["is_required"],
        {"ready": 0, "requires_ancestors": 1, "hard_incompatible": 2}[item["availability"]],
        item["feature_key"],
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("Catalog frequency list is required")
    return tuple(str(item) for item in value)


def _frozen_recipe_index(
    registry: dict[str, Any] | None,
) -> dict[tuple[str, str], tuple[frozenset[str], ...]]:
    grouped: dict[tuple[str, str], list[frozenset[str]]] = defaultdict(list)
    if registry is None:
        return {}
    for record in registry.get("records", []):
        mapping = record.get("mapping", {})
        family_key = str(mapping.get("family_key", ""))
        preset_key = mapping.get("parameter_preset_key")
        inputs = mapping.get("input_signal_variant_keys")
        if (
            family_key not in _FROZEN_RECIPE_FAMILIES
            or not isinstance(preset_key, str)
            or not isinstance(inputs, list)
        ):
            continue
        grouped[(family_key, preset_key)].append(
            frozenset(str(item) for item in inputs)
        )
    return {key: tuple(values) for key, values in grouped.items()}


def _occurrence_label(key: OccurrenceKey) -> str:
    return f"{key[0]}@{key[1]}"


def _stage(value: int) -> Stage:
    if value not in {0, 1, 2, 3}:
        raise ValueError(f"Invalid stage: {value}")
    return value  # type: ignore[return-value]


def _retained_aggregation_axis(
    selections: object,
    retained_families: list[str],
    aggregations: dict[str, _Aggregation],
    axis: Literal["targets", "training_presets"],
) -> dict[str, list[str]]:
    if not isinstance(selections, dict):
        return {}
    retained: dict[str, list[str]] = {}
    for raw_family_key, raw_values in selections.items():
        family_key = str(raw_family_key)
        if family_key not in retained_families or not isinstance(raw_values, list):
            continue
        available = {
            item.preset_key for item in getattr(aggregations[family_key], axis)
        }
        retained[family_key] = [
            str(value) for value in raw_values if str(value) in available
        ]
    return retained


def representative_workspace_service() -> GraphWorkspacePreviewService:
    manifest = (
        Path(__file__).parents[3]
        / "v0.22"
        / "catalogs"
        / "releases"
        / "catalog_release.v0.22.13.json"
    )
    return GraphWorkspacePreviewService.from_manifest(manifest)
