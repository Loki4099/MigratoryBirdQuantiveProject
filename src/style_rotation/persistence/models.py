from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from style_rotation.domain.enums import (
    ArchiveStatus,
    DataVersionStatus,
    ExperimentStatus,
    RunStatus,
)
from style_rotation.persistence.base import Base, CreatedAtMixin


class DataVersion(CreatedAtMixin, Base):
    __tablename__ = "data_versions"

    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    coverage_start: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[date] = mapped_column(Date, nullable=False)
    request_parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DataVersionStatus.PENDING.value
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("coverage_start <= coverage_end", name="coverage_dates_ordered"),
        CheckConstraint("status IN ('pending','published','failed')", name="valid_status"),
    )


class Asset(CreatedAtMixin, Base):
    __tablename__ = "assets"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    symbol: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    exchange_calendar: Mapped[str] = mapped_column(String(20), nullable=False, default="XNYS")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (CheckConstraint("role IN ('candidate','benchmark')", name="valid_role"),)


class RawMarketPrice(Base):
    __tablename__ = "raw_market_prices"

    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_versions.data_version_id", ondelete="CASCADE"),
        primary_key=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.asset_id"), primary_key=True
    )
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open_raw: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    high_raw: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    low_raw: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    close_raw: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    adj_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    volume_raw: Mapped[int | None] = mapped_column(BigInteger)
    dividends: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=0)
    stock_splits: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=0)
    source_row_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint("volume_raw IS NULL OR volume_raw >= 0", name="nonnegative_volume"),
    )


class RawRateObservation(Base):
    __tablename__ = "raw_rate_observations"

    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_versions.data_version_id", ondelete="CASCADE"),
        primary_key=True,
    )
    series_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    observation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    available_date: Mapped[date] = mapped_column(Date, nullable=False)
    annual_rate_percent: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    source_row_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "available_date >= observation_date", name="available_not_before_observation"
        ),
    )


class CleanMarketPrice(Base):
    __tablename__ = "clean_market_prices"

    data_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    cleaning_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.asset_id"), primary_key=True
    )
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open_adj: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    high_adj: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    low_adj: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    close_adj: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    adj_factor: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    volume_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dividends: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    stock_splits: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["data_version_id"], ["data_versions.data_version_id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["cleaning_version_id"], ["cleaning_versions.cleaning_version_id"]),
        CheckConstraint("adj_factor > 0", name="positive_adjustment_factor"),
        CheckConstraint("volume_raw >= 0", name="nonnegative_volume"),
    )


class ReserveDailyReturn(Base):
    __tablename__ = "reserve_daily_returns"

    data_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    cleaning_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    nav_date: Mapped[date] = mapped_column(Date, primary_key=True)
    series_id: Mapped[str] = mapped_column(String(30), nullable=False)
    source_observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_available_date: Mapped[date] = mapped_column(Date, nullable=False)
    annual_rate_percent: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    calendar_daily_factor: Mapped[Decimal] = mapped_column(Numeric(24, 16), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["data_version_id"], ["data_versions.data_version_id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["cleaning_version_id"], ["cleaning_versions.cleaning_version_id"]),
        CheckConstraint("calendar_daily_factor > 0", name="positive_daily_factor"),
    )


class CleanDataset(CreatedAtMixin, Base):
    __tablename__ = "clean_datasets"

    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_versions.data_version_id"), primary_key=True
    )
    cleaning_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cleaning_versions.cleaning_version_id"), primary_key=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    coverage_start: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[date] = mapped_column(Date, nullable=False)
    common_market_start: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="published")
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("coverage_start <= coverage_end", name="coverage_dates_ordered"),
        CheckConstraint("common_market_start >= coverage_start", name="common_start_in_coverage"),
        CheckConstraint("status = 'published'", name="published_only"),
    )


class DataQualityEvent(CreatedAtMixin, Base):
    __tablename__ = "data_quality_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_versions.data_version_id", ondelete="CASCADE"),
        nullable=False,
    )
    cleaning_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cleaning_versions.cleaning_version_id")
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(100), nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.asset_id")
    )
    series_id: Mapped[str | None] = mapped_column(String(30))
    event_date: Mapped[date | None] = mapped_column(Date)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint("severity IN ('info','warning','error')", name="valid_severity"),
        Index("ix_data_quality_version_severity", "data_version_id", "severity"),
    )


class CleaningVersion(CreatedAtMixin, Base):
    __tablename__ = "cleaning_versions"

    cleaning_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    rules_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class FactorVersion(CreatedAtMixin, Base):
    __tablename__ = "factor_versions"

    factor_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    registry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class FactorDefinition(CreatedAtMixin, Base):
    __tablename__ = "factor_definitions"

    factor_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    factor_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factor_versions.factor_version_id", ondelete="CASCADE")
    )
    definition_key: Mapped[str] = mapped_column(String(100), nullable=False)
    family: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    required_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    direction: Mapped[str] = mapped_column(String(30), nullable=False)
    implementation_key: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "factor_version_id", "definition_key", name="uq_factor_definition_version_key"
        ),
        CheckConstraint(
            "direction IN ('higher_is_better','lower_is_better')",
            name="valid_direction",
        ),
    )


class FactorVariant(CreatedAtMixin, Base):
    __tablename__ = "factor_variants"

    factor_variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    factor_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factor_versions.factor_version_id", ondelete="CASCADE")
    )
    factor_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factor_definitions.factor_definition_id", ondelete="CASCADE"),
    )
    variant_key: Mapped[str] = mapped_column(String(150), nullable=False)
    parameters: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    minimum_observations: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("factor_version_id", "variant_key", name="uq_factor_variant_version_key"),
        UniqueConstraint(
            "factor_version_id",
            "factor_variant_id",
            name="uq_factor_variant_version_identity",
        ),
        CheckConstraint("minimum_observations > 0", name="positive_minimum_observations"),
    )


class FactorValue(Base):
    __tablename__ = "factor_values"

    data_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    cleaning_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    factor_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    factor_variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    raw_value: Mapped[Decimal] = mapped_column(Numeric(30, 14), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["data_version_id", "cleaning_version_id", "asset_id", "trade_date"],
            [
                "clean_market_prices.data_version_id",
                "clean_market_prices.cleaning_version_id",
                "clean_market_prices.asset_id",
                "clean_market_prices.trade_date",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["factor_version_id", "factor_variant_id"],
            ["factor_variants.factor_version_id", "factor_variants.factor_variant_id"],
        ),
        Index(
            "ix_factor_values_variant_asset_date",
            "factor_variant_id",
            "asset_id",
            "trade_date",
        ),
    )


class FactorDataset(CreatedAtMixin, Base):
    __tablename__ = "factor_datasets"

    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_versions.data_version_id"), primary_key=True
    )
    cleaning_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cleaning_versions.cleaning_version_id"), primary_key=True
    )
    factor_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factor_versions.factor_version_id"), primary_key=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    common_valid_start: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[date] = mapped_column(Date, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="published")
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("common_valid_start <= coverage_end", name="valid_coverage"),
        CheckConstraint("row_count > 0", name="positive_row_count"),
        CheckConstraint("status = 'published'", name="published_only"),
    )


class StrategyVersion(CreatedAtMixin, Base):
    __tablename__ = "strategy_versions"

    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class SignalDataset(CreatedAtMixin, Base):
    __tablename__ = "signal_datasets"

    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_versions.data_version_id"), primary_key=True
    )
    cleaning_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cleaning_versions.cleaning_version_id"), primary_key=True
    )
    factor_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factor_versions.factor_version_id"), primary_key=True
    )
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategy_versions.strategy_version_id"), primary_key=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_signal_date: Mapped[date] = mapped_column(Date, nullable=False)
    first_execution_date: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[date] = mapped_column(Date, nullable=False)
    event_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    position_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="published")
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("first_signal_date < first_execution_date", name="signal_before_execution"),
        CheckConstraint("first_execution_date <= coverage_end", name="execution_in_coverage"),
        CheckConstraint("event_count > 0", name="positive_event_count"),
        CheckConstraint("position_count = event_count * 4", name="four_positions_per_event"),
        CheckConstraint("status = 'published'", name="published_only"),
    )


class RebalanceEvent(Base):
    __tablename__ = "rebalance_events"

    rebalance_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    data_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    cleaning_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    factor_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    factor_variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rebalance_frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy_template: Mapped[str] = mapped_column(String(30), nullable=False)
    signal_date: Mapped[date] = mapped_column(Date, nullable=False)
    execution_date: Mapped[date] = mapped_column(Date, nullable=False)
    eligible_count: Mapped[int] = mapped_column(Integer, nullable=False)
    tie_flag: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reserve_target_weight: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            [
                "data_version_id",
                "cleaning_version_id",
                "factor_version_id",
                "strategy_version_id",
            ],
            [
                "signal_datasets.data_version_id",
                "signal_datasets.cleaning_version_id",
                "signal_datasets.factor_version_id",
                "signal_datasets.strategy_version_id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["factor_version_id", "factor_variant_id"],
            ["factor_variants.factor_version_id", "factor_variants.factor_variant_id"],
        ),
        UniqueConstraint(
            "data_version_id",
            "cleaning_version_id",
            "factor_version_id",
            "strategy_version_id",
            "factor_variant_id",
            "rebalance_frequency",
            "strategy_template",
            "signal_date",
            name="uq_rebalance_event_identity",
        ),
        CheckConstraint("signal_date < execution_date", name="signal_before_execution"),
        CheckConstraint("eligible_count BETWEEN 0 AND 4", name="valid_eligible_count"),
        CheckConstraint(
            "reserve_target_weight >= 0 AND reserve_target_weight <= 1",
            name="valid_reserve_weight",
        ),
        CheckConstraint("rebalance_frequency IN ('weekly','monthly')", name="valid_frequency"),
        CheckConstraint(
            "strategy_template IN ('cross_sectional','trend_filtered')",
            name="valid_template",
        ),
        Index(
            "ix_rebalance_events_variant_frequency_date",
            "factor_variant_id",
            "rebalance_frequency",
            "signal_date",
        ),
    )


class TargetPosition(Base):
    __tablename__ = "target_positions"

    rebalance_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalance_events.rebalance_event_id", ondelete="CASCADE"),
        primary_key=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.asset_id"), primary_key=True
    )
    raw_factor_value: Mapped[Decimal] = mapped_column(Numeric(30, 14), nullable=False)
    oriented_factor_value: Mapped[Decimal] = mapped_column(Numeric(30, 14), nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer)
    trend_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    tie_flag: Mapped[bool] = mapped_column(Boolean, nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    target_weight: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)

    __table_args__ = (
        CheckConstraint("rank IS NULL OR rank BETWEEN 1 AND 4", name="valid_rank"),
        CheckConstraint("target_weight >= 0 AND target_weight <= 0.5", name="valid_weight"),
        CheckConstraint(
            "(selected AND target_weight = 0.5) OR (NOT selected AND target_weight = 0)",
            name="selection_matches_weight",
        ),
        Index("ix_target_positions_asset_event", "asset_id", "rebalance_event_id"),
    )


class EngineVersion(CreatedAtMixin, Base):
    __tablename__ = "engine_versions"

    engine_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    git_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    dependency_lock_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    python_version: Mapped[str] = mapped_column(String(30), nullable=False)


class DataContract(CreatedAtMixin, Base):
    __tablename__ = "data_contracts"

    data_contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    layer: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("layer", "name", "schema_version", name="uq_data_contract_identity"),
        UniqueConstraint("contract_hash", name="uq_data_contract_hash"),
    )


class Experiment(CreatedAtMixin, Base):
    __tablename__ = "experiments"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    system_version: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ExperimentStatus.DRAFT.value
    )
    description: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    runs: Mapped[list[BacktestRun]] = relationship(back_populates="experiment")

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','running','completed','archived')",
            name="valid_status",
        ),
    )


class BacktestRun(CreatedAtMixin, Base):
    __tablename__ = "backtest_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiments.experiment_id", ondelete="CASCADE"),
        nullable=False,
    )
    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_versions.data_version_id"), nullable=False
    )
    cleaning_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cleaning_versions.cleaning_version_id"), nullable=False
    )
    factor_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factor_versions.factor_version_id"), nullable=False
    )
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategy_versions.strategy_version_id"), nullable=False
    )
    engine_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("engine_versions.engine_version_id"), nullable=False
    )
    run_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    factor_variant_key: Mapped[str] = mapped_column(String(150), nullable=False)
    warmup_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    official_signal_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    first_execution_date: Mapped[date] = mapped_column(Date, nullable=False)
    official_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    rebalance_frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy_template: Mapped[str] = mapped_column(String(30), nullable=False)
    transaction_cost_bps: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RunStatus.PENDING.value)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_type: Mapped[str | None] = mapped_column(String(200))
    error_message: Mapped[str | None] = mapped_column(Text)

    experiment: Mapped[Experiment] = relationship(back_populates="runs")
    events: Mapped[list[RunEvent]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "warmup_start_date <= official_signal_start_date", name="warmup_before_signal"
        ),
        CheckConstraint(
            "official_signal_start_date <= first_execution_date", name="signal_before_execution"
        ),
        CheckConstraint("first_execution_date <= official_end_date", name="execution_before_end"),
        CheckConstraint("transaction_cost_bps >= 0", name="nonnegative_cost"),
        CheckConstraint(
            "status IN ('pending','running','completed','failed')", name="valid_status"
        ),
        CheckConstraint("rebalance_frequency IN ('weekly','monthly')", name="valid_frequency"),
        CheckConstraint(
            "strategy_template IN ('cross_sectional','trend_filtered')",
            name="valid_template",
        ),
        Index("ix_backtest_runs_experiment_status", "experiment_id", "status"),
    )


class RunEvent(CreatedAtMixin, Base):
    __tablename__ = "run_events"

    run_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    run: Mapped[BacktestRun] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint("run_id", "sequence_no", name="uq_run_event_sequence"),
        CheckConstraint("sequence_no >= 0", name="nonnegative_sequence"),
    )


class VersionArchive(CreatedAtMixin, Base):
    __tablename__ = "version_archives"

    archive_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    system_version: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ArchiveStatus.PENDING.value
    )
    archive_uri: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restore_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','verified','restore_tested','failed')",
            name="valid_status",
        ),
    )
