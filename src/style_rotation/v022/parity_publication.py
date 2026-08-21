from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy import Connection, Engine, bindparam, text

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.legacy_compat_runtime import LegacyCompatibilityRuntime
from style_rotation.v022.migration import (
    MigrationRegistry,
    load_migration_registry,
    migration_registry_summary,
)

COMPARATOR_VERSION = "v022-point-parity-v1"


@dataclass(frozen=True, slots=True)
class ParityEvidencePublication:
    migration_registry_id: uuid.UUID
    registry_artifact_id: uuid.UUID
    registry_reused: bool
    evidence_artifact_ids: tuple[uuid.UUID, ...]
    reused_evidence_count: int
    source_registry_fingerprint: str
    evidence_document_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["migration_registry_id"] = str(self.migration_registry_id)
        payload["registry_artifact_id"] = str(self.registry_artifact_id)
        payload["evidence_artifact_ids"] = [
            str(item) for item in self.evidence_artifact_ids
        ]
        return payload


def publish_parity_evidence(
    engine: Engine,
    registry_path: Path,
    evidence_path: Path,
    *,
    catalog_release_artifact_id: uuid.UUID,
) -> ParityEvidencePublication:
    registry = load_migration_registry(registry_path)
    evidence = load_and_validate_parity_evidence(evidence_path, registry)
    registry_fingerprint = migration_registry_summary(registry)["registry_fingerprint"]
    evidence_fingerprint = str(evidence["evidence_fingerprint"])
    with engine.begin() as connection:
        release_id, mapped_components = _catalog_context(
            connection,
            catalog_release_artifact_id,
            tuple(item.mapping.variant_key for item in registry.records),
        )
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        evidence_results = []
        evidence_ids = []
        records_by_key = {
            (item.component_kind, item.legacy_key): item for item in registry.records
        }
        for record in evidence["records"]:
            component_kind = cast(
                Literal["factor_variant", "signal_version"],
                str(record["component_kind"]),
            )
            identity = (component_kind, str(record["legacy_key"]))
            migration_record = records_by_key[identity]
            mapped_key = migration_record.mapping.variant_key
            mapped_artifact_id = mapped_components[mapped_key]
            comparison_fingerprint = sha256_hexdigest(record)
            result = service.publish(
                artifact_type="v022_parity_evidence",
                artifact_key=f"{identity[0]}:{identity[1]}",
                version_number=semantic_version_number(str(evidence["evidence_version"])),
                semantic_payload={
                    "oracle_baseline_id": registry.oracle_baseline_id,
                    "source_registry_fingerprint": registry_fingerprint,
                    "evidence_document_fingerprint": evidence_fingerprint,
                    "component_kind": identity[0],
                    "legacy_key": identity[1],
                    "mapped_variant_key": mapped_key,
                    "comparator_version": COMPARATOR_VERSION,
                    "comparison_policy": evidence["comparison_policy"],
                },
                content_payload={
                    "evidence_record_id": record["evidence_record_id"],
                    "comparison_fingerprint": comparison_fingerprint,
                    "comparisons": record["comparisons"],
                    "passed": True,
                },
                dependencies=(
                    DependencyInput(
                        catalog_release_artifact_id, "catalog_release", 0
                    ),
                    DependencyInput(mapped_artifact_id, "mapped_component", 1),
                ),
                reason=f"publish v0.22 parity Evidence for {identity[1]}",
                draft_writer=partial(
                    _write_evidence,
                    release_id=release_id,
                    mapped_component_artifact_id=mapped_artifact_id,
                    source_registry_fingerprint=registry_fingerprint,
                    evidence_document_fingerprint=evidence_fingerprint,
                    record=record,
                ),
            )
            evidence_results.append(result)
            evidence_ids.append(
                cast(
                    uuid.UUID,
                    connection.scalar(
                        text(
                            "SELECT parity_evidence_id "
                            "FROM compatibility.v022_parity_evidence "
                            "WHERE artifact_id=:artifact"
                        ),
                        {"artifact": result.artifact_id},
                    ),
                )
            )
        registry_result = service.publish(
            artifact_type="v022_migration_registry",
            artifact_key=registry.oracle_baseline_id,
            version_number=semantic_version_number(str(registry.registry_version)),
            semantic_payload={
                "registry_version": str(registry.registry_version),
                "source_registry_fingerprint": registry_fingerprint,
                "oracle_baseline_id": registry.oracle_baseline_id,
                "evidence_document_fingerprint": evidence_fingerprint,
                "runtime_contract_fingerprint": evidence[
                    "runtime_contract_fingerprint"
                ],
                "migration_status": "parity_passed",
            },
            content_payload={
                "summary": evidence["summary"],
                "evidence_artifacts": [
                    {
                        "component_kind": record["component_kind"],
                        "legacy_key": record["legacy_key"],
                        "artifact_id": str(result.artifact_id),
                        "semantic_fingerprint": result.semantic_fingerprint,
                        "content_hash": result.content_hash,
                    }
                    for record, result in zip(
                        evidence["records"], evidence_results, strict=True
                    )
                ],
            },
            dependencies=(
                DependencyInput(catalog_release_artifact_id, "catalog_release", 0),
                *tuple(
                    DependencyInput(result.artifact_id, "parity_evidence", ordinal + 1)
                    for ordinal, result in enumerate(evidence_results)
                ),
            ),
            reason="publish v0.22 Factor/Signal parity Migration Registry",
            draft_writer=partial(
                _write_registry,
                release_id=release_id,
                registry=registry,
                source_registry_fingerprint=registry_fingerprint,
                evidence=evidence,
                evidence_ids=tuple(evidence_ids),
            ),
        )
        migration_registry_id = cast(
            uuid.UUID,
            connection.scalar(
                text(
                    "SELECT migration_registry_id "
                    "FROM compatibility.v022_migration_registry "
                    "WHERE artifact_id=:artifact"
                ),
                {"artifact": registry_result.artifact_id},
            ),
        )
        _verify_publication(
            connection,
            migration_registry_id,
            registry_result.artifact_id,
            registry_fingerprint,
            evidence_fingerprint,
        )
    return ParityEvidencePublication(
        migration_registry_id=migration_registry_id,
        registry_artifact_id=registry_result.artifact_id,
        registry_reused=registry_result.reused,
        evidence_artifact_ids=tuple(item.artifact_id for item in evidence_results),
        reused_evidence_count=sum(item.reused for item in evidence_results),
        source_registry_fingerprint=registry_fingerprint,
        evidence_document_fingerprint=evidence_fingerprint,
    )


def load_and_validate_parity_evidence(
    path: Path, registry: MigrationRegistry
) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    provided_fingerprint = document.get("evidence_fingerprint")
    fingerprint_payload = {
        key: value for key, value in document.items() if key != "evidence_fingerprint"
    }
    if provided_fingerprint != sha256_hexdigest(fingerprint_payload):
        raise ValueError("Parity Evidence document fingerprint drift")
    registry_fingerprint = migration_registry_summary(registry)["registry_fingerprint"]
    if document.get("registry_fingerprint") != registry_fingerprint:
        raise ValueError("Parity Evidence references the wrong Migration Registry")
    if document.get("registry_version") != str(registry.registry_version):
        raise ValueError("Parity Evidence Registry version drift")
    if document.get("oracle_baseline_id") != registry.oracle_baseline_id:
        raise ValueError("Parity Evidence Oracle baseline drift")
    if document.get("evidence_type") != "v022_legacy_point_parity":
        raise ValueError("Unsupported Parity Evidence type")
    if document.get("evidence_version") != "0.22.0":
        raise ValueError("Unsupported Parity Evidence version")
    if document.get("runtime_contract_fingerprint") != (
        LegacyCompatibilityRuntime(registry).runtime_contract_fingerprint
    ):
        raise ValueError("Parity Evidence runtime contract drift")
    if document.get("comparison_policy") != {
        "expected_values_source": "frozen_v021_database_artifact_points",
        "float_abs_rel_tolerance": "9.9999999999999998e-13",
        "decimal_quantum": "1E-18",
        "missing_reason_comparison": "legacy_unknown_not_claimed",
        "unexplained_mismatch_allowed": False,
    }:
        raise ValueError("Parity Evidence comparison policy drift")
    summary = document.get("summary")
    if summary != {
        "factor_variant_count": 28,
        "signal_version_count": 51,
        "comparison_count": 158,
        "passed_record_count": 79,
        "failed_record_count": 0,
        "passed": True,
    }:
        raise ValueError("Parity Evidence summary is not a complete passing M4 result")
    records = document.get("records")
    if not isinstance(records, list) or len(records) != 79:
        raise ValueError("Parity Evidence must contain 79 concrete records")
    expected = {
        (item.component_kind, item.legacy_key): item for item in registry.records
    }
    actual: dict[tuple[str, str], dict[str, Any]] = {}
    record_ids: set[uuid.UUID] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Parity Evidence record must be an object")
        identity = (str(record.get("component_kind")), str(record.get("legacy_key")))
        if identity in actual or identity not in expected:
            raise ValueError(f"Unknown or duplicate Parity Evidence record: {identity}")
        migration_record = expected[identity]
        if record.get("mapped_variant_key") != migration_record.mapping.variant_key:
            raise ValueError(f"Parity Evidence mapping drift: {identity[1]}")
        if record.get("passed") is not True:
            raise ValueError(f"Parity Evidence record did not pass: {identity[1]}")
        try:
            record_id = uuid.UUID(str(record["evidence_record_id"]))
        except (KeyError, ValueError) as error:
            raise ValueError(f"Invalid Evidence record ID: {identity[1]}") from error
        if record_id in record_ids:
            raise ValueError("Duplicate Evidence record ID")
        record_ids.add(record_id)
        _validate_comparisons(record, migration_record)
        actual[identity] = record
    if set(actual) != set(expected):
        raise ValueError("Parity Evidence inventory does not match Migration Registry")
    return cast(dict[str, Any], document)


def _validate_comparisons(record: dict[str, Any], migration_record: Any) -> None:
    comparisons = record.get("comparisons")
    if not isinstance(comparisons, list) or len(comparisons) != 2:
        raise ValueError(f'Parity Evidence requires two contexts: {record.get("legacy_key")}')
    expected_oracles = {
        str(oracle.artifact_id): oracle for oracle in migration_record.oracle_outputs
    }
    actual_ids = set()
    for comparison in comparisons:
        if not isinstance(comparison, dict):
            raise ValueError("Parity comparison must be an object")
        artifact_id = str(comparison.get("oracle_artifact_id"))
        oracle = expected_oracles.get(artifact_id)
        if oracle is None or artifact_id in actual_ids:
            raise ValueError("Parity comparison Oracle binding drift")
        actual_ids.add(artifact_id)
        required = {
            "expected_content_hash": oracle.content_hash,
            "expected_row_count": oracle.row_count,
            "actual_row_count": oracle.row_count,
            "matched_row_count": oracle.row_count,
            "missing_row_count": 0,
            "extra_row_count": 0,
            "numeric_mismatch_count": 0,
            "state_mismatch_count": 0,
            "event_mismatch_count": 0,
            "passed": True,
        }
        if any(comparison.get(key) != value for key, value in required.items()):
            raise ValueError(f'Parity comparison failed: {record.get("legacy_key")}')
    if actual_ids != set(expected_oracles):
        raise ValueError("Parity comparison does not cover both frozen Oracles")


def _catalog_context(
    connection: Connection,
    release_artifact_id: uuid.UUID,
    mapped_keys: tuple[str, ...],
) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    release_id = connection.scalar(
        text(
            "SELECT release.catalog_release_id FROM workspace.v022_catalog_release release "
            "JOIN lineage.artifact artifact ON artifact.artifact_id=release.artifact_id "
            "WHERE release.artifact_id=:artifact AND artifact.status='published'"
        ),
        {"artifact": release_artifact_id},
    )
    if release_id is None:
        raise ValueError("Published v0.22 Catalog Release is required for parity publication")
    rows = connection.execute(
        text(
            "SELECT component_key, component_artifact_id "
            "FROM workspace.v022_catalog_release_component "
            "WHERE catalog_release_id=:release AND component_kind='feature_variant' "
            "AND component_key IN :keys"
        ).bindparams(bindparam("keys", expanding=True)),
        {"release": release_id, "keys": mapped_keys},
    ).all()
    mapped = {str(row.component_key): row.component_artifact_id for row in rows}
    if set(mapped) != set(mapped_keys):
        raise ValueError("Catalog Release does not contain every migrated Feature Variant")
    return cast(uuid.UUID, release_id), mapped


def _write_evidence(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    release_id: uuid.UUID,
    mapped_component_artifact_id: uuid.UUID,
    source_registry_fingerprint: str,
    evidence_document_fingerprint: str,
    record: dict[str, Any],
) -> None:
    connection.execute(
        text(
            "INSERT INTO compatibility.v022_parity_evidence "
            "(parity_evidence_id,artifact_id,catalog_release_id,"
            "mapped_component_artifact_id,evidence_record_id,source_registry_fingerprint,"
            "evidence_document_fingerprint,component_kind,legacy_key,mapped_variant_key,"
            "comparator_version,comparison_count,comparisons,passed) VALUES "
            "(:id,:artifact,:release,:mapped,:record_id,:registry_fp,:evidence_fp,:kind,"
            ":legacy,:variant,:comparator,2,CAST(:comparisons AS jsonb),true)"
        ),
        {
            "id": uuid.uuid4(),
            "artifact": artifact_id,
            "release": release_id,
            "mapped": mapped_component_artifact_id,
            "record_id": uuid.UUID(str(record["evidence_record_id"])),
            "registry_fp": source_registry_fingerprint,
            "evidence_fp": evidence_document_fingerprint,
            "kind": record["component_kind"],
            "legacy": record["legacy_key"],
            "variant": record["mapped_variant_key"],
            "comparator": COMPARATOR_VERSION,
            "comparisons": _json(record["comparisons"]),
        },
    )


def _write_registry(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    release_id: uuid.UUID,
    registry: MigrationRegistry,
    source_registry_fingerprint: str,
    evidence: dict[str, Any],
    evidence_ids: tuple[uuid.UUID, ...],
) -> None:
    registry_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO compatibility.v022_migration_registry "
            "(migration_registry_id,artifact_id,catalog_release_id,registry_version,"
            "source_registry_fingerprint,oracle_baseline_id,evidence_document_fingerprint,"
            "runtime_contract_fingerprint,migration_status,factor_variant_count,"
            "signal_version_count,comparison_count) VALUES "
            "(:id,:artifact,:release,:version,:registry_fp,:baseline,:evidence_fp,"
            ":runtime_fp,'parity_passed',28,51,158)"
        ),
        {
            "id": registry_id,
            "artifact": artifact_id,
            "release": release_id,
            "version": str(registry.registry_version),
            "registry_fp": source_registry_fingerprint,
            "baseline": registry.oracle_baseline_id,
            "evidence_fp": evidence["evidence_fingerprint"],
            "runtime_fp": evidence["runtime_contract_fingerprint"],
        },
    )
    connection.execute(
        text(
            "INSERT INTO compatibility.v022_migration_registry_member "
            "(migration_registry_id,parity_evidence_id,ordinal,component_kind,legacy_key) "
            "VALUES (:registry,:evidence,:ordinal,:kind,:legacy)"
        ),
        [
            {
                "registry": registry_id,
                "evidence": evidence_id,
                "ordinal": ordinal,
                "kind": record["component_kind"],
                "legacy": record["legacy_key"],
            }
            for ordinal, (evidence_id, record) in enumerate(
                zip(evidence_ids, evidence["records"], strict=True)
            )
        ],
    )


def _verify_publication(
    connection: Connection,
    registry_id: uuid.UUID,
    registry_artifact_id: uuid.UUID,
    registry_fingerprint: str,
    evidence_fingerprint: str,
) -> None:
    row = connection.execute(
        text(
            "SELECT registry.migration_status,registry.factor_variant_count,"
            "registry.signal_version_count,registry.comparison_count,artifact.status,"
            "count(member.parity_evidence_id) member_count,"
            "count(evidence.parity_evidence_id) FILTER (WHERE evidence.passed) passed_count "
            "FROM compatibility.v022_migration_registry registry "
            "JOIN lineage.artifact artifact ON artifact.artifact_id=registry.artifact_id "
            "JOIN compatibility.v022_migration_registry_member member "
            "ON member.migration_registry_id=registry.migration_registry_id "
            "JOIN compatibility.v022_parity_evidence evidence "
            "ON evidence.parity_evidence_id=member.parity_evidence_id "
            "WHERE registry.migration_registry_id=:registry "
            "AND registry.artifact_id=:artifact "
            "AND registry.source_registry_fingerprint=:registry_fp "
            "AND registry.evidence_document_fingerprint=:evidence_fp "
            "GROUP BY registry.migration_registry_id,artifact.status"
        ),
        {
            "registry": registry_id,
            "artifact": registry_artifact_id,
            "registry_fp": registry_fingerprint,
            "evidence_fp": evidence_fingerprint,
        },
    ).one()
    if tuple(row) != ("parity_passed", 28, 51, 158, "published", 79, 79):
        raise ValueError("Published M4 Migration Registry failed completeness verification")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
