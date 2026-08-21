from __future__ import annotations

import hashlib
import io
import json
import uuid
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from functools import partial
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.aggregation_work_runtime import (
    AggregationOutputPoint,
    encode_final_signal_numeric_parquet,
)
from style_rotation.v022.dag import (
    ClaimedGraphWork,
    GraphDagService,
    WorkPlan,
    execution_fingerprint,
)
from style_rotation.v022.incremental_runtime import (
    IncrementalExecutionContract,
    IncrementalRunPlan,
    partition_sessions_by_calendar_year,
    plan_incremental_run,
    record_partition_plan,
)
from style_rotation.v022.payload_runtime import (
    ExecutedPartitionPayload,
    LocalPayloadObjectStore,
    NodeOutputPayload,
    PublishedNodeOutput,
    publish_node_output,
    publish_node_output_bundle,
)
from style_rotation.v022.processing_calculation_context import (
    ProcessingCalculationContextService,
    ProcessingCalculationContextSpec,
)
from style_rotation.v022.processing_runtime import (
    AMIHUD_DAILY_PRIMITIVES_IMPLEMENTATION,
    DOWNSIDE_DEVIATION_IMPLEMENTATION,
    LAGGED_RETURN_IMPLEMENTATION,
    MAXIMUM_DRAWDOWN_IMPLEMENTATION,
    MOVING_AVERAGE_RATIO_IMPLEMENTATION,
    PPO_HISTOGRAM_IMPLEMENTATION,
    REALIZED_VOLATILITY_IMPLEMENTATION,
    RELATIVE_DOLLAR_VOLUME_IMPLEMENTATION,
    RETURN_EXCESS_KURTOSIS_IMPLEMENTATION,
    RETURN_SKEWNESS_IMPLEMENTATION,
    RSI_WILDER_IMPLEMENTATION,
    TOTAL_RETURN_IMPLEMENTATION,
    Panel,
    execute_catalog_node,
    execute_representative_graph,
)
from style_rotation.v022.runtime_contract import V022RuntimeContractError, V022RuntimeDataError
from style_rotation.v022.runtime_telemetry import (
    LocalRuntimeTelemetry,
    PeriodicLeaseHeartbeat,
    RuntimeTelemetryIdentity,
)

RAW_FEATURE_KEYS = ("adjusted_close", "close_raw", "volume_raw")
_INTERMEDIATE_ENCODING_BATCH_ROWS = 50_000
RAW_DATA_FIELDS = {
    "adjusted_close": ("adj_close", "price"),
    "close_raw": ("close_raw", "price"),
    "volume_raw": ("volume_raw", "shares"),
}
FINAL_FEATURE_KEYS = (
    "return_continuation__w120",
    "price_cross_above_ma__s1_l200",
    "low_illiquidity_quality__w20",
)
CATALOG_STAGE1_FEATURE_KEYS = (
    "moving_average_ratio__s1_l200",
    "total_return__w120",
)
CATALOG_STAGE1_RAW_INPUTS = {
    TOTAL_RETURN_IMPLEMENTATION: {"adjusted_close": "close_adj"},
    MOVING_AVERAGE_RATIO_IMPLEMENTATION: {"adjusted_close": "close_adj"},
    LAGGED_RETURN_IMPLEMENTATION: {"adjusted_close": "close_adj"},
    MAXIMUM_DRAWDOWN_IMPLEMENTATION: {"adjusted_close": "close_adj"},
    REALIZED_VOLATILITY_IMPLEMENTATION: {"adjusted_close": "close_adj"},
    DOWNSIDE_DEVIATION_IMPLEMENTATION: {"adjusted_close": "close_adj"},
    RELATIVE_DOLLAR_VOLUME_IMPLEMENTATION: {
        "close_raw": "close_raw",
        "volume_raw": "volume_raw",
    },
    RETURN_SKEWNESS_IMPLEMENTATION: {"adjusted_close": "close_adj"},
    RETURN_EXCESS_KURTOSIS_IMPLEMENTATION: {"adjusted_close": "close_adj"},
    RSI_WILDER_IMPLEMENTATION: {"adjusted_close": "close_adj"},
    PPO_HISTOGRAM_IMPLEMENTATION: {"adjusted_close": "close_adj"},
}
CATALOG_SINGLE_OUTPUT_STAGE1_IMPLEMENTATIONS = frozenset(CATALOG_STAGE1_RAW_INPUTS)
AMIHUD_STAGE1_WORK_KEY = "amihud_daily_primitives__canonical"
AMIHUD_STAGE1_OUTPUTS = {
    "simple_return__amihud_daily": ("simple_return", "decimal_return"),
    "dollar_volume__close_times_volume": ("dollar_volume", "currency"),
    "daily_price_impact__amihud": ("daily_price_impact", "return_per_currency"),
}
MATERIALIZED_FEATURE_KEYS = (
    CATALOG_STAGE1_FEATURE_KEYS + (AMIHUD_STAGE1_WORK_KEY,)
)
MATERIALIZED_OUTPUT_FEATURE_KEYS = (
    CATALOG_STAGE1_FEATURE_KEYS + tuple(AMIHUD_STAGE1_OUTPUTS)
)
STAGE2_FEATURE_KEYS = (
    "amihud_illiquidity__w20",
    "price_cross_above_ma__s1_l200",
)
STAGE2_INPUT_FEATURES = {
    "amihud_illiquidity__w20": "daily_price_impact__amihud",
    "price_cross_above_ma__s1_l200": "moving_average_ratio__s1_l200",
}
STAGE3_FEATURE_KEYS = (
    "low_illiquidity_quality__w20",
    "return_continuation__w120",
)
STAGE3_INPUT_FEATURES = {
    "low_illiquidity_quality__w20": "amihud_illiquidity__w20",
    "return_continuation__w120": "total_return__w120",
}
CANONICAL_MARKET_INPUT = "canonical_market_bars"
CANONICAL_ENCODING_KEY = "canonical_parquet"
CANONICAL_ENCODING_VERSION = 1
SNAPSHOT_SEMANTIC_MODE = "back_adjusted_historical_research"
RAW_COLUMNS = (
    "session_date",
    "asset_id",
    "value",
    "known_at",
    "vintage_id",
    "missing_reason",
    "unit",
)


@dataclass(frozen=True, slots=True)
class RawSnapshotPoint:
    asset_id: uuid.UUID
    asset_key: str
    session_date: date
    value: Decimal
    known_at: datetime
    vintage_id: str
    unit: str


@dataclass(frozen=True, slots=True)
class FrozenRawInputs:
    adjusted_close: tuple[RawSnapshotPoint, ...]
    close_raw: tuple[RawSnapshotPoint, ...]
    volume_raw: tuple[RawSnapshotPoint, ...]


@dataclass(frozen=True, slots=True)
class RepresentativeFeaturePoint:
    asset_id: uuid.UUID
    asset_key: str
    session_date: date
    value: Decimal | None
    known_at: datetime
    input_revision: str
    missing_reason: str | None


@dataclass(frozen=True, slots=True)
class RepresentativeSnapshotExecution:
    features: Mapping[str, tuple[RepresentativeFeaturePoint, ...]]


@dataclass(frozen=True, slots=True)
class PublishedRawPayload:
    feature_variant_key: str
    feature_version_id: uuid.UUID
    payload_manifest_id: uuid.UUID
    manifest_artifact_id: uuid.UUID
    manifest_hash: str
    reused_publication: bool


@dataclass(frozen=True, slots=True)
class PublishedRawPayloadBundle:
    compiled_execution_data_context_id: uuid.UUID
    dataset_publication_id: uuid.UUID
    snapshot_semantic_mode: str
    outputs: tuple[PublishedRawPayload, ...]
    product_input_snapshot_id: uuid.UUID | None = None
    calculation_context_id: uuid.UUID | None = None
    calculation_context_artifact_id: uuid.UUID | None = None
    calculation_context_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class RepresentativeNodePublicationTarget:
    feature_variant_key: str
    node_run_id: uuid.UUID
    output_port_key: str
    plan: IncrementalRunPlan


@dataclass(frozen=True, slots=True)
class CatalogStage1PublicationTarget:
    feature_variant_key: str
    implementation_key: str
    parameters: Mapping[str, object]
    node_run_id: uuid.UUID
    output_port_key: str
    output_unit: str
    plan: IncrementalRunPlan


@dataclass(frozen=True, slots=True)
class CatalogMultiOutputPublicationTarget:
    node_run_id: uuid.UUID
    implementation_key: str
    parameters: Mapping[str, object]
    outputs: Mapping[str, tuple[str, str]]
    plan: IncrementalRunPlan


@dataclass(frozen=True, slots=True)
class CatalogStage2PublicationTarget:
    feature_variant_key: str
    implementation_key: str
    parameters: Mapping[str, object]
    input_port_key: str
    output_port_key: str
    output_unit: str
    output_contract_key: str
    node_run_id: uuid.UUID
    plan: IncrementalRunPlan


@dataclass(frozen=True, slots=True)
class PublishedRepresentativeNodeOutputs:
    outputs: Mapping[str, PublishedNodeOutput]


@dataclass(frozen=True, slots=True)
class PublishedFeatureManifest:
    feature_variant_key: str
    payload_manifest_id: uuid.UUID
    manifest_artifact_id: uuid.UUID
    manifest_hash: str


@dataclass(frozen=True, slots=True)
class _CatalogLayerTopology:
    feature_keys: tuple[str, ...]
    input_features: Mapping[str, str]
    input_ports: Mapping[str, str]
    output_contracts: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RepresentativeProcessingMaterialization:
    graph_run_id: uuid.UUID
    compiled_research_graph_id: uuid.UUID
    compiled_execution_data_context_id: uuid.UUID
    requested_range: Mapping[str, object]
    raw_payloads: PublishedRawPayloadBundle
    stage3_outputs: Mapping[str, PublishedNodeOutput]


@dataclass(frozen=True, slots=True)
class _CompiledTerminalNode:
    feature_variant_key: str
    compiled_graph_node_id: uuid.UUID
    node_version_id: uuid.UUID
    node_version_artifact_id: uuid.UUID
    resolved_parameters: Mapping[str, object]
    execution_contract: IncrementalExecutionContract
    determinism_policy: str
    cache_policy: str
    output_port_key: str
    execution_fingerprint: str
    implementation_key: str | None = None
    output_unit: str | None = None
    additional_outputs: Mapping[str, tuple[str, str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _MaterializedNodeTarget:
    compiled: _CompiledTerminalNode
    graph_work_item_id: uuid.UUID
    node_run_id: uuid.UUID
    plan: IncrementalRunPlan
    claim: ClaimedGraphWork | None


@dataclass(frozen=True, slots=True)
class _RawFeatureContext:
    feature_variant_key: str
    source_field: str
    unit: str
    feature_version_id: uuid.UUID
    feature_artifact_id: uuid.UUID
    payload_contract_version_id: uuid.UUID
    payload_contract_artifact_id: uuid.UUID
    output_port_key: str


@dataclass(frozen=True, slots=True)
class _DatasetSnapshotContext:
    compiled_execution_data_context_id: uuid.UUID
    execution_data_context_artifact_id: uuid.UUID
    dataset_publication_id: uuid.UUID
    dataset_artifact_id: uuid.UUID
    catalog_release_id: uuid.UUID
    encoding_id: uuid.UUID
    encoding_artifact_id: uuid.UUID
    assets: Mapping[uuid.UUID, tuple[uuid.UUID, str]]
    snapshots: Mapping[uuid.UUID, tuple[uuid.UUID, datetime, datetime]]
    session_closes: Mapping[date, datetime]
    features: tuple[_RawFeatureContext, ...]
    coverage_start: date
    coverage_end: date
    product_input_snapshot_id: uuid.UUID | None = None
    product_input_snapshot_artifact_id: uuid.UUID | None = None
    calendar_version_id: uuid.UUID | None = None
    calendar_artifact_id: uuid.UUID | None = None
    calculation_context_id: uuid.UUID | None = None
    calculation_context_artifact_id: uuid.UUID | None = None
    calculation_context_fingerprint: str | None = None


def _resolve_asset_snapshot_proofs(
    *,
    assets: Mapping[uuid.UUID, tuple[uuid.UUID, str]],
    snapshot_rows: tuple[Mapping[str, object], ...],
    security_identifier_rows: tuple[Mapping[str, object], ...],
) -> dict[uuid.UUID, tuple[uuid.UUID, datetime, datetime]]:
    snapshots_by_symbol: dict[str, tuple[uuid.UUID, datetime, datetime]] = {}
    snapshots_by_security: dict[uuid.UUID, tuple[uuid.UUID, datetime, datetime]] = {}
    for row in snapshot_rows:
        symbol = row["asset_symbol"]
        artifact_id = row["artifact_id"]
        fetched_at = row["fetched_at"]
        as_of_at = row["as_of_at"]
        if (
            row["status"] != "published"
            or not isinstance(symbol, str)
            or not isinstance(artifact_id, uuid.UUID)
            or not isinstance(fetched_at, datetime)
            or not isinstance(as_of_at, datetime)
        ):
            raise V022RuntimeDataError(
                "representative_snapshot_proof_ambiguous",
                "Dataset source snapshots do not prove one published boundary per Asset",
            )
        canonical_symbol = symbol.strip().casefold()
        proof = (artifact_id, fetched_at, as_of_at)
        if not canonical_symbol or (
            canonical_symbol in snapshots_by_symbol
            and snapshots_by_symbol[canonical_symbol] != proof
        ):
            raise V022RuntimeDataError(
                "representative_snapshot_proof_ambiguous",
                "Dataset source snapshots do not prove one unique boundary per listing symbol",
            )
        snapshots_by_symbol[canonical_symbol] = proof
        subject_security_id = row.get("subject_security_id")
        subject_fetch_status = row.get("subject_fetch_status")
        if subject_security_id is not None:
            if (
                not isinstance(subject_security_id, uuid.UUID)
                or subject_fetch_status != "fetched"
                or (
                    subject_security_id in snapshots_by_security
                    and snapshots_by_security[subject_security_id] != proof
                )
            ):
                raise V022RuntimeDataError(
                    "representative_snapshot_proof_ambiguous",
                    "Dataset source subjects do not prove one fetched snapshot per Security",
                )
            snapshots_by_security[subject_security_id] = proof
    if len(snapshots_by_symbol) != len(snapshot_rows):
        raise V022RuntimeDataError(
            "representative_snapshot_proof_ambiguous",
            "Dataset source snapshots do not prove one unique boundary per listing symbol",
        )

    symbols_by_security: dict[uuid.UUID, set[str]] = defaultdict(set)
    for row in security_identifier_rows:
        security_id = row["security_id"]
        identifier = row["identifier_value"]
        if (
            isinstance(security_id, uuid.UUID)
            and isinstance(identifier, str)
            and identifier.strip()
        ):
            symbols_by_security[security_id].add(identifier.strip().casefold())

    resolved: dict[uuid.UUID, tuple[uuid.UUID, datetime, datetime]] = {}
    for security_id, (_, asset_key) in assets.items():
        direct_proof = snapshots_by_security.get(security_id)
        candidates = (
            {direct_proof}
            if direct_proof is not None
            else {
                snapshots_by_symbol[symbol]
                for symbol in symbols_by_security.get(security_id, set())
                if symbol in snapshots_by_symbol
            }
        )
        if len(candidates) != 1:
            reason = (
                "representative_snapshot_proof_missing"
                if not candidates
                else "representative_snapshot_proof_ambiguous"
            )
            raise V022RuntimeDataError(
                reason,
                f"Frozen Asset {asset_key!r} does not resolve to one source snapshot proof",
            )
        proof = candidates.pop()
        if proof[1].utcoffset() is None or proof[2].utcoffset() is None:
            raise V022RuntimeDataError(
                "representative_snapshot_proof_missing",
                f"Frozen Asset {asset_key!r} has no timezone-aware source snapshot proof",
            )
        resolved[security_id] = proof
    return resolved


@dataclass(frozen=True, slots=True)
class _PreparedRawPayload:
    context: _RawFeatureContext
    content: bytes
    content_hash: str
    storage_uri: str
    byte_size: int
    row_count: int
    start: date
    end: date
    session_count: int
    known_at_start: datetime
    known_at_end: datetime
    snapshot_semantics: Mapping[str, object]
    payload_object_id: uuid.UUID
    payload_partition_id: uuid.UUID
    partition_descriptor_hash: str
    payload_manifest_id: uuid.UUID
    logical_payload_fingerprint: str
    manifest_hash: str


def execute_representative_snapshot(
    raw: FrozenRawInputs,
) -> RepresentativeSnapshotExecution:
    """Execute the three representative Stage-3 chains from one frozen snapshot.

    This is intentionally the narrow v0.22 vertical slice.  It accepts exactly the
    three Raw inputs used by the representative graph and does not infer or generate
    any additional research path.
    """

    indexed = {
        "adjusted_close": _index_raw_points(raw.adjusted_close, "adjusted_close"),
        "close_raw": _index_raw_points(raw.close_raw, "close_raw"),
        "volume_raw": _index_raw_points(raw.volume_raw, "volume_raw"),
    }
    identities = set(indexed["adjusted_close"])
    if not identities or any(set(points) != identities for points in indexed.values()):
        raise V022RuntimeDataError(
            "representative_raw_input_panel_incomplete",
            "The representative pipeline requires one exact adjusted-close, "
            "raw-close, and volume panel",
        )
    asset_keys = {
        identity[0]: indexed["adjusted_close"][identity].asset_key
        for identity in identities
    }
    for identity in identities:
        expected_key = asset_keys[identity[0]]
        if any(points[identity].asset_key != expected_key for points in indexed.values()):
            raise V022RuntimeDataError(
                "representative_raw_asset_identity_drift",
                "Raw input panels disagree on an Asset identity",
            )

    def panel(key: str) -> Panel:
        return {
            (points.asset_key, identity[1]): points.value
            for identity, points in indexed[key].items()
        }

    calculation = execute_representative_graph(
        adjusted_close=panel("adjusted_close"),
        close_raw=panel("close_raw"),
        volume_raw=panel("volume_raw"),
    )
    known_at_by_identity = {
        identity: max(
            points[identity].known_at.astimezone(UTC) for points in indexed.values()
        )
        for identity in identities
    }
    input_revision = sha256_hexdigest(
        tuple(
            (
                key,
                str(identity[0]),
                identity[1],
                indexed[key][identity].vintage_id,
            )
            for key in RAW_FEATURE_KEYS
            for identity in sorted(identities, key=lambda item: (item[1], str(item[0])))
        )
    )
    asset_ids_by_key = {value: key for key, value in asset_keys.items()}
    output: dict[str, tuple[RepresentativeFeaturePoint, ...]] = {}
    for feature_key, values in calculation.features.items():
        rows: list[RepresentativeFeaturePoint] = []
        for (asset_key, session), value in sorted(
            values.items(), key=lambda item: (item[0][1], item[0][0])
        ):
            asset_id = asset_ids_by_key[asset_key]
            rows.append(
                RepresentativeFeaturePoint(
                    asset_id=asset_id,
                    asset_key=asset_key,
                    session_date=session,
                    value=value,
                    known_at=known_at_by_identity[(asset_id, session)],
                    input_revision=input_revision,
                    missing_reason="insufficient_history" if value is None else None,
                )
            )
        output[feature_key] = tuple(
            sorted(rows, key=lambda item: (item.session_date, str(item.asset_id)))
        )
    if not set(FINAL_FEATURE_KEYS).issubset(output):
        raise V022RuntimeContractError(
            "representative_final_signal_missing",
            "Representative processing did not produce its three frozen Stage-3 signals",
        )
    return RepresentativeSnapshotExecution(output)


def encode_raw_numeric_parquet(points: tuple[RawSnapshotPoint, ...]) -> bytes:
    if not points:
        raise V022RuntimeDataError(
            "representative_raw_payload_empty", "Raw snapshot Payload cannot be empty"
        )
    ordered = tuple(sorted(points, key=lambda item: (item.session_date, str(item.asset_id))))
    if ordered != points:
        raise V022RuntimeContractError(
            "representative_raw_payload_not_canonical",
            "Raw snapshot points must use canonical session-date Asset order",
        )
    try:
        table = pa.Table.from_pylist(
            [
                {
                    "session_date": item.session_date,
                    "asset_id": str(item.asset_id),
                    "value": item.value,
                    "known_at": item.known_at.astimezone(UTC),
                    "vintage_id": item.vintage_id,
                    "missing_reason": None,
                    "unit": item.unit,
                }
                for item in ordered
            ],
            schema=_raw_arrow_schema(),
        )
    except (pa.ArrowInvalid, pa.ArrowTypeError) as error:
        raise V022RuntimeDataError(
            "representative_raw_payload_schema_invalid",
            "Raw snapshot values cannot satisfy the canonical numeric payload",
        ) from error
    buffer = io.BytesIO()
    pq.write_table(
        table,
        buffer,
        compression="zstd",
        use_dictionary=False,
        version="2.6",
        write_statistics=True,
    )
    return buffer.getvalue()


def encode_representative_final_signal_parquet(
    points: tuple[RepresentativeFeaturePoint, ...],
) -> bytes:
    return encode_final_signal_numeric_parquet(
        tuple(
            AggregationOutputPoint(
                asset_id=item.asset_id,
                asset_key=item.asset_key,
                decision_date=item.session_date,
                signal_value=item.value,
                known_at=item.known_at,
                input_revision=item.input_revision,
                missing_reason=item.missing_reason,
            )
            for item in points
        )
    )


def encode_intermediate_numeric_parquet(
    points: tuple[RepresentativeFeaturePoint, ...], *, unit: str
) -> bytes:
    """Encode the frozen intermediate_numeric_feature v1 contract."""

    if not points:
        raise V022RuntimeDataError(
            "processing_intermediate_payload_empty",
            "An intermediate Processing Payload cannot be empty",
        )
    if not unit.strip():
        raise V022RuntimeContractError(
            "processing_intermediate_unit_missing",
            "An intermediate Processing Payload requires its frozen unit",
        )
    ordered = tuple(sorted(points, key=lambda item: (item.session_date, str(item.asset_id))))
    if ordered != points:
        raise V022RuntimeContractError(
            "processing_intermediate_payload_not_canonical",
            "Intermediate points must use canonical session-date Asset order",
        )
    schema = _intermediate_numeric_arrow_schema()
    buffer = io.BytesIO()
    writer: pq.ParquetWriter | None = None
    try:
        for offset in range(0, len(points), _INTERMEDIATE_ENCODING_BATCH_ROWS):
            chunk = points[offset : offset + _INTERMEDIATE_ENCODING_BATCH_ROWS]
            table = pa.Table.from_arrays(
                [
                    pa.array(
                        [item.session_date for item in chunk], type=pa.date32()
                    ),
                    pa.array([str(item.asset_id) for item in chunk], type=pa.string()),
                    pa.array(
                        [
                            item.value.quantize(
                                Decimal("1e-18"), rounding=ROUND_HALF_EVEN
                            )
                            if item.value is not None
                            else None
                            for item in chunk
                        ],
                        type=pa.decimal128(38, 18),
                    ),
                    pa.array(
                        [item.known_at.astimezone(UTC) for item in chunk],
                        type=pa.timestamp("us", tz="UTC"),
                    ),
                    pa.array(
                        [item.input_revision for item in chunk], type=pa.string()
                    ),
                    pa.array(
                        [item.missing_reason for item in chunk], type=pa.string()
                    ),
                    pa.array([unit] * len(chunk), type=pa.string()),
                ],
                schema=schema,
            )
            if writer is None:
                writer = pq.ParquetWriter(
                    buffer,
                    schema,
                    compression="zstd",
                    use_dictionary=False,
                    version="2.6",
                    write_statistics=True,
                )
            writer.write_table(table)
            del table
    except (pa.ArrowInvalid, pa.ArrowTypeError) as error:
        raise V022RuntimeDataError(
            "processing_intermediate_payload_schema_invalid",
            "Processing output cannot satisfy intermediate_numeric_feature v1",
        ) from error
    finally:
        if writer is not None:
            writer.close()
    content = buffer.getvalue()
    buffer.close()
    pa.default_memory_pool().release_unused()
    return content


def read_intermediate_numeric_manifest(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    manifest: PublishedFeatureManifest,
    asset_keys: Mapping[uuid.UUID, str],
    session_dates: frozenset[date] | None = None,
) -> tuple[RepresentativeFeaturePoint, ...]:
    """Read one exact published intermediate_numeric_feature v1 Manifest."""

    with engine.connect() as connection:
        header = connection.execute(
            text(
                """
                SELECT manifest.artifact_id,manifest.manifest_hash,
                       manifest.partition_count,manifest.materialization_state,
                       artifact.status,family.contract_key,contract.version_number,
                       encoding.encoding_key,encoding.version_number AS encoding_version
                  FROM data.payload_manifest manifest
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=manifest.artifact_id
                  JOIN data.payload_contract_version contract
                    ON contract.payload_contract_version_id=
                       manifest.payload_contract_version_id
                  JOIN data.payload_contract_family family
                    ON family.payload_contract_family_id=
                       contract.payload_contract_family_id
                  JOIN data.physical_encoding_version encoding
                    ON encoding.physical_encoding_version_id=
                       manifest.physical_encoding_version_id
                 WHERE manifest.payload_manifest_id=:manifest
                """
            ),
            {"manifest": manifest.payload_manifest_id},
        ).mappings().one_or_none()
        partition_statement = text(
            """
            SELECT link.ordinal,object.storage_uri,object.object_content_hash,
                   object.byte_size,object.object_state,
                   object.verification_status,object.verified_at
              FROM data.payload_manifest_partition link
              JOIN data.payload_partition partition
                ON partition.payload_partition_id=link.payload_partition_id
              JOIN data.payload_object object
                ON object.payload_object_id=partition.payload_object_id
             WHERE link.payload_manifest_id=:manifest
               AND (
                 CAST(:start AS date) IS NULL OR
                 (partition.coverage_document->>'end')::date >= CAST(:start AS date)
               )
               AND (
                 CAST(:end AS date) IS NULL OR
                 (partition.coverage_document->>'start')::date <= CAST(:end AS date)
               )
             ORDER BY link.ordinal
            """
        )
        partitions = tuple(
            connection.execute(
                partition_statement,
                {
                    "manifest": manifest.payload_manifest_id,
                    "start": min(session_dates) if session_dates else None,
                    "end": max(session_dates) if session_dates else None,
                },
            ).mappings()
        )
    if (
        header is None
        or header["artifact_id"] != manifest.manifest_artifact_id
        or header["manifest_hash"] != manifest.manifest_hash
        or header["status"] != "published"
        or header["materialization_state"] != "materialized"
        or header["contract_key"] != "intermediate_numeric_feature"
        or header["version_number"] != 1
        or header["encoding_key"] != CANONICAL_ENCODING_KEY
        or header["encoding_version"] != CANONICAL_ENCODING_VERSION
        or (session_dates is None and header["partition_count"] != len(partitions))
        or not partitions
    ):
        raise V022RuntimeDataError(
            "processing_intermediate_manifest_invalid",
            "Stage-2 input is not its exact published intermediate Manifest",
        )
    points: list[RepresentativeFeaturePoint] = []
    previous_ordinal = -1
    for ordinal, partition in enumerate(partitions):
        if (
            (session_dates is None and partition["ordinal"] != ordinal)
            or partition["ordinal"] <= previous_ordinal
            or partition["object_state"] != "published"
            or partition["verification_status"] != "verified"
            or partition["verified_at"] is None
        ):
            raise V022RuntimeDataError(
                "processing_intermediate_object_unverified",
                "Stage-2 input requires contiguous verified Payload Objects",
            )
        previous_ordinal = partition["ordinal"]
        content = object_store.read(partition["storage_uri"])
        if (
            len(content) != partition["byte_size"]
            or hashlib.sha256(content).hexdigest() != partition["object_content_hash"]
        ):
            raise V022RuntimeDataError(
                "processing_intermediate_object_mismatch",
                "Stage-2 input bytes differ from their frozen object identity",
            )
        points.extend(
            _parse_intermediate_numeric_parquet(
                content, asset_keys, session_dates=session_dates
            )
        )
    ordered = tuple(points)
    if ordered != tuple(
        sorted(ordered, key=lambda item: (item.session_date, str(item.asset_id)))
    ):
        raise V022RuntimeContractError(
            "processing_intermediate_manifest_not_canonical",
            "Stage-2 input rows are not in canonical order",
        )
    return ordered


def _parse_intermediate_numeric_parquet(
    content: bytes,
    asset_keys: Mapping[uuid.UUID, str],
    *,
    session_dates: frozenset[date] | None = None,
) -> tuple[RepresentativeFeaturePoint, ...]:
    try:
        table = pq.read_table(io.BytesIO(content))
    except Exception as error:
        raise V022RuntimeDataError(
            "processing_intermediate_payload_unreadable",
            "Stage-2 input is not readable canonical Parquet",
        ) from error
    if not table.schema.equals(_intermediate_numeric_arrow_schema(), check_metadata=False):
        raise V022RuntimeContractError(
            "processing_intermediate_payload_schema_mismatch",
            "Stage-2 input does not satisfy intermediate_numeric_feature v1",
        )
    if session_dates is not None:
        table = table.filter(
            pc.is_in(
                table.column("session_date"),
                value_set=pa.array(sorted(session_dates), type=pa.date32()),
            )
        )
    result: list[RepresentativeFeaturePoint] = []
    for row in table.to_pylist():
        asset_id = uuid.UUID(str(row["asset_id"]))
        asset_key = asset_keys.get(asset_id)
        if asset_key is None:
            raise V022RuntimeDataError(
                "processing_intermediate_asset_unfrozen",
                "Stage-2 input contains an Asset outside the frozen context",
            )
        result.append(
            RepresentativeFeaturePoint(
                asset_id,
                asset_key,
                row["session_date"],
                row["feature_value"],
                row["known_at"].astimezone(UTC),
                row["input_revision"],
                row["missing_reason"],
            )
        )
    return tuple(result)


def publish_frozen_market_raw_payloads(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    compiled_execution_data_context_id: uuid.UUID,
    requested_start: date,
    requested_end: date,
) -> PublishedRawPayloadBundle:
    """Publish the exact Raw Manifest set required by one compiled context."""

    if requested_start > requested_end:
        raise ValueError("requested_start must not follow requested_end")
    context = _load_dataset_snapshot_context(
        engine,
        compiled_execution_data_context_id=compiled_execution_data_context_id,
        requested_start=requested_start,
        requested_end=requested_end,
    )
    return _publish_market_raw_payloads(engine, object_store=object_store, context=context)


def publish_product_market_raw_payloads(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    product_input_snapshot_id: uuid.UUID,
    compiled_research_graph_id: uuid.UUID,
) -> PublishedRawPayloadBundle:
    """Publish Raw Manifests from one immutable Product Input Snapshot.

    The Compiled Graph contributes only the frozen calculation/catalog definition.
    Dataset, members, Calendar, coverage, and source provenance all come from the
    Product Input Snapshot and its exact children.
    """

    context = _load_product_dataset_snapshot_context(
        engine,
        product_input_snapshot_id=product_input_snapshot_id,
        compiled_research_graph_id=compiled_research_graph_id,
    )
    return _publish_market_raw_payloads(engine, object_store=object_store, context=context)


def _publish_market_raw_payloads(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    context: _DatasetSnapshotContext,
) -> PublishedRawPayloadBundle:
    existing = _existing_raw_payload_bundle(engine, context=context)
    if existing is not None:
        return existing
    rows = _load_market_rows(
        engine,
        context=context,
        requested_start=context.coverage_start,
        requested_end=context.coverage_end,
    )
    service = ArtifactService(engine)
    outputs: list[PublishedRawPayload] = []
    publication_context_id = (
        context.product_input_snapshot_id
        or context.calculation_context_id
        or context.compiled_execution_data_context_id
    )
    # Prepare and publish one field at a time.  Keeping every encoded Parquet object
    # alive together triples the cold-start byte peak without adding atomicity: each
    # immutable Manifest already owns an independent Artifact transaction.
    for feature in context.features:
        item = _prepare_raw_payload(object_store, context, feature, rows)
        semantic = {
            "dataset_publication_id": context.dataset_publication_id,
            "feature_version_id": item.context.feature_version_id,
            "feature_variant_key": item.context.feature_variant_key,
            "snapshot_semantics": item.snapshot_semantics,
            "coverage_start": item.start,
            "coverage_end": item.end,
        }
        if context.product_input_snapshot_id is None:
            if context.calculation_context_id is None:
                raise V022RuntimeContractError(
                    "processing_calculation_context_missing",
                    "Research Raw publication requires a Calculation Context",
                )
            semantic["calculation_context_id"] = context.calculation_context_id
        else:
            semantic["product_input_snapshot_id"] = context.product_input_snapshot_id
        content = {
            **semantic,
            "manifest_hash": item.manifest_hash,
            "logical_payload_fingerprint": item.logical_payload_fingerprint,
            "object_content_hash": item.content_hash,
            "row_count": item.row_count,
        }
        result = service.publish(
            artifact_type="v022_payload_manifest",
            artifact_key=(
                f"dataset-output:{context.dataset_publication_id}:"
                f"{publication_context_id}:"
                f"{item.context.feature_variant_key}"
            ),
            version_number=1,
            semantic_payload=semantic,
            content_payload=content,
            dependencies=(
                DependencyInput(context.dataset_artifact_id, "dataset_publication", 0),
                DependencyInput(item.context.feature_artifact_id, "feature_version", 1),
                DependencyInput(context.encoding_artifact_id, "physical_encoding", 2),
                DependencyInput(
                    context.product_input_snapshot_artifact_id
                    or context.calculation_context_artifact_id
                    or context.execution_data_context_artifact_id,
                    (
                        "product_input_snapshot"
                        if context.product_input_snapshot_artifact_id is not None
                        else (
                            "processing_calculation_context"
                            if context.calculation_context_artifact_id is not None
                            else "compiled_execution_data_context"
                        )
                    ),
                    3,
                ),
            ),
            reason=(
                f"publish {'Product' if context.product_input_snapshot_id else 'exploratory'} "
                f"Dataset Raw Payload "
                f"{context.dataset_publication_id}:{item.context.feature_variant_key}"
            ),
            draft_writer=partial(
                _write_raw_payload,
                context=context,
                prepared=item,
            ),
        )
        with engine.connect() as connection:
            manifest_id = cast(
                uuid.UUID,
                connection.scalar(
                    text(
                        "SELECT payload_manifest_id FROM data.payload_manifest "
                        "WHERE artifact_id=:artifact"
                    ),
                    {"artifact": result.artifact_id},
                ),
            )
        outputs.append(
            PublishedRawPayload(
                feature_variant_key=item.context.feature_variant_key,
                feature_version_id=item.context.feature_version_id,
                payload_manifest_id=manifest_id,
                manifest_artifact_id=result.artifact_id,
                manifest_hash=item.manifest_hash,
                reused_publication=result.reused,
            )
        )
    return PublishedRawPayloadBundle(
        compiled_execution_data_context_id=context.compiled_execution_data_context_id,
        dataset_publication_id=context.dataset_publication_id,
        snapshot_semantic_mode=SNAPSHOT_SEMANTIC_MODE,
        outputs=tuple(sorted(outputs, key=lambda item: item.feature_variant_key)),
        product_input_snapshot_id=context.product_input_snapshot_id,
        calculation_context_id=context.calculation_context_id,
        calculation_context_artifact_id=context.calculation_context_artifact_id,
        calculation_context_fingerprint=context.calculation_context_fingerprint,
    )


def _existing_raw_payload_bundle(
    engine: Engine, *, context: _DatasetSnapshotContext
) -> PublishedRawPayloadBundle | None:
    """Resolve a complete immutable Raw binding without rereading market history."""

    expected = {item.feature_version_id: item for item in context.features}
    if context.product_input_snapshot_id is None:
        if context.calculation_context_id is None:
            raise V022RuntimeContractError(
                "processing_calculation_context_missing",
                "Research Raw lookup requires a Calculation Context",
            )
        statement = text(
            """
            SELECT binding.feature_version_id,binding.dataset_publication_id,
                   binding.payload_manifest_id,manifest.artifact_id,
                   manifest.manifest_hash,artifact.status
              FROM data.v022_calculation_context_payload_binding binding
              JOIN data.payload_manifest manifest
                ON manifest.payload_manifest_id=binding.payload_manifest_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
             WHERE binding.calculation_context_id=:identity
            """
        )
        identity = context.calculation_context_id
    else:
        statement = text(
            """
            SELECT binding.feature_version_id,binding.dataset_publication_id,
                   binding.payload_manifest_id,manifest.artifact_id,
                   manifest.manifest_hash,artifact.status
              FROM data.v022_product_input_payload_binding binding
              JOIN data.payload_manifest manifest
                ON manifest.payload_manifest_id=binding.payload_manifest_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=manifest.artifact_id
             WHERE binding.product_input_snapshot_id=:identity
            """
        )
        identity = context.product_input_snapshot_id
    with engine.connect() as connection:
        rows = tuple(connection.execute(statement, {"identity": identity}).mappings())
    actual = {row["feature_version_id"]: row for row in rows}
    if not rows:
        return None
    if (
        set(actual) != set(expected)
        or len(actual) != len(rows)
        or any(
            row["dataset_publication_id"] != context.dataset_publication_id
            or row["status"] != "published"
            for row in rows
        )
    ):
        raise V022RuntimeContractError(
            "raw_payload_binding_set_incomplete",
            "Existing Raw Payload bindings do not reproduce the frozen input set",
        )
    outputs = tuple(
        PublishedRawPayload(
            feature_variant_key=feature.feature_variant_key,
            feature_version_id=feature.feature_version_id,
            payload_manifest_id=actual[feature.feature_version_id]["payload_manifest_id"],
            manifest_artifact_id=actual[feature.feature_version_id]["artifact_id"],
            manifest_hash=actual[feature.feature_version_id]["manifest_hash"],
            reused_publication=True,
        )
        for feature in sorted(expected.values(), key=lambda item: item.feature_variant_key)
    )
    return PublishedRawPayloadBundle(
        compiled_execution_data_context_id=context.compiled_execution_data_context_id,
        dataset_publication_id=context.dataset_publication_id,
        snapshot_semantic_mode=SNAPSHOT_SEMANTIC_MODE,
        outputs=outputs,
        product_input_snapshot_id=context.product_input_snapshot_id,
        calculation_context_id=context.calculation_context_id,
        calculation_context_artifact_id=context.calculation_context_artifact_id,
        calculation_context_fingerprint=context.calculation_context_fingerprint,
    )


def materialize_representative_processing(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    compiled_research_graph_id: uuid.UUID,
    requested_start: date,
    requested_end: date,
    requested_by: str,
    executor_version: str,
    environment_fingerprint: str,
    product_input_snapshot_id: uuid.UUID | None = None,
) -> RepresentativeProcessingMaterialization:
    """Materialize the exact three-layer Processing topology of a compiled Graph.

    Raw payload publication remains shared, while every Processing Node, typed port,
    and downstream Aggregation input is resolved from the immutable compiled Graph.
    """

    if not requested_by.strip() or not executor_version.strip():
        raise ValueError("requested_by and executor_version must be nonblank")
    if requested_start > requested_end:
        raise ValueError("requested_start must not follow requested_end")
    if len(environment_fingerprint) != 64 or any(
        item not in "0123456789abcdef" for item in environment_fingerprint
    ):
        raise ValueError("environment_fingerprint must be a lowercase SHA-256 hash")
    requested_range: dict[str, object] = {
        "start": requested_start.isoformat(),
        "end": requested_end.isoformat(),
    }
    context_id, context_artifact_id = _execution_context_for_graph(
        engine, compiled_research_graph_id
    )
    if product_input_snapshot_id is not None:
        product_start, product_end = _product_input_range(
            engine, product_input_snapshot_id=product_input_snapshot_id
        )
        if requested_start != product_start or requested_end != product_end:
            raise V022RuntimeContractError(
                "product_processing_range_mismatch",
                "Product Processing must consume the exact frozen Snapshot interval",
            )
    raw_payloads = (
        publish_frozen_market_raw_payloads(
            engine,
            object_store=object_store,
            compiled_execution_data_context_id=context_id,
            requested_start=requested_start,
            requested_end=requested_end,
        )
        if product_input_snapshot_id is None
        else publish_product_market_raw_payloads(
            engine,
            object_store=object_store,
            product_input_snapshot_id=product_input_snapshot_id,
            compiled_research_graph_id=compiled_research_graph_id,
        )
    )
    raw_by_key = {item.feature_variant_key: item for item in raw_payloads.outputs}
    if not raw_by_key or not set(raw_by_key).issubset(RAW_FEATURE_KEYS):
        raise V022RuntimeDataError(
            "representative_raw_manifest_set_incomplete",
            "Catalog Processing requires a nonempty supported Raw Payload Manifest set",
        )
    processing_context_artifact_id = (
        raw_payloads.calculation_context_artifact_id or context_artifact_id
    )
    processing_context_role = (
        "processing_calculation_context"
        if raw_payloads.calculation_context_artifact_id is not None
        else "compiled_execution_data_context"
    )
    compiled = _load_compiled_terminal_nodes(
        engine,
        compiled_research_graph_id=compiled_research_graph_id,
        processing_context_artifact_id=processing_context_artifact_id,
        processing_context_role=processing_context_role,
        raw_payloads=raw_payloads.outputs,
        requested_range=requested_range,
        executor_version=executor_version,
        environment_fingerprint=environment_fingerprint,
    )
    plans = tuple(
        WorkPlan(
            occurrence_kind="node",
            occurrence_key=f"representative:{item.compiled_graph_node_id}",
            execution_fingerprint=item.execution_fingerprint,
            priority=10 + ordinal,
        )
        for ordinal, item in enumerate(compiled)
    )
    dag = GraphDagService(engine)
    graph_run = dag.plan_run(
        compiled_research_graph_id=compiled_research_graph_id,
        requested_by=requested_by,
        requested_range=requested_range,
        environment_fingerprint=environment_fingerprint,
        work=plans,
    )
    replayed = getattr(graph_run, "reused", False)
    if replayed:
        return _existing_representative_materialization(
            engine,
            graph_run_id=graph_run.graph_run_id,
            compiled_research_graph_id=compiled_research_graph_id,
            compiled_execution_data_context_id=context_id,
            requested_range=requested_range,
            raw_payloads=raw_payloads,
            compiled=compiled,
        )
    sessions = _requested_sessions(
        engine,
        dataset_publication_id=raw_payloads.dataset_publication_id,
        requested_start=requested_start,
        requested_end=requested_end,
    )
    source_revisions = {
        session: sha256_hexdigest(
            tuple(
                (key, raw_by_key[key].manifest_hash)
                for key in sorted(raw_by_key)
            )
            + (("session", session.isoformat()),)
        )
        for session in sessions
    }
    raw: FrozenRawInputs | None = None

    def full_raw() -> FrozenRawInputs:
        nonlocal raw
        if raw is None:
            raw = _load_frozen_raw_inputs(
                engine,
                compiled_execution_data_context_id=context_id,
                requested_start=requested_start,
                requested_end=requested_end,
                product_input_snapshot_id=product_input_snapshot_id,
                compiled_research_graph_id=compiled_research_graph_id,
            )
        return raw
    materialized: list[_MaterializedNodeTarget] = []
    telemetry = LocalRuntimeTelemetry()
    published: dict[str, PublishedNodeOutput] = (
        _existing_materialized_outputs(
            engine, graph_run_id=graph_run.graph_run_id, compiled=compiled
        )
        if replayed
        else {}
    )
    planned_items = () if replayed else zip(compiled, graph_run.work_item_ids, strict=True)
    for item, work_item_id in planned_items:
        status = _graph_work_status(engine, work_item_id)
        plan = plan_incremental_run(
            contract=item.execution_contract,
            partitions=partition_sessions_by_calendar_year(
                partition_key=item.execution_contract.partition_key,
                sessions=sessions,
            ),
            source_revisions=source_revisions,
        )
        if status in {"completed", "reused"}:
            node_run_id = _bind_reused_node_run(
                engine,
                graph_run_id=graph_run.graph_run_id,
                graph_work_item_id=work_item_id,
                compiled=item,
            )
            materialized.append(
                _MaterializedNodeTarget(item, work_item_id, node_run_id, plan, None)
            )
            continue
        claim = dag.claim(graph_run.graph_run_id, worker_key=executor_version)
        if claim is None or claim.graph_work_item_id != work_item_id:
            raise V022RuntimeContractError(
                "representative_graph_work_claim_mismatch",
                "Representative Processing could not claim its exact planned Node Work",
            )
        node_run_id = _publish_node_run(
            engine,
            graph_run_id=graph_run.graph_run_id,
            graph_work_item_id=work_item_id,
            compiled=item,
            raw_payloads=raw_payloads.outputs,
            processing_context_artifact_id=processing_context_artifact_id,
            processing_context_role=processing_context_role,
            requested_range=requested_range,
            executor_version=executor_version,
            environment_fingerprint=environment_fingerprint,
        )
        record_partition_plan(engine, node_run_id=node_run_id, plan=plan)
        current = _MaterializedNodeTarget(item, work_item_id, node_run_id, plan, claim)
        materialized.append(current)
        try:
            with telemetry.span(
                RuntimeTelemetryIdentity(
                    worker_key=executor_version,
                    stage="processing_stage1_node",
                    graph_run_id=graph_run.graph_run_id,
                    graph_work_item_id=work_item_id,
                    work_kind="node",
                ),
                details={
                    "feature_variant_key": item.feature_variant_key,
                    "implementation_key": item.implementation_key,
                },
            ) as span:
                with PeriodicLeaseHeartbeat(
                    partial(dag.renew, claim, worker_key=executor_version)
                ) as lease:
                    if item.feature_variant_key == AMIHUD_STAGE1_WORK_KEY:
                        if item.implementation_key is None or not item.additional_outputs:
                            raise V022RuntimeContractError(
                                "catalog_multi_output_compiled_identity_incomplete",
                                "Compiled Amihud Node lacks its implementation or "
                                "output identities",
                            )
                        result = publish_catalog_multi_output_stage1_node(
                            engine,
                            object_store=object_store,
                            raw=full_raw(),
                            target=CatalogMultiOutputPublicationTarget(
                                node_run_id,
                                item.implementation_key,
                                item.resolved_parameters,
                                item.additional_outputs,
                                plan,
                            ),
                        )
                    elif (
                        item.implementation_key
                        in CATALOG_SINGLE_OUTPUT_STAGE1_IMPLEMENTATIONS
                    ):
                        if item.implementation_key is None or item.output_unit is None:
                            raise V022RuntimeContractError(
                                "catalog_stage1_compiled_identity_incomplete",
                                "Compiled Stage-1 Node lacks implementation or "
                                "output-unit identity",
                            )
                        result = publish_partitioned_catalog_stage1_node_output(
                            engine,
                            object_store=object_store,
                            compiled_execution_data_context_id=context_id,
                            target=CatalogStage1PublicationTarget(
                                item.feature_variant_key,
                                item.implementation_key,
                                item.resolved_parameters,
                                node_run_id,
                                item.output_port_key,
                                item.output_unit,
                                plan,
                            ),
                            product_input_snapshot_id=product_input_snapshot_id,
                            compiled_research_graph_id=compiled_research_graph_id,
                        )
                    else:
                        result = _publish_representative_node_targets(
                            engine,
                            object_store=object_store,
                            raw=full_raw(),
                            targets=(
                                RepresentativeNodePublicationTarget(
                                    item.feature_variant_key,
                                    node_run_id,
                                    item.output_port_key,
                                    plan,
                                ),
                            ),
                        )
                if lease.error is not None:
                    raise RuntimeError("Stage-1 Work lease heartbeat failed") from lease.error
                outputs = tuple(result.outputs.values())
                span.record(
                    output_count=len(outputs),
                    executed_partition_count=sum(
                        output.executed_partition_count for output in outputs
                    ),
                    reused_partition_count=sum(
                        output.reused_partition_count for output in outputs
                    ),
                    cache_hit_count=sum(output.reused_publication for output in outputs),
                )
                published.update(result.outputs)
            dag.finish(claim, worker_key=executor_version, status="completed")
        except Exception:
            with suppress(Exception):
                dag.finish(
                    claim,
                    worker_key=executor_version,
                    status="failed",
                    details={"reason": "representative_processing_failed"},
                )
            raise
    for materialized_item in materialized:
        if materialized_item.claim is None:
            if materialized_item.compiled.feature_variant_key == AMIHUD_STAGE1_WORK_KEY:
                published.update(
                    {
                        variant: _existing_node_output(
                            engine, materialized_item.node_run_id, port
                        )
                        for variant, (port, _unit) in AMIHUD_STAGE1_OUTPUTS.items()
                    }
                )
            else:
                published[materialized_item.compiled.feature_variant_key] = (
                    _existing_node_output(
                        engine,
                        materialized_item.node_run_id,
                        materialized_item.compiled.output_port_key,
                    )
                )
    expected_stage1_outputs = {
        output_key
        for item in compiled
        for output_key in (
            tuple(item.additional_outputs)
            if item.feature_variant_key == AMIHUD_STAGE1_WORK_KEY
            else (item.feature_variant_key,)
        )
    }
    if set(published) != expected_stage1_outputs:
        raise V022RuntimeDataError(
            "catalog_stage1_node_output_set_incomplete",
            "Catalog Processing did not materialize its exact planned Stage-1 outputs",
        )
    asset_keys = _execution_context_asset_keys(
        engine, compiled_execution_data_context_id=context_id
    )
    raw = None
    _stage2_run_id, stage2_outputs = _materialize_representative_stage2(
        engine,
        object_store=object_store,
        compiled_research_graph_id=compiled_research_graph_id,
        processing_context_artifact_id=processing_context_artifact_id,
        processing_context_role=processing_context_role,
        requested_range=requested_range,
        requested_by=requested_by,
        executor_version=executor_version,
        environment_fingerprint=environment_fingerprint,
        sessions=sessions,
        source_revisions=source_revisions,
        sources={
            key: _published_feature_manifest(engine, key, output)
            for key, output in published.items()
        },
        asset_keys=asset_keys,
        fallback_graph_run_id=graph_run.graph_run_id,
    )
    published.update(stage2_outputs)
    stage3_run_id, stage3_outputs = _materialize_representative_stage3(
        engine,
        object_store=object_store,
        compiled_research_graph_id=compiled_research_graph_id,
        processing_context_artifact_id=processing_context_artifact_id,
        processing_context_role=processing_context_role,
        requested_range=requested_range,
        requested_by=requested_by,
        executor_version=executor_version,
        environment_fingerprint=environment_fingerprint,
        sessions=sessions,
        source_revisions=source_revisions,
        sources={
            key: _published_feature_manifest(engine, key, output)
            for key, output in published.items()
        },
        asset_keys=asset_keys,
        fallback_graph_run_id=_stage2_run_id,
    )
    published.update(stage3_outputs)
    aggregation_inputs = _aggregation_input_feature_keys(
        engine, compiled_research_graph_id
    )
    return RepresentativeProcessingMaterialization(
        graph_run_id=stage3_run_id,
        compiled_research_graph_id=compiled_research_graph_id,
        compiled_execution_data_context_id=context_id,
        requested_range=requested_range,
        raw_payloads=raw_payloads,
        stage3_outputs={key: published[key] for key in aggregation_inputs},
    )


def materialize_product_representative_processing(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    product_input_snapshot_id: uuid.UUID,
    compiled_research_graph_id: uuid.UUID,
    requested_by: str,
    executor_version: str,
    environment_fingerprint: str,
) -> RepresentativeProcessingMaterialization:
    """Materialize Processing for one exact Product Input Snapshot interval."""

    requested_start, requested_end = _product_input_range(
        engine, product_input_snapshot_id=product_input_snapshot_id
    )
    return materialize_representative_processing(
        engine,
        object_store=object_store,
        compiled_research_graph_id=compiled_research_graph_id,
        requested_start=requested_start,
        requested_end=requested_end,
        requested_by=requested_by,
        executor_version=executor_version,
        environment_fingerprint=environment_fingerprint,
        product_input_snapshot_id=product_input_snapshot_id,
    )


def _existing_representative_materialization(
    engine: Engine,
    *,
    graph_run_id: uuid.UUID,
    compiled_research_graph_id: uuid.UUID,
    compiled_execution_data_context_id: uuid.UUID,
    requested_range: Mapping[str, object],
    raw_payloads: PublishedRawPayloadBundle,
    compiled: tuple[_CompiledTerminalNode, ...],
) -> RepresentativeProcessingMaterialization:
    with engine.connect() as connection:
        run = connection.execute(
            text(
                "SELECT status FROM workspace.v022_graph_run WHERE graph_run_id=:run"
            ),
            {"run": graph_run_id},
        ).mappings().one()
        if run["status"] != "completed":
            raise V022RuntimeContractError(
                "representative_processing_replay_incomplete",
                "The exact representative Processing Run exists but is not complete",
            )
        bindings = connection.execute(
            text(
                """
                SELECT binding.compiled_graph_node_id,binding.node_run_id,
                       consumer.occurrence_key,work.execution_fingerprint,
                       work.status,node.status AS node_status
                  FROM workspace.v022_graph_work_consumer consumer
                  JOIN workspace.v022_graph_work_item work
                    ON work.graph_work_item_id=consumer.graph_work_item_id
                  JOIN processing.graph_run_node_binding binding
                    ON binding.graph_run_id=consumer.graph_run_id
                   AND binding.graph_work_item_id=consumer.graph_work_item_id
                  JOIN processing.node_run node ON node.node_run_id=binding.node_run_id
                 WHERE consumer.graph_run_id=:run
                """
            ),
            {"run": graph_run_id},
        ).mappings().all()
    expected = {
        (f"representative:{item.compiled_graph_node_id}", item.execution_fingerprint): item
        for item in compiled
    }
    actual = {(row["occurrence_key"], row["execution_fingerprint"]): row for row in bindings}
    if set(actual) != set(expected) or any(
        row["status"] not in {"completed", "reused"} or row["node_status"] != "completed"
        for row in actual.values()
    ):
        raise V022RuntimeContractError(
            "representative_processing_replay_incomplete",
            "The exact representative Stage-1 Run lacks its three completed Nodes",
        )
    outputs = {
        item.feature_variant_key: _existing_node_output(
            engine,
            actual[(f"representative:{item.compiled_graph_node_id}", item.execution_fingerprint)][
                "node_run_id"
            ],
            item.output_port_key,
        )
        for item in compiled
        if item.feature_variant_key in FINAL_FEATURE_KEYS
    }
    return RepresentativeProcessingMaterialization(
        graph_run_id=graph_run_id,
        compiled_research_graph_id=compiled_research_graph_id,
        compiled_execution_data_context_id=compiled_execution_data_context_id,
        requested_range=requested_range,
        raw_payloads=raw_payloads,
        stage3_outputs=outputs,
    )


def _existing_materialized_outputs(
    engine: Engine,
    *,
    graph_run_id: uuid.UUID,
    compiled: tuple[_CompiledTerminalNode, ...],
) -> dict[str, PublishedNodeOutput]:
    with engine.connect() as connection:
        bindings = {
            row["compiled_graph_node_id"]: row["node_run_id"]
            for row in connection.execute(
                text(
                    """
                    SELECT compiled_graph_node_id,node_run_id
                      FROM processing.graph_run_node_binding
                     WHERE graph_run_id=:run
                    """
                ),
                {"run": graph_run_id},
            ).mappings()
        }
    result: dict[str, PublishedNodeOutput] = {}
    for item in compiled:
        node_run_id = bindings[item.compiled_graph_node_id]
        if item.feature_variant_key == AMIHUD_STAGE1_WORK_KEY:
            result.update(
                {
                    variant: _existing_node_output(engine, node_run_id, port)
                    for variant, (port, _unit) in AMIHUD_STAGE1_OUTPUTS.items()
                }
            )
        else:
            result[item.feature_variant_key] = _existing_node_output(
                engine, node_run_id, item.output_port_key
            )
    return result


def publish_representative_stage3_node_outputs(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    raw: FrozenRawInputs,
    targets: tuple[RepresentativeNodePublicationTarget, ...],
) -> PublishedRepresentativeNodeOutputs:
    """Publish the three Stage-3 outputs into pre-created exact Node Runs.

    Production assembly remains responsible for creating and binding each Node Run
    to its compiled Graph Work item.  This function owns only the deterministic
    calculation and the existing atomic Payload publication path.
    """

    by_key = {item.feature_variant_key: item for item in targets}
    if len(by_key) != len(targets) or set(by_key) != set(FINAL_FEATURE_KEYS):
        raise V022RuntimeContractError(
            "representative_stage3_targets_incomplete",
            "Exactly one publication target is required for each representative Stage-3 signal",
        )
    return _publish_representative_node_targets(
        engine, object_store=object_store, raw=raw, targets=targets
    )


def publish_catalog_stage1_node_outputs(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    raw: FrozenRawInputs,
    targets: tuple[CatalogStage1PublicationTarget, ...],
) -> PublishedRepresentativeNodeOutputs:
    """Execute supported Catalog Stage-1 Nodes and publish their exact Node outputs.

    The caller owns Graph Work, Node Run, and partition-plan creation.  This boundary
    owns typed Catalog dispatch, intermediate Payload encoding, and atomic publication.
    """

    if not targets or len({item.feature_variant_key for item in targets}) != len(targets):
        raise V022RuntimeContractError(
            "catalog_stage1_targets_invalid",
            "Stage-1 publication targets must be non-empty and variant-unique",
        )
    target_bindings = {
        target.feature_variant_key: CATALOG_STAGE1_RAW_INPUTS.get(target.implementation_key)
        for target in targets
    }
    if any(bindings is None for bindings in target_bindings.values()):
        raise V022RuntimeContractError(
            "catalog_stage1_implementation_unsupported",
            "Stage-1 publisher does not support the requested implementation",
        )
    required_raw = {
        key
        for bindings in target_bindings.values()
        for key in cast(Mapping[str, str], bindings)
    }
    raw_sequences = {
        "adjusted_close": raw.adjusted_close,
        "close_raw": raw.close_raw,
        "volume_raw": raw.volume_raw,
    }
    raw_points = {
        key: _index_raw_points(raw_sequences[key], key) for key in sorted(required_raw)
    }
    published: dict[str, PublishedNodeOutput] = {}
    for target in sorted(targets, key=lambda item: item.feature_variant_key):
        bindings = cast(Mapping[str, str], target_bindings[target.feature_variant_key])
        selected_inputs = {key: raw_points[key] for key in bindings}
        identities = set(next(iter(selected_inputs.values())))
        if not identities or any(
            set(points) != identities for points in selected_inputs.values()
        ):
            raise V022RuntimeDataError(
                "catalog_stage1_raw_panel_incomplete",
                "Stage-1 inputs must contain exact aligned Raw panels",
            )
        identity_by_key_session = {
            (point.asset_key, identity[1]): identity
            for identity, point in next(iter(selected_inputs.values())).items()
        }
        input_revision = sha256_hexdigest(
            tuple(
                (
                    key,
                    str(identity[0]),
                    identity[1],
                    selected_inputs[key][identity].vintage_id,
                )
                for key in sorted(selected_inputs)
                for identity in sorted(
                    identities, key=lambda item: (item[1], str(item[0]))
                )
            )
        )
        execution = execute_catalog_node(
            target.implementation_key,
            parameters=target.parameters,
            input_ports={
                port: {
                    (point.asset_key, identity[1]): point.value
                    for identity, point in selected_inputs[key].items()
                }
                for key, port in bindings.items()
            },
        )
        if set(execution.output_ports) != {target.output_port_key}:
            raise V022RuntimeContractError(
                "catalog_stage1_output_port_mismatch",
                "Catalog execution output does not match the frozen Node output port",
            )
        values = execution.output_ports[target.output_port_key]
        points = tuple(
            sorted(
                (
                    RepresentativeFeaturePoint(
                        asset_id=identity_by_key_session[key][0],
                        asset_key=key[0],
                        session_date=key[1],
                        value=value,
                        known_at=max(
                            points[identity_by_key_session[key]].known_at
                            for points in selected_inputs.values()
                        ),
                        input_revision=input_revision,
                        missing_reason="insufficient_history" if value is None else None,
                    )
                    for key, value in values.items()
                ),
                key=lambda item: (item.session_date, str(item.asset_id)),
            )
        )
        payloads: list[ExecutedPartitionPayload] = []
        for work in target.plan.partitions:
            if work.disposition != "execute":
                raise V022RuntimeContractError(
                    "catalog_stage1_reuse_unsupported",
                    "The initial Catalog Stage-1 publisher requires fresh partitions",
                )
            output_sessions = frozenset(work.output_sessions)
            partition_points = tuple(
                point for point in points if point.session_date in output_sessions
            )
            if not partition_points:
                raise V022RuntimeDataError(
                    "catalog_stage1_partition_empty",
                    "A planned Stage-1 partition has no calculated feature rows",
                )
            payloads.append(
                ExecutedPartitionPayload(
                    partition_key_hash=work.partition_key_hash,
                    content=encode_intermediate_numeric_parquet(
                        partition_points, unit=target.output_unit
                    ),
                    statistics={
                        "feature_variant_key": target.feature_variant_key,
                        "missing_count": sum(
                            point.value is None for point in partition_points
                        ),
                        "finite_count": sum(
                            point.value is not None for point in partition_points
                        ),
                    },
                )
            )
        published[target.feature_variant_key] = publish_node_output(
            engine,
            object_store=object_store,
            node_run_id=target.node_run_id,
            output_port_key=target.output_port_key,
            plan=target.plan,
            executed_payloads=tuple(payloads),
            retention_class="research",
        )
    return PublishedRepresentativeNodeOutputs(published)


def publish_partitioned_catalog_stage1_node_output(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    compiled_execution_data_context_id: uuid.UUID,
    target: CatalogStage1PublicationTarget,
    product_input_snapshot_id: uuid.UUID | None = None,
    compiled_research_graph_id: uuid.UUID | None = None,
) -> PublishedRepresentativeNodeOutputs:
    """Execute one Catalog Stage-1 Node one frozen physical partition at a time."""

    bindings = CATALOG_STAGE1_RAW_INPUTS.get(target.implementation_key)
    if bindings is None:
        raise V022RuntimeContractError(
            "catalog_stage1_implementation_unsupported",
            "Stage-1 publisher does not support the requested implementation",
        )
    context = (
        _load_dataset_snapshot_context(
            engine,
            compiled_execution_data_context_id=compiled_execution_data_context_id,
            requested_start=target.plan.session_axis[0],
            requested_end=target.plan.session_axis[-1],
        )
        if product_input_snapshot_id is None
        else _load_product_dataset_snapshot_context(
            engine,
            product_input_snapshot_id=product_input_snapshot_id,
            compiled_research_graph_id=cast(uuid.UUID, compiled_research_graph_id),
        )
    )
    payloads: list[ExecutedPartitionPayload] = []
    for work in target.plan.partitions:
        if work.disposition != "execute":
            raise V022RuntimeContractError(
                "catalog_stage1_reuse_unsupported",
                "The initial Catalog Stage-1 publisher requires fresh partitions",
            )
        raw = _load_raw_inputs_from_context(
            engine,
            context=context,
            requested_start=work.calculation_sessions[0],
            requested_end=work.calculation_sessions[-1],
            required_features=frozenset(bindings),
            require_all_assets=False,
        )
        payloads.append(
            _execute_catalog_stage1_partition(
                raw=raw,
                target=target,
                output_sessions=frozenset(work.output_sessions),
                partition_key_hash=work.partition_key_hash,
            )
        )
    published = publish_node_output(
        engine,
        object_store=object_store,
        node_run_id=target.node_run_id,
        output_port_key=target.output_port_key,
        plan=target.plan,
        executed_payloads=tuple(payloads),
        retention_class="research",
    )
    return PublishedRepresentativeNodeOutputs({target.feature_variant_key: published})


def _execute_catalog_stage1_partition(
    *,
    raw: FrozenRawInputs,
    target: CatalogStage1PublicationTarget,
    output_sessions: frozenset[date],
    partition_key_hash: str,
) -> ExecutedPartitionPayload:
    bindings = CATALOG_STAGE1_RAW_INPUTS[target.implementation_key]
    raw_sequences = {
        "adjusted_close": raw.adjusted_close,
        "close_raw": raw.close_raw,
        "volume_raw": raw.volume_raw,
    }
    selected_inputs = {
        key: _index_raw_points(raw_sequences[key], key) for key in bindings
    }
    identities = set(next(iter(selected_inputs.values())))
    if not identities or any(set(points) != identities for points in selected_inputs.values()):
        raise V022RuntimeDataError(
            "catalog_stage1_raw_panel_incomplete",
            "Stage-1 inputs must contain exact aligned Raw panels",
        )
    identity_by_key_session = {
        (point.asset_key, identity[1]): identity
        for identity, point in next(iter(selected_inputs.values())).items()
    }
    input_revision = sha256_hexdigest(
        {
            "partition_key_hash": partition_key_hash,
            "calculation_start": min(identity[1] for identity in identities),
            "calculation_end": max(identity[1] for identity in identities),
            "inputs": tuple(
                (key, selected_inputs[key][identity].vintage_id)
                for key in sorted(selected_inputs)
                for identity in sorted(identities, key=lambda item: (item[1], str(item[0])))
            ),
        }
    )
    execution = execute_catalog_node(
        target.implementation_key,
        parameters=target.parameters,
        input_ports={
            port: {
                (point.asset_key, identity[1]): point.value
                for identity, point in selected_inputs[key].items()
            }
            for key, port in bindings.items()
        },
    )
    if set(execution.output_ports) != {target.output_port_key}:
        raise V022RuntimeContractError(
            "catalog_stage1_output_port_mismatch",
            "Catalog execution output does not match the frozen Node output port",
        )
    values = execution.output_ports[target.output_port_key]
    points = tuple(
        sorted(
            (
                RepresentativeFeaturePoint(
                    asset_id=identity_by_key_session[key][0],
                    asset_key=key[0],
                    session_date=key[1],
                    value=value,
                    known_at=max(
                        input_points[identity_by_key_session[key]].known_at
                        for input_points in selected_inputs.values()
                    ),
                    input_revision=input_revision,
                    missing_reason="insufficient_history" if value is None else None,
                )
                for key, value in values.items()
                if key[1] in output_sessions
            ),
            key=lambda item: (item.session_date, str(item.asset_id)),
        )
    )
    if not points:
        raise V022RuntimeDataError(
            "catalog_stage1_partition_empty",
            "A planned Stage-1 partition has no calculated feature rows",
        )
    return ExecutedPartitionPayload(
        partition_key_hash=partition_key_hash,
        content=encode_intermediate_numeric_parquet(points, unit=target.output_unit),
        statistics={
            "feature_variant_key": target.feature_variant_key,
            "missing_count": sum(point.value is None for point in points),
            "finite_count": sum(point.value is not None for point in points),
        },
    )


def publish_catalog_multi_output_stage1_node(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    raw: FrozenRawInputs,
    target: CatalogMultiOutputPublicationTarget,
) -> PublishedRepresentativeNodeOutputs:
    """Atomically publish every output of the supported Amihud Stage-1 Node."""

    if target.implementation_key != AMIHUD_DAILY_PRIMITIVES_IMPLEMENTATION:
        raise V022RuntimeContractError(
            "catalog_multi_output_implementation_unsupported",
            "The initial multi-output publisher supports only Amihud daily primitives",
        )
    raw_by_port = {
        "close_adj": _index_raw_points(raw.adjusted_close, "adjusted_close"),
        "close_raw": _index_raw_points(raw.close_raw, "close_raw"),
        "volume_raw": _index_raw_points(raw.volume_raw, "volume_raw"),
    }
    identities = set(raw_by_port["close_adj"])
    if not identities or any(set(points) != identities for points in raw_by_port.values()):
        raise V022RuntimeDataError(
            "catalog_multi_output_raw_panel_incomplete",
            "Amihud daily primitives require three exact aligned Raw panels",
        )
    execution = execute_catalog_node(
        target.implementation_key,
        parameters=target.parameters,
        input_ports={
            port: {
                (point.asset_key, identity[1]): point.value
                for identity, point in points.items()
            }
            for port, points in raw_by_port.items()
        },
    )
    expected_ports = {value[0] for value in target.outputs.values()}
    if set(execution.output_ports) != expected_ports or len(target.outputs) != 3:
        raise V022RuntimeContractError(
            "catalog_multi_output_port_mismatch",
            "Amihud execution must match its three frozen output ports",
        )
    input_revision = sha256_hexdigest(
        tuple(
            (
                port,
                str(identity[0]),
                identity[1],
                raw_by_port[port][identity].vintage_id,
            )
            for port in sorted(raw_by_port)
            for identity in sorted(identities, key=lambda item: (item[1], str(item[0])))
        )
    )
    payload_outputs: list[NodeOutputPayload] = []
    variant_by_port = {port: (variant, unit) for variant, (port, unit) in target.outputs.items()}
    for port, values in sorted(execution.output_ports.items()):
        variant, unit = variant_by_port[port]
        points = tuple(
            RepresentativeFeaturePoint(
                asset_id=identity[0],
                asset_key=raw_by_port["close_adj"][identity].asset_key,
                session_date=identity[1],
                value=values[(raw_by_port["close_adj"][identity].asset_key, identity[1])],
                known_at=max(raw_by_port[key][identity].known_at for key in raw_by_port),
                input_revision=input_revision,
                missing_reason=(
                    "insufficient_history_or_zero_volume"
                    if values[(raw_by_port["close_adj"][identity].asset_key, identity[1])]
                    is None
                    else None
                ),
            )
            for identity in sorted(identities, key=lambda item: (item[1], str(item[0])))
        )
        partitions: list[ExecutedPartitionPayload] = []
        for work in target.plan.partitions:
            if work.disposition != "execute":
                raise V022RuntimeContractError(
                    "catalog_multi_output_reuse_unsupported",
                    "The initial Amihud publisher requires fresh partitions",
                )
            output_sessions = frozenset(work.output_sessions)
            selected = tuple(
                point for point in points if point.session_date in output_sessions
            )
            if not selected:
                raise V022RuntimeDataError(
                    "catalog_multi_output_partition_empty",
                    "A planned Amihud partition has no calculated rows",
                )
            partitions.append(
                ExecutedPartitionPayload(
                    work.partition_key_hash,
                    encode_intermediate_numeric_parquet(selected, unit=unit),
                    {
                        "feature_variant_key": variant,
                        "missing_count": sum(point.value is None for point in selected),
                        "finite_count": sum(point.value is not None for point in selected),
                    },
                )
            )
        payload_outputs.append(NodeOutputPayload(port, tuple(partitions), "research"))
    bundle = publish_node_output_bundle(
        engine,
        object_store=object_store,
        node_run_id=target.node_run_id,
        plan=target.plan,
        outputs=tuple(payload_outputs),
    )
    published_by_port = {
        payload.output_port_key: published
        for payload, published in zip(
            sorted(payload_outputs, key=lambda item: item.output_port_key),
            bundle.outputs,
            strict=True,
        )
    }
    return PublishedRepresentativeNodeOutputs(
        {
            variant: published_by_port[port]
            for variant, (port, _unit) in target.outputs.items()
        }
    )


def publish_catalog_stage2_node_output(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    inputs: tuple[RepresentativeFeaturePoint, ...],
    target: CatalogStage2PublicationTarget,
) -> PublishedRepresentativeNodeOutputs:
    """Execute one supported Stage-2 Node from an exact intermediate Manifest."""

    panel: Panel = {
        (item.asset_key, item.session_date): item.value for item in inputs
    }
    if len(panel) != len(inputs):
        raise V022RuntimeDataError(
            "catalog_stage2_input_duplicate",
            "Stage-2 input contains duplicate Asset/session rows",
        )
    execution = execute_catalog_node(
        target.implementation_key,
        parameters=target.parameters,
        input_ports={target.input_port_key: panel},
    )
    if set(execution.output_ports) != {target.output_port_key}:
        raise V022RuntimeContractError(
            "catalog_stage2_output_port_mismatch",
            "Stage-2 execution output differs from its frozen Catalog port",
        )
    indexed = {(item.asset_key, item.session_date): item for item in inputs}
    points = tuple(
        RepresentativeFeaturePoint(
            source.asset_id,
            source.asset_key,
            source.session_date,
            value,
            source.known_at,
            source.input_revision,
            "insufficient_history" if value is None else None,
        )
        for key, value in sorted(
            execution.output_ports[target.output_port_key].items(),
            key=lambda item: (item[0][1], str(indexed[item[0]].asset_id)),
        )
        for source in (indexed[key],)
    )
    payloads: list[ExecutedPartitionPayload] = []
    for work in target.plan.partitions:
        if work.disposition != "execute":
            raise V022RuntimeContractError(
                "catalog_stage2_reuse_unsupported",
                "The initial Stage-2 publisher requires fresh partitions",
            )
        output_sessions = frozenset(work.output_sessions)
        selected = tuple(
            item for item in points if item.session_date in output_sessions
        )
        if not selected:
            raise V022RuntimeDataError(
                "catalog_stage2_partition_empty",
                "A planned Stage-2 partition has no calculated rows",
            )
        content = (
            encode_representative_final_signal_parquet(selected)
            if target.output_contract_key == "final_signal_numeric"
            else encode_intermediate_numeric_parquet(selected, unit=target.output_unit)
        )
        payloads.append(
            ExecutedPartitionPayload(
                work.partition_key_hash,
                content,
                {
                    "feature_variant_key": target.feature_variant_key,
                    "missing_count": sum(item.value is None for item in selected),
                    "finite_count": sum(item.value is not None for item in selected),
                },
            )
        )
    published = publish_node_output(
        engine,
        object_store=object_store,
        node_run_id=target.node_run_id,
        output_port_key=target.output_port_key,
        plan=target.plan,
        executed_payloads=tuple(payloads),
        retention_class="research",
    )
    return PublishedRepresentativeNodeOutputs({target.feature_variant_key: published})


def execute_catalog_stage2_from_manifest(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    input_manifest: PublishedFeatureManifest,
    asset_keys: Mapping[uuid.UUID, str],
    target: CatalogStage2PublicationTarget,
) -> PublishedRepresentativeNodeOutputs:
    """Read one exact Stage-1 Manifest, execute Stage 2, and publish its Node output."""

    inputs = read_intermediate_numeric_manifest(
        engine,
        object_store=object_store,
        manifest=input_manifest,
        asset_keys=asset_keys,
    )
    return publish_catalog_stage2_node_output(
        engine,
        object_store=object_store,
        inputs=inputs,
        target=target,
    )


def execute_partitioned_catalog_stage2_from_manifest(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    input_manifest: PublishedFeatureManifest,
    asset_keys: Mapping[uuid.UUID, str],
    target: CatalogStage2PublicationTarget,
) -> PublishedRepresentativeNodeOutputs:
    """Execute a Stage-2/3 node with one source/output partition resident at a time."""

    payloads: list[ExecutedPartitionPayload] = []
    for work in target.plan.partitions:
        if work.disposition != "execute":
            raise V022RuntimeContractError(
                "catalog_stage2_reuse_unsupported",
                "The initial Stage-2 publisher requires fresh partitions",
            )
        inputs = read_intermediate_numeric_manifest(
            engine,
            object_store=object_store,
            manifest=input_manifest,
            asset_keys=asset_keys,
            session_dates=frozenset(work.calculation_sessions),
        )
        payloads.append(
            _execute_catalog_stage2_partition(
                inputs=inputs,
                target=target,
                output_sessions=frozenset(work.output_sessions),
                partition_key_hash=work.partition_key_hash,
            )
        )
    published = publish_node_output(
        engine,
        object_store=object_store,
        node_run_id=target.node_run_id,
        output_port_key=target.output_port_key,
        plan=target.plan,
        executed_payloads=tuple(payloads),
        retention_class="research",
    )
    return PublishedRepresentativeNodeOutputs({target.feature_variant_key: published})


def _execute_catalog_stage2_partition(
    *,
    inputs: tuple[RepresentativeFeaturePoint, ...],
    target: CatalogStage2PublicationTarget,
    output_sessions: frozenset[date],
    partition_key_hash: str,
) -> ExecutedPartitionPayload:
    panel: Panel = {(item.asset_key, item.session_date): item.value for item in inputs}
    if len(panel) != len(inputs):
        raise V022RuntimeDataError(
            "catalog_stage2_input_duplicate",
            "Stage-2 input contains duplicate Asset/session rows",
        )
    execution = execute_catalog_node(
        target.implementation_key,
        parameters=target.parameters,
        input_ports={target.input_port_key: panel},
    )
    if set(execution.output_ports) != {target.output_port_key}:
        raise V022RuntimeContractError(
            "catalog_stage2_output_port_mismatch",
            "Stage-2 execution output differs from its frozen Catalog port",
        )
    indexed = {(item.asset_key, item.session_date): item for item in inputs}
    points = tuple(
        RepresentativeFeaturePoint(
            source.asset_id,
            source.asset_key,
            source.session_date,
            value,
            source.known_at,
            source.input_revision,
            "insufficient_history" if value is None else None,
        )
        for key, value in sorted(
            execution.output_ports[target.output_port_key].items(),
            key=lambda item: (item[0][1], str(indexed[item[0]].asset_id)),
        )
        for source in (indexed[key],)
        if source.session_date in output_sessions
    )
    if not points:
        raise V022RuntimeDataError(
            "catalog_stage2_partition_empty",
            "A planned Stage-2 partition has no calculated rows",
        )
    content = (
        encode_representative_final_signal_parquet(points)
        if target.output_contract_key == "final_signal_numeric"
        else encode_intermediate_numeric_parquet(points, unit=target.output_unit)
    )
    return ExecutedPartitionPayload(
        partition_key_hash,
        content,
        {
            "feature_variant_key": target.feature_variant_key,
            "missing_count": sum(item.value is None for item in points),
            "finite_count": sum(item.value is not None for item in points),
        },
    )


def _materialize_representative_stage2(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    compiled_research_graph_id: uuid.UUID,
    processing_context_artifact_id: uuid.UUID,
    processing_context_role: str,
    requested_range: Mapping[str, object],
    requested_by: str,
    executor_version: str,
    environment_fingerprint: str,
    sessions: tuple[date, ...],
    source_revisions: Mapping[date, str],
    sources: Mapping[str, PublishedFeatureManifest],
    asset_keys: Mapping[uuid.UUID, str],
    topology: _CatalogLayerTopology | None = None,
    fallback_graph_run_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, dict[str, PublishedNodeOutput]]:
    topology = topology or _catalog_layer_topology(
        engine, compiled_research_graph_id, stage_no=2
    )
    if not topology.feature_keys:
        if fallback_graph_run_id is None:
            raise V022RuntimeContractError(
                "catalog_manifest_layer_empty_without_predecessor",
                "An empty Stage-2 layer requires its predecessor Graph Run identity",
            )
        return fallback_graph_run_id, {}
    return _materialize_representative_manifest_layer(
        engine,
        object_store=object_store,
        compiled_research_graph_id=compiled_research_graph_id,
        processing_context_artifact_id=processing_context_artifact_id,
        processing_context_role=processing_context_role,
        requested_range=requested_range,
        requested_by=requested_by,
        executor_version=executor_version,
        environment_fingerprint=environment_fingerprint,
        sessions=sessions,
        source_revisions=source_revisions,
        sources=sources,
        asset_keys=asset_keys,
        feature_keys=topology.feature_keys,
        input_features=topology.input_features,
        input_ports=topology.input_ports,
        output_contracts=topology.output_contracts,
        layer_key="stage2",
        priority_base=30,
    )


def _materialize_representative_stage3(
    engine: Engine,
    *,
    topology: _CatalogLayerTopology | None = None,
    **kwargs: Any,
) -> tuple[uuid.UUID, dict[str, PublishedNodeOutput]]:
    compiled_research_graph_id = cast(uuid.UUID, kwargs["compiled_research_graph_id"])
    topology = topology or _catalog_layer_topology(
        engine, compiled_research_graph_id, stage_no=3
    )
    if not topology.feature_keys:
        fallback_graph_run_id = cast(
            uuid.UUID | None, kwargs.pop("fallback_graph_run_id", None)
        )
        if fallback_graph_run_id is None:
            raise V022RuntimeContractError(
                "catalog_manifest_layer_empty_without_predecessor",
                "An empty Stage-3 layer requires its predecessor Graph Run identity",
            )
        return fallback_graph_run_id, {}
    kwargs.pop("fallback_graph_run_id", None)
    return _materialize_representative_manifest_layer(
        engine,
        feature_keys=topology.feature_keys,
        input_features=topology.input_features,
        input_ports=topology.input_ports,
        output_contracts=topology.output_contracts,
        layer_key="stage3",
        priority_base=40,
        **kwargs,
    )


def _materialize_representative_manifest_layer(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    compiled_research_graph_id: uuid.UUID,
    processing_context_artifact_id: uuid.UUID,
    processing_context_role: str,
    requested_range: Mapping[str, object],
    requested_by: str,
    executor_version: str,
    environment_fingerprint: str,
    sessions: tuple[date, ...],
    source_revisions: Mapping[date, str],
    sources: Mapping[str, PublishedFeatureManifest],
    asset_keys: Mapping[uuid.UUID, str],
    feature_keys: tuple[str, ...],
    input_features: Mapping[str, str],
    input_ports: Mapping[str, str],
    output_contracts: Mapping[str, str],
    layer_key: str,
    priority_base: int,
) -> tuple[uuid.UUID, dict[str, PublishedNodeOutput]]:
    compiled = _load_compiled_manifest_nodes(
        engine,
        compiled_research_graph_id=compiled_research_graph_id,
        processing_context_artifact_id=processing_context_artifact_id,
        processing_context_role=processing_context_role,
        requested_range=requested_range,
        executor_version=executor_version,
        environment_fingerprint=environment_fingerprint,
        sources=sources,
        feature_keys=feature_keys,
        input_features=input_features,
        reader_contract="intermediate_numeric_feature_v1",
    )
    plans = tuple(
        WorkPlan(
            "node",
            f"representative-{layer_key}:{item.compiled_graph_node_id}",
            item.execution_fingerprint,
            priority=priority_base + ordinal,
        )
        for ordinal, item in enumerate(compiled)
    )
    dag = GraphDagService(engine)
    graph_run = dag.plan_run(
        compiled_research_graph_id=compiled_research_graph_id,
        requested_by=requested_by,
        requested_range=dict(requested_range),
        environment_fingerprint=environment_fingerprint,
        work=plans,
    )
    if graph_run.reused:
        return graph_run.graph_run_id, _existing_materialized_outputs(
            engine, graph_run_id=graph_run.graph_run_id, compiled=compiled
        )
    telemetry = LocalRuntimeTelemetry()
    published: dict[str, PublishedNodeOutput] = {}
    for item, work_item_id in zip(compiled, graph_run.work_item_ids, strict=True):
        source_key = input_features[item.feature_variant_key]
        source = sources[source_key]
        plan = plan_incremental_run(
            contract=item.execution_contract,
            partitions=partition_sessions_by_calendar_year(
                partition_key=item.execution_contract.partition_key,
                sessions=sessions,
            ),
            source_revisions={
                session: sha256_hexdigest(
                    (source.manifest_hash, source_key, source_revisions[session])
                )
                for session in sessions
            },
        )
        status = _graph_work_status(engine, work_item_id)
        if status in {"completed", "reused"}:
            node_run_id = _bind_reused_node_run(
                engine,
                graph_run_id=graph_run.graph_run_id,
                graph_work_item_id=work_item_id,
                compiled=item,
            )
            published[item.feature_variant_key] = _existing_node_output(
                engine,
                node_run_id,
                item.output_port_key,
            )
            continue
        claim = dag.claim(graph_run.graph_run_id, worker_key=executor_version)
        if claim is None or claim.graph_work_item_id != work_item_id:
            raise V022RuntimeContractError(
                f"representative_{layer_key}_claim_mismatch",
                f"{layer_key.title()} Processing could not claim its exact Node Work",
            )
        try:
            with telemetry.span(
                RuntimeTelemetryIdentity(
                    worker_key=executor_version,
                    stage=f"processing_{layer_key}_node",
                    graph_run_id=graph_run.graph_run_id,
                    graph_work_item_id=work_item_id,
                    work_kind="node",
                ),
                details={
                    "feature_variant_key": item.feature_variant_key,
                    "implementation_key": item.implementation_key,
                    "source_feature_key": source_key,
                },
            ) as span:
                with PeriodicLeaseHeartbeat(
                    partial(dag.renew, claim, worker_key=executor_version)
                ) as lease:
                    node_run_id = _publish_stage2_node_run(
                        engine,
                        graph_run_id=graph_run.graph_run_id,
                        graph_work_item_id=work_item_id,
                        compiled=item,
                        source=source,
                        input_port_key=input_ports[item.feature_variant_key],
                        processing_context_artifact_id=processing_context_artifact_id,
                        processing_context_role=processing_context_role,
                        requested_range=requested_range,
                        executor_version=executor_version,
                        environment_fingerprint=environment_fingerprint,
                    )
                    record_partition_plan(engine, node_run_id=node_run_id, plan=plan)
                    result = execute_partitioned_catalog_stage2_from_manifest(
                        engine,
                        object_store=object_store,
                        input_manifest=source,
                        asset_keys=asset_keys,
                        target=CatalogStage2PublicationTarget(
                            item.feature_variant_key,
                            cast(str, item.implementation_key),
                            item.resolved_parameters,
                            input_ports[item.feature_variant_key],
                            item.output_port_key,
                            cast(str, item.output_unit),
                            output_contracts[item.feature_variant_key],
                            node_run_id,
                            plan,
                        ),
                    )
                if lease.error is not None:
                    raise RuntimeError(
                        f"{layer_key.title()} Work lease heartbeat failed"
                    ) from lease.error
                outputs = tuple(result.outputs.values())
                span.record(
                    output_count=len(outputs),
                    executed_partition_count=sum(
                        output.executed_partition_count for output in outputs
                    ),
                    reused_partition_count=sum(
                        output.reused_partition_count for output in outputs
                    ),
                    cache_hit_count=sum(output.reused_publication for output in outputs),
                )
                published.update(result.outputs)
            dag.finish(claim, worker_key=executor_version, status="completed")
        except Exception:
            with suppress(Exception):
                dag.finish(
                    claim,
                    worker_key=executor_version,
                    status="failed",
                    details={"reason": f"representative_{layer_key}_failed"},
                )
            raise
    return graph_run.graph_run_id, published


def _publish_representative_node_targets(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    raw: FrozenRawInputs,
    targets: tuple[RepresentativeNodePublicationTarget, ...],
) -> PublishedRepresentativeNodeOutputs:
    by_key = {item.feature_variant_key: item for item in targets}
    if (
        not by_key
        or len(by_key) != len(targets)
        or not set(by_key).issubset(FINAL_FEATURE_KEYS)
    ):
        raise V022RuntimeContractError(
            "representative_stage3_targets_invalid",
            "Node publication targets must be a unique subset of the representative signals",
        )
    execution = execute_representative_snapshot(raw)
    published: dict[str, PublishedNodeOutput] = {}
    for feature_key, target in sorted(by_key.items()):
        points = execution.features[feature_key]
        payloads: list[ExecutedPartitionPayload] = []
        for work in target.plan.partitions:
            if work.disposition != "execute":
                raise V022RuntimeContractError(
                    "representative_stage3_reuse_unsupported",
                    "The initial representative vertical slice publishes fresh Node partitions",
                )
            output_sessions = frozenset(work.output_sessions)
            selected = tuple(
                item for item in points if item.session_date in output_sessions
            )
            if not selected:
                raise V022RuntimeDataError(
                    "representative_stage3_partition_empty",
                    "A planned Stage-3 partition has no calculated signal rows",
                )
            payloads.append(
                ExecutedPartitionPayload(
                    partition_key_hash=work.partition_key_hash,
                    content=encode_representative_final_signal_parquet(selected),
                    statistics={
                        "feature_variant_key": feature_key,
                        "missing_count": sum(item.value is None for item in selected),
                        "finite_count": sum(item.value is not None for item in selected),
                    },
                )
            )
        published[feature_key] = publish_node_output(
            engine,
            object_store=object_store,
            node_run_id=target.node_run_id,
            output_port_key=target.output_port_key,
            plan=target.plan,
            executed_payloads=tuple(payloads),
            retention_class="research",
        )
    return PublishedRepresentativeNodeOutputs(published)


def _execution_context_for_graph(
    engine: Engine, compiled_research_graph_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT context.compiled_execution_data_context_id,context.artifact_id,
                       artifact.status
                  FROM workspace.v022_compiled_execution_data_context context
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=context.artifact_id
                 WHERE context.compiled_research_graph_id=:graph
                """
            ),
            {"graph": compiled_research_graph_id},
        ).mappings().one_or_none()
    if row is None or row["status"] != "published":
        raise V022RuntimeContractError(
            "representative_execution_context_missing",
            "Compiled Graph has no published Execution Data Context",
        )
    return row["compiled_execution_data_context_id"], row["artifact_id"]


def _load_compiled_terminal_nodes(
    engine: Engine,
    *,
    compiled_research_graph_id: uuid.UUID,
    processing_context_artifact_id: uuid.UUID,
    processing_context_role: str,
    raw_payloads: tuple[PublishedRawPayload, ...],
    requested_range: Mapping[str, object],
    executor_version: str,
    environment_fingerprint: str,
) -> tuple[_CompiledTerminalNode, ...]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT occurrence.compiled_graph_node_id,occurrence.output_port_key,
                       feature_variant.variant_key AS feature_variant_key,
                       node.node_version_id,node_version.artifact_id,
                       node_variant.parameters,node_version.implementation_key,
                       feature.execution_semantics->>'unit' AS output_unit,
                       node_version.execution_contract,
                       node_version.determinism_policy,node_version.cache_policy,
                       artifact.status
                  FROM workspace.compiled_feature_occurrence occurrence
                  JOIN processing.feature_version feature
                    ON feature.feature_version_id=occurrence.feature_version_id
                  JOIN processing.feature_variant feature_variant
                    ON feature_variant.feature_variant_id=feature.feature_variant_id
                  JOIN workspace.compiled_graph_node node
                    ON node.compiled_graph_node_id=occurrence.compiled_graph_node_id
                  JOIN processing.node_version node_version
                    ON node_version.node_version_id=node.node_version_id
                  JOIN processing.node_variant node_variant
                    ON node_variant.node_variant_id=node_version.node_variant_id
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=node_version.artifact_id
                 WHERE occurrence.compiled_research_graph_id=:graph
                   AND occurrence.production_kind='node_output'
                   AND node_version.stage_no=1
                 ORDER BY feature_variant.variant_key
                """
            ),
            {"graph": compiled_research_graph_id},
        ).mappings().all()
    if not rows or any(row["status"] != "published" for row in rows):
        raise V022RuntimeContractError(
            "representative_compiled_node_set_incomplete",
            "Compiled Graph has no published Stage-1 producer Node set",
        )
    supported_implementations = CATALOG_SINGLE_OUTPUT_STAGE1_IMPLEMENTATIONS | {
        AMIHUD_DAILY_PRIMITIVES_IMPLEMENTATION
    }
    unsupported = sorted(
        {
            row["implementation_key"]
            for row in rows
            if row["implementation_key"] not in supported_implementations
        }
    )
    if unsupported:
        raise V022RuntimeContractError(
            "catalog_stage1_implementation_unsupported",
            "Compiled Graph contains a Stage-1 implementation not yet available "
            "in the v0.22 runtime: "
            + ", ".join(unsupported),
        )
    result: list[_CompiledTerminalNode] = []
    for row in rows:
        if row["feature_variant_key"] in AMIHUD_STAGE1_OUTPUTS:
            continue
        contract = _execution_contract(row["execution_contract"])
        input_features = CATALOG_STAGE1_RAW_INPUTS.get(row["implementation_key"])
        selected_raw = (
            tuple(
                item
                for item in raw_payloads
                if item.feature_variant_key in input_features
            )
            if input_features is not None
            else raw_payloads
        )
        ordered_inputs = tuple(
            (
                item.feature_variant_key,
                ordinal,
                item.payload_manifest_id,
                item.manifest_hash,
            )
            for ordinal, item in enumerate(
                sorted(selected_raw, key=lambda candidate: candidate.feature_variant_key)
            )
        )
        fingerprint = execution_fingerprint(
            component_version_id=row["node_version_id"],
            resolved_parameters=row["parameters"],
            ordered_input_manifests=ordered_inputs,
            resource_bindings=((
                processing_context_role,
                processing_context_artifact_id,
            ),),
            requested_range=dict(requested_range),
            executor_version=executor_version,
            environment_fingerprint=environment_fingerprint,
            determinism_policy=row["determinism_policy"],
            cache_policy=row["cache_policy"],
            payload_reader_contract="representative_frozen_snapshot_v1",
            target_or_fold_identity=row["feature_variant_key"],
        )
        result.append(
            _CompiledTerminalNode(
                feature_variant_key=row["feature_variant_key"],
                compiled_graph_node_id=row["compiled_graph_node_id"],
                node_version_id=row["node_version_id"],
                node_version_artifact_id=row["artifact_id"],
                resolved_parameters=row["parameters"],
                execution_contract=contract,
                determinism_policy=row["determinism_policy"],
                cache_policy=row["cache_policy"],
                output_port_key=row["output_port_key"],
                execution_fingerprint=fingerprint,
                implementation_key=row["implementation_key"],
                output_unit=row["output_unit"],
            )
        )
    amihud_rows = [
        row for row in rows if row["feature_variant_key"] in AMIHUD_STAGE1_OUTPUTS
    ]
    amihud_identity = {
        (
            row["compiled_graph_node_id"],
            row["node_version_id"],
            row["artifact_id"],
            row["implementation_key"],
        )
        for row in amihud_rows
    }
    if amihud_rows and (len(amihud_rows) != 3 or len(amihud_identity) != 1):
        raise V022RuntimeContractError(
            "representative_amihud_compiled_identity_invalid",
            "The three Amihud outputs must share one exact compiled Node identity",
        )
    if amihud_rows:
        amihud = amihud_rows[0]
        amihud_inputs = tuple(
            (
                item.feature_variant_key,
                ordinal,
                item.payload_manifest_id,
                item.manifest_hash,
            )
            for ordinal, item in enumerate(
                sorted(raw_payloads, key=lambda candidate: candidate.feature_variant_key)
            )
        )
        amihud_fingerprint = execution_fingerprint(
            component_version_id=amihud["node_version_id"],
            resolved_parameters=amihud["parameters"],
            ordered_input_manifests=amihud_inputs,
            resource_bindings=((
                processing_context_role,
                processing_context_artifact_id,
            ),),
            requested_range=dict(requested_range),
            executor_version=executor_version,
            environment_fingerprint=environment_fingerprint,
            determinism_policy=amihud["determinism_policy"],
            cache_policy=amihud["cache_policy"],
            payload_reader_contract="representative_frozen_snapshot_v1",
            target_or_fold_identity=AMIHUD_STAGE1_WORK_KEY,
        )
        result.append(
            _CompiledTerminalNode(
                feature_variant_key=AMIHUD_STAGE1_WORK_KEY,
                compiled_graph_node_id=amihud["compiled_graph_node_id"],
                node_version_id=amihud["node_version_id"],
                node_version_artifact_id=amihud["artifact_id"],
                resolved_parameters=amihud["parameters"],
                execution_contract=_execution_contract(amihud["execution_contract"]),
                determinism_policy=amihud["determinism_policy"],
                cache_policy=amihud["cache_policy"],
                output_port_key="",
                execution_fingerprint=amihud_fingerprint,
                implementation_key=amihud["implementation_key"],
                additional_outputs=AMIHUD_STAGE1_OUTPUTS,
            )
        )
    return tuple(sorted(result, key=lambda item: item.feature_variant_key))


def _load_compiled_manifest_nodes(
    engine: Engine,
    *,
    compiled_research_graph_id: uuid.UUID,
    processing_context_artifact_id: uuid.UUID,
    processing_context_role: str,
    requested_range: Mapping[str, object],
    executor_version: str,
    environment_fingerprint: str,
    sources: Mapping[str, PublishedFeatureManifest],
    feature_keys: tuple[str, ...],
    input_features: Mapping[str, str],
    reader_contract: str,
) -> tuple[_CompiledTerminalNode, ...]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT occurrence.compiled_graph_node_id,occurrence.output_port_key,
                       feature_variant.variant_key AS feature_variant_key,
                       node.node_version_id,node_version.artifact_id,
                       node_variant.parameters,node_version.implementation_key,
                       feature.execution_semantics->>'unit' AS output_unit,
                       node_version.execution_contract,node_version.determinism_policy,
                       node_version.cache_policy,artifact.status
                  FROM workspace.compiled_feature_occurrence occurrence
                  JOIN processing.feature_version feature
                    ON feature.feature_version_id=occurrence.feature_version_id
                  JOIN processing.feature_variant feature_variant
                    ON feature_variant.feature_variant_id=feature.feature_variant_id
                  JOIN workspace.compiled_graph_node node
                    ON node.compiled_graph_node_id=occurrence.compiled_graph_node_id
                  JOIN processing.node_version node_version
                    ON node_version.node_version_id=node.node_version_id
                  JOIN processing.node_variant node_variant
                    ON node_variant.node_variant_id=node_version.node_variant_id
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=node_version.artifact_id
                 WHERE occurrence.compiled_research_graph_id=:graph
                   AND occurrence.production_kind='node_output'
                   AND feature_variant.variant_key IN :keys
                 ORDER BY feature_variant.variant_key
                """
            ).bindparams(bindparam("keys", expanding=True)),
            {"graph": compiled_research_graph_id, "keys": feature_keys},
        ).mappings().all()
    if {row["feature_variant_key"] for row in rows} != set(feature_keys) or any(
        row["status"] != "published" for row in rows
    ):
        raise V022RuntimeContractError(
            "representative_manifest_layer_compiled_set_incomplete",
            "Compiled Graph lacks a required manifest-backed Processing Node",
        )
    result: list[_CompiledTerminalNode] = []
    for row in rows:
        source_key = input_features[row["feature_variant_key"]]
        source = sources[source_key]
        fingerprint = execution_fingerprint(
            component_version_id=row["node_version_id"],
            resolved_parameters=row["parameters"],
            ordered_input_manifests=((
                source_key,
                0,
                source.payload_manifest_id,
                source.manifest_hash,
            ),),
            resource_bindings=((
                processing_context_role,
                processing_context_artifact_id,
            ),),
            requested_range=dict(requested_range),
            executor_version=executor_version,
            environment_fingerprint=environment_fingerprint,
            determinism_policy=row["determinism_policy"],
            cache_policy=row["cache_policy"],
            payload_reader_contract=reader_contract,
            target_or_fold_identity=row["feature_variant_key"],
        )
        result.append(
            _CompiledTerminalNode(
                row["feature_variant_key"],
                row["compiled_graph_node_id"],
                row["node_version_id"],
                row["artifact_id"],
                row["parameters"],
                _execution_contract(row["execution_contract"]),
                row["determinism_policy"],
                row["cache_policy"],
                row["output_port_key"],
                fingerprint,
                row["implementation_key"],
                row["output_unit"],
            )
        )
    return tuple(result)


def _catalog_layer_topology(
    engine: Engine,
    compiled_research_graph_id: uuid.UUID,
    *,
    stage_no: int,
) -> _CatalogLayerTopology:
    if stage_no not in {2, 3}:
        raise ValueError("Catalog manifest layers must be Stage 2 or Stage 3")
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT output_variant.variant_key AS feature_variant_key,
                       input.input_port_key,
                       source_variant.variant_key AS source_feature_variant_key,
                       contract_family.contract_key AS output_contract_key
                  FROM workspace.compiled_graph_node node
                  JOIN workspace.compiled_feature_occurrence output_occurrence
                    ON output_occurrence.compiled_graph_node_id=node.compiled_graph_node_id
                   AND output_occurrence.production_kind='node_output'
                  JOIN processing.feature_version output_feature
                    ON output_feature.feature_version_id=output_occurrence.feature_version_id
                  JOIN processing.feature_variant output_variant
                    ON output_variant.feature_variant_id=output_feature.feature_variant_id
                  JOIN data.payload_contract_version contract_version
                    ON contract_version.payload_contract_version_id=
                       output_feature.payload_contract_version_id
                  JOIN data.payload_contract_family contract_family
                    ON contract_family.payload_contract_family_id=
                       contract_version.payload_contract_family_id
                  JOIN workspace.compiled_node_input input
                    ON input.compiled_graph_node_id=node.compiled_graph_node_id
                  JOIN workspace.compiled_feature_occurrence source_occurrence
                    ON source_occurrence.compiled_feature_occurrence_id=
                       input.source_occurrence_id
                  JOIN processing.feature_version source_feature
                    ON source_feature.feature_version_id=source_occurrence.feature_version_id
                  JOIN processing.feature_variant source_variant
                    ON source_variant.feature_variant_id=source_feature.feature_variant_id
                 WHERE node.compiled_research_graph_id=:graph
                   AND node.stage_no=:stage
                 ORDER BY output_variant.variant_key,input.ordinal
                """
            ),
            {"graph": compiled_research_graph_id, "stage": stage_no},
        ).mappings().all()
    grouped: dict[str, list[RowMapping]] = defaultdict(list)
    for row in rows:
        grouped[row["feature_variant_key"]].append(row)
    if any(len(items) != 1 for items in grouped.values()):
        raise V022RuntimeContractError(
            "catalog_manifest_layer_topology_unsupported",
            "Each compiled v0.22 parity Signal Node must have one exact input",
        )
    keys = tuple(sorted(grouped))
    return _CatalogLayerTopology(
        keys,
        {key: grouped[key][0]["source_feature_variant_key"] for key in keys},
        {key: grouped[key][0]["input_port_key"] for key in keys},
        {key: grouped[key][0]["output_contract_key"] for key in keys},
    )


def _aggregation_input_feature_keys(
    engine: Engine, compiled_research_graph_id: uuid.UUID
) -> tuple[str, ...]:
    with engine.connect() as connection:
        keys = tuple(
            connection.scalars(
                text(
                    """
                    SELECT DISTINCT variant.variant_key
                      FROM workspace.compiled_aggregation_input input
                      JOIN workspace.compiled_aggregation_instance instance
                        ON instance.compiled_aggregation_instance_id=
                           input.compiled_aggregation_instance_id
                      JOIN workspace.compiled_feature_occurrence occurrence
                        ON occurrence.compiled_feature_occurrence_id=
                           input.compiled_feature_occurrence_id
                      JOIN processing.feature_version feature
                        ON feature.feature_version_id=occurrence.feature_version_id
                      JOIN processing.feature_variant variant
                        ON variant.feature_variant_id=feature.feature_variant_id
                     WHERE instance.compiled_research_graph_id=:graph
                     ORDER BY variant.variant_key
                    """
                ),
                {"graph": compiled_research_graph_id},
            )
        )
    if not keys:
        raise V022RuntimeContractError(
            "catalog_aggregation_input_set_empty",
            "Compiled Graph has no frozen Aggregation input Signal",
        )
    return keys


def _published_feature_manifest(
    engine: Engine, feature_variant_key: str, output: PublishedNodeOutput
) -> PublishedFeatureManifest:
    with engine.connect() as connection:
        manifest_hash = connection.scalar(
            text(
                "SELECT manifest_hash FROM data.payload_manifest "
                "WHERE payload_manifest_id=:manifest AND artifact_id=:artifact"
            ),
            {
                "manifest": output.payload_manifest_id,
                "artifact": output.manifest_artifact_id,
            },
        )
    if not isinstance(manifest_hash, str):
        raise V022RuntimeDataError(
            "processing_published_manifest_missing",
            "Published Node output has no exact Payload Manifest identity",
        )
    return PublishedFeatureManifest(
        feature_variant_key,
        output.payload_manifest_id,
        output.manifest_artifact_id,
        manifest_hash,
    )


def _execution_contract(value: object) -> IncrementalExecutionContract:
    if not isinstance(value, dict):
        raise V022RuntimeContractError(
            "representative_node_execution_contract_invalid",
            "Representative Node execution contract is not an object",
        )
    partition_key = value.get("partition_key")
    if not isinstance(partition_key, list) or len(partition_key) != 1:
        raise V022RuntimeContractError(
            "representative_node_partition_contract_unsupported",
            "Representative vertical slice requires one frozen partition-key field",
        )
    try:
        return IncrementalExecutionContract(
            execution_mode=cast(Any, value.get("execution_mode")),
            partition_key=(str(partition_key[0]),),
            lookback=int(value.get("lookback", 0)),
            lookforward=int(value.get("lookforward", 0)),
            revision_impact_policy=cast(
                Any, value.get("revision_impact_policy", "windowed_forward")
            ),
        )
    except (TypeError, ValueError) as error:
        raise V022RuntimeContractError(
            "representative_node_execution_contract_invalid",
            "Representative Node has an unsupported execution contract",
        ) from error


def _requested_sessions(
    engine: Engine,
    *,
    dataset_publication_id: uuid.UUID,
    requested_start: date,
    requested_end: date,
) -> tuple[date, ...]:
    with engine.connect() as connection:
        sessions = tuple(
            connection.scalars(
                text(
                    """
                    SELECT DISTINCT session_date FROM data.daily_bar
                     WHERE dataset_publication_id=:dataset
                       AND session_date BETWEEN :start AND :end
                     ORDER BY session_date
                    """
                ),
                {
                    "dataset": dataset_publication_id,
                    "start": requested_start,
                    "end": requested_end,
                },
            )
        )
    if not sessions:
        raise V022RuntimeDataError(
            "representative_requested_sessions_empty",
            "Requested Dataset interval contains no completed sessions",
        )
    return sessions


def _load_frozen_raw_inputs(
    engine: Engine,
    *,
    compiled_execution_data_context_id: uuid.UUID,
    requested_start: date,
    requested_end: date,
    product_input_snapshot_id: uuid.UUID | None = None,
    compiled_research_graph_id: uuid.UUID | None = None,
    required_features: frozenset[str] = frozenset(RAW_DATA_FIELDS),
    require_all_assets: bool = True,
) -> FrozenRawInputs:
    if not required_features or not required_features.issubset(RAW_DATA_FIELDS):
        raise ValueError("required_features must be a nonempty supported Raw feature set")
    if product_input_snapshot_id is not None and compiled_research_graph_id is None:
        raise V022RuntimeContractError(
            "product_processing_graph_missing",
            "Product Processing requires one exact Compiled Graph calculation definition",
        )
    context = (
        _load_dataset_snapshot_context(
            engine,
            compiled_execution_data_context_id=compiled_execution_data_context_id,
            requested_start=requested_start,
            requested_end=requested_end,
        )
        if product_input_snapshot_id is None
        else _load_product_dataset_snapshot_context(
            engine,
            product_input_snapshot_id=product_input_snapshot_id,
            compiled_research_graph_id=cast(uuid.UUID, compiled_research_graph_id),
        )
    )
    return _load_raw_inputs_from_context(
        engine,
        context=context,
        requested_start=requested_start,
        requested_end=requested_end,
        required_features=required_features,
        require_all_assets=require_all_assets,
    )


def _load_raw_inputs_from_context(
    engine: Engine,
    *,
    context: _DatasetSnapshotContext,
    requested_start: date,
    requested_end: date,
    required_features: frozenset[str],
    require_all_assets: bool,
) -> FrozenRawInputs:
    security_by_legacy = {
        legacy_id: (security_id, asset_key)
        for security_id, (legacy_id, asset_key) in context.assets.items()
    }
    result: dict[str, list[RawSnapshotPoint]] = {
        feature_key: [] for feature_key in required_features
    }
    for row in _iter_market_rows(
        engine,
        context=context,
        requested_start=requested_start,
        requested_end=requested_end,
        require_all_assets=require_all_assets,
    ):
        security_id, asset_key = security_by_legacy[row["asset_id"]]
        for feature_key in sorted(required_features):
            source_field, unit = RAW_DATA_FIELDS[feature_key]
            result[feature_key].append(
                RawSnapshotPoint(
                    asset_id=security_id,
                    asset_key=asset_key,
                    session_date=row["session_date"],
                    value=Decimal(row[source_field]),
                    known_at=context.session_closes[row["session_date"]],
                    vintage_id=str(context.dataset_publication_id),
                    unit=unit,
                )
            )
    for points in result.values():
        points.sort(key=lambda item: (item.session_date, str(item.asset_id)))
    return FrozenRawInputs(
        adjusted_close=tuple(result.get("adjusted_close", ())),
        close_raw=tuple(result.get("close_raw", ())),
        volume_raw=tuple(result.get("volume_raw", ())),
    )


def _execution_context_asset_keys(
    engine: Engine, *, compiled_execution_data_context_id: uuid.UUID
) -> dict[uuid.UUID, str]:
    with engine.connect() as connection:
        security_ids_value = connection.scalar(
            text(
                """
                SELECT security_ids
                  FROM workspace.v022_compiled_execution_data_input
                 WHERE compiled_execution_data_context_id=:context
                   AND input_key=:input_key
                """
            ),
            {
                "context": compiled_execution_data_context_id,
                "input_key": CANONICAL_MARKET_INPUT,
            },
        )
        security_ids = _uuid_array(security_ids_value)
        rows = connection.execute(
            text(
                """
                SELECT security_id,security_key
                  FROM catalog.security
                 WHERE security_id IN :security_ids
                 ORDER BY security_id
                """
            ).bindparams(bindparam("security_ids", expanding=True)),
            {"security_ids": security_ids},
        ).mappings().all()
    if len(rows) != len(security_ids):
        raise V022RuntimeDataError(
            "representative_asset_mapping_unavailable",
            "Every frozen Security requires one canonical asset key",
        )
    return {row["security_id"]: row["security_key"] for row in rows}


def _product_input_range(
    engine: Engine, *, product_input_snapshot_id: uuid.UUID
) -> tuple[date, date]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT input.input_start,input.input_end,artifact.status
                  FROM product.v022_product_input_snapshot input
                  JOIN lineage.artifact artifact ON artifact.artifact_id=input.artifact_id
                 WHERE input.product_input_snapshot_id=:snapshot
                """
            ),
            {"snapshot": product_input_snapshot_id},
        ).mappings().one_or_none()
    if row is None or row["status"] != "published":
        raise V022RuntimeContractError(
            "product_input_snapshot_unavailable",
            "Product Processing requires one published Product Input Snapshot",
        )
    return row["input_start"], row["input_end"]


def _graph_work_status(engine: Engine, graph_work_item_id: uuid.UUID) -> str:
    with engine.connect() as connection:
        status = connection.scalar(
            text(
                "SELECT status FROM workspace.v022_graph_work_item "
                "WHERE graph_work_item_id=:item"
            ),
            {"item": graph_work_item_id},
        )
    if not isinstance(status, str):
        raise V022RuntimeContractError(
            "representative_graph_work_missing", "Planned Node Work is unavailable"
        )
    return status


def _publish_node_run(
    engine: Engine,
    *,
    graph_run_id: uuid.UUID,
    graph_work_item_id: uuid.UUID,
    compiled: _CompiledTerminalNode,
    raw_payloads: tuple[PublishedRawPayload, ...],
    processing_context_artifact_id: uuid.UUID,
    processing_context_role: str,
    requested_range: Mapping[str, object],
    executor_version: str,
    environment_fingerprint: str,
) -> uuid.UUID:
    selected_raw = _node_raw_inputs(compiled, raw_payloads)
    node_run_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"bird-v022:representative-node-run:{compiled.execution_fingerprint}",
    )
    semantic = {
        "node_run_id": node_run_id,
        "node_version_id": compiled.node_version_id,
        "execution_fingerprint": compiled.execution_fingerprint,
        "requested_range": requested_range,
        "executor_version": executor_version,
        "environment_fingerprint": environment_fingerprint,
    }
    result = ArtifactService(engine).publish(
        artifact_type="v022_node_run",
        artifact_key=str(node_run_id),
        version_number=1,
        semantic_payload=semantic,
        content_payload=semantic,
        dependencies=(
            DependencyInput(compiled.node_version_artifact_id, "node_version", 0),
            *tuple(
                DependencyInput(
                    item.manifest_artifact_id,
                    f"input:{item.feature_variant_key}",
                    ordinal + 1,
                )
                for ordinal, item in enumerate(
                    sorted(selected_raw, key=lambda candidate: candidate.feature_variant_key)
                )
            ),
            DependencyInput(
                processing_context_artifact_id,
                processing_context_role,
                4,
            ),
        ),
        reason=f"publish representative Node Run {node_run_id}",
        draft_writer=partial(
            _write_node_run,
            node_run_id=node_run_id,
            graph_run_id=graph_run_id,
            graph_work_item_id=graph_work_item_id,
            compiled=compiled,
            raw_payloads=selected_raw,
            requested_range=requested_range,
            executor_version=executor_version,
            environment_fingerprint=environment_fingerprint,
        ),
    )
    if result.reused:
        raise V022RuntimeContractError(
            "representative_node_run_unexpected_reuse",
            "Fresh Graph Work resolved to an already published Node Run",
        )
    return node_run_id


def _publish_stage2_node_run(
    engine: Engine,
    *,
    graph_run_id: uuid.UUID,
    graph_work_item_id: uuid.UUID,
    compiled: _CompiledTerminalNode,
    source: PublishedFeatureManifest,
    input_port_key: str,
    processing_context_artifact_id: uuid.UUID,
    processing_context_role: str,
    requested_range: Mapping[str, object],
    executor_version: str,
    environment_fingerprint: str,
) -> uuid.UUID:
    node_run_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"bird-v022:representative-node-run:{compiled.execution_fingerprint}",
    )
    semantic = {
        "node_run_id": node_run_id,
        "node_version_id": compiled.node_version_id,
        "execution_fingerprint": compiled.execution_fingerprint,
        "requested_range": requested_range,
        "executor_version": executor_version,
        "environment_fingerprint": environment_fingerprint,
    }
    result = ArtifactService(engine).publish(
        artifact_type="v022_node_run",
        artifact_key=str(node_run_id),
        version_number=1,
        semantic_payload=semantic,
        content_payload=semantic,
        dependencies=(
            DependencyInput(compiled.node_version_artifact_id, "node_version", 0),
            DependencyInput(source.manifest_artifact_id, f"input:{input_port_key}", 1),
            DependencyInput(
                processing_context_artifact_id,
                processing_context_role,
                2,
            ),
        ),
        reason=f"publish representative Stage-2 Node Run {node_run_id}",
        draft_writer=partial(
            _write_stage2_node_run,
            node_run_id=node_run_id,
            graph_run_id=graph_run_id,
            graph_work_item_id=graph_work_item_id,
            compiled=compiled,
            source=source,
            input_port_key=input_port_key,
            requested_range=requested_range,
            executor_version=executor_version,
            environment_fingerprint=environment_fingerprint,
        ),
    )
    if result.reused:
        raise V022RuntimeContractError(
            "representative_stage2_node_run_unexpected_reuse",
            "Fresh Stage-2 Graph Work resolved to an existing Node Run",
        )
    return node_run_id


def _write_stage2_node_run(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    node_run_id: uuid.UUID,
    graph_run_id: uuid.UUID,
    graph_work_item_id: uuid.UUID,
    compiled: _CompiledTerminalNode,
    source: PublishedFeatureManifest,
    input_port_key: str,
    requested_range: Mapping[str, object],
    executor_version: str,
    environment_fingerprint: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO processing.node_run (
              node_run_id,artifact_id,node_version_id,execution_fingerprint,
              resolved_parameters,requested_range,executor_version,
              environment_fingerprint,status,cache_eligible,started_at
            ) VALUES (
              :run,:artifact,:node,:fingerprint,CAST(:parameters AS jsonb),
              CAST(:range AS jsonb),:executor,:environment,'running',true,:started_at
            )
            """
        ),
        {
            "run": node_run_id,
            "artifact": artifact_id,
            "node": compiled.node_version_id,
            "fingerprint": compiled.execution_fingerprint,
            "parameters": _json(compiled.resolved_parameters),
            "range": _json(requested_range),
            "executor": executor_version,
            "environment": environment_fingerprint,
            "started_at": datetime.now(UTC),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO processing.node_run_input (
              node_run_id,input_port_key,payload_manifest_id,ordinal,manifest_hash
            ) VALUES (:run,:port,:manifest,0,:hash)
            """
        ),
        {
            "run": node_run_id,
            "port": input_port_key,
            "manifest": source.payload_manifest_id,
            "hash": source.manifest_hash,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO processing.graph_run_node_binding (
              graph_run_id,compiled_graph_node_id,graph_work_item_id,node_run_id,
              binding_disposition
            ) VALUES (:graph_run,:compiled_node,:work,:node_run,'executed')
            """
        ),
        {
            "graph_run": graph_run_id,
            "compiled_node": compiled.compiled_graph_node_id,
            "work": graph_work_item_id,
            "node_run": node_run_id,
        },
    )


def _write_node_run(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    node_run_id: uuid.UUID,
    graph_run_id: uuid.UUID,
    graph_work_item_id: uuid.UUID,
    compiled: _CompiledTerminalNode,
    raw_payloads: tuple[PublishedRawPayload, ...],
    requested_range: Mapping[str, object],
    executor_version: str,
    environment_fingerprint: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO processing.node_run (
              node_run_id,artifact_id,node_version_id,execution_fingerprint,
              resolved_parameters,requested_range,executor_version,
              environment_fingerprint,status,cache_eligible,started_at
            ) VALUES (
              :run,:artifact,:node,:fingerprint,CAST(:parameters AS jsonb),
              CAST(:range AS jsonb),:executor,:environment,'running',true,:started_at
            )
            """
        ),
        {
            "run": node_run_id,
            "artifact": artifact_id,
            "node": compiled.node_version_id,
            "fingerprint": compiled.execution_fingerprint,
            "parameters": _json(compiled.resolved_parameters),
            "range": _json(requested_range),
            "executor": executor_version,
            "environment": environment_fingerprint,
            "started_at": datetime.now(UTC),
        },
    )
    for ordinal, item in enumerate(
        sorted(raw_payloads, key=lambda candidate: candidate.feature_variant_key)
    ):
        connection.execute(
            text(
                """
                INSERT INTO processing.node_run_input (
                  node_run_id,input_port_key,payload_manifest_id,ordinal,manifest_hash
                ) VALUES (:run,:port,:manifest,:ordinal,:hash)
                """
            ),
            {
                "run": node_run_id,
                "port": _node_input_port(compiled, item.feature_variant_key),
                "manifest": item.payload_manifest_id,
                "ordinal": ordinal,
                "hash": item.manifest_hash,
            },
        )
    connection.execute(
        text(
            """
            INSERT INTO processing.graph_run_node_binding (
              graph_run_id,compiled_graph_node_id,graph_work_item_id,node_run_id,
              binding_disposition
            ) VALUES (:graph_run,:compiled_node,:work,:node_run,'executed')
            """
        ),
        {
            "graph_run": graph_run_id,
            "compiled_node": compiled.compiled_graph_node_id,
            "work": graph_work_item_id,
            "node_run": node_run_id,
        },
    )


def _bind_reused_node_run(
    engine: Engine,
    *,
    graph_run_id: uuid.UUID,
    graph_work_item_id: uuid.UUID,
    compiled: _CompiledTerminalNode,
) -> uuid.UUID:
    with engine.begin() as connection:
        node_run_id = connection.scalar(
            text(
                """
                SELECT node_run_id FROM processing.node_run
                 WHERE execution_fingerprint=:fingerprint AND status='completed'
                """
            ),
            {"fingerprint": compiled.execution_fingerprint},
        )
        if not isinstance(node_run_id, uuid.UUID):
            raise V022RuntimeDataError(
                "representative_reused_node_output_missing",
                "Reusable Graph Work has no completed Node Run",
            )
        connection.execute(
            text(
                """
                INSERT INTO processing.graph_run_node_binding (
                  graph_run_id,compiled_graph_node_id,graph_work_item_id,node_run_id,
                  binding_disposition
                ) VALUES (:graph_run,:compiled_node,:work,:node_run,'reused')
                ON CONFLICT (graph_run_id,compiled_graph_node_id) DO NOTHING
                """
            ),
            {
                "graph_run": graph_run_id,
                "compiled_node": compiled.compiled_graph_node_id,
                "work": graph_work_item_id,
                "node_run": node_run_id,
            },
        )
    return node_run_id


def _node_raw_inputs(
    compiled: _CompiledTerminalNode,
    raw_payloads: tuple[PublishedRawPayload, ...],
) -> tuple[PublishedRawPayload, ...]:
    if compiled.feature_variant_key == AMIHUD_STAGE1_WORK_KEY:
        return raw_payloads
    if compiled.implementation_key not in CATALOG_SINGLE_OUTPUT_STAGE1_IMPLEMENTATIONS:
        return raw_payloads
    expected = CATALOG_STAGE1_RAW_INPUTS[compiled.implementation_key]
    selected = tuple(
        item for item in raw_payloads if item.feature_variant_key in expected
    )
    if len(selected) != len(expected):
        raise V022RuntimeDataError(
            "catalog_stage1_raw_manifest_missing",
            "Catalog Stage-1 execution requires its exact frozen Raw input set",
        )
    return selected


def _node_input_port(compiled: _CompiledTerminalNode, raw_feature_key: str) -> str:
    if compiled.implementation_key in CATALOG_SINGLE_OUTPUT_STAGE1_IMPLEMENTATIONS:
        return CATALOG_STAGE1_RAW_INPUTS[compiled.implementation_key][raw_feature_key]
    if compiled.feature_variant_key == AMIHUD_STAGE1_WORK_KEY:
        return {
            "adjusted_close": "close_adj",
            "close_raw": "close_raw",
            "volume_raw": "volume_raw",
        }[raw_feature_key]
    return "representative_raw"


def _manifest_node_input_port(feature_variant_key: str) -> str:
    return {
        "price_cross_above_ma__s1_l200": "ma_ratio",
        "amihud_illiquidity__w20": "daily_price_impact",
        "return_continuation__w120": "feature",
        "low_illiquidity_quality__w20": "feature",
    }[feature_variant_key]


def _manifest_node_output_contract(feature_variant_key: str) -> str:
    return (
        "intermediate_numeric_feature"
        if feature_variant_key == "amihud_illiquidity__w20"
        else "final_signal_numeric"
    )


def _existing_node_output(
    engine: Engine, node_run_id: uuid.UUID, output_port_key: str
) -> PublishedNodeOutput:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT output.payload_manifest_id,manifest.artifact_id
                  FROM processing.node_run_output output
                  JOIN data.payload_manifest manifest
                    ON manifest.payload_manifest_id=output.payload_manifest_id
                 WHERE output.node_run_id=:run AND output.output_port_key=:port
                """
            ),
            {"run": node_run_id, "port": output_port_key},
        ).mappings().one_or_none()
        if row is None:
            raise V022RuntimeDataError(
                "representative_reused_manifest_missing",
                "Completed representative Node Run has no exact output Manifest",
            )
        partitions = tuple(
            connection.scalars(
                text(
                    """
                    SELECT payload_partition_id FROM data.payload_manifest_partition
                     WHERE payload_manifest_id=:manifest ORDER BY ordinal
                    """
                ),
                {"manifest": row["payload_manifest_id"]},
            )
        )
    return PublishedNodeOutput(
        node_run_id=node_run_id,
        payload_manifest_id=row["payload_manifest_id"],
        manifest_artifact_id=row["artifact_id"],
        payload_partition_ids=partitions,
        executed_partition_count=0,
        reused_partition_count=len(partitions),
        reused_publication=True,
    )


def _index_raw_points(
    points: tuple[RawSnapshotPoint, ...], label: str
) -> dict[tuple[uuid.UUID, date], RawSnapshotPoint]:
    if not points:
        raise V022RuntimeDataError(
            "representative_raw_input_missing", f"Required Raw input is missing: {label}"
        )
    indexed = {(item.asset_id, item.session_date): item for item in points}
    if len(indexed) != len(points):
        raise V022RuntimeDataError(
            "representative_raw_input_duplicate",
            f"Raw input contains duplicate Asset/session rows: {label}",
        )
    if any(item.known_at.utcoffset() is None or not item.vintage_id.strip() for item in points):
        raise V022RuntimeDataError(
            "representative_raw_input_pit_invalid",
            f"Raw input lacks a proven known-at or vintage identity: {label}",
        )
    return indexed


def _raw_arrow_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("session_date", pa.date32(), nullable=False),
            pa.field("asset_id", pa.string(), nullable=False),
            pa.field("value", pa.decimal128(38, 18), nullable=False),
            pa.field("known_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("vintage_id", pa.string(), nullable=False),
            pa.field("missing_reason", pa.string(), nullable=True),
            pa.field("unit", pa.string(), nullable=False),
        ]
    )


def _intermediate_numeric_arrow_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("session_date", pa.date32(), nullable=False),
            pa.field("asset_id", pa.string(), nullable=False),
            pa.field("feature_value", pa.decimal128(38, 18), nullable=True),
            pa.field("known_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("input_revision", pa.string(), nullable=False),
            pa.field("missing_reason", pa.string(), nullable=True),
            pa.field("unit", pa.string(), nullable=False),
        ]
    )


def _load_dataset_snapshot_context(
    engine: Engine,
    *,
    compiled_execution_data_context_id: uuid.UUID,
    requested_start: date,
    requested_end: date,
) -> _DatasetSnapshotContext:
    with engine.connect() as connection:
        header = connection.execute(
            text(
                """
                SELECT context.compiled_research_graph_id,
                       context.artifact_id AS execution_data_context_artifact_id,
                       graph.catalog_release_id,input.dataset_publication_id,
                       input.dataset_artifact_id,input.calendar_version_id,
                       calendar.artifact_id AS calendar_artifact_id,
                       input.coverage_start,input.coverage_end,
                       input.security_ids,dataset.value_kind,
                       dataset_artifact.status AS dataset_status,
                       calendar_artifact.status AS calendar_status
                  FROM workspace.v022_compiled_execution_data_context context
                  JOIN workspace.compiled_research_graph graph
                    ON graph.compiled_research_graph_id=context.compiled_research_graph_id
                  JOIN workspace.v022_compiled_execution_data_input input
                    ON input.compiled_execution_data_context_id=
                       context.compiled_execution_data_context_id
                   AND input.input_key=:input_key
                  JOIN data.dataset_publication dataset
                    ON dataset.dataset_publication_id=input.dataset_publication_id
                   AND dataset.artifact_id=input.dataset_artifact_id
                  JOIN lineage.artifact dataset_artifact
                    ON dataset_artifact.artifact_id=dataset.artifact_id
                  JOIN catalog.calendar_version calendar
                    ON calendar.calendar_version_id=input.calendar_version_id
                  JOIN lineage.artifact calendar_artifact
                    ON calendar_artifact.artifact_id=calendar.artifact_id
                 WHERE context.compiled_execution_data_context_id=:context
                """
            ),
            {"context": compiled_execution_data_context_id, "input_key": CANONICAL_MARKET_INPUT},
        ).mappings().one_or_none()
        if (
            header is None
            or header["value_kind"] != "daily_bar"
            or header["dataset_status"] != "published"
            or header["calendar_status"] != "published"
            or header["coverage_start"] > requested_start
            or header["coverage_end"] < requested_end
        ):
            raise V022RuntimeDataError(
                "representative_market_dataset_unavailable",
                "The compiled context has no exact published daily-bar Dataset for the request",
            )
        security_ids = _uuid_array(header["security_ids"])
        if not isinstance(header["calendar_version_id"], uuid.UUID):
            raise V022RuntimeDataError(
                "representative_market_calendar_unavailable",
                "The frozen daily-bar Dataset requires one exact exchange Calendar",
            )
        session_rows = connection.execute(
            text(
                """
                SELECT session_date,close_at_utc
                  FROM catalog.calendar_session
                 WHERE calendar_version_id=:calendar
                   AND session_date BETWEEN :start AND :end
                 ORDER BY session_date
                """
            ),
            {
                "calendar": header["calendar_version_id"],
                "start": header["coverage_start"],
                "end": header["coverage_end"],
            },
        ).mappings().all()
        if not session_rows or any(
            row["close_at_utc"].utcoffset() is None for row in session_rows
        ):
            raise V022RuntimeDataError(
                "representative_market_calendar_unavailable",
                "The exact exchange Calendar has no timezone-aware session closes",
            )
        executable_start = session_rows[0]["session_date"]
        executable_end = session_rows[-1]["session_date"]
        if requested_start < executable_start or requested_end > executable_end:
            raise V022RuntimeDataError(
                "representative_market_calendar_unavailable",
                "The requested interval falls outside the exact exchange Calendar",
            )
        session_closes = {
            row["session_date"]: row["close_at_utc"].astimezone(UTC)
            for row in session_rows
        }
        asset_rows = connection.execute(
            text(
                """
                SELECT security.security_id,security.legacy_asset_id,security.security_key
                  FROM catalog.security security
                 WHERE security.security_id IN :security_ids
                 ORDER BY security.security_id
                """
            ).bindparams(bindparam("security_ids", expanding=True)),
            {"security_ids": security_ids},
        ).mappings().all()
        if len(asset_rows) != len(security_ids) or any(
            row["legacy_asset_id"] is None for row in asset_rows
        ):
            raise V022RuntimeDataError(
                "representative_asset_mapping_unavailable",
                "Every frozen Security requires one canonical daily-bar Asset identity",
            )
        assets = {
            row["security_id"]: (row["legacy_asset_id"], row["security_key"])
            for row in asset_rows
        }
        security_identifier_rows = connection.execute(
            text(
                """
                SELECT identifier.security_id,identifier.identifier_value
                  FROM catalog.security_identifier identifier
                 WHERE identifier.security_id IN :security_ids
                   AND identifier.identifier_type IN ('symbol','alias')
                 ORDER BY identifier.security_id,identifier.identifier_type,
                          identifier.identifier_value
                """
            ).bindparams(bindparam("security_ids", expanding=True)),
            {"security_ids": list(assets)},
        ).mappings().all()
        snapshot_rows = cast(
            list[Mapping[str, object]],
            list(
                connection.execute(
                text(
                """
                SELECT snapshot.artifact_id,snapshot.fetched_at,snapshot.as_of_at,
                       lower(coalesce(snapshot.request_parameters->>'tickers',
                                      snapshot.request_parameters->>'ticker')) AS asset_symbol,
                       artifact.status,
                       subject.security_id AS subject_security_id,
                       subject.fetch_status AS subject_fetch_status
                  FROM data.dataset_input input
                  JOIN data.source_snapshot snapshot
                    ON snapshot.source_snapshot_id=input.source_snapshot_id
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=snapshot.artifact_id
                  LEFT JOIN data.source_snapshot_security_subject subject
                    ON subject.source_snapshot_id=snapshot.source_snapshot_id
                 WHERE input.dataset_publication_id=:dataset
                 ORDER BY input.ordinal
                """
                ),
                {"dataset": header["dataset_publication_id"]},
                ).mappings().all()
            ),
        )
        if not snapshot_rows:
            import_proof = connection.execute(
                text(
                    """
                    SELECT import_manifest.artifact_id,import_manifest.created_at,
                           artifact.status
                      FROM lineage.artifact_dependency dependency
                      JOIN data.v022_external_import_manifest import_manifest
                        ON import_manifest.artifact_id=dependency.depends_on_artifact_id
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=import_manifest.artifact_id
                     WHERE dependency.artifact_id=:dataset_artifact
                       AND dependency.role='external_import_manifest'
                     ORDER BY import_manifest.external_import_manifest_id
                    """
                ),
                {"dataset_artifact": header["dataset_artifact_id"]},
            ).mappings().all()
            if len(import_proof) == 1:
                proof = import_proof[0]
                snapshot_rows = [
                    {
                        "artifact_id": proof["artifact_id"],
                        "fetched_at": proof["created_at"],
                        "as_of_at": proof["created_at"],
                        "asset_symbol": security_key.casefold(),
                        "status": proof["status"],
                        "subject_security_id": security_id,
                        "subject_fetch_status": "fetched",
                    }
                    for security_id, (_asset_id, security_key) in assets.items()
                ]
        snapshots = _resolve_asset_snapshot_proofs(
            assets=assets,
            snapshot_rows=cast(tuple[Mapping[str, object], ...], tuple(snapshot_rows)),
            security_identifier_rows=cast(
                tuple[Mapping[str, object], ...], tuple(security_identifier_rows)
            ),
        )
        feature_rows = connection.execute(
            text(
                """
                SELECT occurrence.feature_version_id,variant.variant_key,
                       feature.artifact_id AS feature_artifact_id,
                       feature.payload_contract_version_id,
                       contract.artifact_id AS payload_contract_artifact_id,
                       feature.output_port_key,feature.execution_semantics->>'source_field'
                         AS source_field,
                       feature.execution_semantics->>'unit' AS unit,
                       component.component_artifact_id
                  FROM workspace.compiled_feature_occurrence occurrence
                  JOIN processing.feature_version feature
                    ON feature.feature_version_id=occurrence.feature_version_id
                  JOIN processing.feature_variant variant
                    ON variant.feature_variant_id=feature.feature_variant_id
                  JOIN data.payload_contract_version contract
                    ON contract.payload_contract_version_id=
                       feature.payload_contract_version_id
                  LEFT JOIN workspace.v022_catalog_release_component component
                    ON component.catalog_release_id=:release
                   AND component.component_artifact_id=feature.artifact_id
                   AND component.component_kind='feature_version'
                 WHERE occurrence.compiled_research_graph_id=:graph
                   AND occurrence.production_kind='raw_input'
                 ORDER BY variant.variant_key
                """
            ),
            {
                "release": header["catalog_release_id"],
                "graph": header["compiled_research_graph_id"],
            },
        ).mappings().all()
        raw_feature_keys = {row["variant_key"] for row in feature_rows}
        if not raw_feature_keys or not raw_feature_keys.issubset(RAW_FEATURE_KEYS) or any(
            row["component_artifact_id"] is None
            or not row["source_field"]
            or not row["unit"]
            for row in feature_rows
        ):
            raise V022RuntimeContractError(
                "representative_raw_catalog_incomplete",
                "The compiled Graph contains no complete supported Raw Feature Version set",
            )
        encoding = connection.execute(
            text(
                """
                SELECT encoding.physical_encoding_version_id,encoding.artifact_id
                  FROM data.physical_encoding_version encoding
                  JOIN workspace.v022_catalog_release_component component
                    ON component.catalog_release_id=:release
                   AND component.component_artifact_id=encoding.artifact_id
                   AND component.component_kind='physical_encoding_version'
                 WHERE encoding.encoding_key=:key AND encoding.version_number=:version
                """
            ),
            {
                "release": header["catalog_release_id"],
                "key": CANONICAL_ENCODING_KEY,
                "version": CANONICAL_ENCODING_VERSION,
            },
        ).mappings().one()
    context = _DatasetSnapshotContext(
        compiled_execution_data_context_id=compiled_execution_data_context_id,
        execution_data_context_artifact_id=header[
            "execution_data_context_artifact_id"
        ],
        dataset_publication_id=header["dataset_publication_id"],
        dataset_artifact_id=header["dataset_artifact_id"],
        catalog_release_id=header["catalog_release_id"],
        encoding_id=encoding["physical_encoding_version_id"],
        encoding_artifact_id=encoding["artifact_id"],
        assets=assets,
        snapshots=snapshots,
        session_closes=session_closes,
        features=tuple(
            _RawFeatureContext(
                feature_variant_key=row["variant_key"],
                source_field=row["source_field"],
                unit=row["unit"],
                feature_version_id=row["feature_version_id"],
                feature_artifact_id=row["feature_artifact_id"],
                payload_contract_version_id=row["payload_contract_version_id"],
                payload_contract_artifact_id=row["payload_contract_artifact_id"],
                output_port_key=row["output_port_key"],
            )
            for row in feature_rows
        ),
        coverage_start=executable_start,
        coverage_end=executable_end,
        calendar_version_id=header["calendar_version_id"],
        calendar_artifact_id=header["calendar_artifact_id"],
    )
    calculation = ProcessingCalculationContextService(engine).publish(
        ProcessingCalculationContextSpec(
            compiled_execution_data_context_id=compiled_execution_data_context_id,
            dataset_publication_id=context.dataset_publication_id,
            dataset_artifact_id=context.dataset_artifact_id,
            calendar_version_id=header["calendar_version_id"],
            calendar_artifact_id=header["calendar_artifact_id"],
            coverage_start=context.coverage_start,
            coverage_end=context.coverage_end,
            security_ids=tuple(sorted(context.assets, key=str)),
            raw_feature_versions=tuple(
                sorted(
                    (
                        (item.feature_version_id, item.feature_artifact_id)
                        for item in context.features
                    ),
                    key=lambda item: str(item[0]),
                )
            ),
            source_snapshot_artifact_ids=tuple(
                sorted({item[0] for item in context.snapshots.values()}, key=str)
            ),
        )
    )
    return replace(
        context,
        calculation_context_id=calculation.calculation_context_id,
        calculation_context_artifact_id=calculation.artifact_id,
        calculation_context_fingerprint=calculation.context_fingerprint,
    )


def _load_product_dataset_snapshot_context(
    engine: Engine,
    *,
    product_input_snapshot_id: uuid.UUID,
    compiled_research_graph_id: uuid.UUID,
) -> _DatasetSnapshotContext:
    with engine.connect() as connection:
        header = connection.execute(
            text(
                """
                SELECT input.artifact_id AS product_input_snapshot_artifact_id,
                       input.dataset_publication_id,input.dataset_artifact_id,
                       input.calendar_version_id,input.input_start,input.input_end,
                       input.snapshot_document,artifact.status AS input_status,
                       dataset.value_kind,dataset.coverage_start AS dataset_start,
                       dataset.coverage_end AS dataset_end,
                       dataset_artifact.status AS dataset_status,
                       graph.catalog_release_id,
                       context.compiled_execution_data_context_id,
                       context.artifact_id AS execution_data_context_artifact_id,
                       context_artifact.status AS context_status
                  FROM product.v022_product_input_snapshot input
                  JOIN lineage.artifact artifact ON artifact.artifact_id=input.artifact_id
                  JOIN data.dataset_publication dataset
                    ON dataset.dataset_publication_id=input.dataset_publication_id
                   AND dataset.artifact_id=input.dataset_artifact_id
                  JOIN lineage.artifact dataset_artifact
                    ON dataset_artifact.artifact_id=dataset.artifact_id
                  JOIN product.v022_product_enrollment enrollment
                    ON enrollment.product_enrollment_id=input.product_enrollment_id
                  JOIN product.v022_execution_version execution
                    ON execution.execution_version_id=enrollment.execution_version_id
                  JOIN experiment.v022_research_configuration_snapshot configuration
                    ON configuration.configuration_snapshot_id=
                       execution.configuration_snapshot_id
                  JOIN workspace.compiled_research_graph graph
                    ON graph.compiled_research_graph_id=
                       configuration.compiled_research_graph_id
                   AND graph.compiled_research_graph_id=:graph
                  JOIN workspace.v022_compiled_execution_data_context context
                    ON context.compiled_research_graph_id=graph.compiled_research_graph_id
                  JOIN lineage.artifact context_artifact
                    ON context_artifact.artifact_id=context.artifact_id
                 WHERE input.product_input_snapshot_id=:snapshot
                """
            ),
            {"snapshot": product_input_snapshot_id, "graph": compiled_research_graph_id},
        ).mappings().one_or_none()
        if (
            header is None
            or header["input_status"] != "published"
            or header["context_status"] != "published"
            or header["dataset_status"] != "published"
            or header["value_kind"] != "daily_bar"
            or header["dataset_start"] > header["input_start"]
            or header["dataset_end"] < header["input_end"]
        ):
            raise V022RuntimeDataError(
                "product_market_snapshot_unavailable",
                "Product Raw execution requires one published Snapshot and its exact "
                "daily-bar Dataset",
            )
        snapshot_document = header["snapshot_document"]
        if (
            not isinstance(snapshot_document, dict)
            or not isinstance(snapshot_document.get("member_count"), int)
            or snapshot_document["member_count"] < 1
        ):
            raise V022RuntimeContractError(
                "product_market_member_set_incomplete",
                "Product Input Snapshot has no canonical member-count identity",
            )
        member_rows = connection.execute(
            text(
                """
                SELECT member.security_id,member.legacy_asset_id,member.asset_key,
                       member.ordinal,member.is_uniformly_excluded
                  FROM product.v022_product_input_member member
                 WHERE member.product_input_snapshot_id=:snapshot
                 ORDER BY member.ordinal
                """
            ),
            {"snapshot": product_input_snapshot_id},
        ).mappings().all()
        expected_member_count = snapshot_document["member_count"]
        if (
            not member_rows
            or len(member_rows) != expected_member_count
            or [row["ordinal"] for row in member_rows] != list(range(len(member_rows)))
        ):
            raise V022RuntimeContractError(
                "product_market_member_set_incomplete",
                "Product Input Snapshot has no exact complete member projection",
            )
        assets = _product_executable_assets(member_rows)
        session_rows = connection.execute(
            text(
                """
                SELECT session_date,close_at_utc
                  FROM catalog.calendar_session
                 WHERE calendar_version_id=:calendar
                   AND session_date BETWEEN :start AND :end
                 ORDER BY session_date
                """
            ),
            {
                "calendar": header["calendar_version_id"],
                "start": header["input_start"],
                "end": header["input_end"],
            },
        ).mappings().all()
        if (
            not session_rows
            or session_rows[0]["session_date"] != header["input_start"]
            or session_rows[-1]["session_date"] != header["input_end"]
            or any(row["close_at_utc"].utcoffset() is None for row in session_rows)
        ):
            raise V022RuntimeDataError(
                "product_market_calendar_unavailable",
                "Product Input Snapshot Calendar does not reproduce its exact input interval",
            )
        session_closes = {
            row["session_date"]: row["close_at_utc"].astimezone(UTC)
            for row in session_rows
        }
        security_identifier_rows = connection.execute(
            text(
                """
                SELECT identifier.security_id,identifier.identifier_value
                  FROM catalog.security_identifier identifier
                 WHERE identifier.security_id IN :security_ids
                   AND identifier.identifier_type IN ('symbol','alias')
                 ORDER BY identifier.security_id,identifier.identifier_type,
                          identifier.identifier_value
                """
            ).bindparams(bindparam("security_ids", expanding=True)),
            {"security_ids": list(assets)},
        ).mappings().all()
        snapshot_rows = connection.execute(
            text(
                """
                SELECT snapshot.artifact_id,snapshot.fetched_at,snapshot.as_of_at,
                       lower(coalesce(snapshot.request_parameters->>'tickers',
                                      snapshot.request_parameters->>'ticker')) AS asset_symbol,
                       artifact.status,
                       subject.security_id AS subject_security_id,
                       subject.fetch_status AS subject_fetch_status
                  FROM data.dataset_input input
                  JOIN data.source_snapshot snapshot
                    ON snapshot.source_snapshot_id=input.source_snapshot_id
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=snapshot.artifact_id
                  LEFT JOIN data.source_snapshot_security_subject subject
                    ON subject.source_snapshot_id=snapshot.source_snapshot_id
                 WHERE input.dataset_publication_id=:dataset
                 ORDER BY input.ordinal
                """
            ),
            {"dataset": header["dataset_publication_id"]},
        ).mappings().all()
        snapshots = _resolve_asset_snapshot_proofs(
            assets=assets,
            snapshot_rows=cast(tuple[Mapping[str, object], ...], tuple(snapshot_rows)),
            security_identifier_rows=cast(
                tuple[Mapping[str, object], ...], tuple(security_identifier_rows)
            ),
        )
        feature_rows = connection.execute(
            text(
                """
                SELECT occurrence.feature_version_id,variant.variant_key,
                       feature.artifact_id AS feature_artifact_id,
                       feature.payload_contract_version_id,
                       contract.artifact_id AS payload_contract_artifact_id,
                       feature.output_port_key,feature.execution_semantics->>'source_field'
                         AS source_field,
                       feature.execution_semantics->>'unit' AS unit,
                       component.component_artifact_id
                  FROM workspace.compiled_feature_occurrence occurrence
                  JOIN processing.feature_version feature
                    ON feature.feature_version_id=occurrence.feature_version_id
                  JOIN processing.feature_variant variant
                    ON variant.feature_variant_id=feature.feature_variant_id
                  JOIN data.payload_contract_version contract
                    ON contract.payload_contract_version_id=feature.payload_contract_version_id
                  LEFT JOIN workspace.v022_catalog_release_component component
                    ON component.catalog_release_id=:release
                   AND component.component_artifact_id=feature.artifact_id
                   AND component.component_kind='feature_version'
                 WHERE occurrence.compiled_research_graph_id=:graph
                   AND occurrence.production_kind='raw_input'
                 ORDER BY variant.variant_key
                """
            ),
            {"release": header["catalog_release_id"], "graph": compiled_research_graph_id},
        ).mappings().all()
        raw_feature_keys = {row["variant_key"] for row in feature_rows}
        if not raw_feature_keys or not raw_feature_keys.issubset(RAW_FEATURE_KEYS) or any(
            row["component_artifact_id"] is None
            or not row["source_field"]
            or not row["unit"]
            for row in feature_rows
        ):
            raise V022RuntimeContractError(
                "product_raw_catalog_incomplete",
                "Compiled Graph contains no complete supported Raw Feature definition set",
            )
        encoding = connection.execute(
            text(
                """
                SELECT encoding.physical_encoding_version_id,encoding.artifact_id
                  FROM data.physical_encoding_version encoding
                  JOIN workspace.v022_catalog_release_component component
                    ON component.catalog_release_id=:release
                   AND component.component_artifact_id=encoding.artifact_id
                   AND component.component_kind='physical_encoding_version'
                 WHERE encoding.encoding_key=:key AND encoding.version_number=:version
                """
            ),
            {
                "release": header["catalog_release_id"],
                "key": CANONICAL_ENCODING_KEY,
                "version": CANONICAL_ENCODING_VERSION,
            },
        ).mappings().one()
    return _DatasetSnapshotContext(
        compiled_execution_data_context_id=header[
            "compiled_execution_data_context_id"
        ],
        execution_data_context_artifact_id=header[
            "execution_data_context_artifact_id"
        ],
        dataset_publication_id=header["dataset_publication_id"],
        dataset_artifact_id=header["dataset_artifact_id"],
        catalog_release_id=header["catalog_release_id"],
        encoding_id=encoding["physical_encoding_version_id"],
        encoding_artifact_id=encoding["artifact_id"],
        assets=assets,
        snapshots=snapshots,
        session_closes=session_closes,
        features=tuple(
            _RawFeatureContext(
                feature_variant_key=row["variant_key"],
                source_field=row["source_field"],
                unit=row["unit"],
                feature_version_id=row["feature_version_id"],
                feature_artifact_id=row["feature_artifact_id"],
                payload_contract_version_id=row["payload_contract_version_id"],
                payload_contract_artifact_id=row["payload_contract_artifact_id"],
                output_port_key=row["output_port_key"],
            )
            for row in feature_rows
        ),
        coverage_start=header["input_start"],
        coverage_end=header["input_end"],
        product_input_snapshot_id=product_input_snapshot_id,
        product_input_snapshot_artifact_id=header[
            "product_input_snapshot_artifact_id"
        ],
    )


def _product_executable_assets(
    member_rows: Sequence[RowMapping | Mapping[str, object]],
) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    executable = tuple(
        row for row in member_rows if row["is_uniformly_excluded"] is not True
    )
    assets = {
        cast(uuid.UUID, row["security_id"]): (
            cast(uuid.UUID, row["legacy_asset_id"]),
            cast(str, row["asset_key"]),
        )
        for row in executable
    }
    if not assets or len(assets) != len(executable):
        raise V022RuntimeContractError(
            "product_market_member_set_incomplete",
            "Product Input Snapshot has no unique executable Security identities",
        )
    return assets


def _load_market_rows(
    engine: Engine,
    *,
    context: _DatasetSnapshotContext,
    requested_start: date,
    requested_end: date,
) -> tuple[RowMapping, ...]:
    legacy_ids = [item[0] for item in context.assets.values()]
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT asset_id,session_date,adj_close,close_raw,volume_raw
                  FROM data.daily_bar
                 WHERE dataset_publication_id=:dataset
                   AND asset_id IN :asset_ids
                   AND session_date BETWEEN :start AND :end
                 ORDER BY session_date,asset_id
                """
            ).bindparams(bindparam("asset_ids", expanding=True)),
            {
                "dataset": context.dataset_publication_id,
                "asset_ids": legacy_ids,
                "start": requested_start,
                "end": requested_end,
            },
        ).mappings().all()
    if (
        not rows
        or {row["asset_id"] for row in rows} != set(legacy_ids)
        or any(row["session_date"] not in context.session_closes for row in rows)
    ):
        raise V022RuntimeDataError(
            "representative_market_rows_incomplete",
            "The frozen Dataset does not reproduce every requested Asset",
        )
    return tuple(rows)


def _iter_market_rows(
    engine: Engine,
    *,
    context: _DatasetSnapshotContext,
    requested_start: date,
    requested_end: date,
    batch_rows: int = 50_000,
    require_all_assets: bool = True,
) -> Iterator[RowMapping]:
    """Stream canonical market rows without retaining SQLAlchemy Row objects."""

    if batch_rows <= 0:
        raise ValueError("batch_rows must be positive")
    legacy_ids = [item[0] for item in context.assets.values()]
    observed_assets: set[uuid.UUID] = set()
    observed = 0
    statement = text(
        """
        SELECT asset_id,session_date,adj_close,close_raw,volume_raw
          FROM data.daily_bar
         WHERE dataset_publication_id=:dataset
           AND asset_id IN :asset_ids
           AND session_date BETWEEN :start AND :end
         ORDER BY session_date,asset_id
        """
    ).bindparams(bindparam("asset_ids", expanding=True))
    with engine.connect() as connection:
        rows = (
            connection.execution_options(stream_results=True)
            .execute(
                statement,
                {
                    "dataset": context.dataset_publication_id,
                    "asset_ids": legacy_ids,
                    "start": requested_start,
                    "end": requested_end,
                },
            )
            .mappings()
            .yield_per(batch_rows)
        )
        for row in rows:
            observed += 1
            observed_assets.add(row["asset_id"])
            if row["session_date"] not in context.session_closes:
                raise V022RuntimeDataError(
                    "representative_market_rows_incomplete",
                    "A streamed market row falls outside the frozen Calendar",
                )
            yield row
    if observed == 0 or not observed_assets.issubset(set(legacy_ids)):
        raise V022RuntimeDataError(
            "representative_market_rows_incomplete",
            "The frozen Dataset returned no valid rows for the requested Asset set",
        )
    if require_all_assets and observed_assets != set(legacy_ids):
        raise V022RuntimeDataError(
            "representative_market_rows_incomplete",
            "The frozen Dataset does not reproduce every requested Asset",
        )


def _prepare_raw_payload(
    object_store: LocalPayloadObjectStore,
    context: _DatasetSnapshotContext,
    feature: _RawFeatureContext,
    rows: tuple[RowMapping, ...],
) -> _PreparedRawPayload:
    security_by_legacy = {
        legacy_id: (security_id, asset_key)
        for security_id, (legacy_id, asset_key) in context.assets.items()
    }
    points = tuple(
        sorted(
            (
                RawSnapshotPoint(
                    asset_id=security_by_legacy[row["asset_id"]][0],
                    asset_key=security_by_legacy[row["asset_id"]][1],
                    session_date=row["session_date"],
                    value=Decimal(row[feature.source_field]),
                    known_at=context.session_closes[row["session_date"]],
                    vintage_id=str(context.dataset_publication_id),
                    unit=feature.unit,
                )
                for row in rows
            ),
            key=lambda item: (item.session_date, str(item.asset_id)),
        )
    )
    content = encode_raw_numeric_parquet(points)
    stored = object_store.publish(content, file_extension="parquet")
    sessions = tuple(sorted({item.session_date for item in points}))
    known_at = tuple(item.known_at.astimezone(UTC) for item in points)
    snapshot_semantics: dict[str, object] = {
        "semantic_mode": SNAPSHOT_SEMANTIC_MODE,
        "known_at_rule": "xnys_session_close_at_utc",
        "input_revision_rule": "dataset_publication_id",
        "price_basis": "back_adjusted",
        "product_warning_required": True,
        "dataset_publication_id": str(context.dataset_publication_id),
        "source_snapshot_provenance": [
            {
                "security_id": str(security_id),
                "source_snapshot_artifact_id": str(snapshot[0]),
                "fetched_at": snapshot[1].astimezone(UTC).isoformat(),
                "as_of_at": snapshot[2].astimezone(UTC).isoformat(),
            }
            for security_id, snapshot in sorted(
                context.snapshots.items(), key=lambda item: str(item[0])
            )
        ],
    }
    if context.product_input_snapshot_id is None:
        if context.calculation_context_id is None:
            raise V022RuntimeContractError(
                "processing_calculation_context_missing",
                "Research Raw preparation requires a Calculation Context",
            )
        snapshot_semantics["calculation_context_id"] = str(
            context.calculation_context_id
        )
    else:
        snapshot_semantics["product_input_snapshot_id"] = str(
            context.product_input_snapshot_id
        )
        snapshot_semantics["calculation_definition_context_id"] = str(
            context.compiled_execution_data_context_id
        )
    coverage = {
        "start": sessions[0].isoformat(),
        "end": sessions[-1].isoformat(),
        "session_count": len(sessions),
    }
    partition_key = {
        "dataset_publication_id": str(context.dataset_publication_id),
        "feature_variant_key": feature.feature_variant_key,
    }
    partition_key_hash = sha256_hexdigest(partition_key)
    descriptor = sha256_hexdigest(
        {
            "object_content_hash": stored.content_hash,
            "payload_contract_version_id": feature.payload_contract_version_id,
            "physical_encoding_version_id": context.encoding_id,
            "partition_key_hash": partition_key_hash,
            "coverage": coverage,
            "row_count": len(points),
        }
    )
    logical = sha256_hexdigest(
        {
            "dataset_publication_id": context.dataset_publication_id,
            "feature_version_id": feature.feature_version_id,
            "payload_contract_version_id": feature.payload_contract_version_id,
            "partition_descriptor_hash": descriptor,
            "snapshot_semantics": snapshot_semantics,
        }
    )
    manifest_hash = sha256_hexdigest(
        {
            "producer_artifact_id": context.dataset_artifact_id,
            "producer_output_port_key": feature.output_port_key,
            "physical_encoding_version_id": context.encoding_id,
            "logical_payload_fingerprint": logical,
            "partition_descriptor_hash": descriptor,
        }
    )
    return _PreparedRawPayload(
        context=feature,
        content=content,
        content_hash=stored.content_hash,
        storage_uri=stored.storage_uri,
        byte_size=stored.byte_size,
        row_count=len(points),
        start=sessions[0],
        end=sessions[-1],
        session_count=len(sessions),
        known_at_start=min(known_at),
        known_at_end=max(known_at),
        snapshot_semantics=snapshot_semantics,
        payload_object_id=uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird-v022:payload-object:{stored.content_hash}"
        ),
        payload_partition_id=uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird-v022:payload-partition:{descriptor}"
        ),
        partition_descriptor_hash=descriptor,
        payload_manifest_id=uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird-v022:payload-manifest:{manifest_hash}"
        ),
        logical_payload_fingerprint=logical,
        manifest_hash=manifest_hash,
    )


def _write_raw_payload(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _DatasetSnapshotContext,
    prepared: _PreparedRawPayload,
) -> None:
    coverage = {
        "start": prepared.start.isoformat(),
        "end": prepared.end.isoformat(),
        "session_count": prepared.session_count,
    }
    partition_key = {
        "fields": {
            "dataset_publication_id": str(context.dataset_publication_id),
            "feature_variant_key": prepared.context.feature_variant_key,
        },
        "partition_key_hash": sha256_hexdigest(
            {
                "dataset_publication_id": str(context.dataset_publication_id),
                "feature_variant_key": prepared.context.feature_variant_key,
            }
        ),
    }
    connection.execute(
        text(
            """
            INSERT INTO data.payload_object (
              payload_object_id,object_content_hash,storage_uri,byte_size,
              object_state,verification_status,verified_at
            ) VALUES (
              :id,:hash,:uri,:bytes,'published','verified',:verified_at
            ) ON CONFLICT (object_content_hash) DO NOTHING
            """
        ),
        {
            "id": prepared.payload_object_id,
            "hash": prepared.content_hash,
            "uri": prepared.storage_uri,
            "bytes": prepared.byte_size,
            "verified_at": datetime.now(UTC),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO data.payload_partition (
              payload_partition_id,payload_object_id,partition_descriptor_hash,
              byte_size,row_or_item_count,partition_key,coverage_document,statistics
            ) VALUES (
              :id,:object,:descriptor,:bytes,:rows,CAST(:key AS jsonb),
              CAST(:coverage AS jsonb),CAST(:statistics AS jsonb)
            ) ON CONFLICT (partition_descriptor_hash) DO NOTHING
            """
        ),
        {
            "id": prepared.payload_partition_id,
            "object": prepared.payload_object_id,
            "descriptor": prepared.partition_descriptor_hash,
            "bytes": prepared.byte_size,
            "rows": prepared.row_count,
            "key": _json(partition_key),
            "coverage": _json(coverage),
            "statistics": _json(
                {
                    "missing_count": 0,
                    "finite_count": prepared.row_count,
                    "snapshot_semantic_mode": SNAPSHOT_SEMANTIC_MODE,
                }
            ),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO data.payload_manifest (
              payload_manifest_id,artifact_id,payload_contract_version_id,
              physical_encoding_version_id,producer_artifact_id,
              producer_output_port_key,logical_payload_fingerprint,manifest_hash,
              partition_count,byte_size,row_or_item_count,coverage_document,
              retention_class,materialization_state
            ) VALUES (
              :id,:artifact,:contract,:encoding,:producer,:port,:logical,:hash,
              1,:bytes,:rows,CAST(:coverage AS jsonb),'research','materialized'
            )
            """
        ),
        {
            "id": prepared.payload_manifest_id,
            "artifact": artifact_id,
            "contract": prepared.context.payload_contract_version_id,
            "encoding": context.encoding_id,
            "producer": context.dataset_artifact_id,
            "port": prepared.context.output_port_key,
            "logical": prepared.logical_payload_fingerprint,
            "hash": prepared.manifest_hash,
            "bytes": prepared.byte_size,
            "rows": prepared.row_count,
            "coverage": _json(coverage),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO data.payload_manifest_partition (
              payload_manifest_id,payload_partition_id,ordinal
            ) VALUES (:manifest,:partition,0)
            """
        ),
        {
            "manifest": prepared.payload_manifest_id,
            "partition": prepared.payload_partition_id,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO data.payload_quality_summary (
              payload_quality_summary_id,payload_manifest_id,quality_status,
              missing_count,invalid_count,coverage_ratio,quality_document
            ) VALUES (
              :id,:manifest,'passed',0,0,1,CAST(:document AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"bird-v022:payload-quality:{prepared.manifest_hash}",
            ),
            "manifest": prepared.payload_manifest_id,
            "document": _json(
                {
                    "policy": "back_adjusted_historical_research_v1",
                    "row_count": prepared.row_count,
                }
            ),
        },
    )
    if context.product_input_snapshot_id is None:
        if context.calculation_context_id is None:
            raise V022RuntimeContractError(
                "processing_calculation_context_missing",
                "Research Raw binding requires a Calculation Context",
            )
        connection.execute(
            text(
                """
                INSERT INTO data.v022_calculation_context_payload_binding (
                  calculation_context_payload_binding_id,
                  calculation_context_id,dataset_publication_id,
                  feature_version_id,payload_manifest_id,known_at_start,known_at_end,
                  snapshot_semantics
                ) VALUES (
                  :id,:context,:dataset,:feature,:manifest,:known_start,:known_end,
                  CAST(:semantics AS jsonb)
                )
                """
            ),
            {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"bird-v022:calculation-context-payload:"
                    f"{context.calculation_context_id}:"
                    f"{prepared.context.feature_version_id}",
                ),
                "context": context.calculation_context_id,
                "dataset": context.dataset_publication_id,
                "feature": prepared.context.feature_version_id,
                "manifest": prepared.payload_manifest_id,
                "known_start": prepared.known_at_start,
                "known_end": prepared.known_at_end,
                "semantics": _json(prepared.snapshot_semantics),
            },
        )
        return
    binding_document = {
        "contract_version": "v0.22.product_input_payload_binding.v1",
        "product_input_snapshot_id": str(context.product_input_snapshot_id),
        "dataset_publication_id": str(context.dataset_publication_id),
        "feature_version_id": str(prepared.context.feature_version_id),
        "payload_manifest_id": str(prepared.payload_manifest_id),
        "coverage_start": prepared.start.isoformat(),
        "coverage_end": prepared.end.isoformat(),
    }
    binding_fingerprint = sha256_hexdigest(binding_document)
    connection.execute(
        text(
            """
            INSERT INTO data.v022_product_input_payload_binding (
              product_input_payload_binding_id,product_input_snapshot_id,
              dataset_publication_id,feature_version_id,payload_manifest_id,
              coverage_start,coverage_end,binding_document,binding_fingerprint
            ) VALUES (
              :id,:snapshot,:dataset,:feature,:manifest,:start,:end,
              CAST(:document AS jsonb),:fingerprint
            )
            """
        ),
        {
            "id": uuid.uuid5(
                uuid.NAMESPACE_URL,
                "bird-v022:product-input-payload:"
                f"{context.product_input_snapshot_id}:"
                f"{prepared.context.feature_version_id}",
            ),
            "snapshot": context.product_input_snapshot_id,
            "dataset": context.dataset_publication_id,
            "feature": prepared.context.feature_version_id,
            "manifest": prepared.payload_manifest_id,
            "start": prepared.start,
            "end": prepared.end,
            "document": _json(binding_document),
            "fingerprint": binding_fingerprint,
        },
    )


def _uuid_array(value: object) -> tuple[uuid.UUID, ...]:
    if not isinstance(value, list) or not value:
        raise V022RuntimeContractError(
            "representative_asset_context_invalid",
            "Compiled execution input has no frozen Security identities",
        )
    try:
        result = tuple(uuid.UUID(str(item)) for item in value)
    except (TypeError, ValueError) as error:
        raise V022RuntimeContractError(
            "representative_asset_context_invalid",
            "Compiled execution input contains an invalid Security identity",
        ) from error
    if len(result) != len(set(result)):
        raise V022RuntimeContractError(
            "representative_asset_context_invalid",
            "Compiled execution input contains duplicate Security identities",
        )
    return result


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
