from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, text

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.migration import (
    LegacyOracleOutput,
    MigrationRegistry,
    MigrationRegistryRecord,
    load_migration_registry,
    migration_registry_summary,
)
from style_rotation.v022.model_compat_runtime import (
    AggregationPoint,
    AggregationSignalPoint,
    LegacyModelCompatibilityRuntime,
)
from style_rotation.v022.model_migration import load_model_migration_registry


@dataclass(frozen=True, slots=True)
class ModelPointComparison:
    oracle_artifact_id: uuid.UUID
    bundle_key: str
    bundle_version: int
    expected_row_count: int
    actual_row_count: int
    matched_row_count: int
    missing_row_count: int
    extra_row_count: int
    score_mismatch_count: int
    direction_mismatch_count: int
    confidence_mismatch_count: int
    max_abs_score_error: str
    passed: bool


class V021ModelParityError(RuntimeError):
    """Raised when Model Oracle identity or point parity cannot be established."""


class V021ModelParityHarness:
    def __init__(
        self,
        engine: Engine,
        model_registry: dict[str, Any],
        signal_registry: MigrationRegistry,
    ) -> None:
        self._engine = engine
        self._model_registry = model_registry
        self._signal_registry = signal_registry
        self._runtime = LegacyModelCompatibilityRuntime(model_registry)

    @classmethod
    def from_registry_paths(
        cls,
        engine: Engine,
        *,
        model_registry_path: Path,
        signal_registry_path: Path,
    ) -> V021ModelParityHarness:
        return cls(
            engine,
            load_model_migration_registry(model_registry_path),
            load_migration_registry(signal_registry_path),
        )

    def build_evidence(self) -> dict[str, Any]:
        comparisons: dict[str, list[ModelPointComparison]] = defaultdict(list)
        contexts = _contexts(self._model_registry)
        signal_records = tuple(
            record
            for record in self._signal_registry.records
            if record.component_kind == "signal_version"
        )
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                database_name = connection.execute(
                    text("SELECT current_database()")
                ).scalar_one()
                if database_name != "style_rotation":
                    raise V021ModelParityError(
                        "Model parity requires the frozen style_rotation Oracle"
                    )
                for context in contexts:
                    signals = {
                        record.legacy_key: _signal_points(
                            connection,
                            record,
                            _signal_oracle(record, context),
                        )
                        for record in signal_records
                    }
                    calculations = {
                        item.legacy_key: item
                        for item in self._runtime.execute_all(signals)
                    }
                    for record in self._model_registry["records"]:
                        oracle = _model_oracle(record, context)
                        expected = _model_points(connection, record, oracle)
                        comparisons[str(record["legacy_key"])].append(
                            compare_model_points(
                                calculations[str(record["legacy_key"])].points,
                                expected,
                                oracle,
                            )
                        )
            finally:
                transaction.rollback()
        records = [
            {
                "legacy_key": record["legacy_key"],
                "family_key": record["mapping"]["family_key"],
                "parameter_preset_key": record["mapping"]["parameter_preset_key"],
                "comparisons": [
                    _comparison_payload(item)
                    for item in comparisons[str(record["legacy_key"])]
                ],
                "passed": all(
                    item.passed
                    for item in comparisons[str(record["legacy_key"])]
                ),
            }
            for record in self._model_registry["records"]
        ]
        passed = all(record["passed"] for record in records)
        document: dict[str, Any] = {
            "evidence_type": "v022_legacy_model_point_parity",
            "evidence_version": "0.22.0",
            "oracle_baseline_id": self._model_registry["oracle_baseline_id"],
            "model_registry_fingerprint": self._model_registry[
                "registry_fingerprint"
            ],
            "signal_registry_fingerprint": migration_registry_summary(
                self._signal_registry
            )["registry_fingerprint"],
            "runtime_contract_fingerprint": self._runtime.runtime_contract_fingerprint,
            "comparison_policy": {
                "input_source": "frozen_v021_signal_dataset_points_already_m4_parity_passed",
                "score": "exact_decimal_1e-18",
                "direction": "exact",
                "confidence": "exact_decimal_1e-18",
                "unexplained_mismatch_allowed": False,
            },
            "records": records,
            "summary": {
                "model_specification_count": len(records),
                "comparison_count": sum(
                    len(record["comparisons"]) for record in records
                ),
                "passed_record_count": sum(record["passed"] for record in records),
                "failed_record_count": sum(not record["passed"] for record in records),
                "passed": passed,
            },
        }
        document["evidence_fingerprint"] = sha256_hexdigest(document)
        return document


def validate_model_parity_evidence(
    evidence: dict[str, Any],
    *,
    model_registry: dict[str, Any],
    signal_registry: MigrationRegistry,
) -> None:
    if evidence.get("evidence_type") != "v022_legacy_model_point_parity":
        raise ValueError("invalid Model parity evidence type")
    if evidence.get("model_registry_fingerprint") != model_registry[
        "registry_fingerprint"
    ]:
        raise ValueError("Model parity evidence Registry drift")
    if evidence.get("signal_registry_fingerprint") != migration_registry_summary(
        signal_registry
    )["registry_fingerprint"]:
        raise ValueError("Model parity evidence Signal Registry drift")
    records = evidence.get("records")
    if not isinstance(records, list) or len(records) != 86:
        raise ValueError("Model parity evidence must contain 86 records")
    expected_keys = {record["legacy_key"] for record in model_registry["records"]}
    if {record["legacy_key"] for record in records} != expected_keys:
        raise ValueError("Model parity evidence coverage drift")
    if any(
        not record["passed"]
        or len(record["comparisons"]) != 2
        or any(not comparison["passed"] for comparison in record["comparisons"])
        for record in records
    ):
        raise ValueError("Model parity evidence contains failed comparisons")
    summary = evidence.get("summary")
    if summary != {
        "model_specification_count": 86,
        "comparison_count": 172,
        "passed_record_count": 86,
        "failed_record_count": 0,
        "passed": True,
    }:
        raise ValueError("Model parity evidence summary drift")
    payload = dict(evidence)
    fingerprint = payload.pop("evidence_fingerprint", None)
    if fingerprint != sha256_hexdigest(payload):
        raise ValueError("Model parity evidence fingerprint drift")


def compare_model_points(
    actual: tuple[AggregationPoint, ...],
    expected: tuple[AggregationPoint, ...],
    oracle: dict[str, Any],
) -> ModelPointComparison:
    actual_by_key = {
        (point.asset_id, point.observation_date): point for point in actual
    }
    expected_by_key = {
        (point.asset_id, point.observation_date): point for point in expected
    }
    shared = set(actual_by_key) & set(expected_by_key)
    score_mismatches = 0
    direction_mismatches = 0
    confidence_mismatches = 0
    max_abs_score_error = Decimal()
    for identity in shared:
        actual_point = actual_by_key[identity]
        expected_point = expected_by_key[identity]
        error = abs(actual_point.score - expected_point.score)
        max_abs_score_error = max(max_abs_score_error, error)
        score_mismatches += actual_point.score != expected_point.score
        direction_mismatches += actual_point.direction != expected_point.direction
        confidence_mismatches += actual_point.confidence != expected_point.confidence
    missing = len(set(expected_by_key) - set(actual_by_key))
    extra = len(set(actual_by_key) - set(expected_by_key))
    passed = not any(
        (
            missing,
            extra,
            score_mismatches,
            direction_mismatches,
            confidence_mismatches,
            len(actual_by_key) != oracle["row_count"],
            len(expected_by_key) != oracle["row_count"],
        )
    )
    return ModelPointComparison(
        oracle_artifact_id=uuid.UUID(oracle["artifact_id"]),
        bundle_key=str(oracle["bundle_key"]),
        bundle_version=int(oracle["bundle_version"]),
        expected_row_count=len(expected_by_key),
        actual_row_count=len(actual_by_key),
        matched_row_count=len(shared),
        missing_row_count=missing,
        extra_row_count=extra,
        score_mismatch_count=score_mismatches,
        direction_mismatch_count=direction_mismatches,
        confidence_mismatch_count=confidence_mismatches,
        max_abs_score_error=str(max_abs_score_error),
        passed=passed,
    )


def _contexts(registry: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    contexts = {
        (output["bundle_key"], output["bundle_version"])
        for record in registry["records"]
        for output in record["oracle_outputs"]
    }
    if len(contexts) != 2:
        raise V021ModelParityError(
            f"Expected two frozen Model Oracle contexts, found {len(contexts)}"
        )
    return tuple(sorted(contexts))


def _signal_oracle(
    record: MigrationRegistryRecord, context: tuple[str, int]
) -> LegacyOracleOutput:
    matches = tuple(
        oracle
        for oracle in record.oracle_outputs
        if (oracle.bundle_key, oracle.bundle_version) == context
    )
    if len(matches) != 1:
        raise V021ModelParityError(
            f"Missing Signal Oracle for {record.legacy_key} in {context}"
        )
    return matches[0]


def _model_oracle(record: dict[str, Any], context: tuple[str, int]) -> dict[str, Any]:
    matches = tuple(
        oracle
        for oracle in record["oracle_outputs"]
        if (oracle["bundle_key"], oracle["bundle_version"]) == context
    )
    if len(matches) != 1:
        raise V021ModelParityError(
            f"Missing Model Oracle for {record['legacy_key']} in {context}"
        )
    return dict(matches[0])


def _signal_points(
    connection: Connection,
    record: MigrationRegistryRecord,
    oracle: LegacyOracleOutput,
) -> tuple[AggregationSignalPoint, ...]:
    row = (
        connection.execute(
            text(
                "SELECT dataset.signal_dataset_id, artifact.status, "
                "artifact.semantic_fingerprint, artifact.content_hash, "
                "definition.signal_key, bundle_definition.bundle_key, "
                "bundle.version_number bundle_version FROM signal.signal_dataset dataset "
                "JOIN lineage.artifact artifact ON artifact.artifact_id=dataset.artifact_id "
                "JOIN signal.signal_version version ON "
                "version.signal_version_id=dataset.signal_version_id "
                "JOIN signal.signal_definition definition ON "
                "definition.signal_definition_id=version.signal_definition_id "
                "JOIN data.data_bundle_version bundle ON "
                "bundle.data_bundle_version_id=dataset.data_bundle_version_id "
                "JOIN data.data_bundle_definition bundle_definition ON "
                "bundle_definition.data_bundle_definition_id="
                "bundle.data_bundle_definition_id WHERE dataset.artifact_id=:artifact"
            ),
            {"artifact": oracle.artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or (
        row["status"],
        row["semantic_fingerprint"],
        row["content_hash"],
        row["signal_key"],
        row["bundle_key"],
        row["bundle_version"],
    ) != (
        "published",
        oracle.semantic_fingerprint,
        oracle.content_hash,
        record.legacy_key,
        oracle.bundle_key,
        oracle.bundle_version,
    ):
        raise V021ModelParityError(f"Frozen Signal Oracle drift: {record.legacy_key}")
    points = connection.execute(
        text(
            "SELECT value.asset_id, asset.asset_key, value.observation_date, value.score "
            "FROM signal.signal_value value JOIN catalog.asset asset "
            "ON asset.asset_id=value.asset_id WHERE value.signal_dataset_id=:dataset "
            "ORDER BY asset.asset_key,value.observation_date,value.asset_id"
        ),
        {"dataset": row["signal_dataset_id"]},
    ).mappings().all()
    return tuple(
        AggregationSignalPoint(
            point["asset_id"],
            str(point["asset_key"]),
            point["observation_date"],
            Decimal(point["score"]),
        )
        for point in points
    )


def _model_points(
    connection: Connection,
    record: dict[str, Any],
    oracle: dict[str, Any],
) -> tuple[AggregationPoint, ...]:
    row = (
        connection.execute(
            text(
                "SELECT dataset.model_dataset_id,dataset.input_set_hash,"
                "dataset.coverage_start,dataset.coverage_end,dataset.row_count,"
                "artifact.status,artifact.semantic_fingerprint,artifact.content_hash,"
                "specification.specification_key,bundle_definition.bundle_key,"
                "bundle.version_number bundle_version,universe_definition.universe_key,"
                "universe.version_number universe_version,engine_definition.engine_key,"
                "engine.version_number engine_version FROM model.model_dataset dataset "
                "JOIN lineage.artifact artifact ON artifact.artifact_id=dataset.artifact_id "
                "JOIN model.model_specification specification ON "
                "specification.model_specification_id=dataset.model_specification_id "
                "JOIN data.data_bundle_version bundle ON "
                "bundle.data_bundle_version_id=dataset.data_bundle_version_id "
                "JOIN data.data_bundle_definition bundle_definition ON "
                "bundle_definition.data_bundle_definition_id="
                "bundle.data_bundle_definition_id JOIN catalog.universe_version universe ON "
                "universe.universe_version_id=dataset.universe_version_id "
                "JOIN catalog.universe_definition universe_definition ON "
                "universe_definition.universe_definition_id="
                "universe.universe_definition_id JOIN ops.engine_version engine ON "
                "engine.engine_version_id=dataset.engine_version_id "
                "JOIN ops.engine_definition engine_definition ON "
                "engine_definition.engine_definition_id=engine.engine_definition_id "
                "WHERE dataset.artifact_id=:artifact"
            ),
            {"artifact": oracle["artifact_id"]},
        )
        .mappings()
        .one_or_none()
    )
    expected = (
        "published",
        oracle["semantic_fingerprint"],
        oracle["content_hash"],
        record["legacy_key"],
        oracle["bundle_key"],
        oracle["bundle_version"],
        oracle["universe_key"],
        oracle["universe_version"],
        oracle["engine_key"],
        oracle["engine_version"],
        date.fromisoformat(oracle["coverage_start"]),
        date.fromisoformat(oracle["coverage_end"]),
        oracle["row_count"],
        oracle["input_set_hash"],
    )
    if row is None or (
        row["status"],
        row["semantic_fingerprint"],
        row["content_hash"],
        row["specification_key"],
        row["bundle_key"],
        row["bundle_version"],
        row["universe_key"],
        row["universe_version"],
        row["engine_key"],
        row["engine_version"],
        row["coverage_start"],
        row["coverage_end"],
        row["row_count"],
        row["input_set_hash"],
    ) != expected:
        raise V021ModelParityError(f"Frozen Model Oracle drift: {record['legacy_key']}")
    points = connection.execute(
        text(
            "SELECT value.asset_id,asset.asset_key,value.observation_date,value.score,"
            "value.direction,value.confidence FROM model.model_value value "
            "JOIN catalog.asset asset ON asset.asset_id=value.asset_id "
            "WHERE value.model_dataset_id=:dataset "
            "ORDER BY asset.asset_key,value.observation_date,value.asset_id"
        ),
        {"dataset": row["model_dataset_id"]},
    ).mappings().all()
    return tuple(
        AggregationPoint(
            point["asset_id"],
            str(point["asset_key"]),
            point["observation_date"],
            Decimal(point["score"]),
            str(point["direction"]),
            Decimal(point["confidence"]),
        )
        for point in points
    )


def _comparison_payload(comparison: ModelPointComparison) -> dict[str, Any]:
    return {**asdict(comparison), "oracle_artifact_id": str(comparison.oracle_artifact_id)}
