from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SignalForm = Literal["continuous", "threshold_state", "crossover_event", "recent_event"]
SignalDirection = Literal["higher_is_better", "lower_is_better"]
RuleValue = int | float | str | bool


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignalDefaults(StrictModel):
    missing_policy: Literal["error_after_common_warmup"]
    tie_policy: Literal["average_rank"]
    continuous_normalization: Literal["cross_sectional_centered_rank_-1_1"]


class SignalTemplateSeed(StrictModel):
    key: str = Field(min_length=1, max_length=120)
    factor_variants: list[str] = Field(min_length=1)
    form: SignalForm
    direction: SignalDirection
    rule: dict[str, RuleValue] | None = None
    economic_family: str = Field(min_length=1, max_length=80)
    dimension_hint: str = Field(min_length=1, max_length=80)
    rationale_type: Literal[
        "academic",
        "institutional_research",
        "market_convention",
        "practitioner_hypothesis",
    ]
    rationale: str = Field(min_length=1)
    research_tier: Literal["canonical", "sensitivity", "exploratory"]
    product_eligible: bool

    @model_validator(mode="after")
    def validate_rule(self) -> SignalTemplateSeed:
        _unique("factor variant key within signal template", self.factor_variants)
        if self.form == "continuous":
            if self.rule is not None:
                raise ValueError("Continuous signals cannot define a discrete rule")
            return self
        if self.rule is None:
            raise ValueError(f"{self.form} signals require an explicit rule")
        if self.form == "threshold_state":
            required = {"operator", "threshold", "true_score", "false_score"}
            if set(self.rule) != required:
                raise ValueError(f"Threshold-state rule must contain exactly {sorted(required)}")
            if self.rule["operator"] not in {">", ">=", "<", "<="}:
                raise ValueError("Unsupported threshold-state operator")
        elif self.form == "crossover_event":
            required = {"previous", "current", "event_score", "otherwise"}
            if set(self.rule) != required:
                raise ValueError(f"Crossover rule must contain exactly {sorted(required)}")
            if self.rule["otherwise"] != "neutral":
                raise ValueError("Crossover non-events must be explicitly neutral")
        return self


class SignalCatalog(StrictModel):
    catalog_type: Literal["signal"]
    catalog_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    key_rule: Literal["{template_key}__{factor_variant_key}"]
    defaults: SignalDefaults
    templates: list[SignalTemplateSeed] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> SignalCatalog:
        _unique("signal template key", [item.key for item in self.templates])
        _unique(
            "generated signal key",
            [
                generated_signal_key(item.key, factor_variant_key)
                for item in self.templates
                for factor_variant_key in item.factor_variants
            ],
        )
        return self


def generated_signal_key(template_key: str, factor_variant_key: str) -> str:
    return f"{template_key}__{factor_variant_key}"


def _unique(label: str, values: Sequence[object]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")
