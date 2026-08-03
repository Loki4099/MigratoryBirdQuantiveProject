from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalendarSeed(StrictModel):
    key: str
    name: str
    timezone: str
    venue_mic: str = Field(pattern=r"^[A-Z0-9]{4}$")


class ClassificationValueSeed(StrictModel):
    key: str
    label_key: str


class ClassificationSchemeSeed(StrictModel):
    key: str
    name: str
    values: list[ClassificationValueSeed]


class ListingSeed(StrictModel):
    key: str
    venue_mic: str = Field(pattern=r"^[A-Z0-9]{4}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    timezone: str
    calendar: str
    symbol: str


class AssetSeed(StrictModel):
    key: str
    name: str
    asset_type: Literal["etf", "equity", "fund", "index", "commodity"]
    listing: ListingSeed
    classifications: dict[str, str]


class UniverseMemberSeed(StrictModel):
    asset: str
    role: Literal["candidate", "benchmark", "auxiliary_tradable"]
    ordinal: int = Field(ge=0)


class UniverseSeed(StrictModel):
    key: str
    name: str
    description: str
    version_number: int = Field(ge=1)
    members: list[UniverseMemberSeed]


class RequirementSeed(StrictModel):
    key: str
    subject: Literal["universe_candidate", "universe_benchmark", "reference_series", "calendar"]
    series_key: str
    fields: list[str] = Field(min_length=1)
    interval_unit: str
    interval_count: int = Field(ge=1)
    calendar_type: str
    session_type: str
    timestamp_semantics: str


class RequirementSetSeed(StrictModel):
    key: str
    name: str
    description: str
    version_number: int = Field(ge=1)
    requirements: list[RequirementSeed] = Field(min_length=1)


class ResearchScopeCatalog(StrictModel):
    catalog_type: Literal["research_scope"]
    catalog_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    as_of_date: date
    calendar_definitions: list[CalendarSeed] = Field(min_length=1)
    classification_schemes: list[ClassificationSchemeSeed] = Field(min_length=1)
    assets: list[AssetSeed] = Field(min_length=1)
    universe: UniverseSeed
    data_requirement_set: RequirementSetSeed

    @model_validator(mode="after")
    def validate_references(self) -> ResearchScopeCatalog:
        _unique("calendar key", [item.key for item in self.calendar_definitions])
        _unique("classification scheme key", [item.key for item in self.classification_schemes])
        _unique("asset key", [item.key for item in self.assets])
        _unique("listing key", [item.listing.key for item in self.assets])
        _unique("listing symbol", [item.listing.symbol for item in self.assets])
        _unique("universe ordinal", [item.ordinal for item in self.universe.members])
        _unique("universe asset", [item.asset for item in self.universe.members])
        _unique(
            "data requirement key",
            [item.key for item in self.data_requirement_set.requirements],
        )
        if sorted(item.ordinal for item in self.universe.members) != list(
            range(len(self.universe.members))
        ):
            raise ValueError("Universe ordinals must be contiguous from zero")
        asset_keys = {item.key for item in self.assets}
        calendar_keys = {item.key for item in self.calendar_definitions}
        schemes = {
            item.key: {value.key for value in item.values} for item in self.classification_schemes
        }
        for asset in self.assets:
            if asset.listing.calendar not in calendar_keys:
                raise ValueError(f"Unknown listing calendar: {asset.listing.calendar}")
            if set(asset.classifications) != set(schemes):
                raise ValueError(f"Asset {asset.key} must classify every configured scheme")
            for scheme, value in asset.classifications.items():
                if value not in schemes[scheme]:
                    raise ValueError(f"Unknown classification {scheme}:{value}")
        if any(item.asset not in asset_keys for item in self.universe.members):
            raise ValueError("Universe references an unknown asset")
        roles = [item.role for item in self.universe.members]
        if roles.count("benchmark") != 1 or "candidate" not in roles:
            raise ValueError("Universe requires candidates and exactly one product benchmark")
        for requirement in self.data_requirement_set.requirements:
            _unique(f"fields for {requirement.key}", requirement.fields)
        return self


def _unique(label: str, values: Sequence[object]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")
