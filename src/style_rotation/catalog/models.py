from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from style_rotation.persistence.base import Base, CreatedAtMixin


class MasterDataRelease(CreatedAtMixin, Base):
    __tablename__ = "master_data_release"
    __table_args__ = (
        UniqueConstraint(
            "release_key", "version_number", name="uq_master_data_release_key_version"
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        {"schema": "catalog"},
    )

    master_data_release_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage.artifact.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    release_key: Mapped[str] = mapped_column(String(120), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)


class Asset(CreatedAtMixin, Base):
    __tablename__ = "asset"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('etf', 'equity', 'fund', 'index', 'commodity')",
            name="asset_type_allowed",
        ),
        CheckConstraint("status IN ('active', 'inactive')", name="status_allowed"),
        {"schema": "catalog"},
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    master_data_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.master_data_release.master_data_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")


class AssetIdentifier(CreatedAtMixin, Base):
    __tablename__ = "asset_identifier"
    __table_args__ = (
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to", name="valid_period"
        ),
        Index("ix_asset_identifier_lookup", "identifier_type", "identifier_value"),
        {"schema": "catalog"},
    )

    asset_identifier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    master_data_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.master_data_release.master_data_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.asset.asset_id", ondelete="RESTRICT"),
        nullable=False,
    )
    identifier_type: Mapped[str] = mapped_column(String(30), nullable=False)
    identifier_value: Mapped[str] = mapped_column(String(80), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class CalendarDefinition(CreatedAtMixin, Base):
    __tablename__ = "calendar_definition"
    __table_args__ = ({"schema": "catalog"},)

    calendar_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    master_data_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.master_data_release.master_data_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    calendar_key: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    venue_mic: Mapped[str] = mapped_column(String(4), nullable=False)


class AssetListing(CreatedAtMixin, Base):
    __tablename__ = "asset_listing"
    __table_args__ = (
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso_code"),
        CheckConstraint("venue_mic ~ '^[A-Z0-9]{4}$'", name="venue_mic_format"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to", name="valid_period"
        ),
        {"schema": "catalog"},
    )

    asset_listing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    master_data_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.master_data_release.master_data_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.asset.asset_id", ondelete="RESTRICT"),
        nullable=False,
    )
    calendar_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.calendar_definition.calendar_definition_id", ondelete="RESTRICT"),
        nullable=False,
    )
    listing_key: Mapped[str] = mapped_column(String(140), nullable=False, unique=True)
    venue_mic: Mapped[str] = mapped_column(String(4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class ListingSymbol(CreatedAtMixin, Base):
    __tablename__ = "listing_symbol"
    __table_args__ = (
        CheckConstraint("symbol_type IN ('ticker', 'vendor_symbol')", name="symbol_type_allowed"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to", name="valid_period"
        ),
        Index("ix_listing_symbol_lookup", "symbol_type", "symbol"),
        {"schema": "catalog"},
    )

    listing_symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    master_data_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.master_data_release.master_data_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.asset_listing.asset_listing_id", ondelete="RESTRICT"),
        nullable=False,
    )
    symbol_type: Mapped[str] = mapped_column(String(30), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class ClassificationScheme(CreatedAtMixin, Base):
    __tablename__ = "classification_scheme"
    __table_args__ = ({"schema": "catalog"},)

    classification_scheme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    master_data_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.master_data_release.master_data_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    scheme_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class ClassificationValue(CreatedAtMixin, Base):
    __tablename__ = "classification_value"
    __table_args__ = (
        UniqueConstraint(
            "classification_scheme_id",
            "value_key",
            name="uq_classification_value_scheme_value_key",
        ),
        {"schema": "catalog"},
    )

    classification_value_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    master_data_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.master_data_release.master_data_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    classification_scheme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.classification_scheme.classification_scheme_id", ondelete="RESTRICT"),
        nullable=False,
    )
    value_key: Mapped[str] = mapped_column(String(100), nullable=False)
    label_key: Mapped[str] = mapped_column(String(200), nullable=False)


class AssetClassification(CreatedAtMixin, Base):
    __tablename__ = "asset_classification"
    __table_args__ = (
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to", name="valid_period"
        ),
        {"schema": "catalog"},
    )

    asset_classification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    master_data_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.master_data_release.master_data_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.asset.asset_id", ondelete="RESTRICT"),
        nullable=False,
    )
    classification_value_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.classification_value.classification_value_id", ondelete="RESTRICT"),
        nullable=False,
    )
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class UniverseDefinition(CreatedAtMixin, Base):
    __tablename__ = "universe_definition"
    __table_args__ = ({"schema": "catalog"},)

    universe_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    master_data_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.master_data_release.master_data_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    universe_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class UniverseVersion(CreatedAtMixin, Base):
    __tablename__ = "universe_version"
    __table_args__ = (
        UniqueConstraint(
            "universe_definition_id",
            "version_number",
            name="uq_universe_version_definition_version",
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint("member_count >= 1", name="member_count_positive"),
        {"schema": "catalog"},
    )

    universe_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    universe_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.universe_definition.universe_definition_id", ondelete="RESTRICT"),
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


class UniverseMember(CreatedAtMixin, Base):
    __tablename__ = "universe_member"
    __table_args__ = (
        UniqueConstraint(
            "universe_version_id", "asset_id", name="uq_universe_member_version_asset"
        ),
        UniqueConstraint(
            "universe_version_id", "ordinal", name="uq_universe_member_version_ordinal"
        ),
        CheckConstraint(
            "role IN ('candidate', 'benchmark', 'auxiliary_tradable')", name="role_allowed"
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        {"schema": "catalog"},
    )

    universe_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    universe_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.universe_version.universe_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.asset.asset_id", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class DataRequirementDefinition(CreatedAtMixin, Base):
    __tablename__ = "data_requirement_definition"
    __table_args__ = ({"schema": "catalog"},)

    data_requirement_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    master_data_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog.master_data_release.master_data_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    requirement_set_key: Mapped[str] = mapped_column(String(140), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class DataRequirementVersion(CreatedAtMixin, Base):
    __tablename__ = "data_requirement_version"
    __table_args__ = (
        UniqueConstraint(
            "data_requirement_definition_id",
            "version_number",
            name="uq_data_requirement_version_definition_version",
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint("requirement_count >= 1", name="requirement_count_positive"),
        {"schema": "catalog"},
    )

    data_requirement_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    data_requirement_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "catalog.data_requirement_definition.data_requirement_definition_id",
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
    requirement_count: Mapped[int] = mapped_column(Integer, nullable=False)


class DataRequirementMember(CreatedAtMixin, Base):
    __tablename__ = "data_requirement_member"
    __table_args__ = (
        UniqueConstraint(
            "data_requirement_version_id",
            "requirement_key",
            name="uq_data_requirement_member_version_requirement_key",
        ),
        CheckConstraint(
            "subject IN ('universe_candidate', 'universe_benchmark', "
            "'reference_series', 'calendar')",
            name="subject_allowed",
        ),
        CheckConstraint("interval_count >= 1", name="interval_count_positive"),
        {"schema": "catalog"},
    )

    data_requirement_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    data_requirement_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "catalog.data_requirement_version.data_requirement_version_id", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    requirement_key: Mapped[str] = mapped_column(String(140), nullable=False)
    subject: Mapped[str] = mapped_column(String(40), nullable=False)
    series_key: Mapped[str] = mapped_column(String(120), nullable=False)
    fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    interval_unit: Mapped[str] = mapped_column(String(30), nullable=False)
    interval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    calendar_type: Mapped[str] = mapped_column(String(30), nullable=False)
    session_type: Mapped[str] = mapped_column(String(30), nullable=False)
    timestamp_semantics: Mapped[str] = mapped_column(String(80), nullable=False)
