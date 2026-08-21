from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal, NoReturn

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.dag import WorkKind
from style_rotation.v022.runtime_contract import V022RuntimeContractError

CONTRACT_VERSION = "v0.22.0"
RUNTIME_CATALOG_VERSION = semantic_version_number("0.22.13")
PROCESSING_RUNTIME_EXECUTOR_VERSION = "v022-first-slice-runtime-20"
PROCESSING_RUNTIME_ENVIRONMENT_FINGERPRINT = sha256_hexdigest(
    {
        "contract_version": CONTRACT_VERSION,
        "runtime": PROCESSING_RUNTIME_EXECUTOR_VERSION,
    }
)

ProductionKind = Literal["raw_input", "node_output", "layer_projection"]


@dataclass(frozen=True, slots=True)
class SuiteRuntimePlanRequest:
    research_suite_id: uuid.UUID
    requested_by: str
    requested_range: dict[str, object]
    executor_version: str
    environment_fingerprint: str
    evaluation_cohort_version_id: uuid.UUID | None = None
    materialization_range: dict[str, object] | None = None
    source_executor_version: str | None = None
    source_environment_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimePayloadPins:
    strategy_target_payload_contract_version_id: uuid.UUID
    defense_decision_payload_contract_version_id: uuid.UUID
    sleeve_merge_payload_contract_version_id: uuid.UUID
    portfolio_cell_payload_contract_version_id: uuid.UUID
    physical_encoding_version_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class RuntimeOccurrence:
    compiled_feature_occurrence_id: uuid.UUID
    production_kind: ProductionKind
    payload_contract_version_id: uuid.UUID
    feature_variant_key: str
    source_occurrence_id: uuid.UUID | None = None
    compiled_graph_node_id: uuid.UUID | None = None
    output_port_key: str | None = None
    feature_version_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ExactSourceMaterialization:
    """A read-side proof for an already materialized Aggregation source.

    A Node source must point at its existing completed/reused Graph Work Item.  The
    planner never manufactures a completed Node Work Item or a Payload Manifest.
    Raw sources intentionally have no Work Item; they become usable only after a
    separate Dataset-to-Payload publication path provides an exact Manifest.
    """

    terminal_occurrence_id: uuid.UUID
    source_kind: Literal["raw_input", "node_output"]
    payload_manifest_id: uuid.UUID
    payload_manifest_artifact_id: uuid.UUID
    manifest_hash: str
    logical_payload_fingerprint: str
    payload_contract_version_id: uuid.UUID
    physical_encoding_version_id: uuid.UUID
    producer_artifact_id: uuid.UUID
    producer_output_port_key: str
    manifest_artifact_status: str
    materialization_state: str
    quality_status: str
    coverage_start: date
    coverage_end: date
    compiled_graph_node_id: uuid.UUID | None = None
    node_run_id: uuid.UUID | None = None
    graph_work_item_id: uuid.UUID | None = None
    node_execution_fingerprint: str | None = None
    node_run_status: str | None = None
    graph_work_status: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeAggregationInput:
    slot_key: str
    ordinal: int
    compiled_feature_occurrence_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class RuntimeAggregationEnsembleMember:
    ordinal: int
    target_group_ordinal: int
    member_ordinal_within_target: int
    target_version_id: uuid.UUID
    target_version_artifact_id: uuid.UUID
    target_key: str
    target_semantics: dict[str, object]
    training_preset_version_id: uuid.UUID
    training_preset_version_artifact_id: uuid.UUID
    training_preset_key: str
    training_preset_semantics: dict[str, object]


@dataclass(frozen=True, slots=True)
class RuntimeAggregation:
    compiled_aggregation_instance_id: uuid.UUID
    aggregation_version_id: uuid.UUID
    aggregation_version_artifact_id: uuid.UUID
    parameter_preset_version_id: uuid.UUID | None
    instance_fingerprint: str
    resolved_parameters: dict[str, object]
    output_payload_contract_version_id: uuid.UUID
    inputs: tuple[RuntimeAggregationInput, ...]
    execution_mode: Literal["deterministic", "supervised"] = "deterministic"
    implementation_key: str | None = None
    target_version_id: uuid.UUID | None = None
    target_version_artifact_id: uuid.UUID | None = None
    target_semantics: dict[str, object] | None = None
    training_preset_version_id: uuid.UUID | None = None
    training_preset_version_artifact_id: uuid.UUID | None = None
    ensemble_spec_id: uuid.UUID | None = None
    ensemble_spec_artifact_id: uuid.UUID | None = None
    ensemble_fingerprint: str | None = None
    ensemble_document: dict[str, object] | None = None
    ensemble_members: tuple[RuntimeAggregationEnsembleMember, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeDefenseBinding:
    defense_version_id: uuid.UUID
    timing_policy_version_id: uuid.UUID
    allocation_policy_version_id: uuid.UUID
    compiled_defense_execution_context_id: uuid.UUID
    defense_context_fingerprint: str


@dataclass(frozen=True, slots=True)
class RuntimeBranch:
    research_suite_branch_id: uuid.UUID
    compiled_strategy_branch_id: uuid.UUID
    configuration_snapshot_id: uuid.UUID
    compiled_aggregation_instance_id: uuid.UUID
    strategy_version_id: uuid.UUID
    strategy_parameter_preset_version_id: uuid.UUID
    branch_fingerprint: str
    configuration_fingerprint: str
    defense: RuntimeDefenseBinding | None


@dataclass(frozen=True, slots=True)
class RuntimeCell:
    research_cell_id: uuid.UUID
    research_suite_branch_id: uuid.UUID
    evaluation_context_ordinal: int
    evaluation_policy_context_fingerprint: str
    portfolio_evaluation_data_context_id: uuid.UUID
    portfolio_evaluation_data_context_artifact_id: uuid.UUID
    evaluation_data_context_fingerprint: str
    evaluation_data_coverage_start: date
    evaluation_data_coverage_end: date


@dataclass(frozen=True, slots=True)
class VerifiedSuiteRuntimeFacts:
    """Published immutable identities loaded by a read-only DB preflight.

    The future DB loader is responsible for proving each Artifact is published and
    each identity belongs to the exact Suite/Graph/Catalog.  The pure builder still
    validates graph closure and materialization proofs so missing runtime data is
    rejected before the write transaction starts.
    """

    research_suite_id: uuid.UUID
    research_suite_artifact_id: uuid.UUID
    suite_fingerprint: str
    compiled_research_graph_id: uuid.UUID
    compiled_graph_artifact_id: uuid.UUID
    graph_fingerprint: str
    catalog_release_id: uuid.UUID
    catalog_release_artifact_id: uuid.UUID
    compiled_execution_data_context_id: uuid.UUID
    execution_data_context_artifact_id: uuid.UUID
    execution_data_context_fingerprint: str
    evaluation_cohort_version_id: uuid.UUID | None
    evaluation_cohort_artifact_id: uuid.UUID | None
    evaluation_cohort_fingerprint: str | None
    evaluation_cohort_research_tier: str | None
    evaluation_cohort_frequency: str | None
    evaluation_cohort_warmup_range: dict[str, object] | None
    expected_branch_count: int
    expected_cell_count: int
    effective_range: dict[str, object]
    payload_pins: RuntimePayloadPins
    occurrences: tuple[RuntimeOccurrence, ...]
    source_materializations: tuple[ExactSourceMaterialization, ...]
    aggregations: tuple[RuntimeAggregation, ...]
    branches: tuple[RuntimeBranch, ...]
    cells: tuple[RuntimeCell, ...]
    evaluation_cohort_runtime_contract_id: uuid.UUID | None = None
    evaluation_cohort_runtime_fingerprint: str | None = None
    dataset_gate_assessment_id: uuid.UUID | None = None
    dataset_gate_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeWorkBlueprint:
    occurrence_kind: WorkKind
    occurrence_key: str
    execution_fingerprint: str
    required_upstream_keys: tuple[str, ...]
    semantic_identity: dict[str, object]
    priority: int
    required_existing_work_item_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class SuiteRuntimePreflight:
    run_fingerprint: str
    plan_fingerprint: str
    work: tuple[RuntimeWorkBlueprint, ...]
    strategy_target_work_count: int
    defense_decision_work_count: int
    sleeve_merge_work_count: int
    portfolio_cell_work_count: int
    typed_work_count: int
    graph_consumer_count: int


class SuiteRuntimePlanner:
    """Read-only preflight facade for the M79 Suite runtime.

    `preflight` deliberately opens only a read connection.  The atomic writer is a
    later phase and must consume this complete immutable fact set; it is never
    entered when exact Raw/Processing materialization is absent.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load_verified_facts(
        self, request: SuiteRuntimePlanRequest
    ) -> VerifiedSuiteRuntimeFacts:
        with self._engine.connect() as connection:
            return load_verified_facts(connection, request)

    def preflight(self, request: SuiteRuntimePlanRequest) -> SuiteRuntimePreflight:
        return build_suite_runtime_preflight(
            request, self.load_verified_facts(request)
        )


def load_verified_facts(
    connection: Connection,
    request: SuiteRuntimePlanRequest,
) -> VerifiedSuiteRuntimeFacts:
    """Load one exact published Suite closure without selecting a latest identity."""

    header = _load_suite_header(connection, request)
    pins = _load_runtime_payload_pins(connection, header["catalog_release_id"])
    occurrences = _load_runtime_occurrences(
        connection, header["compiled_research_graph_id"]
    )
    aggregations = _load_runtime_aggregations(
        connection, header["compiled_research_graph_id"]
    )
    branches = _load_runtime_branches(connection, header)
    cells = _load_runtime_cells(connection, header)
    execution_inputs = _load_execution_input_coverage(
        connection, header["compiled_execution_data_context_id"]
    )
    _validate_loaded_closure_counts(
        header,
        occurrences=occurrences,
        aggregations=aggregations,
        branches=branches,
        cells=cells,
        execution_inputs=execution_inputs,
    )
    required_terminal_ids = _required_terminal_occurrence_ids(
        occurrences, aggregations
    )
    materializations = _load_exact_source_materializations(
        connection,
        header=header,
        request=request,
        payload_pins=pins,
        occurrences=occurrences,
        required_terminal_ids=required_terminal_ids,
    )
    effective_range = _exact_runtime_range(
        request.requested_range,
        cohort_warmup_range=(
            {
                "start": header["cohort_warmup_start"].isoformat(),
                "end": header["cohort_evaluation_end"].isoformat(),
            }
            if header["evaluation_cohort_version_id"] is not None
            else None
        ),
        execution_inputs=tuple(
            (row["coverage_start"], row["coverage_end"])
            for row in execution_inputs
        ),
        cells=cells,
        materializations=materializations,
    )
    return VerifiedSuiteRuntimeFacts(
        research_suite_id=header["research_suite_id"],
        research_suite_artifact_id=header["research_suite_artifact_id"],
        suite_fingerprint=header["suite_fingerprint"],
        compiled_research_graph_id=header["compiled_research_graph_id"],
        compiled_graph_artifact_id=header["compiled_graph_artifact_id"],
        graph_fingerprint=header["graph_fingerprint"],
        catalog_release_id=header["catalog_release_id"],
        catalog_release_artifact_id=header["catalog_release_artifact_id"],
        compiled_execution_data_context_id=header[
            "compiled_execution_data_context_id"
        ],
        execution_data_context_artifact_id=header[
            "execution_data_context_artifact_id"
        ],
        execution_data_context_fingerprint=header[
            "execution_data_context_fingerprint"
        ],
        evaluation_cohort_version_id=header["evaluation_cohort_version_id"],
        evaluation_cohort_artifact_id=header["evaluation_cohort_artifact_id"],
        evaluation_cohort_fingerprint=header["evaluation_cohort_fingerprint"],
        evaluation_cohort_research_tier=header["evaluation_cohort_research_tier"],
        evaluation_cohort_frequency=header["evaluation_cohort_frequency"],
        evaluation_cohort_warmup_range=(
            {
                "start": header["cohort_warmup_start"].isoformat(),
                "end": header["cohort_evaluation_end"].isoformat(),
            }
            if header["evaluation_cohort_version_id"] is not None
            else None
        ),
        expected_branch_count=header["branch_count"],
        expected_cell_count=header["cell_count"],
        effective_range=effective_range,
        payload_pins=pins,
        occurrences=occurrences,
        source_materializations=materializations,
        aggregations=aggregations,
        branches=branches,
        cells=cells,
        evaluation_cohort_runtime_contract_id=header.get(
            "evaluation_cohort_runtime_contract_id"
        ),
        evaluation_cohort_runtime_fingerprint=header.get(
            "evaluation_cohort_runtime_fingerprint"
        ),
        dataset_gate_assessment_id=header.get("dataset_gate_assessment_id"),
        dataset_gate_fingerprint=header.get("dataset_gate_fingerprint"),
    )


def _validate_loaded_closure_counts(
    header: RowMapping,
    *,
    occurrences: tuple[RuntimeOccurrence, ...],
    aggregations: tuple[RuntimeAggregation, ...],
    branches: tuple[RuntimeBranch, ...],
    cells: tuple[RuntimeCell, ...],
    execution_inputs: tuple[RowMapping, ...],
) -> None:
    if (
        len(occurrences) != header["occurrence_count"]
        or len(aggregations) != header["aggregation_instance_count"]
        or len(branches) != header["branch_count"]
        or len(cells) != header["cell_count"]
        or len(execution_inputs) != header["execution_input_count"]
    ):
        _fail(
            "v022_suite_runtime_loaded_closure_incomplete",
            "Read-only runtime facts do not match the exact immutable Graph and Suite counts",
        )


def build_suite_runtime_preflight(
    request: SuiteRuntimePlanRequest,
    facts: VerifiedSuiteRuntimeFacts,
) -> SuiteRuntimePreflight:
    """Build a deterministic Suite DAG without writing any external state.

    This is deliberately separate from the future connection-bound writer.  A
    materialization failure therefore occurs before Graph Run, Suite binding,
    Artifact, Plan, or Work Spec insertion is possible.
    """

    effective_start, effective_end = _validate_request(request, facts)
    occurrences: dict[uuid.UUID, RuntimeOccurrence] = _unique_by_id(
        facts.occurrences,
        key=lambda item: item.compiled_feature_occurrence_id,
        reason="v022_runtime_occurrence_identity_duplicate",
    )
    materializations: dict[uuid.UUID, ExactSourceMaterialization] = _unique_by_id(
        facts.source_materializations,
        key=lambda item: item.terminal_occurrence_id,
        reason="v022_source_materialization_identity_duplicate",
    )
    aggregations: dict[uuid.UUID, RuntimeAggregation] = _unique_by_id(
        facts.aggregations,
        key=lambda item: item.compiled_aggregation_instance_id,
        reason="v022_runtime_aggregation_identity_duplicate",
    )
    branches: dict[uuid.UUID, RuntimeBranch] = _unique_by_id(
        facts.branches,
        key=lambda item: item.research_suite_branch_id,
        reason="v022_runtime_branch_identity_duplicate",
    )
    cells: dict[uuid.UUID, RuntimeCell] = _unique_by_id(
        facts.cells,
        key=lambda item: item.research_cell_id,
        reason="v022_runtime_cell_identity_duplicate",
    )

    if len(branches) != facts.expected_branch_count or len(cells) != facts.expected_cell_count:
        _fail(
            "v022_suite_runtime_matrix_incomplete",
            "Runtime facts do not exactly cover the immutable Suite matrix",
        )
    if not aggregations or not branches or not cells:
        _fail("v022_suite_runtime_matrix_empty", "Runtime Suite DAG cannot be empty")
    for cell in cells.values():
        if cell.evaluation_data_coverage_start > cell.evaluation_data_coverage_end:
            _fail(
                "v022_portfolio_evaluation_coverage_invalid",
                "Portfolio Evaluation Data Context coverage is inverted",
                portfolio_evaluation_data_context_id=(
                    cell.portfolio_evaluation_data_context_id
                ),
            )
        if (
            cell.evaluation_data_coverage_start > effective_start
            or cell.evaluation_data_coverage_end < effective_end
        ):
            _fail(
                "v022_portfolio_evaluation_coverage_insufficient",
                "Portfolio Evaluation Data Context does not cover the effective range",
                portfolio_evaluation_data_context_id=(
                    cell.portfolio_evaluation_data_context_id
                ),
                coverage_start=cell.evaluation_data_coverage_start,
                coverage_end=cell.evaluation_data_coverage_end,
                effective_start=effective_start,
                effective_end=effective_end,
            )

    work: list[RuntimeWorkBlueprint] = []
    node_work: dict[uuid.UUID, RuntimeWorkBlueprint] = {}
    aggregation_work: dict[uuid.UUID, RuntimeWorkBlueprint] = {}

    for aggregation in sorted(
        aggregations.values(), key=lambda item: str(item.compiled_aggregation_instance_id)
    ):
        if not aggregation.inputs:
            _fail(
                "v022_aggregation_input_set_empty",
                "Runtime Aggregation requires at least one explicit input",
            )
        ordered_inputs = sorted(aggregation.inputs, key=lambda item: (item.slot_key, item.ordinal))
        if len({(item.slot_key, item.ordinal) for item in ordered_inputs}) != len(ordered_inputs):
            _fail(
                "v022_aggregation_input_identity_duplicate",
                "Runtime Aggregation input slot and ordinal must be unique",
            )
        input_semantics: list[dict[str, object]] = []
        upstream_keys: set[str] = set()
        for item in ordered_inputs:
            terminal = _terminal_occurrence(
                item.compiled_feature_occurrence_id,
                occurrences,
            )
            binding = materializations.get(terminal.compiled_feature_occurrence_id)
            if binding is None:
                if terminal.production_kind == "raw_input":
                    _fail(
                        "v022_raw_input_manifest_binding_missing",
                        "Raw Aggregation input has no exact Dataset-to-Payload Manifest binding",
                        occurrence_id=terminal.compiled_feature_occurrence_id,
                    )
                _fail(
                    "v022_processing_materialization_missing",
                    "Processing Aggregation input has no exact completed Node output Manifest",
                    occurrence_id=terminal.compiled_feature_occurrence_id,
                )
            _validate_materialization(terminal, binding)
            if (
                binding.physical_encoding_version_id
                != facts.payload_pins.physical_encoding_version_id
            ):
                _fail(
                    "v022_source_materialization_encoding_mismatch",
                    "Source Payload Manifest does not use the Catalog-pinned encoding",
                    payload_manifest_id=binding.payload_manifest_id,
                )
            if (
                binding.coverage_start > binding.coverage_end
                or binding.coverage_start > effective_start
                or binding.coverage_end < effective_end
            ):
                _fail(
                    "v022_source_materialization_coverage_insufficient",
                    "Source Payload Manifest does not cover the effective range",
                    payload_manifest_id=binding.payload_manifest_id,
                    coverage_start=binding.coverage_start,
                    coverage_end=binding.coverage_end,
                    effective_start=effective_start,
                    effective_end=effective_end,
                )
            if terminal.production_kind == "node_output":
                assert binding.compiled_graph_node_id is not None
                assert binding.graph_work_item_id is not None
                assert binding.node_execution_fingerprint is not None
                key = f"node:{binding.compiled_graph_node_id}"
                candidate = RuntimeWorkBlueprint(
                    occurrence_kind="node",
                    occurrence_key=key,
                    execution_fingerprint=binding.node_execution_fingerprint,
                    required_upstream_keys=(),
                    semantic_identity={
                        "contract_version": CONTRACT_VERSION,
                        "work_kind": "node",
                        "compiled_graph_node_id": str(binding.compiled_graph_node_id),
                        "node_run_id": str(binding.node_run_id),
                    },
                    priority=100,
                    required_existing_work_item_id=binding.graph_work_item_id,
                )
                prior = node_work.get(binding.compiled_graph_node_id)
                if prior is not None and prior != candidate:
                    _fail(
                        "v022_processing_materialization_ambiguous",
                        "One compiled Node resolves to conflicting exact materializations",
                    )
                node_work[binding.compiled_graph_node_id] = candidate
                upstream_keys.add(key)
            input_semantics.append(
                {
                    "slot_key": item.slot_key,
                    "ordinal": item.ordinal,
                    "compiled_feature_occurrence_id": str(
                        item.compiled_feature_occurrence_id
                    ),
                    "terminal_occurrence_id": str(
                        terminal.compiled_feature_occurrence_id
                    ),
                    "payload_manifest_id": str(binding.payload_manifest_id),
                    "manifest_hash": binding.manifest_hash,
                    "logical_payload_fingerprint": binding.logical_payload_fingerprint,
                }
            )
        semantic: dict[str, object] = {
            "contract_version": CONTRACT_VERSION,
            "work_kind": "aggregation",
            "execution_mode": aggregation.execution_mode,
            "implementation_key": aggregation.implementation_key,
            "aggregation_version_id": str(aggregation.aggregation_version_id),
            "parameter_preset_version_id": (
                str(aggregation.parameter_preset_version_id)
                if aggregation.parameter_preset_version_id is not None
                else None
            ),
            "instance_fingerprint": aggregation.instance_fingerprint,
            "resolved_parameters": aggregation.resolved_parameters,
            "target_version_id": (
                str(aggregation.target_version_id)
                if aggregation.target_version_id is not None
                else None
            ),
            "target_semantics": aggregation.target_semantics,
            "training_preset_version_id": (
                str(aggregation.training_preset_version_id)
                if aggregation.training_preset_version_id is not None
                else None
            ),
            "ensemble_spec_id": (
                str(aggregation.ensemble_spec_id)
                if aggregation.ensemble_spec_id is not None
                else None
            ),
            "ensemble_fingerprint": aggregation.ensemble_fingerprint,
            "ensemble_document": aggregation.ensemble_document,
            "ensemble_members": [
                {
                    "ordinal": member.ordinal,
                    "target_group_ordinal": member.target_group_ordinal,
                    "member_ordinal_within_target": (
                        member.member_ordinal_within_target
                    ),
                    "target_version_id": str(member.target_version_id),
                    "target_semantics": member.target_semantics,
                    "training_preset_version_id": str(
                        member.training_preset_version_id
                    ),
                    "training_preset_semantics": (
                        member.training_preset_semantics
                    ),
                }
                for member in aggregation.ensemble_members
            ],
            "ordered_input_manifests": input_semantics,
            "output_payload_contract_version_id": str(
                aggregation.output_payload_contract_version_id
            ),
            "physical_encoding_version_id": str(
                facts.payload_pins.physical_encoding_version_id
            ),
        }
        blueprint = _new_work(
            request,
            "aggregation",
            f"aggregation:{aggregation.compiled_aggregation_instance_id}",
            semantic,
            tuple(sorted(upstream_keys)),
            priority=200,
        )
        aggregation_work[aggregation.compiled_aggregation_instance_id] = blueprint

    work.extend(sorted(node_work.values(), key=lambda item: item.occurrence_key))
    work.extend(sorted(aggregation_work.values(), key=lambda item: item.occurrence_key))

    branch_work: dict[uuid.UUID, tuple[RuntimeWorkBlueprint, RuntimeWorkBlueprint]] = {}
    defense_count = 0
    for branch in sorted(branches.values(), key=lambda item: str(item.research_suite_branch_id)):
        source = aggregation_work.get(branch.compiled_aggregation_instance_id)
        if source is None:
            _fail(
                "v022_runtime_branch_aggregation_missing",
                "Runtime Branch references an Aggregation outside its exact Graph",
            )
        common = {
            "compiled_strategy_branch_id": str(branch.compiled_strategy_branch_id),
            "configuration_snapshot_id": str(branch.configuration_snapshot_id),
            "branch_fingerprint": branch.branch_fingerprint,
            "configuration_fingerprint": branch.configuration_fingerprint,
            "effective_range": facts.effective_range,
            "evaluation_cohort_version_id": (
                str(facts.evaluation_cohort_version_id)
                if facts.evaluation_cohort_version_id is not None
                else None
            ),
            "evaluation_cohort_fingerprint": facts.evaluation_cohort_fingerprint,
            "evaluation_cohort_runtime_contract_id": (
                str(facts.evaluation_cohort_runtime_contract_id)
                if facts.evaluation_cohort_runtime_contract_id is not None
                else None
            ),
            "evaluation_cohort_runtime_fingerprint": (
                facts.evaluation_cohort_runtime_fingerprint
            ),
            "dataset_gate_assessment_id": (
                str(facts.dataset_gate_assessment_id)
                if facts.dataset_gate_assessment_id is not None
                else None
            ),
            "dataset_gate_fingerprint": facts.dataset_gate_fingerprint,
        }
        strategy_semantic: dict[str, object] = {
            "contract_version": CONTRACT_VERSION,
            "work_kind": "strategy_target",
            **common,
            "strategy_version_id": str(branch.strategy_version_id),
            "strategy_parameter_preset_version_id": str(
                branch.strategy_parameter_preset_version_id
            ),
            "compiled_execution_data_context_id": str(
                facts.compiled_execution_data_context_id
            ),
            "execution_data_context_fingerprint": (
                facts.execution_data_context_fingerprint
            ),
            "source_aggregation_execution_fingerprint": source.execution_fingerprint,
            "output_payload_contract_version_id": str(
                facts.payload_pins.strategy_target_payload_contract_version_id
            ),
            "physical_encoding_version_id": str(
                facts.payload_pins.physical_encoding_version_id
            ),
        }
        strategy = _new_work(
            request,
            "strategy_target",
            f"strategy_target:{branch.compiled_strategy_branch_id}",
            strategy_semantic,
            (source.occurrence_key,),
            priority=300,
        )
        work.append(strategy)

        defense: RuntimeWorkBlueprint | None = None
        if branch.defense is not None:
            defense_count += 1
            defense_semantic: dict[str, object] = {
                "contract_version": CONTRACT_VERSION,
                "work_kind": "defense_decision",
                **common,
                "defense_version_id": str(branch.defense.defense_version_id),
                "timing_policy_version_id": str(
                    branch.defense.timing_policy_version_id
                ),
                "allocation_policy_version_id": str(
                    branch.defense.allocation_policy_version_id
                ),
                "compiled_defense_execution_context_id": str(
                    branch.defense.compiled_defense_execution_context_id
                ),
                "defense_context_fingerprint": branch.defense.defense_context_fingerprint,
                "source_strategy_execution_fingerprint": strategy.execution_fingerprint,
                "output_payload_contract_version_id": str(
                    facts.payload_pins.defense_decision_payload_contract_version_id
                ),
                "physical_encoding_version_id": str(
                    facts.payload_pins.physical_encoding_version_id
                ),
            }
            defense = _new_work(
                request,
                "defense_decision",
                f"defense_decision:{branch.compiled_strategy_branch_id}",
                defense_semantic,
                (strategy.occurrence_key,),
                priority=400,
            )
            work.append(defense)

        merge_semantic: dict[str, object] = {
            "contract_version": CONTRACT_VERSION,
            "work_kind": "sleeve_merge",
            **common,
            "source_strategy_execution_fingerprint": strategy.execution_fingerprint,
            "source_defense_execution_fingerprint": (
                defense.execution_fingerprint if defense is not None else None
            ),
            "output_payload_contract_version_id": str(
                facts.payload_pins.sleeve_merge_payload_contract_version_id
            ),
            "physical_encoding_version_id": str(
                facts.payload_pins.physical_encoding_version_id
            ),
        }
        merge_sources = [strategy.occurrence_key]
        if defense is not None:
            merge_sources.append(defense.occurrence_key)
        merge = _new_work(
            request,
            "sleeve_merge",
            f"sleeve_merge:{branch.compiled_strategy_branch_id}",
            merge_semantic,
            tuple(merge_sources),
            priority=500,
        )
        work.append(merge)
        branch_work[branch.research_suite_branch_id] = strategy, merge

    for cell in sorted(cells.values(), key=lambda item: str(item.research_cell_id)):
        pair = branch_work.get(cell.research_suite_branch_id)
        if pair is None:
            _fail(
                "v022_runtime_cell_branch_missing",
                "Runtime Cell references a Branch outside its exact Suite",
            )
        strategy, merge = pair
        cell_semantic: dict[str, object] = {
            "contract_version": CONTRACT_VERSION,
            "work_kind": "portfolio_cell",
            "compiled_strategy_branch_id": strategy.semantic_identity[
                "compiled_strategy_branch_id"
            ],
            "configuration_snapshot_id": strategy.semantic_identity[
                "configuration_snapshot_id"
            ],
            "evaluation_context_ordinal": cell.evaluation_context_ordinal,
            "evaluation_policy_context_fingerprint": (
                cell.evaluation_policy_context_fingerprint
            ),
            "portfolio_evaluation_data_context_id": str(
                cell.portfolio_evaluation_data_context_id
            ),
            "evaluation_data_context_fingerprint": (
                cell.evaluation_data_context_fingerprint
            ),
            "evaluation_data_coverage_start": cell.evaluation_data_coverage_start,
            "evaluation_data_coverage_end": cell.evaluation_data_coverage_end,
            "effective_range": facts.effective_range,
            "evaluation_cohort_version_id": (
                str(facts.evaluation_cohort_version_id)
                if facts.evaluation_cohort_version_id is not None
                else None
            ),
            "evaluation_cohort_fingerprint": facts.evaluation_cohort_fingerprint,
            "evaluation_cohort_runtime_contract_id": (
                str(facts.evaluation_cohort_runtime_contract_id)
                if facts.evaluation_cohort_runtime_contract_id is not None
                else None
            ),
            "evaluation_cohort_runtime_fingerprint": (
                facts.evaluation_cohort_runtime_fingerprint
            ),
            "dataset_gate_assessment_id": (
                str(facts.dataset_gate_assessment_id)
                if facts.dataset_gate_assessment_id is not None
                else None
            ),
            "dataset_gate_fingerprint": facts.dataset_gate_fingerprint,
            "source_merge_execution_fingerprint": merge.execution_fingerprint,
            "output_payload_contract_version_id": str(
                facts.payload_pins.portfolio_cell_payload_contract_version_id
            ),
            "physical_encoding_version_id": str(
                facts.payload_pins.physical_encoding_version_id
            ),
        }
        cell_work = _new_work(
            request,
            "portfolio_cell",
            "portfolio_cell:pending-global-fingerprint",
            cell_semantic,
            (merge.occurrence_key,),
            priority=600,
        )
        work.append(
            replace(
                cell_work,
                occurrence_key=f"portfolio_cell:{cell_work.execution_fingerprint}",
            )
        )

    _validate_work_graph(work)
    strategy_count = len(branches)
    merge_count = len(branches)
    cell_count = len(cells)
    typed_count = strategy_count + defense_count + merge_count + cell_count
    run_fingerprint = sha256_hexdigest(
        {
            "contract_version": CONTRACT_VERSION,
            "suite_fingerprint": facts.suite_fingerprint,
            "compiled_graph_fingerprint": facts.graph_fingerprint,
            "requested_range": request.requested_range,
            "environment_fingerprint": request.environment_fingerprint,
            "evaluation_cohort_version_id": (
                str(facts.evaluation_cohort_version_id)
                if facts.evaluation_cohort_version_id is not None
                else None
            ),
            "evaluation_cohort_fingerprint": facts.evaluation_cohort_fingerprint,
            "evaluation_cohort_runtime_fingerprint": (
                facts.evaluation_cohort_runtime_fingerprint
            ),
            "dataset_gate_fingerprint": facts.dataset_gate_fingerprint,
            "work": [
                {
                    "occurrence_kind": item.occurrence_kind,
                    "occurrence_key": item.occurrence_key,
                    "execution_fingerprint": item.execution_fingerprint,
                    "required_upstream_keys": item.required_upstream_keys,
                }
                for item in work
            ],
        }
    )
    plan_fingerprint = sha256_hexdigest(
        {
            "contract_version": CONTRACT_VERSION,
            "suite_fingerprint": facts.suite_fingerprint,
            "run_fingerprint": run_fingerprint,
            "catalog_release_id": facts.catalog_release_id,
            "execution_data_context_fingerprint": facts.execution_data_context_fingerprint,
            "effective_range": facts.effective_range,
            "evaluation_cohort_version_id": (
                str(facts.evaluation_cohort_version_id)
                if facts.evaluation_cohort_version_id is not None
                else None
            ),
            "evaluation_cohort_fingerprint": facts.evaluation_cohort_fingerprint,
            "evaluation_cohort_runtime_contract_id": (
                str(facts.evaluation_cohort_runtime_contract_id)
                if facts.evaluation_cohort_runtime_contract_id is not None
                else None
            ),
            "evaluation_cohort_runtime_fingerprint": (
                facts.evaluation_cohort_runtime_fingerprint
            ),
            "dataset_gate_assessment_id": (
                str(facts.dataset_gate_assessment_id)
                if facts.dataset_gate_assessment_id is not None
                else None
            ),
            "dataset_gate_fingerprint": facts.dataset_gate_fingerprint,
            "payload_pins": facts.payload_pins,
            "typed_work_count": typed_count,
            "graph_consumer_count": len(work),
        }
    )
    return SuiteRuntimePreflight(
        run_fingerprint=run_fingerprint,
        plan_fingerprint=plan_fingerprint,
        work=tuple(work),
        strategy_target_work_count=strategy_count,
        defense_decision_work_count=defense_count,
        sleeve_merge_work_count=merge_count,
        portfolio_cell_work_count=cell_count,
        typed_work_count=typed_count,
        graph_consumer_count=len(work),
    )


def _load_suite_header(
    connection: Connection, request: SuiteRuntimePlanRequest
) -> RowMapping:
    rows = connection.execute(
        text(
            """
            SELECT suite.research_suite_id,
                   suite.artifact_id AS research_suite_artifact_id,
                   suite.suite_fingerprint,suite.branch_count,suite.cell_count,
                   suite.compiled_research_graph_id,
                   suite.evaluation_matrix_policy_id,
                   suite.contract_version,suite.suite_mode,
                   suite_artifact.artifact_type AS suite_artifact_type,
                   suite_artifact.artifact_key AS suite_artifact_key,
                   suite_artifact.version_number AS suite_artifact_version,
                   suite_artifact.status AS suite_artifact_status,
                   suite_artifact.semantic_fingerprint
                     AS suite_artifact_semantic_fingerprint,
                   graph.artifact_id AS compiled_graph_artifact_id,
                   graph.graph_fingerprint,graph.catalog_release_id,graph.frequency,
                   graph.contract_version AS graph_contract_version,
                   graph.strategy_branch_count,graph.aggregation_instance_count,
                   graph.occurrence_count,graph_artifact.artifact_type
                     AS graph_artifact_type,
                   graph_artifact.artifact_key AS graph_artifact_key,
                   graph_artifact.version_number AS graph_artifact_version,
                   graph_artifact.status AS graph_artifact_status,
                   release.artifact_id AS catalog_release_artifact_id,
                   release.release_key,release.version_number AS release_version,
                   release.contract_version AS release_contract_version,
                   release_artifact.status AS catalog_release_artifact_status,
                   risk.compiled_execution_data_context_id,
                   risk.artifact_id AS execution_data_context_artifact_id,
                   risk.context_fingerprint AS execution_data_context_fingerprint,
                   risk.input_count AS execution_input_count,
                   risk.contract_version AS risk_contract_version,
                   risk_artifact.artifact_type AS risk_artifact_type,
                   risk_artifact.status AS risk_artifact_status,
                   cohort.evaluation_cohort_version_id,
                   cohort.artifact_id AS evaluation_cohort_artifact_id,
                   cohort.cohort_fingerprint AS evaluation_cohort_fingerprint,
                   cohort.research_tier AS evaluation_cohort_research_tier,
                   cohort.frequency AS evaluation_cohort_frequency,
                   cohort.warmup_start AS cohort_warmup_start,
                   cohort.evaluation_start AS cohort_evaluation_start,
                   cohort.evaluation_end AS cohort_evaluation_end,
                   cohort_artifact.status AS evaluation_cohort_artifact_status,
                   runtime_contract.evaluation_cohort_runtime_contract_id,
                   runtime_contract.runtime_fingerprint AS
                     evaluation_cohort_runtime_fingerprint,
                   runtime_contract.dataset_gate_assessment_id,
                   runtime_contract.dataset_gate_fingerprint,
                   runtime_contract.ranking_eligibility AS runtime_ranking_eligibility,
                   runtime_artifact.status AS cohort_runtime_artifact_status
              FROM experiment.v022_research_suite suite
              JOIN lineage.artifact suite_artifact
                ON suite_artifact.artifact_id=suite.artifact_id
              JOIN workspace.compiled_research_graph graph
                ON graph.compiled_research_graph_id=suite.compiled_research_graph_id
              JOIN lineage.artifact graph_artifact
                ON graph_artifact.artifact_id=graph.artifact_id
              JOIN workspace.v022_catalog_release release
                ON release.catalog_release_id=graph.catalog_release_id
              JOIN lineage.artifact release_artifact
                ON release_artifact.artifact_id=release.artifact_id
              JOIN workspace.v022_compiled_execution_data_context risk
                ON risk.compiled_research_graph_id=graph.compiled_research_graph_id
              JOIN lineage.artifact risk_artifact
                ON risk_artifact.artifact_id=risk.artifact_id
              LEFT JOIN experiment.v022_research_suite_evaluation_cohort_binding
                suite_cohort
                ON suite_cohort.research_suite_id=suite.research_suite_id
              LEFT JOIN experiment.v022_evaluation_cohort_version cohort
                ON cohort.evaluation_cohort_version_id=
                   suite_cohort.evaluation_cohort_version_id
              LEFT JOIN lineage.artifact cohort_artifact
                ON cohort_artifact.artifact_id=cohort.artifact_id
              LEFT JOIN experiment.v022_evaluation_cohort_runtime_contract runtime_contract
                ON runtime_contract.evaluation_cohort_version_id=
                   cohort.evaluation_cohort_version_id
              LEFT JOIN lineage.artifact runtime_artifact
                ON runtime_artifact.artifact_id=runtime_contract.artifact_id
             WHERE suite.research_suite_id=:suite
            """
        ),
        {"suite": request.research_suite_id},
    ).mappings().all()
    if len(rows) != 1:
        _fail(
            "v022_suite_runtime_identity_not_unique",
            "Runtime request requires exactly one immutable Suite closure",
            research_suite_id=request.research_suite_id,
            match_count=len(rows),
        )
    row = rows[0]
    if (
        row["contract_version"] != CONTRACT_VERSION
        or row["suite_mode"] != "exploratory"
        or row["suite_artifact_type"] != "v022_research_suite"
        or row["suite_artifact_version"] != 1
        or row["suite_artifact_status"] != "published"
        or row["suite_artifact_semantic_fingerprint"] is None
        or row["graph_contract_version"] != CONTRACT_VERSION
        or row["graph_artifact_type"] != "v022_compiled_research_graph"
        or row["graph_artifact_key"] != row["graph_fingerprint"]
        or row["graph_artifact_version"] != 1
        or row["graph_artifact_status"] != "published"
        or row["release_key"] != "bird_v022_catalog"
        or row["release_version"] != RUNTIME_CATALOG_VERSION
        or row["release_contract_version"] != CONTRACT_VERSION
        or row["catalog_release_artifact_status"] != "published"
        or row["risk_contract_version"] != CONTRACT_VERSION
        or row["risk_artifact_type"] != "v022_compiled_execution_data_context"
        or row["risk_artifact_status"] != "published"
        or row["branch_count"] != row["strategy_branch_count"]
        or row["branch_count"] < 1
        or row["cell_count"] < 1
        or row["aggregation_instance_count"] < 1
        or row["occurrence_count"] < 1
        or row["execution_input_count"] < 1
        or (
            row["evaluation_cohort_version_id"] is not None
            and (
                row["evaluation_cohort_artifact_status"] != "published"
                or row["evaluation_cohort_frequency"] != row["frequency"]
                or row["cohort_evaluation_start"] > row["cohort_evaluation_end"]
                 or row["cohort_warmup_start"] >= row["cohort_evaluation_start"]
                 or row["cohort_runtime_artifact_status"] != "published"
                 or row["evaluation_cohort_runtime_contract_id"] is None
                 or row["runtime_ranking_eligibility"]
                    != row["evaluation_cohort_research_tier"]
                 or not _is_hash(row["evaluation_cohort_runtime_fingerprint"])
                 or not _is_hash(row["dataset_gate_fingerprint"])
             )
        )
    ):
        _fail(
            "v022_suite_runtime_identity_invalid",
            "Suite, Graph, Catalog .6, or Risk Context is not exact and published",
        )
    if (
        request.evaluation_cohort_version_id
        != row["evaluation_cohort_version_id"]
    ):
        _fail(
            "v022_suite_evaluation_cohort_binding_mismatch",
            "Runtime request must name the exact Evaluation Cohort bound to the Suite",
        )
    if row["evaluation_cohort_version_id"] is not None:
        requested_start, requested_end = _strict_range(
            request.requested_range, label="requested"
        )
        if (
            requested_start != row["cohort_evaluation_start"]
            or requested_end != row["cohort_evaluation_end"]
        ):
            _fail(
                "v022_evaluation_cohort_range_mismatch",
                "Runtime requested range must exactly equal the bound Cohort evaluation range",
            )
    return row


def _load_runtime_payload_pins(
    connection: Connection, catalog_release_id: uuid.UUID
) -> RuntimePayloadPins:
    expected = {
        "strategy_unit_risk_target": "strategy",
        "defense_budget_decision": "defense",
        "merged_portfolio_target": "merge",
        "portfolio_cell_result": "cell",
    }
    contract_rows = connection.execute(
        text(
            """
            SELECT family.contract_key,version.payload_contract_version_id,
                   version.version_number,artifact.status,component.component_kind,
                   component.component_key,component.component_version,
                   component.component_fingerprint,artifact.semantic_fingerprint
              FROM workspace.v022_catalog_release_component component
              JOIN data.payload_contract_version version
                ON version.artifact_id=component.component_artifact_id
              JOIN data.payload_contract_family family
                ON family.payload_contract_family_id=version.payload_contract_family_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             WHERE component.catalog_release_id=:release
               AND component.component_kind='payload_contract_version'
               AND family.contract_key IN :keys
            """
        ).bindparams(bindparam("keys", expanding=True)),
        {"release": catalog_release_id, "keys": tuple(expected)},
    ).mappings().all()
    indexed: dict[str, uuid.UUID] = {}
    for row in contract_rows:
        key = row["contract_key"]
        if (
            key in indexed
            or row["version_number"] != 1
            or row["status"] != "published"
            or row["component_key"] != key
            or row["component_version"] != 1
            or row["component_fingerprint"] != row["semantic_fingerprint"]
        ):
            _fail(
                "v022_runtime_payload_pin_invalid",
                "Runtime payload contract is ambiguous or not an exact .6 component",
            )
        indexed[key] = row["payload_contract_version_id"]
    if set(indexed) != set(expected):
        _fail(
            "v022_runtime_payload_pin_incomplete",
            "Catalog .6 lacks the exact four runtime payload contracts",
        )
    encoding_rows = connection.execute(
        text(
            """
            SELECT encoding.physical_encoding_version_id,encoding.version_number,
                   artifact.status,component.component_key,
                   component.component_version,component.component_fingerprint,
                   artifact.semantic_fingerprint
              FROM workspace.v022_catalog_release_component component
              JOIN data.physical_encoding_version encoding
                ON encoding.artifact_id=component.component_artifact_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=encoding.artifact_id
             WHERE component.catalog_release_id=:release
               AND component.component_kind='physical_encoding_version'
               AND encoding.encoding_key='canonical_parquet'
            """
        ),
        {"release": catalog_release_id},
    ).mappings().all()
    if len(encoding_rows) != 1:
        _fail(
            "v022_runtime_encoding_pin_not_unique",
            "Catalog .6 requires exactly one canonical Parquet encoding",
        )
    encoding = encoding_rows[0]
    if (
        encoding["version_number"] != 1
        or encoding["status"] != "published"
        or encoding["component_key"] != "canonical_parquet"
        or encoding["component_version"] != 1
        or encoding["component_fingerprint"] != encoding["semantic_fingerprint"]
    ):
        _fail(
            "v022_runtime_encoding_pin_invalid",
            "Canonical Parquet encoding is not an exact published .6 component",
        )
    return RuntimePayloadPins(
        strategy_target_payload_contract_version_id=indexed[
            "strategy_unit_risk_target"
        ],
        defense_decision_payload_contract_version_id=indexed[
            "defense_budget_decision"
        ],
        sleeve_merge_payload_contract_version_id=indexed[
            "merged_portfolio_target"
        ],
        portfolio_cell_payload_contract_version_id=indexed["portfolio_cell_result"],
        physical_encoding_version_id=encoding["physical_encoding_version_id"],
    )


def _load_runtime_occurrences(
    connection: Connection, compiled_research_graph_id: uuid.UUID
) -> tuple[RuntimeOccurrence, ...]:
    rows = connection.execute(
        text(
            """
            SELECT occurrence.compiled_feature_occurrence_id,
                   occurrence.production_kind,occurrence.source_occurrence_id,
                   occurrence.compiled_graph_node_id,occurrence.output_port_key,
                   occurrence.feature_version_id,
                   occurrence.stage_no,feature.payload_contract_version_id,
                   variant.variant_key,feature_artifact.status AS feature_status,
                   component.component_artifact_id
              FROM workspace.compiled_feature_occurrence occurrence
              JOIN workspace.compiled_research_graph graph
                ON graph.compiled_research_graph_id=
                   occurrence.compiled_research_graph_id
              JOIN processing.feature_version feature
                ON feature.feature_version_id=occurrence.feature_version_id
              JOIN processing.feature_variant variant
                ON variant.feature_variant_id=feature.feature_variant_id
              JOIN lineage.artifact feature_artifact
                ON feature_artifact.artifact_id=feature.artifact_id
              LEFT JOIN workspace.v022_catalog_release_component component
                ON component.catalog_release_id=graph.catalog_release_id
               AND component.component_artifact_id=feature.artifact_id
               AND component.component_kind='feature_version'
             WHERE occurrence.compiled_research_graph_id=:graph
             ORDER BY occurrence.compiled_feature_occurrence_id
            """
        ),
        {"graph": compiled_research_graph_id},
    ).mappings().all()
    if not rows or any(
        row["feature_status"] != "published"
        or row["component_artifact_id"] is None
        or not row["variant_key"].strip()
        for row in rows
    ):
        _fail(
            "v022_runtime_occurrence_catalog_invalid",
            "Compiled occurrences are not exact published Graph Catalog components",
        )
    return tuple(
        RuntimeOccurrence(
            row["compiled_feature_occurrence_id"],
            row["production_kind"],
            row["payload_contract_version_id"],
            row["variant_key"],
            row["source_occurrence_id"],
            row["compiled_graph_node_id"],
            row["output_port_key"],
            row["feature_version_id"],
        )
        for row in rows
    )


def _load_runtime_aggregations(
    connection: Connection, compiled_research_graph_id: uuid.UUID
) -> tuple[RuntimeAggregation, ...]:
    rows = connection.execute(
        text(
            """
            SELECT instance.compiled_aggregation_instance_id,
                   instance.aggregation_version_id,
                   version.artifact_id AS aggregation_version_artifact_id,
                   instance.parameter_preset_version_id,
                   instance.target_version_id,
                   target.artifact_id AS target_version_artifact_id,
                   target.semantics AS target_semantics,
                   instance.training_preset_version_id,
                   training.artifact_id AS training_preset_version_artifact_id,
                   ensemble_binding.ensemble_spec_id,
                   ensemble.artifact_id AS ensemble_spec_artifact_id,
                   ensemble.ensemble_fingerprint,
                   ensemble.ensemble_document,
                   instance.instance_fingerprint,
                   instance.output_payload_contract_version_id,
                   version.execution_mode,version.implementation_key,
                   version.aggregation_family_id,
                   version_artifact.status AS version_status,
                   component.component_artifact_id,
                   CASE WHEN version.execution_mode='supervised' AND
                                  ensemble.ensemble_spec_id IS NOT NULL
                        THEN ensemble.ensemble_document
                        WHEN version.execution_mode='supervised'
                        THEN coalesce(training.semantics,'{}'::jsonb)
                        ELSE coalesce(preset.semantics,'{}'::jsonb)
                    END AS resolved_parameters,
                   preset_artifact.status AS preset_status,
                   target_artifact.status AS target_status,
                   target_definition.aggregation_family_id AS target_family_id,
                   target_component.component_artifact_id AS target_component_id,
                   training_artifact.status AS training_status,
                   training_definition.aggregation_family_id AS training_family_id,
                   training_component.component_artifact_id AS training_component_id,
                   ensemble_artifact.status AS ensemble_status
              FROM workspace.compiled_aggregation_instance instance
              JOIN workspace.compiled_research_graph graph
                ON graph.compiled_research_graph_id=
                   instance.compiled_research_graph_id
              JOIN aggregation.aggregation_version version
                ON version.aggregation_version_id=instance.aggregation_version_id
              JOIN lineage.artifact version_artifact
                ON version_artifact.artifact_id=version.artifact_id
              JOIN workspace.v022_catalog_release_component component
                ON component.catalog_release_id=graph.catalog_release_id
               AND component.component_artifact_id=version.artifact_id
               AND component.component_kind='aggregation_version'
              LEFT JOIN aggregation.parameter_preset_version preset
                ON preset.parameter_preset_version_id=
                   instance.parameter_preset_version_id
              LEFT JOIN lineage.artifact preset_artifact
                ON preset_artifact.artifact_id=preset.artifact_id
              LEFT JOIN aggregation.target_version target
                ON target.target_version_id=instance.target_version_id
              LEFT JOIN aggregation.target_definition target_definition
                ON target_definition.target_definition_id=
                   target.target_definition_id
              LEFT JOIN lineage.artifact target_artifact
                ON target_artifact.artifact_id=target.artifact_id
              LEFT JOIN workspace.v022_catalog_release_component target_component
                ON target_component.catalog_release_id=graph.catalog_release_id
               AND target_component.component_artifact_id=target.artifact_id
               AND target_component.component_kind='aggregation_target_version'
              LEFT JOIN aggregation.training_preset_version training
                ON training.training_preset_version_id=
                   instance.training_preset_version_id
              LEFT JOIN aggregation.training_preset_definition training_definition
                ON training_definition.training_preset_definition_id=
                   training.training_preset_definition_id
              LEFT JOIN lineage.artifact training_artifact
                ON training_artifact.artifact_id=training.artifact_id
              LEFT JOIN workspace.v022_catalog_release_component training_component
                ON training_component.catalog_release_id=graph.catalog_release_id
               AND training_component.component_artifact_id=training.artifact_id
               AND training_component.component_kind=
                   'aggregation_training_preset_version'
              LEFT JOIN workspace.v022_compiled_trainable_ensemble_binding
                ensemble_binding
                ON ensemble_binding.compiled_aggregation_instance_id=
                   instance.compiled_aggregation_instance_id
              LEFT JOIN aggregation.v022_trainable_ensemble_spec ensemble
                ON ensemble.ensemble_spec_id=ensemble_binding.ensemble_spec_id
              LEFT JOIN lineage.artifact ensemble_artifact
                ON ensemble_artifact.artifact_id=ensemble.artifact_id
             WHERE instance.compiled_research_graph_id=:graph
             ORDER BY instance.compiled_aggregation_instance_id
            """
        ),
        {"graph": compiled_research_graph_id},
    ).mappings().all()
    if not rows or any(not _runtime_aggregation_row_valid(row) for row in rows):
        _fail(
            "v022_runtime_aggregation_catalog_invalid",
            "Runtime Aggregations are not exact published Catalog components",
        )
    contract_count = connection.scalar(
        text(
            """
            SELECT count(*)
              FROM data.payload_contract_version version
              JOIN data.payload_contract_family family
                ON family.payload_contract_family_id=version.payload_contract_family_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
              JOIN workspace.v022_catalog_release_component component
                ON component.catalog_release_id=(
                  SELECT catalog_release_id FROM workspace.compiled_research_graph
                   WHERE compiled_research_graph_id=:graph
                )
               AND component.component_artifact_id=version.artifact_id
               AND component.component_kind='payload_contract_version'
             WHERE family.contract_key='final_signal_numeric'
               AND version.version_number=1 AND artifact.status='published'
            """
        ),
        {"graph": compiled_research_graph_id},
    )
    if contract_count != 1:
        _fail(
            "v022_runtime_aggregation_output_contract_invalid",
            "Graph Catalog requires one exact final_signal_numeric v1 contract",
        )
    valid_contracts = set(
        connection.scalars(
            text(
                """
                SELECT version.payload_contract_version_id
                  FROM data.payload_contract_version version
                  JOIN data.payload_contract_family family
                    ON family.payload_contract_family_id=
                       version.payload_contract_family_id
                  JOIN workspace.v022_catalog_release_component component
                    ON component.component_artifact_id=version.artifact_id
                   AND component.catalog_release_id=(
                     SELECT catalog_release_id FROM workspace.compiled_research_graph
                      WHERE compiled_research_graph_id=:graph
                   )
                 WHERE family.contract_key='final_signal_numeric'
                   AND version.version_number=1
                """
            ),
            {"graph": compiled_research_graph_id},
        )
    )
    if any(row["output_payload_contract_version_id"] not in valid_contracts for row in rows):
        _fail(
            "v022_runtime_aggregation_output_contract_invalid",
            "Compiled Aggregation output is not exact final_signal_numeric v1",
        )
    instance_ids = tuple(row["compiled_aggregation_instance_id"] for row in rows)
    member_rows = connection.execute(
        text(
            """
            SELECT binding.compiled_aggregation_instance_id,
                   member.ordinal,member.target_group_ordinal,
                   member.member_ordinal_within_target,
                   member.target_version_id,target.artifact_id AS target_artifact_id,
                   target_definition.target_key,target.semantics AS target_semantics,
                   target_artifact.status AS target_status,
                   target_definition.aggregation_family_id AS target_family_id,
                   target_component.component_artifact_id AS target_component_id,
                   member.training_preset_version_id,
                   training.artifact_id AS training_artifact_id,
                   training_definition.training_preset_key,
                   training.semantics AS training_semantics,
                   training_artifact.status AS training_status,
                   training_definition.aggregation_family_id AS training_family_id,
                   training_component.component_artifact_id AS training_component_id,
                   version.aggregation_family_id
              FROM workspace.v022_compiled_trainable_ensemble_binding binding
              JOIN workspace.compiled_aggregation_instance instance
                ON instance.compiled_aggregation_instance_id=
                   binding.compiled_aggregation_instance_id
              JOIN workspace.compiled_research_graph graph
                ON graph.compiled_research_graph_id=instance.compiled_research_graph_id
              JOIN aggregation.aggregation_version version
                ON version.aggregation_version_id=instance.aggregation_version_id
              JOIN aggregation.v022_trainable_ensemble_member member
                ON member.ensemble_spec_id=binding.ensemble_spec_id
              JOIN aggregation.target_version target
                ON target.target_version_id=member.target_version_id
              JOIN aggregation.target_definition target_definition
                ON target_definition.target_definition_id=target.target_definition_id
              JOIN lineage.artifact target_artifact
                ON target_artifact.artifact_id=target.artifact_id
              JOIN workspace.v022_catalog_release_component target_component
                ON target_component.catalog_release_id=graph.catalog_release_id
               AND target_component.component_artifact_id=target.artifact_id
               AND target_component.component_kind='aggregation_target_version'
              JOIN aggregation.training_preset_version training
                ON training.training_preset_version_id=
                   member.training_preset_version_id
              JOIN aggregation.training_preset_definition training_definition
                ON training_definition.training_preset_definition_id=
                   training.training_preset_definition_id
              JOIN lineage.artifact training_artifact
                ON training_artifact.artifact_id=training.artifact_id
              JOIN workspace.v022_catalog_release_component training_component
                ON training_component.catalog_release_id=graph.catalog_release_id
               AND training_component.component_artifact_id=training.artifact_id
               AND training_component.component_kind=
                   'aggregation_training_preset_version'
             WHERE binding.compiled_aggregation_instance_id IN :instances
             ORDER BY binding.compiled_aggregation_instance_id,member.ordinal
            """
        ).bindparams(bindparam("instances", expanding=True)),
        {"instances": instance_ids},
    ).mappings().all()
    grouped_members: dict[
        uuid.UUID, list[RuntimeAggregationEnsembleMember]
    ] = {item: [] for item in instance_ids}
    for member in member_rows:
        if (
            member["target_status"] != "published"
            or member["training_status"] != "published"
            or member["target_family_id"] != member["aggregation_family_id"]
            or member["training_family_id"] != member["aggregation_family_id"]
            or member["target_component_id"] is None
            or member["training_component_id"] is None
        ):
            _fail(
                "v022_runtime_aggregation_ensemble_member_invalid",
                "Trainable Ensemble member is not an exact published Catalog component",
            )
        grouped_members[member["compiled_aggregation_instance_id"]].append(
            RuntimeAggregationEnsembleMember(
                ordinal=member["ordinal"],
                target_group_ordinal=member["target_group_ordinal"],
                member_ordinal_within_target=member[
                    "member_ordinal_within_target"
                ],
                target_version_id=member["target_version_id"],
                target_version_artifact_id=member["target_artifact_id"],
                target_key=member["target_key"],
                target_semantics=member["target_semantics"],
                training_preset_version_id=member[
                    "training_preset_version_id"
                ],
                training_preset_version_artifact_id=member[
                    "training_artifact_id"
                ],
                training_preset_key=member["training_preset_key"],
                training_preset_semantics=member["training_semantics"],
            )
        )
    for row in rows:
        members = grouped_members[row["compiled_aggregation_instance_id"]]
        if row["ensemble_spec_id"] is None:
            if members:
                _fail(
                    "v022_runtime_aggregation_ensemble_member_invalid",
                    "Direct Aggregation cannot load internal Ensemble members",
                )
            continue
        document = row["ensemble_document"]
        expected_count = document.get("member_count") if isinstance(document, dict) else None
        if (
            not 2 <= len(members) <= 12
            or tuple(item.ordinal for item in members) != tuple(range(len(members)))
            or expected_count != len(members)
        ):
            _fail(
                "v022_runtime_aggregation_ensemble_member_invalid",
                "Trainable Ensemble member closure is incomplete or non-contiguous",
            )
    input_rows = connection.execute(
        text(
            """
            SELECT compiled_aggregation_instance_id,slot_key,ordinal,
                   compiled_feature_occurrence_id
              FROM workspace.compiled_aggregation_input
             WHERE compiled_aggregation_instance_id IN :instances
             ORDER BY compiled_aggregation_instance_id,slot_key,ordinal
            """
        ).bindparams(bindparam("instances", expanding=True)),
        {"instances": instance_ids},
    ).mappings().all()
    grouped: dict[uuid.UUID, list[RuntimeAggregationInput]] = {
        item: [] for item in instance_ids
    }
    for row in input_rows:
        grouped[row["compiled_aggregation_instance_id"]].append(
            RuntimeAggregationInput(
                row["slot_key"],
                row["ordinal"],
                row["compiled_feature_occurrence_id"],
            )
        )
    if any(not grouped[item] for item in instance_ids):
        _fail(
            "v022_aggregation_input_set_empty",
            "Every compiled Runtime Aggregation needs at least one exact input",
        )
    return tuple(
        RuntimeAggregation(
            row["compiled_aggregation_instance_id"],
            row["aggregation_version_id"],
            row["aggregation_version_artifact_id"],
            row["parameter_preset_version_id"],
            row["instance_fingerprint"],
            row["resolved_parameters"],
            row["output_payload_contract_version_id"],
            tuple(grouped[row["compiled_aggregation_instance_id"]]),
            execution_mode=row["execution_mode"],
            implementation_key=row["implementation_key"],
            target_version_id=row["target_version_id"],
            target_version_artifact_id=row["target_version_artifact_id"],
            target_semantics=row["target_semantics"],
            training_preset_version_id=row["training_preset_version_id"],
            training_preset_version_artifact_id=row[
                "training_preset_version_artifact_id"
            ],
            ensemble_spec_id=row["ensemble_spec_id"],
            ensemble_spec_artifact_id=row["ensemble_spec_artifact_id"],
            ensemble_fingerprint=row["ensemble_fingerprint"],
            ensemble_document=row["ensemble_document"],
            ensemble_members=tuple(
                grouped_members[row["compiled_aggregation_instance_id"]]
            ),
        )
        for row in rows
    )


def _runtime_aggregation_row_valid(row: RowMapping) -> bool:
    if row["version_status"] != "published":
        return False
    if row["execution_mode"] == "deterministic":
        return (
            row["target_version_id"] is None
            and row["training_preset_version_id"] is None
            and row["ensemble_spec_id"] is None
            and (
                row["parameter_preset_version_id"] is None
                or row["preset_status"] == "published"
            )
        )
    if row["execution_mode"] == "supervised":
        if row["ensemble_spec_id"] is not None:
            return (
                row["parameter_preset_version_id"] is None
                and row["target_version_id"] is None
                and row["training_preset_version_id"] is None
                and row["ensemble_spec_artifact_id"] is not None
                and row["ensemble_status"] == "published"
                and row["ensemble_fingerprint"] is not None
                and row["ensemble_document"] is not None
            )
        return (
            row["parameter_preset_version_id"] is None
            and row["target_version_id"] is not None
            and row["target_version_artifact_id"] is not None
            and row["target_status"] == "published"
            and row["target_family_id"] == row["aggregation_family_id"]
            and row["target_component_id"] is not None
            and row["training_preset_version_id"] is not None
            and row["training_preset_version_artifact_id"] is not None
            and row["training_status"] == "published"
            and row["training_family_id"] == row["aggregation_family_id"]
            and row["training_component_id"] is not None
            and row["ensemble_spec_id"] is None
        )
    return False


def _load_runtime_branches(
    connection: Connection, header: RowMapping
) -> tuple[RuntimeBranch, ...]:
    rows = connection.execute(
        text(
            """
            SELECT suite_branch.research_suite_branch_id,
                   suite_branch.compiled_strategy_branch_id,
                   suite_branch.configuration_snapshot_id,
                   suite_branch.branch_fingerprint,
                   branch.compiled_aggregation_instance_id,
                   branch.strategy_version_id,branch.defense_version_id,
                   snapshot.configuration_fingerprint,
                   snapshot_artifact.status AS snapshot_status,
                   preset.strategy_parameter_preset_version_id,
                   preset_artifact.status AS preset_status,
                   binding.compiled_execution_data_context_id,
                   binding.execution_data_context_artifact_id,
                   binding.execution_data_context_fingerprint,
                   binding.defense_package_artifact_id,
                   binding.timing_policy_version_id,
                   binding.allocation_policy_version_id,
                   binding.compiled_defense_execution_context_id,
                   binding.defense_execution_context_artifact_id,
                   binding.defense_execution_context_fingerprint,
                   experiment.v022_graph_uses_composed_defense(
                     branch.compiled_research_graph_id
                   ) AS uses_composed_defense,
                   defense_context_artifact.status AS defense_context_status,
                   package_artifact.status AS defense_package_status
              FROM experiment.v022_research_suite_branch suite_branch
              JOIN strategy.v022_compiled_strategy_branch branch
                ON branch.compiled_strategy_branch_id=
                   suite_branch.compiled_strategy_branch_id
              JOIN experiment.v022_research_configuration_snapshot snapshot
                ON snapshot.configuration_snapshot_id=
                   suite_branch.configuration_snapshot_id
              JOIN lineage.artifact snapshot_artifact
                ON snapshot_artifact.artifact_id=snapshot.artifact_id
              JOIN strategy.v022_compiled_strategy_branch_preset_binding preset
                ON preset.compiled_strategy_branch_id=
                   suite_branch.compiled_strategy_branch_id
              JOIN strategy.v022_strategy_parameter_preset_version preset_version
                ON preset_version.strategy_parameter_preset_version_id=
                   preset.strategy_parameter_preset_version_id
              JOIN lineage.artifact preset_artifact
                ON preset_artifact.artifact_id=preset_version.artifact_id
              LEFT JOIN experiment.v022_configuration_execution_context_binding binding
                ON binding.configuration_snapshot_id=
                   suite_branch.configuration_snapshot_id
              LEFT JOIN lineage.artifact defense_context_artifact
                ON defense_context_artifact.artifact_id=
                   binding.defense_execution_context_artifact_id
              LEFT JOIN lineage.artifact package_artifact
                ON package_artifact.artifact_id=binding.defense_package_artifact_id
             WHERE suite_branch.research_suite_id=:suite
             ORDER BY suite_branch.ordinal
            """
        ),
        {"suite": header["research_suite_id"]},
    ).mappings().all()
    if len(rows) != header["branch_count"]:
        _fail(
            "v022_runtime_branch_matrix_incomplete",
            "Suite Branch rows do not match the immutable branch count",
        )
    result: list[RuntimeBranch] = []
    for row in rows:
        if not _runtime_branch_risk_context_valid(row, header):
            _fail(
                "v022_runtime_branch_context_invalid",
                "Suite Branch Snapshot, Preset, or Risk Context is not exact and published",
            )
        if row["defense_version_id"] is None:
            defense = None
            if any(
                row[key] is not None
                for key in (
                    "defense_package_artifact_id",
                    "timing_policy_version_id",
                    "allocation_policy_version_id",
                    "compiled_defense_execution_context_id",
                    "defense_execution_context_artifact_id",
                    "defense_execution_context_fingerprint",
                )
            ):
                _fail(
                    "v022_runtime_branch_defense_context_invalid",
                    "No-defense Branch carries unexpected Defense Context identity",
                )
        else:
            if (
                row["defense_package_status"] != "published"
                or row["defense_context_status"] != "published"
                or any(
                    row[key] is None
                    for key in (
                        "timing_policy_version_id",
                        "allocation_policy_version_id",
                        "compiled_defense_execution_context_id",
                        "defense_execution_context_fingerprint",
                    )
                )
            ):
                _fail(
                    "v022_runtime_branch_defense_context_invalid",
                    "Defended Branch lacks its exact published Package Context",
                )
            defense = RuntimeDefenseBinding(
                row["defense_version_id"],
                row["timing_policy_version_id"],
                row["allocation_policy_version_id"],
                row["compiled_defense_execution_context_id"],
                row["defense_execution_context_fingerprint"],
            )
        result.append(
            RuntimeBranch(
                row["research_suite_branch_id"],
                row["compiled_strategy_branch_id"],
                row["configuration_snapshot_id"],
                row["compiled_aggregation_instance_id"],
                row["strategy_version_id"],
                row["strategy_parameter_preset_version_id"],
                row["branch_fingerprint"],
                row["configuration_fingerprint"],
                defense,
            )
        )
    return tuple(result)


def _runtime_branch_risk_context_valid(
    row: RowMapping | Mapping[str, object],
    header: RowMapping | Mapping[str, object],
) -> bool:
    if row["snapshot_status"] != "published" or row["preset_status"] != "published":
        return False
    binding_keys = (
        "compiled_execution_data_context_id",
        "execution_data_context_artifact_id",
        "execution_data_context_fingerprint",
    )
    if not row["uses_composed_defense"]:
        # Legacy/simple Defense Catalogs deliberately publish configuration
        # snapshots without an execution-context binding.  The Suite header
        # still freezes the exact risk context used by all runtime work.
        return all(row[key] is None for key in binding_keys)
    return (
        row["compiled_execution_data_context_id"]
        == header["compiled_execution_data_context_id"]
        and row["execution_data_context_artifact_id"]
        == header["execution_data_context_artifact_id"]
        and row["execution_data_context_fingerprint"]
        == header["execution_data_context_fingerprint"]
    )


def _load_runtime_cells(
    connection: Connection, header: RowMapping
) -> tuple[RuntimeCell, ...]:
    rows = connection.execute(
        text(
            """
            SELECT cell.research_cell_id,cell.research_suite_branch_id,
                   cell.evaluation_context_ordinal,
                   cell.evaluation_context_fingerprint,
                   context.portfolio_evaluation_data_context_id,
                   context.artifact_id,
                   context.context_fingerprint,context.coverage_start,
                   context.coverage_end,context_artifact.status AS context_status,
                   context.data_bundle_version_id,context.data_bundle_artifact_id,
                   bundle_artifact.status AS data_bundle_status,
                   context.artifact_semantic_fingerprint,
                   context_artifact.semantic_fingerprint,
                   count(input.ordinal) AS input_count
              FROM experiment.v022_research_cell cell
              JOIN experiment.v022_research_cell_evaluation_data_context_binding
                   context_binding
                ON context_binding.research_cell_id=cell.research_cell_id
              JOIN experiment.v022_portfolio_evaluation_data_context context
                ON context.portfolio_evaluation_data_context_id=
                   context_binding.portfolio_evaluation_data_context_id
               AND context.evaluation_matrix_policy_id=
                   cell.evaluation_matrix_policy_id
               AND context.evaluation_context_ordinal=
                   cell.evaluation_context_ordinal
              JOIN lineage.artifact context_artifact
                ON context_artifact.artifact_id=context.artifact_id
              JOIN lineage.artifact bundle_artifact
                ON bundle_artifact.artifact_id=context.data_bundle_artifact_id
              LEFT JOIN experiment.v022_portfolio_evaluation_data_input input
                ON input.portfolio_evaluation_data_context_id=
                   context.portfolio_evaluation_data_context_id
             WHERE cell.research_suite_id=:suite
             GROUP BY cell.research_cell_id,cell.research_suite_branch_id,
                      cell.evaluation_context_ordinal,
                      cell.evaluation_context_fingerprint,
                      context.portfolio_evaluation_data_context_id,
                      context.artifact_id,context.context_fingerprint,
                      context.coverage_start,context.coverage_end,
                      context_artifact.status,context.data_bundle_version_id,
                      context.data_bundle_artifact_id,bundle_artifact.status,
                      context.artifact_semantic_fingerprint,
                      context_artifact.semantic_fingerprint
             ORDER BY cell.ordinal
            """
        ),
        {"suite": header["research_suite_id"]},
    ).mappings().all()
    if len(rows) != header["cell_count"] or any(
        row["context_status"] != "published"
        or row["data_bundle_version_id"] is None
        or row["data_bundle_status"] != "published"
        or row["artifact_semantic_fingerprint"] != row["semantic_fingerprint"]
        or row["input_count"] != 2
        for row in rows
    ):
        _fail(
            "v022_runtime_cell_evaluation_context_invalid",
            "Every Cell requires one exact published Bundle-backed two-input "
            "Evaluation Data Context",
        )
    return tuple(
        RuntimeCell(
            row["research_cell_id"],
            row["research_suite_branch_id"],
            row["evaluation_context_ordinal"],
            row["evaluation_context_fingerprint"],
            row["portfolio_evaluation_data_context_id"],
            row["artifact_id"],
            row["context_fingerprint"],
            row["coverage_start"],
            row["coverage_end"],
        )
        for row in rows
    )


def _load_execution_input_coverage(
    connection: Connection, compiled_execution_data_context_id: uuid.UUID
) -> tuple[RowMapping, ...]:
    rows = tuple(
        connection.execute(
            text(
                """
                SELECT input.ordinal,input.coverage_start,input.coverage_end,
                       dataset_artifact.status AS dataset_status,
                       calendar_artifact.status AS calendar_status
                  FROM workspace.v022_compiled_execution_data_input input
                  JOIN lineage.artifact dataset_artifact
                    ON dataset_artifact.artifact_id=input.dataset_artifact_id
                  LEFT JOIN lineage.artifact calendar_artifact
                    ON calendar_artifact.artifact_id=input.calendar_artifact_id
                 WHERE input.compiled_execution_data_context_id=:context
                 ORDER BY input.ordinal
                """
            ),
            {"context": compiled_execution_data_context_id},
        ).mappings()
    )
    if not rows or any(
        row["dataset_status"] != "published"
        or (
            row["calendar_status"] is not None
            and row["calendar_status"] != "published"
        )
        for row in rows
    ):
        _fail(
            "v022_runtime_execution_data_context_invalid",
            "Risk Execution Data Context inputs are not exact and published",
        )
    return rows


def _required_terminal_occurrence_ids(
    occurrences: tuple[RuntimeOccurrence, ...],
    aggregations: tuple[RuntimeAggregation, ...],
) -> tuple[uuid.UUID, ...]:
    indexed = {
        item.compiled_feature_occurrence_id: item for item in occurrences
    }
    return tuple(
        sorted(
            {
                _terminal_occurrence(item.compiled_feature_occurrence_id, indexed)
                .compiled_feature_occurrence_id
                for aggregation in aggregations
                for item in aggregation.inputs
            }
        )
    )


def _load_exact_source_materializations(
    connection: Connection,
    *,
    header: RowMapping,
    request: SuiteRuntimePlanRequest,
    payload_pins: RuntimePayloadPins,
    occurrences: tuple[RuntimeOccurrence, ...],
    required_terminal_ids: tuple[uuid.UUID, ...],
) -> tuple[ExactSourceMaterialization, ...]:
    indexed = {
        item.compiled_feature_occurrence_id: item for item in occurrences
    }
    result: list[ExactSourceMaterialization] = []
    for occurrence_id in required_terminal_ids:
        occurrence = indexed[occurrence_id]
        if occurrence.production_kind == "raw_input":
            if occurrence.feature_version_id is None:
                _fail(
                    "v022_raw_input_feature_identity_missing",
                    "Raw occurrence has no exact Feature Version identity",
                )
            row = connection.execute(
                text(
                    """
                    SELECT manifest.payload_manifest_id,
                           manifest.artifact_id AS payload_manifest_artifact_id,
                           manifest.manifest_hash,
                           manifest.logical_payload_fingerprint,
                           manifest.payload_contract_version_id,
                           manifest.physical_encoding_version_id,
                           manifest.producer_artifact_id,
                           manifest.producer_output_port_key,
                           manifest.materialization_state,
                           manifest.coverage_document,
                           manifest.partition_count,manifest.byte_size,
                           manifest.row_or_item_count,
                           manifest_artifact.status AS manifest_artifact_status,
                           manifest_artifact.artifact_type AS manifest_artifact_type,
                           manifest_artifact.artifact_key AS manifest_artifact_key,
                           manifest_artifact.version_number AS manifest_artifact_version,
                           quality.quality_status,
                           count(link.payload_partition_id) OVER (
                             PARTITION BY manifest.payload_manifest_id
                           ) AS actual_partition_count,
                           coalesce(sum(partition.byte_size) OVER (
                             PARTITION BY manifest.payload_manifest_id
                           ),0) AS actual_byte_size,
                           coalesce(sum(partition.row_or_item_count) OVER (
                             PARTITION BY manifest.payload_manifest_id
                           ),0) AS actual_item_count,
                           min(link.ordinal) OVER (
                             PARTITION BY manifest.payload_manifest_id
                           ) AS minimum_ordinal,
                           max(link.ordinal) OVER (
                             PARTITION BY manifest.payload_manifest_id
                           ) AS maximum_ordinal,
                           bool_and(object.object_state='published' AND
                             object.verification_status='verified' AND
                             object.verified_at IS NOT NULL AND
                             partition.byte_size=object.byte_size) OVER (
                             PARTITION BY manifest.payload_manifest_id
                           ) AS objects_verified
                      FROM workspace.v022_compiled_execution_data_input input
                      JOIN data.v022_execution_context_payload_binding binding
                        ON binding.compiled_execution_data_context_id=
                           input.compiled_execution_data_context_id
                       AND binding.dataset_publication_id=input.dataset_publication_id
                       AND binding.feature_version_id=:feature_version
                      JOIN data.payload_manifest manifest
                        ON manifest.payload_manifest_id=binding.payload_manifest_id
                       AND manifest.producer_artifact_id=input.dataset_artifact_id
                      JOIN lineage.artifact manifest_artifact
                        ON manifest_artifact.artifact_id=manifest.artifact_id
                      JOIN data.payload_contract_version manifest_contract
                        ON manifest_contract.payload_contract_version_id=
                           manifest.payload_contract_version_id
                      JOIN workspace.v022_catalog_release_component contract_component
                        ON contract_component.catalog_release_id=:release
                       AND contract_component.component_artifact_id=
                           manifest_contract.artifact_id
                       AND contract_component.component_kind='payload_contract_version'
                      JOIN data.physical_encoding_version manifest_encoding
                        ON manifest_encoding.physical_encoding_version_id=
                           manifest.physical_encoding_version_id
                      JOIN workspace.v022_catalog_release_component encoding_component
                        ON encoding_component.catalog_release_id=:release
                       AND encoding_component.component_artifact_id=
                           manifest_encoding.artifact_id
                       AND encoding_component.component_kind='physical_encoding_version'
                      JOIN data.payload_quality_summary quality
                        ON quality.payload_manifest_id=manifest.payload_manifest_id
                      LEFT JOIN data.payload_manifest_partition link
                        ON link.payload_manifest_id=manifest.payload_manifest_id
                      LEFT JOIN data.payload_partition partition
                        ON partition.payload_partition_id=link.payload_partition_id
                      LEFT JOIN data.payload_object object
                        ON object.payload_object_id=partition.payload_object_id
                     WHERE input.compiled_execution_data_context_id=:context
                       AND input.input_key='canonical_market_bars'
                       AND manifest.payload_contract_version_id=:contract
                       AND manifest.physical_encoding_version_id=:encoding
                       AND manifest.materialization_state='materialized'
                       AND manifest_artifact.status='published'
                       AND quality.quality_status IN ('passed','warning')
                    """
                ),
                {
                    "feature_version": occurrence.feature_version_id,
                    "release": header["catalog_release_id"],
                    "context": header["compiled_execution_data_context_id"],
                    "contract": occurrence.payload_contract_version_id,
                    "encoding": payload_pins.physical_encoding_version_id,
                },
            ).mappings().one_or_none()
            if row is None:
                continue
            _validate_manifest_structure_row(row)
            coverage_start, coverage_end = _manifest_coverage(
                row["coverage_document"]
            )
            result.append(
                ExactSourceMaterialization(
                    terminal_occurrence_id=occurrence_id,
                    source_kind="raw_input",
                    payload_manifest_id=row["payload_manifest_id"],
                    payload_manifest_artifact_id=row[
                        "payload_manifest_artifact_id"
                    ],
                    manifest_hash=row["manifest_hash"],
                    logical_payload_fingerprint=row[
                        "logical_payload_fingerprint"
                    ],
                    payload_contract_version_id=row[
                        "payload_contract_version_id"
                    ],
                    physical_encoding_version_id=row[
                        "physical_encoding_version_id"
                    ],
                    producer_artifact_id=row["producer_artifact_id"],
                    producer_output_port_key=row["producer_output_port_key"],
                    manifest_artifact_status=row["manifest_artifact_status"],
                    materialization_state=row["materialization_state"],
                    quality_status=row["quality_status"],
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                )
            )
            continue
        if occurrence.production_kind != "node_output":
            _fail(
                "v022_runtime_occurrence_terminal_invalid",
                "Aggregation projection did not terminate in Raw or Node output",
            )
        rows = connection.execute(
            text(
                """
                SELECT DISTINCT manifest.payload_manifest_id,
                       manifest.artifact_id AS payload_manifest_artifact_id,
                       manifest.manifest_hash,
                       manifest.logical_payload_fingerprint,
                       manifest.payload_contract_version_id,
                       manifest.physical_encoding_version_id,
                       manifest.producer_artifact_id,
                       manifest.producer_output_port_key,
                       manifest.materialization_state,
                       manifest.coverage_document,
                       manifest.partition_count,manifest.byte_size,
                       manifest.row_or_item_count,
                       manifest_artifact.status AS manifest_artifact_status,
                       manifest_artifact.artifact_type AS manifest_artifact_type,
                       manifest_artifact.artifact_key AS manifest_artifact_key,
                       manifest_artifact.version_number AS manifest_artifact_version,
                       quality.quality_status,node.node_run_id,node.status
                         AS node_run_status,node.execution_fingerprint
                         AS node_execution_fingerprint,
                       work.graph_work_item_id,work.status AS graph_work_status,
                       node_binding.compiled_graph_node_id,
                       count(link.payload_partition_id) OVER (
                         PARTITION BY manifest.payload_manifest_id
                       ) AS actual_partition_count,
                       coalesce(sum(partition.byte_size) OVER (
                         PARTITION BY manifest.payload_manifest_id
                       ),0) AS actual_byte_size,
                       coalesce(sum(partition.row_or_item_count) OVER (
                         PARTITION BY manifest.payload_manifest_id
                       ),0) AS actual_item_count,
                       min(link.ordinal) OVER (
                         PARTITION BY manifest.payload_manifest_id
                       ) AS minimum_ordinal,
                       max(link.ordinal) OVER (
                         PARTITION BY manifest.payload_manifest_id
                       ) AS maximum_ordinal,
                       bool_and(object.object_state='published' AND
                         object.verification_status='verified' AND
                         object.verified_at IS NOT NULL AND
                         partition.byte_size=object.byte_size) OVER (
                         PARTITION BY manifest.payload_manifest_id
                       ) AS objects_verified
                  FROM (
                    SELECT DISTINCT node_run_id,compiled_graph_node_id,
                                    graph_work_item_id
                      FROM processing.graph_run_node_binding
                  ) node_binding
                  JOIN processing.node_run node
                    ON node.node_run_id=node_binding.node_run_id
                  JOIN processing.node_run_output output
                    ON output.node_run_id=node.node_run_id
                   AND output.output_port_key=:output_port
                  JOIN data.payload_manifest manifest
                    ON manifest.payload_manifest_id=output.payload_manifest_id
                  JOIN lineage.artifact manifest_artifact
                    ON manifest_artifact.artifact_id=manifest.artifact_id
                  JOIN lineage.artifact node_artifact
                    ON node_artifact.artifact_id=node.artifact_id
                   AND manifest.producer_artifact_id=node.artifact_id
                   AND node_artifact.status='published'
                  JOIN data.payload_contract_version manifest_contract
                    ON manifest_contract.payload_contract_version_id=
                       manifest.payload_contract_version_id
                  JOIN workspace.v022_catalog_release_component contract_component
                    ON contract_component.catalog_release_id=:release
                   AND contract_component.component_artifact_id=
                       manifest_contract.artifact_id
                   AND contract_component.component_kind='payload_contract_version'
                  JOIN data.physical_encoding_version manifest_encoding
                    ON manifest_encoding.physical_encoding_version_id=
                       manifest.physical_encoding_version_id
                  JOIN workspace.v022_catalog_release_component encoding_component
                    ON encoding_component.catalog_release_id=:release
                   AND encoding_component.component_artifact_id=
                       manifest_encoding.artifact_id
                   AND encoding_component.component_kind='physical_encoding_version'
                  JOIN data.payload_quality_summary quality
                    ON quality.payload_manifest_id=manifest.payload_manifest_id
                  LEFT JOIN data.payload_manifest_partition link
                    ON link.payload_manifest_id=manifest.payload_manifest_id
                  LEFT JOIN data.payload_partition partition
                    ON partition.payload_partition_id=link.payload_partition_id
                  LEFT JOIN data.payload_object object
                    ON object.payload_object_id=partition.payload_object_id
                  JOIN workspace.v022_graph_work_item work
                    ON work.graph_work_item_id=node_binding.graph_work_item_id
                   AND work.execution_fingerprint=node.execution_fingerprint
                   AND work.work_kind='node'
                 WHERE node_binding.compiled_graph_node_id=:node
                   AND node.requested_range=CAST(:requested_range AS jsonb)
                   AND node.executor_version=:executor
                   AND node.environment_fingerprint=:environment
                   AND node.status='completed'
                   AND work.status IN ('completed','reused')
                   AND manifest.payload_contract_version_id=:contract
                   AND manifest.physical_encoding_version_id=:encoding
                   AND manifest.materialization_state='materialized'
                   AND manifest_artifact.status='published'
                   AND quality.quality_status IN ('passed','warning')
                """
            ),
            {
                "node": occurrence.compiled_graph_node_id,
                "output_port": occurrence.output_port_key,
                "requested_range": _plain_json(
                    request.materialization_range or request.requested_range
                ),
                "executor": (
                    request.source_executor_version or request.executor_version
                ),
                "environment": (
                    request.source_environment_fingerprint
                    or request.environment_fingerprint
                ),
                "contract": occurrence.payload_contract_version_id,
                "encoding": payload_pins.physical_encoding_version_id,
                "release": header["catalog_release_id"],
            },
        ).mappings().all()
        if len(rows) == 0:
            continue
        if len(rows) != 1:
            _fail(
                "v022_processing_materialization_ambiguous",
                "Node output preflight resolved more than one exact materialization",
                occurrence_id=occurrence_id,
                match_count=len(rows),
            )
        row = rows[0]
        _validate_manifest_structure_row(
            row,
            expected_artifact_key=(
                f"node-output:{row['node_run_id']}:"
                f"{row['producer_output_port_key']}"
            ),
        )
        coverage_start, coverage_end = _manifest_coverage(
            row["coverage_document"]
        )
        result.append(
            ExactSourceMaterialization(
                terminal_occurrence_id=occurrence_id,
                source_kind="node_output",
                payload_manifest_id=row["payload_manifest_id"],
                payload_manifest_artifact_id=row[
                    "payload_manifest_artifact_id"
                ],
                manifest_hash=row["manifest_hash"],
                logical_payload_fingerprint=row[
                    "logical_payload_fingerprint"
                ],
                payload_contract_version_id=row[
                    "payload_contract_version_id"
                ],
                physical_encoding_version_id=row[
                    "physical_encoding_version_id"
                ],
                producer_artifact_id=row["producer_artifact_id"],
                producer_output_port_key=row["producer_output_port_key"],
                manifest_artifact_status=row["manifest_artifact_status"],
                materialization_state=row["materialization_state"],
                quality_status=row["quality_status"],
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                compiled_graph_node_id=row["compiled_graph_node_id"],
                node_run_id=row["node_run_id"],
                graph_work_item_id=row["graph_work_item_id"],
                node_execution_fingerprint=row[
                    "node_execution_fingerprint"
                ],
                node_run_status=row["node_run_status"],
                graph_work_status=row["graph_work_status"],
            )
        )
    return tuple(result)


def _validate_manifest_structure_row(
    row: Mapping[str, object] | RowMapping,
    *,
    expected_artifact_key: str | None = None,
) -> None:
    partition_count = row.get("partition_count")
    if (
        row.get("manifest_artifact_type") != "v022_payload_manifest"
        or row.get("manifest_artifact_version") != 1
        or not str(row.get("manifest_artifact_key") or "").strip()
        or (
            expected_artifact_key is not None
            and row.get("manifest_artifact_key") != expected_artifact_key
        )
        or not isinstance(partition_count, int)
        or isinstance(partition_count, bool)
        or partition_count < 1
        or row.get("actual_partition_count") != partition_count
        or row.get("actual_byte_size") != row.get("byte_size")
        or row.get("actual_item_count") != row.get("row_or_item_count")
        or row.get("minimum_ordinal") != 0
        or row.get("maximum_ordinal") != partition_count - 1
        or row.get("objects_verified") is not True
    ):
        _fail(
            "v022_processing_manifest_structure_invalid",
            "Node Payload Manifest partitions and verified objects are incomplete",
            payload_manifest_id=row.get("payload_manifest_id"),
        )


def _exact_runtime_range(
    requested_range: dict[str, object],
    *,
    cohort_warmup_range: dict[str, object] | None,
    execution_inputs: tuple[tuple[date, date], ...],
    cells: tuple[RuntimeCell, ...],
    materializations: tuple[ExactSourceMaterialization, ...],
) -> dict[str, object]:
    requested_start, requested_end = _strict_range(
        requested_range, label="requested"
    )
    required_input_start = requested_start
    if cohort_warmup_range is not None:
        warmup_start, warmup_end = _strict_range(
            cohort_warmup_range, label="cohort_warmup"
        )
        if warmup_end != requested_end or warmup_start >= requested_start:
            _fail(
                "v022_evaluation_cohort_range_invalid",
                "Evaluation Cohort warmup and evaluation boundaries are inconsistent",
            )
        required_input_start = warmup_start
    insufficient_inputs = [
        (start, end)
        for start, end in execution_inputs
        if start > required_input_start or end < requested_end
    ]
    if insufficient_inputs:
        _fail(
            "v022_evaluation_input_coverage_insufficient",
            "Execution inputs do not cover the frozen warmup and evaluation interval",
            required_start=required_input_start,
            required_end=requested_end,
            insufficient_count=len(insufficient_inputs),
        )
    insufficient_cells = [
        item
        for item in cells
        if item.evaluation_data_coverage_start > requested_start
        or item.evaluation_data_coverage_end < requested_end
    ]
    if insufficient_cells:
        _fail(
            "v022_evaluation_context_coverage_insufficient",
            "Portfolio evaluation inputs do not cover the frozen evaluation interval",
            required_start=requested_start,
            required_end=requested_end,
            insufficient_count=len(insufficient_cells),
        )
    insufficient_sources = [
        item
        for item in materializations
        if item.coverage_start > requested_start or item.coverage_end < requested_end
    ]
    if insufficient_sources:
        _fail(
            "v022_source_materialization_coverage_insufficient",
            "Source materializations do not cover the frozen evaluation interval",
            required_start=requested_start,
            required_end=requested_end,
            insufficient_count=len(insufficient_sources),
        )
    return {
        "start": requested_start.isoformat(),
        "end": requested_end.isoformat(),
    }


def _manifest_coverage(value: object) -> tuple[date, date]:
    if not isinstance(value, Mapping) or set(value) != {
        "start",
        "end",
        "session_count",
    }:
        _fail(
            "v022_processing_manifest_coverage_invalid",
            "Node Payload Manifest coverage is not canonical",
        )
    raw_start = value.get("start")
    raw_end = value.get("end")
    count = value.get("session_count")
    if (
        not isinstance(raw_start, str)
        or not isinstance(raw_end, str)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 1
    ):
        _fail(
            "v022_processing_manifest_coverage_invalid",
            "Node Payload Manifest coverage has invalid boundaries or count",
        )
    try:
        start = date.fromisoformat(raw_start)
        end = date.fromisoformat(raw_end)
    except ValueError:
        _fail(
            "v022_processing_manifest_coverage_invalid",
            "Node Payload Manifest coverage boundaries are not ISO dates",
        )
    if raw_start != start.isoformat() or raw_end != end.isoformat() or start > end:
        _fail(
            "v022_processing_manifest_coverage_invalid",
            "Node Payload Manifest coverage is inverted or noncanonical",
        )
    return start, end


def _plain_json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _new_work(
    request: SuiteRuntimePlanRequest,
    kind: WorkKind,
    occurrence_key: str,
    semantic: Mapping[str, object],
    upstream: tuple[str, ...],
    *,
    priority: int,
) -> RuntimeWorkBlueprint:
    fingerprint = sha256_hexdigest(
        {
            "work_semantic_identity": semantic,
            "requested_range": request.requested_range,
            "evaluation_cohort_version_id": (
                str(request.evaluation_cohort_version_id)
                if request.evaluation_cohort_version_id is not None
                else None
            ),
            "executor_version": request.executor_version,
            "environment_fingerprint": request.environment_fingerprint,
        }
    )
    return RuntimeWorkBlueprint(
        kind,
        occurrence_key,
        fingerprint,
        upstream,
        dict(semantic),
        priority,
    )


def _terminal_occurrence(
    occurrence_id: uuid.UUID,
    occurrences: dict[uuid.UUID, RuntimeOccurrence],
) -> RuntimeOccurrence:
    seen: set[uuid.UUID] = set()
    current_id = occurrence_id
    while True:
        if current_id in seen:
            _fail(
                "v022_runtime_occurrence_projection_cycle",
                "Compiled layer projection chain contains a cycle",
            )
        seen.add(current_id)
        current = occurrences.get(current_id)
        if current is None:
            _fail(
                "v022_runtime_occurrence_missing",
                "Aggregation input references an unknown compiled occurrence",
                occurrence_id=current_id,
            )
        if current.production_kind != "layer_projection":
            return current
        if current.source_occurrence_id is None:
            _fail(
                "v022_runtime_occurrence_projection_invalid",
                "Layer projection has no exact source occurrence",
            )
        current_id = current.source_occurrence_id


def _validate_materialization(
    terminal: RuntimeOccurrence,
    binding: ExactSourceMaterialization,
) -> None:
    if binding.source_kind != terminal.production_kind:
        _fail(
            "v022_source_materialization_kind_mismatch",
            "Source materialization does not match its terminal occurrence kind",
        )
    reason = (
        "v022_raw_input_manifest_binding_invalid"
        if terminal.production_kind == "raw_input"
        else "v022_processing_materialization_invalid"
    )
    if (
        binding.manifest_artifact_status != "published"
        or binding.materialization_state != "materialized"
        or binding.quality_status not in {"passed", "warning"}
        or not _is_hash(binding.manifest_hash)
        or not _is_hash(binding.logical_payload_fingerprint)
        or not binding.producer_output_port_key.strip()
        or binding.payload_contract_version_id != terminal.payload_contract_version_id
    ):
        _fail(reason, "Source Payload Manifest is not exact, published, and materialized")
    if terminal.production_kind == "raw_input":
        if any(
            value is not None
            for value in (
                binding.compiled_graph_node_id,
                binding.node_run_id,
                binding.graph_work_item_id,
                binding.node_execution_fingerprint,
                binding.node_run_status,
                binding.graph_work_status,
            )
        ):
            _fail(reason, "Raw source materialization cannot impersonate a Node output")
        return
    if (
        binding.compiled_graph_node_id is None
        or binding.compiled_graph_node_id != terminal.compiled_graph_node_id
        or binding.node_run_id is None
        or binding.graph_work_item_id is None
        or binding.node_execution_fingerprint is None
        or not _is_hash(binding.node_execution_fingerprint)
        or binding.node_run_status != "completed"
        or binding.graph_work_status not in {"completed", "reused"}
        or binding.producer_output_port_key != terminal.output_port_key
    ):
        _fail(reason, "Node source is not backed by its exact completed reusable output")


def _validate_request(
    request: SuiteRuntimePlanRequest,
    facts: VerifiedSuiteRuntimeFacts,
) -> tuple[date, date]:
    if request.research_suite_id != facts.research_suite_id:
        _fail(
            "v022_suite_runtime_identity_mismatch",
            "Runtime request and preflight facts refer to different Suites",
        )
    if not request.requested_by.strip() or not request.executor_version.strip():
        _fail(
            "v022_suite_runtime_request_invalid",
            "Runtime actor and executor version must be nonblank",
        )
    requested_start, requested_end = _strict_range(
        request.requested_range, label="requested"
    )
    effective_start, effective_end = _strict_range(
        facts.effective_range, label="effective"
    )
    if effective_start != requested_start or effective_end != requested_end:
        _fail(
            "v022_suite_runtime_effective_range_mismatch",
            "Effective range must exactly equal the frozen requested range",
            requested_start=requested_start,
            requested_end=requested_end,
            effective_start=effective_start,
            effective_end=effective_end,
        )
    for value in (
        request.environment_fingerprint,
        request.source_environment_fingerprint or request.environment_fingerprint,
        facts.suite_fingerprint,
        facts.graph_fingerprint,
        facts.execution_data_context_fingerprint,
    ):
        if not _is_hash(value):
            _fail(
                "v022_suite_runtime_fingerprint_invalid",
                "Runtime immutable fingerprints must be lowercase SHA-256 values",
            )
    if facts.evaluation_cohort_version_id is not None:
        if (
            request.evaluation_cohort_version_id
            != facts.evaluation_cohort_version_id
            or facts.evaluation_cohort_research_tier
            not in {"rankable_research", "exploratory_only"}
            or facts.evaluation_cohort_frequency not in {"weekly", "monthly"}
            or facts.evaluation_cohort_fingerprint is None
            or not _is_hash(facts.evaluation_cohort_fingerprint)
            or facts.evaluation_cohort_runtime_contract_id is None
            or facts.evaluation_cohort_runtime_fingerprint is None
            or not _is_hash(facts.evaluation_cohort_runtime_fingerprint)
            or facts.dataset_gate_assessment_id is None
            or facts.dataset_gate_fingerprint is None
            or not _is_hash(facts.dataset_gate_fingerprint)
        ):
            _fail(
                "v022_evaluation_cohort_identity_invalid",
                "Runtime requires one exact published Evaluation Cohort identity",
            )
        if (
            request.materialization_range is None
            or request.materialization_range
            != facts.evaluation_cohort_warmup_range
        ):
            _fail(
                "v022_evaluation_cohort_materialization_range_mismatch",
                "Source materialization must use the exact Cohort warmup interval",
            )
    if facts.expected_branch_count < 1 or facts.expected_cell_count < 1:
        _fail(
            "v022_suite_runtime_matrix_empty",
            "Immutable Suite must contain at least one Branch and Cell",
        )
    return effective_start, effective_end


def _strict_range(value: dict[str, object], *, label: str) -> tuple[date, date]:
    if set(value) != {"start", "end"}:
        _fail(
            "v022_suite_runtime_range_invalid",
            f"{label.capitalize()} range must contain exactly start and end",
            range_label=label,
        )
    raw_start = value.get("start")
    raw_end = value.get("end")
    if not isinstance(raw_start, str) or not isinstance(raw_end, str):
        _fail(
            "v022_suite_runtime_range_invalid",
            f"{label.capitalize()} range boundaries must be ISO date strings",
            range_label=label,
        )
    try:
        start = date.fromisoformat(raw_start)
        end = date.fromisoformat(raw_end)
    except ValueError:
        _fail(
            "v022_suite_runtime_range_invalid",
            f"{label.capitalize()} range boundaries are not valid ISO dates",
            range_label=label,
        )
    if raw_start != start.isoformat() or raw_end != end.isoformat() or start > end:
        _fail(
            "v022_suite_runtime_range_invalid",
            f"{label.capitalize()} range is not canonical and ordered",
            range_label=label,
        )
    return start, end


def _validate_work_graph(work: list[RuntimeWorkBlueprint]) -> None:
    keys = {item.occurrence_key for item in work}
    if len(keys) != len(work):
        _fail(
            "v022_suite_runtime_occurrence_key_duplicate",
            "One Graph Run cannot bind the same occurrence key twice",
        )
    fingerprints = {item.execution_fingerprint for item in work}
    if len(fingerprints) != len(work):
        _fail(
            "v022_suite_runtime_work_alias_unsupported",
            "One Graph Run cannot bind one global Work Item to multiple occurrences",
        )
    available: set[str] = set()
    for item in work:
        if any(key not in available for key in item.required_upstream_keys):
            _fail(
                "v022_suite_runtime_topology_invalid",
                "Runtime Work is not in a closed topological order",
            )
        available.add(item.occurrence_key)


def _unique_by_id[T, K](
    values: tuple[T, ...],
    *,
    key: Callable[[T], K],
    reason: str,
) -> dict[K, T]:
    keyed: dict[K, T] = {}
    for value in values:
        identity = key(value)
        if identity in keyed:
            _fail(reason, "Immutable runtime identity appears more than once")
        keyed[identity] = value
    return keyed


def _is_hash(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _fail(reason: str, message: str, **details: object) -> NoReturn:
    raise V022RuntimeContractError(reason, message, details=dict(details))
