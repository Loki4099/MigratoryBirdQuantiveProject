from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from style_rotation.persistence.base import Base, CreatedAtMixin


class DataContractRelease(CreatedAtMixin, Base):
    __tablename__ = "data_contract_release"
    __table_args__ = (
        UniqueConstraint(
            "release_key", "version_number", name="uq_data_contract_release_key_version"
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        {"schema": "data"},
    )

    data_contract_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    release_key: Mapped[str] = mapped_column(String(120), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)


class SourceProvider(CreatedAtMixin, Base):
    __tablename__ = "source_provider"
    __table_args__ = (
        CheckConstraint(
            "provider_type IN ('market_data_wrapper', 'official_api', 'software_calendar')",
            name="provider_type_allowed",
        ),
        {"schema": "data"},
    )

    source_provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    data_contract_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.data_contract_release.data_contract_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(40), nullable=False)
    homepage: Mapped[str] = mapped_column(Text, nullable=False)
    terms_note: Mapped[str] = mapped_column(Text, nullable=False)


class DataSeriesDefinition(CreatedAtMixin, Base):
    __tablename__ = "data_series_definition"
    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('asset_listing', 'reference_series', 'calendar')",
            name="subject_type_allowed",
        ),
        CheckConstraint(
            "value_kind IN ('market_bar', 'rate_observation', 'calendar_session')",
            name="value_kind_allowed",
        ),
        {"schema": "data"},
    )

    data_series_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    data_contract_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.data_contract_release.data_contract_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    series_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(40), nullable=False)


class DataSeriesVersion(CreatedAtMixin, Base):
    __tablename__ = "data_series_version"
    __table_args__ = (
        UniqueConstraint(
            "data_series_definition_id",
            "version_number",
            name="uq_data_series_version_definition_version",
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint("interval_count >= 1", name="interval_count_positive"),
        {"schema": "data"},
    )

    data_series_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    data_series_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.data_series_definition.data_series_definition_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.source_provider.source_provider_id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_series_key: Mapped[str] = mapped_column(String(160), nullable=False)
    interval_unit: Mapped[str] = mapped_column(String(30), nullable=False)
    interval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    calendar_key: Mapped[str] = mapped_column(String(40), nullable=False)
    timestamp_semantics: Mapped[str] = mapped_column(String(100), nullable=False)
    availability_semantics: Mapped[str] = mapped_column(String(160), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(80), nullable=False)
    request_template: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    field_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class CleaningDefinition(CreatedAtMixin, Base):
    __tablename__ = "cleaning_definition"
    __table_args__ = ({"schema": "data"},)

    cleaning_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    data_contract_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.data_contract_release.data_contract_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    cleaning_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class CleaningVersion(CreatedAtMixin, Base):
    __tablename__ = "cleaning_version"
    __table_args__ = (
        UniqueConstraint(
            "cleaning_definition_id",
            "version_number",
            name="uq_cleaning_version_definition_version",
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        {"schema": "data"},
    )

    cleaning_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    cleaning_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.cleaning_definition.cleaning_definition_id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    implementation_key: Mapped[str] = mapped_column(String(140), nullable=False)
    implementation_version: Mapped[str] = mapped_column(String(80), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class SourceSnapshot(CreatedAtMixin, Base):
    __tablename__ = "source_snapshot"
    __table_args__ = (
        CheckConstraint("fetched_at >= requested_at", name="fetch_time_ordered"),
        CheckConstraint("raw_size_bytes >= 0", name="raw_size_nonnegative"),
        CheckConstraint("payload_compression = 'zlib'", name="compression_allowed"),
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="payload_hash_sha256"),
        Index("ix_source_snapshot_series_fetched", "data_series_version_id", "fetched_at"),
        {"schema": "data"},
    )

    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    data_series_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.data_series_version.data_series_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    snapshot_key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    as_of_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(80), nullable=False)
    request_parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_compression: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    compressed_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class CalendarVersion(CreatedAtMixin, Base):
    __tablename__ = "calendar_version"
    __table_args__ = (
        UniqueConstraint(
            "calendar_definition_id",
            "version_number",
            name="uq_calendar_version_definition_version",
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint("coverage_start <= coverage_end", name="coverage_ordered"),
        CheckConstraint("session_count >= 1", name="session_count_positive"),
        {"schema": "catalog"},
    )

    calendar_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    calendar_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.calendar_definition.calendar_definition_id", ondelete="RESTRICT"),
        nullable=False,
    )
    data_series_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.data_series_version.data_series_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.source_snapshot.source_snapshot_id", ondelete="RESTRICT"),
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    library_name: Mapped[str] = mapped_column(String(120), nullable=False)
    library_version: Mapped[str] = mapped_column(String(80), nullable=False)
    coverage_start: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[date] = mapped_column(Date, nullable=False)
    session_count: Mapped[int] = mapped_column(Integer, nullable=False)


class CalendarSession(Base):
    __tablename__ = "calendar_session"
    __table_args__ = (
        CheckConstraint("open_at_utc < close_at_utc", name="open_before_close"),
        {"schema": "catalog"},
    )

    calendar_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.calendar_version.calendar_version_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    session_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_early_close: Mapped[bool] = mapped_column(nullable=False)


class DatasetPublication(CreatedAtMixin, Base):
    __tablename__ = "dataset_publication"
    __table_args__ = (
        UniqueConstraint("dataset_key", "version_number", name="uq_dataset_key_version"),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint("dataset_kind IN ('canonical', 'derived')", name="dataset_kind_allowed"),
        CheckConstraint(
            "value_kind IN ('daily_bar', 'rate_observation', 'reserve_return')",
            name="value_kind_allowed",
        ),
        CheckConstraint("coverage_start <= coverage_end", name="coverage_ordered"),
        CheckConstraint("row_count >= 1", name="row_count_positive"),
        {"schema": "data"},
    )

    dataset_publication_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    cleaning_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.cleaning_version.cleaning_version_id", ondelete="RESTRICT"),
    )
    calendar_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.calendar_version.calendar_version_id", ondelete="RESTRICT"),
    )
    dataset_key: Mapped[str] = mapped_column(String(140), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    coverage_start: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[date] = mapped_column(Date, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)


class DatasetInput(Base):
    __tablename__ = "dataset_input"
    __table_args__ = (
        UniqueConstraint("dataset_publication_id", "role", "ordinal", name="uq_dataset_input_role"),
        CheckConstraint(
            "num_nonnulls(source_snapshot_id, upstream_dataset_publication_id) = 1",
            name="exactly_one_input",
        ),
        {"schema": "data"},
    )

    dataset_input_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    dataset_publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.dataset_publication.dataset_publication_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.source_snapshot.source_snapshot_id", ondelete="RESTRICT"),
    )
    upstream_dataset_publication_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.dataset_publication.dataset_publication_id", ondelete="RESTRICT"),
    )
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class DailyBar(Base):
    __tablename__ = "daily_bar"
    __table_args__ = (
        CheckConstraint(
            "LEAST(open_raw, high_raw, low_raw, close_raw, adj_close, "
            "open_adj, high_adj, low_adj, close_adj, adjustment_factor) > 0",
            name="prices_positive",
        ),
        CheckConstraint("volume_raw >= 0", name="volume_nonnegative"),
        {"schema": "data"},
    )

    dataset_publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.dataset_publication.dataset_publication_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.asset.asset_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    session_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open_raw: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    high_raw: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    low_raw: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    close_raw: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    adj_close: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    open_adj: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    high_adj: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    low_adj: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    close_adj: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    adjustment_factor: Mapped[Decimal] = mapped_column(Numeric(24, 14), nullable=False)
    volume_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)


class CorporateAction(Base):
    __tablename__ = "corporate_action"
    __table_args__ = (
        CheckConstraint("cash_dividend >= 0", name="cash_dividend_nonnegative"),
        CheckConstraint("split_ratio >= 0", name="split_ratio_nonnegative"),
        CheckConstraint("cash_dividend > 0 OR split_ratio > 0", name="action_nonempty"),
        {"schema": "data"},
    )

    dataset_publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.dataset_publication.dataset_publication_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.asset.asset_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    effective_date: Mapped[date] = mapped_column(Date, primary_key=True)
    cash_dividend: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    split_ratio: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)


class CanonicalRateObservation(Base):
    __tablename__ = "rate_observation"
    __table_args__ = ({"schema": "data"},)

    dataset_publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.dataset_publication.dataset_publication_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    series_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    observation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    available_date: Mapped[date] = mapped_column(Date, nullable=False)
    annual_rate_percent: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)


class DatasetCoverage(Base):
    __tablename__ = "dataset_coverage"
    __table_args__ = (
        CheckConstraint("coverage_start <= coverage_end", name="coverage_ordered"),
        CheckConstraint("observation_count >= 1", name="observation_count_positive"),
        CheckConstraint("missing_count >= 0", name="missing_count_nonnegative"),
        UniqueConstraint(
            "dataset_publication_id", "subject_key", name="uq_dataset_coverage_subject"
        ),
        {"schema": "data"},
    )

    dataset_coverage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    dataset_publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.dataset_publication.dataset_publication_id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog.asset.asset_id", ondelete="RESTRICT")
    )
    subject_key: Mapped[str] = mapped_column(String(100), nullable=False)
    coverage_start: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[date] = mapped_column(Date, nullable=False)
    observation_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    missing_count: Mapped[int] = mapped_column(BigInteger, nullable=False)


class QualityIssueRecord(CreatedAtMixin, Base):
    __tablename__ = "quality_issue"
    __table_args__ = (
        CheckConstraint("severity IN ('info', 'warning', 'error')", name="severity_allowed"),
        Index("ix_quality_issue_dataset_severity", "dataset_publication_id", "severity"),
        {"schema": "data"},
    )

    quality_issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    dataset_publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.dataset_publication.dataset_publication_id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog.asset.asset_id", ondelete="RESTRICT")
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(100), nullable=False)
    event_date: Mapped[date | None] = mapped_column(Date)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ReserveReturn(Base):
    __tablename__ = "reserve_return"
    __table_args__ = (
        CheckConstraint("interval_start < interval_end", name="interval_ordered"),
        CheckConstraint("calendar_days >= 1", name="calendar_days_positive"),
        CheckConstraint("accrual_factor > 0", name="accrual_factor_positive"),
        CheckConstraint("staleness_days >= 0", name="staleness_nonnegative"),
        CheckConstraint("quality_status IN ('normal', 'warning')", name="quality_status_allowed"),
        {"schema": "data"},
    )

    dataset_publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.dataset_publication.dataset_publication_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    interval_start: Mapped[date] = mapped_column(Date, primary_key=True)
    interval_end: Mapped[date] = mapped_column(Date, nullable=False)
    source_observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_available_date: Mapped[date] = mapped_column(Date, nullable=False)
    annual_rate_percent: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    calendar_days: Mapped[int] = mapped_column(Integer, nullable=False)
    accrual_factor: Mapped[Decimal] = mapped_column(Numeric(24, 14), nullable=False)
    staleness_days: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_status: Mapped[str] = mapped_column(String(20), nullable=False)


class DataBundleDefinition(CreatedAtMixin, Base):
    __tablename__ = "data_bundle_definition"
    __table_args__ = ({"schema": "data"},)

    data_bundle_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    bundle_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class DataBundleVersion(CreatedAtMixin, Base):
    __tablename__ = "data_bundle_version"
    __table_args__ = (
        UniqueConstraint(
            "data_bundle_definition_id", "version_number", name="uq_data_bundle_version"
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint("member_count >= 1", name="member_count_positive"),
        CheckConstraint("coverage_start <= coverage_end", name="coverage_ordered"),
        {"schema": "data"},
    )

    data_bundle_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    data_bundle_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.data_bundle_definition.data_bundle_definition_id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_start: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[date] = mapped_column(Date, nullable=False)


class DataBundleMember(Base):
    __tablename__ = "data_bundle_member"
    __table_args__ = (
        UniqueConstraint("data_bundle_version_id", "role", name="uq_data_bundle_member_role"),
        CheckConstraint(
            "num_nonnulls(dataset_publication_id, calendar_version_id) = 1",
            name="exactly_one_member",
        ),
        {"schema": "data"},
    )

    data_bundle_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    data_bundle_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.data_bundle_version.data_bundle_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    dataset_publication_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.dataset_publication.dataset_publication_id", ondelete="RESTRICT"),
    )
    calendar_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.calendar_version.calendar_version_id", ondelete="RESTRICT"),
    )
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class EligibilitySnapshot(CreatedAtMixin, Base):
    __tablename__ = "eligibility_snapshot"
    __table_args__ = (
        CheckConstraint("requested_start <= requested_end", name="requested_range_ordered"),
        CheckConstraint("warmup_observations >= 1", name="warmup_positive"),
        CheckConstraint("eligible_count >= 0", name="eligible_count_nonnegative"),
        CheckConstraint("member_count >= eligible_count", name="eligible_count_bounded"),
        {"schema": "catalog"},
    )

    eligibility_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    universe_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.universe_version.universe_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    data_requirement_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "catalog.data_requirement_version.data_requirement_version_id", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    data_bundle_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.data_bundle_version.data_bundle_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    snapshot_key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    requested_start: Mapped[date] = mapped_column(Date, nullable=False)
    requested_end: Mapped[date] = mapped_column(Date, nullable=False)
    warmup_observations: Mapped[int] = mapped_column(Integer, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_count: Mapped[int] = mapped_column(Integer, nullable=False)


class EligibilityItem(Base):
    __tablename__ = "eligibility_item"
    __table_args__ = (
        UniqueConstraint("eligibility_snapshot_id", "asset_id", name="uq_eligibility_item_asset"),
        {"schema": "catalog"},
    )

    eligibility_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    eligibility_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.eligibility_snapshot.eligibility_snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.asset.asset_id", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    is_eligible: Mapped[bool] = mapped_column(nullable=False)
    available_start: Mapped[date | None] = mapped_column(Date)
    available_end: Mapped[date | None] = mapped_column(Date)
    data_ready_date: Mapped[date | None] = mapped_column(Date)
    observation_count: Mapped[int] = mapped_column(BigInteger, nullable=False)


class EligibilityIssue(Base):
    __tablename__ = "eligibility_issue"
    __table_args__ = (
        CheckConstraint("severity IN ('warning', 'error')", name="severity_allowed"),
        {"schema": "catalog"},
    )

    eligibility_issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    eligibility_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.eligibility_item.eligibility_item_id", ondelete="RESTRICT"),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    issue_code: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ReserveReturnModelDefinition(CreatedAtMixin, Base):
    __tablename__ = "reserve_return_model_definition"
    __table_args__ = ({"schema": "experiment"},)

    reserve_return_model_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    model_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)


class ReserveReturnModelVersion(CreatedAtMixin, Base):
    __tablename__ = "reserve_return_model_version"
    __table_args__ = (
        UniqueConstraint(
            "reserve_return_model_definition_id",
            "version_number",
            name="uq_reserve_return_model_version",
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint("warning_after_days >= 0", name="warning_days_nonnegative"),
        CheckConstraint("error_after_days >= warning_after_days", name="staleness_ordered"),
        {"schema": "experiment"},
    )

    reserve_return_model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    reserve_return_model_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "experiment.reserve_return_model_definition.reserve_return_model_definition_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    accrual_method: Mapped[str] = mapped_column(String(80), nullable=False)
    day_count_basis: Mapped[str] = mapped_column(String(40), nullable=False)
    warning_after_days: Mapped[int] = mapped_column(Integer, nullable=False)
    error_after_days: Mapped[int] = mapped_column(Integer, nullable=False)


class ForwardReturnDefinition(CreatedAtMixin, Base):
    __tablename__ = "forward_return_definition"
    __table_args__ = ({"schema": "data"},)

    forward_return_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    target_key: Mapped[str] = mapped_column(String(140), nullable=False, unique=True)


class ForwardReturnVersion(CreatedAtMixin, Base):
    __tablename__ = "forward_return_version"
    __table_args__ = (
        UniqueConstraint(
            "forward_return_definition_id",
            "version_number",
            name="uq_forward_return_version_definition_version",
        ),
        {"schema": "data"},
    )

    forward_return_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    forward_return_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.forward_return_definition.forward_return_definition_id"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage.artifact.artifact_id"), nullable=False, unique=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    decision_rule: Mapped[str] = mapped_column(String(80), nullable=False)
    decision_time: Mapped[str] = mapped_column(String(40), nullable=False)
    execution_policy: Mapped[str] = mapped_column(String(100), nullable=False)
    start_price: Mapped[str] = mapped_column(String(40), nullable=False)
    end_price: Mapped[str] = mapped_column(String(80), nullable=False)
    execution_lag_sessions: Mapped[int] = mapped_column(Integer, nullable=False)
    overlap_policy: Mapped[str] = mapped_column(String(80), nullable=False)
    calendar_key: Mapped[str] = mapped_column(String(40), nullable=False)
    included_member_roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False)


class ForwardReturnDataset(CreatedAtMixin, Base):
    __tablename__ = "forward_return_dataset"
    __table_args__ = ({"schema": "data"},)

    forward_return_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage.artifact.artifact_id"), nullable=False, unique=True
    )
    forward_return_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data.forward_return_version.forward_return_version_id")
    )
    universe_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog.universe_version.universe_version_id")
    )
    data_bundle_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data.data_bundle_version.data_bundle_version_id")
    )
    market_dataset_publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data.dataset_publication.dataset_publication_id")
    )
    calendar_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog.calendar_version.calendar_version_id")
    )
    engine_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ops.engine_version.engine_version_id")
    )
    requested_start: Mapped[date] = mapped_column(Date, nullable=False)
    requested_end: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_start: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[date] = mapped_column(Date, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ForwardReturnValue(Base):
    __tablename__ = "forward_return_value"
    __table_args__ = ({"schema": "data"},)

    forward_return_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.forward_return_dataset.forward_return_dataset_id"),
        primary_key=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog.asset.asset_id"), primary_key=True
    )
    decision_date: Mapped[date] = mapped_column(Date, primary_key=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    forward_return: Mapped[Decimal] = mapped_column(Numeric(28, 18), nullable=False)
