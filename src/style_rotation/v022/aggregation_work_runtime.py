from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.aggregation_runtime import (
    AggregationDimension,
    WeightedAggregationInput,
    execute_deterministic_aggregation,
)
from style_rotation.v022.dag import ClaimedGraphWork
from style_rotation.v022.model_compat_runtime import (
    AggregationSignalPoint,
    LegacyModelCompatibilityRuntime,
)
from style_rotation.v022.model_migration import load_model_migration_registry
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore
from style_rotation.v022.runtime_contract import (
    V022RuntimeContractError,
    V022RuntimeDataError,
)

FINAL_SIGNAL_CONTRACT_KEY = "final_signal_numeric"
FINAL_SIGNAL_CONTRACT_VERSION = 1
FINAL_SIGNAL_OUTPUT_PORT = "final_signal_numeric"
CANONICAL_ENCODING_KEY = "canonical_parquet"
CANONICAL_ENCODING_VERSION = 1
OUTPUT_ARTIFACT_TYPE = "v022_payload_manifest"
AGGREGATION_RUN_ARTIFACT_TYPE = "v022_aggregation_run"
OUTPUT_COLUMNS = (
    "decision_date",
    "asset_id",
    "signal_value",
    "known_at",
    "input_revision",
    "missing_reason",
)
_OBJECT_URI = re.compile(
    r"payload-object://sha256/([0-9a-f]{64})\.([a-z0-9][a-z0-9._-]{0,19})"
)


@dataclass(frozen=True, slots=True)
class SignalManifestPoint:
    asset_id: uuid.UUID
    asset_key: str
    decision_date: date
    signal_value: Decimal | None
    known_at: datetime
    input_revision: str
    missing_reason: str | None


@dataclass(frozen=True, slots=True)
class VerifiedAggregationInput:
    compiled_feature_occurrence_id: uuid.UUID
    feature_variant_key: str
    slot_key: str
    ordinal: int
    payload_manifest_id: uuid.UUID
    manifest_artifact_id: uuid.UUID
    manifest_hash: str
    points: tuple[SignalManifestPoint, ...]


@dataclass(frozen=True, slots=True)
class AggregationOutputPoint:
    asset_id: uuid.UUID
    asset_key: str
    decision_date: date
    signal_value: Decimal | None
    known_at: datetime
    input_revision: str
    missing_reason: str | None


@dataclass(frozen=True, slots=True)
class AggregationCalculation:
    family_key: str
    parameter_preset_key: str | None
    points: tuple[AggregationOutputPoint, ...]
    calculation_fingerprint: str


@dataclass(frozen=True, slots=True)
class PublishedAggregationOutput:
    aggregation_run_id: uuid.UUID
    graph_work_item_id: uuid.UUID
    artifact_id: uuid.UUID
    payload_manifest_id: uuid.UUID
    payload_partition_id: uuid.UUID
    manifest_hash: str
    calculation_fingerprint: str
    reused_publication: bool


@dataclass(frozen=True, slots=True)
class _AggregationInputBinding:
    compiled_feature_occurrence_id: uuid.UUID
    feature_variant_key: str
    slot_key: str
    ordinal: int
    payload_manifest_id: uuid.UUID
    manifest_artifact_id: uuid.UUID
    manifest_hash: str


@dataclass(frozen=True, slots=True)
class _AggregationEnsembleMember:
    ordinal: int
    target_group_ordinal: int
    member_ordinal_within_target: int
    target_version_id: uuid.UUID
    target_version_artifact_id: uuid.UUID
    target_key: str
    target_semantics: Mapping[str, object]
    training_preset_version_id: uuid.UUID
    training_preset_version_artifact_id: uuid.UUID
    training_preset_key: str
    training_preset_semantics: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _AggregationWorkContext:
    graph_run_id: uuid.UUID
    graph_work_item_id: uuid.UUID
    fencing_token: int
    worker_key: str
    execution_fingerprint: str
    catalog_release_id: uuid.UUID
    aggregation_run_id: uuid.UUID
    aggregation_run_artifact_id: uuid.UUID
    aggregation_version_id: uuid.UUID
    aggregation_version_artifact_id: uuid.UUID
    execution_mode: str
    compiled_research_graph_id: uuid.UUID
    compiled_execution_data_context_id: uuid.UUID
    research_suite_id: uuid.UUID
    target_version_id: uuid.UUID | None
    target_version_artifact_id: uuid.UUID | None
    target_key: str | None
    target_semantics: Mapping[str, object] | None
    training_preset_version_id: uuid.UUID | None
    training_preset_version_artifact_id: uuid.UUID | None
    training_preset_key: str | None
    training_preset_semantics: Mapping[str, object] | None
    ensemble_spec_id: uuid.UUID | None
    ensemble_spec_artifact_id: uuid.UUID | None
    ensemble_fingerprint: str | None
    ensemble_document: Mapping[str, object] | None
    ensemble_members: tuple[_AggregationEnsembleMember, ...]
    feature_schema_version_id: uuid.UUID | None
    feature_schema_artifact_id: uuid.UUID | None
    feature_schema_document: Mapping[str, object] | None
    feature_schema_fingerprint: str | None
    output_payload_contract_version_id: uuid.UUID
    physical_encoding_version_id: uuid.UUID
    family_key: str
    parameter_preset_key: str | None
    resolved_parameters: Mapping[str, object]
    requested_range: Mapping[str, object]
    executor_version: str
    environment_fingerprint: str
    asset_keys: Mapping[uuid.UUID, str]
    decision_dates: frozenset[date]
    inputs: tuple[_AggregationInputBinding, ...]
    compiled_recipe: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class _PreparedAggregationOutput:
    content: bytes
    content_hash: str
    storage_uri: str
    byte_size: int
    row_count: int
    missing_count: int
    coverage_document: Mapping[str, object]
    partition_key: Mapping[str, object]
    payload_object_id: uuid.UUID
    payload_partition_id: uuid.UUID
    partition_descriptor_hash: str
    payload_manifest_id: uuid.UUID
    logical_payload_fingerprint: str
    manifest_hash: str


class FrozenAggregationRecipeResolver:
    """Resolve only an explicitly frozen v0.21-to-v0.22 recipe.

    Hierarchical and directional Aggregations cannot infer dimensions or weights
    from selected inputs.  The frozen registry must identify exactly one recipe by
    Family, Parameter Preset, and the complete selected Feature Variant set.
    """

    def __init__(self, registry: Mapping[str, Any]) -> None:
        self.registry = registry
        self.runtime = LegacyModelCompatibilityRuntime(registry)

    @classmethod
    def from_path(cls, path: Path) -> FrozenAggregationRecipeResolver:
        return cls(load_model_migration_registry(path))

    def resolve(
        self,
        family_key: str,
        parameter_preset_key: str | None,
        feature_variant_keys: Sequence[str],
    ) -> Mapping[str, Any]:
        if len(feature_variant_keys) != len(set(feature_variant_keys)):
            raise V022RuntimeContractError(
                "aggregation_duplicate_feature_variant",
                "Aggregation inputs must have unique Feature Variant identities",
            )
        selected = frozenset(feature_variant_keys)
        matches = []
        for record in self.registry["records"]:
            mapping = record["mapping"]
            if (
                mapping["family_key"] == family_key
                and mapping.get("parameter_preset_key") == parameter_preset_key
                and frozenset(mapping["input_signal_variant_keys"]) == selected
                and len(mapping["input_signal_variant_keys"]) == len(selected)
            ):
                matches.append(record)
        if len(matches) != 1:
            raise V022RuntimeContractError(
                "aggregation_frozen_recipe_not_unique",
                "Hierarchical Aggregation requires exactly one frozen recipe",
                details={
                    "family_key": family_key,
                    "parameter_preset_key": parameter_preset_key,
                    "feature_variant_keys": sorted(selected),
                    "match_count": len(matches),
                },
            )
        return cast(Mapping[str, Any], matches[0])


class VerifiedSignalManifestReader:
    """Read a published, verified, materialized final-signal Manifest.

    The database is authoritative for object state and Artifact identity.  A raw
    URI supplied by a caller is never accepted.
    """

    def __init__(self, engine: Engine, object_root: Path) -> None:
        self._engine = engine
        self._object_root = object_root.resolve()

    def read(
        self,
        *,
        payload_manifest_id: uuid.UUID,
        expected_manifest_hash: str,
        expected_artifact_id: uuid.UUID,
        catalog_release_id: uuid.UUID,
        allowed_asset_keys: Mapping[uuid.UUID, str],
        decision_dates: frozenset[date] | None = None,
        require_exact_asset_context: bool = True,
    ) -> tuple[SignalManifestPoint, ...]:
        with self._engine.connect() as connection:
            header = _manifest_header(connection, payload_manifest_id)
            partitions = _manifest_partitions(connection, payload_manifest_id)
            _validate_manifest_header(
                header,
                expected_manifest_hash=expected_manifest_hash,
                expected_artifact_id=expected_artifact_id,
                partition_count=len(partitions),
            )
            _validate_manifest_partition_projection(header, partitions)
            selected_partitions = (
                partitions
                if decision_dates is None
                else tuple(
                    row
                    for row in partitions
                    if _partition_intersects_dates(row, decision_dates)
                )
            )
            if not selected_partitions:
                raise V022RuntimeDataError(
                    "aggregation_decision_partition_missing",
                    "No Payload Partition covers the frozen decision-date panel",
                )
            contents = tuple(
                self._read_verified_object(row) for row in selected_partitions
            )
            asset_ids = {
                asset_id
                for row, content in zip(selected_partitions, contents, strict=True)
                for asset_id in _parquet_asset_ids(
                    content, row["file_extension"]
                )
            }
            _validate_manifest_catalog_membership(
                connection,
                catalog_release_id=catalog_release_id,
                header=header,
            )
            # A date-pruned projection can legitimately omit Context members that
            # list only outside the selected years.  The complete published
            # Manifest identity remains exact; the physical projection must be a
            # nonempty subset and can never introduce an outside Security.
            if require_exact_asset_context and decision_dates is None:
                validate_exact_asset_context(asset_ids, allowed_asset_keys)
            else:
                validate_asset_context_subset(asset_ids, allowed_asset_keys)
        points: list[SignalManifestPoint] = []
        for row, content in zip(selected_partitions, contents, strict=True):
            partition_points = parse_final_signal_numeric_parquet(
                content, allowed_asset_keys, decision_dates=decision_dates
            )
            if decision_dates is None:
                _validate_partition_coverage(row, partition_points)
                partition_row_count = len(partition_points)
            else:
                partition_row_count, partition_dates = _parquet_date_coverage(
                    content, "decision_date"
                )
                if (
                    row["row_or_item_count"] != partition_row_count
                    or row["coverage_document"]
                    != _coverage_document_from_dates(partition_dates)
                ):
                    raise V022RuntimeDataError(
                        "aggregation_partition_coverage_mismatch",
                        "Payload Partition coverage differs from its canonical rows",
                    )
            points.extend(partition_points)
        ordered = tuple(points)
        _validate_canonical_point_order(ordered)
        if decision_dates is None:
            _validate_manifest_coverage(header["coverage_document"], ordered)
        if decision_dates is not None:
            validate_exact_decision_date_coverage(ordered, decision_dates)
        return ordered

    def _read_verified_object(self, row: RowMapping) -> bytes:
        if (
            row["object_state"] != "published"
            or row["verification_status"] != "verified"
            or row["verified_at"] is None
        ):
            raise V022RuntimeDataError(
                "aggregation_payload_object_unverified",
                "Aggregation input Object must be published and independently verified",
            )
        match = _OBJECT_URI.fullmatch(str(row["storage_uri"]))
        if match is None or match.group(1) != row["object_content_hash"]:
            raise V022RuntimeDataError(
                "aggregation_payload_object_uri_invalid",
                "Payload Object URI is not its exact content-addressed identity",
            )
        if match.group(2) != row["file_extension"]:
            raise V022RuntimeDataError(
                "aggregation_payload_encoding_mismatch",
                "Payload Object extension differs from the frozen encoding",
            )
        object_directory = (self._object_root / "sha256").resolve()
        path = (object_directory / f"{match.group(1)}.{match.group(2)}").resolve()
        if path.parent != object_directory:
            raise V022RuntimeDataError(
                "aggregation_payload_object_path_escape",
                "Payload Object URI escapes the configured object root",
            )
        try:
            content = path.read_bytes()
        except OSError as error:
            raise V022RuntimeDataError(
                "aggregation_payload_object_unreadable",
                "Verified Payload Object bytes are unavailable",
            ) from error
        if (
            hashlib.sha256(content).hexdigest() != row["object_content_hash"]
            or len(content) != row["object_byte_size"]
            or len(content) != row["partition_byte_size"]
        ):
            raise V022RuntimeDataError(
                "aggregation_payload_object_hash_mismatch",
                "Payload Object bytes differ from their verified database identity",
            )
        return content


class AggregationWorkExecutor:
    """Materialize one already-planned deterministic Aggregation GraphWork item."""

    def __init__(
        self,
        engine: Engine,
        *,
        object_store: LocalPayloadObjectStore,
        object_root: Path,
        model_registry_path: Path | None = None,
    ) -> None:
        self._engine = engine
        self._object_store = object_store
        self._reader = VerifiedSignalManifestReader(engine, object_root)
        self._recipe_resolver = (
            FrozenAggregationRecipeResolver.from_path(model_registry_path)
            if model_registry_path is not None
            else None
        )

    def execute(
        self,
        *,
        graph_run_id: uuid.UUID,
        claim: ClaimedGraphWork,
        worker_key: str,
    ) -> PublishedAggregationOutput:
        if claim.work_kind != "aggregation":
            raise V022RuntimeContractError(
                "aggregation_work_kind_mismatch",
                "Aggregation executor accepts only Aggregation GraphWork",
            )
        context = _load_work_context(
            self._engine,
            graph_run_id=graph_run_id,
            claim=claim,
            worker_key=worker_key,
        )
        verified_inputs = tuple(
            VerifiedAggregationInput(
                compiled_feature_occurrence_id=item.compiled_feature_occurrence_id,
                feature_variant_key=item.feature_variant_key,
                slot_key=item.slot_key,
                ordinal=item.ordinal,
                payload_manifest_id=item.payload_manifest_id,
                manifest_artifact_id=item.manifest_artifact_id,
                manifest_hash=item.manifest_hash,
                points=self._reader.read(
                    payload_manifest_id=item.payload_manifest_id,
                    expected_manifest_hash=item.manifest_hash,
                    expected_artifact_id=item.manifest_artifact_id,
                    catalog_release_id=context.catalog_release_id,
                    allowed_asset_keys=context.asset_keys,
                    decision_dates=context.decision_dates,
                ),
            )
            for item in context.inputs
        )
        calculation = execute_verified_aggregation(
            family_key=context.family_key,
            parameter_preset_key=context.parameter_preset_key,
            inputs=verified_inputs,
            recipe_resolver=self._recipe_resolver,
            compiled_recipe=context.compiled_recipe,
        )
        content = encode_final_signal_numeric_parquet(calculation.points)
        prepared = _prepare_aggregation_output(
            self._object_store,
            context=context,
            calculation=calculation,
            content=content,
        )
        return _publish_aggregation_output(
            self._engine,
            context=context,
            calculation=calculation,
            prepared=prepared,
        )


def parse_final_signal_numeric_parquet(
    content: bytes,
    asset_keys: Mapping[uuid.UUID, str],
    *,
    decision_dates: frozenset[date] | None = None,
) -> tuple[SignalManifestPoint, ...]:
    try:
        table = pq.read_table(io.BytesIO(content))
    except Exception as error:
        raise V022RuntimeDataError(
            "aggregation_payload_not_parquet",
            "Aggregation input is not readable canonical Parquet",
        ) from error
    if tuple(table.column_names) != OUTPUT_COLUMNS:
        raise V022RuntimeContractError(
            "aggregation_payload_schema_mismatch",
            "final_signal_numeric requires its exact canonical column order",
            details={"actual_columns": table.column_names},
        )
    if not table.schema.equals(_final_signal_arrow_schema(), check_metadata=False):
        raise V022RuntimeContractError(
            "aggregation_payload_schema_mismatch",
            "final_signal_numeric requires date32/UUID-string/Decimal-Q18/UTC-us fields",
            details={"actual_schema": str(table.schema)},
        )
    if decision_dates is not None:
        table = table.filter(
            pc.is_in(
                table.column("decision_date"),
                value_set=pa.array(sorted(decision_dates), type=pa.date32()),
            )
        )
    rows = table.to_pylist()
    points: list[SignalManifestPoint] = []
    for row in rows:
        try:
            asset_id = uuid.UUID(str(row["asset_id"]))
        except (TypeError, ValueError) as error:
            raise V022RuntimeDataError(
                "aggregation_asset_identity_invalid",
                "final_signal_numeric contains an invalid Asset identity",
            ) from error
        asset_key = asset_keys.get(asset_id)
        if asset_key is None:
            raise V022RuntimeDataError(
                "aggregation_asset_identity_unfrozen",
                "final_signal_numeric Asset is absent from the frozen Registry",
                details={"asset_id": str(asset_id)},
            )
        decision_date = row["decision_date"]
        if not isinstance(decision_date, date) or isinstance(decision_date, datetime):
            raise V022RuntimeDataError(
                "aggregation_decision_date_invalid",
                "final_signal_numeric decision_date must be a date",
            )
        signal_value = row["signal_value"]
        if signal_value is not None and (
            not isinstance(signal_value, Decimal) or not signal_value.is_finite()
        ):
            raise V022RuntimeDataError(
                "aggregation_signal_value_invalid",
                "final_signal_numeric value must be a finite canonical Decimal or missing",
            )
        known_at = row["known_at"]
        if not isinstance(known_at, datetime) or known_at.utcoffset() is None:
            raise V022RuntimeDataError(
                "aggregation_known_at_invalid",
                "final_signal_numeric known_at must be timezone-aware",
            )
        revision = row["input_revision"]
        if not isinstance(revision, str) or not revision.strip():
            raise V022RuntimeDataError(
                "aggregation_input_revision_invalid",
                "final_signal_numeric input_revision must be nonblank",
            )
        missing_reason = row["missing_reason"]
        if signal_value is None:
            if not isinstance(missing_reason, str) or not missing_reason.strip():
                raise V022RuntimeDataError(
                    "aggregation_missing_reason_required",
                    "A missing signal value requires an explicit missing_reason",
                )
        elif missing_reason is not None:
            raise V022RuntimeDataError(
                "aggregation_unexpected_missing_reason",
                "A present signal value cannot carry a missing_reason",
            )
        points.append(
            SignalManifestPoint(
                asset_id=asset_id,
                asset_key=asset_key,
                decision_date=decision_date,
                signal_value=signal_value,
                known_at=known_at.astimezone(UTC),
                input_revision=revision,
                missing_reason=missing_reason,
            )
        )
    ordered = tuple(points)
    _validate_canonical_point_order(ordered)
    return ordered


def validate_exact_asset_context(
    asset_ids: set[uuid.UUID],
    allowed_asset_keys: Mapping[uuid.UUID, str],
) -> None:
    if not asset_ids or asset_ids != set(allowed_asset_keys):
        raise V022RuntimeDataError(
            "aggregation_asset_context_mismatch",
            "Aggregation input does not reproduce the exact frozen Asset Context",
            details={
                "expected_asset_count": len(allowed_asset_keys),
                "actual_asset_count": len(asset_ids),
            },
        )


def validate_asset_context_subset(
    asset_ids: set[uuid.UUID],
    allowed_asset_keys: Mapping[uuid.UUID, str],
) -> None:
    """Require a nonempty signal panel contained by its frozen Asset Context.

    Trainable aggregations emit the exact frozen feature-complete candidate
    panel, which can legitimately exclude Context members that never become
    eligible during the evaluation window.  It must never introduce a member
    outside the Context.  The stricter equality contract remains the default
    for deterministic processing and aggregation outputs.
    """

    unexpected = asset_ids - set(allowed_asset_keys)
    if not asset_ids or unexpected:
        raise V022RuntimeDataError(
            "aggregation_asset_context_mismatch",
            "Aggregation input contains no assets or an asset outside the frozen Asset Context",
            details={
                "expected_asset_count": len(allowed_asset_keys),
                "actual_asset_count": len(asset_ids),
                "unexpected_asset_count": len(unexpected),
            },
        )


def validate_exact_decision_date_coverage(
    points: Sequence[SignalManifestPoint], decision_dates: frozenset[date]
) -> None:
    observed = frozenset(item.decision_date for item in points)
    if observed != decision_dates:
        raise V022RuntimeDataError(
            "aggregation_decision_date_coverage_mismatch",
            "Filtered Aggregation input does not cover every frozen decision date",
            details={
                "missing": [
                    item.isoformat() for item in sorted(decision_dates.difference(observed))
                ],
                "unexpected": [
                    item.isoformat() for item in sorted(observed.difference(decision_dates))
                ],
            },
        )


def _validate_native_recipe(
    recipe: Mapping[str, object],
    inputs: tuple[VerifiedAggregationInput, ...],
) -> None:
    expected_inputs = [item.feature_variant_key for item in inputs]
    if (
        recipe.get("recipe_kind") != "native_hierarchical_equal_v2"
        or recipe.get("family_key") != "hierarchical_weighted_mean"
        or recipe.get("parameter_preset_key")
        != "active_dimension_equal_component_equal_v1"
        or recipe.get("input_scale") != "centered_rank"
        or recipe.get("direction") != "higher_is_better"
        or recipe.get("missing_policy") != "fail_complete_case"
        or recipe.get("ordered_inputs") != expected_inputs
    ):
        raise V022RuntimeContractError(
            "aggregation_compiled_recipe_identity_invalid",
            "Native hierarchical Recipe does not reproduce the compiled input identity",
        )
    dimensions = recipe.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise V022RuntimeContractError(
            "aggregation_compiled_recipe_dimensions_invalid",
            "Native hierarchical Recipe requires nonempty dimensions",
        )
    observed: list[str] = []
    dimension_keys: set[str] = set()
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            raise V022RuntimeContractError(
                "aggregation_compiled_recipe_dimensions_invalid",
                "Native hierarchical dimension must be an object",
            )
        dimension_key = dimension.get("dimension_key")
        components = dimension.get("components")
        if (
            not isinstance(dimension_key, str)
            or not dimension_key
            or dimension_key in dimension_keys
            or not isinstance(components, list)
            or not components
        ):
            raise V022RuntimeContractError(
                "aggregation_compiled_recipe_dimensions_invalid",
                "Native hierarchical dimensions must be unique and nonempty",
            )
        dimension_keys.add(dimension_key)
        for component in components:
            if not isinstance(component, dict) or not isinstance(
                component.get("feature_key"), str
            ):
                raise V022RuntimeContractError(
                    "aggregation_compiled_recipe_component_invalid",
                    "Native hierarchical Recipe component identity is invalid",
                )
            observed.append(cast(str, component["feature_key"]))
    if len(observed) != len(set(observed)) or set(observed) != set(expected_inputs):
        raise V022RuntimeContractError(
            "aggregation_compiled_recipe_input_closure_invalid",
            "Native hierarchical Recipe must consume every compiled input exactly once",
        )


def _execute_native_recipe(
    recipe: Mapping[str, object],
    inputs: tuple[VerifiedAggregationInput, ...],
    indexed: tuple[dict[tuple[date, uuid.UUID], SignalManifestPoint], ...],
    complete: set[tuple[date, uuid.UUID]],
) -> dict[tuple[date, uuid.UUID], Decimal]:
    by_feature = {
        input_.feature_variant_key: points
        for input_, points in zip(inputs, indexed, strict=True)
    }
    dimensions_document = cast(list[dict[str, object]], recipe["dimensions"])
    output: dict[tuple[date, uuid.UUID], Decimal] = {}
    for identity in complete:
        dimensions: list[AggregationDimension] = []
        for dimension in dimensions_document:
            components = cast(list[dict[str, object]], dimension["components"])
            dimensions.append(
                AggregationDimension(
                    dimension_key=cast(str, dimension["dimension_key"]),
                    weight=Decimal(cast(str, dimension["dimension_weight"])),
                    inputs=tuple(
                        WeightedAggregationInput(
                            input_key=cast(str, component["feature_key"]),
                            value=by_feature[
                                cast(str, component["feature_key"])
                            ][identity].signal_value,
                            weight=Decimal(
                                cast(str, component["component_weight"])
                            ),
                        )
                        for component in components
                    ),
                )
            )
        try:
            value = execute_deterministic_aggregation(
                "hierarchical_weighted_mean", tuple(dimensions)
            )
        except (ArithmeticError, ValueError) as error:
            raise V022RuntimeContractError(
                "aggregation_compiled_recipe_weights_invalid",
                "Native hierarchical Recipe weights are invalid",
            ) from error
        if value is None:
            raise V022RuntimeDataError(
                "aggregation_complete_point_missing",
                "A complete native hierarchical Aggregation produced a missing value",
            )
        output[identity] = value
    return output


def execute_verified_aggregation(
    *,
    family_key: str,
    parameter_preset_key: str | None,
    inputs: tuple[VerifiedAggregationInput, ...],
    recipe_resolver: FrozenAggregationRecipeResolver | None = None,
    compiled_recipe: Mapping[str, object] | None = None,
) -> AggregationCalculation:
    if not inputs:
        raise V022RuntimeContractError(
            "aggregation_inputs_empty",
            "Deterministic Aggregation requires at least one published input Manifest",
        )
    if tuple(item.ordinal for item in inputs) != tuple(range(len(inputs))):
        raise V022RuntimeContractError(
            "aggregation_input_ordinal_gap",
            "Aggregation inputs require contiguous compiled ordinals",
        )
    if len({item.compiled_feature_occurrence_id for item in inputs}) != len(inputs):
        raise V022RuntimeContractError(
            "aggregation_input_occurrence_duplicate",
            "Aggregation inputs must bind unique compiled Stage 3 occurrences",
        )
    indexed = tuple(
        {
            (point.decision_date, point.asset_id): point
            for point in item.points
        }
        for item in inputs
    )
    if any(len(points) != len(item.points) for points, item in zip(indexed, inputs, strict=True)):
        raise V022RuntimeDataError(
            "aggregation_input_duplicate_point",
            "Aggregation input contains duplicate decision-date Asset identities",
        )
    identities = set(indexed[0])
    if not identities or any(set(item) != identities for item in indexed[1:]):
        raise V022RuntimeDataError(
            "aggregation_input_panel_mismatch",
            "Aggregation inputs must reproduce one exact decision-date Asset panel",
        )
    if family_key == "single_signal_identity" and len(inputs) != 1:
        raise V022RuntimeContractError(
            "aggregation_identity_cardinality",
            "single_signal_identity requires one exact Manifest",
        )
    recipe: Mapping[str, Any] | None = None
    native_recipe: Mapping[str, object] | None = None
    if (
        family_key == "hierarchical_weighted_mean"
        and parameter_preset_key == "active_dimension_equal_component_equal_v1"
    ):
        if compiled_recipe is None:
            raise V022RuntimeContractError(
                "aggregation_compiled_recipe_required",
                "Native hierarchical Aggregation requires its compiled Recipe",
            )
        _validate_native_recipe(compiled_recipe, inputs)
        native_recipe = compiled_recipe
    elif family_key in {"hierarchical_weighted_mean", "directional_weighted_vote"}:
        if recipe_resolver is None:
            raise V022RuntimeContractError(
                "aggregation_frozen_recipe_required",
                "Weighted Aggregation requires a frozen recipe registry",
            )
        recipe = recipe_resolver.resolve(
            family_key,
            parameter_preset_key,
            tuple(item.feature_variant_key for item in inputs),
        )
    elif family_key not in {"single_signal_identity", "flat_equal_weight_mean"}:
        raise V022RuntimeContractError(
            "aggregation_family_runtime_unsupported",
            f"Unsupported deterministic Aggregation Family: {family_key}",
        )

    complete = {
        identity
        for identity in identities
        if all(item[identity].signal_value is not None for item in indexed)
    }
    calculated: dict[tuple[date, uuid.UUID], Decimal] = {}
    if native_recipe is not None and complete:
        calculated = _execute_native_recipe(
            native_recipe, inputs, indexed, complete
        )
    elif recipe is not None and complete:
        assert recipe_resolver is not None
        calculated = _execute_frozen_recipe(recipe_resolver, recipe, inputs, indexed, complete)
    elif recipe is None:
        for identity in complete:
            values = tuple(item[identity].signal_value for item in indexed)
            dimensions = (
                AggregationDimension(
                    "flat",
                    tuple(
                        WeightedAggregationInput(
                            input_key=input_.feature_variant_key,
                            value=value,
                            weight=Decimal(1) / Decimal(len(inputs)),
                        )
                        for input_, value in zip(inputs, values, strict=True)
                    ),
                    Decimal(1),
                ),
            )
            value = execute_deterministic_aggregation(family_key, dimensions)
            if value is None:
                raise V022RuntimeDataError(
                    "aggregation_complete_point_missing",
                    "A complete deterministic Aggregation produced a missing value",
                )
            calculated[identity] = value

    output: list[AggregationOutputPoint] = []
    for identity in sorted(identities, key=lambda item: (item[0], str(item[1]))):
        source_points = tuple(item[identity] for item in indexed)
        if len({item.asset_key for item in source_points}) != 1:
            raise V022RuntimeDataError(
                "aggregation_asset_key_drift",
                "Frozen Asset identity maps to inconsistent Asset keys",
            )
        output.append(
            AggregationOutputPoint(
                asset_id=identity[1],
                asset_key=source_points[0].asset_key,
                decision_date=identity[0],
                signal_value=calculated.get(identity),
                known_at=max(item.known_at for item in source_points).astimezone(UTC),
                input_revision=sha256_hexdigest(
                    {
                        "ordered_inputs": [
                            {
                                "ordinal": input_.ordinal,
                                "manifest_hash": input_.manifest_hash,
                                "input_revision": point.input_revision,
                            }
                            for input_, point in zip(inputs, source_points, strict=True)
                        ]
                    }
                ),
                missing_reason=(
                    None if identity in calculated else "aggregation_input_missing"
                ),
            )
        )
    _validate_cross_section_minimum(tuple(output))
    fingerprint = sha256_hexdigest(
        {
            "family_key": family_key,
            "parameter_preset_key": parameter_preset_key,
            "ordered_inputs": [
                {
                    "occurrence_id": item.compiled_feature_occurrence_id,
                    "ordinal": item.ordinal,
                    "manifest_hash": item.manifest_hash,
                }
                for item in inputs
            ],
            "compiled_recipe": compiled_recipe,
            "points": output,
        }
    )
    return AggregationCalculation(
        family_key=family_key,
        parameter_preset_key=parameter_preset_key,
        points=tuple(output),
        calculation_fingerprint=fingerprint,
    )


def encode_final_signal_numeric_parquet(
    points: tuple[AggregationOutputPoint, ...],
) -> bytes:
    if not points:
        raise V022RuntimeDataError(
            "aggregation_output_empty",
            "Aggregation output cannot publish an empty Payload",
        )
    ordered = tuple(sorted(points, key=lambda item: (item.decision_date, str(item.asset_id))))
    if ordered != points:
        raise V022RuntimeContractError(
            "aggregation_output_not_canonical",
            "Aggregation output must already use canonical decision-date Asset order",
        )
    schema = _final_signal_arrow_schema()
    buffer = io.BytesIO()
    writer: pq.ParquetWriter | None = None
    try:
        for offset in range(0, len(points), 50_000):
            chunk = points[offset : offset + 50_000]
            table = pa.Table.from_pylist(
                [
                    {
                        "decision_date": item.decision_date,
                        "asset_id": str(item.asset_id),
                        "signal_value": item.signal_value,
                        "known_at": item.known_at.astimezone(UTC),
                        "input_revision": item.input_revision,
                        "missing_reason": item.missing_reason,
                    }
                    for item in chunk
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
    except (pa.ArrowInvalid, pa.ArrowTypeError) as error:
        raise V022RuntimeDataError(
            "aggregation_output_schema_invalid",
            "Aggregation output values cannot satisfy final_signal_numeric v1",
        ) from error
    finally:
        if writer is not None:
            writer.close()
    return buffer.getvalue()


def _final_signal_arrow_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("decision_date", pa.date32(), nullable=False),
            pa.field("asset_id", pa.string(), nullable=False),
            pa.field("signal_value", pa.decimal128(38, 18), nullable=True),
            pa.field("known_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("input_revision", pa.string(), nullable=False),
            pa.field("missing_reason", pa.string(), nullable=True),
        ]
    )


def _execute_frozen_recipe(
    resolver: FrozenAggregationRecipeResolver,
    record: Mapping[str, Any],
    inputs: tuple[VerifiedAggregationInput, ...],
    indexed: tuple[dict[tuple[date, uuid.UUID], SignalManifestPoint], ...],
    complete: set[tuple[date, uuid.UUID]],
) -> dict[tuple[date, uuid.UUID], Decimal]:
    recipe = record["legacy_recipe"]
    by_variant = {
        input_.feature_variant_key: (input_, points)
        for input_, points in zip(inputs, indexed, strict=True)
    }
    signal_points: dict[str, tuple[AggregationSignalPoint, ...]] = {}
    used_variants: set[str] = set()
    for dimension in recipe["dimensions"]:
        for component in dimension["components"]:
            variant_key = str(component["mapped_signal_variant_key"])
            legacy_key = str(component["legacy_signal_key"])
            if legacy_key in signal_points or variant_key not in by_variant:
                raise V022RuntimeContractError(
                    "aggregation_frozen_recipe_invalid",
                    "Frozen recipe does not map one unique selected input per component",
                )
            used_variants.add(variant_key)
            _, points = by_variant[variant_key]
            signal_points[legacy_key] = tuple(
                AggregationSignalPoint(
                    asset_id=points[identity].asset_id,
                    asset_key=points[identity].asset_key,
                    observation_date=points[identity].decision_date,
                    score=cast(Decimal, points[identity].signal_value),
                )
                for identity in sorted(complete, key=lambda item: (item[0], str(item[1])))
            )
    if used_variants != set(by_variant):
        raise V022RuntimeContractError(
            "aggregation_frozen_recipe_input_mismatch",
            "Frozen recipe does not consume every selected input exactly once",
        )
    result = resolver.runtime.execute(str(record["legacy_key"]), signal_points)
    calculated = {
        (point.observation_date, point.asset_id): point.score for point in result.points
    }
    if set(calculated) != complete:
        raise V022RuntimeDataError(
            "aggregation_frozen_recipe_output_panel_mismatch",
            "Frozen recipe output differs from its complete input panel",
        )
    return calculated


def _validate_cross_section_minimum(
    points: tuple[AggregationOutputPoint, ...],
) -> None:
    present_by_date: defaultdict[date, int] = defaultdict(int)
    all_dates: set[date] = set()
    for point in points:
        all_dates.add(point.decision_date)
        if point.signal_value is not None:
            present_by_date[point.decision_date] += 1
    if all_dates and not any(present_by_date[item] >= 2 for item in all_dates):
        raise V022RuntimeDataError(
            "aggregation_cross_section_too_small",
            "Aggregation output contains no decision date with two finite Asset values",
            details={"decision_dates": [item.isoformat() for item in sorted(all_dates)]},
        )


def _manifest_header(
    connection: Connection, payload_manifest_id: uuid.UUID
) -> RowMapping:
    row = connection.execute(
        text(
            """
            SELECT manifest.payload_manifest_id,manifest.artifact_id,
                   manifest.payload_contract_version_id,
                   manifest.physical_encoding_version_id,
                   manifest.producer_artifact_id,
                   manifest.producer_output_port_key,
                   manifest.logical_payload_fingerprint,manifest.manifest_hash,
                   manifest.partition_count,manifest.byte_size,
                   manifest.row_or_item_count,manifest.coverage_document,
                   manifest.materialization_state,
                   artifact.artifact_type AS manifest_artifact_type,
                   artifact.status AS manifest_artifact_status,
                   producer.status AS producer_artifact_status,
                   family.contract_key,contract.version_number AS contract_version,
                   contract.artifact_id AS contract_artifact_id,
                   contract.schema_document,contract_artifact.status AS contract_artifact_status,
                   encoding.encoding_key,encoding.version_number AS encoding_version,
                   encoding.artifact_id AS encoding_artifact_id,
                   encoding.file_extension,encoding_artifact.status AS encoding_artifact_status
              FROM data.payload_manifest manifest
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=manifest.artifact_id
              JOIN lineage.artifact producer
                ON producer.artifact_id=manifest.producer_artifact_id
              JOIN data.payload_contract_version contract
                ON contract.payload_contract_version_id=
                   manifest.payload_contract_version_id
              JOIN data.payload_contract_family family
                ON family.payload_contract_family_id=
                   contract.payload_contract_family_id
              JOIN lineage.artifact contract_artifact
                ON contract_artifact.artifact_id=contract.artifact_id
              JOIN data.physical_encoding_version encoding
                ON encoding.physical_encoding_version_id=
                   manifest.physical_encoding_version_id
              JOIN lineage.artifact encoding_artifact
                ON encoding_artifact.artifact_id=encoding.artifact_id
             WHERE manifest.payload_manifest_id=:manifest
            """
        ),
        {"manifest": payload_manifest_id},
    ).mappings().one_or_none()
    if row is None:
        raise V022RuntimeDataError(
            "aggregation_manifest_not_found",
            "Exact Aggregation input Payload Manifest does not exist",
        )
    return row


def _manifest_partitions(
    connection: Connection, payload_manifest_id: uuid.UUID
) -> tuple[RowMapping, ...]:
    return tuple(
        connection.execute(
            text(
                """
                SELECT link.ordinal,partition.payload_partition_id,
                       partition.partition_descriptor_hash,
                       partition.byte_size AS partition_byte_size,
                       partition.row_or_item_count,partition.partition_key,
                       partition.coverage_document,partition.statistics,
                       object.object_content_hash,object.storage_uri,
                       object.byte_size AS object_byte_size,object.object_state,
                       object.verification_status,object.verified_at,
                       encoding.file_extension
                  FROM data.payload_manifest manifest
                  JOIN data.physical_encoding_version encoding
                    ON encoding.physical_encoding_version_id=
                       manifest.physical_encoding_version_id
                  JOIN data.payload_manifest_partition link
                    ON link.payload_manifest_id=manifest.payload_manifest_id
                  JOIN data.payload_partition partition
                    ON partition.payload_partition_id=link.payload_partition_id
                  JOIN data.payload_object object
                    ON object.payload_object_id=partition.payload_object_id
                 WHERE manifest.payload_manifest_id=:manifest
                 ORDER BY link.ordinal
                """
            ),
            {"manifest": payload_manifest_id},
        ).mappings()
    )


def _validate_manifest_header(
    header: RowMapping,
    *,
    expected_manifest_hash: str,
    expected_artifact_id: uuid.UUID,
    partition_count: int,
) -> None:
    schema = header["schema_document"]
    if (
        header["artifact_id"] != expected_artifact_id
        or header["manifest_hash"] != expected_manifest_hash
        or header["manifest_artifact_type"] != OUTPUT_ARTIFACT_TYPE
        or header["manifest_artifact_status"] != "published"
        or header["producer_artifact_status"] != "published"
        or header["contract_artifact_status"] != "published"
        or header["encoding_artifact_status"] != "published"
        or header["materialization_state"] != "materialized"
    ):
        raise V022RuntimeDataError(
            "aggregation_manifest_identity_invalid",
            "Aggregation input Manifest is not its exact published materialized identity",
        )
    if (
        header["contract_key"] != FINAL_SIGNAL_CONTRACT_KEY
        or header["contract_version"] != FINAL_SIGNAL_CONTRACT_VERSION
        or not isinstance(schema, dict)
        or schema.get("value_field") != "signal_value"
        or header["encoding_key"] != CANONICAL_ENCODING_KEY
        or header["encoding_version"] != CANONICAL_ENCODING_VERSION
        or header["file_extension"] != "parquet"
    ):
        raise V022RuntimeContractError(
            "aggregation_manifest_contract_invalid",
            "Aggregation input must use final_signal_numeric v1 canonical Parquet",
        )
    if partition_count < 1 or header["partition_count"] != partition_count:
        raise V022RuntimeDataError(
            "aggregation_manifest_partition_count_mismatch",
            "Payload Manifest partition projection is incomplete",
        )


def _validate_manifest_partition_projection(
    header: RowMapping, partitions: tuple[RowMapping, ...]
) -> None:
    """Validate the complete projection before pruning physical object reads."""

    if any(row["ordinal"] != ordinal for ordinal, row in enumerate(partitions)):
        raise V022RuntimeDataError(
            "aggregation_manifest_partition_ordinal_gap",
            "Payload Manifest partitions require contiguous canonical ordinals",
        )
    if (
        sum(cast(int, row["partition_byte_size"]) for row in partitions)
        != header["byte_size"]
        or sum(cast(int, row["row_or_item_count"]) for row in partitions)
        != header["row_or_item_count"]
    ):
        raise V022RuntimeDataError(
            "aggregation_manifest_totals_mismatch",
            "Payload Manifest byte or row totals differ from verified partitions",
        )
    coverage_rows = tuple(_partition_coverage(row) for row in partitions)
    if any(
        current[0] <= previous[1]
        for previous, current in zip(coverage_rows, coverage_rows[1:], strict=False)
    ):
        raise V022RuntimeDataError(
            "aggregation_manifest_partition_coverage_overlap",
            "Payload Manifest partition coverage must be ordered and non-overlapping",
        )
    projected_coverage = {
        "start": coverage_rows[0][0].isoformat(),
        "end": coverage_rows[-1][1].isoformat(),
        "session_count": sum(item[2] for item in coverage_rows),
    }
    if header["coverage_document"] != projected_coverage:
        raise V022RuntimeDataError(
            "aggregation_manifest_coverage_mismatch",
            "Payload Manifest coverage differs from its partition projection",
        )


def _partition_intersects_dates(
    row: RowMapping, decision_dates: frozenset[date]
) -> bool:
    start, end, _session_count = _partition_coverage(row)
    return any(start <= item <= end for item in decision_dates)


def _partition_coverage(row: RowMapping) -> tuple[date, date, int]:
    document = row["coverage_document"]
    if not isinstance(document, dict):
        raise V022RuntimeDataError(
            "aggregation_partition_coverage_mismatch",
            "Payload Partition coverage is not a canonical document",
        )
    try:
        start = date.fromisoformat(cast(str, document["start"]))
        end = date.fromisoformat(cast(str, document["end"]))
        session_count = cast(int, document["session_count"])
    except (KeyError, TypeError, ValueError) as error:
        raise V022RuntimeDataError(
            "aggregation_partition_coverage_mismatch",
            "Payload Partition coverage is not a canonical document",
        ) from error
    if start > end or not isinstance(session_count, int) or session_count < 1:
        raise V022RuntimeDataError(
            "aggregation_partition_coverage_mismatch",
            "Payload Partition coverage is not a canonical document",
        )
    return start, end, session_count


def _parquet_asset_ids(content: bytes, file_extension: str) -> set[uuid.UUID]:
    if file_extension != "parquet":
        raise V022RuntimeContractError(
            "aggregation_payload_encoding_unsupported",
            "Aggregation runtime supports canonical Parquet only",
        )
    try:
        table = pq.read_table(io.BytesIO(content), columns=["asset_id"])
        return {
            uuid.UUID(str(value.as_py()))
            for value in pc.unique(table.column("asset_id"))
        }
    except Exception as error:
        raise V022RuntimeDataError(
            "aggregation_payload_asset_scan_failed",
            "Cannot resolve frozen Asset identities from Aggregation input",
        ) from error


def _parquet_date_coverage(content: bytes, column_name: str) -> tuple[int, set[date]]:
    try:
        table = pq.read_table(io.BytesIO(content), columns=[column_name])
        dates = {cast(date, value.as_py()) for value in pc.unique(table.column(column_name))}
        return table.num_rows, dates
    except Exception as error:
        raise V022RuntimeDataError(
            "aggregation_payload_coverage_scan_failed",
            "Cannot verify Payload date coverage from canonical Parquet",
        ) from error


def _validate_manifest_catalog_membership(
    connection: Connection,
    *,
    catalog_release_id: uuid.UUID,
    header: RowMapping,
) -> None:
    memberships = connection.execute(
        text(
            """
            SELECT component_artifact_id,component_kind,component_key,component_version
              FROM workspace.v022_catalog_release_component
             WHERE catalog_release_id=:release
               AND component_artifact_id=ANY(:artifacts)
            """
        ),
        {
            "release": catalog_release_id,
            "artifacts": [
                header["contract_artifact_id"],
                header["encoding_artifact_id"],
            ],
        },
    ).mappings().all()
    expected = {
        (
            header["contract_artifact_id"],
            "payload_contract_version",
            FINAL_SIGNAL_CONTRACT_KEY,
            FINAL_SIGNAL_CONTRACT_VERSION,
        ),
        (
            header["encoding_artifact_id"],
            "physical_encoding_version",
            CANONICAL_ENCODING_KEY,
            CANONICAL_ENCODING_VERSION,
        ),
    }
    actual = {
        (
            row["component_artifact_id"],
            row["component_kind"],
            row["component_key"],
            row["component_version"],
        )
        for row in memberships
    }
    if actual != expected:
        raise V022RuntimeContractError(
            "aggregation_manifest_catalog_unpinned",
            "Input Payload Contract or encoding is outside the Graph-pinned Catalog Release",
        )


def _validate_canonical_point_order(
    points: Sequence[SignalManifestPoint],
) -> None:
    identities = tuple((item.decision_date, str(item.asset_id)) for item in points)
    if not identities or identities != tuple(sorted(identities)):
        raise V022RuntimeDataError(
            "aggregation_payload_order_invalid",
            "final_signal_numeric rows require canonical decision-date Asset order",
        )
    if len(identities) != len(set(identities)):
        raise V022RuntimeDataError(
            "aggregation_payload_duplicate_identity",
            "final_signal_numeric contains duplicate primary keys",
        )


def _validate_partition_coverage(
    row: RowMapping, points: tuple[SignalManifestPoint, ...]
) -> None:
    expected = _coverage_document(points)
    if (
        row["row_or_item_count"] != len(points)
        or row["coverage_document"] != expected
    ):
        raise V022RuntimeDataError(
            "aggregation_partition_coverage_mismatch",
            "Payload Partition coverage differs from its canonical rows",
        )


def _validate_manifest_coverage(
    document: object, points: tuple[SignalManifestPoint, ...]
) -> None:
    if document != _coverage_document(points):
        raise V022RuntimeDataError(
            "aggregation_manifest_coverage_mismatch",
            "Payload Manifest coverage differs from its canonical rows",
        )


def _coverage_document(
    points: Sequence[SignalManifestPoint | AggregationOutputPoint],
) -> dict[str, object]:
    return _coverage_document_from_dates({item.decision_date for item in points})


def _coverage_document_from_dates(session_dates: set[date]) -> dict[str, object]:
    sessions = sorted(session_dates)
    if not sessions:
        raise V022RuntimeDataError(
            "aggregation_payload_empty",
            "Aggregation Payload cannot have empty coverage",
        )
    return {
        "start": sessions[0].isoformat(),
        "end": sessions[-1].isoformat(),
        "session_count": len(sessions),
    }


def _load_work_context(
    engine: Engine,
    *,
    graph_run_id: uuid.UUID,
    claim: ClaimedGraphWork,
    worker_key: str,
    execution_mode: str = "deterministic",
) -> _AggregationWorkContext:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT graph_run.graph_run_id,graph_run.compiled_research_graph_id,
                       graph_run.status AS graph_run_status,
                       graph_run.requested_range,
                       graph_run.environment_fingerprint AS graph_environment_fingerprint,
                       graph.catalog_release_id AS graph_catalog_release_id,
                        plan.research_suite_id,plan.catalog_release_id,
                        plan.compiled_execution_data_context_id,
                       plan.physical_encoding_version_id AS plan_encoding_version_id,
                       plan.requested_range AS plan_requested_range,
                       plan.environment_fingerprint AS plan_environment_fingerprint,
                       plan_artifact.status AS plan_artifact_status,
                       execution_context.compiled_research_graph_id AS context_graph_id,
                       context_artifact.status AS context_artifact_status,
                       consumer.occurrence_kind,consumer.occurrence_key,
                       consumer.binding_disposition,consumer.released_at,
                       work.graph_work_item_id,work.work_kind,work.status AS work_status,
                       work.execution_fingerprint,work.lease_owner,
                       work.lease_expires_at,work.lease_expires_at>now() AS lease_active,
                       work.fencing_token,
                       work.cancel_requested_at,
                       binding.compiled_aggregation_instance_id,
                       binding.aggregation_run_id,binding.binding_disposition
                         AS aggregation_binding_disposition,
                       aggregation_run.artifact_id AS aggregation_run_artifact_id,
                       aggregation_run.aggregation_version_id,
                       aggregation_run.parameter_preset_version_id,
                       aggregation_run.target_version_id,
                       aggregation_run.training_preset_version_id,
                       aggregation_run.ensemble_spec_id,
                       aggregation_run.execution_fingerprint
                         AS aggregation_execution_fingerprint,
                       aggregation_run.resolved_parameters,
                       aggregation_run.executor_version,
                       aggregation_run.environment_fingerprint,
                       aggregation_run.status AS aggregation_run_status,
                       run_artifact.artifact_type AS aggregation_run_artifact_type,
                       run_artifact.artifact_key AS aggregation_run_artifact_key,
                       run_artifact.version_number AS aggregation_run_artifact_version,
                       run_artifact.status AS aggregation_run_artifact_status,
                       instance.compiled_research_graph_id AS instance_graph_id,
                       instance.aggregation_version_id AS instance_aggregation_version_id,
                       instance.parameter_preset_version_id
                         AS instance_parameter_preset_version_id,
                       instance.output_payload_contract_version_id,
                       aggregation_version.artifact_id AS aggregation_version_artifact_id,
                       aggregation_version.execution_mode,
                       aggregation_version.implementation_key,
                       aggregation_version_artifact.status
                         AS aggregation_version_artifact_status,
                       family.family_key,
                       preset_definition.parameter_preset_key,
                       preset_version.semantics AS parameter_preset_semantics,
                       target_definition.target_key,
                       target_version.semantics AS target_semantics,
                       target_version.artifact_id AS target_version_artifact_id,
                       target_artifact.status AS target_artifact_status,
                       training_definition.training_preset_key,
                       training_version.semantics AS training_preset_semantics,
                       training_version.artifact_id
                         AS training_preset_version_artifact_id,
                       training_artifact.status AS training_artifact_status,
                       compiled_ensemble.ensemble_spec_id
                         AS compiled_ensemble_spec_id,
                       ensemble.artifact_id AS ensemble_spec_artifact_id,
                       ensemble.ensemble_fingerprint,
                       ensemble.ensemble_document,
                       ensemble_artifact.status AS ensemble_artifact_status,
                       feature_schema.feature_schema_version_id,
                       feature_schema.artifact_id AS feature_schema_artifact_id,
                       feature_schema.ordered_feature_document
                         AS feature_schema_document,
                       feature_schema.feature_schema_fingerprint,
                       feature_schema_artifact.status AS feature_schema_artifact_status,
                       contract_family.contract_key AS output_contract_key,
                       contract.version_number AS output_contract_version,
                       contract.artifact_id AS output_contract_artifact_id,
                       contract_artifact.status AS output_contract_artifact_status,
                       encoding.physical_encoding_version_id,
                       encoding.artifact_id AS encoding_artifact_id,
                       encoding_artifact.status AS encoding_artifact_status,
                       compiled_recipe.recipe_fingerprint,
                       compiled_recipe.recipe_document
                  FROM workspace.v022_graph_work_consumer consumer
                  JOIN workspace.v022_graph_run graph_run
                    ON graph_run.graph_run_id=consumer.graph_run_id
                  JOIN workspace.compiled_research_graph graph
                    ON graph.compiled_research_graph_id=
                       graph_run.compiled_research_graph_id
                  JOIN experiment.v022_suite_runtime_plan plan
                    ON plan.graph_run_id=graph_run.graph_run_id
                  JOIN lineage.artifact plan_artifact
                    ON plan_artifact.artifact_id=plan.artifact_id
                  JOIN workspace.v022_compiled_execution_data_context execution_context
                    ON execution_context.compiled_execution_data_context_id=
                       plan.compiled_execution_data_context_id
                  JOIN lineage.artifact context_artifact
                    ON context_artifact.artifact_id=execution_context.artifact_id
                  JOIN workspace.v022_graph_work_item work
                    ON work.graph_work_item_id=consumer.graph_work_item_id
                  JOIN aggregation.graph_run_aggregation_binding binding
                    ON binding.graph_run_id=consumer.graph_run_id
                   AND binding.graph_work_item_id=consumer.graph_work_item_id
                  JOIN aggregation.aggregation_run aggregation_run
                    ON aggregation_run.aggregation_run_id=binding.aggregation_run_id
                  JOIN lineage.artifact run_artifact
                    ON run_artifact.artifact_id=aggregation_run.artifact_id
                  JOIN workspace.compiled_aggregation_instance instance
                    ON instance.compiled_aggregation_instance_id=
                       binding.compiled_aggregation_instance_id
                  JOIN aggregation.aggregation_version aggregation_version
                    ON aggregation_version.aggregation_version_id=
                       instance.aggregation_version_id
                  JOIN lineage.artifact aggregation_version_artifact
                    ON aggregation_version_artifact.artifact_id=
                       aggregation_version.artifact_id
                  JOIN aggregation.aggregation_family family
                    ON family.aggregation_family_id=
                       aggregation_version.aggregation_family_id
                  LEFT JOIN aggregation.parameter_preset_version preset_version
                    ON preset_version.parameter_preset_version_id=
                       instance.parameter_preset_version_id
                  LEFT JOIN aggregation.parameter_preset_definition preset_definition
                    ON preset_definition.parameter_preset_definition_id=
                       preset_version.parameter_preset_definition_id
                  LEFT JOIN aggregation.target_version target_version
                    ON target_version.target_version_id=
                       aggregation_run.target_version_id
                  LEFT JOIN aggregation.target_definition target_definition
                    ON target_definition.target_definition_id=
                       target_version.target_definition_id
                  LEFT JOIN lineage.artifact target_artifact
                    ON target_artifact.artifact_id=target_version.artifact_id
                  LEFT JOIN aggregation.training_preset_version training_version
                    ON training_version.training_preset_version_id=
                       aggregation_run.training_preset_version_id
                  LEFT JOIN aggregation.training_preset_definition training_definition
                    ON training_definition.training_preset_definition_id=
                       training_version.training_preset_definition_id
                  LEFT JOIN lineage.artifact training_artifact
                    ON training_artifact.artifact_id=training_version.artifact_id
                  LEFT JOIN workspace.v022_compiled_trainable_ensemble_binding
                    compiled_ensemble
                    ON compiled_ensemble.compiled_aggregation_instance_id=
                       instance.compiled_aggregation_instance_id
                  LEFT JOIN aggregation.v022_trainable_ensemble_spec ensemble
                    ON ensemble.ensemble_spec_id=aggregation_run.ensemble_spec_id
                  LEFT JOIN lineage.artifact ensemble_artifact
                    ON ensemble_artifact.artifact_id=ensemble.artifact_id
                  LEFT JOIN workspace.v022_compiled_feature_schema_binding
                    feature_schema_binding
                    ON feature_schema_binding.compiled_aggregation_instance_id=
                       instance.compiled_aggregation_instance_id
                  LEFT JOIN aggregation.v022_feature_schema_version feature_schema
                    ON feature_schema.feature_schema_version_id=
                       feature_schema_binding.feature_schema_version_id
                  LEFT JOIN lineage.artifact feature_schema_artifact
                    ON feature_schema_artifact.artifact_id=feature_schema.artifact_id
                  JOIN data.payload_contract_version contract
                    ON contract.payload_contract_version_id=
                       instance.output_payload_contract_version_id
                  JOIN data.payload_contract_family contract_family
                    ON contract_family.payload_contract_family_id=
                       contract.payload_contract_family_id
                  JOIN lineage.artifact contract_artifact
                    ON contract_artifact.artifact_id=contract.artifact_id
                  JOIN data.physical_encoding_version encoding
                    ON encoding.encoding_key=:encoding_key
                   AND encoding.version_number=:encoding_version
                  JOIN lineage.artifact encoding_artifact
                    ON encoding_artifact.artifact_id=encoding.artifact_id
                  LEFT JOIN workspace.v022_compiled_aggregation_recipe
                    compiled_recipe
                    ON compiled_recipe.compiled_aggregation_instance_id=
                       instance.compiled_aggregation_instance_id
                 WHERE consumer.graph_run_id=:graph_run
                   AND consumer.graph_work_item_id=:work_item
                """
            ),
            {
                "graph_run": graph_run_id,
                "work_item": claim.graph_work_item_id,
                "encoding_key": CANONICAL_ENCODING_KEY,
                "encoding_version": CANONICAL_ENCODING_VERSION,
            },
        ).mappings().one_or_none()
        if row is None:
            raise V022RuntimeContractError(
                "aggregation_work_binding_missing",
                "GraphWork lacks one exact Aggregation Run binding",
            )
        _validate_work_context_row(
            row,
            claim=claim,
            worker_key=worker_key,
            execution_mode=execution_mode,
        )
        inputs = _load_aggregation_inputs(
            connection,
            graph_run_id=graph_run_id,
            catalog_release_id=row["catalog_release_id"],
            compiled_aggregation_instance_id=row[
                "compiled_aggregation_instance_id"
            ],
            aggregation_run_id=row["aggregation_run_id"],
        )
        ensemble_members = _load_aggregation_ensemble_members(
            connection,
            ensemble_spec_id=row["ensemble_spec_id"],
            family_key=row["family_key"],
        )
        asset_keys = _execution_context_asset_keys(
            connection,
            compiled_execution_data_context_id=row[
                "compiled_execution_data_context_id"
            ],
        )
        decision_dates = _suite_cohort_decision_dates(
            connection, research_suite_id=row["research_suite_id"]
        )
        _validate_work_catalog_membership(connection, row)
        _validate_aggregation_run_lineage(
            connection,
            aggregation_run_artifact_id=row["aggregation_run_artifact_id"],
            aggregation_version_artifact_id=row[
                "aggregation_version_artifact_id"
            ],
            inputs=inputs,
            target_version_artifact_id=row["target_version_artifact_id"],
            training_preset_version_artifact_id=row[
                "training_preset_version_artifact_id"
            ],
            ensemble_spec_artifact_id=row["ensemble_spec_artifact_id"],
        )
    parameter_preset_key = _local_preset_key(
        row["family_key"], row["parameter_preset_key"]
    )
    target_key = _local_axis_key(
        row["family_key"], row["target_key"], axis_name="target"
    )
    training_preset_key = _local_axis_key(
        row["family_key"],
        row["training_preset_key"],
        axis_name="training_preset",
    )
    expected_parameters: Mapping[str, object] = (
        row["ensemble_document"]
        if row["ensemble_spec_id"] is not None
        else row["training_preset_semantics"]
        if row["execution_mode"] == "supervised"
        else row["parameter_preset_semantics"]
    ) or {}
    if row["resolved_parameters"] != expected_parameters:
        raise V022RuntimeContractError(
            "aggregation_resolved_parameters_drift",
            "Aggregation Run parameters differ from the exact compiled axis",
        )
    return _AggregationWorkContext(
        graph_run_id=graph_run_id,
        graph_work_item_id=claim.graph_work_item_id,
        fencing_token=claim.fencing_token,
        worker_key=worker_key,
        execution_fingerprint=row["execution_fingerprint"],
        catalog_release_id=row["catalog_release_id"],
        aggregation_run_id=row["aggregation_run_id"],
        aggregation_run_artifact_id=row["aggregation_run_artifact_id"],
        aggregation_version_id=row["aggregation_version_id"],
        aggregation_version_artifact_id=row["aggregation_version_artifact_id"],
        execution_mode=row["execution_mode"],
        compiled_research_graph_id=row["compiled_research_graph_id"],
        compiled_execution_data_context_id=row[
            "compiled_execution_data_context_id"
        ],
        research_suite_id=row["research_suite_id"],
        target_version_id=row["target_version_id"],
        target_version_artifact_id=row["target_version_artifact_id"],
        target_key=target_key,
        target_semantics=row["target_semantics"],
        training_preset_version_id=row["training_preset_version_id"],
        training_preset_version_artifact_id=row[
            "training_preset_version_artifact_id"
        ],
        training_preset_key=training_preset_key,
        training_preset_semantics=row["training_preset_semantics"],
        ensemble_spec_id=row["ensemble_spec_id"],
        ensemble_spec_artifact_id=row["ensemble_spec_artifact_id"],
        ensemble_fingerprint=row["ensemble_fingerprint"],
        ensemble_document=row["ensemble_document"],
        ensemble_members=ensemble_members,
        feature_schema_version_id=row["feature_schema_version_id"],
        feature_schema_artifact_id=row["feature_schema_artifact_id"],
        feature_schema_document=row["feature_schema_document"],
        feature_schema_fingerprint=row["feature_schema_fingerprint"],
        output_payload_contract_version_id=row[
            "output_payload_contract_version_id"
        ],
        physical_encoding_version_id=row["physical_encoding_version_id"],
        family_key=row["family_key"],
        parameter_preset_key=parameter_preset_key,
        resolved_parameters=row["resolved_parameters"],
        requested_range=row["requested_range"],
        executor_version=row["executor_version"],
        environment_fingerprint=row["environment_fingerprint"],
        asset_keys=asset_keys,
        decision_dates=decision_dates,
        inputs=inputs,
        compiled_recipe=row["recipe_document"],
    )


def _validate_work_context_row(
    row: RowMapping,
    *,
    claim: ClaimedGraphWork,
    worker_key: str,
    execution_mode: str,
) -> None:
    if execution_mode not in {"deterministic", "supervised"}:
        raise ValueError("Aggregation execution mode is invalid")
    expected_occurrence = f"aggregation:{row['compiled_aggregation_instance_id']}"
    active = (
        row["graph_run_status"] == "running"
        and row["occurrence_kind"] == "aggregation"
        and row["occurrence_key"] == expected_occurrence
        and row["binding_disposition"] == "execute"
        and row["released_at"] is None
        and row["work_kind"] == "aggregation"
        and row["work_status"] == "running"
        and row["lease_owner"] == worker_key
        and row["fencing_token"] == claim.fencing_token
        and row["lease_expires_at"] is not None
        and row["lease_active"] is True
        and row["cancel_requested_at"] is None
    )
    if not active:
        raise V022RuntimeContractError(
            "aggregation_work_fence_invalid",
            "Aggregation GraphWork lease is stale, released, cancelled, or not exact",
        )
    if (
        row["aggregation_binding_disposition"] != "executed"
        or row["aggregation_run_status"] != "running"
        or row["aggregation_execution_fingerprint"] != row["execution_fingerprint"]
        or row["aggregation_run_artifact_type"] != AGGREGATION_RUN_ARTIFACT_TYPE
        or row["aggregation_run_artifact_key"] != str(row["aggregation_run_id"])
        or row["aggregation_run_artifact_version"] != 1
        or row["aggregation_run_artifact_status"] != "published"
    ):
        raise V022RuntimeContractError(
            "aggregation_run_identity_invalid",
            "GraphWork is not bound to its exact published running Aggregation Run",
        )
    if (
        row["instance_graph_id"] != row["compiled_research_graph_id"]
        or row["context_graph_id"] != row["compiled_research_graph_id"]
        or row["catalog_release_id"] != row["graph_catalog_release_id"]
        or row["plan_artifact_status"] != "published"
        or row["context_artifact_status"] != "published"
        or row["plan_requested_range"] != row["requested_range"]
        or row["plan_environment_fingerprint"]
        != row["graph_environment_fingerprint"]
        or row["aggregation_version_id"] != row["instance_aggregation_version_id"]
        or row["parameter_preset_version_id"]
        != row["instance_parameter_preset_version_id"]
        or row["execution_mode"] != execution_mode
        or row["aggregation_version_artifact_status"] != "published"
        or row["output_contract_key"] != FINAL_SIGNAL_CONTRACT_KEY
        or row["output_contract_version"] != FINAL_SIGNAL_CONTRACT_VERSION
        or row["output_contract_artifact_status"] != "published"
        or row["encoding_artifact_status"] != "published"
        or row["physical_encoding_version_id"] != row["plan_encoding_version_id"]
        or row["environment_fingerprint"] != row["graph_environment_fingerprint"]
    ):
        raise V022RuntimeContractError(
            "aggregation_compiled_identity_mismatch",
            "Aggregation Run differs from its exact compiled identity",
        )
    if execution_mode == "supervised":
        direct_bound = row["target_version_id"] is not None
        ensemble_bound = row["ensemble_spec_id"] is not None
        if direct_bound == ensemble_bound:
            raise V022RuntimeContractError(
                "aggregation_supervised_identity_incomplete",
                "Supervised Aggregation must bind direct axes or one Ensemble Spec",
            )
        common_incomplete = (
            row["feature_schema_version_id"] is None
            or row["feature_schema_artifact_id"] is None
            or row["feature_schema_artifact_status"] != "published"
            or row["feature_schema_document"] is None
            or row["feature_schema_fingerprint"] is None
        )
        direct_incomplete = direct_bound and (
            row["target_version_artifact_id"] is None
            or row["target_artifact_status"] != "published"
            or row["target_key"] is None
            or row["target_semantics"] is None
            or row["training_preset_version_id"] is None
            or row["training_preset_version_artifact_id"] is None
            or row["training_artifact_status"] != "published"
            or row["training_preset_key"] is None
            or row["training_preset_semantics"] is None
            or row["compiled_ensemble_spec_id"] is not None
        )
        ensemble_incomplete = ensemble_bound and (
            row["target_version_id"] is not None
            or row["training_preset_version_id"] is not None
            or row["compiled_ensemble_spec_id"] != row["ensemble_spec_id"]
            or row["ensemble_spec_artifact_id"] is None
            or row["ensemble_artifact_status"] != "published"
            or row["ensemble_fingerprint"] is None
            or row["ensemble_document"] is None
        )
        if common_incomplete or direct_incomplete or ensemble_incomplete:
            raise V022RuntimeContractError(
                "aggregation_supervised_identity_incomplete",
                "Supervised Aggregation lacks its exact published identity or Feature Schema",
            )
        if sha256_hexdigest(row["feature_schema_document"]) != row[
            "feature_schema_fingerprint"
        ]:
            raise V022RuntimeContractError(
                "aggregation_feature_schema_fingerprint_invalid",
                "Supervised Feature Schema fingerprint differs from its document",
            )
        if ensemble_bound and sha256_hexdigest(row["ensemble_document"]) != row[
            "ensemble_fingerprint"
        ]:
            raise V022RuntimeContractError(
                "aggregation_ensemble_fingerprint_invalid",
                "Trainable Ensemble fingerprint differs from its frozen document",
            )
    elif any(
        row[key] is not None
        for key in (
            "target_version_id",
            "training_preset_version_id",
            "ensemble_spec_id",
            "compiled_ensemble_spec_id",
            "feature_schema_version_id",
        )
    ):
        raise V022RuntimeContractError(
            "aggregation_deterministic_axes_invalid",
            "Deterministic Aggregation cannot bind supervised axes",
        )
    expected_implementation = {
        "single_signal_identity": "style_rotation.v022.aggregation.single_signal_identity_v1",
        "flat_equal_weight_mean": "style_rotation.v022.aggregation.flat_equal_weight_mean_v1",
        "directional_weighted_vote": (
            "style_rotation.v022.aggregation.directional_weighted_vote_v1"
        ),
        "ols_cross_sectional_regression": (
            "style_rotation.v022.aggregation.ols_cross_sectional_regression_v1"
        ),
        "ridge_cross_sectional_regression": (
            "style_rotation.v022.aggregation.ridge_cross_sectional_regression_v1"
        ),
        "random_forest_cross_sectional_regression": (
            "style_rotation.v022.aggregation.random_forest_cross_sectional_regression_v1"
        ),
        "lightgbm_cross_sectional_regression": (
            "style_rotation.v022.aggregation.lightgbm_cross_sectional_regression_v1"
        ),
        "xgboost_cross_sectional_regression": (
            "style_rotation.v022.aggregation.xgboost_cross_sectional_regression_v1"
        ),
    }.get(row["family_key"])
    implementation_valid = (
        row["implementation_key"] == expected_implementation
        if expected_implementation is not None
        else row["family_key"] == "hierarchical_weighted_mean"
        and row["implementation_key"]
        in {
            "style_rotation.v022.aggregation.hierarchical_weighted_mean_v1",
            "style_rotation.v022.aggregation.hierarchical_weighted_mean_v2",
        }
    )
    if not implementation_valid:
        raise V022RuntimeContractError(
            "aggregation_implementation_not_frozen",
            "Aggregation Version implementation is not supported by this executor",
        )
    if execution_mode == "supervised":
        if not implementation_valid:
            raise V022RuntimeContractError(
                "aggregation_implementation_not_frozen",
                "Supervised Aggregation implementation is not supported",
            )
        if row["recipe_document"] is not None:
            raise V022RuntimeContractError(
                "aggregation_supervised_recipe_invalid",
                "Supervised Aggregation cannot bind a deterministic Recipe",
            )
        return
    native_recipe_selected = (
        row["family_key"] == "hierarchical_weighted_mean"
        and row["parameter_preset_key"]
        == "hierarchical_weighted_mean__active_dimension_equal_component_equal_v1"
    )
    if native_recipe_selected != (row["recipe_document"] is not None):
        raise V022RuntimeContractError(
            "aggregation_compiled_recipe_binding_invalid",
            "Native Recipe binding does not match the compiled Aggregation preset",
        )
    if row["recipe_document"] is not None and sha256_hexdigest(
        row["recipe_document"]
    ) != row["recipe_fingerprint"]:
        raise V022RuntimeContractError(
            "aggregation_compiled_recipe_fingerprint_invalid",
            "Compiled Aggregation Recipe fingerprint does not match its document",
        )


def _load_aggregation_inputs(
    connection: Connection,
    *,
    graph_run_id: uuid.UUID,
    catalog_release_id: uuid.UUID,
    compiled_aggregation_instance_id: uuid.UUID,
    aggregation_run_id: uuid.UUID,
) -> tuple[_AggregationInputBinding, ...]:
    rows = connection.execute(
        text(
            """
            SELECT compiled.ordinal,compiled.slot_key,
                   compiled.compiled_feature_occurrence_id,
                   variant.variant_key,
                   run_input.payload_manifest_id,run_input.manifest_hash,
                   manifest.artifact_id AS manifest_artifact_id
              FROM workspace.compiled_aggregation_input compiled
              JOIN workspace.compiled_feature_occurrence occurrence
                ON occurrence.compiled_feature_occurrence_id=
                   compiled.compiled_feature_occurrence_id
              JOIN processing.feature_version feature_version
                ON feature_version.feature_version_id=occurrence.feature_version_id
              JOIN processing.feature_variant variant
                ON variant.feature_variant_id=feature_version.feature_variant_id
              LEFT JOIN aggregation.aggregation_run_input run_input
                ON run_input.aggregation_run_id=:aggregation_run
               AND run_input.slot_key=compiled.slot_key
               AND run_input.ordinal=compiled.ordinal
              LEFT JOIN data.payload_manifest manifest
                ON manifest.payload_manifest_id=run_input.payload_manifest_id
             WHERE compiled.compiled_aggregation_instance_id=:instance
             ORDER BY compiled.ordinal,compiled.slot_key
            """
        ),
        {
            "aggregation_run": aggregation_run_id,
            "instance": compiled_aggregation_instance_id,
        },
    ).mappings().all()
    run_input_count = connection.scalar(
        text(
            "SELECT count(*) FROM aggregation.aggregation_run_input "
            "WHERE aggregation_run_id=:run"
        ),
        {"run": aggregation_run_id},
    )
    if (
        not rows
        or run_input_count != len(rows)
        or tuple(row["ordinal"] for row in rows) != tuple(range(len(rows)))
        or any(row["payload_manifest_id"] is None for row in rows)
    ):
        raise V022RuntimeContractError(
            "aggregation_run_input_projection_invalid",
            "Aggregation Run inputs do not exactly reproduce compiled ordered inputs",
        )
    result = []
    for row in rows:
        expected_manifest_id, expected_artifact_id = _expected_occurrence_manifest(
            connection,
            graph_run_id=graph_run_id,
            catalog_release_id=catalog_release_id,
            occurrence_id=row["compiled_feature_occurrence_id"],
        )
        if (
            row["payload_manifest_id"] != expected_manifest_id
            or row["manifest_artifact_id"] != expected_artifact_id
        ):
            raise V022RuntimeContractError(
                "aggregation_occurrence_manifest_mismatch",
                "Aggregation Run input is not the exact Manifest of its compiled occurrence",
            )
        result.append(
            _AggregationInputBinding(
                compiled_feature_occurrence_id=row[
                    "compiled_feature_occurrence_id"
                ],
                feature_variant_key=row["variant_key"],
                slot_key=row["slot_key"],
                ordinal=row["ordinal"],
                payload_manifest_id=row["payload_manifest_id"],
                manifest_artifact_id=row["manifest_artifact_id"],
                manifest_hash=row["manifest_hash"],
            )
        )
    return tuple(result)


def _load_aggregation_ensemble_members(
    connection: Connection,
    *,
    ensemble_spec_id: uuid.UUID | None,
    family_key: str,
) -> tuple[_AggregationEnsembleMember, ...]:
    if ensemble_spec_id is None:
        return ()
    rows = connection.execute(
        text(
            """
            SELECT member.ordinal,member.target_group_ordinal,
                   member.member_ordinal_within_target,
                   member.target_version_id,target.artifact_id AS target_artifact_id,
                   target_definition.target_key,target.semantics AS target_semantics,
                   target_artifact.status AS target_status,
                   member.training_preset_version_id,
                   training.artifact_id AS training_artifact_id,
                   training_definition.training_preset_key,
                   training.semantics AS training_semantics,
                   training_artifact.status AS training_status
              FROM aggregation.v022_trainable_ensemble_member member
              JOIN aggregation.target_version target
                ON target.target_version_id=member.target_version_id
              JOIN aggregation.target_definition target_definition
                ON target_definition.target_definition_id=target.target_definition_id
              JOIN lineage.artifact target_artifact
                ON target_artifact.artifact_id=target.artifact_id
              JOIN aggregation.training_preset_version training
                ON training.training_preset_version_id=
                   member.training_preset_version_id
              JOIN aggregation.training_preset_definition training_definition
                ON training_definition.training_preset_definition_id=
                   training.training_preset_definition_id
              JOIN lineage.artifact training_artifact
                ON training_artifact.artifact_id=training.artifact_id
             WHERE member.ensemble_spec_id=:ensemble
             ORDER BY member.ordinal
            """
        ),
        {"ensemble": ensemble_spec_id},
    ).mappings().all()
    if (
        not 2 <= len(rows) <= 12
        or tuple(row["ordinal"] for row in rows) != tuple(range(len(rows)))
        or any(
            row["target_status"] != "published"
            or row["training_status"] != "published"
            for row in rows
        )
    ):
        raise V022RuntimeContractError(
            "aggregation_ensemble_member_closure_invalid",
            "Trainable Ensemble members are incomplete or unpublished",
        )
    return tuple(
        _AggregationEnsembleMember(
            ordinal=row["ordinal"],
            target_group_ordinal=row["target_group_ordinal"],
            member_ordinal_within_target=row["member_ordinal_within_target"],
            target_version_id=row["target_version_id"],
            target_version_artifact_id=row["target_artifact_id"],
            target_key=cast(
                str,
                _local_axis_key(
                    family_key,
                    row["target_key"],
                    axis_name="target",
                ),
            ),
            target_semantics=row["target_semantics"],
            training_preset_version_id=row["training_preset_version_id"],
            training_preset_version_artifact_id=row["training_artifact_id"],
            training_preset_key=cast(
                str,
                _local_axis_key(
                    family_key,
                    row["training_preset_key"],
                    axis_name="training_preset",
                ),
            ),
            training_preset_semantics=row["training_semantics"],
        )
        for row in rows
    )


def _expected_occurrence_manifest(
    connection: Connection,
    *,
    graph_run_id: uuid.UUID,
    catalog_release_id: uuid.UUID,
    occurrence_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    chain = connection.execute(
        text(
            """
            WITH RECURSIVE chain AS (
              SELECT occurrence.compiled_feature_occurrence_id,
                     occurrence.feature_version_id,occurrence.stage_no,
                     occurrence.production_kind,occurrence.source_occurrence_id,
                     occurrence.compiled_graph_node_id,occurrence.output_port_key
                FROM workspace.compiled_feature_occurrence occurrence
               WHERE occurrence.compiled_feature_occurrence_id=:occurrence
              UNION ALL
              SELECT source.compiled_feature_occurrence_id,
                     source.feature_version_id,source.stage_no,
                     source.production_kind,source.source_occurrence_id,
                     source.compiled_graph_node_id,source.output_port_key
                FROM workspace.compiled_feature_occurrence source
                JOIN chain ON source.compiled_feature_occurrence_id=
                              chain.source_occurrence_id
            )
            SELECT * FROM chain ORDER BY stage_no DESC
            """
        ),
        {"occurrence": occurrence_id},
    ).mappings().all()
    if not chain or chain[0]["compiled_feature_occurrence_id"] != occurrence_id:
        raise V022RuntimeContractError(
            "aggregation_compiled_occurrence_missing",
            "Exact compiled Aggregation input occurrence does not exist",
        )
    feature_version_id = chain[0]["feature_version_id"]
    for current, following in zip(chain, chain[1:], strict=False):
        if (
            current["production_kind"] != "layer_projection"
            or current["source_occurrence_id"]
            != following["compiled_feature_occurrence_id"]
            or current["stage_no"] != following["stage_no"] + 1
            or current["feature_version_id"] != feature_version_id
            or following["feature_version_id"] != feature_version_id
        ):
            raise V022RuntimeContractError(
                "aggregation_projection_chain_invalid",
                "Layer projection is not one exact same-Feature compiled lineage chain",
            )
    producer = chain[-1]
    if producer["production_kind"] == "raw_input":
        raise V022RuntimeContractError(
            "aggregation_raw_manifest_binding_missing",
            "Raw-input projection has no exact runtime Manifest binding; Planner must supply one",
        )
    if producer["production_kind"] != "node_output":
        raise V022RuntimeContractError(
            "aggregation_occurrence_producer_invalid",
            "Aggregation input occurrence does not resolve to a Node output",
        )
    row = connection.execute(
        text(
            """
            SELECT output.payload_manifest_id,manifest.artifact_id,
                   manifest.producer_artifact_id,node_run.artifact_id AS node_run_artifact_id,
                   node_run.node_version_id,
                   compiled_node.node_version_id AS compiled_node_version_id,
                   node_version.artifact_id AS node_version_artifact_id,
                   node_run.status AS node_run_status,
                   node_artifact.status AS node_run_artifact_status,
                   work.status AS node_work_status
              FROM processing.graph_run_node_binding binding
              JOIN workspace.compiled_graph_node compiled_node
                ON compiled_node.compiled_graph_node_id=
                   binding.compiled_graph_node_id
              JOIN processing.node_run node_run
                ON node_run.node_run_id=binding.node_run_id
              JOIN processing.node_version node_version
                ON node_version.node_version_id=node_run.node_version_id
              JOIN lineage.artifact node_artifact
                ON node_artifact.artifact_id=node_run.artifact_id
              JOIN processing.node_run_output output
                ON output.node_run_id=node_run.node_run_id
               AND output.output_port_key=:output_port
              JOIN data.payload_manifest manifest
                ON manifest.payload_manifest_id=output.payload_manifest_id
              JOIN workspace.v022_graph_work_item work
                ON work.graph_work_item_id=binding.graph_work_item_id
             WHERE binding.graph_run_id=:graph_run
               AND binding.compiled_graph_node_id=:compiled_node
            """
        ),
        {
            "graph_run": graph_run_id,
            "compiled_node": producer["compiled_graph_node_id"],
            "output_port": producer["output_port_key"],
        },
    ).mappings().one_or_none()
    if (
        row is None
        or row["producer_artifact_id"] != row["node_run_artifact_id"]
        or row["node_version_id"] != row["compiled_node_version_id"]
        or row["node_run_status"] != "completed"
        or row["node_run_artifact_status"] != "published"
        or row["node_work_status"] not in {"completed", "reused"}
    ):
        raise V022RuntimeContractError(
            "aggregation_upstream_node_output_unpublished",
            "Compiled input does not resolve to an exact completed Node output Manifest",
        )
    membership = connection.scalar(
        text(
            """
            SELECT count(*) FROM workspace.v022_catalog_release_component
             WHERE catalog_release_id=:release
               AND component_artifact_id=:artifact
               AND component_kind='processing_node_version'
            """
        ),
        {
            "release": catalog_release_id,
            "artifact": row["node_version_artifact_id"],
        },
    )
    if membership != 1:
        raise V022RuntimeContractError(
            "aggregation_upstream_node_catalog_unpinned",
            "Upstream Node Version is outside the Graph-pinned Catalog Release",
        )
    return row["payload_manifest_id"], row["artifact_id"]


def _validate_aggregation_run_lineage(
    connection: Connection,
    *,
    aggregation_run_artifact_id: uuid.UUID,
    aggregation_version_artifact_id: uuid.UUID,
    inputs: tuple[_AggregationInputBinding, ...],
    target_version_artifact_id: uuid.UUID | None = None,
    training_preset_version_artifact_id: uuid.UUID | None = None,
    ensemble_spec_artifact_id: uuid.UUID | None = None,
) -> None:
    dependencies = connection.execute(
        text(
            """
            SELECT depends_on_artifact_id,role,ordinal
              FROM lineage.artifact_dependency
             WHERE artifact_id=:artifact
             ORDER BY ordinal,role,depends_on_artifact_id
            """
        ),
        {"artifact": aggregation_run_artifact_id},
    ).mappings().all()
    expected = {
        (aggregation_version_artifact_id, "aggregation_version", 0),
        *{
            (item.manifest_artifact_id, "aggregation_input", item.ordinal + 1)
            for item in inputs
        },
    }
    next_ordinal = len(inputs) + 1
    if target_version_artifact_id is not None:
        expected.add((target_version_artifact_id, "target_version", next_ordinal))
        next_ordinal += 1
    if training_preset_version_artifact_id is not None:
        expected.add(
            (
                training_preset_version_artifact_id,
                "training_preset_version",
                next_ordinal,
            )
        )
        next_ordinal += 1
    if ensemble_spec_artifact_id is not None:
        expected.add(
            (
                ensemble_spec_artifact_id,
                "trainable_ensemble_spec",
                next_ordinal,
            )
        )
    actual = {
        (row["depends_on_artifact_id"], row["role"], row["ordinal"])
        for row in dependencies
    }
    if actual != expected:
        raise V022RuntimeContractError(
            "aggregation_run_lineage_invalid",
            "Aggregation Run Artifact does not freeze its exact Version and input Manifests",
        )


def _suite_cohort_decision_dates(
    connection: Connection, *, research_suite_id: uuid.UUID
) -> frozenset[date]:
    contracts = connection.execute(
        text(
            """
            SELECT contract.evaluation_cohort_runtime_contract_id,
                   contract.evaluation_cohort_version_id
              FROM experiment.v022_research_suite_evaluation_cohort_binding binding
              JOIN experiment.v022_evaluation_cohort_runtime_contract contract
                ON contract.evaluation_cohort_version_id=
                   binding.evaluation_cohort_version_id
             WHERE binding.research_suite_id=:suite
            """
        ),
        {"suite": research_suite_id},
    ).mappings().all()
    if len(contracts) != 1:
        raise V022RuntimeContractError(
            "aggregation_cohort_runtime_contract_missing",
            "Aggregation Work requires one exact frozen Cohort Runtime Contract",
            details={"contract_count": len(contracts)},
        )
    rows = connection.execute(
        text(
            """
            SELECT session.session_date
              FROM experiment.v022_evaluation_cohort_session session
             WHERE session.evaluation_cohort_version_id=:cohort
               AND session.session_role='evaluation'
               AND session.is_decision_session=true
             ORDER BY session.ordinal
            """
        ),
        {"cohort": contracts[0]["evaluation_cohort_version_id"]},
    ).scalars().all()
    decision_dates = frozenset(cast(date, item) for item in rows)
    if not decision_dates:
        raise V022RuntimeDataError(
            "aggregation_cohort_decision_dates_empty",
            "Aggregation Work requires nonempty frozen Cohort decision dates",
        )
    return decision_dates


def _execution_context_asset_keys(
    connection: Connection,
    *,
    compiled_execution_data_context_id: uuid.UUID,
) -> dict[uuid.UUID, str]:
    context = connection.execute(
        text(
            """
            SELECT context.asset_context_document,context.asset_set_definition_id,
                   context.explicit_asset_selection_id
              FROM workspace.v022_compiled_execution_data_context context
             WHERE context.compiled_execution_data_context_id=:context
            """
        ),
        {"context": compiled_execution_data_context_id},
    ).mappings().one()
    document = context["asset_context_document"]
    if document.get("selection_kind") == "fixed_asset_set":
        rows = connection.execute(
            text(
                """
                SELECT member.ordinal,security.security_id,security.security_key
                  FROM catalog.asset_set_member member
                  JOIN catalog.security security
                    ON security.security_id=member.security_id
                 WHERE member.asset_set_definition_id=:definition
                 ORDER BY member.ordinal
                """
            ),
            {"definition": context["asset_set_definition_id"]},
        ).mappings().all()
    elif document.get("selection_kind") == "dynamic_universe_snapshot":
        rows = connection.execute(
            text(
                """
                SELECT member.ordinal,security.security_id,security.security_key
                  FROM catalog.universe_snapshot_member member
                  JOIN catalog.security security
                    ON security.security_id=member.security_id
                 WHERE member.universe_snapshot_id=:snapshot
                 ORDER BY member.ordinal
                """
            ),
            {"snapshot": uuid.UUID(document["universe_snapshot_id"])},
        ).mappings().all()
    else:
        rows = connection.execute(
            text(
                """
                SELECT member.ordinal,security.security_id,security.security_key
                  FROM workspace.v022_explicit_asset_selection_member member
                  JOIN catalog.security security ON security.security_id=member.security_id
                 WHERE member.explicit_asset_selection_id=:selection
                 ORDER BY member.ordinal
                """
            ),
            {"selection": context["explicit_asset_selection_id"]},
        ).mappings().all()
    authoritative = tuple(
        (row["security_id"], row["security_key"]) for row in rows
    )
    documented = tuple(
        (uuid.UUID(item["security_id"]), item["security_key"])
        for item in document.get("members", [])
    )
    if (
        not authoritative
        or authoritative != documented
        or tuple(row["ordinal"] for row in rows) != tuple(range(len(rows)))
    ):
        raise V022RuntimeContractError(
            "aggregation_execution_asset_context_invalid",
            "Compiled Execution Data Context does not reproduce its exact Asset Set",
        )
    return dict(authoritative)


def _validate_work_catalog_membership(
    connection: Connection, row: RowMapping
) -> None:
    expected = {
        (row["aggregation_version_artifact_id"], "aggregation_version"),
        (row["output_contract_artifact_id"], "payload_contract_version"),
        (row["encoding_artifact_id"], "physical_encoding_version"),
    }
    memberships = connection.execute(
        text(
            """
            SELECT component_artifact_id,component_kind
              FROM workspace.v022_catalog_release_component
             WHERE catalog_release_id=:release
               AND component_artifact_id=ANY(:artifacts)
            """
        ),
        {
            "release": row["catalog_release_id"],
            "artifacts": [item[0] for item in expected],
        },
    ).mappings().all()
    actual = {
        (item["component_artifact_id"], item["component_kind"])
        for item in memberships
    }
    if actual != expected:
        raise V022RuntimeContractError(
            "aggregation_runtime_catalog_unpinned",
            "Aggregation Version, output Contract, or encoding is outside the Graph Catalog",
        )


def _local_preset_key(family_key: str, stored_key: str | None) -> str | None:
    return _local_axis_key(
        family_key, stored_key, axis_name="parameter_preset"
    )


def _local_axis_key(
    family_key: str,
    stored_key: str | None,
    *,
    axis_name: str,
) -> str | None:
    if stored_key is None:
        return None
    prefix = f"{family_key}__"
    if not stored_key.startswith(prefix) or len(stored_key) == len(prefix):
        raise V022RuntimeContractError(
            f"aggregation_{axis_name}_identity_invalid",
            f"Aggregation {axis_name.replace('_', ' ').title()} is not owned by its exact Family",
        )
    return stored_key[len(prefix) :]


def _prepare_aggregation_output(
    object_store: LocalPayloadObjectStore,
    *,
    context: _AggregationWorkContext,
    calculation: AggregationCalculation,
    content: bytes,
) -> _PreparedAggregationOutput:
    stored = object_store.publish(content, file_extension="parquet")
    if stored.content_hash != hashlib.sha256(content).hexdigest():
        raise V022RuntimeDataError(
            "aggregation_output_store_hash_mismatch",
            "Content-addressed store returned a different output hash",
        )
    partition_fields = {"aggregation_run_id": str(context.aggregation_run_id)}
    partition_key_hash = sha256_hexdigest(partition_fields)
    partition_key: Mapping[str, object] = {
        "fields": partition_fields,
        "partition_key_hash": partition_key_hash,
    }
    coverage = _coverage_document(calculation.points)
    descriptor_hash = sha256_hexdigest(
        {
            "object_content_hash": stored.content_hash,
            "payload_contract_version_id": context.output_payload_contract_version_id,
            "physical_encoding_version_id": context.physical_encoding_version_id,
            "partition_key_hash": partition_key_hash,
            "partition_key": partition_fields,
            "coverage": coverage,
            "row_count": len(calculation.points),
        }
    )
    payload_object_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"bird-v022:payload-object:{stored.content_hash}"
    )
    payload_partition_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"bird-v022:payload-partition:{descriptor_hash}"
    )
    logical_fingerprint = sha256_hexdigest(
        {
            "payload_contract_version_id": context.output_payload_contract_version_id,
            "calculation_fingerprint": calculation.calculation_fingerprint,
            "partition_descriptor_hash": descriptor_hash,
        }
    )
    manifest_hash = sha256_hexdigest(
        {
            "aggregation_run_id": context.aggregation_run_id,
            "output_port_key": FINAL_SIGNAL_OUTPUT_PORT,
            "physical_encoding_version_id": context.physical_encoding_version_id,
            "logical_payload_fingerprint": logical_fingerprint,
            "payload_partition_id": payload_partition_id,
            "partition_descriptor_hash": descriptor_hash,
        }
    )
    return _PreparedAggregationOutput(
        content=content,
        content_hash=stored.content_hash,
        storage_uri=stored.storage_uri,
        byte_size=stored.byte_size,
        row_count=len(calculation.points),
        missing_count=sum(item.signal_value is None for item in calculation.points),
        coverage_document=coverage,
        partition_key=partition_key,
        payload_object_id=payload_object_id,
        payload_partition_id=payload_partition_id,
        partition_descriptor_hash=descriptor_hash,
        payload_manifest_id=uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird-v022:payload-manifest:{manifest_hash}"
        ),
        logical_payload_fingerprint=logical_fingerprint,
        manifest_hash=manifest_hash,
    )


def _publish_aggregation_output(
    engine: Engine,
    *,
    context: _AggregationWorkContext,
    calculation: AggregationCalculation,
    prepared: _PreparedAggregationOutput,
    additional_dependencies: tuple[DependencyInput, ...] = (),
) -> PublishedAggregationOutput:
    semantic_payload: Mapping[str, object] = {
        "aggregation_run_id": context.aggregation_run_id,
        "graph_work_item_id": context.graph_work_item_id,
        "execution_fingerprint": context.execution_fingerprint,
        "family_key": context.family_key,
        "execution_mode": context.execution_mode,
        "parameter_preset_key": context.parameter_preset_key,
        "target_version_id": context.target_version_id,
        "training_preset_version_id": context.training_preset_version_id,
        "ensemble_spec_id": context.ensemble_spec_id,
        "ensemble_fingerprint": context.ensemble_fingerprint,
        "payload_contract_version_id": context.output_payload_contract_version_id,
        "calculation_fingerprint": calculation.calculation_fingerprint,
        "logical_payload_fingerprint": prepared.logical_payload_fingerprint,
    }
    content_payload: Mapping[str, object] = {
        **semantic_payload,
        "physical_encoding_version_id": context.physical_encoding_version_id,
        "manifest_hash": prepared.manifest_hash,
        "partition_descriptor_hash": prepared.partition_descriptor_hash,
        "object_content_hash": prepared.content_hash,
        "coverage_document": prepared.coverage_document,
        "row_count": prepared.row_count,
        "missing_count": prepared.missing_count,
    }
    dependencies = (
        DependencyInput(
            context.aggregation_run_artifact_id, "producer_aggregation_run", 0
        ),
        *tuple(
            DependencyInput(item.manifest_artifact_id, "aggregation_input", item.ordinal + 1)
            for item in context.inputs
        ),
        *additional_dependencies,
    )
    artifact_key = (
        f"aggregation-output:{context.aggregation_run_id}:{FINAL_SIGNAL_OUTPUT_PORT}"
    )
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"v022-aggregation-output:{context.aggregation_run_id}"},
        )
        _lock_active_claim(connection, context)
        publication = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
            artifact_type=OUTPUT_ARTIFACT_TYPE,
            artifact_key=artifact_key,
            version_number=1,
            semantic_payload=semantic_payload,
            content_payload=content_payload,
            dependencies=dependencies,
            reason=f"publish {context.execution_mode} Aggregation output "
            f"{context.aggregation_run_id}",
            draft_writer=lambda draft_connection, artifact_id: _write_aggregation_manifest(
                draft_connection,
                artifact_id,
                context=context,
                prepared=prepared,
            ),
        )
        _lock_active_claim(connection, context)
        connection.execute(
            text(
                "SELECT workspace.v022_finish_graph_work("
                ":item,:worker,:fence,'completed',CAST(:details AS jsonb))"
            ),
            {
                "item": context.graph_work_item_id,
                "worker": context.worker_key,
                "fence": context.fencing_token,
                "details": _json(
                    {
                        "aggregation_run_id": str(context.aggregation_run_id),
                        "payload_manifest_id": str(prepared.payload_manifest_id),
                        "manifest_hash": prepared.manifest_hash,
                        "calculation_fingerprint": calculation.calculation_fingerprint,
                    }
                ),
            },
        )
        _verify_published_aggregation_output(
            connection,
            context=context,
            artifact_id=publication.artifact_id,
            prepared=prepared,
        )
    return PublishedAggregationOutput(
        aggregation_run_id=context.aggregation_run_id,
        graph_work_item_id=context.graph_work_item_id,
        artifact_id=publication.artifact_id,
        payload_manifest_id=prepared.payload_manifest_id,
        payload_partition_id=prepared.payload_partition_id,
        manifest_hash=prepared.manifest_hash,
        calculation_fingerprint=calculation.calculation_fingerprint,
        reused_publication=publication.reused,
    )


def _lock_active_claim(connection: Connection, context: _AggregationWorkContext) -> None:
    row = connection.execute(
        text(
            """
            SELECT work.status,work.lease_owner,work.lease_expires_at,
                   work.lease_expires_at>now() AS lease_active,
                   work.fencing_token,work.cancel_requested_at,
                   consumer.released_at,graph_run.status AS graph_run_status
              FROM workspace.v022_graph_work_item work
              JOIN workspace.v022_graph_work_consumer consumer
                ON consumer.graph_work_item_id=work.graph_work_item_id
               AND consumer.graph_run_id=:graph_run
              JOIN workspace.v022_graph_run graph_run
                ON graph_run.graph_run_id=consumer.graph_run_id
             WHERE work.graph_work_item_id=:item
             FOR UPDATE OF work
            """
        ),
        {"graph_run": context.graph_run_id, "item": context.graph_work_item_id},
    ).mappings().one_or_none()
    if (
        row is None
        or row["status"] != "running"
        or row["lease_owner"] != context.worker_key
        or row["fencing_token"] != context.fencing_token
        or row["lease_expires_at"] is None
        or row["lease_active"] is not True
        or row["cancel_requested_at"] is not None
        or row["released_at"] is not None
        or row["graph_run_status"] != "running"
    ):
        raise V022RuntimeContractError(
            "aggregation_work_fence_invalid",
            "Aggregation output cannot publish under a stale or cancelled fence",
        )


def _write_aggregation_manifest(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _AggregationWorkContext,
    prepared: _PreparedAggregationOutput,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO data.payload_object (
              payload_object_id,object_content_hash,storage_uri,byte_size,
              object_state,verification_status,verified_at
            ) VALUES (
              :id,:content_hash,:storage_uri,:byte_size,
              'published','verified',:verified_at
            ) ON CONFLICT (object_content_hash) DO NOTHING
            """
        ),
        {
            "id": prepared.payload_object_id,
            "content_hash": prepared.content_hash,
            "storage_uri": prepared.storage_uri,
            "byte_size": prepared.byte_size,
            "verified_at": datetime.now(UTC),
        },
    )
    object_row = connection.execute(
        text(
            """
            SELECT payload_object_id,storage_uri,byte_size,object_state,
                   verification_status,verified_at
              FROM data.payload_object WHERE object_content_hash=:content_hash
            """
        ),
        {"content_hash": prepared.content_hash},
    ).mappings().one()
    if (
        object_row["payload_object_id"] != prepared.payload_object_id
        or object_row["storage_uri"] != prepared.storage_uri
        or object_row["byte_size"] != prepared.byte_size
        or object_row["object_state"] != "published"
        or object_row["verification_status"] != "verified"
        or object_row["verified_at"] is None
    ):
        raise V022RuntimeDataError(
            "aggregation_output_object_conflict",
            "Content-addressed output Object identity is not reusable",
        )
    statistics = {
        "missing_count": prepared.missing_count,
        "finite_count": prepared.row_count - prepared.missing_count,
        "calculation_kind": f"{context.execution_mode}_aggregation",
    }
    connection.execute(
        text(
            """
            INSERT INTO data.payload_partition (
              payload_partition_id,payload_object_id,partition_descriptor_hash,
              byte_size,row_or_item_count,partition_key,coverage_document,statistics
            ) VALUES (
              :id,:object_id,:descriptor,:byte_size,:row_count,
              CAST(:partition_key AS jsonb),CAST(:coverage AS jsonb),
              CAST(:statistics AS jsonb)
            ) ON CONFLICT (partition_descriptor_hash) DO NOTHING
            """
        ),
        {
            "id": prepared.payload_partition_id,
            "object_id": prepared.payload_object_id,
            "descriptor": prepared.partition_descriptor_hash,
            "byte_size": prepared.byte_size,
            "row_count": prepared.row_count,
            "partition_key": _json(prepared.partition_key),
            "coverage": _json(prepared.coverage_document),
            "statistics": _json(statistics),
        },
    )
    partition_row = connection.execute(
        text(
            """
            SELECT payload_partition_id,payload_object_id,byte_size,
                   row_or_item_count,partition_key,coverage_document,statistics
              FROM data.payload_partition
             WHERE partition_descriptor_hash=:descriptor
            """
        ),
        {"descriptor": prepared.partition_descriptor_hash},
    ).mappings().one()
    if (
        partition_row["payload_partition_id"] != prepared.payload_partition_id
        or partition_row["payload_object_id"] != prepared.payload_object_id
        or partition_row["byte_size"] != prepared.byte_size
        or partition_row["row_or_item_count"] != prepared.row_count
        or partition_row["partition_key"] != prepared.partition_key
        or partition_row["coverage_document"] != prepared.coverage_document
        or partition_row["statistics"] != statistics
    ):
        raise V022RuntimeDataError(
            "aggregation_output_partition_conflict",
            "Content-addressed output Partition identity is not reusable",
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
              :id,:artifact,:contract,:encoding,:producer,:port,
              :logical,:manifest_hash,1,:byte_size,:row_count,
              CAST(:coverage AS jsonb),'research','materialized'
            )
            """
        ),
        {
            "id": prepared.payload_manifest_id,
            "artifact": artifact_id,
            "contract": context.output_payload_contract_version_id,
            "encoding": context.physical_encoding_version_id,
            "producer": context.aggregation_run_artifact_id,
            "port": FINAL_SIGNAL_OUTPUT_PORT,
            "logical": prepared.logical_payload_fingerprint,
            "manifest_hash": prepared.manifest_hash,
            "byte_size": prepared.byte_size,
            "row_count": prepared.row_count,
            "coverage": _json(prepared.coverage_document),
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
              :id,:manifest,:status,:missing,0,:coverage,
              CAST(:document AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"bird-v022:payload-quality:{prepared.manifest_hash}",
            ),
            "manifest": prepared.payload_manifest_id,
            "status": "warning" if prepared.missing_count else "passed",
            "missing": prepared.missing_count,
            "coverage": Decimal(prepared.row_count - prepared.missing_count)
            / Decimal(prepared.row_count),
            "document": _json(
                {
                    "policy": "final_signal_numeric_v1",
                    "cross_section_minimum_assets": 2,
                    "row_count": prepared.row_count,
                }
            ),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO aggregation.aggregation_run_output (
              aggregation_run_id,payload_manifest_id
            ) VALUES (:run,:manifest)
            """
        ),
        {"run": context.aggregation_run_id, "manifest": prepared.payload_manifest_id},
    )
    updated = connection.execute(
        text(
            """
            UPDATE aggregation.aggregation_run
               SET status='completed',completed_at=:completed_at
             WHERE aggregation_run_id=:run AND status='running'
            """
        ),
        {"run": context.aggregation_run_id, "completed_at": datetime.now(UTC)},
    )
    if updated.rowcount != 1:
        raise V022RuntimeContractError(
            "aggregation_run_completion_race",
            "Aggregation Run was not running at atomic output publication",
        )
    connection.execute(
        text(
            """
            INSERT INTO aggregation.aggregation_run_cache_entry (
              execution_fingerprint,aggregation_run_id,cache_state,
              eligibility_checked_at
            ) VALUES (:fingerprint,:run,'eligible',:checked_at)
            """
        ),
        {
            "fingerprint": context.execution_fingerprint,
            "run": context.aggregation_run_id,
            "checked_at": datetime.now(UTC),
        },
    )


def _verify_published_aggregation_output(
    connection: Connection,
    *,
    context: _AggregationWorkContext,
    artifact_id: uuid.UUID,
    prepared: _PreparedAggregationOutput,
) -> None:
    row = connection.execute(
        text(
            """
            SELECT artifact.status AS artifact_status,run.status AS run_status,
                   output.payload_manifest_id,manifest.artifact_id,
                   manifest.manifest_hash,manifest.materialization_state,
                   manifest.producer_artifact_id,manifest.payload_contract_version_id,
                   work.status AS work_status,work.lease_owner,work.lease_expires_at
              FROM aggregation.aggregation_run run
              JOIN aggregation.aggregation_run_output output
                ON output.aggregation_run_id=run.aggregation_run_id
              JOIN data.payload_manifest manifest
                ON manifest.payload_manifest_id=output.payload_manifest_id
              JOIN lineage.artifact artifact
                ON artifact.artifact_id=manifest.artifact_id
              JOIN workspace.v022_graph_work_item work
                ON work.graph_work_item_id=:work
             WHERE run.aggregation_run_id=:run
            """
        ),
        {"run": context.aggregation_run_id, "work": context.graph_work_item_id},
    ).mappings().one()
    if (
        row["artifact_status"] != "published"
        or row["run_status"] != "completed"
        or row["payload_manifest_id"] != prepared.payload_manifest_id
        or row["artifact_id"] != artifact_id
        or row["manifest_hash"] != prepared.manifest_hash
        or row["materialization_state"] != "materialized"
        or row["producer_artifact_id"] != context.aggregation_run_artifact_id
        or row["payload_contract_version_id"]
        != context.output_payload_contract_version_id
        or row["work_status"] != "completed"
        or row["lease_owner"] is not None
        or row["lease_expires_at"] is not None
    ):
        raise V022RuntimeDataError(
            "aggregation_output_publication_incomplete",
            "Atomic Aggregation output publication failed verification",
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
