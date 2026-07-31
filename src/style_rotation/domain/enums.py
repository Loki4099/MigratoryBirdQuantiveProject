from enum import StrEnum


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExperimentStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ArchiveStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    RESTORE_TESTED = "restore_tested"
    FAILED = "failed"


class RebalanceFrequency(StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class StrategyTemplate(StrEnum):
    CROSS_SECTIONAL = "cross_sectional"
    TREND_FILTERED = "trend_filtered"


class AssetRole(StrEnum):
    CANDIDATE = "candidate"
    BENCHMARK = "benchmark"


class DataVersionStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class DataQualitySeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
