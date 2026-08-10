from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ParameterValue = int | float | str | bool


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactorVariantSeed(StrictModel):
    key: str = Field(min_length=1, max_length=180)
    parameters: dict[str, ParameterValue] = Field(min_length=1)
    required_price_observations: int = Field(ge=1)
    preset_type: Literal["canonical", "horizon_anchor", "sensitivity", "exploratory"]


class FactorDefinitionSeed(StrictModel):
    key: str = Field(min_length=1, max_length=120)
    family: str = Field(min_length=1, max_length=80)
    definition_version: int = Field(default=1, ge=1)
    formula: str = Field(min_length=1)
    inputs: list[
        Literal[
            "open_raw", "high_raw", "low_raw", "close_raw", "volume_raw",
            "open_adj", "high_adj", "low_adj", "close_adj",
        ]
    ] = Field(min_length=1)
    implementation_key: str = Field(min_length=1, max_length=160)
    output_unit: str = Field(default="dimensionless", min_length=1, max_length=80)
    time_semantics: str = Field(default="known_at_session_close", min_length=1, max_length=160)
    variants: list[FactorVariantSeed] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_variants(self) -> FactorDefinitionSeed:
        _unique("variant key", [item.key for item in self.variants])
        parameter_sets = [tuple(sorted(item.parameters.items())) for item in self.variants]
        _unique("variant parameter set", parameter_sets)
        if any(not item.key.startswith(f"{self.key}__") for item in self.variants):
            raise ValueError(f"Factor variant keys must start with {self.key}__")
        return self


class FactorCatalog(StrictModel):
    catalog_type: Literal["factor"]
    catalog_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    definitions: list[FactorDefinitionSeed] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> FactorCatalog:
        _unique("factor key", [item.key for item in self.definitions])
        _unique(
            "factor implementation key",
            [item.implementation_key for item in self.definitions],
        )
        _unique(
            "factor variant key",
            [variant.key for item in self.definitions for variant in item.variants],
        )
        return self


def _unique(label: str, values: Sequence[object]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")
