from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SelectionOrder = Literal[
    "rank_then_select",
    "rank_select_then_filter",
    "filter_then_rank_select",
]
TrendFilter = Literal["none", "published_threshold_state"]
PresetType = Literal["canonical", "sensitivity"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrategyDefinitionSeed(StrictModel):
    key: str = Field(min_length=1, max_length=160)
    family: Literal["cross_sectional_top_k_rotation"]
    hypothesis: str = Field(min_length=1)


class StrategyInputContractSeed(StrictModel):
    key: str = Field(min_length=1, max_length=160)
    requires_model_score: Literal[True]
    compatible_model_output_types: list[Literal["continuous_score", "directional_score"]] = Field(
        min_length=1
    )
    candidate_input_policy: Literal["complete_eligible_universe"]
    missing_input_policy: Literal["fail_formal_run"]


class VariantTemplateSeed(StrictModel):
    key: str = Field(min_length=1, max_length=160)
    input_contract_key: str = Field(min_length=1, max_length=160)
    selection_order: SelectionOrder
    trend_filter: TrendFilter
    empty_slot_policy: Literal[
        "not_applicable",
        "reserve_without_replacement",
        "eligible_backfill_then_reserve",
    ]

    @model_validator(mode="after")
    def validate_semantics(self) -> VariantTemplateSeed:
        expected = {
            "rank_then_select": ("none", "not_applicable"),
            "rank_select_then_filter": (
                "published_threshold_state",
                "reserve_without_replacement",
            ),
            "filter_then_rank_select": (
                "published_threshold_state",
                "eligible_backfill_then_reserve",
            ),
        }[self.selection_order]
        if (self.trend_filter, self.empty_slot_policy) != expected:
            raise ValueError("Variant filter order and empty-slot semantics are inconsistent")
        return self


class KValueSeed(StrictModel):
    value: Literal[1, 2, 3]
    preset_type: PresetType

    @model_validator(mode="after")
    def validate_tier(self) -> KValueSeed:
        expected = "canonical" if self.value == 2 else "sensitivity"
        if self.preset_type != expected:
            raise ValueError("Only K=2 is canonical; K=1 and K=3 are sensitivity presets")
        return self


class ScheduleSeed(StrictModel):
    key: str = Field(min_length=1, max_length=160)
    frequency: Literal["weekly", "monthly"]
    decision_timing: Literal["last_common_session_close"]
    decision_data_policy: Literal["include_decision_close"]


class ExecutionPolicySeed(StrictModel):
    key: str = Field(min_length=1, max_length=160)
    delay_common_sessions: Literal[1]
    execution_price: Literal["adjusted_open"]
    missing_execution_policy: Literal["fail_formal_run"]


class ExpectedStrategyCounts(StrictModel):
    strategy_variant_configurations: int = Field(gt=0)
    schedule_choices_per_configuration: int = Field(gt=0)


class ProductGenerationPolicy(StrictModel):
    included_model_specification_types: list[
        Literal["dimension_subset_equal_weight", "fixed_weight", "directional_vote"]
    ] = Field(min_length=1)
    single_signal_models: Literal["on_demand_when_signal_product_eligible"]
    event_only_models: Literal["diagnostic_only_unless_future_strategy_declares_compatibility"]


class StrategyCatalog(StrictModel):
    catalog_type: Literal["strategy"]
    catalog_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    definition: StrategyDefinitionSeed
    input_contracts: list[StrategyInputContractSeed] = Field(min_length=1)
    variant_templates: list[VariantTemplateSeed] = Field(min_length=1)
    k_values: list[KValueSeed] = Field(min_length=1)
    schedules: list[ScheduleSeed] = Field(min_length=1)
    execution_policy: ExecutionPolicySeed
    trend_signal: str = Field(min_length=1, max_length=500)
    trend_eligible_state: Literal["positive"]
    trend_boundary_policy: Literal["zero_is_ineligible"]
    tie_policy: Literal["proportional_share_of_remaining_slot_budget"]
    slot_weight_rule: Literal["1 / K"]
    reserve_rule: Literal["unused_slot_budget_to_synthetic_reserve"]
    expected_counts: ExpectedStrategyCounts
    product_generation_policy: ProductGenerationPolicy

    @model_validator(mode="after")
    def validate_catalog(self) -> StrategyCatalog:
        _unique("input contract key", [item.key for item in self.input_contracts])
        _unique("variant template key", [item.key for item in self.variant_templates])
        _unique("K value", [item.value for item in self.k_values])
        _unique("schedule key", [item.key for item in self.schedules])
        _unique("schedule frequency", [item.frequency for item in self.schedules])
        contracts = {item.key for item in self.input_contracts}
        unknown_contracts = {item.input_contract_key for item in self.variant_templates}.difference(
            contracts
        )
        if unknown_contracts:
            raise ValueError(f"Variants reference unknown input contracts: {unknown_contracts}")
        if {item.value for item in self.k_values} != {1, 2, 3}:
            raise ValueError("Strategy catalog must contain exactly K=1, K=2, and K=3")
        if {item.frequency for item in self.schedules} != {"weekly", "monthly"}:
            raise ValueError("Strategy catalog must contain weekly and monthly schedules")
        variant_count = len(self.variant_templates) * len(self.k_values)
        if self.expected_counts.strategy_variant_configurations != variant_count:
            raise ValueError("Expected strategy variant count is inconsistent")
        if self.expected_counts.schedule_choices_per_configuration != len(self.schedules):
            raise ValueError("Expected schedule count is inconsistent")
        return self


class ExpandedStrategyVariant(StrictModel):
    key: str
    template_key: str
    input_contract_key: str
    k: Literal[1, 2, 3]
    preset_type: PresetType
    selection_order: SelectionOrder
    trend_filter: TrendFilter
    empty_slot_policy: str
    tie_policy: str
    slot_weight_rule: str
    reserve_rule: str
    auxiliary_signal_key: str | None
    auxiliary_eligible_state: str | None


def expand_strategy_variants(catalog: StrategyCatalog) -> tuple[ExpandedStrategyVariant, ...]:
    variants = tuple(
        ExpandedStrategyVariant(
            key=f"{template.key}__k{k_value.value}",
            template_key=template.key,
            input_contract_key=template.input_contract_key,
            k=k_value.value,
            preset_type=k_value.preset_type,
            selection_order=template.selection_order,
            trend_filter=template.trend_filter,
            empty_slot_policy=template.empty_slot_policy,
            tie_policy=catalog.tie_policy,
            slot_weight_rule=catalog.slot_weight_rule,
            reserve_rule=catalog.reserve_rule,
            auxiliary_signal_key=(
                catalog.trend_signal if template.trend_filter != "none" else None
            ),
            auxiliary_eligible_state=(
                catalog.trend_eligible_state if template.trend_filter != "none" else None
            ),
        )
        for template in catalog.variant_templates
        for k_value in catalog.k_values
    )
    if len(variants) != catalog.expected_counts.strategy_variant_configurations:
        raise ValueError("Expanded strategy variant count does not match the catalog contract")
    _unique("expanded strategy variant key", [item.key for item in variants])
    return variants


def _unique(label: str, values: list[object]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")
