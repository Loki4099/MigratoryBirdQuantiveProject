from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from style_rotation.v022.aggregation_work_runtime import (
    AggregationOutputPoint,
    FrozenAggregationRecipeResolver,
    SignalManifestPoint,
    VerifiedAggregationInput,
    _partition_intersects_dates,
    _validate_manifest_partition_projection,
    encode_final_signal_numeric_parquet,
    execute_verified_aggregation,
    parse_final_signal_numeric_parquet,
    validate_asset_context_subset,
    validate_exact_asset_context,
    validate_exact_decision_date_coverage,
)
from style_rotation.v022.runtime_contract import (
    V022RuntimeContractError,
    V022RuntimeDataError,
)

REGISTRY = Path("v0.22/m5/model-migration-registry.v0.22.0.json")
DECISION_DATE = date(2026, 8, 7)
KNOWN_AT = datetime(2026, 8, 7, 21, tzinfo=UTC)
ASSETS = (
    (uuid.uuid5(uuid.NAMESPACE_URL, "aggregation-test:asset-a"), "asset_a"),
    (uuid.uuid5(uuid.NAMESPACE_URL, "aggregation-test:asset-b"), "asset_b"),
    (uuid.uuid5(uuid.NAMESPACE_URL, "aggregation-test:asset-c"), "asset_c"),
)


def test_manifest_projection_allows_pruning_to_intersecting_years() -> None:
    partitions = (
        {
            "ordinal": 0,
            "partition_byte_size": 100,
            "row_or_item_count": 500,
            "coverage_document": {
                "start": "2025-01-02",
                "end": "2025-12-31",
                "session_count": 251,
            },
        },
        {
            "ordinal": 1,
            "partition_byte_size": 120,
            "row_or_item_count": 510,
            "coverage_document": {
                "start": "2026-01-02",
                "end": "2026-12-31",
                "session_count": 252,
            },
        },
    )
    header = {
        "byte_size": 220,
        "row_or_item_count": 1010,
        "coverage_document": {
            "start": "2025-01-02",
            "end": "2026-12-31",
            "session_count": 503,
        },
    }

    _validate_manifest_partition_projection(
        cast(Any, header), cast(Any, partitions)
    )

    dates = frozenset({date(2026, 1, 30), date(2026, 12, 18)})
    assert not _partition_intersects_dates(cast(Any, partitions[0]), dates)
    assert _partition_intersects_dates(cast(Any, partitions[1]), dates)


def test_manifest_projection_rejects_gaps_in_ordinals_and_overlapping_coverage() -> None:
    header = {
        "byte_size": 2,
        "row_or_item_count": 2,
        "coverage_document": {
            "start": "2025-01-02",
            "end": "2026-01-02",
            "session_count": 2,
        },
    }
    base = (
        {
            "ordinal": 0,
            "partition_byte_size": 1,
            "row_or_item_count": 1,
            "coverage_document": {
                "start": "2025-01-02",
                "end": "2025-12-31",
                "session_count": 1,
            },
        },
        {
            "ordinal": 2,
            "partition_byte_size": 1,
            "row_or_item_count": 1,
            "coverage_document": {
                "start": "2026-01-02",
                "end": "2026-01-02",
                "session_count": 1,
            },
        },
    )
    with pytest.raises(V022RuntimeDataError) as ordinal_error:
        _validate_manifest_partition_projection(cast(Any, header), cast(Any, base))
    assert ordinal_error.value.reason_code == "aggregation_manifest_partition_ordinal_gap"

    overlapping = (base[0], {**base[1], "ordinal": 1, "coverage_document": {
        "start": "2025-12-31",
        "end": "2026-01-02",
        "session_count": 1,
    }})
    with pytest.raises(V022RuntimeDataError) as overlap_error:
        _validate_manifest_partition_projection(
            cast(Any, header), cast(Any, overlapping)
        )
    assert (
        overlap_error.value.reason_code
        == "aggregation_manifest_partition_coverage_overlap"
    )


def test_final_signal_parquet_round_trip_is_canonical_and_deterministic() -> None:
    points = tuple(
        AggregationOutputPoint(
            asset_id=asset_id,
            asset_key=asset_key,
            decision_date=DECISION_DATE,
            signal_value=Decimal(index).quantize(Decimal("1e-18")),
            known_at=KNOWN_AT,
            input_revision=f"revision-{index}",
            missing_reason=None,
        )
        for index, (asset_id, asset_key) in enumerate(sorted(ASSETS), 1)
    )

    first = encode_final_signal_numeric_parquet(points)
    second = encode_final_signal_numeric_parquet(points)
    restored = parse_final_signal_numeric_parquet(first, dict(ASSETS))

    assert first == second
    assert [item.asset_id for item in restored] == [item.asset_id for item in points]
    assert [item.signal_value for item in restored] == [
        item.signal_value for item in points
    ]
    assert all(item.known_at == KNOWN_AT for item in restored)


def test_final_signal_parser_filters_before_python_rows_and_requires_exact_dates() -> None:
    next_date = DECISION_DATE + timedelta(days=1)
    points = tuple(
        AggregationOutputPoint(
            asset_id=ASSETS[0][0],
            asset_key=ASSETS[0][1],
            decision_date=decision_date,
            signal_value=Decimal("0.250000000000000000"),
            known_at=KNOWN_AT + timedelta(days=offset),
            input_revision=f"revision-{offset}",
            missing_reason=None,
        )
        for offset, decision_date in enumerate((DECISION_DATE, next_date))
    )

    restored = parse_final_signal_numeric_parquet(
        encode_final_signal_numeric_parquet(points),
        {ASSETS[0][0]: ASSETS[0][1]},
        decision_dates=frozenset({next_date}),
    )

    assert tuple(item.decision_date for item in restored) == (next_date,)
    validate_exact_decision_date_coverage(restored, frozenset({next_date}))
    with pytest.raises(V022RuntimeDataError) as error:
        validate_exact_decision_date_coverage(
            restored, frozenset({DECISION_DATE, next_date})
        )
    assert error.value.reason_code == "aggregation_decision_date_coverage_mismatch"


def test_parser_rejects_naive_known_at_and_non_decimal_values() -> None:
    naive = _parquet(
        signal_type=pa.decimal128(38, 18),
        signal_values=[Decimal("0.100000000000000000")],
        known_at_type=pa.timestamp("us"),
        known_at_values=[KNOWN_AT.replace(tzinfo=None)],
    )
    with pytest.raises(V022RuntimeContractError) as naive_error:
        parse_final_signal_numeric_parquet(naive, {ASSETS[0][0]: ASSETS[0][1]})
    assert naive_error.value.reason_code == "aggregation_payload_schema_mismatch"

    floating = _parquet(
        signal_type=pa.float64(),
        signal_values=[0.1],
        known_at_type=pa.timestamp("us", tz="UTC"),
        known_at_values=[KNOWN_AT],
    )
    with pytest.raises(V022RuntimeContractError) as float_error:
        parse_final_signal_numeric_parquet(floating, {ASSETS[0][0]: ASSETS[0][1]})
    assert float_error.value.reason_code == "aggregation_payload_schema_mismatch"


def test_asset_context_requires_exact_frozen_membership_not_global_registry_subset() -> None:
    allowed = dict(ASSETS)
    validate_exact_asset_context(set(allowed), allowed)

    with pytest.raises(V022RuntimeDataError) as missing:
        validate_exact_asset_context({ASSETS[0][0], ASSETS[1][0]}, allowed)
    assert missing.value.reason_code == "aggregation_asset_context_mismatch"

    outsider = uuid.uuid5(uuid.NAMESPACE_URL, "aggregation-test:outsider")
    with pytest.raises(V022RuntimeDataError) as extra:
        validate_exact_asset_context(set(allowed) | {outsider}, allowed)
    assert extra.value.reason_code == "aggregation_asset_context_mismatch"


def test_trainable_asset_context_allows_only_nonempty_frozen_subset() -> None:
    allowed = dict(ASSETS)
    validate_asset_context_subset({ASSETS[0][0], ASSETS[1][0]}, allowed)

    outsider = uuid.uuid5(uuid.NAMESPACE_URL, "aggregation-test:outsider")
    for invalid in (set(), {ASSETS[0][0], outsider}):
        with pytest.raises(V022RuntimeDataError) as error:
            validate_asset_context_subset(invalid, allowed)
        assert error.value.reason_code == "aggregation_asset_context_mismatch"


def test_flat_equal_runtime_preserves_missingness_and_maximum_known_at() -> None:
    first = _input(
        "first_signal",
        0,
        (
            (Decimal("0.2"), KNOWN_AT, None),
            (Decimal("0.4"), KNOWN_AT, None),
            (None, KNOWN_AT, "source_missing"),
        ),
    )
    second_known_at = KNOWN_AT + timedelta(minutes=2)
    second = _input(
        "second_signal",
        1,
        (
            (Decimal("0.6"), second_known_at, None),
            (Decimal("0.8"), second_known_at, None),
            (Decimal("1.0"), second_known_at, None),
        ),
    )

    calculation = execute_verified_aggregation(
        family_key="flat_equal_weight_mean",
        parameter_preset_key="signal_equal_v1",
        inputs=(first, second),
    )

    by_asset = {item.asset_id: item for item in calculation.points}
    assert [by_asset[item[0]].signal_value for item in ASSETS] == [
        Decimal("0.400000000000000000"),
        Decimal("0.600000000000000000"),
        None,
    ]
    assert by_asset[ASSETS[-1][0]].missing_reason == "aggregation_input_missing"
    assert all(item.known_at == second_known_at for item in calculation.points)
    assert len({item.input_revision for item in calculation.points}) == 3


def test_runtime_rejects_a_nonidentical_ordered_input_panel() -> None:
    first = _input(
        "first_signal",
        0,
        tuple((Decimal("0.1"), KNOWN_AT, None) for _ in ASSETS),
    )
    second = _input(
        "second_signal",
        1,
        tuple((Decimal("0.2"), KNOWN_AT, None) for _ in ASSETS),
    )
    second = VerifiedAggregationInput(
        second.compiled_feature_occurrence_id,
        second.feature_variant_key,
        second.slot_key,
        second.ordinal,
        second.payload_manifest_id,
        second.manifest_artifact_id,
        second.manifest_hash,
        second.points[:-1],
    )

    with pytest.raises(V022RuntimeDataError) as error:
        execute_verified_aggregation(
            family_key="flat_equal_weight_mean",
            parameter_preset_key="signal_equal_v1",
            inputs=(first, second),
        )
    assert error.value.reason_code == "aggregation_input_panel_mismatch"


def test_hierarchical_runtime_uses_one_frozen_recipe_and_rejects_unknown_combinations() -> None:
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    record = document["records"][0]
    mapping = record["mapping"]
    resolver = FrozenAggregationRecipeResolver.from_path(REGISTRY)
    inputs = tuple(
        _input(
            variant_key,
            ordinal,
            tuple(
                (
                    Decimal(ordinal + asset_number + 1) / Decimal(100),
                    KNOWN_AT,
                    None,
                )
                for asset_number in range(len(ASSETS))
            ),
        )
        for ordinal, variant_key in enumerate(mapping["input_signal_variant_keys"])
    )

    calculation = execute_verified_aggregation(
        family_key=mapping["family_key"],
        parameter_preset_key=mapping["parameter_preset_key"],
        inputs=inputs,
        recipe_resolver=resolver,
    )
    assert len(calculation.points) == len(ASSETS)
    assert all(item.signal_value is not None for item in calculation.points)

    with pytest.raises(V022RuntimeContractError) as error:
        resolver.resolve(
            mapping["family_key"],
            mapping["parameter_preset_key"],
            (*mapping["input_signal_variant_keys"][:-1], "unpublished_random_signal"),
        )
    assert error.value.reason_code == "aggregation_frozen_recipe_not_unique"


def test_native_hierarchical_runtime_executes_the_compiled_two_level_weights() -> None:
    inputs = (
        _input("momentum_fast", 0, tuple((Decimal("0.2"), KNOWN_AT, None) for _ in ASSETS)),
        _input("momentum_slow", 1, tuple((Decimal("0.6"), KNOWN_AT, None) for _ in ASSETS)),
        _input("low_risk", 2, tuple((Decimal("1.0"), KNOWN_AT, None) for _ in ASSETS)),
    )
    recipe = {
        "contract_version": "v0.22.0",
        "recipe_kind": "native_hierarchical_equal_v2",
        "family_key": "hierarchical_weighted_mean",
        "parameter_preset_key": "active_dimension_equal_component_equal_v1",
        "taxonomy_fingerprint": "d" * 64,
        "input_scale": "centered_rank",
        "direction": "higher_is_better",
        "missing_policy": "fail_complete_case",
        "quantum": "1e-18",
        "rounding": "half_even",
        "ordered_inputs": ["momentum_fast", "momentum_slow", "low_risk"],
        "dimensions": [
            {
                "dimension_key": "momentum_trend",
                "dimension_weight": "0.500000000000000000",
                "components": [
                    {"feature_key": "momentum_fast", "component_weight": "0.500000000000000000"},
                    {"feature_key": "momentum_slow", "component_weight": "0.500000000000000000"},
                ],
            },
            {
                "dimension_key": "risk",
                "dimension_weight": "0.500000000000000000",
                "components": [
                    {"feature_key": "low_risk", "component_weight": "1.000000000000000000"},
                ],
            },
        ],
    }

    calculation = execute_verified_aggregation(
        family_key="hierarchical_weighted_mean",
        parameter_preset_key="active_dimension_equal_component_equal_v1",
        inputs=inputs,
        compiled_recipe=recipe,
    )

    assert {point.signal_value for point in calculation.points} == {
        Decimal("0.700000000000000000")
    }


def test_native_hierarchical_runtime_rejects_recipe_input_drift() -> None:
    inputs = (
        _input("momentum_fast", 0, tuple((Decimal("0.2"), KNOWN_AT, None) for _ in ASSETS)),
        _input("low_risk", 1, tuple((Decimal("1.0"), KNOWN_AT, None) for _ in ASSETS)),
    )
    with pytest.raises(V022RuntimeContractError) as error:
        execute_verified_aggregation(
            family_key="hierarchical_weighted_mean",
            parameter_preset_key="active_dimension_equal_component_equal_v1",
            inputs=inputs,
            compiled_recipe={
                "recipe_kind": "native_hierarchical_equal_v2",
                "family_key": "hierarchical_weighted_mean",
                "parameter_preset_key": "active_dimension_equal_component_equal_v1",
                "input_scale": "centered_rank",
                "direction": "higher_is_better",
                "missing_policy": "fail_complete_case",
                "ordered_inputs": ["different_signal", "low_risk"],
                "dimensions": [],
            },
        )
    assert error.value.reason_code == "aggregation_compiled_recipe_identity_invalid"


def _input(
    variant_key: str,
    ordinal: int,
    values: tuple[tuple[Decimal | None, datetime, str | None], ...],
) -> VerifiedAggregationInput:
    assert len(values) == len(ASSETS)
    manifest_id = uuid.uuid5(uuid.NAMESPACE_URL, f"manifest:{variant_key}")
    return VerifiedAggregationInput(
        compiled_feature_occurrence_id=uuid.uuid5(
            uuid.NAMESPACE_URL, f"occurrence:{variant_key}"
        ),
        feature_variant_key=variant_key,
        slot_key="signal",
        ordinal=ordinal,
        payload_manifest_id=manifest_id,
        manifest_artifact_id=uuid.uuid5(
            uuid.NAMESPACE_URL, f"manifest-artifact:{variant_key}"
        ),
        manifest_hash=uuid.uuid5(
            uuid.NAMESPACE_URL, f"manifest-hash:{variant_key}"
        ).hex
        * 2,
        points=tuple(
            SignalManifestPoint(
                asset_id=asset_id,
                asset_key=asset_key,
                decision_date=DECISION_DATE,
                signal_value=value,
                known_at=known_at,
                input_revision=f"{variant_key}:{asset_key}",
                missing_reason=missing_reason,
            )
            for (asset_id, asset_key), (value, known_at, missing_reason) in zip(
                ASSETS, values, strict=True
            )
        ),
    )


def _parquet(
    *,
    signal_type: pa.DataType,
    signal_values: list[object],
    known_at_type: pa.DataType,
    known_at_values: list[datetime],
) -> bytes:
    table = pa.Table.from_arrays(
        [
            pa.array([DECISION_DATE], type=pa.date32()),
            pa.array([str(ASSETS[0][0])], type=pa.string()),
            pa.array(signal_values, type=signal_type),
            pa.array(known_at_values, type=known_at_type),
            pa.array(["revision"], type=pa.string()),
            pa.array([None], type=pa.string()),
        ],
        names=[
            "decision_date",
            "asset_id",
            "signal_value",
            "known_at",
            "input_revision",
            "missing_reason",
        ],
    )
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd", use_dictionary=False)
    return buffer.getvalue()
