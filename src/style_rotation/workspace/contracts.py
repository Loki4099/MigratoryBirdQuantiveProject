from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Frequency = Literal["weekly", "monthly"]
SignalOutputType = Literal["continuous", "directional", "event"]
ModelOutputType = Literal["continuous_score", "directional_score"]
CompilationReasonCode = Literal[
    "selection_unknown",
    "selection_invalidated",
    "signal_factor_not_selected",
    "signal_frequency_incompatible",
    "model_frequency_incompatible",
    "model_signal_unaccepted",
    "model_signal_ambiguous_slot",
    "model_slot_underflow",
    "model_slot_overflow",
    "strategy_frequency_incompatible",
    "strategy_model_output_incompatible",
    "strategy_requires_continuous_comparable_score",
]
CompilationLayer = Literal["factor", "signal", "model", "strategy"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResearchDraftSelection(StrictModel):
    asset_context_key: str = Field(min_length=1, max_length=200)
    factor_variant_keys: tuple[str, ...] = Field(min_length=1)
    signal_version_keys: tuple[str, ...] = Field(min_length=1)
    model_preset_keys: tuple[str, ...] = Field(min_length=1)
    model_target_keys: tuple[str, ...] = Field(
        default=("cross_sectional_relative_return__h5",), min_length=1
    )
    strategy_preset_keys: tuple[str, ...] = Field(min_length=1)
    frequency: Frequency

    @model_validator(mode="after")
    def validate_unique_selections(self) -> ResearchDraftSelection:
        for label, values in (
            ("factor variant", self.factor_variant_keys),
            ("Signal version", self.signal_version_keys),
            ("Model preset", self.model_preset_keys),
            ("Model target", self.model_target_keys),
            ("Strategy preset", self.strategy_preset_keys),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate {label} selection")
        return self


class SignalDescriptor(StrictModel):
    version_key: str = Field(min_length=1, max_length=200)
    factor_variant_key: str = Field(min_length=1, max_length=200)
    dimension_key: str = Field(min_length=1, max_length=120)
    output_type: SignalOutputType
    frequency: Frequency


class ModelInputSlot(StrictModel):
    slot_key: str = Field(min_length=1, max_length=120)
    allowed_dimension_keys: frozenset[str] = Field(min_length=1)
    allowed_output_types: frozenset[SignalOutputType] = Field(min_length=1)
    minimum_count: int = Field(ge=0)
    maximum_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_count_bounds(self) -> ModelInputSlot:
        if self.minimum_count > self.maximum_count:
            raise ValueError("Model slot minimum_count cannot exceed maximum_count")
        return self


class ModelPresetDescriptor(StrictModel):
    preset_key: str = Field(min_length=1, max_length=200)
    family_key: str = Field(min_length=1, max_length=120)
    output_type: ModelOutputType
    output_comparability: Literal["cross_sectional", "diagnostic_only"]
    supported_frequencies: frozenset[Frequency] = Field(min_length=1)
    input_slots: tuple[ModelInputSlot, ...] = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    target_key: str | None = None

    @model_validator(mode="after")
    def validate_unique_slots(self) -> ModelPresetDescriptor:
        keys = [slot.slot_key for slot in self.input_slots]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate Model input slot")
        return self


class StrategyPresetDescriptor(StrictModel):
    preset_key: str = Field(min_length=1, max_length=200)
    family_key: Literal["multi_etf_top_k", "us_large_cap_top_k"]
    compatible_model_output_types: frozenset[ModelOutputType] = Field(min_length=1)
    supported_frequencies: frozenset[Frequency] = Field(min_length=1)
    target_k: int = Field(ge=1)
    minimum_eligible_assets: int = Field(ge=2)
    formal_minimum_eligible_assets: int = Field(ge=2)
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_asset_thresholds(self) -> StrategyPresetDescriptor:
        if self.formal_minimum_eligible_assets < self.minimum_eligible_assets:
            raise ValueError("Formal minimum cannot be below the launch minimum")
        return self


class SlotAssignment(StrictModel):
    slot_key: str
    signal_version_keys: tuple[str, ...]


class CompiledModelInstance(StrictModel):
    instance_key: str
    preset_key: str
    family_key: str
    output_type: ModelOutputType
    frequency: Frequency
    slot_assignments: tuple[SlotAssignment, ...]
    parameters: dict[str, Any] = Field(default_factory=dict)
    target_key: str | None = None


class CompiledStrategyBranch(StrictModel):
    branch_key: str
    model_instance_key: str
    strategy_preset_key: str
    strategy_family_key: str
    frequency: Frequency
    target_k: int
    parameters: dict[str, Any] = Field(default_factory=dict)
    predictive_cell_count: Literal[1] = 1
    portfolio_cell_count: Literal[6] = 6


class CompilationIssue(StrictModel):
    reason_code: CompilationReasonCode
    layer: CompilationLayer
    object_key: str
    related_keys: tuple[str, ...] = ()


class CompiledResearchSpec(StrictModel):
    specification_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_context_key: str
    factor_variant_keys: tuple[str, ...]
    signal_version_keys: tuple[str, ...]
    model_instances: tuple[CompiledModelInstance, ...]
    strategy_branches: tuple[CompiledStrategyBranch, ...]
    issues: tuple[CompilationIssue, ...]
    predictive_cell_count: int = Field(ge=0)
    portfolio_cell_count: int = Field(ge=0)
    runnable: bool
