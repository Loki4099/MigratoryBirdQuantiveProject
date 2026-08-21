from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ArtifactStatus = Literal["draft", "published", "retired", "superseded", "invalidated", "tainted"]
QualityState = Literal["ok", "partial", "warning", "error"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QualitySummary(ApiModel):
    state: QualityState
    codes: list[str] = Field(default_factory=list)


class ApiContext(ApiModel):
    api_version: Literal["v2"] = "v2"
    system_version: str
    read_only: bool = False


class HealthResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    database_revision: str | None


class V022ReleaseControlResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    state: Literal["hidden", "shadow", "explicit_eligible", "default", "maintenance_read_only"]
    transition_sequence: int
    transition_artifact_id: UUID | None
    default_contract: Literal["v0.21", "v0.22"]
    maintenance_read_only: bool
    shadow_runtime_allowed: bool
    v021_research_creation_allowed: bool
    v022_explicit_creation_allowed: bool


class SessionContextResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    actor_key: str
    roles: list[Literal["researcher", "operator"]]
    authentication_source: str


class V022ExperimentIdentityItem(ApiModel):
    result_evidence_snapshot_id: UUID
    evidence_artifact_id: UUID
    result_artifact_id: UUID
    evidence_class: str
    configuration_snapshot_id: UUID
    configuration_fingerprint: str
    configuration: dict[str, Any]
    display: dict[str, Any]
    created_at: datetime


class V022ExperimentIdentityCatalogResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    items: list[V022ExperimentIdentityItem]


class V022ExperimentComparisonContext(ApiModel):
    evaluation_cohort_version_id: UUID
    evaluation_cohort_fingerprint: str
    cohort_key: str
    frequency: Literal["weekly", "monthly"]
    warmup_start: date
    evaluation_start: date
    evaluation_end: date
    benchmark_key: Literal["spy"]
    cost_bps_per_side: str
    execution_delay_sessions: int = Field(ge=0)
    price_semantics: str


class V022LeaderboardContext(V022ExperimentComparisonContext):
    ranking_cohort_release_id: UUID
    ranking_cohort_artifact_id: UUID
    ranking_version_number: int = Field(ge=1)
    member_count: int = Field(ge=1)


class V022LeaderboardRow(ApiModel):
    rank: int = Field(ge=1)
    result_evidence_snapshot_id: UUID
    result_artifact_id: UUID
    configuration_snapshot_id: UUID
    configuration_fingerprint: str
    configuration: dict[str, Any]
    display: dict[str, Any]
    cagr: str
    benchmark_cagr: str
    cagr_spread: str
    sharpe_ratio: str
    maximum_drawdown: str
    product_candidate: bool
    product_definition_id: UUID | None
    execution_version_id: UUID | None
    product_enrollment_id: UUID | None


class V022ExperimentLeaderboardResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    comparison_context: V022LeaderboardContext
    available_frequencies: list[Literal["weekly", "monthly"]]
    sort: Literal["sharpe_ratio", "cagr", "cagr_spread", "maximum_drawdown"]
    total: int = Field(ge=1)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    rows: list[V022LeaderboardRow]


class V022ExperimentIdentityDetailResponse(V022ExperimentIdentityItem):
    context: ApiContext
    quality: QualitySummary
    comparison_context: V022ExperimentComparisonContext | None
    outcome: str
    quality_status: str
    effective_start: date
    effective_end: date
    core_metrics: dict[str, str | None]
    metrics: dict[str, Any]
    product: dict[str, Any]
    evidence: dict[str, Any]
    evidence_quality: dict[str, Any]
    comparisons: list[dict[str, Any]]
    matched_baselines: list[dict[str, Any]]


class V022ExperimentSeriesPoint(ApiModel):
    session_date: date
    strategy_nav: str
    benchmark_nav: str
    excess_nav: str
    drawdown: str


class V022ExperimentSeriesResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    result_evidence_snapshot_id: UUID
    effective_start: date
    effective_end: date
    total_points: int = Field(ge=1)
    returned_points: int = Field(ge=1)
    points: list[V022ExperimentSeriesPoint] = Field(min_length=1)


class V022ProductCandidateRequest(ApiModel):
    idempotency_key: UUID
    researcher_id: str = Field(min_length=1, max_length=120)
    product_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,119}$")
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=2000)
    version_number: int = Field(default=1, ge=1)


class V022ProductCandidateResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    result_evidence_snapshot_id: UUID
    product_definition_id: UUID
    product_definition_artifact_id: UUID
    execution_version_id: UUID
    execution_version_artifact_id: UUID
    qualification_version_id: UUID
    qualification_version_artifact_id: UUID
    monitoring_policy_version_id: UUID
    monitoring_policy_version_artifact_id: UUID
    version_number: int = Field(ge=1)
    lifecycle: Literal["candidate"]
    reused: bool


class V022ProductPromotionResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    result_evidence_snapshot_id: UUID
    product_definition_id: UUID
    product_definition_artifact_id: UUID
    execution_version_id: UUID
    execution_version_artifact_id: UUID
    qualification_version_id: UUID
    qualification_version_artifact_id: UUID
    monitoring_policy_version_id: UUID
    monitoring_policy_version_artifact_id: UUID
    product_data_disclosure_id: UUID
    product_data_disclosure_artifact_id: UUID
    product_data_disclosure_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    product_eligibility: Literal["eligible", "eligible_with_warnings"]
    warning_codes: list[str]
    product_enrollment_id: UUID
    enrollment_artifact_id: UUID
    decision_schedule_version_id: UUID
    decision_schedule_artifact_id: UUID
    first_eligible_decision_session_id: UUID
    product_ensemble_state_id: UUID | None = None
    product_ensemble_state_artifact_id: UUID | None = None
    product_ensemble_state_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    version_number: int = Field(ge=1)
    lifecycle: Literal["active"]
    reused: bool


class V022DecisionSessionInput(ApiModel):
    session_date: date
    decision_cutoff_at: datetime


class V022ProductEnrollmentRequest(ApiModel):
    idempotency_key: UUID
    researcher_id: str = Field(min_length=1, max_length=120)
    qualification_version_id: UUID
    monitoring_policy_version_id: UUID
    schedule_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,119}$")
    schedule_version_number: int = Field(default=1, ge=1)
    frequency: Literal["weekly", "monthly"]
    sessions: list[V022DecisionSessionInput] = Field(min_length=1)
    oos_anchor_cutoff_at: datetime
    activation_effective_at: datetime


class V022ProductEnrollmentResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    product_enrollment_id: UUID
    enrollment_artifact_id: UUID
    decision_schedule_version_id: UUID
    decision_schedule_artifact_id: UUID
    first_eligible_decision_session_id: UUID
    lifecycle: Literal["active"]
    reused: bool


class V022ProductLifecycleRequest(ApiModel):
    idempotency_key: UUID
    researcher_id: str = Field(min_length=1, max_length=120)
    expected_sequence: int = Field(ge=1)
    target: Literal["active", "suspended", "retired", "invalidated"]
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    reason: str = Field(min_length=1, max_length=2000)
    requested_at: datetime
    effective_at: datetime


class V022ProductLifecycleResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    product_enrollment_id: UUID
    enrollment_lifecycle_event_id: UUID
    artifact_id: UUID
    sequence_number: int = Field(ge=1)
    from_lifecycle: str
    to_lifecycle: str
    event_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    reused: bool


class V022ProductIdentityItem(ApiModel):
    product_enrollment_id: UUID
    enrollment_artifact_id: UUID
    product_key: str
    name: str
    execution_version_number: int
    execution_fingerprint: str
    source_result_evidence_snapshot_id: UUID
    configuration_snapshot_id: UUID
    configuration_fingerprint: str
    configuration: dict[str, Any]
    display: dict[str, Any]
    lifecycle: str
    health: str
    first_eligible_decision_session_id: UUID
    frequency: Literal["weekly", "monthly"]
    first_eligible_decision_session: date
    next_pending_decision_session: date | None
    next_pending_decision_cutoff_at: datetime | None
    decision_pipeline_state: Literal[
        "inactive",
        "scheduled",
        "waiting_for_input",
        "input_prepared",
        "runtime_published",
        "schedule_complete",
    ]
    next_product_input_snapshot_id: UUID | None
    next_product_input_available_at: datetime | None
    next_product_runtime_execution_id: UUID | None
    decision_count: int = Field(ge=0)
    completed_decision_count: int = Field(ge=0)
    missing_decision_count: int = Field(ge=0)
    latest_decision_session: date | None
    latest_decision_status: Literal["completed", "missing"] | None
    oos_anchor_cutoff_at: datetime
    activation_effective_at: datetime
    product_data_disclosure_id: UUID | None
    product_data_disclosure_fingerprint: str | None
    product_eligibility: Literal["eligible", "eligible_with_warnings"] | None
    warning_codes: list[str]


class V022ProductIdentityCatalogResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    items: list[V022ProductIdentityItem]


class V022ProductIdentityDetailResponse(V022ProductIdentityItem):
    context: ApiContext
    quality: QualitySummary
    qualification: dict[str, Any]
    monitoring_policy: dict[str, Any]
    lifecycle_events: list[dict[str, Any]]
    monitoring_snapshots: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    latest_decision: dict[str, Any] | None
    data_disclosure: dict[str, Any]
    active_ensemble_state: dict[str, Any] | None = None


class DomainCapability(ApiModel):
    key: str
    purpose: str
    upstream: list[str]
    delivery_milestone: str
    availability: Literal["available", "planned"]


class CapabilitiesResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    domains: list[DomainCapability]
    endpoints: list[str]
    interface_states: list[str]
    languages: list[Literal["zh-CN", "en"]]


class ArtifactSummary(ApiModel):
    artifact_id: UUID
    artifact_type: str
    artifact_key: str
    version_number: int
    status: ArtifactStatus
    semantic_fingerprint: str | None
    content_hash: str | None
    published_at: datetime | None
    created_at: datetime
    quality: QualitySummary


class ArtifactListResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    items: list[ArtifactSummary]
    total: int
    limit: int
    offset: int


class DependencySummary(ApiModel):
    artifact_id: UUID
    depends_on_artifact_id: UUID
    role: str
    ordinal: int | None


class LineageManifestResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    root_artifact: ArtifactSummary
    manifest_hash: str
    canonical_version: str
    artifacts: list[dict[str, Any]]
    dependencies: list[DependencySummary]
    created_at: datetime


class ArtifactDetailResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    artifact: ArtifactSummary
    direct_dependencies: list[DependencySummary]
    direct_dependents: list[DependencySummary]
    lineage_url: str | None


class ApiError(ApiModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AssetCategoryItem(ApiModel):
    category_key: str
    name: str
    description: str
    asset_count: int


class AssetSetItem(ApiModel):
    set_key: str
    name: str
    set_type: str
    maturity: str
    formal_eligible: bool
    notes: str
    member_security_ids: list[UUID]


class AssetDataInputOption(ApiModel):
    input_key: str
    name: str
    source_kind: str
    available: bool
    selectable: bool
    point_in_time: bool
    downstream_factor_keys: list[str]
    status_note: str


class AssetCatalogItem(ApiModel):
    security_id: UUID
    asset_id: UUID | None
    asset_key: str
    name: str
    category_key: str
    asset_class: str
    instrument_type: str
    status: str
    symbol: str
    aliases: list[str]
    venue_mic: str | None
    currency: str | None
    calendar_key: str | None
    tradability: str
    tags: list[str]
    maturity: str
    target_maturity: str
    missing_requirements: list[str]
    canonical_data_available: bool
    selectable: bool
    v022_candidate_selectable: bool = False
    v022_candidate_reason_codes: list[str] = Field(default_factory=list)
    v022_candidate_dataset_key: str | None = None
    v022_candidate_dataset_version: int | None = None
    data_inputs: list[AssetDataInputOption] = Field(default_factory=list)


class AssetCatalogResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    release_artifact_id: UUID
    release_version_number: int
    catalog_version: str
    as_of_date: str
    total: int
    limit: int
    offset: int
    categories: list[AssetCategoryItem]
    asset_sets: list[AssetSetItem]
    items: list[AssetCatalogItem]


class AssetSeriesPoint(ApiModel):
    session_date: date
    open: float
    high: float
    low: float
    close: float
    adjusted_close: float
    volume: int


class AssetSeriesResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    security_id: UUID
    asset_key: str
    symbol: str
    dataset_artifact_id: UUID
    dataset_version_number: int
    coverage_start: date
    coverage_end: date
    points: list[AssetSeriesPoint]


class WorkspaceFactorVariantOption(ApiModel):
    key: str
    parameters: dict[str, Any]
    required_price_observations: int
    preset_type: str
    selected: bool
    selectable: bool = True
    reason_codes: list[str] = Field(default_factory=list)


class WorkspaceFactorFamilyOption(ApiModel):
    key: str
    family: str
    definition_version: int = 1
    formula: str
    inputs: list[str]
    required_asset_input_keys: list[str]
    implementation_key: str
    output_unit: str
    time_semantics: str
    raw: bool
    variants: list[WorkspaceFactorVariantOption]


class WorkspaceSignalVersionOption(ApiModel):
    version_key: str
    factor_variant_key: str
    selected: bool
    selectable: bool
    reason_codes: list[str]


class WorkspaceSignalFamilyOption(ApiModel):
    key: str
    factor_variants: list[str]
    form: str
    output_type: str
    direction: str
    rule: dict[str, Any] | None = None
    economic_family: str
    dimension_hint: str
    rationale_type: str
    rationale: str
    research_tier: str
    product_eligible: bool
    versions: list[WorkspaceSignalVersionOption]


class WorkspaceModelSlotOption(ApiModel):
    slot_key: str
    allowed_dimension_keys: list[str]
    allowed_output_types: list[str]
    minimum_count: int
    maximum_count: int


class WorkspaceModelPresetOption(ApiModel):
    preset_key: str
    output_type: str
    output_comparability: str
    supported_frequencies: list[str]
    parameters: dict[str, Any]
    target_key: str | None
    input_slots: list[WorkspaceModelSlotOption]
    selectable: bool
    reason_codes: list[str]
    accepted_signal_keys: list[str]


class WorkspaceModelFamilyOption(ApiModel):
    key: str
    name: str
    description: str
    implementation_status: str
    presets: list[WorkspaceModelPresetOption]


class WorkspaceStrategyPresetOption(ApiModel):
    preset_key: str
    parameters: dict[str, Any]
    selected: bool
    selectable: bool
    reason_codes: list[str]
    research_mode: str


class WorkspaceStrategyFamilyOption(ApiModel):
    key: str
    name: str
    description: str
    implementation_status: str
    required_instrument_type: str
    minimum_eligible_assets: int
    formal_minimum_eligible_assets: int
    coverage_ratio: float
    supported_frequencies: list[str]
    compatible_model_output_types: list[str]
    parameter_options: dict[str, list[Any]]
    defaults: dict[str, Any]
    primary_benchmark: str
    research_benchmark: str
    presets: list[WorkspaceStrategyPresetOption]


class WorkspaceModelTargetOption(ApiModel):
    target_key: str
    target_kind: Literal["future_return", "cross_sectional_relative_return"]
    horizon_sessions: Literal[5, 21, 63]
    recommended: bool


class WorkspaceAssetDataInputBlocker(ApiModel):
    security_id: UUID
    input_key: str
    reason_codes: list[str]


class WorkspaceOptionsResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    catalog_artifact_id: UUID
    catalog_version: str
    frequency: str
    model_target_options: list[WorkspaceModelTargetOption]
    unknown_factor_variant_keys: list[str]
    unknown_signal_version_keys: list[str]
    unknown_model_preset_keys: list[str]
    asset_data_input_blockers: list[WorkspaceAssetDataInputBlocker]
    selected_asset_count: int
    usable_asset_count: int
    selected_asset_type_counts: dict[str, int]
    factor_families: list[WorkspaceFactorFamilyOption]
    signal_families: list[WorkspaceSignalFamilyOption]
    model_families: list[WorkspaceModelFamilyOption]
    strategy_families: list[WorkspaceStrategyFamilyOption]


class GraphFeatureSelection(ApiModel):
    feature_key: str
    stage_no: Literal[0, 1, 2, 3]


class GraphWorkspacePreviewRequest(ApiModel):
    frequency: Literal["weekly", "monthly"] = "weekly"
    explicit_features: list[GraphFeatureSelection] = Field(default_factory=list)
    aggregation_family_keys: list[str] = Field(default_factory=lambda: ["flat_equal_weight_mean"])
    aggregation_parameter_preset_keys: dict[str, list[str]] = Field(
        default_factory=lambda: {"flat_equal_weight_mean": ["signal_equal_v1"]}
    )
    strategy_keys: list[str] = Field(default_factory=lambda: ["cross_section_rank_top_k_parity"])
    strategy_parameter_preset_keys: dict[str, list[str]] = Field(
        default_factory=lambda: {"cross_section_rank_top_k_parity": ["k2"]}
    )
    defense_keys: list[str] = Field(default_factory=lambda: ["none"])


class GraphCatalogRelease(ApiModel):
    release_key: str
    catalog_version: str
    contract_version: str
    source_manifest_hash: str


class GraphWorkspaceSummary(ApiModel):
    explicit_count: int
    required_count: int
    stage3_input_count: int
    aggregation_instance_count: int
    strategy_branch_count: int
    backtest_cell_count: int


class GraphResourceEstimate(ApiModel):
    explicit_stage3_inputs: int
    feature_occurrences: int
    ancestor_occurrences: int
    graph_edges: int
    aggregation_candidates: int
    aggregation_instances: int
    strategy_candidates: int
    defense_candidates: int
    strategy_branches: int
    backtest_cells: int
    work_items: int


class GraphAdmissionCheck(ApiModel):
    resource_key: str
    estimated: int
    limit: int
    status: Literal["accepted", "rejected"]


class GraphResourceAdmission(ApiModel):
    policy_id: str
    state: Literal["accepted", "rejected"]
    estimates: GraphResourceEstimate
    checks: list[GraphAdmissionCheck]
    reason_codes: list[str]


class GraphFeatureProducer(ApiModel):
    kind: Literal["raw_input", "node_output", "layer_projection"]
    source_feature_key: str | None = None
    source_stage_no: int | None = None
    node_variant_key: str | None = None
    output_port_key: str | None = None


class GraphSelectEffect(ApiModel):
    ancestor_count: int
    projection_count: int


class GraphFeatureOccurrence(ApiModel):
    family_key: str
    feature_key: str
    name: str
    stage_no: Literal[0, 1, 2, 3]
    origin_stage: Literal[0, 1, 2, 3]
    formula_identity: str
    semantic_role: str
    unit: str
    parameters: dict[str, Any]
    input_feature_keys: list[str]
    output_semantics: dict[str, Any]
    payload_contract_key: str
    direction: str
    aggregation_readiness: str
    research_hypothesis: str
    is_explicit: bool
    is_required: bool
    is_present: bool
    required_by: list[str]
    availability: Literal["ready", "requires_ancestors", "hard_incompatible"]
    lock_state: Literal["unlocked", "locked"]
    locked_by: list[str]
    pinned: bool
    producer: GraphFeatureProducer
    select_effect: GraphSelectEffect
    reason_codes: list[str]


class GraphFeatureFamily(ApiModel):
    family_key: str
    name: str
    pinned: bool
    explicit_count: int
    required_count: int
    available_count: int
    variants: list[GraphFeatureOccurrence]


class GraphWorkspaceStage(ApiModel):
    stage_no: Literal[0, 1, 2, 3]
    explicit_count: int
    required_count: int
    families: list[GraphFeatureFamily]


class GraphAggregationPresetOption(ApiModel):
    preset_key: str
    name: str
    description: str
    version_number: int
    semantics: dict[str, Any]
    selected: bool
    selectable: bool
    reason_codes: list[str]


class GraphAggregationAxisOption(ApiModel):
    key: str
    name: str
    description: str
    version_number: int
    semantics: dict[str, Any]
    selected: bool


class GraphAggregationOption(ApiModel):
    family_key: str
    name: str
    algorithm_identity: str
    objective_semantics: dict[str, Any]
    output_semantics: dict[str, Any]
    execution_mode: str
    input_payload_contract_key: str
    output_payload_contract_key: str
    ordering_policy: str
    input_policy: dict[str, Any]
    compatibility_policy: dict[str, Any]
    missing_policy: dict[str, Any]
    tie_policy: dict[str, Any]
    selected: bool
    minimum_inputs: int
    maximum_inputs: int
    parameter_presets: list[str]
    parameter_preset_definitions: list[GraphAggregationPresetOption]
    selected_parameter_presets: list[str]
    targets: list[GraphAggregationAxisOption] = Field(default_factory=list)
    selected_targets: list[str] = Field(default_factory=list)
    training_presets: list[GraphAggregationAxisOption] = Field(default_factory=list)
    selected_training_presets: list[str] = Field(default_factory=list)
    internal_member_count: int = Field(ge=0)
    accepted_input_count: int


class GraphStrategyParameterPresetOption(ApiModel):
    preset_key: str
    name: str
    description: str
    version_number: int
    parameters: dict[str, Any]
    selected: bool
    selectable: bool
    reason_codes: list[str]


class GraphStrategyOption(ApiModel):
    family_key: str
    variant_key: str
    name: str
    selection_semantics: dict[str, Any]
    research_hypothesis: str
    parameters: dict[str, Any]
    input_payload_contract_key: str
    schedule_policy: dict[str, Any]
    execution_policy: dict[str, Any]
    parameter_presets: list[GraphStrategyParameterPresetOption]
    supported_frequencies: list[str]
    selected: bool
    compatible: bool
    reason_codes: list[str]


class GraphDefenseTimingPolicyOption(ApiModel):
    family_key: str
    variant_key: str
    name: str
    formula_identity: str
    research_hypothesis: str
    version_number: int
    research_status: Literal["exploratory", "parity", "formal"]
    supported_frequencies: list[Literal["weekly", "monthly"]]
    input_policy: dict[str, Any]
    rule: dict[str, Any]


class GraphDefenseAllocationMemberOption(ApiModel):
    ordinal: int
    asset_key: str
    component_role: Literal["defensive_asset", "reserve"]
    sleeve_weight: str


class GraphDefenseAllocationPolicyOption(ApiModel):
    family_key: str
    variant_key: str
    name: str
    formula_identity: str
    research_hypothesis: str
    version_number: int
    asset_registry_catalog_version: str
    asset_set_key: str
    research_status: Literal["exploratory", "parity", "formal"]
    formal_eligible: bool
    missing_member_policy: Literal["fail"]
    reserve_fallback_policy: Literal["forbidden"]
    rebalance_policy: Literal["with_strategy"]
    reserve_return_model: dict[str, Any] | None
    members: list[GraphDefenseAllocationMemberOption]


class GraphDefenseOption(ApiModel):
    family_key: str
    variant_key: str
    name: str
    allocation_semantics: dict[str, Any]
    research_hypothesis: str
    parameters: dict[str, Any]
    input_policy: dict[str, Any]
    allocation_policy_document: dict[str, Any]
    supported_asset_context_keys: list[str]
    selected: bool
    version_number: int | None = None
    composed: bool = False
    research_status: Literal["exploratory", "parity", "formal"] | None = None
    timing_policy: GraphDefenseTimingPolicyOption | None = None
    allocation_policy: GraphDefenseAllocationPolicyOption | None = None
    compatible: bool = True
    reason_codes: list[str] = Field(default_factory=list)


class GraphWorkspaceBlocker(ApiModel):
    layer: str
    object_key: str
    reason_codes: list[str]
    feature_keys: list[str] = Field(default_factory=list)


class GraphDraftDerivedViewResponse(ApiModel):
    catalog_release: GraphCatalogRelease
    selection_fingerprint: str
    derived_state_fingerprint: str
    frequency: Literal["weekly", "monthly"]
    summary: GraphWorkspaceSummary
    aggregation_inputs: list[str]
    aggregations: list[GraphAggregationOption]
    strategies: list[GraphStrategyOption]
    defenses: list[GraphDefenseOption]
    stages: list[GraphWorkspaceStage]
    blockers: list[GraphWorkspaceBlocker]
    warnings: list[str]
    resources: GraphResourceAdmission


class GraphWorkspacePreviewResponse(GraphDraftDerivedViewResponse):
    context: ApiContext
    quality: QualitySummary


class GraphStageFamilyPageResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    graph_draft_id: UUID
    revision: int
    stage_no: Literal[0, 1, 2, 3]
    view_token: str
    pinned_families: list[GraphFeatureFamily]
    catalog_families: list[GraphFeatureFamily]
    next_cursor: str | None
    total_catalog_family_count: int


class GraphDraftCreateRequest(ApiModel):
    researcher_key: str
    draft_key: str
    name: str
    idempotency_key: UUID
    frequency: Literal["weekly", "monthly"] = "weekly"
    asset_context_key: str | None = None
    data_input_keys: list[str] = Field(default_factory=list)


class GraphDraftCloneRequest(ApiModel):
    source_revision: int = Field(ge=1)
    researcher_key: str
    draft_key: str
    name: str
    idempotency_key: UUID


class GraphDraftEventRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    actor_key: str
    idempotency_key: UUID
    event_type: Literal[
        "select_feature_occurrence",
        "deselect_feature_occurrence",
        "batch_select_feature_occurrences",
        "batch_deselect_feature_occurrences",
        "select_all_legal_feature_occurrences",
        "clear_stage_feature_occurrences",
        "select_aggregation_family",
        "deselect_aggregation_family",
        "set_aggregation_parameter_presets",
        "set_aggregation_targets",
        "set_aggregation_training_presets",
        "set_strategy_parameter_presets",
        "select_all_compatible_strategy_presets",
        "clear_strategy_presets",
        "select_strategy",
        "deselect_strategy",
        "select_defense",
        "deselect_defense",
        "select_all_compatible_defenses",
        "clear_defenses",
        "set_frequency",
        "set_asset_selection",
    ]
    event: dict[str, Any]


class GraphDraftResetRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    actor_key: str
    idempotency_key: UUID


class AssetDataExportPreviewRequest(ApiModel):
    researcher_key: str
    graph_draft_id: UUID
    graph_draft_revision: int = Field(ge=1)
    export_format: Literal["parquet", "csv"] = "parquet"
    start_date: date | None = None
    end_date: date | None = None
    fields: list[str] = Field(
        default_factory=lambda: [
            "open_raw",
            "high_raw",
            "low_raw",
            "close_raw",
            "open_adj",
            "high_adj",
            "low_adj",
            "close_adj",
            "adjustment_factor",
            "volume_raw",
        ]
    )


class AssetDataExportPreviewResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    graph_draft_id: UUID
    graph_draft_revision: int
    asset_registry_release_id: UUID
    dataset_publication_id: UUID
    dataset_gate_assessment_id: UUID
    dataset_key: str
    dataset_version_number: int
    price_semantics: str
    asset_count: int = Field(ge=1)
    start_date: date
    end_date: date
    row_count: int = Field(ge=1)
    estimated_bytes: int = Field(ge=1)
    export_format: Literal["parquet", "csv"]
    fields: list[str]
    warning_codes: list[str]
    request_fingerprint: str


class AssetDataExportJobResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    export_job_id: UUID
    work_item_id: UUID
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    stage: str
    processed_rows: int = Field(ge=0)
    processed_bytes: int = Field(ge=0)
    total_rows: int = Field(ge=0)
    estimated_bytes: int = Field(ge=0)
    request_fingerprint: str
    status_url: str
    download_url: str | None
    content_hash: str | None
    byte_size: int | None
    filename: str | None
    expires_at: datetime | None
    local_delivery_path: str | None
    error_code: str | None
    error_message: str | None


class GraphDraftResetSummaryResponse(ApiModel):
    closed_research_round_id: UUID
    opened_research_round_id: UUID
    cancelled_graph_run_count: int = Field(ge=0)
    ordinary_experiment_cleanup_state: Literal["gc_pending", "gc_complete"]


class GraphDraftSnapshotResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    graph_draft_id: UUID
    catalog_release_id: UUID
    draft_key: str
    name: str
    revision: int
    status: str
    asset_context: dict[str, Any]
    resolved_data_binding: dict[str, Any]
    intent: dict[str, Any]
    derived_view: GraphDraftDerivedViewResponse
    cloned_from_graph_draft_id: UUID | None = None
    cloned_from_revision: int | None = None
    applied: bool | None = None
    reset_summary: GraphDraftResetSummaryResponse | None = None


class GraphChangePreviewRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    actor_key: str
    feature_key: str
    stage_no: Literal[0, 1, 2, 3]


class GraphCatalogRebasePreviewRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    actor_key: str


class GraphChangePreviewResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    impact_token: str
    graph_draft_id: UUID
    base_revision: int
    expires_at: datetime
    impact: dict[str, Any]


class GraphChangeConfirmRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    actor_key: str
    idempotency_key: UUID


class GraphDraftCompileRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    actor_key: str
    idempotency_key: UUID


class GraphDraftDefenseExecutionContext(ApiModel):
    compiled_defense_execution_context_id: UUID
    defense_execution_context_artifact_id: UUID
    compiled_execution_data_context_id: UUID
    defense_version_id: UUID
    defense_execution_context_fingerprint: str
    resolved_input_binding_fingerprint: str
    input_count: int = Field(ge=1)
    reused: bool

    @model_validator(mode="after")
    def validate_fingerprints(self) -> GraphDraftDefenseExecutionContext:
        for label, fingerprint in (
            ("Defense Execution Context", self.defense_execution_context_fingerprint),
            ("Defense Resolved Input Binding", self.resolved_input_binding_fingerprint),
        ):
            if len(fingerprint) != 64 or any(
                character not in "0123456789abcdef" for character in fingerprint
            ):
                raise ValueError(f"{label} fingerprint must be lowercase sha256")
        return self


class GraphDraftCompileResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    graph_draft_id: UUID
    graph_draft_revision: int
    draft_intent_id: UUID
    compile_attempt_id: UUID
    compiled_research_graph_id: UUID
    graph_artifact_id: UUID
    graph_fingerprint: str
    reused: bool
    # Nullable only for exact replay of pre-execution-context compile responses.
    # Every newly compiled Graph Draft publishes all four values.
    compiled_execution_data_context_id: UUID | None = None
    execution_data_context_artifact_id: UUID | None = None
    execution_data_context_fingerprint: str | None = None
    execution_data_context_reused: bool | None = None
    # Legacy command replays omitted this key; Pydantic defaults them to no
    # Defense Contexts without inventing an artifact for the `none` option.
    defense_execution_contexts: list[GraphDraftDefenseExecutionContext] = Field(
        default_factory=list
    )
    selection_fingerprint: str | None = None

    @model_validator(mode="after")
    def validate_execution_data_context_identity(self) -> GraphDraftCompileResponse:
        context_identity = (
            self.compiled_execution_data_context_id,
            self.execution_data_context_artifact_id,
            self.execution_data_context_fingerprint,
            self.execution_data_context_reused,
        )
        present = tuple(value is not None for value in context_identity)
        if any(present) and not all(present):
            raise ValueError(
                "Compiled Execution Data Context identity must be wholly present or absent"
            )
        fingerprint = self.execution_data_context_fingerprint
        if fingerprint is not None and (
            len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            raise ValueError("Compiled Execution Data Context fingerprint must be lowercase sha256")
        if self.selection_fingerprint is not None and (
            len(self.selection_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.selection_fingerprint
            )
        ):
            raise ValueError("Graph selection fingerprint must be lowercase sha256")
        defense_version_ids = tuple(
            item.defense_version_id for item in self.defense_execution_contexts
        )
        if len(defense_version_ids) != len(set(defense_version_ids)):
            raise ValueError("Defense Execution Contexts require unique Defense versions")
        if defense_version_ids != tuple(sorted(defense_version_ids, key=str)):
            raise ValueError("Defense Execution Contexts require canonical Defense order")
        if self.defense_execution_contexts and self.compiled_execution_data_context_id is None:
            raise ValueError(
                "Defense Execution Contexts require the Compiled Execution Data Context"
            )
        if any(
            item.compiled_execution_data_context_id
            != self.compiled_execution_data_context_id
            for item in self.defense_execution_contexts
        ):
            raise ValueError(
                "Defense Execution Contexts must bind the response Execution Data Context"
            )
        for label, identities in (
            (
                "Defense Execution Context",
                tuple(
                    item.compiled_defense_execution_context_id
                    for item in self.defense_execution_contexts
                ),
            ),
            (
                "Defense Execution Context Artifact",
                tuple(
                    item.defense_execution_context_artifact_id
                    for item in self.defense_execution_contexts
                ),
            ),
        ):
            if len(identities) != len(set(identities)):
                raise ValueError(f"{label} identities must be unique")
        return self


class GraphSuiteSubmitRequest(ApiModel):
    """Submit one immutable v0.22 Compiled Graph; never reinterpret a Draft."""

    compiled_research_graph_id: UUID
    graph_draft_id: UUID
    graph_draft_revision: int = Field(ge=1)
    actor_key: str
    idempotency_key: UUID
    suite_mode: Literal["exploratory"] = "exploratory"


class GraphSuiteSubmitResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    contract_version: Literal["v0.22.0"] = "v0.22.0"
    research_suite_id: UUID
    suite_artifact_id: UUID
    compiled_research_graph_id: UUID
    graph_fingerprint: str
    suite_fingerprint: str
    strategy_branch_count: int = Field(ge=1)
    backtest_cell_count: int = Field(ge=1)
    status: Literal[
        "not_started",
        "materializing",
        "targeting",
        "merging",
        "evaluating",
        "completed",
        "failed",
        "cancelled",
    ]
    reused: bool
    suite_mode: Literal["exploratory"] = "exploratory"


def _default_graph_suite_launch_frequencies() -> list[
    Literal["weekly", "monthly"]
]:
    return ["weekly", "monthly"]


class GraphSuiteLaunchBatchRequest(ApiModel):
    source_graph_draft_id: UUID
    source_graph_draft_revision: int = Field(ge=1)
    source_compiled_research_graph_id: UUID
    actor_key: str
    idempotency_key: UUID
    frequencies: list[Literal["weekly", "monthly"]] = Field(
        default_factory=_default_graph_suite_launch_frequencies,
        min_length=1,
        max_length=2,
    )
    suite_mode: Literal["exploratory"] = "exploratory"

    @model_validator(mode="after")
    def validate_frequencies(self) -> GraphSuiteLaunchBatchRequest:
        if len(self.frequencies) != len(set(self.frequencies)):
            raise ValueError("Suite Launch Batch frequencies must be unique")
        return self


class GraphSuiteLaunchBatchChild(ApiModel):
    frequency: Literal["weekly", "monthly"]
    graph_draft_id: UUID | None
    graph_draft_revision: int | None = Field(default=None, ge=1)
    compiled_research_graph_id: UUID | None
    research_suite_id: UUID | None
    status: Literal[
        "planning",
        "not_started",
        "materializing",
        "targeting",
        "merging",
        "evaluating",
        "completed",
        "failed",
        "cancelled",
    ]
    total: int = Field(ge=0)
    terminal: int = Field(ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    complete: bool
    stage: Literal[
        "prepare_graph", "admit_graph", "submit_suite", "lock_source", "complete"
    ] | None = None
    failure_code: str | None = None
    failure_summary: str | None = None


class GraphSuiteLaunchBatchResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    contract_version: Literal["v0.22.0"] = "v0.22.0"
    suite_launch_batch_id: UUID
    source_graph_draft_id: UUID
    source_graph_draft_revision: int = Field(ge=1)
    batch_fingerprint: str
    status: Literal[
        "planning", "submitted", "running", "completed", "failed", "cancelled"
    ]
    stage: Literal[
        "prepare_graph", "admit_graph", "submit_suite", "lock_source", "complete"
    ] | None = None
    failed_frequency: Literal["weekly", "monthly"] | None = None
    failure_code: str | None = None
    failure_summary: str | None = None
    children: list[GraphSuiteLaunchBatchChild] = Field(min_length=1, max_length=2)
    reused: bool


class GraphSuiteStatusResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    contract_version: Literal["v0.22.0"] = "v0.22.0"
    research_suite_id: UUID
    compiled_research_graph_id: UUID
    status: Literal[
        "not_started",
        "materializing",
        "targeting",
        "merging",
        "evaluating",
        "completed",
        "failed",
        "cancelled",
    ]
    total: int = Field(ge=0)
    terminal: int = Field(ge=0)
    complete: bool
    status_counts: dict[str, int]
    suite_mode: Literal["exploratory"] = "exploratory"


class GraphSuiteRuntimeReadinessResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    contract_version: Literal["v0.22.0"] = "v0.22.0"
    ready: bool
    state: Literal["ready", "working", "stopped", "stale", "unavailable", "error"]
    worker_key: str | None
    process_id: int | None
    heartbeat_at: datetime | None
    max_age_seconds: int = Field(ge=1)
    error_summary: str | None = None


class GraphSuiteSummary(ApiModel):
    research_suite_id: UUID
    compiled_research_graph_id: UUID
    graph_fingerprint: str
    suite_fingerprint: str
    status: Literal[
        "not_started",
        "materializing",
        "targeting",
        "merging",
        "evaluating",
        "completed",
        "failed",
        "cancelled",
    ]
    total: int = Field(ge=0)
    terminal: int = Field(ge=0)
    complete: bool
    status_counts: dict[str, int]
    strategy_branch_count: int = Field(ge=1)
    backtest_cell_count: int = Field(ge=1)
    suite_mode: Literal["exploratory"] = "exploratory"
    created_at: datetime


class GraphSuiteListResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    contract_version: Literal["v0.22.0"] = "v0.22.0"
    items: list[GraphSuiteSummary]
    total_count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class GraphSuiteMetricDiagnostic(ApiModel):
    metric_group: Literal["absolute", "relative"]
    metric_key: str
    value: str | None
    value_status: Literal["defined", "unavailable"]
    reason_code: str | None
    observation_count: int = Field(ge=0)


class GraphSuiteQualityDiagnostic(ApiModel):
    outcome: Literal["accepted", "data_quality_failed", "capacity_rejected"]
    status: Literal["passed", "warning", "failed"]
    reason_code: str | None
    details: dict[str, Any]
    path_session_count: int = Field(ge=0)


class GraphSuiteExecutionDiagnostic(ApiModel):
    benchmark_asset_id: UUID | None
    benchmark_asset_key: str | None
    cost_policy_key: str | None
    basis_points_per_side: str | None
    execution_delay_sessions: int | None = Field(default=None, ge=0)
    evaluation_input_cutoff_at: datetime | None
    work_execution_fingerprint: str | None
    evaluation_data_context_fingerprint: str | None


class GraphSuiteEvidenceDiagnostic(ApiModel):
    publication_status: Literal["published", "not_published"]
    result_evidence_snapshot_id: UUID | None
    result_evidence_artifact_id: UUID | None
    evidence_fingerprint: str | None
    evidence_class: Literal[
        "walk_forward_backtest", "locked_historical_test", "prospective_oos"
    ] | None
    common_evaluation_panel_id: UUID | None
    common_evaluation_panel_fingerprint: str | None


class GraphSuiteElementMetric(ApiModel):
    metric_key: str
    value: str | None
    reason_code: str | None


class GraphSuiteElementDiagnosticDocument(ApiModel):
    compiled_feature_occurrence_id: UUID
    feature_variant_key: str
    stage_no: int = Field(ge=1, le=3)
    payload_manifest_id: UUID
    manifest_artifact_id: UUID
    manifest_hash: str
    research_direction: Literal["positive", "negative", "unsigned"]
    target_key: str
    target_version_id: UUID
    target_version_artifact_id: UUID
    frequency: Literal["weekly", "monthly"]
    coverage_start: date
    coverage_end: date
    expected_observation_count: int = Field(ge=0)
    observed_value_count: int = Field(ge=0)
    missing_value_count: int = Field(ge=0)
    evaluation_period_count: int = Field(ge=0)
    valid_ic_count: int = Field(ge=0)
    metrics: list[GraphSuiteElementMetric]


class GraphSuiteElementDiagnostic(ApiModel):
    result_element_diagnostic_id: UUID
    artifact_id: UUID
    diagnostic_fingerprint: str
    diagnostic_document: GraphSuiteElementDiagnosticDocument


class GraphSuiteResultDiagnostic(ApiModel):
    metrics: list[GraphSuiteMetricDiagnostic]
    quality: GraphSuiteQualityDiagnostic
    execution: GraphSuiteExecutionDiagnostic
    evidence: GraphSuiteEvidenceDiagnostic
    elements: list[GraphSuiteElementDiagnostic]


class GraphSuiteResultItem(ApiModel):
    research_cell_id: UUID
    research_suite_branch_id: UUID
    compiled_strategy_branch_id: UUID
    configuration_snapshot_id: UUID
    portfolio_evaluation_data_context_id: UUID
    result_artifact_id: UUID
    payload_manifest_id: UUID
    payload_manifest_artifact_id: UUID
    result_fingerprint: str
    logical_payload_fingerprint: str
    manifest_hash: str
    outcome: Literal["accepted", "data_quality_failed", "capacity_rejected"]
    quality_status: Literal["passed", "warning", "failed"]
    effective_start: date
    effective_end: date
    metric_document: dict[str, Any]
    result_document: dict[str, Any]
    diagnostic: GraphSuiteResultDiagnostic


class GraphSuiteResultsResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    contract_version: Literal["v0.22.0"] = "v0.22.0"
    research_suite_id: UUID
    compiled_research_graph_id: UUID
    status: Literal[
        "materializing",
        "targeting",
        "merging",
        "evaluating",
        "completed",
        "failed",
        "cancelled",
    ]
    complete: bool
    expected_result_count: int = Field(ge=1)
    result_count: int = Field(ge=0)
    results: list[GraphSuiteResultItem]


class WorkspaceCompileRequest(ApiModel):
    frequency: Literal["weekly", "monthly"]
    asset_security_ids: list[UUID]
    asset_data_inputs: dict[UUID, list[str]] = Field(default_factory=dict)
    factor_variant_keys: list[str]
    signal_version_keys: list[str]
    model_preset_keys: list[str]
    model_target_keys: list[str] = Field(
        default_factory=lambda: ["cross_sectional_relative_return__h5"]
    )
    strategy_preset_keys: list[str]

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_asset_inputs(cls, value: Any) -> Any:
        return _with_legacy_asset_input_defaults(value)

    @model_validator(mode="after")
    def validate_asset_inputs(self) -> WorkspaceCompileRequest:
        _validate_asset_input_mapping(self.asset_security_ids, self.asset_data_inputs)
        return self


class WorkspaceCompileBlocker(ApiModel):
    layer: str
    object_key: str
    reason_codes: list[str]


class WorkspaceCompilePreviewResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    catalog_artifact_id: UUID
    catalog_version: str
    selected_asset_count: int
    usable_asset_count: int
    compiled: dict[str, Any]
    blockers: list[WorkspaceCompileBlocker]


class WorkspaceDraftSelection(ApiModel):
    frequency: Literal["weekly", "monthly"]
    asset_security_ids: list[UUID]
    asset_data_inputs: dict[UUID, list[str]] = Field(default_factory=dict)
    factor_variant_keys: list[str]
    signal_version_keys: list[str]
    model_preset_keys: list[str]
    model_target_keys: list[str] = Field(
        default_factory=lambda: ["cross_sectional_relative_return__h5"]
    )
    strategy_preset_keys: list[str]

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_asset_inputs(cls, value: Any) -> Any:
        return _with_legacy_asset_input_defaults(value)

    @model_validator(mode="after")
    def validate_asset_inputs(self) -> WorkspaceDraftSelection:
        _validate_asset_input_mapping(self.asset_security_ids, self.asset_data_inputs)
        return self


class WorkspaceDraftSaveRequest(ApiModel):
    idempotency_key: UUID
    researcher_id: str = Field(min_length=1, max_length=120)
    draft_key: str = Field(min_length=1, max_length=180)
    name: str = Field(min_length=1, max_length=240)
    expected_revision: int | None = Field(default=None, ge=0)
    selection: WorkspaceDraftSelection


class WorkspaceDraftResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    research_draft_id: UUID
    researcher_id: str
    draft_key: str
    name: str
    revision: int
    selection: WorkspaceDraftSelection
    last_compiled_artifact_id: UUID | None


class ReleaseGateResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    formal_enabled: bool
    product_enabled: bool
    reason_codes: list[str]


class WorkspaceSuiteSubmitRequest(ApiModel):
    idempotency_key: UUID
    researcher_id: str
    draft_key: str
    expected_revision: int = Field(ge=1)
    suite_mode: Literal["formal", "exploratory"] = "exploratory"


class WorkspaceSuiteSubmitResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    research_suite_id: UUID
    suite_artifact_id: UUID
    suite_key: str
    suite_fingerprint: str
    predictive_cell_count: int
    portfolio_cell_count: int
    queued_work_item_count: int
    reused: bool
    suite_mode: Literal["formal", "exploratory"] = "formal"


class WorkspaceSuiteStatusResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    research_suite_id: UUID
    total: int
    terminal: int
    complete: bool
    status_counts: dict[str, int]
    suite_mode: Literal["formal", "exploratory"] = "formal"


class WorkspaceSuiteCancelResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    research_suite_id: UUID
    affected_work_items: int


class SignalResearchExportRequest(ApiModel):
    frequency: Literal["weekly", "monthly"]
    asset_security_ids: list[UUID] = Field(min_length=1)
    asset_data_inputs: dict[UUID, list[str]] = Field(default_factory=dict)
    signal_version_keys: list[str] = Field(min_length=1)
    include_targets: bool = True

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_asset_inputs(cls, value: Any) -> Any:
        return _with_legacy_asset_input_defaults(value)

    @model_validator(mode="after")
    def validate_asset_inputs(self) -> SignalResearchExportRequest:
        _validate_asset_input_mapping(self.asset_security_ids, self.asset_data_inputs)
        return self


class SignalResearchExportJobResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    export_job_id: UUID
    work_item_id: UUID
    request_fingerprint: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    stage: str
    attempt_count: int
    max_attempts: int
    status_url: str
    download_url: str | None = None
    failure_class: str | None = None
    failure_details: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = None
    byte_size: int | None = None
    expires_at: datetime | None = None


def _with_legacy_asset_input_defaults(value: Any) -> Any:
    """Migrate pre-input-selection API payloads without overriding an explicit empty choice."""
    if not isinstance(value, dict) or "asset_data_inputs" in value:
        return value
    migrated = dict(value)
    migrated["asset_data_inputs"] = {
        str(security_id): ["canonical_market_bars"]
        for security_id in migrated.get("asset_security_ids", [])
    }
    return migrated


def _validate_asset_input_mapping(
    asset_security_ids: list[UUID], asset_data_inputs: dict[UUID, list[str]]
) -> None:
    selected = set(asset_security_ids)
    if set(asset_data_inputs) != selected:
        raise ValueError("Asset data-input selections must exactly match selected assets")
    for security_id, input_keys in asset_data_inputs.items():
        if len(input_keys) != len(set(input_keys)):
            raise ValueError(f"Asset {security_id} contains duplicate data-input selections")
        if any(not key.strip() for key in input_keys):
            raise ValueError(f"Asset {security_id} contains an empty data-input key")


class CommandIdempotencyRequest(ApiModel):
    idempotency_key: UUID


class PromotionQualificationResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    eligible: bool
    reason_codes: list[str]
    warning_codes: list[str] = Field(default_factory=list)
    compiled_strategy_version_id: UUID | None
    source_suite_artifact_id: UUID | None
    comparison_context_id: UUID | None
    qualification_bundle_artifact_id: UUID | None
    result_artifact_ids: list[UUID] = Field(default_factory=list)
    cell_artifact_ids: list[UUID] = Field(default_factory=list)
    predictive_result_artifact_ids: list[UUID] = Field(default_factory=list)
    predictive_cell_artifact_ids: list[UUID] = Field(default_factory=list)
    selection_context: dict[str, Any] = Field(default_factory=dict)


class ProductPromotionRequest(ApiModel):
    idempotency_key: UUID
    name: str = Field(min_length=1, max_length=240)
    researcher_id: str = Field(min_length=1, max_length=120)
    selection_reason: str = Field(min_length=1)
    note: str | None = None


class ProductPromotionResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    product_enrollment_id: UUID
    product_version_artifact_id: UUID
    qualification_bundle_artifact_id: UUID
    lifecycle: str
    revision: int


class ProductLifecycleChangeRequest(ApiModel):
    idempotency_key: UUID
    target: Literal["active", "suspended", "retired", "invalidated"]
    expected_revision: int = Field(ge=1)
    reason_code: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1)
    researcher_id: str = Field(min_length=1, max_length=120)
    requested_at: datetime
    effective_at: datetime


class ProductLifecycleChangeResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    enrollment_id: UUID
    from_lifecycle: str
    to_lifecycle: str
    revision: int
    event_sequence: int
    effective_at: datetime
    applied: bool


class ProductAlertChangeRequest(ApiModel):
    idempotency_key: UUID
    target: Literal["acknowledged", "resolved", "superseded"]
    researcher_id: str = Field(min_length=1, max_length=120)
    note: str | None = None
    occurred_at: datetime


class ProductAlertChangeResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    alert_id: UUID
    from_status: str
    to_status: str
    sequence_number: int
    occurred_at: datetime


class ProductReviewRequest(ApiModel):
    idempotency_key: UUID
    decision: Literal["continue", "suspend", "retire", "replace"]
    researcher_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1)
    evidence: dict[str, Any] = Field(default_factory=dict)
    reviewed_at: datetime


class ProductReviewResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    product_review_id: UUID
    product_enrollment_id: UUID
    decision: str
    reviewed_at: datetime


class ProductCandidateItem(ApiModel):
    enrollment_id: UUID
    product_artifact_id: UUID
    qualification_artifact_id: UUID
    product_key: str
    version_number: int
    name: str
    model_preset_key: str
    strategy_family_key: str
    strategy_preset_key: str
    asset_context_key: str
    lifecycle: str
    health: str
    revision: int
    activated_at: datetime
    monitoring_start_at: datetime | None
    updated_at: datetime
    latest_as_of_session: date | None
    primary_nav: float | None
    stress_nav: float | None
    latest_metrics: dict[str, Any]
    open_alert_count: int
    warning_codes: list[str] = Field(default_factory=list)


class ProductCatalogResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    items: list[ProductCandidateItem]


class ProductEventItem(ApiModel):
    sequence_number: int
    from_lifecycle: str | None
    to_lifecycle: str
    reason_code: str
    reason: str
    researcher_id: str
    requested_at: datetime
    effective_at: datetime


class ProductAlertItem(ApiModel):
    alert_id: UUID
    alert_key: str
    alert_type: str
    severity: str
    opened_at: datetime
    status: str
    evidence: dict[str, Any]


class ProductSnapshotItem(ApiModel):
    artifact_id: UUID
    as_of_session: date
    known_at: datetime
    health: str
    session_count: int
    decision_count: int
    primary_nav: float
    stress_nav: float
    metrics: dict[str, Any]
    health_components: dict[str, Any]


class ProductOosWindow(ApiModel):
    frozen_anchor_session: date | None
    activation_session: date
    latest_published_data_session: date | None
    latest_published_data_known_at: datetime | None
    latest_snapshot_session: date | None
    post_freeze_session_count: int
    prospective_oos_session_count: int
    status: Literal[
        "awaiting_frozen_anchor",
        "awaiting_post_freeze_data",
        "awaiting_first_snapshot",
        "observing",
    ]
    reason_codes: list[str] = Field(default_factory=list)


class ProductReviewItem(ApiModel):
    product_review_id: UUID
    reviewed_at: datetime
    researcher_id: str
    decision: str
    reason: str
    evidence: dict[str, Any]


class ProductResearchAssetItem(ApiModel):
    security_id: UUID
    asset_key: str
    symbol: str
    name: str
    category_key: str | None = None


class ProductResearchChain(ApiModel):
    source_suite_artifact_id: UUID
    selected_result_artifact_id: UUID
    selected_branch_key: str
    frequency: str
    assets: list[ProductResearchAssetItem]
    factor_variant_keys: list[str]
    signal_version_keys: list[str]
    model_preset_keys: list[str]
    model_target_keys: list[str]
    strategy_preset_keys: list[str]
    qualification_result_artifact_ids: list[UUID]


class ProductBacktestMetricItem(ApiModel):
    series_role: str
    metric_scope: str
    metric_key: str
    name: str
    unit: str
    value: float | None
    value_status: str
    reason_code: str | None
    observation_count: int


class ProductBacktestNavPoint(ApiModel):
    nav_date: date
    strategy_wealth: float
    benchmark_wealth: float
    excess_wealth: float
    drawdown: float


class ProductQualificationBacktest(ApiModel):
    result_artifact_id: UUID
    specification: dict[str, Any]
    resolved_start: date | None
    resolved_end: date | None
    observation_count: int
    run_status: str
    metrics: list[ProductBacktestMetricItem]
    nav_series: list[ProductBacktestNavPoint]
    quality_checks: list[dict[str, Any]]


class ProductRecommendationPosition(ApiModel):
    asset_key: str
    symbol: str
    name: str
    allocation_role: Literal["risk", "defense", "reserve"]
    model_score: float | None
    rank: int | None
    target_weight: float
    retained_by_buffer: bool


class ProductRecommendationResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    available: bool
    status: Literal["accepted", "failed"]
    reason_codes: list[str]
    frequency: str
    data_bundle_artifact_id: UUID
    data_as_of_session: date
    data_known_at: datetime
    decision_session: date
    recommended_execution_session: date | None
    next_expected_signal_session: date | None
    eligible_count: int
    rankable_count: int
    coverage_ratio: float
    positions: list[ProductRecommendationPosition]
    not_oos: bool
    refresh_policy: str


class ProductDetailResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    candidate: ProductCandidateItem
    qualification_gate_results: dict[str, Any]
    selection_reason: str
    note: str | None
    events: list[ProductEventItem]
    alerts: list[ProductAlertItem]
    snapshots: list[ProductSnapshotItem]
    oos_window: ProductOosWindow
    reviews: list[ProductReviewItem]
    research_chain: ProductResearchChain | None = None
    qualification_backtest: ProductQualificationBacktest | None = None


class DataRequirementItem(ApiModel):
    requirement_key: str
    subject: str
    series_key: str
    fields: list[str]
    interval_unit: str
    interval_count: int
    calendar_type: str
    session_type: str
    timestamp_semantics: str


class DataRequirementResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    artifact_id: UUID
    requirement_set_key: str
    version_number: int
    items: list[DataRequirementItem]


class SourceSnapshotItem(ApiModel):
    artifact_id: UUID
    series_key: str
    provider_key: str
    snapshot_key: str
    fetched_at: datetime
    as_of_at: datetime
    raw_size_bytes: int
    payload_hash: str


class DatasetCoverageItem(ApiModel):
    subject_key: str
    asset_key: str | None
    coverage_start: date
    coverage_end: date
    observation_count: int
    missing_count: int


class DataQualityIssueItem(ApiModel):
    severity: Literal["info", "warning", "error"]
    rule_code: str
    asset_key: str | None
    event_date: date | None
    message: str
    details: dict[str, Any]


class DatasetPublicationItem(ApiModel):
    artifact_id: UUID
    dataset_key: str
    version_number: int
    dataset_kind: Literal["canonical", "derived"]
    value_kind: Literal["daily_bar", "rate_observation", "reserve_return"]
    coverage_start: date
    coverage_end: date
    row_count: int
    coverage: list[DatasetCoverageItem]
    issues: list[DataQualityIssueItem]
    quality: QualitySummary


class DataBundleMemberItem(ApiModel):
    role: str
    ordinal: int
    artifact_id: UUID
    artifact_type: str
    artifact_key: str
    version_number: int


class DataBundleItem(ApiModel):
    artifact_id: UUID
    bundle_key: str
    name: str
    version_number: int
    coverage_start: date
    coverage_end: date
    member_count: int
    members: list[DataBundleMemberItem]


class EligibilityIssueItem(ApiModel):
    severity: Literal["warning", "error"]
    issue_code: str
    message: str
    details: dict[str, Any]


class EligibilityAssetItem(ApiModel):
    asset_id: UUID
    asset_key: str
    symbol: str
    role: str
    is_eligible: bool
    available_start: date | None
    available_end: date | None
    data_ready_date: date | None
    observation_count: int
    issues: list[EligibilityIssueItem]


class EligibilitySnapshotItem(ApiModel):
    artifact_id: UUID
    snapshot_key: str
    requested_start: date
    requested_end: date
    warmup_observations: int
    member_count: int
    eligible_count: int
    items: list[EligibilityAssetItem]


class DataOverviewResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    sources: list[SourceSnapshotItem]
    datasets: list[DatasetPublicationItem]
    bundle: DataBundleItem | None
    eligibility: EligibilitySnapshotItem | None


class FactorDiagnosticIssueItem(ApiModel):
    variant_key: str
    severity: Literal["info", "warning", "error"]
    issue_code: str
    message: str
    details: dict[str, Any]


class FactorDatasetDiagnosticItem(ApiModel):
    factor_dataset_artifact_id: UUID
    factor_key: str
    measurement_family: str
    formula: str
    output_unit: str
    variant_key: str
    parameters: dict[str, Any]
    preset_type: str
    coverage_start: date
    coverage_end: date
    row_count: int
    observation_count: int
    asset_count: int
    missing_count: int
    mean: float
    standard_deviation: float
    minimum: float
    p05: float
    p25: float
    median: float
    p75: float
    p95: float
    maximum: float
    zero_variance: bool
    quality: QualitySummary


class FactorCorrelationItem(ApiModel):
    left_variant_key: str
    right_variant_key: str
    left_factor_key: str
    right_factor_key: str
    observation_count: int
    spearman_correlation: float | None
    same_definition: bool
    high_correlation: bool


class FactorOverviewResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    diagnostic_artifact_id: UUID
    factor_catalog_artifact_id: UUID
    universe_artifact_id: UUID
    data_bundle_artifact_id: UUID
    eligibility_artifact_id: UUID
    factor_engine_artifact_id: UUID
    diagnostic_engine_artifact_id: UUID
    coverage_start: date
    coverage_end: date
    dataset_count: int
    asset_count: int
    observation_count: int
    pair_count: int
    high_correlation_threshold: float
    datasets: list[FactorDatasetDiagnosticItem]
    correlations: list[FactorCorrelationItem]
    issues: list[FactorDiagnosticIssueItem]


class SignalWindowMetricItem(ApiModel):
    window_key: str
    window_start: date
    window_end: date
    period_count: int
    valid_ic_count: int
    undefined_ic_count: int
    mean_rank_ic: float | None
    median_rank_ic: float | None
    positive_ic_ratio: float | None
    information_ratio: float | None
    mean_top_bottom_spread: float
    event_rate: float | None
    event_asset_concentration: float | None
    non_neutral_rate: float
    mean_top2_turnover: float | None


class SignalDiagnosticItem(ApiModel):
    signal_dataset_artifact_id: UUID
    signal_key: str
    template_key: str
    economic_family: str
    rationale_type: str
    rationale: str
    research_tier: str
    product_eligible: bool
    direction: str
    normalization: str
    output_type: str
    factor_variant_key: str
    full: SignalWindowMetricItem
    stability: list[SignalWindowMetricItem]
    quality: QualitySummary


class SignalPairDiagnosticItem(ApiModel):
    left_signal_key: str
    right_signal_key: str
    score_observation_count: int
    score_spearman: float | None
    spread_period_count: int
    spread_correlation: float | None
    mean_top2_overlap: float
    high_correlation: bool


class SignalDiagnosticIssueItem(ApiModel):
    signal_key: str
    severity: Literal["info", "warning", "error"]
    issue_code: str
    message: str
    details: dict[str, Any]


class SignalOverviewResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    evaluation_artifact_id: UUID
    signal_catalog_artifact_id: UUID
    universe_artifact_id: UUID
    data_bundle_artifact_id: UUID
    eligibility_artifact_id: UUID
    signal_engine_artifact_id: UUID
    evaluation_engine_artifact_id: UUID
    forward_return_artifact_id: UUID
    target_key: str
    frequency: Literal["weekly", "monthly"]
    coverage_start: date
    coverage_end: date
    signal_count: int
    common_period_count: int
    pair_count: int
    high_correlation_threshold: float
    signals: list[SignalDiagnosticItem]
    pairs: list[SignalPairDiagnosticItem]
    issues: list[SignalDiagnosticIssueItem]


class ModelWindowMetricItem(ApiModel):
    window_key: str
    window_start: date
    window_end: date
    period_count: int
    valid_ic_count: int
    undefined_ic_count: int
    mean_rank_ic: float | None
    median_rank_ic: float | None
    positive_ic_ratio: float | None
    information_ratio: float | None
    mean_top_bottom_spread: float
    non_neutral_rate: float
    mean_top2_turnover: float | None
    mean_score_dispersion: float
    mean_confidence: float


class ModelComponentItem(ApiModel):
    signal_key: str
    input_transform: str
    weight: float


class ModelDimensionItem(ApiModel):
    dimension_key: str
    method_key: str
    input_transform: str
    weight: float
    components: list[ModelComponentItem]


class ModelDiagnosticItem(ApiModel):
    model_dataset_artifact_id: UUID
    specification_key: str
    specification_type: str
    model_key: str
    model_family: str
    hypothesis: str
    overall_method_key: str
    tie_output: str
    output_type: str
    active_dimension_count: int
    component_count: int
    research_tier: str
    dimensions: list[ModelDimensionItem]
    full: ModelWindowMetricItem
    stability: list[ModelWindowMetricItem]
    quality: QualitySummary


class ModelPairDiagnosticItem(ApiModel):
    left_specification_key: str
    right_specification_key: str
    score_observation_count: int
    score_spearman: float | None
    spread_period_count: int
    spread_correlation: float | None
    mean_top2_overlap: float
    high_correlation: bool


class ModelAblationItem(ApiModel):
    full_specification_key: str
    ablated_specification_key: str
    removed_dimension_key: str
    window_key: str
    period_count: int
    delta_mean_rank_ic: float | None
    delta_information_ratio: float | None
    delta_mean_top_bottom_spread: float


class ModelDiagnosticIssueItem(ApiModel):
    specification_key: str
    severity: Literal["info", "warning", "error"]
    issue_code: str
    message: str
    details: dict[str, Any]


class ModelOverviewResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    evaluation_artifact_id: UUID
    model_catalog_artifact_id: UUID
    universe_artifact_id: UUID
    data_bundle_artifact_id: UUID
    eligibility_artifact_id: UUID
    model_engine_artifact_id: UUID
    evaluation_engine_artifact_id: UUID
    forward_return_artifact_id: UUID
    target_key: str
    frequency: Literal["weekly", "monthly"]
    coverage_start: date
    coverage_end: date
    model_count: int
    common_period_count: int
    pair_count: int
    ablation_count: int
    high_correlation_threshold: float
    models: list[ModelDiagnosticItem]
    pairs: list[ModelPairDiagnosticItem]
    ablations: list[ModelAblationItem]
    issues: list[ModelDiagnosticIssueItem]


class StrategyVariantItem(ApiModel):
    artifact_id: UUID
    variant_key: str
    template_key: str
    target_k: int
    research_tier: str
    selection_order: str
    trend_filter: str
    auxiliary_signal_key: str | None
    auxiliary_eligible_state: str | None
    empty_slot_policy: str
    tie_policy: str
    slot_weight_rule: str
    reserve_rule: str


class StrategyScheduleItem(ApiModel):
    artifact_id: UUID
    schedule_key: str
    frequency: Literal["weekly", "monthly"]
    decision_timing: str
    decision_data_policy: str


class StrategyExecutionPolicyItem(ApiModel):
    artifact_id: UUID
    policy_key: str
    delay_common_sessions: int
    execution_price: str
    missing_execution_policy: str


class StrategyRuleSetItem(ApiModel):
    definition_artifact_id: UUID
    version_artifact_id: UUID
    strategy_key: str
    strategy_family: str
    hypothesis: str
    version_number: int
    selection_contract: str
    allocation_contract: str
    reserve_contract: str
    compatible_model_output_types: list[str]
    candidate_input_policy: str
    missing_input_policy: str
    variants: list[StrategyVariantItem]
    schedules: list[StrategyScheduleItem]
    execution_policy: StrategyExecutionPolicyItem


class StrategyProductItem(ApiModel):
    artifact_id: UUID
    product_key: str
    version_number: int
    model_specification_key: str
    model_specification_type: str
    model_output_type: str
    variant_key: str
    target_k: int
    research_tier: str
    universe_key: str
    schedule_key: str
    frequency: Literal["weekly", "monthly"]
    execution_policy_key: str
    execution_price: str
    target_path_count: int


class StrategyTargetPathItem(ApiModel):
    artifact_id: UUID
    product_artifact_id: UUID
    product_key: str
    model_dataset_artifact_id: UUID
    model_specification_key: str
    variant_key: str
    target_k: int
    frequency: Literal["weekly", "monthly"]
    coverage_start: date
    coverage_end: date
    decision_count: int
    position_count: int


class StrategyOverviewResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    rules: StrategyRuleSetItem
    products: list[StrategyProductItem]
    target_paths: list[StrategyTargetPathItem]


class StrategyTargetPositionItem(ApiModel):
    asset_key: str
    symbol: str
    model_score: float
    model_rank: float
    selection_rank: float | None
    trend_state: str | None
    strategy_eligible: bool
    selected: bool
    target_weight: float
    decision_reason: str


class StrategyDecisionItem(ApiModel):
    decision_date: date
    target_k: int
    actual_holding_count: int
    boundary_tie_count: int
    reserve_target_weight: float
    positions: list[StrategyTargetPositionItem]


class StrategyTargetPathResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    target_path: StrategyTargetPathItem
    universe_artifact_id: UUID
    data_bundle_artifact_id: UUID
    eligibility_artifact_id: UUID
    engine_artifact_id: UUID
    auxiliary_signal_dataset_artifact_id: UUID | None
    decisions: list[StrategyDecisionItem]


class ExperimentSuiteItem(ApiModel):
    artifact_id: UUID
    research_suite_id: UUID | None = None
    suite_key: str
    version_number: int
    name: str
    description: str
    specification_count: int


class ExperimentSpecificationItem(ApiModel):
    artifact_id: UUID
    result_artifact_id: UUID | None
    suite_artifact_id: UUID
    suite_mode: Literal["formal", "exploratory", "legacy"] = "legacy"
    cell_key: str
    ordinal: int
    product_key: str
    model_specification_key: str
    variant_key: str
    frequency: Literal["weekly", "monthly"]
    benchmark_key: str
    benchmark_category: str
    cost_bps_per_side: float
    template_key: str
    initialization_policy: str
    as_of_date: date | None
    simulation_end: date | None
    status: Literal["accepted", "failed", "running", "pending"]
    availability_status: str | None
    quality_status: str | None
    attempt_number: int | None
    error_summary: str | None
    core_metrics: dict[str, float | None]


class ExperimentOverviewResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    suites: list[ExperimentSuiteItem]
    specifications: list[ExperimentSpecificationItem]
    total_specification_count: int
    filtered_specification_count: int
    accepted_count: int
    failed_count: int
    running_count: int
    pending_count: int
    limit: int
    offset: int


class ExperimentMetricItem(ApiModel):
    series_role: Literal["strategy", "benchmark", "relative", "predictive"]
    metric_scope: Literal["absolute", "relative", "predictive"]
    metric_key: str
    name: str
    unit: str
    value: float | None
    value_status: str
    reason_code: str | None
    observation_count: int


class ExperimentRunEventItem(ApiModel):
    sequence_number: int
    event_type: str
    severity: str
    message: str
    occurred_at: datetime


class ExperimentQualityCheckItem(ApiModel):
    check_key: str
    scope_key: str
    status: str
    severity: str
    message: str


class ExperimentArtifactLinkItem(ApiModel):
    artifact_id: UUID
    role: str
    artifact_type: str
    artifact_key: str


class ExperimentNavPoint(ApiModel):
    nav_date: date
    strategy_wealth: float
    benchmark_wealth: float
    excess_wealth: float
    drawdown: float


class ExperimentResultResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    result_artifact_id: UUID
    specification: ExperimentSpecificationItem
    interval_result_artifact_id: UUID
    requested_start: date | None
    requested_end: date | None
    resolved_start: date | None
    resolved_end: date | None
    normalization_nav_date: date | None
    observation_count: int
    metric_value_count: int
    run_attempt_id: UUID
    run_status: str
    started_at: datetime | None
    completed_at: datetime | None
    metrics: list[ExperimentMetricItem]
    events: list[ExperimentRunEventItem]
    quality_checks: list[ExperimentQualityCheckItem]
    artifacts: list[ExperimentArtifactLinkItem]
    nav_series: list[ExperimentNavPoint]
    promotion_eligible: bool
    promotion_reason_codes: list[str]
    qualification_bundle_artifact_id: UUID | None


class ComparisonCohortItem(ApiModel):
    artifact_id: UUID
    cohort_key: str
    version_number: int
    name: str
    description: str
    context_fingerprint: str
    template_key: str
    initialization_policy: str
    target_k: int
    frequency: Literal["weekly", "monthly"]
    as_of_date: date
    common_data_ready_date: date
    common_simulation_start: date
    common_metric_start: date
    common_metric_end: date
    currency: Literal["USD"]
    member_count: int
    benchmark_key: str
    cost_bps_per_side: float
    required_warmup_observations: int


class ProductRankingEntry(ApiModel):
    rank: int | None
    result_artifact_id: UUID
    product_artifact_id: UUID
    product_key: str
    model_specification_key: str
    variant_key: str
    target_k: int
    frequency: Literal["weekly", "monthly"]
    metric_value: float | None
    value_status: str
    reason_code: str | None
    observation_count: int
    core_metrics: dict[str, float | None]


class ProductRankingResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    cohorts: list[ComparisonCohortItem]
    active_cohort_artifact_id: UUID | None
    selected_metric: str
    ranking_direction: Literal["higher_is_better", "lower_is_better"]
    candidate_count: int
    ranked_count: int
    entries: list[ProductRankingEntry]


class ProductCompareEntry(ApiModel):
    result_artifact_id: UUID
    product_key: str
    model_specification_key: str
    strategy_template_key: str
    variant_key: str
    target_k: int
    frequency: Literal["weekly", "monthly"]
    cost_bps_per_side: float
    template_key: str
    initialization_policy: str
    availability_status: str
    quality_status: str
    resolved_start: date | None
    resolved_end: date | None
    metrics: list[ExperimentMetricItem]


class ProductCompareResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    mode: Literal["controlled", "side_by_side", "identical"]
    changed_dimensions: list[str]
    blocking_context_fields: list[str]
    entries: list[ProductCompareEntry]


class DecisionComponentTraceItem(ApiModel):
    dimension_key: str
    dimension_weight: float
    dimension_transform: str
    signal_key: str
    signal_version_artifact_id: UUID
    signal_dataset_artifact_id: UUID
    signal_score: float
    signal_state: str | None
    input_transform: str
    component_weight: float
    transformed_signal_score: float
    weighted_component_input: float
    overall_contribution: float | None
    factor_key: str
    factor_variant_key: str
    factor_dataset_artifact_id: UUID
    factor_value: float | None
    data_bundle_artifact_id: UUID


class DecisionPositionTraceItem(ApiModel):
    asset_key: str
    symbol: str
    selected: bool
    model_score: float
    model_rank: float
    trend_state: str | None
    target_weight: float
    decision_reason: str
    components: list[DecisionComponentTraceItem]


class DecisionExplorerResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    result_artifact_id: UUID
    target_path_artifact_id: UUID
    model_dataset_artifact_id: UUID
    model_specification_artifact_id: UUID
    universe_artifact_id: UUID
    data_bundle_artifact_id: UUID
    eligibility_artifact_id: UUID
    model_method_key: str
    available_dates: list[date]
    selected_date: date
    target_k: int
    actual_holding_count: int
    reserve_target_weight: float
    positions: list[DecisionPositionTraceItem]
