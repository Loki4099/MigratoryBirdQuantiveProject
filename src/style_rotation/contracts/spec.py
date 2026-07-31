from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from style_rotation.core.canonical import sha256_hexdigest


class ContractLayer(StrEnum):
    RAW = "raw"
    CLEAN = "clean"
    FACTOR = "factor"
    SIGNAL = "signal"
    BACKTEST = "backtest"
    METRICS = "metrics"


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    data_type: str
    nullable: bool
    description: str
    unit: str | None = None
    time_semantics: str | None = None


@dataclass(frozen=True, slots=True)
class DataContractSpec:
    layer: ContractLayer
    name: str
    schema_version: str
    primary_key: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    quality_rules: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        names = [item.name for item in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("Contract field names must be unique")
        missing_keys = set(self.primary_key).difference(names)
        if missing_keys:
            raise ValueError(f"Primary-key fields are not defined: {sorted(missing_keys)}")
        nullable_keys = [
            item.name for item in self.fields if item.name in self.primary_key and item.nullable
        ]
        if nullable_keys:
            raise ValueError(f"Primary-key fields cannot be nullable: {nullable_keys}")

    @property
    def contract_hash(self) -> str:
        return sha256_hexdigest(self)
