from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
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
