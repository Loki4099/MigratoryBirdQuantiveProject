from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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
    read_only: Literal[True] = True


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


class AssetCatalogItem(ApiModel):
    asset_id: UUID
    asset_key: str
    name: str
    asset_type: str
    status: str
    symbol: str
    venue_mic: str
    currency: str
    timezone: str
    calendar_key: str
    classifications: dict[str, str]
    universe_role: str | None
    universe_ordinal: int | None


class AssetCatalogResponse(ApiModel):
    context: ApiContext
    quality: QualitySummary
    release_artifact_id: UUID
    release_version_number: int
    as_of_date: str
    universe_key: str
    items: list[AssetCatalogItem]


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
