from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.aggregation_runtime import (
    flat_equal_weight_mean,
    single_signal_identity,
)
from style_rotation.v022.dag import execution_fingerprint
from style_rotation.v022.graph import (
    AggregationFeatureTaxonomyEntrySpec,
    AggregationFeatureTaxonomySpec,
    AggregationSelection,
    AggregationSpec,
    AssetContextSnapshot,
    DefenseAssetContextSpec,
    DefenseSpec,
    DraftIntent,
    FeatureSelection,
    FeatureSpec,
    GraphCatalog,
    NodeInputSpec,
    NodeSpec,
    StrategyParameterPresetSpec,
    StrategySpec,
    compile_intent,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _catalog() -> GraphCatalog:
    features = {
        "close_raw": FeatureSpec("close_raw", str(uuid.uuid4()), 0, "numeric"),
        "return_1d": FeatureSpec(
            "return_1d", str(uuid.uuid4()), 1, "numeric", "return_node"
        ),
        "smoothed_return": FeatureSpec(
            "smoothed_return", str(uuid.uuid4()), 2, "numeric", "smooth_node"
        ),
        "ranked_return": FeatureSpec(
            "ranked_return", str(uuid.uuid4()), 3, "numeric", "rank_node"
        ),
    }
    nodes = {
        "return_node": NodeSpec(
            "return_node",
            str(uuid.uuid4()),
            1,
            ("return_1d",),
            (NodeInputSpec("close", "close_raw", 0),),
        ),
        "smooth_node": NodeSpec(
            "smooth_node",
            str(uuid.uuid4()),
            2,
            ("smoothed_return",),
            (NodeInputSpec("returns", "return_1d", 0),),
        ),
        "rank_node": NodeSpec(
            "rank_node",
            str(uuid.uuid4()),
            3,
            ("ranked_return",),
            (NodeInputSpec("signal", "smoothed_return", 0),),
        ),
    }
    aggregations = {
        "identity": AggregationSpec(
            "identity", str(uuid.uuid4()), "deterministic", "numeric", 1, 1
        ),
        "supervised": AggregationSpec(
            "supervised",
            str(uuid.uuid4()),
            "supervised",
            "numeric",
            1,
            10,
            ("default",),
            ("future_rank",),
            ("walk_forward",),
        ),
    }
    return GraphCatalog(
        features,
        nodes,
        aggregations,
        {"topk": StrategySpec(str(uuid.uuid4()), ("daily",))},
        {"ma200": str(uuid.uuid4())},
    )


def _intent(**changes: object) -> DraftIntent:
    payload: dict[str, object] = {
        "catalog_release_fingerprint": HASH_A,
        "asset_context_fingerprint": HASH_B,
        "resolved_data_binding_fingerprint": HASH_C,
        "frequency": "daily",
        "aggregation_inputs": ("ranked_return",),
        "explicit_features": (
            FeatureSelection(feature_key="close_raw", visible_stage=0),
            FeatureSelection(feature_key="ranked_return", visible_stage=3),
        ),
        "aggregations": (AggregationSelection(family_key="identity"),),
        "strategy_keys": ("topk",),
        "defense_keys": ("none",),
    }
    payload.update(changes)
    return DraftIntent.model_validate(payload)


def _asset_context(member_count: int = 4) -> AssetContextSnapshot:
    return AssetContextSnapshot.model_validate(
        {
            "contract_version": "v0.22.0",
            "selection_kind": "fixed_asset_set",
            "asset_context_key": "test_asset_context",
            "asset_registry_release_id": str(uuid.uuid4()),
            "asset_registry_artifact_id": str(uuid.uuid4()),
            "asset_registry_catalog_version": "0.21.1",
            "asset_set_definition_id": str(uuid.uuid4()),
            "members": [
                {
                    "ordinal": ordinal,
                    "security_id": str(uuid.uuid4()),
                    "security_key": f"asset_{ordinal}",
                    "instrument_type": "Equity ETF",
                }
                for ordinal in range(member_count)
            ],
        }
    )


def test_three_processing_nodes_expand_to_a_stable_human_defined_graph() -> None:
    catalog = _catalog()
    first = compile_intent(_intent(), catalog)
    reordered = compile_intent(
        _intent(explicit_features=tuple(reversed(_intent().explicit_features))), catalog
    )

    assert first.graph_fingerprint == reordered.graph_fingerprint
    assert [item.node_key for item in first.nodes] == [
        "return_node",
        "smooth_node",
        "rank_node",
    ]
    assert [(item.feature_key, item.stage_no) for item in first.occurrences] == [
        ("close_raw", 0),
        ("return_1d", 1),
        ("smoothed_return", 2),
        ("ranked_return", 3),
    ]
    assert len(first.aggregation_instances) == 1
    assert first.aggregation_instances[0].target_key is None
    assert first.aggregation_instances[0].training_preset_key is None
    normalized_branch = first.normalized_graph["branches"][0]
    assert "strategy_parameter_preset_key" not in normalized_branch
    assert "strategy_parameter_preset_version_id" not in normalized_branch
    assert "strategy_parameter_fingerprint" not in normalized_branch
    assert "resolved_strategy_parameters" not in normalized_branch
    assert set(normalized_branch) == {
        "branch_key",
        "aggregation_instance_key",
        "strategy_key",
        "strategy_version_id",
        "defense_key",
        "defense_version_id",
    }


def test_legacy_defense_normalized_graph_fingerprint_is_frozen() -> None:
    def fixed_uuid(ordinal: int) -> str:
        return f"00000000-0000-4000-8000-{ordinal:012d}"

    catalog = GraphCatalog(
        {"close_raw": FeatureSpec("close_raw", fixed_uuid(1), 0, "numeric")},
        {},
        {
            "identity": AggregationSpec(
                "identity", fixed_uuid(2), "deterministic", "numeric", 1, 1
            )
        },
        {"topk": StrategySpec(fixed_uuid(3), ("weekly",))},
        {"fixed20": fixed_uuid(4)},
    )
    intent = DraftIntent(
        catalog_release_fingerprint=HASH_A,
        asset_context_fingerprint=HASH_B,
        resolved_data_binding_fingerprint=HASH_C,
        frequency="weekly",
        aggregation_inputs=("close_raw",),
        explicit_features=(
            FeatureSelection(feature_key="close_raw", visible_stage=3),
        ),
        aggregations=(AggregationSelection(family_key="identity"),),
        strategy_keys=("topk",),
        defense_keys=("fixed20",),
    )

    result = compile_intent(intent, catalog)

    assert result.normalized_graph["branches"] == [
        {
            "branch_key": "identity__topk__fixed20",
            "aggregation_instance_key": "identity",
            "strategy_key": "topk",
            "strategy_version_id": fixed_uuid(3),
            "defense_key": "fixed20",
            "defense_version_id": fixed_uuid(4),
        }
    ]
    assert result.graph_fingerprint == (
        "c6420ed24379115376b4562f3f4fcdf103853b8ca411a298e5984207300ce885"
    )


def test_raw_feature_is_projected_through_every_layer_without_skipping() -> None:
    catalog = _catalog()
    catalog.aggregations["identity"] = AggregationSpec(
        "identity", str(uuid.uuid4()), "deterministic", "numeric", 1, 1
    )
    result = compile_intent(
        _intent(
            aggregation_inputs=("close_raw",),
            explicit_features=(FeatureSelection(feature_key="close_raw", visible_stage=3),),
        ),
        catalog,
    )
    assert [(item.stage_no, item.production_kind) for item in result.occurrences] == [
        (0, "raw_input"),
        (1, "layer_projection"),
        (2, "layer_projection"),
        (3, "layer_projection"),
    ]


def test_compiler_rejects_projection_not_authorized_by_published_catalog() -> None:
    catalog = _catalog()
    current = catalog.features["close_raw"]
    catalog.features["close_raw"] = FeatureSpec(
        current.feature_key,
        current.version_id,
        current.origin_stage,
        current.payload_contract_key,
        current.producer_node_key,
        current.output_port_key,
        1,
    )

    with pytest.raises(
        ValueError,
        match="close_raw is not published for projection to stage 3",
    ):
        compile_intent(
            _intent(
                aggregation_inputs=("close_raw",),
                explicit_features=(
                    FeatureSelection(feature_key="close_raw", visible_stage=3),
                ),
            ),
            catalog,
        )


def test_deterministic_aggregation_rejects_target_axis() -> None:
    with pytest.raises(ValueError, match="cannot expand Target"):
        compile_intent(
            _intent(
                aggregations=(
                    AggregationSelection(family_key="identity", target_keys=("future_rank",)),
                )
            ),
            _catalog(),
        )


def test_supervised_axes_expand_only_when_declared() -> None:
    result = compile_intent(
        _intent(
            aggregations=(
                AggregationSelection(
                    family_key="supervised",
                    parameter_preset_keys=("default",),
                    target_keys=("future_rank",),
                    training_preset_keys=("walk_forward",),
                ),
            )
        ),
        _catalog(),
    )
    assert len(result.aggregation_instances) == 1
    instance = result.aggregation_instances[0]
    assert instance.target_key == "future_rank"
    assert instance.feature_schema_document == {
        "version_number": 1,
        "ordered_feature_keys": ["ranked_return"],
        "missing_policy": "complete_case_fail_closed",
        "known_at_policy": "feature_known_at_not_after_decision_cutoff",
    }
    assert instance.feature_schema_fingerprint == sha256_hexdigest(
        instance.feature_schema_document
    )
    normalized = result.normalized_graph["aggregation_instances"][0]
    assert normalized["feature_schema_fingerprint"] == (
        instance.feature_schema_fingerprint
    )


def test_supervised_multi_member_axes_compile_one_ensemble_branch() -> None:
    catalog = _catalog()
    current = catalog.aggregations["supervised"]
    catalog.aggregations["supervised"] = AggregationSpec(
        current.family_key,
        current.version_id,
        current.execution_mode,
        current.input_payload_contract_key,
        current.minimum_inputs,
        current.maximum_inputs,
        current.parameter_preset_keys,
        ("h5", "h21"),
        ("alpha1", "alpha10"),
    )
    result = compile_intent(
        _intent(
            aggregations=(
                AggregationSelection(
                    family_key="supervised",
                    parameter_preset_keys=("default",),
                    target_keys=("h21", "h5"),
                    training_preset_keys=("alpha10", "alpha1"),
                ),
            )
        ),
        catalog,
    )

    assert len(result.aggregation_instances) == 1
    assert len(result.branches) == 1
    instance = result.aggregation_instances[0]
    assert instance.target_key is None
    assert instance.training_preset_key is None
    assert [
        (item.target_key, item.training_preset_key)
        for item in instance.ensemble_members
    ] == [
        ("h21", "alpha1"),
        ("h21", "alpha10"),
        ("h5", "alpha1"),
        ("h5", "alpha10"),
    ]
    assert instance.ensemble_spec_fingerprint == sha256_hexdigest(
        instance.ensemble_spec_document
    )
    normalized = result.normalized_graph["aggregation_instances"][0]
    assert normalized["ensemble_spec_fingerprint"] == (
        instance.ensemble_spec_fingerprint
    )


def test_native_hierarchical_recipe_freezes_equal_dimensions_and_components() -> None:
    features = {
        "momentum_fast": FeatureSpec(
            "momentum_fast", str(uuid.uuid4()), 0, "numeric", None, None, 3,
            "return_continuation", "centered_rank", "higher_is_better",
        ),
        "momentum_slow": FeatureSpec(
            "momentum_slow", str(uuid.uuid4()), 0, "numeric", None, None, 3,
            "lagged_return_continuation", "centered_rank", "higher_is_better",
        ),
        "low_risk": FeatureSpec(
            "low_risk", str(uuid.uuid4()), 0, "numeric", None, None, 3,
            "low_volatility", "centered_rank", "higher_is_better",
        ),
    }
    taxonomy = AggregationFeatureTaxonomySpec(
        version_id=str(uuid.uuid4()),
        artifact_id=str(uuid.uuid4()),
        taxonomy_fingerprint="d" * 64,
        entries={
            "return_continuation": AggregationFeatureTaxonomyEntrySpec(
                "return_continuation", "momentum_trend", ("centered_rank",),
                ("higher_is_better",), True,
            ),
            "lagged_return_continuation": AggregationFeatureTaxonomyEntrySpec(
                "lagged_return_continuation", "momentum_trend", ("centered_rank",),
                ("higher_is_better",), True,
            ),
            "low_volatility": AggregationFeatureTaxonomyEntrySpec(
                "low_volatility", "risk", ("centered_rank",),
                ("higher_is_better",), True,
            ),
        },
    )
    catalog = GraphCatalog(
        features,
        {},
        {
            "hierarchical_weighted_mean": AggregationSpec(
                "hierarchical_weighted_mean", str(uuid.uuid4()), "deterministic",
                "numeric", 2, 128,
                ("active_dimension_equal_component_equal_v1",),
            )
        },
        {"topk": StrategySpec(str(uuid.uuid4()), ("daily",))},
        {"none": str(uuid.uuid4())},
        taxonomy,
    )
    intent = _intent(
        aggregation_inputs=("momentum_fast", "momentum_slow", "low_risk"),
        explicit_features=tuple(
            FeatureSelection(feature_key=key, visible_stage=3) for key in features
        ),
        aggregations=(
            AggregationSelection(
                family_key="hierarchical_weighted_mean",
                parameter_preset_keys=("active_dimension_equal_component_equal_v1",),
            ),
        ),
    )

    result = compile_intent(intent, catalog)

    instance = result.aggregation_instances[0]
    assert instance.recipe_fingerprint == sha256_hexdigest(instance.recipe_document)
    assert instance.recipe_document == {
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


def test_native_hierarchical_recipe_rejects_uncalibrated_event_scores() -> None:
    catalog = _catalog()
    catalog.features["ranked_return"] = FeatureSpec(
        "ranked_return", catalog.features["ranked_return"].version_id, 3, "numeric",
        "rank_node", None, 3, "golden_cross_event", "event_score",
        "higher_is_bullish",
    )
    catalog.aggregations["hierarchical_weighted_mean"] = AggregationSpec(
        "hierarchical_weighted_mean", str(uuid.uuid4()), "deterministic", "numeric",
        1, 128, ("active_dimension_equal_component_equal_v1",),
    )
    catalog = replace(
        catalog,
        feature_taxonomy=AggregationFeatureTaxonomySpec(
            str(uuid.uuid4()), str(uuid.uuid4()), "d" * 64,
            {
                "golden_cross_event": AggregationFeatureTaxonomyEntrySpec(
                    "golden_cross_event", "technical_event", ("event_score",),
                    ("higher_is_bullish",), False,
                )
            },
        ),
    )

    with pytest.raises(ValueError, match="aggregation_native_calibration_required"):
        compile_intent(
            _intent(
                aggregation_inputs=("ranked_return", "smoothed_return"),
                explicit_features=(
                    FeatureSelection(feature_key="ranked_return", visible_stage=3),
                    FeatureSelection(feature_key="smoothed_return", visible_stage=3),
                ),
                aggregations=(AggregationSelection(
                    family_key="hierarchical_weighted_mean",
                    parameter_preset_keys=("active_dimension_equal_component_equal_v1",),
                ),),
            ),
            catalog,
        )


def test_strategy_parameter_presets_expand_exact_branches_and_change_identity() -> None:
    catalog = _catalog()
    strategy_version_id = str(uuid.uuid4())
    k1 = StrategyParameterPresetSpec(
        "k1", str(uuid.uuid4()), "1" * 64,
        {"target_k": 1, "selection_buffer": "none", "sector_cap": "none"},
    )
    k2 = StrategyParameterPresetSpec(
        "k2", str(uuid.uuid4()), "2" * 64,
        {"target_k": 2, "selection_buffer": "none", "sector_cap": "none"},
    )
    catalog.strategy_versions["topk"] = StrategySpec(
        strategy_version_id, ("daily",), {"k1": k1, "k2": k2}
    )

    context = _asset_context()
    context_fingerprint = sha256_hexdigest(context.model_dump(mode="json"))
    first = compile_intent(
        _intent(
            asset_context_fingerprint=context_fingerprint,
            strategy_parameter_preset_keys=(("topk", ("k1",)),),
        ),
        catalog,
        asset_context_snapshot=context,
    )
    second = compile_intent(
        _intent(
            asset_context_fingerprint=context_fingerprint,
            strategy_parameter_preset_keys=(("topk", ("k2",)),),
        ),
        catalog,
        asset_context_snapshot=context,
    )

    assert first.graph_fingerprint != second.graph_fingerprint
    assert first.branches[0].strategy_parameter_preset_key == "k1"
    assert first.branches[0].strategy_parameter_preset_version_id == k1.version_id
    assert first.branches[0].resolved_strategy_parameters["target_k"] == 1
    assert "__k1__" in first.branches[0].branch_key
    with pytest.raises(ValueError, match="requires a parameter preset"):
        compile_intent(_intent(), catalog)
    with pytest.raises(ValueError, match="asset_context_required"):
        compile_intent(
            _intent(strategy_parameter_preset_keys=(("topk", ("k1",)),)),
            catalog,
        )


def test_composed_defense_freezes_policy_identity_and_exact_asset_context() -> None:
    catalog = _catalog()
    context = _asset_context()
    context_fingerprint = sha256_hexdigest(context.model_dump(mode="json"))
    package_id = str(uuid.uuid4())
    timing_id = str(uuid.uuid4())
    allocation_id = str(uuid.uuid4())
    catalog.defense_versions["ma200"] = DefenseSpec(
        package_id,
        "4" * 64,
        timing_id,
        "5" * 64,
        allocation_id,
        "6" * 64,
        ("daily",),
        (
            DefenseAssetContextSpec(
                context.asset_context_key,
                str(context.asset_registry_release_id),
                str(context.asset_registry_artifact_id),
                str(context.asset_set_definition_id),
            ),
        ),
    )

    result = compile_intent(
        _intent(
            asset_context_fingerprint=context_fingerprint,
            defense_keys=("ma200",),
        ),
        catalog,
        asset_context_snapshot=context,
    )

    branch = result.normalized_graph["branches"][0]
    assert branch["defense_version_id"] == package_id
    assert branch["defense_version_fingerprint"] == "4" * 64
    assert branch["defense_timing_policy_version_id"] == timing_id
    assert branch["defense_timing_policy_version_fingerprint"] == "5" * 64
    assert branch["defense_allocation_policy_version_id"] == allocation_id
    assert branch["defense_allocation_policy_version_fingerprint"] == "6" * 64

    unsupported_context = context.model_copy(
        update={"asset_set_definition_id": uuid.uuid4()}
    )
    with pytest.raises(ValueError, match="rejects the exact frozen Asset Context"):
        compile_intent(
            _intent(
                asset_context_fingerprint=sha256_hexdigest(
                    unsupported_context.model_dump(mode="json")
                ),
                defense_keys=("ma200",),
            ),
            catalog,
            asset_context_snapshot=unsupported_context,
        )
    with pytest.raises(ValueError, match="does not support frequency weekly"):
        compile_intent(
            _intent(
                asset_context_fingerprint=context_fingerprint,
                frequency="weekly",
                defense_keys=("ma200",),
            ),
            catalog,
            asset_context_snapshot=context,
        )


def test_minimal_deterministic_aggregation_and_execution_identity() -> None:
    assert single_signal_identity((Decimal("0.1"),)) == Decimal("0.1")
    assert flat_equal_weight_mean((Decimal("0.1"), Decimal("0.2"))) == Decimal(
        "0.150000000000000000"
    )
    assert flat_equal_weight_mean((Decimal("0.1"), None)) is None

    component = uuid.uuid4()
    manifest_a = uuid.uuid4()
    manifest_b = uuid.uuid4()
    common = {
        "component_version_id": component,
        "resolved_parameters": {},
        "resource_bindings": (),
        "requested_range": {"start": "2024-01-01"},
        "executor_version": "m2-v1",
        "environment_fingerprint": HASH_A,
        "determinism_policy": "deterministic",
        "cache_policy": "content_addressed",
        "payload_reader_contract": "parquet-v1",
    }
    first = execution_fingerprint(
        **common,
        ordered_input_manifests=(
            ("input", 0, manifest_a, HASH_B),
            ("input", 1, manifest_b, HASH_C),
        ),
    )
    reversed_inputs = execution_fingerprint(
        **common,
        ordered_input_manifests=(
            ("input", 0, manifest_b, HASH_C),
            ("input", 1, manifest_a, HASH_B),
        ),
    )
    assert first != reversed_inputs
