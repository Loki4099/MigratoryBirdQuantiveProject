from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

AssetMaturity = Literal[
    "cataloged",
    "reference_data",
    "canonical_ready",
    "research_ready",
    "strategy_ready",
    "product_eligible_input",
]
Tradability = Literal["tradable", "reference_only", "synthetic"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AssetCategorySeed(StrictModel):
    key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=600)


class SecuritySeed(StrictModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=300)
    symbol: str = Field(min_length=1, max_length=40)
    aliases: tuple[str, ...] = ()
    category: str
    asset_class: str = Field(min_length=1, max_length=80)
    instrument_type: str = Field(min_length=1, max_length=80)
    tradability: Tradability
    venue_mic: str | None = Field(default=None, pattern=r"^[A-Z0-9]{4}$")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    calendar_key: str | None = None
    tags: frozenset[str] = Field(min_length=1)
    maturity: AssetMaturity
    target_maturity: AssetMaturity
    missing_requirements: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_tradability(self) -> SecuritySeed:
        if self.tradability == "tradable" and (
            self.venue_mic is None or self.currency is None or self.calendar_key is None
        ):
            raise ValueError("Tradable securities require venue, currency, and calendar")
        if self.tradability == "reference_only" and self.maturity in {
            "strategy_ready",
            "product_eligible_input",
        }:
            raise ValueError("Reference-only objects cannot enter a Strategy portfolio")
        if self.tradability == "synthetic" and self.maturity == "product_eligible_input":
            raise ValueError("Synthetic objects cannot be tradable Product inputs")
        if self.maturity != self.target_maturity and not self.missing_requirements:
            raise ValueError("A maturity gap requires explicit missing requirements")
        return self


class AssetSetSeed(StrictModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=240)
    set_type: Literal["fixed", "dynamic_methodology", "defensive_basket"]
    member_keys: tuple[str, ...] = ()
    maturity: AssetMaturity
    formal_eligible: bool
    notes: str = Field(min_length=1, max_length=800)


class AssetRegistryCatalog(StrictModel):
    catalog_type: Literal["asset_registry_v021"]
    catalog_version: str = Field(pattern=r"^0\.21\.\d+$")
    as_of_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    categories: tuple[AssetCategorySeed, ...] = Field(min_length=1)
    securities: tuple[SecuritySeed, ...] = Field(min_length=1)
    asset_sets: tuple[AssetSetSeed, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> AssetRegistryCatalog:
        _unique("category key", [item.key for item in self.categories])
        _unique("security key", [item.key for item in self.securities])
        _unique("security symbol", [item.symbol.upper() for item in self.securities])
        _unique("asset set key", [item.key for item in self.asset_sets])
        categories = {item.key for item in self.categories}
        unknown_categories = {item.category for item in self.securities}.difference(categories)
        if unknown_categories:
            raise ValueError(f"Securities reference unknown categories: {unknown_categories}")
        securities = {item.key for item in self.securities}
        for asset_set in self.asset_sets:
            unknown_members = set(asset_set.member_keys).difference(securities)
            if unknown_members:
                raise ValueError(
                    f"Asset set {asset_set.key} references unknown members: {unknown_members}"
                )
            if asset_set.set_type == "fixed" and not asset_set.member_keys:
                raise ValueError(f"Fixed Asset Set {asset_set.key} requires members")
        return self


def load_asset_registry(path: Path) -> AssetRegistryCatalog:
    return AssetRegistryCatalog.model_validate_json(path.read_text(encoding="utf-8"))


def searchable_document(item: SecuritySeed) -> str:
    values = (
        item.key,
        item.name,
        item.symbol,
        *item.aliases,
        item.category,
        item.asset_class,
        item.instrument_type,
        *sorted(item.tags),
    )
    return " ".join(values).casefold()


def catalog_json_schema() -> dict[str, object]:
    """Expose the exact code-side contract for catalog tooling and editors."""

    return cast(
        dict[str, object],
        json.loads(json.dumps(AssetRegistryCatalog.model_json_schema())),
    )


def _unique(label: str, values: list[object]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")
