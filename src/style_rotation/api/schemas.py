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
