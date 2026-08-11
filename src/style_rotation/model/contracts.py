from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

InputTransform = Literal["identity", "sign", "threshold_state"]
ModelMethod = Literal["weighted_mean", "majority_vote", "weighted_vote"]
SpecificationType = Literal[
    "single_signal",
    "dimension_subset_equal_weight",
    "fixed_weight",
    "directional_vote",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelMethodSeed(StrictModel):
    key: ModelMethod
    input_transforms: list[InputTransform] = Field(min_length=1)


class RepresentativeDimensionSeed(StrictModel):
    key: str = Field(min_length=1, max_length=80)
    method: ModelMethod
    components: list[str] = Field(min_length=1)
    weights: list[float] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_components(self) -> RepresentativeDimensionSeed:
        if len(self.components) != len(self.weights):
            raise ValueError("Dimension component and weight counts must match")
        _unique("component within representative dimension", self.components)
        _positive_normalized(self.weights, f"dimension {self.key}")
        return self


class GenerationRuleSeed(StrictModel):
    key: Literal["single_signal", "dimension_subset_equal_weight"]
    description: str = Field(min_length=1)
    expected_count: int = Field(gt=0)


class FixedWeightSpecificationSeed(StrictModel):
    key: str = Field(min_length=1, max_length=160)
    method: Literal["weighted_mean"]
    dimension_weights: dict[str, float] = Field(min_length=1)


class VoteSpecificationSeed(StrictModel):
    key: str = Field(min_length=1, max_length=160)
    method: Literal["majority_vote", "weighted_vote"]
    dimension_weights: dict[str, float] = Field(min_length=1)
    tie_output: Literal["neutral"]


class ExpectedModelCounts(StrictModel):
    dimension_subset_patterns: int = Field(gt=0)
    concrete_model_specifications: int = Field(gt=0)


class ModelCatalog(StrictModel):
    catalog_type: Literal["model"]
    catalog_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    methods: list[ModelMethodSeed] = Field(min_length=1)
    representative_dimensions: list[RepresentativeDimensionSeed] = Field(min_length=1)
    generation_rules: list[GenerationRuleSeed] = Field(min_length=1)
    fixed_weight_specifications: list[FixedWeightSpecificationSeed]
    vote_specifications: list[VoteSpecificationSeed]
    expected_counts: ExpectedModelCounts

    @model_validator(mode="after")
    def validate_catalog(self) -> ModelCatalog:
        method_keys = [item.key for item in self.methods]
        _unique("model method key", method_keys)
        dimension_keys = [item.key for item in self.representative_dimensions]
        _unique("representative dimension key", dimension_keys)
        known_methods = set(method_keys)
        if any(item.method not in known_methods for item in self.representative_dimensions):
            raise ValueError("Representative dimension references an unknown model method")
        all_components = [
            component
            for dimension in self.representative_dimensions
            for component in dimension.components
        ]
        _unique("signal across representative dimensions", all_components)
        expected_dimensions = set(dimension_keys)
        for fixed_specification in self.fixed_weight_specifications:
            _weight_mapping(
                fixed_specification.dimension_weights,
                expected_dimensions,
                fixed_specification.key,
                normalized=True,
            )
        for vote_specification in self.vote_specifications:
            _weight_mapping(
                vote_specification.dimension_weights,
                expected_dimensions,
                vote_specification.key,
                normalized=False,
            )
        rule_counts = {item.key: item.expected_count for item in self.generation_rules}
        if set(rule_counts) != {"single_signal", "dimension_subset_equal_weight"}:
            raise ValueError("Both canonical model generation rules must be present exactly once")
        subset_count = (2 ** len(self.representative_dimensions)) - 1
        if rule_counts["dimension_subset_equal_weight"] != subset_count:
            raise ValueError("Dimension-subset rule count does not match the dimension set")
        if self.expected_counts.dimension_subset_patterns != subset_count:
            raise ValueError("Expected dimension-subset count is inconsistent")
        concrete_count = (
            rule_counts["single_signal"]
            + subset_count
            + len(self.fixed_weight_specifications)
            + len(self.vote_specifications)
        )
        if self.expected_counts.concrete_model_specifications != concrete_count:
            raise ValueError("Expected concrete model count is inconsistent")
        return self


class ExpandedComponent(StrictModel):
    signal_key: str
    input_transform: InputTransform
    weight: float = Field(gt=0, le=1)


class ExpandedDimension(StrictModel):
    key: str
    method: ModelMethod
    input_transform: InputTransform
    weight: float = Field(gt=0, le=1)
    components: tuple[ExpandedComponent, ...]


class ExpandedModelSpecification(StrictModel):
    key: str
    specification_type: SpecificationType
    method: ModelMethod
    tie_output: Literal["neutral", "not_applicable"]
    output_type: Literal["continuous_score", "directional_score"]
    dimensions: tuple[ExpandedDimension, ...]

    @model_validator(mode="after")
    def validate_expanded_weights(self) -> ExpandedModelSpecification:
        _positive_normalized([item.weight for item in self.dimensions], self.key)
        signal_keys = [
            component.signal_key
            for dimension in self.dimensions
            for component in dimension.components
        ]
        _unique("signal within model specification", signal_keys)
        for dimension in self.dimensions:
            _positive_normalized(
                [component.weight for component in dimension.components],
                f"{self.key}/{dimension.key}",
            )
        return self


def expand_model_specifications(
    catalog: ModelCatalog, signal_keys: Sequence[str]
) -> tuple[ExpandedModelSpecification, ...]:
    ordered_signals = tuple(sorted(signal_keys))
    _unique("published signal key", ordered_signals)
    single_expected = next(
        item.expected_count for item in catalog.generation_rules if item.key == "single_signal"
    )
    if len(ordered_signals) != single_expected:
        raise ValueError(
            "Model catalog expects "
            f"{single_expected} published Signals, found {len(ordered_signals)}"
        )
    dimensions = tuple(catalog.representative_dimensions)
    referenced = {component for item in dimensions for component in item.components}
    unknown = sorted(referenced.difference(ordered_signals))
    if unknown:
        raise ValueError(f"Model catalog references unpublished Signals: {unknown}")

    specifications: list[ExpandedModelSpecification] = []
    for signal_key in ordered_signals:
        specifications.append(
            ExpandedModelSpecification(
                key=f"single_signal__{signal_key}",
                specification_type="single_signal",
                method="weighted_mean",
                tie_output="not_applicable",
                output_type="continuous_score",
                dimensions=(
                    ExpandedDimension(
                        key="single_signal",
                        method="weighted_mean",
                        input_transform="identity",
                        weight=1.0,
                        components=(
                            ExpandedComponent(
                                signal_key=signal_key,
                                input_transform="identity",
                                weight=1.0,
                            ),
                        ),
                    ),
                ),
            )
        )
    for size in range(1, len(dimensions) + 1):
        for subset in combinations(dimensions, size):
            specifications.append(
                ExpandedModelSpecification(
                    key="dimension_equal_weight__" + "+".join(item.key for item in subset),
                    specification_type="dimension_subset_equal_weight",
                    method="weighted_mean",
                    tie_output="not_applicable",
                    output_type="continuous_score",
                    dimensions=tuple(
                        _expanded_dimension(item, weight=1.0 / size, input_transform="identity")
                        for item in subset
                    ),
                )
            )
    by_key = {item.key: item for item in dimensions}
    for fixed_specification in catalog.fixed_weight_specifications:
        specifications.append(
            ExpandedModelSpecification(
                key=fixed_specification.key,
                specification_type="fixed_weight",
                method=fixed_specification.method,
                tie_output="not_applicable",
                output_type="continuous_score",
                dimensions=tuple(
                    _expanded_dimension(by_key[key], weight=weight, input_transform="identity")
                    for key, weight in fixed_specification.dimension_weights.items()
                ),
            )
        )
    for vote_specification in catalog.vote_specifications:
        total = sum(vote_specification.dimension_weights.values())
        specifications.append(
            ExpandedModelSpecification(
                key=vote_specification.key,
                specification_type="directional_vote",
                method=vote_specification.method,
                tie_output=vote_specification.tie_output,
                output_type="directional_score",
                dimensions=tuple(
                    _expanded_dimension(by_key[key], weight=weight / total, input_transform="sign")
                    for key, weight in vote_specification.dimension_weights.items()
                ),
            )
        )
    if len(specifications) != catalog.expected_counts.concrete_model_specifications:
        raise ValueError("Expanded model count does not match the catalog contract")
    _unique("expanded model specification key", [item.key for item in specifications])
    return tuple(specifications)


def _expanded_dimension(
    seed: RepresentativeDimensionSeed, *, weight: float, input_transform: InputTransform
) -> ExpandedDimension:
    return ExpandedDimension(
        key=seed.key,
        method=seed.method,
        input_transform=input_transform,
        weight=weight,
        components=tuple(
            ExpandedComponent(signal_key=key, input_transform="identity", weight=component_weight)
            for key, component_weight in zip(seed.components, seed.weights, strict=True)
        ),
    )


def _positive_normalized(weights: Sequence[float], label: str) -> None:
    if not weights or any(weight <= 0 for weight in weights):
        raise ValueError(f"{label} weights must all be positive")
    if abs(sum(weights) - 1.0) > 1e-12:
        raise ValueError(f"{label} weights must sum to one")


def _weight_mapping(
    weights: dict[str, float], expected: set[str], label: str, *, normalized: bool
) -> None:
    if set(weights) != expected:
        raise ValueError(f"{label} must contain every representative dimension exactly once")
    if any(weight <= 0 for weight in weights.values()):
        raise ValueError(f"{label} weights must all be positive")
    if normalized:
        _positive_normalized(list(weights.values()), label)


def _unique(label: str, values: Sequence[object]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")
