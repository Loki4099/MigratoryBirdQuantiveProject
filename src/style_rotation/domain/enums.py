from enum import StrEnum


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkItemStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REUSED = "reused"


class WorkFailureClass(StrEnum):
    INFRASTRUCTURE = "infrastructure"
    INTERRUPTED = "interrupted"
    DATA_QUALITY = "data_quality"
    CAPACITY = "capacity"
    CONTRACT = "contract"


class ProductLifecycle(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"
    INVALIDATED = "invalidated"


class ProductHealth(StrEnum):
    OBSERVING = "observing"
    HEALTHY = "healthy"
    WATCH = "watch"
    WARNING = "warning"
    DATA_INTERRUPTED = "data_interrupted"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


class ReviewDecision(StrEnum):
    CONTINUE = "continue"
    SUSPEND = "suspend"
    RETIRE = "retire"
    REPLACE = "replace"


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


class FactorDirection(StrEnum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
