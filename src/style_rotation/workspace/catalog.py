from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.factor.contracts import FactorCatalog
from style_rotation.lineage.service import ArtifactService
from style_rotation.signal.contracts import SignalCatalog
from style_rotation.workspace.contracts import ModelPresetDescriptor


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RawFactorSeed(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    family: Literal["raw_market_data"]
    formula: str
    input_field: str
    unit: str


class WorkspaceModelPreset(StrictModel):
    preset_key: str
    output_type: Literal["continuous_score", "directional_score"]
    output_comparability: Literal["cross_sectional", "diagnostic_only"]
    supported_frequencies: frozenset[Literal["weekly", "monthly"]]
    parameters: dict[str, str | bool | int | float]
    target_key: str | None
    input_slots: tuple[dict[str, Any], ...] = Field(min_length=1)

    def descriptor(self, family_key: str) -> ModelPresetDescriptor:
        return ModelPresetDescriptor.model_validate(
            {
                **self.model_dump(mode="json", exclude={"parameters", "target_key"}),
                "family_key": family_key,
            }
        )


class WorkspaceModelFamily(StrictModel):
    key: str
    name: str
    description: str
    implementation_status: Literal["available", "planned"]
    presets: tuple[WorkspaceModelPreset, ...] = Field(min_length=1)


class WorkspaceStrategyFamily(StrictModel):
    key: Literal["multi_etf_top_k", "us_large_cap_top_k"]
    name: str
    description: str
    implementation_status: Literal["available", "planned"]
    required_instrument_type: Literal["Equity ETF", "Common Stock"]
    minimum_eligible_assets: int = Field(ge=2)
    formal_minimum_eligible_assets: int = Field(ge=2)
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    supported_frequencies: frozenset[Literal["weekly", "monthly"]]
    compatible_model_output_types: frozenset[Literal["continuous_score", "directional_score"]]
    parameter_options: dict[str, tuple[str | int, ...]]
    defaults: dict[str, str | int]
    primary_benchmark: str
    research_benchmark: str

    @model_validator(mode="after")
    def validate_parameters(self) -> WorkspaceStrategyFamily:
        required = {"target_k", "defense", "selection_buffer", "sector_cap"}
        if set(self.parameter_options) != required or set(self.defaults) != required:
            raise ValueError("Strategy parameter axes must use the four frozen v0.21 keys")
        for key, default in self.defaults.items():
            if default not in self.parameter_options[key]:
                raise ValueError(f"Strategy default {key} is not a published option")
        k_values = self.parameter_options["target_k"]
        if any(not isinstance(value, int) or value < 1 for value in k_values):
            raise ValueError("Strategy target_k options must be positive integers")
        if self.formal_minimum_eligible_assets < self.minimum_eligible_assets:
            raise ValueError("Formal Strategy threshold cannot be below launch threshold")
        return self


class WorkspaceContractSeed(StrictModel):
    catalog_type: Literal["workspace_contracts_v021"]
    catalog_version: str = Field(pattern=r"^0\.21\.\d+$")
    raw_factors: tuple[RawFactorSeed, ...]
    model_families: tuple[WorkspaceModelFamily, ...]
    strategy_families: tuple[WorkspaceStrategyFamily, ...]

    @model_validator(mode="after")
    def validate_unique_keys(self) -> WorkspaceContractSeed:
        _unique("raw Factor", [item.key for item in self.raw_factors])
        _unique("Model family", [item.key for item in self.model_families])
        _unique("Strategy family", [item.key for item in self.strategy_families])
        _unique(
            "Model preset",
            [preset.preset_key for family in self.model_families for preset in family.presets],
        )
        for family in self.model_families:
            for preset in family.presets:
                preset.descriptor(family.key)
                if family.implementation_status == "planned" and preset.target_key is None:
                    raise ValueError("Planned training Model presets require an explicit target")
        return self


def build_component_document(
    factor_path: Path, signal_path: Path, workspace_path: Path
) -> dict[str, Any]:
    factors = FactorCatalog.model_validate_json(factor_path.read_text(encoding="utf-8"))
    signals = SignalCatalog.model_validate_json(signal_path.read_text(encoding="utf-8"))
    workspace = WorkspaceContractSeed.model_validate_json(
        workspace_path.read_text(encoding="utf-8")
    )
    known_variants = {
        variant.key for definition in factors.definitions for variant in definition.variants
    }
    unknown_signal_inputs = {
        factor_variant
        for template in signals.templates
        for factor_variant in template.factor_variants
        if factor_variant not in known_variants
    }
    if unknown_signal_inputs:
        raise ValueError(
            f"Signal templates reference unknown Factor variants: {unknown_signal_inputs}"
        )
    raw_factors = [
        {
            "key": item.key,
            "family": item.family,
            "formula": item.formula,
            "inputs": [item.input_field],
            "required_asset_input_keys": ["canonical_market_bars"],
            "implementation_key": f"{item.key}_passthrough_v1",
            "output_unit": item.unit,
            "time_semantics": "known_at_session_close",
            "variants": [
                {
                    "key": f"{item.key}__raw",
                    "parameters": {"field": item.input_field},
                    "required_price_observations": 1,
                    "preset_type": "canonical",
                }
            ],
            "raw": True,
        }
        for item in workspace.raw_factors
    ]
    factor_definitions = [
        {
            **item.model_dump(mode="python"),
            "required_asset_input_keys": ["canonical_market_bars"],
            "raw": False,
        }
        for item in factors.definitions
    ]
    document = {
        "catalog_type": "workspace_component_catalog",
        "catalog_version": workspace.catalog_version,
        "factor_source_version": factors.catalog_version,
        "signal_source_version": signals.catalog_version,
        "factor_families": [*raw_factors, *factor_definitions],
        "signal_defaults": signals.defaults.model_dump(mode="python"),
        "signal_templates": [item.model_dump(mode="python") for item in signals.templates],
        "model_families": [item.model_dump(mode="python") for item in workspace.model_families],
        "strategy_families": [
            item.model_dump(mode="python") for item in workspace.strategy_families
        ],
    }
    return cast(dict[str, Any], _canonical_json_value(document))


def publish_component_catalog(
    engine: Engine, *, factor_path: Path, signal_path: Path, workspace_path: Path
) -> dict[str, Any]:
    document = build_component_document(factor_path, signal_path, workspace_path)
    version = str(document["catalog_version"])
    with engine.begin() as transaction:
        service = ArtifactService(cast(Engine, _BoundConnection(transaction)))
        result = service.publish(
            artifact_type="workspace_component_catalog",
            artifact_key="v021_research_components",
            version_number=semantic_version_number(version),
            semantic_payload=document,
            content_payload=document,
            reason=f"publish Workspace component catalog {version}",
            draft_writer=lambda connection, artifact_id: _write_component_catalog(
                connection, artifact_id, version, document
            ),
        )
    output = asdict(result)
    output["artifact_id"] = str(result.artifact_id)
    return {"catalog_type": "workspace_components", **output}


def _write_component_catalog(
    connection: Connection,
    artifact_id: uuid.UUID,
    version: str,
    document: dict[str, Any],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO workspace.component_catalog (
                component_catalog_id, artifact_id, catalog_key, version_number,
                catalog_version, document
            ) VALUES (
                :id, :artifact_id, 'v021_research_components', :version_number,
                :catalog_version, CAST(:document AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "artifact_id": artifact_id,
            "version_number": semantic_version_number(version),
            "catalog_version": version,
            "document": json.dumps(document, ensure_ascii=False),
        },
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)


def _unique(label: str, values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label} key")


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical_json_value(item) for item in value), key=str)
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    return value
