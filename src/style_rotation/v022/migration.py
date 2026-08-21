from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.contracts import Fingerprint, Key, SemVer, StrictModel

MigrationStatus = Literal[
    "unmapped",
    "mapped",
    "catalog_validated",
    "executable",
    "oracle_bound",
    "parity_passed",
    "research_reviewed",
    "release_approved",
    "blocked",
]


class LegacyOracleOutput(StrictModel):
    artifact_id: UUID
    semantic_fingerprint: Fingerprint
    content_hash: Fingerprint
    bundle_key: Key
    bundle_version: int = Field(ge=1)
    universe_key: Key
    universe_version: int = Field(ge=1)
    engine_key: Key
    engine_version: int = Field(ge=1)
    coverage_start: str
    coverage_end: str
    row_count: int = Field(ge=0)


class V022MigrationMapping(StrictModel):
    family_key: Key
    variant_key: Key
    origin_stage: int = Field(ge=1, le=3)
    selectable_stage: Literal[3] = 3
    mapping_kind: Literal["legacy_parity_node_output"] = "legacy_parity_node_output"


class MigrationRegistryRecord(StrictModel):
    component_kind: Literal["factor_variant", "signal_version"]
    legacy_key: Key
    legacy_family_key: Key
    legacy_recipe: dict[str, Any]
    mapping: V022MigrationMapping
    oracle_outputs: list[LegacyOracleOutput] = Field(min_length=1)
    status: MigrationStatus
    parity_evidence_artifact_id: UUID | None = None
    blocked_reason: str | None = None

    @model_validator(mode="after")
    def validate_status_evidence(self) -> MigrationRegistryRecord:
        if self.status == "blocked" and not self.blocked_reason:
            raise ValueError("Blocked migration requires blocked_reason")
        if self.status != "blocked" and self.blocked_reason is not None:
            raise ValueError("Only blocked migration may declare blocked_reason")
        passed = {"parity_passed", "research_reviewed", "release_approved"}
        if self.status in passed and self.parity_evidence_artifact_id is None:
            raise ValueError("Parity-passed migration requires independent Evidence")
        return self


class MigrationRegistry(StrictModel):
    catalog_type: Literal["v022_migration_registry"]
    registry_version: SemVer
    contract_version: Literal["v0.22.0"]
    oracle_baseline_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,159}$")
    factor_catalog_fingerprint: Fingerprint
    signal_catalog_fingerprint: Fingerprint
    oracle_manifest_fingerprint: Fingerprint
    records: list[MigrationRegistryRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_inventory(self) -> MigrationRegistry:
        identities = [(item.component_kind, item.legacy_key) for item in self.records]
        if len(identities) != len(set(identities)):
            raise ValueError("Duplicate legacy migration identity")
        mappings = [
            (item.component_kind, item.mapping.variant_key) for item in self.records
        ]
        if len(mappings) != len(set(mappings)):
            raise ValueError("Duplicate v0.22 migration mapping")
        counts = {
            kind: sum(item.component_kind == kind for item in self.records)
            for kind in ("factor_variant", "signal_version")
        }
        if counts != {"factor_variant": 28, "signal_version": 51}:
            raise ValueError(f"Migration Registry count mismatch: {counts}")
        return self


def load_migration_registry(
    path: Path,
    *,
    factor_catalog_path: Path = Path("v0.2/catalogs/factors.v0.2.0.json"),
    signal_catalog_path: Path = Path("v0.2/catalogs/signals.v0.2.0.json"),
    oracle_manifest_path: Path = Path(
        "v0.22/m0/v021-baseline-manifest.v0.22.0.json"
    ),
) -> MigrationRegistry:
    document = _read_json(path)
    factors = _read_json(factor_catalog_path)
    signals = _read_json(signal_catalog_path)
    oracle = _read_json(oracle_manifest_path)
    registry = MigrationRegistry.model_validate(document)
    expected_factors = {
        variant["key"]: definition["key"]
        for definition in factors["definitions"]
        for variant in definition["variants"]
    }
    expected_signals = {
        f'{template["key"]}__{variant_key}': template["key"]
        for template in signals["templates"]
        for variant_key in template["factor_variants"]
    }
    _validate_source_fingerprint(
        "Factor Catalog", registry.factor_catalog_fingerprint, factors
    )
    _validate_source_fingerprint(
        "Signal Catalog", registry.signal_catalog_fingerprint, signals
    )
    _validate_source_fingerprint(
        "Oracle manifest", registry.oracle_manifest_fingerprint, oracle
    )
    _validate_records(registry, "factor_variant", expected_factors)
    _validate_records(registry, "signal_version", expected_signals)
    if registry.oracle_baseline_id != oracle["baseline_id"]:
        raise ValueError("Migration Registry references the wrong Oracle baseline")
    _validate_oracles(registry, oracle)
    return registry


def migration_registry_summary(registry: MigrationRegistry) -> dict[str, Any]:
    status_counts = {
        status: sum(item.status == status for item in registry.records)
        for status in sorted({item.status for item in registry.records})
    }
    return {
        "registry_version": registry.registry_version,
        "factor_variant_count": sum(
            item.component_kind == "factor_variant" for item in registry.records
        ),
        "signal_version_count": sum(
            item.component_kind == "signal_version" for item in registry.records
        ),
        "oracle_binding_count": sum(
            len(item.oracle_outputs) for item in registry.records
        ),
        "status_counts": status_counts,
        "registry_fingerprint": sha256_hexdigest(registry.model_dump(mode="json")),
    }


def _validate_records(
    registry: MigrationRegistry,
    component_kind: Literal["factor_variant", "signal_version"],
    expected: dict[str, str],
) -> None:
    actual = {
        item.legacy_key: item.legacy_family_key
        for item in registry.records
        if item.component_kind == component_kind
    }
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        drift = sorted(
            key for key in set(actual) & set(expected) if actual[key] != expected[key]
        )
        raise ValueError(
            f"{component_kind} coverage mismatch: missing={missing}, extra={extra}, "
            f"family_drift={drift}"
        )


def _validate_oracles(registry: MigrationRegistry, oracle: dict[str, Any]) -> None:
    expected: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
    groups = (
        ("factor_variant", "factor_datasets", "variant_key"),
        ("signal_version", "signal_datasets", "signal_key"),
    )
    for kind, group, key_field in groups:
        for output in oracle["oracle_outputs"][group]:
            expected.setdefault((kind, output[key_field]), set()).add(
                (
                    output["artifact_id"],
                    output["semantic_fingerprint"],
                    output["content_hash"],
                )
            )
    for record in registry.records:
        actual = {
            (
                str(output.artifact_id),
                output.semantic_fingerprint,
                output.content_hash,
            )
            for output in record.oracle_outputs
        }
        if actual != expected.get((record.component_kind, record.legacy_key), set()):
            raise ValueError(f"Oracle binding drift for {record.legacy_key}")


def _validate_source_fingerprint(label: str, expected: str, document: Any) -> None:
    if sha256_hexdigest(document) != expected:
        raise ValueError(f"{label} fingerprint drift")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Migration input does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid migration JSON {path}: {error}") from error
