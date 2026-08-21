from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from style_rotation.core.canonical import sha256_hexdigest

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,159}$")]
Fingerprint = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SemVer = Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
CanonicalDecimal = Annotated[
    str,
    StringConstraints(pattern=r"^-?(?:0|[1-9]\d*)\.\d{18}$"),
]
PayloadKind = Literal[
    "numeric_scalar",
    "tabular",
    "vector_series",
    "text_series",
    "event_series",
    "structured_pattern",
    "document_reference",
    "tensor_series",
    "model_state",
    "mapping_table",
    "opaque_bundle",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PayloadContractSeed(StrictModel):
    contract_key: Key
    name: str = Field(min_length=1, max_length=240)
    semantic_role: Key
    description: str = Field(min_length=1)
    version_number: int = Field(ge=1)
    payload_kind: PayloadKind
    schema_document: dict[str, object]
    entity_axis: dict[str, object]
    time_axis: dict[str, object]
    observation_grain: dict[str, object]
    primary_key_fields: list[Key] = Field(min_length=1)
    ordering_contract: dict[str, object]
    missingness_contract: dict[str, object]
    pit_contract: dict[str, object]
    quality_contract: dict[str, object]
    aggregation_role: Key
    export_policy: dict[str, object]
    compatibility_class: Literal[
        "initial",
        "backward_compatible",
        "forward_compatible",
        "breaking",
        "semantic_change",
    ]

    @model_validator(mode="after")
    def validate_axes(self) -> PayloadContractSeed:
        _unique("payload primary-key field", self.primary_key_fields)
        entity_kind = self.entity_axis.get("kind")
        if entity_kind not in {
            "none",
            "asset",
            "security",
            "issuer",
            "document",
            "event",
            "portfolio",
            "custom",
        }:
            raise ValueError(f"Invalid entity_axis.kind for {self.contract_key}")
        if "known_at_field" not in self.time_axis:
            raise ValueError(f"Payload {self.contract_key} must freeze known_at_field")
        return self


class PhysicalEncodingSeed(StrictModel):
    encoding_key: Key
    version_number: int = Field(ge=1)
    media_type: str = Field(min_length=1, max_length=160)
    file_extension: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,19}$")]
    compression: Key
    writer_version: str = Field(min_length=1, max_length=120)
    reader_min_version: str = Field(min_length=1, max_length=120)
    reader_max_version: str | None = Field(default=None, max_length=120)
    canonicalization_policy: dict[str, object]
    partition_policy: dict[str, object]
    encryption_policy: dict[str, object]
    verification_implementation: str = Field(min_length=1, max_length=240)


class PayloadCatalog(StrictModel):
    catalog_type: Literal["v022_payload"]
    catalog_version: SemVer
    extends: list[str] = Field(default_factory=list)
    contracts: list[PayloadContractSeed] = Field(min_length=1)
    encodings: list[PhysicalEncodingSeed]

    @model_validator(mode="after")
    def validate_catalog(self) -> PayloadCatalog:
        _unique("payload contract key", [item.contract_key for item in self.contracts])
        _unique("physical encoding key", [item.encoding_key for item in self.encodings])
        for value in self.extends:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or path.suffix != ".json":
                raise ValueError("Payload Catalog extends must be a safe relative JSON path")
        return self


class RawInputSeed(StrictModel):
    family_key: Key
    variant_key: Key
    name: str = Field(min_length=1, max_length=240)
    source_series_key: Key
    source_field: Key
    formula_identity: str = Field(min_length=1)
    semantic_role: Key
    unit: Key
    direction: Literal[
        "not_applicable",
        "higher_is_better",
        "lower_is_better",
        "higher_is_bullish",
        "higher_is_bearish",
    ]
    payload_contract_key: Key
    aggregation_readiness: Literal[
        "aggregation_ready", "not_aggregation_ready", "requires_explicit_adapter"
    ]
    research_hypothesis: str = Field(min_length=1)


class RawInputCatalog(StrictModel):
    catalog_type: Literal["v022_raw_inputs"]
    catalog_version: SemVer
    raw_inputs: list[RawInputSeed] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> RawInputCatalog:
        _unique("raw family key", [item.family_key for item in self.raw_inputs])
        _unique("raw variant key", [item.variant_key for item in self.raw_inputs])
        _unique(
            "raw source field",
            [(item.source_series_key, item.source_field) for item in self.raw_inputs],
        )
        return self


class NodePortSeed(StrictModel):
    port_key: Key
    direction: Literal["input", "output"]
    ordinal: int = Field(ge=0)
    payload_contract_key: Key
    binding_cardinality: Literal["required"]
    semantics: dict[str, object]


class NodeInputBindingSeed(StrictModel):
    input_port_key: Key
    source_feature_variant_key: Key
    binding_role: Key
    ordinal: int = Field(ge=0)


class NodeOutputFeatureSeed(StrictModel):
    output_port_key: Key
    family_key: Key
    variant_key: Key
    name: str = Field(min_length=1, max_length=240)
    formula_identity: str = Field(min_length=1)
    parameters: dict[str, object]
    semantic_role: Key
    unit: Key
    direction: Literal[
        "not_applicable",
        "higher_is_better",
        "lower_is_better",
        "higher_is_bullish",
        "higher_is_bearish",
    ]
    payload_contract_key: Key
    aggregation_readiness: Literal[
        "aggregation_ready", "not_aggregation_ready", "requires_explicit_adapter"
    ]
    research_hypothesis: str = Field(min_length=1)
    research_tier: Literal["canonical", "sensitivity", "research_only", "compatibility"]
    output_semantics: dict[str, object]


class ProcessingNodeSeed(StrictModel):
    node_key: Key
    variant_key: Key
    name: str = Field(min_length=1, max_length=240)
    algorithm_identity: str = Field(min_length=1)
    parameters: dict[str, object]
    version_number: int = Field(ge=1)
    stage_no: int = Field(ge=1, le=3)
    implementation_key: str = Field(min_length=1, max_length=240)
    implementation_version: str = Field(min_length=1, max_length=80)
    determinism_policy: Literal["deterministic", "seeded", "externally_frozen"]
    cache_policy: Literal["content_addressed", "disabled"]
    execution_contract: dict[str, object]
    ports: list[NodePortSeed] = Field(min_length=2)
    input_bindings: list[NodeInputBindingSeed] = Field(min_length=1)
    output_features: list[NodeOutputFeatureSeed] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ports(self) -> ProcessingNodeSeed:
        _unique("node port key", [item.port_key for item in self.ports])
        _unique("node port ordinal", [(item.direction, item.ordinal) for item in self.ports])
        inputs = {item.port_key for item in self.ports if item.direction == "input"}
        outputs = {item.port_key for item in self.ports if item.direction == "output"}
        if not inputs or not outputs:
            raise ValueError("Processing node requires input and output ports")
        _unique("node binding role", [item.binding_role for item in self.input_bindings])
        _unique("bound input port", [item.input_port_key for item in self.input_bindings])
        if {item.input_port_key for item in self.input_bindings} != inputs:
            raise ValueError("Every input port must have exactly one required fixed binding")
        _unique(
            "node output feature variant",
            [item.variant_key for item in self.output_features],
        )
        _unique(
            "node output feature port",
            [item.output_port_key for item in self.output_features],
        )
        if {item.output_port_key for item in self.output_features} != outputs:
            raise ValueError("Every output port must map to exactly one Feature Variant")
        port_contracts = {
            item.port_key: item.payload_contract_key
            for item in self.ports
            if item.direction == "output"
        }
        if any(
            port_contracts[item.output_port_key] != item.payload_contract_key
            for item in self.output_features
        ):
            raise ValueError("Output Feature and output port Payload Contracts must match")
        return self


class ProcessingCatalog(StrictModel):
    catalog_type: Literal["v022_processing"]
    catalog_version: SemVer
    nodes: list[ProcessingNodeSeed]

    @model_validator(mode="after")
    def validate_catalog(self) -> ProcessingCatalog:
        _unique("processing node variant key", [item.variant_key for item in self.nodes])
        definitions: dict[str, tuple[str, str]] = {}
        for item in self.nodes:
            identity = (item.name, item.algorithm_identity)
            previous = definitions.setdefault(item.node_key, identity)
            if previous != identity:
                raise ValueError("Node Definition identity must be stable across Variants")
        _unique(
            "produced feature variant",
            [
                output.variant_key
                for item in self.nodes
                for output in item.output_features
            ],
        )
        return self


class PresetSeed(StrictModel):
    preset_key: Key
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1)
    version_number: int = Field(ge=1)
    semantics: dict[str, object]


class TargetSeed(StrictModel):
    target_key: Key
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1)
    version_number: int = Field(ge=1)
    semantics: dict[str, object]


class AggregationFamilySeed(StrictModel):
    family_key: Key
    name: str = Field(min_length=1, max_length=240)
    algorithm_identity: str = Field(min_length=1)
    objective_semantics: dict[str, object]
    output_semantics: dict[str, object]
    version_number: int = Field(ge=1)
    execution_mode: Literal["deterministic", "supervised"]
    implementation_key: str = Field(min_length=1, max_length=240)
    input_payload_contract_key: Key
    output_payload_contract_key: Key
    minimum_inputs: int = Field(ge=1)
    maximum_inputs: int = Field(ge=1)
    ordering_policy: Literal["explicit_input_order", "family_then_variant"]
    input_policy: dict[str, object]
    compatibility_policy: dict[str, object]
    missing_policy: dict[str, object]
    tie_policy: dict[str, object]
    parameter_presets: list[PresetSeed]
    targets: list[TargetSeed]
    training_presets: list[PresetSeed]

    @model_validator(mode="after")
    def validate_axes(self) -> AggregationFamilySeed:
        if self.maximum_inputs < self.minimum_inputs:
            raise ValueError("Aggregation maximum_inputs must cover minimum_inputs")
        _unique(
            "aggregation parameter preset",
            [item.preset_key for item in self.parameter_presets],
        )
        _unique("aggregation target", [item.target_key for item in self.targets])
        _unique("aggregation training preset", [item.preset_key for item in self.training_presets])
        if self.execution_mode == "deterministic" and (self.targets or self.training_presets):
            raise ValueError("Deterministic aggregation cannot declare target/training axes")
        if self.execution_mode == "supervised" and not (self.targets and self.training_presets):
            raise ValueError("Supervised aggregation requires target and training axes")
        return self


class AggregationFeatureTaxonomyEntrySeed(StrictModel):
    feature_family_key: Key
    research_dimension_key: Key
    accepted_units: list[Key] = Field(min_length=1)
    accepted_directions: list[
        Literal[
            "higher_is_better",
            "lower_is_better",
            "higher_is_bullish",
            "higher_is_bearish",
        ]
    ] = Field(min_length=1)
    native_hierarchical_eligible: bool
    calibration_key: Key | None = None

    @model_validator(mode="after")
    def validate_native_eligibility(self) -> AggregationFeatureTaxonomyEntrySeed:
        _unique("taxonomy accepted unit", self.accepted_units)
        _unique("taxonomy accepted direction", self.accepted_directions)
        if self.native_hierarchical_eligible:
            if self.accepted_units != ["centered_rank"]:
                raise ValueError(
                    "Native hierarchical inputs must use the centered_rank scale"
                )
            if self.accepted_directions != ["higher_is_better"]:
                raise ValueError(
                    "Native hierarchical inputs must use higher_is_better direction"
                )
            if self.calibration_key is not None:
                raise ValueError(
                    "Already comparable native inputs cannot declare calibration"
                )
        elif self.calibration_key is not None:
            raise ValueError(
                "A published calibration must be released as a comparable Feature Version"
            )
        return self


class AggregationFeatureTaxonomySeed(StrictModel):
    taxonomy_key: Key
    version_number: int = Field(ge=1)
    description: str = Field(min_length=1)
    entries: list[AggregationFeatureTaxonomyEntrySeed] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_entries(self) -> AggregationFeatureTaxonomySeed:
        _unique(
            "aggregation taxonomy feature family",
            [item.feature_family_key for item in self.entries],
        )
        return self


class AggregationCatalog(StrictModel):
    catalog_type: Literal["v022_aggregation"]
    catalog_version: SemVer
    extends: list[str] = Field(default_factory=list)
    feature_taxonomy: AggregationFeatureTaxonomySeed | None = None
    families: list[AggregationFamilySeed] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_catalog(self) -> AggregationCatalog:
        _unique("aggregation family", [item.family_key for item in self.families])
        if not self.families and not self.extends:
            raise ValueError("Aggregation Catalog requires families or an inherited Catalog")
        for value in self.extends:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or path.suffix != ".json":
                raise ValueError("Aggregation Catalog extends must be a safe relative JSON path")
        return self


class StrategySeed(StrictModel):
    family_key: Key
    variant_key: Key
    name: str = Field(min_length=1, max_length=240)
    selection_semantics: dict[str, object]
    research_hypothesis: str = Field(min_length=1)
    parameters: dict[str, object]
    version_number: int = Field(ge=1)
    implementation_key: str = Field(min_length=1, max_length=240)
    input_payload_contract_key: Key
    schedule_policy: dict[str, object]
    execution_policy: dict[str, object]


class StrategyParameterPresetSeed(StrictModel):
    strategy_variant_key: Key
    preset_key: Key
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1)
    version_number: int = Field(ge=1)
    parameters: dict[str, object] = Field(min_length=1)


class StrategyCatalog(StrictModel):
    catalog_type: Literal["v022_strategy"]
    catalog_version: SemVer
    strategies: list[StrategySeed] = Field(min_length=1)
    parameter_presets: list[StrategyParameterPresetSeed] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_catalog(self) -> StrategyCatalog:
        _unique("strategy variant", [item.variant_key for item in self.strategies])
        _unique(
            "strategy parameter preset",
            [
                (item.strategy_variant_key, item.preset_key)
                for item in self.parameter_presets
            ],
        )
        strategy_keys = {item.variant_key for item in self.strategies}
        strategies_by_key = {item.variant_key: item for item in self.strategies}
        unknown_owners = sorted(
            {
                item.strategy_variant_key
                for item in self.parameter_presets
                if item.strategy_variant_key not in strategy_keys
            }
        )
        if unknown_owners:
            raise ValueError(
                f"Strategy parameter presets reference unknown Variants: {unknown_owners}"
            )
        if self.parameter_presets:
            missing_preset_owners = sorted(
                strategy_keys
                - {item.strategy_variant_key for item in self.parameter_presets}
            )
            if missing_preset_owners:
                raise ValueError(
                    "A parameterized Strategy Catalog requires a preset for every "
                    f"Variant: {missing_preset_owners}"
                )
        semantic_presets: set[tuple[str, str]] = set()
        required_parameter_keys = {"target_k", "selection_buffer", "sector_cap"}
        for preset in self.parameter_presets:
            if len(
                f"{preset.strategy_variant_key}__{preset.preset_key}".encode()
            ) > 200:
                raise ValueError(
                    "Strategy parameter preset Variant-scoped key exceeds the "
                    "Artifact key contract"
                )
            if set(preset.parameters) != required_parameter_keys:
                raise ValueError(
                    "Strategy parameter preset must resolve target_k, "
                    "selection_buffer, and sector_cap exactly"
                )
            strategy = strategies_by_key[preset.strategy_variant_key]
            target_k = preset.parameters["target_k"]
            if isinstance(target_k, bool) or not isinstance(target_k, int):
                raise ValueError("Strategy parameter preset target_k must be an integer")
            allowed_k = strategy.parameters.get("allowed_k")
            if not isinstance(allowed_k, list) or target_k not in allowed_k:
                raise ValueError(
                    f"Strategy parameter preset target_k is outside its Variant: "
                    f"{preset.strategy_variant_key}:{preset.preset_key}"
                )
            for key in ("selection_buffer", "sector_cap"):
                allowed = strategy.parameters.get(key, ["none"])
                if not isinstance(allowed, list) or preset.parameters[key] not in allowed:
                    raise ValueError(
                        f"Strategy parameter preset {key} is outside its Variant: "
                        f"{preset.strategy_variant_key}:{preset.preset_key}"
                    )
            semantic_key = (
                preset.strategy_variant_key,
                sha256_hexdigest(preset.parameters),
            )
            if semantic_key in semantic_presets:
                raise ValueError(
                    f"Duplicate Strategy parameter semantics: "
                    f"{preset.strategy_variant_key}:{preset.preset_key}"
                )
            semantic_presets.add(semantic_key)
        family_contracts: dict[str, tuple[object, ...]] = {}
        for item in self.strategies:
            contract = (
                item.name,
                item.selection_semantics,
                item.research_hypothesis,
            )
            previous = family_contracts.setdefault(item.family_key, contract)
            if previous != contract:
                raise ValueError(
                    f"Strategy Family semantics drift across variants: {item.family_key}"
                )
        return self


class DefensePolicyRef(StrictModel):
    variant_key: Key
    version_number: int = Field(ge=1)


class DefenseSeed(StrictModel):
    family_key: Key
    variant_key: Key
    name: str = Field(min_length=1, max_length=240)
    allocation_semantics: dict[str, object]
    research_hypothesis: str = Field(min_length=1)
    parameters: dict[str, object]
    version_number: int = Field(ge=1)
    implementation_key: str = Field(min_length=1, max_length=240)
    input_policy: dict[str, object]
    allocation_policy: dict[str, object]
    supported_asset_context_keys: list[Key] = Field(min_length=1)
    timing_policy_ref: DefensePolicyRef | None = None
    defensive_allocation_policy_ref: DefensePolicyRef | None = None
    research_status: Literal["exploratory", "parity", "formal"] | None = None


class DefenseBudgetPairSeed(StrictModel):
    risk_budget: CanonicalDecimal
    defense_budget: CanonicalDecimal

    @model_validator(mode="after")
    def validate_budget_pair(self) -> DefenseBudgetPairSeed:
        risk = Decimal(self.risk_budget)
        defense = Decimal(self.defense_budget)
        if not Decimal(0) <= risk <= Decimal(1):
            raise ValueError("Defense risk budget must be inside [0, 1]")
        if not Decimal(0) <= defense <= Decimal(1):
            raise ValueError("Defense budget must be inside [0, 1]")
        if risk + defense != Decimal(1):
            raise ValueError("Defense risk and defense budgets must sum exactly to one")
        return self


class FixedDefenseBudgetRuleSeed(StrictModel):
    rule_type: Literal["fixed_budget"]
    budget: DefenseBudgetPairSeed


class MovingAverageTieredBudgetRuleSeed(StrictModel):
    rule_type: Literal["moving_average_tiered_budget"]
    reference_asset_key: Key
    price_field: Literal["adjusted_close"]
    moving_average_window_sessions: int = Field(ge=2)
    indicator_key: Literal["spy_close_div_sma200_minus_one"]
    upper_threshold: CanonicalDecimal
    lower_threshold: CanonicalDecimal
    above_upper: DefenseBudgetPairSeed
    middle: DefenseBudgetPairSeed
    below_lower: DefenseBudgetPairSeed
    boundary_policy: Literal["strict_outer_inclusive_middle"]

    @model_validator(mode="after")
    def validate_tiers(self) -> MovingAverageTieredBudgetRuleSeed:
        upper = Decimal(self.upper_threshold)
        lower = Decimal(self.lower_threshold)
        if not Decimal(-1) <= lower < upper <= Decimal(1):
            raise ValueError("Defense timing thresholds must be ordered inside [-1, 1]")
        defense_budgets = tuple(
            Decimal(item.defense_budget)
            for item in (self.above_upper, self.middle, self.below_lower)
        )
        if tuple(sorted(defense_budgets)) != defense_budgets:
            raise ValueError("Defense budget tiers must not decrease as the regime weakens")
        return self


DefenseTimingRuleSeed = Annotated[
    FixedDefenseBudgetRuleSeed | MovingAverageTieredBudgetRuleSeed,
    Field(discriminator="rule_type"),
]


class DefenseTimingInputPolicySeed(StrictModel):
    market_timing_signal_required: bool
    known_at_required: bool
    missing_input_policy: Literal["fail"]
    stale_input_policy: Literal["fail"]
    decision_cutoff: Literal["scheduled_session_close"]
    execution_policy: Literal["next_common_session_raw_open"]


class DefenseTimingPolicySeed(StrictModel):
    family_key: Key
    variant_key: Key
    name: str = Field(min_length=1, max_length=240)
    formula_identity: str = Field(min_length=1)
    research_hypothesis: str = Field(min_length=1)
    version_number: int = Field(ge=1)
    implementation_key: str = Field(min_length=1, max_length=240)
    research_status: Literal["exploratory", "parity", "formal"]
    supported_frequencies: list[Literal["weekly", "monthly"]] = Field(min_length=1)
    input_policy: DefenseTimingInputPolicySeed
    rule: DefenseTimingRuleSeed

    @model_validator(mode="after")
    def validate_policy(self) -> DefenseTimingPolicySeed:
        _unique("Defense Timing frequency", self.supported_frequencies)
        if isinstance(self.rule, FixedDefenseBudgetRuleSeed):
            if (
                self.input_policy.market_timing_signal_required
                or self.input_policy.known_at_required
            ):
                raise ValueError(
                    "Fixed Defense Timing cannot require a market signal or known-at input"
                )
        elif (
            not self.input_policy.market_timing_signal_required
            or not self.input_policy.known_at_required
        ):
            raise ValueError("MA-tiered Defense Timing requires a known-at market signal")
        return self


class DefenseAllocationMemberSeed(StrictModel):
    ordinal: int = Field(ge=0)
    asset_key: Key
    component_role: Literal["defensive_asset", "reserve"]
    sleeve_weight: CanonicalDecimal

    @model_validator(mode="after")
    def validate_weight(self) -> DefenseAllocationMemberSeed:
        weight = Decimal(self.sleeve_weight)
        if not Decimal(0) < weight <= Decimal(1):
            raise ValueError("Defensive Allocation member weight must be inside (0, 1]")
        return self


class ReserveReturnModelRefSeed(StrictModel):
    model_key: Literal["dgs3mo_cash_accrual_proxy"]
    version_number: Literal[1]


class DefenseAllocationPolicySeed(StrictModel):
    family_key: Key
    variant_key: Key
    name: str = Field(min_length=1, max_length=240)
    formula_identity: str = Field(min_length=1)
    research_hypothesis: str = Field(min_length=1)
    version_number: int = Field(ge=1)
    implementation_key: str = Field(min_length=1, max_length=240)
    asset_registry_catalog_version: SemVer
    asset_set_key: Key
    research_status: Literal["exploratory", "parity", "formal"]
    formal_eligible: bool
    missing_member_policy: Literal["fail"]
    reserve_fallback_policy: Literal["forbidden"]
    rebalance_policy: Literal["with_strategy"]
    reserve_return_model_ref: ReserveReturnModelRefSeed | None = None
    members: list[DefenseAllocationMemberSeed] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_policy(self) -> DefenseAllocationPolicySeed:
        _unique("Defensive Allocation member ordinal", [item.ordinal for item in self.members])
        _unique("Defensive Allocation member asset", [item.asset_key for item in self.members])
        if [item.ordinal for item in self.members] != list(range(len(self.members))):
            raise ValueError("Defensive Allocation member ordinals must be contiguous and ordered")
        if sum((Decimal(item.sleeve_weight) for item in self.members), Decimal()) != Decimal(1):
            raise ValueError("Defensive Allocation member weights must sum exactly to one")
        reserve_member_count = sum(
            item.component_role == "reserve" for item in self.members
        )
        if reserve_member_count > 1:
            raise ValueError("Defensive Allocation can declare at most one reserve member")
        if (reserve_member_count == 1) != (self.reserve_return_model_ref is not None):
            raise ValueError(
                "Defensive Allocation requires a Reserve Return Model exactly when it "
                "contains a reserve member"
            )
        if self.research_status == "formal" and not self.formal_eligible:
            raise ValueError("A formal Defensive Allocation must be formal eligible")
        return self


class DefenseCatalog(StrictModel):
    catalog_type: Literal["v022_defense"]
    catalog_version: SemVer
    timing_policies: list[DefenseTimingPolicySeed] = Field(default_factory=list)
    allocation_policies: list[DefenseAllocationPolicySeed] = Field(default_factory=list)
    defenses: list[DefenseSeed] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> DefenseCatalog:
        _unique("defense variant", [item.variant_key for item in self.defenses])
        _unique(
            "Defense Timing variant", [item.variant_key for item in self.timing_policies]
        )
        _unique(
            "Defensive Allocation variant",
            [item.variant_key for item in self.allocation_policies],
        )
        if "none" in {item.variant_key for item in self.defenses}:
            raise ValueError("none is a null Defense Package and cannot be published")
        _validate_defense_family_semantics(self.defenses)
        _validate_policy_family_semantics("Defense Timing", self.timing_policies)
        _validate_policy_family_semantics(
            "Defensive Allocation", self.allocation_policies
        )
        if _semver_tuple(self.catalog_version) >= (0, 22, 2):
            self._validate_composed_packages()
        return self

    def _validate_composed_packages(self) -> None:
        if not self.timing_policies or not self.allocation_policies:
            raise ValueError("Composed Defense Catalog requires Timing and Allocation policies")
        timing = {
            (item.variant_key, item.version_number): item
            for item in self.timing_policies
        }
        allocations = {
            (item.variant_key, item.version_number): item
            for item in self.allocation_policies
        }
        referenced_timing: set[tuple[str, int]] = set()
        referenced_allocations: set[tuple[str, int]] = set()
        for package in self.defenses:
            if (
                package.timing_policy_ref is None
                or package.defensive_allocation_policy_ref is None
                or package.research_status is None
            ):
                raise ValueError(
                    "Every composed Defense Package requires exact Timing, Allocation, "
                    "and research-status identity"
                )
            if not package.implementation_key.startswith(
                "style_rotation.v022.defense_package."
            ):
                raise ValueError(
                    "A composed Defense Package must use a v0.22-owned implementation"
                )
            _unique(
                "Defense Package supported Asset Context",
                package.supported_asset_context_keys,
            )
            if any(
                key in {"us_style_etf", "us_large_cap_equity"}
                for key in package.supported_asset_context_keys
            ):
                raise ValueError(
                    "A composed Defense Package requires exact Asset Registry set keys"
                )
            timing_identity = (
                package.timing_policy_ref.variant_key,
                package.timing_policy_ref.version_number,
            )
            allocation_identity = (
                package.defensive_allocation_policy_ref.variant_key,
                package.defensive_allocation_policy_ref.version_number,
            )
            if timing_identity not in timing:
                raise ValueError(
                    f"Defense Package references unknown Timing Policy: {timing_identity}"
                )
            if allocation_identity not in allocations:
                raise ValueError(
                    "Defense Package references unknown Allocation Policy: "
                    f"{allocation_identity}"
                )
            allocation = allocations[allocation_identity]
            if package.research_status == "formal" and not allocation.formal_eligible:
                raise ValueError(
                    "A formal Defense Package cannot use an ineligible Allocation Policy"
                )
            referenced_timing.add(timing_identity)
            referenced_allocations.add(allocation_identity)
        if referenced_timing != set(timing):
            raise ValueError("Defense Catalog contains an unreferenced Timing Policy")
        if referenced_allocations != set(allocations):
            raise ValueError("Defense Catalog contains an unreferenced Allocation Policy")


class ReleaseFileSeed(StrictModel):
    component_group: Literal[
        "payload", "raw_inputs", "processing", "aggregation", "strategy", "defense"
    ]
    path: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_relative_path(self) -> ReleaseFileSeed:
        path = PurePosixPath(self.path)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".json":
            raise ValueError("Release component path must be a safe relative JSON path")
        return self


class ExpectedReleaseCounts(StrictModel):
    payload_contracts: int = Field(ge=1)
    physical_encodings: int = Field(ge=1)
    raw_inputs: int = Field(ge=1)
    processing_nodes: int = Field(ge=0)
    processing_features: int = Field(default=0, ge=0)
    aggregation_families: int = Field(ge=1)
    strategies: int = Field(ge=1)
    strategy_parameter_presets: int = Field(default=0, ge=0)
    defense_timing_policies: int = Field(default=0, ge=0)
    defense_allocation_policies: int = Field(default=0, ge=0)
    defenses: int = Field(ge=1)


class CatalogReleaseManifest(StrictModel):
    catalog_type: Literal["v022_release"]
    catalog_version: SemVer
    release_key: Key
    contract_version: Literal["v0.22.0"]
    processing_stage_count: Literal[3]
    publisher_actor: Key
    reviewer_actor: Key
    files: list[ReleaseFileSeed] = Field(min_length=6)
    expected_counts: ExpectedReleaseCounts

    @model_validator(mode="after")
    def validate_files(self) -> CatalogReleaseManifest:
        _unique("release file group", [item.component_group for item in self.files])
        if {item.component_group for item in self.files} != {
            "payload",
            "raw_inputs",
            "processing",
            "aggregation",
            "strategy",
            "defense",
        }:
            raise ValueError("Release must contain every M1 component group exactly once")
        _unique("release file path", [item.path for item in self.files])
        return self


class CatalogBundle(StrictModel):
    release: CatalogReleaseManifest
    payload: PayloadCatalog
    raw_inputs: RawInputCatalog
    processing: ProcessingCatalog
    aggregation: AggregationCatalog
    strategy: StrategyCatalog
    defense: DefenseCatalog
    source_manifest_hash: Fingerprint

    @model_validator(mode="after")
    def validate_bundle(self) -> CatalogBundle:
        version = self.release.catalog_version
        catalogs = (
            self.payload,
            self.raw_inputs,
            self.processing,
            self.aggregation,
            self.strategy,
            self.defense,
        )
        release_parts = tuple(int(part) for part in version.split("."))
        for item in catalogs:
            component_parts = tuple(int(part) for part in item.catalog_version.split("."))
            if component_parts[:2] != release_parts[:2] or component_parts > release_parts:
                raise ValueError(
                    "Component Catalog version must be same-line and not newer than Release"
                )
        expected = self.release.expected_counts
        actual = (
            len(self.payload.contracts),
            len(self.payload.encodings),
            len(self.raw_inputs.raw_inputs),
            len(self.processing.nodes),
            sum(len(item.output_features) for item in self.processing.nodes),
            len(self.aggregation.families),
            len(self.strategy.strategies),
            len(self.strategy.parameter_presets),
            len(self.defense.timing_policies),
            len(self.defense.allocation_policies),
            len(self.defense.defenses),
        )
        declared = (
            expected.payload_contracts,
            expected.physical_encodings,
            expected.raw_inputs,
            expected.processing_nodes,
            expected.processing_features,
            expected.aggregation_families,
            expected.strategies,
            expected.strategy_parameter_presets,
            expected.defense_timing_policies,
            expected.defense_allocation_policies,
            expected.defenses,
        )
        if actual != declared:
            raise ValueError(f"Release count mismatch: expected {declared}, found {actual}")
        contract_keys = {item.contract_key for item in self.payload.contracts}
        references = {
            *(item.payload_contract_key for item in self.raw_inputs.raw_inputs),
            *(port.payload_contract_key for node in self.processing.nodes for port in node.ports),
            *(
                output.payload_contract_key
                for node in self.processing.nodes
                for output in node.output_features
            ),
            *(item.input_payload_contract_key for item in self.aggregation.families),
            *(item.output_payload_contract_key for item in self.aggregation.families),
            *(item.input_payload_contract_key for item in self.strategy.strategies),
        }
        unknown = sorted(references - contract_keys)
        if unknown:
            raise ValueError(f"Catalog references unknown Payload Contracts: {unknown}")
        if len(self.raw_inputs.raw_inputs) != 9:
            raise ValueError("v0.22.0 M1 must publish exactly 9 Raw Inputs")
        deterministic = {
            item.family_key
            for item in self.aggregation.families
            if item.execution_mode == "deterministic"
        }
        if deterministic != {
            "single_signal_identity",
            "flat_equal_weight_mean",
            "hierarchical_weighted_mean",
            "directional_weighted_vote",
        }:
            raise ValueError("v0.22.0 must publish exactly the four deterministic families")
        self._validate_node_bindings()
        return self

    def _validate_node_bindings(self) -> None:
        stages = {item.variant_key: 0 for item in self.raw_inputs.raw_inputs}
        for node in sorted(self.processing.nodes, key=lambda item: item.stage_no):
            for binding in node.input_bindings:
                source_stage = stages.get(binding.source_feature_variant_key)
                if source_stage is None:
                    raise ValueError(
                        f"Node {node.variant_key} references unknown feature "
                        f"{binding.source_feature_variant_key}"
                    )
                if source_stage >= node.stage_no:
                    raise ValueError("Processing source must originate in an earlier stage")
            for output in node.output_features:
                if output.variant_key in stages:
                    raise ValueError(
                        f"Duplicate produced feature identity: {output.variant_key}"
                    )
                stages[output.variant_key] = node.stage_no


def _semver_tuple(value: str) -> tuple[int, int, int]:
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def _validate_defense_family_semantics(items: Sequence[DefenseSeed]) -> None:
    contracts: dict[str, tuple[object, ...]] = {}
    for item in items:
        contract = (
            item.name,
            item.allocation_semantics,
            item.research_hypothesis,
        )
        previous = contracts.setdefault(item.family_key, contract)
        if previous != contract:
            raise ValueError(
                f"Defense Package Family semantics drift across variants: {item.family_key}"
            )


def _validate_policy_family_semantics(
    label: str,
    items: Sequence[DefenseTimingPolicySeed | DefenseAllocationPolicySeed],
) -> None:
    contracts: dict[str, tuple[object, ...]] = {}
    for item in items:
        contract = (item.name, item.formula_identity, item.research_hypothesis)
        previous = contracts.setdefault(item.family_key, contract)
        if previous != contract:
            raise ValueError(f"{label} Family semantics drift across variants: {item.family_key}")


def _unique(label: str, values: Sequence[object]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")
