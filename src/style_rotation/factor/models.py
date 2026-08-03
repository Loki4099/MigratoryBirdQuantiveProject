from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from style_rotation.persistence.base import Base, CreatedAtMixin


class FactorDefinition(CreatedAtMixin, Base):
    __tablename__ = "factor_definition"
    __table_args__ = ({"schema": "factor"},)

    factor_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    factor_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    measurement_family: Mapped[str] = mapped_column(String(80), nullable=False)


class FactorDefinitionVersion(CreatedAtMixin, Base):
    __tablename__ = "factor_definition_version"
    __table_args__ = (
        UniqueConstraint("factor_definition_id", "version_number", name="uq_definition_version"),
        CheckConstraint("version_number >= 1", name="version_positive"),
        {"schema": "factor"},
    )

    factor_definition_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    factor_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factor.factor_definition.factor_definition_id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    inputs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    output_unit: Mapped[str] = mapped_column(String(80), nullable=False)
    time_semantics: Mapped[str] = mapped_column(String(160), nullable=False)
    implementation_key: Mapped[str] = mapped_column(String(160), nullable=False)


class FactorVariant(CreatedAtMixin, Base):
    __tablename__ = "factor_variant"
    __table_args__ = (
        UniqueConstraint(
            "factor_definition_version_id", "parameter_hash", name="uq_definition_parameters"
        ),
        UniqueConstraint(
            "factor_definition_version_id", "variant_key", name="uq_definition_variant_key"
        ),
        CheckConstraint("required_price_observations >= 1", name="required_observations_positive"),
        {"schema": "factor"},
    )

    factor_variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    factor_definition_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "factor.factor_definition_version.factor_definition_version_id", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    variant_key: Mapped[str] = mapped_column(String(180), nullable=False)
    parameters: Mapped[dict[str, int | float | str | bool]] = mapped_column(JSONB, nullable=False)
    parameter_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    required_price_observations: Mapped[int] = mapped_column(Integer, nullable=False)
    preset_type: Mapped[str] = mapped_column(String(40), nullable=False)


class FactorDataset(CreatedAtMixin, Base):
    __tablename__ = "factor_dataset"
    __table_args__ = (
        UniqueConstraint(
            "factor_variant_id",
            "universe_version_id",
            "data_bundle_version_id",
            "eligibility_snapshot_id",
            "engine_version_id",
            name="uq_exact_inputs",
        ),
        CheckConstraint("coverage_start <= coverage_end", name="coverage_ordered"),
        CheckConstraint("row_count >= 1", name="row_count_positive"),
        {"schema": "factor"},
    )

    factor_dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    factor_variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factor.factor_variant.factor_variant_id", ondelete="RESTRICT"),
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


class FactorValue(Base):
    __tablename__ = "factor_value"
    __table_args__ = ({"schema": "factor"},)

    factor_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factor.factor_dataset.factor_dataset_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.asset.asset_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    observation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)


class FactorQualityIssue(CreatedAtMixin, Base):
    __tablename__ = "factor_quality_issue"
    __table_args__ = (
        CheckConstraint("severity IN ('info', 'warning', 'error')", name="severity_allowed"),
        {"schema": "factor"},
    )

    factor_quality_issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    factor_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factor.factor_dataset.factor_dataset_id", ondelete="RESTRICT"),
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
