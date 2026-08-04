from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

ComparisonMode = Literal["controlled", "side_by_side", "identical"]

_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "model": ("model_specification_id",),
    "strategy": ("strategy_template_key", "strategy_semantics"),
    "k": ("target_k",),
    "frequency": ("frequency",),
    "cost": ("cost_model_version_id", "cost_bps_per_side"),
    "interval": ("interval_semantics",),
}

_PROTECTED_FIELDS = (
    "universe_version_id",
    "data_bundle_version_id",
    "eligibility_snapshot_id",
    "execution_policy_version_id",
    "reserve_return_model_version_id",
    "benchmark_version_id",
    "performance_metric_catalog_id",
    "accounting_engine_version_id",
    "benchmark_engine_version_id",
    "performance_engine_version_id",
    "currency",
)


@dataclass(frozen=True, slots=True)
class ComparisonClassification:
    mode: ComparisonMode
    changed_dimensions: tuple[str, ...]
    blocking_context_fields: tuple[str, ...]


def classify_comparison(rows: Sequence[Mapping[str, Any]]) -> ComparisonClassification:
    """Classify a multi-result view without pretending an uncontrolled view is causal."""
    if len(rows) < 2:
        raise ValueError("A comparison requires at least two results")
    changed = tuple(
        name for name, fields in _DIMENSIONS.items()
        if any(len({row[field] for row in rows}) > 1 for field in fields)
    )
    blockers = tuple(
        field for field in _PROTECTED_FIELDS if len({row[field] for row in rows}) > 1
    )
    if not changed and not blockers:
        mode: ComparisonMode = "identical"
    elif len(changed) == 1 and not blockers:
        mode = "controlled"
    else:
        mode = "side_by_side"
    return ComparisonClassification(mode, changed, blockers)
