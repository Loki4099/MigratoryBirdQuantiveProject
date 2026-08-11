from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from style_rotation.persistence.base import Base, CreatedAtMixin


class ModelMethodDefinition(CreatedAtMixin, Base):
    __tablename__ = "model_method_definition"
    __table_args__ = ({"schema": "model"},)

    model_method_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage.artifact.artifact_id"), unique=True
    )
    method_key: Mapped[str] = mapped_column(String(80), unique=True)


class ModelMethodVersion(CreatedAtMixin, Base):
    __tablename__ = "model_method_version"
    __table_args__ = (
        UniqueConstraint(
            "model_method_definition_id",
            "version_number",
            name="uq_model_method_definition_version",
        ),
        CheckConstraint("version_number >= 1", name="version_positive"),
        {"schema": "model"},
    )

    model_method_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    model_method_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model.model_method_definition.model_method_definition_id")
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage.artifact.artifact_id"), unique=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    supported_input_transforms: Mapped[list[str]] = mapped_column(JSONB)
    missing_policy: Mapped[str] = mapped_column(String(80))
    neutral_policy: Mapped[str] = mapped_column(String(80))
    tie_policy: Mapped[str] = mapped_column(String(80))
    output_scaling: Mapped[str] = mapped_column(String(80))


class ModelDefinition(CreatedAtMixin, Base):
    __tablename__ = "model_definition"
    __table_args__ = ({"schema": "model"},)

    model_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage.artifact.artifact_id"), unique=True
    )
    model_key: Mapped[str] = mapped_column(String(160), unique=True)
    model_family: Mapped[str] = mapped_column(String(100))
    hypothesis: Mapped[str] = mapped_column(Text)


class ModelDefinitionVersion(CreatedAtMixin, Base):
    __tablename__ = "model_definition_version"
    __table_args__ = (
        UniqueConstraint(
            "model_definition_id", "version_number", name="uq_model_definition_version"
        ),
        CheckConstraint("version_number >= 1", name="version_positive"),
        {"schema": "model"},
    )

    model_definition_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    model_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model.model_definition.model_definition_id")
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage.artifact.artifact_id"), unique=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    architecture: Mapped[str] = mapped_column(String(100))
    missing_policy: Mapped[str] = mapped_column(String(100))
    neutral_policy: Mapped[str] = mapped_column(String(100))


class ModelSpecification(CreatedAtMixin, Base):
    __tablename__ = "model_specification"
    __table_args__ = ({"schema": "model"},)

    model_specification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    model_definition_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model.model_definition_version.model_definition_version_id")
    )
    overall_method_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model.model_method_version.model_method_version_id")
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage.artifact.artifact_id"), unique=True
    )
    specification_key: Mapped[str] = mapped_column(String(500), unique=True)
    specification_type: Mapped[str] = mapped_column(String(60))
    tie_output: Mapped[str] = mapped_column(String(40))
    output_type: Mapped[str] = mapped_column(String(40))
    active_dimension_count: Mapped[int] = mapped_column(Integer)
    component_count: Mapped[int] = mapped_column(Integer)
    research_tier: Mapped[str] = mapped_column(String(30))


class ModelDimension(CreatedAtMixin, Base):
    __tablename__ = "model_dimension"
    __table_args__ = (
        UniqueConstraint(
            "model_specification_id", "model_dimension_id", name="uq_model_dimension_identity"
        ),
        UniqueConstraint("model_specification_id", "dimension_key", name="uq_model_dimension_key"),
        UniqueConstraint("model_specification_id", "ordinal", name="uq_model_dimension_ordinal"),
        {"schema": "model"},
    )

    model_dimension_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    model_specification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model.model_specification.model_specification_id")
    )
    method_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model.model_method_version.model_method_version_id")
    )
    dimension_key: Mapped[str] = mapped_column(String(80))
    ordinal: Mapped[int] = mapped_column(Integer)
    input_transform: Mapped[str] = mapped_column(String(40))
    weight: Mapped[Decimal] = mapped_column(Numeric(24, 18))


class ModelComponent(CreatedAtMixin, Base):
    __tablename__ = "model_component"
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_specification_id", "model_dimension_id"],
            [
                "model.model_dimension.model_specification_id",
                "model.model_dimension.model_dimension_id",
            ],
        ),
        UniqueConstraint(
            "model_dimension_id", "ordinal", name="uq_model_component_dimension_ordinal"
        ),
        UniqueConstraint(
            "model_specification_id", "signal_version_id", name="uq_model_component_signal"
        ),
        {"schema": "model"},
    )

    model_component_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    model_specification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    model_dimension_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    signal_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signal.signal_version.signal_version_id")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    input_transform: Mapped[str] = mapped_column(String(40))
    weight: Mapped[Decimal] = mapped_column(Numeric(24, 18))


class ModelDataset(CreatedAtMixin, Base):
    __tablename__ = "model_dataset"
    __table_args__ = (
        UniqueConstraint(
            "model_specification_id",
            "universe_version_id",
            "data_bundle_version_id",
            "eligibility_snapshot_id",
            "engine_version_id",
            "input_set_hash",
            name="uq_model_dataset_exact_inputs",
        ),
        {"schema": "model"},
    )

    model_dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage.artifact.artifact_id"), unique=True
    )
    model_specification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model.model_specification.model_specification_id")
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
    engine_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ops.engine_version.engine_version_id")
    )
    input_set_hash: Mapped[str] = mapped_column(String(64))
    coverage_start: Mapped[date] = mapped_column(Date)
    coverage_end: Mapped[date] = mapped_column(Date)
    row_count: Mapped[int] = mapped_column(BigInteger)


class ModelDatasetInput(CreatedAtMixin, Base):
    __tablename__ = "model_dataset_input"
    __table_args__ = (
        UniqueConstraint(
            "model_dataset_id", "signal_dataset_id", name="uq_model_dataset_signal_input"
        ),
        {"schema": "model"},
    )

    model_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model.model_dataset.model_dataset_id"),
        primary_key=True,
    )
    model_component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model.model_component.model_component_id"),
        primary_key=True,
    )
    signal_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signal.signal_dataset.signal_dataset_id")
    )


class ModelValue(Base):
    __tablename__ = "model_value"
    __table_args__ = ({"schema": "model"},)

    model_dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model.model_dataset.model_dataset_id"),
        primary_key=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog.asset.asset_id"), primary_key=True
    )
    observation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    score: Mapped[Decimal] = mapped_column(Numeric(24, 18))
    direction: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[Decimal] = mapped_column(Numeric(24, 18))
