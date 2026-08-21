from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.v022.runtime_contract import V022RuntimeContractError
from style_rotation.v022.suite_runtime_planner import (
    RUNTIME_CATALOG_VERSION,
    ExactSourceMaterialization,
    RuntimeAggregation,
    RuntimeAggregationEnsembleMember,
    RuntimeAggregationInput,
    RuntimeBranch,
    RuntimeCell,
    RuntimeDefenseBinding,
    RuntimeOccurrence,
    RuntimePayloadPins,
    SuiteRuntimePlanRequest,
    VerifiedSuiteRuntimeFacts,
    _exact_runtime_range,
    _runtime_branch_risk_context_valid,
    _validate_manifest_structure_row,
    build_suite_runtime_preflight,
    load_verified_facts,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def test_runtime_catalog_version_uses_persisted_semantic_identity() -> None:
    assert semantic_version_number("0.22.13") == RUNTIME_CATALOG_VERSION
    assert RUNTIME_CATALOG_VERSION == 22014


def test_simple_defense_branch_accepts_deliberately_context_free_snapshot() -> None:
    row = {
        "snapshot_status": "published",
        "preset_status": "published",
        "uses_composed_defense": False,
        "compiled_execution_data_context_id": None,
        "execution_data_context_artifact_id": None,
        "execution_data_context_fingerprint": None,
    }
    assert _runtime_branch_risk_context_valid(row, {})


def test_composed_defense_branch_requires_exact_suite_risk_context() -> None:
    context_id = _id("risk-context")
    artifact_id = _id("risk-context-artifact")
    row = {
        "snapshot_status": "published",
        "preset_status": "published",
        "uses_composed_defense": True,
        "compiled_execution_data_context_id": context_id,
        "execution_data_context_artifact_id": artifact_id,
        "execution_data_context_fingerprint": HASH_A,
    }
    header = {
        "compiled_execution_data_context_id": context_id,
        "execution_data_context_artifact_id": artifact_id,
        "execution_data_context_fingerprint": HASH_A,
    }
    assert _runtime_branch_risk_context_valid(row, header)
    assert not _runtime_branch_risk_context_valid(
        {**row, "execution_data_context_fingerprint": HASH_B}, header
    )


def _id(key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"v022-suite-runtime-planner-test:{key}")


def _request(*, suite_id: uuid.UUID | None = None) -> SuiteRuntimePlanRequest:
    return SuiteRuntimePlanRequest(
        research_suite_id=suite_id or _id("suite"),
        requested_by="researcher",
        requested_range={"start": "2020-01-01", "end": "2025-12-31"},
        executor_version="v022-runtime-1",
        environment_fingerprint=HASH_A,
    )


def _facts() -> VerifiedSuiteRuntimeFacts:
    occurrence = RuntimeOccurrence(
        compiled_feature_occurrence_id=_id("node-output-occurrence"),
        production_kind="node_output",
        payload_contract_version_id=_id("numeric-contract"),
        feature_variant_key="ranked_signal",
        compiled_graph_node_id=_id("node"),
        output_port_key="ranked_signal",
    )
    projection = RuntimeOccurrence(
        compiled_feature_occurrence_id=_id("stage-3-projection"),
        production_kind="layer_projection",
        payload_contract_version_id=_id("numeric-contract"),
        feature_variant_key="ranked_signal",
        source_occurrence_id=occurrence.compiled_feature_occurrence_id,
    )
    materialization = ExactSourceMaterialization(
        terminal_occurrence_id=occurrence.compiled_feature_occurrence_id,
        source_kind="node_output",
        payload_manifest_id=_id("node-manifest"),
        payload_manifest_artifact_id=_id("node-manifest-artifact"),
        manifest_hash=HASH_B,
        logical_payload_fingerprint=HASH_C,
        payload_contract_version_id=_id("numeric-contract"),
        physical_encoding_version_id=_id("parquet-encoding"),
        producer_artifact_id=_id("node-run-artifact"),
        producer_output_port_key="ranked_signal",
        manifest_artifact_status="published",
        materialization_state="materialized",
        quality_status="passed",
        coverage_start=date(2019, 1, 1),
        coverage_end=date(2025, 12, 31),
        compiled_graph_node_id=_id("node"),
        node_run_id=_id("node-run"),
        graph_work_item_id=_id("node-work"),
        node_execution_fingerprint=HASH_D,
        node_run_status="completed",
        graph_work_status="completed",
    )
    aggregation = RuntimeAggregation(
        compiled_aggregation_instance_id=_id("aggregation-instance"),
        aggregation_version_id=_id("aggregation-version"),
        aggregation_version_artifact_id=_id("aggregation-version-artifact"),
        parameter_preset_version_id=_id("aggregation-preset"),
        instance_fingerprint=HASH_B,
        resolved_parameters={"weighting": "equal"},
        output_payload_contract_version_id=_id("aggregation-contract"),
        inputs=(
            RuntimeAggregationInput(
                slot_key="signals",
                ordinal=0,
                compiled_feature_occurrence_id=projection.compiled_feature_occurrence_id,
            ),
        ),
    )
    branch = RuntimeBranch(
        research_suite_branch_id=_id("suite-branch"),
        compiled_strategy_branch_id=_id("compiled-branch"),
        configuration_snapshot_id=_id("configuration-snapshot"),
        compiled_aggregation_instance_id=aggregation.compiled_aggregation_instance_id,
        strategy_version_id=_id("strategy-version"),
        strategy_parameter_preset_version_id=_id("strategy-preset"),
        branch_fingerprint=HASH_C,
        configuration_fingerprint=HASH_D,
        defense=RuntimeDefenseBinding(
            defense_version_id=_id("defense-version"),
            timing_policy_version_id=_id("timing-policy"),
            allocation_policy_version_id=_id("allocation-policy"),
            compiled_defense_execution_context_id=_id("defense-context"),
            defense_context_fingerprint=HASH_A,
        ),
    )
    cell = RuntimeCell(
        research_cell_id=_id("cell"),
        research_suite_branch_id=branch.research_suite_branch_id,
        evaluation_context_ordinal=0,
        evaluation_policy_context_fingerprint=HASH_A,
        portfolio_evaluation_data_context_id=_id("evaluation-data-context"),
        portfolio_evaluation_data_context_artifact_id=_id(
            "evaluation-data-context-artifact"
        ),
        evaluation_data_context_fingerprint=HASH_B,
        evaluation_data_coverage_start=date(2019, 1, 1),
        evaluation_data_coverage_end=date(2025, 12, 31),
    )
    return VerifiedSuiteRuntimeFacts(
        research_suite_id=_id("suite"),
        research_suite_artifact_id=_id("suite-artifact"),
        suite_fingerprint=HASH_B,
        compiled_research_graph_id=_id("graph"),
        compiled_graph_artifact_id=_id("graph-artifact"),
        graph_fingerprint=HASH_C,
        catalog_release_id=_id("catalog-release"),
        catalog_release_artifact_id=_id("catalog-release-artifact"),
        compiled_execution_data_context_id=_id("risk-context"),
        execution_data_context_artifact_id=_id("risk-context-artifact"),
        execution_data_context_fingerprint=HASH_D,
        evaluation_cohort_version_id=None,
        evaluation_cohort_artifact_id=None,
        evaluation_cohort_fingerprint=None,
        evaluation_cohort_research_tier=None,
        evaluation_cohort_frequency=None,
        evaluation_cohort_warmup_range=None,
        expected_branch_count=1,
        expected_cell_count=1,
        effective_range={"start": "2020-01-01", "end": "2025-12-31"},
        payload_pins=RuntimePayloadPins(
            strategy_target_payload_contract_version_id=_id("strategy-contract"),
            defense_decision_payload_contract_version_id=_id("defense-contract"),
            sleeve_merge_payload_contract_version_id=_id("merge-contract"),
            portfolio_cell_payload_contract_version_id=_id("cell-contract"),
            physical_encoding_version_id=_id("parquet-encoding"),
        ),
        occurrences=(projection, occurrence),
        source_materializations=(materialization,),
        aggregations=(aggregation,),
        branches=(branch,),
        cells=(cell,),
    )


def test_preflight_builds_real_source_to_portfolio_dag_without_writes() -> None:
    writes: list[object] = []
    result = build_suite_runtime_preflight(_request(), _facts())

    assert writes == []
    assert [item.occurrence_kind for item in result.work] == [
        "node",
        "aggregation",
        "strategy_target",
        "defense_decision",
        "sleeve_merge",
        "portfolio_cell",
    ]
    by_kind = {item.occurrence_kind: item for item in result.work}
    assert by_kind["node"].required_existing_work_item_id == _id("node-work")
    assert by_kind["aggregation"].required_upstream_keys == (
        by_kind["node"].occurrence_key,
    )
    assert by_kind["strategy_target"].required_upstream_keys == (
        by_kind["aggregation"].occurrence_key,
    )
    assert by_kind["defense_decision"].required_upstream_keys == (
        by_kind["strategy_target"].occurrence_key,
    )
    assert by_kind["sleeve_merge"].required_upstream_keys == (
        by_kind["strategy_target"].occurrence_key,
        by_kind["defense_decision"].occurrence_key,
    )
    assert by_kind["portfolio_cell"].required_upstream_keys == (
        by_kind["sleeve_merge"].occurrence_key,
    )
    assert result.typed_work_count == 4
    assert result.graph_consumer_count == 6


def test_supervised_aggregation_work_pins_exact_target_and_training_axes() -> None:
    facts = _facts()
    target_id = _id("forward-rank-h5")
    training_id = _id("expanding-daily-ols")
    supervised = replace(
        facts.aggregations[0],
        parameter_preset_version_id=None,
        execution_mode="supervised",
        implementation_key=(
            "style_rotation.v022.aggregation.ols_cross_sectional_regression_v1"
        ),
        target_version_id=target_id,
        target_version_artifact_id=_id("forward-rank-h5-artifact"),
        target_semantics={"horizon_sessions": 5},
        training_preset_version_id=training_id,
        training_preset_version_artifact_id=_id(
            "expanding-daily-ols-artifact"
        ),
        resolved_parameters={
            "adapter_key": "ols_cross_sectional_regression",
            "random_split": False,
        },
    )

    result = build_suite_runtime_preflight(
        _request(), replace(facts, aggregations=(supervised,))
    )

    aggregation = next(
        item for item in result.work if item.occurrence_kind == "aggregation"
    )
    assert aggregation.semantic_identity["execution_mode"] == "supervised"
    assert aggregation.semantic_identity["target_version_id"] == str(target_id)
    assert aggregation.semantic_identity["target_semantics"] == {
        "horizon_sessions": 5
    }
    assert aggregation.semantic_identity["training_preset_version_id"] == str(
        training_id
    )
    assert aggregation.required_upstream_keys == ("node:" + str(_id("node")),)


def test_supervised_ensemble_work_pins_ordered_internal_members() -> None:
    facts = _facts()
    members = tuple(
        RuntimeAggregationEnsembleMember(
            ordinal=ordinal,
            target_group_ordinal=ordinal,
            member_ordinal_within_target=0,
            target_version_id=_id(f"target-{ordinal}"),
            target_version_artifact_id=_id(f"target-artifact-{ordinal}"),
            target_key=f"forward_rank_h{5 + ordinal * 5}",
            target_semantics={"horizon_sessions": 5 + ordinal * 5},
            training_preset_version_id=_id(f"training-{ordinal}"),
            training_preset_version_artifact_id=_id(
                f"training-artifact-{ordinal}"
            ),
            training_preset_key=f"ridge-{ordinal}",
            training_preset_semantics={"alpha": str(ordinal + 1)},
        )
        for ordinal in range(2)
    )
    document = {
        "member_count": 2,
        "target_group_count": 2,
        "combination_policy": "equal_within_target_equal_across_targets_v1",
    }
    supervised = replace(
        facts.aggregations[0],
        parameter_preset_version_id=None,
        execution_mode="supervised",
        implementation_key=(
            "style_rotation.v022.aggregation.ridge_cross_sectional_regression_v1"
        ),
        resolved_parameters=document,
        ensemble_spec_id=_id("ensemble-spec"),
        ensemble_spec_artifact_id=_id("ensemble-spec-artifact"),
        ensemble_fingerprint=HASH_A,
        ensemble_document=document,
        ensemble_members=members,
    )

    result = build_suite_runtime_preflight(
        _request(), replace(facts, aggregations=(supervised,))
    )

    aggregation = next(
        item for item in result.work if item.occurrence_kind == "aggregation"
    )
    assert aggregation.semantic_identity["target_version_id"] is None
    assert aggregation.semantic_identity["training_preset_version_id"] is None
    assert aggregation.semantic_identity["ensemble_spec_id"] == str(
        _id("ensemble-spec")
    )
    assert [
        item["ordinal"]
        for item in aggregation.semantic_identity["ensemble_members"]
    ] == [0, 1]

    changed = replace(
        supervised,
        ensemble_members=(
            replace(members[0], target_semantics={"horizon_sessions": 21}),
            members[1],
        ),
    )
    changed_result = build_suite_runtime_preflight(
        _request(), replace(facts, aggregations=(changed,))
    )
    changed_work = next(
        item
        for item in changed_result.work
        if item.occurrence_kind == "aggregation"
    )
    assert changed_work.execution_fingerprint != aggregation.execution_fingerprint


def test_raw_input_without_exact_manifest_blocks_before_any_write() -> None:
    facts = _facts()
    raw = RuntimeOccurrence(
        _id("raw"), "raw_input", _id("numeric-contract"), "close_raw"
    )
    projection = RuntimeOccurrence(
        _id("raw-projection"),
        "layer_projection",
        _id("numeric-contract"),
        "close_raw",
        source_occurrence_id=raw.compiled_feature_occurrence_id,
    )
    aggregation = replace(
        facts.aggregations[0],
        inputs=(RuntimeAggregationInput("signals", 0, projection.compiled_feature_occurrence_id),),
    )
    facts = replace(
        facts,
        occurrences=(projection, raw),
        source_materializations=(),
        aggregations=(aggregation,),
    )
    writes: list[object] = []

    with pytest.raises(V022RuntimeContractError) as error:
        build_suite_runtime_preflight(_request(), facts)

    assert error.value.reason_code == "v022_raw_input_manifest_binding_missing"
    assert writes == []


def test_unmaterialized_processing_source_blocks_before_any_write() -> None:
    facts = replace(_facts(), source_materializations=())
    writes: list[object] = []

    with pytest.raises(V022RuntimeContractError) as error:
        build_suite_runtime_preflight(_request(), facts)

    assert error.value.reason_code == "v022_processing_materialization_missing"
    assert writes == []


def test_processing_source_must_be_exact_completed_reusable_output() -> None:
    facts = _facts()
    invalid = replace(facts.source_materializations[0], node_run_status="running")

    with pytest.raises(V022RuntimeContractError) as error:
        build_suite_runtime_preflight(
            _request(), replace(facts, source_materializations=(invalid,))
        )

    assert error.value.reason_code == "v022_processing_materialization_invalid"


def test_aggregation_work_identity_pins_the_ordered_exact_input_manifest() -> None:
    facts = _facts()
    first = build_suite_runtime_preflight(_request(), facts)
    replacement = replace(
        facts.source_materializations[0],
        payload_manifest_id=_id("replacement-node-manifest"),
        payload_manifest_artifact_id=_id("replacement-node-manifest-artifact"),
        manifest_hash=HASH_A,
        logical_payload_fingerprint=HASH_D,
    )
    second = build_suite_runtime_preflight(
        _request(), replace(facts, source_materializations=(replacement,))
    )

    first_node = next(item for item in first.work if item.occurrence_kind == "node")
    second_node = next(item for item in second.work if item.occurrence_kind == "node")
    first_aggregation = next(
        item for item in first.work if item.occurrence_kind == "aggregation"
    )
    second_aggregation = next(
        item for item in second.work if item.occurrence_kind == "aggregation"
    )
    assert first_node.execution_fingerprint == second_node.execution_fingerprint
    assert first_aggregation.execution_fingerprint != second_aggregation.execution_fingerprint
    assert first_aggregation.semantic_identity["ordered_input_manifests"] != (
        second_aggregation.semantic_identity["ordered_input_manifests"]
    )


def test_source_manifest_must_use_catalog_pinned_encoding() -> None:
    facts = _facts()
    materialization = replace(
        facts.source_materializations[0],
        physical_encoding_version_id=_id("foreign-encoding"),
    )

    with pytest.raises(V022RuntimeContractError) as error:
        build_suite_runtime_preflight(
            _request(), replace(facts, source_materializations=(materialization,))
        )

    assert error.value.reason_code == "v022_source_materialization_encoding_mismatch"


def test_physical_encoding_pin_changes_aggregation_and_every_typed_work_identity() -> None:
    facts = _facts()
    first = build_suite_runtime_preflight(_request(), facts)
    replacement_encoding = _id("replacement-parquet-encoding")
    pins = replace(
        facts.payload_pins, physical_encoding_version_id=replacement_encoding
    )
    materialization = replace(
        facts.source_materializations[0],
        physical_encoding_version_id=replacement_encoding,
    )
    second = build_suite_runtime_preflight(
        _request(),
        replace(
            facts,
            payload_pins=pins,
            source_materializations=(materialization,),
        ),
    )

    first_by_kind = {item.occurrence_kind: item for item in first.work}
    second_by_kind = {item.occurrence_kind: item for item in second.work}
    assert first_by_kind["node"].execution_fingerprint == (
        second_by_kind["node"].execution_fingerprint
    )
    for kind in (
        "aggregation",
        "strategy_target",
        "defense_decision",
        "sleeve_merge",
        "portfolio_cell",
    ):
        assert first_by_kind[kind].execution_fingerprint != (
            second_by_kind[kind].execution_fingerprint
        )


def test_effective_range_cannot_drift_from_frozen_request() -> None:
    facts = _facts()
    with pytest.raises(V022RuntimeContractError) as error:
        build_suite_runtime_preflight(
            _request(),
            replace(
                facts,
                effective_range={"start": "2020-03-01", "end": "2025-12-31"},
            ),
        )

    assert error.value.reason_code == "v022_suite_runtime_effective_range_mismatch"


def test_manifest_loader_requires_verified_timestamp_and_matching_object_size() -> None:
    source = Path("src/style_rotation/v022/suite_runtime_planner.py").read_text(
        encoding="utf-8"
    )

    assert "object.verified_at IS NOT NULL" in source
    assert "partition.byte_size=object.byte_size" in source


def test_loader_pins_exact_cell_binding_and_deduplicates_reused_node_binding() -> None:
    source = Path("src/style_rotation/v022/suite_runtime_planner.py").read_text(
        encoding="utf-8"
    )

    assert (
        "JOIN experiment.v022_research_cell_evaluation_data_context_binding"
        in source
    )
    assert "SELECT DISTINCT node_run_id,compiled_graph_node_id" in source
    assert "v022_processing_materialization_ambiguous" in source


@pytest.mark.parametrize(
    "changes",
    [
        {"partition_count": 0, "actual_partition_count": 0, "maximum_ordinal": None},
        {"objects_verified": False},
    ],
)
def test_node_manifest_structure_rejects_empty_partitions_and_unverified_objects(
    changes: dict[str, object],
) -> None:
    row: dict[str, object] = {
        "payload_manifest_id": _id("manifest"),
        "manifest_artifact_type": "v022_payload_manifest",
        "manifest_artifact_key": "node-output:test:signal",
        "manifest_artifact_version": 1,
        "partition_count": 1,
        "actual_partition_count": 1,
        "byte_size": 128,
        "actual_byte_size": 128,
        "row_or_item_count": 10,
        "actual_item_count": 10,
        "minimum_ordinal": 0,
        "maximum_ordinal": 0,
        "objects_verified": True,
    }
    row.update(changes)

    with pytest.raises(V022RuntimeContractError) as error:
        _validate_manifest_structure_row(row)

    assert error.value.reason_code == "v022_processing_manifest_structure_invalid"


def test_no_defense_branch_uses_direct_strategy_to_merge_edge() -> None:
    facts = _facts()
    branch = replace(facts.branches[0], defense=None)
    result = build_suite_runtime_preflight(
        _request(), replace(facts, branches=(branch,))
    )

    assert "defense_decision" not in {item.occurrence_kind for item in result.work}
    strategy = next(item for item in result.work if item.occurrence_kind == "strategy_target")
    merge = next(item for item in result.work if item.occurrence_kind == "sleeve_merge")
    assert merge.required_upstream_keys == (strategy.occurrence_key,)
    assert result.defense_decision_work_count == 0
    assert result.typed_work_count == 3
    assert result.graph_consumer_count == 5


def test_projection_cycle_is_rejected_deterministically() -> None:
    facts = _facts()
    first = RuntimeOccurrence(
        _id("cycle-a"),
        "layer_projection",
        _id("numeric-contract"),
        "cycle_a",
        source_occurrence_id=_id("cycle-b"),
    )
    second = RuntimeOccurrence(
        _id("cycle-b"),
        "layer_projection",
        _id("numeric-contract"),
        "cycle_b",
        source_occurrence_id=_id("cycle-a"),
    )
    aggregation = replace(
        facts.aggregations[0],
        inputs=(RuntimeAggregationInput("signals", 0, first.compiled_feature_occurrence_id),),
    )

    with pytest.raises(V022RuntimeContractError) as error:
        build_suite_runtime_preflight(
            _request(),
            replace(
                facts,
                occurrences=(second, first),
                source_materializations=(),
                aggregations=(aggregation,),
            ),
        )

    assert error.value.reason_code == "v022_runtime_occurrence_projection_cycle"


@pytest.mark.parametrize(
    ("requested_range", "effective_range"),
    [
        ({"start": "2020-01-01"}, {"start": "2020-01-01", "end": "2021-01-01"}),
        (
            {"start": "2020-01-01", "end": "2021-01-01", "timezone": "UTC"},
            {"start": "2020-01-01", "end": "2021-01-01"},
        ),
        (
            {"start": "2021-01-01", "end": "2020-01-01"},
            {"start": "2020-01-01", "end": "2021-01-01"},
        ),
        (
            {"start": "2020-01-01", "end": "2021-01-01"},
            {"start": "not-a-date", "end": "2021-01-01"},
        ),
    ],
)
def test_requested_and_effective_ranges_are_strict_iso_intervals(
    requested_range: dict[str, object], effective_range: dict[str, object]
) -> None:
    request = replace(_request(), requested_range=requested_range)
    facts = replace(_facts(), effective_range=effective_range)

    with pytest.raises(V022RuntimeContractError) as error:
        build_suite_runtime_preflight(request, facts)

    assert error.value.reason_code == "v022_suite_runtime_range_invalid"


def test_effective_range_must_exactly_equal_requested_range() -> None:
    facts = replace(
        _facts(), effective_range={"start": "2019-12-31", "end": "2025-12-31"}
    )

    with pytest.raises(V022RuntimeContractError) as error:
        build_suite_runtime_preflight(_request(), facts)

    assert error.value.reason_code == (
        "v022_suite_runtime_effective_range_mismatch"
    )


def test_cohort_identity_and_warmup_range_are_frozen_into_work() -> None:
    cohort_id = _id("evaluation-cohort")
    request = replace(
        _request(),
        evaluation_cohort_version_id=cohort_id,
        materialization_range={"start": "2018-01-01", "end": "2025-12-31"},
    )
    facts = replace(
        _facts(),
        evaluation_cohort_version_id=cohort_id,
        evaluation_cohort_artifact_id=_id("evaluation-cohort-artifact"),
        evaluation_cohort_fingerprint=HASH_A,
        evaluation_cohort_research_tier="rankable_research",
        evaluation_cohort_frequency="weekly",
        evaluation_cohort_warmup_range={
            "start": "2018-01-01",
            "end": "2025-12-31",
        },
        evaluation_cohort_runtime_contract_id=_id("cohort-runtime-contract"),
        evaluation_cohort_runtime_fingerprint=HASH_B,
        dataset_gate_assessment_id=_id("dataset-gate-assessment"),
        dataset_gate_fingerprint=HASH_C,
    )

    result = build_suite_runtime_preflight(request, facts)

    typed = [item for item in result.work if item.occurrence_kind == "portfolio_cell"]
    assert typed[0].semantic_identity["evaluation_cohort_version_id"] == str(cohort_id)
    assert typed[0].semantic_identity["evaluation_cohort_fingerprint"] == HASH_A


def test_exact_runtime_range_rejects_short_input_instead_of_moving_start() -> None:
    facts = _facts()
    with pytest.raises(V022RuntimeContractError) as error:
        _exact_runtime_range(
            {"start": "2020-01-01", "end": "2025-12-31"},
            cohort_warmup_range={"start": "2018-01-01", "end": "2025-12-31"},
            execution_inputs=((date(2018, 1, 2), date(2025, 12, 31)),),
            cells=facts.cells,
            materializations=facts.source_materializations,
        )

    assert error.value.reason_code == "v022_evaluation_input_coverage_insufficient"


def test_every_cell_evaluation_context_must_cover_effective_range() -> None:
    facts = _facts()
    cell = replace(
        facts.cells[0], evaluation_data_coverage_start=date(2020, 3, 1)
    )

    with pytest.raises(V022RuntimeContractError) as error:
        build_suite_runtime_preflight(_request(), replace(facts, cells=(cell,)))

    assert error.value.reason_code == (
        "v022_portfolio_evaluation_coverage_insufficient"
    )


def test_work_identity_is_reusable_across_suite_plan_identity() -> None:
    first_facts = _facts()
    first = build_suite_runtime_preflight(_request(), first_facts)
    second_suite_id = _id("second-suite")
    second_branch_id = _id("second-suite-branch")
    second_cell_id = _id("second-cell")
    second_branch = replace(
        first_facts.branches[0], research_suite_branch_id=second_branch_id
    )
    second_cell = replace(
        first_facts.cells[0],
        research_cell_id=second_cell_id,
        research_suite_branch_id=second_branch_id,
    )
    second_facts = replace(
        first_facts,
        research_suite_id=second_suite_id,
        research_suite_artifact_id=_id("second-suite-artifact"),
        suite_fingerprint=HASH_A,
        branches=(second_branch,),
        cells=(second_cell,),
    )
    second = build_suite_runtime_preflight(
        _request(suite_id=second_suite_id), second_facts
    )

    assert [item.execution_fingerprint for item in first.work] == [
        item.execution_fingerprint for item in second.work
    ]
    assert [item.occurrence_key for item in first.work] == [
        item.occurrence_key for item in second.work
    ]
    assert first.plan_fingerprint != second.plan_fingerprint
    assert first.run_fingerprint != second.run_fingerprint


def test_verified_facts_loader_uses_only_read_queries_and_never_latest_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    class Result:
        def mappings(self) -> Result:
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class ReadConnection:
        def execute(self, statement: object, parameters: object = None) -> Result:
            compiled = str(statement)
            statements.append(compiled)
            return Result()

    monkeypatch.setattr(
        "style_rotation.v022.suite_runtime_planner._load_suite_header",
        lambda connection, request: {
            "research_suite_id": request.research_suite_id,
            "research_suite_artifact_id": _id("suite-artifact"),
            "suite_fingerprint": HASH_A,
            "compiled_research_graph_id": _id("graph"),
            "compiled_graph_artifact_id": _id("graph-artifact"),
            "graph_fingerprint": HASH_B,
            "catalog_release_id": _id("catalog-release"),
            "catalog_release_artifact_id": _id("catalog-artifact"),
            "compiled_execution_data_context_id": _id("risk-context"),
            "execution_data_context_artifact_id": _id("risk-artifact"),
            "execution_data_context_fingerprint": HASH_C,
            "evaluation_cohort_version_id": None,
            "evaluation_cohort_artifact_id": None,
            "evaluation_cohort_fingerprint": None,
            "evaluation_cohort_research_tier": None,
            "evaluation_cohort_frequency": None,
            "cohort_warmup_start": None,
            "cohort_evaluation_end": None,
            "branch_count": 1,
            "cell_count": 1,
            "occurrence_count": len(_facts().occurrences),
            "aggregation_instance_count": len(_facts().aggregations),
            "execution_input_count": 1,
        },
    )
    monkeypatch.setattr(
        "style_rotation.v022.suite_runtime_planner._load_runtime_payload_pins",
        lambda connection, release: _facts().payload_pins,
    )
    monkeypatch.setattr(
        "style_rotation.v022.suite_runtime_planner._load_runtime_occurrences",
        lambda connection, graph: _facts().occurrences,
    )
    monkeypatch.setattr(
        "style_rotation.v022.suite_runtime_planner._load_runtime_aggregations",
        lambda connection, graph: _facts().aggregations,
    )
    monkeypatch.setattr(
        "style_rotation.v022.suite_runtime_planner._load_runtime_branches",
        lambda connection, header: _facts().branches,
    )
    monkeypatch.setattr(
        "style_rotation.v022.suite_runtime_planner._load_runtime_cells",
        lambda connection, header: _facts().cells,
    )
    monkeypatch.setattr(
        "style_rotation.v022.suite_runtime_planner._load_exact_source_materializations",
        lambda *args, **kwargs: _facts().source_materializations,
    )
    monkeypatch.setattr(
        "style_rotation.v022.suite_runtime_planner._load_execution_input_coverage",
        lambda connection, context: (
            {
                "coverage_start": date(2019, 1, 1),
                "coverage_end": date(2025, 12, 31),
            },
        ),
    )

    facts = load_verified_facts(ReadConnection(), _request())  # type: ignore[arg-type]

    assert facts.research_suite_id == _id("suite")
    assert statements == []
    source = Path("src/style_rotation/v022/suite_runtime_planner.py").read_text(
        encoding="utf-8"
    )
    upper = source.upper()
    assert all(token not in upper for token in ("INSERT INTO", "UPDATE ", "DELETE FROM"))
    assert " LIMIT 1" not in upper
    assert "MAX(CREATED_AT" not in upper
    assert "ORDER BY CREATED_AT" not in upper
