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
