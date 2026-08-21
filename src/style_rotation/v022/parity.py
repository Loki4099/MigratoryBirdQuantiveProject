from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Connection, Engine, bindparam, text

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.factor.calculator import FactorBar, FactorPoint
from style_rotation.signal.calculator import SignalPoint
from style_rotation.v022.legacy_compat_runtime import LegacyCompatibilityRuntime
from style_rotation.v022.migration import (
    LegacyOracleOutput,
    MigrationRegistry,
    MigrationRegistryRecord,
    load_migration_registry,
    migration_registry_summary,
)


@dataclass(frozen=True, slots=True)
class PointComparison:
    oracle_artifact_id: uuid.UUID
    bundle_key: str
    bundle_version: int
    expected_content_hash: str
    expected_row_count: int
    actual_row_count: int
    matched_row_count: int
    missing_row_count: int
    extra_row_count: int
    numeric_mismatch_count: int
    state_mismatch_count: int
    event_mismatch_count: int
    max_abs_error: str
    max_rel_error: str
    passed: bool


@dataclass(frozen=True, slots=True)
class RecordParityEvidence:
    evidence_record_id: uuid.UUID
    component_kind: Literal["factor_variant", "signal_version"]
    legacy_key: str
    mapped_variant_key: str
    comparisons: tuple[PointComparison, ...]
    passed: bool


@dataclass(frozen=True, slots=True)
class _OracleContext:
    bundle_key: str
    bundle_version: int
    coverage_start: date
    coverage_end: date
    bars_by_asset: dict[uuid.UUID, tuple[FactorBar, ...]]
    candidate_asset_ids: frozenset[uuid.UUID]


class V021ParityError(RuntimeError):
    """Raised when frozen Oracle identity or point parity cannot be established."""


class V021ParityHarness:
    """Compare v0.22 compatibility output with frozen v0.21 database values."""

    def __init__(
        self,
        engine: Engine,
        registry: MigrationRegistry,
        *,
        float_tolerance: float = 1e-12,
        decimal_quantum: Decimal = Decimal("0.000000000000000001"),
    ) -> None:
        self._engine = engine
        self._registry = registry
        self._runtime = LegacyCompatibilityRuntime(registry)
        self._float_tolerance = float_tolerance
        self._decimal_quantum = decimal_quantum

    @classmethod
    def from_registry_path(
        cls,
        engine: Engine,
        registry_path: Path,
    ) -> V021ParityHarness:
        return cls(engine, load_migration_registry(registry_path))

    def build_evidence(self) -> dict[str, Any]:
        by_kind = {
            kind: tuple(
                record
                for record in self._registry.records
                if record.component_kind == kind
            )
            for kind in ("factor_variant", "signal_version")
        }
        comparisons: dict[tuple[str, str], list[PointComparison]] = defaultdict(list)
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                database_name = str(
                    connection.execute(text("SELECT current_database()")) .scalar_one()
                )
                if database_name != "style_rotation":
                    raise V021ParityError(
                        "Parity Oracle must be read from the frozen style_rotation database"
                    )
                contexts = self._contexts(connection, by_kind["factor_variant"])
                signal_by_factor = _signals_by_factor(by_kind["signal_version"])
                for context_key, context in sorted(contexts.items()):
                    for factor_record in by_kind["factor_variant"]:
                        factor_oracle = _oracle_for_context(factor_record, context_key)
                        actual_factor = self._runtime.execute_factor(
                            factor_record.legacy_key,
                            context.bars_by_asset,
                            coverage_start=context.coverage_start,
                            coverage_end=context.coverage_end,
                        )
                        expected_factor = _factor_points(
                            connection, factor_record, factor_oracle
                        )
                        comparisons[("factor_variant", factor_record.legacy_key)].append(
                            compare_factor_points(
                                actual_factor.calculation.points,
                                expected_factor,
                                factor_oracle,
                                tolerance=self._float_tolerance,
                            )
                        )
                        for signal_record in signal_by_factor[factor_record.legacy_key]:
                            signal_oracle = _oracle_for_context(signal_record, context_key)
                            actual_signal = self._runtime.execute_signal(
                                signal_record.legacy_key,
                                actual_factor,
                                candidate_asset_ids=context.candidate_asset_ids,
                            )
                            expected_signal = _signal_points(
                                connection, signal_record, signal_oracle
                            )
                            comparisons[("signal_version", signal_record.legacy_key)].append(
                                compare_signal_points(
                                    actual_signal.calculation.points,
                                    expected_signal,
                                    signal_oracle,
                                    decimal_quantum=self._decimal_quantum,
                                )
                            )
                transaction.commit()
            except Exception:
                transaction.rollback()
                raise

        records = tuple(
            _record_evidence(record, comparisons[(record.component_kind, record.legacy_key)])
            for record in self._registry.records
        )
        passed = all(record.passed for record in records)
        summary = {
            "factor_variant_count": sum(
                item.component_kind == "factor_variant" for item in records
            ),
            "signal_version_count": sum(
                item.component_kind == "signal_version" for item in records
            ),
            "comparison_count": sum(len(item.comparisons) for item in records),
            "passed_record_count": sum(item.passed for item in records),
            "failed_record_count": sum(not item.passed for item in records),
            "passed": passed,
        }
        document: dict[str, Any] = {
            "evidence_type": "v022_legacy_point_parity",
            "evidence_version": "0.22.0",
            "oracle_baseline_id": self._registry.oracle_baseline_id,
            "registry_version": str(self._registry.registry_version),
            "registry_fingerprint": migration_registry_summary(self._registry)[
                "registry_fingerprint"
            ],
            "runtime_contract_fingerprint": self._runtime.runtime_contract_fingerprint,
            "comparison_policy": {
                "expected_values_source": "frozen_v021_database_artifact_points",
                "float_abs_rel_tolerance": _float_text(self._float_tolerance),
                "decimal_quantum": str(self._decimal_quantum),
                "missing_reason_comparison": "legacy_unknown_not_claimed",
                "unexplained_mismatch_allowed": False,
            },
            "records": [_record_payload(record) for record in records],
            "summary": summary,
        }
        document["evidence_fingerprint"] = sha256_hexdigest(document)
        return document

    def _contexts(
        self,
        connection: Connection,
        factor_records: tuple[MigrationRegistryRecord, ...],
    ) -> dict[tuple[str, int], _OracleContext]:
        representatives: dict[tuple[str, int], LegacyOracleOutput] = {}
        for record in factor_records:
            for oracle in record.oracle_outputs:
                representatives.setdefault((oracle.bundle_key, oracle.bundle_version), oracle)
        if len(representatives) != 2:
            raise V021ParityError(
                f"Expected two frozen Oracle contexts, found {len(representatives)}"
            )
        return {
            key: _load_context(connection, oracle)
            for key, oracle in representatives.items()
        }


def compare_factor_points(
    actual: tuple[FactorPoint, ...],
    expected: tuple[FactorPoint, ...],
    oracle: LegacyOracleOutput,
    *,
    tolerance: float,
) -> PointComparison:
    actual_by_key = {
        (point.asset_id, point.observation_date): point.value for point in actual
    }
    expected_by_key = {
        (point.asset_id, point.observation_date): point.value for point in expected
    }
    shared = set(actual_by_key) & set(expected_by_key)
    max_abs = 0.0
    max_rel = 0.0
    mismatches = 0
    for key in shared:
        actual_value = actual_by_key[key]
        expected_value = expected_by_key[key]
        absolute = abs(actual_value - expected_value)
        denominator = max(abs(actual_value), abs(expected_value))
        relative = 0.0 if denominator == 0 else absolute / denominator
        max_abs = max(max_abs, absolute)
        max_rel = max(max_rel, relative)
        if not math.isclose(
            actual_value,
            expected_value,
            rel_tol=tolerance,
            abs_tol=tolerance,
        ):
            mismatches += 1
    return _comparison(
        oracle,
        actual_count=len(actual_by_key),
        expected_count=len(expected_by_key),
        matched_count=len(shared),
        missing_count=len(set(expected_by_key) - set(actual_by_key)),
        extra_count=len(set(actual_by_key) - set(expected_by_key)),
        numeric_mismatches=mismatches,
        state_mismatches=0,
        event_mismatches=0,
        max_abs=max_abs,
        max_rel=max_rel,
    )


def compare_signal_points(
    actual: tuple[SignalPoint, ...],
    expected: tuple[SignalPoint, ...],
    oracle: LegacyOracleOutput,
    *,
    decimal_quantum: Decimal,
) -> PointComparison:
    actual_by_key = {
        (point.asset_id, point.observation_date): point for point in actual
    }
    expected_by_key = {
        (point.asset_id, point.observation_date): point for point in expected
    }
    shared = set(actual_by_key) & set(expected_by_key)
    max_abs = Decimal(0)
    max_rel = Decimal(0)
    numeric_mismatches = 0
    state_mismatches = 0
    event_mismatches = 0
    for key in shared:
        actual_point = actual_by_key[key]
        expected_point = expected_by_key[key]
        absolute = abs(actual_point.score - expected_point.score)
        denominator = max(abs(actual_point.score), abs(expected_point.score))
        relative = Decimal(0) if denominator == 0 else absolute / denominator
        max_abs = max(max_abs, absolute)
        max_rel = max(max_rel, relative)
        numeric_mismatches += absolute > decimal_quantum
        state_mismatches += actual_point.state != expected_point.state
        event_mismatches += actual_point.event != expected_point.event
    return _comparison(
        oracle,
        actual_count=len(actual_by_key),
        expected_count=len(expected_by_key),
        matched_count=len(shared),
        missing_count=len(set(expected_by_key) - set(actual_by_key)),
        extra_count=len(set(actual_by_key) - set(expected_by_key)),
        numeric_mismatches=numeric_mismatches,
        state_mismatches=state_mismatches,
        event_mismatches=event_mismatches,
        max_abs=float(max_abs),
        max_rel=float(max_rel),
    )


def _comparison(
    oracle: LegacyOracleOutput,
    *,
    actual_count: int,
    expected_count: int,
    matched_count: int,
    missing_count: int,
    extra_count: int,
    numeric_mismatches: int,
    state_mismatches: int,
    event_mismatches: int,
    max_abs: float,
    max_rel: float,
) -> PointComparison:
    passed = (
        expected_count == oracle.row_count
        and actual_count == expected_count
        and missing_count == 0
        and extra_count == 0
        and numeric_mismatches == 0
        and state_mismatches == 0
        and event_mismatches == 0
    )
    return PointComparison(
        oracle_artifact_id=oracle.artifact_id,
        bundle_key=oracle.bundle_key,
        bundle_version=oracle.bundle_version,
        expected_content_hash=oracle.content_hash,
        expected_row_count=expected_count,
        actual_row_count=actual_count,
        matched_row_count=matched_count,
        missing_row_count=missing_count,
        extra_row_count=extra_count,
        numeric_mismatch_count=numeric_mismatches,
        state_mismatch_count=state_mismatches,
        event_mismatch_count=event_mismatches,
        max_abs_error=_float_text(max_abs),
        max_rel_error=_float_text(max_rel),
        passed=passed,
    )


def _load_context(connection: Connection, oracle: LegacyOracleOutput) -> _OracleContext:
    metadata = _verified_factor_dataset(connection, oracle)
    eligible_assets = (
        connection.execute(
            text(
                "SELECT item.asset_id, asset.asset_key FROM catalog.eligibility_item item "
                "JOIN catalog.asset asset ON asset.asset_id=item.asset_id "
                "WHERE item.eligibility_snapshot_id=:snapshot AND item.is_eligible "
                "ORDER BY asset.asset_key"
            ),
            {"snapshot": metadata["eligibility_snapshot_id"]},
        )
        .mappings()
        .all()
    )
    asset_keys = {row["asset_id"]: str(row["asset_key"]) for row in eligible_assets}
    candidate_asset_ids = frozenset(
        connection.execute(
            text(
                "SELECT asset_id FROM catalog.universe_member "
                "WHERE universe_version_id=:universe AND role='candidate'"
            ),
            {"universe": metadata["universe_version_id"]},
        ).scalars()
    )
    if not candidate_asset_ids or not candidate_asset_ids <= set(asset_keys):
        raise V021ParityError("Frozen Oracle candidate asset scope is invalid")
    rows = (
        connection.execute(
            text(
                "SELECT asset_id, session_date, close_adj, close_raw, volume_raw, "
                "open_raw, high_raw, low_raw, open_adj, high_adj, low_adj "
                "FROM data.daily_bar WHERE dataset_publication_id=:dataset "
                "AND asset_id IN :assets AND session_date <= :coverage_end "
                "ORDER BY asset_id, session_date"
            ).bindparams(bindparam("assets", expanding=True)),
            {
                "dataset": metadata["market_dataset_id"],
                "assets": tuple(asset_keys),
                "coverage_end": metadata["coverage_end"],
            },
        )
        .mappings()
        .all()
    )
    bars: dict[uuid.UUID, list[FactorBar]] = {asset_id: [] for asset_id in asset_keys}
    for row in rows:
        bars[row["asset_id"]].append(
            FactorBar(
                asset_id=row["asset_id"],
                asset_key=asset_keys[row["asset_id"]],
                session_date=row["session_date"],
                close_adj=row["close_adj"],
                close_raw=row["close_raw"],
                volume_raw=row["volume_raw"],
                open_raw=row["open_raw"],
                high_raw=row["high_raw"],
                low_raw=row["low_raw"],
                open_adj=row["open_adj"],
                high_adj=row["high_adj"],
                low_adj=row["low_adj"],
            )
        )
    return _OracleContext(
        bundle_key=oracle.bundle_key,
        bundle_version=oracle.bundle_version,
        coverage_start=metadata["coverage_start"],
        coverage_end=metadata["coverage_end"],
        bars_by_asset={key: tuple(value) for key, value in bars.items()},
        candidate_asset_ids=candidate_asset_ids,
    )


def _verified_factor_dataset(
    connection: Connection, oracle: LegacyOracleOutput
) -> Any:
    row = (
        connection.execute(
            text(
                "SELECT dataset.*, member.dataset_publication_id market_dataset_id, "
                "artifact.semantic_fingerprint, artifact.content_hash, artifact.status, "
                "definition.factor_key, variant.variant_key, bundle_definition.bundle_key, "
                "bundle.version_number bundle_version "
                "FROM factor.factor_dataset dataset "
                "JOIN lineage.artifact artifact ON artifact.artifact_id=dataset.artifact_id "
                "JOIN factor.factor_variant variant "
                "ON variant.factor_variant_id=dataset.factor_variant_id "
                "JOIN factor.factor_definition_version definition_version "
                "ON definition_version.factor_definition_version_id="
                "variant.factor_definition_version_id "
                "JOIN factor.factor_definition definition "
                "ON definition.factor_definition_id=definition_version.factor_definition_id "
                "JOIN data.data_bundle_version bundle "
                "ON bundle.data_bundle_version_id=dataset.data_bundle_version_id "
                "JOIN data.data_bundle_definition bundle_definition "
                "ON bundle_definition.data_bundle_definition_id="
                "bundle.data_bundle_definition_id "
                "JOIN data.data_bundle_member member "
                "ON member.data_bundle_version_id=bundle.data_bundle_version_id "
                "AND member.role='canonical_market' "
                "WHERE dataset.artifact_id=:artifact"
            ),
            {"artifact": oracle.artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise V021ParityError(f"Frozen factor Artifact missing: {oracle.artifact_id}")
    _verify_oracle_row(row, oracle)
    return row


def _factor_points(
    connection: Connection,
    record: MigrationRegistryRecord,
    oracle: LegacyOracleOutput,
) -> tuple[FactorPoint, ...]:
    row = _verified_factor_dataset(connection, oracle)
    if row["variant_key"] != record.legacy_key:
        raise V021ParityError(f"Factor Oracle identity drift: {record.legacy_key}")
    points = (
        connection.execute(
            text(
                "SELECT value.asset_id, asset.asset_key, value.observation_date, value.value "
                "FROM factor.factor_value value JOIN catalog.asset asset "
                "ON asset.asset_id=value.asset_id "
                "WHERE value.factor_dataset_id=:dataset "
                "ORDER BY asset.asset_key, value.observation_date, value.asset_id"
            ),
            {"dataset": row["factor_dataset_id"]},
        )
        .mappings()
        .all()
    )
    return tuple(
        FactorPoint(
            point["asset_id"],
            str(point["asset_key"]),
            point["observation_date"],
            point["value"],
        )
        for point in points
    )


def _signal_points(
    connection: Connection,
    record: MigrationRegistryRecord,
    oracle: LegacyOracleOutput,
) -> tuple[SignalPoint, ...]:
    row = (
        connection.execute(
            text(
                "SELECT dataset.signal_dataset_id, artifact.semantic_fingerprint, "
                "artifact.content_hash, artifact.status, definition.signal_key, "
                "bundle_definition.bundle_key, bundle.version_number bundle_version "
                "FROM signal.signal_dataset dataset JOIN lineage.artifact artifact "
                "ON artifact.artifact_id=dataset.artifact_id "
                "JOIN signal.signal_version version "
                "ON version.signal_version_id=dataset.signal_version_id "
                "JOIN signal.signal_definition definition "
                "ON definition.signal_definition_id=version.signal_definition_id "
                "JOIN data.data_bundle_version bundle "
                "ON bundle.data_bundle_version_id=dataset.data_bundle_version_id "
                "JOIN data.data_bundle_definition bundle_definition "
                "ON bundle_definition.data_bundle_definition_id="
                "bundle.data_bundle_definition_id "
                "WHERE dataset.artifact_id=:artifact"
            ),
            {"artifact": oracle.artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise V021ParityError(f"Frozen signal Artifact missing: {oracle.artifact_id}")
    _verify_oracle_row(row, oracle)
    if row["signal_key"] != record.legacy_key:
        raise V021ParityError(f"Signal Oracle identity drift: {record.legacy_key}")
    points = (
        connection.execute(
            text(
                "SELECT value.asset_id, asset.asset_key, value.observation_date, "
                "value.score, value.state, value.event FROM signal.signal_value value "
                "JOIN catalog.asset asset ON asset.asset_id=value.asset_id "
                "WHERE value.signal_dataset_id=:dataset "
                "ORDER BY asset.asset_key, value.observation_date, value.asset_id"
            ),
            {"dataset": row["signal_dataset_id"]},
        )
        .mappings()
        .all()
    )
    return tuple(
        SignalPoint(
            point["asset_id"],
            str(point["asset_key"]),
            point["observation_date"],
            point["score"],
            point["state"],
            point["event"],
        )
        for point in points
    )


def _verify_oracle_row(row: Any, oracle: LegacyOracleOutput) -> None:
    actual = (
        row["status"],
        row["semantic_fingerprint"],
        row["content_hash"],
        row["bundle_key"],
        row["bundle_version"],
    )
    expected = (
        "published",
        oracle.semantic_fingerprint,
        oracle.content_hash,
        oracle.bundle_key,
        oracle.bundle_version,
    )
    if actual != expected:
        raise V021ParityError(f"Frozen Oracle Artifact drift: {oracle.artifact_id}")


def _signals_by_factor(
    records: tuple[MigrationRegistryRecord, ...],
) -> dict[str, tuple[MigrationRegistryRecord, ...]]:
    grouped: dict[str, list[MigrationRegistryRecord]] = defaultdict(list)
    for record in records:
        grouped[str(record.legacy_recipe["factor_variant_key"])].append(record)
    return {key: tuple(value) for key, value in grouped.items()}


def _oracle_for_context(
    record: MigrationRegistryRecord, context: tuple[str, int]
) -> LegacyOracleOutput:
    matches = tuple(
        oracle
        for oracle in record.oracle_outputs
        if (oracle.bundle_key, oracle.bundle_version) == context
    )
    if len(matches) != 1:
        raise V021ParityError(
            f"Expected one Oracle for {record.legacy_key} in {context}, found {len(matches)}"
        )
    return matches[0]


def _record_evidence(
    record: MigrationRegistryRecord,
    comparisons: list[PointComparison],
) -> RecordParityEvidence:
    ordered = tuple(sorted(comparisons, key=lambda item: (item.bundle_key, item.bundle_version)))
    if len(ordered) != 2:
        raise V021ParityError(
            f"Expected two comparisons for {record.legacy_key}, found {len(ordered)}"
        )
    passed = all(item.passed for item in ordered)
    payload = {
        "component_kind": record.component_kind,
        "legacy_key": record.legacy_key,
        "mapped_variant_key": record.mapping.variant_key,
        "comparisons": [asdict(item) for item in ordered],
        "passed": passed,
    }
    fingerprint = sha256_hexdigest(payload)
    return RecordParityEvidence(
        evidence_record_id=uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird-v022:parity-record:{fingerprint}"
        ),
        component_kind=record.component_kind,
        legacy_key=record.legacy_key,
        mapped_variant_key=record.mapping.variant_key,
        comparisons=ordered,
        passed=passed,
    )


def _record_payload(record: RecordParityEvidence) -> dict[str, Any]:
    payload = asdict(record)
    payload["evidence_record_id"] = str(record.evidence_record_id)
    payload["comparisons"] = [
        {
            **asdict(comparison),
            "oracle_artifact_id": str(comparison.oracle_artifact_id),
        }
        for comparison in record.comparisons
    ]
    return payload


def _float_text(value: float) -> str:
    return format(value, ".17g")
