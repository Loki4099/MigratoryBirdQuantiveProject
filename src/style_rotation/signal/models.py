from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
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
