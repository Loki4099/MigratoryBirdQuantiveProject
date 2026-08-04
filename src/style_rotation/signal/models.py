from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from style_rotation.persistence.base import Base, CreatedAtMixin


class SignalDefinition(CreatedAtMixin, Base):
    __tablename__ = "signal_definition"
    __table_args__ = ({"schema": "signal"},)

    signal_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    signal_key: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    template_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    economic_family: Mapped[str] = mapped_column(String(80), nullable=False)
    rationale_type: Mapped[str] = mapped_column(String(40), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    research_tier: Mapped[str] = mapped_column(String(30), nullable=False)
    product_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)


class SignalVersion(CreatedAtMixin, Base):
    __tablename__ = "signal_version"
    __table_args__ = (
        UniqueConstraint(
            "signal_definition_id", "version_number", name="uq_signal_version_definition_version"
        ),
        CheckConstraint("version_number >= 1", name="version_positive"),
        {"schema": "signal"},
    )

    signal_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    signal_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signal.signal_definition.signal_definition_id", ondelete="RESTRICT"),
        nullable=False,
    )
    factor_variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factor.factor_variant.factor_variant_id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(String(30), nullable=False)
    normalization: Mapped[str] = mapped_column(String(80), nullable=False)
    extreme_policy: Mapped[str] = mapped_column(String(80), nullable=False)
    missing_policy: Mapped[str] = mapped_column(String(80), nullable=False)
    tie_policy: Mapped[str] = mapped_column(String(40), nullable=False)
    output_type: Mapped[str] = mapped_column(String(40), nullable=False)
    rule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    calculation_frequency: Mapped[str] = mapped_column(String(30), nullable=False)
    time_semantics: Mapped[str] = mapped_column(String(160), nullable=False)
    evaluation_horizon_policy: Mapped[str] = mapped_column(String(100), nullable=False)


class SignalDataset(CreatedAtMixin, Base):
    __tablename__ = "signal_dataset"
    __table_args__ = (
        UniqueConstraint(
            "signal_version_id",
            "factor_dataset_id",
            "engine_version_id",
            name="uq_signal_dataset_exact_inputs",
        ),
        CheckConstraint("coverage_start <= coverage_end", name="coverage_ordered"),
        CheckConstraint("row_count >= 1", name="row_count_positive"),
        {"schema": "signal"},
    )

    signal_dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    signal_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signal.signal_version.signal_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    factor_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factor.factor_dataset.factor_dataset_id", ondelete="RESTRICT"),
        nullable=False,
    )
    universe_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.universe_version.universe_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    data_bundle_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data.data_bundle_version.data_bundle_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    eligibility_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.eligibility_snapshot.eligibility_snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    engine_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ops.engine_version.engine_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    coverage_start: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[date] = mapped_column(Date, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SignalValue(Base):
    __tablename__ = "signal_value"
    __table_args__ = ({"schema": "signal"},)

    signal_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signal.signal_dataset.signal_dataset_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.asset.asset_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    observation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    score: Mapped[Decimal] = mapped_column(Numeric(24, 18), nullable=False)
    state: Mapped[str | None] = mapped_column(String(20))
    event: Mapped[bool | None] = mapped_column(Boolean)


class SignalQualityIssue(CreatedAtMixin, Base):
    __tablename__ = "signal_quality_issue"
    __table_args__ = (
        CheckConstraint("severity IN ('info', 'warning', 'error')", name="severity_allowed"),
        {"schema": "signal"},
    )

    signal_quality_issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    signal_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signal.signal_dataset.signal_dataset_id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog.asset.asset_id", ondelete="RESTRICT")
    )
    observation_date: Mapped[date | None] = mapped_column(Date)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    issue_code: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class SignalEvaluation(CreatedAtMixin, Base):
    __tablename__ = "signal_evaluation"
    __table_args__ = ({"schema": "signal"},)

    signal_evaluation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage.artifact.artifact_id"), unique=True
    )
    signal_catalog_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage.artifact.artifact_id")
    )
    universe_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog.universe_version.universe_version_id")
    )
    data_bundle_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data.data_bundle_version.data_bundle_version_id")
    )
    eligibility_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog.eligibility_snapshot.eligibility_snapshot_id")
    )
    signal_engine_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ops.engine_version.engine_version_id")
    )
    evaluation_engine_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ops.engine_version.engine_version_id")
    )
    forward_return_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data.forward_return_dataset.forward_return_dataset_id")
    )
    frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    coverage_start: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[date] = mapped_column(Date, nullable=False)
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False)
    period_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pair_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    high_correlation_threshold: Mapped[float] = mapped_column(Float, nullable=False)


class SignalEvaluationPeriod(Base):
    __tablename__ = "signal_evaluation_period"
    __table_args__ = ({"schema": "signal"},)

    signal_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signal.signal_evaluation.signal_evaluation_id"),
        primary_key=True,
    )
    signal_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signal.signal_dataset.signal_dataset_id"), primary_key=True
    )
    decision_date: Mapped[date] = mapped_column(Date, primary_key=True)
    rank_ic: Mapped[float | None] = mapped_column(Float)
    rank_ic_reason: Mapped[str | None] = mapped_column(String(100))
    top_bottom_spread: Mapped[float] = mapped_column(Float, nullable=False)
    active_count: Mapped[int] = mapped_column(Integer, nullable=False)
    event_count: Mapped[int | None] = mapped_column(Integer)


class SignalEvaluationMetric(Base):
    __tablename__ = "signal_evaluation_metric"
    __table_args__ = ({"schema": "signal"},)

    signal_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signal.signal_evaluation.signal_evaluation_id"),
        primary_key=True,
    )
    signal_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signal.signal_dataset.signal_dataset_id"), primary_key=True
    )
    window_key: Mapped[str] = mapped_column(String(30), primary_key=True)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_count: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_ic_count: Mapped[int] = mapped_column(Integer, nullable=False)
    undefined_ic_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mean_rank_ic: Mapped[float | None] = mapped_column(Float)
    median_rank_ic: Mapped[float | None] = mapped_column(Float)
    positive_ic_ratio: Mapped[float | None] = mapped_column(Float)
    information_ratio: Mapped[float | None] = mapped_column(Float)
    mean_top_bottom_spread: Mapped[float] = mapped_column(Float, nullable=False)
    event_rate: Mapped[float | None] = mapped_column(Float)
    event_asset_concentration: Mapped[float | None] = mapped_column(Float)
    non_neutral_rate: Mapped[float] = mapped_column(Float, nullable=False)
    mean_top2_turnover: Mapped[float | None] = mapped_column(Float)


class SignalPairDiagnostic(Base):
    __tablename__ = "signal_pair_diagnostic"
    __table_args__ = ({"schema": "signal"},)

    signal_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signal.signal_evaluation.signal_evaluation_id"),
        primary_key=True,
    )
    left_signal_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signal.signal_dataset.signal_dataset_id"), primary_key=True
    )
    right_signal_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signal.signal_dataset.signal_dataset_id"), primary_key=True
    )
    score_observation_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    score_spearman: Mapped[float | None] = mapped_column(Float)
    spread_period_count: Mapped[int] = mapped_column(Integer, nullable=False)
    spread_correlation: Mapped[float | None] = mapped_column(Float)
    mean_top2_overlap: Mapped[float] = mapped_column(Float, nullable=False)
    high_correlation: Mapped[bool] = mapped_column(Boolean, nullable=False)


class SignalDiagnosticIssue(CreatedAtMixin, Base):
    __tablename__ = "signal_diagnostic_issue"
    __table_args__ = ({"schema": "signal"},)

    signal_diagnostic_issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    signal_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signal.signal_evaluation.signal_evaluation_id")
    )
    signal_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signal.signal_dataset.signal_dataset_id")
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    issue_code: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
