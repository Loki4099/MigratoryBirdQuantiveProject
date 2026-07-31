from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from style_rotation.domain.enums import FactorDirection


@dataclass(frozen=True, slots=True)
class FactorDefinitionSpec:
    key: str
    family: str
    name: str
    description: str
    formula: str
    required_fields: tuple[str, ...]
    direction: FactorDirection
    implementation_key: str


@dataclass(frozen=True, slots=True)
class FactorVariantSpec:
    key: str
    definition_key: str
    parameters: dict[str, int]
    minimum_observations: int


@dataclass(frozen=True, slots=True)
class FactorPoint:
    variant_key: str
    symbol: str
    trade_date: date
    raw_value: Decimal


@dataclass(frozen=True, slots=True)
class FactorComputationResult:
    points: tuple[FactorPoint, ...]
    common_valid_start: date
    coverage_end: date
    content_hash: str


@dataclass(frozen=True, slots=True)
class FactorPublicationOutcome:
    factor_version_id: uuid.UUID
    reused: bool
    factor_value_rows: int
    common_valid_start: date
